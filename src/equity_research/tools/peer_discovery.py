"""Discover and rank operating-company peers from public market sources."""

from __future__ import annotations

import logging
import math
import os
import re
from typing import Any, Dict, List, Optional

import requests

from .cache import TTL_PEERS, cache_get, cache_set
from .peer_analysis import fetch_peer_metadata

logger = logging.getLogger("PeerDiscovery")

YAHOO_RECS_URL = (
    "https://query2.finance.yahoo.com/v6/finance/recommendationsbysymbol/{ticker}"
)
FINNHUB_PEERS_URL = "https://finnhub.io/api/v1/stock/peers"
YAHOO_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; EquityResearchDesk/1.0)",
    "Accept": "application/json",
}
MAX_CANDIDATES = 12
MAX_PEERS = 4
TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,7}$")
INDEX_OR_ETF = frozenset(
    {
        "SPY",
        "QQQ",
        "IWM",
        "DIA",
        "VTI",
        "VOO",
        "IVV",
        "VEA",
        "VWO",
        "AGG",
        "BND",
        "TLT",
        "GLD",
        "SLV",
        "USO",
        "UNG",
        "XLF",
        "XLE",
        "XLY",
        "XLP",
        "XLI",
        "XLK",
        "XLV",
        "XLB",
        "XLRE",
        "XLC",
        "ARKK",
        "IWF",
        "IWD",
        "IWN",
        "IWO",
        "RSP",
        "MDY",
        "IJH",
        "IJR",
        "EFA",
        "EEM",
    }
)
PASSIVE_QUOTE_TYPES = frozenset(
    {"ETF", "MUTUALFUND", "INDEX", "CURRENCY", "FUTURE", "OPTION"}
)


def _clean_ticker(value: Any) -> Optional[str]:
    symbol = str(value or "").strip().upper()
    if not symbol or not TICKER_RE.match(symbol):
        return None
    if symbol in INDEX_OR_ETF or symbol.startswith("^"):
        return None
    return symbol


def is_passive_vehicle(metadata: Optional[Dict[str, Any]]) -> bool:
    info = metadata or {}
    quote_type = str(info.get("quote_type") or info.get("quoteType") or "").upper()
    if quote_type in PASSIVE_QUOTE_TYPES:
        return True
    name = " ".join(
        str(info.get(key) or "") for key in ("company_name", "longName", "shortName")
    ).upper()
    return " ETF" in f" {name}" or name.endswith("ETF")


def _yahoo_recommended_symbols(ticker: str) -> List[Dict[str, Any]]:
    url = YAHOO_RECS_URL.format(ticker=ticker)
    response = requests.get(url, headers=YAHOO_HEADERS, timeout=20)
    response.raise_for_status()
    payload = response.json() or {}
    rows = []
    for block in ((payload.get("finance") or {}).get("result") or []):
        for item in block.get("recommendedSymbols") or []:
            symbol = _clean_ticker(item.get("symbol"))
            if not symbol or symbol == ticker:
                continue
            try:
                score = float(item.get("score") or 0.0)
            except (TypeError, ValueError):
                score = 0.0
            rows.append(
                {
                    "ticker": symbol,
                    "score": score,
                    "source": "yahoo_recommendations",
                }
            )
    return rows


def _finnhub_peer_symbols(ticker: str) -> List[str]:
    token = os.getenv("FINNHUB_API_KEY", "").strip()
    if not token:
        return []
    response = requests.get(
        FINNHUB_PEERS_URL,
        params={"symbol": ticker, "token": token},
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list):
        return []
    symbols = []
    for raw in payload:
        symbol = _clean_ticker(raw)
        if symbol and symbol != ticker:
            symbols.append(symbol)
    return symbols


def discover_peer_candidates(ticker: str) -> Dict[str, Any]:
    """
    Harvest similar-stock candidates from Yahoo, and Finnhub when a key exists.

    Does not choose the final comps; Competitive ranks and reasons over this list.
    """
    clean = ticker.strip().upper()
    cached = cache_get("peer_discovery", clean, TTL_PEERS)
    if isinstance(cached, dict) and cached.get("target") == clean:
        logger.info("Using cached peer candidates for %s", clean)
        return cached

    by_ticker: Dict[str, Dict[str, Any]] = {}
    sources_used: List[str] = []

    try:
        for row in _yahoo_recommended_symbols(clean):
            current = by_ticker.setdefault(
                row["ticker"],
                {"ticker": row["ticker"], "sources": [], "yahoo_score": 0.0},
            )
            if "yahoo_recommendations" not in current["sources"]:
                current["sources"].append("yahoo_recommendations")
            current["yahoo_score"] = max(current["yahoo_score"], row["score"])
        if by_ticker:
            sources_used.append("yahoo_recommendations")
    except Exception:
        logger.exception("Yahoo similar-stock lookup failed for %s", clean)

    try:
        for symbol in _finnhub_peer_symbols(clean):
            current = by_ticker.setdefault(
                symbol,
                {"ticker": symbol, "sources": [], "yahoo_score": 0.0},
            )
            if "finnhub_peers" not in current["sources"]:
                current["sources"].append("finnhub_peers")
        if any("finnhub_peers" in row["sources"] for row in by_ticker.values()):
            sources_used.append("finnhub_peers")
    except Exception:
        logger.exception("Finnhub peer lookup failed for %s", clean)

    candidates = sorted(
        by_ticker.values(),
        key=lambda row: (-float(row.get("yahoo_score") or 0.0), row["ticker"]),
    )[:MAX_CANDIDATES]
    result = {
        "target": clean,
        "candidates": candidates,
        "sources_used": sources_used,
    }
    cache_set("peer_discovery", clean, result)
    logger.info(
        "Discovered %d peer candidate(s) for %s from %s",
        len(candidates),
        clean,
        ", ".join(sources_used) or "no source",
    )
    return result


def score_peer(
    target_meta: Dict[str, Any],
    candidate_meta: Dict[str, Any],
    yahoo_score: float = 0.0,
) -> float:
    """Higher is a better operating-company comparable."""
    if is_passive_vehicle(candidate_meta):
        return -1_000.0
    score = float(yahoo_score or 0.0) * 10.0
    target_industry = str(target_meta.get("industry") or "").strip().lower()
    peer_industry = str(candidate_meta.get("industry") or "").strip().lower()
    target_sector = str(target_meta.get("sector") or "").strip().lower()
    peer_sector = str(candidate_meta.get("sector") or "").strip().lower()
    if target_industry and target_industry == peer_industry:
        score += 100.0
    elif target_sector and target_sector == peer_sector:
        score += 40.0
    else:
        score -= 15.0
    target_cap = target_meta.get("market_cap")
    peer_cap = candidate_meta.get("market_cap")
    try:
        if target_cap and peer_cap and float(target_cap) > 0 and float(peer_cap) > 0:
            gap = abs(math.log(float(peer_cap) / float(target_cap)))
            score += max(0.0, 25.0 - 10.0 * gap)
    except (TypeError, ValueError):
        pass
    return score


def apply_named_picks(
    candidates: List[Dict[str, Any]],
    picks: List[str],
    max_peers: int = MAX_PEERS,
) -> List[str]:
    """Keep only tickers that appeared in the harvested candidate list."""
    allowed = {str(row.get("ticker") or "").upper() for row in candidates}
    selected: List[str] = []
    for raw in picks or []:
        symbol = _clean_ticker(raw)
        if not symbol or symbol not in allowed or symbol in selected:
            continue
        selected.append(symbol)
        if len(selected) >= max_peers:
            break
    return selected


def clip_rejected_picks(
    candidates: List[Dict[str, Any]],
    rejected: Any,
) -> List[Dict[str, str]]:
    """Keep rejected-comp notes only for harvested tickers."""
    allowed = {str(row.get("ticker") or "").upper() for row in candidates}
    clipped: List[Dict[str, str]] = []
    seen = set()
    for raw in rejected or []:
        if isinstance(raw, dict):
            symbol = _clean_ticker(raw.get("ticker"))
            reason = str(raw.get("reason") or "").strip()
        else:
            symbol = _clean_ticker(raw)
            reason = ""
        if not symbol or symbol not in allowed or symbol in seen:
            continue
        seen.add(symbol)
        clipped.append({"ticker": symbol, "reason": reason})
    return clipped


def rank_peer_candidates(
    target: str,
    candidates: List[Dict[str, Any]],
    metadata: Dict[str, Dict[str, Any]],
    max_peers: int = MAX_PEERS,
) -> Dict[str, Any]:
    """Deterministic ranking: same industry first, then sector, then Yahoo score."""
    target_meta = metadata.get(target) or {}
    ranked: List[Dict[str, Any]] = []
    rejected: List[Dict[str, str]] = []
    for row in candidates:
        symbol = str(row.get("ticker") or "").upper()
        if not symbol or symbol == target:
            continue
        meta = metadata.get(symbol) or {}
        if is_passive_vehicle(meta):
            rejected.append({"ticker": symbol, "reason": "ETF or other passive vehicle."})
            continue
        ranked.append(
            {
                "ticker": symbol,
                "score": score_peer(target_meta, meta, float(row.get("yahoo_score") or 0.0)),
                "industry": meta.get("industry"),
                "sector": meta.get("sector"),
                "sources": row.get("sources") or [],
            }
        )
    ranked.sort(key=lambda item: (-item["score"], item["ticker"]))
    target_industry = str(target_meta.get("industry") or "").strip().lower()
    target_sector = str(target_meta.get("sector") or "").strip().lower()

    def _match(row: Dict[str, Any], field: str, expected: str) -> bool:
        if not expected:
            return False
        return str(row.get(field) or "").strip().lower() == expected

    industry_hits = [row for row in ranked if _match(row, "industry", target_industry)]
    sector_hits = [
        row
        for row in ranked
        if row not in industry_hits and _match(row, "sector", target_sector)
    ]
    other = [
        row for row in ranked if row not in industry_hits and row not in sector_hits
    ]
    selected_rows: List[Dict[str, Any]] = []
    for bucket in (industry_hits, sector_hits):
        for row in bucket:
            selected_rows.append(row)
            if len(selected_rows) >= max_peers:
                break
        if len(selected_rows) >= max_peers:
            break
    if not selected_rows:
        selected_rows = other[:max_peers]
    selected = [item["ticker"] for item in selected_rows]
    selected_set = set(selected)
    for item in ranked:
        if item["ticker"] in selected_set:
            continue
        if any(drop["ticker"] == item["ticker"] for drop in rejected):
            continue
        rejected.append(
            {
                "ticker": item["ticker"],
                "reason": "Lower industry/sector fit than the selected comps.",
            }
        )
    industry = target_meta.get("industry") or target_meta.get("sector") or "the target industry"
    if selected:
        rationale = (
            f"Selected {', '.join(selected)} as the closest listed operating comps "
            f"to {target} in {industry}. Ranked Yahoo/Finnhub similar-stock "
            "candidates by industry match, then sector, then size proximity. "
            "Dropped ETFs and weaker matches."
        )
    else:
        rationale = (
            f"No usable operating-company comps remained for {target} after "
            "filtering ETFs and incomplete metadata."
        )
    return {
        "selected": selected,
        "rejected": rejected,
        "ranked": ranked,
        "rationale": rationale,
        "mode": "deterministic",
    }


def hydrate_peer_metadata(
    tickers: List[str],
    existing: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Dict[str, Any]]:
    """Fetch metadata for discovery candidates; skip names that already exist."""
    metadata = dict(existing or {})
    for raw in tickers:
        symbol = _clean_ticker(raw)
        if not symbol or symbol in metadata:
            continue
        try:
            metadata[symbol] = fetch_peer_metadata(symbol)
        except Exception:
            logger.exception("Peer metadata unavailable for %s", symbol)
    return metadata
