"""Post-Quant arithmetic integrity checks and bounded retry routing."""

from __future__ import annotations

import math
from math import ceil
from typing import Any, Dict, List, Literal

from ..graphs.state import EquityResearchState

MAX_REVISIONS = 3
TERMINAL_VALUE_WARNING_THRESHOLD = 0.85


def _finding(
    severity: str,
    code: str,
    message: str,
    retryable: bool = False,
) -> Dict[str, Any]:
    return {
        "severity": severity,
        "code": code,
        "message": message,
        "retryable": retryable,
    }


def post_quant_reviewer_node(state: EquityResearchState) -> Dict[str, Any]:
    """Verify model identities; flag economic sensitivities without outcome-fitting."""
    findings: List[Dict[str, Any]] = []
    summary = state.get("valuation_summary") or {}
    dcf = summary.get("dcf") or {}
    inputs = summary.get("valuation_date_inputs") or {}

    required = {
        "enterprise_value": dcf.get("enterprise_value"),
        "equity_value": dcf.get("equity_value"),
        "intrinsic_value_per_share": dcf.get("intrinsic_value_per_share"),
        "terminal_wacc": dcf.get("terminal_wacc_applied"),
        "terminal_growth": dcf.get("terminal_growth_rate_applied"),
    }
    missing = [name for name, value in required.items() if value is None]
    if missing:
        findings.append(
            _finding(
                "error",
                "MISSING_OUTPUTS",
                f"Missing Quant outputs: {', '.join(missing)}.",
                retryable=True,
            )
        )
    else:
        nonfinite = [
            name
            for name, value in required.items()
            if not math.isfinite(float(value))
        ]
        if nonfinite:
            findings.append(
                _finding(
                    "error",
                    "NONFINITE_OUTPUTS",
                    f"Non-finite Quant outputs: {', '.join(nonfinite)}.",
                    retryable=True,
                )
            )

    if not missing:
        terminal_wacc = float(required["terminal_wacc"])
        terminal_growth = float(required["terminal_growth"])
        if terminal_wacc - terminal_growth < 0.01:
            findings.append(
                _finding(
                    "error",
                    "UNSAFE_TERMINAL_SPREAD",
                    "Terminal WACC-growth spread is below 100 bps.",
                )
            )

        enterprise_value = float(required["enterprise_value"])
        equity_value = float(required["equity_value"])
        cash = float(inputs.get("cash_and_equivalents") or 0.0)
        debt = float(inputs.get("total_debt") or 0.0)
        shares = float(inputs.get("shares_outstanding") or 0.0)
        expected_equity = enterprise_value + cash - debt
        tolerance = max(abs(expected_equity), 1.0) * 1e-8
        if abs(equity_value - expected_equity) > tolerance:
            findings.append(
                _finding(
                    "error",
                    "EQUITY_BRIDGE_MISMATCH",
                    "Enterprise-to-equity value bridge does not reconcile.",
                    retryable=True,
                )
            )
        if shares > 0:
            expected_per_share = equity_value / shares
            if abs(float(required["intrinsic_value_per_share"]) - expected_per_share) > max(
                abs(expected_per_share), 1.0
            ) * 1e-8:
                findings.append(
                    _finding(
                        "error",
                        "PER_SHARE_MISMATCH",
                        "Equity value per share does not reconcile.",
                        retryable=True,
                    )
                )

        terminal_share = dcf.get("terminal_value_share_of_enterprise_value")
        if terminal_share is not None and float(terminal_share) > TERMINAL_VALUE_WARNING_THRESHOLD:
            findings.append(
                _finding(
                    "warning",
                    "TERMINAL_VALUE_CONCENTRATION",
                    (
                        f"Terminal value is {float(terminal_share):.1%} of enterprise "
                        "value; review sensitivity rather than altering assumptions to pass."
                    ),
                )
            )
        if equity_value < 0:
            findings.append(
                _finding(
                    "warning",
                    "NEGATIVE_EQUITY_VALUE",
                    (
                        "Raw DCF equity value is negative. Preserve this distress signal; "
                        "limited-liability economic value is floored at zero only for display."
                    ),
                )
            )

        projections = dcf.get("projections") or []
        negative_fcff_years = [
            int(projection.get("year", index + 1))
            for index, projection in enumerate(projections)
            if float(projection.get("fcff") or 0.0) < 0
        ]
        distress_threshold = ceil(len(projections) * 0.70)
        if projections and len(negative_fcff_years) >= distress_threshold:
            findings.append(
                _finding(
                    "high_warning",
                    "PERSISTENT_NEGATIVE_FCFF",
                    (
                        f"FCFF is negative in {len(negative_fcff_years)} of "
                        f"{len(projections)} explicit forecast years "
                        f"({negative_fcff_years}). This indicates modelled capital "
                        "intensity or cash-flow fragility, but is not by itself proof "
                        "of insolvency or a going-concern condition."
                    ),
                )
            )

    errors = [finding for finding in findings if finding["severity"] == "error"]
    retryable = errors and all(finding["retryable"] for finding in errors)
    revision_count = int(state.get("revision_count") or 0)

    if errors and retryable and revision_count < MAX_REVISIONS:
        action = "retry"
        revision_count += 1
    elif errors:
        action = "stop"
    elif findings:
        action = "warn"
    else:
        action = "pass"

    feedback = " | ".join(finding["message"] for finding in findings)
    if not feedback:
        feedback = "Arithmetic identities and terminal spread checks passed."
    return {
        "is_math_verified": not errors,
        "review_findings": findings,
        "reviewer_feedback": feedback,
        "review_action": action,
        "revision_count": revision_count,
    }


def route_after_post_quant_review(
    state: EquityResearchState,
) -> Literal["retry_quant", "continue"]:
    """Retry only recomputable integrity failures, capped by MAX_REVISIONS."""
    return "retry_quant" if state.get("review_action") == "retry" else "continue"
