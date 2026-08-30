"""Peer harvest ranking: industry first, no invented tickers, ETFs out."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from equity_research.agents.competitive import select_comparable_set
from equity_research.graphs.defaults import initial_state
from equity_research.tools.peer_discovery import (
    apply_named_picks,
    clip_rejected_picks,
    rank_peer_candidates,
    score_peer,
)
from equity_research.utils.llm_client import LLMNotConfiguredError


class PeerRankingTests(unittest.TestCase):
    def test_same_industry_beats_high_yahoo_score_in_another_industry(self):
        ranked = rank_peer_candidates(
            "TJX",
            [
                {"ticker": "WMT", "yahoo_score": 0.9, "sources": ["yahoo_recommendations"]},
                {"ticker": "ROST", "yahoo_score": 0.2, "sources": ["yahoo_recommendations"]},
            ],
            {
                "TJX": {
                    "industry": "Apparel Retail",
                    "sector": "Consumer Cyclical",
                    "market_cap": 100_000_000_000,
                },
                "WMT": {
                    "industry": "Discount Stores",
                    "sector": "Consumer Defensive",
                    "market_cap": 400_000_000_000,
                },
                "ROST": {
                    "industry": "Apparel Retail",
                    "sector": "Consumer Cyclical",
                    "market_cap": 50_000_000_000,
                },
            },
        )
        self.assertEqual(ranked["selected"][0], "ROST")
        self.assertIn("ROST", ranked["selected"])

    def test_etfs_are_rejected(self):
        ranked = rank_peer_candidates(
            "TJX",
            [{"ticker": "XLY", "yahoo_score": 1.0, "sources": ["yahoo_recommendations"]}],
            {
                "TJX": {"industry": "Apparel Retail"},
                "XLY": {
                    "quote_type": "ETF",
                    "company_name": "Consumer Discretionary Select Sector SPDR",
                },
            },
        )
        self.assertEqual(ranked["selected"], [])
        self.assertTrue(ranked["rejected"])

    def test_apply_named_picks_cannot_invent_a_ticker(self):
        candidates = [{"ticker": "ROST"}, {"ticker": "BURL"}]
        self.assertEqual(
            apply_named_picks(candidates, ["ROST", "FAKE", "SPY", "BURL"]),
            ["ROST", "BURL"],
        )

    def test_rejected_picks_cannot_invent_a_ticker(self):
        candidates = [{"ticker": "ROST"}, {"ticker": "BURL"}]
        clipped = clip_rejected_picks(
            candidates,
            [
                {"ticker": "ROST", "reason": "too close"},
                {"ticker": "BURL", "reason": "see www.example.com/note"},
                {"ticker": "NOTREAL", "reason": "invented"},
                "SPY",
            ],
        )
        self.assertEqual(
            clipped,
            [
                {"ticker": "ROST", "reason": "too close"},
                {"ticker": "BURL", "reason": ""},
            ],
        )

    def test_score_prefers_industry_match(self):
        target = {"industry": "Apparel Retail", "sector": "Consumer Cyclical"}
        same = score_peer(target, {"industry": "Apparel Retail", "sector": "Consumer Cyclical"})
        other = score_peer(target, {"industry": "Discount Stores", "sector": "Consumer Defensive"})
        self.assertGreater(same, other)

    def test_does_not_dilute_industry_comps_with_other_sectors(self):
        ranked = rank_peer_candidates(
            "TJX",
            [
                {"ticker": "ROST", "yahoo_score": 0.2, "sources": ["yahoo_recommendations"]},
                {"ticker": "DG", "yahoo_score": 0.9, "sources": ["yahoo_recommendations"]},
            ],
            {
                "TJX": {
                    "industry": "Apparel Retail",
                    "sector": "Consumer Cyclical",
                },
                "ROST": {
                    "industry": "Apparel Retail",
                    "sector": "Consumer Cyclical",
                },
                "DG": {
                    "industry": "Discount Stores",
                    "sector": "Consumer Defensive",
                },
            },
        )
        self.assertEqual(ranked["selected"], ["ROST"])
        self.assertNotIn("DG", ranked["selected"])


class CompetitiveSelectionTests(unittest.TestCase):
    @patch("equity_research.agents.competitive.chat_json")
    def test_pinned_peers_are_not_replaced(self, mock_chat):
        mock_chat.return_value = {
            "rationale": "Operator pinned off-price apparel comps."
        }
        state = initial_state("TJX", "2026", competitor_tickers=["ROST", "BURL"])
        result = select_comparable_set(state)
        self.assertEqual(result["mode"], "pinned")
        self.assertEqual(result["selected"], ["ROST", "BURL"])
        self.assertIn("off-price", result["rationale"])
        mock_chat.assert_called_once()

    @patch("equity_research.agents.competitive.chat_json")
    def test_auto_select_requires_llm_rationale(self, mock_chat):
        mock_chat.side_effect = LLMNotConfiguredError("need key")
        state = initial_state("TJX", "2026")
        state["discovered_peers"] = {
            "candidates": [
                {"ticker": "ROST", "yahoo_score": 0.4, "sources": ["yahoo_recommendations"]},
                {"ticker": "BURL", "yahoo_score": 0.3, "sources": ["yahoo_recommendations"]},
            ],
            "sources_used": ["yahoo_recommendations"],
        }
        state["peer_metadata"] = {
            "TJX": {
                "industry": "Apparel Retail",
                "sector": "Consumer Cyclical",
                "market_cap": 100_000_000_000,
            },
            "ROST": {
                "industry": "Apparel Retail",
                "sector": "Consumer Cyclical",
                "market_cap": 50_000_000_000,
            },
            "BURL": {
                "industry": "Apparel Retail",
                "sector": "Consumer Cyclical",
                "market_cap": 15_000_000_000,
            },
        }
        with self.assertRaises(LLMNotConfiguredError):
            select_comparable_set(state)

    @patch("equity_research.agents.competitive.chat_json")
    def test_llm_picks_are_clipped_to_harvested_names(self, mock_chat):
        mock_chat.return_value = {
            "selected": ["ROST", "NOTREAL", "BURL"],
            "rejected": [{"ticker": "WMT", "reason": "Different industry."}],
            "rationale": "Keep the off-price peers.",
        }
        state = initial_state("TJX", "2026")
        state["discovered_peers"] = {
            "candidates": [
                {"ticker": "ROST", "yahoo_score": 0.4, "sources": ["yahoo_recommendations"]},
                {"ticker": "BURL", "yahoo_score": 0.3, "sources": ["yahoo_recommendations"]},
                {"ticker": "WMT", "yahoo_score": 0.2, "sources": ["yahoo_recommendations"]},
            ]
        }
        state["peer_metadata"] = {
            "TJX": {"industry": "Apparel Retail", "sector": "Consumer Cyclical"},
            "ROST": {"industry": "Apparel Retail", "sector": "Consumer Cyclical"},
            "BURL": {"industry": "Apparel Retail", "sector": "Consumer Cyclical"},
            "WMT": {"industry": "Discount Stores", "sector": "Consumer Defensive"},
        }
        result = select_comparable_set(state)
        self.assertEqual(result["mode"], "llm")
        self.assertEqual(result["selected"], ["ROST", "BURL"])
        self.assertNotIn("NOTREAL", result["selected"])


if __name__ == "__main__":
    unittest.main()
