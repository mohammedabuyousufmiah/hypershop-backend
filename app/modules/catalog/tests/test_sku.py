from __future__ import annotations

import pytest

from app.modules.catalog.sku import (
    generate_mother_sku,
    is_valid_barcode,
    is_valid_mother_sku,
    variant_sku_for,
)


def test_generated_mother_sku_matches_format() -> None:
    for _ in range(50):
        sku = generate_mother_sku()
        assert is_valid_mother_sku(sku)


def test_generated_mother_sku_has_no_ambiguous_glyphs() -> None:
    forbidden = set("IO01")
    for _ in range(200):
        sku = generate_mother_sku()
        body = sku.split("-", 1)[1]
        assert not (set(body) & forbidden), f"ambiguous glyph in {sku}"


def test_variant_sku_zero_pads_three_digits() -> None:
    assert variant_sku_for("HS-ABCDEFGH", index=1) == "HS-ABCDEFGH-V001"
    assert variant_sku_for("HS-ABCDEFGH", index=42) == "HS-ABCDEFGH-V042"
    assert variant_sku_for("HS-ABCDEFGH", index=999) == "HS-ABCDEFGH-V999"


def test_variant_sku_rejects_out_of_range_index() -> None:
    with pytest.raises(ValueError):
        variant_sku_for("HS-ABCDEFGH", index=0)
    with pytest.raises(ValueError):
        variant_sku_for("HS-ABCDEFGH", index=1000)


def test_barcode_valid_lengths() -> None:
    assert is_valid_barcode("12345678")
    assert is_valid_barcode("8901030865278")
    assert is_valid_barcode("ABCD1234ZZ")
    assert is_valid_barcode("a" * 64)


def test_barcode_invalid_inputs() -> None:
    assert not is_valid_barcode("")
    assert not is_valid_barcode("1234567")  # 7 chars — too short
    assert not is_valid_barcode("a" * 65)  # too long
    assert not is_valid_barcode("abc 123")  # whitespace
    assert not is_valid_barcode("hello!")  # punctuation
