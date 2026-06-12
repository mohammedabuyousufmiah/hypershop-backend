"""ORM models for Supervisor + Last-Mile Manager operational queues."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db.base import Base
from app.core.db.mixins import TimestampMixin


class SupervisorAction(Base):
    """Append-only supervisor action log."""

    __tablename__ = "supervisor_actions"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    action_code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    supervisor_id: Mapped[str] = mapped_column(String(80), nullable=False)
    supervisor_role: Mapped[str] = mapped_column(String(64), nullable=False)
    order_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    entity_type: Mapped[str] = mapped_column(String(80), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(120), nullable=False)
    action_type: Mapped[str] = mapped_column(String(80), nullable=False)
    old_status: Mapped[str | None] = mapped_column(String(80), nullable=True)
    new_status: Mapped[str | None] = mapped_column(String(80), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    rule_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True,
    )
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    device_info: Mapped[str | None] = mapped_column(String(512), nullable=True)
    approval_status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="auto_approved",
    )
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=text("(now() AT TIME ZONE 'UTC')"),
    )

    __table_args__ = (
        CheckConstraint(
            "approval_status IN ('auto_approved','pending_manager',"
            "'approved','rejected')",
            name="ck_supervisor_actions_status",
        ),
        Index("ix_supervisor_actions_supervisor", "supervisor_id", "created_at"),
        Index("ix_supervisor_actions_order", "order_id", "created_at"),
        Index("ix_supervisor_actions_entity", "entity_type", "entity_id"),
        Index("ix_supervisor_actions_rule", "rule_id"),
    )


class ManagerAction(Base):
    """Append-only manager action log."""

    __tablename__ = "manager_actions"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    action_code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    manager_id: Mapped[str] = mapped_column(String(80), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(80), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(120), nullable=False)
    action_type: Mapped[str] = mapped_column(String(100), nullable=False)
    old_status: Mapped[str | None] = mapped_column(String(80), nullable=True)
    new_status: Mapped[str | None] = mapped_column(String(80), nullable=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(80), nullable=True)
    device_info: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=text("(now() AT TIME ZONE 'UTC')"),
    )

    __table_args__ = (
        Index("ix_manager_actions_manager", "manager_id", "created_at"),
        Index("ix_manager_actions_entity", "entity_type", "entity_id"),
        Index("ix_manager_actions_action_type", "action_type", "created_at"),
    )


class ManagerApproval(Base, TimestampMixin):
    __tablename__ = "manager_approvals"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    approval_code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    escalation_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True,
    )
    order_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    requested_by: Mapped[str] = mapped_column(String(80), nullable=False)
    manager_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    approval_type: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, server_default="pending",
    )
    priority: Mapped[str] = mapped_column(
        String(30), nullable=False, server_default="normal",
    )
    assigned_manager_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    sla_due_at: Mapped[datetime | None] = mapped_column(nullable=True)
    request_reason: Mapped[str] = mapped_column(Text, nullable=False)
    manager_decision: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_requested_at: Mapped[datetime | None] = mapped_column(nullable=True)
    evidence_request_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0",
    )
    decided_at: Mapped[datetime | None] = mapped_column(nullable=True)
    decision_action_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True,
    )

    __table_args__ = (
        CheckConstraint(
            "approval_type IN ('COMPLAINT_PRIORITY','FAILED_DELIVERY_DECISION',"
            "'FRAUD_ESCALATION','HIGH_VALUE_ORDER_REVIEW','ORDER_HOLD_RELEASE',"
            "'ORDER_MANUAL_HOLD','RETURN_TO_SELLER','RIDER_REASSIGNMENT',"
            "'RIDER_WARNING','SELLER_WARNING')",
            name="ck_manager_approvals_approval_type",
        ),
        CheckConstraint(
            "status IN ('pending','approved','rejected','expired','escalated')",
            name="ck_manager_approvals_status",
        ),
        CheckConstraint(
            "priority IN ('low','normal','high','critical')",
            name="ck_manager_approvals_priority",
        ),
        Index("ix_manager_approvals_escalation", "escalation_id"),
        Index("ix_manager_approvals_order", "order_id"),
        Index("ix_manager_approvals_status", "status", "created_at"),
        Index("ix_manager_approvals_priority", "priority", "created_at"),
        Index("ix_manager_approvals_assigned_manager",
              "assigned_manager_id", "status"),
    )


class OrderEscalation(Base):
    __tablename__ = "order_escalations"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    escalation_code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    order_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    subject_type: Mapped[str] = mapped_column(String(64), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(120), nullable=False)
    priority: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="normal",
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="open",
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_by: Mapped[str] = mapped_column(String(80), nullable=False)
    audit_log_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=text("(now() AT TIME ZONE 'UTC')"),
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('open','acknowledged','in_progress','resolved','rejected')",
            name="ck_order_escalations_status",
        ),
        CheckConstraint(
            "priority IN ('low','normal','high','critical')",
            name="ck_order_escalations_priority",
        ),
        Index("ix_order_escalations_order", "order_id", "created_at"),
        Index("ix_order_escalations_status", "status", "created_at"),
        Index("ix_order_escalations_subject", "subject_type", "subject_id"),
    )


class FailedDeliveryReview(Base, TimestampMixin):
    __tablename__ = "failed_delivery_reviews"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    review_code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    order_id: Mapped[str] = mapped_column(String(80), nullable=False)
    delivery_task_id: Mapped[str] = mapped_column(String(80), nullable=False)
    delivery_attempt_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="pending",
    )
    proof_timestamp: Mapped[datetime | None] = mapped_column(nullable=True)
    rider_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    call_attempt: Mapped[str | None] = mapped_column(Text, nullable=True)
    gps_location: Mapped[str | None] = mapped_column(Text, nullable=True)
    customer_support_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    photo_evidence_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    decision: Mapped[str | None] = mapped_column(String(64), nullable=True)
    decision_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    decision_audit_log_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True,
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','reviewed','manager_review','resolved')",
            name="ck_failed_delivery_reviews_status",
        ),
        Index("ix_failed_delivery_reviews_order", "order_id"),
        Index("ix_failed_delivery_reviews_task", "delivery_task_id"),
        Index("ix_failed_delivery_reviews_status", "status", "created_at"),
    )


class OrderSlaEvent(Base):
    __tablename__ = "order_sla_events"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    event_code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    order_id: Mapped[str] = mapped_column(String(80), nullable=False)
    current_status: Mapped[str] = mapped_column(String(32), nullable=False)
    sla_type: Mapped[str] = mapped_column(String(64), nullable=False)
    expected_before: Mapped[datetime] = mapped_column(nullable=False)
    breached_at: Mapped[datetime | None] = mapped_column(nullable=True)
    severity: Mapped[str] = mapped_column(
        String(24), nullable=False, server_default="normal",
    )
    is_resolved: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"),
    )
    resolved_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=text("(now() AT TIME ZONE 'UTC')"),
    )

    __table_args__ = (
        CheckConstraint(
            "severity IN ('low','normal','high','critical')",
            name="ck_order_sla_events_severity",
        ),
        Index("ix_order_sla_events_active", "is_resolved", "sla_type", "order_id"),
        Index("ix_order_sla_events_order", "order_id", "created_at"),
        Index("ix_order_sla_events_breached", "breached_at"),
    )


class RiskFlag(Base):
    __tablename__ = "risk_flags"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    flag_code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    actor_type: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(80), nullable=False)
    order_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    risk_status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="open",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true"),
    )
    created_by: Mapped[str] = mapped_column(String(80), nullable=False)
    audit_log_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=text("(now() AT TIME ZONE 'UTC')"),
    )

    __table_args__ = (
        CheckConstraint(
            "actor_type IN ('seller','rider','customer')",
            name="ck_risk_flags_actor_type",
        ),
        CheckConstraint(
            "risk_status IN ('open','investigating','confirmed','dismissed')",
            name="ck_risk_flags_status",
        ),
        Index("ix_risk_flags_actor", "actor_type", "actor_id", "created_at"),
        Index("ix_risk_flags_order", "order_id", "created_at"),
        Index("ix_risk_flags_active", "is_active", "risk_status"),
    )


class SupervisorRule(Base, TimestampMixin):
    __tablename__ = "supervisor_rules"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    rule_code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    rule_name: Mapped[str] = mapped_column(String(180), nullable=False)
    rule_category: Mapped[str] = mapped_column(String(40), nullable=False)
    condition_type: Mapped[str] = mapped_column(String(80), nullable=False)
    condition_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    action_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    severity: Mapped[str] = mapped_column(String(24), nullable=False)
    responsible_role: Mapped[str] = mapped_column(String(64), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true"),
    )
    requires_reason: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"),
    )
    requires_evidence: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"),
    )
    requires_manager_approval: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"),
    )
    created_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(80), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "severity IN ('low','normal','high','critical')",
            name="ck_supervisor_rules_severity",
        ),
        Index("ix_supervisor_rules_category_active", "rule_category", "is_active"),
        Index("ix_supervisor_rules_responsible_role", "responsible_role"),
        Index("ix_supervisor_rules_severity", "severity"),
    )


class SupervisorShiftReport(Base, TimestampMixin):
    __tablename__ = "supervisor_shift_reports"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    report_code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    shift_id: Mapped[str] = mapped_column(String(64), nullable=False)
    supervisor_id: Mapped[str] = mapped_column(String(80), nullable=False)
    supervisor_role: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="submitted",
    )
    shift_start: Mapped[datetime | None] = mapped_column(nullable=True)
    shift_end: Mapped[datetime | None] = mapped_column(nullable=True)
    total_orders_monitored: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0",
    )
    stuck_orders_found: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0",
    )
    escalations_created: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0",
    )
    failed_deliveries_reviewed: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0",
    )
    complaints_reviewed: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0",
    )
    cod_risks_escalated: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0",
    )
    unresolved_cases: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0",
    )
    handover_note: Mapped[str] = mapped_column(Text, nullable=False)
    manager_review_status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="pending",
    )
    manager_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    submitted_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=text("(now() AT TIME ZONE 'UTC')"),
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('submitted','reviewed','flagged')",
            name="ck_supervisor_shift_reports_status",
        ),
        CheckConstraint(
            "manager_review_status IN ('pending','acknowledged','requires_action')",
            name="ck_supervisor_shift_reports_mgr_status",
        ),
        Index("ix_supervisor_shift_reports_shift", "shift_id", "supervisor_id"),
        Index("ix_supervisor_shift_reports_status", "status", "submitted_at"),
        Index("ix_supervisor_shift_reports_manager_status",
              "manager_review_status", "submitted_at"),
    )
