"""Deterministic tests for lifecycle classification, WACC, and DCF math."""

import unittest
from datetime import datetime

from equity_research.tools.firm_classifier import (
    calculate_revenue_cagr,
    classify_firm_and_adjust_assumptions,
    extract_operating_baseline,
)
from equity_research.tools.valuation import (
    calculate_wacc,
    perform_3stage_dcf_valuation,
)


def sample_income_statement():
    return {
        datetime(2023, 12, 31): {
            "Total Revenue": 100_000_000.0,
            "Operating Income": 15_000_000.0,
        },
        datetime(2024, 12, 31): {
            "Total Revenue": 120_000_000.0,
            "Operating Income": 20_000_000.0,
        },
        datetime(2025, 12, 31): {
            "Total Revenue": 144_000_000.0,
            "Operating Income": 25_000_000.0,
        },
    }


class ClassifierTests(unittest.TestCase):
    def test_period_major_yahoo_shape(self):
        revenue, ebit = extract_operating_baseline(sample_income_statement())
        self.assertEqual(revenue, 144_000_000.0)
        self.assertEqual(ebit, 25_000_000.0)
        # Calendar-day annualization includes the 2024 leap day.
        self.assertAlmostEqual(
            calculate_revenue_cagr(sample_income_statement()), 0.2, places=3
        )

    def test_high_growth_small_cap(self):
        result = classify_firm_and_adjust_assumptions(
            1_000_000_000,
            sample_income_statement(),
            {"sector": "Technology", "industry": "Software"},
        )
        self.assertEqual(result["firm_type"], "High-Growth Small-Cap")
        self.assertEqual(result["size_premium"], 0.02)
        self.assertTrue(result["fcff_supported"])

    def test_scale_up_uses_longer_horizon_and_higher_growth_band(self):
        income = {
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
        }
        result = classify_firm_and_adjust_assumptions(
            55_000_000_000,
            income,
            {"sector": "Communication Services", "industry": "Internet Content"},
        )
        self.assertEqual(result["firm_type"], "Scale-up High-Growth")
        self.assertEqual(result["high_growth_years"], 8)
        self.assertEqual(result["transition_years"], 5)
        self.assertAlmostEqual(result["high_growth_rate_bounds"][0], 0.20)
        self.assertAlmostEqual(result["high_growth_rate_bounds"][1], 0.50)
        self.assertAlmostEqual(result["high_growth_rate"], 0.50)
        self.assertGreater(result["price_to_sales"], 15)
        self.assertAlmostEqual(result["terminal_margin"], 0.15)

    def test_high_ps_below_scaleup_cagr_stays_large_cap_high_growth(self):
        income = {
            datetime(2024, 12, 31): {
                "Total Revenue": 666_666_667.0,
                "Operating Income": 80_000_000.0,
            },
            datetime(2025, 12, 31): {
                "Total Revenue": 800_000_000.0,
                "Operating Income": 96_000_000.0,
            },
        }
        result = classify_firm_and_adjust_assumptions(
            20_000_000_000,
            income,
            {"sector": "Technology", "industry": "Software"},
        )
        self.assertEqual(result["firm_type"], "High-Growth Large-Cap")
        self.assertGreater(result["price_to_sales"], 15)
        self.assertAlmostEqual(result["high_growth_rate_bounds"][1], 0.20)

    def test_financial_services_firm_is_out_of_scope(self):
        result = classify_firm_and_adjust_assumptions(
            50_000_000_000,
            sample_income_statement(),
            {"sector": "Financial Services", "industry": "Banks—Diversified"},
        )
        self.assertFalse(result["fcff_supported"])


class ValuationTests(unittest.TestCase):
    def test_zero_debt_wacc_equals_cost_of_equity(self):
        result = calculate_wacc(100, 10, 0, 0.10, 0.05)
        self.assertAlmostEqual(result["wacc"], 0.10)
        self.assertEqual(result["weight_debt"], 0)

    def test_transition_reaches_terminal_assumptions(self):
        result = perform_3stage_dcf_valuation(
            base_revenue=100,
            base_ebit=15,
            sales_to_capital=1.5,
            high_growth_rate=0.15,
            wacc=0.10,
            terminal_wacc=0.08,
            shares_outstanding=10,
            total_debt=20,
            cash_and_equivalents=10,
            high_growth_years=2,
            transition_years=3,
            terminal_growth_rate=0.025,
            terminal_margin=0.12,
            stable_sales_to_capital=2.0,
        )
        last = result["projections"][-1]
        self.assertAlmostEqual(last["growth_rate"], 0.025)
        self.assertAlmostEqual(last["operating_margin"], 0.12)
        self.assertAlmostEqual(last["sales_to_capital"], 2.0)
        self.assertAlmostEqual(last["wacc"], 0.08)
        self.assertGreater(result["enterprise_value"], 0)

    def test_pnl_precedes_fcff_and_eps_uses_shares(self):
        result = perform_3stage_dcf_valuation(
            base_revenue=100,
            base_ebit=20,
            sales_to_capital=2.0,
            high_growth_rate=0.10,
            wacc=0.10,
            terminal_wacc=0.08,
            shares_outstanding=10,
            total_debt=0,
            cash_and_equivalents=0,
            high_growth_years=1,
            transition_years=1,
            terminal_growth_rate=0.025,
            terminal_margin=0.20,
            stable_sales_to_capital=2.0,
            interest_expense=5.0,
        )
        year1 = result["projections"][0]
        self.assertAlmostEqual(year1["revenue"], 110.0)
        self.assertAlmostEqual(year1["ebit"], 22.0)
        self.assertAlmostEqual(year1["interest_expense"], 5.0)
        self.assertAlmostEqual(year1["ebt"], 17.0)
        self.assertAlmostEqual(year1["net_income"], 17.0 * 0.79)
        self.assertAlmostEqual(year1["eps"], year1["net_income"] / 10)
        self.assertAlmostEqual(year1["nopat"], 22.0 * 0.79)
        self.assertAlmostEqual(year1["fcff"], year1["nopat"] - (10.0 / 2.0))
        self.assertIn("Revenue grows", result["pnl_method"])

    def test_rejects_unsafe_terminal_spread(self):
        with self.assertRaises(ValueError):
            perform_3stage_dcf_valuation(
                base_revenue=100,
                base_ebit=15,
                sales_to_capital=1.5,
                high_growth_rate=0.10,
                wacc=0.08,
                terminal_wacc=0.03,
                shares_outstanding=10,
                total_debt=0,
                cash_and_equivalents=0,
                terminal_growth_rate=0.025,
            )


if __name__ == "__main__":
    unittest.main()
