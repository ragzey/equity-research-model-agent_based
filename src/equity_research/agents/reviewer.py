"""Pre-Quant assumption reviewer translating research evidence into DCF overrides."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

from ..graphs.desk import (
    QUANT,
    REVIEWER,
    WRITER,
    apply_override_decisions,
    format_transcript,
    inbox,
    make_message,
)
from ..graphs.state import EquityResearchState
from ..prompts.desk import REVIEWER_SYSTEM, REVIEWER_USER
from ..tools.consensus import blend_high_growth_rate
from ..tools.firm_classifier import (
    classify_firm_and_adjust_assumptions,
    extract_operating_baseline,
)
from ..tools.qual_to_quant import generate_valuation_overrides
from ..utils.llm_client import LLMCallError, chat_json

logger = logging.getLogger("ValuationAssumptionReviewer")


def _propose_overrides(state: EquityResearchState) -> Dict[str, Any]:
    income_statement = state.get("income_statement") or {}
    if not income_statement:
        raise ValueError("Income statement is required before assumption review.")

    peer_matrix = state.get("peer_comparison_matrix")
    market_cap = None
    comparable_target_margin = None
    if peer_matrix:
        target = peer_matrix.get("target")
        target_metrics = (peer_matrix.get("metrics") or {}).get(target, {})
        market_cap = target_metrics.get("market_cap")
        margin_pct = target_metrics.get("operating_margin_pct")
        if margin_pct is not None:
            comparable_target_margin = float(margin_pct) / 100.0

    if market_cap is None:
        ticker = state["ticker"].strip().upper()
        market_cap = (
            (state.get("peer_metadata") or {}).get(ticker, {}).get("market_cap")
        )
    if market_cap is None:
        raise ValueError(
            "Market cap is required for baseline assumptions; run Aggregator with peer metadata."
        )

    info = (state.get("peer_metadata") or {}).get(
        state["ticker"].strip().upper(), {}
    )
    baseline = classify_firm_and_adjust_assumptions(
        float(market_cap), income_statement, info
    )
    base_revenue, base_ebit = extract_operating_baseline(income_statement)
    target_margin = (
        comparable_target_margin
        if comparable_target_margin is not None
        else base_ebit / base_revenue
    )
    filing_evidence = state.get("qualitative_evidence") or []
    qualitative_risk_input = " ".join(
        item.get("excerpt", "") for item in filing_evidence
    ) or state.get("qualitative_analysis_summary")

    overrides = generate_valuation_overrides(
        target_margin=target_margin,
        peer_comparison_matrix=peer_matrix,
        qualitative_summary=qualitative_risk_input,
        industry_outlook=state.get("industry_outlook"),
        default_terminal_margin=baseline["terminal_margin"],
        default_high_growth_years=baseline["high_growth_years"],
    )
    overrides["baseline_firm_type"] = baseline["firm_type"]
    growth_rate, growth_rationale = blend_high_growth_rate(
        baseline["high_growth_rate"],
        tuple(baseline["high_growth_rate_bounds"]),
        state.get("consensus_growth"),
    )
    overrides["high_growth_rate"] = growth_rate
    overrides.setdefault("rationales", {})["high_growth_rate"] = growth_rationale
    return {"baseline": baseline, "proposed": overrides}


def _llm_decisions(
    state: EquityResearchState,
    baseline: Dict[str, Any],
    proposed: Dict[str, Any],
) -> Dict[str, Any]:
    transcript = format_transcript(inbox(state.get("agent_messages"), REVIEWER))
    compact_baseline = {
        "firm_type": baseline.get("firm_type"),
        "terminal_margin": baseline.get("terminal_margin"),
        "high_growth_years": baseline.get("high_growth_years"),
        "high_growth_rate": baseline.get("high_growth_rate"),
        "company_specific_risk_premium": 0.0,
    }
    compact_proposed = {
        key: proposed.get(key)
        for key in (
            "terminal_margin",
            "company_specific_risk_premium",
            "high_growth_years",
            "high_growth_rate",
        )
    }
    compact_proposed["rationales"] = proposed.get("rationales")
    user = REVIEWER_USER.format(
        ticker=state["ticker"],
        baseline_json=json.dumps(compact_baseline, indent=2, default=str),
        proposed_json=json.dumps(compact_proposed, indent=2, default=str),
        transcript=transcript,
        qualitative=(state.get("qualitative_analysis_summary") or "")[:6000],
        outlook=(state.get("industry_outlook") or "")[:4000],
    )
    parsed = chat_json(
        [
            {"role": "system", "content": REVIEWER_SYSTEM},
            {"role": "user", "content": user},
        ],
        timeout=90,
        required=True,
    )
    return parsed or {}


def valuation_assumption_reviewer_node(
    state: EquityResearchState,
) -> Dict[str, Any]:
    """
    Build reviewed, bounded overrides before Quant runs.

    Python proposes candidates. The reviewer agent only accepts or rejects them.
    This does not set `is_math_verified`; arithmetic verification belongs to a
    separate post-Quant review step.
    """
    bundle = _propose_overrides(state)
    baseline = bundle["baseline"]
    proposed = bundle["proposed"]
    baseline_for_revert = {
        "terminal_margin": baseline["terminal_margin"],
        "company_specific_risk_premium": 0.0,
        "high_growth_years": baseline["high_growth_years"],
        "high_growth_rate": baseline["high_growth_rate"],
    }

    llm_payload = _llm_decisions(state, baseline, proposed)
    raw_decisions = llm_payload.get("decisions")
    if not isinstance(raw_decisions, list) or not raw_decisions:
        raise LLMCallError(
            "Assumption reviewer did not return accept/reject decisions."
        )
    mode = "llm"
    decisions = [item for item in raw_decisions if isinstance(item, dict)]
    if not decisions:
        raise LLMCallError(
            "Assumption reviewer did not return usable accept/reject decisions."
        )

    overrides = apply_override_decisions(
        proposed,
        baseline_for_revert,
        decisions,
        mode=mode,
    )
    notes_to_quant = str(llm_payload.get("notes_to_quant") or "").strip()
    notes_to_writer = str(llm_payload.get("notes_to_writer") or "").strip()
    if not notes_to_quant:
        notes_to_quant = (
            "Quant may use only accepted overrides; rejected keys reverted to baseline."
        )
    if not notes_to_writer:
        accepted = [row["key"] for row in overrides["decisions"] if row["action"] == "accept"]
        rejected = [row["key"] for row in overrides["decisions"] if row["action"] == "reject"]
        notes_to_writer = (
            f"Accepted: {', '.join(accepted) or 'none'}. "
            f"Rejected back to baseline: {', '.join(rejected) or 'none'}."
        )

    messages = [
        make_message(REVIEWER, QUANT, "override_decision", notes_to_quant, {
            "mode": mode,
            "decisions": overrides["decisions"],
        }),
        make_message(REVIEWER, WRITER, "desk_notes", notes_to_writer, {
            "mode": mode,
            "decisions": overrides["decisions"],
        }),
    ]
    logger.info(
        "DCF overrides reviewed for %s (%s): margin %.1f%%, growth %.1f%%, horizon %d years, CSRP %.2f%%",
        state["ticker"],
        mode,
        overrides["terminal_margin"] * 100,
        overrides["high_growth_rate"] * 100,
        overrides["high_growth_years"],
        overrides["company_specific_risk_premium"] * 100,
    )
    return {"dcf_overrides": overrides, "agent_messages": messages}
