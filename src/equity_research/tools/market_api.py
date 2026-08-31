"""Yahoo Finance market-data tool with SEC companyfacts fallback."""

import logging
from typing import Any, Dict, Optional, Tuple

import yfinance as ticker_engine

from .cache import TTL_STATEMENTS, cache_get, cache_set

logger = logging.getLogger("MarketApiTool")

_INCOME_ATTRS = (
    "financials",
    "incomestmt",
    "income_stmt",
    "yearly_income_stmt",
    "quarterly_financials",
    "quarterly_income_stmt",
)
_BALANCE_ATTRS = (
    "balance_sheet",
    "balancesheet",
    "yearly_balance_sheet",
    "quarterly_balance_sheet",
)
_CASH_ATTRS = (
    "cash_flow",
    "cashflow",
    "yearly_cashflow",
    "quarterly_cashflow",
    "quarterly_cash_flow",
)


def _is_empty_statement(statement: Any) -> bool:
    """Treat missing or empty pandas objects as a failed data pull."""
    if statement is None:
        return True
    empty = getattr(statement, "empty", None)
    if empty is None:
        return not bool(statement)
    return bool(empty)


def _frame_to_dict(frame: Any) -> Optional[Dict[str, Any]]:
    if _is_empty_statement(frame):
        return None
    converter = getattr(frame, "to_dict", None)
    if callable(converter):
        payload = converter()
        return payload if isinstance(payload, dict) and payload else None
    return frame if isinstance(frame, dict) and frame else None


def _first_frame(company: Any, names: Tuple[str, ...]) -> Any:
    for name in names:
        try:
            frame = getattr(company, name, None)
        except Exception:
            logger.exception("Yahoo attribute %s failed", name)
            continue
        if callable(frame):
            try:
                frame = frame()
            except Exception:
                logger.exception("Yahoo getter %s failed", name)
                continue
        if not _is_empty_statement(frame):
            return frame
    getters = {
        "financials": "get_income_stmt",
        "incomestmt": "get_income_stmt",
        "income_stmt": "get_income_stmt",
        "balance_sheet": "get_balance_sheet",
        "balancesheet": "get_balance_sheet",
        "cash_flow": "get_cash_flow",
        "cashflow": "get_cash_flow",
    }
    tried = set()
    for name in names:
        getter_name = getters.get(name)
        if not getter_name or getter_name in tried:
            continue
        tried.add(getter_name)
        getter = getattr(company, getter_name, None)
        if not callable(getter):
            continue
        try:
            frame = getter()
        except Exception:
            logger.exception("Yahoo %s failed", getter_name)
            continue
        if not _is_empty_statement(frame):
            return frame
    return None


def _fast_info_map(company: Any) -> Dict[str, Any]:
    fast = getattr(company, "fast_info", None)
    if fast is None:
        return {}
    mapping = (
        ("last_price", "currentPrice"),
        ("lastPrice", "currentPrice"),
        ("market_cap", "marketCap"),
        ("marketCap", "marketCap"),
        ("shares", "sharesOutstanding"),
        ("shares_outstanding", "sharesOutstanding"),
        ("currency", "currency"),
        ("regular_market_price", "regularMarketPrice"),
    )
    info: Dict[str, Any] = {}
    for src, dest in mapping:
        value = None
        try:
            value = fast[src]
        except Exception:
            value = getattr(fast, src, None)
        if value is None:
            continue
        try:
            if dest in {"currentPrice", "regularMarketPrice", "marketCap", "sharesOutstanding"}:
                number = float(value)
                if number == number:
                    info[dest] = number
            else:
                info[dest] = value
        except (TypeError, ValueError):
            continue
    return info


def _yahoo_quote_info(company: Any) -> Dict[str, Any]:
    info: Dict[str, Any] = {}
    try:
        raw = company.info
        if isinstance(raw, dict):
            info.update(raw)
    except Exception:
        logger.exception("Yahoo .info unavailable")
    info.update({key: value for key, value in _fast_info_map(company).items() if value is not None})
    if not info.get("currentPrice") and info.get("regularMarketPrice"):
        info["currentPrice"] = info["regularMarketPrice"]
    return info


def _yahoo_statements(company: Any) -> Dict[str, Any]:
    income = _frame_to_dict(_first_frame(company, _INCOME_ATTRS))
    balance = _frame_to_dict(_first_frame(company, _BALANCE_ATTRS))
    cash = _frame_to_dict(_first_frame(company, _CASH_ATTRS))
    payload: Dict[str, Any] = {"statement_source": "yahoo"}
    if income:
        payload["income_statement"] = income
    if balance:
        payload["balance_sheet"] = balance
    if cash:
        payload["cash_flow_statement"] = cash
    return payload


def _has_core_statements(payload: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(payload, dict):
        return False
    return bool(payload.get("income_statement")) and bool(payload.get("balance_sheet"))


def fetch_financial_statements(ticker: str) -> Optional[Dict[str, Any]]:
    """
    Acts as our digital Bloomberg Terminal.

    Yahoo first (annual, then quarterly aliases). If those frames are empty,
    fall back to SEC XBRL companyfacts so a name like Apple still has a P&L.
    """
    from .sec_api import resolve_listed_symbol
    from .sec_facts import fetch_companyfacts_statements

    clean_ticker = ticker.strip().upper()
    listed = resolve_listed_symbol(clean_ticker) or clean_ticker
    cached = cache_get("yahoo_statements", listed, TTL_STATEMENTS)
    if isinstance(cached, dict) and cached.get("income_statement"):
        logger.info(
            "Using cached financial statements for %s (%s)",
            listed,
            cached.get("statement_source") or "cached",
        )
        return cached

    logger.info("Initiating financial data pull for ticker: %s", listed)
    info: Dict[str, Any] = {}
    yahoo_payload: Dict[str, Any] = {}

    try:
        company = ticker_engine.Ticker(listed)
        info = _yahoo_quote_info(company)
        yahoo_payload = _yahoo_statements(company)
    except Exception:
        logger.exception("Yahoo Finance pull failed for %s", listed)

    financial_data: Optional[Dict[str, Any]] = None
    if _has_core_statements(yahoo_payload) and yahoo_payload.get("cash_flow_statement"):
        financial_data = {
            **yahoo_payload,
            "info": info,
            "resolved_ticker": listed,
        }
        logger.info("Successfully fetched Yahoo financial statements for %s", listed)
    else:
        if yahoo_payload:
            logger.warning(
                "Yahoo statements incomplete for %s (income=%s, balance=%s, cash=%s); trying SEC companyfacts.",
                listed,
                bool(yahoo_payload.get("income_statement")),
                bool(yahoo_payload.get("balance_sheet")),
                bool(yahoo_payload.get("cash_flow_statement")),
            )
        sec_payload = fetch_companyfacts_statements(listed)
        if _has_core_statements(sec_payload):
            financial_data = {
                "income_statement": sec_payload["income_statement"],
                "balance_sheet": sec_payload["balance_sheet"],
                "cash_flow_statement": sec_payload.get("cash_flow_statement") or {},
                "info": info,
                "resolved_ticker": listed,
                "statement_source": "sec_companyfacts",
            }
            logger.info("Using SEC companyfacts statements for %s", listed)
        elif _has_core_statements(yahoo_payload):
            financial_data = {
                "income_statement": yahoo_payload["income_statement"],
                "balance_sheet": yahoo_payload["balance_sheet"],
                "cash_flow_statement": yahoo_payload.get("cash_flow_statement") or {},
                "info": info,
                "resolved_ticker": listed,
                "statement_source": "yahoo_partial",
            }
            logger.warning("Using Yahoo income and balance without a cash-flow statement for %s", listed)

    if not financial_data:
        logger.error("Failed to fetch statements from Yahoo and SEC for ticker: %s", listed)
        return None

    cache_set("yahoo_statements", listed, financial_data)
    return financial_data
