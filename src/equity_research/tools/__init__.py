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
from .qual_to_quant import generate_valuation_overrides
from .sec_api import (
    fetch_latest_10k_sections,
    fetch_latest_10k_text,
    fetch_sec_section,
    get_cik_for_ticker,
)
from .peer_discovery import (
    apply_named_picks,
    discover_peer_candidates,
    rank_peer_candidates,
)
from .price_history import fetch_rebased_price_history
from .report_pack import build_report_pack
from .valuation import (
    build_dcf_sensitivity_grid,
    calculate_wacc,
    perform_3stage_dcf_valuation,
)

__all__ = [
    "build_peer_comparison_matrix",
    "build_dcf_sensitivity_grid",
    "calculate_cost_of_debt",
    "calculate_wacc",
    "classify_firm_and_adjust_assumptions",
    "extract_ebit_and_interest",
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
    "fetch_sec_section",
    "build_report_pack",
    "generate_valuation_overrides",
    "get_cik_for_ticker",
    "get_outstanding_bonds_for_ticker",
    "is_financial_services_firm",
    "perform_3stage_dcf_valuation",
]
