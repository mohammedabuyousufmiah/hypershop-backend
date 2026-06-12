"""Warehouse Mother-QR workflow tables (Phase E of role-rule wiring).

Revision ID: 0093_mother_qr_workflow
Revises: 0092_supervisor_lm_operations
Create Date: 2026-05-26

Adds the 3 core tables for the Mother-QR canonical lifecycle from the
``hypershop-warehouse-mother-qr-updated`` package (2026-05-26):

  * mother_qr_items          — one row per receiving unit. Carries the
                                current status, location, and full
                                provenance (supplier / batch / serial).
                                Status transitions are constrained by
                                ``service.apply_scan`` against the
                                ``TRANSITIONS`` matrix; the DB CHECK
                                here is the catalog of allowed status
                                values only.
  * mother_qr_scan_events    — append-only scan-log. Every transition
                                writes a row here BEFORE the parent
                                ``mother_qr_items.status`` flips, so
                                the audit trail is complete even on
                                rollback.
  * warehouse_locations      — registered Shelf/Rack/Bin QR codes.
                                Lets the service refuse a shelf scan
                                when the target QR isn't in the
                                location registry.

Status enum (42 values) and scan action enum (27 values) mirror the
package's ``MotherQrStatus`` / ``ScanAction`` StrEnums. The full
``TRANSITIONS`` matrix (which actor role may move from which source
set to which target) lives in
``app/modules/mother_qr/transitions.py`` so the DB stays free of
role-specific FK joins.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0093_mother_qr_workflow"
down_revision = "0092_supervisor_lm_operations"
branch_labels = None
depends_on = None


_STATUS_VALUES = (
    "'GATE_IN','RECEIVED','QC_PENDING','QC_PASSED','QC_FAILED',"
    "'HOLD','HOLD_FOR_REVIEW','DAMAGED_AT_RECEIVING','WRONG_ITEM_RECEIVED',"
    "'SHORT_QUANTITY','EXCESS_QUANTITY','RECEIVING_DISCREPANCY',"
    "'SHELVED','SHELF_ASSIGNED','SELLABLE_STOCK','AVAILABLE',"
    "'DAMAGED_STOCK','RESERVED','PICKED','PACKED','DISPATCH_READY',"
    "'RIDER_HANDOVER','HANDED_TO_RIDER','OUT_FOR_DELIVERY',"
    "'FAILED_DELIVERY_REVIEW','RESCHEDULED_DELIVERY',"
    "'FAILED_DELIVERY_SUSPICIOUS','FAILED_DELIVERY_MANAGER_REVIEW',"
    "'DELIVERED','QUARANTINED','RETURN_REQUESTED','RETURNED_TO_WAREHOUSE',"
    "'RETURN_RECEIVED','RETURN_QC_PENDING','RETURNED_TO_STOCK',"
    "'DAMAGED','LOST','DISPOSED','SELLER_RETURN','RETURN_RECEIVED_AT_HUB',"
    "'RETURN_INVENTORY_REVIEW','RETURN_FINANCE_REVIEW'"
)


def upgrade() -> None:
    # ------ mother_qr_items ------
    op.create_table(
        "mother_qr_items",
        sa.Column("id", postgresql.UUID(as_uuid=True),
                  primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("mother_qr", sa.String(120), nullable=False, unique=True),
        sa.Column("receiving_batch_qr", sa.String(120), nullable=True),
        sa.Column("sku", sa.String(80), nullable=False),
        sa.Column("product_name", sa.String(200), nullable=False),
        sa.Column("quantity", sa.Integer, nullable=False, server_default="1"),
        sa.Column("status", sa.String(40), nullable=False,
                  server_default="GATE_IN"),
        sa.Column("location_code", sa.String(80), nullable=False),
        sa.Column("location_hierarchy_json", postgresql.JSONB, nullable=True),
        sa.Column("warehouse_id", sa.String(80), nullable=False),
        sa.Column("received_by", sa.String(80), nullable=False),
        sa.Column("order_id", sa.String(80), nullable=True),
        sa.Column("supplier_id", sa.String(80), nullable=True),
        sa.Column("purchase_order_id", sa.String(80), nullable=True),
        sa.Column("batch_no", sa.String(80), nullable=True),
        sa.Column("serial_no", sa.String(80), nullable=True),
        sa.Column("expiry_date", sa.String(40), nullable=True),
        sa.Column("metadata_json", postgresql.JSONB, nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
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
            f"status IN ({_STATUS_VALUES})",
            name="ck_mother_qr_items_status",
        ),
        sa.CheckConstraint("quantity > 0", name="ck_mother_qr_items_qty_pos"),
    )
    op.create_index("ix_mother_qr_items_sku", "mother_qr_items",
                    ["sku", "warehouse_id"])
    op.create_index("ix_mother_qr_items_status", "mother_qr_items", ["status"])
    op.create_index("ix_mother_qr_items_order", "mother_qr_items", ["order_id"])
    op.create_index("ix_mother_qr_items_supplier", "mother_qr_items",
                    ["supplier_id"])

    # ------ mother_qr_scan_events ------
    op.create_table(
        "mother_qr_scan_events",
        sa.Column("id", postgresql.UUID(as_uuid=True),
                  primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("event_code", sa.String(32), nullable=False, unique=True),
        sa.Column("mother_qr", sa.String(120), nullable=False),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("old_status", sa.String(40), nullable=True),
        sa.Column("new_status", sa.String(40), nullable=False),
        sa.Column("actor_id", sa.String(80), nullable=False),
        sa.Column("actor_role", sa.String(48), nullable=False),
        sa.Column("location_code", sa.String(80), nullable=False),
        sa.Column("scanned_qr", sa.String(120), nullable=True),
        sa.Column("scan_type", sa.String(32), nullable=True),
        sa.Column("order_id", sa.String(80), nullable=True),
        sa.Column("device_id", sa.String(120), nullable=True),
        sa.Column("result", sa.String(32), nullable=False,
                  server_default="ok"),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column("evidence_url", sa.String(1000), nullable=True),
        sa.Column("metadata_json", postgresql.JSONB, nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
    )
    op.create_index("ix_mqr_scan_events_mqr", "mother_qr_scan_events",
                    ["mother_qr", "created_at"])
    op.create_index("ix_mqr_scan_events_action", "mother_qr_scan_events",
                    ["action", "created_at"])
    op.create_index("ix_mqr_scan_events_actor", "mother_qr_scan_events",
                    ["actor_id", "created_at"])
    op.create_index("ix_mqr_scan_events_order", "mother_qr_scan_events",
                    ["order_id"])

    # ------ warehouse_locations (registered Shelf/Rack/Bin QRs) ------
    op.create_table(
        "warehouse_locations",
        sa.Column("id", postgresql.UUID(as_uuid=True),
                  primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("qr_code", sa.String(120), nullable=False, unique=True),
        sa.Column("warehouse_id", sa.String(80), nullable=False),
        sa.Column("zone", sa.String(40), nullable=False),
        sa.Column("aisle", sa.String(40), nullable=False),
        sa.Column("rack", sa.String(40), nullable=False),
        sa.Column("shelf", sa.String(40), nullable=False),
        sa.Column("bin", sa.String(40), nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False,
                  server_default=sa.text("true")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
    )
    op.create_index("ix_warehouse_locations_wh_active", "warehouse_locations",
                    ["warehouse_id", "is_active"])


def downgrade() -> None:
    op.drop_index("ix_warehouse_locations_wh_active", "warehouse_locations")
    op.drop_table("warehouse_locations")
    for ix in ("ix_mqr_scan_events_order", "ix_mqr_scan_events_actor",
               "ix_mqr_scan_events_action", "ix_mqr_scan_events_mqr"):
        op.drop_index(ix, "mother_qr_scan_events")
    op.drop_table("mother_qr_scan_events")
    for ix in ("ix_mother_qr_items_supplier", "ix_mother_qr_items_order",
               "ix_mother_qr_items_status", "ix_mother_qr_items_sku"):
        op.drop_index(ix, "mother_qr_items")
    op.drop_table("mother_qr_items")
