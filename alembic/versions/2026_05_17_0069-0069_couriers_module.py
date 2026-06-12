"""0069 couriers module — external courier integrations (Pathao, RedX,
Sundarban, Steadfast). Five tables:

- hypershop_courier_providers      — registry (code, capabilities, zones)
- hypershop_courier_credentials    — per-provider OAuth / API creds
- hypershop_courier_shipments      — outbound shipments, AWB, status
- hypershop_courier_status_events  — webhook event log
- hypershop_courier_cod_remittances — COD reconciliation per shipment

Money is BIGINT minor (paisa). Provider codes are lowercase string keys
matching the python `CourierProvider.code` attribute.

Revision ID: 0069_couriers_module
Revises:    0068_ad_wallet_recharges
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0069_couriers_module"
down_revision = "0068_ad_wallet_recharges"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ─── 1. providers registry ──────────────────────────────────────
    op.create_table(
        "hypershop_courier_providers",
        sa.Column("code", sa.String(32), primary_key=True),
        sa.Column("display_name", sa.String(120), nullable=False),
        sa.Column("country", sa.String(2), nullable=False, server_default=sa.text("'BD'")),
        sa.Column("is_enabled", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("supports_cod", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("supports_pickup", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("supports_return", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("coverage_zones", postgresql.JSONB, nullable=False,
                  server_default=sa.text("'[]'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("(now() AT TIME ZONE 'UTC')")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("(now() AT TIME ZONE 'UTC')")),
    )

    # ─── 2. credentials ─────────────────────────────────────────────
    op.create_table(
        "hypershop_courier_credentials",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("provider_code", sa.String(32), nullable=False),
        sa.Column("environment", sa.String(16), nullable=False,
                  server_default=sa.text("'sandbox'")),
        sa.Column("base_url", sa.String(256), nullable=False),
        sa.Column("api_key", sa.Text, nullable=True),
        sa.Column("api_secret", sa.Text, nullable=True),
        sa.Column("client_id", sa.String(128), nullable=True),
        sa.Column("merchant_id", sa.String(128), nullable=True),
        sa.Column("extra_config", postgresql.JSONB, nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("last_validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("(now() AT TIME ZONE 'UTC')")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("(now() AT TIME ZONE 'UTC')")),
        sa.ForeignKeyConstraint(["provider_code"], ["hypershop_courier_providers.code"],
                                ondelete="CASCADE"),
        sa.CheckConstraint(
            "environment IN ('sandbox','production')",
            name="ck_hypershop_courier_creds_env",
        ),
    )
    op.create_index(
        "ix_hypershop_courier_creds_provider_active",
        "hypershop_courier_credentials",
        ["provider_code", "is_active"],
    )

    # ─── 3. shipments ───────────────────────────────────────────────
    op.create_table(
        "hypershop_courier_shipments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider_code", sa.String(32), nullable=False),
        sa.Column("provider_shipment_id", sa.String(128), nullable=True),
        sa.Column("tracking_number", sa.String(128), nullable=True),
        sa.Column("status", sa.String(32), nullable=False,
                  server_default=sa.text("'created'")),
        sa.Column("service_type", sa.String(32), nullable=False,
                  server_default=sa.text("'regular'")),
        sa.Column("is_cod", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("cod_amount_minor", sa.BigInteger, nullable=False, server_default=sa.text("0")),
        sa.Column("shipping_charge_minor", sa.BigInteger, nullable=False,
                  server_default=sa.text("0")),
        sa.Column("label_url", sa.Text, nullable=True),
        sa.Column("pickup_address", postgresql.JSONB, nullable=True),
        sa.Column("delivery_address", postgresql.JSONB, nullable=True),
        sa.Column("provider_response", postgresql.JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("(now() AT TIME ZONE 'UTC')")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("(now() AT TIME ZONE 'UTC')")),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["provider_code"], ["hypershop_courier_providers.code"]),
        sa.CheckConstraint(
            "status IN ('created','pickup_pending','in_transit','out_for_delivery',"
            "'delivered','returned','cancelled','failed','exception')",
            name="ck_hypershop_courier_shipment_status",
        ),
        sa.CheckConstraint(
            "service_type IN ('regular','express','same_day','next_day')",
            name="ck_hypershop_courier_shipment_service",
        ),
        sa.CheckConstraint(
            "cod_amount_minor >= 0",
            name="ck_hypershop_courier_shipment_cod_nonneg",
        ),
    )
    op.create_index(
        "ix_hypershop_courier_shipments_order",
        "hypershop_courier_shipments",
        ["order_id"],
    )
    op.create_index(
        "ix_hypershop_courier_shipments_provider_id",
        "hypershop_courier_shipments",
        ["provider_code", "provider_shipment_id"],
        unique=True,
        postgresql_where=sa.text("provider_shipment_id IS NOT NULL"),
    )
    op.create_index(
        "ix_hypershop_courier_shipments_status",
        "hypershop_courier_shipments",
        ["status", "created_at"],
    )

    # ─── 4. status events ───────────────────────────────────────────
    op.create_table(
        "hypershop_courier_status_events",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("shipment_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("provider_code", sa.String(32), nullable=False),
        sa.Column("provider_shipment_id", sa.String(128), nullable=True),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("mapped_status", sa.String(32), nullable=True),
        sa.Column("raw_payload", postgresql.JSONB, nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("(now() AT TIME ZONE 'UTC')")),
        sa.ForeignKeyConstraint(["shipment_id"], ["hypershop_courier_shipments.id"],
                                ondelete="SET NULL"),
    )
    op.create_index(
        "ix_hypershop_courier_status_shipment",
        "hypershop_courier_status_events",
        ["shipment_id", sa.text("received_at DESC")],
    )

    # ─── 5. COD remittances ─────────────────────────────────────────
    op.create_table(
        "hypershop_courier_cod_remittances",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("provider_code", sa.String(32), nullable=False),
        sa.Column("shipment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("cod_amount_minor", sa.BigInteger, nullable=False),
        sa.Column("courier_fee_minor", sa.BigInteger, nullable=False, server_default=sa.text("0")),
        sa.Column("settled_amount_minor", sa.BigInteger, nullable=False),
        sa.Column("settlement_reference", sa.String(128), nullable=True),
        sa.Column("status", sa.String(16), nullable=False,
                  server_default=sa.text("'pending'")),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("(now() AT TIME ZONE 'UTC')")),
        sa.ForeignKeyConstraint(["shipment_id"], ["hypershop_courier_shipments.id"]),
        sa.CheckConstraint(
            "status IN ('pending','settled','disputed','reconciled')",
            name="ck_hypershop_courier_cod_remit_status",
        ),
    )
    op.create_index(
        "ix_hypershop_courier_cod_remit_provider",
        "hypershop_courier_cod_remittances",
        ["provider_code", "status"],
    )

    # ─── Seed the 4 known BD couriers (disabled by default — operator
    #     enables once credentials arrive) ──────────────────────────
    op.execute("""
        INSERT INTO hypershop_courier_providers
          (code, display_name, country, is_enabled, supports_cod,
           supports_pickup, supports_return, coverage_zones)
        VALUES
          ('pathao', 'Pathao Courier', 'BD', false, true, true, true,
           '["DHK","CTG","SYL","KHL","RAJ","RNG","BAR","MYM"]'::jsonb),
          ('redx', 'RedX', 'BD', false, true, true, true,
           '["DHK","CTG","SYL","KHL","RAJ","RNG","BAR","MYM"]'::jsonb),
          ('sundarban', 'Sundarban Courier', 'BD', false, true, false, true,
           '["DHK","CTG","SYL","KHL","RAJ","RNG","BAR","MYM"]'::jsonb),
          ('steadfast', 'Steadfast Courier', 'BD', false, true, true, true,
           '["DHK","CTG","SYL","KHL","RAJ","RNG","BAR","MYM"]'::jsonb)
        ON CONFLICT (code) DO NOTHING;
    """)


def downgrade() -> None:
    op.drop_index("ix_hypershop_courier_cod_remit_provider",
                  table_name="hypershop_courier_cod_remittances")
    op.drop_table("hypershop_courier_cod_remittances")
    op.drop_index("ix_hypershop_courier_status_shipment",
                  table_name="hypershop_courier_status_events")
    op.drop_table("hypershop_courier_status_events")
    op.drop_index("ix_hypershop_courier_shipments_status",
                  table_name="hypershop_courier_shipments")
    op.drop_index("ix_hypershop_courier_shipments_provider_id",
                  table_name="hypershop_courier_shipments")
    op.drop_index("ix_hypershop_courier_shipments_order",
                  table_name="hypershop_courier_shipments")
    op.drop_table("hypershop_courier_shipments")
    op.drop_index("ix_hypershop_courier_creds_provider_active",
                  table_name="hypershop_courier_credentials")
    op.drop_table("hypershop_courier_credentials")
    op.drop_table("hypershop_courier_providers")
