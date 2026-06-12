"""ORM models for the Finance Manager operational queues.

Sits alongside ``models.py`` (the accounting kernel — accounts /
journal entries / supplier bills). These tables are workflow QUEUES,
not double-entry ledgers — every row represents an operator decision
pending or recorded by the Finance Manager.

See migration ``0090_finance_operations`` for the source schema and
``docs/AUTHORITY_MATRIX.md`` for the rules each table enforces.
"""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db.base import Base
from app.core.db.mixins import TimestampMixin


class FinanceAuditLog(Base):
    """Append-only Finance Manager action audit trail.

    One row per attempted-or-executed action. Insert-only at the DB
    level — the migration includes a (commented-out for now) ``REVOKE
    UPDATE, DELETE`` grant that flips on after the audit-export role
    is wired in ``grants.sql``. Until then immutability is enforced at
    the application layer (no UPDATE / DELETE statements anywhere in
    the finance service).
    """

    __tablename__ = "finance_audit_logs"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    audit_code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    actor_id: Mapped[str] = mapped_column(String(80), nullable=False)
    actor_role: Mapped[str] = mapped_column(String(48), nullable=False)
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(48), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(80), nullable=False)
    order_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    old_status: Mapped[str | None] = mapped_column(String(48), nullable=True)
    new_status: Mapped[str] = mapped_column(String(48), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    amount_minor: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    currency: Mapped[str] = mapped_column(
        String(8), nullable=False, server_default="BDT",
    )
    reference_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    device_info: Mapped[str | None] = mapped_column(String(255), nullable=True)
    metadata_json: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )
    created_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=text("(now() AT TIME ZONE 'UTC')"),
    )

    __table_args__ = (
        Index("ix_finance_audit_logs_entity", "entity_type", "entity_id"),
        Index("ix_finance_audit_logs_actor_created", "actor_id", "created_at"),
        Index("ix_finance_audit_logs_action", "action"),
    )


class FinanceRefundApproval(Base, TimestampMixin):
    __tablename__ = "finance_refund_approvals"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    order_id: Mapped[str] = mapped_column(String(80), nullable=False)
    payment_ref: Mapped[str | None] = mapped_column(String(80), nullable=True)
    requested_by: Mapped[str] = mapped_column(String(80), nullable=False)
    requested_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=text("(now() AT TIME ZONE 'UTC')"),
    )
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(
        String(8), nullable=False, server_default="BDT",
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, server_default="pending",
    )
    decided_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(nullable=True)
    decision_audit_log_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True,
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','approved','rejected','held')",
            name="ck_refund_approvals_status",
        ),
        CheckConstraint("amount_minor > 0", name="ck_refund_approvals_amount_pos"),
        Index("ix_refund_approvals_order", "order_id"),
        Index("ix_refund_approvals_status", "status"),
    )


class FinanceCodSettlement(Base, TimestampMixin):
    __tablename__ = "finance_cod_settlements"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    settlement_code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    rider_id: Mapped[str] = mapped_column(String(80), nullable=False)
    handover_date: Mapped[date] = mapped_column(Date, nullable=False)
    delivered_cod_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0",
    )
    expected_cod_minor: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default="0",
    )
    collected_cod_minor: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default="0",
    )
    deposited_cod_minor: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default="0",
    )
    mismatch_minor: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default="0",
    )
    settlement_ref: Mapped[str | None] = mapped_column(String(80), nullable=True)
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, server_default="pending",
    )
    verified_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(nullable=True)
    decision_audit_log_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True,
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','verified','rejected','reconciled')",
            name="ck_cod_settlements_status",
        ),
        Index("ix_cod_settlements_rider", "rider_id"),
        Index("ix_cod_settlements_date", "handover_date"),
        Index("ix_cod_settlements_status", "status"),
    )


class FinanceSellerPayoutBatch(Base, TimestampMixin):
    __tablename__ = "finance_seller_payout_batches"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    batch_code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    seller_id: Mapped[str] = mapped_column(String(80), nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    payable_minor: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default="0",
    )
    commission_minor: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default="0",
    )
    chargeback_minor: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default="0",
    )
    net_minor: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default="0",
    )
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, server_default="pending",
    )
    approved_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(nullable=True)
    released_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    released_at: Mapped[datetime | None] = mapped_column(nullable=True)
    held_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    held_at: Mapped[datetime | None] = mapped_column(nullable=True)
    hold_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    decision_audit_log_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True,
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','approved','held','released','failed')",
            name="ck_seller_payout_status",
        ),
        Index("ix_seller_payouts_seller", "seller_id"),
        Index("ix_seller_payouts_status", "status"),
    )


class FinanceRiderPayoutBatch(Base, TimestampMixin):
    __tablename__ = "finance_rider_payout_batches"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    batch_code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    rider_id: Mapped[str] = mapped_column(String(80), nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    incentive_minor: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default="0",
    )
    penalty_minor: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default="0",
    )
    net_minor: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default="0",
    )
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, server_default="pending",
    )
    approved_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(nullable=True)
    released_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    released_at: Mapped[datetime | None] = mapped_column(nullable=True)
    verified_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(nullable=True)
    held_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    held_at: Mapped[datetime | None] = mapped_column(nullable=True)
    hold_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    decision_audit_log_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True,
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','approved','held','released','verified','failed')",
            name="ck_rider_payout_status",
        ),
        Index("ix_rider_payouts_rider", "rider_id"),
        Index("ix_rider_payouts_status", "status"),
    )


class FinanceWalletLedgerAdjustment(Base, TimestampMixin):
    __tablename__ = "finance_wallet_ledger_adjustments"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    wallet_type: Mapped[str] = mapped_column(String(24), nullable=False)
    wallet_owner_id: Mapped[str] = mapped_column(String(80), nullable=False)
    direction: Mapped[str] = mapped_column(String(8), nullable=False)
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(
        String(8), nullable=False, server_default="BDT",
    )
    source_type: Mapped[str] = mapped_column(String(48), nullable=False)
    source_ref: Mapped[str] = mapped_column(String(80), nullable=False)
    requested_by: Mapped[str] = mapped_column(String(80), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, server_default="pending",
    )
    approved_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(nullable=True)
    decision_audit_log_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True,
    )

    __table_args__ = (
        CheckConstraint(
            "wallet_type IN ('customer','rider','seller')",
            name="ck_wallet_adj_type",
        ),
        CheckConstraint(
            "direction IN ('debit','credit')",
            name="ck_wallet_adj_direction",
        ),
        CheckConstraint(
            "status IN ('pending','approved','rejected')",
            name="ck_wallet_adj_status",
        ),
        CheckConstraint("amount_minor > 0", name="ck_wallet_adj_amount_pos"),
        Index("ix_wallet_adj_owner", "wallet_type", "wallet_owner_id"),
        Index("ix_wallet_adj_status", "status"),
    )


class FinanceAdjustmentRequest(Base, TimestampMixin):
    __tablename__ = "finance_adjustment_requests"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    request_code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    category: Mapped[str] = mapped_column(String(48), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(48), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(80), nullable=False)
    amount_minor: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    currency: Mapped[str] = mapped_column(
        String(8), nullable=False, server_default="BDT",
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    requested_by: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, server_default="pending",
    )
    resolved_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(nullable=True)
    decision_audit_log_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True,
    )
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','approved','rejected','resolved')",
            name="ck_adjustment_requests_status",
        ),
        Index("ix_adjustment_requests_status", "status"),
    )
