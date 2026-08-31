"""Operating-cycle arithmetic, operations packet, and sales-to-capital menus."""

from __future__ import annotations

import unittest
from datetime import datetime
from unittest.mock import patch

from equity_research.agents.operations import (
    normalize_operations_packet,
    operations_node,
)
from equity_research.graphs.defaults import initial_state
from equity_research.graphs.graph import build_research_graph
from equity_research.tools.assumption_menus import (
    allowed_stc_choices,
    apply_architect_choices,
    build_assumption_bundle,
    build_choice_menus,
)
from equity_research.tools.operating_cycle import measure_operating_cycle
from equity_research.tools.report_pack import build_report_pack


def _period_statements():
    income = {
        datetime(2024, 12, 31): {
            "Total Revenue": 100.0,
            "Cost Of Revenue": 60.0,
            "Operating Income": 18.0,
        },
        datetime(2025, 12, 31): {
            "Total Revenue": 120.0,
            "Cost Of Revenue": 72.0,
            "Operating Income": 22.0,
        },
    }
    balance = {
        datetime(2024, 12, 31): {
            "Accounts Receivable": 10.0,
            "Inventory": 20.0,
            "Accounts Payable": 8.0,
            "Net PPE": 50.0,
        },
        datetime(2025, 12, 31): {
            "Accounts Receivable": 20.0,
            "Inventory": 24.0,
            "Accounts Payable": 10.0,
            "Net PPE": 70.0,
        },
    }
    return income, balance


def _absorbing_packet():
    return {
        "working_capital": {
            "view": "absorbing",
            "evidence": "Python working-capital view is absorbing.",
        },
        "cash_conversion": {
            "view": "lengthening",
            "evidence": "Python cash-conversion view is lengthening.",
        },
        "reinvestment": {
            "view": "heavy",
            "evidence": "Python reinvestment view is heavy.",
        },
    }


def _releasing_packet():
    return {
        "working_capital": {
            "view": "releasing",
            "evidence": "Python working-capital view is releasing.",
        },
        "cash_conversion": {
            "view": "shortening",
            "evidence": "Python cash-conversion view is shortening.",
        },
        "reinvestment": {
            "view": "asset_light",
            "evidence": "Python reinvestment view is asset_light.",
        },
    }


def _bundle_state():
    income, balance = _period_statements()
    state = initial_state("TPR", "2026")
    state.update(
        {
            "income_statement": income,
            "balance_sheet": balance,
            "peer_comparison_matrix": {
                "target": "TPR",
                "competitors": ["RL"],
                "metrics": {
                    "TPR": {
                        "operating_margin_pct": 18.0,
                        "market_cap": 20_000_000_000,
                    }
                },
            },
            "peer_metadata": {
                "TPR": {
                    "market_cap": 20_000_000_000,
                    "sector": "Consumer Cyclical",
                    "industry": "Luxury Goods",
                }
            },
        }
    )
    return state


class OperatingCycleTests(unittest.TestCase):
    def test_ccc_nwc_and_implied_sales_to_capital(self):
        income, balance = _period_statements()
        metrics = measure_operating_cycle(
            income, balance, classifier_sales_to_capital=1.8
        )
        self.assertAlmostEqual(metrics["dso_days"], 20.0 / 120.0 * 365.0, places=4)
        self.assertAlmostEqual(metrics["dio_days"], 24.0 / 72.0 * 365.0, places=4)
        self.assertAlmostEqual(metrics["dpo_days"], 10.0 / 72.0 * 365.0, places=4)
        self.assertAlmostEqual(
            metrics["ccc_days"],
            metrics["dso_days"] + metrics["dio_days"] - metrics["dpo_days"],
            places=4,
        )
        self.assertAlmostEqual(metrics["nwc"], 34.0)
        self.assertAlmostEqual(metrics["nwc_to_sales"], 34.0 / 120.0, places=4)
        self.assertAlmostEqual(metrics["delta_nwc"], 12.0)
        self.assertAlmostEqual(metrics["implied_sales_to_capital"], 20.0 / 32.0, places=4)
        self.assertEqual(metrics["working_capital_view"], "absorbing")
        self.assertEqual(metrics["reinvestment_view"], "heavy")
        self.assertEqual(metrics["cash_conversion_view"], "lengthening")

    def test_service_firm_treats_missing_inventory_as_zero_dio(self):
        income = {
            datetime(2024, 12, 31): {"Total Revenue": 80.0, "Cost Of Revenue": 20.0},
            datetime(2025, 12, 31): {"Total Revenue": 100.0, "Cost Of Revenue": 25.0},
        }
        balance = {
            datetime(2024, 12, 31): {
                "Accounts Receivable": 8.0,
                "Accounts Payable": 4.0,
                "Net PPE": 10.0,
            },
            datetime(2025, 12, 31): {
                "Accounts Receivable": 10.0,
                "Accounts Payable": 5.0,
                "Net PPE": 11.0,
            },
        }
        metrics = measure_operating_cycle(income, balance)
        self.assertEqual(metrics["dio_days"], 0.0)
        self.assertIsNotNone(metrics["ccc_days"])
        self.assertAlmostEqual(
            metrics["ccc_days"],
            metrics["dso_days"] - metrics["dpo_days"],
            places=4,
        )

    def test_capital_release_on_growth_is_asset_light(self):
        income = {
            datetime(2024, 12, 31): {"Total Revenue": 100.0, "Cost Of Revenue": 60.0},
            datetime(2025, 12, 31): {"Total Revenue": 120.0, "Cost Of Revenue": 70.0},
        }
        balance = {
            datetime(2024, 12, 31): {
                "Accounts Receivable": 20.0,
                "Inventory": 30.0,
                "Accounts Payable": 5.0,
                "Net PPE": 80.0,
            },
            datetime(2025, 12, 31): {
                "Accounts Receivable": 12.0,
                "Inventory": 18.0,
                "Accounts Payable": 10.0,
                "Net PPE": 70.0,
            },
        }
        metrics = measure_operating_cycle(income, balance, classifier_sales_to_capital=1.8)
        self.assertTrue(metrics["capital_released_on_growth"])
        self.assertIsNone(metrics["implied_sales_to_capital"])
        self.assertEqual(metrics["reinvestment_view"], "asset_light")
        self.assertEqual(metrics["working_capital_view"], "releasing")


class OperationsPacketTests(unittest.TestCase):
    def test_python_metric_view_wins_over_contradicting_llm(self):
        income, balance = _period_statements()
        metrics = measure_operating_cycle(
            income, balance, classifier_sales_to_capital=1.8
        )
        packet = normalize_operations_packet(
            {
                "working_capital": {
                    "view": "releasing",
                    "evidence": "Python working-capital view is absorbing.",
                },
                "cash_conversion": {
                    "view": "shortening",
                    "evidence": "Python cash-conversion view is lengthening.",
                },
                "reinvestment": {
                    "view": "asset_light",
                    "evidence": "Python reinvestment view is heavy.",
                },
                "narrative": "Working capital is releasing cash this year.",
            },
            metrics,
            ledger_text="Python working-capital view is absorbing. "
            "Python cash-conversion view is lengthening. "
            "Python reinvestment view is heavy. 28.0 1.8",
        )
        self.assertEqual(packet["working_capital"]["view"], "absorbing")
        self.assertEqual(packet["cash_conversion"]["view"], "lengthening")
        self.assertEqual(packet["reinvestment"]["view"], "heavy")
        self.assertIn("Python working-capital view", packet["working_capital"]["evidence"])

    def test_llm_cannot_invent_wc_view_when_metrics_are_missing(self):
        packet = normalize_operations_packet(
            {
                "working_capital": {
                    "view": "absorbing",
                    "evidence": "The company is expanding digital and international retail.",
                },
                "cash_conversion": {
                    "view": "lengthening",
                    "evidence": "The company is expanding digital and international retail.",
                },
                "reinvestment": {
                    "view": "heavy",
                    "evidence": "The company is expanding digital and international retail.",
                },
            },
            measure_operating_cycle({}, {}),
            ledger_text="The company is expanding digital and international retail.",
        )
        self.assertEqual(packet["working_capital"]["view"], "insufficient")
        self.assertEqual(packet["cash_conversion"]["view"], "insufficient")
        self.assertEqual(packet["reinvestment"]["view"], "insufficient")

    @patch("equity_research.agents.operations.chat_json")
    def test_operations_node_writes_packet(self, mock_chat):
        mock_chat.return_value = {
            "cash_conversion": {
                "view": "lengthening",
                "evidence": "Python cash-conversion view is lengthening.",
            },
            "working_capital": {
                "view": "absorbing",
                "evidence": "Python working-capital view is absorbing.",
            },
            "reinvestment": {
                "view": "heavy",
                "evidence": "Python reinvestment view is heavy.",
            },
            "narrative": "Python working-capital view is absorbing.",
        }
        result = operations_node(_bundle_state())
        packet = result["operations_packet"]
        self.assertEqual(packet["working_capital"]["view"], "absorbing")
        self.assertIsNotNone(packet["metrics"]["ccc_days"])
        mock_chat.assert_called_once()
        self.assertTrue(mock_chat.call_args.kwargs.get("required"))

    @patch("equity_research.agents.operations.chat_json")
    def test_financial_firm_skips_llm_and_ccc(self, mock_chat):
        state = initial_state("JPM", "2026")
        state["is_financial"] = True
        result = operations_node(state)
        mock_chat.assert_not_called()
        packet = result["operations_packet"]
        self.assertEqual(packet["cash_conversion"]["view"], "insufficient")
        self.assertEqual(packet["working_capital"]["view"], "insufficient")
        self.assertIsNone(packet["metrics"]["ccc_days"])

    def test_inventory_without_cogs_does_not_zero_ccc(self):
        income = {
            datetime(2025, 12, 31): {"Total Revenue": 100.0},
        }
        balance = {
            datetime(2025, 12, 31): {
                "Accounts Receivable": 10.0,
                "Inventory": 20.0,
                "Accounts Payable": 5.0,
            },
        }
        metrics = measure_operating_cycle(income, balance)
        self.assertIsNone(metrics["dio_days"])
        self.assertIsNone(metrics["ccc_days"])


class SalesToCapitalMenuTests(unittest.TestCase):
    def test_empty_operations_packet_only_allows_base(self):
        self.assertEqual(allowed_stc_choices(None), ["base"])
        self.assertEqual(allowed_stc_choices({}), ["base"])

    def test_invented_number_in_architect_reason_falls_back(self):
        state = _bundle_state()
        bundle = build_assumption_bundle(state, risk_free_rate=0.04)
        menus = build_choice_menus(
            bundle,
            {},
            risk_free_rate=0.04,
            operations_packet=_absorbing_packet(),
        )
        proposed = apply_architect_choices(
            bundle,
            menus,
            {"sales_to_capital": "heavy"},
            reasons={"sales_to_capital": "Working capital will absorb $50bn this cycle."},
            ledger_text="Python working-capital view is absorbing.",
        )
        self.assertEqual(proposed["architect_choices"]["sales_to_capital"], "base")

    def test_absorbing_evidence_unlocks_heavy_not_light(self):
        allowed = allowed_stc_choices(_absorbing_packet())
        self.assertIn("heavy", allowed)
        self.assertNotIn("light", allowed)

    def test_releasing_evidence_unlocks_light(self):
        allowed = allowed_stc_choices(_releasing_packet())
        self.assertIn("light", allowed)
        self.assertNotIn("heavy", allowed)

    def test_architect_stretch_without_reason_falls_back_to_base(self):
        state = _bundle_state()
        bundle = build_assumption_bundle(state, risk_free_rate=0.04)
        menus = build_choice_menus(
            bundle,
            {},
            risk_free_rate=0.04,
            operations_packet=_releasing_packet(),
        )
        self.assertIn("light", menus["allowed"]["sales_to_capital"])
        proposed = apply_architect_choices(
            bundle, menus, {"sales_to_capital": "light"}
        )
        self.assertEqual(proposed["architect_choices"]["sales_to_capital"], "base")
        self.assertAlmostEqual(
            proposed["sales_to_capital"], menus["sales_to_capital"]["base"]
        )

    def test_architect_heavy_with_reason_applies_menu_value(self):
        state = _bundle_state()
        bundle = build_assumption_bundle(state, risk_free_rate=0.04)
        menus = build_choice_menus(
            bundle,
            {},
            risk_free_rate=0.04,
            operations_packet=_absorbing_packet(),
        )
        proposed = apply_architect_choices(
            bundle,
            menus,
            {"sales_to_capital": "heavy"},
            reasons={
                "sales_to_capital": "Python working-capital view is absorbing."
            },
        )
        self.assertEqual(proposed["architect_choices"]["sales_to_capital"], "heavy")
        self.assertAlmostEqual(
            proposed["sales_to_capital"], menus["sales_to_capital"]["heavy"]
        )
        self.assertLess(
            proposed["sales_to_capital"], menus["sales_to_capital"]["base"]
        )

    def test_bundle_overwrites_classifier_stc_with_observed_ratio(self):
        bundle = build_assumption_bundle(_bundle_state(), risk_free_rate=0.04)
        cycle = bundle["baseline"]["operating_cycle"]
        self.assertAlmostEqual(
            bundle["baseline"]["sales_to_capital"],
            cycle["observed_sales_to_capital"],
        )
        self.assertAlmostEqual(cycle["implied_sales_to_capital"], 20.0 / 32.0, places=4)

    def test_report_pack_uses_architect_stc_rationale(self):
        state = _bundle_state()
        state["dcf_overrides"] = {
            "architect_choices": {"sales_to_capital": "heavy"},
            "rationales": {
                "sales_to_capital": "Architect chose heavy from CCC lengthening."
            },
            "decisions": [
                {
                    "key": "sales_to_capital",
                    "action": "accept",
                    "reason": "WC absorbing",
                }
            ],
        }
        state["valuation_summary"] = {
            "applied_dcf_assumptions": {
                "high_growth_rate": 0.08,
                "high_growth_years": 5,
                "transition_years": 5,
                "terminal_margin": 0.15,
                "terminal_growth_rate": 0.025,
                "sales_to_capital": 1.2,
            },
            "valuation_date_inputs": {
                "share_price": 10.0,
                "shares_outstanding": 10.0,
                "beta": 1.0,
                "risk_free_rate": 0.04,
                "market_equity_risk_premium": 0.05,
                "company_specific_risk_premium": 0.0,
                "total_debt": 0.0,
                "cash_and_equivalents": 0.0,
            },
            "wacc": {"wacc": 0.08, "weight_equity": 1.0, "weight_debt": 0.0},
            "cost_of_equity": 0.09,
            "cost_of_debt": {"after_tax_cost_of_debt": 0.0, "method_used": "n/a"},
            "dcf": {"intrinsic_value_per_share": 12.0},
        }
        state["operations_packet"] = {
            "metrics": {"ccc_days": 40.0, "nwc_to_sales": 0.23},
            "cash_conversion": {
                "view": "lengthening",
                "evidence": "CCC lengthened versus last year.",
            },
            "working_capital": {
                "view": "absorbing",
                "evidence": "NWC absorbed cash as revenue grew.",
            },
        }
        pack = build_report_pack(dict(state))
        items = {row["item"]: row for row in pack["assumptions"]}
        self.assertIn("Architect chose heavy", items["Sales-to-capital (high-growth)"]["justification"])
        self.assertEqual(items["Cash conversion cycle"]["value"], "40.0 days")
        self.assertIn("NWC / sales", items)


class GraphWiringTests(unittest.TestCase):
    def test_operations_joins_industry_macro_into_router(self):
        graph = build_research_graph()
        edges = {(edge.source, edge.target) for edge in graph.get_graph().edges}
        self.assertIn("operations", graph.nodes)
        self.assertIn("growth_path", graph.nodes)
        self.assertIn(("operations", "growth_path"), edges)
        self.assertIn(("industry_macro", "growth_path"), edges)
        self.assertIn(("company_products", "growth_path"), edges)
        self.assertIn(("growth_path", "valuation_router"), edges)


if __name__ == "__main__":
    unittest.main()
