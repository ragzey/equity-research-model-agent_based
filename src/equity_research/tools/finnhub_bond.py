"""Finnhub Bond API wrapper for FINRA TRACE yields (primary cost-of-debt data path)."""

from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

import requests
from dotenv import load_dotenv

from .cache import TTL_BONDS, cache_get, cache_set

load_dotenv()

logger = logging.getLogger("FinnhubBondTool")

FINNHUB_BASE_URL = "https://finnhub.io/api/v1"
REQUEST_TIMEOUT = 30


def _api_key() -> str:
    key = os.getenv("FINNHUB_API_KEY", "").strip()
    if not key:
        raise ValueError(
            "FINNHUB_API_KEY is not set. Add it to your .env file to use the TRACE bond path."
        )
    return key


def _finnhub_get(path: str, params: Dict[str, Any]) -> Any:
    params = {**params, "token": _api_key()}
    response = requests.get(
        f"{FINNHUB_BASE_URL}{path}",
        params=params,
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    return response.json()


def _recent_trace_dates(lookback_days: int = 7) -> List[str]:
    """Return recent calendar dates (YYYY-MM-DD) to probe for TRACE prints."""
    today = date.today()
    dates: List[str] = []
    for offset in range(lookback_days):
        probe = today - timedelta(days=offset)
        dates.append(probe.isoformat())
    return dates


def _years_to_maturity(maturity_date_str: Optional[str]) -> Optional[float]:
    if not maturity_date_str:
        return None
    try:
        maturity = datetime.strptime(maturity_date_str[:10], "%Y-%m-%d").date()
    except ValueError:
        logger.warning("Could not parse maturity date: %s", maturity_date_str)
        return None
    return max((maturity - date.today()).days / 365.25, 0.0)


def _latest_trace_yield(isin: str) -> Optional[float]:
    """
    Pull the most recent TRACE yield for an ISIN from Finnhub bond_tick.
    Response field `y` is a list of yields (percent); we convert to decimal.
    """
    for trade_date in _recent_trace_dates():
        try:
            payload = _finnhub_get(
                "/bond/tick",
                {
                    "isin": isin,
                    "date": trade_date,
                    "limit": 250,
                    "skip": 0,
                    "exchange": "trace",
                },
            )
        except requests.HTTPError as exc:
            logger.warning("TRACE tick request failed for %s on %s: %s", isin, trade_date, exc)
            continue

        yields = payload.get("y") or []
        numeric = [float(y) for y in yields if y is not None]
        if not numeric:
            continue

        # Finnhub TRACE yields are quoted in percent (e.g. 4.8 = 4.8%).
        latest_yield_pct = numeric[-1]
        logger.info(
            "TRACE yield for %s on %s: %.3f%% (%d ticks)",
            isin,
            trade_date,
            latest_yield_pct,
            len(numeric),
        )
        return latest_yield_pct / 100.0

    logger.warning("No TRACE yield found for ISIN %s in lookback window.", isin)
    return None


def _bond_profile(isin: str) -> Dict[str, Any]:
    return _finnhub_get("/bond/profile", {"isin": isin})


def get_outstanding_bonds_for_ticker(target_bonds: List[str]) -> Optional[List[Dict[str, float]]]:
    """
    Build structured bond quotes for debt_analysis interpolation.

    Finnhub does not expose a 'list all bonds by equity ticker' endpoint. You must
    supply corporate bond ISINs (e.g. from filings or a security master). Each
    returned dict uses keys compatible with debt_analysis:
      - maturity_years
      - ytm  (decimal, e.g. 0.048 for 4.8%)
    """
    if not target_bonds:
        logger.info("No target_bonds ISINs provided; skipping Finnhub TRACE pull.")
        return None

    _api_key()  # Fail fast before per-ISIN loop swallows auth errors.

    quotes: List[Dict[str, float]] = []

    for raw_isin in target_bonds:
        isin = raw_isin.strip().upper()
        if not isin:
            continue

        logger.info("Fetching Finnhub profile + TRACE ticks for ISIN %s", isin)
        cached = cache_get("trace_quote", isin, TTL_BONDS)
        if isinstance(cached, dict):
            if cached.get("skip"):
                logger.info("Using cached TRACE miss for %s", isin)
                continue
            quote = cached.get("quote")
            if isinstance(quote, dict) and quote.get("ytm") is not None:
                quotes.append(quote)
                continue

        try:
            profile = _bond_profile(isin)
            maturity_years = _years_to_maturity(profile.get("maturityDate"))
            ytm = _latest_trace_yield(isin)

            if maturity_years is None or ytm is None:
                logger.warning(
                    "Skipping ISIN %s (maturity=%s, ytm=%s).",
                    isin,
                    maturity_years,
                    ytm,
                )
                cache_set("trace_quote", isin, {"skip": True})
                continue

            quote = {
                "isin": isin,
                "maturity_years": round(maturity_years, 4),
                "ytm": ytm,
            }
            cache_set("trace_quote", isin, {"quote": quote})
            quotes.append(quote)
        except (requests.HTTPError, KeyError, TypeError, ValueError) as exc:
            logger.warning("Failed to resolve bond data for ISIN %s: %s", isin, exc)

    if not quotes:
        logger.error("No usable TRACE bond quotes returned for supplied ISIN list.")
        return None

    logger.info("Resolved %d outstanding bond quote(s) from Finnhub TRACE.", len(quotes))
    return quotes
