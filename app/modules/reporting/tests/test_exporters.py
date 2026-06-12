"""Unit tests for the three exporters (no DB)."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from app.modules.reporting.exporters import csv_exporter, pdf_exporter, xlsx_exporter

_COLS = [
    {"key": "name", "label": "Name", "type": "string"},
    {"key": "amount", "label": "Amount", "type": "money"},
    {"key": "issued_on", "label": "Issued", "type": "date"},
    {"key": "rate", "label": "Rate", "type": "ratio"},
]
_ROWS = [
    {
        "name": "Paracetamol",
        "amount": Decimal("1234.50"),
        "issued_on": date(2026, 5, 4),
        "rate": Decimal("0.0234"),
    },
    {
        # Bengali product name — exporters must not mangle UTF-8.
        "name": "প্যারাসিটামল",
        "amount": Decimal("0"),
        "issued_on": date(2026, 5, 4),
        "rate": Decimal("0.5"),
    },
]
_TS = datetime(2026, 5, 4, 12, 0, tzinfo=timezone.utc)


def test_csv_writes_bom_and_utf8():
    out = csv_exporter.render(
        title="Sales", columns=_COLS, rows=_ROWS, generated_at=_TS,
    )
    assert isinstance(out, bytes)
    # UTF-8 BOM as first 3 bytes.
    assert out.startswith(b"\xef\xbb\xbf"), "CSV should start with UTF-8 BOM"
    text = out[3:].decode("utf-8")
    # Header present.
    assert text.startswith("Name,Amount,Issued,Rate")
    # Bengali product name preserved.
    assert "প্যারাসিটামল" in text
    # Money formatted to 2dp with comma thousands.
    assert '"1,234.50"' in text or "1,234.50" in text
    # Ratio rendered as percent.
    assert "2.34%" in text


def test_xlsx_returns_zip_envelope():
    out = xlsx_exporter.render(
        title="Sales", columns=_COLS, rows=_ROWS, generated_at=_TS,
    )
    # XLSX is a ZIP — the magic bytes "PK\x03\x04" must be present.
    assert out[:4] == b"PK\x03\x04"
    # Bengali bytes are inside the zip's xml — won't be visible without
    # parsing, so a length sanity check is the cheapest signal.
    assert len(out) > 1000


def test_pdf_returns_pdf_header():
    out = pdf_exporter.render(
        title="Sales", columns=_COLS, rows=_ROWS, generated_at=_TS,
    )
    # All PDFs start with "%PDF-".
    assert out.startswith(b"%PDF-")
    assert len(out) > 500


def test_csv_handles_empty_rows():
    out = csv_exporter.render(
        title="Empty", columns=_COLS, rows=[], generated_at=_TS,
    )
    text = out[3:].decode("utf-8")
    # Header line only — no data rows.
    assert text.strip() == "Name,Amount,Issued,Rate"
