"""Lead writer: frozen Python numbers plus LLM-synthesized narrative."""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Any, Dict, List
import json

from ..graphs.desk import WRITER, format_transcript, inbox
from ..graphs.state import EquityResearchState
from ..prompts.desk import WRITER_SYSTEM, WRITER_USER
from ..tools.pdf_memo import write_memo_pdf
from ..tools.report_pack import build_report_pack
from ..utils.llm_client import LLMCallError, chat_json

logger = logging.getLogger("LeadWriter")


def _fmt_number(value: Any, decimals: int = 2) -> str:
    if value is None:
        return "N/A"
    return f"{float(value):,.{decimals}f}"


def _fmt_percent(value: Any) -> str:
    if value is None:
        return "N/A"
    return f"{float(value):.2%}"


def _fmt_usd(value: Any) -> str:
    if value is None:
        return "N/A"
    return f"${float(value):,.2f}"


def _valuation_signal(current_price: Any, intrinsic_value: Any, verified: bool) -> str:
    """Kept for tests and older callers; cover copy now uses the report pack."""
    if not verified or current_price is None or intrinsic_value is None:
        return "Withheld — valuation not mathematically verified."
    price = float(current_price)
    value = max(float(intrinsic_value), 0.0)
    if price <= 0:
        return "Withheld — invalid current price."
    gap = value / price - 1
    if gap >= 0.15:
        label = "Model-implied undervaluation"
    elif gap <= -0.15:
        label = "Model-implied overvaluation"
    else:
        label = "Model-implied broadly fair value"
    return f"{label} ({gap:+.1%} vs current price); not an investment recommendation."


def _header_block(state: EquityResearchState, pack: Dict[str, Any]) -> str:
    name = pack.get("company_name") or state["ticker"]
    ticker = pack.get("ticker") or state["ticker"]
    industry = pack.get("industry") or pack.get("sector") or "n/a"
    country = pack.get("country") or "n/a"
    rating = (pack.get("model_rating") or "Withheld").upper()
    return (
        f"# {name}  |  {ticker}  |  {industry}  |  {country}\n\n"
        f"**Model-implied {rating}** with a 12-month price target of "
        f"{_fmt_usd(pack.get('price_target_12m'))} versus a last price of "
        f"{_fmt_usd(pack.get('share_price'))}.\n\n"
        f"Equity Research  |  Valuation date {date.today().isoformat()}  |  "
        f"Horizon {state.get('target_year')}\n"
    )


def _exec_summary(pack: Dict[str, Any], qualitative_lead: str) -> str:
    rating = (pack.get("model_rating") or "Withheld").upper()
    dcf_w = pack.get("dcf_weight")
    rel_w = pack.get("relative_weight")
    mix = "100% DCF"
    if dcf_w is not None and rel_w is not None and rel_w > 0:
        mix = f"{dcf_w:.0%} three-stage FCFF DCF of {_fmt_usd(pack.get('dcf_value'))} and {rel_w:.0%} peer-median EV/EBITDA of {_fmt_usd(pack.get('relative_value'))}"
    elif pack.get("dcf_value") is not None:
        mix = f"100% three-stage FCFF DCF of {_fmt_usd(pack.get('dcf_value'))}"
    upside = pack.get("upside_to_pt")
    upside_text = _fmt_percent(upside) if upside is not None else "N/A"
    if upside is not None:
        upside_text = f"{upside:+.1%}"
    relative_gap = pack.get("relative_unavailable_reason")
    relative_clause = ""
    if relative_gap and (pack.get("relative_weight") or 0) == 0:
        relative_clause = f" {relative_gap}"
    lead = qualitative_lead.strip()
    if len(lead) > 520:
        lead = lead[:517].rsplit(" ", 1)[0] + "..."
    return f"""## Executive summary

**{rating}** · 12-month price target {_fmt_usd(pack.get("price_target_12m"))} · Share price {_fmt_usd(pack.get("share_price"))} · Upside {upside_text}

As of {date.today().isoformat()}, the model's blended fair value is {_fmt_usd(pack.get("fair_value"))} per share ({mix}).{relative_clause} Rolling that value forward one year at the {_fmt_percent(pack.get("cost_of_equity"))} cost of equity{" after subtracting the indicated dividend" if pack.get("indicated_dividend") else ""} produces a 12-month price target of {_fmt_usd(pack.get("price_target_12m"))}, {upside_text} versus the last price. Using a ±15% band around the current price, the model band is **{rating}**. This is model output, not an investment recommendation.

{lead or "Qualitative filing synthesis was not available."}
"""


def _key_data_markdown(pack: Dict[str, Any]) -> str:
    rows = pack.get("key_data") or []
    if not rows:
        return "Key data unavailable."
    lines = ["| Item | Value |", "|---|---|"]
    for row in rows:
        lines.append(f"| {row.get('label', '')} | {row.get('value', '')} |")
    return "\n".join(lines)


def _valuation_exhibit(pack: Dict[str, Any]) -> str:
    points = pack.get("valuation_points") or []
    if not points:
        return "Valuation-versus-market exhibit unavailable."
    lines = [
        "| Method | Low | Mid / point | High |",
        "|---|---:|---:|---:|",
    ]
    for point in points:
        lines.append(
            "| {label} | {low} | {mid} | {high} |".format(
                label=point.get("label", ""),
                low=_fmt_usd(point.get("low")) if point.get("low") is not None else "—",
                mid=_fmt_usd(point.get("value")),
                high=_fmt_usd(point.get("high")) if point.get("high") is not None else "—",
            )
        )
    takeaway = (
        "The DCF range is the WACC / terminal-growth sensitivity grid. "
        "Relative value re-rates trailing or implied EBITDA at the peer-median "
        "EV/EBITDA. Blended fair value is today's 70/30 mix when peers exist; "
        "the 12-month target compounds that value at the cost of equity."
    )
    if pack.get("relative_value") is None:
        takeaway = (
            "Peer EV/EBITDA was unavailable, so fair value is the DCF. "
            + takeaway
        )
    return "\n".join(lines) + f"\n\n*{takeaway}*"


def _assumption_table(pack: Dict[str, Any]) -> str:
    rows = pack.get("assumptions") or []
    if not rows:
        return "Assumption register unavailable."
    lines = [
        "| Assumption | Value | Justification | Source |",
        "|---|---|---|---|",
    ]
    for row in rows:
        justification = " ".join(str(row.get("justification") or "").split())
        lines.append(
            "| {item} | {value} | {justification} | {source} |".format(
                item=row.get("item", ""),
                value=row.get("value", ""),
                justification=justification,
                source=row.get("source", ""),
            )
        )
    return "\n".join(lines)


def _wacc_appendix(summary: Dict[str, Any], state: EquityResearchState) -> str:
    inputs = summary.get("valuation_date_inputs") or {}
    classification = summary.get("firm_classification") or {}
    cost_of_debt = summary.get("cost_of_debt") or {}
    wacc_block = summary.get("wacc") or {}
    details = cost_of_debt.get("details") or {}
    return f"""- Risk-free rate: {_fmt_percent(inputs.get("risk_free_rate"))}
- Beta: {_fmt_number(inputs.get("beta"))}
- Equity risk premium: {_fmt_percent(inputs.get("market_equity_risk_premium"))}
- Size premium: {_fmt_percent(classification.get("size_premium"))}
- Company-specific premium: {_fmt_percent(inputs.get("company_specific_risk_premium"))}
- Cost of equity: {_fmt_percent(summary.get("cost_of_equity"))}
- Cost of debt method: {cost_of_debt.get("method_used", "N/A")}
- Pre-tax cost of debt: {_fmt_percent(cost_of_debt.get("pre_tax_cost_of_debt"))}
- After-tax cost of debt: {_fmt_percent(cost_of_debt.get("after_tax_cost_of_debt"))}
- Equity weight: {_fmt_percent(wacc_block.get("weight_equity"))}
- Debt weight (book debt proxy): {_fmt_percent(wacc_block.get("weight_debt"))}
- WACC: {_fmt_percent(state.get("discount_rate"))}
- Damodaran spreads as-of: {details.get("damodaran_spreads_as_of", "n/a")}
"""


def _peer_table(matrix: Dict[str, Any]) -> str:
    metrics = matrix.get("metrics") or {}
    if not metrics:
        return "Peer comparison unavailable."
    lines = [
        "| Ticker | Forward P/E | EV/EBITDA | Op. margin | Revenue growth |",
        "|---|---:|---:|---:|---:|",
    ]
    for ticker, values in metrics.items():
        lines.append(
            "| {ticker} | {pe} | {ev} | {margin} | {growth} |".format(
                ticker=ticker,
                pe=_fmt_number(values.get("forward_pe")),
                ev=_fmt_number(values.get("ev_to_ebitda")),
                margin=(
                    f"{_fmt_number(values.get('operating_margin_pct'))}%"
                    if values.get("operating_margin_pct") is not None
                    else "N/A"
                ),
                growth=(
                    f"{_fmt_number(values.get('revenue_growth_yoy_pct'))}%"
                    if values.get("revenue_growth_yoy_pct") is not None
                    else "N/A"
                ),
            )
        )
    return "\n".join(lines)


def _sensitivity_table(grid: Dict[str, Any]) -> str:
    if not grid:
        return "Sensitivity analysis unavailable."
    wacc_values = grid.get("wacc_values") or []
    growth_values = grid.get("terminal_growth_values") or []
    values = grid.get("intrinsic_value_per_share") or []
    base_wacc = grid.get("base_wacc")
    base_growth = grid.get("base_terminal_growth")
    header = "| Terminal growth \\ WACC | " + " | ".join(
        _fmt_percent(value) for value in wacc_values
    ) + " |"
    separator = "|---|" + "---:|" * len(wacc_values)
    lines = [header, separator]
    for growth, row in zip(growth_values, values):
        cells = []
        for wacc, value in zip(wacc_values, row):
            cell = "N/A" if value is None else f"${value:,.2f}"
            if (
                value is not None
                and base_wacc is not None
                and base_growth is not None
                and abs(float(wacc) - float(base_wacc)) < 1e-8
                and abs(float(growth) - float(base_growth)) < 1e-8
            ):
                cell = f"**{cell} (base)**"
            cells.append(cell)
        lines.append(f"| {_fmt_percent(growth)} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _projection_table(projections: List[Dict[str, Any]]) -> str:
    if not projections:
        return "Explicit DCF projections unavailable."
    lines = [
        "| Year | Stage | Revenue | EBIT | NOPAT | Reinvestment | FCFF |",
        "|---:|---|---:|---:|---:|---:|---:|",
    ]
    for row in projections:
        lines.append(
            "| {year} | {stage} | {revenue} | {ebit} | {nopat} | "
            "{reinvestment} | {fcff} |".format(
                year=row.get("year", "N/A"),
                stage=row.get("stage", "N/A"),
                revenue=_fmt_number(row.get("revenue")),
                ebit=_fmt_number(row.get("ebit")),
                nopat=_fmt_number(row.get("nopat")),
                reinvestment=_fmt_number(row.get("reinvestment")),
                fcff=_fmt_number(row.get("fcff")),
            )
        )
    return "\n".join(lines)


def _filing_evidence(state: EquityResearchState) -> str:
    evidence = state.get("qualitative_evidence") or []
    metadata = state.get("sec_filing_metadata") or {}
    lines = []
    if metadata.get("filing_url"):
        lines.append(
            f"- Source filing: [{metadata.get('filing_date', '10-K')}]"
            f"({metadata['filing_url']})"
        )
    lines.extend(
        f"- **{item.get('section', '10-K')}:** {item.get('excerpt', '')}"
        for item in evidence
    )
    return "\n".join(lines) or "No section-tagged filing evidence was captured."


def _cash_flow_warning(findings: List[Dict[str, Any]]) -> str:
    distress = next(
        (
            item
            for item in findings
            if item.get("code") == "PERSISTENT_NEGATIVE_FCFF"
        ),
        None,
    )
    if not distress:
        return ""
    return (
        "\n## Cash-flow durability / solvency watch\n\n"
        f"{distress['message']}\n\n"
        "This screen requires separate liquidity, covenant, maturity, and "
        "going-concern analysis before drawing a credit conclusion.\n"
    )


def _scope_note(valuation_method: str) -> str:
    if valuation_method != "unsupported_financial":
        return ""
    return """
## Scope

This pipeline values non-financial operating companies with WACC and three-stage
FCFF. Banks, insurers, brokers, and other financial firms are out of scope.
"""


def _desk_section(state: EquityResearchState) -> str:
    decisions = (state.get("dcf_overrides") or {}).get("decisions") or []
    mode = (state.get("dcf_overrides") or {}).get("desk_mode") or "llm"
    decision_lines = (
        "\n".join(
            f"- **{row.get('key')}:** {row.get('action')} — {row.get('reason')}"
            for row in decisions
        )
        or "- No accept/reject decisions recorded."
    )
    notes = inbox(state.get("agent_messages"), WRITER)
    note_text = "\n".join(
        f"- {item.get('body')}" for item in notes if item.get("kind") == "desk_notes"
    ) or "- No reviewer notes to writer."
    return (
        f"**Desk mode:** {mode}\n\n"
        "### Handoffs\n\n"
        f"{format_transcript(state.get('agent_messages'))}\n\n"
        "### Assumption decisions (what Quant was allowed to use)\n\n"
        f"{decision_lines}\n\n"
        "### Notes to the writer\n\n"
        f"{note_text}"
    )


def _synthesize_narratives(state: EquityResearchState, frozen: Dict[str, Any]) -> Dict[str, str]:
    industry = state.get("industry_outlook") or "Industry outlook unavailable."
    qualitative = state.get("qualitative_analysis_summary") or "Qualitative assessment unavailable."
    payload = chat_json(
        [
            {"role": "system", "content": WRITER_SYSTEM},
            {
                "role": "user",
                "content": WRITER_USER.format(
                    ticker=state["ticker"],
                    frozen_json=json.dumps(frozen, indent=2, default=str),
                    transcript=format_transcript(state.get("agent_messages")),
                    decisions_json=json.dumps(
                        (state.get("dcf_overrides") or {}).get("decisions") or [],
                        indent=2,
                        default=str,
                    ),
                    qualitative=qualitative[:6000],
                    outlook=industry[:4000],
                ),
            },
        ],
        timeout=90,
        required=True,
    ) or {}
    desk_synthesis = str(payload.get("desk_synthesis") or "").strip()
    if not desk_synthesis:
        raise LLMCallError("Lead writer did not return a desk synthesis.")
    return {
        "industry_outlook": str(payload.get("industry_outlook") or industry),
        "qualitative_narrative": str(
            payload.get("qualitative_narrative") or qualitative
        ),
        "desk_synthesis": desk_synthesis,
    }


def _write_gui_sidecar(
    state: EquityResearchState,
    pack: Dict[str, Any],
    report_path: Path,
    pdf_ok: bool,
) -> None:
    """Persist cover/chart payload so CLI runs open in the GUI without a second pass."""
    classification = (state.get("valuation_summary") or {}).get("firm_classification") or {}
    overrides = state.get("dcf_overrides") or {}
    handoffs = [
        {
            "from_agent": item.get("from_agent"),
            "to_agent": item.get("to_agent"),
            "kind": item.get("kind"),
            "body": item.get("body"),
        }
        for item in state.get("agent_messages") or []
    ]
    payload = {
        "ticker": state.get("ticker"),
        "company_name": pack.get("company_name"),
        "industry": pack.get("industry"),
        "sector": pack.get("sector"),
        "country": pack.get("country"),
        "target_year": state.get("target_year"),
        "firm_type": classification.get("firm_type"),
        "valuation_method": pack.get("valuation_method")
        or state.get("valuation_method"),
        "verified": bool(state.get("is_math_verified")),
        "share_price": pack.get("share_price"),
        "fair_value": pack.get("fair_value"),
        "price_target_12m": pack.get("price_target_12m"),
        "upside_to_pt": pack.get("upside_to_pt"),
        "upside_to_fair_value": pack.get("upside_to_fair_value"),
        "model_rating": pack.get("model_rating"),
        "model_rating_note": pack.get("model_rating_note"),
        "dcf_value": pack.get("dcf_value"),
        "relative_value": pack.get("relative_value"),
        "wacc": state.get("discount_rate"),
        "cost_of_equity": pack.get("cost_of_equity"),
        "desk_mode": overrides.get("desk_mode"),
        "decisions": overrides.get("decisions") or [],
        "rationales": overrides.get("rationales") or {},
        "handoffs": handoffs,
        "peer_selection": state.get("peer_selection"),
        "report_pack": pack,
        "memo_name": report_path.name,
        "pdf_name": report_path.name.replace("_memo.md", "_memo.pdf") if pdf_ok else None,
        "has_pdf": pdf_ok,
    }
    sidecar = report_path.with_name(report_path.name.replace("_memo.md", "_gui.json"))
    sidecar.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def lead_writer_node(state: EquityResearchState) -> Dict[str, Any]:
    """Write a sourced Markdown research note from frozen numbers and LLM narrative."""
    ticker = state["ticker"].strip().upper()
    summary = dict(state.get("valuation_summary") or {})
    inputs = summary.get("valuation_date_inputs") or {}
    classification = summary.get("firm_classification") or {}
    applied = summary.get("applied_dcf_assumptions") or {}
    dcf = summary.get("dcf") or {}
    valuation_method = summary.get("valuation_method") or state.get(
        "valuation_method"
    ) or "corporate_fcff"
    findings: List[Dict[str, Any]] = state.get("review_findings") or []
    raw_intrinsic = state.get("calculated_dcf_value")
    display_intrinsic = (
        max(float(raw_intrinsic), 0.0) if raw_intrinsic is not None else None
    )
    pack = build_report_pack(dict(state))
    summary["report_pack"] = pack

    findings_md = (
        "\n".join(
            f"- **{finding['severity'].upper()} — {finding['code']}:** "
            f"{finding['message']}"
            for finding in findings
        )
        or "- No review findings."
    )
    rationales = (state.get("dcf_overrides") or {}).get("rationales") or {}
    overrides_md = (
        "\n".join(f"- **{key}:** {value}" for key, value in rationales.items())
        or "- No qualitative/competitive override rationale available."
    )
    frozen = {
        "share_price": pack.get("share_price") or inputs.get("share_price"),
        "fair_value": pack.get("fair_value"),
        "price_target_12m": pack.get("price_target_12m"),
        "model_rating": pack.get("model_rating"),
        "selected_peers": (state.get("peer_selection") or {}).get("selected")
        or (state.get("peer_comparison_matrix") or {}).get("competitors"),
        "peer_selection_mode": (state.get("peer_selection") or {}).get("mode"),
        "dcf_value": pack.get("dcf_value"),
        "relative_value": pack.get("relative_value"),
        "discount_rate": state.get("discount_rate"),
        "raw_intrinsic_value_per_share": raw_intrinsic,
        "display_intrinsic_value_per_share": display_intrinsic,
        "high_growth_rate": applied.get("high_growth_rate"),
        "valuation_signal": _valuation_signal(
            inputs.get("share_price"),
            raw_intrinsic,
            bool(state.get("is_math_verified")),
        ),
        "is_math_verified": bool(state.get("is_math_verified")),
    }
    narratives = _synthesize_narratives(state, frozen)
    desk_synthesis = narratives["desk_synthesis"]
    desk_synthesis_md = (
        f"\n\n### Writer synthesis\n\n{desk_synthesis}" if desk_synthesis else ""
    )
    qualitative_lead = narratives["qualitative_narrative"] or ""
    risks = state.get("business_risks") or []
    risk_md = (
        "\n".join(f"- {item}" for item in risks)
        or "- See the qualitative section and Item 1A excerpts below."
    )
    rating = (pack.get("model_rating") or "Withheld").upper()
    relative_detail = pack.get("relative_detail") or {}
    relative_method = relative_detail.get("method") or pack.get(
        "relative_unavailable_reason"
    ) or "n/a"
    peer_selection = state.get("peer_selection") or {}
    peer_names = ", ".join(peer_selection.get("selected") or []) or "none"
    peer_rationale = peer_selection.get("rationale") or (
        "No competitive peer-selection rationale was recorded."
    )

    memo = f"""{_header_block(state, pack)}
> Model output only. The {rating} band uses a ±15% convention versus the last price
> and is not investment advice.

{_exec_summary(pack, qualitative_lead)}
## Key data

- Firm classification: {classification.get("firm_type", "N/A")}
- Arithmetic status: {"VERIFIED" if state.get("is_math_verified") else "NOT VERIFIED"}
- Terminal value / enterprise value: {_fmt_percent(dcf.get("terminal_value_share_of_enterprise_value"))}

{_key_data_markdown(pack)}

## Exhibit 1 — Valuation versus the market

{_valuation_exhibit(pack)}

## Assumption register

{_assumption_table(pack)}
{_scope_note(valuation_method)}
## Company and industry

{narratives["qualitative_narrative"]}

### Industry outlook

{narratives["industry_outlook"]}

### Direct filing evidence

{_filing_evidence(state)}

## Discounted cash flow

The primary value is a three-stage FCFF DCF. High-growth lasts {applied.get("high_growth_years", "n/a")} years at {_fmt_percent(applied.get("high_growth_rate"))}, then fades over {applied.get("transition_years", "n/a")} years to a {_fmt_percent(applied.get("terminal_margin"))} terminal EBIT margin and {_fmt_percent(applied.get("terminal_growth_rate"))} perpetuity growth. Discounting at a {_fmt_percent(state.get("discount_rate"))} WACC produces {_fmt_usd(pack.get("dcf_value"))} per share. Raw model equity value before the limited-liability display floor is {_fmt_usd(raw_intrinsic)}.

## Comparables

The competitive analyst selected **{peer_names}** ({peer_selection.get("mode") or "auto"}). {peer_rationale}

Peer-median EV/EBITDA is the market cross-check ({relative_method}). When available it is weighted 30% against the DCF.

{_peer_table(state.get("peer_comparison_matrix") or {})}

## Risks, catalysts and model band

The model band is **{rating}** because the 12-month price target of {_fmt_usd(pack.get("price_target_12m"))} is {_fmt_percent(pack.get("upside_to_pt"))} versus the last price, using a ±15% initiation convention. Principal modelled risks:

{risk_md}

Value is most sensitive to WACC and terminal growth (grid below). A higher beta, a lower terminal margin, or a missing peer cross-check would move the blended value. This section is a model disclosure, not a recommendation.
{_cash_flow_warning(findings)}

## WACC / terminal-growth sensitivity

{_sensitivity_table(state.get("valuation_sensitivity") or {})}

## Appendix A — WACC build-up

{_wacc_appendix(summary, state)}
## Appendix B — Explicit FCFF projections

{_projection_table(dcf.get("projections") or [])}

## Appendix C — Research desk

{_desk_section(state)}{desk_synthesis_md}

### Override rationales

{overrides_md}

### Arithmetic review

{findings_md}

## Methodology limitations

- Book debt is used as a market-debt proxy.
- Yahoo Finance data may be incomplete or restated.
- Lifecycle and qualitative overrides are bounded policy heuristics.
- High-growth rate may blend bounded historical CAGR with labeled Yahoo consensus; that is not management guidance.
- 10-K CUSIP/ISIN harvest is candidate discovery only. Explicit `--target-bonds` ISINs win when supplied.
- Harvested ISIN candidates: {", ".join(state.get("discovered_bond_isins") or []) or "none"}.
- Damodaran default spreads come from a dated local snapshot, not a live HTML scrape.
- Terminal-value concentration and scenario sensitivity require analyst review.
- The 12-month price target rolls today's fair value forward at the cost of equity; it is not a catalyst or timing forecast.
- The FCFF framework is not used for financial-services firms.
- The model band is not an independently verified Buy/Hold/Sell recommendation.
"""

    project_root = Path(__file__).resolve().parents[3]
    reports_dir = project_root / "outputs" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    stamp = date.today().isoformat()
    report_path = reports_dir / f"{ticker}_{stamp}_memo.md"
    report_path.write_text(memo, encoding="utf-8")
    pdf_path = reports_dir / f"{ticker}_{stamp}_memo.pdf"
    try:
        write_memo_pdf(memo, pdf_path)
        pdf_value: Any = str(pdf_path)
    except Exception:
        logger.exception("PDF memo export failed; Markdown memo is still available.")
        pdf_value = None
    try:
        _write_gui_sidecar(state, pack, report_path, pdf_value is not None)
    except Exception:
        logger.exception("GUI sidecar export failed; the Markdown memo is still available.")
    return {
        "valuation_summary": summary,
        "final_equity_memo_path": str(report_path),
        "final_equity_memo_pdf_path": pdf_value,
    }
