"""Python overlay for the assumption auditor. Labels only; no DCF numbers."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from ..graphs.desk import DECISION_KEYS
from .assumption_menus import (
    allowed_growth_choices,
    allowed_stc_choices,
    allowed_terminal_growth_choices,
    allowed_year_choices,
    _hostile_macro,
    _view,
)

BASE_LABEL = {
    "high_growth_rate": "base",
    "high_growth_years": "base",
    "terminal_growth_rate": "base",
    "terminal_margin": "baseline",
    "company_specific_risk_premium": "none",
    "sales_to_capital": "base",
}
DOWN_LABEL = {
    "high_growth_rate": "low",
    "high_growth_years": "compress",
    "terminal_growth_rate": "low",
}


def _decision_map(overrides: Optional[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    by_key: Dict[str, Dict[str, Any]] = {}
    for raw in (overrides or {}).get("decisions") or []:
        if not isinstance(raw, dict):
            continue
        key = str(raw.get("key") or "").strip()
        if key:
            by_key[key] = raw
    return by_key


def effective_label(overrides: Optional[Dict[str, Any]], key: str) -> str:
    """Label Quant will use: a reviewer reject already sits on baseline."""
    row = _decision_map(overrides).get(key) or {}
    if str(row.get("action") or "").strip().lower() == "reject":
        return BASE_LABEL.get(key, "base")
    choices = (overrides or {}).get("architect_choices") or {}
    label = str(choices.get(key) or "").strip().lower()
    return label or BASE_LABEL.get(key, "base")


def assumption_allow_lists(
    *,
    firm_type: Optional[str],
    industry_packet: Optional[Dict[str, Any]],
    operations_packet: Optional[Dict[str, Any]] = None,
    growth_path_packet: Optional[Dict[str, Any]] = None,
    menus: Optional[Dict[str, Any]] = None,
) -> Dict[str, List[str]]:
    return {
        "high_growth_rate": allowed_growth_choices(
            industry_packet, firm_type=firm_type
        ),
        "high_growth_years": allowed_year_choices(
            industry_packet, firm_type=firm_type
        ),
        "terminal_growth_rate": allowed_terminal_growth_choices(
            industry_packet,
            menus or {},
            firm_type=firm_type,
        ),
        "sales_to_capital": allowed_stc_choices(
            operations_packet, growth_path_packet=growth_path_packet
        ),
    }


def python_assumption_reverts(
    overrides: Optional[Dict[str, Any]],
    *,
    firm_type: Optional[str],
    industry_packet: Optional[Dict[str, Any]],
    operations_packet: Optional[Dict[str, Any]] = None,
    growth_path_packet: Optional[Dict[str, Any]] = None,
    menus: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, str]]:
    """Force-revert labels that are off the current allow-list or stacked."""
    allowed = assumption_allow_lists(
        firm_type=firm_type,
        industry_packet=industry_packet,
        operations_packet=operations_packet,
        growth_path_packet=growth_path_packet,
        menus=menus,
    )
    reverts: List[Dict[str, str]] = []
    seen = set()

    def _revert(key: str, reason: str) -> None:
        if key in seen:
            return
        seen.add(key)
        reverts.append({"key": key, "reason": reason})

    for key, choices in allowed.items():
        label = effective_label(overrides, key)
        if label == BASE_LABEL.get(key, "base"):
            continue
        if label not in choices:
            _revert(
                key,
                (
                    f"Accepted '{label}' is not on the Python allow-list "
                    f"({', '.join(choices)}). Reverted to classifier baseline."
                ),
            )

    category = _view((industry_packet or {}).get("category_growth"))
    if category == "in_line" and not _hostile_macro(industry_packet):
        stacked = [
            key
            for key, down in DOWN_LABEL.items()
            if effective_label(overrides, key) == down and key not in seen
        ]
        if len(stacked) >= 2:
            for key in stacked:
                _revert(
                    key,
                    (
                        "Stacked recession labels on in-line category growth "
                        "without a ledger downswing. Reverted to classifier baseline."
                    ),
                )
    return reverts


def merge_audit_decisions(
    python_reverts: Sequence[Dict[str, str]],
    llm_decisions: Optional[Sequence[Dict[str, Any]]] = None,
    keys: Sequence[str] = DECISION_KEYS,
) -> List[Dict[str, Any]]:
    """Python reverts win. LLM may only add further reverts, never keep a banned label."""
    by_key: Dict[str, Dict[str, Any]] = {
        key: {
            "key": key,
            "action": "accept",
            "reason": "Assumption auditor kept the reviewed label.",
        }
        for key in keys
    }
    for raw in llm_decisions or []:
        if not isinstance(raw, dict):
            continue
        key = str(raw.get("key") or "").strip()
        action = str(raw.get("action") or "").strip().lower()
        reason = str(raw.get("reason") or "").strip()
        if key not in by_key or action != "reject":
            continue
        by_key[key] = {
            "key": key,
            "action": "reject",
            "reason": reason or "Assumption auditor reverted to baseline.",
        }
    for item in python_reverts:
        key = str(item.get("key") or "").strip()
        if key not in by_key:
            continue
        by_key[key] = {
            "key": key,
            "action": "reject",
            "reason": str(item.get("reason") or "Python allow-list revert."),
            "source": "ledger",
        }
    return [by_key[key] for key in keys]
