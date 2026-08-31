"""URL and citation guards for LLM prose."""

from __future__ import annotations

import unittest

from equity_research.utils.grounding import contains_web_link, has_blocked_link


class GroundingTests(unittest.TestCase):
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

    def test_blocked_unless_on_ledger(self):
        allowed = ["https://www.sec.gov/Archives/edgar/data/1/a.htm"]
        self.assertTrue(has_blocked_link("https://evil.test", allowed))
        self.assertFalse(
            has_blocked_link(
                "Filed at https://www.sec.gov/Archives/edgar/data/1/a.htm",
                allowed,
            )
        )
