"""Mobile customer-app support: device tokens + saved addresses,
plus a ``taken_at`` column on ``medication_reminders`` so the
customer can mark a reminder as taken.

Revision ID: 0017_mobile
Revises: 0016_ai
Create Date: 2026-05-03

Notes
- ``customer_addresses`` enforces "one default per customer" via a
  partial unique index (``WHERE is_default = true``). The service
  layer demotes the previous default in the same transaction as
  promoting the new one.
- ``device_tokens.is_active = false`` survives logout — we keep the
  row so push delivery telemetry can still attribute past sends.
- Adds ``taken_at`` + ``snoozed_until`` to ``medication_reminders``
  to let the customer app mark / snooze without a status side-effect
  on the dispatch state machine.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0017_mobile"
down_revision: str | Sequence[str] | None = "0016_ai"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ---------------- Device tokens ----------------
    op.create_table(
        "device_tokens",
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
        ),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("token", sa.String(512), nullable=False),
        sa.Column("app_version", sa.String(32), nullable=True),
        sa.Column("locale", sa.String(16), nullable=True),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
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
        sa.UniqueConstraint("user_id", "token", name="uq_device_tokens_user_token"),
        sa.CheckConstraint(
            "kind IN ('fcm','apns','web')", name="ck_device_tokens_kind_enum",
        ),
    )
    op.create_index(
        "ix_device_tokens_user_active", "device_tokens", ["user_id", "is_active"],
    )
    op.create_index("ix_device_tokens_token", "device_tokens", ["token"])

    # ---------------- Customer addresses ----------------
    op.create_table(
        "customer_addresses",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "customer_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("label", sa.String(32), nullable=False),
        sa.Column("recipient_name", sa.String(160), nullable=False),
        sa.Column("phone", sa.String(32), nullable=False),
        sa.Column("line1", sa.String(255), nullable=False),
        sa.Column("line2", sa.String(255), nullable=True),
        sa.Column("city", sa.String(96), nullable=False),
        sa.Column("district", sa.String(96), nullable=True),
        sa.Column("division", sa.String(96), nullable=True),
        sa.Column("postal_code", sa.String(16), nullable=True),
        sa.Column(
            "country", sa.String(2), nullable=False, server_default="BD",
        ),
        sa.Column(
            "is_default", sa.Boolean(), nullable=False, server_default=sa.text("false"),
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
    )
    op.create_index(
        "ix_customer_addresses_user", "customer_addresses", ["customer_user_id"],
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_customer_addresses_one_default
        ON customer_addresses (customer_user_id)
        WHERE is_default = true
        """,
    )

    # ---------------- Reminder mark-taken / snooze columns ----------------
    op.add_column(
        "medication_reminders",
        sa.Column("taken_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "medication_reminders",
        sa.Column("snoozed_until", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("medication_reminders", "snoozed_until")
    op.drop_column("medication_reminders", "taken_at")

    op.execute("DROP INDEX IF EXISTS uq_customer_addresses_one_default")
    op.drop_index("ix_customer_addresses_user", table_name="customer_addresses")
    op.drop_table("customer_addresses")

    op.drop_index("ix_device_tokens_token", table_name="device_tokens")
    op.drop_index("ix_device_tokens_user_active", table_name="device_tokens")
    op.drop_table("device_tokens")
