"""Tests for the BDT quantize helper used everywhere in the service."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.modules.rider_wallet.service import _q


@pytest.mark.parametrize(
    ("inp", "expected"),
    [
        (Decimal("100"), Decimal("100.00")),
        (Decimal("100.5"), Decimal("100.50")),
        (Decimal("100.555"), Decimal("100.56")),  # ROUND_HALF_EVEN
        (Decimal("100.554"), Decimal("100.55")),
        (100, Decimal("100.00")),
        (100.5, Decimal("100.50")),
        ("100.50", Decimal("100.50")),
        (Decimal("0.00"), Decimal("0.00")),
        (Decimal("0.001"), Decimal("0.00")),
    ],
)
def test_q_quantizes_to_2dp(inp, expected):
    assert _q(inp) == expected


def test_q_returns_decimal_instance():
    assert isinstance(_q("12.345"), Decimal)
