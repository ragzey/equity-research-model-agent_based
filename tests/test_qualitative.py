"""Tests for SEC section extraction, evidence fallback, and graph compilation."""

import unittest

from unittest.mock import patch

from equity_research.agents.qualitative import _deterministic_summary, qualitative_analyst_node
from equity_research.agents.valuation_router import (
    route_valuation_method,
    unsupported_financial_node,
)
from equity_research.graphs.defaults import initial_state
from equity_research.graphs.graph import graph
from equity_research.tools.sec_api import extract_sec_section, sourced_filing_payload


class QualitativeTests(unittest.TestCase):
    def test_extracts_substantive_section_not_toc(self):
        text = (
            "ITEM 1A. Risk Factors 5\nITEM 1B. Unresolved 12\n"
            "ITEM 1A. RISK FACTORS\n"
            + ("Supply chain disruption could materially affect operations. " * 50)
            + "\nITEM 1B. UNRESOLVED STAFF COMMENTS\n"
            + "ITEM 7. MANAGEMENT DISCUSSION\n"
            + ("Revenue and margin discussion for the period. " * 50)
            + "\nITEM 7A. MARKET RISK\n"
        )
        item_1a = extract_sec_section(text, "1A")
        item_7 = extract_sec_section(text, "7")
        self.assertIsNotNone(item_1a)
        self.assertIsNotNone(item_7)
        self.assertIn("Supply chain disruption", item_1a)
        self.assertIn("Revenue and margin", item_7)

    def test_extracts_item_1_business_without_swallowing_1a(self):
        text = (
            "ITEM 1. Business 3\nITEM 1A. Risk 5\n"
            "ITEM 1. BUSINESS\n"
            + ("We sell smartphones, wearables, and related services. " * 50)
            + "\nITEM 1A. RISK FACTORS\n"
            + ("Supply chain disruption could materially affect operations. " * 50)
            + "\nITEM 1B. UNRESOLVED STAFF COMMENTS\n"
        )
        item_1 = extract_sec_section(text, "1")
        item_1a = extract_sec_section(text, "1A")
        self.assertIsNotNone(item_1)
        self.assertIsNotNone(item_1a)
        self.assertIn("smartphones", item_1)
        self.assertNotIn("Supply chain disruption", item_1)
        self.assertIn("Supply chain disruption", item_1a)

    def test_missing_evidence_never_invokes_historical_knowledge(self):
        summary = _deterministic_summary("TEST", "", "")
        self.assertIn("evidence unavailable", summary.lower())
        self.assertIn("no conclusion drawn", summary.lower())

    def test_safe_harbor_boilerplate_is_not_litigation_evidence(self):
        summary = _deterministic_summary(
            "TEST",
            (
                "These are forward-looking statements under the Private Securities "
                "Litigation Reform Act of 1995."
            ),
            "",
        )
        self.assertIn(
            "No configured high-priority phrase was found",
            summary,
        )

    def test_deterministic_evidence_is_section_tagged(self):
        summary = _deterministic_summary(
            "TEST",
            "A supply chain disruption could materially affect operations.",
            "",
        )
        self.assertIn("[Item 1A]", summary)

    @patch("equity_research.agents.qualitative.chat_text")
    def test_url_in_llm_summary_falls_back_to_evidence(self, mock_chat):
        mock_chat.return_value = (
            "1. REGULATORY & LITIGATION RISK\n"
            "- See www.example.com/10k for the lawsuit.\n"
        )
        state = initial_state("TEST", "2026")
        state["sec_filing_sections"] = {
            "item_1a": "A supply chain disruption could materially affect operations.",
            "item_7": "",
        }
        result = qualitative_analyst_node(state)
        summary = result["qualitative_analysis_summary"]
        self.assertNotIn("www.", summary.lower())
        self.assertIn("[Item 1A]", summary)
        self.assertTrue(result["qualitative_evidence"])

    def test_missing_item_1a_does_not_relabel_item_7(self):
        payload = sourced_filing_payload(
            {
                "item_1a": "",
                "item_7": "Management discussion of results.",
                "filing_url": "https://www.sec.gov/Archives/example.htm",
                "filing_date": "2025-03-01",
                "accession_number": "0001",
            }
        )
        self.assertEqual(payload["sec_filing_sections"]["item_1"], "")
        self.assertEqual(payload["sec_filing_sections"]["item_1a"], "")
        self.assertIn(
            "Management discussion", payload["sec_filing_sections"]["item_7"]
        )
        self.assertEqual(payload["sec_filing_chunks"][0], "")
        self.assertIn("Management discussion", payload["sec_filing_chunks"][2])
        self.assertEqual(
            payload["sec_filing_metadata"]["filing_url"],
            "https://www.sec.gov/Archives/example.htm",
        )

    def test_graph_contains_current_nodes(self):
        node_names = set(graph.get_graph().nodes)
        self.assertTrue(
            {
                "aggregator",
                "competitive_analyst",
                "qualitative_analyst",
                "valuation_assumption_reviewer",
                "assumption_auditor",
                "quant_analyst",
            }.issubset(node_names)
        )
        self.assertIn("unsupported_financial", node_names)
        self.assertNotIn("bank_quant_analyst", node_names)

    def test_financial_firms_skip_fcff(self):
        state = initial_state("JPM", "2026")
        state["is_financial"] = True
        self.assertEqual(route_valuation_method(state), "unsupported_financial")
        result = unsupported_financial_node(state)
        self.assertEqual(result["review_action"], "stop")
        self.assertIsNone(result["calculated_dcf_value"])
        self.assertEqual(
            result["review_findings"][0]["code"],
            "UNSUPPORTED_FINANCIAL_FIRM",
        )


if __name__ == "__main__":
    unittest.main()
