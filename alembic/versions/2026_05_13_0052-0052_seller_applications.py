"""0052 seller_applications — sellers phase 4 (self-serve onboarding).

Adds a single ``seller_applications`` table tracking the lifecycle of
a user's application to become a marketplace seller:

  pending → kyc_submitted → approved   (terminal — Seller + SellerUser rows created)
                          ↘ rejected    (terminal — applicant notified, can re-apply after cooldown)
                          ↘ info_requested  (admin asked for more docs; back to kyc_submitted on next upload)

The actual ``Seller`` + ``SellerUser`` rows are created ONLY on
approve. Until then the application is just a queue entry.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0052_seller_applications"
down_revision: str | Sequence[str] | None = "0051_sprint9_phases"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "seller_applications",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "applicant_user_id", UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False, index=True,
        ),
        # Pre-filled business details — copied onto the Seller row on approve
        sa.Column("business_name", sa.String(200), nullable=False),
        sa.Column("contact_email", sa.String(320), nullable=True),
        sa.Column("contact_phone", sa.String(32), nullable=True),
        # KYC documents — stored as URL references (R2/Bunny). Operators can
        # extend with separate seller_application_documents table for
        # multi-document workflows; v1 keeps it inline + simple.
        sa.Column("nid", sa.String(32), nullable=True),
        sa.Column("tin", sa.String(32), nullable=True),
        sa.Column("trade_license_no", sa.String(64), nullable=True),
        sa.Column("trade_license_url", sa.Text, nullable=True),
        sa.Column("nid_front_url", sa.Text, nullable=True),
        sa.Column("nid_back_url", sa.Text, nullable=True),
        sa.Column("bank_account_name", sa.String(200), nullable=True),
        sa.Column("bank_account_number", sa.String(32), nullable=True),
        sa.Column("bank_name", sa.String(120), nullable=True),
        sa.Column("bank_branch", sa.String(120), nullable=True),
        # Application state
        sa.Column(
            "status", sa.String(24), nullable=False, server_default="pending",
        ),
        sa.Column("admin_note", sa.Text, nullable=True),
        sa.Column("rejection_reason", sa.String(500), nullable=True),
        sa.Column("info_requested_text", sa.Text, nullable=True),
        # Once approved, point to the created seller (NULL until then)
        sa.Column(
            "approved_seller_id", UUID(as_uuid=True),
            sa.ForeignKey("sellers.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "moderated_by", UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("moderated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("kyc_submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "status IN ('pending','kyc_submitted','info_requested','approved','rejected')",
            name="ck_seller_applications_status",
        ),
        # One pending/non-terminal application per user — re-apply
        # allowed only once the previous is approved or rejected.
        sa.Index(
            "uq_seller_applications_user_open",
            "applicant_user_id",
            unique=True,
            postgresql_where=sa.text("status IN ('pending','kyc_submitted','info_requested')"),
        ),
    )
    op.create_index(
        "ix_seller_applications_status_created",
        "seller_applications", ["status", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_seller_applications_status_created", table_name="seller_applications",
    )
    op.drop_index(
        "uq_seller_applications_user_open", table_name="seller_applications",
    )
    op.drop_table("seller_applications")
