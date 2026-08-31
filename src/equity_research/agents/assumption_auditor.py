"""Independent assumption auditor: second pair of eyes on labels, before Quant."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from ..graphs.desk import (
    ASSUMPTION_AUDITOR,
    QUANT,
    WRITER,
    apply_override_decisions,
    make_message,
)
from ..graphs.state import EquityResearchState
from ..prompts.desk import ASSUMPTION_AUDITOR_SYSTEM, ASSUMPTION_AUDITOR_USER
from ..tools.assumption_audit import (
    BASE_LABEL,
    merge_audit_decisions,
    python_assumption_reverts,
)
from ..tools.assumption_menus import (
    build_assumption_bundle,
    build_choice_menus,
    policy_terminal_growth,
)
from ..utils.grounding import contains_web_link
from ..utils.llm_client import LLMCallError, LLMNotConfiguredError, chat_json

logger = logging.getLogger("AssumptionAuditor")


def _empty_packet(reason: str) -> Dict[str, Any]:
    return {
        "applicable": False,
        "reverted": [],
        "decisions": [],
        "narrative": reason,
        "source": "ledger",
    }


def _baseline_for_revert(state: EquityResearchState) -> Optional[Dict[str, Any]]:
    rf = ((state.get("industry_macro_packet") or {}).get("macro") or {}).get(
        "risk_free_rate"
    )
    try:
        bundle = build_assumption_bundle(state, risk_free_rate=rf)
    except (TypeError, ValueError):
        return None
    baseline = dict(bundle["baseline"])
    baseline["terminal_growth_rate"] = policy_terminal_growth(
        float(rf) if rf is not None else None,
        firm_type=baseline.get("firm_type"),
        packet=state.get("industry_macro_packet"),
    )
    menus = build_choice_menus(
        bundle,
        state.get("industry_macro_packet"),
        risk_free_rate=rf,
        operations_packet=state.get("operations_packet"),
        growth_path_packet=state.get("growth_path_packet"),
    )
    return {
        "bundle": bundle,
        "baseline": baseline,
        "menus": menus,
        "revert": {
            "terminal_margin": baseline["terminal_margin"],
            "company_specific_risk_premium": 0.0,
            "high_growth_years": baseline["high_growth_years"],
            "high_growth_rate": baseline["high_growth_rate"],
            "terminal_growth_rate": baseline["terminal_growth_rate"],
            "sales_to_capital": baseline.get("sales_to_capital"),
            "stable_sales_to_capital": baseline.get("stable_sales_to_capital"),
        },
    }


def _llm_decisions(state: EquityResearchState, ledger: Dict[str, Any]) -> List[Dict[str, Any]]:
    overrides = state.get("dcf_overrides") or {}
    user = ASSUMPTION_AUDITOR_USER.format(
        ticker=state.get("ticker") or "",
        firm_type=ledger["baseline"].get("firm_type") or "unclassified",
        allowed_json=json.dumps(ledger["menus"].get("allowed") or {}, indent=2, default=str),
        choices_json=json.dumps(
            {
                "architect_choices": overrides.get("architect_choices"),
                "reviewer_decisions": overrides.get("decisions"),
            },
            indent=2,
            default=str,
        ),
        packet_json=json.dumps(
            state.get("industry_macro_packet") or {}, indent=2, default=str
        )[:6000],
        operations_json=json.dumps(
            state.get("operations_packet") or {}, indent=2, default=str
        )[:4000],
        growth_path_json=json.dumps(
            state.get("growth_path_packet") or {}, indent=2, default=str
        )[:4000],
        mix_json=json.dumps(
            {
                "label": (state.get("valuation_mix_packet") or {}).get("label"),
                "allowed": (state.get("valuation_mix_packet") or {}).get("allowed"),
            },
            indent=2,
            default=str,
        ),
    )
    try:
        parsed = chat_json(
            [
                {"role": "system", "content": ASSUMPTION_AUDITOR_SYSTEM},
                {"role": "user", "content": user},
            ],
            timeout=90,
            required=False,
        )
    except (LLMCallError, LLMNotConfiguredError):
        return []
    raw = (parsed or {}).get("decisions")
    if not isinstance(raw, list):
        return []
    cleaned: List[Dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        reason = str(item.get("reason") or "").strip()
        if contains_web_link(reason):
            reason = ""
        cleaned.append(
            {
                "key": str(item.get("key") or "").strip(),
                "action": str(item.get("action") or "").strip().lower(),
                "reason": reason,
            }
        )
    return cleaned


def assumption_auditor_node(state: EquityResearchState) -> Dict[str, Any]:
    """Second independent check of labeled assumptions. Revert only; never type a rate."""
    if state.get("is_financial") or state.get("valuation_method") == "unsupported_financial":
        packet = _empty_packet("Assumption audit is out of scope on the financials path.")
        return {
            "assumption_audit": packet,
            "agent_messages": [
                make_message(
                    ASSUMPTION_AUDITOR,
                    WRITER,
                    "assumption_audit",
                    packet["narrative"],
                    packet,
                )
            ],
        }

    overrides = dict(state.get("dcf_overrides") or {})
    if not overrides:
        packet = _empty_packet("No reviewed DCF overrides to audit.")
        return {"assumption_audit": packet}

    ledger = _baseline_for_revert(state)
    if ledger is None:
        packet = _empty_packet(
            "Assumption auditor could not rebuild the classifier baseline."
        )
        return {"assumption_audit": packet, "dcf_overrides": overrides}

    python_reverts = python_assumption_reverts(
        overrides,
        firm_type=ledger["baseline"].get("firm_type"),
        industry_packet=state.get("industry_macro_packet"),
        operations_packet=state.get("operations_packet"),
        growth_path_packet=state.get("growth_path_packet"),
        menus=ledger["menus"],
    )
    llm_decisions = _llm_decisions(state, ledger)
    decisions = merge_audit_decisions(python_reverts, llm_decisions)
    audited = apply_override_decisions(
        overrides,
        ledger["revert"],
        decisions,
        mode="audited",
    )
    stc_row = next(
        (
            row
            for row in audited.get("decisions") or []
            if row.get("key") == "sales_to_capital" and row.get("action") == "reject"
        ),
        None,
    )
    if stc_row:
        audited["stable_sales_to_capital"] = ledger["revert"].get(
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
        if overrides.get(carry) is not None:
            audited[carry] = overrides[carry]
    choices = dict(audited.get("architect_choices") or {})
    reverted_keys: List[str] = []
    for row in audited.get("decisions") or []:
        if row.get("action") != "reject":
            continue
        key = str(row.get("key") or "")
        reverted_keys.append(key)
        if key in BASE_LABEL:
            choices[key] = BASE_LABEL[key]
    audited["architect_choices"] = choices
    if not reverted_keys:
        audited["desk_mode"] = overrides.get("desk_mode") or "audited"

    python_keys = {item["key"] for item in python_reverts}
    packet = {
        "applicable": True,
        "reverted": reverted_keys,
        "python_reverted": sorted(python_keys),
        "decisions": audited.get("decisions") or [],
        "narrative": (
            (
                "Assumption auditor reverted "
                + ", ".join(reverted_keys)
                + " to classifier baseline."
            )
            if reverted_keys
            else "Assumption auditor kept the reviewed labels."
        ),
        "source": "ledger" if python_keys else "auditor",
    }
    notes = packet["narrative"]
    messages = [
        make_message(
            ASSUMPTION_AUDITOR,
            QUANT,
            "assumption_audit",
            notes,
            {"reverted": reverted_keys, "decisions": packet["decisions"]},
        ),
        make_message(
            ASSUMPTION_AUDITOR,
            WRITER,
            "assumption_audit",
            notes,
            packet,
        ),
    ]
    logger.info(
        "Assumption audit for %s: reverted %s",
        state.get("ticker"),
        reverted_keys or "none",
    )
    return {
        "dcf_overrides": audited,
        "assumption_audit": packet,
        "agent_messages": messages,
    }
