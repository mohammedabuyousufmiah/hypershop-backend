"""Medication reminders

Revision ID: 0010_reminders
Revises: 0009_customer_wallet
Create Date: 2026-05-03

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010_reminders"
down_revision: str | Sequence[str] | None = "0009_customer_wallet"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "medication_reminders",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "patient_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("patient_phone", sa.String(32), nullable=False),
        sa.Column("patient_name", sa.String(160), nullable=False),
        sa.Column("slot", sa.String(16), nullable=False),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("medicine_label", sa.String(255), nullable=False),
        sa.Column("body", sa.String(512), nullable=False),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column(
            "status", sa.String(16), nullable=False, server_default="pending",
        ),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("last_error", sa.String(512), nullable=True),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
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
            "slot IN ('morning','afternoon','night')",
            name="ck_reminders_slot_enum",
        ),
        sa.CheckConstraint(
            "channel IN ('push','sms')",
            name="ck_reminders_channel_enum",
        ),
        sa.CheckConstraint(
            "status IN ('pending','dispatched','sent','failed','cancelled')",
            name="ck_reminders_status_enum",
        ),
    )
    op.create_index(
        "ix_reminders_pending_due",
        "medication_reminders",
        ["status", "scheduled_for"],
    )
    op.create_index(
        "ix_reminders_user_at",
        "medication_reminders",
        ["patient_user_id", "scheduled_for"],
    )
    op.create_index(
        "ix_reminders_phone_at",
        "medication_reminders",
        ["patient_phone", "scheduled_for"],
    )


def downgrade() -> None:
    op.drop_index("ix_reminders_phone_at", table_name="medication_reminders")
    op.drop_index("ix_reminders_user_at", table_name="medication_reminders")
    op.drop_index("ix_reminders_pending_due", table_name="medication_reminders")
    op.drop_table("medication_reminders")
