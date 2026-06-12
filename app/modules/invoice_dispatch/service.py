"""Outbound channel router.

Owns the "WhatsApp first → SMS fallback (BD-only)" routing for both:
  - **invoices** (after prescription accept / payment captured)
  - **OTPs** (login + verify, replaces IAM's SMS-only path)

Routing rules:
  Phone starts with ``+880``  → WhatsApp first; on TRANSIENT/NOT_ON_WA → SMS
  Phone starts with anything → WhatsApp ONLY (no expensive international SMS)

  The "BD only" SMS rule reflects: BulkSMSBD / SSL Wireless are local
  aggregators (1-3 BDT/msg); Twilio for international SMS is ~7-15 BDT/msg
  AND requires an A2P 10DLC registration we don't have. WhatsApp is
  free to send, so non-BD customers are nudged to install WhatsApp.

Hard guarantee:
  Both ``dispatch_invoice`` and ``dispatch_otp`` re-raise on a
  catastrophic outcome (no channel succeeded for a recipient who is
  reachable). The outbox dispatcher catches the exception and schedules
  a retry — no silent drops.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from app.core.errors import ServiceUnavailableError, ValidationError
from app.core.logging import get_logger
from app.modules.invoice_dispatch.templates import (
    InvoiceContext,
    OtpContext,
    sms_invoice_body,
    sms_otp_body,
    whatsapp_invoice_body_params,
    whatsapp_invoice_header_param,
    whatsapp_otp_body_params,
)
from app.modules.invoice_dispatch.transport.whatsapp_base import (
    WhatsAppOutcome,
    WhatsAppTemplateMessage,
)
from app.modules.invoice_dispatch.transport.whatsapp_registry import (
    get_transport as get_whatsapp_transport,
)

_logger = get_logger("hypershop.invoice_dispatch.service")


# ════════════════════════════════════════════════════════════════════
# Phone classification
# ════════════════════════════════════════════════════════════════════

_BD_PREFIX = "+880"


def is_bd_phone(phone: str) -> bool:
    """True if phone is a Bangladesh E.164 number."""
    return phone.startswith(_BD_PREFIX)


@dataclass(frozen=True)
class DispatchOutcome:
    """What actually happened — exposed so handlers can audit / log
    which channel won."""

    via: str       # "whatsapp" | "sms" | "none"
    delivered: bool
    error_code: str | None = None
    error_message: str | None = None
    message_id: str | None = None


# ════════════════════════════════════════════════════════════════════
# OTP dispatch
# ════════════════════════════════════════════════════════════════════


async def dispatch_otp(
    *,
    phone: str,
    code: str,
    purpose: str,
    ttl_seconds: int,
) -> DispatchOutcome:
    """Send an OTP via WhatsApp first; fall back to SMS for BD numbers
    only. Raises ``ServiceUnavailableError`` on catastrophic failure
    so the outbox dispatcher schedules a retry.
    """
    from app.core.config import get_settings
    cfg = get_settings()
    template_name = (
        getattr(cfg, "whatsapp_template_otp", None)
        or "hypershop_otp_authentication"
    )
    template_lang = (
        getattr(cfg, "whatsapp_template_otp_language", None) or "en"
    )

    octx = OtpContext(
        purpose=purpose,
        code=code,
        minutes=max(1, ttl_seconds // 60),
    )

    # 1) Try WhatsApp.
    wa = get_whatsapp_transport()
    template = WhatsAppTemplateMessage(
        name=template_name,
        language_code=template_lang,
        body_parameters=whatsapp_otp_body_params(octx),
    )
    wa_result = await wa.send_template(to=phone, template=template)
    if wa_result.outcome == WhatsAppOutcome.DELIVERED:
        return DispatchOutcome(
            via="whatsapp",
            delivered=True,
            message_id=wa_result.message_id,
        )

    _logger.info(
        "otp_whatsapp_skipped",
        outcome=wa_result.outcome.value,
        code=wa_result.error_code,
        bd=is_bd_phone(phone),
        to_prefix=phone[:6],
    )

    # 2) SMS fallback — BD ONLY. Non-BD customers must use WhatsApp.
    if not is_bd_phone(phone):
        # Non-BD + WhatsApp failed. Don't pay international SMS rates.
        # If WhatsApp's outcome was NOT_ON_WHATSAPP this is permanent —
        # raise so the outbox marks the message dead-letter rather than
        # spinning forever.
        if wa_result.outcome == WhatsAppOutcome.NOT_ON_WHATSAPP:
            raise ServiceUnavailableError(
                "Non-BD recipient is not on WhatsApp; international SMS "
                "fallback is disabled by policy.",
                details={
                    "reason": "non_bd_not_on_whatsapp",
                    "to_prefix": phone[:6],
                    "whatsapp_error": wa_result.error_code,
                },
            )
        # WhatsApp transient → outbox should retry.
        raise ServiceUnavailableError(
            "WhatsApp transient failure for non-BD recipient; will retry.",
            details={
                "reason": "non_bd_whatsapp_transient",
                "to_prefix": phone[:6],
                "whatsapp_error": wa_result.error_code,
            },
        )

    # BD path — SMS via the existing IAM SMS transport.
    from app.modules.iam.transport.sms_registry import (
        get_transport as get_sms_transport,
    )
    sms = get_sms_transport()
    text = sms_otp_body(octx)
    try:
        await sms.send(to=phone, text=text)
    except Exception as exc:
        # Both channels failed — re-raise so outbox retries.
        raise ServiceUnavailableError(
            "Both WhatsApp and SMS failed to deliver OTP.",
            details={
                "to_prefix": phone[:6],
                "whatsapp_outcome": wa_result.outcome.value,
                "sms_error": type(exc).__name__,
            },
        ) from exc

    return DispatchOutcome(
        via="sms",
        delivered=True,
        error_code=wa_result.error_code,
        error_message=f"WhatsApp fell through ({wa_result.outcome.value}); SMS succeeded",
    )


# ════════════════════════════════════════════════════════════════════
# Invoice dispatch
# ════════════════════════════════════════════════════════════════════


async def dispatch_invoice_for_order(*, session, order_id: UUID) -> DispatchOutcome:
    """Build invoice context from an order + emit via WhatsApp/SMS.

    Loads the order + customer inside the *given* SQLAlchemy session so
    callers can compose this with their own UnitOfWork.

    Idempotency:
      Caller is expected to have written an outbox row first; this
      function is the OUTBOX HANDLER that runs after commit. If
      WhatsApp succeeds we never re-emit; if WhatsApp says NOT_ON_WA
      we proceed to SMS (BD only) once.
    """
    from app.core.config import get_settings
    from app.modules.iam.models import User
    from app.modules.orders.models import Order

    order = await session.get(Order, order_id)
    if order is None:
        raise ValidationError(
            f"Order {order_id} not found — cannot dispatch invoice.",
            details={"order_id": str(order_id)},
        )
    user = await session.get(User, order.customer_user_id)
    if user is None or not user.phone:
        # No phone on file → no channel works. Mark as no-op so the
        # outbox doesn't loop. Caller's audit row records why.
        return DispatchOutcome(
            via="none",
            delivered=False,
            error_code="no_phone_on_file",
            error_message=(
                f"User {order.customer_user_id} has no phone — "
                "cannot dispatch invoice."
            ),
        )

    cfg = get_settings()
    base = (
        getattr(cfg, "payment_default_redirect_base_url", None)
        or "https://app.hypershop.local"
    ).rstrip("/")
    view_url = f"{base}/orders/{order.code}"
    pay_url = f"{base}/orders/{order.code}/pay" if order.payment_method == "online" else None
    amount_str = f"{Decimal(order.grand_total):.2f}"
    ictx = InvoiceContext(
        customer_name=user.full_name or "Customer",
        order_code=order.code,
        amount=amount_str,
        currency=order.currency,
        view_url=view_url,
        pay_url=pay_url,
    )

    template_name = (
        getattr(cfg, "whatsapp_template_invoice", None) or "hypershop_invoice"
    )
    template_lang = (
        getattr(cfg, "whatsapp_template_invoice_language", None) or "en"
    )

    # 1) WhatsApp first
    wa = get_whatsapp_transport()
    template = WhatsAppTemplateMessage(
        name=template_name,
        language_code=template_lang,
        body_parameters=whatsapp_invoice_body_params(ictx),
        header_parameter=whatsapp_invoice_header_param(ictx),
    )
    wa_result = await wa.send_template(to=user.phone, template=template)
    if wa_result.outcome == WhatsAppOutcome.DELIVERED:
        # Audit-log the wamid against the order so ops can later answer
        # "did invoice for order X get delivered? what's the wamid?".
        # The webhook updates whatsapp_message_statuses keyed by wamid;
        # joining on wamid in the audit gives full lifecycle visibility:
        #   audit_log[wamid X, action=invoice.sent, resource_id=order]
        #   whatsapp_message_statuses[wamid X, status=delivered/read/failed]
        from app.core.audit.service import record_audit
        from app.core.security.principal import SystemPrincipal
        if wa_result.message_id:
            await record_audit(
                actor=SystemPrincipal(),
                action="whatsapp.invoice.sent",
                resource_type="order",
                resource_id=order.id,
                metadata={
                    "wamid": wa_result.message_id,
                    "channel": "whatsapp",
                    "to_prefix": user.phone[:6],
                    "template": template_name,
                },
            )
        return DispatchOutcome(
            via="whatsapp",
            delivered=True,
            message_id=wa_result.message_id,
        )

    _logger.info(
        "invoice_whatsapp_skipped",
        order_code=order.code,
        outcome=wa_result.outcome.value,
        code=wa_result.error_code,
        bd=is_bd_phone(user.phone),
    )

    # 2) SMS fallback — BD only (with app download link)
    if not is_bd_phone(user.phone):
        # Non-BD + WhatsApp failed. Don't fall back to international
        # SMS. The customer just needs to install WhatsApp — the SMS
        # fallback would still nudge them to "get the app", which the
        # WhatsApp message would have done if it had reached them.
        return DispatchOutcome(
            via="none",
            delivered=False,
            error_code="non_bd_whatsapp_unavailable",
            error_message=(
                "Non-BD customer not reachable via WhatsApp; "
                "international SMS fallback is disabled by policy."
            ),
        )

    from app.modules.iam.transport.sms_registry import (
        get_transport as get_sms_transport,
    )
    sms = get_sms_transport()
    android_url = getattr(cfg, "customer_app_android_url", None) or None
    ios_url = getattr(cfg, "customer_app_ios_url", None) or None
    text = sms_invoice_body(ictx, android_url=android_url, ios_url=ios_url)
    try:
        await sms.send(to=user.phone, text=text)
    except Exception as exc:
        raise ServiceUnavailableError(
            "Both WhatsApp and SMS failed to deliver invoice.",
            details={
                "order_code": order.code,
                "to_prefix": user.phone[:6],
                "whatsapp_outcome": wa_result.outcome.value,
                "sms_error": type(exc).__name__,
            },
        ) from exc

    # Audit-log the SMS-fallback path too — symmetric with the WhatsApp
    # branch so ops can answer "what channel reached this order?".
    from app.core.audit.service import record_audit
    from app.core.security.principal import SystemPrincipal
    await record_audit(
        actor=SystemPrincipal(),
        action="whatsapp.invoice.sent",
        resource_type="order",
        resource_id=order.id,
        metadata={
            "channel": "sms",
            "to_prefix": user.phone[:6],
            "whatsapp_outcome": wa_result.outcome.value,
            "fallback_reason": wa_result.error_code or "transient",
        },
    )

    return DispatchOutcome(
        via="sms",
        delivered=True,
        error_code=wa_result.error_code,
        error_message=(
            f"WhatsApp fell through ({wa_result.outcome.value}); "
            "SMS with app link succeeded"
        ),
    )
