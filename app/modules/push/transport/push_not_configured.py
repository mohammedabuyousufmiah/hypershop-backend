"""Default push transport — returns TRANSIENT_FAILURE so the dispatcher
records the attempt + the outbox dispatcher schedules retry.

We intentionally don't raise: push is async best-effort. The customer
sees no UX impact when push is unconfigured (they still get WhatsApp +
SMS for invoices, and the in-app order screen always shows fresh state
on next refresh).
"""

from __future__ import annotations

from app.core.logging import get_logger
from app.modules.push.transport.push_base import (
    Notification,
    PushOutcome,
    PushSendResult,
)

_logger = get_logger("hypershop.push.not_configured")


class NotConfiguredPushTransport:
    name = "not_configured"
    kind = "any"

    async def send(self, *, token: str, notification: Notification) -> PushSendResult:
        _logger.info(
            "push_skipped_not_configured",
            token_prefix=token[:8] if token else "",
            title=notification.title,
        )
        return PushSendResult(
            outcome=PushOutcome.TRANSIENT_FAILURE,
            error_code="not_configured",
            error_message=(
                "Push transport is not configured — set FCM_* or APNS_* "
                "credentials in env and restart."
            ),
        )
