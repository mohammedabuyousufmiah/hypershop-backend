from __future__ import annotations

import re
import secrets

_MOTHER_PREFIX = "HS"
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no I/O/0/1 to avoid scan ambiguity
_MOTHER_LEN = 8

_MOTHER_RE = re.compile(rf"^{_MOTHER_PREFIX}-[A-Z0-9]{{{_MOTHER_LEN}}}$")
_BARCODE_RE = re.compile(r"^[A-Za-z0-9]{8,64}$")


def generate_mother_sku() -> str:
    """A scan-safe mother SKU.

    Format: ``HS-XXXXXXXX`` where X is from a 32-char alphabet that excludes
    visually ambiguous glyphs (I/O/0/1). 32^8 ≈ 1.1e12 — collision risk is
    negligible in practice; the service still retries on the unique-constraint
    violation as a belt-and-braces measure.
    """
    body = "".join(secrets.choice(_ALPHABET) for _ in range(_MOTHER_LEN))
    return f"{_MOTHER_PREFIX}-{body}"


def is_valid_mother_sku(value: str) -> bool:
    return bool(_MOTHER_RE.match(value))


def variant_sku_for(mother_sku: str, *, index: int) -> str:
    """Deterministic variant SKU under a mother. Index is 1-based."""
    if index < 1 or index > 999:
        raise ValueError("variant index must be 1..999")
    return f"{mother_sku}-V{index:03d}"


def is_valid_barcode(value: str) -> bool:
    """Accept EAN-8/13, UPC-A/E, GTIN-14, Code128 alphanumeric — anything 8–64
    chars from ``[A-Za-z0-9]``. Stricter checksum validation (EAN13 mod-10)
    is intentionally deferred to the inventory module that owns physical scan.
    """
    return bool(_BARCODE_RE.match(value))
