"""Shared anti-hallucination checks for LLM prose."""

from __future__ import annotations

import re
from typing import Optional

# Scheme-less www. still counts. Do not treat [Item 1A] prefixes as links.
_WEB_LINK_RE = re.compile(r"(?i)(?:https?://|ftp://|www\.)")


def contains_web_link(text: Optional[str]) -> bool:
    """True when prose includes a URL or www. host."""
    return bool(_WEB_LINK_RE.search(str(text or "")))
