"""Dated catalysts mapped to model levers. Dates come from the ledger, not the LLM."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

_ISO_RE = re.compile(r"\b(20\d{2})-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])\b")
_US_RE = re.compile(
    r"\b(January|February|March|April|May|June|July|August|September|"
    r"October|November|December)\.? +([0-3]?\d),? +(20\d{2})\b",
    re.IGNORECASE,
)
_QTR_RE = re.compile(r"\b(Q[1-4])\s*(20\d{2})\b", re.IGNORECASE)
_MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}

# keyword substring → (assumption lever, what a print would test)
_FILING_RULES: Sequence[Tuple[Tuple[str, ...], str, str]] = (
    (
        ("share repurchase", "share repurchases", "stock repurchase", "buyback"),
        "shares_outstanding",
        "Buybacks change the per-share DCF; they are not a WACC input.",
    ),
    (
        ("special dividend", "dividend increase", "capital return program"),
        "price_target_12m",
        "The 12-month PT subtracts indicated DPS from FV × (1+Ke).",
    ),
    (
        ("restructuring", "store closures", "impairment"),
        "terminal_margin",
        "Hits the EBIT-margin path and terminal margin.",
    ),
    (
        ("tariff", "trade restriction", "import duty"),
        "high_growth_rate",
        "Hits category demand and the explicit growth rate.",
    ),
    (
        ("acquisition", "merger agreement", "divestiture"),
        "sales_to_capital",
        "Hits invested capital and FCFF reinvestment.",
    ),
    (
        ("working capital", "inventory build", "accounts receivable"),
        "sales_to_capital",
        "NWC sits inside sales-to-capital / FCFF reinvestment.",
    ),
    (
        ("litigation", "class action", "federal trade commission", "department of justice"),
        "company_specific_risk_premium",
        "Hits the company-specific premium on cost of equity if the desk accepted it.",
    ),
    (
        ("capital expenditure", "distribution center", "capacity expansion"),
        "sales_to_capital",
        "Hits reinvestment intensity (sales-to-capital).",
    ),
    (
        ("net sales growth", "comparable store", "same-store"),
        "high_growth_rate",
        "Hits the near-term sales path.",
    ),
)


def _parse_unix(value: Any) -> Optional[date]:
    try:
        ts = float(value)
    except (TypeError, ValueError):
        return None
    if ts > 1e12:
        ts /= 1000.0
    if ts <= 0:
        return None
    try:
        return datetime.fromtimestamp(ts, tz=timezone.utc).date()
    except (OSError, OverflowError, ValueError):
        return None


def _parse_date_value(value: Any) -> Optional[date]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    parsed = _parse_unix(value)
    if parsed:
        return parsed
    text = str(value).strip()[:32]
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


_DATE_WINDOW = 200


def _date_spans(text: str) -> List[Tuple[int, date, str, str]]:
    """Return (char_index, parsed, raw, kind) in the order matches appear."""
    spans: List[Tuple[int, date, str, str]] = []
    blob = text or ""
    for match in _ISO_RE.finditer(blob):
        try:
            spans.append((match.start(), date.fromisoformat(match.group(0)), match.group(0), "iso"))
        except ValueError:
            continue
    for match in _US_RE.finditer(blob):
        month = _MONTHS.get(match.group(1).lower().rstrip("."))
        if not month:
            continue
        try:
            parsed = date(int(match.group(3)), month, int(match.group(2)))
        except ValueError:
            continue
        spans.append((match.start(), parsed, match.group(0), "us"))
    for match in _QTR_RE.finditer(blob):
        quarter = int(match.group(1)[1])
        year = int(match.group(2))
        month = min(12, quarter * 3)
        spans.append((match.start(), date(year, month, 1), match.group(0), "quarter"))
    spans.sort(key=lambda item: item[0])
    return spans


def _best_date_near(
    text: str,
    index: int,
    *,
    window: int = _DATE_WINDOW,
    min_date: Optional[date] = None,
) -> Optional[Tuple[date, str]]:
    nearby = [
        item
        for item in _date_spans(text)
        if abs(item[0] - index) <= window
    ]
    if min_date is not None:
        nearby = [item for item in nearby if item[1] >= min_date]
    if not nearby:
        return None
    full = [item for item in nearby if item[3] != "quarter"]
    pool = full or nearby
    best = min(pool, key=lambda item: abs(item[0] - index))
    return best[1], best[2]


def extract_market_events(info: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Yahoo snapshot dates only. No invented calendar."""
    info = info or {}
    events: List[Dict[str, Any]] = []
    start = _parse_date_value(
        info.get("earningsTimestampStart") or info.get("earningsCallTimestampStart")
    )
    end = _parse_date_value(
        info.get("earningsTimestampEnd") or info.get("earningsCallTimestampEnd")
    )
    point = _parse_date_value(info.get("earningsTimestamp"))
    earnings = start or point or end
    if earnings:
        label = "Next earnings"
        if end and start and start != end:
            detail = f"{start.isoformat()} to {end.isoformat()}"
        else:
            detail = earnings.isoformat()
        events.append(
            {
                "date": earnings.isoformat(),
                "date_label": detail,
                "sort_key": earnings.isoformat(),
                "event": label,
                "scope": "firm",
                "source": "Yahoo Finance",
                "assumption": "high_growth_rate, terminal_margin",
                "model_impact": (
                    "The print tests whether Year-1 revenue, EBIT margin, and "
                    "model EPS are on the accepted path."
                ),
                "evidence": "",
            }
        )
    ex_div = _parse_date_value(info.get("exDividendDate") or info.get("dividendDate"))
    if ex_div:
        events.append(
            {
                "date": ex_div.isoformat(),
                "date_label": ex_div.isoformat(),
                "sort_key": ex_div.isoformat(),
                "event": "Ex-dividend / indicated dividend",
                "scope": "firm",
                "source": "Yahoo Finance",
                "assumption": "price_target_12m",
                "model_impact": (
                    "The 12-month price target subtracts indicated DPS from "
                    "fair value rolled forward at the cost of equity."
                ),
                "evidence": "",
            }
        )
    return events


def _filing_catalysts(
    state: Dict[str, Any],
    *,
    today: Optional[date] = None,
) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    today = today or date.today()
    metadata = state.get("sec_filing_metadata") or {}
    filed = _parse_date_value(metadata.get("filing_date"))
    if filed:
        events.append(
            {
                "date": filed.isoformat(),
                "date_label": filed.isoformat(),
                "sort_key": filed.isoformat(),
                "event": "Latest 10-K filed",
                "scope": "firm",
                "source": "SEC EDGAR",
                "assumption": "sales_to_capital, operations",
                "model_impact": (
                    "Locks the audited statements used for CCC, NWC, "
                    "sales-to-capital, and the operating baseline."
                ),
                "evidence": str(metadata.get("accession_number") or ""),
            }
        )

    evidence = state.get("qualitative_evidence") or []
    snippets: List[Tuple[str, str]] = []
    for item in evidence:
        if not isinstance(item, dict):
            continue
        excerpt = str(item.get("excerpt") or "").strip()
        if excerpt:
            snippets.append((str(item.get("section") or "10-K"), excerpt))
    # Do not scan raw Item 1A/7 dumps: boilerplate "working capital" plus a
    # fiscal year-end date would mint a fake catalyst.

    seen = set()
    for section, text in snippets:
        haystack = text.lower()
        for keywords, lever, impact in _FILING_RULES:
            matched = None
            idx = None
            for token in keywords:
                pos = haystack.find(token)
                if pos < 0:
                    continue
                if idx is None or pos < idx:
                    idx = pos
                    matched = token
            if matched is None or idx is None:
                continue
            center = idx + max(len(matched), 1) // 2
            nearby = _best_date_near(text, center, min_date=today)
            if nearby is None:
                continue
            parsed, raw = nearby
            key = (parsed.isoformat(), lever, matched)
            if key in seen:
                continue
            seen.add(key)
            start = max(0, idx - 80)
            snippet = " ".join(text[start : start + 220].split())
            events.append(
                {
                    "date": parsed.isoformat(),
                    "date_label": raw,
                    "sort_key": parsed.isoformat(),
                    "event": f"{matched.title()} ({section})",
                    "scope": "industry" if lever == "high_growth_rate" else "firm",
                    "source": "10-K excerpt on the ledger",
                    "assumption": lever,
                    "model_impact": impact,
                    "evidence": snippet,
                }
            )
    return events


def build_catalyst_register(
    state: Dict[str, Any],
    *,
    today: Optional[date] = None,
) -> List[Dict[str, Any]]:
    """Combine Yahoo calendar dates with dated 10-K language. No invented dates."""
    today = today or date.today()
    horizon = today + timedelta(days=550)
    floor = today - timedelta(days=400)
    events = list(state.get("event_calendar") or [])
    if not events:
        events.extend(extract_market_events(state.get("market_info") or {}))
    events.extend(_filing_catalysts(state, today=today))

    kept: List[Dict[str, Any]] = []
    seen = set()
    for event in events:
        if not isinstance(event, dict):
            continue
        parsed = _parse_date_value(event.get("date") or event.get("sort_key"))
        if parsed is None or parsed < floor or parsed > horizon:
            continue
        row = dict(event)
        name = str(row.get("event") or "")
        if name.lower().startswith("next earnings") and parsed < today:
            row["event"] = "Last earnings"
        stamp = (
            parsed.isoformat(),
            str(row.get("event") or ""),
            str(row.get("assumption") or ""),
        )
        if stamp in seen:
            continue
        seen.add(stamp)
        row["date"] = parsed.isoformat()
        row["sort_key"] = parsed.isoformat()
        if parsed < today:
            row["timing"] = "recent"
        else:
            row["timing"] = "upcoming"
        kept.append(row)
    kept.sort(key=lambda item: str(item.get("sort_key") or ""))
    return kept[:12]
