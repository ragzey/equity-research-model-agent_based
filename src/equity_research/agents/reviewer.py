"""Independent accept/reject of bounded architect (or Python) DCF candidates."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict

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
from ..tools.assumption_menus import (
    build_assumption_bundle,
    policy_terminal_growth,
)
from ..utils.grounding import contains_web_link
from ..utils.llm_client import LLMCallError, chat_json

logger = logging.getLogger("ValuationAssumptionReviewer")


def _architect_proposed(state: EquityResearchState) -> bool:
    existing = state.get("dcf_overrides") or {}
    return existing.get("desk_mode") == "architect" and existing.get(
        "high_growth_rate"
    ) is not None


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
        "terminal_growth_rate": baseline.get("terminal_growth_rate"),
        "sales_to_capital": baseline.get("sales_to_capital"),
        "company_specific_risk_premium": 0.0,
    }
    compact_proposed = {
        key: proposed.get(key)
        for key in (
            "terminal_margin",
            "company_specific_risk_premium",
            "high_growth_years",
            "high_growth_rate",
            "terminal_growth_rate",
            "sales_to_capital",
            "stable_sales_to_capital",
        )
    }
    compact_proposed["rationales"] = proposed.get("rationales")
    user = REVIEWER_USER.format(
        ticker=state["ticker"],
        baseline_json=json.dumps(compact_baseline, indent=2, default=str),
        proposed_json=json.dumps(compact_proposed, indent=2, default=str),
        architect_json=json.dumps(
            {
                "choices": proposed.get("architect_choices"),
                "allowed": proposed.get("architect_allowed"),
            },
            indent=2,
            default=str,
        ),
        packet_json=json.dumps(
            state.get("industry_macro_packet") or {},
            indent=2,
            default=str,
        )[:6000],
        company_products_json=json.dumps(
            state.get("company_products_packet") or {},
            indent=2,
            default=str,
        )[:6000],
        operations_json=json.dumps(
            state.get("operations_packet") or {},
            indent=2,
            default=str,
        )[:6000],
        growth_path_json=json.dumps(
            state.get("growth_path_packet") or {},
            indent=2,
            default=str,
        )[:6000],
        valuation_mix_json=json.dumps(
            state.get("valuation_mix_packet") or {},
            indent=2,
            default=str,
        )[:6000],
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
    Independent veto. Python/architect propose candidates; this node only
    accepts or rejects them. Arithmetic verification is post-Quant.
    """
    rf = ((state.get("industry_macro_packet") or {}).get("macro") or {}).get(
        "risk_free_rate"
    )
    bundle = build_assumption_bundle(state, risk_free_rate=rf)
    baseline = dict(bundle["baseline"])
    baseline["terminal_growth_rate"] = policy_terminal_growth(
        float(rf) if rf is not None else None,
        firm_type=baseline.get("firm_type"),
        packet=state.get("industry_macro_packet"),
    )
    proposed = dict(state.get("dcf_overrides") or {}) if _architect_proposed(state) else dict(
        bundle["proposed"]
    )
    baseline_for_revert = {
        "terminal_margin": baseline["terminal_margin"],
        "company_specific_risk_premium": 0.0,
        "high_growth_years": baseline["high_growth_years"],
        "high_growth_rate": baseline["high_growth_rate"],
        "terminal_growth_rate": baseline["terminal_growth_rate"],
        "sales_to_capital": baseline.get("sales_to_capital"),
        "stable_sales_to_capital": baseline.get("stable_sales_to_capital"),
    }

    llm_payload = _llm_decisions(state, baseline, proposed)
    raw_decisions = llm_payload.get("decisions")
    if not isinstance(raw_decisions, list) or not raw_decisions:
        raise LLMCallError(
            "Assumption reviewer did not return accept/reject decisions."
        )
    decisions = [item for item in raw_decisions if isinstance(item, dict)]
    if not decisions:
        raise LLMCallError(
            "Assumption reviewer did not return usable accept/reject decisions."
        )

    overrides = apply_override_decisions(
        proposed,
        baseline_for_revert,
        decisions,
        mode="llm",
    )
    stc_row = next(
        (
            row
            for row in overrides.get("decisions") or []
            if row.get("key") == "sales_to_capital"
        ),
        None,
    )
    if stc_row and stc_row.get("action") == "reject":
        overrides["stable_sales_to_capital"] = baseline_for_revert.get(
            "stable_sales_to_capital"
        )
    for carry in (
        "architect_choices",
        "architect_menus",
        "architect_allowed",
        "industry_macro_views",
        "operations_views",
        "growth_path_views",
        "baseline_firm_type",
    ):
        if proposed.get(carry) is not None:
            overrides[carry] = proposed[carry]
    notes_to_quant = str(llm_payload.get("notes_to_quant") or "").strip()
    notes_to_writer = str(llm_payload.get("notes_to_writer") or "").strip()
    if contains_web_link(notes_to_quant):
        notes_to_quant = ""
    if contains_web_link(notes_to_writer):
        notes_to_writer = ""
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
            "mode": "llm",
            "decisions": overrides["decisions"],
        }),
        make_message(REVIEWER, WRITER, "desk_notes", notes_to_writer, {
            "mode": "llm",
            "decisions": overrides["decisions"],
        }),
    ]
    logger.info(
        "DCF overrides reviewed for %s: margin %.1f%%, growth %.1f%%, horizon %d years, CSRP %.2f%%",
        state["ticker"],
        overrides["terminal_margin"] * 100,
        overrides["high_growth_rate"] * 100,
        overrides["high_growth_years"],
        overrides["company_specific_risk_premium"] * 100,
    )
    return {"dcf_overrides": overrides, "agent_messages": messages}
