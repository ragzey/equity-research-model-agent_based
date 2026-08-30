"""Render the equity research memo as a letter PDF."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from fpdf import FPDF
from fpdf.errors import FPDFException
from fpdf.fonts import FontFace

NAVY = (30, 58, 76)
INK = (28, 25, 23)
MUTED = (90, 90, 90)
RULE = (196, 191, 181)
FILL = (236, 232, 223)
WHITE = (255, 255, 255)
BUY = (39, 103, 73)
SELL = (154, 52, 18)

_EXCHANGE_MAP = {
    "NYQ": "NYSE",
    "NYS": "NYSE",
    "NYSE": "NYSE",
    "NMS": "NASDAQ",
    "NGM": "NASDAQ",
    "NASDAQ": "NASDAQ",
    "ASE": "NYSE American",
    "PCX": "NYSE Arca",
    "LSE": "LSE",
}


def pdf_download_stem(override: Optional[str], ticker: str = "") -> str:
    """Short download name: TJX.pdf, not TICKER_date_memo.pdf."""
    raw = (override or ticker or "memo").strip()
    raw = re.sub(r"\.(pdf|md)$", "", raw, flags=re.IGNORECASE)
    stem = re.sub(r"[^A-Za-z0-9_-]+", "", raw)
    return stem[:40] or "memo"


def display_exchange(raw: Optional[str]) -> str:
    if not raw:
        return ""
    token = str(raw).strip()
    return _EXCHANGE_MAP.get(token.upper(), token)


def _latin(text: str) -> str:
    cleaned = (
        (text or "")
        .replace("—", "-")
        .replace("–", "-")
        .replace("’", "'")
        .replace("‘", "'")
        .replace("“", '"')
        .replace("”", '"')
        .replace("•", "-")
        .replace("±", "+/-")
        .replace("×", "x")
    )
    return cleaned.encode("latin-1", "replace").decode("latin-1")


def _break_unbroken(text: str, width: int = 90) -> str:
    text = text.replace("|", " | ")
    text = re.sub(r"-{3,}", "--- ", text)
    pieces: list[str] = []
    for token in text.split(" "):
        if len(token) <= width:
            pieces.append(token)
            continue
        pieces.extend(token[index : index + width] for index in range(0, len(token), width))
    return " ".join(pieces)


def _money(value: Any) -> str:
    if value is None:
        return "n/a"
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return str(value)


def _pct(value: Any) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return str(value)


class ResearchNotePDF(FPDF):
    def __init__(self, identity: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(format="Letter")
        self.identity = identity or {}
        self.set_auto_page_break(auto=True, margin=16)
        self.set_margins(16, 16, 16)

    def header(self) -> None:
        if self.page_no() <= 1:
            return
        ticker = str(self.identity.get("ticker") or "")
        if not ticker:
            return
        self.set_font("Helvetica", size=8)
        self.set_text_color(*MUTED)
        self.cell(0, 5, _latin(f"Equity Research  |  {ticker}"), align="R")
        self.ln(2)
        self.set_draw_color(*RULE)
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.ln(4)

    def footer(self) -> None:
        self.set_y(-12)
        self.set_font("Helvetica", size=8)
        self.set_text_color(*MUTED)
        self.cell(0, 8, str(self.page_no()), align="C")


def _write_wrapped(pdf: FPDF, text: str, height: float) -> None:
    payload = _break_unbroken(_latin(text))
    if not payload.strip():
        pdf.ln(height)
        return
    try:
        pdf.multi_cell(pdf.epw, height, payload)
    except FPDFException:
        pdf.set_font("Helvetica", size=7)
        for index in range(0, len(payload), 70):
            pdf.multi_cell(pdf.epw, 4, payload[index : index + 70])
        pdf.set_font("Helvetica", size=10)


def _rating_fill(rating: str) -> Tuple[int, int, int]:
    token = (rating or "").upper()
    if token == "BUY":
        return BUY
    if token == "SELL":
        return SELL
    return NAVY


def _draw_masthead(pdf: ResearchNotePDF, identity: Dict[str, Any]) -> None:
    company = str(identity.get("company_name") or identity.get("ticker") or "").upper()
    ticker = str(identity.get("ticker") or "")
    exchange = display_exchange(identity.get("exchange"))
    listed = f"{exchange}: {ticker}" if exchange else ticker
    industry = str(identity.get("industry") or "n/a")
    country = str(identity.get("country") or "n/a")
    line = "   |   ".join(part for part in (company, listed, industry, country) if part)
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_text_color(*NAVY)
    _write_wrapped(pdf, line, 5)
    pdf.ln(2)
    pdf.set_draw_color(*NAVY)
    pdf.set_line_width(0.6)
    y = pdf.get_y()
    pdf.line(pdf.l_margin, y, pdf.w - pdf.r_margin, y)
    pdf.set_line_width(0.2)
    pdf.ln(4)
    rating = str(identity.get("rating") or identity.get("model_rating") or "Withheld").upper()
    fill = _rating_fill(rating)
    pdf.set_fill_color(*fill)
    pdf.set_text_color(*WHITE)
    pdf.set_font("Helvetica", "B", 10)
    strip = (
        f"  {rating}      Price target {_money(identity.get('price_target'))}"
        f"      Share price {_money(identity.get('share_price'))}"
        f"      Upside {_pct(identity.get('upside'))}  "
    )
    pdf.cell(pdf.epw, 8, _latin(strip), fill=True)
    pdf.ln(10)
    pdf.set_text_color(*MUTED)
    pdf.set_font("Helvetica", size=9)
    valuation_date = identity.get("valuation_date") or date.today()
    if isinstance(valuation_date, date):
        date_label = valuation_date.strftime("%d %B %Y")
    else:
        date_label = str(valuation_date)
    _write_wrapped(pdf, f"Equity Research   |   Valuation date {date_label}", 5)
    pdf.set_text_color(*INK)
    pdf.ln(3)


def _parse_table(lines: Sequence[str], start: int) -> Tuple[List[List[str]], int]:
    rows: List[List[str]] = []
    index = start
    while index < len(lines):
        line = lines[index].rstrip()
        if not line.strip().startswith("|"):
            break
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if cells and not all(re.fullmatch(r":?-{3,}:?", cell or "") for cell in cells):
            rows.append(cells)
        index += 1
    return rows, index


def _render_table(pdf: ResearchNotePDF, rows: List[List[str]]) -> None:
    if not rows:
        return
    width = max(len(row) for row in rows)
    normalised = [row + [""] * (width - len(row)) for row in rows]
    pdf.ln(1)
    heading = FontFace(emphasis="BOLD", color=INK, fill_color=FILL, size_pt=7)
    try:
        with pdf.table(
            first_row_as_headings=True,
            headings_style=heading,
            line_height=4.2,
            text_align="LEFT",
            padding=1.2,
            wrapmode="CHAR",
        ) as table:
            for row in normalised:
                table_row = table.row()
                for cell in row:
                    table_row.cell(_latin(cell))
    except Exception:
        pdf.set_font("Helvetica", size=7)
        for row in normalised:
            _write_wrapped(pdf, " | ".join(row), 4)
        pdf.set_font("Helvetica", size=10)
    pdf.ln(2)


def _render_markdown(
    pdf: ResearchNotePDF,
    markdown_text: str,
    *,
    skip_opening_masthead: bool,
) -> None:
    lines = markdown_text.splitlines()
    index = 0
    skipping = skip_opening_masthead
    while index < len(lines):
        line = lines[index].rstrip()
        if skipping:
            if line.startswith("## "):
                skipping = False
            else:
                index += 1
                continue
        if line.strip().startswith("|"):
            rows, index = _parse_table(lines, index)
            _render_table(pdf, rows)
            continue
        if not line.strip():
            pdf.ln(3)
            index += 1
            continue
        if line.startswith("# "):
            pdf.set_font("Helvetica", "B", 13)
            pdf.set_text_color(*NAVY)
            _write_wrapped(pdf, line[2:], 7)
            pdf.set_text_color(*INK)
            pdf.set_font("Helvetica", size=10)
            index += 1
            continue
        if line.startswith("## "):
            pdf.ln(3)
            pdf.set_font("Helvetica", "B", 12)
            pdf.set_text_color(*NAVY)
            _write_wrapped(pdf, line[3:], 6)
            pdf.set_draw_color(*RULE)
            y = pdf.get_y()
            pdf.line(pdf.l_margin, y, pdf.w - pdf.r_margin, y)
            pdf.ln(3)
            pdf.set_text_color(*INK)
            pdf.set_font("Helvetica", size=10)
            index += 1
            continue
        if line.startswith("### "):
            pdf.set_font("Helvetica", "B", 10)
            _write_wrapped(pdf, line[4:], 5)
            pdf.set_font("Helvetica", size=10)
            index += 1
            continue
        stripped = re.sub(r"[*_`]+", "", line)
        if stripped.lstrip().startswith("- "):
            pdf.set_x(pdf.l_margin + 3)
            _write_wrapped(pdf, stripped.lstrip(), 5)
            index += 1
            continue
        if stripped.lstrip().startswith(">"):
            pdf.set_font("Helvetica", "I", 9)
            pdf.set_text_color(*MUTED)
            _write_wrapped(pdf, stripped.lstrip().lstrip("> ").strip(), 5)
            pdf.set_text_color(*INK)
            pdf.set_font("Helvetica", size=10)
            index += 1
            continue
        _write_wrapped(pdf, stripped, 5)
        index += 1


def write_memo_pdf(
    markdown_text: str,
    pdf_path: Path,
    *,
    identity: Optional[Dict[str, Any]] = None,
) -> Path:
    """Render the memo as an initiation-style equity research PDF."""
    identity = dict(identity or {})
    pdf = ResearchNotePDF(identity)
    pdf.add_page()
    if identity.get("ticker") or identity.get("company_name"):
        _draw_masthead(pdf, identity)
    pdf.set_font("Helvetica", size=10)
    pdf.set_text_color(*INK)
    _render_markdown(
        pdf,
        markdown_text,
        skip_opening_masthead=bool(identity.get("ticker")),
    )
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(pdf_path))
    return pdf_path
