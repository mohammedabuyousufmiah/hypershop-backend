"""Outbound message templates — both WhatsApp template parameter sets
AND SMS body strings.

WhatsApp uses pre-approved templates (operator registers them on
business.facebook.com). The body parameters are positional; we expose
the assembly here so naming is consistent across the dispatcher +
tests.

SMS bodies are inline strings sent via the BD SMS aggregators. Bilingual
(English + a tiny Bangla note) — most BD users prefer English-script
numerals + a hint of Bangla for warmth.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class InvoiceContext:
    """Inputs the dispatcher computes once and passes to both WhatsApp
    + SMS template builders so messages are consistent across channels.
    """

    customer_name: str
    order_code: str
    amount: str        # already-formatted "1,234.50"
    currency: str
    view_url: str      # link to the invoice / order page
    pay_url: str | None = None  # link to payment page if still due


@dataclass(frozen=True)
class OtpContext:
    purpose: str       # "login" | "email_verify" | "password_reset"
    code: str
    minutes: int


# ════════════════════════════════════════════════════════════════════
# WhatsApp template parameter assembly
# ════════════════════════════════════════════════════════════════════
# Operator must register these template names on Meta. Body parameters
# are POSITIONAL — the order here MUST match the template's body
# placeholder order ({{1}}, {{2}}, ...).


def whatsapp_invoice_body_params(ctx: InvoiceContext) -> tuple[str, ...]:
    """Body parameters for the ``hypershop_invoice`` template.

    Recommended template body (operator registers this with Meta):

      Hi {{1}}, your Hypershop order {{2}} is confirmed.
      Total: {{3}} {{4}}.
      View invoice: {{5}}
    """
    return (
        ctx.customer_name,
        ctx.order_code,
        ctx.amount,
        ctx.currency,
        ctx.view_url,
    )


def whatsapp_invoice_header_param(ctx: InvoiceContext) -> str:
    """Optional text header — usually "Order {order_code}"."""
    return f"Order {ctx.order_code}"


def whatsapp_otp_body_params(ctx: OtpContext) -> tuple[str, ...]:
    """Body parameters for the ``hypershop_otp_authentication`` template.

    Meta auto-approves OTP-category templates with body:

      Your Hypershop verification code is {{1}}. It expires in {{2}} minutes.

    The auth-category template ALSO needs a single button parameter
    (the code) for the "copy code" UX — but template button parameters
    are not exposed in our minimal interface yet. Operators who want
    the copy-code button should register a UTILITY template with the
    same body and skip the button.
    """
    return (ctx.code, str(ctx.minutes))


# ════════════════════════════════════════════════════════════════════
# SMS bodies
# ════════════════════════════════════════════════════════════════════
# Kept short to fit in 1–2 GSM-7 segments (160 chars per segment).
# English-only for max compatibility; Bangla glyphs require UCS-2
# encoding which halves the per-segment chars.


def sms_invoice_body(
    ctx: InvoiceContext,
    *,
    android_url: str | None,
    ios_url: str | None,
) -> str:
    """SMS body when WhatsApp dispatch falls back. Includes the customer-
    app download link so the recipient can install Hypershop and view
    the full invoice in-app instead of a one-shot URL."""
    lines = [
        f"Hi {ctx.customer_name.split()[0] if ctx.customer_name else 'customer'},",
        f"Hypershop order {ctx.order_code} confirmed.",
        f"Total: {ctx.amount} {ctx.currency}.",
        f"Invoice: {ctx.view_url}",
    ]
    app_links: list[str] = []
    if android_url:
        app_links.append(f"Android: {android_url}")
    if ios_url:
        app_links.append(f"iOS: {ios_url}")
    if app_links:
        lines.append("Get the app -> " + " | ".join(app_links))
    return " ".join(lines)


def sms_otp_body(ctx: OtpContext) -> str:
    """Compact OTP SMS — same shape as the existing IAM SMS template
    but kept here so the dispatcher owns the body when routing through
    the WhatsApp-first path."""
    return (
        f"Hypershop {ctx.purpose.replace('_', ' ')} code: {ctx.code} "
        f"(expires in {max(1, ctx.minutes)} min). Don't share this code."
    )
