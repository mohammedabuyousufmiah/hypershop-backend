from __future__ import annotations

from app.external_adapters.base import AIReplyRequest, AIReplyResult
from app.integrations import OpenAIClient


class OpenAITextAdapter:
    name = "openai"

    def __init__(self) -> None:
        self.client = OpenAIClient()

    @property
    def enabled(self) -> bool:
        return self.client.enabled

    async def customer_reply(self, request: AIReplyRequest) -> AIReplyResult:
        text, confidence = await self.client.customer_reply(
            request.customer_language,
            request.customer_text,
            request.product_context,
        )
        return AIReplyResult(
            text=text,
            confidence=confidence,
            provider=self.name,
            raw={"tenant_id": request.tenant_id, "enabled": self.enabled},
        )
