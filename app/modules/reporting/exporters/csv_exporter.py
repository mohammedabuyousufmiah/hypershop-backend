"""CSV exporter — UTF-8 with BOM so Excel-on-Windows opens it cleanly.

The BOM is the cheapest way to make Excel auto-detect UTF-8 (without
it, Excel guesses the system codepage and Bengali becomes mojibake).
"""

from __future__ import annotations

import csv
import io
from datetime import datetime
from typing import Any

from app.modules.reporting.exporters._format import format_cell


def render(
    *,
    title: str,  # noqa: ARG001 — title is in the filename, not the CSV body
    columns: list[dict[str, str]],
    rows: list[dict[str, Any]],
    generated_at: datetime,  # noqa: ARG001
) -> bytes:
    buf = io.StringIO(newline="")
    # csv.writer with QUOTE_MINIMAL — Excel parses quoted commas
    # correctly. Use lineterminator="\n" so file size matches our
    # SHA computation regardless of platform.
    writer = csv.writer(buf, quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
    headers = [c.get("label") or c.get("key", "") for c in columns]
    writer.writerow(headers)
    for r in rows:
        writer.writerow([
            format_cell(r.get(c.get("key", "")), c.get("type"))
            for c in columns
        ])
    # ﻿ = UTF-8 BOM (read by Excel as "this is UTF-8").
    return ("﻿" + buf.getvalue()).encode("utf-8")
