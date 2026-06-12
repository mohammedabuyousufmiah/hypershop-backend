"""CRUD skeletons for the Sponsored Products module.

No transaction management — the caller's UnitOfWork owns commit/rollback.
Auction / billing / quality-score recompute live in service.py.
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.ads.codes import (
    WALLET_TXN_KIND_ADJUST,
    WALLET_TXN_KIND_CLICK_DEBIT,
    WALLET_TXN_KIND_MANUAL_CREDIT,
    WALLET_TXN_KIND_RECHARGE,
    WALLET_TXN_KIND_REFUND,
)
from app.modules.ads.models import (
    HypershopAdCampaign,
    HypershopAdGroup,
    HypershopAdWallet,
    HypershopAdWalletRecharge,
    HypershopAdWalletTxn,
)


class InsufficientAdBalance(Exception):
    """Raised when a debit would drive the wallet below zero."""


class AdWalletIdempotencyError(Exception):
    """Raised when an idempotency conflict cannot be safely resolved."""


def _kind_for_credit(source_type: str) -> str:
    if source_type in ("manual_credit", "manual"):
        return WALLET_TXN_KIND_MANUAL_CREDIT
    if source_type == "click_refund":
        return WALLET_TXN_KIND_REFUND
    if source_type == "manual_adjust":
        return WALLET_TXN_KIND_ADJUST
    return WALLET_TXN_KIND_RECHARGE


def _kind_for_debit(source_type: str) -> str:
    if source_type == "ad_clicks":
        return WALLET_TXN_KIND_CLICK_DEBIT
    if source_type == "manual_debit":
        return WALLET_TXN_KIND_MANUAL_CREDIT
    return WALLET_TXN_KIND_ADJUST


async def get_or_create_ad_wallet(
    session: AsyncSession, seller_id: UUID,
) -> HypershopAdWallet:
    existing = (
        await session.execute(
            select(HypershopAdWallet).where(
                HypershopAdWallet.seller_id == seller_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    wallet = HypershopAdWallet(seller_id=seller_id)
    session.add(wallet)
    await session.flush()
    return wallet


async def lock_ad_wallet(
    session: AsyncSession, seller_id: UUID,
) -> HypershopAdWallet:
    """Resolve the wallet row + acquire a row-level write lock."""
    # Ensure the wallet exists (without lock) — first request bootstrap.
    await get_or_create_ad_wallet(session, seller_id)
    locked = (
        await session.execute(
            select(HypershopAdWallet)
            .where(HypershopAdWallet.seller_id == seller_id)
            .with_for_update()
        )
    ).scalar_one()
    return locked


async def get_ad_wallet_txn_by_source(
    session: AsyncSession,
    source_type: str,
    source_id: UUID,
) -> HypershopAdWalletTxn | None:
    stmt = select(HypershopAdWalletTxn).where(
        HypershopAdWalletTxn.source_type == source_type,
        HypershopAdWalletTxn.source_id == source_id,
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def credit_ad_wallet(
    session: AsyncSession,
    *,
    seller_id: UUID,
    amount_minor: int,
    source_type: str,
    source_id: UUID,
    memo: str | None = None,
) -> HypershopAdWalletTxn:
    if amount_minor <= 0:
        raise ValueError("credit amount must be positive")
    existing = await get_ad_wallet_txn_by_source(session, source_type, source_id)
    if existing is not None:
        return existing
    wallet = await lock_ad_wallet(session, seller_id)
    new_balance = wallet.balance_minor + amount_minor
    txn = HypershopAdWalletTxn(
        ad_wallet_id=wallet.id,
        kind=_kind_for_credit(source_type),
        amount_minor=amount_minor,
        balance_after_minor=new_balance,
        source_type=source_type,
        source_id=source_id,
        memo=memo,
    )
    session.add(txn)
    wallet.balance_minor = new_balance
    await session.flush()
    return txn


async def debit_ad_wallet(
    session: AsyncSession,
    *,
    seller_id: UUID,
    amount_minor: int,
    source_type: str,
    source_id: UUID,
    memo: str | None = None,
) -> HypershopAdWalletTxn:
    if amount_minor <= 0:
        raise ValueError("debit amount must be positive")
    existing = await get_ad_wallet_txn_by_source(session, source_type, source_id)
    if existing is not None:
        return existing
    wallet = await lock_ad_wallet(session, seller_id)
    if wallet.balance_minor < amount_minor:
        raise InsufficientAdBalance(
            f"wallet balance {wallet.balance_minor} < required {amount_minor}",
        )
    new_balance = wallet.balance_minor - amount_minor
    txn = HypershopAdWalletTxn(
        ad_wallet_id=wallet.id,
        kind=_kind_for_debit(source_type),
        amount_minor=-amount_minor,
        balance_after_minor=new_balance,
        source_type=source_type,
        source_id=source_id,
        memo=memo,
    )
    session.add(txn)
    wallet.balance_minor = new_balance
    wallet.total_spent_minor = wallet.total_spent_minor + amount_minor
    await session.flush()
    return txn


async def list_ad_wallet_txns(
    session: AsyncSession,
    *,
    seller_id: UUID,
    limit: int = 50,
    offset: int = 0,
    kind: str | None = None,
) -> tuple[list[HypershopAdWalletTxn], int]:
    wallet = await get_or_create_ad_wallet(session, seller_id)
    base = select(HypershopAdWalletTxn).where(
        HypershopAdWalletTxn.ad_wallet_id == wallet.id,
    )
    if kind is not None:
        base = base.where(HypershopAdWalletTxn.kind == kind)
    total = (
        await session.execute(
            select(func.count()).select_from(base.subquery()),
        )
    ).scalar_one()
    rows = list(
        (
            await session.execute(
                base.order_by(HypershopAdWalletTxn.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
        ).scalars().all()
    )
    return rows, int(total)


async def list_wallets(
    session: AsyncSession,
    *,
    low_balance_only: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[HypershopAdWallet], int]:
    base = select(HypershopAdWallet)
    if low_balance_only:
        base = base.where(HypershopAdWallet.balance_minor < 100_000)
    total = (
        await session.execute(
            select(func.count()).select_from(base.subquery()),
        )
    ).scalar_one()
    rows = list(
        (
            await session.execute(
                base.order_by(HypershopAdWallet.balance_minor.asc())
                .limit(limit)
                .offset(offset)
            )
        ).scalars().all()
    )
    return rows, int(total)


async def update_auto_recharge(
    session: AsyncSession,
    *,
    seller_id: UUID,
    enabled: bool,
    threshold_minor: int | None,
    amount_minor: int | None,
) -> HypershopAdWallet:
    wallet = await lock_ad_wallet(session, seller_id)
    wallet.auto_recharge_enabled = enabled
    if enabled:
        wallet.auto_recharge_threshold_minor = threshold_minor
        wallet.auto_recharge_amount_minor = amount_minor
    else:
        wallet.auto_recharge_threshold_minor = None
        wallet.auto_recharge_amount_minor = None
    await session.flush()
    return wallet


# ── Persisted recharges (Phase 1.B) ────────────────────────────────


async def create_recharge(
    session: AsyncSession,
    *,
    ad_wallet_id: UUID,
    seller_id: UUID,
    amount_minor: int,
    provider: str,
) -> HypershopAdWalletRecharge:
    """Insert a new recharge row in status='initiated'.

    The provider call has not yet happened — see
    :func:`update_recharge` to flip to ``pending`` once the gateway
    returns its ``provider_payment_id``.
    """
    row = HypershopAdWalletRecharge(
        ad_wallet_id=ad_wallet_id,
        seller_id=seller_id,
        amount_minor=int(amount_minor),
        provider=provider,
        status="initiated",
    )
    session.add(row)
    await session.flush()
    return row


async def get_recharge(
    session: AsyncSession, recharge_id: UUID,
) -> HypershopAdWalletRecharge | None:
    return await session.get(HypershopAdWalletRecharge, recharge_id)


async def get_recharge_by_provider_payment_id(
    session: AsyncSession,
    *,
    provider: str,
    provider_payment_id: str,
) -> HypershopAdWalletRecharge | None:
    stmt = select(HypershopAdWalletRecharge).where(
        HypershopAdWalletRecharge.provider == provider,
        HypershopAdWalletRecharge.provider_payment_id == provider_payment_id,
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def lock_recharge_for_finalize(
    session: AsyncSession,
    *,
    provider: str,
    provider_payment_id: str,
) -> HypershopAdWalletRecharge | None:
    """Same as :func:`get_recharge_by_provider_payment_id` but row-locks.

    Used by the webhook finalize path so two parallel deliveries cannot
    both run the credit branch.
    """
    stmt = (
        select(HypershopAdWalletRecharge)
        .where(
            HypershopAdWalletRecharge.provider == provider,
            HypershopAdWalletRecharge.provider_payment_id == provider_payment_id,
        )
        .with_for_update()
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def update_recharge(
    session: AsyncSession,
    recharge_id: UUID,
    **fields,
) -> HypershopAdWalletRecharge | None:
    row = await session.get(HypershopAdWalletRecharge, recharge_id)
    if row is None:
        return None
    for key, value in fields.items():
        if hasattr(row, key):
            setattr(row, key, value)
    await session.flush()
    return row


async def list_recharges_for_seller(
    session: AsyncSession,
    *,
    seller_id: UUID,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[HypershopAdWalletRecharge], int]:
    base = select(HypershopAdWalletRecharge).where(
        HypershopAdWalletRecharge.seller_id == seller_id,
    )
    total = (
        await session.execute(
            select(func.count()).select_from(base.subquery()),
        )
    ).scalar_one()
    rows = list(
        (
            await session.execute(
                base.order_by(HypershopAdWalletRecharge.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
        ).scalars().all()
    )
    return rows, int(total)


# ── Pending-recharge tracking (uses module_settings as ephemeral store) ──


async def stash_pending_recharge(
    session: AsyncSession,
    *,
    provider_payment_id: str,
    seller_id: UUID,
    amount_minor: int,
) -> None:
    from app.modules.admin_config.models import ModuleSetting

    setting = ModuleSetting(
        module_key="ads",
        setting_key=f"pending_recharge:{provider_payment_id}",
        value_type="json",
        value={
            "seller_id": str(seller_id),
            "amount_minor": int(amount_minor),
            "provider_payment_id": provider_payment_id,
        },
        is_secret=False,
    )
    session.add(setting)
    await session.flush()


async def pop_pending_recharge(
    session: AsyncSession,
    *,
    provider_payment_id: str,
) -> dict | None:
    from app.modules.admin_config.models import ModuleSetting

    stmt = select(ModuleSetting).where(
        ModuleSetting.module_key == "ads",
        ModuleSetting.setting_key == f"pending_recharge:{provider_payment_id}",
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        return None
    payload = dict(row.value or {})
    await session.delete(row)
    await session.flush()
    return payload


# ── Campaign / ad group skeleton (unchanged from Phase 1.A) ──


async def list_campaigns(
    session: AsyncSession,
    seller_id: UUID,
    *,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[HypershopAdCampaign]:
    stmt = select(HypershopAdCampaign).where(
        HypershopAdCampaign.seller_id == seller_id,
    )
    if status is not None:
        stmt = stmt.where(HypershopAdCampaign.status == status)
    stmt = stmt.order_by(HypershopAdCampaign.created_at.desc()).limit(limit).offset(offset)
    return list((await session.execute(stmt)).scalars().all())


async def create_campaign(
    session: AsyncSession,
    *,
    seller_id: UUID,
    name: str,
    daily_budget_minor: int,
    status: str = "draft",
    total_budget_minor: int | None = None,
    start_at=None,
    end_at=None,
) -> HypershopAdCampaign:
    campaign = HypershopAdCampaign(
        seller_id=seller_id,
        name=name,
        status=status,
        daily_budget_minor=daily_budget_minor,
        total_budget_minor=total_budget_minor,
        start_at=start_at,
        end_at=end_at,
    )
    session.add(campaign)
    await session.flush()
    return campaign


async def get_campaign(
    session: AsyncSession, campaign_id: UUID,
) -> HypershopAdCampaign | None:
    return await session.get(HypershopAdCampaign, campaign_id)


async def update_campaign(
    session: AsyncSession, campaign_id: UUID, **fields,
) -> HypershopAdCampaign | None:
    campaign = await session.get(HypershopAdCampaign, campaign_id)
    if campaign is None:
        return None
    for key, value in fields.items():
        if value is not None and hasattr(campaign, key):
            setattr(campaign, key, value)
    await session.flush()
    return campaign


async def list_ad_groups(
    session: AsyncSession, campaign_id: UUID,
) -> list[HypershopAdGroup]:
    stmt = (
        select(HypershopAdGroup)
        .where(HypershopAdGroup.campaign_id == campaign_id)
        .order_by(HypershopAdGroup.created_at.desc())
    )
    return list((await session.execute(stmt)).scalars().all())


async def create_ad_group(
    session: AsyncSession,
    *,
    campaign_id: UUID,
    name: str,
    default_bid_minor: int,
    target_type: str,
    targets: list,
    product_ids: list[UUID],
    status: str = "active",
) -> HypershopAdGroup:
    group = HypershopAdGroup(
        campaign_id=campaign_id,
        name=name,
        status=status,
        default_bid_minor=default_bid_minor,
        target_type=target_type,
        targets=targets,
        product_ids=product_ids,
    )
    session.add(group)
    await session.flush()
    return group


async def get_ad_group(
    session: AsyncSession, ad_group_id: UUID,
) -> HypershopAdGroup | None:
    return await session.get(HypershopAdGroup, ad_group_id)


async def update_ad_group(
    session: AsyncSession, ad_group_id: UUID, **fields,
) -> HypershopAdGroup | None:
    group = await session.get(HypershopAdGroup, ad_group_id)
    if group is None:
        return None
    for key, value in fields.items():
        if value is not None and hasattr(group, key):
            setattr(group, key, value)
    await session.flush()
    return group
