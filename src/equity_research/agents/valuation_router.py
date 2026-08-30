"""Route non-financial firms to FCFF; withhold a DCF for financial firms."""

from __future__ import annotations

from typing import Any, Dict, Literal

from ..graphs.state import EquityResearchState


def valuation_router_node(state: EquityResearchState) -> Dict[str, Any]:
    """Fan-in synchronization node before selecting the valuation model."""
    return {}


def route_valuation_method(
    state: EquityResearchState,
) -> Literal["corporate_fcff", "unsupported_financial"]:
    if state.get("is_financial") or state.get("valuation_method") == "unsupported_financial":
        return "unsupported_financial"
    return "corporate_fcff"


def unsupported_financial_node(state: EquityResearchState) -> Dict[str, Any]:
    """Stop without inventing an FCFF or bank model for financial firms."""
    message = (
        "This pipeline values non-financial operating companies with WACC and "
        "three-stage FCFF. Banks, insurers, brokers, and other financial firms "
        "are out of scope."
    )
    summary = dict(state.get("valuation_summary") or {})
    summary.update({"valuation_method": "unsupported_financial"})
    return {
        "discount_rate": None,
        "calculated_dcf_value": None,
        "valuation_summary": summary,
        "is_math_verified": False,
        "review_action": "stop",
        "review_findings": [
            {
                "severity": "error",
                "code": "UNSUPPORTED_FINANCIAL_FIRM",
                "message": message,
                "retryable": False,
            }
        ],
        "reviewer_feedback": message,
    }
