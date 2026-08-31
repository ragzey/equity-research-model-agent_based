"""Labeled DCF / relative mix from firm type, peer fit, and industry packet."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from equity_research.agents.valuation_mix import valuation_mix_node
from equity_research.graphs.defaults import initial_state
from equity_research.graphs.graph import build_research_graph
from equity_research.tools.report_pack import blend_fair_value, build_report_pack
from equity_research.tools.valuation_mix import (
    MIX_WEIGHTS,
    ledger_mix_path,
    mix_weights_from_state,
    overlay_ledger_valuation_mix,
)


def _scale_up_metrics(**overrides):
    metrics = {
        "firm_type": "Scale-up High-Growth",
        "is_financial": False,
        "is_scale_up": True,
        "is_mature": False,
        "price_to_sales": 105.0,
        "peer_count": 4,
        "same_industry_count": 1,
        "same_sector_count": 2,
        "has_peer_median": True,
        "peer_median_ev_ebitda": 18.0,
        "target_ebitda": 80_000_000.0,
        "category_growth": "insufficient",
        "cycle": "insufficient",
        "demand_inflection": "insufficient",
        "scale_view": "still_ramping",
    }
    metrics.update(overrides)
    return metrics


def _mature_tight_metrics(**overrides):
    metrics = {
        "firm_type": "Mature Large-Cap",
        "is_financial": False,
        "is_scale_up": False,
        "is_mature": True,
        "industry": "apparel retail",
        "price_to_sales": 2.4,
        "peer_count": 3,
        "same_industry_count": 3,
        "same_sector_count": 3,
        "has_peer_median": True,
        "peer_median_ev_ebitda": 14.0,
        "target_ebitda": 6_000_000_000.0,
        "category_growth": "in_line",
        "cycle": "mid",
        "demand_inflection": "none",
        "scale_view": "not_applicable",
    }
    metrics.update(overrides)
    return metrics


class MixLedgerTests(unittest.TestCase):
    def test_scale_up_defaults_to_dcf_heavy_and_blocks_balanced(self):
        filled = ledger_mix_path(_scale_up_metrics())
        self.assertEqual(filled["default_label"], "dcf_heavy")
        self.assertEqual(filled["allowed"], ["dcf_heavy"])
        self.assertEqual(filled["relative_role"]["view"], "poor_descriptor")
        self.assertNotIn("balanced", filled["allowed"])

    def test_mature_tight_comps_default_to_balanced(self):
        filled = ledger_mix_path(_mature_tight_metrics())
        self.assertEqual(filled["default_label"], "balanced")
        self.assertIn("balanced", filled["allowed"])
        self.assertIn("base", filled["allowed"])
        self.assertEqual(filled["peer_fit"]["view"], "tight")
        self.assertEqual(filled["relative_role"]["view"], "industry_standard")

    def test_high_growth_thin_comps_stay_on_base_seventy_thirty(self):
        filled = ledger_mix_path(
            {
                "firm_type": "High-Growth Large-Cap",
                "is_financial": False,
                "is_scale_up": False,
                "is_mature": False,
                "peer_count": 1,
                "same_industry_count": 0,
                "has_peer_median": True,
                "target_ebitda": 10.0,
            }
        )
        self.assertEqual(filled["default_label"], "base")
        self.assertEqual(MIX_WEIGHTS["dcf_heavy"], (0.90, 0.10))
        self.assertEqual(MIX_WEIGHTS["base"], (0.70, 0.30))
        self.assertIn("dcf_heavy", filled["allowed"])
        self.assertNotIn("balanced", filled["allowed"])

    def test_overlay_rejects_balanced_on_a_scale_up(self):
        packet = overlay_ledger_valuation_mix(
            {"mix_view": {"view": "balanced", "evidence": "Industry trades on EV/EBITDA."}},
            _scale_up_metrics(),
        )
        self.assertEqual(packet["label"], "dcf_heavy")
        self.assertAlmostEqual(packet["dcf_weight"], 0.90)
        self.assertAlmostEqual(packet["relative_weight"], 0.10)

    def test_overlay_keeps_allowed_dcf_heavy_on_high_growth(self):
        metrics = {
            "firm_type": "High-Growth Large-Cap",
            "is_financial": False,
            "is_scale_up": False,
            "is_mature": False,
            "peer_count": 1,
            "same_industry_count": 0,
            "has_peer_median": True,
            "target_ebitda": 10.0,
        }
        packet = overlay_ledger_valuation_mix(
            {
                "mix_view": {
                    "view": "dcf_heavy",
                    "evidence": "Selected peer count is 1.",
                }
            },
            metrics,
        )
        self.assertEqual(packet["label"], "dcf_heavy")
        self.assertAlmostEqual(packet["dcf_weight"], 0.90)

    def test_llm_cannot_type_a_custom_percentage(self):
        packet = overlay_ledger_valuation_mix(
            {
                "mix_view": {"view": "base"},
                "dcf_weight": 0.62,
                "relative_weight": 0.38,
            },
            _mature_tight_metrics(),
        )
        self.assertEqual(packet["label"], "base")
        self.assertAlmostEqual(packet["dcf_weight"], 0.70)
        self.assertAlmostEqual(packet["relative_weight"], 0.30)


def _pack_state(**extra):
    state = {
        "ticker": "TEST",
        "is_math_verified": True,
        "calculated_dcf_value": 100.0,
        "discount_rate": 0.08,
        "valuation_method": "corporate_fcff",
        "market_info": {"longName": "Test Corp", "industry": "Software"},
        "peer_metadata": {"TEST": {"company_name": "Test Corp", "industry": "Software"}},
        "peer_comparison_matrix": {
            "target": "TEST",
            "competitors": ["PEER"],
            "metrics": {
                "TEST": {"ev_to_ebitda": 10.0, "ebitda": 10.0},
                "PEER": {"ev_to_ebitda": 8.0},
            },
            "peer_medians": {"ev_to_ebitda": 8.0},
        },
        "valuation_summary": {
            "valuation_method": "corporate_fcff",
            "cost_of_equity": 0.10,
            "valuation_date_inputs": {
                "share_price": 90.0,
                "shares_outstanding": 10.0,
                "market_cap": 900.0,
                "total_debt": 20.0,
                "cash_and_equivalents": 10.0,
                "indicated_dividend": 1.0,
            },
            "applied_dcf_assumptions": {
                "high_growth_rate": 0.12,
                "high_growth_years": 5,
                "transition_years": 5,
                "terminal_margin": 0.18,
                "terminal_growth_rate": 0.025,
                "sales_to_capital": 1.8,
            },
            "firm_classification": {"firm_type": "High-Growth Large-Cap"},
            "cost_of_debt": {"method_used": "Synthetic", "details": {}},
            "wacc": {"wacc": 0.08},
        },
        "valuation_sensitivity": {
            "intrinsic_value_per_share": [[90.0, 100.0], [80.0, 110.0]]
        },
    }
    state.update(extra)
    return state


class MixPackTests(unittest.TestCase):
    def test_pack_uses_scale_up_dcf_heavy_weights(self):
        packet = overlay_ledger_valuation_mix({}, _scale_up_metrics())
        state = _pack_state(valuation_mix_packet=packet)
        pack = build_report_pack(state)
        self.assertAlmostEqual(pack["dcf_weight"], 0.90)
        self.assertAlmostEqual(pack["relative_weight"], 0.10)
        self.assertAlmostEqual(pack["fair_value"], 0.90 * 100 + 0.10 * 7)
        self.assertEqual(pack["valuation_mix"], "dcf_heavy")

    def test_pack_without_packet_keeps_initiation_base_for_high_growth(self):
        pack = build_report_pack(_pack_state())
        self.assertAlmostEqual(pack["dcf_weight"], 0.70)
        self.assertAlmostEqual(pack["fair_value"], 0.7 * 100 + 0.3 * 7)

    def test_blend_helper_still_defaults_to_seventy_thirty(self):
        value, dcf_w, rel_w = blend_fair_value(100.0, 50.0)
        self.assertAlmostEqual(value, 85.0)
        self.assertAlmostEqual(dcf_w, 0.70)
        self.assertAlmostEqual(rel_w, 0.30)

    def test_mix_weights_from_state_ignore_forged_floats(self):
        state = {
            "valuation_mix_packet": {
                "label": "dcf_heavy",
                "dcf_weight": 0.10,
                "relative_weight": 0.90,
            }
        }
        dcf_w, rel_w = mix_weights_from_state(state)
        self.assertAlmostEqual(dcf_w, 0.90)
        self.assertAlmostEqual(rel_w, 0.10)


class MixAgentTests(unittest.TestCase):
    @patch("equity_research.agents.valuation_mix.chat_json")
    def test_financial_skips_llm(self, mock_chat):
        state = initial_state("JPM", "2026")
        state["is_financial"] = True
        state["market_info"] = {"sector": "Financial Services"}
        result = valuation_mix_node(state)
        mock_chat.assert_not_called()
        self.assertFalse(result["valuation_mix_packet"]["applicable"])
        self.assertEqual(result["valuation_mix_packet"]["label"], "not_applicable")

    def test_graph_runs_mix_after_growth_path(self):
        graph = build_research_graph()
        edges = {(edge.source, edge.target) for edge in graph.get_graph().edges}
        self.assertIn("valuation_mix", graph.nodes)
        self.assertIn(("growth_path", "valuation_mix"), edges)
        self.assertIn(("valuation_mix", "valuation_router"), edges)


if __name__ == "__main__":
    unittest.main()
