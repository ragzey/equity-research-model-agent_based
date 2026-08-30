"""Tests for post-Quant review, sensitivity, and bank valuation."""

import unittest

from equity_research.agents.post_quant_reviewer import post_quant_reviewer_node
from equity_research.graphs.defaults import initial_state
from equity_research.tools.valuation import build_dcf_sensitivity_grid


class PostQuantReviewTests(unittest.TestCase):
    def _state(self):
        state = initial_state("TEST", "2026")
        state.update(
            {
                "discount_rate": 0.10,
                "calculated_dcf_value": 9.0,
                "valuation_summary": {
                    "valuation_date_inputs": {
                        "cash_and_equivalents": 10.0,
                        "total_debt": 20.0,
                        "shares_outstanding": 10.0,
                    },
                    "dcf": {
                        "enterprise_value": 100.0,
                        "equity_value": 90.0,
                        "intrinsic_value_per_share": 9.0,
                        "terminal_wacc_applied": 0.08,
                        "terminal_growth_rate_applied": 0.025,
                        "terminal_value_share_of_enterprise_value": 0.60,
                    },
                },
            }
        )
        return state

    def test_valid_math_passes(self):
        result = post_quant_reviewer_node(self._state())
        self.assertTrue(result["is_math_verified"])
        self.assertEqual(result["review_action"], "pass")

    def test_terminal_concentration_is_warning_not_assumption_fitting(self):
        state = self._state()
        state["valuation_summary"]["dcf"][
            "terminal_value_share_of_enterprise_value"
        ] = 0.90
        result = post_quant_reviewer_node(state)
        self.assertTrue(result["is_math_verified"])
        self.assertEqual(result["review_action"], "warn")

    def test_recomputable_bridge_error_requests_bounded_retry(self):
        state = self._state()
        state["valuation_summary"]["dcf"]["equity_value"] = 80.0
        result = post_quant_reviewer_node(state)
        self.assertFalse(result["is_math_verified"])
        self.assertEqual(result["review_action"], "retry")
        self.assertEqual(result["revision_count"], 1)

    def test_persistent_negative_fcff_is_warning_not_bankruptcy_claim(self):
        state = self._state()
        state["valuation_summary"]["dcf"]["projections"] = [
            {"year": year, "fcff": -1.0 if year <= 7 else 1.0}
            for year in range(1, 11)
        ]
        result = post_quant_reviewer_node(state)
        self.assertTrue(result["is_math_verified"])
        self.assertEqual(result["review_action"], "warn")
        finding = next(
            item
            for item in result["review_findings"]
            if item["code"] == "PERSISTENT_NEGATIVE_FCFF"
        )
        self.assertIn("not by itself proof", finding["message"])


class SensitivityTests(unittest.TestCase):
    def test_grid_is_5_by_5_and_serializable(self):
        grid = build_dcf_sensitivity_grid(
            base_revenue=100,
            base_ebit=15,
            sales_to_capital=1.5,
            high_growth_rate=0.10,
            base_wacc=0.10,
            base_terminal_wacc=0.08,
            base_terminal_growth=0.025,
            shares_outstanding=10,
            total_debt=20,
            cash_and_equivalents=10,
            high_growth_years=3,
            transition_years=3,
            terminal_margin=0.12,
            stable_sales_to_capital=2.0,
        )
        self.assertEqual(len(grid["wacc_values"]), 5)
        self.assertEqual(len(grid["terminal_growth_values"]), 5)
        self.assertTrue(all(len(row) == 5 for row in grid["intrinsic_value_per_share"]))


if __name__ == "__main__":
    unittest.main()
