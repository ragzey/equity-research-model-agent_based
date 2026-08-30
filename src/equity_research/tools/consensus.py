"""Labeled forward growth from Yahoo analyst estimates, not management guidance."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

import yfinance as yf

from .cache import TTL_CONSENSUS, cache_get, cache_set

logger = logging.getLogger("ConsensusGrowth")

CONSENSUS_ABS_CAP = 0.40


def _as_growth(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:
        return None
    if abs(number) > 1.5:
        number /= 100.0
    if number <= -0.50 or number > CONSENSUS_ABS_CAP:
        return None
    return number


def _growth_from_estimate_frame(frame: Any) -> Optional[float]:
    if frame is None:
        return None
    empty = getattr(frame, "empty", None)
    if empty:
        return None
    try:
        columns = list(frame.columns)
    except Exception:
        return None
    preferred = [column for column in ("+1y", "0y", "+1q") if column in columns]
    if not preferred:
        return None
    column = preferred[0]
    for index_label in ("growth", "Growth"):
        if index_label in getattr(frame, "index", []):
            return _as_growth(frame.loc[index_label, column])
    lowered = {str(index).strip().lower(): index for index in frame.index}
    if "growth" in lowered:
        return _as_growth(frame.loc[lowered["growth"], column])
    return None


def extract_consensus_growth(
    ticker: str,
    info: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Read Yahoo +1y revenue-estimate growth when present.

    Trailing `revenueGrowth` is used only if the estimate table is missing.
    This is sell-side consensus, not a company forecast.
    """
    clean = ticker.strip().upper()
    cached = cache_get("consensus", clean, TTL_CONSENSUS)
    if isinstance(cached, dict):
        return cached

    source = None
    growth = None
    try:
        stock = yf.Ticker(clean)
        growth = _growth_from_estimate_frame(getattr(stock, "revenue_estimate", None))
        if growth is not None:
            source = "yahoo_revenue_estimate_+1y"
    except Exception:
        logger.exception("Yahoo revenue_estimate unavailable for %s", clean)

    if growth is None:
        growth = _as_growth((info or {}).get("revenueGrowth"))
        if growth is not None:
            source = "yahoo_info_revenueGrowth_trailing"

    result = {
        "growth": growth,
        "source": source,
        "label": (
            "Yahoo sell-side consensus / reported growth; not management guidance"
            if source
            else None
        ),
    }
    cache_set("consensus", clean, result)
    return result


def blend_high_growth_rate(
    historical_rate: float,
    bounds: Tuple[float, float],
    consensus: Optional[Dict[str, Any]] = None,
) -> Tuple[float, str]:
    """Average historical CAGR with consensus, then re-apply the firm-type bounds."""
    low, high = bounds
    historical = min(max(float(historical_rate), low), high)
    source = str((consensus or {}).get("source") or "")
    consensus_growth = (consensus or {}).get("growth")
    if consensus_growth is None:
        return historical, "High-growth rate from bounded historical revenue CAGR."
    if "trailing" in source.lower():
        return (
            historical,
            (
                f"High-growth rate {historical:.1%} uses bounded historical CAGR only. "
                f"{source} is trailing reported growth, not a forward estimate, so it "
                "was not blended into the DCF."
            ),
        )
    consensus_growth = min(max(float(consensus_growth), 0.0), CONSENSUS_ABS_CAP)
    blended = 0.5 * historical + 0.5 * consensus_growth
    applied = min(max(blended, low), high)
    rationale = (
        f"High-growth rate {applied:.1%} is a 50/50 blend of bounded historical "
        f"CAGR {historical:.1%} and {consensus.get('source')} {consensus_growth:.1%}, "
        f"then clipped to the firm-type band {low:.1%}-{high:.1%}. "
        "This is labeled consensus, not a management forecast."
    )
    return applied, rationale
