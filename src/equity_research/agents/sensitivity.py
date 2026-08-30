"""Generate DCF sensitivity and operational bull/base/bear after Quant review."""

from typing import Any, Dict

from ..graphs.state import EquityResearchState
from ..tools.operating_scenarios import build_operating_scenarios
from ..tools.valuation import build_dcf_sensitivity_grid


def sensitivity_analyst_node(state: EquityResearchState) -> Dict[str, Any]:
    """Build a 5x5 WACC/g matrix and operating scenarios when valuation is verified."""
    if not state.get("is_math_verified"):
        return {"valuation_sensitivity": None, "operating_scenarios": None}

    summary = state.get("valuation_summary") or {}
    assumptions = summary.get("applied_dcf_assumptions") or {}
    inputs = summary.get("valuation_date_inputs") or {}
    dcf = summary.get("dcf") or {}

    required = (
        "base_revenue",
        "base_ebit",
        "sales_to_capital",
        "high_growth_rate",
        "high_growth_years",
        "transition_years",
        "terminal_margin",
        "stable_sales_to_capital",
    )
    missing = [key for key in required if assumptions.get(key) is None]
    if missing:
        raise ValueError(f"Missing sensitivity assumptions: {', '.join(missing)}")

    grid = build_dcf_sensitivity_grid(
        base_revenue=assumptions["base_revenue"],
        base_ebit=assumptions["base_ebit"],
        sales_to_capital=assumptions["sales_to_capital"],
        high_growth_rate=assumptions["high_growth_rate"],
        base_wacc=dcf["wacc_applied"],
        base_terminal_wacc=dcf["terminal_wacc_applied"],
        base_terminal_growth=dcf["terminal_growth_rate_applied"],
        shares_outstanding=inputs["shares_outstanding"],
        total_debt=inputs["total_debt"],
        cash_and_equivalents=inputs["cash_and_equivalents"],
        high_growth_years=assumptions["high_growth_years"],
        transition_years=assumptions["transition_years"],
        terminal_margin=assumptions["terminal_margin"],
        stable_sales_to_capital=assumptions["stable_sales_to_capital"],
    )
    scenarios = build_operating_scenarios(state)
    return {"valuation_sensitivity": grid, "operating_scenarios": scenarios}
