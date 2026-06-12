"""Unit tests for push handler templates + registration.

Focused on the dispatcher-level contract:
  - every event in _PUSH_TEMPLATES has a registered handler
  - every template formats cleanly with the standard order context
  - _dispatch_for_event is a no-op when the payload is missing order_id
    (defensive — protects against producer regressions)
  - _dispatch_for_event is a no-op for an unknown event type (the
    registry should never call us with one, but belt-and-braces)

DB-touching tests (real Order load, real notification fanout) are out
of scope here and are covered by the integration smoke test that
exercises the full outbox worker.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.core.events.dispatcher import _handlers
from app.core.events.models import OutboxMessage, OutboxStatus
from app.modules.push.handlers import (
    _PUSH_TEMPLATES,
    _dispatch_for_event,
    _order_id,
)


# Standard format context — must match what _dispatch_for_event builds
# in production. If a future template uses a new placeholder, this dict
# must grow with it.
_FMT_CTX = {
    "order_code": "HS-2026-000123",
    "customer_name": "Yousuf Miah",
    "currency": "BDT",
    "grand_total": "1450.00",
}


# ---------------- Template coverage ----------------


def test_all_templates_format_cleanly() -> None:
    """Every (title, body) pair must `.format(**ctx)` without KeyError.

    Catches a typo like ``{order_codee}`` at unit-test time rather than
    at fan-out time when a real customer is waiting on a push.
    """
    for event_type, (title, body) in _PUSH_TEMPLATES.items():
        try:
            rendered_title = title.format(**_FMT_CTX)
            rendered_body = body.format(**_FMT_CTX)
        except KeyError as e:  # pragma: no cover — diagnostic
            pytest.fail(f"{event_type}: missing placeholder {e}")
        assert rendered_title, f"{event_type}: empty title"
        assert rendered_body, f"{event_type}: empty body"
        # Order code should land in the body — the deep-link is on
        # `data`, but customers reading the lock-screen need the code.
        assert "HS-2026-000123" in rendered_body, (
            f"{event_type}: body lost the order_code substitution"
        )


# ---------------- Coverage of the events the audit doc claims wired ----------------


@pytest.mark.parametrize(
    "event_type",
    [
        # Pre-existing
        "orders.order.payment_confirmed",
        "orders.order.approved",
        "orders.order.dispatched",
        "orders.order.completed",
        "orders.order.cancelled",
        # Turn 35 additions
        "orders.order.created",
        "payment.failed",
        "payment.cancelled",
        # Turn 36 P1 closures
        "payment.refund.succeeded",
        "deliveries.delivery.failed",
    ],
)
def test_event_is_registered(event_type: str) -> None:
    """The audit doc lists 12 wired events. The handler module must
    register a dispatcher entry for each of them at import time."""
    assert event_type in _handlers, f"{event_type} not in _handlers"
    assert _dispatch_for_event in _handlers[event_type], (
        f"{event_type} registered but not pointing at _dispatch_for_event"
    )


def test_template_count_matches_audit_doc() -> None:
    """Guard against silent template additions/removals diverging from
    the audit doc. If you add a template, also update
    docs/NOTIFICATIONS_AUDIT.md."""
    assert len(_PUSH_TEMPLATES) == 10, (
        f"expected 10 templates per audit doc; found {len(_PUSH_TEMPLATES)}"
    )


# ---------------- _order_id helper ----------------


def test_order_id_returns_uuid_for_valid_string() -> None:
    expected = uuid4()
    assert _order_id({"order_id": str(expected)}) == expected


def test_order_id_returns_none_for_missing() -> None:
    assert _order_id({}) is None
    assert _order_id({"order_id": None}) is None
    assert _order_id({"order_id": ""}) is None


def test_order_id_returns_none_for_invalid() -> None:
    assert _order_id({"order_id": "not-a-uuid"}) is None


# ---------------- _dispatch_for_event early-exit branches ----------------


def _msg(event_type: str, payload: dict) -> OutboxMessage:
    return OutboxMessage(
        type=event_type,
        payload=payload,
        metadata_={},
        status=OutboxStatus.DISPATCHING,
        attempts=0,
    )


@pytest.mark.asyncio
async def test_dispatch_no_op_for_unknown_event_type() -> None:
    # Should return immediately without touching the DB.
    await _dispatch_for_event(_msg("totally.unknown.event", {"order_id": str(uuid4())}))


@pytest.mark.asyncio
async def test_dispatch_no_op_when_order_id_missing() -> None:
    # Defensive — the producer should always include order_id, but if a
    # bad payload slips through, we silently drop rather than crashing
    # the worker. (Outbox dead-letters after 8 attempts on exception.)
    await _dispatch_for_event(_msg("orders.order.created", {}))


@pytest.mark.asyncio
async def test_dispatch_no_op_when_order_id_invalid() -> None:
    await _dispatch_for_event(
        _msg("orders.order.created", {"order_id": "not-a-uuid"}),
    )
