"""Firm products, mix, and company-specific watch items. Not DCF numbers."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict

from ..agents.industry_macro import (
    MIX_VIEWS,
    PRICING_VIEWS,
    _block_open,
    _filing_blob,
    _ground_block,
    _ground_named_list,
    _ground_narrative,
    _ground_watch_items,
    _peer_snapshot,
    _pricing_from_ledger,
    _source_catalog,
)
from ..graphs.desk import ARCHITECT, COMPANY_PRODUCTS, REVIEWER, WRITER, make_message
from ..graphs.state import EquityResearchState
from ..prompts.desk import COMPANY_PRODUCTS_SYSTEM, COMPANY_PRODUCTS_USER
from ..tools.web_research import (
    format_web_research,
    ledger_source_urls,
    web_research_blob,
)
from ..utils.llm_client import LLMCallError, chat_json
from ..graphs.desk import ARCHITECT, COMPANY_PRODUCTS, REVIEWER, WRITER, make_message
from ..graphs.state import EquityResearchState
from ..prompts.desk import COMPANY_PRODUCTS_SYSTEM, COMPANY_PRODUCTS_USER
from ..utils.llm_client import LLMCallError, chat_json

logger = logging.getLogger("CompanyProductsAnalyst")


def normalize_company_products_packet(
    payload: Any,
    *,
    ledger_text: str = "",
    filing_text: str = "",
    allowed_tickers: Any = None,
    source_catalog: Any = None,
    allowed_urls: Any = None,
) -> Dict[str, Any]:
    raw = payload if isinstance(payload, dict) else {}
    source = filing_text or ledger_text
    catalog = list(source_catalog or [])
    urls = list(allowed_urls or [])
    mix = raw.get("mix") if isinstance(raw.get("mix"), dict) else {}
    pricing = raw.get("pricing_power") if isinstance(raw.get("pricing_power"), dict) else {}
    return {
        "products": _ground_named_list(
            raw.get("products") or raw.get("segments"),
            source,
        ),
        "mix": _ground_block(
            mix,
            view_key="view",
            allowed=MIX_VIEWS,
            ledger_text=source,
            source_catalog=catalog,
        ),
        "pricing_power": _ground_block(
            pricing,
            view_key="view",
            allowed=PRICING_VIEWS,
            ledger_text=ledger_text or source,
            source_catalog=catalog,
        ),
        "firm_catalysts": _ground_watch_items(
            raw.get("firm_catalysts") or raw.get("catalysts"),
            source,
            source_catalog=catalog,
        ),
        "narrative": _ground_narrative(
            raw.get("narrative"),
            ledger_text or source,
            allowed_tickers,
            allowed_urls=urls,
        ),
    }


def overlay_ledger_company_views(
    packet: Dict[str, Any],
    *,
    peer_snapshot: Any = None,
) -> Dict[str, Any]:
    updated = dict(packet or {})
    if _block_open(updated.get("pricing_power"), "view"):
        filled = _pricing_from_ledger(peer_snapshot)
        if filled:
            updated["pricing_power"] = filled
    if not str(updated.get("narrative") or "").strip():
        bits = []
        products = updated.get("products") or []
        if products:
            bits.append("Products on the ledger: " + "; ".join(str(item) for item in products) + ".")
        for key in ("mix", "pricing_power"):
            text = str((updated.get(key) or {}).get("evidence") or "").strip()
            if text:
                bits.append(text)
        updated["narrative"] = " ".join(bits)
    return updated


def company_products_node(state: EquityResearchState) -> Dict[str, Any]:
    """Structured commercial packet used for margin/CSRP, not for growth labels."""
    ticker = str(state.get("ticker") or "").strip().upper()
    meta = (state.get("peer_metadata") or {}).get(ticker) or {}
    market = state.get("market_info") or {}
    filing_text = _filing_blob(state)
    excerpts = "\n".join(
        str(item.get("excerpt") or "")
        for item in (state.get("qualitative_evidence") or [])
        if isinstance(item, dict)
    )
    snapshot = _peer_snapshot(state)
    peer_json = json.dumps(snapshot, default=str)
    web_blob = web_research_blob(state)
    web_prompt = format_web_research(state.get("web_research") or [])
    ledger_text = "\n".join(
        part
        for part in (
            filing_text,
            excerpts,
            web_blob,
            peer_json,
            ticker,
            str(meta.get("sector") or market.get("sector") or ""),
            str(meta.get("industry") or market.get("industry") or ""),
            web_prompt,
        )
        if part
    )
    grounded = "\n".join(part for part in (filing_text, excerpts, web_blob) if part)
    catalog = _source_catalog(state, filing_text=grounded)
    allowed_urls = ledger_source_urls(state)
    allowed = {ticker}
    for symbol in state.get("competitor_tickers") or []:
        if str(symbol).strip():
            allowed.add(str(symbol).strip().upper())
    payload = chat_json(
        [
            {"role": "system", "content": COMPANY_PRODUCTS_SYSTEM},
            {
                "role": "user",
                "content": COMPANY_PRODUCTS_USER.format(
                    ticker=ticker,
                    peer_json=peer_json[:8000],
                    qualitative=(state.get("qualitative_analysis_summary") or "")[:4000],
                    filing=filing_text[:8000],
                    web_research=web_prompt[:12000],
                ),
            },
        ],
        timeout=90,
        required=True,
    )
    if not payload:
        raise LLMCallError("Company/products analyst did not return a JSON object.")
    packet = normalize_company_products_packet(
        payload,
        ledger_text=ledger_text,
        filing_text=grounded,
        allowed_tickers=allowed,
        source_catalog=catalog,
        allowed_urls=allowed_urls,
    )
    packet = overlay_ledger_company_views(packet, peer_snapshot=snapshot)
    products = packet.get("products") or []
    narrative = packet.get("narrative") or (
        "No Item 1 product names were grounded; pricing uses the peer ledger when present."
    )
    body = (
        f"{ticker} products packet: {len(products)} named product(s), "
        f"mix {packet['mix']['view']}, pricing {packet['pricing_power']['view']}."
    )
    messages = [
        make_message(
            COMPANY_PRODUCTS,
            ARCHITECT,
            "company_products_packet",
            body,
            {"products": products, "views": {"mix": packet["mix"], "pricing": packet["pricing_power"]}},
        ),
        make_message(
            COMPANY_PRODUCTS,
            REVIEWER,
            "company_products_packet",
            body,
            {"firm_catalysts": packet.get("firm_catalysts")},
        ),
        make_message(
            COMPANY_PRODUCTS,
            WRITER,
            "company_products_narrative",
            narrative,
            {"products": products},
        ),
    ]
    logger.info(body)
    return {
        "company_products_packet": packet,
        "agent_messages": messages,
    }
