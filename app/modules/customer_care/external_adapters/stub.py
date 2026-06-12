from __future__ import annotations

from typing import Any

from app.external_adapters.base import (
    AIReplyRequest,
    AIReplyResult,
    PaymentEvent,
    VoiceCallRequest,
    VoiceCallResult,
)


class StubWhatsAppAdapter:
    name = "stub_whatsapp"

    @property
    def enabled(self) -> bool:
        return False

    async def send_text(self, to: str, text: str) -> dict[str, Any]:
        return {"dry_run": True, "provider": self.name, "to": to, "text": text}

    async def send_image(
        self, to: str, image_url: str, caption: str | None = None
    ) -> dict[str, Any]:
        return {
            "dry_run": True,
            "provider": self.name,
            "to": to,
            "image_url": image_url,
            "caption": caption,
        }


class StubAITextAdapter:
    name = "stub_ai"

    @property
    def enabled(self) -> bool:
        return False

    async def customer_reply(self, request: AIReplyRequest) -> AIReplyResult:
        return AIReplyResult(
            text=None,
            confidence=0.0,
            provider=self.name,
            raw={"dry_run": True, "tenant_id": request.tenant_id},
        )


class StubPaymentWebhookAdapter:
    name = "stub_payment"

    @property
    def enabled(self) -> bool:
        return False

    def normalize_event(self, payload: dict[str, Any]) -> PaymentEvent:
        return PaymentEvent(
            event_id=str(payload.get("id") or payload.get("event_id") or "dry-run"),
            event_type=str(payload.get("type") or payload.get("event_type") or "unknown"),
            status=str(payload.get("status") or "unknown"),
            amount=payload.get("amount"),
            currency=payload.get("currency"),
            customer_phone=payload.get("customer_phone"),
            order_id=payload.get("order_id"),
            raw={"dry_run": True, "payload": payload},
        )


class StubVoiceProviderAdapter:
    name = "stub_voice"

    @property
    def enabled(self) -> bool:
        return False

    async def place_call(self, request: VoiceCallRequest) -> VoiceCallResult:
        return VoiceCallResult(
            ok=False,
            provider=self.name,
            error="Voice provider is not configured. Add a live adapter and credentials.",
            raw={"dry_run": True, "to_phone": request.to_phone, "tenant_id": request.tenant_id},
        )
