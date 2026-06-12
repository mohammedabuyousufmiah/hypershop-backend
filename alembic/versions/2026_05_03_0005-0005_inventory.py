"""Inventory: suppliers, warehouses, POs, goods receipts, batches, ledger, balances

Revision ID: 0005_inventory
Revises: 0004_catalog_medicine
Create Date: 2026-05-03

The ``stock_ledger`` table is locked down with REVOKE UPDATE, DELETE so a
service bug cannot quietly mutate history. Reversals must be written as new
rows. Mirrors the ``audit_log`` defence pattern from migration 0001.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_inventory"
down_revision: str | Sequence[str] | None = "0004_catalog_medicine"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ---------- Suppliers ----------
    op.create_table(
        "suppliers",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("contact_email", sa.String(255), nullable=True),
        sa.Column("contact_phone", sa.String(32), nullable=True),
        sa.Column("address", sa.String(1024), nullable=True),
        sa.Column("tax_id", sa.String(64), nullable=True),
        sa.Column(
            "linked_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
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
        sa.UniqueConstraint("code", name="uq_suppliers_code"),
        sa.UniqueConstraint("name", name="uq_suppliers_name"),
    )
    op.create_index("ix_suppliers_is_active", "suppliers", ["is_active"])

    # ---------- Warehouses ----------
    op.create_table(
        "warehouses",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
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
        sa.UniqueConstraint("code", name="uq_warehouses_code"),
        sa.UniqueConstraint("name", name="uq_warehouses_name"),
    )
    # Seed default warehouse so single-location setups work out of the box.
    op.execute(
        """
        INSERT INTO warehouses (code, name, is_active)
        VALUES ('MAIN', 'Main Warehouse', true)
        ON CONFLICT (code) DO NOTHING
        """,
    )

    # ---------- Purchase orders ----------
    op.create_table(
        "purchase_orders",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column(
            "supplier_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("suppliers.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("expected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.String(2048), nullable=True),
        sa.Column(
            "created_by",
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
        sa.UniqueConstraint("code", name="uq_purchase_orders_code"),
        sa.CheckConstraint(
            "status IN ('draft','submitted','partial','received','cancelled')",
            name="ck_purchase_orders_status_enum",
        ),
        sa.CheckConstraint(
            "currency = upper(currency) AND length(currency) = 3",
            name="ck_purchase_orders_currency_iso",
        ),
    )
    op.create_index("ix_purchase_orders_supplier_id", "purchase_orders", ["supplier_id"])
    op.create_index("ix_purchase_orders_status", "purchase_orders", ["status"])

    op.create_table(
        "purchase_order_lines",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "purchase_order_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("purchase_orders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "variant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("product_variants.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("quantity_ordered", sa.Integer(), nullable=False),
        sa.Column("unit_cost", sa.Numeric(14, 2), nullable=False),
        sa.Column(
            "quantity_received", sa.Integer(), nullable=False, server_default=sa.text("0"),
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
        sa.CheckConstraint("quantity_ordered > 0", name="ck_pol_quantity_positive"),
        sa.CheckConstraint(
            "quantity_received >= 0 AND quantity_received <= quantity_ordered",
            name="ck_pol_quantity_received_bounds",
        ),
        sa.CheckConstraint("unit_cost >= 0", name="ck_pol_unit_cost_nonneg"),
    )
    op.create_index(
        "ix_purchase_order_lines_po", "purchase_order_lines", ["purchase_order_id"],
    )

    # ---------- Batches ----------
    op.create_table(
        "batches",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "variant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("product_variants.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("batch_number", sa.String(64), nullable=False),
        sa.Column(
            "supplier_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("suppliers.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("manufacturer", sa.String(160), nullable=True),
        sa.Column("manufacture_date", sa.Date(), nullable=True),
        sa.Column("expiry_date", sa.Date(), nullable=False),
        sa.Column("mrp", sa.Numeric(14, 2), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("notes", sa.String(1024), nullable=True),
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
            "variant_id", "batch_number", name="uq_batches_variant_number",
        ),
        sa.CheckConstraint(
            "manufacture_date IS NULL OR expiry_date >= manufacture_date",
            name="ck_batches_expiry_after_manufacture",
        ),
        sa.CheckConstraint(
            "status IN ('active','expired','blocked')",
            name="ck_batches_status_enum",
        ),
    )
    op.create_index("ix_batches_variant_expiry", "batches", ["variant_id", "expiry_date"])
    op.create_index("ix_batches_expiry", "batches", ["expiry_date"])
    op.create_index("ix_batches_status", "batches", ["status"])

    # ---------- Goods receipts ----------
    op.create_table(
        "goods_receipts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column(
            "supplier_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("suppliers.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "purchase_order_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("purchase_orders.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("supplier_invoice_number", sa.String(64), nullable=False),
        sa.Column("supplier_invoice_date", sa.Date(), nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.Column(
            "received_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "warehouse_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("warehouses.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("notes", sa.String(2048), nullable=True),
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
        sa.UniqueConstraint("code", name="uq_goods_receipts_code"),
        sa.UniqueConstraint(
            "supplier_id",
            "supplier_invoice_number",
            name="uq_goods_receipts_supplier_invoice",
        ),
        sa.CheckConstraint(
            "currency = upper(currency) AND length(currency) = 3",
            name="ck_goods_receipts_currency_iso",
        ),
    )
    op.create_index("ix_goods_receipts_supplier_id", "goods_receipts", ["supplier_id"])
    op.create_index("ix_goods_receipts_received_at", "goods_receipts", ["received_at"])

    op.create_table(
        "goods_receipt_lines",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "goods_receipt_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("goods_receipts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "variant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("product_variants.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "batch_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("batches.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit_cost", sa.Numeric(14, 2), nullable=False),
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
        sa.CheckConstraint("quantity > 0", name="ck_grl_quantity_positive"),
        sa.CheckConstraint("unit_cost >= 0", name="ck_grl_unit_cost_nonneg"),
    )
    op.create_index("ix_goods_receipt_lines_gr", "goods_receipt_lines", ["goods_receipt_id"])
    op.create_index("ix_goods_receipt_lines_variant", "goods_receipt_lines", ["variant_id"])
    op.create_index("ix_goods_receipt_lines_batch", "goods_receipt_lines", ["batch_id"])

    # ---------- Stock ledger ----------
    op.create_table(
        "stock_ledger",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.Column(
            "variant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("product_variants.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "batch_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("batches.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "warehouse_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("warehouses.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("bucket", sa.String(16), nullable=False),
        sa.Column("quantity_delta", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("reference_type", sa.String(48), nullable=True),
        sa.Column("reference_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("correlation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "actor_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("notes", sa.String(512), nullable=True),
        sa.Column(
            "extra",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.CheckConstraint("quantity_delta <> 0", name="ck_stock_ledger_delta_nonzero"),
        sa.CheckConstraint(
            "bucket IN ('available','reserved','damaged','expired','blocked')",
            name="ck_stock_ledger_bucket_enum",
        ),
        sa.CheckConstraint(
            "kind IN ('receipt','reserve','release','consume','damage','expire',"
            "'block','unblock','adjust_in','adjust_out','return')",
            name="ck_stock_ledger_kind_enum",
        ),
    )
    op.create_index(
        "ix_stock_ledger_variant_bucket_at",
        "stock_ledger",
        ["variant_id", "bucket", "occurred_at"],
    )
    op.create_index("ix_stock_ledger_batch_bucket", "stock_ledger", ["batch_id", "bucket"])
    op.create_index("ix_stock_ledger_correlation", "stock_ledger", ["correlation_id"])
    op.create_index(
        "ix_stock_ledger_reference", "stock_ledger", ["reference_type", "reference_id"],
    )

    # Lock the ledger: no updates, no deletes. Reversals are inverse INSERTs.
    op.execute(
        """
        DO $$
        BEGIN
          REVOKE UPDATE, DELETE ON TABLE stock_ledger FROM PUBLIC;
        EXCEPTION WHEN insufficient_privilege THEN
          NULL;
        END$$;
        """,
    )

    # ---------- Stock balances cache ----------
    op.create_table(
        "stock_balances",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "variant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("product_variants.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "batch_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("batches.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "warehouse_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("warehouses.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("bucket", sa.String(16), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default=sa.text("0")),
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
            "variant_id",
            "batch_id",
            "warehouse_id",
            "bucket",
            name="uq_stock_balances_grain",
        ),
        sa.CheckConstraint("quantity >= 0", name="ck_stock_balances_quantity_nonneg"),
        sa.CheckConstraint(
            "bucket IN ('available','reserved','damaged','expired','blocked')",
            name="ck_stock_balances_bucket_enum",
        ),
    )
    op.create_index(
        "ix_stock_balances_variant_bucket", "stock_balances", ["variant_id", "bucket"],
    )
    op.create_index("ix_stock_balances_batch", "stock_balances", ["batch_id"])


def downgrade() -> None:
    op.drop_index("ix_stock_balances_batch", table_name="stock_balances")
    op.drop_index("ix_stock_balances_variant_bucket", table_name="stock_balances")
    op.drop_table("stock_balances")

    op.drop_index("ix_stock_ledger_reference", table_name="stock_ledger")
    op.drop_index("ix_stock_ledger_correlation", table_name="stock_ledger")
    op.drop_index("ix_stock_ledger_batch_bucket", table_name="stock_ledger")
    op.drop_index("ix_stock_ledger_variant_bucket_at", table_name="stock_ledger")
    op.drop_table("stock_ledger")

    op.drop_index("ix_goods_receipt_lines_batch", table_name="goods_receipt_lines")
    op.drop_index("ix_goods_receipt_lines_variant", table_name="goods_receipt_lines")
    op.drop_index("ix_goods_receipt_lines_gr", table_name="goods_receipt_lines")
    op.drop_table("goods_receipt_lines")

    op.drop_index("ix_goods_receipts_received_at", table_name="goods_receipts")
    op.drop_index("ix_goods_receipts_supplier_id", table_name="goods_receipts")
    op.drop_table("goods_receipts")

    op.drop_index("ix_batches_status", table_name="batches")
    op.drop_index("ix_batches_expiry", table_name="batches")
    op.drop_index("ix_batches_variant_expiry", table_name="batches")
    op.drop_table("batches")

    op.drop_index("ix_purchase_order_lines_po", table_name="purchase_order_lines")
    op.drop_table("purchase_order_lines")

    op.drop_index("ix_purchase_orders_status", table_name="purchase_orders")
    op.drop_index("ix_purchase_orders_supplier_id", table_name="purchase_orders")
    op.drop_table("purchase_orders")

    op.drop_table("warehouses")

    op.drop_index("ix_suppliers_is_active", table_name="suppliers")
    op.drop_table("suppliers")
