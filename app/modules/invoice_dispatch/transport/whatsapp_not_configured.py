"""Default WhatsApp transport — returns TRANSIENT_FAILURE so the
dispatch service falls through to SMS without a hard error.

This is the only "graceful no-op" adapter in the codebase. Rationale:
  - WhatsApp is the *preferred* channel, not the *required* channel.
  - When WhatsApp is unconfigured, customers should still get the
    invoice via SMS — the user explicitly asked for that fallback.
  - Returning TRANSIENT_FAILURE (not DELIVERED) means the dispatcher
    treats it as a fail and tries SMS next. Different from the
    NotConfigured pattern in payments/SMS where the operator MUST
    intervene; here the operator can leave WhatsApp off intentionally.

  - The error_code "missing_setting" still surfaces in logs so ops
    can spot accidental gaps (e.g. WHATSAPP_PROVIDER set but creds
    typo'd → bound to NotConfigured → all WhatsApp sends silently
    skipped). The factory logs a WARNING in the same case.
"""

from __future__ import annotations

from app.core.logging import get_logger
from app.modules.invoice_dispatch.transport.whatsapp_base import (
    WhatsAppOutcome,
    WhatsAppSendResult,
    WhatsAppTemplateMessage,
)

_logger = get_logger("hypershop.invoice_dispatch.whatsapp.not_configured")


class NotConfiguredWhatsAppTransport:
    name = "not_configured"

    async def send_template(
        self,
        *,
        to: str,
        template: WhatsAppTemplateMessage,
    ) -> WhatsAppSendResult:
        _logger.info(
            "whatsapp_skipped_not_configured",
            to_prefix=to[:6],
            template=template.name,
        )
        return WhatsAppSendResult(
            outcome=WhatsAppOutcome.TRANSIENT_FAILURE,
            error_code="not_configured",
            error_message=(
                "WhatsApp is not configured — set WHATSAPP_PROVIDER and "
                "the matching META_WHATSAPP_* credentials, then restart."
            ),
        )
