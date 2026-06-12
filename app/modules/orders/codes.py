from __future__ import annotations

import secrets
from datetime import date

_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def make_order_code(*, today: date | None = None) -> str:
    d = (today or date.today()).strftime("%Y%m%d")
    tail = "".join(secrets.choice(_ALPHABET) for _ in range(5))
    return f"HSO-{d}-{tail}"
