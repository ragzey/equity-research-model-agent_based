"""Industry, market, and macro analyst: structured demand packet, not DCF numbers."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, Iterable, List, Optional, Set

from ..agents.quant import fetch_ten_year_treasury_yield
from ..graphs.desk import ARCHITECT, INDUSTRY_MACRO, REVIEWER, WRITER, make_message
from ..graphs.state import EquityResearchState
from ..prompts.desk import INDUSTRY_MACRO_SYSTEM, INDUSTRY_MACRO_USER
from ..tools.firm_classifier import calculate_revenue_cagr
from ..utils.llm_client import LLMCallError, chat_json

logger = logging.getLogger("IndustryMacroAnalyst")

CATEGORY_VIEWS = {"above_history", "in_line", "below_history", "insufficient"}
PRICING_VIEWS = {"strong", "neutral", "weak", "insufficient"}
CYCLE_VIEWS = {"upswing", "mid", "downswing", "secular", "insufficient"}
MACRO_VIEWS = {"tailwind", "neutral", "headwind", "insufficient"}
INFLECTION_VIEWS = {"positive", "negative", "none", "insufficient"}
_CAPS_RE = re.compile(r"\b[A-Z]{2,5}\b")
_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
_KNOWN_CAPS = {
    "CAGR",
    "CCC",
    "CPI",
    "DCF",
    "DIO",
    "DPO",
    "DSO",
    "EBIT",
    "EBITDA",
    "EU",
    "FCFF",
    "FED",
    "FOMC",
    "FX",
    "GAAP",
    "GDP",
    "IFRS",
    "NWC",
    "PPE",
    "SEC",
    "STC",
    "TAM",
    "UK",
    "US",
    "USD",
    "WACC",
    "YOY",
}


def _clip_view(value: Any, allowed: set[str], default: str = "insufficient") -> str:
    label = str(value or "").strip().lower()
    return label if label in allowed else default


def _evidence(value: Any, limit: int = 400) -> str:
    text = " ".join(str(value or "").split())
    if "http://" in text.lower() or "https://" in text.lower():
        return ""
    return text[:limit]


def _in_ledger(excerpt: str, ledger_text: str, *, min_len: int = 24) -> bool:
    needle = " ".join((excerpt or "").split()).lower()
    haystack = " ".join((ledger_text or "").split()).lower()
    if len(needle) < min_len or not haystack:
        return False
    return needle in haystack


def _novel_caps(
    text: str,
    allowed: Optional[Iterable[str]] = None,
    background: str = "",
) -> List[str]:
    permitted: Set[str] = {str(item).upper() for item in (allowed or []) if item}
    background_tokens = set(_CAPS_RE.findall(background or ""))
    found: List[str] = []
    for token in _CAPS_RE.findall(text or ""):
        if token in permitted or token in _KNOWN_CAPS or token in background_tokens:
            continue
        if token not in found:
            found.append(token)
    return found


def _numbers_in_ledger(sentence: str, ledger_text: str) -> bool:
    """Every number in the sentence must appear as that number in the ledger.

    Substring matches are rejected: 12 does not count as grounded by 120.
    """
    haystack = (ledger_text or "").replace(",", "")
    for token in _NUMBER_RE.findall((sentence or "").replace(",", "")):
        pattern = rf"(?<![\d.]){re.escape(token)}(?![\d.])"
        if not re.search(pattern, haystack):
            return False
    return True


def _ground_narrative(
    value: Any,
    ledger_text: str,
    allowed_tickers: Optional[Iterable[str]] = None,
) -> str:
    text = _evidence(value, limit=1800)
    if not text:
        return ""
    if _novel_caps(text, allowed_tickers, ledger_text):
        return ""
    kept: List[str] = []
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        chunk = sentence.strip()
        if not chunk:
            continue
        if not _numbers_in_ledger(chunk, ledger_text):
            continue
        kept.append(chunk)
    return " ".join(kept).strip()


def _ground_block(
    block: Dict[str, Any],
    *,
    view_key: str,
    allowed: set[str],
    ledger_text: str,
) -> Dict[str, str]:
    view = _clip_view(block.get(view_key) or block.get("view"), allowed)
    evidence = _evidence(block.get("evidence"))
    if evidence and not _in_ledger(evidence, ledger_text):
        view = "insufficient"
        evidence = ""
    elif view != "insufficient" and not evidence:
        view = "insufficient"
    return {view_key: view, "evidence": evidence}


def _filing_blob(state: EquityResearchState) -> str:
    sections = state.get("sec_filing_sections") or {}
    parts = [
        str(sections.get("item_1a") or sections.get("Item 1A") or ""),
        str(sections.get("item_7") or sections.get("Item 7") or ""),
    ]
    chunks = state.get("sec_filing_chunks") or []
    if isinstance(chunks, list):
        parts.extend(str(item) for item in chunks[:4] if item)
    return "\n".join(part for part in parts if part)


def _peer_snapshot(state: EquityResearchState) -> Dict[str, Any]:
    matrix = state.get("peer_comparison_matrix") or {}
    metrics = matrix.get("metrics") or {}
    compact = {}
    for ticker, row in metrics.items():
        if not isinstance(row, dict):
            continue
        compact[str(ticker)] = {
            "operating_margin_pct": row.get("operating_margin_pct"),
            "revenue_growth_yoy_pct": row.get("revenue_growth_yoy_pct"),
            "ev_to_ebitda": row.get("ev_to_ebitda"),
        }
    return {
        "target": matrix.get("target"),
        "competitors": matrix.get("competitors"),
        "metrics": compact,
    }


def normalize_industry_macro_packet(
    payload: Optional[Dict[str, Any]],
    *,
    risk_free_rate: Optional[float],
    ledger_text: str = "",
    filing_text: str = "",
    allowed_tickers: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    """Keep categorical views; drop URLs, unsourced quotes, and any DCF fields.

    High-band growth and demand-inflection views must be grounded in the 10-K
    (or structured filing excerpts), not in qualitative prose or peer JSON.
    """
    raw = payload if isinstance(payload, dict) else {}
    category = raw.get("category_growth") if isinstance(raw.get("category_growth"), dict) else {}
    pricing = raw.get("pricing_power") if isinstance(raw.get("pricing_power"), dict) else {}
    cycle = raw.get("cycle") if isinstance(raw.get("cycle"), dict) else {}
    macro_raw = raw.get("macro") if isinstance(raw.get("macro"), dict) else {}
    inflection = (
        raw.get("demand_inflection")
        if isinstance(raw.get("demand_inflection"), dict)
        else {}
    )
    growth_source = filing_text or ledger_text
    other_source = ledger_text or filing_text
    macro_evidence = _evidence(macro_raw.get("evidence"))
    if macro_evidence and not _in_ledger(macro_evidence, other_source):
        macro_evidence = ""
    rates_view = _clip_view(macro_raw.get("rates_view"), MACRO_VIEWS)
    fx_view = _clip_view(macro_raw.get("fx_demand_view"), MACRO_VIEWS)
    if not macro_evidence:
        if rates_view != "insufficient":
            rates_view = "insufficient"
        if fx_view != "insufficient":
            fx_view = "insufficient"
    return {
        "category_growth": _ground_block(
            category, view_key="view", allowed=CATEGORY_VIEWS, ledger_text=growth_source
        ),
        "pricing_power": _ground_block(
            pricing, view_key="view", allowed=PRICING_VIEWS, ledger_text=other_source
        ),
        "cycle": _ground_block(
            cycle, view_key="view", allowed=CYCLE_VIEWS, ledger_text=other_source
        ),
        "macro": {
            "rates_view": rates_view,
            "fx_demand_view": fx_view,
            "risk_free_rate": risk_free_rate,
            "evidence": macro_evidence,
        },
        "demand_inflection": _ground_block(
            inflection,
            view_key="direction",
            allowed=INFLECTION_VIEWS,
            ledger_text=growth_source,
        ),
        "narrative": _ground_narrative(
            raw.get("narrative"), other_source, allowed_tickers
        ),
    }


def _driver_lines(packet: Dict[str, Any]) -> List[str]:
    lines = []
    mapping = [
        ("category_growth", "Category growth", "view"),
        ("pricing_power", "Pricing power", "view"),
        ("cycle", "Cycle", "view"),
        ("demand_inflection", "Demand inflection", "direction"),
    ]
    for key, label, field in mapping:
        block = packet.get(key) or {}
        view = block.get(field) or "insufficient"
        evidence = block.get("evidence") or "no excerpt"
        lines.append(f"{label}: {view} ({evidence})")
    macro = packet.get("macro") or {}
    lines.append(
        f"Rates: {macro.get('rates_view') or 'insufficient'}; "
        f"FX/demand: {macro.get('fx_demand_view') or 'insufficient'}"
    )
    return lines


def industry_macro_node(state: EquityResearchState) -> Dict[str, Any]:
    """Write a structured industry/market/macro packet used by the architect."""
    ticker = str(state.get("ticker") or "").strip().upper()
    meta = (state.get("peer_metadata") or {}).get(ticker) or {}
    market = state.get("market_info") or {}
    sector = str(meta.get("sector") or market.get("sector") or "n/a")
    industry = str(meta.get("industry") or market.get("industry") or "n/a")
    cagr = calculate_revenue_cagr(state.get("income_statement") or {})
    consensus = state.get("consensus_growth") or {}
    risk_free_rate = fetch_ten_year_treasury_yield()
    peer_json = json.dumps(_peer_snapshot(state), default=str)
    filing_text = _filing_blob(state)
    excerpts = "\n".join(
        str(item.get("excerpt") or "")
        for item in (state.get("qualitative_evidence") or [])
        if isinstance(item, dict)
    )
    cagr_line = (
        f"Historical revenue CAGR is {cagr:.1%}" if cagr is not None else ""
    )
    treasury_line = f"10-year Treasury yield is {risk_free_rate:.2%}"
    # Qualitative prose is shown to the model but is not a grounding source.
    # Invented summary sentences must not unlock high-band growth.
    ledger_text = "\n".join(
        [
            filing_text,
            excerpts,
            peer_json,
            json.dumps(consensus, default=str),
            cagr_line,
            treasury_line,
            ticker,
            sector,
            industry,
        ]
    )
    allowed_tickers = {ticker}
    for symbol in (state.get("competitor_tickers") or []):
        if str(symbol).strip():
            allowed_tickers.add(str(symbol).strip().upper())
    snapshot = _peer_snapshot(state)
    for symbol in snapshot.get("competitors") or []:
        if str(symbol).strip():
            allowed_tickers.add(str(symbol).strip().upper())
    if snapshot.get("target"):
        allowed_tickers.add(str(snapshot["target"]).strip().upper())
    payload = chat_json(
        [
            {"role": "system", "content": INDUSTRY_MACRO_SYSTEM},
            {
                "role": "user",
                "content": INDUSTRY_MACRO_USER.format(
                    ticker=ticker,
                    sector=sector,
                    industry=industry,
                    historical_cagr=(
                        f"{cagr:.1%}" if cagr is not None else "n/a"
                    ),
                    consensus_json=json.dumps(consensus, default=str)[:2000],
                    risk_free_rate=f"{risk_free_rate:.2%}",
                    peer_json=peer_json[:8000],
                    qualitative=(state.get("qualitative_analysis_summary") or "")[:5000],
                    filing=filing_text[:8000],
                ),
            },
        ],
        timeout=90,
        required=True,
    )
    if not payload:
        raise LLMCallError("Industry/macro analyst did not return a JSON object.")
    packet = normalize_industry_macro_packet(
        payload,
        risk_free_rate=risk_free_rate,
        ledger_text=ledger_text,
        filing_text="\n".join(part for part in (filing_text, excerpts) if part),
        allowed_tickers=allowed_tickers,
    )
    narrative = packet.get("narrative") or "Industry and macro evidence was insufficient."
    body = (
        f"{ticker} industry/macro packet: category {packet['category_growth']['view']}, "
        f"cycle {packet['cycle']['view']}, inflection "
        f"{packet['demand_inflection']['direction']}."
    )
    messages = [
        make_message(
            INDUSTRY_MACRO,
            ARCHITECT,
            "industry_macro_packet",
            body,
            {"views": {key: packet[key] for key in packet if key != "narrative"}},
        ),
        make_message(
            INDUSTRY_MACRO,
            REVIEWER,
            "industry_macro_packet",
            body,
            {"views": packet["category_growth"]},
        ),
        make_message(
            INDUSTRY_MACRO,
            WRITER,
            "industry_macro_narrative",
            narrative,
            {"drivers": _driver_lines(packet)},
        ),
    ]
    logger.info(body)
    return {
        "industry_macro_packet": packet,
        "industry_outlook": narrative,
        "agent_messages": messages,
    }
