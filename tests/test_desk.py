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
from equity_research.utils.llm_client import parse_json_object


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
            "rationales": {"terminal_margin": "policy lift"},
        }
        baseline = {
            "terminal_margin": 0.12,
            "company_specific_risk_premium": 0.0,
            "high_growth_years": 3,
            "high_growth_rate": 0.05,
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
                }
            ],
            mode="llm",
        )
        self.assertAlmostEqual(applied["terminal_margin"], 0.12)
        self.assertEqual(applied["company_specific_risk_premium"], 0.0075)
        self.assertEqual(applied["desk_mode"], "llm")
        self.assertIn("REJECTED", applied["rationales"]["terminal_margin"])

    def test_qualitative_and_competitive_post_handoffs(self):
        qual = _qualitative_handoffs(
            "MSFT",
            [{"section": "Item 1A", "excerpt": "The company faces material litigation."}],
            "brief",
        )
        self.assertTrue(any(item["to_agent"] == "assumption_reviewer" for item in qual))
        self.assertTrue(any(item["kind"] == "risk_finding" for item in qual))

        comp = _competitive_handoffs("TARGET", SMALL_CAP_MATRIX, "outlook")
        kinds = {item["kind"] for item in comp}
        self.assertIn("moat_challenge", kinds)
        self.assertIn("positioning_claim", kinds)
        self.assertIn("assumption_reviewer", format_transcript(comp))

    @patch("equity_research.agents.reviewer.llm_configured", return_value=False)
    def test_reviewer_auto_accepts_without_llm(self, _configured):
        result = valuation_assumption_reviewer_node(_review_state())
        overrides = result["dcf_overrides"]
        self.assertEqual(overrides["desk_mode"], "deterministic")
        self.assertGreater(overrides["terminal_margin"], 0.12)
        self.assertEqual(overrides["company_specific_risk_premium"], 0.0075)
        targets = {item["to_agent"] for item in result["agent_messages"]}
        self.assertEqual(targets, {"quant_analyst", "lead_writer"})

    @patch("equity_research.agents.reviewer.chat_json")
    @patch("equity_research.agents.reviewer.llm_configured", return_value=True)
    def test_reviewer_llm_can_reject_terminal_margin_lift(self, _configured, mock_chat):
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

    def test_graph_still_compiles(self):
        graph = build_research_graph()
        self.assertIsNotNone(graph)


if __name__ == "__main__":
    unittest.main()
