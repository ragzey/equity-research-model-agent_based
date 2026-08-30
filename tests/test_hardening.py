"""Tests for cache TTL, ISIN/CUSIP harvest, consensus bounds, and Damodaran snapshot."""

from __future__ import annotations

import os
import re
import sqlite3
import tempfile
import unittest
import zlib
from datetime import datetime
from pathlib import Path

import pandas as pd

from equity_research.tools.bond_identifiers import (
    cusip_to_isin,
    extract_bond_isins,
    is_valid_isin,
    merge_isin_lists,
)
from equity_research.tools.cache import cache_get, cache_set
from equity_research.tools.consensus import (
    _as_growth,
    _growth_from_estimate_frame,
    blend_high_growth_rate,
)
from equity_research.tools.debt_analysis import (
    get_synthetic_spread,
    load_damodaran_spread_table,
)
from equity_research.tools.pdf_memo import pdf_download_stem, write_memo_pdf


class CacheTtlTests(unittest.TestCase):
    def setUp(self):
        handle = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
        handle.close()
        self.db_path = handle.name
        self._previous = os.environ.get("CACHE_DB_PATH")
        os.environ["CACHE_DB_PATH"] = self.db_path

    def tearDown(self):
        if self._previous is None:
            os.environ.pop("CACHE_DB_PATH", None)
        else:
            os.environ["CACHE_DB_PATH"] = self._previous
        try:
            os.unlink(self.db_path)
        except OSError:
            pass

    def test_round_trip_and_datetime_key_revival(self):
        payload = {datetime(2024, 12, 31): {"Total Revenue": 100.0}}
        cache_set("yahoo_statements", "TEST", payload)
        loaded = cache_get("yahoo_statements", "TEST", ttl_seconds=3600)
        self.assertIsInstance(loaded, dict)
        key = next(iter(loaded))
        self.assertIsInstance(key, datetime)
        self.assertEqual(key.date(), datetime(2024, 12, 31).date())
        self.assertEqual(loaded[key]["Total Revenue"], 100.0)

    def test_expired_entries_are_ignored(self):
        cache_set("yahoo_statements", "OLD", {"income_statement": {"a": 1}})
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                "UPDATE cache SET stored_at = ? WHERE cache_key = ?",
                ("2000-01-01T00:00:00", "yahoo_statements:OLD"),
            )
            connection.commit()
        self.assertIsNone(cache_get("yahoo_statements", "OLD", ttl_seconds=60))


class BondIdentifierTests(unittest.TestCase):
    def test_known_us_cusip_converts_to_valid_isin(self):
        isin = cusip_to_isin("037833100")
        self.assertEqual(isin, "US0378331005")
        self.assertTrue(is_valid_isin(isin))
        self.assertFalse(is_valid_isin("US0378331006"))

    def test_harvest_requires_debt_context_and_valid_check_digit(self):
        self.assertEqual(extract_bond_isins("Common stock CUSIP 037833100"), [])
        harvested = extract_bond_isins(
            "The senior notes due 2027 (CUSIP 037833100) are unsecured."
        )
        self.assertEqual(harvested, ["US0378331005"])
        self.assertNotIn("USUNSECURED8", harvested)
        self.assertEqual(
            extract_bond_isins("Senior notes due 2027 ISIN US0378331006"),
            [],
        )

    def test_merge_drops_invalid_and_deduplicates(self):
        merged = merge_isin_lists(
            ["US0378331005", "not-an-isin", "US0378331006"],
            ["US0378331005"],
        )
        self.assertEqual(merged, ["US0378331005"])


class ConsensusBoundTests(unittest.TestCase):
    def test_rejects_extreme_growth_and_accepts_percent_form(self):
        self.assertIsNone(_as_growth(0.50))
        self.assertIsNone(_as_growth(-0.60))
        self.assertAlmostEqual(_as_growth(12), 0.12)
        self.assertAlmostEqual(_as_growth(0.12), 0.12)

    def test_prefers_plus_one_year_estimate_column(self):
        frame = pd.DataFrame({"0y": [0.10], "+1y": [0.12]}, index=["growth"])
        self.assertAlmostEqual(_growth_from_estimate_frame(frame), 0.12)

    def test_blend_is_reclipped_to_firm_type_band(self):
        rate, rationale = blend_high_growth_rate(
            0.07,
            (0.02, 0.07),
            {"growth": 0.20, "source": "yahoo_revenue_estimate_+1y"},
        )
        self.assertAlmostEqual(rate, 0.07)
        self.assertIn("yahoo_revenue_estimate_+1y", rationale)
        self.assertIn("not a management forecast", rationale.lower())

        mid, _ = blend_high_growth_rate(
            0.10,
            (0.08, 0.20),
            {"growth": 0.16, "source": "yahoo_revenue_estimate_+1y"},
        )
        self.assertAlmostEqual(mid, 0.13)


class DamodaranSnapshotTests(unittest.TestCase):
    def test_dated_file_matches_legacy_coverage_buckets(self):
        table = load_damodaran_spread_table()
        self.assertEqual(table["as_of"], "2026-01-05")
        self.assertEqual(get_synthetic_spread(9.0), ("AAA", 0.0069))
        self.assertEqual(get_synthetic_spread(7.0), ("AA", 0.0085))
        self.assertEqual(get_synthetic_spread(0.4)[0], "D")


class PdfMemoTests(unittest.TestCase):
    def test_download_stem_uses_ticker(self):
        self.assertEqual(pdf_download_stem(None, "TJX"), "TJX")
        self.assertEqual(pdf_download_stem("TJX.pdf", "MSFT"), "TJX")

    def test_writes_a_nonempty_pdf(self):
        directory = Path(tempfile.mkdtemp())
        path = directory / "memo.pdf"
        markdown = (
            "# Title\n\nHello world.\n\n"
            "| A | B | C | D | E |\n"
            "|---|---:|---:|---:|---:|\n"
            "| 1 | 2 | 3 | 4 | 5 |\n"
        )
        write_memo_pdf(markdown, path)
        self.assertTrue(path.exists())
        self.assertGreater(path.stat().st_size, 0)

    def test_pdf_starts_with_the_research_note(self):
        directory = Path(tempfile.mkdtemp())
        path = directory / "note.pdf"
        markdown = (
            "# THE TJX COMPANIES | TJX | Retail | United States\n\n"
            "## Executive summary\n\nHold at fair value with a 12-month target.\n"
        )
        write_memo_pdf(
            markdown,
            path,
            identity={
                "ticker": "TJX",
                "company_name": "The TJX Companies, Inc.",
                "exchange": "NYQ",
                "industry": "Apparel Retail",
                "country": "United States",
                "rating": "Hold",
                "price_target": 151.84,
                "share_price": 151.5,
                "upside": 0.002,
            },
        )
        payload = path.read_bytes()
        extracted = []
        for match in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", payload, re.S):
            try:
                extracted.append(zlib.decompress(match.group(1)).decode("latin-1", "replace"))
            except zlib.error:
                continue
        text = "\n".join(extracted)
        self.assertIn("Equity Research", text)
        self.assertIn("Executive summary", text)
        self.assertTrue(text.strip().startswith("THE TJX COMPANIES") or "THE TJX COMPANIES" in text[:400])


if __name__ == "__main__":
    unittest.main()
