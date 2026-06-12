from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


class AdapterNotConfiguredError(RuntimeError):
    """Raised when live external API mode is requested without credentials."""


@dataclass(frozen=True)
class AIReplyRequest:
    customer_language: str
    customer_text: str
    product_context: str = ""
    tenant_id: str = "default"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AIReplyResult:
    text: str | None
    confidence: float = 0.0
    provider: str = "stub"
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PaymentEvent:
    event_id: str
    event_type: str
    status: str
    amount: int | None = None
    currency: str | None = None
    customer_phone: str | None = None
    order_id: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class VoiceCallRequest:
    to_phone: str
    script: str
    tenant_id: str = "default"
    language: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class VoiceCallResult:
    ok: bool
    call_id: str | None = None
    provider: str = "stub"
    error: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


class WhatsAppMessageAdapter(Protocol):
    name: str

    @property
    def enabled(self) -> bool: ...

    async def send_text(self, to: str, text: str) -> dict[str, Any]: ...

    async def send_image(
        self, to: str, image_url: str, caption: str | None = None
    ) -> dict[str, Any]: ...


class AITextAdapter(Protocol):
    name: str

    @property
    def enabled(self) -> bool: ...

    async def customer_reply(self, request: AIReplyRequest) -> AIReplyResult: ...


class PaymentWebhookAdapter(Protocol):
    name: str

    @property
    def enabled(self) -> bool: ...

    def normalize_event(self, payload: dict[str, Any]) -> PaymentEvent: ...


class VoiceProviderAdapter(Protocol):
    name: str

    @property
    def enabled(self) -> bool: ...

    async def place_call(self, request: VoiceCallRequest) -> VoiceCallResult: ...


# ─── Inbound voice-call adapter (added 2026-05-16) ─────────────────────
@dataclass(frozen=True)
class InboundCallEvent:
    """Normalised inbound voice-call event extracted from a provider's
    webhook body. Producers should not couple to the provider's raw
    payload shape — the adapter does the translation here.

    ``event_type`` follows our internal vocabulary:
      - ``ringing``  — call is alerting, no agent yet
      - ``answered`` — connected leg established
      - ``ended``    — call disconnected (any reason)
      - ``missed``   — alerted but never answered before timeout
    """
    provider: str
    provider_call_id: str
    event_type: str
    from_phone: str
    to_number: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CallControlResult:
    ok: bool
    error: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


class InboundVoiceAdapter(Protocol):
    """Adapter for providers that push inbound voice-call webhooks to us.

    Implementations verify provider-specific signatures, normalise the
    event payload into ``InboundCallEvent``, and provide call-control
    verbs (transfer / hangup) for the dispatch UI.
    """
    name: str

    @property
    def enabled(self) -> bool: ...

    def verify_webhook_signature(
        self, *, raw_body: bytes, headers: dict[str, str],
    ) -> tuple[bool, str | None]:
        """Return (ok, reason_if_rejected). ``reason`` is for logging only —
        never echo back to the caller.
        """
        ...

    def parse_inbound_event(self, payload: dict[str, Any]) -> InboundCallEvent: ...

    async def transfer_call(
        self, *, provider_call_id: str, target_sip_uri: str,
    ) -> CallControlResult: ...

    async def hangup_call(self, *, provider_call_id: str) -> CallControlResult: ...
