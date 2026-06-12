from __future__ import annotations

import os
from functools import lru_cache

from app.external_adapters.base import (
    AITextAdapter,
    PaymentWebhookAdapter,
    VoiceProviderAdapter,
    WhatsAppMessageAdapter,
)
from app.external_adapters.stub import (
    StubAITextAdapter,
    StubPaymentWebhookAdapter,
    StubVoiceProviderAdapter,
    StubWhatsAppAdapter,
)


def _provider(name: str, default: str = "stub") -> str:
    return os.getenv(name, default).strip().lower() or default


@lru_cache
def whatsapp_adapter() -> WhatsAppMessageAdapter:
    provider = _provider("EXTERNAL_WHATSAPP_PROVIDER")
    if provider in {"stub", "dry_run", "none"}:
        return StubWhatsAppAdapter()
    if provider == "whatsapp_cloud":
        from app.integrations import WhatsAppClient

        return WhatsAppClient()
    return StubWhatsAppAdapter()


@lru_cache
def ai_adapter() -> AITextAdapter:
    provider = _provider("EXTERNAL_AI_PROVIDER")
    if provider in {"stub", "dry_run", "none"}:
        return StubAITextAdapter()
    if provider == "openai":
        from app.external_adapters.real import OpenAITextAdapter

        return OpenAITextAdapter()
    return StubAITextAdapter()


@lru_cache
def payment_adapter() -> PaymentWebhookAdapter:
    provider = _provider("EXTERNAL_PAYMENT_PROVIDER")
    if provider in {"stub", "dry_run", "none", "custom"}:
        return StubPaymentWebhookAdapter()
    return StubPaymentWebhookAdapter()


@lru_cache
def voice_adapter() -> VoiceProviderAdapter:
    provider = _provider("EXTERNAL_VOICE_PROVIDER")
    if provider in {"stub", "dry_run", "none", "android_sim_gateway"}:
        return StubVoiceProviderAdapter()
    return StubVoiceProviderAdapter()
