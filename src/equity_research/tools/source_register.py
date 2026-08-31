"""Build a ledger-only Sources and references register. No invented URLs."""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def _row(
    item: str,
    detail: str,
    source: str,
    url: str = "",
    used_for: str = "",
) -> Dict[str, str]:
    return {
        "item": item,
        "detail": detail,
        "source": source,
        "url": url,
        "used_for": used_for,
    }


def build_source_register(state: Dict[str, Any]) -> List[Dict[str, str]]:
    """Cite every research input the desk actually used on this run."""
    rows: List[Dict[str, str]] = []
    ticker = str(state.get("ticker") or "").upper()
    summary = state.get("valuation_summary") or {}
    inputs = summary.get("valuation_date_inputs") or {}
    cost_of_debt = summary.get("cost_of_debt") or {}
    details = cost_of_debt.get("details") or {}
    metadata = state.get("sec_filing_metadata") or {}
    selection = state.get("peer_selection") or {}
    discovered = state.get("discovered_peers") or {}
    consensus = state.get("consensus_growth") or {}
    history = state.get("price_history") or {}
    sections = state.get("sec_filing_sections") or {}
    evidence = state.get("qualitative_evidence") or []

    if metadata.get("filing_url") or sections.get("item_1a") or sections.get("item_7") or evidence:
        accession = metadata.get("accession_number") or "n/a"
        filing_date = metadata.get("filing_date") or "n/a"
        rows.append(
            _row(
                "SEC 10-K",
                (
                    f"{ticker} accession {accession}, filed {filing_date}. "
                    "Item 1, Item 1A, and Item 7 excerpts on the ledger."
                ),
                "SEC EDGAR",
                str(metadata.get("filing_url") or ""),
                "Qualitative analysis, risk phrases, ISIN harvest",
            )
        )

    rows.append(
        _row(
            "Financial statements and quote",
            (
                f"Income statement, balance sheet, cash flow, share price, beta, "
                f"shares, and 52-week range for {ticker}."
            ),
            "Yahoo Finance (yfinance)",
            "",
            "Aggregator, Quant, operating-cycle metrics, report pack",
        )
    )
    if inputs.get("cash_field_missing"):
        rows.append(
            _row(
                "Cash and equivalents",
                "Yahoo balance sheet had no usable cash field; Quant used 0.",
                "Yahoo Finance (yfinance)",
                "",
                "Net debt / DCF equity bridge",
            )
        )
    rows.append(
        _row(
            "Risk-free rate",
            "US 10-year Treasury yield from Yahoo ^TNX.",
            "Yahoo Finance ^TNX",
            "",
            "CAPM cost of equity and terminal-growth cap",
        )
    )

    harvest_sources = selection.get("sources_used") or discovered.get("sources_used") or []
    if "yahoo_recommendations" in harvest_sources:
        rows.append(
            _row(
                "Similar-stock harvest",
                "Yahoo Finance recommendationsbysymbol candidate list.",
                "Yahoo Finance",
                "",
                "Competitive analyst comparable set",
            )
        )
    if "finnhub_peers" in harvest_sources:
        rows.append(
            _row(
                "Similar-stock harvest",
                "Finnhub /stock/peers candidate list.",
                "Finnhub",
                "",
                "Competitive analyst comparable set",
            )
        )
    selected = selection.get("selected") or []
    if selected:
        rejected = selection.get("rejected") or []
        dropped = ", ".join(
            str(item.get("ticker") if isinstance(item, dict) else item)
            for item in rejected[:8]
        )
        rows.append(
            _row(
                "Comparable set",
                (
                    f"Selected {', '.join(str(item) for item in selected)}"
                    + (f". Dropped from harvest: {dropped}" if dropped else "")
                    + f". Mode: {selection.get('mode') or 'n/a'}."
                ),
                "Competitive analyst (clipped to harvested tickers)",
                "",
                "Peer EV/EBITDA cross-check",
            )
        )

    if consensus:
        rows.append(
            _row(
                "Growth overlay",
                str(consensus.get("label") or consensus.get("source") or "Yahoo growth field"),
                str(consensus.get("source") or "Yahoo Finance"),
                "",
                "High-growth rate candidate for the reviewer",
            )
        )

    street = state.get("street_snapshot") or {}
    info = state.get("market_info") or {}
    if (
        street.get("target_mean")
        or street.get("forward_eps")
        or street.get("n_analysts")
        or info.get("targetMeanPrice")
        or info.get("forwardEps")
    ):
        n_analysts = street.get("n_analysts")
        rows.append(
            _row(
                "Street consensus",
                (
                    "Yahoo targetMeanPrice, forwardEps, and analyst count"
                    + (f" ({int(n_analysts)} analysts)" if n_analysts is not None else "")
                    + ". Not management guidance."
                ),
                "Yahoo Finance",
                "",
                "Model versus Street table and thesis spine",
            )
        )

    calendar = state.get("event_calendar") or []
    filing_date = (state.get("sec_filing_metadata") or {}).get("filing_date")
    if calendar or filing_date:
        rows.append(
            _row(
                "Event calendar",
                "Yahoo earnings and dividend timestamps plus dated 10-K excerpts.",
                "Yahoo Finance / SEC 10-K ledger",
                "",
                "Catalyst register (dates are not invented)",
            )
        )

    bonds = state.get("outstanding_bonds") or []
    method = str(cost_of_debt.get("method_used") or "")
    method_l = method.lower()
    if bonds and ("trace" in method_l or "interpolation" in method_l):
        rows.append(
            _row(
                "Bond yields",
                f"{len(bonds)} Finnhub TRACE quote(s) on harvested or operator ISINs.",
                "Finnhub TRACE",
                "",
                "After-tax cost of debt",
            )
        )
    elif "damodaran" in method_l or "synthetic" in method_l or details.get("damodaran_spreads_as_of"):
        as_of = details.get("damodaran_spreads_as_of")
        rows.append(
            _row(
                "Default spreads",
                f"Damodaran synthetic rating spreads as-of {as_of or 'local snapshot'}.",
                str(details.get("source") or "data/damodaran_spreads.json"),
                "",
                "Cost of debt when TRACE quotes are unavailable",
            )
        )

    harvested_isins = state.get("discovered_bond_isins") or []
    if harvested_isins:
        rows.append(
            _row(
                "Bond identifiers",
                f"ISIN candidates harvested from the 10-K: {', '.join(harvested_isins[:12])}.",
                "SEC 10-K Item 7/8",
                str(metadata.get("filing_url") or ""),
                "TRACE lookup candidates (not a security master)",
            )
        )

    if history.get("points"):
        rows.append(
            _row(
                "Price history",
                (
                    f"Weekly adjusted closes for {ticker} vs "
                    f"{history.get('benchmark_label') or history.get('benchmark') or 'SPY'}, "
                    f"rebased from {history.get('start') or 'the window start'}."
                ),
                "Yahoo Finance",
                "",
                "Exhibit 2 indexed performance",
            )
        )

    for doc in state.get("web_research") or []:
        if not isinstance(doc, dict):
            continue
        url = str(doc.get("url") or "").strip()
        if not url:
            continue
        title = str(doc.get("title") or "Untitled").strip()
        publisher = str(doc.get("publisher") or "").strip()
        used_for = str(doc.get("used_for") or "market")
        tier = str(doc.get("tier") or "high_quality")
        label = {
            "market": "Market research",
            "industry": "Industry research",
            "firm": "Firm research",
        }.get(used_for, "Web research")
        rows.append(
            _row(
                label,
                title,
                f"{publisher or 'Allowlisted source'} ({tier.replace('_', ' ')})",
                url,
                {
                    "market": "Industry/macro category demand and market names",
                    "industry": "Industry/macro cycle, catalysts, and outlook",
                    "firm": "Company products, mix, and firm watch items",
                }.get(used_for, "Industry/macro and company/products packets"),
            )
        )

    rows.append(
        _row(
            "Equity risk premium and tax rate",
            "Market ERP 5.0% (desk policy). Statutory tax rate 21% on after-tax Kd.",
            "Desk policy",
            "",
            "WACC",
        )
    )
    rows.append(
        _row(
            "Valuation math",
            (
                "WACC, three-stage FCFF from the operating P&L, labeled DCF/relative "
                "mix (dcf_heavy 90/10, base 70/30, balanced 55/45), "
                "12-month price target, operating bull/base/bear, and ±15% "
                "model band are computed in Python. The LLM is not a source "
                "for those figures."
            ),
            "equity_research.tools.valuation / report_pack",
            "",
            "Fair value, DCF, model band",
        )
    )
    rows.append(
        _row(
            "Narrative agents",
            (
                "Competitive, Qualitative, industry/macro, company/products, operations, growth-path, valuation-mix, assumption architect, "
                "assumption reviewer, writer, and independent auditor call OpenAI or "
                "Gemini on ledger evidence. They may not invent tickers, URLs, or DCF "
                "inputs. Market/industry/firm URLs appear only when Python fetched an "
                "allowlisted page. The architect may only pick Python menu labels. The auditor "
                "may correct narrative; it may not rewrite WACC or DCF."
            ),
            "OpenAI or Gemini Chat Completions (narrative only)",
            "",
            "Prose, peer rationale, accept/reject reasons",
        )
    )
    return rows


def sources_markdown(rows: Optional[List[Dict[str, str]]]) -> str:
    if not rows:
        return "No sourced inputs were recorded on the ledger for this run."
    lines = [
        "| Input | What was used | Source | Used for |",
        "|---|---|---|---|",
    ]
    for row in rows:
        detail = row.get("detail") or ""
        url = (row.get("url") or "").strip()
        if url:
            detail = f"{detail} [link]({url})"
        lines.append(
            "| {item} | {detail} | {source} | {used_for} |".format(
                item=row.get("item") or "",
                detail=detail,
                source=row.get("source") or "",
                used_for=row.get("used_for") or "",
            )
        )
    return "\n".join(lines)
