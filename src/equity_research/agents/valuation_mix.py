"""Valuation-mix analyst: firm/industry fit of DCF versus peer EV/EBITDA."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from ..graphs.desk import ARCHITECT, REVIEWER, VALUATION_MIX, WRITER, make_message
from ..graphs.state import EquityResearchState
from ..prompts.desk import VALUATION_MIX_SYSTEM, VALUATION_MIX_USER
from ..tools.firm_classifier import is_financial_services_firm
from ..tools.valuation_mix import (
    MIX_LABELS,
    PEER_FIT_VIEWS,
    RELATIVE_ROLES,
    mix_ledger,
    mix_metrics,
    overlay_ledger_valuation_mix,
)
from ..tools.web_research import format_web_research, web_research_blob
from ..utils.llm_client import LLMCallError, chat_json
from .industry_macro import (
    _filing_blob,
    _ground_block,
    _ground_narrative,
)

logger = logging.getLogger("ValuationMixAnalyst")

MIX_VIEWS = set(MIX_LABELS) | {"not_applicable", "insufficient"}


def _not_applicable_packet(reason: str, metrics: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return overlay_ledger_valuation_mix(
        {"narrative": reason},
        dict(metrics or {}, is_financial=True),
    )


def normalize_valuation_mix_packet(
    payload: Optional[Dict[str, Any]],
    *,
    ledger_text: str = "",
    allowed_tickers: Optional[List[str]] = None,
) -> Dict[str, Any]:
    raw = payload if isinstance(payload, dict) else {}
    return {
        "mix_view": _ground_block(
            raw.get("mix_view") or {},
            view_key="view",
            allowed=MIX_VIEWS,
            ledger_text=ledger_text,
        ),
        "peer_fit": _ground_block(
            raw.get("peer_fit") or {},
            view_key="view",
            allowed=PEER_FIT_VIEWS,
            ledger_text=ledger_text,
        ),
        "relative_role": _ground_block(
            raw.get("relative_role") or {},
            view_key="view",
            allowed=RELATIVE_ROLES,
            ledger_text=ledger_text,
        ),
        "narrative": _ground_narrative(
            raw.get("narrative"), ledger_text, allowed_tickers
        ),
    }


def valuation_mix_node(state: EquityResearchState) -> Dict[str, Any]:
    """Labeled mix packet. Weights stay on the Python menu."""
    ticker = str(state.get("ticker") or "").strip().upper()
    market = state.get("market_info") or {}
    metrics = mix_metrics(state)
    if state.get("is_financial") or is_financial_services_firm(market) or metrics.get("is_financial"):
        packet = _not_applicable_packet(
            "Valuation mix is out of scope for financial firms on this FCFF desk.",
            metrics,
        )
        body = f"{ticker} valuation-mix skipped: financial firm out of FCFF scope."
        logger.info(body)
        return {
            "valuation_mix_packet": packet,
            "agent_messages": [
                make_message(VALUATION_MIX, WRITER, "valuation_mix_narrative", packet["narrative"], {}),
            ],
        }

    ledger = mix_ledger(metrics)
    filing = _filing_blob(state)
    excerpts = "\n".join(
        str(item.get("excerpt") or "")
        for item in (state.get("qualitative_evidence") or [])
        if isinstance(item, dict)
    )
    web_blob = web_research_blob(state)
    web_prompt = format_web_research(state.get("web_research") or [])
    industry = state.get("industry_macro_packet") or {}
    products = state.get("company_products_packet") or {}
    growth_path = state.get("growth_path_packet") or {}
    selection = state.get("peer_selection") or {}
    ledger_text = "\n".join(
        part
        for part in (
            ledger,
            filing,
            excerpts,
            web_blob,
            json.dumps(metrics, default=str),
            json.dumps(industry, default=str),
            json.dumps(products, default=str),
            json.dumps(growth_path, default=str),
            json.dumps(selection, default=str),
        )
        if part
    )
    allowed = {ticker}
    for symbol in list(state.get("competitor_tickers") or []) + list(
        selection.get("selected") or []
    ):
        if str(symbol).strip():
            allowed.add(str(symbol).strip().upper())
    default_packet = overlay_ledger_valuation_mix({}, metrics)
    payload = chat_json(
        [
            {"role": "system", "content": VALUATION_MIX_SYSTEM},
            {
                "role": "user",
                "content": VALUATION_MIX_USER.format(
                    ticker=ticker,
                    metrics_json=json.dumps(metrics, indent=2, default=str)[:8000],
                    metric_ledger=ledger,
                    allowed_labels=", ".join(default_packet.get("allowed") or ["base"]),
                    default_label=default_packet.get("default_label") or "base",
                    industry_json=json.dumps(industry, indent=2, default=str)[:4000],
                    products_json=json.dumps(products, indent=2, default=str)[:4000],
                    growth_path_json=json.dumps(growth_path, indent=2, default=str)[:4000],
                    peers_json=json.dumps(
                        {
                            "selected": selection.get("selected"),
                            "rejected": selection.get("rejected"),
                            "mode": selection.get("mode"),
                            "rationale": selection.get("rationale"),
                            "competitors": (state.get("peer_comparison_matrix") or {}).get(
                                "competitors"
                            ),
                        },
                        indent=2,
                        default=str,
                    )[:4000],
                    qualitative=(state.get("qualitative_analysis_summary") or "")[:4000],
                    filing=filing[:8000],
                    web_research=web_prompt[:8000],
                ),
            },
        ],
        timeout=90,
        required=True,
    )
    if not payload:
        raise LLMCallError("Valuation-mix analyst did not return a JSON object.")
    packet = normalize_valuation_mix_packet(
        payload, ledger_text=ledger_text, allowed_tickers=list(allowed)
    )
    packet = overlay_ledger_valuation_mix(packet, metrics)
    body = (
        f"{ticker} valuation mix: {packet['label']} "
        f"({packet['dcf_weight']:.0%} DCF / {packet['relative_weight']:.0%} relative); "
        f"peer fit {packet['peer_fit']['view']}; "
        f"relative role {packet['relative_role']['view']}."
    )
    logger.info(body)
    payload_meta = {
        "label": packet["label"],
        "dcf_weight": packet["dcf_weight"],
        "relative_weight": packet["relative_weight"],
        "peer_fit": packet["peer_fit"]["view"],
        "relative_role": packet["relative_role"]["view"],
        "allowed": packet.get("allowed"),
    }
    return {
        "valuation_mix_packet": packet,
        "agent_messages": [
            make_message(
                VALUATION_MIX, ARCHITECT, "valuation_mix_packet", body, payload_meta
            ),
            make_message(
                VALUATION_MIX, REVIEWER, "valuation_mix_packet", body, payload_meta
            ),
            make_message(
                VALUATION_MIX,
                WRITER,
                "valuation_mix_narrative",
                packet.get("narrative") or body,
                {},
            ),
        ],
    }
