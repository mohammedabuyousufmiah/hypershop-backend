"""Mother-QR scan service — applies the canonical state machine.

The single public entry point ``apply_scan`` validates the scan
against the ``TRANSITIONS`` table, runs the matrix red-line for
rider-only finality, writes the scan_event row, then flips the
mother_qr_items.status field — in one transaction. Failure at any
step rolls back BOTH writes (audit + status), so the system never
ends up with a half-applied transition.

Special guards (in addition to TRANSITIONS):
  * CONFIRM_DELIVERED / START_DELIVERY hit the Phase A matrix
    (``AuthorityAction.CONFIRM_DELIVERED``) — even if a future role
    gets added to ``_RIDER_ROLE``, the matrix still blocks Admin
    from confirming a delivery.
  * Damaged stock cannot return to sellable — no transition from
    DAMAGED / DAMAGED_AT_RECEIVING back to AVAILABLE (enforced by
    the absence of such a row in TRANSITIONS).
  * Append-only scan_events — the service never UPDATEs / DELETEs
    a scan_event row; corrections write a reversal event.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.iam.authority_matrix import (
    AuthorityAction,
    AuthorityViolation,
    ensure_action_allowed,
)
from app.modules.mother_qr.models import (
    MotherQrItem,
    MotherQrScanEvent,
    WarehouseLocation,
)
from app.modules.mother_qr.transitions import (
    TRANSITIONS,
    MotherQrStatus,
    ScanAction,
)


class MotherQrRuleViolation(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


@dataclass(slots=True)
class ScanActor:
    actor_id: str
    role: str


def _event_code() -> str:
    return f"SCN-{secrets.token_hex(4).upper()}"


# Map rider-only scan actions to the matrix verbs so even Admin /
# Super Admin cannot bypass via a future role addition.
_MATRIX_FOR_SCAN: dict[ScanAction, AuthorityAction] = {
    ScanAction.START_DELIVERY: AuthorityAction.START_OUT_FOR_DELIVERY,
    ScanAction.CONFIRM_DELIVERED: AuthorityAction.CONFIRM_DELIVERED,
}


async def _require_registered_location(
    session: AsyncSession, *, qr_code: str, warehouse_id: str,
) -> WarehouseLocation:
    row = (await session.execute(
        select(WarehouseLocation).where(
            WarehouseLocation.qr_code == qr_code,
            WarehouseLocation.warehouse_id == warehouse_id,
            WarehouseLocation.is_active.is_(True),
        ).limit(1),
    )).scalar_one_or_none()
    if row is None:
        raise MotherQrRuleViolation(
            422, "location_unregistered",
            f"Shelf/bin QR {qr_code!r} is not registered for warehouse "
            f"{warehouse_id!r}.",
        )
    return row


async def gate_in(
    session: AsyncSession,
    *,
    actor: ScanActor,
    mother_qr: str,
    sku: str,
    product_name: str,
    warehouse_id: str,
    location_code: str = "GATE",
    supplier_id: str | None = None,
    purchase_order_id: str | None = None,
    batch_no: str | None = None,
    serial_no: str | None = None,
    expiry_date: str | None = None,
) -> MotherQrItem:
    """Create a brand-new mother_qr_items row in GATE_IN status.

    Mirrors the package's gate-in flow. Pair with an immediate
    ``apply_scan(RECEIVE)`` to move it to RECEIVED.
    """
    # Receiving-role gate
    from app.modules.mother_qr.transitions import _RECEIVING_ROLES  # noqa
    if actor.role not in _RECEIVING_ROLES and actor.role not in (
        "admin", "super_admin",
    ):
        raise MotherQrRuleViolation(
            403, "role_denied",
            f"Role {actor.role!r} cannot create Mother-QR gate-in entries.",
        )
    item = MotherQrItem(
        id=uuid4(),
        mother_qr=mother_qr,
        sku=sku,
        product_name=product_name,
        quantity=1,
        status=MotherQrStatus.GATE_IN.value,
        location_code=location_code,
        warehouse_id=warehouse_id,
        received_by=actor.actor_id,
        supplier_id=supplier_id,
        purchase_order_id=purchase_order_id,
        batch_no=batch_no,
        serial_no=serial_no,
        expiry_date=expiry_date,
    )
    session.add(item)
    # First scan_event row — captures the gate-in scan itself.
    event = MotherQrScanEvent(
        id=uuid4(),
        event_code=_event_code(),
        mother_qr=mother_qr,
        action=ScanAction.GATE_IN.value,
        old_status=None,
        new_status=MotherQrStatus.GATE_IN.value,
        actor_id=actor.actor_id,
        actor_role=actor.role,
        location_code=location_code,
        scan_type="gate_in",
        result="ok",
    )
    session.add(event)
    await session.flush()
    return item


async def apply_scan(
    session: AsyncSession,
    *,
    actor: ScanActor,
    mother_qr: str,
    action: ScanAction,
    location_code: str,
    scanned_qr: str | None = None,
    order_id: str | None = None,
    device_id: str | None = None,
    reason: str | None = None,
    evidence_url: str | None = None,
) -> tuple[MotherQrItem, MotherQrScanEvent]:
    """Execute one scan-driven transition.

    Order of checks (cheapest first):
      1. Look up TRANSITIONS row — unknown action → 422
      2. Matrix red-line (rider-only verbs) → 403
      3. Role gate from TRANSITIONS → 403
      4. Mother-QR exists → 404
      5. Current status in allowed source set → 409
      6. Shelf scan: registered location → 422
      7. Scan event INSERT + status UPDATE in one transaction
    """
    rule = TRANSITIONS.get(action)
    if rule is None:
        raise MotherQrRuleViolation(
            422, "unknown_action",
            f"ScanAction {action.value!r} has no transition rule.",
        )
    sources, target, allowed_roles = rule

    matrix_action = _MATRIX_FOR_SCAN.get(action)
    if matrix_action is not None:
        try:
            ensure_action_allowed(role_name=actor.role, action=matrix_action)
        except AuthorityViolation as v:
            raise MotherQrRuleViolation(
                403, "authority_violation", str(v),
            ) from v

    if actor.role not in allowed_roles:
        raise MotherQrRuleViolation(
            403, "role_denied",
            f"Role {actor.role!r} cannot perform scan action "
            f"{action.value!r}. Required: {sorted(allowed_roles)}.",
        )

    item = (await session.execute(
        select(MotherQrItem).where(MotherQrItem.mother_qr == mother_qr),
    )).scalar_one_or_none()
    if item is None:
        raise MotherQrRuleViolation(
            404, "mother_qr_not_found",
            f"Mother-QR {mother_qr!r} not registered.",
        )

    current = MotherQrStatus(item.status)
    if current not in sources:
        raise MotherQrRuleViolation(
            409, "invalid_transition",
            f"Cannot apply {action.value!r} from current status "
            f"{current.value!r}. Allowed sources: "
            f"{sorted(s.value for s in sources)}.",
        )

    # Shelf scan requires registered location QR
    if action == ScanAction.SHELF and scanned_qr:
        await _require_registered_location(
            session, qr_code=scanned_qr, warehouse_id=item.warehouse_id,
        )

    # Audit-first write
    event = MotherQrScanEvent(
        id=uuid4(),
        event_code=_event_code(),
        mother_qr=mother_qr,
        action=action.value,
        old_status=current.value,
        new_status=target.value,
        actor_id=actor.actor_id,
        actor_role=actor.role,
        location_code=location_code,
        scanned_qr=scanned_qr,
        order_id=order_id,
        device_id=device_id,
        result="ok",
        reason=reason,
        evidence_url=evidence_url,
    )
    session.add(event)
    await session.flush()

    item.status = target.value
    item.location_code = location_code
    if order_id and not item.order_id:
        item.order_id = order_id
    await session.flush()
    return item, event
