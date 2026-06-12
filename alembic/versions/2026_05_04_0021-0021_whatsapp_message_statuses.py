"""WhatsApp delivery-status receipts.

Revision ID: 0021_whatsapp_message_statuses
Revises: 0020_payments
Create Date: 2026-05-04
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0021_whatsapp_message_statuses"
down_revision: str | Sequence[str] | None = "0020_payments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "whatsapp_message_statuses",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("wamid", sa.String(128), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("recipient_msisdn", sa.String(20), nullable=False),
        sa.Column(
            "status_timestamp",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.Column("error_code", sa.String(32), nullable=True),
        sa.Column("error_title", sa.String(256), nullable=True),
        sa.Column("error_message", sa.String(2048), nullable=True),
        sa.Column("raw_payload", postgresql.JSONB(), nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
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
        sa.UniqueConstraint(
            "wamid", "status",
            name="uq_whatsapp_message_statuses_wamid_status",
        ),
        sa.CheckConstraint(
            "status IN ('sent','delivered','read','failed','deleted')",
            name="ck_whatsapp_message_statuses_status_enum",
        ),
    )
    op.create_index(
        "ix_whatsapp_message_statuses_wamid",
        "whatsapp_message_statuses",
        ["wamid"],
    )
    op.create_index(
        "ix_whatsapp_message_statuses_recipient_received",
        "whatsapp_message_statuses",
        ["recipient_msisdn", "received_at"],
    )
    # Partial index — only failed rows. Tiny on disk, fast for ops queries.
    op.create_index(
        "ix_whatsapp_message_statuses_failed_received",
        "whatsapp_message_statuses",
        ["status", "received_at"],
        postgresql_where=sa.text("status = 'failed'"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_whatsapp_message_statuses_failed_received",
        table_name="whatsapp_message_statuses",
    )
    op.drop_index(
        "ix_whatsapp_message_statuses_recipient_received",
        table_name="whatsapp_message_statuses",
    )
    op.drop_index(
        "ix_whatsapp_message_statuses_wamid",
        table_name="whatsapp_message_statuses",
    )
    op.drop_table("whatsapp_message_statuses")
