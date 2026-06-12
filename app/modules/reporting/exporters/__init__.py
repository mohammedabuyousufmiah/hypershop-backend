"""Export pipeline — turns rows + columns into a downloadable file.

Each exporter is a single function::

    def render(*, title, columns, rows, generated_at) -> bytes

The service layer picks one based on requested format, calls
:func:`storage.write_atomically`, and returns a signed URL.
"""

from __future__ import annotations

from app.modules.reporting.exporters import csv_exporter, pdf_exporter, xlsx_exporter
from app.modules.reporting.state import ExportFormat

_RENDERERS = {
    ExportFormat.CSV: csv_exporter.render,
    ExportFormat.XLSX: xlsx_exporter.render,
    ExportFormat.PDF: pdf_exporter.render,
}


def render(
    *,
    fmt: str,
    title: str,
    columns: list[dict[str, str]],
    rows: list[dict],
    generated_at,
) -> bytes:
    """Dispatch to the right exporter. Raises KeyError for unknown fmt."""
    return _RENDERERS[ExportFormat(fmt)](
        title=title,
        columns=columns,
        rows=rows,
        generated_at=generated_at,
    )
