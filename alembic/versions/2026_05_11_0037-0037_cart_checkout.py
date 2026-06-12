"""cart + checkout — Phase B-1 customer-facing modules

Revision ID: 0037_cart_checkout
Revises: 0036_product_qa
Create Date: 2026-05-11

Adds three tables:
  - carts                — one open cart per user (or per guest session_token)
  - cart_items           — line items, price-snapshot for drift detection
  - checkout_sessions    — DRAFT → CONFIRMED → orders.id link

All FKs against existing entities (users, products, product_variants,
sellers, orders). Partial unique indexes guarantee one open cart per
identity.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0037_cart_checkout"
down_revision: str | Sequence[str] | None = "0036_product_qa"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ---- carts ----
    op.create_table(
        "carts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("session_token", sa.String(64), nullable=True),
        sa.Column(
            "status", sa.String(16), nullable=False, server_default="open"
        ),
        sa.Column(
            "currency", sa.String(3), nullable=False, server_default="BDT"
        ),
        sa.Column("country_code", sa.String(2), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC'::text)"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC'::text)"),
            onupdate=sa.text("(now() AT TIME ZONE 'UTC'::text)"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE",
            name="fk_carts_user_id_users",
        ),
        sa.CheckConstraint(
            "status IN ('open','merged','abandoned','converted')",
            name="ck_carts_status_enum",
        ),
        sa.CheckConstraint(
            "(user_id IS NOT NULL) OR (session_token IS NOT NULL)",
            name="ck_carts_owner_present",
        ),
    )
    op.create_index("ix_carts_session_token", "carts", ["session_token"])
    op.create_index("ix_carts_status", "carts", ["status"])
    # Partial unique indexes — one open cart per identity.
    op.execute(
        "CREATE UNIQUE INDEX ux_carts_open_user ON carts (user_id) "
        "WHERE status = 'open' AND user_id IS NOT NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX ux_carts_open_session ON carts (session_token) "
        "WHERE status = 'open' AND session_token IS NOT NULL"
    )

    # ---- cart_items ----
    op.create_table(
        "cart_items",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("cart_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("variant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("seller_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "quantity", sa.Integer(), nullable=False, server_default=sa.text("1")
        ),
        sa.Column("price_snapshot", sa.Numeric(14, 2), nullable=False),
        sa.Column(
            "currency", sa.String(3), nullable=False, server_default="BDT"
        ),
        sa.Column(
            "added_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC'::text)"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC'::text)"),
            onupdate=sa.text("(now() AT TIME ZONE 'UTC'::text)"),
        ),
        sa.ForeignKeyConstraint(
            ["cart_id"], ["carts.id"], ondelete="CASCADE",
            name="fk_cart_items_cart_id_carts",
        ),
        sa.ForeignKeyConstraint(
            ["variant_id"], ["product_variants.id"], ondelete="RESTRICT",
            name="fk_cart_items_variant_id_product_variants",
        ),
        sa.ForeignKeyConstraint(
            ["product_id"], ["products.id"], ondelete="RESTRICT",
            name="fk_cart_items_product_id_products",
        ),
        sa.ForeignKeyConstraint(
            ["seller_id"], ["sellers.id"], ondelete="SET NULL",
            name="fk_cart_items_seller_id_sellers",
        ),
        sa.CheckConstraint("quantity > 0", name="ck_cart_items_quantity_positive"),
        sa.UniqueConstraint(
            "cart_id", "variant_id", name="uq_cart_items_cart_variant"
        ),
    )
    op.create_index("ix_cart_items_cart_id", "cart_items", ["cart_id"])

    # ---- checkout_sessions ----
    op.create_table(
        "checkout_sessions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("cart_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "status", sa.String(16), nullable=False, server_default="draft"
        ),
        sa.Column(
            "currency", sa.String(3), nullable=False, server_default="BDT"
        ),
        sa.Column("subtotal", sa.Numeric(14, 2), nullable=False),
        sa.Column(
            "shipping_total", sa.Numeric(14, 2), nullable=False, server_default="0"
        ),
        sa.Column(
            "tax_total", sa.Numeric(14, 2), nullable=False, server_default="0"
        ),
        sa.Column(
            "discount_total", sa.Numeric(14, 2), nullable=False, server_default="0"
        ),
        sa.Column(
            "loyalty_redeemed", sa.Numeric(14, 2), nullable=False, server_default="0"
        ),
        sa.Column("grand_total", sa.Numeric(14, 2), nullable=False),
        sa.Column(
            "shipping_address_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "billing_address_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "payment_method", sa.String(16), nullable=False, server_default="cod"
        ),
        sa.Column("note", sa.String(500), nullable=True),
        sa.Column(
            "snapshot_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("cancelled_reason", sa.String(255), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC'::text)"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC'::text)"),
            onupdate=sa.text("(now() AT TIME ZONE 'UTC'::text)"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE",
            name="fk_checkout_sessions_user_id_users",
        ),
        sa.ForeignKeyConstraint(
            ["cart_id"], ["carts.id"], ondelete="RESTRICT",
            name="fk_checkout_sessions_cart_id_carts",
        ),
        sa.ForeignKeyConstraint(
            ["order_id"], ["orders.id"], ondelete="SET NULL",
            name="fk_checkout_sessions_order_id_orders",
        ),
        sa.CheckConstraint(
            "status IN ('draft','confirmed','cancelled','expired')",
            name="ck_checkout_sessions_status_enum",
        ),
    )
    op.create_index(
        "ix_checkout_sessions_user", "checkout_sessions", ["user_id"]
    )
    op.create_index(
        "ix_checkout_sessions_cart", "checkout_sessions", ["cart_id"]
    )
    op.create_index(
        "ix_checkout_sessions_status", "checkout_sessions", ["status"]
    )


def downgrade() -> None:
    op.drop_index("ix_checkout_sessions_status", table_name="checkout_sessions")
    op.drop_index("ix_checkout_sessions_cart", table_name="checkout_sessions")
    op.drop_index("ix_checkout_sessions_user", table_name="checkout_sessions")
    op.drop_table("checkout_sessions")

    op.drop_index("ix_cart_items_cart_id", table_name="cart_items")
    op.drop_table("cart_items")

    op.execute("DROP INDEX IF EXISTS ux_carts_open_session")
    op.execute("DROP INDEX IF EXISTS ux_carts_open_user")
    op.drop_index("ix_carts_status", table_name="carts")
    op.drop_index("ix_carts_session_token", table_name="carts")
    op.drop_table("carts")
