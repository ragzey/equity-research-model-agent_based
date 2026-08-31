"""Peer-group relative valuation metrics via Yahoo Finance."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import yfinance as yf

from .cache import TTL_PEERS, cache_get, cache_set

logger = logging.getLogger("PeerAnalysisTool")

METRIC_KEYS = (
    "trailing_pe",
    "forward_pe",
    "ev_to_ebitda",
    "operating_margin_pct",
    "revenue_growth_yoy_pct",
)


def _safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        result = float(value)
        if result != result:  # NaN
            return None
        return result
    except (TypeError, ValueError):
        return None


def _pct_from_fraction(value: Any) -> Optional[float]:
    numeric = _safe_float(value)
    if numeric is None:
        return None
    return round(numeric * 100.0, 2)


def fetch_relative_valuation_metrics(ticker: str) -> Dict[str, Any]:
    """
    Pull key relative-valuation fields from yfinance `.info`.
    Returns nulls for unavailable metrics (common for banks, REITs, etc.).
    """
    clean = ticker.strip().upper()
    cached = cache_get("peer_metrics", clean, TTL_PEERS)
    if isinstance(cached, dict) and cached.get("ticker") == clean:
        logger.info("Using cached relative valuation metrics for %s", clean)
        return cached

    logger.info("Fetching relative valuation metrics for %s", clean)

    info = yf.Ticker(clean).info or {}
    metrics = {
        "ticker": clean,
        "company_name": info.get("longName") or info.get("shortName"),
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        "market_cap": _safe_float(info.get("marketCap")),
        "trailing_pe": _safe_float(info.get("trailingPE")),
        "forward_pe": _safe_float(info.get("forwardPE")),
        "ev_to_ebitda": _safe_float(info.get("enterpriseToEbitda")),
        "ebitda": _safe_float(info.get("ebitda")),
        "operating_margin_pct": _pct_from_fraction(info.get("operatingMargins")),
        "revenue_growth_yoy_pct": _pct_from_fraction(info.get("revenueGrowth")),
    }
    logger.info("Metrics resolved for %s: %s", clean, {k: metrics[k] for k in METRIC_KEYS})
    cache_set("peer_metrics", clean, metrics)
    return metrics


def fetch_peer_metadata(ticker: str) -> Dict[str, Any]:
    """Lightweight company metadata for aggregator pre-fetch."""
    clean = ticker.strip().upper()
    cached = cache_get("peer_metadata", clean, TTL_PEERS)
    if isinstance(cached, dict) and cached.get("ticker") == clean:
        return cached
    info = yf.Ticker(clean).info or {}
    metadata = {
        "ticker": clean,
        "company_name": info.get("longName") or info.get("shortName"),
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        "market_cap": info.get("marketCap"),
        "country": info.get("country"),
        "website": info.get("website"),
        "quote_type": info.get("quoteType"),
    }
    cache_set("peer_metadata", clean, metadata)
    return metadata


def _median(values: List[float]) -> Optional[float]:
    if not values:
        return None
    sorted_vals = sorted(values)
    mid = len(sorted_vals) // 2
    if len(sorted_vals) % 2:
        return round(sorted_vals[mid], 4)
    return round((sorted_vals[mid - 1] + sorted_vals[mid]) / 2.0, 4)


DISTRESSED_EV_EBITDA_RATIO = 0.50


def _median_ev_ebitda(values: List[float]) -> Optional[float]:
    """Drop distressed cheap outliers, then take the median of what remains.

    A 5x department-store multiple should not pull an off-price peer set
    that otherwise trades at mid-teens EV/EBITDA.
    """
    core = [value for value in values if value is not None and value > 0]
    if not core:
        return None
    preliminary = _median(core)
    if preliminary is None or len(core) < 3:
        return preliminary
    floor = preliminary * DISTRESSED_EV_EBITDA_RATIO
    kept = [value for value in core if value >= floor]
    if len(kept) < 2:
        return preliminary
    return _median(kept)


def build_peer_comparison_matrix(
    target_ticker: str,
    competitor_tickers: List[str],
) -> Dict[str, Any]:
    """
    Build a peer comparison table for the target and each competitor.
    Includes peer-group medians (competitors only, excluding target).
    """
    target = target_ticker.strip().upper()
    peers = [t.strip().upper() for t in competitor_tickers if t.strip()]
    peers = [t for t in peers if t != target]

    all_tickers = [target] + peers
    by_ticker: Dict[str, Dict[str, Any]] = {}
    for symbol in all_tickers:
        try:
            by_ticker[symbol] = fetch_relative_valuation_metrics(symbol)
        except Exception as exc:
            logger.exception("Peer metrics unavailable for %s", symbol)
            by_ticker[symbol] = {
                "ticker": symbol,
                "error": f"metrics unavailable: {type(exc).__name__}",
            }

    peer_medians: Dict[str, Optional[float]] = {}
    for metric in METRIC_KEYS:
        peer_values = [
            v
            for sym in peers
            if (v := _safe_float(by_ticker.get(sym, {}).get(metric))) is not None
        ]
        if metric == "ev_to_ebitda":
            peer_medians[metric] = _median_ev_ebitda(peer_values)
        else:
            peer_medians[metric] = _median(peer_values)

    return {
        "target": target,
        "competitors": peers,
        "metrics": by_ticker,
        "peer_medians": peer_medians,
    }
