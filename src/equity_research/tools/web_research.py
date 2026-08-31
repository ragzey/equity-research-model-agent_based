"""Allowlisted first-party and high-quality third-party research onto the ledger.

The LLM never browses. Python fetches, then agents may quote excerpts and copy
`source_url` values that already exist on this list.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv

from ..utils.grounding import hostname_of, normalize_url
from .cache import TTL_WEB, cache_get, cache_set

load_dotenv()

logger = logging.getLogger("WebResearchTool")

REQUEST_TIMEOUT = 12
PAGE_TIMEOUT = 8
MAX_DOCS = 10
MAX_EXCERPT = 2500
MAX_QUERIES = 4
LOOKBACK_DAYS = 45
TAVILY_SEARCH_URL = "https://api.tavily.com/search"
FINNHUB_NEWS_URL = "https://finnhub.io/api/v1/company-news"
USER_AGENT = "Mozilla/5.0 (compatible; EquityResearchDesk/1.0)"

# Suffix match on hostname. Blogs, Seeking Alpha, and Motley Fool stay off.
HIGH_QUALITY_DOMAINS: Dict[str, str] = {
    "sec.gov": "first_party",
    "reuters.com": "high_quality",
    "bloomberg.com": "high_quality",
    "ft.com": "high_quality",
    "wsj.com": "high_quality",
    "nytimes.com": "high_quality",
    "economist.com": "high_quality",
    "bbc.com": "high_quality",
    "bbc.co.uk": "high_quality",
    "apnews.com": "high_quality",
    "cnbc.com": "high_quality",
    "marketwatch.com": "high_quality",
    "washingtonpost.com": "high_quality",
    "federalreserve.gov": "high_quality",
    "bls.gov": "high_quality",
    "bea.gov": "high_quality",
    "census.gov": "high_quality",
    "treasury.gov": "high_quality",
    "imf.org": "high_quality",
    "oecd.org": "high_quality",
    "ecb.europa.eu": "high_quality",
    "bis.org": "high_quality",
    "worldbank.org": "high_quality",
    "eia.gov": "high_quality",
    "fda.gov": "high_quality",
    "ftc.gov": "high_quality",
    "justice.gov": "high_quality",
    "finance.yahoo.com": "high_quality",
    "yahoo.com": "high_quality",
    "morningstar.com": "high_quality",
    "spglobal.com": "high_quality",
    "moodys.com": "high_quality",
    "fitchratings.com": "high_quality",
    "nasdaq.com": "high_quality",
    "mckinsey.com": "high_quality",
    "bain.com": "high_quality",
    "bcg.com": "high_quality",
    "idc.com": "high_quality",
    "gartner.com": "high_quality",
    "forrester.com": "high_quality",
}

_SKIP_SUFFIXES = (".pdf", ".zip", ".xlsx", ".xls", ".ppt", ".pptx", ".doc", ".docx")


class _HTMLText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip = 0
        self.parts: List[str] = []

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in {"script", "style", "noscript"}:
            self._skip += 1
        elif tag in {"p", "br", "div", "li", "h1", "h2", "h3", "tr"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self._skip:
            self._skip -= 1
        if tag in {"p", "div", "li", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def web_research_enabled() -> bool:
    flag = os.getenv("WEB_RESEARCH_ENABLED", "1").strip().lower()
    return flag not in {"0", "false", "no", "off"}


def _tavily_key() -> str:
    return os.getenv("TAVILY_API_KEY", "").strip()


def _finnhub_key() -> str:
    return os.getenv("FINNHUB_API_KEY", "").strip()


def registrable_host(host: str) -> str:
    parts = [part for part in str(host or "").lower().split(".") if part]
    if len(parts) >= 3 and parts[-2] in {"co", "com", "gov", "ac", "net", "org"}:
        return ".".join(parts[-3:])
    if len(parts) >= 2:
        return ".".join(parts[-2:])
    return ".".join(parts)


def first_party_hosts(website: Optional[str]) -> List[str]:
    host = hostname_of(website)
    if not host:
        return []
    root = registrable_host(host)
    hosts = [host]
    if root and root not in hosts:
        hosts.append(root)
    return hosts


def classify_host(url: str, extra_first_party: Optional[Iterable[str]] = None) -> Tuple[bool, str]:
    host = hostname_of(url)
    if not host:
        return False, ""
    for extra in extra_first_party or []:
        extra_host = hostname_of(extra) if "://" in str(extra) else str(extra or "").lower().lstrip(".")
        if not extra_host:
            continue
        root = registrable_host(extra_host)
        if host == extra_host or host.endswith("." + extra_host) or (
            root and (host == root or host.endswith("." + root))
        ):
            return True, "first_party"
    for domain, tier in HIGH_QUALITY_DOMAINS.items():
        if host == domain or host.endswith("." + domain):
            return True, tier
    return False, ""


def tavily_include_domains(extra_first_party: Optional[Iterable[str]] = None) -> List[str]:
    domains = list(HIGH_QUALITY_DOMAINS.keys())
    for host in extra_first_party or []:
        root = registrable_host(hostname_of(host) or str(host).lower())
        if root and root not in domains:
            domains.append(root)
    return domains[:40]


def html_to_text(html: str) -> str:
    parser = _HTMLText()
    try:
        parser.feed(html or "")
        parser.close()
    except Exception:
        return " ".join(re.sub(r"<[^>]+>", " ", html or "").split())
    return " ".join(" ".join(parser.parts).split())


def _clip_excerpt(text: str, limit: int = MAX_EXCERPT) -> str:
    return " ".join(str(text or "").split())[:limit]


def _usable_url(url: str) -> bool:
    raw = str(url or "").strip()
    if not raw.lower().startswith("http"):
        return False
    lowered = raw.lower().split("?", 1)[0]
    return not lowered.endswith(_SKIP_SUFFIXES)


def _doc(
    *,
    url: str,
    title: str,
    publisher: str,
    excerpt: str,
    tier: str,
    query: str,
    used_for: str,
    retrieved_at: Optional[str] = None,
) -> Optional[Dict[str, str]]:
    if not _usable_url(url) or not excerpt.strip():
        return None
    return {
        "url": str(url).strip(),
        "title": " ".join(str(title or "").split())[:240],
        "publisher": " ".join(str(publisher or "").split())[:120],
        "excerpt": _clip_excerpt(excerpt),
        "tier": tier,
        "query": query,
        "used_for": used_for,
        "retrieved_at": retrieved_at
        or datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def parse_tavily_results(
    payload: Any,
    *,
    extra_first_party: Optional[Iterable[str]] = None,
    query: str = "",
    used_for: str = "market",
) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    results = payload.get("results") if isinstance(payload, dict) else payload
    if not isinstance(results, list):
        return rows
    for item in results:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        allowed, tier = classify_host(url, extra_first_party)
        if not allowed:
            continue
        excerpt = str(item.get("content") or item.get("raw_content") or "").strip()
        title = str(item.get("title") or "").strip()
        publisher = hostname_of(url)
        doc = _doc(
            url=url,
            title=title,
            publisher=publisher,
            excerpt=excerpt or title,
            tier=tier,
            query=query,
            used_for=used_for,
        )
        if doc:
            rows.append(doc)
    return rows


def parse_yahoo_news(
    items: Any,
    *,
    extra_first_party: Optional[Iterable[str]] = None,
    used_for: str = "firm",
) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        content = item.get("content") if isinstance(item.get("content"), dict) else {}
        canonical = content.get("canonicalUrl") if isinstance(content.get("canonicalUrl"), dict) else {}
        url = str(
            item.get("link")
            or item.get("url")
            or canonical.get("url")
            or ""
        ).strip()
        title = str(item.get("title") or content.get("title") or "").strip()
        publisher = str(
            item.get("publisher")
            or (content.get("provider") or {}).get("displayName")
            or hostname_of(url)
        ).strip()
        summary = str(item.get("summary") or content.get("summary") or "").strip()
        allowed, tier = classify_host(url, extra_first_party)
        if not allowed:
            continue
        doc = _doc(
            url=url,
            title=title,
            publisher=publisher,
            excerpt=summary or title,
            tier=tier,
            query="yahoo_news",
            used_for=used_for,
        )
        if doc:
            rows.append(doc)
    return rows


def parse_finnhub_news(
    items: Any,
    *,
    extra_first_party: Optional[Iterable[str]] = None,
    used_for: str = "firm",
) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        allowed, tier = classify_host(url, extra_first_party)
        if not allowed:
            continue
        title = str(item.get("headline") or item.get("title") or "").strip()
        summary = str(item.get("summary") or "").strip()
        publisher = str(item.get("source") or hostname_of(url)).strip()
        doc = _doc(
            url=url,
            title=title,
            publisher=publisher,
            excerpt=summary or title,
            tier=tier,
            query="finnhub_news",
            used_for=used_for,
        )
        if doc:
            rows.append(doc)
    return rows


def research_queries(
    *,
    ticker: str,
    company_name: str = "",
    sector: str = "",
    industry: str = "",
    year: Optional[int] = None,
) -> List[Tuple[str, str]]:
    year = year or date.today().year
    name = company_name or ticker
    industry_label = industry or sector or name
    queries: List[Tuple[str, str]] = []
    if industry or sector:
        queries.append(
            (f"{industry_label} market outlook {year}", "market")
        )
        queries.append(
            (f"{industry_label} industry demand growth {year}", "industry")
        )
    queries.append((f"{name} {ticker} product demand {year}", "firm"))
    if sector and sector.lower() not in {industry_label.lower(), "n/a"}:
        queries.append((f"{sector} demand conditions {year}", "market"))
    # Deduplicate while keeping order.
    seen = set()
    unique: List[Tuple[str, str]] = []
    for query, used_for in queries:
        key = query.lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append((query, used_for))
        if len(unique) >= MAX_QUERIES:
            break
    return unique


def format_web_research(docs: Optional[Sequence[Dict[str, Any]]]) -> str:
    lines: List[str] = []
    for index, doc in enumerate(docs or [], start=1):
        if not isinstance(doc, dict):
            continue
        url = str(doc.get("url") or "").strip()
        excerpt = str(doc.get("excerpt") or "").strip()
        if not url or not excerpt:
            continue
        title = str(doc.get("title") or "Untitled").strip()
        publisher = str(doc.get("publisher") or hostname_of(url)).strip()
        tier = str(doc.get("tier") or "").strip()
        used_for = str(doc.get("used_for") or "").strip()
        lines.append(
            f"[{index}] {title} | {publisher} | {tier} | {used_for} | {url}\n{excerpt}"
        )
    return "\n\n".join(lines) if lines else "No allowlisted web research on the ledger."


def web_research_blob(state: Optional[Dict[str, Any]]) -> str:
    docs = (state or {}).get("web_research") or []
    parts: List[str] = []
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        for key in ("title", "excerpt"):
            text = str(doc.get(key) or "").strip()
            if text:
                parts.append(text)
    return "\n".join(parts)


def ledger_source_urls(state: Optional[Dict[str, Any]]) -> List[str]:
    urls: List[str] = []
    meta = (state or {}).get("sec_filing_metadata") or {}
    filing_url = str(meta.get("filing_url") or "").strip()
    if filing_url:
        urls.append(filing_url)
    for doc in (state or {}).get("web_research") or []:
        if isinstance(doc, dict):
            url = str(doc.get("url") or "").strip()
            if url and url not in urls:
                urls.append(url)
    return urls


def _dedupe(docs: Sequence[Dict[str, str]]) -> List[Dict[str, str]]:
    kept: List[Dict[str, str]] = []
    seen = set()
    for doc in docs:
        key = normalize_url(doc.get("url"))
        if not key or key in seen:
            continue
        seen.add(key)
        kept.append(doc)
        if len(kept) >= MAX_DOCS:
            break
    return kept


def _enrich_short_excerpts(
    docs: Sequence[Dict[str, str]],
    extra_first_party: Optional[Iterable[str]] = None,
) -> List[Dict[str, str]]:
    enriched: List[Dict[str, str]] = []
    for doc in docs:
        updated = dict(doc)
        excerpt = str(updated.get("excerpt") or "")
        if len(excerpt) >= 400:
            enriched.append(updated)
            continue
        page = fetch_page_text(updated.get("url") or "", extra_first_party=extra_first_party)
        if page and len(page) > len(excerpt):
            updated["excerpt"] = _clip_excerpt(page)
        enriched.append(updated)
    return enriched


def fetch_page_text(
    url: str,
    *,
    extra_first_party: Optional[Iterable[str]] = None,
) -> str:
    allowed, _tier = classify_host(url, extra_first_party)
    if not allowed or not _usable_url(url):
        return ""
    try:
        response = requests.get(
            url,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
            timeout=PAGE_TIMEOUT,
        )
        response.raise_for_status()
        ctype = str(response.headers.get("Content-Type") or "").lower()
        if "html" not in ctype and "text" not in ctype and ctype:
            return ""
        return html_to_text(response.text)
    except Exception:
        logger.info("Page fetch skipped for %s", url)
        return ""


def _tavily_search(
    query: str,
    *,
    extra_first_party: Optional[Iterable[str]] = None,
    used_for: str,
) -> List[Dict[str, str]]:
    key = _tavily_key()
    if not key:
        return []
    try:
        response = requests.post(
            TAVILY_SEARCH_URL,
            json={
                "api_key": key,
                "query": query,
                "search_depth": "basic",
                "include_answer": False,
                "max_results": 5,
                "include_domains": tavily_include_domains(extra_first_party),
            },
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        return parse_tavily_results(
            response.json(),
            extra_first_party=extra_first_party,
            query=query,
            used_for=used_for,
        )
    except Exception:
        logger.exception("Tavily search failed for query %s", query)
        return []


def _yahoo_news(ticker: str, extra_first_party: Optional[Iterable[str]] = None) -> List[Dict[str, str]]:
    try:
        import yfinance as ticker_engine

        company = ticker_engine.Ticker(ticker)
        items = company.news or []
        return parse_yahoo_news(items, extra_first_party=extra_first_party, used_for="firm")
    except Exception:
        logger.exception("Yahoo news unavailable for %s", ticker)
        return []


def _finnhub_news(ticker: str, extra_first_party: Optional[Iterable[str]] = None) -> List[Dict[str, str]]:
    key = _finnhub_key()
    if not key:
        return []
    end = date.today()
    start = end - timedelta(days=LOOKBACK_DAYS)
    try:
        response = requests.get(
            FINNHUB_NEWS_URL,
            params={
                "symbol": ticker,
                "from": start.isoformat(),
                "to": end.isoformat(),
                "token": key,
            },
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        return parse_finnhub_news(
            response.json(),
            extra_first_party=extra_first_party,
            used_for="firm",
        )
    except Exception:
        logger.exception("Finnhub company news unavailable for %s", ticker)
        return []


def fetch_web_research(
    ticker: str,
    *,
    company_name: str = "",
    sector: str = "",
    industry: str = "",
    website: str = "",
    year: Optional[int] = None,
) -> List[Dict[str, str]]:
    """Fetch allowlisted market / industry / firm pages onto the ledger."""
    symbol = str(ticker or "").strip().upper()
    if not symbol or not web_research_enabled():
        return []
    cache_key = "|".join(
        [
            symbol,
            str(year or date.today().year),
            str(industry or ""),
            str(sector or ""),
        ]
    )
    cached = cache_get("web_research", cache_key, TTL_WEB)
    if isinstance(cached, list):
        return cached

    extra = first_party_hosts(website)
    collected: List[Dict[str, str]] = []
    for query, used_for in research_queries(
        ticker=symbol,
        company_name=company_name,
        sector=sector,
        industry=industry,
        year=year,
    ):
        collected.extend(
            _tavily_search(query, extra_first_party=extra, used_for=used_for)
        )
    collected.extend(_yahoo_news(symbol, extra))
    collected.extend(_finnhub_news(symbol, extra))
    docs = _dedupe(_enrich_short_excerpts(collected, extra_first_party=extra))
    # Drop rows that still have almost no body after fetch.
    docs = [doc for doc in docs if len(str(doc.get("excerpt") or "")) >= 40]
    cache_set("web_research", cache_key, docs)
    logger.info("Stored %d allowlisted web research document(s) for %s.", len(docs), symbol)
    return docs
