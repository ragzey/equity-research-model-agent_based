"""Conservative CUSIP/ISIN harvest from SEC debt-footnote text."""

from __future__ import annotations

import re
from typing import Iterable, List

ISIN_PATTERN = re.compile(r"\b([A-Z]{2}[A-Z0-9]{9}[0-9])\b")
LABELED_CUSIP_PATTERN = re.compile(
    r"CUSIP(?:\s*(?:NO\.?|NUMBER|#|:))?\s*[:\-]?\s*([0-9A-Z]*\d[0-9A-Z]*)\b"
)
DEBT_MARKERS = (
    "debenture",
    "indenture",
    "senior unsecured",
    "senior notes",
    "unsecured notes",
    "registered notes",
    "outstanding notes",
    "issued notes",
    "notes due",
    "bonds due",
    "bond due",
    "due 20",
)
MAX_ISINS = 8
CONTEXT_CHARS = 80


def isin_check_digit(body11: str) -> str:
    """Return the ISIN check digit for the first 11 characters."""
    expanded: List[int] = []
    for character in body11.upper():
        if character.isdigit():
            expanded.append(int(character))
        else:
            number = ord(character) - 55
            expanded.extend(int(digit) for digit in str(number))
    total = 0
    for index, digit in enumerate(reversed(expanded)):
        if index % 2 == 0:
            doubled = digit * 2
            total += doubled // 10 + doubled % 10
        else:
            total += digit
    return str((10 - (total % 10)) % 10)


def is_valid_isin(value: str) -> bool:
    candidate = value.strip().upper().replace(" ", "")
    if len(candidate) != 12 or not candidate[:2].isalpha():
        return False
    if not re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}[0-9]", candidate):
        return False
    return candidate[11] == isin_check_digit(candidate[:11])


def cusip_to_isin(cusip: str, country: str = "US") -> str:
    clean = cusip.strip().upper().replace(" ", "")
    if len(clean) != 9 or not re.fullmatch(r"[0-9A-Z]{9}", clean):
        raise ValueError("CUSIP must be 9 alphanumeric characters.")
    body = f"{country.upper()}{clean}"
    return body + isin_check_digit(body)


def _context(text: str, start: int, end: int) -> str:
    return text[max(0, start - CONTEXT_CHARS) : min(len(text), end + CONTEXT_CHARS)]


def _has_debt_context(context: str) -> bool:
    lowered = context.lower()
    return any(marker in lowered for marker in DEBT_MARKERS)


def extract_bond_isins(text: str, limit: int = MAX_ISINS) -> List[str]:
    """
    Pull ISINs, and US CUSIPs only when the nearby text looks like a debt footnote.

    Invalid check digits are dropped. This is candidate discovery, not a security master.
    """
    if not text:
        return []
    found: List[str] = []
    seen = set()

    def _add(isin: str) -> None:
        if isin in seen or len(found) >= limit:
            return
        if not is_valid_isin(isin):
            return
        seen.add(isin)
        found.append(isin)

    upper = text.upper()
    for match in ISIN_PATTERN.finditer(upper):
        if _has_debt_context(_context(upper, match.start(), match.end())):
            _add(match.group(1))

    for match in LABELED_CUSIP_PATTERN.finditer(upper):
        cusip = match.group(1)
        if len(cusip) != 9:
            continue
        context = _context(upper, match.start(), match.end())
        if not _has_debt_context(context):
            continue
        try:
            _add(cusip_to_isin(cusip))
        except ValueError:
            continue
        if len(found) >= limit:
            break
    return found


def merge_isin_lists(*groups: Iterable[str]) -> List[str]:
    merged: List[str] = []
    seen = set()
    for group in groups:
        for raw in group or ():
            isin = str(raw).strip().upper().replace(" ", "")
            if isin in seen or not is_valid_isin(isin):
                continue
            seen.add(isin)
            merged.append(isin)
            if len(merged) >= MAX_ISINS:
                return merged
    return merged
