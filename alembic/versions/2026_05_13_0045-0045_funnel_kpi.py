"""0045 funnel_kpi — customer behavior tracking + retargeting + KPI dashboard.

Four tables, all isolated from existing schema (no FK to users/products
on purpose — funnel uses opaque ``external_customer_id`` strings so the
table can be wiped/replaced without cascading damage):

* ``funnel_customers`` — one row per tracked external identity, holding
  the rolling score + segment + consent flags.
* ``funnel_events`` — append-only event log with idempotency_key
  uniqueness. Indexed for the dashboard's distinct-customer-by-event
  rollups + by product/category for the per-asset KPIs.
* ``funnel_followup_tasks`` — WhatsApp / customer-care queue, gated by
  marketing+whatsapp consent at insert time.
* ``funnel_retargeting_export_logs`` — every Meta/Google/TikTok/WhatsApp
  audience export with the consent-filter count for the privacy
  dashboard.

Source: ``hypershop_funnel_engine_merged_ready/alembic/versions/
20260513_001_merged_funnel_kpi.py`` adapted to project conventions
(timestamptz, naming convention via the project's metadata).
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0045_funnel_kpi"
down_revision: str | Sequence[str] | None = "0044_user_pins"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "funnel_customers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("external_customer_id", sa.String(length=128), nullable=False),
        sa.Column("hypershop_customer_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(length=128), nullable=True),
        sa.Column("phone", sa.String(length=32), nullable=True),
        sa.Column("email", sa.String(length=128), nullable=True),
        sa.Column("marketing_consent", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("whatsapp_consent", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("sms_consent", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("ad_retargeting_consent", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("current_score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("segment", sa.String(length=64), nullable=False, server_default="Cold Visitor"),
        sa.Column("last_event_name", sa.String(length=64), nullable=True),
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_funnel_customers_external_customer_id",
        "funnel_customers", ["external_customer_id"], unique=True,
    )
    op.create_index(
        "ix_funnel_customers_hypershop_customer_id",
        "funnel_customers", ["hypershop_customer_id"],
    )
    op.create_index("ix_funnel_customers_phone", "funnel_customers", ["phone"])
    op.create_index("ix_funnel_customers_email", "funnel_customers", ["email"])
    op.create_index(
        "ix_funnel_customers_segment_score",
        "funnel_customers", ["segment", "current_score"],
    )

    op.create_table(
        "funnel_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "customer_id", sa.Integer(),
            sa.ForeignKey("funnel_customers.id", name="fk_funnel_events_customer_id_funnel_customers"),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(length=160), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("event_name", sa.String(length=64), nullable=False),
        sa.Column("product_id", sa.String(length=64), nullable=True),
        sa.Column("category_id", sa.String(length=64), nullable=True),
        sa.Column("campaign_id", sa.String(length=64), nullable=True),
        sa.Column("session_id", sa.String(length=128), nullable=True),
        sa.Column("value", sa.Float(), nullable=False, server_default="0"),
        sa.Column("score_delta", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("metadata_json", sa.Text(), nullable=True),
        sa.Column("user_agent_hash", sa.String(length=128), nullable=True),
        sa.Column("ip_hash", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_funnel_events_idempotency_key",
        "funnel_events", ["idempotency_key"], unique=True,
    )
    op.create_index("ix_funnel_events_customer_id", "funnel_events", ["customer_id"])
    op.create_index("ix_funnel_events_source", "funnel_events", ["source"])
    op.create_index("ix_funnel_events_event_name", "funnel_events", ["event_name"])
    op.create_index("ix_funnel_events_product_id", "funnel_events", ["product_id"])
    op.create_index("ix_funnel_events_category_id", "funnel_events", ["category_id"])
    op.create_index("ix_funnel_events_campaign_id", "funnel_events", ["campaign_id"])
    op.create_index("ix_funnel_events_session_id", "funnel_events", ["session_id"])
    op.create_index("ix_funnel_events_created_at", "funnel_events", ["created_at"])
    op.create_index(
        "ix_funnel_events_customer_event_time",
        "funnel_events", ["customer_id", "event_name", "created_at"],
    )
    op.create_index(
        "ix_funnel_events_event_created",
        "funnel_events", ["event_name", "created_at"],
    )
    op.create_index(
        "ix_funnel_events_source_created",
        "funnel_events", ["source", "created_at"],
    )
    op.create_index(
        "ix_funnel_events_product_created",
        "funnel_events", ["product_id", "created_at"],
    )
    op.create_index(
        "ix_funnel_events_category_created",
        "funnel_events", ["category_id", "created_at"],
    )

    op.create_table(
        "funnel_followup_tasks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "customer_id", sa.Integer(),
            sa.ForeignKey("funnel_customers.id", name="fk_funnel_followup_tasks_customer_id_funnel_customers"),
            nullable=False,
        ),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.String(length=128), nullable=False),
        sa.Column("message_template_key", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("blocked_reason", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_funnel_followup_tasks_customer_id",
        "funnel_followup_tasks", ["customer_id"],
    )
    op.create_index(
        "ix_funnel_followups_status_created",
        "funnel_followup_tasks", ["status", "created_at"],
    )

    op.create_table(
        "funnel_retargeting_export_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("platform", sa.String(length=32), nullable=False),
        sa.Column("segment", sa.String(length=64), nullable=False),
        sa.Column("exported_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("consent_filtered_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_funnel_retargeting_export_logs_platform",
        "funnel_retargeting_export_logs", ["platform"],
    )
    op.create_index(
        "ix_funnel_retargeting_export_logs_segment",
        "funnel_retargeting_export_logs", ["segment"],
    )


def downgrade() -> None:
    op.drop_index("ix_funnel_retargeting_export_logs_segment", table_name="funnel_retargeting_export_logs")
    op.drop_index("ix_funnel_retargeting_export_logs_platform", table_name="funnel_retargeting_export_logs")
    op.drop_table("funnel_retargeting_export_logs")

    op.drop_index("ix_funnel_followups_status_created", table_name="funnel_followup_tasks")
    op.drop_index("ix_funnel_followup_tasks_customer_id", table_name="funnel_followup_tasks")
    op.drop_table("funnel_followup_tasks")

    for ix in [
        "ix_funnel_events_category_created",
        "ix_funnel_events_product_created",
        "ix_funnel_events_source_created",
        "ix_funnel_events_event_created",
        "ix_funnel_events_customer_event_time",
        "ix_funnel_events_created_at",
        "ix_funnel_events_session_id",
        "ix_funnel_events_campaign_id",
        "ix_funnel_events_category_id",
        "ix_funnel_events_product_id",
        "ix_funnel_events_event_name",
        "ix_funnel_events_source",
        "ix_funnel_events_customer_id",
        "ix_funnel_events_idempotency_key",
    ]:
        op.drop_index(ix, table_name="funnel_events")
    op.drop_table("funnel_events")

    for ix in [
        "ix_funnel_customers_segment_score",
        "ix_funnel_customers_email",
        "ix_funnel_customers_phone",
        "ix_funnel_customers_hypershop_customer_id",
        "ix_funnel_customers_external_customer_id",
    ]:
        op.drop_index(ix, table_name="funnel_customers")
    op.drop_table("funnel_customers")
