"""Compliant SEC EDGAR retrieval and evidence-only 10-K section extraction."""

from __future__ import annotations

import logging
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple

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
# Used only when the SEC listing map is down or the operator typed a name.
# These are directory aliases, not invented tickers.
ISSUER_ALIASES = {
    "APPLE": "AAPL",
    "APPLEINC": "AAPL",
    "MICROSOFT": "MSFT",
    "MICROSOFTCORP": "MSFT",
    "GOOGLE": "GOOGL",
    "ALPHABET": "GOOGL",
    "ALPHABETINC": "GOOGL",
    "AMAZON": "AMZN",
    "AMAZONCOM": "AMZN",
    "NVIDIA": "NVDA",
    "TESLA": "TSLA",
    "META": "META",
    "FACEBOOK": "META",
    "NETFLIX": "NFLX",
    "COSTCO": "COST",
    "COSTCOWHOLESALE": "COST",
}

SECTION_BOUNDARIES = {
    "1": (("ITEM 1.", "ITEM 1 BUSINESS"), ("ITEM 1A.", "ITEM 1A")),
    "1A": (("ITEM 1A.", "ITEM 1A"), ("ITEM 1B.", "ITEM 1B")),
    "7": (("ITEM 7.", "ITEM 7"), ("ITEM 7A.", "ITEM 7A")),
    "8": (("ITEM 8.", "ITEM 8"), ("ITEM 9.", "ITEM 9", "ITEM 9A")),
}
SECTION_MAX_CHARS = {
    "1": MAX_SECTION_CHARS,
    "1A": MAX_SECTION_CHARS,
    "7": MAX_SECTION_CHARS,
    "8": 1_500_000,
}


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


def _company_ticker_map() -> Dict[str, Any]:
    cached = cache_get("sec_tickers", "all", TTL_TICKER_MAP)
    if isinstance(cached, dict) and cached:
        return cached
    payload = _sec_get("https://www.sec.gov/files/company_tickers.json").json()
    if isinstance(payload, dict):
        cache_set("sec_tickers", "all", payload)
        return payload
    return {}


def _alnum_key(text: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(text or "").upper())


def _name_key(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", str(text or "").lower()))


def resolve_listed_symbol(
    query: str,
    ticker_data: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """
    Map a ticker or a company name to an SEC-listed symbol.

    'AAPL' stays AAPL. 'Apple' / 'APPLE' / 'Apple Inc.' resolve to AAPL.
    """
    raw = str(query or "").strip()
    if not raw:
        return None
    symbol = raw.upper().replace(" ", "")
    symbol = symbol.replace("_", "-")
    alias = ISSUER_ALIASES.get(_alnum_key(raw)) or ISSUER_ALIASES.get(_alnum_key(symbol))
    if alias:
        logger.info("Resolved issuer %r to listed ticker %s via alias", raw, alias)
        return alias
    if re.fullmatch(r"[A-Z]{1,5}(?:[.-][A-Z]{1,2})?", symbol):
        return symbol

    try:
        listing = ticker_data if isinstance(ticker_data, dict) else _company_ticker_map()
    except Exception:
        logger.exception("SEC ticker map unavailable while resolving %s", raw)
        listing = {}
    entries = [
        row
        for row in (listing or {}).values()
        if isinstance(row, dict) and row.get("ticker")
    ]
    by_ticker = {str(row["ticker"]).strip().upper(): row for row in entries}
    if symbol in by_ticker:
        return symbol

    needle = _name_key(raw)
    if len(needle.replace(" ", "")) < 4:
        return None

    ranked: List[Tuple[int, int, str]] = []
    for row in entries:
        ticker = str(row.get("ticker") or "").strip().upper()
        title_n = _name_key(row.get("title") or "")
        if not ticker or not title_n:
            continue
        reit = any(token in title_n.split() for token in ("reit", "etf", "trust"))
        first = title_n.split(" ", 1)[0]
        needle_first = needle.split(" ", 1)[0]
        if title_n == needle:
            score = 0
        elif title_n.startswith(f"{needle} ") or needle.startswith(f"{title_n} "):
            score = 1 if not reit else 5
        elif first == needle_first and needle_first == needle and not reit:
            score = 2
        elif first == needle_first and needle_first == needle:
            score = 6
        elif f" {needle} " in f" {title_n} " and not reit:
            score = 3
        else:
            continue
        ranked.append((score, len(title_n), ticker))
    if ranked:
        ranked.sort()
        resolved = ranked[0][2]
        logger.info("Resolved issuer %r to listed ticker %s", raw, resolved)
        return resolved
    return None


def get_cik_for_ticker(ticker: str) -> Optional[str]:
    """Map a ticker to its 10-digit SEC CIK."""
    listed = resolve_listed_symbol(ticker) or ticker.strip().upper()
    cached_cik = cache_get("sec_cik", listed, TTL_TICKER_MAP)
    if isinstance(cached_cik, str) and cached_cik:
        return cached_cik
    try:
        ticker_data = _company_ticker_map()
        for entry in ticker_data.values():
            if str(entry.get("ticker") or "").upper() == listed:
                cik = str(entry["cik_str"]).zfill(10)
                logger.info("CIK resolved: %s -> %s", listed, cik)
                cache_set("sec_cik", listed, cik)
                return cik
        logger.error("CIK not found for ticker: %s", listed)
    except Exception:
        logger.exception("Error fetching CIK mapping for %s", listed)
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
    """Extract Item 1, 1A, 7, or 8 from already-cleaned 10-K text."""
    normalized = section_id.strip().upper()
    if normalized not in SECTION_BOUNDARIES:
        raise ValueError("section_id must be '1', '1A', '7', or '8'.")
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
    """Download one filing and return sourced Item 1, 1A, 7, and 8 excerpts."""
    filing = _download_latest_10k(ticker)
    if not filing:
        return None
    clean_text = filing.pop("clean_text")
    item_1 = extract_sec_section(clean_text, "1")
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
        "Extracted 10-K sections for %s | Item 1: %d | Item 1A: %d | Item 7: %d | Item 8: %d chars",
        ticker,
        len(item_1 or ""),
        len(item_1a or ""),
        len(item_7 or ""),
        len(item_8 or ""),
    )
    return {
        **filing,
        "item_1": item_1,
        "item_1a": item_1a,
        "item_7": item_7,
        "item_8": item_8,
    }


def sourced_filing_payload(sections: Dict[str, Any]) -> Dict[str, Any]:
    """Labeled Item 1 / 1A / 7 plus filing URL metadata. Never drop a missing section."""
    item_1 = str(sections.get("item_1") or "")[:MAX_SECTION_CHARS]
    item_1a = str(sections.get("item_1a") or "")[:MAX_SECTION_CHARS]
    item_7 = str(sections.get("item_7") or "")[:MAX_SECTION_CHARS]
    metadata = {
        key: str(sections[key])
        for key in ("filing_url", "filing_date", "accession_number")
        if sections.get(key)
    }
    payload: Dict[str, Any] = {}
    if item_1 or item_1a or item_7:
        payload["sec_filing_sections"] = {
            "item_1": item_1,
            "item_1a": item_1a,
            "item_7": item_7,
        }
        payload["sec_filing_chunks"] = [item_1, item_1a, item_7]
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
        raise ValueError("section_id must be '1', '1A', '7', or '8'.")
    key = {"1": "item_1", "1A": "item_1a", "7": "item_7", "8": "item_8"}[normalized]
    return sections.get(key)


def fetch_latest_10k_text(ticker: str) -> Optional[str]:
    """Backward-compatible preferred excerpt: Item 1A, otherwise Item 7."""
    sections = fetch_latest_10k_sections(ticker)
    if not sections:
        return None
    return sections.get("item_1a") or sections.get("item_7")
