"""WhatsApp transport contract.

This file ships the *interface* + the canonical "send result" enum.
Concrete adapters (Meta Cloud, Twilio WhatsApp, ...) live as siblings.

Why a custom result enum (not just exceptions):
  The dispatch service needs to distinguish three outcomes:

    1. delivered   — the message reached the recipient
    2. NOT_ON_WA   — the recipient does not have WhatsApp on this number
                     (this is the trigger for SMS fallback — NOT a retry)
    3. transient   — transport / quota / 5xx (retry path; no fallback)

  Mapping all three to exceptions would force the service to inspect
  exception details to decide between fallback vs retry. A typed enum
  keeps the branching readable.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class WhatsAppOutcome(StrEnum):
    DELIVERED = "delivered"
    NOT_ON_WHATSAPP = "not_on_whatsapp"
    TRANSIENT_FAILURE = "transient_failure"


@dataclass(frozen=True)
class WhatsAppSendResult:
    outcome: WhatsAppOutcome
    # Provider message id (e.g. WhatsApp wamid) when outcome=DELIVERED.
    message_id: str | None = None
    # Provider's error code (e.g. "131026") when not delivered.
    error_code: str | None = None
    # Short error message — safe to surface in audit / payment_attempts.
    error_message: str | None = None


@dataclass(frozen=True)
class WhatsAppTemplateMessage:
    """Pre-approved Meta template message — REQUIRED for cold messages
    outside the 24-hour conversation window. Operators register the
    template on business.facebook.com and pass the name + ordered
    parameters here.
    """

    name: str
    language_code: str  # e.g. "en", "bn"
    body_parameters: tuple[str, ...]
    # Optional named header parameter — many invoice templates use a
    # text header showing the order code.
    header_parameter: str | None = None


class WhatsAppTransport(Protocol):
    """Outgoing WhatsApp transport.

    Adapters MUST raise neither IntegrationError nor ServiceUnavailableError
    — they MUST translate every outcome into a WhatsAppSendResult so the
    dispatch service can branch on outcome cleanly. The only exception
    that should ever propagate is ValidationError for a malformed phone
    number (caller's bug; will not be retried).
    """

    name: str

    async def send_template(
        self,
        *,
        to: str,
        template: WhatsAppTemplateMessage,
    ) -> WhatsAppSendResult: ...
