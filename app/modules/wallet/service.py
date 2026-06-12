"""Hypershop wallet service.

Public API:
  * get_or_create_wallet(user_id) — idempotent, returns the row.
  * get_balance(user_id) — fast read (single row).
  * credit(user_id, amount_minor, source_type, source_id?, memo?) — bumps
    balance + appends a credit txn. Returns the txn row.
  * debit(user_id, amount_minor, source_type, source_id?, memo?) — same
    but downward; raises ConflictError if balance would go negative.
  * list_transactions(user_id, offset, limit) — newest first.

Money is integer minor units everywhere internally. The API layer
converts to decimal-as-string for FE consumption.
"""
from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError
from app.core.time import utc_now
from app.modules.wallet.models import HypershopWallet, HypershopWalletTxn


def minor_to_decimal_str(minor: int, *, places: int = 2) -> str:
    """1234 → "12.34". Avoids float for money safety."""
    sign = "-" if minor < 0 else ""
    abs_minor = abs(int(minor))
    base = 10 ** places
    whole, frac = divmod(abs_minor, base)
    return f"{sign}{whole}.{frac:0{places}d}"


def decimal_str_to_minor(value: str, *, places: int = 2) -> int:
    return int((Decimal(value) * (10 ** places)).to_integral_value())


class WalletService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_or_create_wallet(
        self, *, user_id: UUID, currency: str = "BDT",
    ) -> HypershopWallet:
        existing = (
            await self.session.execute(
                select(HypershopWallet).where(
                    HypershopWallet.customer_user_id == user_id
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing

        wallet = HypershopWallet(
            customer_user_id=user_id,
            currency=currency,
            balance_minor=0,
            status="ACTIVE",
        )
        self.session.add(wallet)
        await self.session.flush()
        return wallet

    async def get_balance(self, *, user_id: UUID) -> HypershopWallet:
        return await self.get_or_create_wallet(user_id=user_id)

    async def credit(
        self,
        *,
        user_id: UUID,
        amount_minor: int,
        source_type: str,
        source_id: UUID | None = None,
        memo: str | None = None,
    ) -> HypershopWalletTxn:
        if amount_minor <= 0:
            raise ConflictError("Credit amount must be positive.")
        wallet = await self.get_or_create_wallet(user_id=user_id)
        if wallet.status != "ACTIVE":
            raise ConflictError(f"Wallet is {wallet.status}, cannot credit.")

        new_balance = int(wallet.balance_minor) + int(amount_minor)
        wallet.balance_minor = new_balance
        wallet.last_activity_at = utc_now()

        txn = HypershopWalletTxn(
            wallet_id=wallet.id,
            kind="credit",
            amount_minor=amount_minor,
            balance_after_minor=new_balance,
            source_type=source_type,
            source_id=source_id,
            memo=memo,
        )
        self.session.add(txn)
        await self.session.flush()
        return txn

    async def debit(
        self,
        *,
        user_id: UUID,
        amount_minor: int,
        source_type: str,
        source_id: UUID | None = None,
        memo: str | None = None,
    ) -> HypershopWalletTxn:
        if amount_minor <= 0:
            raise ConflictError("Debit amount must be positive.")
        wallet = await self.get_or_create_wallet(user_id=user_id)
        if wallet.status != "ACTIVE":
            raise ConflictError(f"Wallet is {wallet.status}, cannot debit.")
        if wallet.balance_minor < amount_minor:
            raise ConflictError(
                f"Insufficient wallet balance: have {wallet.balance_minor}, "
                f"need {amount_minor}.",
            )

        new_balance = int(wallet.balance_minor) - int(amount_minor)
        wallet.balance_minor = new_balance
        wallet.last_activity_at = utc_now()

        txn = HypershopWalletTxn(
            wallet_id=wallet.id,
            kind="debit",
            amount_minor=amount_minor,
            balance_after_minor=new_balance,
            source_type=source_type,
            source_id=source_id,
            memo=memo,
        )
        self.session.add(txn)
        await self.session.flush()
        return txn

    async def list_transactions(
        self, *, user_id: UUID, offset: int = 0, limit: int = 50,
    ) -> tuple[list[HypershopWalletTxn], int]:
        wallet = await self.get_or_create_wallet(user_id=user_id)
        total = (
            await self.session.execute(
                select(func.count(HypershopWalletTxn.id))
                .where(HypershopWalletTxn.wallet_id == wallet.id)
            )
        ).scalar_one()
        rows = (
            await self.session.execute(
                select(HypershopWalletTxn)
                .where(HypershopWalletTxn.wallet_id == wallet.id)
                .order_by(HypershopWalletTxn.created_at.desc())
                .offset(offset)
                .limit(limit)
            )
        ).scalars().all()
        return list(rows), int(total)
