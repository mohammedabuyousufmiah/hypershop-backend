"""Tests for the settlement-pending carve-out in eligibility checks.

A rider who has submitted MFS settlements covering their full payable
shouldn't get assignment-locked just because finance hasn't verified
yet — they did their part.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest


def _wallet(
    *,
    payable=Decimal("0"),
    pending=Decimal("0"),
    locked=False,
    frozen=False,
    cf_approved=False,
    cf_amount=Decimal("0"),
    cf_expires=None,
    locked_reason=None,
):
    return SimpleNamespace(
        is_frozen=frozen,
        assignment_locked=locked,
        assignment_locked_reason=locked_reason,
        wallet_payable_to_company=payable,
        wallet_pending_settlement=pending,
        carry_forward_approved=cf_approved,
        carry_forward_amount=cf_amount,
        carry_forward_expires_at=cf_expires,
    )


# ---------------- AssignmentEligibilityService.check ----------------

@pytest.mark.asyncio
async def test_check_allows_when_pending_covers_full_payable():
    """Carve-out: rider submitted settlement covering full payable →
    eligible to start shift even though finance hasn't verified.
    """
    from app.modules.rider_wallet.service import (
        AssignmentEligibilityService,
    )
    rider_id = uuid4()
    wallet = _wallet(payable=Decimal("100"), pending=Decimal("100"))
    svc = AssignmentEligibilityService.__new__(AssignmentEligibilityService)
    svc.wallets = SimpleNamespace(
        get_by_rider=AsyncMock(return_value=wallet),
    )
    ok, reason = await svc.check(rider_id=rider_id)
    assert ok is True
    assert reason is None


@pytest.mark.asyncio
async def test_check_allows_when_pending_exceeds_payable():
    """Pending > payable (rider over-submitted) is also fine."""
    from app.modules.rider_wallet.service import (
        AssignmentEligibilityService,
    )
    wallet = _wallet(payable=Decimal("100"), pending=Decimal("150"))
    svc = AssignmentEligibilityService.__new__(AssignmentEligibilityService)
    svc.wallets = SimpleNamespace(
        get_by_rider=AsyncMock(return_value=wallet),
    )
    ok, reason = await svc.check(rider_id=uuid4())
    assert ok is True


@pytest.mark.asyncio
async def test_check_blocks_when_pending_only_partial():
    """Partial settlement (pending < payable) doesn't earn the
    carve-out — rider is short.
    """
    from app.modules.rider_wallet.service import (
        AssignmentEligibilityService,
    )
    wallet = _wallet(payable=Decimal("100"), pending=Decimal("60"))
    svc = AssignmentEligibilityService.__new__(AssignmentEligibilityService)
    svc.wallets = SimpleNamespace(
        get_by_rider=AsyncMock(return_value=wallet),
    )
    ok, reason = await svc.check(rider_id=uuid4())
    assert ok is False
    assert "settlement" in reason.lower()


@pytest.mark.asyncio
async def test_check_blocks_when_no_pending_and_no_carry_forward():
    """The original baseline: payable but nothing else → blocked."""
    from app.modules.rider_wallet.service import (
        AssignmentEligibilityService,
    )
    wallet = _wallet(payable=Decimal("100"))
    svc = AssignmentEligibilityService.__new__(AssignmentEligibilityService)
    svc.wallets = SimpleNamespace(
        get_by_rider=AsyncMock(return_value=wallet),
    )
    ok, reason = await svc.check(rider_id=uuid4())
    assert ok is False


@pytest.mark.asyncio
async def test_check_frozen_wallet_blocks_even_with_full_pending():
    """Frozen overrides everything — including the pending carve-out."""
    from app.modules.rider_wallet.service import (
        AssignmentEligibilityService,
    )
    wallet = _wallet(
        payable=Decimal("100"), pending=Decimal("100"), frozen=True,
    )
    svc = AssignmentEligibilityService.__new__(AssignmentEligibilityService)
    svc.wallets = SimpleNamespace(
        get_by_rider=AsyncMock(return_value=wallet),
    )
    ok, reason = await svc.check(rider_id=uuid4())
    assert ok is False
    assert "frozen" in reason.lower()


@pytest.mark.asyncio
async def test_check_carry_forward_still_allows():
    """Active carry-forward continues to allow — independent path
    from the new pending carve-out.
    """
    from app.modules.rider_wallet.service import (
        AssignmentEligibilityService,
    )
    wallet = _wallet(
        payable=Decimal("100"),
        cf_approved=True,
        cf_amount=Decimal("100"),
        cf_expires=datetime.now(timezone.utc) + timedelta(hours=12),
    )
    svc = AssignmentEligibilityService.__new__(AssignmentEligibilityService)
    svc.wallets = SimpleNamespace(
        get_by_rider=AsyncMock(return_value=wallet),
    )
    ok, _ = await svc.check(rider_id=uuid4())
    assert ok is True


# ---------------- apply_lock_if_unpaid ----------------

@pytest.mark.asyncio
async def test_apply_lock_skips_when_pending_covers_payable():
    """Nightly sweep should leave a rider with full pending alone."""
    from app.modules.rider_wallet.service import (
        AssignmentEligibilityService,
    )
    wallet = _wallet(payable=Decimal("100"), pending=Decimal("100"))
    svc = AssignmentEligibilityService.__new__(AssignmentEligibilityService)
    svc.session = None  # not touched on this code path
    svc.wallets = SimpleNamespace(
        get_by_rider=AsyncMock(return_value=wallet),
    )
    locked = await svc.apply_lock_if_unpaid(rider_id=uuid4())
    assert locked is False


@pytest.mark.asyncio
async def test_apply_lock_runs_when_pending_partial():
    """Partial pending — sweep should still fire."""
    from app.modules.rider_wallet.service import (
        AssignmentEligibilityService,
        RiderWalletService,
    )
    wallet = _wallet(payable=Decimal("100"), pending=Decimal("40"))
    wallet.id = uuid4()
    svc = AssignmentEligibilityService.__new__(AssignmentEligibilityService)
    svc.session = SimpleNamespace()
    svc.wallets = SimpleNamespace(
        get_by_rider=AsyncMock(return_value=wallet),
    )
    # Patch RiderWalletService.lock to track invocation without DB.
    called = {}

    async def fake_lock(self, *, rider_id, reason, principal):  # noqa: ARG001
        called["rider_id"] = rider_id
        called["reason"] = reason
        return wallet

    import app.modules.rider_wallet.service as svc_mod
    orig_init = svc_mod.RiderWalletService.__init__
    orig_lock = svc_mod.RiderWalletService.lock
    svc_mod.RiderWalletService.__init__ = lambda self, session: None
    svc_mod.RiderWalletService.lock = fake_lock
    try:
        result = await svc.apply_lock_if_unpaid(rider_id=uuid4())
    finally:
        svc_mod.RiderWalletService.__init__ = orig_init
        svc_mod.RiderWalletService.lock = orig_lock
    assert result is True
    assert called["reason"] == "Previous-day settlement due"
