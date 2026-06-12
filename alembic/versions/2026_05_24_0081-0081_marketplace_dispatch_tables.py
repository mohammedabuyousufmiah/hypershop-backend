"""11 marketplace dispatch domain tables

Revision ID: 0081_marketplace_dispatch_tables
Revises: 0080_fulfillment_stage
Create Date: 2026-05-24

Adds the persistent dispatch domain that feeds the Marketplace
Fulfillment hub (16 surfaces) with real rows instead of derived views:

  1.  marketplace_dispatch_tasks      central work queue, one row per
                                      pickup/delivery/return task
  2.  marketplace_pickup_assignments  rider <-> seller-pickup assignment
                                      (scan + accept + pickup timestamps)
  3.  marketplace_delivery_assignments rider <-> delivery assignment
                                      (batch grouping, started/completed)
  4.  marketplace_delivery_attempts   per-attempt outcome log (POD,
                                      signature, COD collected, GPS)
  5.  marketplace_return_movements    return-leg movement audit
                                      (to_hub / at_hub / to_seller /
                                      with_seller, scan + photo)
  6.  marketplace_dispatch_batches    pickup/delivery/return batches
                                      (run-sheet)
  7.  marketplace_rider_capacity      per-rider snapshot — active counts,
                                      COD float, max caps, last GPS
  8.  marketplace_cod_risk_logs       per-order COD risk score + reasons +
                                      action (block / flag / escalate)
  9.  marketplace_sla_alerts          SLA breach events, raised/resolved
  10. marketplace_dispatch_escalations  supervisor/finance/manager
                                      escalation tickets
  11. marketplace_dispatch_audit_logs  domain-scoped audit (before/after
                                      diffs); REVOKE UPDATE/DELETE from
                                      PUBLIC mirroring core audit_log

All tables prefixed ``marketplace_`` to keep them scoped to the
fulfillment domain and out of the generic catalog/orders namespace.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision = "0081_marketplace_dispatch_tables"
down_revision = "0080_fulfillment_stage"
branch_labels = None
depends_on = None


_NOW_UTC = sa.text("(now() AT TIME ZONE 'UTC')")
_UUID = pg.UUID(as_uuid=True)


def _uuid_pk() -> sa.Column:
    return sa.Column(
        "id", _UUID, primary_key=True,
        server_default=sa.text("gen_random_uuid()"),
    )


def _created_at() -> sa.Column:
    return sa.Column(
        "created_at", sa.DateTime(), nullable=False, server_default=_NOW_UTC,
    )


def _updated_at() -> sa.Column:
    return sa.Column(
        "updated_at", sa.DateTime(), nullable=False, server_default=_NOW_UTC,
    )


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. marketplace_dispatch_tasks — central queue
    # ------------------------------------------------------------------
    op.create_table(
        "marketplace_dispatch_tasks",
        _uuid_pk(),
        sa.Column("order_id", _UUID,
                  sa.ForeignKey("orders.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("task_type", sa.String(20), nullable=False),
        sa.Column("current_stage", sa.String(40), nullable=False),
        sa.Column("status", sa.String(20), nullable=False,
                  server_default=sa.text("'pending'")),
        sa.Column("priority", sa.Integer, nullable=False,
                  server_default=sa.text("0")),
        sa.Column("assigned_rider_id", _UUID,
                  sa.ForeignKey("users.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("sla_due_at", sa.DateTime(), nullable=True),
        sa.Column("notes", sa.String(512), nullable=True),
        _created_at(),
        _updated_at(),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "task_type IN ('pickup','delivery','return')",
            name="task_type_enum",
        ),
        sa.CheckConstraint(
            "status IN ('pending','assigned','in_progress','completed',"
            "'failed','cancelled')",
            name="task_status_enum",
        ),
    )
    op.create_index("ix_dt_status_priority",
                    "marketplace_dispatch_tasks",
                    ["status", "priority"])
    op.create_index("ix_dt_order",
                    "marketplace_dispatch_tasks", ["order_id"])
    op.create_index("ix_dt_rider",
                    "marketplace_dispatch_tasks", ["assigned_rider_id"])
    op.create_index("ix_dt_sla_due",
                    "marketplace_dispatch_tasks", ["sla_due_at"])

    # ------------------------------------------------------------------
    # 2. marketplace_pickup_assignments
    # ------------------------------------------------------------------
    op.create_table(
        "marketplace_pickup_assignments",
        _uuid_pk(),
        sa.Column("task_id", _UUID,
                  sa.ForeignKey("marketplace_dispatch_tasks.id",
                                ondelete="CASCADE"),
                  nullable=False),
        sa.Column("order_id", _UUID,
                  sa.ForeignKey("orders.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("rider_id", _UUID,
                  sa.ForeignKey("users.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("seller_hub", sa.String(80), nullable=True),
        sa.Column("assigned_at", sa.DateTime(), nullable=False,
                  server_default=_NOW_UTC),
        sa.Column("accepted_at", sa.DateTime(), nullable=True),
        sa.Column("picked_up_at", sa.DateTime(), nullable=True),
        sa.Column("scan_id", sa.String(80), nullable=True),
        sa.Column("status", sa.String(20), nullable=False,
                  server_default=sa.text("'assigned'")),
        sa.Column("meta", pg.JSONB(), nullable=True),
        sa.CheckConstraint(
            "status IN ('assigned','accepted','picked_up','failed','cancelled')",
            name="pickup_status_enum",
        ),
    )
    op.create_index("ix_pickup_rider_status",
                    "marketplace_pickup_assignments",
                    ["rider_id", "status"])
    op.create_index("ix_pickup_order",
                    "marketplace_pickup_assignments", ["order_id"])

    # ------------------------------------------------------------------
    # 3. marketplace_delivery_assignments
    # ------------------------------------------------------------------
    op.create_table(
        "marketplace_delivery_assignments",
        _uuid_pk(),
        sa.Column("task_id", _UUID,
                  sa.ForeignKey("marketplace_dispatch_tasks.id",
                                ondelete="CASCADE"),
                  nullable=False),
        sa.Column("order_id", _UUID,
                  sa.ForeignKey("orders.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("rider_id", _UUID,
                  sa.ForeignKey("users.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("batch_id", _UUID, nullable=True),  # FK added later
        sa.Column("sequence_no", sa.Integer, nullable=True),
        sa.Column("assigned_at", sa.DateTime(), nullable=False,
                  server_default=_NOW_UTC),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False,
                  server_default=sa.text("'assigned'")),
        sa.Column("meta", pg.JSONB(), nullable=True),
        sa.CheckConstraint(
            "status IN ('assigned','in_transit','delivered','failed','cancelled')",
            name="delivery_status_enum",
        ),
    )
    op.create_index("ix_del_rider_status",
                    "marketplace_delivery_assignments",
                    ["rider_id", "status"])
    op.create_index("ix_del_order",
                    "marketplace_delivery_assignments", ["order_id"])
    op.create_index("ix_del_batch",
                    "marketplace_delivery_assignments", ["batch_id"])

    # ------------------------------------------------------------------
    # 4. marketplace_delivery_attempts
    # ------------------------------------------------------------------
    op.create_table(
        "marketplace_delivery_attempts",
        _uuid_pk(),
        sa.Column("order_id", _UUID,
                  sa.ForeignKey("orders.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("rider_id", _UUID,
                  sa.ForeignKey("users.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("attempt_no", sa.Integer, nullable=False),
        sa.Column("attempted_at", sa.DateTime(), nullable=False,
                  server_default=_NOW_UTC),
        sa.Column("outcome", sa.String(20), nullable=False),
        sa.Column("failure_reason", sa.String(200), nullable=True),
        sa.Column("pod_photo_url", sa.String(500), nullable=True),
        sa.Column("signature_url", sa.String(500), nullable=True),
        sa.Column("cod_collected_minor", sa.BigInteger, nullable=True),
        sa.Column("gps_lat", sa.Numeric(9, 6), nullable=True),
        sa.Column("gps_lng", sa.Numeric(9, 6), nullable=True),
        sa.Column("notes", sa.String(400), nullable=True),
        sa.CheckConstraint(
            "outcome IN ('delivered','failed','rescheduled','customer_unreachable',"
            "'address_issue','cod_refused','partial')",
            name="attempt_outcome_enum",
        ),
        sa.CheckConstraint("attempt_no >= 1", name="attempt_no_positive"),
        sa.UniqueConstraint("order_id", "attempt_no", name="uq_order_attempt"),
    )
    op.create_index("ix_attempts_order_time",
                    "marketplace_delivery_attempts",
                    ["order_id", "attempted_at"])
    op.create_index("ix_attempts_rider",
                    "marketplace_delivery_attempts", ["rider_id"])

    # ------------------------------------------------------------------
    # 5. marketplace_return_movements
    # ------------------------------------------------------------------
    op.create_table(
        "marketplace_return_movements",
        _uuid_pk(),
        sa.Column("order_id", _UUID,
                  sa.ForeignKey("orders.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("return_request_id", _UUID, nullable=True),
        sa.Column("leg", sa.String(20), nullable=False),
        sa.Column("from_location", sa.String(120), nullable=True),
        sa.Column("to_location", sa.String(120), nullable=True),
        sa.Column("moved_at", sa.DateTime(), nullable=False,
                  server_default=_NOW_UTC),
        sa.Column("moved_by_rider_id", _UUID,
                  sa.ForeignKey("users.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("scan_id", sa.String(80), nullable=True),
        sa.Column("photo_url", sa.String(500), nullable=True),
        sa.Column("signature_url", sa.String(500), nullable=True),
        sa.Column("seller_otp", sa.String(12), nullable=True),
        sa.Column("notes", sa.String(400), nullable=True),
        sa.CheckConstraint(
            "leg IN ('to_hub','at_hub','to_seller','with_seller','seller_signed')",
            name="movement_leg_enum",
        ),
    )
    op.create_index("ix_rm_order_time",
                    "marketplace_return_movements",
                    ["order_id", "moved_at"])
    op.create_index("ix_rm_return",
                    "marketplace_return_movements", ["return_request_id"])

    # ------------------------------------------------------------------
    # 6. marketplace_dispatch_batches
    # ------------------------------------------------------------------
    op.create_table(
        "marketplace_dispatch_batches",
        _uuid_pk(),
        sa.Column("batch_code", sa.String(40), nullable=False, unique=True),
        sa.Column("rider_id", _UUID,
                  sa.ForeignKey("users.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("batch_type", sa.String(20), nullable=False),
        sa.Column("hub_id", sa.String(40), nullable=True),
        sa.Column("order_count", sa.Integer, nullable=False,
                  server_default=sa.text("0")),
        sa.Column("created_by", _UUID,
                  sa.ForeignKey("users.id", ondelete="SET NULL"),
                  nullable=True),
        _created_at(),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False,
                  server_default=sa.text("'pending'")),
        sa.Column("meta", pg.JSONB(), nullable=True),
        sa.CheckConstraint(
            "batch_type IN ('pickup','delivery','return')",
            name="batch_type_enum",
        ),
        sa.CheckConstraint(
            "status IN ('pending','active','completed','cancelled')",
            name="batch_status_enum",
        ),
        sa.CheckConstraint("order_count >= 0", name="batch_order_count_nonneg"),
    )
    op.create_index("ix_batch_rider_status",
                    "marketplace_dispatch_batches",
                    ["rider_id", "status"])
    op.create_index("ix_batch_type_status",
                    "marketplace_dispatch_batches",
                    ["batch_type", "status"])
    # Add the deferred FK from delivery_assignments.batch_id.
    op.create_foreign_key(
        "fk_delivery_batch",
        "marketplace_delivery_assignments",
        "marketplace_dispatch_batches",
        ["batch_id"], ["id"],
        ondelete="SET NULL",
    )

    # ------------------------------------------------------------------
    # 7. marketplace_rider_capacity  (one row per rider)
    # ------------------------------------------------------------------
    op.create_table(
        "marketplace_rider_capacity",
        sa.Column("rider_id", _UUID,
                  sa.ForeignKey("users.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("active_pickup_count", sa.Integer, nullable=False,
                  server_default=sa.text("0")),
        sa.Column("active_delivery_count", sa.Integer, nullable=False,
                  server_default=sa.text("0")),
        sa.Column("max_pickup", sa.Integer, nullable=False,
                  server_default=sa.text("10")),
        sa.Column("max_delivery", sa.Integer, nullable=False,
                  server_default=sa.text("20")),
        sa.Column("cod_float_minor", sa.BigInteger, nullable=False,
                  server_default=sa.text("0")),
        sa.Column("max_cod_float_minor", sa.BigInteger, nullable=False,
                  server_default=sa.text("5000000")),  # 50,000 BDT default
        sa.Column("vehicle_type", sa.String(20), nullable=True),
        sa.Column("last_location_lat", sa.Numeric(9, 6), nullable=True),
        sa.Column("last_location_lng", sa.Numeric(9, 6), nullable=True),
        sa.Column("available", sa.Boolean, nullable=False,
                  server_default=sa.text("true")),
        sa.Column("last_update_at", sa.DateTime(), nullable=False,
                  server_default=_NOW_UTC),
        sa.CheckConstraint(
            "vehicle_type IS NULL OR vehicle_type IN "
            "('bicycle','motorbike','van','truck','foot')",
            name="vehicle_type_enum",
        ),
        sa.CheckConstraint(
            "active_pickup_count >= 0 AND active_delivery_count >= 0 "
            "AND cod_float_minor >= 0",
            name="rider_cap_nonneg",
        ),
    )
    op.create_index("ix_rc_available",
                    "marketplace_rider_capacity", ["available"])

    # ------------------------------------------------------------------
    # 8. marketplace_cod_risk_logs
    # ------------------------------------------------------------------
    op.create_table(
        "marketplace_cod_risk_logs",
        _uuid_pk(),
        sa.Column("order_id", _UUID,
                  sa.ForeignKey("orders.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("rider_id", _UUID,
                  sa.ForeignKey("users.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("risk_score", sa.Integer, nullable=False),
        sa.Column("risk_factors", pg.JSONB(), nullable=True),
        sa.Column("action_taken", sa.String(20), nullable=False,
                  server_default=sa.text("'none'")),
        sa.Column("flagged_at", sa.DateTime(), nullable=False,
                  server_default=_NOW_UTC),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.Column("resolved_by", _UUID,
                  sa.ForeignKey("users.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("notes", sa.String(400), nullable=True),
        sa.CheckConstraint(
            "action_taken IN ('none','flagged','blocked','escalated','cleared')",
            name="cod_action_enum",
        ),
        sa.CheckConstraint(
            "risk_score >= 0 AND risk_score <= 100", name="cod_score_range",
        ),
    )
    op.create_index("ix_cod_order",
                    "marketplace_cod_risk_logs", ["order_id"])
    op.create_index("ix_cod_score_time",
                    "marketplace_cod_risk_logs", ["risk_score", "flagged_at"])

    # ------------------------------------------------------------------
    # 9. marketplace_sla_alerts
    # ------------------------------------------------------------------
    op.create_table(
        "marketplace_sla_alerts",
        _uuid_pk(),
        sa.Column("order_id", _UUID,
                  sa.ForeignKey("orders.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("sla_type", sa.String(20), nullable=False),
        sa.Column("breach_minutes", sa.Integer, nullable=False),
        sa.Column("severity", sa.String(10), nullable=False),
        sa.Column("raised_at", sa.DateTime(), nullable=False,
                  server_default=_NOW_UTC),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.Column("resolved_by", _UUID,
                  sa.ForeignKey("users.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("resolution_action", sa.String(40), nullable=True),
        sa.Column("notes", sa.String(400), nullable=True),
        sa.CheckConstraint(
            "sla_type IN ('dispatch','pickup','delivery','return','seller_pack')",
            name="sla_type_enum",
        ),
        sa.CheckConstraint(
            "severity IN ('warning','critical','escalated')",
            name="sla_severity_enum",
        ),
        sa.CheckConstraint("breach_minutes >= 0", name="sla_breach_nonneg"),
    )
    op.create_index("ix_sla_order_time",
                    "marketplace_sla_alerts", ["order_id", "raised_at"])
    op.create_index("ix_sla_type_unresolved",
                    "marketplace_sla_alerts",
                    ["sla_type"],
                    postgresql_where=sa.text("resolved_at IS NULL"))

    # ------------------------------------------------------------------
    # 10. marketplace_dispatch_escalations
    # ------------------------------------------------------------------
    op.create_table(
        "marketplace_dispatch_escalations",
        _uuid_pk(),
        sa.Column("order_id", _UUID,
                  sa.ForeignKey("orders.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("escalation_level", sa.String(20), nullable=False),
        sa.Column("reason", sa.String(400), nullable=False),
        sa.Column("escalated_by", _UUID,
                  sa.ForeignKey("users.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("escalated_to", _UUID,
                  sa.ForeignKey("users.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("status", sa.String(20), nullable=False,
                  server_default=sa.text("'open'")),
        _created_at(),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.Column("resolution_notes", sa.String(400), nullable=True),
        sa.CheckConstraint(
            "escalation_level IN ('supervisor','finance','manager',"
            "'super_admin','rider_manager','seller_manager')",
            name="esc_level_enum",
        ),
        sa.CheckConstraint(
            "status IN ('open','in_progress','resolved','dismissed')",
            name="esc_status_enum",
        ),
    )
    op.create_index("ix_esc_status",
                    "marketplace_dispatch_escalations", ["status"])
    op.create_index("ix_esc_order_time",
                    "marketplace_dispatch_escalations",
                    ["order_id", "created_at"])

    # ------------------------------------------------------------------
    # 11. marketplace_dispatch_audit_logs (append-only, REVOKE UPDATE/DELETE)
    # ------------------------------------------------------------------
    op.create_table(
        "marketplace_dispatch_audit_logs",
        _uuid_pk(),
        sa.Column("actor_user_id", _UUID,
                  sa.ForeignKey("users.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("action", sa.String(80), nullable=False),
        sa.Column("resource_type", sa.String(40), nullable=False),
        sa.Column("resource_id", sa.String(80), nullable=True),
        sa.Column("before", pg.JSONB(), nullable=True),
        sa.Column("after", pg.JSONB(), nullable=True),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column("user_agent", sa.String(300), nullable=True),
        _created_at(),
    )
    op.create_index("ix_dal_actor_time",
                    "marketplace_dispatch_audit_logs",
                    ["actor_user_id", "created_at"])
    op.create_index("ix_dal_resource",
                    "marketplace_dispatch_audit_logs",
                    ["resource_type", "resource_id"])
    op.create_index("ix_dal_action_time",
                    "marketplace_dispatch_audit_logs",
                    ["action", "created_at"])
    # Defence-in-depth: revoke mutation on audit table (mirrors core
    # audit_log + order_status_history + order_fulfillment_stage_history).
    op.execute(
        "REVOKE UPDATE, DELETE ON TABLE marketplace_dispatch_audit_logs "
        "FROM PUBLIC"
    )


def downgrade() -> None:
    op.drop_table("marketplace_dispatch_audit_logs")
    op.drop_table("marketplace_dispatch_escalations")
    op.drop_table("marketplace_sla_alerts")
    op.drop_table("marketplace_cod_risk_logs")
    op.drop_table("marketplace_rider_capacity")
    op.drop_constraint(
        "fk_delivery_batch",
        "marketplace_delivery_assignments",
        type_="foreignkey",
    )
    op.drop_table("marketplace_dispatch_batches")
    op.drop_table("marketplace_return_movements")
    op.drop_table("marketplace_delivery_attempts")
    op.drop_table("marketplace_delivery_assignments")
    op.drop_table("marketplace_pickup_assignments")
    op.drop_table("marketplace_dispatch_tasks")
