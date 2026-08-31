"""Shared anti-hallucination checks for LLM prose."""

from __future__ import annotations

import re
from typing import Iterable, List, Optional, Sequence
from urllib.parse import urlparse

# Scheme-less www. still counts. Do not treat [Item 1A] prefixes as links.
_WEB_LINK_RE = re.compile(r"(?i)(?:https?://|ftp://|www\.)")
_URL_TOKEN_RE = re.compile(r"(?i)\b(?:https?://|ftp://|www\.)[^\s)>\]]+")


def contains_web_link(text: Optional[str]) -> bool:
    """True when prose includes a URL or www. host."""
    return bool(_WEB_LINK_RE.search(str(text or "")))


def extract_urls(text: Optional[str]) -> List[str]:
    """Return URL tokens found in prose, including scheme-less www. hosts."""
    found: List[str] = []
    for raw in _URL_TOKEN_RE.findall(str(text or "")):
        token = str(raw).rstrip(".,;:\"'")
        if token and token not in found:
            found.append(token)
    return found


def strip_urls(text: Optional[str]) -> str:
    """Remove URL tokens so evidence quotes stay quote-only."""
    cleaned = _URL_TOKEN_RE.sub(" ", str(text or ""))
    return " ".join(cleaned.split())


def normalize_url(url: Optional[str]) -> str:
    """Lowercase host, drop fragment and trailing slash, add https for www."""
    raw = str(url or "").strip()
    if not raw:
        return ""
    if raw.lower().startswith("www."):
        raw = "https://" + raw
    parsed = urlparse(raw)
    if not parsed.scheme or not parsed.netloc:
        return raw.rstrip("/").split("#")[0]
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    path = parsed.path.rstrip("/")
    query = f"?{parsed.query}" if parsed.query else ""
    return f"{parsed.scheme.lower()}://{host}{path}{query}"


def hostname_of(url: Optional[str]) -> str:
    raw = str(url or "").strip()
    if raw.lower().startswith("www."):
        raw = "https://" + raw
    host = (urlparse(raw).netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def url_on_ledger(url: Optional[str], allowed: Optional[Iterable[str]] = None) -> bool:
    """True when url matches a fetched ledger URL (normalized, or same path prefix)."""
    target = normalize_url(url)
    if not target:
        return False
    for item in allowed or []:
        kept = normalize_url(item)
        if not kept:
            continue
        if target == kept or target.startswith(kept + "/") or kept.startswith(target + "/"):
            return True
    return False


def has_unledgered_link(
    text: Optional[str],
    allowed: Optional[Iterable[str]] = None,
) -> bool:
    """True when prose contains a URL that was not fetched onto the ledger."""
    allowed_list = [str(item) for item in (allowed or []) if item]
    for url in extract_urls(text):
        if not url_on_ledger(url, allowed_list):
            return True
    return False


def has_blocked_link(
    text: Optional[str],
    allowed: Optional[Iterable[str]] = None,
) -> bool:
    """Drop invented links. Ledger URLs are allowed when the allow-list is supplied."""
    if not contains_web_link(text):
        return False
    allowed_list = [str(item) for item in (allowed or []) if item]
    if not allowed_list:
        return True
    return has_unledgered_link(text, allowed_list)


def ledger_urls_from_docs(docs: Optional[Sequence[object]]) -> List[str]:
    urls: List[str] = []
    for item in docs or []:
        if isinstance(item, dict):
            url = str(item.get("url") or "").strip()
        else:
            url = str(item or "").strip()
        if url and url not in urls:
            urls.append(url)
    return urls
