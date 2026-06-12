"""Rider KYC — onboarding submission for rider mobile app.

Backs `/api/v1/rider/kyc` (GET + POST). Stores per-user textual KYC,
photo URLs, address, and one reference contact. Hub admin reviews at
/admin/rider-kyc.

Revision: 0095_rider_kyc
Down revision: 0094_seo_domination
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0095_rider_kyc"
down_revision = "0094_seo_domination"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rider_kyc_submissions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,  # one submission per rider user
        ),
        sa.Column("full_name", sa.String(160), nullable=False),
        sa.Column("profile_photo_url", sa.Text, nullable=True),
        sa.Column("nid_number", sa.String(32), nullable=True),
        sa.Column("nid_front_url", sa.Text, nullable=True),
        sa.Column("nid_back_url", sa.Text, nullable=True),
        sa.Column("mobile_primary", sa.String(32), nullable=False),
        sa.Column("mobile_secondary", sa.String(32), nullable=True),
        sa.Column("house_no", sa.String(64), nullable=True),
        sa.Column("flat_no", sa.String(32), nullable=True),
        sa.Column("area", sa.String(160), nullable=True),
        sa.Column("lat", sa.Float, nullable=True),
        sa.Column("lng", sa.Float, nullable=True),
        sa.Column("reference_name", sa.String(160), nullable=True),
        sa.Column("reference_nid", sa.String(32), nullable=True),
        sa.Column("reference_mobile", sa.String(32), nullable=True),
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("rejection_reason", sa.Text, nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "reviewed_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.CheckConstraint(
            "status IN ('pending','verified','rejected')",
            name="ck_rider_kyc_status",
        ),
    )
    op.create_index(
        "ix_rider_kyc_status",
        "rider_kyc_submissions",
        ["status", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_rider_kyc_status", table_name="rider_kyc_submissions")
    op.drop_table("rider_kyc_submissions")
