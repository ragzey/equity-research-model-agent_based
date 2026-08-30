"""Default ledger values when initializing a LangGraph run."""

from typing import Any, Dict, List, Optional

from .state import EquityResearchState


def initial_state(
    ticker: str,
    target_year: str,
    target_bonds: Optional[List[str]] = None,
    competitor_tickers: Optional[List[str]] = None,
) -> EquityResearchState:
    """Build a fully-populated starting state with safe defaults."""
    return EquityResearchState(
        ticker=ticker.strip().upper(),
        target_year=target_year,
        target_bonds=target_bonds,
        competitor_tickers=competitor_tickers,
        income_statement=None,
        balance_sheet=None,
        cash_flow_statement=None,
        recent_news=None,
        sec_filing_chunks=None,
        sec_filing_sections=None,
        sec_filing_metadata=None,
        outstanding_bonds=None,
        peer_metadata=None,
        market_info=None,
        consensus_growth=None,
        street_snapshot=None,
        discovered_bond_isins=None,
        discovered_peers=None,
        price_history=None,
        is_financial=False,
        valuation_method=None,
        discount_rate=None,
        calculated_dcf_value=None,
        valuation_summary=None,
        business_risks=None,
        competitive_advantages=None,
        qualitative_analysis_summary=None,
        qualitative_evidence=None,
        peer_comparison_matrix=None,
        peer_selection=None,
        industry_outlook=None,
        industry_macro_packet=None,
        operations_packet=None,
        event_calendar=None,
        dcf_overrides=None,
        valuation_sensitivity=None,
        operating_scenarios=None,
        agent_messages=[],
        audit_report=None,
        is_math_verified=False,
        reviewer_feedback=None,
        review_findings=None,
        review_action=None,
        revision_count=0,
        final_equity_memo_path=None,
        final_equity_memo_pdf_path=None,
    )


def as_dict(state: EquityResearchState) -> Dict[str, Any]:
    """TypedDict → plain dict (useful for logging and tests)."""
    return dict(state)
