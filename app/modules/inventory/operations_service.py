"""Inventory Manager operational service — same validation pattern
as ``finance.operations_service`` (Phase B). Every state-changing
call funnels through ``execute_inventory_action`` which:

  1. Maps the verb to the matching ``AuthorityAction`` red-line
     (matrix from Phase A).
  2. Enforces the actor role (``inventory_manager / admin / super_admin``
     for state-changing verbs).
  3. Enforces reason / evidence / reference / no-self-approve gates.
  4. Writes the audit log row FIRST.
  5. Hands the audit row's id back so the caller can stamp it on the
     workflow row's ``decision_audit_log_id`` field.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.iam.authority_matrix import (
    AuthorityAction,
    AuthorityViolation,
    ensure_action_allowed,
)
from app.modules.inventory.operations_actions import (
    EVIDENCE_REQUIRED_ACTIONS,
    NO_SELF_APPROVE_ACTIONS,
    REFERENCE_REQUIRED_ACTIONS,
    VIEW_ACTIONS,
    InventoryAction,
)
from app.modules.inventory.operations_models import (
    DamagedLostInventory,
    InventoryAuditLog,
    ReturnStockReview,
    StockAdjustmentRequest,
)


class InventoryRuleViolation(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


# Map each inventory action onto the matching matrix red-line. The
# matrix verbs cover the high-risk surface (CHANGE_STOCK_STATUS /
# RETURN_TO_STOCK_DECISION). All other verbs pass through with no
# matrix red-line because they're stock-truth actions that the
# inventory_manager role is explicitly granted.
_MATRIX_ACTION_MAP: dict[InventoryAction, AuthorityAction] = {
    InventoryAction.BLOCK_UNAVAILABLE_STOCK_FROM_SELLING:
        AuthorityAction.CHANGE_STOCK_STATUS,
    InventoryAction.UNBLOCK_STOCK:
        AuthorityAction.CHANGE_STOCK_STATUS,
    InventoryAction.APPROVE_RETURN_TO_STOCK:
        AuthorityAction.RETURN_TO_STOCK_DECISION,
    InventoryAction.REJECT_RETURN_TO_STOCK:
        AuthorityAction.RETURN_TO_STOCK_DECISION,
}


@dataclass(slots=True)
class InventoryActor:
    actor_id: str
    role: str


@dataclass(slots=True)
class InventoryActionPayload:
    entity_type: str
    entity_id: str
    new_status: str
    reason: str
    requested_by: str | None = None
    sku: str | None = None
    warehouse_id: str | None = None
    qty_before: int | None = None
    qty_after: int | None = None
    qty_delta: int | None = None
    old_status: str | None = None
    evidence_url: str | None = None
    reference_id: str | None = None
    ip_address: str | None = None
    device_info: str | None = None
    metadata: dict | None = None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _audit_code() -> str:
    return f"INVA-{secrets.token_hex(4).upper()}"


def _validate_payload(
    action: InventoryAction,
    payload: InventoryActionPayload,
    actor: InventoryActor,
) -> None:
    if action not in VIEW_ACTIONS and not payload.reason:
        raise InventoryRuleViolation(
            422, "reason_required",
            "Inventory Manager action requires a reason.",
        )
    if action in EVIDENCE_REQUIRED_ACTIONS and not payload.evidence_url:
        raise InventoryRuleViolation(
            422, "evidence_required",
            f"Action {action.value} requires an evidence URL.",
        )
    if action in REFERENCE_REQUIRED_ACTIONS and not payload.reference_id:
        raise InventoryRuleViolation(
            422, "reference_required",
            f"Action {action.value} requires a reference ID.",
        )
    if (
        action in NO_SELF_APPROVE_ACTIONS
        and payload.requested_by is not None
        and payload.requested_by == actor.actor_id
    ):
        raise InventoryRuleViolation(
            403, "no_self_approve",
            "Inventory Manager cannot self-approve their own request.",
        )


async def execute_inventory_action(
    *,
    session: AsyncSession,
    actor: InventoryActor,
    action: InventoryAction,
    payload: InventoryActionPayload,
) -> InventoryAuditLog:
    """Validate + audit-log an Inventory Manager action.

    See ``finance.operations_service.execute_finance_action`` for the
    matching cascade docstring — this is the inventory-domain twin.
    """
    matrix_action = _MATRIX_ACTION_MAP.get(action)
    if matrix_action is not None:
        try:
            ensure_action_allowed(role_name=actor.role, action=matrix_action)
        except AuthorityViolation as v:
            raise InventoryRuleViolation(
                403, "authority_violation", str(v),
            ) from v

    if action not in VIEW_ACTIONS:
        if actor.role not in ("inventory_manager", "admin", "super_admin"):
            raise InventoryRuleViolation(
                403, "role_denied",
                f"Role {actor.role!r} cannot execute Inventory action "
                f"{action.value!r}.",
            )

    _validate_payload(action, payload, actor)

    row = InventoryAuditLog(
        id=uuid4(),
        audit_code=_audit_code(),
        actor_id=actor.actor_id,
        actor_role=actor.role,
        action=action.value,
        entity_type=payload.entity_type,
        entity_id=payload.entity_id,
        sku=payload.sku,
        warehouse_id=payload.warehouse_id,
        qty_before=payload.qty_before,
        qty_after=payload.qty_after,
        qty_delta=payload.qty_delta,
        old_status=payload.old_status,
        new_status=payload.new_status,
        reason=payload.reason or "",
        evidence_url=payload.evidence_url,
        reference_id=payload.reference_id,
        ip_address=payload.ip_address,
        device_info=payload.device_info,
        metadata_json=payload.metadata or {},
    )
    session.add(row)
    await session.flush()
    return row


# ============================================================
#  Workflow row helpers (called AFTER execute_inventory_action)
# ============================================================
async def apply_stock_adjustment_decision(
    session: AsyncSession,
    *,
    request_id: UUID,
    decided_by: str,
    audit_log_id: UUID,
    new_status: str,
    decision_note: str | None = None,
) -> StockAdjustmentRequest:
    row = await session.get(StockAdjustmentRequest, request_id)
    if row is None:
        raise InventoryRuleViolation(404, "not_found",
                                      "Adjustment request not found.")
    if row.status != "pending":
        raise InventoryRuleViolation(
            409, "already_decided",
            f"Request is already in status={row.status!r}.",
        )
    row.status = new_status
    row.decided_by = decided_by
    row.decided_at = _utc_now()
    row.decision_audit_log_id = audit_log_id
    if decision_note:
        row.decision_note = decision_note
    await session.flush()
    return row


async def apply_return_review_decision(
    session: AsyncSession,
    *,
    review_id: UUID,
    decided_by: str,
    audit_log_id: UUID,
    new_status: str,
    decision_note: str | None = None,
) -> ReturnStockReview:
    row = await session.get(ReturnStockReview, review_id)
    if row is None:
        raise InventoryRuleViolation(404, "not_found", "Return review not found.")
    if row.status != "pending":
        raise InventoryRuleViolation(
            409, "already_decided",
            f"Review is already in status={row.status!r}.",
        )
    # Inventory Manager cannot approve return-to-stock unless QC has
    # passed AND the Mother-QR match was recorded (per spec).
    if new_status == "approved":
        if row.qc_status != "qc_passed":
            raise InventoryRuleViolation(
                422, "qc_not_passed",
                "Return-to-stock requires QC pass before approval.",
            )
        if row.mother_qr_match is not True:
            raise InventoryRuleViolation(
                422, "mother_qr_mismatch",
                "Return-to-stock requires Mother-QR match confirmation.",
            )
    row.status = new_status
    row.decided_by = decided_by
    row.decided_at = _utc_now()
    row.decision_audit_log_id = audit_log_id
    if decision_note:
        row.decision_note = decision_note
    await session.flush()
    return row


async def apply_damaged_lost_confirmation(
    session: AsyncSession,
    *,
    incident_id: UUID,
    confirmed_by: str,
    audit_log_id: UUID,
    new_status: str,
) -> DamagedLostInventory:
    row = await session.get(DamagedLostInventory, incident_id)
    if row is None:
        raise InventoryRuleViolation(404, "not_found", "Incident not found.")
    if row.status not in ("pending",):
        raise InventoryRuleViolation(
            409, "already_decided",
            f"Incident is already in status={row.status!r}.",
        )
    row.status = new_status
    row.confirmed_by = confirmed_by
    row.confirmed_at = _utc_now()
    row.decision_audit_log_id = audit_log_id
    await session.flush()
    return row
