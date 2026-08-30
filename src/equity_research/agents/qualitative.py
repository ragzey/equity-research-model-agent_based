"""Evidence-grounded Qualitative Analyst for SEC Item 1A and Item 7."""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, Iterable, List, Optional

from ..graphs.desk import QUALITATIVE, REVIEWER, WRITER, make_message
from ..graphs.state import EquityResearchState
from ..prompts.desk import QUALITATIVE_SYSTEM
from ..tools.sec_api import fetch_latest_10k_sections
from ..utils.llm_client import LLMCallError, chat_text

logger = logging.getLogger("QualitativeAnalyst")
RISK_TERMS = (
    "antitrust",
    "anti-trust",
    "litigation",
    "lawsuit",
    "ftc",
    "sec investigation",
    "compliance fine",
    "supply chain disruption",
    "material shortage",
    "labor strike",
    "patent expiration",
    "patent cliff",
    "research and development failure",
    "market saturation",
    "price erosion",
    "secular decline",
    "technological obsolescence",
)
BOILERPLATE_EXCLUSIONS = (
    "private securities litigation reform act",
    "forward-looking statements",
)


def _sentences(text: str) -> List[str]:
    return [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?])\s+|\n+", text)
        if sentence.strip()
    ]


def _evidence_snippets(
    texts: Iterable[str],
    terms: Iterable[str] = RISK_TERMS,
    limit: int = 12,
) -> List[str]:
    """Return unique source sentences containing configured risk phrases."""
    matches: List[str] = []
    seen = set()
    for text in texts:
        for sentence in _sentences(text):
            lowered = sentence.lower()
            if any(exclusion in lowered for exclusion in BOILERPLATE_EXCLUSIONS):
                continue
            if any(term in lowered for term in terms):
                normalized = " ".join(sentence.split())
                if normalized not in seen:
                    matches.append(normalized[:800])
                    seen.add(normalized)
                    if len(matches) >= limit:
                        return matches
    return matches


def _structured_evidence(item_1a: str, item_7: str) -> List[Dict[str, str]]:
    """Build compact, section-tagged direct quotes for downstream auditing."""
    evidence: List[Dict[str, str]] = []
    seen = set()
    for section, text in (("Item 1A", item_1a), ("Item 7", item_7)):
        for excerpt in _evidence_snippets((text,), limit=12):
            if excerpt not in seen:
                evidence.append({"section": section, "excerpt": excerpt})
                seen.add(excerpt)
            if len(evidence) >= 12:
                return evidence
    return evidence


def _deterministic_summary(
    ticker: str,
    item_1a: str,
    item_7: str,
) -> str:
    """Evidence-only fallback; never substitutes model memory for a missing filing."""
    evidence = _structured_evidence(item_1a, item_7)
    if not item_1a and not item_7:
        return (
            "1. REGULATORY & LITIGATION RISK:\n"
            "- SEC filing evidence unavailable; no conclusion drawn.\n\n"
            "2. OPERATIONAL & SUPPLY CHAIN RISK:\n"
            "- SEC filing evidence unavailable; no conclusion drawn.\n\n"
            "3. OVERALL STRATEGIC OUTLOOK:\n"
            f"- A sourced qualitative assessment for {ticker} could not be produced."
        )

    regulatory = [
        item
        for item in evidence
        if any(
            term in item["excerpt"].lower()
            for term in (
                "antitrust",
                "anti-trust",
                "litigation",
                "lawsuit",
                "ftc",
                "sec investigation",
                "compliance fine",
            )
        )
    ]
    operational = [item for item in evidence if item not in regulatory]

    def bullets(values: List[Dict[str, str]]) -> str:
        if not values:
            return "- No configured high-priority phrase was found in the extracted sections."
        return "\n".join(
            f"- [{value['section']}] {value['excerpt']}" for value in values[:6]
        )

    return (
        "1. REGULATORY & LITIGATION RISK:\n"
        f"{bullets(regulatory)}\n\n"
        "2. OPERATIONAL & SUPPLY CHAIN RISK:\n"
        f"{bullets(operational)}\n\n"
        "3. OVERALL STRATEGIC OUTLOOK:\n"
        f"- Evidence-only fallback for {ticker}; competitive positioning should be "
        "read alongside peer_comparison_matrix and industry_outlook."
    )


def _tags_for_excerpt(excerpt: str) -> List[str]:
    lowered = excerpt.lower()
    return [term for term in RISK_TERMS if term in lowered]


def _qualitative_handoffs(
    ticker: str,
    evidence: List[Dict[str, str]],
    summary: str,
) -> List[Dict[str, Any]]:
    messages = [
        make_message(
            QUALITATIVE,
            REVIEWER,
            "risk_brief",
            f"{ticker} qualitative brief for assumption review.",
            {"summary": (summary or "")[:4000], "finding_count": len(evidence)},
        ),
        make_message(
            QUALITATIVE,
            WRITER,
            "qualitative_claim",
            (summary or f"{ticker} qualitative assessment.")[:1500],
            {"finding_count": len(evidence)},
        ),
    ]
    for item in evidence[:8]:
        excerpt = item.get("excerpt") or ""
        tags = _tags_for_excerpt(excerpt)
        messages.append(
            make_message(
                QUALITATIVE,
                REVIEWER,
                "risk_finding",
                excerpt[:400],
                {
                    "section": item.get("section"),
                    "excerpt": excerpt[:800],
                    "tags": tags,
                },
            )
        )
    return messages


def _llm_summary(ticker: str, item_1a: str, item_7: str) -> Optional[str]:
    user_prompt = f"""Analyze only the SEC excerpts below for {ticker}. Do not use memory,
outside facts, or unsupported inference. If evidence is absent, say so.

Use exactly these headings:
1. REGULATORY & LITIGATION RISK
2. OPERATIONAL & SUPPLY CHAIN RISK
3. OVERALL STRATEGIC OUTLOOK

Flag antitrust, litigation, lawsuits, FTC/SEC investigations, compliance fines,
supply-chain disruption, material shortages, labor strikes, patent expiration,
patent cliffs, R&D failure, saturation, price erosion, secular decline, and
obsolescence only when the excerpts support them. Include short evidence
phrases, prefix every evidence bullet with [Item 1A] or [Item 7], and
distinguish disclosed risks from events that have occurred.

ITEM 1A - RISK FACTORS:
{item_1a[:25_000]}

ITEM 7 - MD&A:
{item_7[:20_000]}
"""
    return chat_text(
        [
            {"role": "system", "content": QUALITATIVE_SYSTEM},
            {"role": "user", "content": user_prompt},
        ],
        timeout=90,
        required=True,
    )


def qualitative_analyst_node(state: EquityResearchState) -> Dict[str, Any]:
    """Return a sourced qualitative summary as a partial LangGraph state update."""
    ticker = state["ticker"].strip().upper()
    chunks = state.get("sec_filing_chunks") or []
    item_1a = chunks[0] if chunks else ""
    item_7 = chunks[1] if len(chunks) > 1 else ""

    if not item_1a and not item_7:
        sections = fetch_latest_10k_sections(ticker)
        if sections:
            item_1a = sections.get("item_1a") or ""
            item_7 = sections.get("item_7") or ""

    logger.info(
        "Qualitative source for %s | Item 1A: %d chars | Item 7: %d chars",
        ticker,
        len(item_1a),
        len(item_7),
    )

    summary = _llm_summary(ticker, item_1a or "", item_7 or "")
    if not summary:
        raise LLMCallError("Qualitative analyst returned an empty filing assessment.")

    evidence = _structured_evidence(item_1a, item_7)
    business_risks = []
    seen_risks = set()
    for item in evidence:
        for tag in _tags_for_excerpt(item.get("excerpt") or ""):
            if tag not in seen_risks:
                business_risks.append(tag)
                seen_risks.add(tag)

    return {
        "qualitative_analysis_summary": summary,
        "qualitative_evidence": evidence,
        "business_risks": business_risks or None,
        "agent_messages": _qualitative_handoffs(ticker, evidence, summary),
    }
