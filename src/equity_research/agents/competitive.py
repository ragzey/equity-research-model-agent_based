"""Competitive Analyst node: peer multiples matrix + industry outlook."""

import logging
from statistics import median
from typing import Any, Dict, List, Optional

from ..graphs.desk import COMPETITIVE, REVIEWER, WRITER, make_message
from ..graphs.state import EquityResearchState
from ..tools.peer_analysis import build_peer_comparison_matrix
from ..utils.llm_synthesis import synthesize_industry_outlook

logger = logging.getLogger("CompetitiveAnalyst")


def _as_margin(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        margin = float(value)
    except (TypeError, ValueError):
        return None
    if margin != margin:
        return None
    return margin / 100.0 if abs(margin) > 1 else margin


def _competitive_handoffs(
    target: str,
    matrix: Dict[str, Any],
    outlook: str,
) -> List[Dict[str, Any]]:
    metrics = matrix.get("metrics") or {}
    target_metrics = metrics.get(target) or {}
    peer_margins = []
    for ticker, values in metrics.items():
        if ticker == target or not isinstance(values, dict):
            continue
        parsed = _as_margin(values.get("operating_margin_pct"))
        if parsed is not None:
            peer_margins.append(parsed)
    target_margin = _as_margin(target_metrics.get("operating_margin_pct"))
    peer_median = median(peer_margins) if peer_margins else None
    spread = (
        target_margin - peer_median
        if target_margin is not None and peer_median is not None
        else None
    )

    messages = [
        make_message(
            COMPETITIVE,
            WRITER,
            "positioning_claim",
            f"{target} peer positioning brief.",
            {
                "outlook": (outlook or "")[:4000],
                "target_margin": target_margin,
                "peer_median_margin": peer_median,
                "margin_spread": spread,
            },
        )
    ]
    if spread is None:
        messages.append(
            make_message(
                COMPETITIVE,
                REVIEWER,
                "moat_challenge",
                "Insufficient peer margins to support a moat inference.",
                {"target_margin": target_margin, "peer_median_margin": peer_median},
            )
        )
        return messages

    if spread >= 0.03:
        body = (
            f"{target} operating margin {target_margin:.1%} exceeds peer median "
            f"{peer_median:.1%} by {spread:.1%}. That is a profitability gap, not "
            "standalone proof of a durable moat. Do not lift terminal margin unless "
            "filing evidence shows barriers, switching costs, or network effects."
        )
    else:
        body = (
            f"{target} operating margin {target_margin:.1%} does not beat peer median "
            f"{peer_median:.1%} by 300 bp. Do not treat current profitability as a moat."
        )
    messages.append(
        make_message(
            COMPETITIVE,
            REVIEWER,
            "moat_challenge",
            body,
            {
                "target_margin": target_margin,
                "peer_median_margin": peer_median,
                "margin_spread": spread,
                "superiority_threshold": 0.03,
            },
        )
    )
    return messages


def competitive_analyst_node(state: EquityResearchState) -> Dict[str, Any]:
    """
    Benchmark the target against a peer group on relative valuation metrics.
    Writes peer_comparison_matrix and industry_outlook to the ledger.
    """
    target = state["ticker"].strip().upper()
    competitors: List[str] = state.get("competitor_tickers") or []

    if not competitors:
        logger.warning(
            "No competitor_tickers on ledger; skipping competitive analysis for %s.",
            target,
        )
        return {
            "peer_comparison_matrix": None,
            "industry_outlook": None,
            "agent_messages": [
                make_message(
                    COMPETITIVE,
                    REVIEWER,
                    "moat_challenge",
                    "No peer group supplied; cannot support a moat-based margin lift.",
                )
            ],
        }

    logger.info(
        "Competitive Analyst benchmarking %s against peers: %s",
        target,
        ", ".join(competitors),
    )

    matrix = build_peer_comparison_matrix(target, competitors)

    filing_chunks = state.get("sec_filing_chunks") or []
    filing_excerpt: Optional[str] = (
        "\n\n".join(filing_chunks[:2]) if filing_chunks else None
    )

    outlook = synthesize_industry_outlook(target, matrix, filing_excerpt)
    handoffs = _competitive_handoffs(target, matrix, outlook)

    logger.info(
        "Peer matrix built for %d tickers; industry outlook length %d chars.",
        len(matrix.get("metrics", {})),
        len(outlook),
    )

    return {
        "peer_comparison_matrix": matrix,
        "industry_outlook": outlook,
        "competitive_advantages": (
            "See competitive handoff: margin gaps are not standalone moat proof."
        ),
        "agent_messages": handoffs,
    }
