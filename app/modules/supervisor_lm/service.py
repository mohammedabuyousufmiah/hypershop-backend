"""Supervisor + Last-Mile Manager service — audit-first writes
with authority_matrix red-line enforcement.

Two entry points:

  * ``record_supervisor_action`` — for supervisor verbs (flag, escalate,
    review). Auto-approved when the rule + role pairing allows it,
    otherwise written as ``pending_manager``.

  * ``record_manager_decision`` — for manager-tier verbs (approve /
    reject escalation, release hold, decide failed-delivery dispute).
    Runs the matrix red-line first (manager cannot start
    OUT_FOR_DELIVERY etc.), then writes the manager_actions row.

The 10 manager-approval types from the package
(``RIDER_REASSIGNMENT``, ``ORDER_MANUAL_HOLD``, etc.) are mapped to
``AuthorityAction`` red-lines per the Phase A matrix.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.iam.authority_matrix import (
    AuthorityAction,
    AuthorityViolation,
    ensure_action_allowed,
)
from app.modules.supervisor_lm.models import (
    FailedDeliveryReview,
    ManagerAction,
    ManagerApproval,
    OrderEscalation,
    RiskFlag,
    SupervisorAction,
)


class SupervisorActionType(StrEnum):
    """Supervisor verbs — flagging / escalating / reviewing."""

    FLAG_RISK = "FLAG_RISK"
    CREATE_ESCALATION = "CREATE_ESCALATION"
    REVIEW_FAILED_DELIVERY = "REVIEW_FAILED_DELIVERY"
    REVIEW_COMPLAINT = "REVIEW_COMPLAINT"
    REQUEST_RIDER_REASSIGNMENT = "REQUEST_RIDER_REASSIGNMENT"
    SUBMIT_SHIFT_REPORT = "SUBMIT_SHIFT_REPORT"
    ADD_DELAY_REASON = "ADD_DELAY_REASON"


class ManagerApprovalType(StrEnum):
    """10 manager-approval verbs that the manager decides on."""

    COMPLAINT_PRIORITY = "COMPLAINT_PRIORITY"
    FAILED_DELIVERY_DECISION = "FAILED_DELIVERY_DECISION"
    FRAUD_ESCALATION = "FRAUD_ESCALATION"
    HIGH_VALUE_ORDER_REVIEW = "HIGH_VALUE_ORDER_REVIEW"
    ORDER_HOLD_RELEASE = "ORDER_HOLD_RELEASE"
    ORDER_MANUAL_HOLD = "ORDER_MANUAL_HOLD"
    RETURN_TO_SELLER = "RETURN_TO_SELLER"
    RIDER_REASSIGNMENT = "RIDER_REASSIGNMENT"
    RIDER_WARNING = "RIDER_WARNING"
    SELLER_WARNING = "SELLER_WARNING"


# Map manager-approval verbs onto the matrix red-line they violate
# (so manager_lm can't accidentally do something only Admin can do).
_MANAGER_MATRIX_MAP: dict[ManagerApprovalType, AuthorityAction] = {
    ManagerApprovalType.ORDER_MANUAL_HOLD: AuthorityAction.APPROVE_ORDER_HOLD,
    ManagerApprovalType.ORDER_HOLD_RELEASE: AuthorityAction.RELEASE_MANUAL_HOLD,
    ManagerApprovalType.FAILED_DELIVERY_DECISION:
        AuthorityAction.APPROVE_FAILED_DELIVERY_DISPUTE_DECISION,
    ManagerApprovalType.RIDER_REASSIGNMENT: AuthorityAction.RIDER_REASSIGNMENT,
    ManagerApprovalType.RETURN_TO_SELLER: AuthorityAction.APPROVE_DELIVERY_EXCEPTION,
}


class SupervisorLmRuleViolation(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


@dataclass(slots=True)
class SupervisorActor:
    actor_id: str
    role: str


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _supervisor_code() -> str:
    return f"SACT-{secrets.token_hex(4).upper()}"


def _manager_code() -> str:
    return f"MACT-{secrets.token_hex(4).upper()}"


def _approval_code() -> str:
    return f"APRV-{secrets.token_hex(4).upper()}"


def _escalation_code() -> str:
    return f"ESC-{secrets.token_hex(4).upper()}"


def _flag_code() -> str:
    return f"FLAG-{secrets.token_hex(4).upper()}"


def _review_code() -> str:
    return f"FDR-{secrets.token_hex(4).upper()}"


# ============================================================
#  Supervisor entry — record + matrix check
# ============================================================
SUPERVISOR_ROLES = frozenset({
    "supervisor",
    "fulfillment_supervisor",
    "seller_operations_supervisor",
    "customer_support_supervisor",
    "finance_supervisor",
    "admin",
    "super_admin",
})


async def record_supervisor_action(
    *,
    session: AsyncSession,
    actor: SupervisorActor,
    action_type: SupervisorActionType,
    entity_type: str,
    entity_id: str,
    reason: str | None,
    order_id: str | None = None,
    evidence_url: str | None = None,
    rule_id: UUID | None = None,
    requires_manager_approval: bool = False,
    ip_address: str | None = None,
    device_info: str | None = None,
) -> SupervisorAction:
    """Write a supervisor action with matrix + role check.

    When ``requires_manager_approval=True`` the row is created with
    ``approval_status='pending_manager'`` and the caller is expected
    to create a paired ``ManagerApproval`` row separately. Otherwise
    the row is auto-approved (supervisor-tier verb that doesn't need
    manager sign-off).
    """
    if actor.role not in SUPERVISOR_ROLES:
        raise SupervisorLmRuleViolation(
            403, "role_denied",
            f"Role {actor.role!r} cannot perform supervisor actions.",
        )
    # Matrix check — supervisor blocked from manager-only actions.
    # Use APPROVE_OPERATIONAL_EXCEPTION as the canonical guard for
    # any supervisor verb that touches manager territory.
    if requires_manager_approval and actor.role == "supervisor":
        try:
            ensure_action_allowed(
                role_name=actor.role,
                action=AuthorityAction.APPROVE_OPERATIONAL_EXCEPTION,
            )
        except AuthorityViolation as v:
            # Expected — surface it as 403 so the supervisor knows the
            # row will land as ``pending_manager``.
            raise SupervisorLmRuleViolation(
                403, "manager_approval_required",
                f"Supervisor cannot finalise this verb directly "
                f"(matrix: {v}). Submit as escalation instead.",
            ) from v

    row = SupervisorAction(
        id=uuid4(),
        action_code=_supervisor_code(),
        supervisor_id=actor.actor_id,
        supervisor_role=actor.role,
        order_id=order_id,
        entity_type=entity_type,
        entity_id=entity_id,
        action_type=action_type.value,
        reason=reason,
        evidence_url=evidence_url,
        rule_id=rule_id,
        ip_address=ip_address,
        device_info=device_info,
        approval_status=(
            "pending_manager" if requires_manager_approval
            else "auto_approved"
        ),
    )
    session.add(row)
    await session.flush()
    return row


# ============================================================
#  Manager entry — matrix-gated approval decisions
# ============================================================
MANAGER_ROLES = frozenset({
    "operations_manager_lm",
    "rider_manager",
    "admin",
    "super_admin",
})


async def record_manager_decision(
    *,
    session: AsyncSession,
    actor: SupervisorActor,
    approval_id: UUID,
    decision: str,
    decision_reason: str,
    evidence_url: str | None = None,
    ip_address: str | None = None,
    device_info: str | None = None,
) -> tuple[ManagerApproval, ManagerAction]:
    """Manager finalises a pending approval row.

    Flow:
      1. Role gate — only manager-tier roles may call this.
      2. Matrix red-line — manager_lm cannot do system / wallet /
         money / rider-only actions; the matrix map enforces this for
         the 5 verbs that overlap manager + matrix surfaces.
      3. No-self-approve — manager cannot approve a request they
         themselves submitted.
      4. Already-decided guard — pending → approved/rejected only.
      5. Audit row INSERT + approval row UPDATE in one transaction.
    """
    if actor.role not in MANAGER_ROLES:
        raise SupervisorLmRuleViolation(
            403, "role_denied",
            f"Role {actor.role!r} cannot finalise manager approvals.",
        )

    approval = await session.get(ManagerApproval, approval_id)
    if approval is None:
        raise SupervisorLmRuleViolation(404, "not_found", "Approval not found.")
    if approval.status != "pending":
        raise SupervisorLmRuleViolation(
            409, "already_decided",
            f"Approval is already in status={approval.status!r}.",
        )
    if approval.requested_by == actor.actor_id:
        raise SupervisorLmRuleViolation(
            403, "no_self_approve",
            "Manager cannot approve their own request.",
        )
    if decision not in ("approved", "rejected"):
        raise SupervisorLmRuleViolation(
            422, "invalid_decision",
            "decision must be 'approved' or 'rejected'.",
        )
    if not decision_reason:
        raise SupervisorLmRuleViolation(
            422, "reason_required",
            "Manager decision requires a reason.",
        )

    # Matrix red-line for the verb being approved
    matrix_action = _MANAGER_MATRIX_MAP.get(
        ManagerApprovalType(approval.approval_type)
    )
    if matrix_action is not None:
        try:
            ensure_action_allowed(role_name=actor.role, action=matrix_action)
        except AuthorityViolation as v:
            raise SupervisorLmRuleViolation(
                403, "authority_violation", str(v),
            ) from v

    # Write audit row first
    action = ManagerAction(
        id=uuid4(),
        action_code=_manager_code(),
        manager_id=actor.actor_id,
        entity_type="manager_approval",
        entity_id=str(approval_id),
        action_type=f"DECIDE_{approval.approval_type}",
        old_status="pending",
        new_status=decision,
        reason=decision_reason,
        evidence_url=evidence_url,
        ip_address=ip_address,
        device_info=device_info,
    )
    session.add(action)
    await session.flush()

    # Update approval row
    approval.status = decision
    approval.manager_id = actor.actor_id
    approval.manager_decision = decision_reason
    approval.evidence_url = evidence_url
    approval.decided_at = _utc_now()
    approval.decision_action_id = action.id
    await session.flush()
    return approval, action


# ============================================================
#  Builders — create approval / escalation / risk-flag rows
# ============================================================
async def create_manager_approval(
    session: AsyncSession,
    *,
    approval_type: ManagerApprovalType,
    requested_by: str,
    request_reason: str,
    order_id: str | None = None,
    escalation_id: UUID | None = None,
    priority: str = "normal",
    evidence_url: str | None = None,
    sla_due_at: datetime | None = None,
) -> ManagerApproval:
    row = ManagerApproval(
        id=uuid4(),
        approval_code=_approval_code(),
        escalation_id=escalation_id,
        order_id=order_id,
        requested_by=requested_by,
        approval_type=approval_type.value,
        priority=priority,
        sla_due_at=sla_due_at,
        request_reason=request_reason,
        evidence_url=evidence_url,
    )
    session.add(row)
    await session.flush()
    return row


async def create_order_escalation(
    session: AsyncSession,
    *,
    actor: SupervisorActor,
    subject_type: str,
    subject_id: str,
    reason: str,
    order_id: str | None = None,
    priority: str = "normal",
    evidence_url: str | None = None,
) -> OrderEscalation:
    # Pair with a SupervisorAction row for traceability
    sup_action = await record_supervisor_action(
        session=session, actor=actor,
        action_type=SupervisorActionType.CREATE_ESCALATION,
        entity_type=subject_type, entity_id=subject_id,
        order_id=order_id, reason=reason, evidence_url=evidence_url,
    )
    row = OrderEscalation(
        id=uuid4(),
        escalation_code=_escalation_code(),
        order_id=order_id,
        subject_type=subject_type,
        subject_id=subject_id,
        priority=priority,
        reason=reason,
        evidence_url=evidence_url,
        created_by=actor.actor_id,
        audit_log_id=sup_action.id,
    )
    session.add(row)
    await session.flush()
    return row


async def create_risk_flag(
    session: AsyncSession,
    *,
    actor: SupervisorActor,
    actor_type: str,
    target_actor_id: str,
    reason: str,
    order_id: str | None = None,
    evidence_url: str | None = None,
) -> RiskFlag:
    if actor_type not in ("seller", "rider", "customer"):
        raise SupervisorLmRuleViolation(
            422, "invalid_actor_type",
            "actor_type must be one of seller / rider / customer.",
        )
    sup_action = await record_supervisor_action(
        session=session, actor=actor,
        action_type=SupervisorActionType.FLAG_RISK,
        entity_type=f"{actor_type}_risk", entity_id=target_actor_id,
        order_id=order_id, reason=reason, evidence_url=evidence_url,
    )
    row = RiskFlag(
        id=uuid4(),
        flag_code=_flag_code(),
        actor_type=actor_type,
        actor_id=target_actor_id,
        order_id=order_id,
        reason=reason,
        evidence_url=evidence_url,
        created_by=actor.actor_id,
        audit_log_id=sup_action.id,
    )
    session.add(row)
    await session.flush()
    return row


async def create_failed_delivery_review(
    session: AsyncSession,
    *,
    actor: SupervisorActor,
    order_id: str,
    delivery_task_id: str,
    delivery_attempt_id: str | None,
    rider_note: str | None,
    call_attempt: str | None,
    gps_location: str | None,
    photo_evidence_url: str | None,
) -> FailedDeliveryReview:
    """Create the review row + paired supervisor action.

    Supervisor can ONLY create the review; the actual ``decision``
    requires the failed-delivery decision verb, which is manager-only
    per the matrix (`APPROVE_FAILED_DELIVERY_DISPUTE_DECISION`).
    """
    # Per spec — cannot review without prior rider proof chain
    if not (rider_note and call_attempt and gps_location):
        raise SupervisorLmRuleViolation(
            422, "rider_proof_missing",
            "Failed delivery review needs prior rider proof "
            "(call_attempt, GPS, rider_note).",
        )
    sup_action = await record_supervisor_action(
        session=session, actor=actor,
        action_type=SupervisorActionType.REVIEW_FAILED_DELIVERY,
        entity_type="delivery_task", entity_id=delivery_task_id,
        order_id=order_id, reason="failed_delivery_review_started",
    )
    row = FailedDeliveryReview(
        id=uuid4(),
        review_code=_review_code(),
        order_id=order_id,
        delivery_task_id=delivery_task_id,
        delivery_attempt_id=delivery_attempt_id,
        proof_timestamp=_utc_now(),
        rider_note=rider_note,
        call_attempt=call_attempt,
        gps_location=gps_location,
        photo_evidence_url=photo_evidence_url,
        decision_audit_log_id=sup_action.id,
    )
    session.add(row)
    await session.flush()
    return row
