"""Phase 1.B wallet tests — credit / debit / idempotency / pagination."""
from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from app.core.db.session import get_sessionmaker
from app.modules.ads import repository as repo
from app.modules.ads import service as ads_service

pytestmark = pytest.mark.integration


async def _make_seller(s, slug: str = "ads-test-seller") -> UUID:
    row = (
        await s.execute(
            text(
                """
                INSERT INTO sellers (
                    business_name, slug, status, commission_percent,
                    payout_cadence, payout_method
                ) VALUES (
                    :n, :s, 'approved', 0.00, 'monthly', 'bank_transfer'
                )
                ON CONFLICT (slug) DO UPDATE SET status = 'approved'
                RETURNING id
                """
            ),
            {"n": slug.replace("-", " ").title(), "s": slug},
        )
    ).scalar_one()
    return row


async def test_get_balance_creates_wallet_if_missing() -> None:
    sm = get_sessionmaker()
    async with sm() as s, s.begin():
        seller_id = await _make_seller(s, slug="ads-seller-a")
        data = await ads_service.get_balance(s, seller_id)
    assert data["balance_minor"] == 0
    assert data["total_spent_minor"] == 0
    assert data["currency"] == "BDT"
    assert data["auto_recharge_enabled"] is False


async def test_credit_increments_balance() -> None:
    sm = get_sessionmaker()
    async with sm() as s, s.begin():
        seller_id = await _make_seller(s, slug="ads-seller-b")
        await repo.credit_ad_wallet(
            s,
            seller_id=seller_id,
            amount_minor=50_000,
            source_type="bkash_recharge",
            source_id=uuid4(),
            memo="initial",
        )
        data = await ads_service.get_balance(s, seller_id)
    assert data["balance_minor"] == 50_000


async def test_credit_idempotent_on_same_source() -> None:
    sm = get_sessionmaker()
    async with sm() as s, s.begin():
        seller_id = await _make_seller(s, slug="ads-seller-c")
        src = uuid4()
        await repo.credit_ad_wallet(
            s, seller_id=seller_id, amount_minor=50_000,
            source_type="bkash_recharge", source_id=src, memo="first",
        )
        await repo.credit_ad_wallet(
            s, seller_id=seller_id, amount_minor=50_000,
            source_type="bkash_recharge", source_id=src, memo="dup",
        )
        data = await ads_service.get_balance(s, seller_id)
        rows, total = await repo.list_ad_wallet_txns(s, seller_id=seller_id)
    assert data["balance_minor"] == 50_000
    assert total == 1


async def test_debit_succeeds_when_balance_sufficient() -> None:
    sm = get_sessionmaker()
    async with sm() as s, s.begin():
        seller_id = await _make_seller(s, slug="ads-seller-d")
        await repo.credit_ad_wallet(
            s, seller_id=seller_id, amount_minor=50_000,
            source_type="bkash_recharge", source_id=uuid4(),
        )
        await repo.debit_ad_wallet(
            s, seller_id=seller_id, amount_minor=10_000,
            source_type="ad_clicks", source_id=uuid4(),
            memo="click-1",
        )
        data = await ads_service.get_balance(s, seller_id)
    assert data["balance_minor"] == 40_000
    assert data["total_spent_minor"] == 10_000


async def test_debit_raises_when_insufficient() -> None:
    sm = get_sessionmaker()
    async with sm() as s, s.begin():
        seller_id = await _make_seller(s, slug="ads-seller-e")
        with pytest.raises(ads_service.InsufficientAdBalance):
            await repo.debit_ad_wallet(
                s, seller_id=seller_id, amount_minor=100,
                source_type="ad_clicks", source_id=uuid4(),
            )


async def test_debit_idempotent() -> None:
    sm = get_sessionmaker()
    async with sm() as s, s.begin():
        seller_id = await _make_seller(s, slug="ads-seller-f")
        await repo.credit_ad_wallet(
            s, seller_id=seller_id, amount_minor=50_000,
            source_type="bkash_recharge", source_id=uuid4(),
        )
        click_id = uuid4()
        await repo.debit_ad_wallet(
            s, seller_id=seller_id, amount_minor=10_000,
            source_type="ad_clicks", source_id=click_id,
        )
        await repo.debit_ad_wallet(
            s, seller_id=seller_id, amount_minor=10_000,
            source_type="ad_clicks", source_id=click_id,
        )
        data = await ads_service.get_balance(s, seller_id)
    assert data["balance_minor"] == 40_000
    assert data["total_spent_minor"] == 10_000


async def test_list_transactions_orders_desc_paginates() -> None:
    sm = get_sessionmaker()
    async with sm() as s, s.begin():
        seller_id = await _make_seller(s, slug="ads-seller-g")
        for _ in range(3):
            await repo.credit_ad_wallet(
                s, seller_id=seller_id, amount_minor=10_000,
                source_type="bkash_recharge", source_id=uuid4(),
            )
        await repo.debit_ad_wallet(
            s, seller_id=seller_id, amount_minor=5_000,
            source_type="ad_clicks", source_id=uuid4(),
        )
        rows, total = await repo.list_ad_wallet_txns(
            s, seller_id=seller_id, limit=2, offset=0,
        )
    assert total == 4
    assert len(rows) == 2
    # Latest first; most recent op was the debit.
    assert rows[0].amount_minor < 0


async def test_initiate_recharge_creates_pending_row() -> None:
    """Phase 1.B — initiate_recharge inserts a recharge row and the
    provider call flips it to status='pending' with a redirect URL."""
    from app.modules.ads.models import HypershopAdWalletRecharge
    from app.modules.payments.providers import (
        bind_provider, reset_provider_binding,
    )
    from app.modules.payments.providers.fake import FakePaymentProvider
    from sqlalchemy import select

    # FakePaymentProvider auto-succeeds create_payment, returning a
    # synthetic provider_payment_id + checkout URL.
    reset_provider_binding()
    bind_provider("bkash", FakePaymentProvider(public_base_url="http://localhost:3000"))
    try:
        sm = get_sessionmaker()
        async with sm() as s, s.begin():
            seller_id = await _make_seller(s, slug="ads-seller-init")
            data = await ads_service.initiate_recharge(
                s, seller_id=seller_id, amount_minor=50_000,
                bkash_msisdn="+8801711234567",
            )
            assert data["status"] == "pending"
            assert data["recharge_id"]
            assert data["redirect_url"].startswith("http")

            row = (
                await s.execute(
                    select(HypershopAdWalletRecharge).where(
                        HypershopAdWalletRecharge.seller_id == seller_id,
                    )
                )
            ).scalar_one()
            assert row.status == "pending"
            assert row.provider == "bkash"
            assert row.provider_payment_id is not None
            assert row.amount_minor == 50_000
    finally:
        reset_provider_binding()


async def test_callback_credits_wallet_idempotent() -> None:
    """Phase 1.B — finalize_recharge_from_callback credits exactly once
    even when the same provider_payment_id is delivered twice."""
    from app.modules.payments.providers import (
        bind_provider, reset_provider_binding,
    )
    from app.modules.payments.providers.fake import FakePaymentProvider

    reset_provider_binding()
    bind_provider("bkash", FakePaymentProvider(public_base_url="http://localhost:3000"))
    try:
        sm = get_sessionmaker()
        # Initiate the recharge — produces a persisted recharge row
        # with provider_payment_id set by the fake provider.
        async with sm() as s, s.begin():
            seller_id = await _make_seller(s, slug="ads-seller-cbk")
            data = await ads_service.initiate_recharge(
                s, seller_id=seller_id, amount_minor=80_000,
                bkash_msisdn="+8801711234567",
            )
            ppid = data["payment_id"]

        # First callback delivery — should credit.
        async with sm() as s, s.begin():
            await ads_service.finalize_recharge_from_callback(
                s, provider="bkash", provider_payment_id=ppid,
            )

        # Second callback delivery — must NOT double-credit.
        async with sm() as s, s.begin():
            await ads_service.finalize_recharge_from_callback(
                s, provider="bkash", provider_payment_id=ppid,
            )

        async with sm() as s, s.begin():
            balance = await ads_service.get_balance(s, seller_id)
            rows, total = await repo.list_ad_wallet_txns(s, seller_id=seller_id)
        assert balance["balance_minor"] == 80_000
        assert total == 1
    finally:
        reset_provider_binding()


async def test_debit_wallet_atomic_via_service() -> None:
    """Phase 1.B — service.debit_wallet decrements balance + records txn."""
    sm = get_sessionmaker()
    async with sm() as s, s.begin():
        seller_id = await _make_seller(s, slug="ads-seller-debit")
        await repo.credit_ad_wallet(
            s, seller_id=seller_id, amount_minor=50_000,
            source_type="bkash_recharge", source_id=uuid4(),
        )
        await ads_service.debit_wallet(
            s, seller_id=seller_id, amount_minor=20_000,
            source_type="ad_clicks", source_id=uuid4(), memo="auction-click",
        )
        data = await ads_service.get_balance(s, seller_id)
    assert data["balance_minor"] == 30_000
    assert data["total_spent_minor"] == 20_000


async def test_debit_insufficient_funds_via_service() -> None:
    """Phase 1.B — service.debit_wallet raises on empty wallet."""
    sm = get_sessionmaker()
    async with sm() as s, s.begin():
        seller_id = await _make_seller(s, slug="ads-seller-empty")
        with pytest.raises(ads_service.AdWalletInsufficientFunds):
            await ads_service.debit_wallet(
                s, seller_id=seller_id, amount_minor=100,
                source_type="ad_clicks", source_id=uuid4(),
            )


async def test_manual_credit_admin_path() -> None:
    """Phase 1.B — admin manual_credit reflects in balance + memo carries
    admin user id for audit trail."""
    sm = get_sessionmaker()
    async with sm() as s, s.begin():
        seller_id = await _make_seller(s, slug="ads-seller-goodwill")
        admin_user_id = uuid4()
        txn = await ads_service.manual_credit(
            s, seller_id=seller_id, amount_minor=100_000,
            admin_user_id=admin_user_id, memo="goodwill",
        )
        assert txn.amount_minor == 100_000
        assert str(admin_user_id) in (txn.memo or "")
        data = await ads_service.get_balance(s, seller_id)
    assert data["balance_minor"] == 100_000


async def test_initiate_recharge_provider_failure_marks_failed() -> None:
    """Phase 1.B — when no provider is bound (or it raises
    IntegrationError), the recharge row is marked status='failed' and
    AdRechargeProviderError is raised for the API layer to 502."""
    from app.modules.ads.models import HypershopAdWalletRecharge
    from app.modules.payments.providers import reset_provider_binding
    from sqlalchemy import select

    reset_provider_binding()  # No provider bound at all → NotConfigured
    try:
        sm = get_sessionmaker()
        async with sm() as s, s.begin():
            seller_id = await _make_seller(s, slug="ads-seller-noprovider")
            with pytest.raises(ads_service.AdRechargeProviderError):
                await ads_service.initiate_recharge(
                    s, seller_id=seller_id, amount_minor=50_000,
                    bkash_msisdn="+8801711234567",
                )
            # The recharge row should exist and be marked failed.
            row = (
                await s.execute(
                    select(HypershopAdWalletRecharge).where(
                        HypershopAdWalletRecharge.seller_id == seller_id,
                    )
                )
            ).scalar_one()
            assert row.status == "failed"
            assert row.failure_reason is not None
    finally:
        reset_provider_binding()


async def test_auto_recharge_config_update() -> None:
    sm = get_sessionmaker()
    async with sm() as s, s.begin():
        seller_id = await _make_seller(s, slug="ads-seller-h")
        data = await ads_service.update_auto_recharge(
            s, seller_id=seller_id, enabled=True,
            threshold_minor=50_000, amount_minor=200_000,
        )
    assert data["auto_recharge_enabled"] is True
    assert data["auto_recharge_threshold_minor"] == 50_000
    assert data["auto_recharge_amount_minor"] == 200_000

    async with sm() as s, s.begin():
        data = await ads_service.update_auto_recharge(
            s, seller_id=seller_id, enabled=False,
            threshold_minor=None, amount_minor=None,
        )
    assert data["auto_recharge_enabled"] is False
    assert data["auto_recharge_threshold_minor"] is None
    assert data["auto_recharge_amount_minor"] is None
