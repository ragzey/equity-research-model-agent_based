"""Operations analyst: cash conversion, working capital, and reinvestment intensity."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from ..graphs.desk import ARCHITECT, OPERATIONS, REVIEWER, WRITER, make_message
from ..graphs.state import EquityResearchState
from ..prompts.desk import OPERATIONS_SYSTEM, OPERATIONS_USER
from ..tools.firm_classifier import classify_firm_and_adjust_assumptions
from ..tools.operating_cycle import (
    measure_operating_cycle,
    operating_cycle_ledger,
)
from ..utils.llm_client import LLMCallError, chat_json
from .industry_macro import (
    _clip_view,
    _evidence,
    _filing_blob,
    _ground_block,
    _ground_narrative,
)

logger = logging.getLogger("OperationsAnalyst")

CCC_VIEWS = {"lengthening", "stable", "shortening", "insufficient"}
WC_VIEWS = {"absorbing", "stable", "releasing", "insufficient"}
REINVEST_VIEWS = {"heavy", "typical", "asset_light", "insufficient"}


def _python_evidence(metrics: Dict[str, Any], view_key: str) -> str:
    ledger = operating_cycle_ledger(metrics)
    needle = {
        "cash_conversion": "Python cash-conversion view",
        "working_capital": "Python working-capital view",
        "reinvestment": "Python reinvestment view",
    }.get(view_key, "")
    for line in ledger.splitlines():
        if needle and needle.lower() in line.lower():
            return line
    return ledger.splitlines()[0] if ledger else ""


def _reconcile(
    block: Any,
    *,
    metric_view: str,
    allowed: set[str],
    ledger_text: str,
    python_evidence: str,
) -> Dict[str, str]:
    raw = block if isinstance(block, dict) else {}
    grounded = _ground_block(
        raw, view_key="view", allowed=allowed, ledger_text=ledger_text
    )
    metric = _clip_view(metric_view, allowed)
    if metric != "insufficient":
        return {"view": metric, "evidence": _evidence(python_evidence)}
    return {"view": "insufficient", "evidence": grounded.get("evidence") or ""}


def normalize_operations_packet(
    payload: Optional[Dict[str, Any]],
    metrics: Dict[str, Any],
    *,
    ledger_text: str = "",
    allowed_tickers: Optional[List[str]] = None,
) -> Dict[str, Any]:
    raw = payload if isinstance(payload, dict) else {}
    return {
        "metrics": {
            key: metrics.get(key)
            for key in (
                "ccc_days",
                "dso_days",
                "dio_days",
                "dpo_days",
                "nwc",
                "nwc_to_sales",
                "delta_nwc",
                "implied_sales_to_capital",
                "observed_sales_to_capital",
                "capital_released_on_growth",
                "source",
            )
        },
        "cash_conversion": _reconcile(
            raw.get("cash_conversion"),
            metric_view=str(metrics.get("cash_conversion_view") or "insufficient"),
            allowed=CCC_VIEWS,
            ledger_text=ledger_text,
            python_evidence=_python_evidence(metrics, "cash_conversion"),
        ),
        "working_capital": _reconcile(
            raw.get("working_capital"),
            metric_view=str(metrics.get("working_capital_view") or "insufficient"),
            allowed=WC_VIEWS,
            ledger_text=ledger_text,
            python_evidence=_python_evidence(metrics, "working_capital"),
        ),
        "reinvestment": _reconcile(
            raw.get("reinvestment"),
            metric_view=str(metrics.get("reinvestment_view") or "insufficient"),
            allowed=REINVEST_VIEWS,
            ledger_text=ledger_text,
            python_evidence=_python_evidence(metrics, "reinvestment"),
        ),
        "narrative": _ground_narrative(
            raw.get("narrative"), ledger_text, allowed_tickers
        ),
    }


def _classifier_stc(state: EquityResearchState) -> Optional[float]:
    ticker = str(state.get("ticker") or "").strip().upper()
    income = state.get("income_statement") or {}
    if not income:
        return None
    meta = (state.get("peer_metadata") or {}).get(ticker) or {}
    market = state.get("market_info") or {}
    info = {**market, **meta}
    matrix = state.get("peer_comparison_matrix") or {}
    target = matrix.get("target") or ticker
    market_cap = ((matrix.get("metrics") or {}).get(target) or {}).get("market_cap")
    if market_cap is None:
        market_cap = meta.get("market_cap") or market.get("marketCap")
    if market_cap is None:
        return None
    try:
        baseline = classify_firm_and_adjust_assumptions(
            float(market_cap), income, info
        )
    except (TypeError, ValueError):
        return None
    return float(baseline.get("sales_to_capital") or 0) or None


def _insufficient_packet(reason: str) -> Dict[str, Any]:
    metrics = {
        key: None
        for key in (
            "ccc_days",
            "dso_days",
            "dio_days",
            "dpo_days",
            "nwc",
            "nwc_to_sales",
            "delta_nwc",
            "implied_sales_to_capital",
            "observed_sales_to_capital",
        )
    }
    metrics["capital_released_on_growth"] = False
    metrics["source"] = reason
    block = {"view": "insufficient", "evidence": reason}
    return {
        "metrics": metrics,
        "cash_conversion": dict(block),
        "working_capital": dict(block),
        "reinvestment": dict(block),
        "narrative": reason,
    }


def operations_node(state: EquityResearchState) -> Dict[str, Any]:
    """Write a metric-first operations packet used by the architect."""
    ticker = str(state.get("ticker") or "").strip().upper()
    if state.get("is_financial") or state.get("valuation_method") == "unsupported_financial":
        reason = (
            "Working-capital CCC and sales-to-capital are not defined for this "
            "FCFF desk on banks, insurers, brokers, or other financial firms."
        )
        packet = _insufficient_packet(reason)
        body = f"{ticker} operations skipped: financial firm out of FCFF scope."
        logger.info(body)
        return {
            "operations_packet": packet,
            "agent_messages": [
                make_message(OPERATIONS, WRITER, "operations_narrative", reason, {}),
            ],
        }
    classifier_stc = _classifier_stc(state)
    metrics = measure_operating_cycle(
        state.get("income_statement"),
        state.get("balance_sheet"),
        classifier_sales_to_capital=classifier_stc,
    )
    metric_ledger = operating_cycle_ledger(metrics)
    filing = _filing_blob(state)
    excerpts = "\n".join(
        str(item.get("excerpt") or "")
        for item in (state.get("qualitative_evidence") or [])
        if isinstance(item, dict)
    )
    ledger_text = "\n".join(part for part in (metric_ledger, filing, excerpts) if part)
    payload = chat_json(
        [
            {"role": "system", "content": OPERATIONS_SYSTEM},
            {
                "role": "user",
                "content": OPERATIONS_USER.format(
                    ticker=ticker,
                    metrics_json=json.dumps(metrics, indent=2, default=str)[:6000],
                    metric_ledger=metric_ledger or "n/a",
                    qualitative=(state.get("qualitative_analysis_summary") or "")[:4000],
                    filing=filing[:8000],
                ),
            },
        ],
        timeout=90,
        required=True,
    )
    if not payload:
        raise LLMCallError("Operations analyst did not return a JSON object.")
    packet = normalize_operations_packet(
        payload,
        metrics,
        ledger_text=ledger_text,
        allowed_tickers=[ticker],
    )
    narrative = (
        packet.get("narrative")
        or "Operations evidence was limited to Python working-capital arithmetic."
    )
    body = (
        f"{ticker} operations packet: CCC {packet['cash_conversion']['view']}, "
        f"WC {packet['working_capital']['view']}, reinvestment "
        f"{packet['reinvestment']['view']}."
    )
    messages = [
        make_message(
            OPERATIONS,
            ARCHITECT,
            "operations_packet",
            body,
            {
                "views": {
                    key: packet[key]
                    for key in ("cash_conversion", "working_capital", "reinvestment")
                }
            },
        ),
        make_message(
            OPERATIONS,
            REVIEWER,
            "operations_packet",
            body,
            {"metrics": packet.get("metrics")},
        ),
        make_message(
            OPERATIONS, WRITER, "operations_narrative", narrative, {"drivers": body}
        ),
    ]
    logger.info(body)
    return {
        "operations_packet": packet,
        "agent_messages": messages,
    }
