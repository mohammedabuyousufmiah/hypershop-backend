"""External API adapter contracts and default provider registry."""

from app.modules.customer_care.external_adapters.base import (
    AdapterNotConfiguredError,
    AIReplyRequest,
    AIReplyResult,
    AITextAdapter,
    CallControlResult,
    InboundCallEvent,
    InboundVoiceAdapter,
    PaymentEvent,
    PaymentWebhookAdapter,
    VoiceCallRequest,
    VoiceCallResult,
    VoiceProviderAdapter,
    WhatsAppMessageAdapter,
)

__all__ = [
    "AdapterNotConfiguredError",
    "AIReplyRequest",
    "AIReplyResult",
    "AITextAdapter",
    "CallControlResult",
    "InboundCallEvent",
    "InboundVoiceAdapter",
    "PaymentEvent",
    "PaymentWebhookAdapter",
    "VoiceCallRequest",
    "VoiceCallResult",
    "VoiceProviderAdapter",
    "WhatsAppMessageAdapter",
]
