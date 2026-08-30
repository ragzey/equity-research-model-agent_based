"""Research-desk messages: how Qual, Competitive, Reviewer, and Writer hand off work."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

QUALITATIVE = "qualitative_analyst"
COMPETITIVE = "competitive_analyst"
INDUSTRY_MACRO = "industry_macro"
ARCHITECT = "assumption_architect"
REVIEWER = "assumption_reviewer"
QUANT = "quant_analyst"
WRITER = "lead_writer"
AUDITOR = "independent_auditor"

DECISION_KEYS = (
    "terminal_margin",
    "company_specific_risk_premium",
    "high_growth_years",
    "high_growth_rate",
    "terminal_growth_rate",
)


def make_message(
    from_agent: str,
    to_agent: str,
    kind: str,
    body: str,
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return {
        "from_agent": from_agent,
        "to_agent": to_agent,
        "kind": kind,
        "body": body,
        "payload": payload or {},
    }


def inbox(messages: Optional[Iterable[Dict[str, Any]]], to_agent: str) -> List[Dict[str, Any]]:
    return [item for item in (messages or []) if item.get("to_agent") == to_agent]


def format_transcript(messages: Optional[Iterable[Dict[str, Any]]]) -> str:
    lines = []
    for item in messages or []:
        kind = item.get("kind", "note")
        source = item.get("from_agent", "unknown")
        dest = item.get("to_agent", "unknown")
        body = str(item.get("body") or "").strip()
        lines.append(f"- **{source} → {dest}** ({kind}): {body}")
    return "\n".join(lines) or "- No research-desk handoffs were recorded."


def revert_value(key: str, baseline: Dict[str, Any]) -> Any:
    if key == "company_specific_risk_premium":
        return 0.0
    return baseline[key]


def apply_override_decisions(
    proposed: Dict[str, Any],
    baseline: Dict[str, Any],
    decisions: Optional[Iterable[Dict[str, Any]]],
    *,
    mode: str = "deterministic",
) -> Dict[str, Any]:
    """
    Accept or reject proposed DCF overrides. Rejection reverts to classifier baseline.

    The reviewer may not invent new numeric values; it may only keep or discard
    already-bounded candidates.
    """
    applied = dict(proposed)
    rationales = dict(proposed.get("rationales") or {})
    by_key: Dict[str, Dict[str, Any]] = {}
    for raw in decisions or []:
        if not isinstance(raw, dict):
            continue
        key = str(raw.get("key") or "").strip()
        if key:
            by_key[key] = raw

    audit: List[Dict[str, Any]] = []
    for key in DECISION_KEYS:
        decision = by_key.get(key) or {}
        action = str(decision.get("action") or "").strip().lower()
        reason = str(decision.get("reason") or "").strip()
        if action not in {"accept", "reject"}:
            action = "reject"
            reason = reason or (
                "Missing or invalid reviewer action; reverted to baseline."
            )
        if action == "reject":
            applied[key] = revert_value(key, baseline)
            prior = rationales.get(key, "")
            rationales[key] = (
                f"REJECTED by assumption reviewer; reverted to baseline. {reason} "
                f"Candidate rationale was: {prior}"
            ).strip()
            audit.append(
                {
                    "key": key,
                    "action": "reject",
                    "reason": reason or "Rejected without additional reason.",
                    "applied": applied[key],
                }
            )
        else:
            audit.append(
                {
                    "key": key,
                    "action": "accept",
                    "reason": reason or "Candidate retained.",
                    "applied": applied[key],
                }
            )
            if reason and key in rationales:
                rationales[key] = f"{rationales[key]} Desk decision: {reason}"
            elif reason:
                rationales[key] = reason

    applied["rationales"] = rationales
    applied["decisions"] = audit
    applied["desk_mode"] = mode
    return applied
