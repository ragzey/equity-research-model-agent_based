"""Growth-path agent: scale-up horizon, STC fade, and stretch growth menu."""

from __future__ import annotations

import unittest
from datetime import datetime
from unittest.mock import patch

from equity_research.agents.growth_path import (
    growth_path_node,
    overlay_ledger_growth_path,
)
from equity_research.graphs.defaults import initial_state
from equity_research.graphs.graph import build_research_graph
from equity_research.tools.assumption_menus import (
    allowed_stc_choices,
    apply_architect_choices,
    build_assumption_bundle,
    build_choice_menus,
)


def _scale_up_metrics(**overrides):
    metrics = {
        "firm_type": "Scale-up High-Growth",
        "price_to_sales": 105.0,
        "historical_cagr": 2.397,
        "base_revenue": 529_800_000.0,
        "current_operating_margin": 0.142,
        "observed_sales_to_capital": 0.60,
        "stable_sales_to_capital": 2.0,
        "fade_sales_to_capital": 1.30,
        "base_high_growth_rate": 0.50,
        "base_high_growth_years": 8,
        "implied_explicit_revenue": 529_800_000.0 * (1.5 ** 8),
        "scale_terminal_margin": 0.18,
        "mature_terminal_margin": 0.22,
    }
    metrics.update(overrides)
    return metrics


def _scale_up_state():
    state = initial_state("NBIS", "2026")
    state.update(
        {
            "income_statement": {
                datetime(2023, 12, 31): {
                    "Total Revenue": 50_000_000.0,
                    "Operating Income": 5_000_000.0,
                },
                datetime(2024, 12, 31): {
                    "Total Revenue": 200_000_000.0,
                    "Operating Income": 20_000_000.0,
                },
                datetime(2025, 12, 31): {
                    "Total Revenue": 530_000_000.0,
                    "Operating Income": 75_000_000.0,
                },
            },
            "peer_comparison_matrix": {
                "target": "NBIS",
                "metrics": {"NBIS": {"market_cap": 55_000_000_000}},
            },
            "peer_metadata": {
                "NBIS": {
                    "market_cap": 55_000_000_000,
                    "sector": "Communication Services",
                    "industry": "Internet Content",
                }
            },
            "operations_packet": {
                "metrics": {"observed_sales_to_capital": 0.60},
                "reinvestment": {
                    "view": "heavy",
                    "evidence": "Python reinvestment view is heavy.",
                },
            },
            "growth_path_packet": overlay_ledger_growth_path({}, _scale_up_metrics()),
        }
    )
    return state


class GrowthPathOverlayTests(unittest.TestCase):
    def test_scale_up_ledger_sets_ramping_extend_fade_and_scale_margin(self):
        packet = overlay_ledger_growth_path({}, _scale_up_metrics())
        self.assertTrue(packet["applicable"])
        self.assertEqual(packet["scale_view"]["view"], "still_ramping")
        self.assertEqual(packet["horizon_view"]["view"], "extend")
        self.assertEqual(packet["reinvestment_path"]["view"], "fade")
        self.assertEqual(packet["margin_path"]["view"], "scale")
        self.assertIn("1.30", packet["reinvestment_path"]["evidence"])

    def test_mature_firm_is_not_applicable(self):
        packet = overlay_ledger_growth_path(
            {},
            {
                "firm_type": "Mature Large-Cap",
                "historical_cagr": 0.03,
                "price_to_sales": 8.0,
            },
        )
        self.assertFalse(packet["applicable"])
        self.assertEqual(packet["scale_view"]["view"], "not_applicable")
        self.assertEqual(packet["reinvestment_path"]["view"], "not_applicable")

    def test_harvest_cannot_stick_when_observed_stc_is_at_the_floor(self):
        packet = overlay_ledger_growth_path(
            {
                "reinvestment_path": {
                    "view": "harvest",
                    "evidence": "Observed sales-to-capital is 0.60.",
                }
            },
            _scale_up_metrics(),
        )
        self.assertEqual(packet["reinvestment_path"]["view"], "fade")


class GrowthPathMenuTests(unittest.TestCase):
    def test_fade_and_stretch_growth_unlock_on_scale_up_packet(self):
        state = _scale_up_state()
        bundle = build_assumption_bundle(state, risk_free_rate=0.0476)
        menus = build_choice_menus(
            bundle,
            {},
            risk_free_rate=0.0476,
            growth_path_packet=state["growth_path_packet"],
        )
        self.assertIn("high", menus["allowed"]["high_growth_rate"])
        self.assertAlmostEqual(menus["high_growth_rate"]["high"], 0.80)
        self.assertAlmostEqual(menus["high_growth_rate"]["base"], 0.50)
        self.assertIn("extend", menus["allowed"]["high_growth_years"])
        self.assertIn("fade", menus["allowed"]["sales_to_capital"])
        self.assertAlmostEqual(menus["sales_to_capital"]["fade"], 1.30, places=2)
        self.assertIn("proposed", menus["allowed"]["terminal_margin"])
        self.assertAlmostEqual(menus["terminal_margin"]["proposed"], 0.18)

    def test_architect_can_pick_fade_and_stretch_with_ledger_reason(self):
        state = _scale_up_state()
        bundle = build_assumption_bundle(state, risk_free_rate=0.0476)
        packet = state["growth_path_packet"]
        menus = build_choice_menus(
            bundle,
            {},
            risk_free_rate=0.0476,
            growth_path_packet=packet,
        )
        ledger = " ".join(
            str((packet.get(key) or {}).get("evidence") or "")
            for key in (
                "scale_view",
                "horizon_view",
                "reinvestment_path",
                "margin_path",
            )
        )
        proposed = apply_architect_choices(
            bundle,
            menus,
            {
                "high_growth_rate": "high",
                "high_growth_years": "extend",
                "sales_to_capital": "fade",
                "terminal_margin": "proposed",
            },
            reasons={
                "high_growth_rate": packet["scale_view"]["evidence"],
                "high_growth_years": packet["horizon_view"]["evidence"],
                "sales_to_capital": packet["reinvestment_path"]["evidence"],
                "terminal_margin": packet["margin_path"]["evidence"],
            },
            ledger_text=ledger,
        )
        self.assertEqual(proposed["architect_choices"]["high_growth_rate"], "high")
        self.assertAlmostEqual(proposed["high_growth_rate"], 0.80)
        self.assertEqual(proposed["architect_choices"]["high_growth_years"], "extend")
        self.assertEqual(proposed["high_growth_years"], 10)
        self.assertEqual(proposed["architect_choices"]["sales_to_capital"], "fade")
        self.assertAlmostEqual(proposed["sales_to_capital"], 1.30, places=2)
        self.assertAlmostEqual(proposed["terminal_margin"], 0.18)

    def test_fade_not_allowed_without_growth_path(self):
        self.assertNotIn("fade", allowed_stc_choices({}))


class GrowthPathNodeTests(unittest.TestCase):
    @patch("equity_research.agents.growth_path.chat_json")
    def test_mature_name_does_not_call_the_llm(self, mock_chat):
        state = initial_state("TPR", "2026")
        state.update(
            {
                "income_statement": {
                    datetime(2024, 12, 31): {
                        "Total Revenue": 8_000_000_000.0,
                        "Operating Income": 1_440_000_000.0,
                    },
                    datetime(2025, 12, 31): {
                        "Total Revenue": 8_240_000_000.0,
                        "Operating Income": 1_480_000_000.0,
                    },
                },
                "peer_metadata": {
                    "TPR": {
                        "market_cap": 20_000_000_000,
                        "sector": "Consumer Cyclical",
                    }
                },
            }
        )
        result = growth_path_node(state)
        mock_chat.assert_not_called()
        self.assertFalse(result["growth_path_packet"]["applicable"])
        self.assertEqual(
            result["growth_path_packet"]["scale_view"]["view"], "not_applicable"
        )

    @patch("equity_research.agents.growth_path.chat_json")
    def test_scale_up_node_keeps_python_path_labels(self, mock_chat):
        mock_chat.return_value = {
            "scale_view": {"view": "stretched", "evidence": "made up TAM of $500bn"},
            "horizon_view": {"view": "compress", "evidence": "invented"},
            "reinvestment_path": {"view": "harvest", "evidence": "invented"},
            "margin_path": {"view": "current", "evidence": "invented"},
            "narrative": "made up TAM of $500bn",
        }
        result = growth_path_node(_scale_up_state())
        packet = result["growth_path_packet"]
        mock_chat.assert_called_once()
        self.assertEqual(packet["scale_view"]["view"], "still_ramping")
        self.assertEqual(packet["horizon_view"]["view"], "extend")
        self.assertEqual(packet["reinvestment_path"]["view"], "fade")
        self.assertEqual(packet["margin_path"]["view"], "scale")
        self.assertNotIn("500bn", packet.get("narrative") or "")

    def test_graph_runs_growth_path_before_the_router(self):
        graph = build_research_graph()
        edges = {(edge.source, edge.target) for edge in graph.get_graph().edges}
        self.assertIn("growth_path", graph.nodes)
        self.assertIn(("growth_path", "valuation_mix"), edges)
        self.assertIn(("valuation_mix", "valuation_router"), edges)
        self.assertIn(("operations", "growth_path"), edges)


if __name__ == "__main__":
    unittest.main()
