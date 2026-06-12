"""Pure-Python tests for the text normalizer (no DB needed)."""

from __future__ import annotations

import pytest

from app.modules.search.normalizer import (
    normalize_search_text,
    to_tsquery_string,
    tokenize_query,
)


# ---------------- normalize_search_text ----------------


@pytest.mark.parametrize(
    ("inp", "expected"),
    [
        ("Paracetamol 500mg", "paracetamol 500mg"),
        ("Café noir", "cafe noir"),  # diacritic stripped
        ("BÉNADRYL!", "benadryl"),
        ("  multiple   spaces  ", "multiple spaces"),
        ("Comma, period. Bang!", "comma period bang"),
        ("", ""),
        (None, ""),
        ("UPPER", "upper"),
        # Bengali characters survive — not in the diacritic class
        ("প্যারাসিটামল", "প্যারাসিটামল"),
    ],
)
def test_normalize_search_text(inp: str | None, expected: str) -> None:
    assert normalize_search_text(inp) == expected


# ---------------- tokenize_query ----------------


def test_tokenize_drops_short_tokens() -> None:
    # 'a' is 1 char and gets dropped; 'panadol' survives
    assert tokenize_query("a panadol") == ["panadol"]


def test_tokenize_handles_punctuation() -> None:
    assert tokenize_query("paracetamol, 500mg!") == ["paracetamol", "500mg"]


def test_tokenize_empty() -> None:
    assert tokenize_query("") == []
    assert tokenize_query(None) == []
    assert tokenize_query("a b c") == []  # all 1-char


def test_tokenize_min_len_override() -> None:
    assert tokenize_query("a panadol", min_token_len=1) == ["a", "panadol"]


# ---------------- to_tsquery_string ----------------


def test_tsquery_and_join() -> None:
    assert to_tsquery_string("paracetamol cold") == "paracetamol & cold"


def test_tsquery_prefix_mode() -> None:
    out = to_tsquery_string("para cold", prefix=True)
    assert out == "para:* & cold:*"


def test_tsquery_empty_input() -> None:
    assert to_tsquery_string("") == ""
    assert to_tsquery_string(None) == ""
    assert to_tsquery_string("a b") == ""  # all dropped


def test_tsquery_strips_punctuation_in_tokens() -> None:
    # "panadol!" should normalise to "panadol", not "panadol!"
    assert to_tsquery_string("panadol! cold") == "panadol & cold"
