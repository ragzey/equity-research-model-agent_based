"""SEC companyfacts parser — no network."""

from __future__ import annotations

import unittest
from datetime import date

from equity_research.tools.sec_facts import statements_from_companyfacts


def _usd_points(*pairs):
    return {
        "units": {
            "USD": [
                {
                    "fy": year,
                    "fp": "FY",
                    "form": "10-K",
                    "val": value,
                    "filed": f"{year}-11-01",
                }
                for year, value in pairs
            ]
        }
    }


class CompanyFactsParserTests(unittest.TestCase):
    def test_builds_yahoo_like_statements(self):
        payload = {
            "facts": {
                "us-gaap": {
                    "Revenues": _usd_points((2023, 100.0), (2024, 110.0)),
                    "OperatingIncomeLoss": _usd_points((2023, 20.0), (2024, 22.0)),
                    "AccountsReceivableNetCurrent": _usd_points((2024, 8.0)),
                    "PropertyPlantAndEquipmentNet": _usd_points((2024, 40.0)),
                    "NetCashProvidedByUsedInOperatingActivities": _usd_points(
                        (2024, 30.0)
                    ),
                }
            }
        }
        statements = statements_from_companyfacts(payload)
        self.assertIsNotNone(statements)
        income = statements["income_statement"]
        self.assertEqual(income["Total Revenue"][date(2024, 12, 31)], 110.0)
        self.assertEqual(income["Operating Income"][date(2024, 12, 31)], 22.0)
        self.assertIn("Accounts Receivable", statements["balance_sheet"])
        self.assertEqual(statements["statement_source"], "sec_companyfacts")

    def test_rejects_payload_without_revenue(self):
        self.assertIsNone(statements_from_companyfacts({"facts": {"us-gaap": {}}}))
