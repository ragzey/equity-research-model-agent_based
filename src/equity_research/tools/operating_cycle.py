"""Observed cash-conversion and reinvestment metrics from the financial statements."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

DAYS = 365.0
STC_FLOOR = 0.60
STC_CAP = 5.00
NWC_ABSORBING = 0.03
CCC_MOVE_DAYS = 8.0

RECEIVABLE_LABELS = (
    "accounts receivable",
    "receivables",
    "net receivables",
    "accounts receivable net",
)
INVENTORY_LABELS = ("inventory", "inventories")
PAYABLE_LABELS = (
    "accounts payable",
    "payables",
    "trade payables",
)
PPE_LABELS = (
    "net ppe",
    "net property plant and equipment",
    "property plant and equipment net",
    "property plant equipment net",
)
REVENUE_LABELS = ("total revenue", "revenue")
COGS_LABELS = (
    "cost of revenue",
    "cost of goods sold",
    "reconciled cost of revenue",
    "costofrevenue",
)


def _normalize(value: Any) -> str:
    return str(value).strip().lower()


def _period_sort_key(value: Any) -> Tuple[int, str]:
    if isinstance(value, (date, datetime)):
        return (1, value.isoformat())
    return (0, str(value))


def _as_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:
        return None
    return number


def _period_rows(statement: Optional[Dict[Any, Any]]) -> List[Tuple[Any, Dict[str, Any]]]:
    if not statement:
        return []
    first_value = next(iter(statement.values()), None)
    if not isinstance(first_value, dict):
        return []
    inner = {_normalize(key) for key in first_value}
    looks_period_major = any(
        token in key
        for key in inner
        for token in (
            "receivable",
            "inventory",
            "payable",
            "ppe",
            "cash",
            "revenue",
            "cost of",
            "operating income",
        )
    )
    if looks_period_major:
        rows = [
            (period, {_normalize(key): value for key, value in values.items()})
            for period, values in statement.items()
            if isinstance(values, dict)
        ]
    else:
        by_period: Dict[Any, Dict[str, Any]] = {}
        for metric, observations in statement.items():
            if not isinstance(observations, dict):
                continue
            for period, value in observations.items():
                by_period.setdefault(period, {})[_normalize(metric)] = value
        rows = list(by_period.items())
    rows.sort(key=lambda item: _period_sort_key(item[0]))
    return rows


def _match(
    row: Dict[str, Any],
    labels: Sequence[str],
    *,
    contains: Sequence[str] = (),
    exclude: Sequence[str] = (),
) -> Optional[float]:
    for label in labels:
        number = _as_float(row.get(label))
        if number is not None:
            return number
    for key, value in row.items():
        if any(token in key for token in exclude):
            continue
        if any(token in key for token in contains):
            number = _as_float(value)
            if number is not None:
                return number
    return None


def _nwc(row: Dict[str, Any]) -> Optional[float]:
    receivables = _match(
        row,
        RECEIVABLE_LABELS,
        contains=("accounts receivable", "receivable"),
        exclude=("tax", "income tax", "note"),
    )
    inventory = _match(row, INVENTORY_LABELS, contains=("inventory",), exclude=("progress",))
    payables = _match(
        row,
        PAYABLE_LABELS,
        contains=("accounts payable",),
        exclude=("tax", "notes", "income tax"),
    )
    if receivables is None and inventory is None and payables is None:
        return None
    return (receivables or 0.0) + (inventory or 0.0) - (payables or 0.0)


def _ppe(row: Dict[str, Any]) -> Optional[float]:
    return _match(
        row,
        PPE_LABELS,
        contains=("net ppe", "property plant and equipment net", "net property plant"),
        exclude=("gross",),
    )


def _ratio(numer: Optional[float], denom: Optional[float]) -> Optional[float]:
    if numer is None or denom is None or denom == 0:
        return None
    return numer / denom * DAYS


def clip_sales_to_capital(value: Any, fallback: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = float(fallback)
    if number != number:
        number = float(fallback)
    return min(STC_CAP, max(STC_FLOOR, number))


def infer_cash_conversion_view(
    ccc_days: Optional[float],
    prior_ccc_days: Optional[float],
) -> str:
    if ccc_days is None:
        return "insufficient"
    if prior_ccc_days is None:
        return "stable"
    delta = ccc_days - prior_ccc_days
    if delta >= CCC_MOVE_DAYS:
        return "lengthening"
    if delta <= -CCC_MOVE_DAYS:
        return "shortening"
    return "stable"


def infer_working_capital_view(
    nwc_to_sales: Optional[float],
    delta_nwc: Optional[float],
    delta_revenue: Optional[float],
) -> str:
    if nwc_to_sales is None and delta_nwc is None:
        return "insufficient"
    if delta_nwc is not None and delta_revenue is not None and abs(delta_revenue) > 1e-9:
        if delta_nwc / abs(delta_revenue) >= NWC_ABSORBING:
            return "absorbing"
        if delta_nwc / abs(delta_revenue) <= -NWC_ABSORBING:
            return "releasing"
        return "stable"
    if nwc_to_sales is not None and nwc_to_sales <= 0:
        return "releasing"
    if nwc_to_sales is not None and nwc_to_sales >= 0.20:
        return "absorbing"
    return "stable"


def infer_reinvestment_view(
    implied_stc: Optional[float],
    classifier_stc: Optional[float],
    capital_released: bool,
) -> str:
    if capital_released:
        return "asset_light"
    if implied_stc is None:
        return "insufficient"
    baseline = float(classifier_stc) if classifier_stc else 1.8
    if implied_stc <= baseline * 0.80:
        return "heavy"
    if implied_stc >= baseline * 1.20:
        return "asset_light"
    return "typical"


def measure_operating_cycle(
    income_statement: Optional[Dict[Any, Any]],
    balance_sheet: Optional[Dict[Any, Any]],
    *,
    classifier_sales_to_capital: Optional[float] = None,
) -> Dict[str, Any]:
    """Python ledger metrics. No LLM. Missing lines become insufficient views."""
    income_rows = _period_rows(income_statement)
    balance_rows = _period_rows(balance_sheet)
    latest_income = income_rows[-1][1] if income_rows else {}
    prior_income = income_rows[-2][1] if len(income_rows) >= 2 else {}
    latest_bs = balance_rows[-1][1] if balance_rows else {}
    prior_bs = balance_rows[-2][1] if len(balance_rows) >= 2 else {}

    revenue = _match(latest_income, REVENUE_LABELS, contains=("total revenue",))
    prior_revenue = _match(prior_income, REVENUE_LABELS, contains=("total revenue",))
    cogs = _match(latest_income, COGS_LABELS, contains=("cost of revenue", "cost of goods"))
    prior_cogs = _match(prior_income, COGS_LABELS, contains=("cost of revenue", "cost of goods"))

    receivables = _match(
        latest_bs,
        RECEIVABLE_LABELS,
        contains=("accounts receivable", "receivable"),
        exclude=("tax", "note"),
    )
    inventory = _match(latest_bs, INVENTORY_LABELS, contains=("inventory",))
    payables = _match(
        latest_bs,
        PAYABLE_LABELS,
        contains=("accounts payable",),
        exclude=("tax", "notes"),
    )
    nwc = _nwc(latest_bs)
    prior_nwc = _nwc(prior_bs) if prior_bs else None
    ppe = _ppe(latest_bs)
    prior_ppe = _ppe(prior_bs) if prior_bs else None

    dso = _ratio(receivables, revenue)
    if inventory is None:
        dio = 0.0 if receivables is not None else None
    else:
        dio = _ratio(inventory, cogs)
    dpo = _ratio(payables, cogs if cogs is not None else revenue)
    ccc = None
    if dso is not None:
        if inventory is not None and dio is None:
            ccc = None
        else:
            ccc = dso + (dio or 0.0) - (dpo or 0.0)

    prior_dso = _ratio(
        _match(prior_bs, RECEIVABLE_LABELS, contains=("accounts receivable", "receivable"), exclude=("tax", "note")),
        prior_revenue,
    )
    prior_inventory = _match(prior_bs, INVENTORY_LABELS, contains=("inventory",))
    if prior_inventory is None:
        prior_dio = 0.0 if prior_dso is not None else None
    else:
        prior_dio = _ratio(prior_inventory, prior_cogs)
    prior_dpo = _ratio(
        _match(prior_bs, PAYABLE_LABELS, contains=("accounts payable",), exclude=("tax", "notes")),
        prior_cogs if prior_cogs is not None else prior_revenue,
    )
    prior_ccc = None
    if prior_dso is not None:
        if prior_inventory is not None and prior_dio is None:
            prior_ccc = None
        else:
            prior_ccc = prior_dso + (prior_dio or 0.0) - (prior_dpo or 0.0)

    nwc_to_sales = (nwc / revenue) if nwc is not None and revenue else None
    delta_nwc = (nwc - prior_nwc) if nwc is not None and prior_nwc is not None else None
    delta_ppe = (ppe - prior_ppe) if ppe is not None and prior_ppe is not None else None
    delta_revenue = (
        (revenue - prior_revenue) if revenue is not None and prior_revenue is not None else None
    )
    delta_capital = None
    if delta_nwc is not None or delta_ppe is not None:
        delta_capital = (delta_nwc or 0.0) + (delta_ppe or 0.0)

    implied_stc = None
    capital_released = False
    if delta_revenue is not None and delta_revenue > 0 and delta_capital is not None:
        if delta_capital > 0:
            implied_stc = clip_sales_to_capital(delta_revenue / delta_capital, 1.8)
        else:
            capital_released = True

    classifier = (
        float(classifier_sales_to_capital)
        if classifier_sales_to_capital is not None
        else None
    )
    observed_stc = implied_stc if implied_stc is not None else classifier
    if observed_stc is not None:
        observed_stc = clip_sales_to_capital(observed_stc, classifier or 1.8)

    ccc_view = infer_cash_conversion_view(ccc, prior_ccc)
    wc_view = infer_working_capital_view(nwc_to_sales, delta_nwc, delta_revenue)
    reinvestment_view = infer_reinvestment_view(implied_stc, classifier, capital_released)

    return {
        "revenue": revenue,
        "cogs": cogs,
        "receivables": receivables,
        "inventory": inventory,
        "payables": payables,
        "nwc": nwc,
        "nwc_to_sales": nwc_to_sales,
        "delta_nwc": delta_nwc,
        "ppe": ppe,
        "delta_ppe": delta_ppe,
        "delta_revenue": delta_revenue,
        "delta_invested_capital": delta_capital,
        "dso_days": dso,
        "dio_days": dio,
        "dpo_days": dpo,
        "ccc_days": ccc,
        "prior_ccc_days": prior_ccc,
        "implied_sales_to_capital": implied_stc,
        "observed_sales_to_capital": observed_stc,
        "capital_released_on_growth": capital_released,
        "cash_conversion_view": ccc_view,
        "working_capital_view": wc_view,
        "reinvestment_view": reinvestment_view,
        "source": (
            "observed ΔRevenue / Δ(NWC+Net PPE)"
            if implied_stc is not None
            else (
                "capital released while revenue grew"
                if capital_released
                else "classifier fallback; working-capital lines incomplete"
            )
        ),
    }


def operating_cycle_ledger(metrics: Optional[Dict[str, Any]]) -> str:
    """Sentences the operations agent may quote as evidence."""
    metrics = metrics or {}
    lines: List[str] = []
    if metrics.get("ccc_days") is not None:
        lines.append(f"Cash conversion cycle is {metrics['ccc_days']:.1f} days.")
    if metrics.get("dso_days") is not None:
        lines.append(f"Days sales outstanding is {metrics['dso_days']:.1f} days.")
    if metrics.get("dio_days") is not None:
        lines.append(f"Days inventory outstanding is {metrics['dio_days']:.1f} days.")
    if metrics.get("dpo_days") is not None:
        lines.append(f"Days payable outstanding is {metrics['dpo_days']:.1f} days.")
    if metrics.get("nwc_to_sales") is not None:
        lines.append(f"Net working capital to sales is {metrics['nwc_to_sales']:.1%}.")
    if metrics.get("delta_nwc") is not None:
        lines.append(f"Change in net working capital is {metrics['delta_nwc']:.4g}.")
    if metrics.get("implied_sales_to_capital") is not None:
        lines.append(
            f"Implied sales-to-capital is {metrics['implied_sales_to_capital']:.2f}."
        )
    if metrics.get("prior_ccc_days") is not None and metrics.get("ccc_days") is not None:
        delta = metrics["ccc_days"] - metrics["prior_ccc_days"]
        direction = "lengthened" if delta >= 0 else "shortened"
        lines.append(
            f"Cash conversion cycle {direction} {abs(delta):.1f} days versus the prior year."
        )
    if metrics.get("capital_released_on_growth"):
        lines.append("Invested capital declined while revenue grew.")
    view = metrics.get("working_capital_view")
    if view and view != "insufficient":
        lines.append(f"Python working-capital view is {view}.")
    view = metrics.get("cash_conversion_view")
    if view and view != "insufficient":
        lines.append(f"Python cash-conversion view is {view}.")
    view = metrics.get("reinvestment_view")
    if view and view != "insufficient":
        lines.append(f"Python reinvestment view is {view}.")
    return "\n".join(lines)
