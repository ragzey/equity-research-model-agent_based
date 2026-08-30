"""Shared tools for market data, filings, and search."""

from .peer_analysis import (
    build_peer_comparison_matrix,
    fetch_peer_metadata,
    fetch_relative_valuation_metrics,
)
from .finnhub_bond import get_outstanding_bonds_for_ticker
from .debt_analysis import calculate_cost_of_debt, extract_ebit_and_interest
from .firm_classifier import (
    classify_firm_and_adjust_assumptions,
    extract_operating_baseline,
    is_financial_services_firm,
)
from .market_api import fetch_financial_statements
from .catalysts import build_catalyst_register, extract_market_events
from .operating_cycle import clip_sales_to_capital, measure_operating_cycle
from .operating_scenarios import build_operating_scenarios
from .street import build_thesis_pack, extract_street_snapshot
from .qual_to_quant import generate_valuation_overrides
from .assumption_menus import (
    apply_architect_choices,
    build_assumption_bundle,
    clip_terminal_growth,
    policy_terminal_growth,
)
from .sec_api import (
    fetch_latest_10k_sections,
    fetch_latest_10k_text,
    fetch_sec_section,
    get_cik_for_ticker,
    sourced_filing_payload,
)
from .peer_discovery import (
    apply_named_picks,
    clip_rejected_picks,
    discover_peer_candidates,
    rank_peer_candidates,
)
from .price_history import fetch_rebased_price_history
from .report_pack import build_report_pack
from .source_register import build_source_register
from .valuation import (
    build_dcf_sensitivity_grid,
    calculate_wacc,
    perform_3stage_dcf_valuation,
)

__all__ = [
    "build_peer_comparison_matrix",
    "build_dcf_sensitivity_grid",
    "build_catalyst_register",
    "build_operating_scenarios",
    "build_thesis_pack",
    "extract_street_snapshot",
    "calculate_cost_of_debt",
    "calculate_wacc",
    "classify_firm_and_adjust_assumptions",
    "extract_ebit_and_interest",
    "extract_market_events",
    "extract_operating_baseline",
    "fetch_financial_statements",
    "fetch_latest_10k_sections",
    "fetch_latest_10k_text",
    "fetch_peer_metadata",
    "fetch_relative_valuation_metrics",
    "fetch_rebased_price_history",
    "discover_peer_candidates",
    "rank_peer_candidates",
    "apply_named_picks",
    "clip_rejected_picks",
    "fetch_sec_section",
    "sourced_filing_payload",
    "build_report_pack",
    "build_source_register",
    "build_assumption_bundle",
    "apply_architect_choices",
    "clip_sales_to_capital",
    "clip_terminal_growth",
    "policy_terminal_growth",
    "measure_operating_cycle",
    "generate_valuation_overrides",
    "get_cik_for_ticker",
    "get_outstanding_bonds_for_ticker",
    "is_financial_services_firm",
    "perform_3stage_dcf_valuation",
]
