"""Sell-side-style report pack: blended fair value, 12-month PT, assumption register."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from .source_register import build_source_register

# Same 70/30 split as a standard initiation that treats DCF as primary
# and peer EV/EBITDA as the market cross-check.
DCF_WEIGHT = 0.70
RELATIVE_WEIGHT = 0.30
RATING_BAND = 0.15


def _finite(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _floor_zero(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    return max(float(value), 0.0)


def blend_fair_value(
    dcf_value: Optional[float],
    relative_value: Optional[float],
    dcf_weight: float = DCF_WEIGHT,
    relative_weight: float = RELATIVE_WEIGHT,
) -> Tuple[Optional[float], float, float]:
    """Return (blended value, applied DCF weight, applied relative weight)."""
    dcf = _finite(dcf_value)
    relative = _finite(relative_value)
    if dcf is None and relative is None:
        return None, 0.0, 0.0
    if relative is None:
        return dcf, 1.0, 0.0
    if dcf is None:
        return relative, 0.0, 1.0
    total = dcf_weight + relative_weight
    if total <= 0:
        return dcf, 1.0, 0.0
    applied_dcf = dcf_weight / total
    applied_rel = relative_weight / total
    return applied_dcf * dcf + applied_rel * relative, applied_dcf, applied_rel


def price_target_12m(
    fair_value: Optional[float],
    cost_of_equity: Optional[float],
    indicated_dividend: Optional[float] = None,
) -> Optional[float]:
    """Roll today's fair value forward one year at Ke, then subtract DPS if known."""
    value = _finite(fair_value)
    ke = _finite(cost_of_equity)
    if value is None or ke is None:
        return None
    dividend = _finite(indicated_dividend) or 0.0
    return value * (1.0 + ke) - dividend


def model_rating(
    upside: Optional[float],
    band: float = RATING_BAND,
) -> Optional[str]:
    """±15% band versus the current price. Illustrative model output, not advice."""
    gap = _finite(upside)
    if gap is None:
        return None
    if gap >= band:
        return "Buy"
    if gap <= -band:
        return "Sell"
    return "Hold"


def implied_price_from_ev_ebitda(
    *,
    peer_median_ev_ebitda: Optional[float],
    target_ev_ebitda: Optional[float],
    target_ebitda: Optional[float],
    market_cap: Optional[float],
    total_debt: float,
    cash: float,
    shares: float,
) -> Optional[Dict[str, Any]]:
    """
    Re-rate the target at the peer-median EV/EBITDA.

    EBITDA is Yahoo trailing when present; otherwise it is implied from
    current enterprise value and the target's own EV/EBITDA.
    """
    multiple = _finite(peer_median_ev_ebitda)
    if multiple is None or multiple <= 0 or shares <= 0:
        return None
    ebitda = _finite(target_ebitda)
    method = "yahoo_trailing_ebitda"
    if ebitda is None or ebitda <= 0:
        own_multiple = _finite(target_ev_ebitda)
        cap = _finite(market_cap)
        if own_multiple is None or own_multiple <= 0 or cap is None:
            return None
        market_ev = cap + float(total_debt) - float(cash)
        if market_ev <= 0:
            return None
        ebitda = market_ev / own_multiple
        method = "implied_from_current_ev_ebitda"
        if ebitda <= 0:
            return None
    implied_ev = multiple * ebitda
    implied_equity = implied_ev - float(total_debt) + float(cash)
    return {
        "implied_price": implied_equity / shares,
        "ebitda_used": ebitda,
        "implied_enterprise_value": implied_ev,
        "implied_equity_value": implied_equity,
        "peer_median_ev_ebitda": multiple,
        "method": method,
    }


def _fmt_usd(value: Any, digits: int = 2) -> str:
    number = _finite(value)
    if number is None:
        return "N/A"
    return f"${number:,.{digits}f}"


def _fmt_pct(value: Any, digits: int = 2) -> str:
    number = _finite(value)
    if number is None:
        return "N/A"
    return f"{number:.{digits}%}"


def _fmt_multiple(value: Any) -> str:
    number = _finite(value)
    if number is None:
        return "N/A"
    return f"{number:.1f}x"


def _fmt_millions(value: Any) -> str:
    number = _finite(value)
    if number is None:
        return "N/A"
    return f"${number / 1_000_000:,.0f}m"


def _fmt_shares(value: Any) -> str:
    number = _finite(value)
    if number is None:
        return "N/A"
    return f"{number / 1_000_000:,.1f}m"


def _upside(numerator: Optional[float], price: Optional[float]) -> Optional[float]:
    value = _finite(numerator)
    spot = _finite(price)
    if value is None or spot is None or spot <= 0:
        return None
    return value / spot - 1.0


def _sensitivity_range(grid: Optional[Dict[str, Any]]) -> Tuple[Optional[float], Optional[float]]:
    if not grid:
        return None, None
    values = grid.get("intrinsic_value_per_share") or []
    flat: List[float] = []
    for row in values:
        if not isinstance(row, (list, tuple)):
            continue
        for cell in row:
            number = _finite(cell)
            if number is not None:
                flat.append(max(number, 0.0))
    if not flat:
        return None, None
    return min(flat), max(flat)


def _decision_map(state: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    decisions = (state.get("dcf_overrides") or {}).get("decisions") or []
    return {
        str(row.get("key")): row
        for row in decisions
        if isinstance(row, dict) and row.get("key")
    }


def _desk_note(decisions: Dict[str, Dict[str, Any]], key: str) -> str:
    row = decisions.get(key) or {}
    action = row.get("action")
    reason = row.get("reason")
    if not action:
        return ""
    extra = f" — {str(reason).rstrip('.')}" if reason else ""
    return f" Desk {action}{extra}."


def _identity(state: Dict[str, Any]) -> Dict[str, Optional[str]]:
    ticker = str(state.get("ticker") or "").strip().upper()
    peers = state.get("peer_metadata") or {}
    meta = peers.get(ticker) or {}
    info = state.get("market_info") or {}
    summary = state.get("valuation_summary") or {}
    classification = summary.get("firm_classification") or {}
    return {
        "ticker": ticker,
        "company_name": (
            meta.get("company_name")
            or info.get("longName")
            or info.get("shortName")
            or ticker
        ),
        "sector": meta.get("sector") or info.get("sector") or classification.get("sector"),
        "industry": (
            meta.get("industry") or info.get("industry") or classification.get("industry")
        ),
        "country": meta.get("country") or info.get("country"),
        "exchange": info.get("fullExchangeName") or info.get("exchange"),
    }


def _relative_block(state: Dict[str, Any], inputs: Dict[str, Any]) -> Dict[str, Any]:
    matrix = state.get("peer_comparison_matrix") or {}
    ticker = str(state.get("ticker") or "").strip().upper()
    competitors = matrix.get("competitors") or []
    medians = matrix.get("peer_medians") or {}
    metrics = (matrix.get("metrics") or {}).get(ticker) or {}
    if not competitors:
        return {"result": None, "reason": "No peer group supplied."}
    peer_median = _finite(medians.get("ev_to_ebitda"))
    if peer_median is None:
        return {"result": None, "reason": "Peer-median EV/EBITDA unavailable."}
    result = implied_price_from_ev_ebitda(
        peer_median_ev_ebitda=peer_median,
        target_ev_ebitda=_finite(metrics.get("ev_to_ebitda")),
        target_ebitda=_finite(metrics.get("ebitda")),
        market_cap=_finite(inputs.get("market_cap")),
        total_debt=_finite(inputs.get("total_debt")) or 0.0,
        cash=_finite(inputs.get("cash_and_equivalents")) or 0.0,
        shares=_finite(inputs.get("shares_outstanding")) or 0.0,
    )
    if result is None:
        return {
            "result": None,
            "reason": "Target EBITDA could not be observed or implied from EV/EBITDA.",
        }
    return {"result": result, "reason": None}


def _assumption_rows(
    state: Dict[str, Any],
    *,
    dcf_weight: float,
    relative_weight: float,
    relative_reason: Optional[str],
    ke: Optional[float],
    indicated_dividend: Optional[float],
) -> List[Dict[str, str]]:
    summary = state.get("valuation_summary") or {}
    inputs = summary.get("valuation_date_inputs") or {}
    applied = summary.get("applied_dcf_assumptions") or {}
    classification = summary.get("firm_classification") or {}
    cost_of_debt = summary.get("cost_of_debt") or {}
    wacc_block = summary.get("wacc") or {}
    rationales = (state.get("dcf_overrides") or {}).get("rationales") or {}
    consensus = state.get("consensus_growth") or {}
    decisions = _decision_map(state)
    details = cost_of_debt.get("details") or {}
    operations = state.get("operations_packet") or {}
    cycle_metrics = operations.get("metrics") or (summary.get("operating_cycle") or {})

    mix_justification = (
        "DCF is primary because it uses firm-specific growth, margin and "
        "reinvestment. Peer EV/EBITDA is a market cross-check, not a substitute."
    )
    if relative_weight == 0:
        mix_justification = (
            relative_reason
            or "Peer EV/EBITDA cross-check unavailable; fair value is 100% DCF."
        )

    rows: List[Dict[str, str]] = [
        {
            "item": "Valuation mix",
            "value": f"{dcf_weight:.0%} DCF / {relative_weight:.0%} peer EV/EBITDA",
            "justification": mix_justification,
            "source": "Desk policy (standard initiation weights)",
        },
    ]
    selection = state.get("peer_selection") or {}
    selected_peers = selection.get("selected") or (
        (state.get("peer_comparison_matrix") or {}).get("competitors")
    )
    if selected_peers:
        rows.append(
            {
                "item": "Peer group",
                "value": ", ".join(str(item) for item in selected_peers),
                "justification": str(
                    selection.get("rationale")
                    or "Competitive analyst comparable set used for the EV/EBITDA cross-check."
                ),
                "source": (
                    f"Competitive analyst ({selection.get('mode') or 'auto'})"
                    if selection
                    else "Competitive analyst"
                ),
            }
        )
    rows.extend(
        [
        {
            "item": "High-growth rate",
            "value": _fmt_pct(applied.get("high_growth_rate")),
            "justification": str(
                rationales.get("high_growth_rate")
                or classification.get("methodology_note")
                or "Lifecycle classifier on historical revenue CAGR, then bounded."
            )
            + _desk_note(decisions, "high_growth_rate"),
            "source": (
                "Assumption architect menu / reviewer"
                if (state.get("dcf_overrides") or {}).get("architect_choices")
                else consensus.get("source")
                or "Firm classifier + optional Yahoo consensus overlay"
            ),
        },
        {
            "item": "High-growth years",
            "value": str(applied.get("high_growth_years") or "N/A"),
            "justification": str(
                rationales.get("high_growth_horizon")
                or "Lifecycle default for the classified firm type."
            )
            + _desk_note(decisions, "high_growth_years"),
            "source": "Assumption architect / reviewer"
            if (state.get("dcf_overrides") or {}).get("architect_choices")
            else "Firm classifier / assumption reviewer",
        },
        {
            "item": "Transition years",
            "value": str(applied.get("transition_years") or "N/A"),
            "justification": "Linear fade from high-growth to terminal margin, growth and WACC.",
            "source": "Firm classifier",
        },
        {
            "item": "Terminal EBIT margin",
            "value": _fmt_pct(applied.get("terminal_margin")),
            "justification": str(
                rationales.get("terminal_margin")
                or "Classifier fade from current operating margin, with a moat lift only if the desk accepted it."
            )
            + _desk_note(decisions, "terminal_margin"),
            "source": "Firm classifier / competitive overlay / reviewer",
        },
        {
            "item": "Terminal growth",
            "value": _fmt_pct(applied.get("terminal_growth_rate")),
            "justification": str(
                rationales.get("terminal_growth_rate")
                or (
                    "Perpetuity growth from firm type and the economy (Rf minus a "
                    "firm-type spread), with a hard ceiling of min(5%, Rf − 50bp). "
                    "Not a universal 2.5% cap."
                )
            )
            + _desk_note(decisions, "terminal_growth_rate"),
            "source": "Architect menu / Quant policy",
        },
        {
            "item": "Sales-to-capital (high-growth)",
            "value": _fmt_usd(applied.get("sales_to_capital"), 2).replace("$", ""),
            "justification": str(
                rationales.get("sales_to_capital")
                or (
                    "Reinvestment = ΔRevenue / sales-to-capital. Observed from "
                    "Δ(NWC + net PPE) when the statements support it; otherwise "
                    "the firm-type default. Heavy/light only if the operations "
                    "packet is evidenced."
                )
            )
            + _desk_note(decisions, "sales_to_capital"),
            "source": (
                "Operations packet / architect menu / reviewer"
                if (state.get("dcf_overrides") or {}).get("architect_choices")
                else "Observed operating cycle / firm classifier"
            ),
        },
        {
            "item": "Cash conversion cycle",
            "value": (
                f"{_finite(cycle_metrics.get('ccc_days')):.1f} days"
                if _finite(cycle_metrics.get("ccc_days")) is not None
                else "N/A"
            ),
            "justification": str(
                (operations.get("cash_conversion") or {}).get("evidence")
                or "DSO + DIO − DPO from the latest two annual statements. "
                "Python arithmetic; the operations agent may only explain it."
            ),
            "source": "Python operating cycle / operations analyst",
        },
        {
            "item": "NWC / sales",
            "value": _fmt_pct(cycle_metrics.get("nwc_to_sales")),
            "justification": str(
                (operations.get("working_capital") or {}).get("evidence")
                or "Net working capital is AR + inventory − AP. A rising ratio "
                "absorbs cash as revenue grows and lowers FCFF."
            ),
            "source": "Python operating cycle / operations analyst",
        },
        {
            "item": "WACC",
            "value": _fmt_pct(state.get("discount_rate") or wacc_block.get("wacc")),
            "justification": (
                f"Market equity weight {_fmt_pct(wacc_block.get('weight_equity'))} "
                f"× cost of equity {_fmt_pct(ke)} + book-debt weight "
                f"{_fmt_pct(wacc_block.get('weight_debt'))} × after-tax Kd "
                f"{_fmt_pct(cost_of_debt.get('after_tax_cost_of_debt'))}. "
                "Book debt is used as a market-debt proxy."
            ),
            "source": "Python WACC",
        },
        {
            "item": "Cost of equity",
            "value": _fmt_pct(ke),
            "justification": (
                "Rf {rf} + beta {beta} × ERP {erp} + size premium {size} "
                "+ company-specific premium {csrp}."
            ).format(
                rf=_fmt_pct(inputs.get("risk_free_rate")),
                beta=(
                    f"{_finite(inputs.get('beta')):.2f}"
                    if _finite(inputs.get("beta")) is not None
                    else "N/A"
                ),
                erp=_fmt_pct(inputs.get("market_equity_risk_premium")),
                size=_fmt_pct(classification.get("size_premium")),
                csrp=_fmt_pct(inputs.get("company_specific_risk_premium")),
            ),
            "source": "CAPM + additive size and company-specific premia",
        },
        {
            "item": "Beta",
            "value": f"{_finite(inputs.get('beta')):.2f}"
            if _finite(inputs.get("beta")) is not None
            else "N/A",
            "justification": "Yahoo Finance levered beta. A low published beta is valuation-supportive, not conservative.",
            "source": "Yahoo Finance",
        },
        {
            "item": "Risk-free rate",
            "value": _fmt_pct(inputs.get("risk_free_rate")),
            "justification": "Live 10-year U.S. Treasury yield (^TNX).",
            "source": "Yahoo Finance ^TNX",
        },
        {
            "item": "Equity risk premium",
            "value": _fmt_pct(inputs.get("market_equity_risk_premium")),
            "justification": "Policy default of 5% unless a reviewed override in the 3%–8% band is supplied.",
            "source": "Desk policy",
        },
        {
            "item": "Size premium",
            "value": _fmt_pct(classification.get("size_premium")),
            "justification": f"Assigned by firm type ({classification.get('firm_type') or 'n/a'}).",
            "source": "Firm classifier",
        },
        {
            "item": "Company-specific risk premium",
            "value": _fmt_pct(inputs.get("company_specific_risk_premium")),
            "justification": str(
                rationales.get("company_specific_risk_premium")
                or "Added to Ke only when Item 1A/7 language matches configured regulatory or operational phrases."
            )
            + _desk_note(decisions, "company_specific_risk_premium"),
            "source": "Qualitative overlay / reviewer",
        },
        {
            "item": "Pre-tax cost of debt",
            "value": _fmt_pct(cost_of_debt.get("pre_tax_cost_of_debt")),
            "justification": (
                f"Method: {cost_of_debt.get('method_used') or 'N/A'}. "
                + (
                    f"Synthetic rating {details.get('synthetic_rating')} from ICR "
                    f"{details.get('interest_coverage_ratio')}; Damodaran spreads as-of "
                    f"{details.get('damodaran_spreads_as_of')}."
                    if details.get("synthetic_rating")
                    else "TRACE interpolation when valid ISINs and Finnhub quotes exist."
                )
            ),
            "source": str(cost_of_debt.get("method_used") or "Cost of debt module"),
        },
        {
            "item": "Marginal tax rate",
            "value": _fmt_pct(cost_of_debt.get("marginal_tax_rate_applied") or 0.21),
            "justification": "U.S. statutory federal rate applied to after-tax Kd and NOPAT. Not a cash tax forecast.",
            "source": "Desk policy",
        },
        {
            "item": "12-month price target",
            "value": "FV × (1 + Ke)"
            + (" − indicated DPS" if indicated_dividend else ""),
            "justification": (
                "Today's blended fair value is rolled forward one year at the cost of "
                "equity"
                + (
                    f", then reduced by the indicated dividend of {_fmt_usd(indicated_dividend)}."
                    if indicated_dividend
                    else ". No indicated dividend was in the Yahoo snapshot."
                )
                + " This is the expected price if the shares are fairly priced today "
                "and earn Ke; it is not a timing or catalyst forecast."
            ),
            "source": "Python report pack",
        },
        {
            "item": "Model band",
            "value": "±15% versus last price",
            "justification": (
                "Buy if 12-month target upside is at least 15%, Sell if at most −15%, "
                "otherwise Hold. Same band convention as a standard initiation note. "
                "This is model output, not an investment recommendation."
            ),
            "source": "Desk policy",
        },
        ]
    )
    return rows


def _key_data_rows(pack: Dict[str, Any]) -> List[Dict[str, str]]:
    low = pack.get("fifty_two_week_low")
    high = pack.get("fifty_two_week_high")
    range_text = (
        f"{_fmt_usd(low)} – {_fmt_usd(high)}"
        if _finite(low) is not None and _finite(high) is not None
        else "N/A"
    )
    return [
        {"label": "Model rating", "value": pack.get("model_rating") or "Withheld"},
        {"label": "12-month price target", "value": _fmt_usd(pack.get("price_target_12m"))},
        {"label": "Share price, last", "value": _fmt_usd(pack.get("share_price"))},
        {"label": "Upside / (downside) to PT", "value": _fmt_pct(pack.get("upside_to_pt"))},
        {"label": "Today's fair value", "value": _fmt_usd(pack.get("fair_value"))},
        {"label": "DCF value / share", "value": _fmt_usd(pack.get("dcf_value"))},
        {
            "label": "Relative EV/EBITDA / share",
            "value": _fmt_usd(pack.get("relative_value")),
        },
        {"label": "Market cap", "value": _fmt_millions(pack.get("market_cap"))},
        {"label": "Enterprise value (market)", "value": _fmt_millions(pack.get("market_ev"))},
        {"label": "Net debt / (cash)", "value": _fmt_millions(pack.get("net_debt"))},
        {"label": "Diluted shares", "value": _fmt_shares(pack.get("shares_outstanding"))},
        {"label": "52-week range (Yahoo)", "value": range_text},
        {"label": "WACC", "value": _fmt_pct(pack.get("wacc"))},
        {"label": "Cost of equity", "value": _fmt_pct(pack.get("cost_of_equity"))},
        {"label": "Terminal growth", "value": _fmt_pct(pack.get("terminal_growth"))},
        {"label": "Peer median EV/EBITDA", "value": _fmt_multiple(pack.get("peer_median_ev_ebitda"))},
    ]


def build_report_pack(state: Dict[str, Any]) -> Dict[str, Any]:
    """Assemble cover numbers, valuation points, and the assumption register."""
    identity = _identity(state)
    summary = state.get("valuation_summary") or {}
    inputs = summary.get("valuation_date_inputs") or {}
    applied = summary.get("applied_dcf_assumptions") or {}
    info = state.get("market_info") or {}
    dcf_block = summary.get("dcf") or {}
    valuation_method = (
        summary.get("valuation_method")
        or state.get("valuation_method")
        or "corporate_fcff"
    )
    verified = bool(state.get("is_math_verified"))
    raw_dcf = _finite(state.get("calculated_dcf_value"))
    dcf_value = _floor_zero(raw_dcf)
    share_price = _finite(inputs.get("share_price"))
    ke = _finite(summary.get("cost_of_equity"))
    dividend = _finite(
        inputs.get("indicated_dividend")
        or info.get("dividendRate")
        or info.get("trailingAnnualDividendRate")
    )
    market_cap = _finite(inputs.get("market_cap"))
    debt = _finite(inputs.get("total_debt")) or 0.0
    cash = _finite(inputs.get("cash_and_equivalents")) or 0.0
    shares = _finite(inputs.get("shares_outstanding"))
    market_ev = (market_cap + debt - cash) if market_cap is not None else None
    relative_info = _relative_block(state, inputs)
    relative_result = relative_info.get("result")
    relative_value = None
    if isinstance(relative_result, dict):
        relative_value = _finite(relative_result.get("implied_price"))

    if valuation_method == "unsupported_financial":
        fair_value = None
        dcf_weight, relative_weight = 0.0, 0.0
    else:
        fair_value, dcf_weight, relative_weight = blend_fair_value(
            dcf_value, relative_value
        )

    target = price_target_12m(fair_value, ke, dividend)
    if target is not None:
        target = max(target, 0.0)
    upside_fv = _upside(fair_value, share_price)
    upside_pt = _upside(target, share_price)
    rating = model_rating(upside_pt) if verified else None
    dcf_low, dcf_high = _sensitivity_range(state.get("valuation_sensitivity"))
    peer_median = None
    matrix = state.get("peer_comparison_matrix") or {}
    if isinstance(matrix.get("peer_medians"), dict):
        peer_median = _finite(matrix["peer_medians"].get("ev_to_ebitda"))

    fifty_low = _finite(inputs.get("fifty_two_week_low") or info.get("fiftyTwoWeekLow"))
    fifty_high = _finite(
        inputs.get("fifty_two_week_high") or info.get("fiftyTwoWeekHigh")
    )

    pack: Dict[str, Any] = {
        **identity,
        "valuation_method": valuation_method,
        "verified": verified,
        "share_price": share_price,
        "raw_dcf_value": raw_dcf,
        "dcf_value": dcf_value,
        "dcf_low": dcf_low,
        "dcf_high": dcf_high,
        "relative_value": relative_value,
        "relative_detail": relative_result,
        "relative_unavailable_reason": relative_info.get("reason"),
        "fair_value": fair_value,
        "dcf_weight": dcf_weight,
        "relative_weight": relative_weight,
        "price_target_12m": target,
        "cost_of_equity": ke,
        "indicated_dividend": dividend,
        "upside_to_fair_value": upside_fv,
        "upside_to_pt": upside_pt,
        "model_rating": rating,
        "model_rating_note": (
            "Model-implied band using a ±15% convention versus the last price, "
            "applied to the 12-month price target. Not an investment recommendation."
        ),
        "wacc": _finite(state.get("discount_rate")),
        "terminal_growth": _finite(applied.get("terminal_growth_rate")),
        "market_cap": market_cap,
        "market_ev": market_ev,
        "net_debt": debt - cash,
        "shares_outstanding": shares,
        "peer_median_ev_ebitda": peer_median,
        "fifty_two_week_low": fifty_low,
        "fifty_two_week_high": fifty_high,
        "price_history": state.get("price_history"),
        "pt_method": (
            "Today's blended fair value × (1 + cost of equity)"
            + (" minus indicated dividend" if dividend else "")
            + "."
        ),
    }
    pack["assumptions"] = _assumption_rows(
        state,
        dcf_weight=dcf_weight,
        relative_weight=relative_weight,
        relative_reason=relative_info.get("reason"),
        ke=ke,
        indicated_dividend=dividend,
    )
    pack["sources"] = build_source_register(state)
    pack["key_data"] = _key_data_rows(pack)
    pack["valuation_points"] = _valuation_points(pack)
    return pack


def _valuation_points(pack: Dict[str, Any]) -> List[Dict[str, Any]]:
    points: List[Dict[str, Any]] = []
    dcf = pack.get("dcf_value")
    if dcf is not None:
        points.append(
            {
                "label": "DCF",
                "value": dcf,
                "low": pack.get("dcf_low"),
                "high": pack.get("dcf_high"),
                "kind": "range",
            }
        )
    relative = pack.get("relative_value")
    if relative is not None:
        points.append(
            {
                "label": "Relative EV/EBITDA",
                "value": relative,
                "low": None,
                "high": None,
                "kind": "marker",
            }
        )
    fair = pack.get("fair_value")
    if fair is not None:
        points.append(
            {
                "label": "Blended fair value",
                "value": fair,
                "low": None,
                "high": None,
                "kind": "marker",
            }
        )
    target = pack.get("price_target_12m")
    if target is not None:
        points.append(
            {
                "label": "12-month price target",
                "value": target,
                "low": None,
                "high": None,
                "kind": "marker",
            }
        )
    price = pack.get("share_price")
    if price is not None:
        points.append(
            {
                "label": "Current market price",
                "value": price,
                "low": None,
                "high": None,
                "kind": "market",
            }
        )
    return points
