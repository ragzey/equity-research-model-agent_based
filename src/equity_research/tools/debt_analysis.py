"""Cost of debt: TRACE/YTM interpolation first, Damodaran synthetic rating as fallback."""

import json
import logging
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

from .cache import TTL_DAMODARAN, cache_get, cache_set

logger = logging.getLogger("DebtAnalysisTool")

PROJECT_ROOT = Path(__file__).resolve().parents[3]
BUNDLED_SPREADS_PATH = PROJECT_ROOT / "data" / "damodaran_spreads.json"
DAMODARAN_RATINGS_URL = (
    "https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/ratings.html"
)

HARDCODED_BUCKETS: List[Dict[str, Any]] = [
    {"gt": 8.5, "lte": None, "rating": "AAA", "spread": 0.0069},
    {"gt": 6.5, "lte": 8.5, "rating": "AA", "spread": 0.0085},
    {"gt": 5.5, "lte": 6.5, "rating": "A+", "spread": 0.0107},
    {"gt": 4.25, "lte": 5.5, "rating": "A", "spread": 0.0121},
    {"gt": 3.0, "lte": 4.25, "rating": "A-", "spread": 0.0143},
    {"gt": 2.5, "lte": 3.0, "rating": "BBB", "spread": 0.0182},
    {"gt": 2.25, "lte": 2.5, "rating": "BB+", "spread": 0.0228},
    {"gt": 2.0, "lte": 2.25, "rating": "BB", "spread": 0.0275},
    {"gt": 1.75, "lte": 2.0, "rating": "B+", "spread": 0.0387},
    {"gt": 1.5, "lte": 1.75, "rating": "B", "spread": 0.0514},
    {"gt": 1.25, "lte": 1.5, "rating": "B-", "spread": 0.0652},
    {"gt": 0.8, "lte": 1.25, "rating": "CCC", "spread": 0.0880},
    {"gt": 0.5, "lte": 0.8, "rating": "CC", "spread": 0.1150},
    {"gt": None, "lte": 0.5, "rating": "D", "spread": 0.1500},
]
HARDCODED_TABLE = {
    "as_of": "2026-01-05",
    "source": "Hardcoded Damodaran large-cap non-financial snapshot.",
    "source_url": DAMODARAN_RATINGS_URL,
    "firm_class": "large_cap_nonfinancial",
    "buckets": HARDCODED_BUCKETS,
}

EBIT_LABELS = ("ebit", "operating income", "operatingincome")
INTEREST_LABELS = (
    "interest expense",
    "interestexpense",
    "interest expense non operating",
    "interest expense, net",
    "interest expense net of interest income",
)


def _valid_spread_table(payload: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(payload, dict):
        return None
    buckets = payload.get("buckets")
    if not isinstance(buckets, list) or len(buckets) < 8:
        return None
    ratings = []
    for bucket in buckets:
        if not isinstance(bucket, dict):
            return None
        if bucket.get("rating") is None or bucket.get("spread") is None:
            return None
        ratings.append(str(bucket["rating"]).upper())
    if "AAA" not in ratings or "D" not in ratings:
        return None
    return payload


def _load_bundled_spread_table() -> Optional[Dict[str, Any]]:
    try:
        payload = json.loads(BUNDLED_SPREADS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.exception("Could not read bundled Damodaran spread file.")
        return None
    return _valid_spread_table(payload)


def refresh_damodaran_spreads() -> Optional[Dict[str, Any]]:
    """
    Optional refresh of Damodaran's published coverage table.

    A failed or incomplete parse leaves the dated snapshot in place.
    Enable with DAMODARAN_REFRESH=1.
    """
    flag = os.getenv("DAMODARAN_REFRESH", "").strip().lower()
    if flag not in {"1", "true", "yes"}:
        return None
    try:
        response = requests.get(DAMODARAN_RATINGS_URL, timeout=30)
        response.raise_for_status()
        text = re.sub(r"<[^>]+>", " ", response.text)
        text = re.sub(r"\s+", " ", text)
        matches = re.findall(
            r"(AAA|AA|A\+|A-|A|BBB|BB\+|BB|B\+|B-|B|CCC|CC|D)"
            r"[^0-9%]{0,40}"
            r"([0-9]+\.[0-9]+)\s*%",
            text,
            flags=re.IGNORECASE,
        )
        seen = {}
        for rating, spread_pct in matches:
            rating = rating.upper()
            if rating not in seen:
                seen[rating] = float(spread_pct) / 100.0
        if "AAA" not in seen or "D" not in seen or len(seen) < 8:
            logger.warning("Damodaran HTML refresh parsed too few ratings; keeping snapshot.")
            return None
        bundled = _load_bundled_spread_table() or HARDCODED_TABLE
        refreshed_buckets = []
        for bucket in bundled["buckets"]:
            rating = str(bucket["rating"]).upper()
            spread = seen.get(rating, bucket["spread"])
            refreshed_buckets.append({**bucket, "spread": spread, "rating": rating})
        table = {
            "as_of": datetime_utc_date(),
            "source": "Damodaran ratings.html refresh; coverage buckets retained from dated snapshot.",
            "source_url": DAMODARAN_RATINGS_URL,
            "firm_class": "large_cap_nonfinancial",
            "buckets": refreshed_buckets,
        }
        if not _valid_spread_table(table):
            return None
        cache_set("damodaran", "spreads", table)
        logger.info("Cached Damodaran spread refresh as-of %s", table["as_of"])
        return table
    except Exception:
        logger.exception("Damodaran HTML refresh failed; keeping dated snapshot.")
        return None


def datetime_utc_date() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).date().isoformat()


@lru_cache(maxsize=1)
def load_damodaran_spread_table() -> Dict[str, Any]:
    """Load dated Damodaran spreads; HTML scrape is never the source of truth."""
    refreshed = refresh_damodaran_spreads()
    if refreshed:
        return refreshed
    cached = _valid_spread_table(cache_get("damodaran", "spreads", TTL_DAMODARAN))
    if cached:
        return cached
    bundled = _load_bundled_spread_table()
    if bundled:
        return bundled
    logger.warning("Using hardcoded Damodaran spread fallback.")
    return HARDCODED_TABLE


def _match_spread_bucket(coverage_ratio: float, buckets: List[Dict[str, Any]]) -> Tuple[str, float]:
    for bucket in buckets:
        lower = bucket.get("gt")
        upper = bucket.get("lte")
        if lower is not None and not coverage_ratio > float(lower):
            continue
        if upper is not None and not coverage_ratio <= float(upper):
            continue
        return str(bucket["rating"]), float(bucket["spread"])
    return "D", 0.1500


def get_synthetic_spread(coverage_ratio: float) -> Tuple[str, float]:
    """
    Maps an Interest Coverage Ratio to a Synthetic Credit Rating and Default Spread.
    Source: dated Damodaran large-cap non-financial coverage buckets.
    Returns: (Credit Rating, Default Spread as decimal)
    """
    table = load_damodaran_spread_table()
    return _match_spread_bucket(float(coverage_ratio), table["buckets"])


def _normalize_key(key: Any) -> str:
    return str(key).strip().lower()


def _latest_numeric(dates: Dict[Any, Any]) -> Optional[float]:
    if not dates:
        return None
    try:
        latest = max(dates.keys())
    except TypeError:
        latest = sorted((str(k) for k in dates.keys()), reverse=True)[0]
        for key in dates:
            if str(key) == latest:
                latest = key
                break
    value = dates.get(latest)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _is_period_major(income_statement: Dict[str, Any]) -> bool:
    """Yahoo `DataFrame.to_dict()` is {period: {line_item: value}}."""
    first_val = next(iter(income_statement.values()), None)
    if not isinstance(first_val, dict) or not first_val:
        return False
    nested = {_normalize_key(k) for k in first_val.keys()}
    return any(
        any(token in key for token in ("ebit", "interest", "income", "revenue"))
        for key in nested
    )


def extract_ebit_and_interest(
    income_statement: Optional[Dict[str, Any]],
) -> Tuple[Optional[float], Optional[float]]:
    """
    Parse EBIT and Interest Expense from the ledger income statement.
    Supports both Yahoo period-major dicts and metric-major dicts.
    Interest is returned as an absolute (positive) burden.
    """
    if not income_statement:
        return None, None

    ebit: Optional[float] = None
    interest_expense: Optional[float] = None

    if _is_period_major(income_statement):
        try:
            latest_period = max(income_statement.keys())
        except TypeError:
            latest_period = next(iter(income_statement))
        items = income_statement.get(latest_period) or {}
        if isinstance(items, dict):
            for key, value in items.items():
                nk = _normalize_key(key)
                if ebit is None and nk in EBIT_LABELS:
                    try:
                        ebit = float(value)
                    except (TypeError, ValueError):
                        pass
                if interest_expense is None and nk in INTEREST_LABELS:
                    try:
                        interest_expense = abs(float(value))
                    except (TypeError, ValueError):
                        pass
    else:
        for key, dates in income_statement.items():
            nk = _normalize_key(key)
            if not isinstance(dates, dict):
                continue
            if ebit is None and nk in EBIT_LABELS:
                ebit = _latest_numeric(dates)
            if interest_expense is None and nk in INTEREST_LABELS:
                extracted = _latest_numeric(dates)
                if extracted is not None:
                    interest_expense = abs(extracted)

    logger.info("Extracted from ledger: EBIT = %s, Interest Expense = %s", ebit, interest_expense)
    return ebit, interest_expense


def _bond_maturity(bond: Dict[str, float]) -> float:
    if "maturity_years" in bond:
        return float(bond["maturity_years"])
    return float(bond["years_to_maturity"])


def _bond_ytm(bond: Dict[str, float]) -> float:
    if "ytm" in bond:
        return float(bond["ytm"])
    return float(bond["yield"])


def interpolate_bond_yields(
    outstanding_bonds: List[Dict[str, float]],
    target_maturity: float = 10.0,
) -> Optional[float]:
    """
    Linear interpolation of liquid-bond YTMs to a target maturity (default 10 years).
    Accepts {maturity_years, ytm} or {years_to_maturity, yield}. Coupons are not valid input.
    """
    if not outstanding_bonds:
        return None

    normalized: List[Tuple[float, float]] = []
    for bond in outstanding_bonds:
        try:
            normalized.append((_bond_maturity(bond), _bond_ytm(bond)))
        except (KeyError, TypeError, ValueError):
            logger.warning("Skipping malformed bond quote: %s", bond)

    if not normalized:
        return None

    normalized.sort(key=lambda pair: pair[0])
    maturities = [pair[0] for pair in normalized]
    yields = [pair[1] for pair in normalized]

    for maturity, ytm in normalized:
        if abs(maturity - target_maturity) < 0.01:
            logger.info("Exact maturity match found for target %s years.", target_maturity)
            return ytm

    if len(normalized) == 1:
        logger.warning(
            "Only one outstanding bond found with maturity %s yrs and YTM %.2f%%. Using as proxy.",
            maturities[0],
            yields[0] * 100,
        )
        return yields[0]

    if target_maturity < maturities[0]:
        logger.warning(
            "Target maturity (%s yrs) is shorter than shortest bond (%s yrs). Using shortest yield.",
            target_maturity,
            maturities[0],
        )
        return yields[0]

    if target_maturity > maturities[-1]:
        logger.warning(
            "Target maturity (%s yrs) is longer than longest bond (%s yrs). Extrapolating linearly.",
            target_maturity,
            maturities[-1],
        )
        denom = maturities[-1] - maturities[-2]
        if denom == 0:
            return yields[-1]
        slope = (yields[-1] - yields[-2]) / denom
        extrapolated = yields[-1] + slope * (target_maturity - maturities[-1])
        return max(0.0, float(extrapolated))

    idx = 0
    while idx < len(maturities) and maturities[idx] < target_maturity:
        idx += 1
    t1, t2 = maturities[idx - 1], maturities[idx]
    y1, y2 = yields[idx - 1], yields[idx]
    interpolated_yield = y1 + (target_maturity - t1) / (t2 - t1) * (y2 - y1)
    logger.info(
        "Interpolated pre-tax yield for %s-year horizon: %.2f%%",
        target_maturity,
        interpolated_yield * 100,
    )
    return float(interpolated_yield)


def calculate_cost_of_debt(
    ebit: Optional[float] = None,
    interest_expense: Optional[float] = None,
    outstanding_bonds: Optional[List[Dict[str, float]]] = None,
    target_maturity: float = 10.0,
    risk_free_rate: float = 0.042,
    marginal_tax_rate: float = 0.21,
    target_horizon_years: Optional[float] = None,
    effective_tax_rate: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Hybrid cost of debt:
    1. Gold standard: structured market YTMs only (TRACE / bond API). No coupons, no web search.
    2. Fallback: Damodaran ICR using EBIT and abs(interest), plus live 10Y Treasury.
    Tax shield uses the statutory marginal rate (US 21% default), not a noisy effective rate.
    """
    horizon = target_horizon_years if target_horizon_years is not None else target_maturity
    tax_rate = effective_tax_rate if effective_tax_rate is not None else marginal_tax_rate

    method_used = "Synthetic Credit Rating (Fallback)"
    pre_tax_cost: Optional[float] = None
    rating = "N/A"
    coverage_ratio: Optional[float] = None
    default_spread: Optional[float] = None

    if outstanding_bonds:
        interpolated_rate = interpolate_bond_yields(outstanding_bonds, horizon)
        if interpolated_rate is not None:
            pre_tax_cost = interpolated_rate
            method_used = "Linear Interpolation (Market-Implied)"
            logger.info("Successfully calculated Cost of Debt using market-implied bond interpolation.")

    if pre_tax_cost is None:
        if ebit is None or interest_expense is None:
            raise ValueError(
                "Insufficient financial inputs. Provide structured outstanding bond YTMs "
                "or both EBIT and Interest Expense for the synthetic rating fallback."
            )

        abs_interest = abs(interest_expense)
        if abs_interest <= 0:
            logger.info("Interest expense is zero. Firm has a near-zero cash interest burden.")
            coverage_ratio = 999.0
        else:
            coverage_ratio = ebit / abs_interest

        rating, default_spread = get_synthetic_spread(coverage_ratio)
        pre_tax_cost = risk_free_rate + default_spread
        spread_table = load_damodaran_spread_table()
        logger.info(
            "Using synthetic fallback: Coverage Ratio = %.2f -> Rating: %s (spreads as-of %s)",
            coverage_ratio,
            rating,
            spread_table.get("as_of"),
        )

    after_tax_cost = pre_tax_cost * (1 - tax_rate)
    logger.info("Cost of Debt calculations complete. After-Tax Cost: %.2f%%", after_tax_cost * 100)

    result: Dict[str, Any] = {
        "method_used": method_used,
        "pre_tax_cost_of_debt": round(pre_tax_cost, 6),
        "after_tax_cost_of_debt": round(after_tax_cost, 6),
        "marginal_tax_rate_applied": tax_rate,
        "details": {},
    }

    if method_used == "Linear Interpolation (Market-Implied)":
        result["details"] = {
            "target_maturity_years": horizon,
            "bonds_analyzed_count": len(outstanding_bonds or []),
        }
    else:
        result["details"] = {
            "interest_coverage_ratio": None if coverage_ratio is None else round(coverage_ratio, 2),
            "synthetic_rating": rating,
            "implied_default_spread": default_spread,
            "risk_free_rate_applied": risk_free_rate,
            "damodaran_spreads_as_of": load_damodaran_spread_table().get("as_of"),
            "damodaran_spreads_source": load_damodaran_spread_table().get("source"),
        }

    return result
