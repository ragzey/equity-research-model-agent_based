"""Allowlisted URL helpers and web-research parsers."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from equity_research.tools.web_research import (
    classify_host,
    fetch_web_research,
    first_party_hosts,
    html_to_text,
    parse_finnhub_news,
    parse_tavily_results,
    parse_yahoo_news,
    research_queries,
)
from equity_research.utils.grounding import (
    contains_web_link,
    extract_urls,
    has_blocked_link,
    has_unledgered_link,
    normalize_url,
    strip_urls,
    url_on_ledger,
)


class GroundingUrlTests(unittest.TestCase):
    def test_scheme_and_www_count_as_links(self):
        self.assertTrue(contains_web_link("See https://evil.test/note"))
        self.assertTrue(contains_web_link("Filed at http://example.com"))
        self.assertTrue(contains_web_link("Source: www.example.com/10k"))
        self.assertTrue(contains_web_link("ftp://files.example.com/x"))

    def test_item_prefixes_and_plain_prose_are_not_links(self):
        self.assertFalse(contains_web_link("[Item 1A] The company faces litigation."))
        self.assertFalse(contains_web_link("Worldwide demand remains in line."))
        self.assertFalse(contains_web_link(""))
        self.assertFalse(contains_web_link(None))

    def test_ledger_urls_are_allowed_invented_urls_are_not(self):
        text = "Demand is firm per https://www.reuters.com/markets/phones"
        allowed = ["https://www.reuters.com/markets/phones"]
        self.assertFalse(has_blocked_link(text, allowed))
        self.assertTrue(has_unledgered_link("See https://evil.test/tam", allowed))
        self.assertTrue(has_blocked_link("See https://evil.test/tam", allowed))
        self.assertTrue(has_blocked_link("See https://evil.test/tam", None))

    def test_strip_and_normalize(self):
        self.assertEqual(
            strip_urls("Quote here https://www.reuters.com/a more quote"),
            "Quote here more quote",
        )
        self.assertEqual(
            normalize_url("https://WWW.Reuters.com/tech/phones/"),
            "https://reuters.com/tech/phones",
        )
        self.assertTrue(
            url_on_ledger(
                "https://www.reuters.com/tech/phones",
                ["https://reuters.com/tech/phones/"],
            )
        )
        self.assertEqual(
            extract_urls("See www.sec.gov/Archives/x and https://ft.com/a"),
            ["www.sec.gov/Archives/x", "https://ft.com/a"],
        )


class WebResearchTests(unittest.TestCase):
    def test_allowlist_accepts_reuters_and_issuer_ir(self):
        ok, tier = classify_host("https://www.reuters.com/markets/us")
        self.assertTrue(ok)
        self.assertEqual(tier, "high_quality")
        extra = first_party_hosts("https://www.apple.com")
        ok, tier = classify_host("https://investor.apple.com/news", extra)
        self.assertTrue(ok)
        self.assertEqual(tier, "first_party")
        rejected, _ = classify_host("https://seekingalpha.com/article/1")
        self.assertFalse(rejected)

    def test_tavily_parser_drops_off_list_hosts(self):
        rows = parse_tavily_results(
            {
                "results": [
                    {
                        "url": "https://www.reuters.com/technology/phones",
                        "title": "Phones",
                        "content": "Global smartphone shipments rose in the latest quarter.",
                    },
                    {
                        "url": "https://seekingalpha.com/article/secret-tam",
                        "title": "TAM",
                        "content": "The TAM is $1 trillion.",
                    },
                ]
            },
            query="smartphone market",
            used_for="market",
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["url"], "https://www.reuters.com/technology/phones")
        self.assertEqual(rows[0]["used_for"], "market")

    def test_yahoo_and_finnhub_parsers(self):
        yahoo = parse_yahoo_news(
            [
                {
                    "content": {
                        "title": "Apple demand",
                        "summary": "iPhone demand held up in China this quarter.",
                        "canonicalUrl": {"url": "https://www.cnbc.com/apple-demand"},
                        "provider": {"displayName": "CNBC"},
                    }
                }
            ]
        )
        self.assertEqual(len(yahoo), 1)
        self.assertEqual(yahoo[0]["publisher"], "CNBC")
        finnhub = parse_finnhub_news(
            [
                {
                    "headline": "Fed holds rates",
                    "summary": "The Federal Reserve held the funds rate unchanged.",
                    "url": "https://www.federalreserve.gov/newsevents/pressreleases/x.htm",
                    "source": "Federal Reserve",
                }
            ]
        )
        self.assertEqual(len(finnhub), 1)
        self.assertEqual(finnhub[0]["tier"], "high_quality")

    def test_html_to_text_strips_scripts(self):
        text = html_to_text(
            "<html><script>alert(1)</script><p>Shipments rose 4 percent.</p></html>"
        )
        self.assertIn("Shipments rose 4 percent", text)
        self.assertNotIn("alert", text)

    def test_research_queries_include_market_and_firm(self):
        queries = research_queries(
            ticker="AAPL",
            company_name="Apple",
            sector="Technology",
            industry="Consumer Electronics",
            year=2026,
        )
        joined = " ".join(query for query, _ in queries)
        self.assertIn("Consumer Electronics", joined)
        self.assertTrue(any(used == "market" for _, used in queries))
        self.assertTrue(any(used == "firm" for _, used in queries))

    def test_writer_driver_table_prints_fetched_hyperlink(self):
        from equity_research.agents.writer import _industry_driver_table

        table = _industry_driver_table(
            {
                "category_growth": {
                    "view": "above_history",
                    "evidence": "Global smartphone shipments are expected to grow.",
                    "source_url": "https://www.reuters.com/technology/smartphones-outlook",
                }
            }
        )
        self.assertIn(
            "[source](https://www.reuters.com/technology/smartphones-outlook)",
            table,
        )
