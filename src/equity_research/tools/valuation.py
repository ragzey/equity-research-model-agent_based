"""Validated WACC and three-stage FCFF valuation mathematics."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("ValuationTool")


def _require_finite(name: str, value: float) -> float:
    numeric = float(value)
    if numeric != numeric or numeric in (float("inf"), float("-inf")):
        raise ValueError(f"{name} must be finite.")
    return numeric


def calculate_wacc(
    share_price: float,
    shares_outstanding: float,
    total_debt: float,
    cost_of_equity: float,
    after_tax_cost_of_debt: float,
) -> Dict[str, Any]:
    """Calculate WACC using market equity and book debt as a documented proxy."""
    price = _require_finite("share_price", share_price)
    shares = _require_finite("shares_outstanding", shares_outstanding)
    debt = _require_finite("total_debt", total_debt)
    ke = _require_finite("cost_of_equity", cost_of_equity)
    after_tax_kd = _require_finite(
        "after_tax_cost_of_debt", after_tax_cost_of_debt
    )

    if price <= 0 or shares <= 0:
        raise ValueError("Share price and shares outstanding must be positive.")
    if debt < 0:
        raise ValueError("Total debt cannot be negative.")
    if ke <= 0 or after_tax_kd < 0:
        raise ValueError("Capital costs must be non-negative and cost of equity positive.")

    market_value_equity = price * shares
    total_capital = market_value_equity + debt
    weight_equity = market_value_equity / total_capital
    weight_debt = debt / total_capital
    wacc = weight_equity * ke + weight_debt * after_tax_kd

    logger.info(
        "WACC %.2f%% | equity weight %.1f%% | debt weight %.1f%%",
        wacc * 100,
        weight_equity * 100,
        weight_debt * 100,
    )
    return {
        "wacc": wacc,
        "market_value_equity": market_value_equity,
        "market_value_debt_proxy": debt,
        "weight_equity": weight_equity,
        "weight_debt": weight_debt,
    }


def project_3stage_fcff(
    base_revenue: float,
    base_ebit: float,
    sales_to_capital: float,
    high_growth_rate: float,
    high_growth_years: int = 5,
    transition_years: int = 5,
    terminal_growth_rate: float = 0.025,
    terminal_margin: float = 0.15,
    stable_sales_to_capital: float = 2.0,
    marginal_tax_rate: float = 0.21,
    interest_expense: float = 0.0,
    shares_outstanding: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """Project the operating P&L first, then unlevered FCFF from NOPAT − reinvestment."""
    revenue = _require_finite("base_revenue", base_revenue)
    ebit = _require_finite("base_ebit", base_ebit)
    initial_stc = _require_finite("sales_to_capital", sales_to_capital)
    stable_stc = _require_finite("stable_sales_to_capital", stable_sales_to_capital)
    growth = _require_finite("high_growth_rate", high_growth_rate)
    stable_growth = _require_finite("terminal_growth_rate", terminal_growth_rate)
    stable_margin = _require_finite("terminal_margin", terminal_margin)
    tax_rate = _require_finite("marginal_tax_rate", marginal_tax_rate)

    if revenue <= 0:
        raise ValueError("Base revenue must be positive.")
    if initial_stc <= 0 or stable_stc <= 0:
        raise ValueError("Sales-to-capital ratios must be positive.")
    if high_growth_years < 0 or transition_years < 0:
        raise ValueError("Forecast horizons cannot be negative.")
    if high_growth_years + transition_years <= 0:
        raise ValueError("At least one explicit forecast year is required.")
    if not 0 <= tax_rate < 1:
        raise ValueError("Marginal tax rate must be between 0 and 1.")
    if growth <= -1 or stable_growth <= -1:
        raise ValueError("Growth rates must exceed -100%.")
    interest = max(0.0, _require_finite("interest_expense", interest_expense))
    shares = None
    if shares_outstanding is not None:
        shares = _require_finite("shares_outstanding", shares_outstanding)
        if shares <= 0:
            shares = None

    initial_margin = ebit / revenue
    projections: List[Dict[str, Any]] = []
    current_revenue = revenue

    for year in range(1, high_growth_years + transition_years + 1):
        if year <= high_growth_years:
            year_growth = growth
            margin = initial_margin
            year_stc = initial_stc
            stage = "high_growth"
        else:
            # Reaches terminal assumptions exactly in the final transition year.
            step = (year - high_growth_years) / transition_years
            year_growth = growth + step * (stable_growth - growth)
            margin = initial_margin + step * (stable_margin - initial_margin)
            year_stc = initial_stc + step * (stable_stc - initial_stc)
            stage = "transition"

        prior_revenue = current_revenue
        current_revenue = prior_revenue * (1 + year_growth)
        projected_ebit = current_revenue * margin
        nopat = projected_ebit * (1 - tax_rate)
        revenue_change = current_revenue - prior_revenue
        reinvestment = revenue_change / year_stc
        fcff = nopat - reinvestment
        ebt = projected_ebit - interest
        tax = ebt * tax_rate
        net_income = ebt - tax
        row: Dict[str, Any] = {
            "year": year,
            "stage": stage,
            "growth_rate": year_growth,
            "revenue": current_revenue,
            "operating_margin": margin,
            "ebit": projected_ebit,
            "interest_expense": interest,
            "ebt": ebt,
            "tax": tax,
            "net_income": net_income,
            "nopat": nopat,
            "sales_to_capital": year_stc,
            "reinvestment": reinvestment,
            "fcff": fcff,
        }
        if shares is not None:
            row["eps"] = net_income / shares
        projections.append(row)

    return projections


def perform_3stage_dcf_valuation(
    base_revenue: float,
    base_ebit: float,
    sales_to_capital: float,
    high_growth_rate: float,
    wacc: float,
    terminal_wacc: float,
    shares_outstanding: float,
    total_debt: float,
    cash_and_equivalents: float,
    high_growth_years: int = 5,
    transition_years: int = 5,
    terminal_growth_rate: float = 0.025,
    terminal_margin: float = 0.15,
    stable_sales_to_capital: float = 2.0,
    marginal_tax_rate: float = 0.21,
    interest_expense: float = 0.0,
) -> Dict[str, Any]:
    """Run a three-stage enterprise DCF with annual, transitioning discount rates."""
    initial_wacc = _require_finite("wacc", wacc)
    mature_wacc = _require_finite("terminal_wacc", terminal_wacc)
    terminal_growth = _require_finite(
        "terminal_growth_rate", terminal_growth_rate
    )
    shares = _require_finite("shares_outstanding", shares_outstanding)
    debt = _require_finite("total_debt", total_debt)
    cash = _require_finite("cash_and_equivalents", cash_and_equivalents)

    if initial_wacc <= -1 or mature_wacc <= -1:
        raise ValueError("WACC must exceed -100%.")
    if mature_wacc <= terminal_growth:
        raise ValueError("Terminal WACC must exceed terminal growth.")
    if mature_wacc - terminal_growth < 0.01:
        raise ValueError("Terminal WACC-growth spread must be at least 1%.")
    if shares <= 0:
        raise ValueError("Shares outstanding must be positive.")
    if debt < 0 or cash < 0:
        raise ValueError("Debt and cash cannot be negative.")

    projections = project_3stage_fcff(
        base_revenue=base_revenue,
        base_ebit=base_ebit,
        sales_to_capital=sales_to_capital,
        high_growth_rate=high_growth_rate,
        high_growth_years=high_growth_years,
        transition_years=transition_years,
        terminal_growth_rate=terminal_growth,
        terminal_margin=terminal_margin,
        stable_sales_to_capital=stable_sales_to_capital,
        marginal_tax_rate=marginal_tax_rate,
        interest_expense=interest_expense,
        shares_outstanding=shares,
    )

    cumulative_discount_factor = 1.0
    pv_of_fcffs = 0.0
    for projection in projections:
        year = projection["year"]
        if year <= high_growth_years or transition_years == 0:
            current_wacc = initial_wacc
        else:
            step = (year - high_growth_years) / transition_years
            current_wacc = initial_wacc + step * (mature_wacc - initial_wacc)

        cumulative_discount_factor *= 1 + current_wacc
        projection["wacc"] = current_wacc
        projection["discount_factor"] = cumulative_discount_factor
        projection["pv_fcff"] = projection["fcff"] / cumulative_discount_factor
        pv_of_fcffs += projection["pv_fcff"]

    final_revenue = projections[-1]["revenue"]
    terminal_revenue = final_revenue * (1 + terminal_growth)
    terminal_ebit = terminal_revenue * terminal_margin
    terminal_nopat = terminal_ebit * (1 - marginal_tax_rate)
    terminal_reinvestment = (
        terminal_revenue - final_revenue
    ) / stable_sales_to_capital
    terminal_fcff = terminal_nopat - terminal_reinvestment

    terminal_value = terminal_fcff / (mature_wacc - terminal_growth)
    pv_of_terminal_value = terminal_value / cumulative_discount_factor
    enterprise_value = pv_of_fcffs + pv_of_terminal_value
    equity_value = enterprise_value + cash - debt
    intrinsic_value_per_share = equity_value / shares
    implied_incremental_roc = (
        terminal_margin * (1 - marginal_tax_rate) * stable_sales_to_capital
    )

    return {
        "projections": projections,
        "pv_of_fcffs": pv_of_fcffs,
        "terminal_revenue": terminal_revenue,
        "terminal_nopat": terminal_nopat,
        "terminal_reinvestment": terminal_reinvestment,
        "terminal_fcff": terminal_fcff,
        "terminal_value": terminal_value,
        "pv_of_terminal_value": pv_of_terminal_value,
        "terminal_value_share_of_enterprise_value": (
            pv_of_terminal_value / enterprise_value if enterprise_value else None
        ),
        "enterprise_value": enterprise_value,
        "equity_value": equity_value,
        "intrinsic_value_per_share": intrinsic_value_per_share,
        "wacc_applied": initial_wacc,
        "terminal_wacc_applied": mature_wacc,
        "terminal_growth_rate_applied": terminal_growth,
        "terminal_incremental_roc_implied": implied_incremental_roc,
        "pnl_method": (
            "Revenue grows at the labeled high-growth then transition rates. "
            "EBIT is revenue × the stage operating margin. Model net income is "
            "EBIT minus last-reported interest expense, taxed at the statutory "
            "marginal rate. FCFF stays unlevered: NOPAT minus reinvestment. "
            "Model EPS is model NI / diluted shares, not Street EPS."
        ),
    }


def _centered_terminal_growth_values(base: float) -> List[float]:
    """Five perpetuity-growth points around the applied g, clipped to 1.5%–5%."""
    floor = 0.015
    cap = 0.05
    offsets = (-0.01, -0.005, 0.0, 0.005, 0.01)
    unique: List[float] = []
    for offset in offsets:
        value = round(min(cap, max(floor, base + offset)), 6)
        if not unique or abs(value - unique[-1]) > 1e-9:
            unique.append(value)
    step = 0.0025
    guard = 0
    while len(unique) < 5 and guard < 24:
        guard += 1
        room_up = cap - unique[-1]
        room_down = unique[0] - floor
        if room_up >= room_down and room_up > 1e-9:
            nxt = round(min(cap, unique[-1] + step), 6)
            if abs(nxt - unique[-1]) > 1e-9:
                unique.append(nxt)
            else:
                step += 0.0025
        elif room_down > 1e-9:
            nxt = round(max(floor, unique[0] - step), 6)
            if abs(nxt - unique[0]) > 1e-9:
                unique.insert(0, nxt)
            else:
                step += 0.0025
        else:
            break
    return unique[:5]


def build_dcf_sensitivity_grid(
    *,
    base_revenue: float,
    base_ebit: float,
    sales_to_capital: float,
    high_growth_rate: float,
    base_wacc: float,
    base_terminal_wacc: float,
    base_terminal_growth: float,
    shares_outstanding: float,
    total_debt: float,
    cash_and_equivalents: float,
    high_growth_years: int,
    transition_years: int,
    terminal_margin: float,
    stable_sales_to_capital: float,
    marginal_tax_rate: float = 0.21,
) -> Dict[str, Any]:
    """
    Return a checkpoint-safe 5x5 WACC/perpetuity-growth sensitivity grid.

    Each WACC scenario shifts both initial and terminal WACC by the same delta,
    preserving the model's transition shape. Data is stored as lists rather
    than a pandas DataFrame so LangGraph checkpoints remain serializable.
    """
    base_rate = _require_finite("base_wacc", base_wacc)
    terminal_rate = _require_finite("base_terminal_wacc", base_terminal_wacc)
    base_growth = _require_finite("base_terminal_growth", base_terminal_growth)
    wacc_values = [base_rate + step for step in (-0.01, -0.005, 0.0, 0.005, 0.01)]
    growth_values = _centered_terminal_growth_values(base_growth)
    values: List[List[Any]] = []

    for growth in growth_values:
        row: List[Any] = []
        for scenario_wacc in wacc_values:
            delta = scenario_wacc - base_rate
            scenario_terminal_wacc = terminal_rate + delta
            try:
                result = perform_3stage_dcf_valuation(
                    base_revenue=base_revenue,
                    base_ebit=base_ebit,
                    sales_to_capital=sales_to_capital,
                    high_growth_rate=high_growth_rate,
                    wacc=scenario_wacc,
                    terminal_wacc=scenario_terminal_wacc,
                    shares_outstanding=shares_outstanding,
                    total_debt=total_debt,
                    cash_and_equivalents=cash_and_equivalents,
                    high_growth_years=high_growth_years,
                    transition_years=transition_years,
                    terminal_growth_rate=growth,
                    terminal_margin=terminal_margin,
                    stable_sales_to_capital=stable_sales_to_capital,
                    marginal_tax_rate=marginal_tax_rate,
                )
                row.append(round(result["intrinsic_value_per_share"], 4))
            except ValueError:
                row.append(None)
        values.append(row)

    return {
        "wacc_values": [round(value, 6) for value in wacc_values],
        "terminal_growth_values": growth_values,
        "intrinsic_value_per_share": values,
        "base_wacc": round(base_rate, 6),
        "base_terminal_growth": round(base_growth, 6),
        "methodology": (
            "Initial and terminal WACC shift together by -100/-50/0/+50/+100 "
            "bps; terminal growth is centered on the applied perpetuity g "
            "(±100/50 bp), clipped to 1.50%–5.00%."
        ),
    }
