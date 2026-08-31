"""Writer memo helpers: catch merged-function NameErrors before a GUI run."""

from __future__ import annotations

import ast
import inspect
import unittest

from equity_research.agents import writer


class WriterTableTests(unittest.TestCase):
    def test_pnl_forecast_table_is_defined_and_renders(self):
        empty = writer._pnl_forecast_table([])
        self.assertIn("unavailable", empty.lower())
        table = writer._pnl_forecast_table(
            [
                {
                    "year": 2026,
                    "stage": "high_growth",
                    "revenue": 1_000.0,
                    "ebit": 150.0,
                    "operating_margin": 0.15,
                    "net_income": 100.0,
                    "eps": 0.40,
                    "fcff": -50.0,
                }
            ]
        )
        self.assertIn("2026", table)
        self.assertIn("high_growth", table)
        self.assertIn("150.00", table)

    def test_growth_path_table_skips_when_not_applicable(self):
        self.assertEqual(writer._growth_path_table({}), "")
        self.assertEqual(writer._growth_path_table({"applicable": False}), "")
        body = writer._growth_path_table(
            {
                "applicable": True,
                "scale_view": {"view": "still_ramping", "evidence": "P/S 105x"},
                "horizon_view": {"view": "extend", "evidence": "eight years"},
                "reinvestment_path": {"view": "fade", "evidence": "STC 0.60"},
                "margin_path": {"view": "scale", "evidence": "18% floor"},
                "metrics": {"price_to_sales": 105.0, "fade_sales_to_capital": 1.30},
            }
        )
        self.assertIn("### Growth path", body)
        self.assertIn("still_ramping", body)
        self.assertIn("1.30", body)

    def test_valuation_mix_table_renders_python_weights(self):
        body = writer._valuation_mix_table(
            {
                "applicable": True,
                "label": "dcf_heavy",
                "dcf_weight": 0.90,
                "relative_weight": 0.10,
                "mix_view": {"view": "dcf_heavy", "evidence": "P/S 105x"},
                "peer_fit": {"view": "mixed", "evidence": "four selected peers"},
                "relative_role": {
                    "view": "poor_descriptor",
                    "evidence": "trailing EV/EBITDA is a poor descriptor",
                },
                "metrics": {"peer_count": 4, "same_industry_count": 1},
            }
        )
        self.assertIn("### Valuation mix", body)
        self.assertIn("dcf_heavy", body)
        self.assertIn("90.00%", body)

    def test_lead_writer_helper_calls_resolve_on_the_module(self):
        source = inspect.getsource(writer.lead_writer_node)
        tree = ast.parse(source)
        names = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id.startswith("_")
        }
        self.assertIn("_pnl_forecast_table", names)
        self.assertIn("_growth_path_table", names)
        self.assertIn("_valuation_mix_table", names)
        missing = [
            name
            for name in sorted(names)
            if not callable(getattr(writer, name, None))
        ]
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
