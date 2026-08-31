"""Industry, market, and macro analyst: structured demand packet, not DCF numbers."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from ..agents.quant import fetch_ten_year_treasury_yield
from ..graphs.desk import ARCHITECT, INDUSTRY_MACRO, REVIEWER, WRITER, make_message
from ..graphs.state import EquityResearchState
from ..prompts.desk import INDUSTRY_MACRO_SYSTEM, INDUSTRY_MACRO_USER
from ..tools.firm_classifier import (
    MATURE_GROWTH_REFERENCE,
    SCALEUP_CAGR,
    calculate_revenue_cagr,
)
from ..tools.web_research import (
    format_web_research,
    ledger_source_urls,
    web_research_blob,
)
from ..utils.grounding import (
    contains_web_link,
    extract_urls,
    has_blocked_link,
    strip_urls,
    url_on_ledger,
)
from ..utils.llm_client import LLMCallError, chat_json

logger = logging.getLogger("IndustryMacroAnalyst")

CATEGORY_VIEWS = {"above_history", "in_line", "below_history", "insufficient"}
PRICING_VIEWS = {"strong", "neutral", "weak", "insufficient"}
MIX_VIEWS = {"rising", "stable", "shifting", "insufficient"}
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
    "FTC",
    "DOJ",
    "FDA",
    "EPA",
    "IMF",
    "WTO",
    "ECB",
    "OECD",
}


def _clip_view(value: Any, allowed: set[str], default: str = "insufficient") -> str:
    label = str(value or "").strip().lower()
    return label if label in allowed else default


def _evidence(value: Any, limit: int = 400) -> str:
    text = strip_urls(" ".join(str(value or "").split()))
    return text[:limit]


def _claimed_url(block: Any) -> str:
    if not isinstance(block, dict):
        return ""
    claimed = str(block.get("source_url") or block.get("url") or "").strip()
    if claimed:
        return claimed
    urls = extract_urls(str(block.get("evidence") or ""))
    return urls[0] if urls else ""


def _resolve_source_url(
    evidence: str,
    claimed: str,
    catalog: Optional[List[Dict[str, Any]]] = None,
) -> str:
    rows = [row for row in (catalog or []) if isinstance(row, dict)]
    urls = [str(row.get("url") or "") for row in rows if row.get("url")]
    if claimed and url_on_ledger(claimed, urls):
        for row in rows:
            if url_on_ledger(claimed, [str(row.get("url") or "")]):
                return str(row.get("url") or "")
    if not evidence:
        return ""
    web_first = [row for row in rows if str(row.get("used_for") or "") != "filing"]
    filing_rows = [row for row in rows if str(row.get("used_for") or "") == "filing"]
    for row in web_first + filing_rows:
        excerpt = str(row.get("excerpt") or "")
        if _in_ledger(evidence, excerpt, min_len=24) and row.get("url"):
            return str(row.get("url") or "")
    return ""


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
    allowed_urls: Optional[Iterable[str]] = None,
) -> str:
    raw = " ".join(str(value or "").split())
    if has_blocked_link(raw, allowed_urls):
        return ""
    text = _evidence(raw, limit=1800)
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
    source_catalog: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, str]:
    view = _clip_view(block.get(view_key) or block.get("view"), allowed)
    evidence = _evidence(block.get("evidence"))
    if evidence and not _in_ledger(evidence, ledger_text):
        view = "insufficient"
        evidence = ""
    elif view != "insufficient" and not evidence:
        view = "insufficient"
    source_url = ""
    if evidence:
        source_url = _resolve_source_url(
            evidence, _claimed_url(block), source_catalog
        )
    payload = {view_key: view, "evidence": evidence}
    if source_url:
        payload["source_url"] = source_url
    return payload


def _filing_blob(state: EquityResearchState) -> str:
    sections = state.get("sec_filing_sections") or {}
    parts = [
        str(sections.get("item_1") or sections.get("Item 1") or ""),
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


def _source_catalog(
    state: Optional[EquityResearchState] = None,
    *,
    filing_text: str = "",
    filing_url: str = "",
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    url = filing_url or str(((state or {}).get("sec_filing_metadata") or {}).get("filing_url") or "")
    blob = filing_text
    if blob or url:
        rows.append(
            {
                "url": url,
                "title": "SEC 10-K",
                "publisher": "SEC EDGAR",
                "excerpt": blob,
                "tier": "first_party",
                "used_for": "filing",
            }
        )
    for doc in (state or {}).get("web_research") or []:
        if isinstance(doc, dict) and (doc.get("url") or doc.get("excerpt")):
            rows.append(doc)
    return rows


WATCH_LEVERS = {
    "high_growth_rate",
    "high_growth_years",
    "terminal_growth_rate",
    "terminal_margin",
    "sales_to_capital",
    "company_specific_risk_premium",
    "shares_outstanding",
    "price_target_12m",
}


def _ground_named_list(raw: Any, ledger_text: str, *, limit: int = 8) -> List[str]:
    """Keep short market/product names that actually appear in the ledger."""
    names: List[str] = []
    items = raw if isinstance(raw, list) else []
    for item in items:
        if isinstance(item, dict):
            text = _evidence(
                item.get("name") or item.get("label") or item.get("market") or ""
            )
        else:
            text = _evidence(item)
        if len(text) < 4 or contains_web_link(text):
            continue
        if not _in_ledger(text, ledger_text, min_len=4):
            continue
        if text not in names:
            names.append(text)
        if len(names) >= limit:
            break
    return names


def _ground_watch_items(
    raw: Any,
    ledger_text: str,
    *,
    limit: int = 8,
    source_catalog: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, str]]:
    """Undated watch items. Dates are never taken from the LLM."""
    rows: List[Dict[str, str]] = []
    items = raw if isinstance(raw, list) else []
    for item in items:
        if not isinstance(item, dict):
            continue
        evidence = _evidence(item.get("evidence"))
        event = _evidence(item.get("event") or item.get("name"), limit=160)
        if not evidence or not _in_ledger(evidence, ledger_text):
            continue
        lever = str(item.get("assumption") or item.get("lever") or "").strip()
        if lever not in WATCH_LEVERS:
            lever = ""
        row = {
            "event": event or evidence[:120],
            "evidence": evidence,
            "assumption": lever,
        }
        source_url = _resolve_source_url(evidence, _claimed_url(item), source_catalog)
        if source_url:
            row["source_url"] = source_url
        rows.append(row)
        if len(rows) >= limit:
            break
    return rows


def normalize_industry_macro_packet(
    payload: Optional[Dict[str, Any]],
    *,
    risk_free_rate: Optional[float],
    ledger_text: str = "",
    filing_text: str = "",
    allowed_tickers: Optional[Iterable[str]] = None,
    source_catalog: Optional[List[Dict[str, Any]]] = None,
    allowed_urls: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    """Keep categorical views; drop invented URLs, unsourced quotes, and DCF fields.

    High-band growth and demand-inflection views must be grounded in the 10-K
    or in Python-fetched allowlisted web excerpts — not qualitative prose or peer JSON.
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
    catalog = list(source_catalog or [])
    urls = list(allowed_urls or [])
    if not urls:
        urls = [str(row.get("url") or "") for row in catalog if row.get("url")]
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
    macro_url = (
        _resolve_source_url(macro_evidence, _claimed_url(macro_raw), catalog)
        if macro_evidence
        else ""
    )
    macro_block: Dict[str, Any] = {
        "rates_view": rates_view,
        "fx_demand_view": fx_view,
        "risk_free_rate": risk_free_rate,
        "evidence": macro_evidence,
    }
    if macro_url:
        macro_block["source_url"] = macro_url
    return {
        "category_growth": _ground_block(
            category,
            view_key="view",
            allowed=CATEGORY_VIEWS,
            ledger_text=growth_source,
            source_catalog=catalog,
        ),
        "pricing_power": _ground_block(
            pricing,
            view_key="view",
            allowed=PRICING_VIEWS,
            ledger_text=other_source,
            source_catalog=catalog,
        ),
        "cycle": _ground_block(
            cycle,
            view_key="view",
            allowed=CYCLE_VIEWS,
            ledger_text=other_source,
            source_catalog=catalog,
        ),
        "macro": macro_block,
        "demand_inflection": _ground_block(
            inflection,
            view_key="direction",
            allowed=INFLECTION_VIEWS,
            ledger_text=growth_source,
            source_catalog=catalog,
        ),
        "markets": _ground_named_list(raw.get("markets"), growth_source or other_source),
        "industry_catalysts": _ground_watch_items(
            raw.get("industry_catalysts") or raw.get("catalysts"),
            growth_source or other_source,
            source_catalog=catalog,
        ),
        "narrative": _ground_narrative(
            raw.get("narrative"),
            other_source,
            allowed_tickers,
            allowed_urls=urls,
        ),
    }


LEDGER_GROWTH_GAP = 0.02
PRICING_GAP_POINTS = 3.0
CYCLE_GROWTH_POINTS = 2.0


def _block_open(block: Any, field: str) -> bool:
    if not isinstance(block, dict):
        return True
    view = str(block.get(field) or "insufficient").strip().lower()
    evidence = str(block.get("evidence") or "").strip()
    return view == "insufficient" or not evidence


def _view(block: Any) -> str:
    if not isinstance(block, dict):
        return "insufficient"
    return str(block.get("view") or "insufficient").strip().lower()


def _forward_consensus_growth(consensus: Optional[Dict[str, Any]]) -> Optional[float]:
    if not isinstance(consensus, dict):
        return None
    source = str(consensus.get("source") or "").lower()
    if "trailing" in source:
        return None
    try:
        growth = float(consensus.get("growth"))
    except (TypeError, ValueError):
        return None
    if growth != growth:
        return None
    return growth


def _category_from_ledger(
    historical_cagr: Optional[float],
    consensus: Optional[Dict[str, Any]],
) -> Optional[Dict[str, str]]:
    if historical_cagr is None:
        return None
    forward = _forward_consensus_growth(consensus)
    if forward is None:
        if historical_cagr >= SCALEUP_CAGR:
            return {
                "view": "above_history",
                "evidence": (
                    f"Historical revenue CAGR is {historical_cagr:.1%}, well above "
                    f"a mature {MATURE_GROWTH_REFERENCE:.0%} run-rate, so category "
                    "growth is treated as a scale-up path versus a mature firm."
                ),
                "source": "ledger",
            }
        return {
            "view": "in_line",
            "evidence": (
                f"Historical revenue CAGR is {historical_cagr:.1%}; no forward "
                "consensus growth was available to classify category growth versus history."
            ),
            "source": "ledger",
        }
    gap = forward - historical_cagr
    if gap >= LEDGER_GROWTH_GAP:
        view = "above_history"
    elif gap <= -LEDGER_GROWTH_GAP:
        view = "below_history"
    else:
        view = "in_line"
    source = str((consensus or {}).get("source") or "consensus")
    return {
        "view": view,
        "evidence": (
            f"Forward {source} growth {forward:.1%} versus historical revenue "
            f"CAGR {historical_cagr:.1%}."
        ),
        "source": "ledger",
    }


def _margins_from_snapshot(
    snapshot: Optional[Dict[str, Any]],
) -> Tuple[Optional[float], Optional[float], Optional[str]]:
    snapshot = snapshot or {}
    metrics = snapshot.get("metrics") or {}
    target = str(snapshot.get("target") or "").strip().upper()
    target_row = metrics.get(target) if target else None
    target_margin = None
    if isinstance(target_row, dict):
        try:
            target_margin = float(target_row.get("operating_margin_pct"))
        except (TypeError, ValueError):
            target_margin = None
    peers: List[float] = []
    for symbol, row in metrics.items():
        if str(symbol).strip().upper() == target or not isinstance(row, dict):
            continue
        try:
            peers.append(float(row.get("operating_margin_pct")))
        except (TypeError, ValueError):
            continue
    if target_margin is None or not peers:
        return target_margin, None, target or None
    peers.sort()
    mid = peers[len(peers) // 2]
    return target_margin, mid, target or None


def _pricing_from_ledger(snapshot: Optional[Dict[str, Any]]) -> Optional[Dict[str, str]]:
    target_margin, peer_median, ticker = _margins_from_snapshot(snapshot)
    if target_margin is None or peer_median is None or not ticker:
        return None
    gap = target_margin - peer_median
    if gap >= PRICING_GAP_POINTS:
        view = "strong"
    elif gap <= -PRICING_GAP_POINTS:
        view = "weak"
    else:
        view = "neutral"
    return {
        "view": view,
        "evidence": (
            f"{ticker} operating margin {target_margin / 100.0:.1%} versus peer "
            f"median {peer_median / 100.0:.1%}."
        ),
        "source": "ledger",
    }


def _cycle_from_ledger(snapshot: Optional[Dict[str, Any]]) -> Optional[Dict[str, str]]:
    snapshot = snapshot or {}
    metrics = snapshot.get("metrics") or {}
    growths: List[float] = []
    for row in metrics.values():
        if not isinstance(row, dict):
            continue
        try:
            growths.append(float(row.get("revenue_growth_yoy_pct")))
        except (TypeError, ValueError):
            continue
    if not growths:
        return None
    growths.sort()
    median = growths[len(growths) // 2]
    if median >= CYCLE_GROWTH_POINTS:
        view = "upswing"
    elif median <= -CYCLE_GROWTH_POINTS:
        view = "downswing"
    else:
        view = "mid"
    return {
        "view": view,
        "evidence": (
            f"Peer and target trailing revenue growth median is {median:.1f} percent."
        ),
        "source": "ledger",
    }


def _macro_from_ledger(
    packet: Dict[str, Any],
    risk_free_rate: Optional[float],
) -> Dict[str, Any]:
    macro = dict(packet.get("macro") or {})
    if risk_free_rate is None:
        return macro
    evidence = str(macro.get("evidence") or "").strip()
    rates = str(macro.get("rates_view") or "insufficient").strip().lower()
    if rates == "insufficient" or not evidence:
        treasury = f"10-year Treasury yield is {risk_free_rate:.2%}"
        if not evidence:
            macro["evidence"] = treasury + "."
        elif treasury.lower() not in evidence.lower():
            macro["evidence"] = f"{evidence} {treasury}."
        if rates == "insufficient":
            macro["rates_view"] = "neutral"
        macro["source"] = "ledger"
    macro["risk_free_rate"] = risk_free_rate
    return macro


def overlay_ledger_industry_views(
    packet: Dict[str, Any],
    *,
    historical_cagr: Optional[float],
    consensus: Optional[Dict[str, Any]],
    peer_snapshot: Optional[Dict[str, Any]],
    risk_free_rate: Optional[float],
) -> Dict[str, Any]:
    """Fill views the 10-K quote filter left empty, using numbers already on the ledger.

    Does not invent tickers or DCF outputs. Stretch labels still require
    above-history or a positive inflection after this overlay.
    """
    updated = dict(packet or {})
    filled = _category_from_ledger(historical_cagr, consensus)
    if filled:
        current = updated.get("category_growth") or {}
        ledger_in_line = (
            _view(current) == "in_line"
            and str(current.get("source") or "") == "ledger"
            and filled.get("view") == "above_history"
        )
        if _block_open(current, "view") or ledger_in_line:
            updated["category_growth"] = filled
    if _block_open(updated.get("pricing_power"), "view"):
        filled = _pricing_from_ledger(peer_snapshot)
        if filled:
            updated["pricing_power"] = filled
    ledger_cycle = _cycle_from_ledger(peer_snapshot)
    if ledger_cycle:
        # Trailing peer/target growth owns the cycle. A fetched consumer
        # snippet about another ticker cannot call downswing while the
        # prints are still positive.
        updated["cycle"] = ledger_cycle
    updated["macro"] = _macro_from_ledger(updated, risk_free_rate)
    inflection = updated.get("demand_inflection") or {}
    category_view = str(
        (updated.get("category_growth") or {}).get("view") or "insufficient"
    )
    cycle_view = _view(updated.get("cycle"))
    inf_dir = str(
        inflection.get("direction") or inflection.get("view") or "insufficient"
    ).strip().lower()
    if _block_open(inflection, "direction"):
        updated["demand_inflection"] = {
            "direction": "none",
            "evidence": (
                "No 10-K excerpt established a demand inflection; category growth "
                f"is classified from the ledger as {category_view}."
            ),
            "source": "ledger",
        }
    elif (
        inf_dir == "negative"
        and cycle_view in {"mid", "upswing"}
        and category_view != "below_history"
    ):
        updated["demand_inflection"] = {
            "direction": "none",
            "evidence": (
                f"Peer/target trailing cycle is {cycle_view} and category growth "
                f"is {category_view}; that is not a demand break for this firm."
            ),
            "source": "ledger",
        }
    if not str(updated.get("narrative") or "").strip():
        bits = []
        for key, field in (
            ("category_growth", "evidence"),
            ("pricing_power", "evidence"),
            ("cycle", "evidence"),
        ):
            text = str((updated.get(key) or {}).get(field) or "").strip()
            if text:
                bits.append(text)
        macro_ev = str((updated.get("macro") or {}).get("evidence") or "").strip()
        if macro_ev:
            bits.append(macro_ev)
        updated["narrative"] = " ".join(bits)
    return updated


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
    web_blob = web_research_blob(state)
    web_prompt = format_web_research(state.get("web_research") or [])
    # Qualitative prose is shown to the model but is not a grounding source.
    # Invented summary sentences must not unlock high-band growth.
    # Allowlisted web excerpts may ground category growth and market names.
    ledger_text = "\n".join(
        [
            filing_text,
            excerpts,
            web_blob,
            peer_json,
            json.dumps(consensus, default=str),
            cagr_line,
            treasury_line,
            ticker,
            sector,
            industry,
            web_prompt,
        ]
    )
    growth_text = "\n".join(part for part in (filing_text, excerpts, web_blob) if part)
    catalog = _source_catalog(state, filing_text=growth_text)
    allowed_urls = ledger_source_urls(state)
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
                    web_research=web_prompt[:12000],
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
        filing_text=growth_text,
        allowed_tickers=allowed_tickers,
        source_catalog=catalog,
        allowed_urls=allowed_urls,
    )
    packet = overlay_ledger_industry_views(
        packet,
        historical_cagr=cagr,
        consensus=consensus if isinstance(consensus, dict) else None,
        peer_snapshot=snapshot,
        risk_free_rate=risk_free_rate,
    )
    if not packet.get("markets"):
        fallback = []
        for label in (industry, sector):
            text = " ".join(str(label or "").split())
            if text and text.lower() not in {"n/a", "none"} and len(text) >= 4:
                fallback.append(text)
        if fallback:
            packet["markets"] = fallback
            packet["markets_source"] = "yahoo_sector_industry"
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
