"""Tests for research-desk handoffs and accept/reject review."""

from __future__ import annotations

import unittest
from datetime import datetime
from unittest.mock import patch

from equity_research.agents.competitive import _competitive_handoffs
from equity_research.agents.qualitative import _qualitative_handoffs
from equity_research.agents.reviewer import valuation_assumption_reviewer_node
from equity_research.graphs.defaults import initial_state
from equity_research.graphs.desk import apply_override_decisions, format_transcript
from equity_research.graphs.graph import build_research_graph
from equity_research.utils.llm_client import LLMNotConfiguredError, parse_json_object


SMALL_CAP_MATRIX = {
    "target": "TARGET",
    "competitors": ["PEER1", "PEER2"],
    "metrics": {
        "TARGET": {"operating_margin_pct": 30.0, "market_cap": 1_000_000_000},
        "PEER1": {"operating_margin_pct": 18.0},
        "PEER2": {"operating_margin_pct": 20.0},
    },
}


def _review_state():
    state = initial_state("TARGET", "2025", competitor_tickers=["PEER1", "PEER2"])
    state.update(
        {
            "income_statement": {
                datetime(2024, 12, 31): {
                    "Total Revenue": 100.0,
                    "Operating Income": 25.0,
                },
                datetime(2025, 12, 31): {
                    "Total Revenue": 105.0,
                    "Operating Income": 32.0,
                },
            },
            "peer_comparison_matrix": SMALL_CAP_MATRIX,
            "peer_metadata": {
                "TARGET": {
                    "market_cap": 1_000_000_000,
                    "sector": "Technology",
                }
            },
            "qualitative_analysis_summary": "Material litigation is ongoing.",
            "qualitative_evidence": [
                {
                    "section": "Item 1A",
                    "excerpt": "The company faces material litigation.",
                }
            ],
            "industry_outlook": "The market faces price erosion.",
            "agent_messages": _competitive_handoffs(
                "TARGET",
                SMALL_CAP_MATRIX,
                "The market faces price erosion.",
            ),
        }
    )
    return state


class DeskProtocolTests(unittest.TestCase):
    def test_parse_json_object_strips_fences(self):
        parsed = parse_json_object("```json\n{\"action\": \"reject\"}\n```")
        self.assertEqual(parsed, {"action": "reject"})

    def test_reject_reverts_to_baseline_and_cannot_invent_a_new_number(self):
        proposed = {
            "terminal_margin": 0.15,
            "company_specific_risk_premium": 0.0075,
            "high_growth_years": 2,
            "high_growth_rate": 0.08,
            "terminal_growth_rate": 0.025,
            "rationales": {"terminal_margin": "policy lift"},
        }
        baseline = {
            "terminal_margin": 0.12,
            "company_specific_risk_premium": 0.0,
            "high_growth_years": 3,
            "high_growth_rate": 0.05,
            "terminal_growth_rate": 0.02,
        }
        applied = apply_override_decisions(
            proposed,
            baseline,
            [
                {
                    "key": "terminal_margin",
                    "action": "reject",
                    "reason": "Margin is not a moat.",
                    "value": 0.99,
                },
                {"key": "company_specific_risk_premium", "action": "accept", "reason": "ok"},
                {"key": "high_growth_years", "action": "accept", "reason": "ok"},
                {"key": "high_growth_rate", "action": "accept", "reason": "ok"},
            ],
            mode="llm",
        )
        self.assertAlmostEqual(applied["terminal_margin"], 0.12)
        self.assertEqual(applied["company_specific_risk_premium"], 0.0075)
        self.assertEqual(applied["desk_mode"], "llm")
        self.assertIn("REJECTED", applied["rationales"]["terminal_margin"])

    def test_missing_reviewer_action_reverts_to_baseline(self):
        proposed = {
            "terminal_margin": 0.15,
            "company_specific_risk_premium": 0.0075,
            "high_growth_years": 2,
            "high_growth_rate": 0.08,
            "terminal_growth_rate": 0.025,
            "rationales": {},
        }
        baseline = {
            "terminal_margin": 0.12,
            "company_specific_risk_premium": 0.0,
            "high_growth_years": 3,
            "high_growth_rate": 0.05,
            "terminal_growth_rate": 0.02,
        }
        applied = apply_override_decisions(proposed, baseline, [], mode="llm")
        self.assertAlmostEqual(applied["terminal_margin"], 0.12)
        self.assertEqual(applied["company_specific_risk_premium"], 0.0)
        self.assertEqual(applied["high_growth_years"], 3)
        self.assertAlmostEqual(applied["high_growth_rate"], 0.05)
        self.assertTrue(all(row["action"] == "reject" for row in applied["decisions"]))

    def test_qualitative_and_competitive_post_handoffs(self):
        qual = _qualitative_handoffs(
            "MSFT",
            [{"section": "Item 1A", "excerpt": "The company faces material litigation."}],
            "brief",
        )
        self.assertTrue(any(item["to_agent"] == "assumption_reviewer" for item in qual))
        self.assertTrue(any(item["to_agent"] == "lead_writer" for item in qual))
        self.assertTrue(any(item["kind"] == "risk_finding" for item in qual))

        comp = _competitive_handoffs("TARGET", SMALL_CAP_MATRIX, "outlook")
        kinds = {item["kind"] for item in comp}
        self.assertIn("moat_challenge", kinds)
        self.assertIn("positioning_claim", kinds)
        self.assertIn("assumption_reviewer", format_transcript(comp))

    @patch("equity_research.agents.reviewer.chat_json")
    def test_reviewer_requires_llm_decisions(self, mock_chat):
        mock_chat.side_effect = LLMNotConfiguredError("need key")
        with self.assertRaises(LLMNotConfiguredError):
            valuation_assumption_reviewer_node(_review_state())

    @patch("equity_research.agents.reviewer.chat_json")
    def test_reviewer_llm_can_reject_terminal_margin_lift(self, mock_chat):
        mock_chat.return_value = {
            "decisions": [
                {
                    "key": "terminal_margin",
                    "action": "reject",
                    "reason": "Competitive analyst: margin gap is not a moat.",
                },
                {
                    "key": "company_specific_risk_premium",
                    "action": "accept",
                    "reason": "Litigation excerpt supports the premium.",
                },
                {"key": "high_growth_years", "action": "accept", "reason": "ok"},
                {"key": "high_growth_rate", "action": "accept", "reason": "ok"},
            ],
            "notes_to_quant": "Use baseline terminal margin.",
            "notes_to_writer": "Disclose the rejected moat lift.",
        }
        result = valuation_assumption_reviewer_node(_review_state())
        overrides = result["dcf_overrides"]
        self.assertEqual(overrides["desk_mode"], "llm")
        self.assertAlmostEqual(overrides["terminal_margin"], 0.12)
        self.assertEqual(overrides["company_specific_risk_premium"], 0.0075)
        mock_chat.assert_called_once()
        kwargs = mock_chat.call_args.kwargs
        self.assertTrue(kwargs.get("required"))

    def test_graph_still_compiles(self):
        graph = build_research_graph()
        self.assertIsNotNone(graph)
        self.assertIn("independent_auditor", graph.nodes)
        self.assertIn("lead_writer", graph.nodes)
        self.assertIn("industry_macro", graph.nodes)
        self.assertIn("company_products", graph.nodes)
        self.assertIn("operations", graph.nodes)
        self.assertIn("assumption_architect", graph.nodes)


if __name__ == "__main__":
    unittest.main()
