"""Assumption architect: labeled menu picks, never free-typed DCF numbers."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict

from ..agents.quant import fetch_ten_year_treasury_yield
from ..graphs.desk import ARCHITECT, QUANT, REVIEWER, WRITER, make_message
from ..graphs.state import EquityResearchState
from ..prompts.desk import ARCHITECT_SYSTEM, ARCHITECT_USER
from ..tools.assumption_menus import (
    apply_architect_choices,
    build_assumption_bundle,
    build_choice_menus,
)
from ..tools.operating_cycle import operating_cycle_ledger
from ..utils.llm_client import LLMCallError, chat_json

logger = logging.getLogger("AssumptionArchitect")


def assumption_architect_node(state: EquityResearchState) -> Dict[str, Any]:
    """Choose bounded DCF candidates from firm + industry/macro + trailing baseline."""
    ticker = str(state.get("ticker") or "").strip().upper()
    risk_free_rate = fetch_ten_year_treasury_yield()
    packet = state.get("industry_macro_packet") or {}
    operations = state.get("operations_packet") or {}
    bundle = build_assumption_bundle(state, risk_free_rate=risk_free_rate)
    menus = build_choice_menus(
        bundle,
        packet,
        risk_free_rate=risk_free_rate,
        operations_packet=operations,
    )
    compact_menus = {
        "menus": {
            key: menus[key]
            for key in (
                "high_growth_rate",
                "high_growth_years",
                "terminal_growth_rate",
                "terminal_margin",
                "company_specific_risk_premium",
                "sales_to_capital",
            )
            if key in menus
        },
        "allowed": menus.get("allowed"),
    }
    payload = chat_json(
        [
            {"role": "system", "content": ARCHITECT_SYSTEM},
            {
                "role": "user",
                "content": ARCHITECT_USER.format(
                    ticker=ticker,
                    baseline_json=json.dumps(
                        {
                            "firm_type": bundle["baseline"].get("firm_type"),
                            "historical_revenue_cagr": bundle["baseline"].get(
                                "historical_revenue_cagr"
                            ),
                            "high_growth_rate": bundle["baseline"].get("high_growth_rate"),
                            "high_growth_rate_bounds": bundle["baseline"].get(
                                "high_growth_rate_bounds"
                            ),
                            "high_growth_years": bundle["baseline"].get("high_growth_years"),
                            "terminal_margin": bundle["baseline"].get("terminal_margin"),
                            "sales_to_capital": bundle["baseline"].get("sales_to_capital"),
                            "operating_cycle": {
                                "ccc_days": (bundle["baseline"].get("operating_cycle") or {}).get("ccc_days"),
                                "nwc_to_sales": (bundle["baseline"].get("operating_cycle") or {}).get("nwc_to_sales"),
                                "implied_sales_to_capital": (bundle["baseline"].get("operating_cycle") or {}).get("implied_sales_to_capital"),
                            },
                        },
                        indent=2,
                        default=str,
                    ),
                    proposed_json=json.dumps(
                        {
                            key: bundle["proposed"].get(key)
                            for key in (
                                "high_growth_rate",
                                "high_growth_years",
                                "terminal_margin",
                                "terminal_growth_rate",
                                "company_specific_risk_premium",
                                "sales_to_capital",
                                "stable_sales_to_capital",
                            )
                        },
                        indent=2,
                        default=str,
                    ),
                    packet_json=json.dumps(packet, indent=2, default=str)[:6000],
                    operations_json=json.dumps(operations, indent=2, default=str)[:6000],
                    menus_json=json.dumps(compact_menus, indent=2, default=str),
                ),
            },
        ],
        timeout=90,
        required=True,
    )
    if not payload:
        raise LLMCallError("Assumption architect did not return a JSON object.")
    excerpts = "\n".join(
        str(item.get("excerpt") or "")
        for item in (state.get("qualitative_evidence") or [])
        if isinstance(item, dict)
    )
    choice_ledger = "\n".join(
        part
        for part in (
            json.dumps(packet, default=str),
            json.dumps(operations, default=str),
            json.dumps(compact_menus, default=str),
            operating_cycle_ledger(bundle["baseline"].get("operating_cycle") or {}),
            excerpts,
        )
        if part
    )
    proposed = apply_architect_choices(
        bundle,
        menus,
        payload,
        reasons=payload.get("reasons") if isinstance(payload, dict) else None,
        ledger_text=choice_ledger,
    )
    proposed["industry_macro_views"] = {
        "category_growth": (packet.get("category_growth") or {}).get("view"),
        "cycle": (packet.get("cycle") or {}).get("view"),
        "demand_inflection": (packet.get("demand_inflection") or {}).get("direction"),
        "rates_view": (packet.get("macro") or {}).get("rates_view"),
    }
    proposed["operations_views"] = {
        "cash_conversion": (operations.get("cash_conversion") or {}).get("view"),
        "working_capital": (operations.get("working_capital") or {}).get("view"),
        "reinvestment": (operations.get("reinvestment") or {}).get("view"),
    }
    choices = proposed.get("architect_choices") or {}
    body = (
        f"{ticker} architect labels: growth={choices.get('high_growth_rate')}, "
        f"years={choices.get('high_growth_years')}, "
        f"g={choices.get('terminal_growth_rate')}, "
        f"stc={choices.get('sales_to_capital')}."
    )
    messages = [
        make_message(
            ARCHITECT,
            REVIEWER,
            "architect_candidates",
            body,
            {
                "choices": choices,
                "allowed": proposed.get("architect_allowed"),
            },
        ),
        make_message(
            ARCHITECT,
            QUANT,
            "architect_candidates",
            "Reviewer must accept or reject these bounded candidates before Quant runs.",
            {"choices": choices},
        ),
        make_message(ARCHITECT, WRITER, "architect_candidates", body, {"choices": choices}),
    ]
    logger.info(
        "Architect candidates for %s: g=%.1f%% years=%s terminal g=%.1f%%",
        ticker,
        float(proposed["high_growth_rate"]) * 100,
        proposed["high_growth_years"],
        float(proposed["terminal_growth_rate"]) * 100,
    )
    return {"dcf_overrides": proposed, "agent_messages": messages}
