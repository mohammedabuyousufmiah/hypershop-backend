"""Compliance: licenses, pharmacists, shifts, check log

Revision ID: 0014_compliance
Revises: 0013_returns
Create Date: 2026-05-03

``compliance_check_log`` is REVOKEd UPDATE/DELETE — audit trail must not
be rewritable. Pharmacist shifts get a partial unique on
``WHERE closed_at IS NULL`` so a single pharmacist cannot have two open
shifts simultaneously (defence against double-check-in).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0014_compliance"
down_revision: str | Sequence[str] | None = "0013_returns"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "compliance_licenses",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("license_type", sa.String(32), nullable=False),
        sa.Column("license_number", sa.String(96), nullable=False),
        sa.Column("issuing_authority", sa.String(160), nullable=False),
        sa.Column("issued_on", sa.Date(), nullable=False),
        sa.Column("expires_on", sa.Date(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("notes", sa.String(2048), nullable=True),
        sa.Column("suspended_reason", sa.String(512), nullable=True),
        sa.Column("revoked_reason", sa.String(512), nullable=True),
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
        sa.UniqueConstraint("license_number", name="uq_compliance_licenses_number"),
        sa.CheckConstraint(
            "license_type IN ('drug_license','trade_license',"
            "'gst_registration','other')",
            name="ck_compliance_licenses_type_enum",
        ),
        sa.CheckConstraint(
            "status IN ('active','suspended','revoked')",
            name="ck_compliance_licenses_status_enum",
        ),
        sa.CheckConstraint(
            "expires_on >= issued_on",
            name="ck_compliance_licenses_expires_after_issue",
        ),
    )
    op.create_index(
        "ix_compliance_licenses_status_expires",
        "compliance_licenses",
        ["status", "expires_on"],
    )
    op.create_index(
        "ix_compliance_licenses_type", "compliance_licenses", ["license_type"],
    )

    op.create_table(
        "pharmacists",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("council_registration_no", sa.String(64), nullable=False),
        sa.Column("contact_phone", sa.String(32), nullable=True),
        sa.Column(
            "linked_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.text("true"),
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
        sa.UniqueConstraint("code", name="uq_pharmacists_code"),
        sa.UniqueConstraint(
            "council_registration_no", name="uq_pharmacists_council_reg",
        ),
    )
    op.create_index("ix_pharmacists_is_active", "pharmacists", ["is_active"])

    op.create_table(
        "pharmacist_shifts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "pharmacist_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pharmacists.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "opened_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.String(1024), nullable=True),
        sa.CheckConstraint(
            "closed_at IS NULL OR closed_at >= opened_at",
            name="ck_pharmacist_shifts_closed_after_open",
        ),
    )
    op.create_index(
        "ix_pharmacist_shifts_open", "pharmacist_shifts", ["closed_at"],
    )
    op.create_index(
        "ix_pharmacist_shifts_pharmacist",
        "pharmacist_shifts",
        ["pharmacist_id", "opened_at"],
    )
    # At most one open shift per pharmacist.
    op.execute(
        """
        CREATE UNIQUE INDEX uq_pharmacist_shifts_one_open_per_pharmacist
        ON pharmacist_shifts (pharmacist_id)
        WHERE closed_at IS NULL
        """,
    )

    op.create_table(
        "compliance_check_log",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.String(512), nullable=True),
        sa.Column(
            "actor_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("reference_type", sa.String(48), nullable=True),
        sa.Column(
            "reference_id", postgresql.UUID(as_uuid=True), nullable=True,
        ),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.CheckConstraint(
            "kind IN ('sales_license','rx_pharmacist_on_duty')",
            name="ck_compliance_check_log_kind_enum",
        ),
    )
    op.create_index(
        "ix_compliance_check_log_kind_at",
        "compliance_check_log",
        ["kind", "occurred_at"],
    )
    op.create_index(
        "ix_compliance_check_log_reference",
        "compliance_check_log",
        ["reference_type", "reference_id"],
    )

    op.execute(
        """
        DO $$
        BEGIN
          REVOKE UPDATE, DELETE ON TABLE compliance_check_log FROM PUBLIC;
        EXCEPTION WHEN insufficient_privilege THEN
          NULL;
        END$$;
        """,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_compliance_check_log_reference", table_name="compliance_check_log",
    )
    op.drop_index(
        "ix_compliance_check_log_kind_at", table_name="compliance_check_log",
    )
    op.drop_table("compliance_check_log")

    op.execute(
        "DROP INDEX IF EXISTS uq_pharmacist_shifts_one_open_per_pharmacist",
    )
    op.drop_index(
        "ix_pharmacist_shifts_pharmacist", table_name="pharmacist_shifts",
    )
    op.drop_index("ix_pharmacist_shifts_open", table_name="pharmacist_shifts")
    op.drop_table("pharmacist_shifts")

    op.drop_index("ix_pharmacists_is_active", table_name="pharmacists")
    op.drop_table("pharmacists")

    op.drop_index(
        "ix_compliance_licenses_type", table_name="compliance_licenses",
    )
    op.drop_index(
        "ix_compliance_licenses_status_expires", table_name="compliance_licenses",
    )
    op.drop_table("compliance_licenses")
