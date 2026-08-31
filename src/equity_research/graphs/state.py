"""Shared LangGraph ledger that every research agent reads from and writes to."""

import operator
from typing import Annotated, Any, Dict, List, Optional

from typing_extensions import TypedDict


class EquityResearchState(TypedDict):
    # 1. Inputs (The initial project brief)
    ticker: str  # E.g., "AAPL", "MSFT"
    target_year: str  # E.g., "2025" or "Q3 2026"
    target_bonds: Optional[List[str]]  # Corporate bond ISINs for Finnhub TRACE lookup
    competitor_tickers: Optional[List[str]]  # Peer group for relative valuation (3–5 tickers)

    # 2. Raw Material (Gathered by the Data Aggregator)
    income_statement: Optional[Dict[str, Any]]
    balance_sheet: Optional[Dict[str, Any]]
    cash_flow_statement: Optional[Dict[str, Any]]
    recent_news: Optional[List[Dict[str, str]]]
    web_research: Optional[List[Dict[str, str]]]  # Allowlisted fetched pages + excerpts
    sec_filing_chunks: Optional[List[str]]  # Raw text extracted from 10-K
    sec_filing_metadata: Optional[Dict[str, str]]  # URL, date, accession
    outstanding_bonds: Optional[List[Dict[str, Any]]]  # {maturity_years, ytm/yield, isin?}
    peer_metadata: Optional[Dict[str, Dict[str, Any]]]  # Aggregator pre-fetch per ticker
    market_info: Optional[Dict[str, Any]]  # Yahoo .info snapshot for the target
    consensus_growth: Optional[Dict[str, Any]]  # Labeled Yahoo consensus overlay
    street_snapshot: Optional[Dict[str, Any]]  # Yahoo target / EPS / rec, no invented quotes
    discovered_bond_isins: Optional[List[str]]  # 10-K harvested ISIN candidates
    discovered_peers: Optional[Dict[str, Any]]  # Yahoo/Finnhub similar-stock harvest
    price_history: Optional[Dict[str, Any]]  # Indexed 12-month closes vs market
    sec_filing_sections: Optional[Dict[str, str]]  # Labeled item_1a / item_7 text
    is_financial: bool
    valuation_method: Optional[str]  # corporate_fcff or unsupported_financial

    # 3. Model Calculations (Quant: Python WACC + three-stage FCFF)
    discount_rate: Optional[float]  # Calculated or assumed WACC
    calculated_dcf_value: Optional[float]  # Intrinsic value per share
    valuation_summary: Optional[Dict[str, Any]]  # Key financial metrics & growth rates

    # 4. Qualitative & Competitive Analyses
    business_risks: Optional[List[str]]  # MD&A / Risk Factors highlights
    competitive_advantages: Optional[str]  # Moat evaluation
    qualitative_analysis_summary: Optional[str]  # Reviewed SEC qualitative synthesis
    qualitative_evidence: Optional[List[Dict[str, str]]]  # Section-tagged filing quotes
    peer_comparison_matrix: Optional[Dict[str, Any]]  # Relative valuation vs peers
    peer_selection: Optional[Dict[str, Any]]  # Competitive analyst keep/drop rationale
    industry_outlook: Optional[str]  # Industry/macro demand narrative
    industry_macro_packet: Optional[Dict[str, Any]]  # Structured category/cycle/macro views
    company_products_packet: Optional[Dict[str, Any]]  # Item 1 products, mix, firm catalysts
    operations_packet: Optional[Dict[str, Any]]  # CCC, NWC, reinvestment views + metrics
    growth_path_packet: Optional[Dict[str, Any]]  # Scale-up horizon, STC fade, margin path
    valuation_mix_packet: Optional[Dict[str, Any]]  # Labeled DCF / relative mix from firm + industry
    event_calendar: Optional[List[Dict[str, Any]]]  # Yahoo earnings / dividend dates
    dcf_overrides: Optional[Dict[str, Any]]  # Architect candidates after reviewer veto
    assumption_audit: Optional[Dict[str, Any]]  # Independent assumption auditor revert packet
    valuation_sensitivity: Optional[Dict[str, Any]]  # Serializable WACC/g grid
    operating_scenarios: Optional[Dict[str, Any]]  # Bear/base/bull from operating menus
    agent_messages: Annotated[List[Dict[str, Any]], operator.add]  # Desk handoffs (append-only)
    audit_report: Optional[Dict[str, Any]]  # Independent auditor findings and corrections

    # 5. Quality Control (Updated by the Reviewer Agent)
    is_math_verified: bool  # Has the compliance check passed?
    reviewer_feedback: Optional[str]  # Guidance if calculations need revision
    review_findings: Optional[List[Dict[str, Any]]]  # Errors and warnings
    review_action: Optional[str]  # pass, warn, retry, or stop
    revision_count: int  # Tracking loop safety

    # 6. Final Deliverable (Compiled by the Lead Writer)
    final_equity_memo_path: Optional[str]  # Path to local Markdown output in outputs/reports/
    final_equity_memo_pdf_path: Optional[str]  # Presentation copy of the memo
