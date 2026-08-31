"""Independent assumption auditor: Python overlay plus optional LLM revert."""

from __future__ import annotations

import unittest
from datetime import datetime
from unittest.mock import patch

from equity_research.agents.assumption_auditor import assumption_auditor_node
from equity_research.graphs.defaults import initial_state
from equity_research.graphs.graph import build_research_graph
from equity_research.tools.assumption_audit import (
    effective_label,
    python_assumption_reverts,
)


def _inline_packet():
    return {
        "category_growth": {
            "view": "in_line",
            "evidence": "Historical revenue CAGR is 6.5%.",
            "source": "ledger",
        },
        "cycle": {"view": "mid", "evidence": "Peer growth median is 5.0 percent."},
        "demand_inflection": {"direction": "none", "evidence": "No demand break."},
    }


def _overrides(**labels):
    choices = {
        "high_growth_rate": "base",
        "high_growth_years": "base",
        "terminal_growth_rate": "base",
        "terminal_margin": "baseline",
        "company_specific_risk_premium": "none",
        "sales_to_capital": "base",
    }
    choices.update(labels)
    return {
        "architect_choices": choices,
        "decisions": [
            {"key": key, "action": "accept", "reason": "reviewer kept it"}
            for key in choices
        ],
        "high_growth_rate": 0.02 if choices["high_growth_rate"] == "low" else 0.065,
        "high_growth_years": 2 if choices["high_growth_years"] == "compress" else 3,
        "terminal_growth_rate": 0.015 if choices["terminal_growth_rate"] == "low" else 0.03,
        "terminal_margin": 0.12,
        "company_specific_risk_premium": 0.0,
        "sales_to_capital": 1.38 if choices["sales_to_capital"] == "heavy" else 1.85,
        "desk_mode": "llm",
    }


class PolicyTests(unittest.TestCase):
    def test_reviewer_reject_counts_as_baseline(self):
        overrides = _overrides(high_growth_rate="low")
        overrides["decisions"] = [
            {"key": "high_growth_rate", "action": "reject", "reason": "already reverted"}
        ]
        self.assertEqual(effective_label(overrides, "high_growth_rate"), "base")
        self.assertEqual(
            python_assumption_reverts(
                overrides,
                firm_type="Mature Large-Cap",
                industry_packet=_inline_packet(),
            ),
            [],
        )

    def test_scale_up_cannot_keep_low_growth(self):
        reverts = python_assumption_reverts(
            _overrides(high_growth_rate="low"),
            firm_type="Scale-up High-Growth",
            industry_packet=_inline_packet(),
        )
        self.assertEqual([row["key"] for row in reverts], ["high_growth_rate"])

    def test_mature_in_line_cannot_keep_a_recession_stack(self):
        reverts = python_assumption_reverts(
            _overrides(
                high_growth_rate="low",
                high_growth_years="compress",
                terminal_growth_rate="low",
            ),
            firm_type="Mature Large-Cap",
            industry_packet=_inline_packet(),
        )
        keys = {row["key"] for row in reverts}
        self.assertIn("high_growth_rate", keys)
        self.assertIn("high_growth_years", keys)
        self.assertIn("terminal_growth_rate", keys)

    def test_stable_ccc_cannot_keep_heavy_stc(self):
        reverts = python_assumption_reverts(
            _overrides(sales_to_capital="heavy"),
            firm_type="Mature Large-Cap",
            industry_packet=_inline_packet(),
            operations_packet={
                "working_capital": {
                    "view": "absorbing",
                    "evidence": "Python working-capital view is absorbing.",
                },
                "cash_conversion": {
                    "view": "stable",
                    "evidence": "Python cash-conversion view is stable.",
                },
                "reinvestment": {
                    "view": "typical",
                    "evidence": "Python reinvestment view is typical.",
                },
            },
        )
        self.assertEqual([row["key"] for row in reverts], ["sales_to_capital"])


class NodeTests(unittest.TestCase):
    def _state(self, **extra):
        state = initial_state("TJX", "2026")
        state.update(
            {
                "income_statement": {
                    datetime(2024, 12, 31): {
                        "Total Revenue": 50_000_000_000.0,
                        "Operating Income": 5_000_000_000.0,
                    },
                    datetime(2025, 12, 31): {
                        "Total Revenue": 53_000_000_000.0,
                        "Operating Income": 5_300_000_000.0,
                    },
                },
                "peer_comparison_matrix": {
                    "target": "TJX",
                    "competitors": ["ROST"],
                    "metrics": {
                        "TJX": {"market_cap": 150_000_000_000},
                        "ROST": {"operating_margin_pct": 12.0},
                    },
                },
                "peer_metadata": {
                    "TJX": {
                        "market_cap": 150_000_000_000,
                        "sector": "Consumer Cyclical",
                        "industry": "Apparel Retail",
                    }
                },
                "industry_macro_packet": _inline_packet(),
                "dcf_overrides": _overrides(
                    high_growth_rate="low",
                    high_growth_years="compress",
                    terminal_growth_rate="low",
                ),
            }
        )
        state.update(extra)
        return state

    @patch("equity_research.agents.assumption_auditor.chat_json", return_value=None)
    def test_python_reverts_even_when_llm_is_silent(self, _chat):
        result = assumption_auditor_node(self._state())
        packet = result["assumption_audit"]
        self.assertTrue(packet["applicable"])
        self.assertIn("high_growth_rate", packet["reverted"])
        self.assertEqual(
            result["dcf_overrides"]["architect_choices"]["high_growth_rate"], "base"
        )
        self.assertGreater(result["dcf_overrides"]["high_growth_rate"], 0.05)
        self.assertLess(result["dcf_overrides"]["high_growth_rate"], 0.07)

    def test_financials_skip(self):
        state = initial_state("JPM", "2026")
        state["is_financial"] = True
        result = assumption_auditor_node(state)
        self.assertFalse(result["assumption_audit"]["applicable"])

    def test_graph_runs_assumption_auditor_before_quant(self):
        graph = build_research_graph()
        edges = {(edge.source, edge.target) for edge in graph.get_graph().edges}
        self.assertIn("assumption_auditor", graph.nodes)
        self.assertIn(("valuation_assumption_reviewer", "assumption_auditor"), edges)
        self.assertIn(("assumption_auditor", "quant_analyst"), edges)
        self.assertNotIn(("valuation_assumption_reviewer", "quant_analyst"), edges)


if __name__ == "__main__":
    unittest.main()
