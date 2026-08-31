"""Twelve-month adjusted closes versus a market benchmark, rebased to 100."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import yfinance as yf

from .cache import TTL_PRICE_HISTORY, cache_get, cache_set

logger = logging.getLogger("PriceHistory")

DEFAULT_BENCHMARK = "SPY"
DEFAULT_PERIOD = "1y"
MIN_POINTS = 8


def _close_map(history: Any) -> Dict[str, float]:
    if history is None or getattr(history, "empty", True):
        return {}
    closes: Dict[str, float] = {}
    try:
        series = history["Close"]
    except Exception:
        return {}
    for timestamp, value in series.items():
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        if numeric != numeric or numeric <= 0:
            continue
        if hasattr(timestamp, "date"):
            date_key = timestamp.date().isoformat()
        else:
            date_key = str(timestamp)[:10]
        closes[date_key] = numeric
    return closes


def rebase_aligned_series(
    stock: Dict[str, float],
    benchmark: Dict[str, float],
) -> List[Dict[str, Any]]:
    """Intersect dates and rebase both series to 100 at the first overlap."""
    dates = sorted(set(stock) & set(benchmark))
    if len(dates) < MIN_POINTS:
        return []
    stock0 = stock[dates[0]]
    bench0 = benchmark[dates[0]]
    if stock0 <= 0 or bench0 <= 0:
        return []
    points: List[Dict[str, Any]] = []
    for date_key in dates:
        stock_px = stock[date_key]
        bench_px = benchmark[date_key]
        points.append(
            {
                "date": date_key,
                "stock": round(100.0 * stock_px / stock0, 4),
                "benchmark": round(100.0 * bench_px / bench0, 4),
                "stock_price": round(stock_px, 4),
                "benchmark_price": round(bench_px, 4),
            }
        )
    return points


def _download_closes(symbol: str, period: str) -> Dict[str, float]:
    history = yf.Ticker(symbol).history(
        period=period,
        interval="1wk",
        auto_adjust=True,
    )
    return _close_map(history)


def fetch_rebased_price_history(
    ticker: str,
    benchmark: str = DEFAULT_BENCHMARK,
    period: str = DEFAULT_PERIOD,
) -> Optional[Dict[str, Any]]:
    """
    Weekly adjusted closes for the target and a market ETF, indexed to 100.

    Failures return None so the rest of the pipeline can still write a memo.
    """
    from .sec_api import resolve_listed_symbol

    clean = resolve_listed_symbol(ticker) or ticker.strip().upper()
    bench = benchmark.strip().upper()
    cache_key = f"{clean}:{bench}:{period}"
    cached = cache_get("price_history", cache_key, TTL_PRICE_HISTORY)
    if isinstance(cached, dict) and cached.get("points"):
        return cached

    try:
        stock_closes = _download_closes(clean, period)
        bench_closes = _download_closes(bench, period)
    except Exception:
        logger.exception("Price history download failed for %s vs %s", clean, bench)
        return None

    points = rebase_aligned_series(stock_closes, bench_closes)
    if not points:
        logger.warning("Not enough overlapping price history for %s vs %s", clean, bench)
        return None

    payload: Dict[str, Any] = {
        "ticker": clean,
        "benchmark": bench,
        "benchmark_label": "S&P 500 (SPY)" if bench == "SPY" else bench,
        "period": period,
        "interval": "1wk",
        "start": points[0]["date"],
        "end": points[-1]["date"],
        "points": points,
    }
    cache_set("price_history", cache_key, payload)
    logger.info(
        "Indexed %d weekly points for %s vs %s (%s to %s)",
        len(points),
        clean,
        bench,
        payload["start"],
        payload["end"],
    )
    return payload
