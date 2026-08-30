"""Operating P&L path, bull/base/bear menus, and dated catalysts."""

from __future__ import annotations

import unittest
from datetime import date, datetime, timezone

from equity_research.agents.sensitivity import sensitivity_analyst_node
from equity_research.tools.catalysts import (
    build_catalyst_register,
    extract_market_events,
)
from equity_research.tools.firm_classifier import (
    extract_latest_net_income,
    extract_operating_pnl_anchor,
)
from equity_research.tools.operating_scenarios import (
    build_operating_scenarios,
    scenario_dcf_range,
)
from equity_research.tools.report_pack import build_report_pack


def _scenario_state():
    return {
        "ticker": "TEST",
        "is_math_verified": True,
        "dcf_overrides": {
            "architect_choices": {
                "high_growth_rate": "base",
                "high_growth_years": "base",
                "terminal_growth_rate": "base",
                "terminal_margin": "baseline",
                "sales_to_capital": "base",
            },
            "architect_menus": {
                "high_growth_rate": {"low": 0.04, "base": 0.10, "high": 0.18},
                "high_growth_years": {"compress": 3, "base": 5, "extend": 7},
                "terminal_growth_rate": {"low": 0.015, "base": 0.025, "high": 0.035},
                "terminal_margin": {"baseline": 0.12, "proposed": 0.16},
                "sales_to_capital": {"heavy": 1.2, "base": 1.6, "light": 2.0},
            },
            "architect_allowed": {
                "high_growth_rate": ["low", "base", "high"],
                "high_growth_years": ["compress", "base", "extend"],
                "terminal_growth_rate": ["low", "base", "high"],
                "terminal_margin": ["baseline", "proposed"],
                "sales_to_capital": ["heavy", "base", "light"],
            },
        },
        "valuation_summary": {
            "cost_of_equity": 0.10,
            "applied_dcf_assumptions": {
                "base_revenue": 100.0,
                "base_ebit": 15.0,
                "interest_expense": 2.0,
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
                "risk_free_rate": 0.045,
                "indicated_dividend": 0.0,
            },
            "dcf": {
                "wacc_applied": 0.09,
                "terminal_wacc_applied": 0.08,
                "terminal_growth_rate_applied": 0.025,
                "intrinsic_value_per_share": 12.0,
                "projections": [
                    {
                        "year": 1,
                        "stage": "high_growth",
                        "revenue": 110.0,
                        "ebit": 16.5,
                        "eps": 1.1,
                        "fcff": 8.0,
                    }
                ],
            },
            "firm_classification": {
                "firm_type": "Mature Large-Cap",
                "stable_sales_to_capital": 1.8,
            },
        },
    }


class PnlForecastTests(unittest.TestCase):
    def test_latest_net_income_from_statements(self):
        income = {
            datetime(2024, 12, 31): {
                "Total Revenue": 100.0,
                "Operating Income": 20.0,
                "Net Income": 12.0,
            },
            datetime(2025, 12, 31): {
                "Total Revenue": 110.0,
                "Operating Income": 22.0,
                "Net Income": 14.0,
            },
        }
        self.assertEqual(extract_latest_net_income(income), 14.0)

    def test_anchor_does_not_mix_stub_year_net_income(self):
        income = {
            datetime(2024, 12, 31): {
                "Total Revenue": 100.0,
                "Operating Income": 20.0,
                "Net Income": 12.0,
                "Interest Expense": -3.0,
                "Diluted EPS": 1.25,
            },
            datetime(2025, 12, 31): {
                "Total Revenue": 110.0,
                "Net Income": 99.0,
            },
        }
        anchor = extract_operating_pnl_anchor(income)
        self.assertEqual(anchor["revenue"], 100.0)
        self.assertEqual(anchor["ebit"], 20.0)
        self.assertEqual(anchor["net_income"], 12.0)
        self.assertEqual(anchor["interest_expense"], 3.0)
        self.assertEqual(anchor["reported_eps"], 1.25)
        self.assertEqual(anchor["period_label"], "2024-12-31")
        self.assertEqual(extract_latest_net_income(income), 12.0)


class OperatingScenarioTests(unittest.TestCase):
    def test_bull_exceeds_bear_when_stretch_labels_are_allowed(self):
        scenarios = build_operating_scenarios(_scenario_state())
        self.assertIsNotNone(scenarios)
        cases = scenarios["cases"]
        self.assertEqual(cases["bull"]["labels"]["high_growth_rate"], "high")
        self.assertEqual(cases["bear"]["labels"]["high_growth_rate"], "low")
        self.assertEqual(cases["bull"]["labels"]["sales_to_capital"], "light")
        self.assertEqual(cases["bear"]["labels"]["sales_to_capital"], "heavy")
        self.assertGreater(cases["bull"]["dcf_per_share"], cases["base"]["dcf_per_share"])
        self.assertGreater(cases["base"]["dcf_per_share"], cases["bear"]["dcf_per_share"])
        self.assertAlmostEqual(scenarios["wacc_held"], 0.09)

    def test_sensitivity_node_attaches_scenarios(self):
        state = _scenario_state()
        result = sensitivity_analyst_node(state)
        self.assertIsNotNone(result["valuation_sensitivity"])
        self.assertIn("bear", (result["operating_scenarios"] or {}).get("cases") or {})

    def test_base_labels_follow_applied_rates_not_rejected_architect_pick(self):
        state = _scenario_state()
        state["dcf_overrides"]["architect_choices"]["high_growth_rate"] = "high"
        scenarios = build_operating_scenarios(state)
        self.assertEqual(scenarios["cases"]["base"]["labels"]["high_growth_rate"], "base")
        self.assertAlmostEqual(scenarios["cases"]["base"]["high_growth_rate"], 0.10)

    def test_empty_allow_list_does_not_unlock_high(self):
        state = _scenario_state()
        state["dcf_overrides"]["architect_allowed"] = {}
        scenarios = build_operating_scenarios(state)
        self.assertNotEqual(
            scenarios["cases"]["bull"]["labels"].get("high_growth_rate"), "high"
        )
        self.assertNotEqual(
            scenarios["cases"]["bull"]["labels"].get("sales_to_capital"), "light"
        )

    def test_empty_key_allow_list_does_not_unlock_high(self):
        state = _scenario_state()
        state["dcf_overrides"]["architect_allowed"]["high_growth_rate"] = []
        scenarios = build_operating_scenarios(state)
        self.assertNotEqual(
            scenarios["cases"]["bull"]["labels"].get("high_growth_rate"), "high"
        )

    def test_scenario_range_accepts_packed_case_list(self):
        low, high = scenario_dcf_range(
            {"cases": [{"dcf_per_share": 4.0}, {"dcf_per_share": 9.0}]}
        )
        self.assertEqual(low, 4.0)
        self.assertEqual(high, 9.0)


class CatalystTests(unittest.TestCase):
    def test_yahoo_earnings_and_ex_div_are_dated(self):
        earnings = datetime(2026, 11, 15, tzinfo=timezone.utc).timestamp()
        ex_div = datetime(2026, 10, 1, tzinfo=timezone.utc).timestamp()
        events = extract_market_events(
            {"earningsTimestampStart": earnings, "exDividendDate": ex_div}
        )
        names = {item["event"] for item in events}
        self.assertIn("Next earnings", names)
        self.assertTrue(any(item["date"] == "2026-11-15" for item in events))
        self.assertTrue(
            any("price_target_12m" in item["assumption"] for item in events)
        )

    def test_filing_excerpt_maps_buyback_to_shares_without_inventing_dates(self):
        state = {
            "event_calendar": [],
            "market_info": {},
            "sec_filing_metadata": {
                "filing_date": "2026-03-31",
                "accession_number": "000123",
            },
            "qualitative_evidence": [
                {
                    "section": "Item 7",
                    "excerpt": (
                        "The Board authorized a $2.0 billion share repurchase "
                        "program through March 15, 2027."
                    ),
                }
            ],
        }
        rows = build_catalyst_register(state, today=date(2026, 8, 30))
        levers = {row["assumption"] for row in rows}
        self.assertIn("sales_to_capital, operations", levers)
        self.assertTrue(any("shares_outstanding" in row["assumption"] for row in rows))
        self.assertTrue(any(row["date"] == "2027-03-15" for row in rows))
        invented = build_catalyst_register(
            {"event_calendar": [], "market_info": {}, "sec_filing_sections": {}},
            today=date(2026, 8, 30),
        )
        self.assertEqual(invented, [])

    def test_distant_date_does_not_attach_to_working_capital(self):
        excerpt = (
            "The company manages working capital as part of ordinary operations. "
            + ("padding " * 80)
            + "On March 1, 2027 the Board set the fiscal calendar."
        )
        state = {
            "event_calendar": [],
            "market_info": {},
            "sec_filing_metadata": {},
            "qualitative_evidence": [{"section": "Item 7", "excerpt": excerpt}],
        }
        rows = build_catalyst_register(state, today=date(2026, 8, 30))
        self.assertFalse(
            any("working capital" in str(row.get("event") or "").lower() for row in rows)
        )

    def test_raw_section_dump_is_not_scanned_for_keyword_catalysts(self):
        state = {
            "event_calendar": [],
            "market_info": {},
            "sec_filing_metadata": {},
            "sec_filing_sections": {
                "Item 7": (
                    "Accounts receivable and working capital increased after "
                    "August 1, 2026."
                )
            },
            "qualitative_evidence": [],
        }
        rows = build_catalyst_register(state, today=date(2026, 8, 30))
        self.assertEqual(rows, [])

    def test_historical_as_of_date_is_not_a_catalyst(self):
        state = {
            "event_calendar": [],
            "market_info": {},
            "sec_filing_metadata": {},
            "qualitative_evidence": [
                {
                    "section": "Item 7",
                    "excerpt": (
                        "As of January 31, 2026, accounts receivable were "
                        "$4.1 billion and working capital remained adequate."
                    ),
                }
            ],
        }
        rows = build_catalyst_register(state, today=date(2026, 8, 30))
        self.assertEqual(rows, [])


class ReportPackForecastTests(unittest.TestCase):
    def test_pack_includes_pnl_and_catalyst_slots(self):
        state = _scenario_state()
        state["operating_scenarios"] = build_operating_scenarios(state)
        state["calculated_dcf_value"] = 12.0
        state["discount_rate"] = 0.09
        state["valuation_method"] = "corporate_fcff"
        pack = build_report_pack(state)
        self.assertTrue(pack["pnl_forecast"])
        self.assertEqual(pack["pnl_forecast"][0]["year"], "Last reported")
        self.assertEqual(pack["pnl_forecast"][0]["stage"], "reported")
        self.assertEqual(pack["pnl_forecast"][0]["eps_basis"], "reported")
        self.assertEqual(pack["pnl_forecast"][1]["eps_basis"], "model")
        self.assertIsNotNone(pack["operating_scenarios"])
        self.assertIn("bear_pt", pack["operating_scenarios"])

    def test_pack_uses_statement_period_and_eps_basis(self):
        state = _scenario_state()
        applied = state["valuation_summary"]["applied_dcf_assumptions"]
        applied["base_period"] = "2025-12-31"
        applied["base_eps_basis"] = "statement"
        applied["base_eps"] = 1.4
        state["calculated_dcf_value"] = 12.0
        state["discount_rate"] = 0.09
        state["valuation_method"] = "corporate_fcff"
        pack = build_report_pack(state)
        self.assertEqual(pack["pnl_forecast"][0]["year"], "2025-12-31")
        self.assertEqual(pack["pnl_forecast"][0]["eps_basis"], "statement")
        self.assertAlmostEqual(pack["pnl_forecast"][0]["eps"], 1.4)


if __name__ == "__main__":
    unittest.main()
