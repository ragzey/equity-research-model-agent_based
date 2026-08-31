"""Resolve company names to listed tickers without inventing symbols."""

from __future__ import annotations

import unittest

from equity_research.tools.sec_api import resolve_listed_symbol


_MAP = {
    "0": {"ticker": "AAPL", "title": "Apple Inc.", "cik_str": 320193},
    "1": {
        "ticker": "APLE",
        "title": "Apple Hospitality REIT, Inc.",
        "cik_str": 1,
    },
    "2": {"ticker": "MSFT", "title": "MICROSOFT CORP", "cik_str": 789019},
    "3": {"ticker": "GOOGL", "title": "Alphabet Inc.", "cik_str": 1652044},
    "4": {"ticker": "AAAA", "title": "Zephyr Gadgets Inc.", "cik_str": 2},
    "5": {
        "ticker": "ZEPH",
        "title": "Zephyr Hospitality REIT, Inc.",
        "cik_str": 3,
    },
}


class ResolveListedSymbolTests(unittest.TestCase):
    def test_exact_ticker_is_unchanged(self):
        self.assertEqual(resolve_listed_symbol("AAPL", _MAP), "AAPL")
        self.assertEqual(resolve_listed_symbol("msft", _MAP), "MSFT")

    def test_company_name_maps_to_operating_issuer(self):
        self.assertEqual(resolve_listed_symbol("Apple", _MAP), "AAPL")
        self.assertEqual(resolve_listed_symbol("APPLE", _MAP), "AAPL")
        self.assertEqual(resolve_listed_symbol("Apple Inc.", _MAP), "AAPL")
        self.assertEqual(resolve_listed_symbol("APPLE INC.", _MAP), "AAPL")

    def test_alias_works_when_sec_map_is_empty(self):
        self.assertEqual(resolve_listed_symbol("Apple", {}), "AAPL")
        self.assertEqual(resolve_listed_symbol("APPLE INC.", {}), "AAPL")

    def test_does_not_prefer_reit_homonym(self):
        self.assertEqual(resolve_listed_symbol("Apple", _MAP), "AAPL")
        self.assertEqual(resolve_listed_symbol("Zephyr", _MAP), "AAAA")
        self.assertEqual(resolve_listed_symbol("Zephyr Gadgets Inc.", _MAP), "AAAA")

    def test_unknown_name_returns_none(self):
        self.assertIsNone(resolve_listed_symbol("Not A Listed Co", _MAP))
