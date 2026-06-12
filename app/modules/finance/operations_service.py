"""Finance Manager operational service — rules engine + audit-first writes.

Every state-changing Finance Manager call funnels through
``execute_finance_action`` which:

  1. Maps the verb to the matching ``AuthorityAction`` red-line and
     calls ``ensure_action_allowed`` (Phase A matrix).
  2. Enforces the package-level pre-conditions (allowed actor role,
     reason / evidence / reference / amount / currency fields,
     no-self-approve, no-double-approve).
  3. Writes the audit log row FIRST (single insert; no flush retry).
  4. Hands control back to the caller to perform the actual state
     mutation (so we can mark ``decision_audit_log_id`` on the
     refund / settlement / payout row).

The split intentionally puts audit BEFORE business writes — even if
the workflow row update fails, the audit captures the attempted
action + actor (auditor can still see "Finance Manager X tried to
approve refund Y at T").
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import date, datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.iam.authority_matrix import (
    AuthorityAction,
    AuthorityViolation,
    ensure_action_allowed,
)
from app.modules.finance.operations_actions import (
    EVIDENCE_REQUIRED_ACTIONS,
    MONEY_MOVEMENT_ACTIONS,
    NO_SELF_APPROVE_ACTIONS,
    REFERENCE_REQUIRED_ACTIONS,
    VIEW_ACTIONS,
    FinanceAction,
)
from app.modules.finance.operations_models import (
    FinanceAuditLog,
    FinanceCodSettlement,
    FinanceRefundApproval,
    FinanceRiderPayoutBatch,
    FinanceSellerPayoutBatch,
    FinanceWalletLedgerAdjustment,
)


class FinanceRuleViolation(Exception):
    """Raised when a Finance Manager call fails a rule gate.

    Carries the status code the API layer should surface (403 for
    authority / RBAC, 422 for missing fields / bad payload). Service
    callers convert this to ``HTTPException`` at the router boundary.
    """

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


# Map each operational action onto the matching matrix red-line. View
# actions don't need a matrix check (read-only). Money / state actions
# point at the closest matrix verb; if a perfect 1:1 doesn't exist the
# closest neighbour is used (e.g. ``HOLD_SELLER_PAYOUT`` neighbours
# ``RELEASE_SELLER_PAYOUT`` — both are payout-state changes).
_MATRIX_ACTION_MAP: dict[FinanceAction, AuthorityAction] = {
    FinanceAction.APPROVE_REFUND: AuthorityAction.APPROVE_REFUND,
    FinanceAction.SETTLE_COD: AuthorityAction.SETTLE_COD,
    FinanceAction.VERIFY_RIDER_COD_SETTLEMENT: AuthorityAction.SETTLE_COD,
    FinanceAction.RECONCILE_COD_MISMATCH: AuthorityAction.ADJUST_COD,
    FinanceAction.APPROVE_SELLER_PAYOUT_BATCH: AuthorityAction.RELEASE_SELLER_PAYOUT,
    FinanceAction.RELEASE_SELLER_PAYOUT: AuthorityAction.RELEASE_SELLER_PAYOUT,
    FinanceAction.HOLD_SELLER_PAYOUT: AuthorityAction.RELEASE_SELLER_PAYOUT,
    FinanceAction.RELEASE_SELLER_PAYOUT_HOLD: AuthorityAction.RELEASE_SELLER_PAYOUT,
    FinanceAction.APPROVE_RIDER_PAYOUT_BATCH: AuthorityAction.RELEASE_RIDER_PAYOUT,
    FinanceAction.RELEASE_RIDER_PAYOUT: AuthorityAction.RELEASE_RIDER_PAYOUT,
    FinanceAction.HOLD_RIDER_PAYOUT_BATCH: AuthorityAction.RELEASE_RIDER_PAYOUT,
    FinanceAction.APPROVE_WALLET_ADJUSTMENT: AuthorityAction.EDIT_WALLET,
}


@dataclass(slots=True)
class FinanceActor:
    """The acting principal — extracted from the request session."""

    actor_id: str
    role: str


@dataclass(slots=True)
class FinanceActionPayload:
    """Everything the matrix needs to validate a Finance action.

    Keeping it a plain dataclass (not a Pydantic model) means we can
    construct one inside the router from disparate sources (query +
    body + path param) without serialisation overhead.
    """

    entity_type: str
    entity_id: str
    new_status: str
    reason: str
    requested_by: str | None = None
    order_id: str | None = None
    old_status: str | None = None
    evidence_url: str | None = None
    amount_minor: int | None = None
    currency: str = "BDT"
    reference_id: str | None = None
    ip_address: str | None = None
    device_info: str | None = None
    metadata: dict | None = None


# ============================================================
#  Helpers
# ============================================================
def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _audit_code() -> str:
    """Human-readable audit code stamped on each row.

    Pattern: ``FACT-<8 hex>`` — auditor can paste it into a search
    bar to locate the row from a Slack ping or printed report.
    """
    return f"FACT-{secrets.token_hex(4).upper()}"


def _validate_payload(
    action: FinanceAction, payload: FinanceActionPayload, actor: FinanceActor,
) -> None:
    """Raise ``FinanceRuleViolation`` when the payload misses a
    field the action class requires. Mirrors the package's domain
    classification (REASON / EVIDENCE / REFERENCE / MONEY).
    """
    if action not in VIEW_ACTIONS and not payload.reason:
        raise FinanceRuleViolation(
            422, "reason_required",
            "Finance Manager action requires a reason.",
        )
    if action in EVIDENCE_REQUIRED_ACTIONS and not payload.evidence_url:
        raise FinanceRuleViolation(
            422, "evidence_required",
            f"Action {action.value} requires an evidence URL.",
        )
    if action in REFERENCE_REQUIRED_ACTIONS and not payload.reference_id:
        raise FinanceRuleViolation(
            422, "reference_required",
            f"Action {action.value} requires a reference ID.",
        )
    if action in MONEY_MOVEMENT_ACTIONS:
        if payload.amount_minor is None or payload.amount_minor <= 0:
            raise FinanceRuleViolation(
                422, "amount_required",
                "Money-movement action requires a positive BDT amount.",
            )
        if payload.currency != "BDT":
            raise FinanceRuleViolation(
                422, "currency_invalid",
                "Finance currency must be BDT.",
            )
    if (
        action in NO_SELF_APPROVE_ACTIONS
        and payload.requested_by is not None
        and payload.requested_by == actor.actor_id
    ):
        raise FinanceRuleViolation(
            403, "no_self_approve",
            "Finance Manager cannot self-approve their own request.",
        )


async def _no_double_approve(
    session: AsyncSession, action: FinanceAction, entity_id: str,
) -> None:
    """Prevent the same refund / payout being approved twice.

    Reads the audit log for any prior approval-class action on the
    same entity. Cheap (indexed on ``entity_type+entity_id``) and run
    only for approval verbs.
    """
    if action not in (
        FinanceAction.APPROVE_REFUND,
        FinanceAction.APPROVE_SELLER_PAYOUT_BATCH,
        FinanceAction.APPROVE_RIDER_PAYOUT_BATCH,
        FinanceAction.APPROVE_WALLET_ADJUSTMENT,
        FinanceAction.APPROVE_CUSTOMER_COMPENSATION,
        FinanceAction.APPROVE_SELLER_CHARGEBACK,
    ):
        return
    prior = await session.execute(
        select(FinanceAuditLog.id).where(
            FinanceAuditLog.entity_id == entity_id,
            FinanceAuditLog.action == action.value,
        ).limit(1),
    )
    if prior.scalar_one_or_none() is not None:
        raise FinanceRuleViolation(
            409, "already_approved",
            f"{action.value} has already been recorded for entity {entity_id}.",
        )


# ============================================================
#  Public entry point
# ============================================================
async def execute_finance_action(
    *,
    session: AsyncSession,
    actor: FinanceActor,
    action: FinanceAction,
    payload: FinanceActionPayload,
) -> FinanceAuditLog:
    """Validate + audit-log a Finance Manager action.

    Order of checks (cheapest first → expensive DB read last):
      1. Matrix red-line for the actor's role.
      2. Actor role gate — only ``finance_manager`` / ``admin`` /
         ``super_admin`` may execute money-movement verbs (read verbs
         pass through any role with the perm).
      3. Payload gate (reason / evidence / reference / amount / self-
         approve).
      4. Double-approve gate (DB read).
      5. Audit log write.

    Caller is responsible for the downstream workflow row update
    (refund.status='approved', payout.released_at=now, etc.) — pass
    the returned ``FinanceAuditLog.id`` as that row's
    ``decision_audit_log_id`` so the audit trail is bi-directional.
    """
    matrix_action = _MATRIX_ACTION_MAP.get(action)
    if matrix_action is not None:
        try:
            ensure_action_allowed(role_name=actor.role, action=matrix_action)
        except AuthorityViolation as v:
            raise FinanceRuleViolation(
                403, "authority_violation", str(v),
            ) from v

    # Read verbs: allowed for any role that holds the RBAC perm. The
    # role gate below applies only to state-changing actions.
    if action not in VIEW_ACTIONS:
        if actor.role not in ("finance_manager", "admin", "super_admin"):
            raise FinanceRuleViolation(
                403, "role_denied",
                f"Role {actor.role!r} cannot execute Finance Manager "
                f"action {action.value!r}.",
            )

    _validate_payload(action, payload, actor)
    await _no_double_approve(session, action, payload.entity_id)

    row = FinanceAuditLog(
        id=uuid4(),
        audit_code=_audit_code(),
        actor_id=actor.actor_id,
        actor_role=actor.role,
        action=action.value,
        entity_type=payload.entity_type,
        entity_id=payload.entity_id,
        order_id=payload.order_id,
        old_status=payload.old_status,
        new_status=payload.new_status,
        reason=payload.reason or "",
        evidence_url=payload.evidence_url,
        amount_minor=payload.amount_minor,
        currency=payload.currency,
        reference_id=payload.reference_id,
        ip_address=payload.ip_address,
        device_info=payload.device_info,
        metadata_json=payload.metadata or {},
    )
    session.add(row)
    await session.flush()
    return row


# ============================================================
#  Workflow row helpers (called AFTER execute_finance_action)
# ============================================================
async def apply_refund_decision(
    session: AsyncSession,
    *,
    refund_id: UUID,
    decided_by: str,
    audit_log_id: UUID,
    new_status: str,
    note: str | None = None,
) -> FinanceRefundApproval:
    """Flip the refund row's status field after the audit log lands."""
    row = await session.get(FinanceRefundApproval, refund_id)
    if row is None:
        raise FinanceRuleViolation(404, "not_found", "Refund not found.")
    if row.status not in ("pending", "held"):
        raise FinanceRuleViolation(
            409, "refund_already_decided",
            f"Refund is already in status={row.status!r}.",
        )
    row.status = new_status
    row.decided_by = decided_by
    row.decided_at = _utc_now()
    row.decision_audit_log_id = audit_log_id
    if note:
        row.note = note
    await session.flush()
    return row


async def apply_cod_settlement_decision(
    session: AsyncSession,
    *,
    settlement_id: UUID,
    verified_by: str,
    audit_log_id: UUID,
    new_status: str,
    note: str | None = None,
) -> FinanceCodSettlement:
    row = await session.get(FinanceCodSettlement, settlement_id)
    if row is None:
        raise FinanceRuleViolation(404, "not_found", "Settlement not found.")
    if row.status not in ("pending", "reconciled"):
        raise FinanceRuleViolation(
            409, "settlement_already_decided",
            f"Settlement is already in status={row.status!r}.",
        )
    row.status = new_status
    row.verified_by = verified_by
    row.verified_at = _utc_now()
    row.decision_audit_log_id = audit_log_id
    if note:
        row.note = note
    await session.flush()
    return row


async def apply_seller_payout_decision(
    session: AsyncSession,
    *,
    batch_id: UUID,
    actor_id: str,
    audit_log_id: UUID,
    new_status: str,
    hold_reason: str | None = None,
) -> FinanceSellerPayoutBatch:
    row = await session.get(FinanceSellerPayoutBatch, batch_id)
    if row is None:
        raise FinanceRuleViolation(404, "not_found", "Batch not found.")
    now = _utc_now()
    row.status = new_status
    row.decision_audit_log_id = audit_log_id
    if new_status == "approved":
        row.approved_by = actor_id
        row.approved_at = now
    elif new_status == "released":
        row.released_by = actor_id
        row.released_at = now
    elif new_status == "held":
        row.held_by = actor_id
        row.held_at = now
        row.hold_reason = hold_reason
    await session.flush()
    return row


async def apply_rider_payout_decision(
    session: AsyncSession,
    *,
    batch_id: UUID,
    actor_id: str,
    audit_log_id: UUID,
    new_status: str,
    hold_reason: str | None = None,
) -> FinanceRiderPayoutBatch:
    row = await session.get(FinanceRiderPayoutBatch, batch_id)
    if row is None:
        raise FinanceRuleViolation(404, "not_found", "Batch not found.")
    now = _utc_now()
    row.status = new_status
    row.decision_audit_log_id = audit_log_id
    if new_status == "approved":
        row.approved_by = actor_id
        row.approved_at = now
    elif new_status == "released":
        row.released_by = actor_id
        row.released_at = now
    elif new_status == "verified":
        row.verified_by = actor_id
        row.verified_at = now
    elif new_status == "held":
        row.held_by = actor_id
        row.held_at = now
        row.hold_reason = hold_reason
    await session.flush()
    return row


async def apply_wallet_adjustment_decision(
    session: AsyncSession,
    *,
    adjustment_id: UUID,
    approved_by: str,
    audit_log_id: UUID,
    new_status: str,
) -> FinanceWalletLedgerAdjustment:
    row = await session.get(FinanceWalletLedgerAdjustment, adjustment_id)
    if row is None:
        raise FinanceRuleViolation(404, "not_found", "Adjustment not found.")
    if row.status != "pending":
        raise FinanceRuleViolation(
            409, "wallet_adj_already_decided",
            f"Adjustment is already in status={row.status!r}.",
        )
    row.status = new_status
    row.approved_by = approved_by
    row.approved_at = _utc_now()
    row.decision_audit_log_id = audit_log_id
    await session.flush()
    return row
