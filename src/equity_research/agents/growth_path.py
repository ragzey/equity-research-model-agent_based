"""Growth-path analyst: scale-up horizon, reinvestment fade, and margin path."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from ..graphs.desk import ARCHITECT, GROWTH_PATH, REVIEWER, WRITER, make_message
from ..graphs.state import EquityResearchState
from ..prompts.desk import GROWTH_PATH_SYSTEM, GROWTH_PATH_USER
from ..tools.firm_classifier import (
    MATURE_TERMINAL_MARGIN,
    SCALE_TERMINAL_MARGIN,
    SCALEUP_BASE_CAP,
    classify_firm_and_adjust_assumptions,
    extract_operating_baseline,
    is_financial_services_firm,
)
from ..tools.assumption_menus import is_scale_up_lifecycle
from ..tools.operating_cycle import clip_sales_to_capital, measure_operating_cycle
from ..tools.web_research import format_web_research, web_research_blob
from ..utils.llm_client import LLMCallError, chat_json
from .industry_macro import (
    _filing_blob,
    _ground_block,
    _ground_narrative,
    _view,
)

logger = logging.getLogger("GrowthPathAnalyst")

SCALE_VIEWS = {
    "still_ramping",
    "stretched",
    "in_line",
    "not_applicable",
    "insufficient",
}
HORIZON_VIEWS = {"compress", "base", "extend", "not_applicable", "insufficient"}
REINVEST_PATHS = {"build", "fade", "harvest", "not_applicable", "insufficient"}
MARGIN_PATHS = {"current", "scale", "mature", "not_applicable", "insufficient"}
HARVEST_STC_FLOOR = 1.0


def _na_block(reason: str) -> Dict[str, str]:
    return {"view": "not_applicable", "evidence": reason, "source": "ledger"}


def _block(view: str, evidence: str) -> Dict[str, str]:
    return {"view": view, "evidence": evidence, "source": "ledger"}


def fade_sales_to_capital(observed: float, stable: float) -> float:
    """Midpoint of build-phase STC and the stable ratio, clipped to the desk rails."""
    return clip_sales_to_capital((float(observed) + float(stable)) / 2.0, observed)


def _market_cap_and_info(state: EquityResearchState) -> tuple[Optional[float], Dict[str, Any]]:
    ticker = str(state.get("ticker") or "").strip().upper()
    meta = (state.get("peer_metadata") or {}).get(ticker) or {}
    market = state.get("market_info") or {}
    info = {**market, **meta}
    matrix = state.get("peer_comparison_matrix") or {}
    target = matrix.get("target") or ticker
    market_cap = ((matrix.get("metrics") or {}).get(target) or {}).get("market_cap")
    if market_cap is None:
        market_cap = meta.get("market_cap") or market.get("marketCap")
    try:
        cap = float(market_cap) if market_cap is not None else None
    except (TypeError, ValueError):
        cap = None
    if cap is not None and cap <= 0:
        cap = None
    return cap, info


def classify_from_state(state: EquityResearchState) -> Optional[Dict[str, Any]]:
    income = state.get("income_statement") or {}
    if not income:
        return None
    cap, info = _market_cap_and_info(state)
    if cap is None:
        return None
    try:
        return classify_firm_and_adjust_assumptions(cap, income, info)
    except (TypeError, ValueError):
        return None


def growth_path_metrics(state: EquityResearchState) -> Dict[str, Any]:
    """Frozen Python numbers the LLM may cite. No DCF or TAM."""
    baseline = classify_from_state(state) or {}
    income = state.get("income_statement") or {}
    revenue = ebit = None
    try:
        revenue, ebit = extract_operating_baseline(income)
    except ValueError:
        pass
    observed = ((state.get("operations_packet") or {}).get("metrics") or {}).get(
        "observed_sales_to_capital"
    )
    if observed is None:
        cycle = measure_operating_cycle(
            income,
            state.get("balance_sheet"),
            classifier_sales_to_capital=baseline.get("sales_to_capital"),
        )
        observed = cycle.get("observed_sales_to_capital")
    stable = float(baseline.get("stable_sales_to_capital") or 2.0)
    cagr = baseline.get("historical_revenue_cagr")
    ps = baseline.get("price_to_sales")
    base_rate = float(baseline.get("high_growth_rate") or SCALEUP_BASE_CAP)
    years = int(baseline.get("high_growth_years") or 8)
    implied = None
    if revenue:
        implied = float(revenue) * ((1.0 + base_rate) ** years)
    fade_stc = None
    if observed is not None:
        fade_stc = fade_sales_to_capital(float(observed), stable)
    return {
        "firm_type": baseline.get("firm_type"),
        "price_to_sales": ps,
        "historical_cagr": cagr,
        "base_revenue": revenue,
        "current_operating_margin": (ebit / revenue) if revenue and ebit is not None else None,
        "observed_sales_to_capital": observed,
        "stable_sales_to_capital": stable,
        "fade_sales_to_capital": fade_stc,
        "base_high_growth_rate": base_rate,
        "base_high_growth_years": years,
        "implied_explicit_revenue": implied,
        "scale_terminal_margin": SCALE_TERMINAL_MARGIN,
        "mature_terminal_margin": MATURE_TERMINAL_MARGIN,
    }


def growth_path_ledger(metrics: Dict[str, Any]) -> str:
    lines = []
    firm = metrics.get("firm_type") or "Unclassified"
    lines.append(f"Python firm type is {firm}.")
    cagr = metrics.get("historical_cagr")
    if cagr is not None:
        lines.append(f"Historical revenue CAGR is {float(cagr):.1%}.")
    ps = metrics.get("price_to_sales")
    if ps is not None:
        lines.append(f"Price-to-sales is {float(ps):.1f} times last revenue.")
    observed = metrics.get("observed_sales_to_capital")
    stable = metrics.get("stable_sales_to_capital")
    fade = metrics.get("fade_sales_to_capital")
    if observed is not None:
        lines.append(f"Observed sales-to-capital is {float(observed):.2f}.")
    if stable is not None:
        lines.append(f"Stable sales-to-capital is {float(stable):.2f}.")
    if fade is not None:
        lines.append(
            f"Fade sales-to-capital is {float(fade):.2f}, the midpoint of observed "
            "build-phase intensity and the stable ratio."
        )
    margin = metrics.get("current_operating_margin")
    if margin is not None:
        lines.append(f"Statement EBIT margin is {float(margin):.1%}.")
    lines.append(
        f"Scale terminal margin is {float(metrics.get('scale_terminal_margin') or SCALE_TERMINAL_MARGIN):.0%}."
    )
    lines.append(
        f"Mature terminal margin is {float(metrics.get('mature_terminal_margin') or MATURE_TERMINAL_MARGIN):.0%}."
    )
    implied = metrics.get("implied_explicit_revenue")
    years = metrics.get("base_high_growth_years")
    rate = metrics.get("base_high_growth_rate")
    if implied is not None and years is not None and rate is not None:
        lines.append(
            f"Implied year-{int(years)} revenue at the {float(rate):.0%} clipped base "
            f"rate is {float(implied):.3g}."
        )
    return "\n".join(lines)


def _path_from_ledger(metrics: Dict[str, Any]) -> Dict[str, Dict[str, str]]:
    firm_type = metrics.get("firm_type")
    if not is_scale_up_lifecycle(firm_type):
        reason = (
            f"Firm type {firm_type or 'unclassified'} is not a scale-up; "
            "last year's P&L is the firm the market is pricing."
        )
        na = _na_block(reason)
        return {
            "scale_view": na,
            "horizon_view": dict(na),
            "reinvestment_path": dict(na),
            "margin_path": dict(na),
        }
    cagr = metrics.get("historical_cagr")
    ps = metrics.get("price_to_sales")
    cagr_text = f"{float(cagr):.1%}" if cagr is not None else "n/a"
    ps_text = f"{float(ps):.1f}" if ps is not None else "n/a"
    scale = _block(
        "still_ramping",
        (
            f"Historical revenue CAGR is {cagr_text} and price-to-sales is "
            f"{ps_text} times last revenue, so last year's P&L is not the firm "
            "the market is pricing."
        ),
    )
    years = int(metrics.get("base_high_growth_years") or 8)
    horizon = _block(
        "extend",
        (
            f"Scale-up high-growth is {years} years at the clipped base rate; "
            "extend keeps the explicit window on the scale-up rail rather than "
            "compressing it toward a mature firm."
        ),
    )
    observed = metrics.get("observed_sales_to_capital")
    stable = float(metrics.get("stable_sales_to_capital") or 2.0)
    fade = metrics.get("fade_sales_to_capital")
    if (
        observed is not None
        and fade is not None
        and float(observed) < stable - 0.25
    ):
        reinvest = _block(
            "fade",
            (
                f"Observed sales-to-capital is {float(observed):.2f} versus stable "
                f"{stable:.2f}; fade sales-to-capital is {float(fade):.2f}, so "
                "build-phase intensity cannot be held for the whole explicit period "
                "as revenue scales."
            ),
        )
    elif observed is not None:
        reinvest = _block(
            "build",
            (
                f"Observed sales-to-capital is {float(observed):.2f}, close enough "
                f"to stable {stable:.2f} that the high-growth ratio stays on the "
                "build-phase print."
            ),
        )
    else:
        reinvest = _block(
            "fade",
            "Observed sales-to-capital is missing; scale-ups fade toward the stable ratio.",
        )
    current_m = metrics.get("current_operating_margin")
    scale_m = float(metrics.get("scale_terminal_margin") or SCALE_TERMINAL_MARGIN)
    if current_m is None or float(current_m) < scale_m:
        margin = _block(
            "scale",
            (
                f"Statement EBIT margin is "
                f"{f'{float(current_m):.1%}' if current_m is not None else 'n/a'}; "
                f"scale terminal margin is {scale_m:.0%}, a normal operating firm "
                "rather than last year's print."
            ),
        )
    else:
        margin = _block(
            "current",
            (
                f"Statement EBIT margin is {float(current_m):.1%}, already at or "
                f"above scale terminal margin {scale_m:.0%}."
            ),
        )
    return {
        "scale_view": scale,
        "horizon_view": horizon,
        "reinvestment_path": reinvest,
        "margin_path": margin,
    }


def _not_applicable_packet(reason: str, metrics: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    na = _na_block(reason)
    return {
        "applicable": False,
        "metrics": metrics or {},
        "scale_view": dict(na),
        "horizon_view": dict(na),
        "reinvestment_path": dict(na),
        "margin_path": dict(na),
        "narrative": reason,
    }


def normalize_growth_path_packet(
    payload: Optional[Dict[str, Any]],
    *,
    ledger_text: str = "",
    allowed_tickers: Optional[List[str]] = None,
) -> Dict[str, Any]:
    raw = payload if isinstance(payload, dict) else {}
    return {
        "scale_view": _ground_block(
            raw.get("scale_view") or {},
            view_key="view",
            allowed=SCALE_VIEWS,
            ledger_text=ledger_text,
        ),
        "horizon_view": _ground_block(
            raw.get("horizon_view") or {},
            view_key="view",
            allowed=HORIZON_VIEWS,
            ledger_text=ledger_text,
        ),
        "reinvestment_path": _ground_block(
            raw.get("reinvestment_path") or {},
            view_key="view",
            allowed=REINVEST_PATHS,
            ledger_text=ledger_text,
        ),
        "margin_path": _ground_block(
            raw.get("margin_path") or {},
            view_key="view",
            allowed=MARGIN_PATHS,
            ledger_text=ledger_text,
        ),
        "narrative": _ground_narrative(
            raw.get("narrative"), ledger_text, allowed_tickers
        ),
    }


def overlay_ledger_growth_path(
    packet: Dict[str, Any],
    metrics: Dict[str, Any],
) -> Dict[str, Any]:
    """Fill or correct views from the Python ledger. Harvest cannot stick at STC 0.60."""
    updated = dict(packet or {})
    filled = _path_from_ledger(metrics)
    applicable = is_scale_up_lifecycle(metrics.get("firm_type"))
    updated["applicable"] = applicable
    updated["metrics"] = metrics
    for key in ("scale_view", "horizon_view", "reinvestment_path", "margin_path"):
        updated[key] = filled[key]
    observed = metrics.get("observed_sales_to_capital")
    if (
        applicable
        and _view(updated.get("reinvestment_path")) == "harvest"
        and observed is not None
        and float(observed) < HARVEST_STC_FLOOR
    ):
        fade_block = filled["reinvestment_path"]
        if _view(fade_block) != "fade":
            fade = metrics.get("fade_sales_to_capital")
            fade_block = _block(
                "fade",
                (
                    f"Observed sales-to-capital is {float(observed):.2f}, still at "
                    "build-phase intensity, so harvest is not available; fade "
                    f"sales-to-capital is {float(fade):.2f}"
                    if fade is not None
                    else f"Observed sales-to-capital is {float(observed):.2f}; harvest is not available"
                ),
            )
        updated["reinvestment_path"] = fade_block
    if not str(updated.get("narrative") or "").strip():
        bits = []
        for key in ("scale_view", "horizon_view", "reinvestment_path", "margin_path"):
            text = str((updated.get(key) or {}).get("evidence") or "").strip()
            if text:
                bits.append(text)
        updated["narrative"] = " ".join(bits)
    return updated


def growth_path_node(state: EquityResearchState) -> Dict[str, Any]:
    """Structured scale-up packet used to unlock growth, years, STC fade, and margin."""
    ticker = str(state.get("ticker") or "").strip().upper()
    market = state.get("market_info") or {}
    if state.get("is_financial") or is_financial_services_firm(market):
        packet = _not_applicable_packet(
            "Growth-path analysis is out of scope for financial firms on this FCFF desk."
        )
        body = f"{ticker} growth-path skipped: financial firm out of FCFF scope."
        logger.info(body)
        return {
            "growth_path_packet": packet,
            "agent_messages": [
                make_message(GROWTH_PATH, WRITER, "growth_path_narrative", packet["narrative"], {}),
            ],
        }

    metrics = growth_path_metrics(state)
    ledger = growth_path_ledger(metrics)
    if not is_scale_up_lifecycle(metrics.get("firm_type")):
        packet = overlay_ledger_growth_path({}, metrics)
        body = (
            f"{ticker} growth-path not applicable "
            f"({metrics.get('firm_type') or 'unclassified'})."
        )
        logger.info(body)
        return {
            "growth_path_packet": packet,
            "agent_messages": [
                make_message(GROWTH_PATH, ARCHITECT, "growth_path_packet", body, {"applicable": False}),
                make_message(GROWTH_PATH, REVIEWER, "growth_path_packet", body, {"applicable": False}),
                make_message(GROWTH_PATH, WRITER, "growth_path_narrative", packet.get("narrative") or body, {}),
            ],
        }

    filing = _filing_blob(state)
    excerpts = "\n".join(
        str(item.get("excerpt") or "")
        for item in (state.get("qualitative_evidence") or [])
        if isinstance(item, dict)
    )
    web_blob = web_research_blob(state)
    web_prompt = format_web_research(state.get("web_research") or [])
    industry = state.get("industry_macro_packet") or {}
    operations = state.get("operations_packet") or {}
    products = state.get("company_products_packet") or {}
    ledger_text = "\n".join(
        part
        for part in (
            ledger,
            filing,
            excerpts,
            web_blob,
            json.dumps(metrics, default=str),
            json.dumps(industry, default=str),
            json.dumps(operations, default=str),
            json.dumps(products, default=str),
        )
        if part
    )
    allowed = {ticker}
    for symbol in state.get("competitor_tickers") or []:
        if str(symbol).strip():
            allowed.add(str(symbol).strip().upper())
    payload = chat_json(
        [
            {"role": "system", "content": GROWTH_PATH_SYSTEM},
            {
                "role": "user",
                "content": GROWTH_PATH_USER.format(
                    ticker=ticker,
                    metrics_json=json.dumps(metrics, indent=2, default=str)[:8000],
                    metric_ledger=ledger,
                    industry_json=json.dumps(industry, indent=2, default=str)[:4000],
                    operations_json=json.dumps(operations, indent=2, default=str)[:4000],
                    products_json=json.dumps(products, indent=2, default=str)[:4000],
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
        raise LLMCallError("Growth-path analyst did not return a JSON object.")
    packet = normalize_growth_path_packet(
        payload, ledger_text=ledger_text, allowed_tickers=list(allowed)
    )
    packet = overlay_ledger_growth_path(packet, metrics)
    body = (
        f"{ticker} growth-path: scale {packet['scale_view']['view']}, "
        f"horizon {packet['horizon_view']['view']}, "
        f"reinvestment {packet['reinvestment_path']['view']}, "
        f"margin {packet['margin_path']['view']}."
    )
    logger.info(body)
    return {
        "growth_path_packet": packet,
        "agent_messages": [
            make_message(
                GROWTH_PATH,
                ARCHITECT,
                "growth_path_packet",
                body,
                {
                    "scale_view": packet["scale_view"]["view"],
                    "horizon_view": packet["horizon_view"]["view"],
                    "reinvestment_path": packet["reinvestment_path"]["view"],
                    "margin_path": packet["margin_path"]["view"],
                },
            ),
            make_message(
                GROWTH_PATH,
                REVIEWER,
                "growth_path_packet",
                body,
                {
                    "scale_view": packet["scale_view"]["view"],
                    "horizon_view": packet["horizon_view"]["view"],
                    "reinvestment_path": packet["reinvestment_path"]["view"],
                    "margin_path": packet["margin_path"]["view"],
                },
            ),
            make_message(
                GROWTH_PATH,
                WRITER,
                "growth_path_narrative",
                packet.get("narrative") or body,
                {},
            ),
        ],
    }
