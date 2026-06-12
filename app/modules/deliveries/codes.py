from __future__ import annotations

import secrets

_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def make_rider_code() -> str:
    return "RD-" + "".join(secrets.choice(_ALPHABET) for _ in range(6))
