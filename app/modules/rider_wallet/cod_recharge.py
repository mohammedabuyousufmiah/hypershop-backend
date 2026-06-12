"""Rider COD recharge — Bkash Tokenized Checkout integration (Track A).

Wraps `BkashProvider.create_payment` + `execute_payment` so a rider can
push their collected COD back to the company merchant directly from
their Bkash wallet, via a WebView that opens the Bkash hosted page.

Flow:
  1. Rider taps "Pay COD via Bkash" with amount + their Bkash mobile.
  2. Backend `initiate` → bkash.create_payment(...) → stores
     RiderCodRechargeSession row → returns bkash_url to the app.
  3. App opens bkash_url in a WebView. Rider enters PIN/OTP on Bkash's
     hosted page, then Bkash redirects to our callback URL.
  4. WebView intercepts the callback URL, extracts paymentID, closes,
     and calls backend `verify`.
  5. Backend `verify` → bkash.execute_payment(paymentID) → on capture:
        - Insert auto-verified `rider_settlements` row.
        - Append `settlement_verified` ledger entry (debit on payable).
        - Decrement `rider_wallets.wallet_payable_to_company`.
        - Mark session row `status=completed`.

Idempotency:
  - `idempotency_key` (per request) is unique-indexed on session table.
  - `provider_payment_id` is unique-indexed; an `execute` retry returns
    the same captured row instead of double-booking.
  - Session ownership is enforced — verify rejects sessions for other
    riders.

Failure handling:
  - Any 4xx/5xx from Bkash → session.status = "failed" with reason.
  - Bkash session expiry (~30 min) → session.status = "expired".
  - Wallet frozen → raises WalletFrozenError before any external call.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import IntegrationError, ValidationError
from app.core.logging import get_logger
from app.core.security.principal import Principal
from app.core.time import utc_now
from app.modules.payments.codes import PROVIDER_BKASH
from app.modules.payments.providers.base import CreatePaymentRequest
from app.modules.payments.providers.registry import get_provider
from app.modules.rider_wallet.errors import (
    InvalidSettlementAmountError,
    WalletFrozenError,
    WalletNotFoundError,
)
from app.modules.rider_wallet.models import RiderCodRechargeSession
from app.modules.rider_wallet.repository import (
    RiderSettlementRepository,
    RiderWalletLedgerRepository,
    RiderWalletRepository,
)
from app.modules.rider_wallet.service import RiderWalletService, _q
from app.modules.rider_wallet.state import (
    LedgerDirection,
    LedgerEntryType,
    SettlementStatus,
)

_log = get_logger("hypershop.rider_wallet.cod_recharge")


COD_RECHARGE_STATUS_INITIATED = "initiated"
COD_RECHARGE_STATUS_IN_PROGRESS = "in_progress"
COD_RECHARGE_STATUS_COMPLETED = "completed"
COD_RECHARGE_STATUS_FAILED = "failed"
COD_RECHARGE_STATUS_CANCELLED = "cancelled"
COD_RECHARGE_STATUS_EXPIRED = "expired"


def _mask_phone(number: str) -> str:
    """01XXXXXXXXX → ***XXXX (last 4 digits)."""
    cleaned = "".join(ch for ch in number if ch.isdigit())
    if len(cleaned) < 4:
        return "***"
    return "***" + cleaned[-4:]


def _build_callback_url(*, base: str, session_id: UUID) -> str:
    base_clean = base.rstrip("/")
    return f"{base_clean}/api/v1/rider/wallet/cod/recharge/callback?session={session_id}"


class RiderCodRechargeService:
    """Service for the in-app Bkash recharge flow."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.wallets = RiderWalletRepository(session)
        self.ledgers = RiderWalletLedgerRepository(session)
        self.settlements = RiderSettlementRepository(session)
        self.wallet_svc = RiderWalletService(session)

    # ------------------------------------------------------------------
    # initiate
    # ------------------------------------------------------------------
    async def initiate(
        self, *,
        rider_id: UUID,
        amount: Decimal,
        mfs_number: str,
        idempotency_key: str | None,
        api_base_url: str,
        principal: Principal,
    ) -> RiderCodRechargeSession:
        """Open a Bkash session and persist a tracking row.

        ``api_base_url`` should be something like ``https://api.hypershop.com.bd``;
        used to construct the Bkash callback URL the WebView will intercept.
        """
        if amount <= 0:
            raise InvalidSettlementAmountError("Recharge amount must be > 0")
        amount = _q(amount)

        # Idempotency: if the same key already has a session, return it.
        if idempotency_key:
            existing = await self._get_by_idempotency_key(idempotency_key)
            if existing is not None:
                if existing.rider_id != rider_id:
                    raise ValidationError(
                        "idempotency_key reused by a different rider",
                        details={"key": idempotency_key},
                    )
                return existing

        wallet = await self.wallet_svc.get_or_create(
            rider_id=rider_id, principal=principal,
        )
        if wallet.is_frozen:
            raise WalletFrozenError("Cannot recharge on a frozen wallet.")

        if amount > wallet.wallet_payable_to_company:
            raise ValidationError(
                "Recharge amount exceeds amount payable to company.",
                details={
                    "requested": str(amount),
                    "payable": str(wallet.wallet_payable_to_company),
                },
            )

        # Reserve a session row so we have a stable id to embed in the
        # Bkash callback URL.
        session_id = uuid4()
        order_code = f"COD-RCH-{session_id.hex[:12].upper()}"
        callback_url = _build_callback_url(
            base=api_base_url, session_id=session_id,
        )

        provider = get_provider(PROVIDER_BKASH)
        if provider is None:
            raise IntegrationError(
                "Bkash provider is not bound on this server.",
                details={"hint": "Check BKASH_* env vars; see payments/providers/factory.py"},
            )

        intent_id = f"rider-cod-rch:{session_id}"
        create_result = await provider.create_payment(
            CreatePaymentRequest(
                intent_id=intent_id,
                order_code=order_code,
                amount=amount,
                currency="BDT",
                customer_phone=mfs_number,
                success_url=callback_url,
            ),
        )

        row = RiderCodRechargeSession(
            id=session_id,
            rider_id=rider_id,
            wallet_id=wallet.id,
            shift_id=None,  # Optional — could be inferred from active shift later.
            requested_amount=amount,
            captured_amount=Decimal("0"),
            mfs_provider="bkash",
            payer_mfs_number=mfs_number,
            provider_payment_id=create_result.provider_payment_id,
            provider_trx_id=None,
            bkash_url=create_result.checkout_url,
            status=COD_RECHARGE_STATUS_INITIATED,
            failure_reason=None,
            expires_at=create_result.expires_at,
            completed_at=None,
            idempotency_key=idempotency_key,
        )
        self.session.add(row)
        await self.session.flush()
        _log.info(
            "rider_cod_recharge_initiated",
            session_id=str(row.id),
            rider_id=str(rider_id),
            amount=str(amount),
            provider_payment_id=row.provider_payment_id,
        )
        return row

    # ------------------------------------------------------------------
    # verify
    # ------------------------------------------------------------------
    async def verify(
        self, *,
        recharge_session_id: UUID,
        provider_payment_id: str,
        principal: Principal,
        rider_id: UUID,
    ) -> RiderCodRechargeSession:
        """Capture the Bkash payment and credit the rider's settlement."""
        row = await self._get_by_id(recharge_session_id)
        if row is None:
            raise ValidationError(
                "Recharge session not found.",
                details={"recharge_session_id": str(recharge_session_id)},
            )
        if row.rider_id != rider_id:
            raise ValidationError(
                "Recharge session does not belong to this rider.",
                details={"recharge_session_id": str(recharge_session_id)},
            )
        if row.provider_payment_id != provider_payment_id:
            raise ValidationError(
                "Provider paymentID does not match the recharge session.",
                details={
                    "session_payment_id": row.provider_payment_id,
                    "given_payment_id": provider_payment_id,
                },
            )

        # Idempotent: already terminal? return as-is.
        if row.status in (
            COD_RECHARGE_STATUS_COMPLETED,
            COD_RECHARGE_STATUS_FAILED,
            COD_RECHARGE_STATUS_CANCELLED,
            COD_RECHARGE_STATUS_EXPIRED,
        ):
            return row

        provider = get_provider(PROVIDER_BKASH)
        if provider is None:
            raise IntegrationError(
                "Bkash provider is not bound on this server.",
            )

        exec_result = await provider.execute_payment(
            intent_id=f"rider-cod-rch:{row.id}",
            provider_payment_id=provider_payment_id,
        )

        if exec_result.status == "captured":
            await self._apply_capture(
                row=row,
                captured_amount=exec_result.amount_captured,
                provider_trx_id=str(exec_result.raw.get("trxID") or ""),
                principal=principal,
            )
        else:
            row.status = COD_RECHARGE_STATUS_FAILED
            row.failure_reason = (
                exec_result.error_message
                or f"Bkash status: {exec_result.status}"
            )[:1024]
            await self.session.flush()
            _log.warning(
                "rider_cod_recharge_failed",
                session_id=str(row.id),
                error_code=exec_result.error_code,
                error_message=exec_result.error_message,
            )
        return row

    # ------------------------------------------------------------------
    # callback (Bkash → us, after rider finishes hosted page)
    # ------------------------------------------------------------------
    async def mark_in_progress(
        self, *, recharge_session_id: UUID,
    ) -> RiderCodRechargeSession | None:
        row = await self._get_by_id(recharge_session_id)
        if row is None:
            return None
        if row.status == COD_RECHARGE_STATUS_INITIATED:
            row.status = COD_RECHARGE_STATUS_IN_PROGRESS
            await self.session.flush()
        return row

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------
    async def _apply_capture(
        self, *,
        row: RiderCodRechargeSession,
        captured_amount: Decimal,
        provider_trx_id: str,
        principal: Principal,
    ) -> None:
        captured_amount = _q(captured_amount)
        if captured_amount <= 0:
            row.status = COD_RECHARGE_STATUS_FAILED
            row.failure_reason = "Bkash reported zero capture amount"
            await self.session.flush()
            return

        wallet = await self.wallets.get_by_rider_id(row.rider_id)
        if wallet is None:
            raise WalletNotFoundError(
                f"Wallet missing for rider {row.rider_id}",
            )

        # Insert auto-verified settlement (Bkash already proved the txn).
        settlement = await self.settlements.add(
            rider_id=row.rider_id,
            shift_id=row.shift_id,
            settlement_date=date.today(),
            mfs_provider="bkash",
            sender_mfs_number=row.payer_mfs_number,
            company_receiver_account="bkash-merchant",  # Set from settings if needed
            submitted_amount=captured_amount,
            transaction_id=provider_trx_id or f"BKASH-{row.provider_payment_id}",
            transaction_time=utc_now(),
            proof_image_url=None,
            status=SettlementStatus.VERIFIED.value,
        )

        # Decrement payable_to_company atomically with the ledger entry.
        new_payable = _q(wallet.wallet_payable_to_company - captured_amount)
        if new_payable < 0:
            new_payable = Decimal("0")

        await self.wallets.update(
            wallet_id=wallet.id,
            wallet_payable_to_company=new_payable,
            wallet_status=RiderWalletService._derive_status(
                wallet,
                payable=new_payable,
                pending=wallet.wallet_pending_settlement,
            ),
        )
        await self.ledgers.add(
            rider_id=row.rider_id,
            shift_id=row.shift_id,
            settlement_id=settlement.id,
            entry_type=LedgerEntryType.SETTLEMENT_VERIFIED.value,
            direction=LedgerDirection.DEBIT.value,
            amount=captured_amount,
            balance_after=new_payable,
            note=f"Bkash recharge captured: {provider_trx_id}",
        )

        row.status = COD_RECHARGE_STATUS_COMPLETED
        row.captured_amount = captured_amount
        row.provider_trx_id = provider_trx_id
        row.completed_at = utc_now()
        await self.session.flush()

        _log.info(
            "rider_cod_recharge_completed",
            session_id=str(row.id),
            rider_id=str(row.rider_id),
            captured=str(captured_amount),
            trx_id=provider_trx_id,
            settlement_id=str(settlement.id),
        )

    async def _get_by_id(
        self, session_id: UUID,
    ) -> RiderCodRechargeSession | None:
        result = await self.session.execute(
            select(RiderCodRechargeSession).where(
                RiderCodRechargeSession.id == session_id,
            ),
        )
        return result.scalar_one_or_none()

    async def _get_by_idempotency_key(
        self, key: str,
    ) -> RiderCodRechargeSession | None:
        result = await self.session.execute(
            select(RiderCodRechargeSession).where(
                RiderCodRechargeSession.idempotency_key == key,
            ),
        )
        return result.scalar_one_or_none()
