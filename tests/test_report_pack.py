"""Tests for blended fair value, 12-month price target, and the model band."""

from __future__ import annotations

import unittest

from equity_research.agents.writer import _header_block
from equity_research.tools.price_history import rebase_aligned_series
from equity_research.tools.report_pack import (
    blend_fair_value,
    build_report_pack,
    implied_price_from_ev_ebitda,
    model_rating,
    price_target_12m,
)


class BlendAndTargetTests(unittest.TestCase):
    def test_blend_is_seventy_thirty(self):
        value, dcf_w, rel_w = blend_fair_value(100.0, 50.0)
        self.assertAlmostEqual(value, 85.0)
        self.assertAlmostEqual(dcf_w, 0.70)
        self.assertAlmostEqual(rel_w, 0.30)

    def test_blend_drops_relative_when_missing(self):
        value, dcf_w, rel_w = blend_fair_value(100.0, None)
        self.assertEqual(value, 100.0)
        self.assertEqual(dcf_w, 1.0)
        self.assertEqual(rel_w, 0.0)

    def test_price_target_rolls_forward_at_ke_minus_dividend(self):
        self.assertAlmostEqual(price_target_12m(100.0, 0.10, 2.0), 108.0)
        self.assertAlmostEqual(price_target_12m(100.0, 0.10, None), 110.0)
        self.assertIsNone(price_target_12m(None, 0.10))

    def test_rating_band(self):
        self.assertEqual(model_rating(0.15), "Buy")
        self.assertEqual(model_rating(0.149), "Hold")
        self.assertEqual(model_rating(0.0), "Hold")
        self.assertEqual(model_rating(-0.15), "Sell")
        self.assertEqual(model_rating(-0.149), "Hold")
        self.assertIsNone(model_rating(None))


class RelativeValueTests(unittest.TestCase):
    def test_implied_price_from_peer_median(self):
        result = implied_price_from_ev_ebitda(
            peer_median_ev_ebitda=8.0,
            target_ev_ebitda=11.0,
            target_ebitda=None,
            market_cap=100.0,
            total_debt=20.0,
            cash=10.0,
            shares=10.0,
        )
        self.assertIsNotNone(result)
        # Market EV = 110, EBITDA = 10, peer 8x -> EV 80, equity 70, $7/share.
        self.assertAlmostEqual(result["implied_price"], 7.0)
        self.assertEqual(result["method"], "implied_from_current_ev_ebitda")

    def test_prefers_reported_ebitda(self):
        result = implied_price_from_ev_ebitda(
            peer_median_ev_ebitda=10.0,
            target_ev_ebitda=20.0,
            target_ebitda=5.0,
            market_cap=100.0,
            total_debt=10.0,
            cash=0.0,
            shares=5.0,
        )
        self.assertAlmostEqual(result["implied_price"], 8.0)
        self.assertEqual(result["method"], "yahoo_trailing_ebitda")

    def test_relative_none_when_median_missing(self):
        self.assertIsNone(
            implied_price_from_ev_ebitda(
                peer_median_ev_ebitda=None,
                target_ev_ebitda=11.0,
                target_ebitda=10.0,
                market_cap=100.0,
                total_debt=0.0,
                cash=0.0,
                shares=10.0,
            )
        )


class ReportPackIntegrationTests(unittest.TestCase):
    def _state(self, **extra):
        state = {
            "ticker": "TEST",
            "is_math_verified": True,
            "calculated_dcf_value": 100.0,
            "discount_rate": 0.08,
            "valuation_method": "corporate_fcff",
            "market_info": {
                "longName": "Test Corp",
                "industry": "Software",
                "country": "United States",
            },
            "peer_metadata": {
                "TEST": {
                    "company_name": "Test Corp",
                    "industry": "Software",
                    "country": "United States",
                }
            },
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
                    "beta": 1.1,
                    "risk_free_rate": 0.04,
                    "market_equity_risk_premium": 0.05,
                    "company_specific_risk_premium": 0.0,
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
                "firm_classification": {
                    "firm_type": "High-Growth Large-Cap",
                    "size_premium": 0.005,
                },
                "cost_of_debt": {
                    "method_used": "Synthetic Credit Rating (Fallback)",
                    "pre_tax_cost_of_debt": 0.05,
                    "after_tax_cost_of_debt": 0.0395,
                    "marginal_tax_rate_applied": 0.21,
                    "details": {
                        "synthetic_rating": "A",
                        "damodaran_spreads_as_of": "2026-01-05",
                    },
                },
                "wacc": {"wacc": 0.08, "weight_equity": 0.9, "weight_debt": 0.1},
            },
            "dcf_overrides": {
                "rationales": {
                    "high_growth_rate": "Blend of history and consensus."
                },
                "decisions": [
                    {
                        "key": "high_growth_rate",
                        "action": "accept",
                        "reason": "Candidate retained.",
                    }
                ],
            },
            "valuation_sensitivity": {
                "intrinsic_value_per_share": [[90.0, 100.0], [80.0, 110.0]]
            },
        }
        state.update(extra)
        return state

    def test_pack_blends_and_rates_hold_near_fair(self):
        pack = build_report_pack(self._state())
        # Relative: 8x * 10 EBITDA = 80 EV; equity = 80 - 20 + 10 = 70; $7/share.
        self.assertAlmostEqual(pack["relative_value"], 7.0)
        self.assertAlmostEqual(pack["fair_value"], 0.7 * 100 + 0.3 * 7)
        self.assertAlmostEqual(
            pack["price_target_12m"],
            pack["fair_value"] * 1.10 - 1.0,
        )
        self.assertEqual(pack["dcf_low"], 80.0)
        self.assertEqual(pack["dcf_high"], 110.0)
        self.assertTrue(pack["assumptions"])
        self.assertTrue(any(row["item"] == "High-growth rate" for row in pack["assumptions"]))
        growth_row = next(row for row in pack["assumptions"] if row["item"] == "High-growth rate")
        self.assertIn("Blend of history and consensus", growth_row["justification"])
        self.assertEqual(pack["model_rating"], "Hold")

    def test_no_peers_uses_full_dcf_weight(self):
        state = self._state()
        state["peer_comparison_matrix"] = {
            "target": "TEST",
            "competitors": [],
            "metrics": {},
            "peer_medians": {},
        }
        pack = build_report_pack(state)
        self.assertIsNone(pack["relative_value"])
        self.assertEqual(pack["dcf_weight"], 1.0)
        self.assertAlmostEqual(pack["fair_value"], 100.0)
        self.assertIn("No peer group", pack["relative_unavailable_reason"])

    def test_negative_dcf_is_floored_for_blend(self):
        pack = build_report_pack(self._state(calculated_dcf_value=-40.0))
        self.assertEqual(pack["dcf_value"], 0.0)
        self.assertEqual(pack["raw_dcf_value"], -40.0)

    def test_unverified_withholds_rating(self):
        pack = build_report_pack(self._state(is_math_verified=False))
        self.assertIsNone(pack["model_rating"])

    def test_header_uses_price_target(self):
        pack = build_report_pack(self._state())
        header = _header_block({"ticker": "TEST", "target_year": "2026"}, pack)
        self.assertIn("12-month price target", header)
        self.assertIn("TEST", header)
        self.assertIn("Model-implied HOLD", header)


class PriceHistoryTests(unittest.TestCase):
    def test_rebase_aligns_and_indexes_to_100(self):
        stock = {"2025-01-03": 50.0, "2025-01-10": 55.0, "2025-01-17": 60.0}
        bench = {"2025-01-03": 200.0, "2025-01-10": 220.0, "2025-01-24": 240.0}
        # Need MIN_POINTS=8, so pad overlapping dates.
        for i in range(8):
            day = f"2025-02-{i + 1:02d}"
            stock[day] = 50.0 + i
            bench[day] = 200.0 + i
        points = rebase_aligned_series(stock, bench)
        self.assertGreaterEqual(len(points), 8)
        self.assertAlmostEqual(points[0]["stock"], 100.0)
        self.assertAlmostEqual(points[0]["benchmark"], 100.0)
        dates = {p["date"] for p in points}
        self.assertNotIn("2025-01-17", dates)
        self.assertNotIn("2025-01-24", dates)


if __name__ == "__main__":
    unittest.main()
