"""Competitive Analyst: discover/select comps, then peer multiples and outlook."""

from __future__ import annotations

import json
import logging
from statistics import median
from typing import Any, Dict, List, Optional

from ..graphs.desk import COMPETITIVE, REVIEWER, WRITER, make_message
from ..graphs.state import EquityResearchState
from ..prompts.desk import (
    COMPETITIVE_PEER_SYSTEM,
    COMPETITIVE_PEER_USER,
    COMPETITIVE_PINNED_USER,
)
from ..tools.peer_analysis import build_peer_comparison_matrix
from ..tools.peer_discovery import (
    MAX_PEERS,
    apply_named_picks,
    clip_rejected_picks,
    rank_peer_candidates,
)
from ..utils.llm_client import LLMCallError, chat_json
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


def _llm_peer_picks(
    target: str,
    industry: str,
    sector: str,
    candidates: List[Dict[str, Any]],
    ranked: Dict[str, Any],
) -> Dict[str, Any]:
    payload = chat_json(
        [
            {"role": "system", "content": COMPETITIVE_PEER_SYSTEM},
            {
                "role": "user",
                "content": COMPETITIVE_PEER_USER.format(
                    ticker=target,
                    industry=industry or "n/a",
                    sector=sector or "n/a",
                    candidates_json=json.dumps(
                        {
                            "candidates": candidates,
                            "harvest_ranking": ranked.get("ranked") or [],
                        },
                        indent=2,
                        default=str,
                    )[:12_000],
                ),
            },
        ],
        timeout=60,
        required=True,
    ) or {}
    selected = apply_named_picks(candidates, payload.get("selected") or [])
    if not selected:
        raise LLMCallError(
            "Competitive analyst did not keep any tickers from the harvested candidate list."
        )
    rejected = clip_rejected_picks(candidates, payload.get("rejected"))
    if not rejected:
        rejected = ranked.get("rejected") or []
    rationale = str(payload.get("rationale") or "").strip()
    if not rationale:
        raise LLMCallError("Competitive analyst did not return a peer-selection rationale.")
    return {
        "selected": selected,
        "rejected": rejected,
        "rationale": rationale,
        "mode": "llm",
    }


def _llm_pinned_rationale(
    target: str,
    industry: str,
    sector: str,
    pinned: List[str],
) -> str:
    payload = chat_json(
        [
            {"role": "system", "content": COMPETITIVE_PEER_SYSTEM},
            {
                "role": "user",
                "content": COMPETITIVE_PINNED_USER.format(
                    ticker=target,
                    industry=industry or "n/a",
                    sector=sector or "n/a",
                    peers_json=json.dumps({"pinned": pinned}, indent=2),
                ),
            },
        ],
        timeout=60,
        required=True,
    ) or {}
    rationale = str(payload.get("rationale") or "").strip()
    if not rationale:
        raise LLMCallError(
            "Competitive analyst did not return a rationale for the pinned peer set."
        )
    return rationale


def select_comparable_set(state: EquityResearchState) -> Dict[str, Any]:
    """Choose comps. Operator-pinned names win; otherwise Competitive ranks harvest."""
    target = state["ticker"].strip().upper()
    pinned = [
        symbol.strip().upper()
        for symbol in (state.get("competitor_tickers") or [])
        if symbol and symbol.strip().upper() != target
    ]
    metadata = state.get("peer_metadata") or {}
    target_meta = metadata.get(target) or state.get("market_info") or {}
    industry = str(target_meta.get("industry") or "")
    sector = str(target_meta.get("sector") or "")
    if pinned:
        selected = pinned[:MAX_PEERS]
        return {
            "selected": selected,
            "rejected": [],
            "rationale": _llm_pinned_rationale(target, industry, sector, selected),
            "mode": "pinned",
        }

    discovered = state.get("discovered_peers") or {}
    candidates = discovered.get("candidates") or []
    ranked = rank_peer_candidates(target, candidates, metadata)
    if not candidates:
        return ranked
    return _llm_peer_picks(target, industry, sector, candidates, ranked)


def competitive_analyst_node(state: EquityResearchState) -> Dict[str, Any]:
    """
    Select a comparable set, then benchmark relative valuation metrics.
    Writes competitor_tickers, peer_selection, matrix, and outlook.
    """
    target = state["ticker"].strip().upper()
    selection = select_comparable_set(state)
    competitors: List[str] = list(selection.get("selected") or [])
    peer_selection = {
        "selected": competitors,
        "rejected": selection.get("rejected") or [],
        "rationale": selection.get("rationale") or "",
        "mode": selection.get("mode") or "llm",
        "sources_used": (state.get("discovered_peers") or {}).get("sources_used") or [],
    }

    selection_body = (
        peer_selection["rationale"]
        or "Competitive analyst did not find a usable peer set."
    )
    selection_message = make_message(
        COMPETITIVE,
        WRITER,
        "peer_selection",
        selection_body,
        peer_selection,
    )
    reviewer_copy = make_message(
        COMPETITIVE,
        REVIEWER,
        "peer_selection",
        selection_body,
        {"selected": competitors, "mode": peer_selection["mode"]},
    )

    if not competitors:
        logger.warning("No usable comps for %s after discovery and ranking.", target)
        return {
            "competitor_tickers": None,
            "peer_selection": peer_selection,
            "peer_comparison_matrix": None,
            "industry_outlook": None,
            "agent_messages": [
                selection_message,
                reviewer_copy,
                make_message(
                    COMPETITIVE,
                    REVIEWER,
                    "moat_challenge",
                    "No peer group could be harvested; cannot support a moat-based margin lift.",
                ),
            ],
        }

    logger.info(
        "Competitive Analyst using %s comps for %s: %s",
        peer_selection["mode"],
        target,
        ", ".join(competitors),
    )
    matrix = build_peer_comparison_matrix(target, competitors)
    filing_chunks = state.get("sec_filing_chunks") or []
    filing_excerpt: Optional[str] = (
        "\n\n".join(filing_chunks[:2]) if filing_chunks else None
    )
    outlook = synthesize_industry_outlook(target, matrix, filing_excerpt)
    handoffs = [selection_message, reviewer_copy]
    handoffs.extend(_competitive_handoffs(target, matrix, outlook))

    logger.info(
        "Peer matrix built for %d tickers; industry outlook length %d chars.",
        len(matrix.get("metrics", {})),
        len(outlook),
    )
    return {
        "competitor_tickers": competitors,
        "peer_selection": peer_selection,
        "peer_comparison_matrix": matrix,
        "industry_outlook": outlook,
        "competitive_advantages": (
            "See competitive handoff: margin gaps are not standalone moat proof."
        ),
        "agent_messages": handoffs,
    }
