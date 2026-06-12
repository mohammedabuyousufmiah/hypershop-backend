from __future__ import annotations

import re
import unicodedata

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def slugify(value: str, *, max_length: int = 80) -> str:
    """Lowercase ASCII slug. Strips diacritics, collapses runs of
    non-alphanumeric to a single hyphen, trims leading/trailing hyphens.
    """
    if not value:
        raise ValueError("cannot slugify empty string")
    nfkd = unicodedata.normalize("NFKD", value)
    ascii_only = nfkd.encode("ascii", "ignore").decode("ascii")
    lower = ascii_only.lower()
    s = _NON_ALNUM.sub("-", lower).strip("-")
    if not s:
        raise ValueError(f"slug is empty after normalization: {value!r}")
    return s[:max_length].rstrip("-")
