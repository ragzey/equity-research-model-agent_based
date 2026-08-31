"""Independent auditor: checks each agent, the Python model, and the memo."""

from __future__ import annotations

import json
import logging
import re
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from ..agents.post_quant_reviewer import post_quant_reviewer_node
from ..agents.writer import _fmt_percent, _fmt_usd
from ..agents.industry_macro import _ground_narrative
from ..graphs.desk import AUDITOR, WRITER, make_message
from ..graphs.state import EquityResearchState
from ..prompts.desk import AUDITOR_SYSTEM, AUDITOR_USER
from ..tools.operating_cycle import operating_cycle_ledger
from ..tools.pdf_memo import write_memo_pdf
from ..tools.report_pack import build_report_pack
from ..tools.web_research import web_research_blob
from ..utils.grounding import contains_web_link
from ..utils.llm_client import LLMCallError, chat_json

logger = logging.getLogger("IndependentAuditor")

_KNOWN_TOKENS = {
    "DCF",
    "WACC",
    "FCFF",
    "EBIT",
    "EBITDA",
    "GAAP",
    "IFRS",
    "SEC",
    "CEO",
    "CFO",
    "EPS",
    "DPS",
    "USD",
    "NYSE",
    "NASDAQ",
    "ITEM",
    "MD&A",
    "CAPM",
    "ERP",
    "LTM",
    "N/A",
    "BUY",
    "HOLD",
    "SELL",
    "CAGR",
    "YOY",
    "FCF",
    "ROIC",
    "NOPAT",
    "NTM",
    "PEG",
    "SOTP",
    "EV",
    "PE",
    "PB",
    "PS",
    "ROE",
    "ROA",
    "ICR",
    "RF",
    "FX",
    "TAM",
    "GDP",
    "CPI",
    "FED",
    "US",
    "UK",
    "EU",
    "CCC",
    "DSO",
    "DIO",
    "DPO",
    "NWC",
    "STC",
    "PPE",
    "AR",
    "AP",
    "FTC",
    "DOJ",
    "FDA",
    "EPA",
    "IMF",
    "WTO",
    "ECB",
    "OECD",
}
_TICKER_RE = re.compile(r"\b[A-Z]{2,5}\b")
_PT_RE = re.compile(
    r"(12-month price target of )(\$[0-9,]+\.\d{2})",
    re.IGNORECASE,
)
_STREET_MEAN_PT_RE = re.compile(
    r"(Street mean 12-month target is )(\$[0-9,]+\.\d{2})",
    re.IGNORECASE,
)
_FAIR_RE = re.compile(
    r"(blended fair value is )(\$[0-9,]+\.\d{2})",
    re.IGNORECASE,
)
_LAST_PRICE_RE = re.compile(
    r"(last price of )(\$[0-9,]+\.\d{2})",
    re.IGNORECASE,
)
_WACC_RE = re.compile(
    r"(WACC of )(\d+\.\d{2}%)",
    re.IGNORECASE,
)
_WACC_LIST_RE = re.compile(
    r"(- WACC: )(\d+\.\d{2}%)",
    re.IGNORECASE,
)
_WACC_INLINE_RE = re.compile(
    r"(Discounting at a )(\d+\.\d{2}%)( WACC)",
    re.IGNORECASE,
)


def _finding(
    agent: str,
    code: str,
    message: str,
    *,
    corrected: bool = False,
) -> Dict[str, Any]:
    return {
        "agent": agent,
        "code": code,
        "message": message,
        "corrected": corrected,
    }


def _upper_tickers(values: Any) -> List[str]:
    out: List[str] = []
    seen = set()
    for raw in values or []:
        symbol = str(raw or "").strip().upper()
        if symbol and symbol not in seen:
            seen.add(symbol)
            out.append(symbol)
    return out


def allowed_tickers(state: EquityResearchState) -> Set[str]:
    target = str(state.get("ticker") or "").strip().upper()
    allowed: Set[str] = {target} if target else set()
    for symbol in state.get("competitor_tickers") or []:
        if str(symbol).strip():
            allowed.add(str(symbol).strip().upper())
    for row in (state.get("discovered_peers") or {}).get("candidates") or []:
        if isinstance(row, dict) and row.get("ticker"):
            allowed.add(str(row["ticker"]).strip().upper())
        elif isinstance(row, str) and row.strip():
            allowed.add(row.strip().upper())
    selection = state.get("peer_selection") or {}
    for symbol in selection.get("selected") or []:
        if str(symbol).strip():
            allowed.add(str(symbol).strip().upper())
    matrix = state.get("peer_comparison_matrix") or {}
    for symbol in matrix.get("competitors") or []:
        if str(symbol).strip():
            allowed.add(str(symbol).strip().upper())
    return {item for item in allowed if item}


def _filing_blob(state: EquityResearchState) -> str:
    sections = state.get("sec_filing_sections") or {}
    parts = [
        str(sections.get("item_1") or sections.get("Item 1") or ""),
        str(sections.get("item_1a") or sections.get("Item 1A") or ""),
        str(sections.get("item_7") or sections.get("Item 7") or ""),
    ]
    chunks = state.get("sec_filing_chunks") or []
    if isinstance(chunks, list):
        parts.extend(str(item) for item in chunks if item)
    elif chunks:
        parts.append(str(chunks))
    return "\n".join(parts)


def clip_peer_selection(
    state: EquityResearchState,
) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    target = str(state.get("ticker") or "").strip().upper()
    harvested = [
        str(row.get("ticker") or "").strip().upper()
        for row in (state.get("discovered_peers") or {}).get("candidates") or []
        if isinstance(row, dict)
    ]
    pinned = _upper_tickers(state.get("competitor_tickers"))
    allowed = set(harvested) | set(pinned)
    allowed.discard(target)
    selection = dict(state.get("peer_selection") or {})
    if not selection:
        return None, []
    selected = _upper_tickers(selection.get("selected"))
    dropped = [symbol for symbol in selected if allowed and symbol not in allowed]
    if not dropped:
        return None, []
    kept = [symbol for symbol in selected if not allowed or symbol in allowed]
    updated = dict(selection)
    updated["selected"] = kept
    updated["auditor_clipped"] = dropped
    findings = [
        _finding(
            "competitive",
            "INVENTED_PEER",
            f"Removed peer ticker(s) not on the harvested/pinned list: {', '.join(dropped)}.",
            corrected=True,
        )
    ]
    matrix = state.get("peer_comparison_matrix")
    matrix_update = None
    if isinstance(matrix, dict) and matrix.get("competitors"):
        matrix_update = dict(matrix)
        matrix_update["competitors"] = [
            symbol
            for symbol in _upper_tickers(matrix.get("competitors"))
            if symbol in kept or symbol == target
        ]
    return {"peer_selection": updated, "matrix": matrix_update}, findings


def clip_qualitative_evidence(
    state: EquityResearchState,
) -> Tuple[Optional[List[Dict[str, str]]], List[Dict[str, Any]]]:
    evidence = list(state.get("qualitative_evidence") or [])
    blob = _filing_blob(state).lower()
    if not evidence:
        return None, []
    if not blob.strip():
        return None, [
            _finding(
                "qualitative",
                "NO_FILING_TEXT",
                "Qualitative evidence could not be checked because filing text is empty.",
            )
        ]
    kept: List[Dict[str, str]] = []
    dropped = 0
    for row in evidence:
        if not isinstance(row, dict):
            dropped += 1
            continue
        excerpt = " ".join(str(row.get("excerpt") or "").split())
        if excerpt and excerpt.lower() in blob:
            kept.append(row)
        else:
            dropped += 1
    if dropped == 0:
        return None, []
    return kept, [
        _finding(
            "qualitative",
            "UNSOURCED_QUOTE",
            f"Dropped {dropped} evidence excerpt(s) that were not in the 10-K sections.",
            corrected=True,
        )
    ]


def novel_tickers(text: str, allowed: Set[str], background: str = "") -> List[str]:
    """Flag all-caps 2–5 letter tokens. Ordinary English is not a ticker."""
    background_tokens = set(_TICKER_RE.findall(background or ""))
    found: List[str] = []
    for token in _TICKER_RE.findall(text or ""):
        if token in allowed or token in _KNOWN_TOKENS or token in background_tokens:
            continue
        if token not in found:
            found.append(token)
    return found


def _grounded_text(
    text: Optional[str],
    *,
    allowed: Set[str],
    background: str,
) -> Optional[str]:
    cleaned = str(text or "").strip()
    if not cleaned or cleaned.lower() in {"null", "none"}:
        return None
    extra = novel_tickers(cleaned, allowed, background)
    if extra:
        return None
    if contains_web_link(cleaned):
        return None
    return cleaned


def align_memo_to_pack(memo: str, pack: Dict[str, Any]) -> Tuple[str, List[Dict[str, Any]]]:
    findings: List[Dict[str, Any]] = []
    updated = memo
    swaps = [
        (
            _PT_RE,
            _fmt_usd(pack.get("price_target_12m")),
            "MEMO_PRICE_TARGET",
            "12-month price target",
        ),
        (
            _FAIR_RE,
            _fmt_usd(pack.get("fair_value")),
            "MEMO_FAIR_VALUE",
            "blended fair value",
        ),
        (
            _LAST_PRICE_RE,
            _fmt_usd(pack.get("share_price")),
            "MEMO_SHARE_PRICE",
            "last price",
        ),
        (
            _STREET_MEAN_PT_RE,
            _fmt_usd((pack.get("street") or {}).get("target_mean")),
            "MEMO_STREET_PT",
            "Street mean 12-month target",
        ),
    ]
    wacc = pack.get("wacc")
    if wacc is not None:
        wacc_text = _fmt_percent(wacc)
        swaps.extend(
            [
                (_WACC_RE, wacc_text, "MEMO_WACC", "WACC"),
                (_WACC_LIST_RE, wacc_text, "MEMO_WACC", "WACC"),
                (_WACC_INLINE_RE, wacc_text, "MEMO_WACC", "WACC"),
            ]
        )

    for pattern, correct, code, label in swaps:
        if not correct or correct == "N/A":
            continue

        def _replacer(match: re.Match[str], value: str = correct, name: str = label, key: str = code) -> str:
            current = match.group(2)
            suffix = match.group(3) if match.lastindex and match.lastindex >= 3 else ""
            if current != value:
                findings.append(
                    _finding(
                        "writer",
                        key,
                        f"Replaced memo {name} {current} with ledger {value}.",
                        corrected=True,
                    )
                )
                return match.group(1) + value + suffix
            return match.group(0)

        updated = pattern.sub(_replacer, updated)

    rating = str(pack.get("model_rating") or "").upper()
    if rating:
        band = re.search(r"model band is \*\*([A-Za-z]+)\*\*", updated, re.IGNORECASE)
        if band and band.group(1).upper() != rating:
            updated = re.sub(
                r"model band is \*\*[A-Za-z]+\*\*",
                f"model band is **{rating}**",
                updated,
                count=1,
                flags=re.IGNORECASE,
            )
            findings.append(
                _finding(
                    "writer",
                    "MEMO_RATING",
                    f"Replaced memo model band {band.group(1).upper()} with ledger {rating}.",
                    corrected=True,
                )
            )
        header = re.search(
            r"\*\*Model-implied ([A-Za-z]+)\*\*",
            updated,
            re.IGNORECASE,
        )
        if header and header.group(1).upper() != rating:
            updated = re.sub(
                r"\*\*Model-implied [A-Za-z]+\*\*",
                f"**Model-implied {rating}**",
                updated,
                count=1,
                flags=re.IGNORECASE,
            )
            findings.append(
                _finding(
                    "writer",
                    "MEMO_HEADER_RATING",
                    f"Replaced memo header rating {header.group(1).upper()} with ledger {rating}.",
                    corrected=True,
                )
            )
    return updated, findings


def _truncate(value: Any, limit: int = 4000) -> str:
    text = str(value or "")
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _memo_excerpt(path: Optional[str], limit: int = 8000) -> str:
    if not path:
        return ""
    file_path = Path(path)
    if not file_path.is_file():
        return ""
    return _truncate(file_path.read_text(encoding="utf-8"), limit)


def _audit_packets(state: EquityResearchState, pack: Dict[str, Any]) -> Dict[str, str]:
    selection = state.get("peer_selection") or {}
    matrix = state.get("peer_comparison_matrix") or {}
    overrides = state.get("dcf_overrides") or {}
    summary = state.get("valuation_summary") or {}
    dcf = summary.get("dcf") or {}
    competitive = {
        "selected": selection.get("selected"),
        "rejected": selection.get("rejected"),
        "rationale": _truncate(selection.get("rationale"), 1500),
        "harvested": [
            row.get("ticker")
            for row in (state.get("discovered_peers") or {}).get("candidates") or []
            if isinstance(row, dict)
        ],
        "pinned": state.get("competitor_tickers"),
        "competitors_in_matrix": matrix.get("competitors"),
        "outlook": _truncate(state.get("industry_outlook"), 2500),
    }
    qualitative = {
        "summary": _truncate(state.get("qualitative_analysis_summary"), 2500),
        "risks": state.get("business_risks"),
        "evidence": state.get("qualitative_evidence") or [],
        "filing_available": bool(_filing_blob(state).strip()),
    }
    reviewer = {
        "decisions": overrides.get("decisions") or [],
        "desk_mode": overrides.get("desk_mode"),
        "notes": _truncate(
            " ".join(
                str(item.get("body") or "")
                for item in state.get("agent_messages") or []
                if item.get("from_agent") == "assumption_reviewer"
            ),
            2000,
        ),
    }
    industry_macro = {
        "packet": state.get("industry_macro_packet") or {},
        "outlook": _truncate(state.get("industry_outlook"), 1500),
    }
    company_products = {
        "packet": state.get("company_products_packet") or {},
        "instruction": (
            "Products and mix must be filing or fetched-page quotes. Flag invented "
            "launch dates, TAM, or URLs. Correct narrative only against Item 1 / 7 "
            "or allowlisted web excerpts on the ledger."
        ),
    }
    architect = {
        "choices": overrides.get("architect_choices"),
        "allowed": overrides.get("architect_allowed"),
        "views": overrides.get("industry_macro_views"),
        "operations_views": overrides.get("operations_views"),
        "instruction": "Flag only. Labels must come from Python menus; ignore any numeric growth the LLM typed.",
    }
    operations = {
        "packet": state.get("operations_packet") or {},
        "instruction": (
            "Python CCC/NWC/sales-to-capital metrics are frozen. Flag invented "
            "working-capital numbers. Correct narrative only against the ledger."
        ),
    }
    growth_path = {
        "packet": state.get("growth_path_packet") or {},
        "instruction": (
            "Python CAGR, price-to-sales, and fade sales-to-capital are frozen. "
            "Flag invented TAM. Correct narrative only against the metric ledger."
        ),
    }
    quant = {
        "valuation_method": pack.get("valuation_method") or state.get("valuation_method"),
        "is_math_verified": bool(state.get("is_math_verified")),
        "review_action": state.get("review_action"),
        "review_findings": state.get("review_findings") or [],
        "wacc": state.get("discount_rate"),
        "dcf_value": pack.get("dcf_value"),
        "fair_value": pack.get("fair_value"),
        "price_target_12m": pack.get("price_target_12m"),
        "model_rating": pack.get("model_rating"),
        "year1_eps": pack.get("year1_eps"),
        "street_target_mean": (pack.get("street") or {}).get("target_mean"),
        "thesis_headline": (pack.get("thesis") or {}).get("headline"),
        "bear_price_target": (pack.get("operating_scenarios") or {}).get("bear_pt"),
        "bull_price_target": (pack.get("operating_scenarios") or {}).get("bull_pt"),
        "terminal_wacc": dcf.get("terminal_wacc_applied"),
        "terminal_growth": dcf.get("terminal_growth_rate_applied"),
        "instruction": "Flag only. Do not replace any of these figures.",
    }
    writer = {
        "frozen": {
            "share_price": pack.get("share_price"),
            "fair_value": pack.get("fair_value"),
            "price_target_12m": pack.get("price_target_12m"),
            "model_rating": pack.get("model_rating"),
            "dcf_value": pack.get("dcf_value"),
            "wacc": pack.get("wacc"),
            "year1_eps": pack.get("year1_eps"),
            "street_target_mean": (pack.get("street") or {}).get("target_mean"),
            "thesis_spine": (pack.get("thesis") or {}).get("spine"),
            "bear_price_target": (pack.get("operating_scenarios") or {}).get("bear_pt"),
            "bull_price_target": (pack.get("operating_scenarios") or {}).get("bull_pt"),
        },
        "memo_excerpt": _memo_excerpt(state.get("final_equity_memo_path")),
    }
    return {
        "competitive_json": json.dumps(competitive, default=str),
        "qualitative_json": json.dumps(qualitative, default=str),
        "industry_macro_json": json.dumps(industry_macro, default=str),
        "company_products_json": json.dumps(company_products, default=str),
        "architect_json": json.dumps(architect, default=str),
        "operations_json": json.dumps(operations, default=str),
        "growth_path_json": json.dumps(growth_path, default=str),
        "reviewer_json": json.dumps(reviewer, default=str),
        "quant_json": json.dumps(quant, default=str),
        "writer_json": json.dumps(writer, default=str),
    }


def _section(payload: Any, name: str) -> Dict[str, Any]:
    if isinstance(payload, dict) and isinstance(payload.get(name), dict):
        return payload[name]
    return {}


def _issues(section: Dict[str, Any], agent: str) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    action = str(section.get("action") or "pass").strip().lower()
    for issue in section.get("issues") or []:
        text = str(issue or "").strip()
        if text:
            findings.append(
                _finding(agent, f"LLM_{action.upper() or 'FLAG'}", text)
            )
    return findings


def _replace_heading_block(memo: str, heading: str, body: str) -> str:
    pattern = re.compile(
        rf"(^|\n)({re.escape(heading)}\n\n)(.*?)(?=\n## |\n### |\Z)",
        re.DOTALL,
    )
    if not pattern.search(memo):
        return memo
    replacement = r"\1\2" + body.strip() + "\n"
    return pattern.sub(replacement, memo, count=1)


def _append_audit_section(memo: str, report: Dict[str, Any]) -> str:
    lines = ["## Independent audit", ""]
    lines.append(
        "An independent auditor checked each desk output against the ledger. "
        "WACC and DCF were not recalculated by the auditor."
    )
    lines.append("")
    for name, block in (report.get("agents") or {}).items():
        action = str((block or {}).get("action") or "pass")
        count = len((block or {}).get("findings") or [])
        extra = f" ({count} finding(s))" if count else ""
        lines.append(f"- **{name}:** {action}{extra}")
    applied = report.get("corrections") or []
    if applied:
        lines.append("")
        lines.append("Corrections applied:")
        for item in applied:
            lines.append(f"- {item}")
    section = "\n".join(lines) + "\n\n"
    marker = "## Sources and references"
    if marker in memo:
        return memo.replace(marker, section + marker, 1)
    return memo.rstrip() + "\n\n" + section


def _rewrite_outputs(
    state: EquityResearchState,
    memo_text: str,
    pack: Dict[str, Any],
) -> Tuple[Optional[str], Optional[str]]:
    memo_path = state.get("final_equity_memo_path")
    if not memo_path:
        return None, None
    path = Path(memo_path)
    path.write_text(memo_text, encoding="utf-8")
    pdf_path = path.with_name(path.name.replace("_memo.md", "_memo.pdf"))
    identity = {
        "ticker": pack.get("ticker") or state.get("ticker"),
        "company_name": pack.get("company_name"),
        "exchange": pack.get("exchange"),
        "industry": pack.get("industry"),
        "country": pack.get("country"),
        "rating": pack.get("model_rating"),
        "price_target": pack.get("price_target_12m"),
        "share_price": pack.get("share_price"),
        "upside": pack.get("upside_to_pt"),
        "valuation_date": date.today(),
    }
    try:
        write_memo_pdf(memo_text, pdf_path, identity=identity)
        return str(path), str(pdf_path)
    except Exception:
        logger.exception("Auditor PDF refresh failed; Markdown memo was still updated.")
        return str(path), state.get("final_equity_memo_pdf_path")


def _patch_sidecar(memo_path: Optional[str], audit_report: Dict[str, Any]) -> None:
    if not memo_path:
        return
    sidecar = Path(memo_path).with_name(
        Path(memo_path).name.replace("_memo.md", "_gui.json")
    )
    if not sidecar.is_file():
        return
    try:
        payload = json.loads(sidecar.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return
    if not isinstance(payload, dict):
        return
    payload["audit_report"] = audit_report
    sidecar.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def independent_auditor_node(state: EquityResearchState) -> Dict[str, Any]:
    """Audit each agent, the Python model, and the written memo; apply safe fixes."""
    ticker = str(state.get("ticker") or "").strip().upper()
    summary = dict(state.get("valuation_summary") or {})
    pack = summary.get("report_pack")
    if not isinstance(pack, dict) or not pack.get("key_data"):
        pack = build_report_pack(dict(state))
        summary["report_pack"] = pack
    allowed = allowed_tickers(state)
    filing = _filing_blob(state)
    web_blob = web_research_blob(state)
    background = " ".join(
        [
            filing,
            web_blob,
            str(state.get("industry_outlook") or ""),
            str(state.get("qualitative_analysis_summary") or ""),
            " ".join(allowed),
        ]
    )

    python_findings: List[Dict[str, Any]] = []
    updates: Dict[str, Any] = {}

    peer_fix, peer_findings = clip_peer_selection(state)
    python_findings.extend(peer_findings)
    if peer_fix:
        updates["peer_selection"] = peer_fix["peer_selection"]
        if peer_fix.get("matrix") is not None:
            updates["peer_comparison_matrix"] = peer_fix["matrix"]
        allowed = allowed_tickers({**dict(state), **updates})

    evidence_fix, evidence_findings = clip_qualitative_evidence(state)
    python_findings.extend(evidence_findings)
    if evidence_fix is not None:
        updates["qualitative_evidence"] = evidence_fix

    model_check = post_quant_reviewer_node(state)
    model_findings = [
        _finding(
            "quant",
            str(item.get("code") or "MODEL"),
            str(item.get("message") or ""),
        )
        for item in (model_check.get("review_findings") or [])
        if item.get("severity") in {"error", "high_warning"}
    ]
    python_findings.extend(model_findings)

    memo_path = state.get("final_equity_memo_path")
    memo_text = ""
    if memo_path and Path(memo_path).is_file():
        memo_text = Path(memo_path).read_text(encoding="utf-8")
        memo_text, memo_findings = align_memo_to_pack(memo_text, pack)
        python_findings.extend(memo_findings)

    packets = _audit_packets({**dict(state), **updates}, pack)
    payload = chat_json(
        [
            {"role": "system", "content": AUDITOR_SYSTEM},
            {
                "role": "user",
                "content": AUDITOR_USER.format(
                    ticker=ticker,
                    valuation_method=pack.get("valuation_method")
                    or state.get("valuation_method")
                    or "corporate_fcff",
                    **packets,
                ),
            },
        ],
        required=True,
    )
    if not payload:
        raise LLMCallError("Independent auditor did not return a JSON object.")

    llm_findings: List[Dict[str, Any]] = []
    corrections: List[str] = []
    agent_blocks: Dict[str, Any] = {}

    competitive = _section(payload, "competitive")
    llm_findings.extend(_issues(competitive, "competitive"))
    outlook = _grounded_text(
        competitive.get("corrected_outlook"),
        allowed=allowed,
        background=background,
    )
    rationale = _grounded_text(
        competitive.get("corrected_rationale"),
        allowed=allowed,
        background=background,
    )
    if outlook:
        updates["industry_outlook"] = outlook
        corrections.append("Replaced industry outlook with auditor-grounded text.")
        memo_text = _replace_heading_block(memo_text, "### Industry outlook", outlook)
    if rationale:
        selection = dict(updates.get("peer_selection") or state.get("peer_selection") or {})
        selection["rationale"] = rationale
        updates["peer_selection"] = selection
        corrections.append("Replaced competitive peer rationale with auditor-grounded text.")
    agent_blocks["competitive"] = {
        "action": competitive.get("action") or ("correct" if outlook or rationale or peer_findings else "pass"),
        "findings": peer_findings + _issues(competitive, "competitive"),
    }

    qualitative = _section(payload, "qualitative")
    llm_findings.extend(_issues(qualitative, "qualitative"))
    summary_text = _grounded_text(
        qualitative.get("corrected_summary"),
        allowed=allowed,
        background=filing or background,
    )
    if summary_text:
        updates["qualitative_analysis_summary"] = summary_text
        corrections.append("Replaced qualitative summary with auditor-grounded text.")
        memo_text = _replace_heading_block(memo_text, "## Company and industry", summary_text)
    agent_blocks["qualitative"] = {
        "action": qualitative.get("action")
        or ("correct" if summary_text or evidence_findings else "pass"),
        "findings": evidence_findings + _issues(qualitative, "qualitative"),
    }

    industry_macro = _section(payload, "industry_macro")
    llm_findings.extend(_issues(industry_macro, "industry_macro"))
    packet = dict(state.get("industry_macro_packet") or {})
    macro_ledger = "\n".join(
        part
        for part in (filing, web_blob, json.dumps(packet, default=str))
        if part
    )
    macro_narrative = _ground_narrative(
        industry_macro.get("corrected_narrative"),
        macro_ledger,
        allowed,
    )
    if macro_narrative:
        packet = dict(packet)
        packet["narrative"] = macro_narrative
        updates["industry_macro_packet"] = packet
        updates["industry_outlook"] = macro_narrative
        corrections.append("Replaced industry/macro narrative with auditor-grounded text.")
        memo_text = _replace_heading_block(memo_text, "### Industry outlook", macro_narrative)
    agent_blocks["industry_macro"] = {
        "action": industry_macro.get("action") or ("correct" if macro_narrative else "pass"),
        "findings": _issues(industry_macro, "industry_macro"),
    }

    company_section = _section(payload, "company_products")
    llm_findings.extend(_issues(company_section, "company_products"))
    products_packet = dict(state.get("company_products_packet") or {})
    products_ledger = "\n".join(
        part
        for part in (filing, web_blob, json.dumps(products_packet, default=str))
        if part
    )
    products_narrative = _ground_narrative(
        company_section.get("corrected_narrative"),
        products_ledger,
        allowed,
    )
    if products_narrative:
        products_packet = dict(products_packet)
        products_packet["narrative"] = products_narrative
        updates["company_products_packet"] = products_packet
        corrections.append("Replaced company/products narrative with auditor-grounded text.")
        memo_text = _replace_heading_block(
            memo_text, "### Company products", products_narrative
        )
    agent_blocks["company_products"] = {
        "action": company_section.get("action")
        or ("correct" if products_narrative else "pass"),
        "findings": _issues(company_section, "company_products"),
    }

    operations_section = _section(payload, "operations")
    llm_findings.extend(_issues(operations_section, "operations"))
    operations_packet = dict(
        updates.get("operations_packet") or state.get("operations_packet") or {}
    )
    ops_ledger = "\n".join(
        part
        for part in (
            filing,
            json.dumps(operations_packet.get("metrics") or {}, default=str),
            operating_cycle_ledger(operations_packet.get("metrics") or {}),
            str(operations_packet.get("narrative") or ""),
        )
        if part
    )
    operations_narrative = _ground_narrative(
        operations_section.get("corrected_narrative"),
        ops_ledger,
        allowed,
    )
    if operations_narrative:
        operations_packet = dict(operations_packet)
        operations_packet["narrative"] = operations_narrative
        updates["operations_packet"] = operations_packet
        corrections.append("Replaced operations narrative with auditor-grounded text.")
        memo_text = _replace_heading_block(
            memo_text, "### Operations commentary", operations_narrative
        )
    agent_blocks["operations"] = {
        "action": operations_section.get("action")
        or ("correct" if operations_narrative else "pass"),
        "findings": _issues(operations_section, "operations"),
    }

    growth_section = _section(payload, "growth_path")
    llm_findings.extend(_issues(growth_section, "growth_path"))
    growth_packet = dict(
        updates.get("growth_path_packet") or state.get("growth_path_packet") or {}
    )
    growth_ledger = "\n".join(
        part
        for part in (
            filing,
            json.dumps(growth_packet.get("metrics") or {}, default=str),
            str(growth_packet.get("narrative") or ""),
        )
        if part
    )
    growth_narrative = _ground_narrative(
        growth_section.get("corrected_narrative"),
        growth_ledger,
        allowed,
    )
    if growth_narrative:
        growth_packet = dict(growth_packet)
        growth_packet["narrative"] = growth_narrative
        updates["growth_path_packet"] = growth_packet
        corrections.append("Replaced growth-path narrative with auditor-grounded text.")
        memo_text = _replace_heading_block(
            memo_text, "### Growth-path commentary", growth_narrative
        )
    agent_blocks["growth_path"] = {
        "action": growth_section.get("action")
        or ("correct" if growth_narrative else "pass"),
        "findings": _issues(growth_section, "growth_path"),
    }

    architect_section = _section(payload, "architect")
    architect_findings = _issues(architect_section, "architect")
    llm_findings.extend(architect_findings)
    agent_blocks["architect"] = {
        "action": architect_section.get("action") or "pass",
        "findings": architect_findings,
        "model_not_rewritten": True,
    }

    reviewer = _section(payload, "reviewer")
    reviewer_findings = _issues(reviewer, "reviewer")
    llm_findings.extend(reviewer_findings)
    agent_blocks["reviewer"] = {
        "action": reviewer.get("action") or "pass",
        "findings": reviewer_findings,
    }

    quant = _section(payload, "quant")
    quant_llm = _issues(quant, "quant")
    llm_findings.extend(quant_llm)
    agent_blocks["quant"] = {
        "action": quant.get("action") or ("flag" if model_findings else "pass"),
        "findings": model_findings + quant_llm,
        "model_not_rewritten": True,
    }

    writer = _section(payload, "writer")
    llm_findings.extend(_issues(writer, "writer"))
    writer_qual = _grounded_text(
        writer.get("corrected_qualitative_narrative"),
        allowed=allowed,
        background=filing or background,
    )
    writer_outlook = _grounded_text(
        writer.get("corrected_industry_outlook"),
        allowed=allowed,
        background=background,
    )
    writer_desk = _grounded_text(
        writer.get("corrected_desk_synthesis"),
        allowed=allowed,
        background=background,
    )
    writer_thesis = _grounded_text(
        writer.get("corrected_investment_thesis"),
        allowed=allowed,
        background=filing or background,
    )
    if writer_qual:
        memo_text = _replace_heading_block(memo_text, "## Company and industry", writer_qual)
        corrections.append("Rewrote company/industry memo prose against ledger evidence.")
    if writer_outlook:
        memo_text = _replace_heading_block(memo_text, "### Industry outlook", writer_outlook)
        corrections.append("Rewrote industry outlook in the memo against ledger evidence.")
    if writer_desk:
        memo_text = _replace_heading_block(memo_text, "### Writer synthesis", writer_desk)
        corrections.append("Rewrote writer synthesis against frozen facts.")
    if writer_thesis:
        memo_text = _replace_heading_block(
            memo_text, "### Why this differs from Street", writer_thesis
        )
        corrections.append("Rewrote thesis why-paragraph against ledger evidence.")
    agent_blocks["writer"] = {
        "action": writer.get("action")
        or ("correct" if any(item.get("corrected") for item in python_findings if item["agent"] == "writer") else "pass"),
        "findings": [item for item in python_findings if item["agent"] == "writer"]
        + _issues(writer, "writer"),
    }

    python_corrections = [
        item["message"] for item in python_findings if item.get("corrected")
    ]
    corrections = python_corrections + corrections

    audit_report = {
        "agents": agent_blocks,
        "findings": python_findings + llm_findings,
        "corrections": corrections,
        "memo_corrected": bool(corrections),
        "model_rewritten": False,
    }
    if memo_text:
        audit_section_needed = "## Independent audit" not in memo_text
        if corrections or audit_section_needed:
            if audit_section_needed:
                memo_text = _append_audit_section(memo_text, audit_report)
            memo_file, pdf_file = _rewrite_outputs(state, memo_text, pack)
            if memo_file:
                updates["final_equity_memo_path"] = memo_file
            if pdf_file:
                updates["final_equity_memo_pdf_path"] = pdf_file

    _patch_sidecar(
        updates.get("final_equity_memo_path") or memo_path,
        audit_report,
    )

    issue_count = len(audit_report["findings"])
    body = (
        f"Independent audit complete for {ticker}: {issue_count} finding(s), "
        f"{len(corrections)} correction(s). Python valuation figures were not rewritten."
    )
    updates["audit_report"] = audit_report
    updates["valuation_summary"] = summary
    updates["agent_messages"] = [
        make_message(
            AUDITOR,
            WRITER,
            "audit",
            body,
            {"finding_count": issue_count, "corrections": corrections[:12]},
        )
    ]
    logger.info(body)
    return updates
