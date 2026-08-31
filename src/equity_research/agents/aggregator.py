"""Data Aggregator node: pulls market, SEC, and optional TRACE bond data onto the ledger."""

import logging
from typing import Any, Dict, List

from ..graphs.state import EquityResearchState
from ..tools.bond_identifiers import extract_bond_isins, merge_isin_lists
from ..tools.consensus import extract_consensus_growth
from ..tools.catalysts import extract_market_events
from ..tools.street import extract_street_snapshot
from ..tools.finnhub_bond import get_outstanding_bonds_for_ticker
from ..tools.firm_classifier import is_financial_services_firm
from ..tools.market_api import fetch_financial_statements
from ..tools.peer_discovery import discover_peer_candidates, hydrate_peer_metadata
from ..tools.price_history import fetch_rebased_price_history
from ..tools.sec_api import (
    fetch_latest_10k_sections,
    resolve_listed_symbol,
    sourced_filing_payload,
)

logger = logging.getLogger("DataAggregator")


def aggregator_node(state: EquityResearchState) -> Dict[str, Any]:
    """
    Junior-intern data pull: fundamentals → state; optional ISIN-driven TRACE bonds.

    Returns a partial state update dict for LangGraph (not a full state copy).
    """
    ticker = state["ticker"].strip().upper()
    listed = resolve_listed_symbol(ticker) or ticker
    if listed != ticker:
        logger.info("Mapped issuer %s to listed ticker %s.", ticker, listed)
    ticker = listed
    logger.info("Aggregator starting data pull for %s", ticker)

    updates: Dict[str, Any] = {"ticker": ticker}
    info: Dict[str, Any] = {}

    # 1. Yahoo Finance statements
    financials = fetch_financial_statements(ticker)
    if financials:
        info = financials.get("info") or {}
        updates["income_statement"] = financials["income_statement"]
        updates["balance_sheet"] = financials["balance_sheet"]
        updates["cash_flow_statement"] = financials["cash_flow_statement"]
        updates["market_info"] = info or None
        updates["event_calendar"] = extract_market_events(info) or None
        logger.info("Saved income, balance, and cash-flow statements to ledger.")
    else:
        logger.error("Market API returned no financial statements for %s.", ticker)
        updates["event_calendar"] = None

    try:
        consensus = extract_consensus_growth(ticker, info)
        updates["consensus_growth"] = consensus
        updates["street_snapshot"] = extract_street_snapshot(info, consensus)
    except Exception:
        logger.exception("Consensus / Street overlay unavailable for %s", ticker)
        updates["consensus_growth"] = None
        updates["street_snapshot"] = None

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
        updates.update(sourced_filing_payload(filing_sections))
        logger.info(
            "Saved sourced 10-K sections | Item 1A: %d chars | Item 7: %d chars",
            len((updates.get("sec_filing_sections") or {}).get("item_1a") or ""),
            len((updates.get("sec_filing_sections") or {}).get("item_7") or ""),
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

    # 4. Peer harvest for the competitive analyst (operator names are optional)
    pinned_peers: List[str] = []
    for raw in state.get("competitor_tickers") or []:
        symbol = raw.strip().upper()
        if symbol and symbol != ticker and symbol not in pinned_peers:
            pinned_peers.append(symbol)

    discovered: Dict[str, Any] = {"target": ticker, "candidates": [], "sources_used": []}
    lookup_symbols = [ticker] + pinned_peers
    if pinned_peers:
        logger.info(
            "Operator pinned %d peer(s); skipping open-ended discovery: %s",
            len(pinned_peers),
            ", ".join(pinned_peers),
        )
        updates["competitor_tickers"] = pinned_peers
    else:
        try:
            discovered = discover_peer_candidates(ticker)
        except Exception:
            logger.exception("Peer discovery failed for %s", ticker)
            discovered = {"target": ticker, "candidates": [], "sources_used": []}
        lookup_symbols.extend(
            str(row.get("ticker"))
            for row in (discovered.get("candidates") or [])
            if row.get("ticker")
        )
        logger.info(
            "No peers supplied; harvested %d similar-stock candidate(s) from %s.",
            len(discovered.get("candidates") or []),
            ", ".join(discovered.get("sources_used") or []) or "no source",
        )
    updates["discovered_peers"] = discovered

    peer_metadata = hydrate_peer_metadata(lookup_symbols)
    updates["peer_metadata"] = peer_metadata or None

    try:
        updates["price_history"] = fetch_rebased_price_history(ticker)
    except Exception:
        logger.exception("Indexed price history unavailable for %s", ticker)
        updates["price_history"] = None

    return updates
