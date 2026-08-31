"""Industry/macro packet and bounded assumption-architect menus."""

from __future__ import annotations

import unittest
from datetime import datetime
from unittest.mock import patch

from equity_research.agents.assumption_architect import assumption_architect_node
from equity_research.agents.industry_macro import (
    _ground_narrative,
    industry_macro_node,
    normalize_industry_macro_packet,
    overlay_ledger_industry_views,
)
from equity_research.agents.reviewer import valuation_assumption_reviewer_node
from equity_research.graphs.defaults import initial_state
from equity_research.graphs.graph import build_research_graph
from equity_research.tools.assumption_menus import (
    allowed_growth_choices,
    allowed_year_choices,
    apply_architect_choices,
    build_assumption_bundle,
    build_choice_menus,
    clip_terminal_growth,
    economy_terminal_cap,
    policy_terminal_growth,
    resolve_labeled_choice,
)


LARGE_CAP_MATRIX = {
    "target": "TPR",
    "competitors": ["RL", "CPRI"],
    "metrics": {
        "TPR": {
            "operating_margin_pct": 18.0,
            "market_cap": 20_000_000_000,
            "revenue_growth_yoy_pct": 3.0,
        },
        "RL": {"operating_margin_pct": 16.0, "revenue_growth_yoy_pct": 4.0},
        "CPRI": {"operating_margin_pct": 14.0, "revenue_growth_yoy_pct": 2.0},
    },
}


def _tpr_state():
    state = initial_state("TPR", "2026", competitor_tickers=["RL", "CPRI"])
    state.update(
        {
            "income_statement": {
                datetime(2024, 12, 31): {
                    "Total Revenue": 100.0,
                    "Operating Income": 18.0,
                },
                datetime(2025, 12, 31): {
                    "Total Revenue": 103.0,
                    "Operating Income": 18.5,
                },
            },
            "peer_comparison_matrix": LARGE_CAP_MATRIX,
            "peer_metadata": {
                "TPR": {
                    "market_cap": 20_000_000_000,
                    "sector": "Consumer Cyclical",
                    "industry": "Luxury Goods",
                }
            },
            "qualitative_analysis_summary": (
                "The company is expanding digital and international retail."
            ),
            "qualitative_evidence": [
                {
                    "section": "Item 7",
                    "excerpt": "The company is expanding digital and international retail.",
                }
            ],
            "sec_filing_sections": {
                "item_7": "The company is expanding digital and international retail."
            },
        }
    )
    return state


def _high_growth_state():
    state = _tpr_state()
    state["income_statement"] = {
        datetime(2024, 12, 31): {
            "Total Revenue": 8_000_000_000.0,
            "Operating Income": 1_440_000_000.0,
        },
        datetime(2025, 12, 31): {
            "Total Revenue": 9_760_000_000.0,
            "Operating Income": 1_760_000_000.0,
        },
    }
    return state


def _constructive_packet():
    return {
        "category_growth": {
            "view": "above_history",
            "evidence": "The company is expanding digital and international retail.",
        },
        "pricing_power": {"view": "neutral", "evidence": "Peer margins are similar."},
        "cycle": {"view": "upswing", "evidence": "Peer revenue growth is positive."},
        "macro": {
            "rates_view": "neutral",
            "fx_demand_view": "insufficient",
            "risk_free_rate": 0.04,
            "evidence": "10-year Treasury is 4.00%.",
        },
        "demand_inflection": {
            "direction": "positive",
            "evidence": "The company is expanding digital and international retail.",
        },
        "narrative": "Demand is improving versus trailing growth.",
    }


class MenuPolicyTests(unittest.TestCase):
    def test_insufficient_packet_cannot_select_high_growth(self):
        self.assertEqual(
            allowed_growth_choices({"category_growth": {"view": "insufficient"}}),
            ["low", "base"],
        )
        self.assertNotIn(
            "high",
            allowed_growth_choices(
                {"category_growth": {"view": "above_history", "evidence": ""}}
            ),
        )

    def test_evidenced_above_history_unlocks_high_band(self):
        self.assertIn("high", allowed_growth_choices(_constructive_packet()))

    def test_scale_up_lifecycle_unlocks_high_without_packet(self):
        self.assertIn(
            "high",
            allowed_growth_choices({}, firm_type="Scale-up High-Growth"),
        )
        self.assertNotIn(
            "low",
            allowed_growth_choices({}, firm_type="Scale-up High-Growth"),
        )
        self.assertNotIn(
            "low",
            allowed_growth_choices({}, firm_type="High-Growth Large-Cap"),
        )
        self.assertIn(
            "extend",
            allowed_year_choices({}, firm_type="Scale-up High-Growth"),
        )
        self.assertNotIn(
            "compress",
            allowed_year_choices({}, firm_type="Scale-up High-Growth"),
        )
        self.assertIn(
            "extend",
            allowed_year_choices({}, firm_type="High-Growth Large-Cap"),
        )
        self.assertNotIn(
            "compress",
            allowed_year_choices({}, firm_type="High-Growth Large-Cap"),
        )
        self.assertNotIn(
            "low",
            allowed_growth_choices({}, firm_type="Mature Large-Cap"),
        )
        self.assertNotIn(
            "compress",
            allowed_year_choices({}, firm_type="Mature Large-Cap"),
        )
        self.assertEqual(
            allowed_year_choices({}, firm_type="Mature Large-Cap"),
            ["base"],
        )

    def test_numeric_llm_growth_falls_back_to_base(self):
        self.assertEqual(resolve_labeled_choice(0.99, ["low", "base", "high"]), "base")
        self.assertEqual(resolve_labeled_choice("high", ["low", "base"]), "base")

    def test_terminal_growth_is_clipped_to_the_economy_ceiling_not_2p5(self):
        self.assertAlmostEqual(clip_terminal_growth(0.09, 0.04), 0.035)
        self.assertAlmostEqual(clip_terminal_growth(-0.01, 0.04), 0.015)
        self.assertAlmostEqual(clip_terminal_growth(None, 0.04), 0.025)
        self.assertAlmostEqual(clip_terminal_growth(0.025, 0.025), 0.020)

    def test_terminal_growth_depends_on_firm_and_macro(self):
        rf = 0.0472
        mature = policy_terminal_growth(rf, firm_type="Mature Large-Cap")
        high_growth = policy_terminal_growth(
            rf, firm_type="High-Growth Large-Cap"
        )
        self.assertAlmostEqual(mature, rf - 0.015)
        self.assertAlmostEqual(high_growth, rf - 0.010)
        self.assertGreater(high_growth, mature)
        self.assertLessEqual(high_growth, economy_terminal_cap(rf))
        self.assertLess(economy_terminal_cap(rf), rf)

        constructive = policy_terminal_growth(
            rf,
            firm_type="Mature Large-Cap",
            packet=_constructive_packet(),
        )
        self.assertGreater(constructive, mature)
        self.assertLessEqual(constructive, economy_terminal_cap(rf))

        hostile = policy_terminal_growth(
            rf,
            firm_type="High-Growth Large-Cap",
            packet={
                "cycle": {
                    "view": "downswing",
                    "evidence": "The cycle is a downswing in the category.",
                },
                "demand_inflection": {
                    "direction": "negative",
                    "evidence": "Demand inflection is negative in the category.",
                },
            },
        )
        self.assertLess(hostile, high_growth)

    def test_high_growth_firm_can_select_economy_terminal_g(self):
        bundle = build_assumption_bundle(
            _high_growth_state(), risk_free_rate=0.04
        )
        self.assertEqual(bundle["baseline"]["firm_type"], "High-Growth Large-Cap")
        menus = build_choice_menus(bundle, {}, risk_free_rate=0.04)
        self.assertIn("high", menus["allowed"]["terminal_growth_rate"])
        proposed = apply_architect_choices(
            bundle,
            menus,
            {"terminal_growth_rate": "high"},
            reasons={
                "terminal_growth_rate": "High-growth lifecycle still compounding above the economy."
            },
        )
        self.assertAlmostEqual(
            proposed["terminal_growth_rate"], economy_terminal_cap(0.04)
        )
        self.assertGreater(proposed["terminal_growth_rate"], 0.025)

    def test_mature_without_evidence_cannot_pick_terminal_high(self):
        bundle = build_assumption_bundle(_tpr_state(), risk_free_rate=0.04)
        menus = build_choice_menus(bundle, {}, risk_free_rate=0.04)
        self.assertNotIn("high", menus["allowed"]["terminal_growth_rate"])
        proposed = apply_architect_choices(
            bundle, menus, {"terminal_growth_rate": "high"}
        )
        self.assertAlmostEqual(
            proposed["terminal_growth_rate"],
            bundle["proposed"]["terminal_growth_rate"],
        )

    def test_architect_applies_high_label_inside_band(self):
        bundle = build_assumption_bundle(_tpr_state(), risk_free_rate=0.04)
        menus = build_choice_menus(
            bundle, _constructive_packet(), risk_free_rate=0.04
        )
        proposed = apply_architect_choices(
            bundle,
            menus,
            {"high_growth_rate": "high", "high_growth_years": "extend"},
            reasons={
                "high_growth_rate": "Category growth is above history with filing evidence.",
                "high_growth_years": "Demand inflection is positive in the packet.",
            },
        )
        low, high = bundle["baseline"]["high_growth_rate_bounds"]
        self.assertAlmostEqual(proposed["high_growth_rate"], high)
        self.assertEqual(proposed["high_growth_years"], min(7, bundle["baseline"]["high_growth_years"] + 2))
        self.assertLessEqual(proposed["high_growth_rate"], high)
        self.assertGreaterEqual(proposed["high_growth_rate"], low)
        self.assertEqual(proposed["desk_mode"], "architect")

    def test_disallowed_high_label_falls_back_to_base(self):
        bundle = build_assumption_bundle(_tpr_state(), risk_free_rate=0.04)
        menus = build_choice_menus(bundle, {}, risk_free_rate=0.04)
        base = bundle["proposed"]["high_growth_rate"]
        proposed = apply_architect_choices(
            bundle, menus, {"high_growth_rate": "high"}
        )
        self.assertAlmostEqual(proposed["high_growth_rate"], base)
        self.assertEqual(proposed["architect_choices"]["high_growth_rate"], "base")

    def test_missing_allow_list_does_not_unlock_high(self):
        bundle = build_assumption_bundle(_tpr_state(), risk_free_rate=0.04)
        menus = build_choice_menus(
            bundle, _constructive_packet(), risk_free_rate=0.04
        )
        self.assertIn("high", menus["high_growth_rate"])
        menus = dict(menus)
        menus["allowed"] = {}
        proposed = apply_architect_choices(
            bundle,
            menus,
            {"high_growth_rate": "high"},
            reasons={
                "high_growth_rate": "Category growth is above history with filing evidence."
            },
        )
        self.assertEqual(proposed["architect_choices"]["high_growth_rate"], "base")
        self.assertAlmostEqual(
            proposed["high_growth_rate"], bundle["proposed"]["high_growth_rate"]
        )


class PacketAndNodeTests(unittest.TestCase):
    def test_normalize_drops_urls_unknown_views_and_invented_quotes(self):
        packet = normalize_industry_macro_packet(
            {
                "category_growth": {
                    "view": "above_history",
                    "evidence": "Aliens control the handbag cycle this decade.",
                },
                "narrative": "Buy TPR now at https://evil.test",
                "high_growth_rate": 0.40,
            },
            risk_free_rate=0.041,
            ledger_text="The company is expanding digital and international retail. 4.10%",
        )
        self.assertEqual(packet["category_growth"]["view"], "insufficient")
        self.assertEqual(packet["category_growth"]["evidence"], "")
        self.assertEqual(packet["narrative"], "")
        self.assertEqual(packet["macro"]["risk_free_rate"], 0.041)
        self.assertNotIn("high_growth_rate", packet)
        self.assertNotIn(
            "high",
            allowed_growth_choices(packet),
        )

    def test_normalize_drops_www_host_without_scheme(self):
        packet = normalize_industry_macro_packet(
            {
                "narrative": "Demand is strong per www.example.com/outlook.",
            },
            risk_free_rate=0.041,
            ledger_text="The company is expanding digital and international retail. 4.10%",
        )
        self.assertEqual(packet["narrative"], "")

    def test_narrative_rejects_substring_numbers(self):
        text = _ground_narrative(
            "Cash conversion is 12 days.",
            "Cash conversion cycle is 120.0 days.",
            ["TPR"],
        )
        self.assertEqual(text, "")
        kept = _ground_narrative(
            "Cash conversion cycle is 120.0 days.",
            "Cash conversion cycle is 120.0 days.",
            ["TPR"],
        )
        self.assertIn("120.0", kept)

    def test_qualitative_prose_cannot_unlock_high_band(self):
        invented = "Gen Z TAM will double the handbag market this decade."
        quote = "The company is expanding digital and international retail."
        packet = normalize_industry_macro_packet(
            {"category_growth": {"view": "above_history", "evidence": invented}},
            risk_free_rate=0.04,
            ledger_text=f"{invented}\n{quote}",
            filing_text=quote,
        )
        self.assertEqual(packet["category_growth"]["view"], "insufficient")
        self.assertNotIn("high", allowed_growth_choices(packet))

    def test_peer_json_cannot_unlock_high_band(self):
        quote = "The company is expanding digital and international retail."
        peer = '{"TPR": {"operating_margin_pct": 18.0, "revenue_growth_yoy_pct": 3.0}}'
        packet = normalize_industry_macro_packet(
            {"category_growth": {"view": "above_history", "evidence": peer}},
            risk_free_rate=0.04,
            ledger_text=peer,
            filing_text=quote,
        )
        self.assertEqual(packet["category_growth"]["view"], "insufficient")

    def test_narrative_drops_unsourced_tam_and_invented_tickers(self):
        quote = "The company is expanding digital and international retail."
        packet = normalize_industry_macro_packet(
            {
                "narrative": (
                    "The TAM is $80 billion. LVMH will take share. "
                    f"{quote}"
                )
            },
            risk_free_rate=0.04,
            ledger_text=quote,
            filing_text=quote,
            allowed_tickers=["TPR"],
        )
        self.assertEqual(packet["narrative"], "")

    def test_ledger_quote_keeps_above_history(self):
        quote = "The company is expanding digital and international retail."
        packet = normalize_industry_macro_packet(
            {
                "category_growth": {"view": "above_history", "evidence": quote},
                "pricing_power": {"view": "neutral", "evidence": quote},
                "cycle": {"view": "mid", "evidence": quote},
                "macro": {
                    "rates_view": "neutral",
                    "fx_demand_view": "insufficient",
                    "evidence": quote,
                },
                "demand_inflection": {"direction": "positive", "evidence": quote},
                "narrative": quote,
            },
            risk_free_rate=0.04,
            ledger_text=quote,
        )
        self.assertEqual(packet["category_growth"]["view"], "above_history")
        self.assertIn("high", allowed_growth_choices(packet))

    def test_allowlisted_web_excerpt_unlocks_above_history_with_hyperlink(self):
        quote = (
            "Global smartphone shipments are expected to grow faster than the "
            "company's recent revenue history this year."
        )
        url = "https://www.reuters.com/technology/smartphones-outlook"
        packet = normalize_industry_macro_packet(
            {
                "category_growth": {
                    "view": "above_history",
                    "evidence": quote,
                    "source_url": url,
                }
            },
            risk_free_rate=0.04,
            ledger_text=quote,
            filing_text=quote,
            source_catalog=[
                {
                    "url": url,
                    "title": "Smartphone outlook",
                    "publisher": "reuters.com",
                    "excerpt": quote,
                    "tier": "high_quality",
                    "used_for": "market",
                }
            ],
        )
        self.assertEqual(packet["category_growth"]["view"], "above_history")
        self.assertEqual(packet["category_growth"]["source_url"], url)
        self.assertIn("high", allowed_growth_choices(packet))

    def test_invented_source_url_is_replaced_by_matching_fetched_page(self):
        quote = (
            "Global smartphone shipments are expected to grow faster than the "
            "company's recent revenue history this year."
        )
        real = "https://www.reuters.com/technology/smartphones-outlook"
        packet = normalize_industry_macro_packet(
            {
                "category_growth": {
                    "view": "above_history",
                    "evidence": quote,
                    "source_url": "https://evil.test/gartner-tam",
                }
            },
            risk_free_rate=0.04,
            ledger_text=quote,
            filing_text=quote,
            source_catalog=[
                {
                    "url": real,
                    "excerpt": quote,
                    "used_for": "market",
                }
            ],
        )
        self.assertEqual(packet["category_growth"]["source_url"], real)
        self.assertNotIn("evil.test", packet["category_growth"]["source_url"])

    def test_ledger_overlay_fills_apple_style_insufficient_packet(self):
        empty = normalize_industry_macro_packet(
            {},
            risk_free_rate=0.041,
            ledger_text="Historical revenue CAGR is 3.0%",
            filing_text="Risk factors include competition.",
        )
        self.assertEqual(empty["category_growth"]["view"], "insufficient")
        filled = overlay_ledger_industry_views(
            empty,
            historical_cagr=0.03,
            consensus={
                "growth": 0.06,
                "source": "yahoo_revenue_estimate_+1y",
            },
            peer_snapshot={
                "target": "AAPL",
                "metrics": {
                    "AAPL": {
                        "operating_margin_pct": 30.0,
                        "revenue_growth_yoy_pct": 2.0,
                    },
                    "MSFT": {
                        "operating_margin_pct": 42.0,
                        "revenue_growth_yoy_pct": 15.0,
                    },
                    "GOOGL": {
                        "operating_margin_pct": 28.0,
                        "revenue_growth_yoy_pct": 10.0,
                    },
                },
            },
            risk_free_rate=0.041,
        )
        self.assertEqual(filled["category_growth"]["view"], "above_history")
        self.assertEqual(filled["category_growth"]["source"], "ledger")
        self.assertEqual(filled["pricing_power"]["view"], "weak")
        self.assertEqual(filled["cycle"]["view"], "upswing")
        self.assertEqual(filled["macro"]["rates_view"], "neutral")
        self.assertIn("4.10%", filled["macro"]["evidence"])
        self.assertEqual(filled["demand_inflection"]["direction"], "none")
        self.assertIn("high", allowed_growth_choices(filled))
        self.assertTrue(filled["narrative"])

    def test_ledger_cycle_overrides_consumer_article_downswing(self):
        filled = overlay_ledger_industry_views(
            {
                "category_growth": {
                    "view": "in_line",
                    "evidence": "Historical revenue CAGR is 6.5%.",
                    "source": "ledger",
                },
                "cycle": {
                    "view": "downswing",
                    "evidence": "middle-class consumers are growing more tight-fisted",
                },
                "demand_inflection": {
                    "direction": "negative",
                    "evidence": "middle-class consumers are growing more tight-fisted",
                },
            },
            historical_cagr=0.065,
            consensus=None,
            peer_snapshot={
                "target": "TJX",
                "metrics": {
                    "TJX": {"revenue_growth_yoy_pct": 5.4, "operating_margin_pct": 10.9},
                    "ROST": {"revenue_growth_yoy_pct": 7.0, "operating_margin_pct": 17.6},
                    "ULTA": {"revenue_growth_yoy_pct": 4.0, "operating_margin_pct": 12.5},
                },
            },
            risk_free_rate=0.0476,
        )
        self.assertIn(filled["cycle"]["view"], {"mid", "upswing"})
        self.assertEqual(filled["demand_inflection"]["direction"], "none")
        self.assertNotIn(
            "low",
            allowed_growth_choices(filled, firm_type="Mature Large-Cap"),
        )
        self.assertNotIn(
            "compress",
            allowed_year_choices(filled, firm_type="Mature Large-Cap"),
        )

    def test_trailing_consensus_does_not_unlock_high_band(self):
        filled = overlay_ledger_industry_views(
            {"category_growth": {"view": "insufficient", "evidence": ""}},
            historical_cagr=0.03,
            consensus={
                "growth": 0.20,
                "source": "yahoo_info_revenueGrowth_trailing",
            },
            peer_snapshot=None,
            risk_free_rate=0.04,
        )
        self.assertEqual(filled["category_growth"]["view"], "in_line")
        self.assertNotIn("high", allowed_growth_choices(filled))

    def test_hyper_cagr_without_forward_is_scale_up_category(self):
        empty = normalize_industry_macro_packet(
            {},
            risk_free_rate=0.04,
            ledger_text="Historical revenue CAGR is 239.7%",
            filing_text="Risk factors include competition.",
        )
        self.assertEqual(empty["category_growth"]["view"], "insufficient")
        filled = overlay_ledger_industry_views(
            empty,
            historical_cagr=2.397,
            consensus=None,
            peer_snapshot=None,
            risk_free_rate=0.04,
        )
        self.assertEqual(filled["category_growth"]["view"], "above_history")
        self.assertEqual(filled["category_growth"]["source"], "ledger")
        self.assertIn("high", allowed_growth_choices(filled))

    @patch("equity_research.agents.industry_macro.fetch_ten_year_treasury_yield", return_value=0.04)
    @patch("equity_research.agents.industry_macro.chat_json")
    def test_industry_macro_node_writes_packet(self, mock_chat, _rf):
        mock_chat.return_value = {
            "category_growth": {
                "view": "above_history",
                "evidence": "The company is expanding digital and international retail.",
            },
            "pricing_power": {"view": "neutral", "evidence": "Peer margins are similar."},
            "cycle": {"view": "mid", "evidence": "Peer growth is mid-single digit."},
            "macro": {
                "rates_view": "neutral",
                "fx_demand_view": "insufficient",
                "evidence": "10-year Treasury is 4.00%.",
            },
            "demand_inflection": {
                "direction": "positive",
                "evidence": "The company is expanding digital and international retail.",
            },
            "narrative": "The company is expanding digital and international retail.",
        }
        result = industry_macro_node(_tpr_state())
        self.assertEqual(
            result["industry_macro_packet"]["category_growth"]["view"],
            "above_history",
        )
        self.assertEqual(
            result["industry_outlook"],
            "The company is expanding digital and international retail.",
        )
        mock_chat.assert_called_once()
        self.assertTrue(mock_chat.call_args.kwargs.get("required"))

    @patch("equity_research.agents.assumption_architect.fetch_ten_year_treasury_yield", return_value=0.04)
    @patch("equity_research.agents.assumption_architect.chat_json")
    def test_architect_ignores_typed_growth_rate(self, mock_chat, _rf):
        state = _tpr_state()
        state["industry_macro_packet"] = _constructive_packet()
        mock_chat.return_value = {
            "high_growth_rate": 0.99,
            "high_growth_years": "base",
            "terminal_growth_rate": "base",
            "terminal_margin": "baseline",
            "company_specific_risk_premium": "none",
        }
        result = assumption_architect_node(state)
        overrides = result["dcf_overrides"]
        bundle = build_assumption_bundle(state, risk_free_rate=0.04)
        self.assertAlmostEqual(
            overrides["high_growth_rate"],
            bundle["proposed"]["high_growth_rate"],
        )
        self.assertNotAlmostEqual(overrides["high_growth_rate"], 0.99)
        self.assertEqual(overrides["architect_choices"]["high_growth_rate"], "base")

    @patch("equity_research.agents.reviewer.chat_json")
    def test_reviewer_can_veto_architect_high_band(self, mock_chat):
        state = _tpr_state()
        bundle = build_assumption_bundle(state, risk_free_rate=0.04)
        menus = build_choice_menus(
            bundle, _constructive_packet(), risk_free_rate=0.04
        )
        state["dcf_overrides"] = apply_architect_choices(
            bundle,
            menus,
            {"high_growth_rate": "high"},
            reasons={
                "high_growth_rate": "Above-history category growth with filing evidence."
            },
        )
        state["industry_macro_packet"] = _constructive_packet()
        mock_chat.return_value = {
            "decisions": [
                {"key": "terminal_margin", "action": "reject", "reason": "no moat"},
                {
                    "key": "company_specific_risk_premium",
                    "action": "reject",
                    "reason": "no premium",
                },
                {"key": "high_growth_years", "action": "accept", "reason": "ok"},
                {
                    "key": "high_growth_rate",
                    "action": "reject",
                    "reason": "stay on trailing baseline",
                },
                {"key": "terminal_growth_rate", "action": "accept", "reason": "ok"},
            ],
            "notes_to_quant": "Use classifier growth.",
            "notes_to_writer": "Architect high band was vetoed.",
        }
        result = valuation_assumption_reviewer_node(state)["dcf_overrides"]
        self.assertAlmostEqual(
            result["high_growth_rate"],
            bundle["baseline"]["high_growth_rate"],
        )
        self.assertIn("REJECTED", result["rationales"]["high_growth_rate"])
        self.assertEqual(result["architect_choices"]["high_growth_rate"], "high")

    def test_graph_wires_industry_and_architect(self):
        graph = build_research_graph()
        edges = {(edge.source, edge.target) for edge in graph.get_graph().edges}
        self.assertIn("industry_macro", graph.nodes)
        self.assertIn("operations", graph.nodes)
        self.assertIn("company_products", graph.nodes)
        self.assertIn("growth_path", graph.nodes)
        self.assertIn("assumption_architect", graph.nodes)
        self.assertIn(("industry_macro", "growth_path"), edges)
        self.assertIn(("company_products", "growth_path"), edges)
        self.assertIn(("operations", "growth_path"), edges)
        self.assertIn(("growth_path", "valuation_mix"), edges)
        self.assertIn(("valuation_mix", "valuation_router"), edges)
        self.assertIn(("assumption_architect", "valuation_assumption_reviewer"), edges)


if __name__ == "__main__":
    unittest.main()
