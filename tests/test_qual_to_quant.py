"""Tests for bounded qualitative-to-quantitative translation."""

import unittest
from datetime import datetime
from unittest.mock import patch

from equity_research.agents.reviewer import valuation_assumption_reviewer_node
from equity_research.graphs.defaults import initial_state
from equity_research.tools.qual_to_quant import (
    analyze_competitive_moat,
    assess_qualitative_risks,
    evaluate_growth_horizon,
    generate_valuation_overrides,
)


PEER_MATRIX = {
    "target": "TARGET",
    "competitors": ["PEER1", "PEER2"],
    "metrics": {
        "TARGET": {"operating_margin_pct": 30.0, "market_cap": 20_000_000_000},
        "PEER1": {"operating_margin_pct": 18.0},
        "PEER2": {"operating_margin_pct": 20.0},
    },
}


class QualToQuantTests(unittest.TestCase):
    def test_reads_current_peer_matrix_shape(self):
        margin, rationale = analyze_competitive_moat(0.30, PEER_MATRIX, 0.15)
        self.assertAlmostEqual(margin, 0.18)
        self.assertIn("peer median", rationale)

    def test_loss_making_advantage_cannot_pull_terminal_below_floor(self):
        matrix = {
            "target": "NBIS",
            "competitors": ["PEER1", "PEER2"],
            "metrics": {
                "NBIS": {"operating_margin_pct": -0.22},
                "PEER1": {"operating_margin_pct": -48.0},
                "PEER2": {"operating_margin_pct": -20.0},
            },
        }
        margin, rationale = analyze_competitive_moat(-0.0022, matrix, 0.12)
        self.assertAlmostEqual(margin, 0.12)
        self.assertIn("classifier floor", rationale)

    def test_company_risk_premium_is_direct_and_bounded(self):
        market_erp, company_premium, _ = assess_qualitative_risks(
            "Material litigation and a supply chain disruption.",
            base_equity_risk_premium=0.05,
        )
        self.assertEqual(market_erp, 0.05)
        self.assertEqual(company_premium, 0.0125)

    def test_saturation_compresses_but_does_not_extend(self):
        years, _ = evaluate_growth_horizon("The market faces price erosion.", 5)
        self.assertEqual(years, 3)

    def test_llm_outlook_cannot_drive_horizon_or_csrp(self):
        result = generate_valuation_overrides(
            target_margin=0.15,
            qualitative_summary="",
            industry_outlook=(
                "The market faces price erosion, secular decline, "
                "and material litigation."
            ),
            default_high_growth_years=5,
        )
        self.assertEqual(result["high_growth_years"], 5)
        self.assertEqual(result["company_specific_risk_premium"], 0.0)

    @patch("equity_research.agents.reviewer.chat_json")
    def test_reviewer_builds_auditable_overrides(self, mock_chat):
        state = initial_state("TARGET", "2025", competitor_tickers=["PEER1", "PEER2"])
        state.update(
            {
                "income_statement": {
                    datetime(2024, 12, 31): {
                        "Total Revenue": 8_000_000_000.0,
                        "Operating Income": 2_000_000_000.0,
                    },
                    datetime(2025, 12, 31): {
                        "Total Revenue": 9_600_000_000.0,
                        "Operating Income": 2_880_000_000.0,
                    },
                },
                "peer_comparison_matrix": PEER_MATRIX,
                "peer_metadata": {
                    "TARGET": {
                        "market_cap": 20_000_000_000,
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
            }
        )
        mock_chat.return_value = {
            "decisions": [
                {"key": "terminal_margin", "action": "accept", "reason": "ok"},
                {
                    "key": "company_specific_risk_premium",
                    "action": "accept",
                    "reason": "litigation",
                },
                {"key": "high_growth_years", "action": "accept", "reason": "ok"},
                {"key": "high_growth_rate", "action": "accept", "reason": "ok"},
            ],
            "notes_to_quant": "Use the bounded candidates.",
            "notes_to_writer": "Disclose CSRP.",
        }
        result = valuation_assumption_reviewer_node(state)["dcf_overrides"]
        self.assertEqual(result["company_specific_risk_premium"], 0.0075)
        self.assertIn("rationales", result)
        self.assertIn("high_growth_rate", result)
        self.assertIn("high_growth_rate", result["rationales"])


if __name__ == "__main__":
    unittest.main()
