"""Fulfillment-stage service helper.

Single entry point for transitioning ``orders.fulfillment_stage`` through
the 21-state graph defined in :mod:`app.modules.orders.state`.

Callers (fulfillment write endpoints, courier webhooks, rider mobile API)
should go through :func:`set_stage` rather than mutating the column
directly so that:

- The transition is validated against ``STAGE_TRANSITIONS``.
- A row is appended to ``order_fulfillment_stage_history``.
- An audit_log entry is written under the ``order.stage.*`` action prefix.

The helper assumes it is invoked inside an open ``uow.transactional()``
block — it never opens its own transaction. Caller-side audit fan-out
(WhatsApp notifications, push events, etc.) should be queued on the
outbox *after* the parent transaction commits.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import HTTPException, status as http_status

from app.modules.orders.models import Order
from app.modules.orders.repository import OrderRepository
from app.modules.orders.state import (
    OrderFulfillmentStage,
    STAGE_TRANSITIONS,
    STAGE_CANCELLABLE,
    STAGE_REQUIRED_META,
    StageProofError,
    StageTransitionError,
    assert_proof_complete,
    assert_stage_transition,
    stage_is_terminal,
)


class StageService:
    """Thin coordinator over OrderRepository for stage transitions."""

    def __init__(self, repo: OrderRepository) -> None:
        self.repo = repo

    async def set_stage(
        self,
        *,
        order_id: UUID,
        target: OrderFulfillmentStage,
        actor_id: UUID | None,
        reason: str | None = None,
        meta: dict | None = None,
        force: bool = False,
    ) -> Order:
        """Move ``order_id`` to ``target`` stage.

        Args:
            order_id: Order to transition.
            target: Desired :class:`OrderFulfillmentStage`.
            actor_id: User performing the action (None = system/cron).
            reason: Short human note attached to the history row.
            meta: Arbitrary JSON payload (rider id, courier ref, etc).
            force: Bypass the STAGE_TRANSITIONS guard. Only set this in
                emergency admin overrides — the history row records the
                override reason so the audit trail is preserved.

        Returns:
            The locked + updated Order row.

        Raises:
            HTTPException 404 if the order is missing.
            HTTPException 409 if the transition is not allowed and
            ``force`` is False.
        """
        order = await self.repo.get_locked(order_id)
        if order is None:
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail="Order not found",
            )

        current = OrderFulfillmentStage(order.fulfillment_stage)
        if current == target:
            # Idempotent no-op — still log so audit retains repeat attempts.
            await self.repo.add_stage_history(
                order_id=order.id,
                from_stage=current.value,
                to_stage=target.value,
                transitioned_by=actor_id,
                reason=(reason or "") + " [idempotent no-op]",
                meta=meta,
            )
            return order

        if not force:
            try:
                assert_stage_transition(current, target)
            except StageTransitionError as e:
                raise HTTPException(
                    status_code=http_status.HTTP_409_CONFLICT,
                    detail=str(e),
                ) from e
            # Required proof check — only enforced when not force-mode.
            try:
                assert_proof_complete(target, meta)
            except StageProofError as e:
                raise HTTPException(
                    status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=str(e),
                ) from e
            # DELIVERED-specific: COD/payment workflow.
            # If the order was placed COD, the rider MUST attach either
            # cod_collected_minor (paisa) OR cod_waived=true + reason.
            # Online-paid orders bypass this branch — their payment was
            # already settled at PAYMENT_VERIFIED.
            if target is OrderFulfillmentStage.DELIVERED \
                    and getattr(order, "payment_method", "") == "cod":
                payload = meta or {}
                has_collected = isinstance(
                    payload.get("cod_collected_minor"), int,
                ) and payload["cod_collected_minor"] > 0
                has_waiver = bool(payload.get("cod_waived")) and bool(
                    payload.get("cod_waive_reason"),
                )
                if not (has_collected or has_waiver):
                    raise HTTPException(
                        status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
                        detail=(
                            "DELIVERED on COD order requires "
                            "cod_collected_minor (paisa, >0) OR "
                            "cod_waived=true + cod_waive_reason"
                        ),
                    )

        order.fulfillment_stage = target.value
        await self.repo.session.flush()
        await self.repo.add_stage_history(
            order_id=order.id,
            from_stage=current.value,
            to_stage=target.value,
            transitioned_by=actor_id,
            reason=reason if not force else f"[FORCE] {reason or ''}",
            meta=meta,
        )
        # Auto-fire customer notification for 6 stages. Late-import to
        # avoid circular dependency on app.modules.fulfillment. Try-
        # except so a notification crash NEVER blocks the stage change.
        STAGE_TO_EVENT = {
            "READY_TO_SHIP":      "order_ready_for_pickup",
            "RIDER_ASSIGNED":     "rider_assigned",
            "OUT_FOR_DELIVERY":   "out_for_delivery",
            "FAILED_DELIVERY":    "delivery_attempt_failed",
            "RESCHEDULED":        "delivery_rescheduled",
            "RETURNING_TO_HUB":   "return_initiated",
        }
        event_key = STAGE_TO_EVENT.get(target.value)
        if event_key:
            try:
                from app.modules.fulfillment.marketplace_api import (
                    _dispatch_customer_notification,
                )
                # Build minimal context — actual templates can use any
                # of {order_code, sla_hours, attempt_no, new_sla, ...}
                # The dispatcher leaves missing keys as [missing_ctx:k]
                # rather than crashing.
                ctx: dict[str, Any] = {}
                if meta:
                    # Pass through scan_id, rider_id, sla, attempt_no etc
                    # so the template renders them when present.
                    for k in ("sla_hours", "attempt_no", "failure_reason",
                              "new_sla", "tracking_url", "delay_minutes",
                              "amount_taka"):
                        if k in meta:
                            ctx[k] = meta[k]
                actor_proxy = type(
                    "Actor", (),
                    {"user_id": actor_id, "permissions": set()},
                )()
                await _dispatch_customer_notification(
                    self.repo.session,
                    event_key=event_key,
                    order_id=order.id,
                    context=ctx,
                    locale="bn",
                    actor=actor_proxy,
                )
            except Exception:  # noqa: BLE001
                # Notification failure NEVER blocks the stage change.
                pass
        return order

    @staticmethod
    def can_cancel_from(stage: OrderFulfillmentStage) -> bool:
        return stage in STAGE_CANCELLABLE

    @staticmethod
    def is_terminal(stage: OrderFulfillmentStage) -> bool:
        return stage_is_terminal(stage)

    @staticmethod
    def allowed_next(stage: OrderFulfillmentStage) -> list[str]:
        """List of next stages valid from ``stage`` (UI dropdown helper).

        Always includes CANCELLED when current stage is in
        ``STAGE_CANCELLABLE``.
        """
        out: set[OrderFulfillmentStage] = set(
            STAGE_TRANSITIONS.get(stage, frozenset()),
        )
        if stage in STAGE_CANCELLABLE:
            out.add(OrderFulfillmentStage.CANCELLED)
        return sorted(s.value for s in out)
