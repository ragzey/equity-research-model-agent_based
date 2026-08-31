"""Transparent lifecycle heuristics for parameterizing the DCF model."""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("FirmClassifier")

REVENUE_LABELS = ("total revenue", "revenue")
EBIT_LABELS = ("ebit", "operating income", "operatingincome")
NET_INCOME_LABELS = (
    "net income",
    "net income common stockholders",
    "net income applicable to common shares",
    "netincome",
)
INTEREST_LABELS = (
    "interest expense",
    "interestexpense",
    "interest expense non operating",
    "interest expense, net",
    "interest expense net of interest income",
)
EPS_LABELS = (
    "diluted eps",
    "diluted earnings per share",
    "dilutedeps",
    "basic eps",
    "basic earnings per share",
    "basiceps",
)
UNSUPPORTED_FCFF_SECTORS = {"financial services", "financials"}

# Scale-up: last year's P&L is not the firm the market is pricing.
SCALEUP_PS_THRESHOLD = 15.0
SCALEUP_CAGR = 0.25
MATURE_GROWTH_REFERENCE = 0.07
MIN_HIGH_GROWTH_YEARS = 2
MAX_HIGH_GROWTH_YEARS = 10
# Base clip for the scale-up classifier rate. Stretch (menu "high") can go
# to SCALEUP_STRETCH_RATE when the growth-path packet says still_ramping.
SCALEUP_BASE_CAP = 0.50
SCALEUP_STRETCH_RATE = 0.80
MAX_HIGH_GROWTH_RATE = SCALEUP_STRETCH_RATE
SCALE_TERMINAL_MARGIN = 0.18
MATURE_TERMINAL_MARGIN = 0.22


def is_financial_services_firm(info: Dict[str, Any]) -> bool:
    """Yahoo sector labels that are out of scope for this FCFF pipeline."""
    sector = str(info.get("sector") or "").strip().lower()
    return sector in UNSUPPORTED_FCFF_SECTORS


def _normalize(value: Any) -> str:
    return str(value).strip().lower()


def _period_sort_key(value: Any) -> Tuple[int, str]:
    if isinstance(value, (date, datetime)):
        return (1, value.isoformat())
    return (0, str(value))


def _period_major_rows(
    income_statement: Dict[Any, Any],
) -> Dict[Any, Dict[str, Any]]:
    """Normalize Yahoo period-major or metric-major statements to period-major rows."""
    if not income_statement:
        return {}

    first_value = next(iter(income_statement.values()), None)
    if not isinstance(first_value, dict):
        return {}

    first_inner_keys = {_normalize(key) for key in first_value}
    if any(label in first_inner_keys for label in REVENUE_LABELS + EBIT_LABELS):
        return {
            period: {_normalize(key): value for key, value in values.items()}
            for period, values in income_statement.items()
            if isinstance(values, dict)
        }

    # Metric-major fallback: {line item: {period: value}}
    rows: Dict[Any, Dict[str, Any]] = {}
    for metric, observations in income_statement.items():
        if not isinstance(observations, dict):
            continue
        for period, value in observations.items():
            rows.setdefault(period, {})[_normalize(metric)] = value
    return rows


def _value_for_labels(
    row: Dict[str, Any], labels: Tuple[str, ...]
) -> Optional[float]:
    for label in labels:
        value = row.get(label)
        if value is None:
            continue
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        if numeric == numeric:
            return numeric
    return None


def extract_revenue_history(
    income_statement: Dict[Any, Any],
) -> List[Tuple[Any, float]]:
    """Return annual revenue observations oldest-to-newest."""
    rows = _period_major_rows(income_statement)
    history: List[Tuple[Any, float]] = []
    for period, row in rows.items():
        revenue = _value_for_labels(row, REVENUE_LABELS)
        if revenue is not None and revenue > 0:
            history.append((period, revenue))
    history.sort(key=lambda item: _period_sort_key(item[0]))
    return history


def _period_label(period: Any) -> Optional[str]:
    if period is None:
        return None
    if isinstance(period, datetime):
        return period.date().isoformat()
    if isinstance(period, date):
        return period.isoformat()
    text = str(period).strip()
    return text[:32] if text else None


def extract_operating_pnl_anchor(income_statement: Dict[Any, Any]) -> Dict[str, Any]:
    """
    One fiscal period for the DCF baseline and the last-reported P&L column.

    Revenue and EBIT must both be present. NI, interest, and EPS are taken from
    that same period when the statements carry them — not from a newer stub year.
    """
    rows = _period_major_rows(income_statement)
    for period in sorted(rows, key=_period_sort_key, reverse=True):
        revenue = _value_for_labels(rows[period], REVENUE_LABELS)
        ebit = _value_for_labels(rows[period], EBIT_LABELS)
        if revenue is None or revenue <= 0 or ebit is None:
            continue
        row = rows[period]
        interest = _value_for_labels(row, INTEREST_LABELS)
        return {
            "period": period,
            "period_label": _period_label(period),
            "revenue": revenue,
            "ebit": ebit,
            "net_income": _value_for_labels(row, NET_INCOME_LABELS),
            "interest_expense": abs(interest) if interest is not None else None,
            "reported_eps": _value_for_labels(row, EPS_LABELS),
        }
    raise ValueError("Latest revenue and EBIT/operating income are required for DCF.")


def extract_operating_baseline(
    income_statement: Dict[Any, Any],
) -> Tuple[float, float]:
    """Return latest positive revenue and corresponding EBIT/operating income."""
    anchor = extract_operating_pnl_anchor(income_statement)
    return float(anchor["revenue"]), float(anchor["ebit"])


def extract_latest_net_income(income_statement: Dict[Any, Any]) -> Optional[float]:
    """Net income from the same period as the DCF revenue/EBIT baseline."""
    try:
        return extract_operating_pnl_anchor(income_statement).get("net_income")
    except ValueError:
        return None


def calculate_revenue_cagr(income_statement: Dict[Any, Any]) -> Optional[float]:
    """Calculate CAGR from at most the latest four annual observations (three intervals)."""
    history = extract_revenue_history(income_statement)
    if len(history) < 2:
        return None
    sample = history[-4:]
    start_period, start_revenue = sample[0]
    end_period, end_revenue = sample[-1]

    if isinstance(start_period, (date, datetime)) and isinstance(
        end_period, (date, datetime)
    ):
        years = max((end_period - start_period).days / 365.25, 1.0)
    else:
        years = float(len(sample) - 1)
    if start_revenue <= 0 or end_revenue <= 0:
        return None
    return (end_revenue / start_revenue) ** (1 / years) - 1


def classify_firm_and_adjust_assumptions(
    market_cap: float,
    income_statement: Dict[Any, Any],
    info: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Apply transparent, configurable lifecycle heuristics.

    These are policy defaults, not forecasts or a substitute for analyst judgment.
    """
    cap = float(market_cap)
    if cap <= 0:
        raise ValueError("Market capitalization must be positive.")

    base_revenue, base_ebit = extract_operating_baseline(income_statement)
    current_margin = base_ebit / base_revenue
    observed_cagr = calculate_revenue_cagr(income_statement)
    revenue_cagr = 0.05 if observed_cagr is None else observed_cagr
    sector = str(info.get("sector") or "").strip()
    industry = str(info.get("industry") or "").strip()
    fcff_supported = not is_financial_services_firm(info)

    is_large = cap >= 10_000_000_000
    is_small = cap < 2_000_000_000
    is_high_growth = revenue_cagr > 0.15
    price_to_sales = cap / base_revenue if base_revenue else None
    # P/S alone is not enough: ordinary high-growth large-caps stay on the
    # 8–20% band. Scale-up needs hyper growth versus a mature run-rate, and
    # dollar revenues (not toy units) so a $20bn cap on $122 of test revenue
    # cannot reclassify the name.
    is_scale_up = bool(
        revenue_cagr >= SCALEUP_CAGR
        and price_to_sales is not None
        and price_to_sales >= SCALEUP_PS_THRESHOLD
        and base_revenue >= 1_000_000
    )

    if is_scale_up:
        firm_type = "Scale-up High-Growth"
        size_premium = 0.010
        growth_low, growth_high = 0.20, SCALEUP_BASE_CAP
        high_growth_rate = min(max(revenue_cagr, growth_low), growth_high)
        sales_to_capital = 1.3
        high_growth_years, transition_years = 8, 5
        if current_margin <= 0:
            terminal_margin = 0.15
        else:
            terminal_margin = max(0.15, min(current_margin, 0.22))
        stable_sales_to_capital = 2.0
    elif is_large and is_high_growth:
        firm_type = "High-Growth Large-Cap"
        size_premium = 0.005
        growth_low, growth_high = 0.08, 0.20
        high_growth_rate = min(max(revenue_cagr, growth_low), growth_high)
        sales_to_capital = 1.8
        high_growth_years, transition_years = 5, 5
        terminal_margin = max(0.12, min(current_margin * 0.85, 0.30))
        stable_sales_to_capital = 2.0
    elif is_large:
        firm_type = "Mature Large-Cap"
        size_premium = 0.0
        growth_low, growth_high = 0.02, 0.07
        high_growth_rate = min(max(revenue_cagr, growth_low), growth_high)
        sales_to_capital = 2.2
        high_growth_years, transition_years = 3, 4
        terminal_margin = max(0.08, min(current_margin * 0.95, 0.25))
        stable_sales_to_capital = 2.0
    elif is_small and is_high_growth:
        firm_type = "High-Growth Small-Cap"
        size_premium = 0.020
        growth_low, growth_high = 0.10, 0.25
        high_growth_rate = min(max(revenue_cagr, growth_low), growth_high)
        sales_to_capital = 1.3
        high_growth_years, transition_years = 5, 5
        terminal_margin = 0.12 if current_margin <= 0 else min(current_margin, 0.18)
        stable_sales_to_capital = 1.8
    elif is_small:
        firm_type = "Mature/Low-Growth Small-Cap"
        size_premium = 0.025
        growth_low, growth_high = 0.01, 0.08
        high_growth_rate = min(max(revenue_cagr, growth_low), growth_high)
        sales_to_capital = 1.5
        high_growth_years, transition_years = 3, 4
        terminal_margin = max(0.05, min(current_margin, 0.12))
        stable_sales_to_capital = 1.8
    else:
        firm_type = "Mid-Cap Growth" if is_high_growth else "Mature Mid-Cap"
        size_premium = 0.010
        growth_low, growth_high = 0.02, 0.18
        high_growth_rate = min(max(revenue_cagr, growth_low), growth_high)
        sales_to_capital = 1.6
        high_growth_years, transition_years = (5, 5) if is_high_growth else (3, 4)
        terminal_margin = max(0.07, min(current_margin * 0.95, 0.20))
        stable_sales_to_capital = 1.9

    logger.info(
        "Firm type %s | CAGR %.2f%% | margin %.2f%% | size premium %.2f%%",
        firm_type,
        revenue_cagr * 100,
        current_margin * 100,
        size_premium * 100,
    )
    return {
        "firm_type": firm_type,
        "market_cap": cap,
        "sector": sector or None,
        "industry": industry or None,
        "fcff_supported": fcff_supported,
        "historical_revenue_cagr": revenue_cagr,
        "current_operating_margin": current_margin,
        "size_premium": size_premium,
        "high_growth_rate": high_growth_rate,
        "high_growth_rate_bounds": [growth_low, growth_high],
        "sales_to_capital": sales_to_capital,
        "high_growth_years": high_growth_years,
        "transition_years": transition_years,
        "terminal_margin": terminal_margin,
        "stable_sales_to_capital": stable_sales_to_capital,
        "price_to_sales": price_to_sales,
        "methodology_note": (
            "Rule-based policy assumptions; review against company guidance, "
            "consensus estimates, sector economics, and valuation date."
        ),
    }
