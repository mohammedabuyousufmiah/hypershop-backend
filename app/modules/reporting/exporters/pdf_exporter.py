"""PDF exporter using fpdf2.

Bengali support:
  - fpdf2's built-in fonts (Helvetica, Times, Courier) cover Latin
    only — Bengali / Devanagari / Arabic glyphs render as boxes.
  - To get readable Bengali, set ``REPORT_PDF_BENGALI_FONT_PATH`` to
    a TrueType file with Bengali glyphs (e.g. NotoSansBengali-Regular.ttf
    from Google Noto Fonts).
  - When the env var is set AND the file exists, we register it as
    "noto" and use it for everything. Otherwise we fall back to
    Helvetica with a one-shot warning log so ops sees the gap once
    on startup and not on every export.

Layout:
  - A4 landscape (more columns fit per row)
  - 14pt title, 9pt headers (bold), 8pt body
  - one row per data row; oversized columns wrap via fpdf's
    multi_cell only on the body, not on header
  - A "Generated <ts> • N rows" footer line under the title
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Any

from fpdf import FPDF
from fpdf.enums import XPos, YPos

from app.core.config import get_settings
from app.core.logging import get_logger
from app.modules.reporting.exporters._format import format_cell

_log = get_logger("hypershop.reporting.pdf")
_warn_once_lock = Lock()
_warned_missing_bengali = False


def _maybe_register_bengali(pdf: FPDF) -> str:
    """Returns the family name to use ('noto' if Bengali registered,
    'Helvetica' otherwise).
    """
    global _warned_missing_bengali
    s = get_settings()
    font_path = (s.report_pdf_bengali_font_path or "").strip()
    if not font_path:
        return "Helvetica"
    p = Path(font_path)
    if not p.is_file():
        with _warn_once_lock:
            if not _warned_missing_bengali:
                _log.warning(
                    "report_pdf_bengali_font_missing",
                    path=font_path,
                )
                _warned_missing_bengali = True
        return "Helvetica"
    try:
        # fpdf2's add_font with uni=True is gone post-2.x; just register
        # a TTF and the unicode handling is automatic.
        pdf.add_font("noto", style="", fname=str(p))
        return "noto"
    except Exception as e:  # noqa: BLE001 — font registration is best-effort
        _log.warning("report_pdf_bengali_font_load_failed", error=str(e))
        return "Helvetica"


def render(
    *,
    title: str,
    columns: list[dict[str, str]],
    rows: list[dict[str, Any]],
    generated_at: datetime,
) -> bytes:
    pdf = FPDF(orientation="L", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=12)
    family = _maybe_register_bengali(pdf)
    # When a Bengali font is registered, fpdf2 supports any UTF-8.
    # Without it, we MUST coerce non-latin1 chars to "?" or fpdf2
    # raises FPDFUnicodeEncodingException on render.
    safe = _make_safer(family)
    pdf.add_page()

    # ---------- title ----------
    pdf.set_font(family, size=14)
    pdf.cell(0, 8, safe(title), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font(family, size=9)
    meta = (
        f"Generated {generated_at.isoformat(timespec='seconds')} "
        f"  -  {len(rows)} row(s)"
    )
    pdf.cell(0, 5, safe(meta), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(2)

    if not columns:
        pdf.set_font(family, size=10)
        pdf.cell(
            0, 8, "(no columns defined)",
            new_x=XPos.LMARGIN, new_y=YPos.NEXT,
        )
        return bytes(pdf.output())

    # ---------- compute column widths ----------
    page_width = pdf.w - pdf.l_margin - pdf.r_margin
    col_widths = _compute_widths(
        columns=columns, rows=rows, page_width=page_width, pdf=pdf,
        family=family, safe=safe,
    )

    # ---------- header row ----------
    pdf.set_font(family, size=9)
    for col, w in zip(columns, col_widths, strict=True):
        pdf.cell(
            w, 7,
            safe(str(col.get("label") or col.get("key", ""))),
            border=1,
        )
    pdf.ln(7)

    # ---------- data rows ----------
    pdf.set_font(family, size=8)
    for r in rows:
        # Page-break detection — repeat header if we'd start a new page.
        if pdf.get_y() > pdf.h - pdf.b_margin - 8:
            pdf.add_page()
            pdf.set_font(family, size=9)
            for col, w in zip(columns, col_widths, strict=True):
                pdf.cell(
                    w, 7,
                    safe(str(col.get("label") or col.get("key", ""))),
                    border=1,
                )
            pdf.ln(7)
            pdf.set_font(family, size=8)

        for col, w in zip(columns, col_widths, strict=True):
            val = format_cell(r.get(col.get("key", "")), col.get("type"))
            pdf.cell(w, 6, _truncate(safe(val), w, pdf), border=1)
        pdf.ln(6)

    return bytes(pdf.output())


def _compute_widths(
    *,
    columns: list[dict[str, str]],
    rows: list[dict[str, Any]],
    page_width: float,
    pdf: FPDF,
    family: str,
    safe,
) -> list[float]:
    """Heuristic: width proportional to longest cell (header/data) per col,
    sample-capped, then normalised to fit page_width.
    """
    pdf.set_font(family, size=8)
    weights: list[float] = []
    for col in columns:
        header = safe(str(col.get("label") or col.get("key", "")))
        sample = (
            safe(format_cell(r.get(col.get("key", "")), col.get("type")))
            for r in rows[:100]
        )
        max_text = max([header, *sample], key=len, default=header)
        # Use approx width via fpdf string-width (mm).
        weights.append(max(15.0, pdf.get_string_width(max_text) + 4.0))
    total = sum(weights) or 1.0
    return [w / total * page_width for w in weights]


def _make_safer(family: str):
    """Return a function that coerces text to be renderable in ``family``.

    For the default Helvetica fallback we keep latin-1 only — anything
    else becomes "?" (matches the user-facing degradation contract:
    Bengali product names render as boxes/marks unless a NotoSansBengali
    TTF is registered via ``REPORT_PDF_BENGALI_FONT_PATH``).
    """
    if family != "Helvetica":
        return lambda s: s if isinstance(s, str) else str(s)

    def _coerce(s) -> str:
        if not isinstance(s, str):
            s = str(s)
        out = []
        for ch in s:
            if ord(ch) <= 0xFF:
                out.append(ch)
            else:
                out.append("?")
        return "".join(out)

    return _coerce


def _truncate(text: str, width_mm: float, pdf: FPDF) -> str:
    """If text is wider than the cell, chop and append an ellipsis.
    Avoids fpdf complaining about too-wide content + falling off page.
    """
    if pdf.get_string_width(text) <= width_mm - 1.5:
        return text
    # Binary-ish chop until it fits.
    while text and pdf.get_string_width(text + "…") > width_mm - 1.5:
        text = text[:-1]
    return (text + "…") if text else ""
