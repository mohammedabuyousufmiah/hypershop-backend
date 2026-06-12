"""Local CC settings shim.

Original CC code imports ``from app.config import settings`` and expects
a function that returns a namespace with WhatsApp / OpenAI / Google
Sheets / RAG knobs. Hypershop's settings (``app.core.config``) doesn't
carry those keys, so we expose a CC-scoped settings function here that
reads directly from environment variables. CC modules now import from
``app.modules.customer_care.config`` instead.

All vars default to safe disabled values, so the module degrades
gracefully if credentials aren't configured (matches Hypershop's
"log-only" transport pattern).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class CCSettings:
    # ---- WhatsApp Cloud API ----
    whatsapp_access_token: str | None = None
    whatsapp_phone_number_id: str | None = None
    whatsapp_verify_token: str = "hypershop-cc"
    whatsapp_api_version: str = "v20.0"
    # HMAC App Secret — used to verify Meta webhook signatures.
    # When set + in production, requests without a valid X-Hub-Signature-256
    # are rejected. When unset, signature verification is logged-only.
    whatsapp_app_secret: str | None = None
    # Approved template names for system-initiated messages. Outside
    # the 24-hour customer-service window Meta blocks free-form text,
    # so the outbox handlers send these templates instead. Each
    # template must be pre-approved in Meta Business Manager.
    template_order_paid: str | None = None
    template_order_dispatched: str | None = None
    template_order_delivered: str | None = None
    template_payment_success: str | None = None
    template_language: str = "en"

    # ---- OpenAI ----
    openai_api_key: str | None = None
    openai_model: str = "gpt-4o-mini"
    openai_embedding_model: str = "text-embedding-3-small"

    # ---- Google Sheets ----
    google_sheets_client_email: str | None = None
    google_sheets_private_key: str | None = None
    google_sheets_spreadsheet_id: str | None = None

    # ---- Voice / media ----
    voice_note_max_audio_bytes: int = 16 * 1024 * 1024
    voice_note_min_audio_bytes: int = 1024

    # ---- RAG ----
    rag_enabled: bool = True
    rag_chunk_max_tokens: int = 400
    rag_chunk_overlap_tokens: int = 60
    rag_top_k: int = 5
    rag_min_score: float = 0.35

    # ---- Queue ----
    redis_url: str | None = None

    # ---- Email channel (sprint 7) ----
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_use_tls: bool = True
    smtp_from_address: str | None = None
    # Inbound email webhook — operator picks one provider (Postmark/
    # Mailgun/SendGrid/SES SNS) and configures it to POST to
    # /api/v1/customer-care/webhooks/email. Optional shared-secret
    # header for verification.
    email_inbound_secret: str | None = None

    # ---- SMS channel (sprint 7) ----
    # Strategy: prefer BulkSMSBD (Bangladesh), fall back to Twilio.
    # Hypershop's own SMS transport is reused if these aren't set.
    bulksms_bd_api_token: str | None = None
    bulksms_bd_sender_id: str | None = None
    twilio_account_sid: str | None = None
    twilio_auth_token: str | None = None
    twilio_from_number: str | None = None
    # ---- Bangladeshi local providers (preferred over Twilio) ----
    # SSL Wireless — major BD SMS aggregator (smsplus.sslwireless.com).
    ssl_sms_api_token: str | None = None
    ssl_sms_sid: str | None = None  # registered masking / sender id
    ssl_sms_base_url: str = "https://smsplus.sslwireless.com"
    # Local SIM gateway (GoIP / Android GSM gateway HTTP API) — routes SMS
    # + click-to-call voice through a physical BD SIM (Grameenphone / Robi /
    # Banglalink / Teletalk). Generic HTTP contract; see channels.py.
    sim_gateway_url: str | None = None
    sim_gateway_token: str | None = None
    sim_gateway_line: str | None = None  # optional port / SIM-slot id
    # Inbound SMS webhook secret
    sms_inbound_secret: str | None = None

    # ---- Facebook Messenger (sprint 7) ----
    # Uses the same Meta Graph API as WhatsApp but the page-level
    # access token + page_id (NOT the WABA / phone_number_id).
    messenger_page_access_token: str | None = None
    messenger_page_id: str | None = None
    messenger_verify_token: str = "hypershop-msgr"

    # ---- Instagram DM (sprint 7) ----
    # Same Meta Graph API surface, different asset id.
    instagram_page_access_token: str | None = None
    instagram_account_id: str | None = None
    instagram_verify_token: str = "hypershop-ig"

    # ---- Web-chat widget (sprint 7) ----
    # An origin allow-list keeps the embed page from being hosted by
    # unauthorised third parties. Comma-separated.
    webchat_allowed_origins: str = "https://www.hypershop.com.bd,http://localhost:3000"

    # ---- Misc ----
    is_production: bool = False
    base_url: str = "http://127.0.0.1:8000"


def _b(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}


def _i(name: str, default: int) -> int:
    v = os.environ.get(name)
    if v is None:
        return default
    try:
        return int(v)
    except ValueError:
        return default


def _f(name: str, default: float) -> float:
    v = os.environ.get(name)
    if v is None:
        return default
    try:
        return float(v)
    except ValueError:
        return default


@lru_cache(maxsize=1)
def settings() -> CCSettings:
    env = os.environ.get("APP_ENV") or os.environ.get("ENVIRONMENT") or "dev"
    return CCSettings(
        whatsapp_access_token=os.environ.get("WHATSAPP_ACCESS_TOKEN"),
        whatsapp_phone_number_id=os.environ.get("WHATSAPP_PHONE_NUMBER_ID"),
        whatsapp_verify_token=os.environ.get("CC_WHATSAPP_VERIFY_TOKEN", "hypershop-cc"),
        whatsapp_api_version=os.environ.get("WHATSAPP_API_VERSION", "v20.0"),
        whatsapp_app_secret=os.environ.get("WHATSAPP_APP_SECRET"),
        template_order_paid=os.environ.get("CC_TEMPLATE_ORDER_PAID"),
        template_order_dispatched=os.environ.get("CC_TEMPLATE_ORDER_DISPATCHED"),
        template_order_delivered=os.environ.get("CC_TEMPLATE_ORDER_DELIVERED"),
        template_payment_success=os.environ.get("CC_TEMPLATE_PAYMENT_SUCCESS"),
        template_language=os.environ.get("CC_TEMPLATE_LANGUAGE", "en"),
        openai_api_key=os.environ.get("OPENAI_API_KEY"),
        openai_model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
        openai_embedding_model=os.environ.get(
            "OPENAI_EMBEDDING_MODEL", "text-embedding-3-small",
        ),
        google_sheets_client_email=os.environ.get("GOOGLE_SHEETS_CLIENT_EMAIL"),
        google_sheets_private_key=os.environ.get("GOOGLE_SHEETS_PRIVATE_KEY"),
        google_sheets_spreadsheet_id=os.environ.get("GOOGLE_SHEETS_SPREADSHEET_ID"),
        voice_note_max_audio_bytes=_i("VOICE_NOTE_MAX_AUDIO_BYTES", 16 * 1024 * 1024),
        voice_note_min_audio_bytes=_i("VOICE_NOTE_MIN_AUDIO_BYTES", 1024),
        rag_enabled=_b("CC_RAG_ENABLED", True),
        rag_chunk_max_tokens=_i("CC_RAG_CHUNK_MAX_TOKENS", 400),
        rag_chunk_overlap_tokens=_i("CC_RAG_CHUNK_OVERLAP_TOKENS", 60),
        rag_top_k=_i("CC_RAG_TOP_K", 5),
        rag_min_score=_f("CC_RAG_MIN_SCORE", 0.35),
        redis_url=os.environ.get("REDIS_URL"),
        is_production=(env == "prod") or (env == "production"),
        base_url=os.environ.get("PUBLIC_BASE_URL", "http://127.0.0.1:8000"),
        smtp_host=os.environ.get("SMTP_HOST"),
        smtp_port=_i("SMTP_PORT", 587),
        smtp_username=os.environ.get("SMTP_USERNAME"),
        smtp_password=os.environ.get("SMTP_PASSWORD"),
        smtp_use_tls=_b("SMTP_USE_TLS", True),
        smtp_from_address=os.environ.get("SMTP_FROM_ADDRESS"),
        email_inbound_secret=os.environ.get("CC_EMAIL_INBOUND_SECRET"),
        bulksms_bd_api_token=os.environ.get("BULKSMS_BD_API_TOKEN"),
        bulksms_bd_sender_id=os.environ.get("BULKSMS_BD_SENDER_ID"),
        twilio_account_sid=os.environ.get("TWILIO_ACCOUNT_SID"),
        twilio_auth_token=os.environ.get("TWILIO_AUTH_TOKEN"),
        twilio_from_number=os.environ.get("TWILIO_FROM_NUMBER"),
        ssl_sms_api_token=os.environ.get("SSL_SMS_API_TOKEN"),
        ssl_sms_sid=os.environ.get("SSL_SMS_SID"),
        ssl_sms_base_url=os.environ.get(
            "SSL_SMS_BASE_URL", "https://smsplus.sslwireless.com",
        ),
        sim_gateway_url=os.environ.get("SIM_GATEWAY_URL"),
        sim_gateway_token=os.environ.get("SIM_GATEWAY_TOKEN"),
        sim_gateway_line=os.environ.get("SIM_GATEWAY_LINE"),
        sms_inbound_secret=os.environ.get("CC_SMS_INBOUND_SECRET"),
        messenger_page_access_token=os.environ.get("MESSENGER_PAGE_ACCESS_TOKEN"),
        messenger_page_id=os.environ.get("MESSENGER_PAGE_ID"),
        messenger_verify_token=os.environ.get("CC_MESSENGER_VERIFY_TOKEN", "hypershop-msgr"),
        instagram_page_access_token=os.environ.get("INSTAGRAM_PAGE_ACCESS_TOKEN"),
        instagram_account_id=os.environ.get("INSTAGRAM_ACCOUNT_ID"),
        instagram_verify_token=os.environ.get("CC_INSTAGRAM_VERIFY_TOKEN", "hypershop-ig"),
        webchat_allowed_origins=os.environ.get(
            "CC_WEBCHAT_ALLOWED_ORIGINS",
            "https://www.hypershop.com.bd,http://localhost:3000",
        ),
    )
