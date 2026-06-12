"""Pure-function tests for the wallet-status derivation helper.

The DB writes are async + need a session, so these focus on the
priority logic in ``RiderWalletService._derive_status`` which is
@staticmethod and can be exercised with a SimpleNamespace stand-in.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.modules.rider_wallet.service import RiderWalletService
from app.modules.rider_wallet.state import WalletStatus


def _w(*, frozen=False, locked=False, payable=Decimal("0"), pending=Decimal("0")):
    return SimpleNamespace(
        is_frozen=frozen,
        assignment_locked=locked,
        wallet_payable_to_company=payable,
        wallet_pending_settlement=pending,
    )


def test_frozen_wins_over_everything():
    assert RiderWalletService._derive_status(
        _w(frozen=True, locked=True, payable=Decimal("100")),
        payable=Decimal("100"), pending=Decimal("0"),
    ) == WalletStatus.FROZEN.value


def test_overdue_blocked_when_locked():
    assert RiderWalletService._derive_status(
        _w(locked=True, payable=Decimal("50")),
        payable=Decimal("50"), pending=Decimal("0"),
    ) == WalletStatus.OVERDUE_BLOCKED.value


def test_partially_settled_when_pending_lt_payable():
    assert RiderWalletService._derive_status(
        _w(payable=Decimal("100"), pending=Decimal("40")),
        payable=Decimal("100"), pending=Decimal("40"),
    ) == WalletStatus.PARTIALLY_SETTLED.value


def test_settlement_submitted_when_pending_only():
    # Payable already 0 (e.g. excess pending) — pure pending state.
    assert RiderWalletService._derive_status(
        _w(payable=Decimal("0"), pending=Decimal("100")),
        payable=Decimal("0"), pending=Decimal("100"),
    ) == WalletStatus.SETTLEMENT_SUBMITTED.value


def test_has_cod_balance_when_payable_only():
    assert RiderWalletService._derive_status(
        _w(payable=Decimal("100"), pending=Decimal("0")),
        payable=Decimal("100"), pending=Decimal("0"),
    ) == WalletStatus.HAS_COD_BALANCE.value


def test_clear_when_zero_zero():
    assert RiderWalletService._derive_status(
        _w(),
        payable=Decimal("0"), pending=Decimal("0"),
    ) == WalletStatus.CLEAR.value


def test_assume_unlocked_overrides_locked_flag():
    # Used by the unlock() path when computing the new status — caller
    # has decided to unlock; status should reflect post-unlock world.
    assert RiderWalletService._derive_status(
        _w(locked=True, payable=Decimal("0")),
        payable=Decimal("0"), pending=Decimal("0"),
        assume_unlocked=True,
    ) == WalletStatus.CLEAR.value


def test_assume_frozen_false_overrides_frozen_flag():
    assert RiderWalletService._derive_status(
        _w(frozen=True, payable=Decimal("0")),
        payable=Decimal("0"), pending=Decimal("0"),
        assume_frozen=False,
    ) == WalletStatus.CLEAR.value
