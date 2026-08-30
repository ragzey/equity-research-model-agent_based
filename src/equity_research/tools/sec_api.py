"""Compliant SEC EDGAR retrieval and evidence-only 10-K section extraction."""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, Optional, Tuple

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

from .cache import TTL_SEC, TTL_TICKER_MAP, cache_get, cache_set

load_dotenv()

logger = logging.getLogger("SECApiTool")

DEFAULT_USER_AGENT = "EquityResearchAgent/1.0 (finance.student@example.com)"
SEC_REQUEST_TIMEOUT = 30
SEC_MIN_INTERVAL_SECONDS = 0.2
MAX_SECTION_CHARS = 50_000

SECTION_BOUNDARIES = {
    "1A": (("ITEM 1A.", "ITEM 1A"), ("ITEM 1B.", "ITEM 1B")),
    "7": (("ITEM 7.", "ITEM 7"), ("ITEM 7A.", "ITEM 7A")),
    "8": (("ITEM 8.", "ITEM 8"), ("ITEM 9.", "ITEM 9", "ITEM 9A")),
}
SECTION_MAX_CHARS = {"1A": MAX_SECTION_CHARS, "7": MAX_SECTION_CHARS, "8": 1_500_000}


def get_headers() -> Dict[str, str]:
    """Return headers required by SEC EDGAR fair-access guidance."""
    user_agent = os.getenv("SEC_USER_AGENT", DEFAULT_USER_AGENT)
    placeholder_markers = ("example.com", "yourname@", "your.email")
    if user_agent == DEFAULT_USER_AGENT or any(
        marker in user_agent.lower() for marker in placeholder_markers
    ):
        logger.warning(
            "SEC_USER_AGENT appears to contain a placeholder. Configure a real contact."
        )
    return {"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"}


def _sec_get(url: str, timeout: Optional[int] = None) -> requests.Response:
    time.sleep(SEC_MIN_INTERVAL_SECONDS)
    response = requests.get(
        url,
        headers=get_headers(),
        timeout=timeout or SEC_REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    return response


def get_cik_for_ticker(ticker: str) -> Optional[str]:
    """Map a ticker to its 10-digit SEC CIK."""
    clean_ticker = ticker.strip().upper()
    cached_cik = cache_get("sec_cik", clean_ticker, TTL_TICKER_MAP)
    if isinstance(cached_cik, str) and cached_cik:
        return cached_cik
    try:
        ticker_data = cache_get("sec_tickers", "all", TTL_TICKER_MAP)
        if not isinstance(ticker_data, dict):
            ticker_data = _sec_get(
                "https://www.sec.gov/files/company_tickers.json"
            ).json()
            cache_set("sec_tickers", "all", ticker_data)
        for entry in ticker_data.values():
            if entry["ticker"] == clean_ticker:
                cik = str(entry["cik_str"]).zfill(10)
                logger.info("CIK resolved: %s -> %s", clean_ticker, cik)
                cache_set("sec_cik", clean_ticker, cik)
                return cik
        logger.error("CIK not found for ticker: %s", clean_ticker)
    except Exception:
        logger.exception("Error fetching CIK mapping for %s", clean_ticker)
    return None


def _clean_html(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for element in soup(["script", "style"]):
        element.decompose()
    text = soup.get_text(separator="\n")
    lines = (line.strip() for line in text.splitlines())
    chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
    return "\n".join(chunk for chunk in chunks if chunk)


def _find_real_section(
    clean_text: str,
    start_keywords: Tuple[str, ...],
    end_keywords: Tuple[str, ...],
    max_chars: int = MAX_SECTION_CHARS,
) -> Optional[str]:
    """Find a substantive section while skipping short table-of-contents entries."""
    upper = clean_text.upper()
    candidates = []
    for keyword in start_keywords:
        offset = 0
        while True:
            index = upper.find(keyword, offset)
            if index == -1:
                break
            candidates.append(index)
            offset = index + len(keyword)

    for start in sorted(set(candidates)):
        end_candidates = [
            upper.find(keyword, start + 10) for keyword in end_keywords
        ]
        valid_ends = [end for end in end_candidates if end > start]
        end = min(valid_ends) if valid_ends else len(clean_text)
        # TOC entries are short; substantive SEC sections are generally much longer.
        if end - start >= 2_000:
            return clean_text[start : min(end, start + max_chars)]
    return None


def extract_sec_section(clean_text: str, section_id: str) -> Optional[str]:
    """Extract Item 1A, Item 7, or Item 8 from already-cleaned 10-K text."""
    normalized = section_id.strip().upper()
    if normalized not in SECTION_BOUNDARIES:
        raise ValueError("section_id must be '1A', '7', or '8'.")
    starts, ends = SECTION_BOUNDARIES[normalized]
    return _find_real_section(
        clean_text,
        starts,
        ends,
        max_chars=SECTION_MAX_CHARS[normalized],
    )


def _download_latest_10k(ticker: str) -> Optional[Dict[str, str]]:
    cik = get_cik_for_ticker(ticker)
    if not cik:
        return None
    try:
        submissions = cache_get("sec_submissions", cik, TTL_TICKER_MAP)
        if not isinstance(submissions, dict):
            submissions = _sec_get(
                f"https://data.sec.gov/submissions/CIK{cik}.json"
            ).json()
            cache_set("sec_submissions", cik, submissions)
        recent = submissions["filings"]["recent"]
        index = next(
            (i for i, form in enumerate(recent["form"]) if form == "10-K"),
            None,
        )
        if index is None:
            logger.error("No recent 10-K found for CIK %s", cik)
            return None

        accession = recent["accessionNumber"][index].replace("-", "")
        cached_filing = cache_get("sec_10k", accession, TTL_SEC)
        if isinstance(cached_filing, dict) and cached_filing.get("clean_text"):
            logger.info("Using cached 10-K text for accession %s", accession)
            return cached_filing

        primary_document = recent["primaryDocument"][index]
        filing_url = (
            "https://www.sec.gov/Archives/edgar/data/"
            f"{int(cik)}/{accession}/{primary_document}"
        )
        logger.info("Downloading 10-K from %s", filing_url)
        clean_text = _clean_html(_sec_get(filing_url).text)
        filing = {
            "clean_text": clean_text,
            "filing_url": filing_url,
            "filing_date": recent["filingDate"][index],
            "accession_number": recent["accessionNumber"][index],
        }
        cache_set("sec_10k", accession, filing)
        return filing
    except Exception:
        logger.exception("Failed to fetch or parse latest 10-K for CIK %s", cik)
        return None


def fetch_latest_10k_sections(
    ticker: str,
    include_extended_financials: bool = False,
) -> Optional[Dict[str, Any]]:
    """Download one filing and return sourced Item 1A, Item 7, and Item 8 excerpts."""
    filing = _download_latest_10k(ticker)
    if not filing:
        return None
    clean_text = filing.pop("clean_text")
    item_1a = extract_sec_section(clean_text, "1A")
    if include_extended_financials:
        starts, ends = SECTION_BOUNDARIES["7"]
        item_7 = _find_real_section(
            clean_text,
            starts,
            ends,
            max_chars=500_000,
        )
    else:
        item_7 = extract_sec_section(clean_text, "7")
    item_8 = extract_sec_section(clean_text, "8")
    logger.info(
        "Extracted 10-K sections for %s | Item 1A: %d | Item 7: %d | Item 8: %d chars",
        ticker,
        len(item_1a or ""),
        len(item_7 or ""),
        len(item_8 or ""),
    )
    return {**filing, "item_1a": item_1a, "item_7": item_7, "item_8": item_8}


def sourced_filing_payload(sections: Dict[str, Any]) -> Dict[str, Any]:
    """Labeled Item 1A / Item 7 plus filing URL metadata. Never drop a missing section."""
    item_1a = str(sections.get("item_1a") or "")[:MAX_SECTION_CHARS]
    item_7 = str(sections.get("item_7") or "")[:MAX_SECTION_CHARS]
    metadata = {
        key: str(sections[key])
        for key in ("filing_url", "filing_date", "accession_number")
        if sections.get(key)
    }
    payload: Dict[str, Any] = {}
    if item_1a or item_7:
        payload["sec_filing_sections"] = {"item_1a": item_1a, "item_7": item_7}
        payload["sec_filing_chunks"] = [item_1a, item_7]
    if metadata:
        payload["sec_filing_metadata"] = metadata
    return payload


def fetch_sec_section(ticker: str, section_id: str) -> Optional[str]:
    """Compatibility helper for callers that need one named section."""
    sections = fetch_latest_10k_sections(ticker)
    if not sections:
        return None
    normalized = section_id.strip().upper()
    if normalized not in SECTION_BOUNDARIES:
        raise ValueError("section_id must be '1A', '7', or '8'.")
    key = {"1A": "item_1a", "7": "item_7", "8": "item_8"}[normalized]
    return sections.get(key)


def fetch_latest_10k_text(ticker: str) -> Optional[str]:
    """Backward-compatible preferred excerpt: Item 1A, otherwise Item 7."""
    sections = fetch_latest_10k_sections(ticker)
    if not sections:
        return None
    return sections.get("item_1a") or sections.get("item_7")
