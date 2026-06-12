"""Push transport contract.

Adapters return a typed outcome rather than raising, so the dispatch
service can branch cleanly between:

  - DELIVERED: success (provider acknowledged + accepted the message)
  - INVALID_TOKEN: the destination token is no longer valid
                   (FCM ``UNREGISTERED`` / APNS ``BadDeviceToken`` etc).
                   Service marks ``device_tokens.is_active = false``
                   so we never dispatch to that token again.
  - TRANSIENT_FAILURE: anything else (5xx, timeout, 429). Outbox retries.

Two transports for two platforms. APNS for iOS (kind='apns'), FCM
for Android + Web (kind='fcm','web').
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class PushOutcome(StrEnum):
    DELIVERED = "delivered"
    INVALID_TOKEN = "invalid_token"
    TRANSIENT_FAILURE = "transient_failure"


@dataclass(frozen=True)
class Notification:
    """Cross-platform notification payload.

    The ``data`` dict maps to:
      - FCM: ``message.data`` (string-keyed string-valued)
      - APNS: keys are flattened into the alert payload's
              ``custom_data`` zone (we put them under top-level keys
              alongside ``aps``, which is the Apple convention).
    """

    title: str
    body: str
    # Useful keys to standardise across the codebase:
    #   "type": "order_status" | "payment" | "delivery" | ...
    #   "order_code": "HSO-XXXXXXX"
    #   "deep_link": "hypershop://orders/HSO-XXXXXXX"
    data: dict[str, str] | None = None
    # Optional badge count (iOS); ignored by FCM/Android.
    badge: int | None = None


@dataclass(frozen=True)
class PushSendResult:
    outcome: PushOutcome
    # Provider message id when delivered.
    message_id: str | None = None
    # Provider error code (e.g. "UNREGISTERED", "BadDeviceToken").
    error_code: str | None = None
    error_message: str | None = None


class PushTransport(Protocol):
    """Outgoing push transport. One instance per kind (fcm / apns)."""

    name: str
    # The device-kind this transport handles ('fcm' or 'apns').
    kind: str

    async def send(
        self, *, token: str, notification: Notification,
    ) -> PushSendResult: ...
