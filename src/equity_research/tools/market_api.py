"""Yahoo Finance market-data tool for the three core financial statements."""

import logging
from typing import Any, Dict, Optional

import yfinance as ticker_engine

from .cache import TTL_STATEMENTS, cache_get, cache_set

logger = logging.getLogger("MarketApiTool")


def _is_empty_statement(statement: Any) -> bool:
    """Treat missing or empty pandas objects as a failed data pull."""
    if statement is None:
        return True
    empty = getattr(statement, "empty", None)
    if empty is None:
        return not bool(statement)
    return bool(empty)


def fetch_financial_statements(ticker: str) -> Optional[Dict[str, Any]]:
    """
    Acts as our digital Bloomberg Terminal.
    Connects to Yahoo Finance to fetch the three core financial statements.
    """
    clean_ticker = ticker.strip().upper()
    cached = cache_get("yahoo_statements", clean_ticker, TTL_STATEMENTS)
    if isinstance(cached, dict) and cached.get("income_statement"):
        logger.info("Using cached financial statements for %s", clean_ticker)
        return cached

    logger.info("Initiating financial data pull for ticker: %s", clean_ticker)

    try:
        company = ticker_engine.Ticker(clean_ticker)

        income_stmt = company.financials
        balance_sheet = company.balance_sheet
        cash_flow = company.cash_flow

        if (
            _is_empty_statement(income_stmt)
            or _is_empty_statement(balance_sheet)
            or _is_empty_statement(cash_flow)
        ):
            logger.error(
                "Failed to fetch statements. Data is empty for ticker: %s",
                clean_ticker,
            )
            return None

        financial_data = {
            "income_statement": income_stmt.to_dict(),
            "balance_sheet": balance_sheet.to_dict(),
            "cash_flow_statement": cash_flow.to_dict(),
            "info": company.info,
        }

        logger.info(
            "Successfully fetched all three financial statements for %s",
            clean_ticker,
        )
        cache_set("yahoo_statements", clean_ticker, financial_data)
        return financial_data

    except Exception:
        logger.exception(
            "An unexpected error occurred while fetching data for %s",
            clean_ticker,
        )
        return None
