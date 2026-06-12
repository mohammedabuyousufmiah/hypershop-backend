from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import (
    Field,
    PostgresDsn,
    RedisDsn,
    SecretStr,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

Environment = Literal["dev", "test", "staging", "production"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        case_sensitive=False,
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: Environment = Field(...)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    service_name: str = "hypershop-api"
    api_prefix: str = "/api/v1"

    database_url: PostgresDsn = Field(...)
    database_sync_url: str = Field(...)
    database_pool_size: int = Field(default=10, ge=1, le=200)
    database_max_overflow: int = Field(default=5, ge=0, le=200)
    database_pool_recycle_seconds: int = Field(default=1800, ge=60)

    redis_url: RedisDsn = Field(...)

    jwt_secret: SecretStr = Field(...)
    jwt_algorithm: Literal["HS256"] = "HS256"
    jwt_access_ttl_seconds: int = Field(default=900, ge=60, le=3600)
    jwt_refresh_ttl_seconds: int = Field(default=7 * 24 * 3600, ge=3600)

    argon2_time_cost: int = Field(default=3, ge=1, le=10)
    argon2_memory_cost_kib: int = Field(default=65536, ge=8192)
    argon2_parallelism: int = Field(default=4, ge=1, le=16)

    rate_limit_login_per_minute: int = Field(default=5, ge=1)
    rate_limit_otp_per_hour: int = Field(default=5, ge=1)
    rate_limit_register_per_hour: int = Field(default=10, ge=1)

    otp_length: int = Field(default=6, ge=4, le=10)
    otp_ttl_seconds: int = Field(default=600, ge=60, le=3600)
    otp_max_attempts: int = Field(default=5, ge=1, le=20)
    # Dev/creds-pending bypass: when true, OTP confirm accepts any non-empty
    # code as long as an active OTP exists for that (email/phone, purpose).
    # Flip off when real SMS/email provider creds are bound in production.
    otp_dev_bypass: bool = Field(default=False)

    password_reset_ttl_seconds: int = Field(default=3600, ge=300)
    password_min_length: int = Field(default=12, ge=8, le=128)

    failed_login_lockout_threshold: int = Field(default=10, ge=3)
    failed_login_lockout_seconds: int = Field(default=900, ge=60)

    # `NoDecode` tells pydantic-settings NOT to try JSON-parsing this env
    # value before validators run. Without it, a CSV like
    # "https://a.tld,https://b.tld" trips JSONDecodeError before our
    # `_split_csv` validator gets a chance.
    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)

    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    smtp_use_tls: bool = True
    smtp_sender: str | None = None

    arq_queue_name: str = "hypershop:jobs"

    # ----- Inventory -----
    inventory_default_warehouse_code: str = Field(default="MAIN", min_length=1, max_length=32)
    inventory_near_expiry_days: int = Field(default=30, ge=1, le=365)
    inventory_expiry_check_interval_minutes: int = Field(default=60, ge=1, le=1440)

    # ----- Customer wallet -----
    # Each granted credit lives this long before expiry. Default ≈ 1 month.
    wallet_credit_lifetime_days: int = Field(default=30, ge=1, le=365)
    # Percentage of an expired credit that survives as a fresh grant. 0 = none.
    wallet_rollover_percent: int = Field(default=0, ge=0, le=100)
    # Currency wallets are denominated in. Single-currency v1; expand later if needed.
    wallet_currency: str = Field(default="BDT", min_length=3, max_length=3)

    # ----- Delivery operations -----
    delivery_pod_dir: str = Field(
        default="/var/hypershop/delivery_pod",
        min_length=1,
        max_length=512,
    )
    delivery_pod_max_file_bytes: int = Field(
        default=8 * 1024 * 1024, ge=1024, le=50 * 1024 * 1024,
    )
    # COD collected vs expected may differ by this many BDT cents and still
    # auto-reconcile. Default 0 = exact match required.
    delivery_cod_auto_reconcile_tolerance_cents: int = Field(
        default=0, ge=0, le=100_000,
    )

    # ----- Finance / accounting -----
    # Default output VAT rate applied to order grand_total when posting
    # revenue. Bangladesh medicines are mostly VAT-exempt, so default is 0.
    # Set to "0.15" for 15% VAT, etc. Treated as VAT-inclusive.
    vat_rate: str = Field(default="0", pattern=r"^\d+(\.\d{1,4})?$")

    # ----- AI providers (Module 20) -----
    # Primary provider; one of {openai, anthropic, gemini, none}.
    # ``none`` (default) leaves the NotConfiguredProvider bound and every
    # AI call returns 502 with a clear message naming the missing keys.
    ai_provider: str = Field(default="none")
    # Comma-separated failover chain: e.g. "anthropic,gemini". Each must
    # itself be configured (its API key present) — unconfigured backups
    # are silently skipped and logged at WARNING.
    ai_backup_providers: str = Field(default="")

    openai_api_key: SecretStr | None = None
    openai_base_url: str | None = None
    openai_model_default: str | None = None

    anthropic_api_key: SecretStr | None = None
    anthropic_base_url: str | None = None
    anthropic_model_default: str | None = None

    gemini_api_key: SecretStr | None = None
    gemini_base_url: str | None = None
    gemini_model_default: str | None = None

    # ----- Payment providers (Module 22) -----
    # Default provider used when /payments/initiate doesn't receive an
    # explicit `provider` field. One of {bkash, sslcommerz, nagad, rocket, none}.
    payment_provider: str = Field(default="none")
    # Public storefront URL — used by the dev-fake payment adapter to
    # build the ``/checkout/fake-pay`` redirect that auto-completes
    # the payment in local + CI. Real Bkash/SSLCommerz use their own
    # *_redirect_base_url settings; this is fake-only.
    frontend_base_url: str | None = Field(default=None)
    # Public base URL where customers land after gateway redirect.
    # Used to build success/failure/cancel URLs when the frontend
    # doesn't override per-call. e.g. https://app.hypershop.example
    payment_default_redirect_base_url: str | None = None
    # Public base URL gateways post webhooks to. MUST be the public
    # FQDN of the API service. e.g. https://api.hypershop.example
    payment_webhook_base_url: str | None = None

    # Bkash
    bkash_app_key: SecretStr | None = None
    bkash_app_secret: SecretStr | None = None
    bkash_username: SecretStr | None = None
    bkash_password: SecretStr | None = None
    bkash_base_url: str | None = None
    bkash_webhook_url: str | None = None

    # SSLCommerz
    sslcommerz_store_id: SecretStr | None = None
    sslcommerz_store_passwd: SecretStr | None = None
    sslcommerz_base_url: str | None = None
    sslcommerz_is_sandbox: bool = True
    sslcommerz_webhook_url: str | None = None

    # Nagad — keys are PEM strings (newlines preserved). DO NOT pass
    # base64 — the adapter loads them via cryptography.serialization.
    nagad_merchant_id: SecretStr | None = None
    nagad_merchant_number: SecretStr | None = None
    nagad_merchant_private_key_pem: SecretStr | None = None
    nagad_public_key_pem: SecretStr | None = None
    nagad_base_url: str | None = None
    nagad_is_sandbox: bool = True
    nagad_callback_base_url: str | None = None
    nagad_webhook_url: str | None = None

    # Rocket (DBBL Mobile Banking) — direct merchant API.
    # If you're routing Rocket via SSLCommerz aggregator, leave these
    # unset and use PAYMENT_PROVIDER=sslcommerz instead.
    rocket_merchant_id: SecretStr | None = None
    rocket_app_key: SecretStr | None = None
    rocket_app_secret: SecretStr | None = None
    rocket_base_url: str | None = None
    rocket_is_sandbox: bool = True
    rocket_webhook_url: str | None = None

    # ----- SMS provider (Module 23) -----
    # One of {bulksmsbd, ssl_wireless, twilio, none}. Default `none` ⇒
    # any SMS dispatch raises ServiceUnavailableError and the outbox
    # dispatcher schedules retry — no silent drops.
    sms_provider: str = Field(default="none")

    # BulkSMSBD (BD-native aggregator)
    bulksmsbd_api_key: SecretStr | None = None
    bulksmsbd_sender_id: str | None = None
    bulksmsbd_base_url: str | None = None  # default http://bulksmsbd.net/api

    # SSL Wireless (BD enterprise aggregator)
    ssl_wireless_api_token: SecretStr | None = None
    ssl_wireless_sid: str | None = None  # approved Sender ID
    ssl_wireless_base_url: str | None = None  # default https://smsplus.sslwireless.com

    # Twilio (international fallback)
    twilio_account_sid: SecretStr | None = None
    twilio_auth_token: SecretStr | None = None
    twilio_from_number: str | None = None  # E.164 (e.g. +13105551212)
    twilio_base_url: str | None = None  # default https://api.twilio.com

    # ----- WhatsApp transport (Module 24) -----
    # One of {meta_cloud, none}. Default `none` ⇒ WhatsApp dispatch is
    # skipped and the dispatcher falls through to SMS (BD only).
    whatsapp_provider: str = Field(default="none")
    # WhatsApp template names registered on business.facebook.com.
    # Operators MUST register these before going live (defaults are the
    # canonical names suggested in invoice_dispatch.templates).
    whatsapp_template_invoice: str = Field(default="hypershop_invoice")
    whatsapp_template_invoice_language: str = Field(default="en", min_length=2, max_length=8)
    whatsapp_template_otp: str = Field(default="hypershop_otp_authentication")
    whatsapp_template_otp_language: str = Field(default="en", min_length=2, max_length=8)

    # Meta Cloud API (preferred — direct from Meta, no aggregator markup)
    meta_whatsapp_access_token: SecretStr | None = None
    meta_whatsapp_phone_number_id: str | None = None
    meta_whatsapp_business_account_id: str | None = None  # for ops/audit only
    meta_whatsapp_api_version: str = Field(default="v21.0")
    # App Secret — used to verify X-Hub-Signature-256 on inbound webhooks.
    # Find under business.facebook.com → App → Settings → Basic → App Secret.
    meta_whatsapp_app_secret: SecretStr | None = None
    # Verify token — arbitrary string YOU pick + paste into Meta's
    # webhook config. Used in the GET handshake.
    meta_whatsapp_verify_token: SecretStr | None = None

    # ----- Customer app download links (used in SMS invoice fallback) -----
    customer_app_android_url: str | None = None
    customer_app_ios_url: str | None = None

    # ----- Push notifications (Module 25) -----
    # FCM HTTP v1: paste the entire service-account JSON as ONE secret
    # string (multi-line JSON with literal \n in private_key is fine —
    # JSON parser handles it). project_id is read from the JSON; override
    # with FCM_PROJECT_ID if you keep multiple projects in one account.
    fcm_service_account_json: SecretStr | None = None
    fcm_project_id: str | None = None

    # APNS token-based auth: Apple gives you a .p8 private key. Paste
    # the full PEM contents here (-----BEGIN PRIVATE KEY----- ... -----END...-----).
    apns_team_id: SecretStr | None = None
    apns_key_id: SecretStr | None = None
    apns_private_key_p8: SecretStr | None = None
    apns_bundle_id: SecretStr | None = None
    apns_is_sandbox: bool = False

    # ----- Search reranker (Module 28) -----
    # 'external_ml' | 'none' (default). 'none' keeps local ts_rank
    # ordering; the search endpoint stays up regardless.
    search_rerank_provider: str = Field(default="none")
    # Generic config for the external_ml adapter — BYO endpoint
    # (Cohere Rerank, Voyage, internal model, anything that takes
    # {"query","candidates"} and returns {"results":[...]} or
    # {"scores":{...}}).
    search_rerank_api_url: str | None = None
    search_rerank_api_token: SecretStr | None = None
    search_rerank_api_auth_header: str = Field(default="Authorization")
    search_rerank_api_auth_scheme: str = Field(default="Bearer")
    search_rerank_api_method: str = Field(default="POST")
    search_rerank_api_static_headers_json: SecretStr | None = None
    search_rerank_timeout_s: float = Field(default=8.0, ge=1.0, le=60.0)

    # ----- Reporting platform (Module 30) -----
    # On-disk root for generated CSV/XLSX/PDF report files. Each
    # report-file row stores its full path here. Files are written
    # atomically + tracked by ``report_files``; a daily cron deletes
    # rows past ``expires_at``.
    report_storage_dir: str = Field(
        default="/var/hypershop/reports",
        min_length=1,
        max_length=512,
    )
    # Signed download tokens live this long. The default 24h is enough
    # for an emailed link to be useful next morning, short enough that
    # a leaked URL has limited replay window.
    report_signed_url_ttl_hours: int = Field(default=24, ge=1, le=168)
    # Path to a TrueType font with Bengali glyphs (e.g.
    # NotoSansBengali-Regular.ttf). Empty = falls back to Helvetica
    # in the PDF exporter — Bengali product names will render as boxes.
    # Vendor the file in your container image for production.
    report_pdf_bengali_font_path: str = Field(default="", max_length=512)

    # ----- Rider wallet (Module 32) -----
    # Company's MFS receiver number that riders settle their COD
    # custody to (bKash personal/agent number). Stamped on every
    # rider_settlement row so finance can reconcile against statements.
    rider_wallet_company_receiver_account: str = Field(
        default="017XXXXXXXX", max_length=64,
    )

    # ----- Supplier payment approval engine (Module 33) -----
    # Bills with grand_total >= this amount bind the "high_value"
    # workflow which requires a 4th super-admin approval. Default
    # 500K BDT — adjust per your operating scale.
    supplier_payment_high_value_threshold_bdt: str = Field(
        default="500000.00", max_length=24,
    )
    # Comma-separated email lists to notify when a bill is waiting
    # for approval at a given level. We use a static list rather than
    # querying users with role X to keep notifications predictable
    # and easy to debug ("why didn't I get the email?" → it's not in
    # the env var). Empty = no notification (handler logs + returns).
    supplier_payment_approver_emails_l1: Annotated[
        list[str], NoDecode,
    ] = Field(default_factory=list)
    supplier_payment_approver_emails_l2: Annotated[
        list[str], NoDecode,
    ] = Field(default_factory=list)
    supplier_payment_approver_emails_l3: Annotated[
        list[str], NoDecode,
    ] = Field(default_factory=list)
    supplier_payment_approver_emails_l4: Annotated[
        list[str], NoDecode,
    ] = Field(default_factory=list)
    # Procurement team gets notified on rejection / return-for-correction
    # so they can fix the bill and resubmit.
    supplier_payment_procurement_emails: Annotated[
        list[str], NoDecode,
    ] = Field(default_factory=list)

    # ----- SEO + dynamic content (Module 34) -----
    # Public site identity used across SEO meta + JSON-LD blocks.
    # ``seo_site_url`` MUST be the customer-facing https URL (NOT the
    # API URL) — used to build canonical_url + sitemap + OG URLs.
    seo_site_name: str = Field(default="Hypershop", max_length=120)
    seo_site_url: str = Field(
        default="https://hypershop.example", max_length=255,
    )
    # Default Open Graph image. Should be a publicly reachable URL
    # (or a /static/... path if your frontend serves images).
    # 1200×630 recommended.
    seo_default_og_image: str = Field(default="", max_length=512)
    # Phone shown in JSON-LD organization schema.
    seo_org_phone: str = Field(default="", max_length=32)
    seo_org_locality: str = Field(default="Dhaka", max_length=120)
    seo_org_country: str = Field(
        default="BD", min_length=2, max_length=2,
    )
    # Full NAP (Name-Address-Phone) for LocalBusiness eligibility —
    # Google Maps + Knowledge Panel need streetAddress + postal + geo.
    seo_org_street: str = Field(default="", max_length=255)
    seo_org_region: str = Field(default="Dhaka", max_length=120)
    seo_org_postal_code: str = Field(default="", max_length=16)
    seo_org_lat: str = Field(default="", max_length=24)
    seo_org_lng: str = Field(default="", max_length=24)
    # Price band hint for LocalBusiness — "৳" / "৳৳" / "$$" etc.
    # Empty disables the priceRange field.
    seo_org_price_range: str = Field(default="৳৳", max_length=8)
    # Opening hours in ISO 8601 day+time-range tokens, comma-separated
    # (e.g. "Mo-Fr 09:00-18:00,Sa 10:00-15:00"). Empty disables.
    seo_org_opening_hours: str = Field(default="", max_length=255)
    # ----- Commerce schema (Google Shopping eligibility) -----
    # MerchantReturnPolicy defaults — admin can later override per product
    # via a new `product_commerce_overrides` table; for now everything
    # ships with these org-wide values so the schema validates.
    seo_return_days: int = Field(default=7, ge=0, le=365)
    seo_return_fees: str = Field(
        default="FreeReturn",
        description="FreeReturn | ReturnFeesCustomerResponsibility",
    )
    seo_return_method: str = Field(
        default="ReturnByMail",
        description="ReturnByMail | ReturnAtKiosk | ReturnInStore",
    )
    # OfferShippingDetails defaults — Dhaka metro (in-city).
    seo_shipping_flat_minor: int = Field(default=6000, ge=0)  # BDT 60.00
    seo_shipping_free_threshold_minor: int = Field(default=99900, ge=0)  # BDT 999
    seo_shipping_handling_min_days: int = Field(default=0, ge=0, le=30)
    seo_shipping_handling_max_days: int = Field(default=1, ge=0, le=30)
    seo_shipping_transit_min_days: int = Field(default=1, ge=0, le=30)
    seo_shipping_transit_max_days: int = Field(default=3, ge=0, le=30)
    # Outside-Dhaka (national) shipping rates — emitted as a second
    # OfferShippingDetails row with addressRegion=BD-everything-else
    # so Google Shopping shows the correct rate to non-Dhaka shoppers.
    seo_shipping_outside_flat_minor: int = Field(default=12000, ge=0)  # BDT 120
    seo_shipping_outside_free_threshold_minor: int = Field(
        default=199900, ge=0,
    )  # BDT 1999
    seo_shipping_outside_transit_min_days: int = Field(default=3, ge=0, le=30)
    seo_shipping_outside_transit_max_days: int = Field(default=7, ge=0, le=30)
    # Dhaka region slug (used as DefinedRegion.addressRegion). Schema
    # accepts ISO 3166-2 BD-13 or the human name; we use "Dhaka" for
    # readability + leave BD-13 wiring to a later iteration.
    seo_shipping_dhaka_region: str = Field(default="Dhaka", max_length=64)
    # ----- IndexNow (Bing/Yandex/Naver instant indexing) -----
    # Master switch — flip when a real key is provisioned + the
    # /{key}.txt verification file is reachable. False keeps the queue
    # silently draining nothing.
    seo_indexnow_enabled: bool = Field(default=False)
    # 8-128 hex characters — Bing assigns; same value goes into the
    # /{key}.txt verification file served from the public root.
    seo_indexnow_key: str = Field(default="", max_length=128)
    # ----- ContactPoint blocks on Organization JSON-LD -----
    # Each block becomes a schema.org ContactPoint with a contactType
    # (customer support / sales / billing). Empty phone disables the
    # block — only the populated ones emit. ``available_languages`` is
    # comma-separated ISO 639-1 codes.
    seo_contact_support_phone: str = Field(default="", max_length=32)
    seo_contact_sales_phone: str = Field(default="", max_length=32)
    seo_contact_billing_phone: str = Field(default="", max_length=32)
    seo_contact_languages: str = Field(default="en,bn", max_length=64)
    seo_contact_hours: str = Field(default="Mo-Su 09:00-22:00", max_length=128)
    # ----- Seasonal return-policy override -----
    # Set when a campaign (Eid, Pohela Boishakh, Victory Day) extends
    # the standard return window. Empty name disables the override.
    seo_return_seasonal_name: str = Field(default="", max_length=80)
    seo_return_seasonal_start: str = Field(default="", max_length=10)   # yyyy-mm-dd
    seo_return_seasonal_end: str = Field(default="", max_length=10)
    seo_return_seasonal_days: int = Field(default=0, ge=0, le=180)
    # ----- Webmaster site-verification tokens -----
    # Each goes into a <meta name="..."> on the storefront root. Empty
    # disables the tag — never emits a placeholder Google would treat
    # as ownership fraud. Tokens come from each engine's Webmaster
    # Tools "HTML tag" verification flow (not the file-upload flow).
    seo_verify_google: str = Field(default="", max_length=128)
    seo_verify_bing: str = Field(default="", max_length=128)
    seo_verify_yandex: str = Field(default="", max_length=128)

    # --- Social login (mobile AuthService google/huawei) ---
    # Server verifies the provider id_token's `aud` against these client IDs.
    # When BOTH are empty social login is disabled (endpoint returns 503), so
    # the feature is safely off until real client IDs are configured.
    google_oauth_client_ids: str = Field(default="", max_length=1024)  # comma-separated
    huawei_oauth_client_ids: str = Field(default="", max_length=1024)
    seo_verify_naver: str = Field(default="", max_length=128)
    seo_verify_seznam: str = Field(default="", max_length=128)
    # Facebook domain verification (Commerce Manager + Business Manager).
    seo_verify_facebook: str = Field(default="", max_length=128)
    # Pinterest domain verification.
    seo_verify_pinterest: str = Field(default="", max_length=128)
    # ----- Storefront CMS revalidation -----
    # Full https URL of the customer-web's /api/revalidate webhook.
    # Empty disables outbound revalidation (storefront falls back to
    # its 60s SWR on the layout fetch).
    storefront_revalidate_url: str = Field(default="", max_length=255)
    # HMAC-SHA256 secret shared with the customer-web webhook; the
    # backend signs every POST so the route can reject impostors.
    storefront_revalidate_secret: str = Field(default="", max_length=128)

    # ----- Product page videos (Module 35) -----
    # On-disk root for original uploads + generated HLS + posters when
    # NOT using R2/S3. Ignored at upload time when ``r2_bucket_name``
    # is set — the api streams uploads straight to R2 instead.
    product_video_storage_dir: str = Field(
        default="/var/hypershop/product_videos",
        min_length=1,
        max_length=512,
    )
    # Hard cap on a single upload, in megabytes. Default 200 MB — enough
    # for ~60s of 4K source before transcode, while bounding the memory
    # + disk hit of a single multipart request. The ffmpeg worker uses
    # this same cap to refuse anomalously large originals.
    product_video_max_size_mb: int = Field(
        default=200, ge=1, le=2048,
    )
    # Reject probed uploads longer than this. Short product videos
    # only — at 60 s the 720p variant lands ~9 MB and the 360p
    # variant ~3 MB; only the 360p stays inside the original 2–5 MB
    # budget. Operators who want a strict per-variant size budget can
    # lower this to 30 s (where 720p fits ~4.5 MB).
    product_video_max_duration_seconds: int = Field(
        default=60, ge=5, le=120,
    )
    # Days to keep the raw original on R2 after a video reaches a
    # terminal state (approved / rejected / disabled). The HLS bundle
    # on Bunny is unaffected by this — only the private raw is purged.
    # ``failed`` rows are NEVER auto-purged so an operator can pull
    # the original to diagnose the FFmpeg crash.
    product_video_raw_retention_days: int = Field(
        default=60, ge=7, le=365,
    )
    # Hard ceiling on how many APPROVED videos a single product can
    # display. The admin "approve" endpoint enforces this and returns
    # 409 with a clear message when the cap is hit; the existing
    # videos must be disabled / rejected first.
    product_video_max_approved_per_product: int = Field(
        default=3, ge=1, le=20,
    )
    # Number of FFmpeg jobs the worker may run per 30s tick. Each
    # ffmpeg process can saturate ~1 CPU; the default keeps the
    # worker responsive on a 2-core box.
    product_video_max_concurrent_jobs: int = Field(
        default=2, ge=1, le=16,
    )
    # When set, public video URLs are built against this base instead
    # of the API. Point at a CDN that fronts ``product_video_storage_dir``
    # for production. Empty = serve through /api/v1/catalog/videos/files.
    # Independent of R2 — R2 has its own ``r2_public_base_url`` below
    # (the public CDN that fronts the R2 bucket).
    product_video_public_base_url: str = Field(default="", max_length=255)

    # ----- Cloudflare R2 / S3-compatible object storage -----
    # When ``r2_bucket_name`` is set, the product_videos module switches
    # from on-disk storage to R2 for both raw uploads and HLS output:
    #
    #   raw:    s3://<bucket>/<r2_private_prefix><video_id>/original.<ext>
    #   public: s3://<bucket>/<r2_public_prefix><video_id>/{poster.jpg,
    #                                                      hls/master.m3u8,
    #                                                      hls/<variant>/...}
    #
    # The public prefix is expected to be served through Cloudflare's
    # CDN under ``r2_public_base_url`` (e.g. https://cdn.hypershop.com).
    # The private prefix is NOT public — only the worker reads from it
    # via boto3. Leave ``r2_bucket_name`` empty to keep using the
    # on-disk path under ``product_video_storage_dir``.
    r2_account_id: str | None = None
    r2_access_key_id: str | None = None
    r2_secret_access_key: str | None = None
    r2_bucket_name: str | None = None
    r2_public_base_url: str = Field(default="", max_length=255)
    r2_private_prefix: str = Field(
        default="raw/product-videos/", max_length=255,
    )
    r2_public_prefix: str = Field(
        default="public/product-videos/", max_length=255,
    )

    # ----- Product images (admin uploads via /admin/catalog/products/.../media/upload) -----
    # Two-mode store, same contract as product_videos:
    #   * If ``r2_bucket_name`` is set, each upload is streamed into R2
    #     under ``<bucket>/<r2_image_prefix><yyyy>/<mm>/<dd>/<uuid>.<ext>``
    #     and the public URL is built from ``r2_public_base_url``.
    #   * Otherwise the file is written under
    #     ``product_image_local_dir`` and the response URL is
    #     ``<product_image_local_public_url_prefix><filename>``.
    #
    # In local dev the default local dir is the customer-web Next.js
    # ``public/products/`` folder so URLs of the form ``/products/<file>``
    # resolve via the storefront's own static serving.
    r2_image_prefix: str = Field(
        default="public/products/", max_length=255,
    )
    product_image_local_dir: str = Field(
        default="C:/Users/imyou/OneDrive/Desktop/Yousuf/E CIMMERCE MASTER DATA/E COMMERCEH MASTER BANDLE/BACKEND/_serve_storefront/hypershop-fullstack/frontend/apps/customer-web/public/products",
        max_length=512,
    )
    product_image_local_public_url_prefix: str = Field(
        default="/products/", max_length=255,
    )
    # Reject uploads larger than this. 5 MB is plenty for a JPG/WebP
    # product photo; raw RAWs and full-res PNGs are out of scope here.
    product_image_max_size_mb: int = Field(default=5, ge=1, le=25)

    # ----- Bunny.net (Storage Zone + Pull Zone CDN for HLS output) -----
    # Pipeline:
    #   raw upload → R2 private prefix (kept, deleted after retention)
    #   FFmpeg HLS + thumbnail → Bunny Storage zone
    #   customer playback URL → Bunny Pull Zone CDN (b-cdn.net or custom)
    #
    # Bunny Storage is NOT S3-compatible; the adapter speaks Bunny's
    # plain HTTP API (PUT / DELETE with an ``AccessKey`` header).
    bunny_storage_zone_name: str | None = None
    bunny_storage_access_key: str | None = None
    # Storage region prefix. Empty = main DE region (``storage.bunnycdn.com``).
    # Other valid values: ``ny`` ``la`` ``sg`` ``uk`` ``se`` ``br`` ``jh``
    # → endpoint becomes ``<region>.storage.bunnycdn.com``. Pick the one
    # closest to your origin's writers (i.e. our worker pods).
    bunny_storage_region: str = Field(default="", max_length=8)
    # Public Pull Zone hostname — what customers fetch HLS from.
    # Either Bunny's b-cdn.net default (e.g. ``hypershop-vid.b-cdn.net``)
    # or a custom domain you've attached to the pull zone in the Bunny
    # dashboard (e.g. ``cdn.hypershop.com``). Required when Bunny is
    # enabled — the worker writes this URL into ``hls_url`` at the end
    # of FFmpeg processing.
    bunny_pull_zone_hostname: str = Field(default="", max_length=255)
    # Optional path prefix prepended to every object the worker uploads
    # into the storage zone. Useful when the same zone hosts multiple
    # apps. Default keeps the storage zone tidy.
    bunny_path_prefix: str = Field(
        default="product-videos/", max_length=255,
    )

    # ----- OpenTelemetry tracing (project-wide) -----
    # Tracing is OFF by default. Set ``otel_exporter_otlp_endpoint``
    # to a collector URL (e.g. ``http://otel-collector:4318``) to
    # turn it on. The instrumentation install happens unconditionally
    # at boot — only the exporter wiring + sampler are gated, so
    # toggling this requires a process restart.
    otel_exporter_otlp_endpoint: str | None = None
    otel_exporter_otlp_headers: SecretStr | None = None  # e.g. "api-key=..."
    otel_service_name: str = Field(default="hypershop-api")
    otel_environment: str = Field(default="dev", max_length=32)
    # 0.0–1.0; 1.0 = sample every span, 0.1 = 10%. Producton-typical 0.1–0.25.
    otel_traces_sample_ratio: float = Field(default=1.0, ge=0.0, le=1.0)

    csp_extra_connect_src: Annotated[list[str], NoDecode] = Field(default_factory=list)

    # Funnel module (added 2026-05-13 — merged_funnel_kpi).
    #
    # Auth is admin-JWT + ``funnel.{view,track,export}`` RBAC permissions
    # — no module-local API key. ``pii_masking`` toggles whether
    # dashboard list endpoints expose raw phone/email or masked
    # variants (admin-debug only — flip to false carefully).
    # ``allow_export_without_marketing_consent`` is a kill-switch that
    # MUST stay False in prod — otherwise consent filtering is bypassed
    # at /retargeting/export and audience rows leak consent-rejected users.
    funnel_pii_masking_enabled: bool = Field(default=True)
    funnel_allow_export_without_marketing_consent: bool = Field(default=False)

    @model_validator(mode="before")
    @classmethod
    def _normalize_database_urls(cls, data: object) -> object:
        """Make DATABASE_URL portable across managed Postgres providers.

        Platforms like Render/Railway/Heroku hand out a *driverless* DSN
        (``postgres://`` or ``postgresql://``). SQLAlchemy's async engine
        needs the ``+asyncpg`` driver and Alembic needs a ``+psycopg2``
        sync URL. So: upgrade a bare DATABASE_URL to ``+asyncpg`` and, when
        DATABASE_SYNC_URL isn't explicitly provided, derive it as the
        ``+psycopg2`` variant. Explicit ``+asyncpg`` / ``+psycopg2`` values
        (our dev/compose setup) pass through unchanged.
        """
        if not isinstance(data, dict):
            return data

        def _find(name: str) -> str | None:
            # env is case-insensitive (case_sensitive=False) — match either.
            for k in data:
                if isinstance(k, str) and k.lower() == name:
                    return k
            return None

        url_key = _find("database_url")
        if url_key is None:
            return data
        raw = data[url_key]
        if not isinstance(raw, str) or not raw:
            return data

        async_url = raw
        if async_url.startswith("postgres://"):
            async_url = "postgresql://" + async_url[len("postgres://") :]
        if async_url.startswith("postgresql://"):
            async_url = "postgresql+asyncpg://" + async_url[len("postgresql://") :]
        data[url_key] = async_url

        sync_key = _find("database_sync_url")
        existing_sync = data.get(sync_key) if sync_key else None
        if not (isinstance(existing_sync, str) and existing_sync):
            data[sync_key or "database_sync_url"] = async_url.replace(
                "+asyncpg", "+psycopg2", 1
            )
        return data

    @field_validator("environment", mode="before")
    @classmethod
    def _normalize_env(cls, v: object) -> object:
        return v.lower() if isinstance(v, str) else v

    @field_validator(
        "cors_origins", "csp_extra_connect_src",
        "supplier_payment_approver_emails_l1",
        "supplier_payment_approver_emails_l2",
        "supplier_payment_approver_emails_l3",
        "supplier_payment_approver_emails_l4",
        "supplier_payment_procurement_emails",
        mode="before",
    )
    @classmethod
    def _split_csv(cls, v: object) -> object:
        if isinstance(v, str):
            v = v.strip()
            if not v:
                return []
            return [item.strip() for item in v.split(",") if item.strip()]
        return v

    @field_validator("jwt_secret")
    @classmethod
    def _jwt_secret_min_length(cls, v: SecretStr) -> SecretStr:
        if len(v.get_secret_value()) < 32:
            raise ValueError("JWT_SECRET must be at least 32 characters")
        return v

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def docs_enabled(self) -> bool:
        return self.environment != "production"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
