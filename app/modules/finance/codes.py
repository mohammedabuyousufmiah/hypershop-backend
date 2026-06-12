"""Code generators for human-readable identifiers.

Journal entries use a calendar-year prefix so an auditor reading a JE code
can immediately tell the fiscal year. Bills/deposits/refunds use scan-safe
random suffixes (no I/O/0/1) so handwritten reconciliation notes don't
get misread.
"""

from __future__ import annotations

import secrets
from datetime import date

_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _suffix(n: int) -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(n))


def make_journal_code(today: date | None = None) -> str:
    today = today or date.today()
    return f"JE-{today.year}-{_suffix(8)}"


def make_supplier_bill_code() -> str:
    return "BILL-" + _suffix(8)


def make_supplier_payment_code() -> str:
    return "PAY-" + _suffix(8)


def make_cod_deposit_code() -> str:
    return "COD-" + _suffix(8)


def make_refund_code() -> str:
    return "RFD-" + _suffix(8)
