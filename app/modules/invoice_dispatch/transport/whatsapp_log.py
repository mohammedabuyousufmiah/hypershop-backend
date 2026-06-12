"""DEV-ONLY log-to-console WhatsApp transport.

Bound when ``WHATSAPP_PROVIDER=log`` and ``ENVIRONMENT != production``.
Mirrors ``sms_log.LogOnlySmsTransport`` for the WhatsApp side — every
template/text dispatch writes a structured log line instead of
hitting the Meta Cloud API. Lets the invoice + OTP-fallback flows
exercise their full code path during local + CI runs without real
Meta merchant credentials.
"""

from __future__ import annotations

from app.core.logging import get_logger


class LogOnlyWhatsAppTransport:
    """Logs the message + returns. Never raises."""

    name = "log"

    def __init__(self) -> None:
        self._logger = get_logger("hypershop.whatsapp.log")

    async def send_text(self, *, to: str, body: str) -> None:
        self._logger.info(
            "whatsapp_log_only_text",
            to=to,
            body=body,
            note="DEV-MODE — real Meta Cloud API not wired.",
        )

    async def send_template(
        self,
        *,
        to: str,
        template_name: str,
        language_code: str = "en",
        components: list | None = None,
    ) -> None:
        self._logger.info(
            "whatsapp_log_only_template",
            to=to,
            template=template_name,
            language=language_code,
            components=components or [],
            note="DEV-MODE — real Meta Cloud API not wired.",
        )

    async def send_document(
        self,
        *,
        to: str,
        document_url: str,
        filename: str,
        caption: str | None = None,
    ) -> None:
        self._logger.info(
            "whatsapp_log_only_document",
            to=to,
            url=document_url,
            filename=filename,
            caption=caption,
            note="DEV-MODE — real Meta Cloud API not wired.",
        )
