"""Inventory Manager operational tables (Phase C of role-rule wiring).

Revision ID: 0091_inventory_operations
Revises: 0090_finance_operations
Create Date: 2026-05-26

Adds the 8 canonical inventory tables from the Inventory Manager Rules
package (2026-05-26). These sit ALONGSIDE the existing
``app.modules.inventory`` Phase-3 tables (stock_balances /
warehouses / etc.) — these new ones are stock-truth WORKFLOW queues
and audit trail, not the bucket-level stock ledger.

Tables:
  inventory_stocks                — per-SKU rollup snapshot
  stock_reservations              — order-cart reservations awaiting
                                    Inventory Manager verification
  stock_movements                 — append-only stock movement ledger
  stock_adjustment_requests       — adjustment decision queue
  return_stock_reviews            — return-to-stock QC + approval queue
  damaged_lost_inventory          — damaged / lost stock register
  seller_stock_accuracy           — seller stock-accuracy scorecard
  inventory_audit_logs            — every Inventory Manager action
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0091_inventory_operations"
down_revision = "0090_finance_operations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------ inventory_audit_logs ------
    op.create_table(
        "inventory_audit_logs",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("audit_code", sa.String(32), nullable=False, unique=True),
        sa.Column("actor_id", sa.String(80), nullable=False),
        sa.Column("actor_role", sa.String(48), nullable=False),
        sa.Column("action", sa.String(80), nullable=False),
        sa.Column("entity_type", sa.String(48), nullable=False),
        sa.Column("entity_id", sa.String(80), nullable=False),
        sa.Column("sku", sa.String(80), nullable=True),
        sa.Column("warehouse_id", sa.String(80), nullable=True),
        sa.Column("qty_before", sa.Integer, nullable=True),
        sa.Column("qty_after", sa.Integer, nullable=True),
        sa.Column("qty_delta", sa.Integer, nullable=True),
        sa.Column("old_status", sa.String(48), nullable=True),
        sa.Column("new_status", sa.String(48), nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("evidence_url", sa.String(512), nullable=True),
        sa.Column("reference_id", sa.String(80), nullable=True),
        sa.Column("ip_address", sa.String(64), nullable=True),
        sa.Column("device_info", sa.String(255), nullable=True),
        sa.Column(
            "metadata_json", postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
    )
    op.create_index("ix_inv_audit_entity", "inventory_audit_logs",
                    ["entity_type", "entity_id"])
    op.create_index("ix_inv_audit_sku", "inventory_audit_logs", ["sku"])
    op.create_index("ix_inv_audit_action", "inventory_audit_logs", ["action"])

    # ------ inventory_stocks (per-SKU rollup) ------
    op.create_table(
        "inventory_stocks",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("sku", sa.String(80), nullable=False),
        sa.Column("warehouse_id", sa.String(80), nullable=False),
        sa.Column("available_qty", sa.Integer, nullable=False, server_default="0"),
        sa.Column("reserved_qty", sa.Integer, nullable=False, server_default="0"),
        sa.Column("damaged_qty", sa.Integer, nullable=False, server_default="0"),
        sa.Column("lost_qty", sa.Integer, nullable=False, server_default="0"),
        sa.Column("quarantine_qty", sa.Integer, nullable=False, server_default="0"),
        sa.Column("low_stock_threshold", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "is_blocked", sa.Boolean, nullable=False,
            server_default=sa.text("false"),
            comment="Inventory Manager hard-block — out-of-stock from sale.",
        ),
        sa.Column("blocked_reason", sa.Text, nullable=True),
        sa.Column("last_movement_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.UniqueConstraint("sku", "warehouse_id", name="uq_inventory_stocks_sku_wh"),
        sa.CheckConstraint("available_qty >= 0", name="ck_inv_stocks_avail_nonneg"),
        sa.CheckConstraint("reserved_qty >= 0", name="ck_inv_stocks_reserved_nonneg"),
    )
    op.create_index("ix_inventory_stocks_sku", "inventory_stocks", ["sku"])
    op.create_index("ix_inventory_stocks_warehouse", "inventory_stocks",
                    ["warehouse_id"])
    op.create_index("ix_inventory_stocks_blocked", "inventory_stocks",
                    ["is_blocked"])

    # ------ stock_reservations ------
    op.create_table(
        "stock_reservations",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("reservation_code", sa.String(32), nullable=False, unique=True),
        sa.Column("order_id", sa.String(80), nullable=False),
        sa.Column("sku", sa.String(80), nullable=False),
        sa.Column("warehouse_id", sa.String(80), nullable=False),
        sa.Column("qty", sa.Integer, nullable=False),
        sa.Column(
            "status", sa.String(24), nullable=False,
            server_default="active",
            comment="active | released | consumed | expired",
        ),
        sa.Column("reserved_by", sa.String(80), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_audit_log_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.CheckConstraint(
            "status IN ('active','released','consumed','expired')",
            name="ck_stock_reservations_status",
        ),
        sa.CheckConstraint("qty > 0", name="ck_stock_reservations_qty_pos"),
    )
    op.create_index("ix_stock_reservations_order", "stock_reservations", ["order_id"])
    op.create_index("ix_stock_reservations_sku", "stock_reservations",
                    ["sku", "warehouse_id"])
    op.create_index("ix_stock_reservations_status", "stock_reservations", ["status"])

    # ------ stock_movements (append-only) ------
    op.create_table(
        "stock_movements",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("movement_code", sa.String(32), nullable=False, unique=True),
        sa.Column("sku", sa.String(80), nullable=False),
        sa.Column("warehouse_id", sa.String(80), nullable=False),
        sa.Column(
            "movement_type", sa.String(32), nullable=False,
            comment=("receive | issue | reserve | release | "
                     "adjust_in | adjust_out | damage | loss | "
                     "return_in | quarantine_in | quarantine_out"),
        ),
        sa.Column("qty_delta", sa.Integer, nullable=False),
        sa.Column("qty_after", sa.Integer, nullable=False),
        sa.Column("source_type", sa.String(48), nullable=True),
        sa.Column("source_ref", sa.String(80), nullable=True),
        sa.Column("actor_id", sa.String(80), nullable=False),
        sa.Column("actor_role", sa.String(48), nullable=False),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column("audit_log_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.CheckConstraint(
            "movement_type IN ('receive','issue','reserve','release',"
            "'adjust_in','adjust_out','damage','loss','return_in',"
            "'quarantine_in','quarantine_out')",
            name="ck_stock_movements_type",
        ),
    )
    op.create_index("ix_stock_movements_sku", "stock_movements",
                    ["sku", "warehouse_id"])
    op.create_index("ix_stock_movements_created", "stock_movements", ["created_at"])

    # ------ stock_adjustment_requests ------
    op.create_table(
        "stock_adjustment_requests",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("request_code", sa.String(32), nullable=False, unique=True),
        sa.Column("sku", sa.String(80), nullable=False),
        sa.Column("warehouse_id", sa.String(80), nullable=False),
        sa.Column(
            "direction", sa.String(8), nullable=False,
            comment="in | out (sign-of-qty_delta)",
        ),
        sa.Column("qty_delta", sa.Integer, nullable=False),
        sa.Column("qty_before", sa.Integer, nullable=False),
        sa.Column(
            "category", sa.String(48), nullable=False,
            comment="cycle_count | damage | loss | theft | spoilage | other",
        ),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("evidence_url", sa.String(512), nullable=True),
        sa.Column("requested_by", sa.String(80), nullable=False),
        sa.Column(
            "status", sa.String(24), nullable=False,
            server_default="pending",
            comment="pending | approved | rejected",
        ),
        sa.Column("decided_by", sa.String(80), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_audit_log_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("decision_note", sa.Text, nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.CheckConstraint(
            "direction IN ('in','out')",
            name="ck_stock_adj_direction",
        ),
        sa.CheckConstraint(
            "status IN ('pending','approved','rejected')",
            name="ck_stock_adj_status",
        ),
        sa.CheckConstraint("qty_delta != 0", name="ck_stock_adj_delta_nonzero"),
    )
    op.create_index("ix_stock_adj_sku", "stock_adjustment_requests",
                    ["sku", "warehouse_id"])
    op.create_index("ix_stock_adj_status", "stock_adjustment_requests", ["status"])

    # ------ return_stock_reviews ------
    op.create_table(
        "return_stock_reviews",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("review_code", sa.String(32), nullable=False, unique=True),
        sa.Column("order_id", sa.String(80), nullable=False),
        sa.Column("return_id", sa.String(80), nullable=False),
        sa.Column("sku", sa.String(80), nullable=False),
        sa.Column("warehouse_id", sa.String(80), nullable=False),
        sa.Column("qty", sa.Integer, nullable=False),
        sa.Column(
            "qc_status", sa.String(24), nullable=False,
            server_default="pending",
            comment="pending | qc_passed | qc_failed",
        ),
        sa.Column("qc_pass_evidence_url", sa.String(512), nullable=True),
        sa.Column("shelf_qr", sa.String(80), nullable=True),
        sa.Column("mother_qr_match", sa.Boolean, nullable=True),
        sa.Column(
            "status", sa.String(24), nullable=False,
            server_default="pending",
            comment="pending | approved | rejected",
        ),
        sa.Column("decided_by", sa.String(80), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_audit_log_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("decision_note", sa.Text, nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.CheckConstraint(
            "qc_status IN ('pending','qc_passed','qc_failed')",
            name="ck_return_review_qc",
        ),
        sa.CheckConstraint(
            "status IN ('pending','approved','rejected')",
            name="ck_return_review_status",
        ),
        sa.CheckConstraint("qty > 0", name="ck_return_review_qty_pos"),
    )
    op.create_index("ix_return_review_return", "return_stock_reviews", ["return_id"])
    op.create_index("ix_return_review_status", "return_stock_reviews", ["status"])

    # ------ damaged_lost_inventory ------
    op.create_table(
        "damaged_lost_inventory",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("incident_code", sa.String(32), nullable=False, unique=True),
        sa.Column("sku", sa.String(80), nullable=False),
        sa.Column("warehouse_id", sa.String(80), nullable=False),
        sa.Column(
            "incident_type", sa.String(24), nullable=False,
            comment="damaged | lost | expired | theft",
        ),
        sa.Column("qty", sa.Integer, nullable=False),
        sa.Column(
            "responsible_party", sa.String(48), nullable=True,
            comment="seller | rider | warehouse | carrier | unknown",
        ),
        sa.Column("source_order_id", sa.String(80), nullable=True),
        sa.Column("source_movement_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("evidence_url", sa.String(512), nullable=False),
        sa.Column("reported_by", sa.String(80), nullable=False),
        sa.Column(
            "status", sa.String(24), nullable=False,
            server_default="pending",
            comment="pending | confirmed | resolved | written_off",
        ),
        sa.Column("confirmed_by", sa.String(80), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_audit_log_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "financial_impact_minor", sa.BigInteger, nullable=True,
            comment="BDT minor — populated when Finance reconciles.",
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.CheckConstraint(
            "incident_type IN ('damaged','lost','expired','theft')",
            name="ck_damaged_lost_type",
        ),
        sa.CheckConstraint(
            "status IN ('pending','confirmed','resolved','written_off')",
            name="ck_damaged_lost_status",
        ),
        sa.CheckConstraint("qty > 0", name="ck_damaged_lost_qty_pos"),
    )
    op.create_index("ix_damaged_lost_sku", "damaged_lost_inventory",
                    ["sku", "warehouse_id"])
    op.create_index("ix_damaged_lost_status", "damaged_lost_inventory", ["status"])
    op.create_index("ix_damaged_lost_type", "damaged_lost_inventory",
                    ["incident_type"])

    # ------ seller_stock_accuracy (scorecard) ------
    op.create_table(
        "seller_stock_accuracy",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("seller_id", sa.String(80), nullable=False),
        sa.Column("scored_at", sa.Date, nullable=False),
        sa.Column("listed_qty", sa.Integer, nullable=False, server_default="0"),
        sa.Column("verified_qty", sa.Integer, nullable=False, server_default="0"),
        sa.Column("oversold_count_30d", sa.Integer, nullable=False, server_default="0"),
        sa.Column("missing_count_30d", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "accuracy_bps", sa.Integer, nullable=False, server_default="10000",
            comment="0-10000 basis points (100.00 = perfect)",
        ),
        sa.Column("audit_requested", sa.Boolean, nullable=False,
                  server_default=sa.text("false")),
        sa.Column("audit_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("audit_requested_by", sa.String(80), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.UniqueConstraint("seller_id", "scored_at",
                            name="uq_seller_accuracy_day"),
        sa.CheckConstraint(
            "accuracy_bps BETWEEN 0 AND 10000",
            name="ck_seller_accuracy_bps_range",
        ),
    )
    op.create_index("ix_seller_accuracy_seller", "seller_stock_accuracy",
                    ["seller_id"])


def downgrade() -> None:
    op.drop_index("ix_seller_accuracy_seller", "seller_stock_accuracy")
    op.drop_table("seller_stock_accuracy")
    op.drop_index("ix_damaged_lost_type", "damaged_lost_inventory")
    op.drop_index("ix_damaged_lost_status", "damaged_lost_inventory")
    op.drop_index("ix_damaged_lost_sku", "damaged_lost_inventory")
    op.drop_table("damaged_lost_inventory")
    op.drop_index("ix_return_review_status", "return_stock_reviews")
    op.drop_index("ix_return_review_return", "return_stock_reviews")
    op.drop_table("return_stock_reviews")
    op.drop_index("ix_stock_adj_status", "stock_adjustment_requests")
    op.drop_index("ix_stock_adj_sku", "stock_adjustment_requests")
    op.drop_table("stock_adjustment_requests")
    op.drop_index("ix_stock_movements_created", "stock_movements")
    op.drop_index("ix_stock_movements_sku", "stock_movements")
    op.drop_table("stock_movements")
    op.drop_index("ix_stock_reservations_status", "stock_reservations")
    op.drop_index("ix_stock_reservations_sku", "stock_reservations")
    op.drop_index("ix_stock_reservations_order", "stock_reservations")
    op.drop_table("stock_reservations")
    op.drop_index("ix_inventory_stocks_blocked", "inventory_stocks")
    op.drop_index("ix_inventory_stocks_warehouse", "inventory_stocks")
    op.drop_index("ix_inventory_stocks_sku", "inventory_stocks")
    op.drop_table("inventory_stocks")
    op.drop_index("ix_inv_audit_action", "inventory_audit_logs")
    op.drop_index("ix_inv_audit_sku", "inventory_audit_logs")
    op.drop_index("ix_inv_audit_entity", "inventory_audit_logs")
    op.drop_table("inventory_audit_logs")
