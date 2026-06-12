"""0053 marketing — Module 48: Marketing automation.

Three tables:
- marketing_audiences      — named customer segments defined by JSON rule sets
- marketing_campaigns      — a target audience + message template + channel + schedule
- marketing_campaign_sends — one row per (campaign × customer) delivery attempt

Audience rule schema (stored as JSONB):
{
  "min_spend": 5000,                // BDT, completed orders only
  "min_orders": 1,                   // completed orders count
  "max_orders": null,
  "last_order_within_days": 30,
  "no_order_within_days": null,      // for win-back: 'haven't ordered in N days'
  "loyalty_tier_in": ["GOLD","PLATINUM"],
  "consent_required": true            // skip customers who opted out
}
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0053_marketing"
down_revision: str | Sequence[str] | None = "0052_seller_applications"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "marketing_audiences",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(120), nullable=False, unique=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("rules", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("estimated_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("counted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("created_by", UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "marketing_campaigns",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("audience_id", UUID(as_uuid=True),
                  sa.ForeignKey("marketing_audiences.id", ondelete="RESTRICT"),
                  nullable=False, index=True),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("template_subject", sa.String(255), nullable=True),
        sa.Column("template_body", sa.Text, nullable=False),
        # Optional Meta template name — when set, WhatsApp campaigns
        # send the approved template instead of free-form text
        sa.Column("whatsapp_template_name", sa.String(80), nullable=True),
        sa.Column("status", sa.String(24), nullable=False, server_default="draft"),
        sa.Column("schedule_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("delivered_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("failed_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_by", UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "channel IN ('whatsapp','email','sms','in_app')",
            name="ck_marketing_campaigns_channel",
        ),
        sa.CheckConstraint(
            "status IN ('draft','scheduled','sending','sent','cancelled','failed')",
            name="ck_marketing_campaigns_status",
        ),
    )
    op.create_index(
        "ix_marketing_campaigns_scheduled",
        "marketing_campaigns", ["status", "schedule_at"],
        postgresql_where=sa.text("status = 'scheduled'"),
    )

    op.create_table(
        "marketing_campaign_sends",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("campaign_id", UUID(as_uuid=True),
                  sa.ForeignKey("marketing_campaigns.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("customer_user_id", UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("provider_message_id", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "status IN ('queued','sent','failed','skipped_consent','skipped_no_contact')",
            name="ck_marketing_sends_status",
        ),
        # One send per (campaign, customer) — prevents accidental double-fires
        sa.UniqueConstraint(
            "campaign_id", "customer_user_id",
            name="uq_marketing_send_campaign_customer",
        ),
    )


def downgrade() -> None:
    op.drop_table("marketing_campaign_sends")
    op.drop_index("ix_marketing_campaigns_scheduled", table_name="marketing_campaigns")
    op.drop_table("marketing_campaigns")
    op.drop_table("marketing_audiences")
