"""Supervisor + Last-Mile Manager operational tables (Phase D of role-rule wiring).

Revision ID: 0092_supervisor_lm_operations
Revises: 0091_inventory_operations
Create Date: 2026-05-26

Adds 9 tables from the Supervisor + Last-Mile Manager Rules package
(2026-05-26). These cover:

  * supervisor_actions          — append-only supervisor action log
  * manager_actions             — append-only manager action log
  * manager_approvals           — manager decision queue (10 approval types)
  * order_escalations           — supervisor → manager escalation queue
  * failed_delivery_reviews     — failed-delivery dispute queue
  * order_sla_events            — SLA breach events
  * risk_flags                  — actor risk flags (seller/rider/customer)
  * supervisor_rules            — admin-configurable rules engine
  * supervisor_shift_reports    — shift report queue + manager review

The 10 manager approval types are constrained by CHECK constraint
on manager_approvals.approval_type — matches the package's enum.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0092_supervisor_lm_operations"
down_revision = "0091_inventory_operations"
branch_labels = None
depends_on = None


_APPROVAL_TYPES_CHECK = (
    "approval_type IN ("
    "'COMPLAINT_PRIORITY','FAILED_DELIVERY_DECISION','FRAUD_ESCALATION',"
    "'HIGH_VALUE_ORDER_REVIEW','ORDER_HOLD_RELEASE','ORDER_MANUAL_HOLD',"
    "'RETURN_TO_SELLER','RIDER_REASSIGNMENT','RIDER_WARNING',"
    "'SELLER_WARNING')"
)


def upgrade() -> None:
    # ------ supervisor_actions ------
    op.create_table(
        "supervisor_actions",
        sa.Column("id", postgresql.UUID(as_uuid=True),
                  primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("action_code", sa.String(32), nullable=False, unique=True),
        sa.Column("supervisor_id", sa.String(80), nullable=False),
        sa.Column("supervisor_role", sa.String(64), nullable=False),
        sa.Column("order_id", sa.String(80), nullable=True),
        sa.Column("entity_type", sa.String(80), nullable=False),
        sa.Column("entity_id", sa.String(120), nullable=False),
        sa.Column("action_type", sa.String(80), nullable=False),
        sa.Column("old_status", sa.String(80), nullable=True),
        sa.Column("new_status", sa.String(80), nullable=True),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column("evidence_url", sa.String(1000), nullable=True),
        sa.Column("rule_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("ip_address", sa.String(64), nullable=True),
        sa.Column("device_info", sa.String(512), nullable=True),
        sa.Column(
            "approval_status", sa.String(32), nullable=False,
            server_default="auto_approved",
            comment="auto_approved | pending_manager | approved | rejected",
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.CheckConstraint(
            "approval_status IN ('auto_approved','pending_manager',"
            "'approved','rejected')",
            name="ck_supervisor_actions_status",
        ),
    )
    op.create_index("ix_supervisor_actions_supervisor", "supervisor_actions",
                    ["supervisor_id", "created_at"])
    op.create_index("ix_supervisor_actions_order", "supervisor_actions",
                    ["order_id", "created_at"])
    op.create_index("ix_supervisor_actions_entity", "supervisor_actions",
                    ["entity_type", "entity_id"])
    op.create_index("ix_supervisor_actions_rule", "supervisor_actions", ["rule_id"])

    # ------ manager_actions ------
    op.create_table(
        "manager_actions",
        sa.Column("id", postgresql.UUID(as_uuid=True),
                  primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("action_code", sa.String(32), nullable=False, unique=True),
        sa.Column("manager_id", sa.String(80), nullable=False),
        sa.Column("entity_type", sa.String(80), nullable=False),
        sa.Column("entity_id", sa.String(120), nullable=False),
        sa.Column("action_type", sa.String(100), nullable=False),
        sa.Column("old_status", sa.String(80), nullable=True),
        sa.Column("new_status", sa.String(80), nullable=True),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("evidence_url", sa.String(1000), nullable=True),
        sa.Column("ip_address", sa.String(80), nullable=True),
        sa.Column("device_info", sa.Text, nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
    )
    op.create_index("ix_manager_actions_manager", "manager_actions",
                    ["manager_id", "created_at"])
    op.create_index("ix_manager_actions_entity", "manager_actions",
                    ["entity_type", "entity_id"])
    op.create_index("ix_manager_actions_action_type", "manager_actions",
                    ["action_type", "created_at"])

    # ------ manager_approvals ------
    op.create_table(
        "manager_approvals",
        sa.Column("id", postgresql.UUID(as_uuid=True),
                  primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("approval_code", sa.String(32), nullable=False, unique=True),
        sa.Column("escalation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("order_id", sa.String(80), nullable=True),
        sa.Column("requested_by", sa.String(80), nullable=False),
        sa.Column("manager_id", sa.String(80), nullable=True),
        sa.Column("approval_type", sa.String(80), nullable=False),
        sa.Column(
            "status", sa.String(30), nullable=False,
            server_default="pending",
            comment="pending | approved | rejected | expired | escalated",
        ),
        sa.Column(
            "priority", sa.String(30), nullable=False,
            server_default="normal",
            comment="low | normal | high | critical",
        ),
        sa.Column("assigned_manager_id", sa.String(80), nullable=True),
        sa.Column("sla_due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("request_reason", sa.Text, nullable=False),
        sa.Column("manager_decision", sa.Text, nullable=True),
        sa.Column("evidence_url", sa.Text, nullable=True),
        sa.Column("evidence_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("evidence_request_count", sa.Integer, nullable=False,
                  server_default="0"),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_action_id", postgresql.UUID(as_uuid=True), nullable=True),
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
            _APPROVAL_TYPES_CHECK,
            name="ck_manager_approvals_approval_type",
        ),
        sa.CheckConstraint(
            "status IN ('pending','approved','rejected','expired','escalated')",
            name="ck_manager_approvals_status",
        ),
        sa.CheckConstraint(
            "priority IN ('low','normal','high','critical')",
            name="ck_manager_approvals_priority",
        ),
    )
    op.create_index("ix_manager_approvals_escalation", "manager_approvals",
                    ["escalation_id"])
    op.create_index("ix_manager_approvals_order", "manager_approvals", ["order_id"])
    op.create_index("ix_manager_approvals_status", "manager_approvals",
                    ["status", "created_at"])
    op.create_index("ix_manager_approvals_priority", "manager_approvals",
                    ["priority", "created_at"])
    op.create_index("ix_manager_approvals_assigned_manager", "manager_approvals",
                    ["assigned_manager_id", "status"])

    # ------ order_escalations ------
    op.create_table(
        "order_escalations",
        sa.Column("id", postgresql.UUID(as_uuid=True),
                  primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("escalation_code", sa.String(32), nullable=False, unique=True),
        sa.Column("order_id", sa.String(80), nullable=True),
        sa.Column("subject_type", sa.String(64), nullable=False),
        sa.Column("subject_id", sa.String(120), nullable=False),
        sa.Column(
            "priority", sa.String(32), nullable=False, server_default="normal",
        ),
        sa.Column(
            "status", sa.String(32), nullable=False, server_default="open",
            comment="open | acknowledged | in_progress | resolved | rejected",
        ),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("evidence_url", sa.String(1000), nullable=True),
        sa.Column("created_by", sa.String(80), nullable=False),
        sa.Column("audit_log_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.CheckConstraint(
            "status IN ('open','acknowledged','in_progress','resolved','rejected')",
            name="ck_order_escalations_status",
        ),
        sa.CheckConstraint(
            "priority IN ('low','normal','high','critical')",
            name="ck_order_escalations_priority",
        ),
    )
    op.create_index("ix_order_escalations_order", "order_escalations",
                    ["order_id", "created_at"])
    op.create_index("ix_order_escalations_status", "order_escalations",
                    ["status", "created_at"])
    op.create_index("ix_order_escalations_subject", "order_escalations",
                    ["subject_type", "subject_id"])

    # ------ failed_delivery_reviews ------
    op.create_table(
        "failed_delivery_reviews",
        sa.Column("id", postgresql.UUID(as_uuid=True),
                  primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("review_code", sa.String(32), nullable=False, unique=True),
        sa.Column("order_id", sa.String(80), nullable=False),
        sa.Column("delivery_task_id", sa.String(80), nullable=False),
        sa.Column("delivery_attempt_id", sa.String(80), nullable=True),
        sa.Column(
            "status", sa.String(32), nullable=False, server_default="pending",
            comment="pending | reviewed | manager_review | resolved",
        ),
        sa.Column("proof_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rider_note", sa.Text, nullable=True),
        sa.Column("call_attempt", sa.Text, nullable=True),
        sa.Column("gps_location", sa.Text, nullable=True),
        sa.Column("customer_support_note", sa.Text, nullable=True),
        sa.Column("photo_evidence_url", sa.String(1000), nullable=True),
        sa.Column(
            "decision", sa.String(64), nullable=True,
            comment="reschedule | return_to_hub | suspicious | manager_review | dropped",
        ),
        sa.Column("decision_reason", sa.Text, nullable=True),
        sa.Column("reviewed_by", sa.String(80), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
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
            "status IN ('pending','reviewed','manager_review','resolved')",
            name="ck_failed_delivery_reviews_status",
        ),
    )
    op.create_index("ix_failed_delivery_reviews_order", "failed_delivery_reviews",
                    ["order_id"])
    op.create_index("ix_failed_delivery_reviews_task", "failed_delivery_reviews",
                    ["delivery_task_id"])
    op.create_index("ix_failed_delivery_reviews_status", "failed_delivery_reviews",
                    ["status", "created_at"])

    # ------ order_sla_events ------
    op.create_table(
        "order_sla_events",
        sa.Column("id", postgresql.UUID(as_uuid=True),
                  primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("event_code", sa.String(32), nullable=False, unique=True),
        sa.Column("order_id", sa.String(80), nullable=False),
        sa.Column("current_status", sa.String(32), nullable=False),
        sa.Column("sla_type", sa.String(64), nullable=False),
        sa.Column("expected_before", sa.DateTime(timezone=True), nullable=False),
        sa.Column("breached_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("severity", sa.String(24), nullable=False, server_default="normal"),
        sa.Column("is_resolved", sa.Boolean, nullable=False,
                  server_default=sa.text("false")),
        sa.Column("resolved_by", sa.String(80), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.CheckConstraint(
            "severity IN ('low','normal','high','critical')",
            name="ck_order_sla_events_severity",
        ),
    )
    op.create_index("ix_order_sla_events_active", "order_sla_events",
                    ["is_resolved", "sla_type", "order_id"])
    op.create_index("ix_order_sla_events_order", "order_sla_events",
                    ["order_id", "created_at"])
    op.create_index("ix_order_sla_events_breached", "order_sla_events", ["breached_at"])

    # ------ risk_flags ------
    op.create_table(
        "risk_flags",
        sa.Column("id", postgresql.UUID(as_uuid=True),
                  primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("flag_code", sa.String(32), nullable=False, unique=True),
        sa.Column(
            "actor_type", sa.String(32), nullable=False,
            comment="seller | rider | customer",
        ),
        sa.Column("actor_id", sa.String(80), nullable=False),
        sa.Column("order_id", sa.String(80), nullable=True),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("evidence_url", sa.String(1000), nullable=True),
        sa.Column(
            "risk_status", sa.String(32), nullable=False, server_default="open",
            comment="open | investigating | confirmed | dismissed",
        ),
        sa.Column("is_active", sa.Boolean, nullable=False,
                  server_default=sa.text("true")),
        sa.Column("created_by", sa.String(80), nullable=False),
        sa.Column("audit_log_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.CheckConstraint(
            "actor_type IN ('seller','rider','customer')",
            name="ck_risk_flags_actor_type",
        ),
        sa.CheckConstraint(
            "risk_status IN ('open','investigating','confirmed','dismissed')",
            name="ck_risk_flags_status",
        ),
    )
    op.create_index("ix_risk_flags_actor", "risk_flags",
                    ["actor_type", "actor_id", "created_at"])
    op.create_index("ix_risk_flags_order", "risk_flags",
                    ["order_id", "created_at"])
    op.create_index("ix_risk_flags_active", "risk_flags", ["is_active", "risk_status"])

    # ------ supervisor_rules ------
    op.create_table(
        "supervisor_rules",
        sa.Column("id", postgresql.UUID(as_uuid=True),
                  primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("rule_code", sa.String(32), nullable=False, unique=True),
        sa.Column("rule_name", sa.String(180), nullable=False),
        sa.Column("rule_category", sa.String(40), nullable=False),
        sa.Column("condition_type", sa.String(80), nullable=False),
        sa.Column("condition_json", postgresql.JSONB, nullable=False),
        sa.Column("action_json", postgresql.JSONB, nullable=False),
        sa.Column("severity", sa.String(24), nullable=False),
        sa.Column("responsible_role", sa.String(64), nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False,
                  server_default=sa.text("true")),
        sa.Column("requires_reason", sa.Boolean, nullable=False,
                  server_default=sa.text("false")),
        sa.Column("requires_evidence", sa.Boolean, nullable=False,
                  server_default=sa.text("false")),
        sa.Column("requires_manager_approval", sa.Boolean, nullable=False,
                  server_default=sa.text("false")),
        sa.Column("created_by", sa.String(80), nullable=True),
        sa.Column("updated_by", sa.String(80), nullable=True),
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
            "severity IN ('low','normal','high','critical')",
            name="ck_supervisor_rules_severity",
        ),
    )
    op.create_index("ix_supervisor_rules_category_active", "supervisor_rules",
                    ["rule_category", "is_active"])
    op.create_index("ix_supervisor_rules_responsible_role", "supervisor_rules",
                    ["responsible_role"])
    op.create_index("ix_supervisor_rules_severity", "supervisor_rules", ["severity"])

    # ------ supervisor_shift_reports ------
    op.create_table(
        "supervisor_shift_reports",
        sa.Column("id", postgresql.UUID(as_uuid=True),
                  primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("report_code", sa.String(32), nullable=False, unique=True),
        sa.Column("shift_id", sa.String(64), nullable=False),
        sa.Column("supervisor_id", sa.String(80), nullable=False),
        sa.Column("supervisor_role", sa.String(64), nullable=False),
        sa.Column(
            "status", sa.String(32), nullable=False, server_default="submitted",
            comment="submitted | reviewed | flagged",
        ),
        sa.Column("shift_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("shift_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("total_orders_monitored", sa.Integer, nullable=False,
                  server_default="0"),
        sa.Column("stuck_orders_found", sa.Integer, nullable=False,
                  server_default="0"),
        sa.Column("escalations_created", sa.Integer, nullable=False,
                  server_default="0"),
        sa.Column("failed_deliveries_reviewed", sa.Integer, nullable=False,
                  server_default="0"),
        sa.Column("complaints_reviewed", sa.Integer, nullable=False,
                  server_default="0"),
        sa.Column("cod_risks_escalated", sa.Integer, nullable=False,
                  server_default="0"),
        sa.Column("unresolved_cases", sa.Integer, nullable=False,
                  server_default="0"),
        sa.Column("handover_note", sa.Text, nullable=False),
        sa.Column(
            "manager_review_status", sa.String(32), nullable=False,
            server_default="pending",
            comment="pending | acknowledged | requires_action",
        ),
        sa.Column("manager_note", sa.Text, nullable=True),
        sa.Column("reviewed_by", sa.String(80), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "submitted_at", sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
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
            "status IN ('submitted','reviewed','flagged')",
            name="ck_supervisor_shift_reports_status",
        ),
        sa.CheckConstraint(
            "manager_review_status IN ('pending','acknowledged','requires_action')",
            name="ck_supervisor_shift_reports_mgr_status",
        ),
    )
    op.create_index("ix_supervisor_shift_reports_shift", "supervisor_shift_reports",
                    ["shift_id", "supervisor_id"])
    op.create_index("ix_supervisor_shift_reports_status", "supervisor_shift_reports",
                    ["status", "submitted_at"])
    op.create_index("ix_supervisor_shift_reports_manager_status",
                    "supervisor_shift_reports",
                    ["manager_review_status", "submitted_at"])


def downgrade() -> None:
    for ix in (
        "ix_supervisor_shift_reports_manager_status",
        "ix_supervisor_shift_reports_status",
        "ix_supervisor_shift_reports_shift",
    ):
        op.drop_index(ix, "supervisor_shift_reports")
    op.drop_table("supervisor_shift_reports")
    for ix in ("ix_supervisor_rules_severity",
               "ix_supervisor_rules_responsible_role",
               "ix_supervisor_rules_category_active"):
        op.drop_index(ix, "supervisor_rules")
    op.drop_table("supervisor_rules")
    for ix in ("ix_risk_flags_active", "ix_risk_flags_order", "ix_risk_flags_actor"):
        op.drop_index(ix, "risk_flags")
    op.drop_table("risk_flags")
    for ix in ("ix_order_sla_events_breached", "ix_order_sla_events_order",
               "ix_order_sla_events_active"):
        op.drop_index(ix, "order_sla_events")
    op.drop_table("order_sla_events")
    for ix in ("ix_failed_delivery_reviews_status",
               "ix_failed_delivery_reviews_task",
               "ix_failed_delivery_reviews_order"):
        op.drop_index(ix, "failed_delivery_reviews")
    op.drop_table("failed_delivery_reviews")
    for ix in ("ix_order_escalations_subject", "ix_order_escalations_status",
               "ix_order_escalations_order"):
        op.drop_index(ix, "order_escalations")
    op.drop_table("order_escalations")
    for ix in ("ix_manager_approvals_assigned_manager",
               "ix_manager_approvals_priority",
               "ix_manager_approvals_status",
               "ix_manager_approvals_order",
               "ix_manager_approvals_escalation"):
        op.drop_index(ix, "manager_approvals")
    op.drop_table("manager_approvals")
    for ix in ("ix_manager_actions_action_type",
               "ix_manager_actions_entity",
               "ix_manager_actions_manager"):
        op.drop_index(ix, "manager_actions")
    op.drop_table("manager_actions")
    for ix in ("ix_supervisor_actions_rule",
               "ix_supervisor_actions_entity",
               "ix_supervisor_actions_order",
               "ix_supervisor_actions_supervisor"):
        op.drop_index(ix, "supervisor_actions")
    op.drop_table("supervisor_actions")
