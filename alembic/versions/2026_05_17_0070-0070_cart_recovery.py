"""0070 cart_recovery module — abandoned cart recovery + win-back.

Two tables:
- hypershop_cart_recovery_dispatches    — one row per (cart, milestone) send
- hypershop_cart_recovery_suppressions  — opt-out / bounce / cap registry

Revision ID: 0070_cart_recovery
Revises:    0069_couriers_module
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0070_cart_recovery"
down_revision = "0069_couriers_module"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "hypershop_cart_recovery_dispatches",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("cart_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("customer_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("milestone", sa.String(24), nullable=False),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("template_code", sa.String(64), nullable=False),
        sa.Column("locale", sa.String(5), nullable=False, server_default=sa.text("'bn'")),
        sa.Column("recipient", sa.String(256), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("suppression_reason", sa.String(64), nullable=True),
        sa.Column("failure_reason", sa.String(256), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("recovered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("recovered_order_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.ForeignKeyConstraint(["cart_id"], ["carts.id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            "milestone IN ('cart_1h','cart_6h','cart_24h','winback_7d','winback_30d')",
            name="ck_cart_recovery_dispatch_milestone",
        ),
        sa.CheckConstraint(
            "channel IN ('whatsapp','email','push','sms')",
            name="ck_cart_recovery_dispatch_channel",
        ),
        sa.CheckConstraint(
            "status IN ('queued','sent','suppressed','failed','log_only')",
            name="ck_cart_recovery_dispatch_status",
        ),
        sa.UniqueConstraint(
            "cart_id", "milestone",
            name="uq_cart_recovery_dispatch_cart_milestone",
        ),
    )
    op.create_index(
        "ix_cart_recovery_dispatch_customer_at",
        "hypershop_cart_recovery_dispatches",
        ["customer_user_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "ix_cart_recovery_dispatch_status_at",
        "hypershop_cart_recovery_dispatches",
        ["status", "created_at"],
    )

    op.create_table(
        "hypershop_cart_recovery_suppressions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("customer_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("reason", sa.String(64), nullable=False),
        sa.Column("key_email", sa.String(256), nullable=True),
        sa.Column("key_phone", sa.String(32), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.CheckConstraint(
            "channel IN ('whatsapp','email','push','sms','all')",
            name="ck_cart_recovery_supp_channel",
        ),
        sa.CheckConstraint(
            "reason IN ('opted_out','bounced','complained','frequency_cap',"
            "'recent_purchase','manual')",
            name="ck_cart_recovery_supp_reason",
        ),
    )
    op.create_index(
        "uq_cart_recovery_supp_user_channel",
        "hypershop_cart_recovery_suppressions",
        ["customer_user_id", "channel"],
        unique=True,
        postgresql_where=sa.text("customer_user_id IS NOT NULL"),
    )
    op.create_index(
        "ix_cart_recovery_supp_email_channel",
        "hypershop_cart_recovery_suppressions",
        ["key_email", "channel"],
        postgresql_where=sa.text("key_email IS NOT NULL"),
    )
    op.create_index(
        "ix_cart_recovery_supp_phone_channel",
        "hypershop_cart_recovery_suppressions",
        ["key_phone", "channel"],
        postgresql_where=sa.text("key_phone IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_cart_recovery_supp_phone_channel",
        table_name="hypershop_cart_recovery_suppressions",
    )
    op.drop_index(
        "ix_cart_recovery_supp_email_channel",
        table_name="hypershop_cart_recovery_suppressions",
    )
    op.drop_index(
        "uq_cart_recovery_supp_user_channel",
        table_name="hypershop_cart_recovery_suppressions",
    )
    op.drop_table("hypershop_cart_recovery_suppressions")
    op.drop_index(
        "ix_cart_recovery_dispatch_status_at",
        table_name="hypershop_cart_recovery_dispatches",
    )
    op.drop_index(
        "ix_cart_recovery_dispatch_customer_at",
        table_name="hypershop_cart_recovery_dispatches",
    )
    op.drop_table("hypershop_cart_recovery_dispatches")
