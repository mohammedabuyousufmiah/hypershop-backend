"""ORM models for the supplier-payment approval engine (Module 33).

Five new tables on top of existing ``fin_supplier_bills`` +
``fin_supplier_payments``:

  supplier_bill_approval_state    — 1:1 with fin_supplier_bills;
                                    holds approval workflow state +
                                    recommendation snapshot + flags
  supplier_bill_approvals         — append-only; one row per
                                    (bill × level) decision
  supplier_payment_recommendations — append-only history of recommendation
                                    engine runs per bill
  supplier_bank_accounts          — verified payout destinations per
                                    supplier (bank / MFS / cash)
  approval_workflows              — config: which thresholds need which
                                    extra approvals

ALTER fin_supplier_payments to add:
  - verification_status (PaymentVerificationStatus enum)
  - proof_file_url
  - executed_by, verified_by
  - bank_account_id  (FK to supplier_bank_accounts; nullable for cash)
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db.base import Base
from app.core.db.mixins import TimestampMixin


# ============================================================
#  supplier_bill_approval_state
# ============================================================
class SupplierBillApprovalState(Base, TimestampMixin):
    """1:1 with ``fin_supplier_bills`` — separates approval/recommendation
    metadata from the financial-ledger row.

    Created lazily on first approval action. A bill that's never gone
    through approval (e.g. legacy bills booked before this module was
    rolled out) simply has no approval_state row and is treated as
    APPROVED_FINAL by the legacy-pay-anyway path. New bills should
    always go through this module.
    """

    __tablename__ = "supplier_bill_approval_state"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    supplier_bill_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("fin_supplier_bills.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )

    # BillApprovalStatus enum.
    approval_status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="draft",
    )
    # Which workflow_code drove this bill (controls thresholds + lvl-4).
    workflow_code: Mapped[str | None] = mapped_column(
        String(80), nullable=True,
    )

    # Recommendation snapshot — populated by the engine; copied here
    # for fast list rendering. The history lives in
    # supplier_payment_recommendations.
    recommended_payment_date: Mapped[date | None] = mapped_column(
        Date, nullable=True,
    )
    recommended_payment_method: Mapped[str | None] = mapped_column(
        String(32), nullable=True,
    )
    recommended_payment_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(16, 2), nullable=True,
    )
    recommendation_score: Mapped[int | None] = mapped_column(
        Integer, nullable=True,
    )
    # PaymentPriority enum.
    payment_priority: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="normal",
    )
    last_recommended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    # Flags surfaced on every list view.
    dispute_flag: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"),
    )
    dispute_reason: Mapped[str | None] = mapped_column(
        Text, nullable=True,
    )
    duplicate_check_flag: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"),
    )
    duplicate_of_bill_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("fin_supplier_bills.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Most-recently approved bank account (set when level-3 chooses a
    # payout destination). Defended by FK; admin can change later via
    # a separate endpoint.
    selected_bank_account_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("supplier_bank_accounts.id", ondelete="SET NULL"),
        nullable=True,
    )

    submitted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    submitted_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    final_approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    __table_args__ = (
        CheckConstraint(
            "approval_status IN ('draft','submitted','level_1_verified',"
            "'level_2_approved','level_3_approved','super_admin_required',"
            "'approved_final','ready_for_payment','paid','reconciled',"
            "'returned_for_correction','rejected','on_hold')",
            name="ck_sbas_status_enum",
        ),
        CheckConstraint(
            "payment_priority IN ('critical','high','normal','low','on_hold')",
            name="ck_sbas_priority_enum",
        ),
        CheckConstraint(
            "recommendation_score IS NULL OR "
            "(recommendation_score >= 0 AND recommendation_score <= 100)",
            name="ck_sbas_score_range",
        ),
        Index(
            "ix_sbas_status_priority",
            "approval_status", "payment_priority",
        ),
        Index("ix_sbas_dispute", "dispute_flag"),
        Index("ix_sbas_duplicate", "duplicate_check_flag"),
    )


# ============================================================
#  supplier_bill_approvals (append-only)
# ============================================================
class SupplierBillApproval(Base):
    """One row per (bill × level) decision.

    Append-only. UNIQUE on (supplier_bill_id, level) so a level can
    only be decided once per bill (corrections require a returned →
    re-submit cycle that creates new rows for re-approval).
    """

    __tablename__ = "supplier_bill_approvals"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    supplier_bill_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("fin_supplier_bills.id", ondelete="CASCADE"),
        nullable=False,
    )
    # 1, 2, 3, or 4. See state.ApprovalLevel.
    level: Mapped[int] = mapped_column(Integer, nullable=False)
    approver_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    # Snapshot — stored even if the user later loses the role, so the
    # audit trail explains "who, with what role, approved when".
    approver_role: Mapped[str] = mapped_column(String(64), nullable=False)
    # ApprovalDecision enum.
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("(now() AT TIME ZONE 'UTC')"),
    )

    __table_args__ = (
        UniqueConstraint(
            "supplier_bill_id", "level",
            name="uq_sba_bill_level",
        ),
        CheckConstraint(
            "level >= 1 AND level <= 4",
            name="ck_sba_level_bounds",
        ),
        CheckConstraint(
            "decision IN ('pending','approved','rejected',"
            "'returned_for_correction')",
            name="ck_sba_decision_enum",
        ),
        Index(
            "ix_sba_bill_level",
            "supplier_bill_id", "level",
        ),
    )


# ============================================================
#  supplier_payment_recommendations
# ============================================================
class SupplierPaymentRecommendation(Base):
    """Append-only history of recommendation-engine runs per bill.

    The latest run's values are mirrored to ``supplier_bill_approval_state``
    for fast list rendering; this table is for debug + analytics
    ("why did we recommend pay-soon for bill X two weeks ago?").
    """

    __tablename__ = "supplier_payment_recommendations"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    supplier_bill_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("fin_supplier_bills.id", ondelete="CASCADE"),
        nullable=False,
    )
    recommended_payment_date: Mapped[date] = mapped_column(
        Date, nullable=False,
    )
    recommended_payment_amount: Mapped[Decimal] = mapped_column(
        Numeric(16, 2), nullable=False,
    )
    recommended_payment_method: Mapped[str | None] = mapped_column(
        String(32), nullable=True,
    )
    # PaymentPriority enum.
    priority_level: Mapped[str] = mapped_column(
        String(16), nullable=False,
    )
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    # Free-form: {"factors": {"overdue_days": 7, "supplier_critical": true, ...}}
    payload_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb"),
    )
    engine_version: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="v1",
    )
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("(now() AT TIME ZONE 'UTC')"),
    )

    __table_args__ = (
        CheckConstraint(
            "priority_level IN ('critical','high','normal','low','on_hold')",
            name="ck_spr_priority_enum",
        ),
        CheckConstraint(
            "score >= 0 AND score <= 100",
            name="ck_spr_score_range",
        ),
        Index(
            "ix_spr_bill_generated",
            "supplier_bill_id", "generated_at",
        ),
    )


# ============================================================
#  supplier_bank_accounts
# ============================================================
class SupplierBankAccount(Base, TimestampMixin):
    """Verified payout destinations per supplier.

    A bill can only reach READY_FOR_PAYMENT if the chosen
    ``selected_bank_account_id`` row has ``is_verified=true``.
    """

    __tablename__ = "supplier_bank_accounts"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    supplier_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("suppliers.id", ondelete="CASCADE"),
        nullable=False,
    )
    # SupplierBankAccountType enum.
    account_type: Mapped[str] = mapped_column(
        String(8), nullable=False,
    )
    account_name: Mapped[str] = mapped_column(String(160), nullable=False)
    bank_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # Mask all but last 4 digits before storing — full PAN is PII.
    account_number_masked: Mapped[str | None] = mapped_column(
        String(64), nullable=True,
    )
    iban_or_branch: Mapped[str | None] = mapped_column(
        String(255), nullable=True,
    )
    mfs_number: Mapped[str | None] = mapped_column(
        String(32), nullable=True,
    )
    is_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"),
    )
    is_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"),
    )
    verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    verified_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true"),
    )

    __table_args__ = (
        CheckConstraint(
            "account_type IN ('bank','mfs','cash')",
            name="ck_sba_acct_type_enum",
        ),
        # Only ONE default per supplier — partial unique index.
        Index(
            "uq_sba_supplier_default",
            "supplier_id",
            unique=True,
            postgresql_where=text("is_default = true AND is_active = true"),
        ),
        Index(
            "ix_sba_supplier_active",
            "supplier_id", "is_active",
        ),
    )


# ============================================================
#  approval_workflows  (config table)
# ============================================================
class ApprovalWorkflow(Base, TimestampMixin):
    """Approval-policy config rows.

    A bill picks a workflow at submit time based on amount thresholds.
    The default workflow ("standard") covers everyday bills; a
    "high_value" workflow with ``requires_super_admin=true`` covers
    bills over the threshold.

    Seeded by ``workflow_seed.seed_default_workflows`` at boot.
    """

    __tablename__ = "supplier_payment_approval_workflows"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    workflow_code: Mapped[str] = mapped_column(
        String(80), nullable=False, unique=True,
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="",
    )

    # Amount threshold (BDT). Bills with grand_total >= this AND lvl-4
    # required AND no other workflow matches better get this one.
    threshold_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(16, 2), nullable=True,
    )
    requires_super_admin: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"),
    )
    # Number of mandatory approval levels (always 3 for "standard"; 4
    # for high-value). Stored to allow future workflows with fewer
    # gates (e.g. 1-step "petty cash").
    min_approval_steps: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("3"),
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true"),
    )

    __table_args__ = (
        CheckConstraint(
            "min_approval_steps >= 1 AND min_approval_steps <= 4",
            name="ck_apw_steps_range",
        ),
        CheckConstraint(
            "threshold_amount IS NULL OR threshold_amount >= 0",
            name="ck_apw_threshold_nonneg",
        ),
    )
