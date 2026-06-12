"""Service-layer for the Sponsored Products module.

Phase 1.B: wallet logic (recharge / debit / refund / manual ops) is real.
Auction + quality scoring still stubs — Phases 1.C/1.D.
"""
from __future__ import annotations

from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.ads import repository as repo
from app.modules.ads.codes import MIN_RECHARGE_MINOR
from app.modules.ads.models import HypershopAdWalletRecharge, HypershopAdWalletTxn
from app.modules.ads.schemas import AuctionWinner


# Re-export so callers can `from ads.service import InsufficientAdBalance`.
InsufficientAdBalance = repo.InsufficientAdBalance
AdWalletIdempotencyError = repo.AdWalletIdempotencyError


# Alias of :class:`InsufficientAdBalance` kept for spec parity with
# the Phase 1.B task description; both names point at the same
# underlying error type so legacy callers and new auction callers
# can catch either via ``except AdWalletInsufficientFunds``.
AdWalletInsufficientFunds = InsufficientAdBalance


class AdWalletInactive(Exception):
    """Raised when debit/credit hits a frozen wallet."""


class AdRechargeNotFound(Exception):
    """Webhook fired for a provider_payment_id we don't recognise."""


class AdRechargeAlreadyFinalized(Exception):
    """Webhook re-delivery — already credited; safe no-op for caller."""


class AdRechargeProviderError(Exception):
    """Provider create_payment failed in the way the API surface needs to
    translate to 502 Bad Gateway."""


# ── Read ────────────────────────────────────────────────────────────


async def get_balance(session: AsyncSession, seller_id: UUID) -> dict:
    wallet = await repo.get_or_create_ad_wallet(session, seller_id)
    return {
        "balance_minor": int(wallet.balance_minor),
        "total_spent_minor": int(wallet.total_spent_minor),
        "auto_recharge_enabled": bool(wallet.auto_recharge_enabled),
        "auto_recharge_threshold_minor": (
            int(wallet.auto_recharge_threshold_minor)
            if wallet.auto_recharge_threshold_minor is not None else None
        ),
        "auto_recharge_amount_minor": (
            int(wallet.auto_recharge_amount_minor)
            if wallet.auto_recharge_amount_minor is not None else None
        ),
        "currency": "BDT",
    }


async def list_transactions(
    session: AsyncSession,
    *,
    seller_id: UUID,
    limit: int = 50,
    offset: int = 0,
    kind: str | None = None,
) -> dict:
    rows, total = await repo.list_ad_wallet_txns(
        session, seller_id=seller_id, limit=limit, offset=offset, kind=kind,
    )
    return {
        "items": rows,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


# ── Recharge (Bkash) ────────────────────────────────────────────────


async def initiate_recharge(
    session: AsyncSession,
    *,
    seller_id: UUID,
    amount_minor: int,
    bkash_msisdn: str | None = None,
    provider: str = "bkash",
    return_callback_url: str | None = None,
) -> dict:
    """Phase 1.B: build a provider checkout and persist a real recharge row.

    1. validate amount
    2. get_or_create_ad_wallet
    3. INSERT recharge row status='initiated'
    4. provider.create_payment(...) using recharge.id as the intent
    5. on success → status='pending', store provider_payment_id + url
    6. on provider failure → status='failed', failure_reason, raise
       :class:`AdRechargeProviderError` (API layer maps to 502)

    Return dict has both new spec keys (``recharge_id``,
    ``redirect_url``, ``status``) and the legacy ones
    (``payment_id``, ``bkash_url``, ``expires_at``) so existing
    seller-API callers keep working without a hard cutover.
    """
    from datetime import timedelta

    from app.core.config import get_settings
    from app.core.errors import IntegrationError, ValidationError
    from app.core.time import utc_now
    from app.modules.payments.providers import get_provider
    from app.modules.payments.providers.base import CreatePaymentRequest
    from app.modules.payments.providers.not_configured import (
        NotConfiguredPaymentProvider,
    )

    if amount_minor < MIN_RECHARGE_MINOR:
        raise ValidationError(
            f"Recharge below minimum {MIN_RECHARGE_MINOR} paisa.",
            details={"min": MIN_RECHARGE_MINOR},
        )

    wallet = await repo.get_or_create_ad_wallet(session, seller_id)
    recharge = await repo.create_recharge(
        session,
        ad_wallet_id=wallet.id,
        seller_id=seller_id,
        amount_minor=amount_minor,
        provider=provider,
    )

    amount_decimal = (Decimal(amount_minor) / Decimal(100)).quantize(Decimal("0.01"))
    short_code = f"AD-RCG-{recharge.id.hex[:10].upper()}"
    settings = get_settings()
    api_base = (
        getattr(settings, "public_api_base_url", None)
        or getattr(settings, "api_base_url", None)
        or "https://api.hypershop.com.bd"
    )
    webhook_url = (
        f"{str(api_base).rstrip('/')}/api/v1/ads/webhooks/{provider}/ad-wallet-recharge"
    )
    success_url = (
        return_callback_url
        or "https://hypershop.com.bd/seller/ads/wallet?recharge=success"
    )

    bound = get_provider(provider)
    is_configured = not isinstance(bound, NotConfiguredPaymentProvider)
    try:
        if not is_configured:
            # Fail loud — operator gets a 502 in dev with no creds so
            # the misconfiguration is obvious rather than papered over.
            raise IntegrationError(
                f"payment provider '{provider}' not configured",
                details={"provider": provider},
            )
        result = await bound.create_payment(
            CreatePaymentRequest(
                intent_id=str(recharge.id),
                order_code=short_code,
                amount=amount_decimal,
                currency="BDT",
                customer_email=None,
                customer_phone=bkash_msisdn,
                customer_name=None,
                success_url=success_url,
                failure_url="https://hypershop.com.bd/seller/ads/wallet?recharge=failed",
                cancel_url="https://hypershop.com.bd/seller/ads/wallet?recharge=cancelled",
                webhook_url=webhook_url,
                metadata={
                    "purpose": "ad_wallet_recharge",
                    "seller_id": str(seller_id),
                    "amount_minor": str(amount_minor),
                    "recharge_id": str(recharge.id),
                },
            ),
        )
    except IntegrationError as e:
        await repo.update_recharge(
            session,
            recharge.id,
            status="failed",
            failure_reason=str(e)[:256],
        )
        raise AdRechargeProviderError(str(e)) from e

    provider_payment_id = result.provider_payment_id
    checkout_url = result.checkout_url
    expires_at = result.expires_at or (utc_now() + timedelta(minutes=30))

    await repo.update_recharge(
        session,
        recharge.id,
        provider_payment_id=provider_payment_id,
        bkash_create_url=checkout_url,
        status="pending",
        provider_response=_scrub(result.raw),
    )

    return {
        # New spec contract:
        "recharge_id": str(recharge.id),
        "redirect_url": checkout_url,
        "status": "pending",
        # Legacy keys kept so seller-API callers stay green.
        "payment_id": provider_payment_id,
        "bkash_url": checkout_url,
        "expires_at": expires_at.isoformat(),
    }


def _scrub(raw: dict | None) -> dict | None:
    """Drop obvious secret-bearing keys before persisting provider raw."""
    if not raw:
        return None
    blocked = {"app_secret", "password", "client_secret", "private_key", "token"}
    return {k: v for k, v in raw.items() if k.lower() not in blocked}


async def finalize_recharge_from_callback(
    session: AsyncSession,
    *,
    provider: str,
    provider_payment_id: str,
    callback_payload: dict | None = None,
) -> HypershopAdWalletRecharge | None:
    """Webhook handler — credit the wallet when the gateway confirms capture.

    Idempotent: re-deliveries hitting a recharge already in 'succeeded' are
    a no-op. The whole credit happens in a single transaction with the
    recharge update so partial failure rolls back cleanly.
    """
    from app.core.errors import IntegrationError
    from app.core.time import utc_now
    from app.modules.payments.providers import get_provider

    recharge = await repo.lock_recharge_for_finalize(
        session, provider=provider, provider_payment_id=provider_payment_id,
    )
    if recharge is None:
        return None
    if recharge.status == "succeeded":
        # Already credited — re-delivery, safe no-op.
        return recharge
    if recharge.status in ("failed", "expired", "refunded"):
        return recharge

    # Confirm with the provider that the seller actually paid.
    try:
        bound = get_provider(provider)
        execute_result = await bound.execute_payment(
            intent_id=str(recharge.id),
            provider_payment_id=provider_payment_id,
        )
    except IntegrationError as e:
        await repo.update_recharge(
            session,
            recharge.id,
            status="failed",
            failure_reason=str(e)[:256],
        )
        return recharge

    if execute_result.status not in ("captured", "authorized"):
        await repo.update_recharge(
            session,
            recharge.id,
            status="failed",
            failure_reason=(
                execute_result.error_message or f"provider status {execute_result.status}"
            )[:256],
        )
        return recharge

    # Provider confirmed — credit the wallet under a row lock.
    # The deterministic source_id ties this credit to the recharge row,
    # so even a stray manual replay can't double-credit (the underlying
    # repo.credit_ad_wallet de-duplicates on (source_type, source_id)).
    txn = await repo.credit_ad_wallet(
        session,
        seller_id=recharge.seller_id,
        amount_minor=int(recharge.amount_minor),
        source_type="ad_wallet_recharges",
        source_id=recharge.id,
        memo=f"{provider.capitalize()} recharge {provider_payment_id}",
    )

    await repo.update_recharge(
        session,
        recharge.id,
        status="succeeded",
        completed_at=utc_now(),
        wallet_txn_id=txn.id,
        provider_transaction_id=(
            execute_result.raw.get("trxID") if execute_result.raw else None
        ),
        provider_response=_scrub(execute_result.raw),
    )

    return await repo.get_recharge(session, recharge.id)


async def handle_recharge_webhook(
    session: AsyncSession,
    *,
    payment_id: str,
    status: str,
    amount_minor: int | None,
    raw_payload: dict | None = None,
) -> HypershopAdWalletTxn | None:
    """Back-compat shim — Phase 1.A used the ``module_settings`` stash;
    Phase 1.B uses the persisted recharges table. Both paths are
    supported here so callers in ``payments/api/webhooks.py`` keep
    working unmodified.
    """
    _ = raw_payload
    if status not in ("captured", "COMPLETED", "completed", "Completed"):
        return None

    # New path — check the persisted table first.
    recharge = await repo.get_recharge_by_provider_payment_id(
        session, provider="bkash", provider_payment_id=payment_id,
    )
    if recharge is not None:
        await finalize_recharge_from_callback(
            session,
            provider="bkash",
            provider_payment_id=payment_id,
            callback_payload=raw_payload,
        )
        # Re-read so we get the wallet_txn_id-linked txn.
        if recharge.wallet_txn_id is not None:
            from app.modules.ads.models import HypershopAdWalletTxn as _T
            return await session.get(_T, recharge.wallet_txn_id)
        return None

    # Legacy path — Phase 1.A ephemeral stash for any in-flight recharges
    # that pre-date Phase 1.B.
    pending = await repo.pop_pending_recharge(
        session, provider_payment_id=payment_id,
    )
    if pending is None:
        return None
    credit_amount = (
        int(amount_minor) if amount_minor is not None
        else int(pending["amount_minor"])
    )
    seller_id = UUID(pending["seller_id"])
    import hashlib
    digest = hashlib.sha256(payment_id.encode("utf-8")).digest()
    source_uuid = UUID(bytes=digest[:16])
    return await repo.credit_ad_wallet(
        session,
        seller_id=seller_id,
        amount_minor=credit_amount,
        source_type="bkash_recharge",
        source_id=source_uuid,
        memo=f"Bkash recharge {payment_id}",
    )


# ── Spec-named wrappers — make the API layer's life easier ─────────


async def get_wallet_balance(session: AsyncSession, seller_id: UUID) -> dict:
    """Spec name for :func:`get_balance` — returns the same payload."""
    return await get_balance(session, seller_id)


async def debit_wallet(
    session: AsyncSession,
    *,
    seller_id: UUID,
    amount_minor: int,
    source_type: str,
    source_id: UUID,
    memo: str | None = None,
) -> HypershopAdWalletTxn:
    """Atomic debit used by the auction click handler (Phase 1.D).

    Wraps :func:`app.modules.ads.repository.debit_ad_wallet` — that
    function already takes a SELECT FOR UPDATE on the wallet row,
    so concurrent click debits serialise on Postgres without losing
    any txn record.
    """
    return await repo.debit_ad_wallet(
        session,
        seller_id=seller_id,
        amount_minor=amount_minor,
        source_type=source_type,
        source_id=source_id,
        memo=memo,
    )


async def refund_click(
    session: AsyncSession,
    *,
    click_id: UUID,
    refund_reason: str,
    admin_user_id: UUID,
) -> HypershopAdWalletTxn:
    """Mark the click invalid + return the spend to the wallet.

    Idempotent: re-running on an already-invalid click returns the
    existing credit txn instead of double-crediting. Uses SELECT FOR
    UPDATE on the wallet via ``credit_ad_wallet`` so concurrent refund
    attempts serialise without lost updates.

    Wired 2026-05-28 (was raise NotImplementedError("Phase 1.D")).
    """
    from sqlalchemy import select
    from .models import HypershopAdClick

    # 1. Load click row + lock
    click = (await session.execute(
        select(HypershopAdClick).where(HypershopAdClick.id == click_id).with_for_update()
    )).scalar_one_or_none()
    if click is None:
        raise ValueError(f"click {click_id} not found")

    # 2. Idempotency — if already invalid, return the existing credit txn
    existing = await repo.get_ad_wallet_txn_by_source(
        session, source_type="click_refund", source_id=click_id,
    )
    if click.is_invalid and existing is not None:
        return existing

    # 3. Mark invalid + audit
    click.is_invalid = True
    if hasattr(click, "invalid_reason"):
        click.invalid_reason = refund_reason
    if hasattr(click, "invalidated_by"):
        click.invalidated_by = admin_user_id

    # 4. Credit back the charged amount
    if click.charged_amount_minor <= 0:
        # Nothing to refund — return a zero-credit placeholder
        await session.flush()
        return existing or HypershopAdWalletTxn()  # type: ignore[call-arg]

    txn = await repo.credit_ad_wallet(
        session,
        seller_id=click.seller_id,
        amount_minor=click.charged_amount_minor,
        source_type="click_refund",
        source_id=click_id,
        memo=f"Refund: {refund_reason} (by admin {admin_user_id})",
    )
    return txn


# ── Admin manual ops ────────────────────────────────────────────────


async def manual_credit(
    session: AsyncSession,
    *,
    seller_id: UUID,
    amount_minor: int,
    admin_user_id: UUID,
    memo: str,
) -> HypershopAdWalletTxn:
    source_id = uuid4()
    return await repo.credit_ad_wallet(
        session,
        seller_id=seller_id,
        amount_minor=amount_minor,
        source_type="manual_credit",
        source_id=source_id,
        memo=f"[admin {admin_user_id}] {memo}",
    )


async def manual_debit(
    session: AsyncSession,
    *,
    seller_id: UUID,
    amount_minor: int,
    admin_user_id: UUID,
    memo: str,
) -> HypershopAdWalletTxn:
    source_id = uuid4()
    return await repo.debit_ad_wallet(
        session,
        seller_id=seller_id,
        amount_minor=amount_minor,
        source_type="manual_debit",
        source_id=source_id,
        memo=f"[admin {admin_user_id}] {memo}",
    )


# ── Click billing (called from auction module in Phase 1.D) ─────────


async def debit_for_click(
    session: AsyncSession,
    *,
    seller_id: UUID,
    amount_minor: int,
    click_id: UUID,
) -> HypershopAdWalletTxn:
    return await repo.debit_ad_wallet(
        session,
        seller_id=seller_id,
        amount_minor=amount_minor,
        source_type="ad_clicks",
        source_id=click_id,
        memo=f"Click charge {click_id}",
    )


# ── Auto-recharge config ────────────────────────────────────────────


async def update_auto_recharge(
    session: AsyncSession,
    *,
    seller_id: UUID,
    enabled: bool,
    threshold_minor: int | None,
    amount_minor: int | None,
) -> dict:
    from app.core.errors import ValidationError

    if enabled and (threshold_minor is None or amount_minor is None):
        raise ValidationError(
            "auto_recharge requires threshold_minor and amount_minor when enabled.",
        )
    wallet = await repo.update_auto_recharge(
        session,
        seller_id=seller_id,
        enabled=enabled,
        threshold_minor=threshold_minor,
        amount_minor=amount_minor,
    )
    return {
        "balance_minor": int(wallet.balance_minor),
        "total_spent_minor": int(wallet.total_spent_minor),
        "auto_recharge_enabled": bool(wallet.auto_recharge_enabled),
        "auto_recharge_threshold_minor": (
            int(wallet.auto_recharge_threshold_minor)
            if wallet.auto_recharge_threshold_minor is not None else None
        ),
        "auto_recharge_amount_minor": (
            int(wallet.auto_recharge_amount_minor)
            if wallet.auto_recharge_amount_minor is not None else None
        ),
        "currency": "BDT",
    }


# ── Phase 1.D — Auction + Click ─────────────────────────────────────


# Click fraud guard: same (user, ad_group) in this window = invalid replay.
_INVALID_CLICK_WINDOW_SECONDS = 60
_INVALID_CLICK_MAX_PER_WINDOW = 3
# Second-price epsilon — add 1 paisa above runner-up to break ties cleanly.
_AUCTION_EPSILON_MINOR = 1


class AdClickAlreadyRecorded(Exception):
    """Click for this impression already on file — return existing record."""


class AdImpressionNotFound(Exception):
    """Click POSTed for an impression we never served."""


async def run_auction(
    session: AsyncSession,
    *,
    surface: str,
    surface_ref: str,
    slot_count: int,
    user_id: UUID | None = None,
    session_id: str | None = None,
) -> list[AuctionWinner]:
    """Second-price keyword/category auction.

    1. SELECT eligible ad_groups whose target matches `surface_ref`
       and whose campaign is active + has budget + wallet has min-bid.
    2. Score = bid * quality_score; sort desc.
    3. Pick top `slot_count`; each winner pays just enough to beat the
       next ad_rank ((next_rank / quality_score) + 0.01 paisa).
    4. Persist impressions (one row per winner) so the click handler
       can find them later. Returns the winner list with impression IDs.
    """
    from sqlalchemy import cast, select
    from sqlalchemy.dialects.postgresql import JSONB

    from app.modules.ads.codes import (
        MIN_BID_MINOR,
        SURFACE_CATEGORY,
        SURFACE_PDP_RELATED,
        SURFACE_SEARCH,
    )
    from app.modules.ads.models import (
        HypershopAdCampaign,
        HypershopAdGroup,
        HypershopAdImpression,
        HypershopAdWallet,
    )

    if surface not in (SURFACE_SEARCH, SURFACE_CATEGORY, SURFACE_PDP_RELATED):
        return []
    if slot_count <= 0:
        return []

    # Target match: JSONB containment. Different surface = different key.
    # search/pdp_related → keyword target; category → category_slug target.
    # `cast(literal, JSONB)` makes the RHS a JSONB so the @> operator
    # picks the JSONB GIN index on the targets column.
    if surface == SURFACE_CATEGORY:
        target_value: list[dict] = [{"category_slug": surface_ref}]
    else:
        target_value = [{"keyword": surface_ref.lower()}]
    target_filter = HypershopAdGroup.targets.op("@>")(
        cast(target_value, JSONB),
    )

    # Eligibility join — campaign active + budget left + wallet has min-bid.
    stmt = (
        select(
            HypershopAdGroup,
            HypershopAdCampaign.seller_id,
            HypershopAdCampaign.daily_budget_minor,
            HypershopAdCampaign.today_spent_minor,
        )
        .join(HypershopAdCampaign, HypershopAdCampaign.id == HypershopAdGroup.campaign_id)
        .join(HypershopAdWallet, HypershopAdWallet.seller_id == HypershopAdCampaign.seller_id)
        .where(
            HypershopAdGroup.status == "active",
            HypershopAdCampaign.status == "active",
            HypershopAdCampaign.today_spent_minor < HypershopAdCampaign.daily_budget_minor,
            HypershopAdWallet.balance_minor >= MIN_BID_MINOR,
            target_filter,
        )
    )
    rows = (await session.execute(stmt)).all()
    if not rows:
        return []

    # Score and sort.
    scored: list[tuple] = []
    for ad_group, seller_id, _budget, _spent in rows:
        bid = int(ad_group.default_bid_minor)
        quality = float(ad_group.quality_score)
        rank = bid * quality
        # Each ad_group can promote multiple products — pick the first one
        # for the MVP. Later: per-product CTR boost.
        if not ad_group.product_ids:
            continue
        product_id = ad_group.product_ids[0]
        scored.append((rank, bid, quality, ad_group, seller_id, product_id))
    scored.sort(key=lambda x: -x[0])

    n = min(slot_count, len(scored))
    if n == 0:
        return []

    # Second-price clearing: floor at MIN_BID, ceiling at own bid.
    from app.modules.ads.codes import MIN_BID_MINOR as _MIN_BID
    impressions: list[HypershopAdImpression] = []
    winners: list[AuctionWinner] = []
    for i in range(n):
        rank_i, bid_i, q_i, ag, seller_i, prod_i = scored[i]
        next_rank = scored[i + 1][0] if i + 1 < len(scored) else _MIN_BID * q_i
        # Pay the smallest bid that would still have beaten the next ad.
        cleared = int(next_rank / q_i) + _AUCTION_EPSILON_MINOR
        cleared = max(cleared, _MIN_BID)
        cleared = min(cleared, bid_i)  # never exceed own bid
        impr = HypershopAdImpression(
            ad_group_id=ag.id,
            product_id=prod_i,
            surface=surface,
            surface_ref=surface_ref[:256],
            slot_position=i + 1,
            user_id=user_id,
            session_id=(session_id or None) if not session_id else session_id[:64],
            bid_at_auction_minor=bid_i,
            cleared_price_minor=cleared,
        )
        session.add(impr)
        impressions.append(impr)

    # Flush so impression IDs (BIGINT identity) are populated.
    await session.flush()

    for i, impr in enumerate(impressions):
        _rank_i, bid_i, q_i, ag, seller_i, prod_i = scored[i]
        winners.append(
            AuctionWinner(
                impression_id=int(impr.id),
                ad_group_id=ag.id,
                product_id=prod_i,
                seller_id=seller_i,
                slot_position=i + 1,
                cleared_price_minor=int(impr.cleared_price_minor),
                bid_minor=int(impr.bid_at_auction_minor),
                quality_score=float(q_i),
            )
        )
    return winners


async def record_click(
    session: AsyncSession,
    *,
    impression_id: int,
    user_id: UUID | None = None,
    session_id: str | None = None,
) -> dict:
    """Charge the seller's ad wallet for one click on a served impression.

    Atomicity contract: wallet lock + click insert + spend bump live in
    a single transaction so a concurrent click on the same wallet
    serialises on Postgres without losing either txn.

    Idempotency: the partial unique index
    ``ix_hypershop_ad_clicks_impr_unique WHERE is_invalid=false``
    guarantees that double-POSTs on the same impression don't double-bill.
    The first-write-wins outcome is returned.

    Invalid-click guard: more than `_INVALID_CLICK_MAX_PER_WINDOW` clicks
    from the same user on the same ad_group within
    `_INVALID_CLICK_WINDOW_SECONDS` flags the click invalid; no debit,
    no spend, audit-trail row only.
    """
    from datetime import timedelta

    from sqlalchemy import func, select

    from app.core.time import utc_now
    from app.modules.ads.models import (
        HypershopAdCampaign,
        HypershopAdClick,
        HypershopAdImpression,
    )

    # 1. Find the impression.
    impr = await session.get(HypershopAdImpression, impression_id)
    if impr is None:
        raise AdImpressionNotFound(f"impression {impression_id} not served")

    # 2. Idempotency check — same impression already clicked (valid)?
    existing_stmt = select(HypershopAdClick).where(
        HypershopAdClick.impression_id == impression_id,
        HypershopAdClick.is_invalid.is_(False),
    )
    existing = (await session.execute(existing_stmt)).scalar_one_or_none()
    if existing is not None:
        return {
            "click_id": int(existing.id),
            "charged_minor": int(existing.charged_amount_minor),
            "redirect_product_id": existing.product_id,
            "deduplicated": True,
        }

    # 3. Invalid-click guard: rate-limit per (user, ad_group).
    is_invalid = False
    invalid_reason: str | None = None
    if user_id is not None:
        since = utc_now() - timedelta(seconds=_INVALID_CLICK_WINDOW_SECONDS)
        recent_stmt = (
            select(func.count())
            .select_from(HypershopAdClick)
            .where(
                HypershopAdClick.user_id == user_id,
                HypershopAdClick.ad_group_id == impr.ad_group_id,
                HypershopAdClick.created_at >= since,
            )
        )
        recent_count = (await session.execute(recent_stmt)).scalar_one() or 0
        if int(recent_count) >= _INVALID_CLICK_MAX_PER_WINDOW:
            is_invalid = True
            invalid_reason = "rate_limited_replay"

    # 4. Resolve seller_id via the parent campaign.
    ag = await repo.get_ad_group(session, impr.ad_group_id)
    if ag is None:
        raise AdImpressionNotFound("impression's ad-group was deleted")
    campaign = await repo.get_campaign(session, ag.campaign_id)
    if campaign is None:
        raise AdImpressionNotFound("impression's campaign was deleted")

    charged = int(impr.cleared_price_minor)

    if is_invalid:
        # Log the click as audit-only — no debit, no spend bump.
        click = HypershopAdClick(
            impression_id=impression_id,
            ad_group_id=impr.ad_group_id,
            product_id=impr.product_id,
            seller_id=campaign.seller_id,
            user_id=user_id,
            charged_amount_minor=0,
            wallet_txn_id=None,
            is_invalid=True,
            invalid_reason=invalid_reason,
        )
        session.add(click)
        await session.flush()
        return {
            "click_id": int(click.id),
            "charged_minor": 0,
            "redirect_product_id": impr.product_id,
            "invalid": True,
            "reason": invalid_reason,
        }

    # 5. Atomic debit + click row + campaign spend bump.
    # repo.debit_ad_wallet takes its own SELECT FOR UPDATE on the wallet.
    try:
        txn = await repo.debit_ad_wallet(
            session,
            seller_id=campaign.seller_id,
            amount_minor=charged,
            source_type="ad_clicks",
            source_id=_int_to_uuid(impression_id),
            memo=f"Click on impression {impression_id}",
        )
    except InsufficientAdBalance:
        # Pause the campaign; bury the click as invalid so the user-redirect
        # still works but no charge goes through.
        campaign.status = "budget_exhausted"
        await session.flush()
        click = HypershopAdClick(
            impression_id=impression_id,
            ad_group_id=impr.ad_group_id,
            product_id=impr.product_id,
            seller_id=campaign.seller_id,
            user_id=user_id,
            charged_amount_minor=0,
            wallet_txn_id=None,
            is_invalid=True,
            invalid_reason="insufficient_balance",
        )
        session.add(click)
        await session.flush()
        return {
            "click_id": int(click.id),
            "charged_minor": 0,
            "redirect_product_id": impr.product_id,
            "invalid": True,
            "reason": "insufficient_balance",
        }

    click = HypershopAdClick(
        impression_id=impression_id,
        ad_group_id=impr.ad_group_id,
        product_id=impr.product_id,
        seller_id=campaign.seller_id,
        user_id=user_id,
        charged_amount_minor=charged,
        wallet_txn_id=txn.id,
        is_invalid=False,
        invalid_reason=None,
    )
    session.add(click)

    # Bump campaign spend; auto-pause if we just hit the daily cap.
    campaign.today_spent_minor = int(campaign.today_spent_minor) + charged
    campaign.total_spent_minor = int(campaign.total_spent_minor) + charged
    if campaign.today_spent_minor >= int(campaign.daily_budget_minor):
        campaign.status = "budget_exhausted"
    await session.flush()

    return {
        "click_id": int(click.id),
        "charged_minor": charged,
        "redirect_product_id": impr.product_id,
        "invalid": False,
    }


def _int_to_uuid(n: int) -> UUID:
    """Deterministic UUID for ad_clicks idempotency source_id.

    Wallet ledger expects a UUID source_id; we synthesise one from the
    impression bigint so the dedup behaviour (same impression =
    same wallet txn) holds even across retries.
    """
    return UUID(int=int(n) & ((1 << 128) - 1))


async def recharge_wallet(
    session: AsyncSession,
    *,
    seller_id: UUID,
    amount_minor: int,
    bkash_payment_id: str,
) -> HypershopAdWalletTxn:
    """Back-compat shim — Phase 1.B replaces this with the
    webhook-driven ``handle_recharge_webhook`` flow."""
    import hashlib
    digest = hashlib.sha256(bkash_payment_id.encode("utf-8")).digest()
    source_uuid = UUID(bytes=digest[:16])
    return await repo.credit_ad_wallet(
        session,
        seller_id=seller_id,
        amount_minor=amount_minor,
        source_type="bkash_recharge",
        source_id=source_uuid,
        memo=f"Bkash recharge {bkash_payment_id}",
    )
