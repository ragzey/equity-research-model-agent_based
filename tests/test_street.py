"""Model versus Street table and evidence-bound thesis spine."""

from __future__ import annotations

import unittest

from equity_research.tools.report_pack import build_report_pack
from equity_research.tools.street import (
    build_street_comparison,
    build_thesis_spine,
    extract_street_snapshot,
)


def _pack_state():
    return {
        "ticker": "TEST",
        "is_math_verified": True,
        "valuation_method": "corporate_fcff",
        "calculated_dcf_value": 12.0,
        "discount_rate": 0.09,
        "market_info": {
            "targetMeanPrice": 15.0,
            "forwardEps": 0.9,
            "numberOfAnalystOpinions": 12,
        },
        "consensus_growth": {
            "growth": 0.08,
            "source": "yahoo_revenue_estimate_+1y",
        },
        "valuation_summary": {
            "cost_of_equity": 0.10,
            "applied_dcf_assumptions": {
                "base_revenue": 100.0,
                "base_ebit": 15.0,
                "high_growth_rate": 0.10,
                "high_growth_years": 5,
                "transition_years": 5,
                "terminal_margin": 0.12,
                "sales_to_capital": 1.6,
                "stable_sales_to_capital": 1.8,
                "terminal_growth_rate": 0.025,
            },
            "valuation_date_inputs": {
                "shares_outstanding": 10.0,
                "total_debt": 20.0,
                "cash_and_equivalents": 10.0,
                "market_cap": 1000.0,
                "share_price": 10.0,
            },
            "dcf": {
                "wacc_applied": 0.09,
                "terminal_wacc_applied": 0.08,
                "intrinsic_value_per_share": 12.0,
                "projections": [{"year": 1, "eps": 1.1}],
            },
        },
    }


class StreetSnapshotTests(unittest.TestCase):
    def test_reads_yahoo_fields_without_inventing(self):
        snapshot = extract_street_snapshot(
            {
                "targetMeanPrice": 120.0,
                "targetMedianPrice": 118.0,
                "forwardEps": 6.5,
                "numberOfAnalystOpinions": 32,
                "recommendationKey": "buy",
            },
            {
                "growth": 0.11,
                "source": "yahoo_revenue_estimate_+1y",
                "forward_eps": 6.4,
            },
        )
        self.assertEqual(snapshot["target_mean"], 120.0)
        self.assertEqual(snapshot["forward_eps"], 6.4)
        self.assertEqual(snapshot["forward_revenue_growth"], 0.11)
        self.assertIsNone(extract_street_snapshot({}, {})["target_mean"])

    def test_trailing_growth_is_not_forward_street(self):
        snapshot = extract_street_snapshot(
            {},
            {"growth": 0.18, "source": "yahoo_info_revenueGrowth_trailing"},
        )
        self.assertEqual(snapshot["revenue_growth"], 0.18)
        self.assertIsNone(snapshot["forward_revenue_growth"])


class StreetComparisonTests(unittest.TestCase):
    def test_model_above_street_on_price_target(self):
        comparison = build_street_comparison(
            snapshot={"target_mean": 100.0, "forward_eps": 5.0, "n_analysts": 20},
            model_price_target=120.0,
            model_year1_eps=5.1,
            model_growth=0.10,
        )
        self.assertEqual(comparison["headline"], "above")
        self.assertAlmostEqual(comparison["pt_gap"], 0.20)
        spine = build_thesis_spine(comparison)
        self.assertIn("$100.00", spine)
        self.assertIn("$120.00", spine)
        self.assertIn("above Street", spine)
        self.assertNotIn("http", spine)

    def test_missing_street_does_not_invent_a_target(self):
        comparison = build_street_comparison(
            snapshot={},
            model_price_target=50.0,
            model_year1_eps=2.0,
            model_growth=0.08,
        )
        self.assertFalse(comparison["has_street"])
        self.assertIsNone(comparison["headline"])
        spine = build_thesis_spine(comparison)
        self.assertIn("$50.00", spine)
        self.assertNotIn("Street mean 12-month target is $", spine)


class ReportPackStreetTests(unittest.TestCase):
    def test_pack_exposes_thesis_and_street_table(self):
        pack = build_report_pack(_pack_state())
        self.assertTrue(pack["street"]["has_street"])
        self.assertIn("spine", pack["thesis"])
        self.assertIn("$15.00", pack["thesis"]["spine"])
        labels = {point["label"] for point in pack["valuation_points"]}
        self.assertIn("Street mean PT", labels)


if __name__ == "__main__":
    unittest.main()
