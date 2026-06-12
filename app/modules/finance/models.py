"""Finance ORM models — double-entry accounting kernel.

Schema design notes
-------------------
- :class:`Account` — chart of accounts. Code is fixed forever; type
  determines normal balance side.
- :class:`AccountingPeriod` — calendar-month grain. ``status='locked'``
  rejects further posts on any date inside the period.
- :class:`JournalEntry` — header. Owns balance invariant (sum of debits =
  sum of credits) at posting time. Status: draft → posted → reversed.
  Reversal does NOT mutate the original; it inserts a new mirror entry.
- :class:`JournalLine` — debit OR credit (exactly one is non-zero per
  line). Append-only after posting (REVOKE UPDATE/DELETE applied to lines
  of posted entries via the migration).
- :class:`SupplierBill` / :class:`SupplierPayment` — AP subledger.
- :class:`CodDeposit` — rider cash-in-hand → bank reconciliation.
- :class:`RefundRecord` — return → refund payable → paid.
- :class:`DailyClose` — snapshot row written at end-of-day close;
  immutable summary used for daily reports.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db.base import Base
from app.core.db.mixins import TimestampMixin


class Account(Base, TimestampMixin):
    __tablename__ = "fin_accounts"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    code: Mapped[str] = mapped_column(String(16), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    description: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    is_system: Mapped[bool] = mapped_column(
        nullable=False, server_default=text("false"),
    )
    is_active: Mapped[bool] = mapped_column(
        nullable=False, server_default=text("true"),
    )

    __table_args__ = (
        CheckConstraint(
            "type IN ('asset','liability','equity','revenue','expense')",
            name="type_enum",
        ),
        CheckConstraint("code = upper(code)", name="code_uppercase_or_digits"),
        Index("ix_fin_accounts_type", "type"),
        Index("ix_fin_accounts_is_active", "is_active"),
    )


class AccountingPeriod(Base, TimestampMixin):
    """A calendar-month period.

    Once ``status='locked'`` (set by ``close_period``), the service rejects
    any journal entry posting whose ``entry_date`` falls inside the period.
    Daily close uses :class:`DailyClose` instead — finer grain.
    """

    __tablename__ = "fin_accounting_periods"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    year: Mapped[int] = mapped_column(nullable=False)
    month: Mapped[int] = mapped_column(nullable=False)
    starts_on: Mapped[date] = mapped_column(Date, nullable=False)
    ends_on: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="open",
    )
    closed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    closed_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    closing_notes: Mapped[str | None] = mapped_column(String(2048), nullable=True)

    __table_args__ = (
        UniqueConstraint("year", "month", name="uq_fin_periods_year_month"),
        CheckConstraint(
            "month BETWEEN 1 AND 12", name="month_range",
        ),
        CheckConstraint(
            "status IN ('open','locked')", name="status_enum",
        ),
        Index("ix_fin_periods_status", "status"),
    )


class JournalEntry(Base, TimestampMixin):
    __tablename__ = "fin_journal_entries"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    entry_date: Mapped[date] = mapped_column(Date, nullable=False)
    narration: Mapped[str] = mapped_column(String(512), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)

    reference_type: Mapped[str | None] = mapped_column(String(48), nullable=True)
    reference_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True,
    )
    reverses_entry_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("fin_journal_entries.id", ondelete="RESTRICT"),
        nullable=True,
    )

    posted_at: Mapped[datetime | None] = mapped_column(nullable=True)
    posted_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    extra: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb"),
    )

    lines: Mapped[list[JournalLine]] = relationship(
        "JournalLine",
        back_populates="entry",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="JournalLine.line_number",
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('draft','posted','reversed')", name="status_enum",
        ),
        CheckConstraint(
            "currency = upper(currency) AND length(currency) = 3",
            name="currency_iso",
        ),
        Index("ix_fin_journal_entries_entry_date", "entry_date"),
        Index("ix_fin_journal_entries_status_date", "status", "entry_date"),
        Index("ix_fin_journal_entries_reference", "reference_type", "reference_id"),
        Index("ix_fin_journal_entries_source", "source"),
    )


class JournalLine(Base):
    """A single debit-or-credit line on a journal entry.

    Exactly one of ``debit`` / ``credit`` is non-zero (CHECK enforced).
    Append-only once the parent entry is posted — the migration revokes
    UPDATE on this table from PUBLIC. Reversal happens by inserting an
    offsetting entry, never by editing existing rows.
    """

    __tablename__ = "fin_journal_lines"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    entry_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("fin_journal_entries.id", ondelete="CASCADE"),
        nullable=False,
    )
    line_number: Mapped[int] = mapped_column(nullable=False)
    account_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("fin_accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    account_code: Mapped[str] = mapped_column(String(16), nullable=False)
    debit: Mapped[Decimal] = mapped_column(
        Numeric(16, 2), nullable=False, server_default=text("0"),
    )
    credit: Mapped[Decimal] = mapped_column(
        Numeric(16, 2), nullable=False, server_default=text("0"),
    )
    memo: Mapped[str | None] = mapped_column(String(512), nullable=True)

    entry: Mapped[JournalEntry] = relationship(
        "JournalEntry", back_populates="lines",
    )

    __table_args__ = (
        UniqueConstraint(
            "entry_id", "line_number", name="uq_fin_journal_lines_entry_line",
        ),
        CheckConstraint("debit >= 0 AND credit >= 0", name="amounts_nonneg"),
        CheckConstraint(
            "(debit > 0 AND credit = 0) OR (debit = 0 AND credit > 0)",
            name="exactly_one_side",
        ),
        Index("ix_fin_journal_lines_account_id", "account_id"),
        Index("ix_fin_journal_lines_entry", "entry_id"),
    )


class SupplierBill(Base, TimestampMixin):
    """An AP subledger record. One row per supplier invoice.

    Booking the bill posts: Dr Inventory / Cr AP-Suppliers (+ VAT if any).
    Settlement (one or more :class:`SupplierPayment` rows) posts:
    Dr AP-Suppliers / Cr Bank.
    """

    __tablename__ = "fin_supplier_bills"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    supplier_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("suppliers.id", ondelete="RESTRICT"),
        nullable=False,
    )
    goods_receipt_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("goods_receipts.id", ondelete="SET NULL"),
        nullable=True,
    )
    bill_number: Mapped[str] = mapped_column(String(96), nullable=False)
    bill_date: Mapped[date] = mapped_column(Date, nullable=False)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    subtotal: Mapped[Decimal] = mapped_column(Numeric(16, 2), nullable=False)
    tax_total: Mapped[Decimal] = mapped_column(
        Numeric(16, 2), nullable=False, server_default=text("0"),
    )
    grand_total: Mapped[Decimal] = mapped_column(Numeric(16, 2), nullable=False)
    amount_paid: Mapped[Decimal] = mapped_column(
        Numeric(16, 2), nullable=False, server_default=text("0"),
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    booking_entry_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("fin_journal_entries.id", ondelete="SET NULL"),
        nullable=True,
    )
    notes: Mapped[str | None] = mapped_column(String(2048), nullable=True)

    payments: Mapped[list[SupplierPayment]] = relationship(
        "SupplierPayment", back_populates="bill", lazy="selectin",
        order_by="SupplierPayment.paid_on",
    )

    __table_args__ = (
        UniqueConstraint(
            "supplier_id", "bill_number", name="uq_fin_supplier_bills_supplier_number",
        ),
        CheckConstraint(
            "status IN ('open','partially_paid','paid','void')", name="status_enum",
        ),
        CheckConstraint(
            "currency = upper(currency) AND length(currency) = 3",
            name="currency_iso",
        ),
        CheckConstraint(
            "subtotal >= 0 AND tax_total >= 0 AND grand_total >= 0",
            name="amounts_nonneg",
        ),
        CheckConstraint(
            "amount_paid >= 0 AND amount_paid <= grand_total",
            name="paid_within_total",
        ),
        Index("ix_fin_supplier_bills_supplier_id", "supplier_id"),
        Index("ix_fin_supplier_bills_status", "status"),
        Index("ix_fin_supplier_bills_due_date", "due_date"),
    )


class SupplierPayment(Base, TimestampMixin):
    __tablename__ = "fin_supplier_payments"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    bill_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("fin_supplier_bills.id", ondelete="RESTRICT"),
        nullable=False,
    )
    paid_on: Mapped[date] = mapped_column(Date, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(16, 2), nullable=False)
    method: Mapped[str] = mapped_column(String(32), nullable=False)
    bank_reference: Mapped[str | None] = mapped_column(String(160), nullable=True)
    notes: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    journal_entry_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("fin_journal_entries.id", ondelete="SET NULL"),
        nullable=True,
    )

    bill: Mapped[SupplierBill] = relationship(
        "SupplierBill", back_populates="payments",
    )

    __table_args__ = (
        CheckConstraint("amount > 0", name="amount_positive"),
        Index("ix_fin_supplier_payments_bill", "bill_id"),
        Index("ix_fin_supplier_payments_paid_on", "paid_on"),
    )


class CodDeposit(Base, TimestampMixin):
    """A rider's cash-in-hand → bank deposit reconciliation.

    Aggregates one or more delivery COD collections. The booking JE
    moves Dr Bank / Cr Cash-on-Hand-Rider for the deposited amount; any
    discrepancy hits Cr Cash-on-Hand-Rider / Dr COD-Short-Over.
    """

    __tablename__ = "fin_cod_deposits"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    rider_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("riders.id", ondelete="RESTRICT"),
        nullable=False,
    )
    deposit_date: Mapped[date] = mapped_column(Date, nullable=False)
    expected_amount: Mapped[Decimal] = mapped_column(Numeric(16, 2), nullable=False)
    deposited_amount: Mapped[Decimal] = mapped_column(Numeric(16, 2), nullable=False)
    discrepancy: Mapped[Decimal] = mapped_column(
        Numeric(16, 2), nullable=False, server_default=text("0"),
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    bank_reference: Mapped[str | None] = mapped_column(String(160), nullable=True)
    notes: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    journal_entry_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("fin_journal_entries.id", ondelete="SET NULL"),
        nullable=True,
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','deposited','discrepancy')", name="status_enum",
        ),
        CheckConstraint(
            "expected_amount >= 0 AND deposited_amount >= 0",
            name="amounts_nonneg",
        ),
        Index("ix_fin_cod_deposits_rider_date", "rider_id", "deposit_date"),
        Index("ix_fin_cod_deposits_status", "status"),
    )


class RefundRecord(Base, TimestampMixin):
    """A refund obligation, accrued from a completed return.

    Accrual posts: Dr Sales-Returns / Cr Refund-Payable. Payment posts:
    Dr Refund-Payable / Cr Bank (or Cash-on-Hand-Rider).
    """

    __tablename__ = "fin_refund_records"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    return_request_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("return_requests.id", ondelete="RESTRICT"),
        nullable=False,
    )
    order_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="RESTRICT"),
        nullable=False,
    )
    customer_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    accrued_amount: Mapped[Decimal] = mapped_column(Numeric(16, 2), nullable=False)
    paid_amount: Mapped[Decimal] = mapped_column(
        Numeric(16, 2), nullable=False, server_default=text("0"),
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    accrual_entry_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("fin_journal_entries.id", ondelete="SET NULL"),
        nullable=True,
    )
    payment_entry_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("fin_journal_entries.id", ondelete="SET NULL"),
        nullable=True,
    )
    paid_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    method: Mapped[str | None] = mapped_column(String(32), nullable=True)
    bank_reference: Mapped[str | None] = mapped_column(String(160), nullable=True)
    notes: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "return_request_id", name="uq_fin_refund_records_return",
        ),
        CheckConstraint(
            "status IN ('pending','paid','cancelled')", name="status_enum",
        ),
        CheckConstraint(
            "accrued_amount >= 0 AND paid_amount >= 0",
            name="amounts_nonneg",
        ),
        CheckConstraint(
            "paid_amount <= accrued_amount", name="paid_within_accrued",
        ),
        Index("ix_fin_refund_records_order", "order_id"),
        Index("ix_fin_refund_records_status", "status"),
    )


class DailyClose(Base):
    """Immutable end-of-day snapshot.

    Locks all journal entries dated on or before ``closing_date`` from
    further posting. The snapshot is used for the daily-summary report
    and as evidence the close was performed. REVOKE UPDATE/DELETE.
    """

    __tablename__ = "fin_daily_closes"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    closing_date: Mapped[date] = mapped_column(Date, nullable=False, unique=True)
    closed_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=text("(now() AT TIME ZONE 'UTC')"),
    )
    closed_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    entry_count: Mapped[int] = mapped_column(nullable=False)
    total_debits: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    total_credits: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    revenue_total: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    cogs_total: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    cash_in_total: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    cash_out_total: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    notes: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    snapshot: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb"),
    )

    __table_args__ = (
        CheckConstraint("entry_count >= 0", name="entry_count_nonneg"),
        Index("ix_fin_daily_closes_closing_date", "closing_date"),
    )
