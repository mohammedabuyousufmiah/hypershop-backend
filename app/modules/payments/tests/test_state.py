"""Pure-Python tests for the state-machine helpers (no DB needed)."""

from __future__ import annotations

import pytest

from app.modules.payments.state import (
    PaymentStatus,
    can_transition,
    is_terminal,
)


def test_terminal_states() -> None:
    assert is_terminal(PaymentStatus.CAPTURED.value)
    assert is_terminal(PaymentStatus.FAILED.value)
    assert is_terminal(PaymentStatus.CANCELLED.value)
    assert is_terminal(PaymentStatus.EXPIRED.value)
    assert is_terminal(PaymentStatus.REFUNDED.value)
    # Non-terminal:
    assert not is_terminal(PaymentStatus.INITIATED.value)
    assert not is_terminal(PaymentStatus.AUTHORIZED.value)
    # PARTIALLY_REFUNDED is non-terminal because more refunds can land:
    assert not is_terminal(PaymentStatus.PARTIALLY_REFUNDED.value)


@pytest.mark.parametrize(
    ("from_state", "to_state"),
    [
        ("initiated", "authorized"),
        ("initiated", "captured"),
        ("initiated", "failed"),
        ("initiated", "cancelled"),
        ("initiated", "expired"),
        ("authorized", "captured"),
        ("authorized", "failed"),
        ("authorized", "cancelled"),
        ("captured", "partially_refunded"),
        ("captured", "refunded"),
        ("partially_refunded", "refunded"),
    ],
)
def test_allowed_transitions(from_state: str, to_state: str) -> None:
    assert can_transition(from_state, to_state)


@pytest.mark.parametrize(
    ("from_state", "to_state"),
    [
        # Cannot go backwards
        ("captured", "initiated"),
        ("captured", "authorized"),
        ("captured", "failed"),
        # Cannot go forwards from terminal failure states
        ("failed", "captured"),
        ("cancelled", "captured"),
        ("expired", "captured"),
        ("refunded", "captured"),
        # Cannot skip from initiated to refunded
        ("initiated", "refunded"),
        ("initiated", "partially_refunded"),
    ],
)
def test_disallowed_transitions(from_state: str, to_state: str) -> None:
    assert not can_transition(from_state, to_state)
