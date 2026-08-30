"""Quant Analyst: lifecycle classification, capital costs, WACC, and FCFF DCF."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Sequence

import yfinance as yf

from ..graphs.state import EquityResearchState
from ..tools.cache import TTL_TREASURY, cache_get, cache_set
from ..tools.debt_analysis import calculate_cost_of_debt, extract_ebit_and_interest
from ..tools.firm_classifier import (
    classify_firm_and_adjust_assumptions,
    extract_operating_baseline,
)
from ..tools.valuation import calculate_wacc, perform_3stage_dcf_valuation

logger = logging.getLogger("QuantAnalyst")

MARGINAL_TAX_RATE = 0.21
EQUITY_RISK_PREMIUM = 0.05
TREASURY_10Y_TICKER = "^TNX"
TERMINAL_GROWTH_DEFAULT = 0.025
TERMINAL_WACC_TARGET = 0.08

DEBT_LABELS = ("total debt",)
CURRENT_DEBT_LABELS = ("current debt", "current debt and capital lease obligation")
LONG_DEBT_LABELS = (
    "long term debt",
    "long term debt and capital lease obligation",
)
CASH_LABELS = (
    "cash cash equivalents and short term investments",
    "cash and cash equivalents",
    "cash cash equivalents and federal funds sold",
)


def _latest_market_price(stock: Optional[yf.Ticker], info: Dict[str, Any]) -> float:
    price = info.get("currentPrice") or info.get("regularMarketPrice")
    if price is None and stock is not None:
        history = stock.history(period="5d")
        if history is not None and not history.empty:
            price = history["Close"].iloc[-1]
    if price is None or float(price) <= 0:
        raise ValueError("A positive current stock price is required.")
    return float(price)


def fetch_ten_year_treasury_yield() -> float:
    """Live 10-year Treasury yield from Yahoo (^TNX is quoted in percent)."""
    cached = cache_get("treasury", "10y", TTL_TREASURY)
    if cached is not None:
        return float(cached)
    logger.info("Fetching live 10-year Treasury yield from %s", TREASURY_10Y_TICKER)
    treasury = yf.Ticker(TREASURY_10Y_TICKER)
    info = treasury.info or {}
    yield_percent = info.get("regularMarketPrice") or info.get("currentPrice")
    if yield_percent is None:
        history = treasury.history(period="5d")
        if history is not None and not history.empty:
            yield_percent = history["Close"].iloc[-1]
    if yield_percent is None or float(yield_percent) <= 0:
        raise ValueError("Could not fetch a valid live 10-year Treasury yield.")
    result = float(yield_percent) / 100.0
    logger.info("Live 10-year Treasury yield: %.2f%%", result * 100)
    cache_set("treasury", "10y", result)
    return result


def _normalize(value: Any) -> str:
    return str(value).strip().lower()


def _latest_balance_row(balance_sheet: Dict[Any, Any]) -> Dict[str, Any]:
    if not balance_sheet:
        return {}
    first_value = next(iter(balance_sheet.values()), None)
    if not isinstance(first_value, dict):
        return {}

    # market_api DataFrame.to_dict(): {period: {line item: value}}
    first_keys = {_normalize(key) for key in first_value}
    if any("debt" in key or "cash" in key for key in first_keys):
        latest = max(balance_sheet.keys())
        return {_normalize(k): v for k, v in balance_sheet[latest].items()}

    # Metric-major compatibility: {line item: {period: value}}
    result: Dict[str, Any] = {}
    for metric, observations in balance_sheet.items():
        if isinstance(observations, dict) and observations:
            latest = max(observations.keys())
            result[_normalize(metric)] = observations[latest]
    return result


def _first_number(row: Dict[str, Any], labels: Sequence[str]) -> Optional[float]:
    for label in labels:
        value = row.get(label)
        if value is None:
            continue
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        if numeric == numeric:
            return numeric
    return None


def extract_debt_and_cash(balance_sheet: Dict[Any, Any]) -> tuple[float, float, bool]:
    """Extract latest total debt and cash from either supported statement shape."""
    row = _latest_balance_row(balance_sheet)
    total_debt = _first_number(row, DEBT_LABELS)
    if total_debt is None:
        current = _first_number(row, CURRENT_DEBT_LABELS) or 0.0
        long_term = _first_number(row, LONG_DEBT_LABELS) or 0.0
        total_debt = current + long_term
    cash = _first_number(row, CASH_LABELS)
    cash_missing = cash is None
    if cash_missing:
        cash = 0.0
        logger.warning("Cash field unavailable; using zero and flagging this in summary.")
    return max(total_debt, 0.0), max(cash, 0.0), cash_missing


def _no_debt_result(risk_free_rate: float) -> Dict[str, Any]:
    return {
        "method_used": "No Debt (zero WACC weight)",
        "pre_tax_cost_of_debt": risk_free_rate,
        "after_tax_cost_of_debt": risk_free_rate * (1 - MARGINAL_TAX_RATE),
        "marginal_tax_rate_applied": MARGINAL_TAX_RATE,
        "details": {"debt_weight_expected": 0.0},
    }


def quant_analyst_node(state: EquityResearchState) -> Dict[str, Any]:
    """Return a partial LangGraph update with WACC, DCF value, and audit details."""
    ticker = state["ticker"].strip().upper()
    income_statement = state.get("income_statement") or {}
    balance_sheet = state.get("balance_sheet") or {}
    if not income_statement or not balance_sheet:
        raise ValueError("Aggregator must populate income and balance sheets before Quant.")

    stock = None
    info = dict(state.get("market_info") or {})
    needs_live_quote = not (
        info.get("currentPrice") or info.get("regularMarketPrice")
    ) or not info.get("sharesOutstanding") or info.get("beta") is None
    if needs_live_quote:
        stock = yf.Ticker(ticker)
        info = {**info, **(stock.info or {})}
    share_price = _latest_market_price(stock, info)
    shares_outstanding = info.get("sharesOutstanding")
    beta = info.get("beta")
    if shares_outstanding is None or float(shares_outstanding) <= 0:
        raise ValueError("Positive shares outstanding are required for WACC and per-share DCF.")
    if beta is None:
        raise ValueError("Beta is unavailable; provide a reviewed beta before running CAPM.")
    shares = float(shares_outstanding)
    beta_value = float(beta)
    market_cap = share_price * shares

    risk_free_rate = fetch_ten_year_treasury_yield()
    assumptions = classify_firm_and_adjust_assumptions(
        market_cap, income_statement, info
    )
    if not assumptions["fcff_supported"]:
        raise ValueError(
            "This FCFF framework is not suitable for financial-services firms."
        )

    overrides = state.get("dcf_overrides") or {}
    market_erp = float(
        overrides.get("market_equity_risk_premium", EQUITY_RISK_PREMIUM)
    )
    company_risk_premium = float(
        overrides.get("company_specific_risk_premium", 0.0)
    )
    high_growth_years = int(
        overrides.get("high_growth_years", assumptions["high_growth_years"])
    )
    terminal_margin = float(
        overrides.get("terminal_margin", assumptions["terminal_margin"])
    )
    if not 2 <= high_growth_years <= 7:
        raise ValueError("Reviewed high-growth horizon must be between 2 and 7 years.")
    if not -0.20 <= terminal_margin <= 0.35:
        raise ValueError("Reviewed terminal margin must be between -20% and 35%.")
    if not 0.03 <= market_erp <= 0.08:
        raise ValueError("Reviewed market ERP must be between 3% and 8%.")
    if not 0 <= company_risk_premium <= 0.0125:
        raise ValueError("Company-specific risk premium must be between 0% and 1.25%.")
    high_growth_rate = float(
        overrides.get("high_growth_rate", assumptions["high_growth_rate"])
    )
    if not 0.0 <= high_growth_rate <= 0.40:
        raise ValueError("Reviewed high-growth rate must be between 0% and 40%.")

    base_revenue, base_ebit = extract_operating_baseline(income_statement)
    total_debt, cash, cash_missing = extract_debt_and_cash(balance_sheet)
    ebit, interest_expense = extract_ebit_and_interest(income_statement)
    outstanding_bonds = state.get("outstanding_bonds") or []

    if total_debt == 0 and not outstanding_bonds:
        debt_results = _no_debt_result(risk_free_rate)
    else:
        debt_results = calculate_cost_of_debt(
            ebit=ebit,
            interest_expense=interest_expense,
            outstanding_bonds=outstanding_bonds or None,
            risk_free_rate=risk_free_rate,
            marginal_tax_rate=MARGINAL_TAX_RATE,
        )

    cost_of_equity = (
        risk_free_rate
        + beta_value * market_erp
        + assumptions["size_premium"]
        + company_risk_premium
    )
    wacc_results = calculate_wacc(
        share_price=share_price,
        shares_outstanding=shares,
        total_debt=total_debt,
        cost_of_equity=cost_of_equity,
        after_tax_cost_of_debt=debt_results["after_tax_cost_of_debt"],
    )
    wacc = wacc_results["wacc"]

    terminal_growth = min(
        TERMINAL_GROWTH_DEFAULT,
        max(0.015, risk_free_rate - 0.015),
    )
    terminal_wacc = max(
        terminal_growth + 0.02,
        min(TERMINAL_WACC_TARGET, max(wacc, 0.065)),
    )

    dcf_results = perform_3stage_dcf_valuation(
        base_revenue=base_revenue,
        base_ebit=base_ebit,
        sales_to_capital=assumptions["sales_to_capital"],
        high_growth_rate=high_growth_rate,
        wacc=wacc,
        terminal_wacc=terminal_wacc,
        shares_outstanding=shares,
        total_debt=total_debt,
        cash_and_equivalents=cash,
        high_growth_years=high_growth_years,
        transition_years=assumptions["transition_years"],
        terminal_growth_rate=terminal_growth,
        terminal_margin=terminal_margin,
        stable_sales_to_capital=assumptions["stable_sales_to_capital"],
        marginal_tax_rate=MARGINAL_TAX_RATE,
    )

    summary = dict(state.get("valuation_summary") or {})
    summary.update(
        {
            "valuation_date_inputs": {
                "share_price": share_price,
                "shares_outstanding": shares,
                "market_cap": market_cap,
                "beta": beta_value,
                "risk_free_rate": risk_free_rate,
                "market_equity_risk_premium": market_erp,
                "company_specific_risk_premium": company_risk_premium,
                "total_debt": total_debt,
                "cash_and_equivalents": cash,
                "cash_field_missing": cash_missing,
                "indicated_dividend": info.get("dividendRate")
                or info.get("trailingAnnualDividendRate"),
                "fifty_two_week_low": info.get("fiftyTwoWeekLow"),
                "fifty_two_week_high": info.get("fiftyTwoWeekHigh"),
            },
            "firm_classification": assumptions,
            "dcf_overrides_applied": overrides or None,
            "applied_dcf_assumptions": {
                "base_revenue": base_revenue,
                "base_ebit": base_ebit,
                "high_growth_years": high_growth_years,
                "transition_years": assumptions["transition_years"],
                "terminal_margin": terminal_margin,
                "high_growth_rate": high_growth_rate,
                "sales_to_capital": assumptions["sales_to_capital"],
                "stable_sales_to_capital": assumptions[
                    "stable_sales_to_capital"
                ],
                "terminal_growth_rate": terminal_growth,
                "terminal_wacc": terminal_wacc,
            },
            "cost_of_equity": cost_of_equity,
            "cost_of_debt": debt_results,
            "wacc": wacc_results,
            "dcf": dcf_results,
        }
    )
    logger.info(
        "%s valuation complete | WACC %.2f%% | intrinsic value %.2f",
        ticker,
        wacc * 100,
        dcf_results["intrinsic_value_per_share"],
    )
    return {
        "discount_rate": wacc,
        "calculated_dcf_value": dcf_results["intrinsic_value_per_share"],
        "valuation_summary": summary,
    }
