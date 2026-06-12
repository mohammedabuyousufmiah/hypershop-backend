from __future__ import annotations

import secrets
from datetime import date

_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_TAIL_LEN = 5


def _tail() -> str:
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_TAIL_LEN))


def make_po_code(*, today: date | None = None) -> str:
    d = (today or date.today()).strftime("%Y%m%d")
    return f"PO-{d}-{_tail()}"


def make_gr_code(*, today: date | None = None) -> str:
    d = (today or date.today()).strftime("%Y%m%d")
    return f"GR-{d}-{_tail()}"
