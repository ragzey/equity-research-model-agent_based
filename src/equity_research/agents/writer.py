"""Deterministic Markdown investment-memo compiler from the validated ledger."""

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
from ..utils.llm_client import chat_json, llm_configured

logger = logging.getLogger("LeadWriter")


def _fmt_number(value: Any, decimals: int = 2) -> str:
    if value is None:
        return "N/A"
    return f"{float(value):,.{decimals}f}"


def _fmt_percent(value: Any) -> str:
    if value is None:
        return "N/A"
    return f"{float(value):.2%}"


def _valuation_signal(current_price: Any, intrinsic_value: Any, verified: bool) -> str:
    """A descriptive scenario signal, deliberately not a Buy/Hold/Sell rating."""
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
    mode = (state.get("dcf_overrides") or {}).get("desk_mode") or "deterministic"
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
    if not llm_configured():
        return {
            "industry_outlook": industry,
            "qualitative_narrative": qualitative,
            "desk_synthesis": "",
        }
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
    )
    if not payload:
        return {
            "industry_outlook": industry,
            "qualitative_narrative": qualitative,
            "desk_synthesis": "",
        }
    return {
        "industry_outlook": str(payload.get("industry_outlook") or industry),
        "qualitative_narrative": str(
            payload.get("qualitative_narrative") or qualitative
        ),
        "desk_synthesis": str(payload.get("desk_synthesis") or "").strip(),
    }


def lead_writer_node(state: EquityResearchState) -> Dict[str, Any]:
    """Write a sourced, deterministic Markdown memo and return its local path."""
    ticker = state["ticker"].strip().upper()
    summary = state.get("valuation_summary") or {}
    inputs = summary.get("valuation_date_inputs") or {}
    classification = summary.get("firm_classification") or {}
    applied = summary.get("applied_dcf_assumptions") or {}
    dcf = summary.get("dcf") or {}
    cost_of_debt = summary.get("cost_of_debt") or {}
    valuation_method = summary.get("valuation_method") or state.get(
        "valuation_method"
    ) or "corporate_fcff"
    findings: List[Dict[str, Any]] = state.get("review_findings") or []
    raw_intrinsic = state.get("calculated_dcf_value")
    display_intrinsic = (
        max(float(raw_intrinsic), 0.0) if raw_intrinsic is not None else None
    )

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
        "share_price": inputs.get("share_price"),
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

    memo = f"""# {ticker} — Equity Research Model Memo

**Model date:** {date.today().isoformat()}  
**Research horizon:** {state.get("target_year")}  
**Arithmetic status:** {"VERIFIED" if state.get("is_math_verified") else "NOT VERIFIED"}

> This document is generated from model inputs and is not investment advice or
> an independently verified Buy/Hold/Sell recommendation.

## Valuation summary

- Firm classification: {classification.get("firm_type", "N/A")}
- Valuation method: {valuation_method}
- Current share price: ${_fmt_number(inputs.get("share_price"))}
- High-growth rate applied: {_fmt_percent(applied.get("high_growth_rate"))}
- Discount rate: {_fmt_percent(state.get("discount_rate"))}
- Raw model equity value/share: ${_fmt_number(raw_intrinsic)}
- Limited-liability display value/share: ${_fmt_number(display_intrinsic)}
- Terminal value / enterprise value: {_fmt_percent(dcf.get("terminal_value_share_of_enterprise_value"))}
- Valuation signal: {_valuation_signal(inputs.get("share_price"), raw_intrinsic, bool(state.get("is_math_verified")))}

## Capital costs

- Cost of equity: {_fmt_percent(summary.get("cost_of_equity"))}
- Cost of debt method: {cost_of_debt.get("method_used", "N/A")}
- Pre-tax cost of debt: {_fmt_percent(cost_of_debt.get("pre_tax_cost_of_debt"))}
- After-tax cost of debt: {_fmt_percent(cost_of_debt.get("after_tax_cost_of_debt"))}
{_scope_note(valuation_method)}

## Peer comparison

{_peer_table(state.get("peer_comparison_matrix") or {})}

## Industry outlook

{narratives["industry_outlook"]}

## SEC qualitative risk assessment

{narratives["qualitative_narrative"]}

### Direct filing evidence

{_filing_evidence(state)}

## Research desk

{_desk_section(state)}{desk_synthesis_md}

## Assumption override audit

{overrides_md}

## Arithmetic review

{findings_md}
{_cash_flow_warning(findings)}

## Explicit DCF projections

{_projection_table(dcf.get("projections") or [])}

## WACC / terminal-growth sensitivity

{_sensitivity_table(state.get("valuation_sensitivity") or {})}

## Methodology limitations

- Book debt is used as a market-debt proxy.
- Yahoo Finance data may be incomplete or restated.
- Lifecycle and qualitative overrides are bounded policy heuristics.
- High-growth rate may blend bounded historical CAGR with labeled Yahoo consensus; that is not management guidance.
- 10-K CUSIP/ISIN harvest is candidate discovery only. Explicit `--target-bonds` ISINs win when supplied.
- Harvested ISIN candidates: {", ".join(state.get("discovered_bond_isins") or []) or "none"}.
- Damodaran default spreads come from a dated local snapshot, not a live HTML scrape.
- Terminal-value concentration and scenario sensitivity require analyst review.
- The FCFF framework is not used for financial-services firms.
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
    return {
        "final_equity_memo_path": str(report_path),
        "final_equity_memo_pdf_path": pdf_value,
    }
