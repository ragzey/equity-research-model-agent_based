"""SEC companyfacts XBRL fallback when Yahoo statements are empty."""

from __future__ import annotations

import logging
from datetime import date
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .cache import TTL_SEC, cache_get, cache_set
from .sec_api import _sec_get, get_cik_for_ticker

logger = logging.getLogger("SECFacts")

INCOME_CONCEPTS: Dict[str, Tuple[str, ...]] = {
    "Total Revenue": (
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
    ),
    "Operating Income": ("OperatingIncomeLoss",),
    "Net Income": ("NetIncomeLoss",),
    "Interest Expense": ("InterestExpense", "InterestExpenseDebt"),
    "Diluted EPS": ("EarningsPerShareDiluted",),
    "Cost Of Revenue": (
        "CostOfGoodsAndServicesSold",
        "CostOfRevenue",
        "CostOfGoodsSold",
    ),
}

BALANCE_CONCEPTS: Dict[str, Tuple[str, ...]] = {
    "Accounts Receivable": ("AccountsReceivableNetCurrent",),
    "Inventory": ("InventoryNet",),
    "Accounts Payable": ("AccountsPayableCurrent",),
    "Net PPE": ("PropertyPlantAndEquipmentNet",),
    "Cash And Cash Equivalents": (
        "CashAndCashEquivalentsAtCarryingValue",
        "CashCashEquivalentsAndShortTermInvestments",
    ),
    "Current Debt": (
        "LongTermDebtCurrent",
        "CommercialPaper",
        "ShortTermBorrowings",
    ),
    "Long Term Debt": (
        "LongTermDebtNoncurrent",
        "LongTermDebt",
        "LongTermDebtAndCapitalLeaseObligations",
    ),
}

CASHFLOW_CONCEPTS: Dict[str, Tuple[str, ...]] = {
    "Operating Cash Flow": ("NetCashProvidedByUsedInOperatingActivities",),
    "Depreciation": (
        "DepreciationDepletionAndAmortization",
        "Depreciation",
    ),
    "Capital Expenditure": (
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsToAcquireProductiveAssets",
    ),
}

_USD_UNIT_KEYS = ("USD", "usd")
_SHARE_UNIT_KEYS = ("USD/shares", "USD/share", "pure")


def _annual_points(fact: Any, unit_keys: Sequence[str] = _USD_UNIT_KEYS) -> Dict[int, float]:
    if not isinstance(fact, dict):
        return {}
    units = fact.get("units") if isinstance(fact.get("units"), dict) else {}
    rows: List[Dict[str, Any]] = []
    for key in unit_keys:
        payload = units.get(key)
        if isinstance(payload, list):
            rows.extend(item for item in payload if isinstance(item, dict))
    if not rows:
        for payload in units.values():
            if isinstance(payload, list):
                rows.extend(item for item in payload if isinstance(item, dict))
    by_fy: Dict[int, Tuple[str, float]] = {}
    for row in rows:
        form = str(row.get("form") or "")
        if form not in {"10-K", "10-K/A"}:
            continue
        if str(row.get("fp") or "").upper() not in {"", "FY"}:
            continue
        try:
            fy = int(row.get("fy"))
            value = float(row.get("val"))
        except (TypeError, ValueError):
            continue
        if value != value:
            continue
        filed = str(row.get("filed") or "")
        previous = by_fy.get(fy)
        if previous is None or filed >= previous[0]:
            by_fy[fy] = (filed, value)
    return {fy: pair[1] for fy, pair in by_fy.items()}


def _best_series(
    gaap: Dict[str, Any],
    names: Sequence[str],
    unit_keys: Sequence[str] = _USD_UNIT_KEYS,
) -> Dict[int, float]:
    best: Dict[int, float] = {}
    for name in names:
        series = _annual_points(gaap.get(name), unit_keys=unit_keys)
        if len(series) > len(best):
            best = series
    return best


def _to_period_map(series: Dict[int, float], years: Iterable[int]) -> Dict[date, float]:
    return {date(int(year), 12, 31): series[year] for year in years if year in series}


def _statement_from_concepts(
    gaap: Dict[str, Any],
    concepts: Dict[str, Tuple[str, ...]],
    years: Sequence[int],
    unit_keys: Sequence[str] = _USD_UNIT_KEYS,
) -> Dict[str, Dict[date, float]]:
    statement: Dict[str, Dict[date, float]] = {}
    for label, names in concepts.items():
        series = _best_series(gaap, names, unit_keys=unit_keys)
        mapped = _to_period_map(series, years)
        if mapped:
            statement[label] = mapped
    return statement


def _merge_debt(balance: Dict[str, Dict[date, float]]) -> None:
    current = balance.get("Current Debt") or {}
    long_term = balance.get("Long Term Debt") or {}
    periods = set(current) | set(long_term)
    if not periods:
        return
    total: Dict[date, float] = {}
    for period in periods:
        total[period] = float(current.get(period) or 0.0) + float(long_term.get(period) or 0.0)
    balance["Total Debt"] = total


def statements_from_companyfacts(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Turn a companyfacts JSON blob into Yahoo-like metric-major statements."""
    facts = payload.get("facts") if isinstance(payload, dict) else None
    if not isinstance(facts, dict):
        return None
    gaap = facts.get("us-gaap") if isinstance(facts.get("us-gaap"), dict) else {}
    if not gaap:
        return None
    revenue = _best_series(gaap, INCOME_CONCEPTS["Total Revenue"])
    ebit = _best_series(gaap, INCOME_CONCEPTS["Operating Income"])
    years = sorted(set(revenue) & set(ebit))[-4:]
    if len(years) < 1:
        years = sorted(revenue)[-4:]
    if not years:
        return None
    income = _statement_from_concepts(gaap, INCOME_CONCEPTS, years)
    if "Diluted EPS" not in income:
        eps = _best_series(
            gaap,
            INCOME_CONCEPTS["Diluted EPS"],
            unit_keys=_SHARE_UNIT_KEYS,
        )
        mapped = _to_period_map(eps, years)
        if mapped:
            income["Diluted EPS"] = mapped
    if "Total Revenue" not in income:
        return None
    balance = _statement_from_concepts(gaap, BALANCE_CONCEPTS, years)
    _merge_debt(balance)
    cash_flow = _statement_from_concepts(gaap, CASHFLOW_CONCEPTS, years)
    if not balance:
        return None
    return {
        "income_statement": income,
        "balance_sheet": balance,
        "cash_flow_statement": cash_flow,
        "statement_source": "sec_companyfacts",
        "fiscal_years": years,
    }


def fetch_companyfacts_statements(ticker: str) -> Optional[Dict[str, Any]]:
    """Download XBRL companyfacts and return statement dicts for the DCF ledger."""
    cik = get_cik_for_ticker(ticker)
    if not cik:
        logger.warning("No CIK for %s; cannot use SEC companyfacts fallback.", ticker)
        return None
    cached = cache_get("sec_facts_statements", cik, TTL_SEC)
    if isinstance(cached, dict) and cached.get("income_statement"):
        logger.info("Using cached SEC companyfacts statements for CIK %s", cik)
        return cached
    try:
        payload = cache_get("sec_companyfacts", cik, TTL_SEC)
        if not isinstance(payload, dict):
            url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
            logger.info("Fetching SEC companyfacts from %s", url)
            payload = _sec_get(url).json()
            if isinstance(payload, dict):
                cache_set("sec_companyfacts", cik, payload)
        statements = statements_from_companyfacts(payload if isinstance(payload, dict) else {})
        if not statements:
            logger.error("SEC companyfacts for CIK %s did not yield usable statements.", cik)
            return None
        cache_set("sec_facts_statements", cik, statements)
        logger.info(
            "SEC companyfacts fallback produced statements for %s | years %s",
            ticker,
            statements.get("fiscal_years"),
        )
        return statements
    except Exception:
        logger.exception("SEC companyfacts fallback failed for %s", ticker)
        return None
