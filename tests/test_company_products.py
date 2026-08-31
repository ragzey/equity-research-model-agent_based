"""Company/products packet grounding."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from equity_research.agents.company_products import (
    company_products_node,
    normalize_company_products_packet,
)
from equity_research.graphs.defaults import initial_state


class CompanyProductsTests(unittest.TestCase):
    def test_drops_invented_product_names_and_dates(self):
        quote = "We sell smartphones, wearables, and related services."
        packet = normalize_company_products_packet(
            {
                "products": ["iPhone 20 Ultra", "smartphones"],
                "mix": {"view": "rising", "evidence": quote},
                "pricing_power": {"view": "strong", "evidence": "Made-up pricing power."},
                "firm_catalysts": [
                    {
                        "event": "Launch on 2027-06-01",
                        "evidence": "Aliens buy the phones.",
                        "assumption": "high_growth_rate",
                    }
                ],
                "narrative": "Buy now at https://evil.test",
            },
            ledger_text=quote,
            filing_text=quote,
            allowed_tickers=["AAPL"],
        )
        self.assertEqual(packet["products"], ["smartphones"])
        self.assertEqual(packet["mix"]["view"], "rising")
        self.assertEqual(packet["pricing_power"]["view"], "insufficient")
        self.assertEqual(packet["firm_catalysts"], [])
        self.assertEqual(packet["narrative"], "")

    def test_web_excerpt_can_name_a_product_and_attach_source(self):
        quote = "We sell smartphones, wearables, and related services."
        web = (
            "The Vision Pro headset is now shipping in additional countries "
            "this quarter according to the company."
        )
        url = "https://www.apple.com/newsroom/vision-pro"
        packet = normalize_company_products_packet(
            {
                "products": ["Vision Pro headset", "smartphones"],
                "mix": {"view": "rising", "evidence": web, "source_url": url},
            },
            ledger_text=f"{quote}\n{web}",
            filing_text=f"{quote}\n{web}",
            allowed_tickers=["AAPL"],
            source_catalog=[
                {
                    "url": url,
                    "excerpt": web,
                    "tier": "first_party",
                    "used_for": "firm",
                }
            ],
            allowed_urls=[url],
        )
        self.assertEqual(packet["products"], ["Vision Pro headset", "smartphones"])
        self.assertEqual(packet["mix"]["view"], "rising")
        self.assertEqual(packet["mix"]["source_url"], url)

    @patch("equity_research.agents.company_products.chat_json")
    def test_node_writes_packet(self, mock_chat):
        quote = "We sell smartphones, wearables, and related services."
        mock_chat.return_value = {
            "products": ["smartphones"],
            "mix": {"view": "stable", "evidence": quote},
            "pricing_power": {"view": "neutral", "evidence": quote},
            "firm_catalysts": [],
            "narrative": quote,
        }
        state = initial_state("AAPL", "2026")
        state["sec_filing_sections"] = {"item_1": quote, "item_1a": "", "item_7": ""}
        state["peer_comparison_matrix"] = {
            "target": "AAPL",
            "competitors": ["MSFT"],
            "metrics": {
                "AAPL": {"operating_margin_pct": 30.0, "revenue_growth_yoy_pct": 2.0},
                "MSFT": {"operating_margin_pct": 42.0, "revenue_growth_yoy_pct": 15.0},
            },
        }
        result = company_products_node(state)
        packet = result["company_products_packet"]
        self.assertEqual(packet["products"], ["smartphones"])
        self.assertEqual(packet["mix"]["view"], "stable")
        mock_chat.assert_called_once()
