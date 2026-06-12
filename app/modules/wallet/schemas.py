"""Pydantic schemas for the customer-facing /me/wallet endpoints.

Output shapes match the existing FE WalletWire / WalletAvailabilityWire
in @ecom/types so AccountWalletClient + AccountOverviewClient render
without modification.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


WalletStatus = Literal["ACTIVE", "FROZEN", "CLOSED"]
TxnKind = Literal["credit", "debit", "adjust"]


class WalletOut(BaseModel):
    """Matches FE WalletWire (id, customer_id, currency, balance, status, ...).

    `balance` is a decimal-as-string for FE/JSON safety; computed from
    `balance_minor / 100` at serialise time.
    """
    id: UUID
    customer_id: UUID
    currency: str
    balance: str
    status: WalletStatus
    last_activity_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=False)


class WalletAvailabilityOut(BaseModel):
    """Matches FE WalletAvailabilityWire — used by checkout to know
    how much wallet credit can be applied to the current cart."""
    available_balance: str
    currency: str
    status: WalletStatus


class WalletTxnOut(BaseModel):
    id: UUID
    wallet_id: UUID
    kind: TxnKind
    amount: str            # decimal as string
    balance_after: str     # decimal as string
    source_type: str | None
    source_id: UUID | None
    memo: str | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=False)


class WalletTxnListOut(BaseModel):
    items: list[WalletTxnOut]
    total: int
