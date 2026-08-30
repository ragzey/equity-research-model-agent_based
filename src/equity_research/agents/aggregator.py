"""Data Aggregator node: pulls market, SEC, and optional TRACE bond data onto the ledger."""

import logging
from typing import Any, Dict, List

from ..graphs.state import EquityResearchState
from ..tools.bond_identifiers import extract_bond_isins, merge_isin_lists
from ..tools.consensus import extract_consensus_growth
from ..tools.finnhub_bond import get_outstanding_bonds_for_ticker
from ..tools.firm_classifier import is_financial_services_firm
from ..tools.market_api import fetch_financial_statements
from ..tools.peer_analysis import fetch_peer_metadata
from ..tools.sec_api import fetch_latest_10k_sections

logger = logging.getLogger("DataAggregator")


def aggregator_node(state: EquityResearchState) -> Dict[str, Any]:
    """
    Junior-intern data pull: fundamentals → state; optional ISIN-driven TRACE bonds.

    Returns a partial state update dict for LangGraph (not a full state copy).
    """
    ticker = state["ticker"].strip().upper()
    logger.info("Aggregator starting data pull for %s", ticker)

    updates: Dict[str, Any] = {}
    info: Dict[str, Any] = {}

    # 1. Yahoo Finance statements
    financials = fetch_financial_statements(ticker)
    if financials:
        info = financials.get("info") or {}
        updates["income_statement"] = financials["income_statement"]
        updates["balance_sheet"] = financials["balance_sheet"]
        updates["cash_flow_statement"] = financials["cash_flow_statement"]
        updates["market_info"] = info or None
        logger.info("Saved income, balance, and cash-flow statements to ledger.")
    else:
        logger.error("Market API returned no financial statements for %s.", ticker)

    try:
        updates["consensus_growth"] = extract_consensus_growth(ticker, info)
    except Exception:
        logger.exception("Consensus growth overlay unavailable for %s", ticker)
        updates["consensus_growth"] = None

    is_financial = is_financial_services_firm(info)
    updates["is_financial"] = is_financial
    updates["valuation_method"] = (
        "unsupported_financial" if is_financial else "corporate_fcff"
    )
    if is_financial:
        logger.info(
            "%s is classified as a financial-services firm; FCFF valuation will be withheld.",
            ticker,
        )

    # 2. SEC 10-K excerpt for qualitative work (wrapped as list for state schema)
    filing_sections = fetch_latest_10k_sections(ticker)
    if filing_sections:
        chunks = [
            section[:50_000]
            for section in (
                filing_sections.get("item_1a"),
                filing_sections.get("item_7"),
            )
            if section
        ]
        updates["sec_filing_chunks"] = chunks or None
        updates["sec_filing_metadata"] = {
            key: str(filing_sections[key])
            for key in ("filing_url", "filing_date", "accession_number")
            if filing_sections.get(key)
        } or None
        logger.info(
            "Saved %d sourced 10-K section(s) to sec_filing_chunks.", len(chunks)
        )
    else:
        logger.warning("SEC 10-K excerpt unavailable for %s.", ticker)

    # 3. TRACE path: explicit CLI ISINs win; otherwise harvest 10-K debt candidates
    harvested: List[str] = []
    if filing_sections:
        harvest_text = "\n".join(
            section
            for section in (
                filing_sections.get("item_7"),
                filing_sections.get("item_8"),
            )
            if section
        )
        harvested = extract_bond_isins(harvest_text)
    updates["discovered_bond_isins"] = harvested or None
    if harvested:
        logger.info(
            "Harvested %d check-digit-valid 10-K ISIN candidate(s); not a security master.",
            len(harvested),
        )

    cli_bonds = merge_isin_lists(state.get("target_bonds") or [])
    if cli_bonds:
        isins_for_trace = cli_bonds
        logger.info(
            "Using %d CLI-supplied ISIN(s) for TRACE; harvested candidates are audit-only.",
            len(cli_bonds),
        )
    else:
        isins_for_trace = harvested
        if isins_for_trace:
            logger.info("No --target-bonds supplied; trying harvested 10-K ISIN candidates.")

    if isins_for_trace:
        try:
            bond_quotes = get_outstanding_bonds_for_ticker(isins_for_trace)
            updates["outstanding_bonds"] = bond_quotes
            if bond_quotes:
                logger.info("Stored %d structured bond YTM quote(s) on ledger.", len(bond_quotes))
            else:
                logger.warning("Finnhub returned no usable bond quotes; outstanding_bonds=None.")
        except ValueError as exc:
            logger.warning("Finnhub bond pull skipped: %s", exc)
            updates["outstanding_bonds"] = None
    else:
        updates["outstanding_bonds"] = None
        logger.info("No valid TRACE ISIN list; outstanding_bonds set to None.")

    # 4. Peer group metadata pre-fetch for Competitive Analyst
    competitor_tickers: Optional[List[str]] = state.get("competitor_tickers")
    peer_group = [ticker]
    if competitor_tickers:
        for raw in competitor_tickers:
            sym = raw.strip().upper()
            if sym and sym not in peer_group:
                peer_group.append(sym)
    if len(peer_group) > 1:
        logger.info(
            "Peer group configured (%d tickers): %s",
            len(peer_group),
            ", ".join(peer_group),
        )
    else:
        logger.info("No competitors supplied; fetching target metadata only.")

    peer_metadata: Dict[str, Dict[str, Any]] = {}
    for symbol in peer_group:
        try:
            peer_metadata[symbol] = fetch_peer_metadata(symbol)
        except Exception:
            logger.exception("Failed to fetch peer metadata for %s", symbol)
    updates["peer_metadata"] = peer_metadata or None

    return updates
