"""Write a plain-text PDF copy of the Markdown memo."""

from __future__ import annotations

import re
from pathlib import Path

from fpdf import FPDF
from fpdf.errors import FPDFException


def _latin(text: str) -> str:
    cleaned = (
        text.replace("—", "-")
        .replace("–", "-")
        .replace("’", "'")
        .replace("“", '"')
        .replace("”", '"')
        .replace("•", "-")
    )
    return cleaned.encode("latin-1", "replace").decode("latin-1")


def _break_unbroken(text: str, width: int = 90) -> str:
    """Insert breaks so table rules and long tokens can wrap in Helvetica."""
    text = text.replace("|", " | ")
    text = re.sub(r"-{3,}", "--- ", text)
    pieces: list[str] = []
    for token in text.split(" "):
        if len(token) <= width:
            pieces.append(token)
            continue
        pieces.extend(token[index : index + width] for index in range(0, len(token), width))
    return " ".join(pieces)


def _write_line(pdf: FPDF, text: str, height: float) -> None:
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


def write_memo_pdf(markdown_text: str, pdf_path: Path) -> Path:
    """Render the memo as a readable PDF without claiming typeset IC quality."""
    pdf = FPDF(format="Letter")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.set_margins(15, 15, 15)
    pdf.add_page()
    pdf.set_font("Helvetica", size=10)
    for raw_line in markdown_text.splitlines():
        line = raw_line.rstrip()
        if line.startswith("# "):
            pdf.set_font("Helvetica", "B", 14)
            _write_line(pdf, line[2:], 8)
            pdf.set_font("Helvetica", size=10)
            continue
        if line.startswith("## "):
            pdf.ln(2)
            pdf.set_font("Helvetica", "B", 12)
            _write_line(pdf, line[3:], 7)
            pdf.set_font("Helvetica", size=10)
            continue
        if line.startswith("### "):
            pdf.set_font("Helvetica", "B", 11)
            _write_line(pdf, line[4:], 6)
            pdf.set_font("Helvetica", size=10)
            continue
        if not line.strip():
            pdf.ln(3)
            continue
        stripped = re.sub(r"[*_`]+", "", line)
        if stripped.lstrip().startswith("|"):
            pdf.set_font("Helvetica", size=7)
            _write_line(pdf, stripped, 4)
            pdf.set_font("Helvetica", size=10)
            continue
        _write_line(pdf, stripped, 5)
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(pdf_path))
    return pdf_path
