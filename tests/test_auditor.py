"""Independent auditor: per-agent checks, Python-safe corrections, frozen math."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from equity_research.agents.independent_auditor import (
    align_memo_to_pack,
    clip_peer_selection,
    clip_qualitative_evidence,
    independent_auditor_node,
    novel_tickers,
)
from equity_research.graphs.defaults import initial_state
from equity_research.graphs.graph import build_research_graph


def _dcf_pass() -> dict:
    return {
        "enterprise_value": 100.0,
        "equity_value": 100.0,
        "intrinsic_value_per_share": 10.0,
        "terminal_wacc_applied": 0.08,
        "terminal_growth_rate_applied": 0.03,
        "terminal_value_share_of_enterprise_value": 0.50,
        "projections": [{"year": 1, "fcff": 12.0}],
    }


def _pack(**overrides) -> dict:
    pack = {
        "key_data": [{"label": "Ticker", "value": "TJX"}],
        "ticker": "TJX",
        "company_name": "TJX",
        "share_price": 90.0,
        "fair_value": 100.0,
        "price_target_12m": 108.0,
        "dcf_value": 100.0,
        "wacc": 0.08,
        "model_rating": "Hold",
        "valuation_method": "corporate_fcff",
        "cost_of_equity": 0.10,
    }
    pack.update(overrides)
    return pack


def _pass_llm(**overrides) -> dict:
    payload = {
        "competitive": {
            "action": "pass",
            "issues": [],
            "corrected_outlook": None,
            "corrected_rationale": None,
        },
        "qualitative": {
            "action": "pass",
            "issues": [],
            "corrected_summary": None,
        },
        "reviewer": {"action": "pass", "issues": []},
        "quant": {
            "action": "pass",
            "issues": [],
            "discount_rate": 0.99,
            "wacc": 0.99,
            "price_target_12m": 1.0,
        },
        "writer": {
            "action": "pass",
            "issues": [],
            "corrected_qualitative_narrative": None,
            "corrected_industry_outlook": None,
            "corrected_desk_synthesis": None,
        },
    }
    payload.update(overrides)
    return payload


def _base_state(tmp: Path) -> dict:
    state = initial_state("TJX", "2026", competitor_tickers=["ROST"])
    filing = "The company faces material litigation in off-price retail."
    memo = tmp / "TJX_2026-01-01_memo.md"
    memo.write_text(
        (
            "# TJX\n\n"
            "**Model-implied BUY** with a 12-month price target of $1.00 "
            "versus a last price of $1.00.\n\n"
            "## Executive summary\n\n"
            "As of 2026-01-01, the model's blended fair value is $1.00 per share. "
            "produces a 12-month price target of $1.00. "
            "Using a ±15% band around the current price, the model band is **Buy**.\n\n"
            "Discounting at a 1.00% WACC produces $1.00 per share.\n"
            "- WACC: 1.00%\n\n"
            "## Company and industry\n\n"
            "The company faces material litigation in off-price retail.\n\n"
            "### Industry outlook\n\n"
            "TJX competes with ROST in off-price retail.\n\n"
            "## Sources and references\n\n"
            "Yahoo Finance.\n"
        ),
        encoding="utf-8",
    )
    sidecar = tmp / "TJX_2026-01-01_gui.json"
    sidecar.write_text(json.dumps({"ticker": "TJX"}), encoding="utf-8")
    state.update(
        {
            "discount_rate": 0.08,
            "calculated_dcf_value": 100.0,
            "is_math_verified": True,
            "review_action": "pass",
            "industry_outlook": "TJX competes with ROST in off-price retail.",
            "qualitative_analysis_summary": (
                "The company faces material litigation in off-price retail."
            ),
            "sec_filing_sections": {"item_1a": filing, "item_7": filing},
            "discovered_peers": {
                "candidates": [{"ticker": "ROST"}, {"ticker": "GPS"}]
            },
            "peer_selection": {
                "selected": ["ROST", "FAKE"],
                "rejected": [],
                "rationale": "ROST is harvested.",
            },
            "peer_comparison_matrix": {
                "target": "TJX",
                "competitors": ["ROST", "FAKE"],
                "metrics": {},
            },
            "qualitative_evidence": [
                {
                    "section": "Item 1A",
                    "excerpt": "The company faces material litigation",
                },
                {
                    "section": "Item 1A",
                    "excerpt": "Aliens control the supply chain.",
                },
            ],
            "valuation_summary": {
                "valuation_method": "corporate_fcff",
                "cost_of_equity": 0.10,
                "dcf": _dcf_pass(),
                "valuation_date_inputs": {
                    "cash_and_equivalents": 20.0,
                    "total_debt": 20.0,
                    "shares_outstanding": 10.0,
                    "share_price": 90.0,
                },
                "report_pack": _pack(),
            },
            "final_equity_memo_path": str(memo),
        }
    )
    return state


class ClipAndAlignTests(unittest.TestCase):
    def test_clip_invented_peer(self):
        state = initial_state("TJX", "2026", competitor_tickers=["ROST"])
        state["discovered_peers"] = {"candidates": [{"ticker": "ROST"}, {"ticker": "GPS"}]}
        state["peer_selection"] = {"selected": ["ROST", "FAKE"], "rejected": []}
        state["peer_comparison_matrix"] = {
            "target": "TJX",
            "competitors": ["ROST", "FAKE"],
        }
        fix, findings = clip_peer_selection(state)
        self.assertIsNotNone(fix)
        self.assertEqual(fix["peer_selection"]["selected"], ["ROST"])
        self.assertEqual(fix["peer_selection"]["auditor_clipped"], ["FAKE"])
        self.assertEqual(fix["matrix"]["competitors"], ["ROST"])
        self.assertEqual(findings[0]["code"], "INVENTED_PEER")
        self.assertTrue(findings[0]["corrected"])

    def test_drop_unsourced_quote(self):
        state = initial_state("TJX", "2026")
        state["sec_filing_sections"] = {
            "item_1a": "The company faces material litigation in California."
        }
        state["qualitative_evidence"] = [
            {
                "section": "Item 1A",
                "excerpt": "The company faces material litigation",
            },
            {"section": "Item 1A", "excerpt": "We invented this quote."},
        ]
        kept, findings = clip_qualitative_evidence(state)
        self.assertEqual(len(kept), 1)
        self.assertIn("material litigation", kept[0]["excerpt"])
        self.assertEqual(findings[0]["code"], "UNSOURCED_QUOTE")

    def test_align_memo_price_target_and_rating(self):
        memo = (
            "**Model-implied BUY** with a 12-month price target of $1.00 versus a last price of $2.00. "
            "the model's blended fair value is $3.00 per share. "
            "the model band is **Buy**. "
            "Discounting at a 1.00% WACC produces value. "
            "- WACC: 1.00%"
        )
        updated, findings = align_memo_to_pack(
            memo,
            {
                "price_target_12m": 108.0,
                "share_price": 90.0,
                "fair_value": 100.0,
                "model_rating": "Hold",
                "wacc": 0.08,
            },
        )
        self.assertIn("$108.00", updated)
        self.assertIn("$90.00", updated)
        self.assertIn("$100.00", updated)
        self.assertIn("8.00%", updated)
        self.assertIn("model band is **HOLD**", updated)
        self.assertIn("**Model-implied HOLD**", updated)
        self.assertNotIn("$1.00", updated)
        codes = {item["code"] for item in findings}
        self.assertIn("MEMO_PRICE_TARGET", codes)
        self.assertIn("MEMO_RATING", codes)
        self.assertIn("MEMO_WACC", codes)

    def test_novel_tickers_ignore_english_and_catch_invented_symbols(self):
        allowed = {"TJX", "ROST"}
        self.assertEqual(
            novel_tickers("TJX competes with ROST in off-price retail.", allowed),
            [],
        )
        self.assertEqual(
            novel_tickers("The company faces material litigation.", allowed),
            [],
        )
        self.assertEqual(
            novel_tickers("TJX will acquire ZZZZ next year.", allowed),
            ["ZZZZ"],
        )
        self.assertEqual(
            novel_tickers("An FTC inquiry is disclosed in Item 1A.", allowed),
            [],
        )

    def test_align_memo_street_mean_target(self):
        updated, findings = align_memo_to_pack(
            "The Street mean 12-month target is $1.00 versus this model's $108.00.",
            {
                "price_target_12m": 108.0,
                "share_price": 90.0,
                "fair_value": 100.0,
                "model_rating": "Hold",
                "street": {"target_mean": 120.0},
            },
        )
        self.assertIn("$120.00", updated)
        self.assertNotIn("$1.00", updated)
        self.assertIn("MEMO_STREET_PT", {item["code"] for item in findings})

    def test_grounded_text_drops_www_links(self):
        from equity_research.agents.independent_auditor import _grounded_text

        self.assertIsNone(
            _grounded_text(
                "See www.example.com for the filing.",
                allowed={"TJX"},
                background="",
            )
        )
        self.assertEqual(
            _grounded_text(
                "Item 1A discloses material litigation.",
                allowed={"TJX"},
                background="",
            ),
            "Item 1A discloses material litigation.",
        )


class IndependentAuditorNodeTests(unittest.TestCase):
    @patch("equity_research.agents.independent_auditor.write_memo_pdf")
    @patch("equity_research.agents.independent_auditor.chat_json")
    def test_node_corrects_agents_but_ignores_llm_wacc(self, mock_chat, mock_pdf):
        mock_chat.return_value = _pass_llm(
            competitive={
                "action": "correct",
                "issues": ["Invented peer FAKE."],
                "corrected_outlook": "TJX competes with ROST in off-price retail.",
                "corrected_rationale": "ROST is harvested.",
            }
        )
        mock_pdf.return_value = None
        with tempfile.TemporaryDirectory() as raw:
            state = _base_state(Path(raw))
            result = independent_auditor_node(state)
            self.assertNotIn("discount_rate", result)
            self.assertNotIn("calculated_dcf_value", result)
            self.assertNotIn("review_action", result)
            self.assertNotIn("revision_count", result)
            self.assertNotIn("is_math_verified", result)
            self.assertEqual(result["peer_selection"]["selected"], ["ROST"])
            self.assertEqual(result["peer_selection"]["auditor_clipped"], ["FAKE"])
            excerpts = [row["excerpt"] for row in result["qualitative_evidence"]]
            self.assertTrue(any("material litigation" in item for item in excerpts))
            self.assertFalse(any("Aliens" in item for item in excerpts))
            memo = Path(result["final_equity_memo_path"]).read_text(encoding="utf-8")
            self.assertIn("$108.00", memo)
            self.assertIn("model band is **HOLD**", memo)
            self.assertIn("## Independent audit", memo)
            self.assertIn("## Sources and references", memo)
            self.assertLess(
                memo.index("## Independent audit"),
                memo.index("## Sources and references"),
            )
            report = result["audit_report"]
            self.assertFalse(report["model_rewritten"])
            self.assertIn("competitive", report["agents"])
            self.assertIn("qualitative", report["agents"])
            self.assertIn("reviewer", report["agents"])
            self.assertIn("quant", report["agents"])
            self.assertIn("writer", report["agents"])
            sidecar = Path(state["final_equity_memo_path"]).with_name(
                Path(state["final_equity_memo_path"]).name.replace(
                    "_memo.md", "_gui.json"
                )
            )
            saved = json.loads(sidecar.read_text(encoding="utf-8"))
            self.assertIn("audit_report", saved)
        mock_chat.assert_called_once()
        self.assertTrue(mock_chat.call_args.kwargs.get("required"))

    @patch("equity_research.agents.independent_auditor.write_memo_pdf")
    @patch("equity_research.agents.independent_auditor.chat_json")
    def test_rejects_ungrounded_outlook_with_invented_ticker(self, mock_chat, mock_pdf):
        mock_chat.return_value = _pass_llm(
            competitive={
                "action": "correct",
                "issues": [],
                "corrected_outlook": "TJX will acquire ZZZZ next year. https://example.com",
                "corrected_rationale": None,
            }
        )
        mock_pdf.return_value = None
        with tempfile.TemporaryDirectory() as raw:
            state = _base_state(Path(raw))
            result = independent_auditor_node(state)
        self.assertNotIn("industry_outlook", result)

    def test_graph_includes_auditor_after_writer(self):
        graph = build_research_graph()
        self.assertIn("independent_auditor", graph.nodes)
        compiled = graph.get_graph()
        edges = {(edge.source, edge.target) for edge in compiled.edges}
        self.assertIn(("lead_writer", "independent_auditor"), edges)
        self.assertIn(("independent_auditor", "__end__"), edges)
        self.assertIn(("industry_macro", "growth_path"), edges)
        self.assertIn(("company_products", "growth_path"), edges)
        self.assertIn(("operations", "growth_path"), edges)
        self.assertIn(("growth_path", "valuation_mix"), edges)
        self.assertIn(("valuation_mix", "valuation_router"), edges)
        self.assertIn(("assumption_architect", "valuation_assumption_reviewer"), edges)


if __name__ == "__main__":
    unittest.main()
