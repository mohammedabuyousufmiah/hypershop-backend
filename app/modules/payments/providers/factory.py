"""Env-driven binding for payment providers.

Called once at app startup (see ``app.main`` lifespan). Reads each
gateway's credentials from ``settings`` and binds the constructed
adapter into the registry. Operators can have BOTH providers bound
simultaneously and pick at initiate-time via ``InitiatePaymentRequest.provider``.

Env contract:
  PAYMENT_PROVIDER:    'bkash' | 'sslcommerz' | 'none' (default fallback)
  BKASH_*:             credentials (see bkash.py)
  SSLCOMMERZ_*:        credentials (see sslcommerz.py)

If a gateway's credentials are incomplete, the factory logs a warning
and skips binding for that gateway — calls to it then return 502.
"""

from __future__ import annotations

from app.core.logging import get_logger
from app.modules.payments.codes import (
    PROVIDER_BKASH,
    PROVIDER_FAKE,
    PROVIDER_NAGAD,
    PROVIDER_ROCKET,
    PROVIDER_SSLCOMMERZ,
)
from app.modules.payments.providers.registry import (
    bind_provider,
    get_provider,
    list_bound_providers,
    set_default_provider,
)

_logger = get_logger("hypershop.payments.factory")


def _secret(value: object) -> str:
    if value is None:
        return ""
    get = getattr(value, "get_secret_value", None)
    if callable(get):
        return str(get() or "")
    return str(value)


def _try_bind_bkash() -> bool:
    """Returns True if the adapter was constructed + bound."""
    from app.core.config import get_settings
    from app.core.errors import IntegrationError

    s = get_settings()
    app_key = _secret(getattr(s, "bkash_app_key", None))
    app_secret = _secret(getattr(s, "bkash_app_secret", None))
    username = _secret(getattr(s, "bkash_username", None))
    password = _secret(getattr(s, "bkash_password", None))
    base_url = getattr(s, "bkash_base_url", None) or None
    webhook_url = getattr(s, "bkash_webhook_url", None) or None

    if not all([app_key, app_secret, username, password, base_url]):
        _logger.warning(
            "bkash_skipped_missing_creds",
            has_app_key=bool(app_key),
            has_app_secret=bool(app_secret),
            has_username=bool(username),
            has_password=bool(password),
            has_base_url=bool(base_url),
        )
        return False

    try:
        from app.modules.payments.providers.bkash import BkashProvider
        adapter = BkashProvider(
            app_key=app_key,
            app_secret=app_secret,
            username=username,
            password=password,
            base_url=base_url,
            webhook_url=webhook_url,
        )
        bind_provider(PROVIDER_BKASH, adapter)
        return True
    except IntegrationError as e:
        _logger.warning("bkash_bind_failed", reason=str(e))
        return False


def _try_bind_sslcommerz() -> bool:
    from app.core.config import get_settings
    from app.core.errors import IntegrationError

    s = get_settings()
    store_id = _secret(getattr(s, "sslcommerz_store_id", None))
    store_passwd = _secret(getattr(s, "sslcommerz_store_passwd", None))
    base_url = getattr(s, "sslcommerz_base_url", None) or None
    is_sandbox = bool(getattr(s, "sslcommerz_is_sandbox", True))
    webhook_url = getattr(s, "sslcommerz_webhook_url", None) or None

    if not all([store_id, store_passwd, base_url]):
        _logger.warning(
            "sslcommerz_skipped_missing_creds",
            has_store_id=bool(store_id),
            has_store_passwd=bool(store_passwd),
            has_base_url=bool(base_url),
        )
        return False

    try:
        from app.modules.payments.providers.sslcommerz import SSLCommerzProvider
        adapter = SSLCommerzProvider(
            store_id=store_id,
            store_passwd=store_passwd,
            base_url=base_url,
            is_sandbox=is_sandbox,
            webhook_url=webhook_url,
        )
        bind_provider(PROVIDER_SSLCOMMERZ, adapter)
        return True
    except IntegrationError as e:
        _logger.warning("sslcommerz_bind_failed", reason=str(e))
        return False


def _try_bind_nagad() -> bool:
    from app.core.config import get_settings
    from app.core.errors import IntegrationError

    s = get_settings()
    merchant_id = _secret(getattr(s, "nagad_merchant_id", None))
    merchant_number = _secret(getattr(s, "nagad_merchant_number", None))
    private_key_pem = _secret(getattr(s, "nagad_merchant_private_key_pem", None))
    public_key_pem = _secret(getattr(s, "nagad_public_key_pem", None))
    base_url = getattr(s, "nagad_base_url", None) or None
    is_sandbox = bool(getattr(s, "nagad_is_sandbox", True))
    callback_base = getattr(s, "nagad_callback_base_url", None) or None
    webhook_url = getattr(s, "nagad_webhook_url", None) or None

    if not all([merchant_id, merchant_number, private_key_pem, public_key_pem, base_url]):
        _logger.warning(
            "nagad_skipped_missing_creds",
            has_merchant_id=bool(merchant_id),
            has_merchant_number=bool(merchant_number),
            has_priv_key=bool(private_key_pem),
            has_nagad_pub_key=bool(public_key_pem),
            has_base_url=bool(base_url),
        )
        return False
    try:
        from app.modules.payments.providers.nagad import NagadProvider
        adapter = NagadProvider(
            merchant_id=merchant_id,
            merchant_number=merchant_number,
            merchant_private_key_pem=private_key_pem,
            nagad_public_key_pem=public_key_pem,
            base_url=base_url,
            is_sandbox=is_sandbox,
            callback_base_url=callback_base,
            webhook_url=webhook_url,
        )
        bind_provider(PROVIDER_NAGAD, adapter)
        return True
    except IntegrationError as e:
        _logger.warning("nagad_bind_failed", reason=str(e))
        return False


def _try_bind_rocket() -> bool:
    from app.core.config import get_settings
    from app.core.errors import IntegrationError

    s = get_settings()
    merchant_id = _secret(getattr(s, "rocket_merchant_id", None))
    app_key = _secret(getattr(s, "rocket_app_key", None))
    app_secret = _secret(getattr(s, "rocket_app_secret", None))
    base_url = getattr(s, "rocket_base_url", None) or None
    is_sandbox = bool(getattr(s, "rocket_is_sandbox", True))
    webhook_url = getattr(s, "rocket_webhook_url", None) or None

    if not all([merchant_id, app_key, app_secret, base_url]):
        _logger.warning(
            "rocket_skipped_missing_creds",
            has_merchant_id=bool(merchant_id),
            has_app_key=bool(app_key),
            has_app_secret=bool(app_secret),
            has_base_url=bool(base_url),
        )
        return False
    try:
        from app.modules.payments.providers.rocket import RocketProvider
        adapter = RocketProvider(
            merchant_id=merchant_id,
            app_key=app_key,
            app_secret=app_secret,
            base_url=base_url,
            is_sandbox=is_sandbox,
            webhook_url=webhook_url,
        )
        bind_provider(PROVIDER_ROCKET, adapter)
        return True
    except IntegrationError as e:
        _logger.warning("rocket_bind_failed", reason=str(e))
        return False


def _try_bind_fake() -> bool:
    """Bind the dev-fake payment provider — auto-succeeds, no creds.

    Hard production gate: refuse to bind when
    ``settings.environment == 'production'`` even if the operator set
    ``PAYMENT_PROVIDER=fake`` — that's the kind of mistake that ships
    free orders to the warehouse, so we make it impossible by code
    rather than discipline.
    """
    from app.core.config import get_settings

    s = get_settings()
    env = (getattr(s, "environment", "") or "").lower()
    if env == "production":
        _logger.warning(
            "fake_payment_provider_refused_in_production",
            environment=env,
        )
        return False

    try:
        from app.modules.payments.providers.fake import FakePaymentProvider
        public_base = (
            getattr(s, "frontend_base_url", None)
            or getattr(s, "site_url", None)
            or "http://localhost:3000"
        )
        webhook = getattr(s, "fake_payment_webhook_url", None) or None
        adapter = FakePaymentProvider(
            public_base_url=str(public_base),
            webhook_url=str(webhook) if webhook else None,
        )
        bind_provider(PROVIDER_FAKE, adapter)
        _logger.info("fake_payment_provider_bound", environment=env)
        return True
    except Exception as e:
        _logger.warning("fake_payment_provider_bind_failed", error=str(e))
        return False


def bind_from_settings() -> dict[str, bool]:
    """Try to bind every supported gateway whose creds are present.
    Set the operator-chosen default. Returns a {provider_name: bound?}
    summary for logging.
    """
    from app.core.config import get_settings

    s = get_settings()
    summary: dict[str, bool] = {
        PROVIDER_BKASH: _try_bind_bkash(),
        PROVIDER_SSLCOMMERZ: _try_bind_sslcommerz(),
        PROVIDER_NAGAD: _try_bind_nagad(),
        PROVIDER_ROCKET: _try_bind_rocket(),
        # Fake provider is bound automatically in non-prod envs so
        # local + CI checkout completes end-to-end without real
        # merchant credentials. See _try_bind_fake for the env gate.
        PROVIDER_FAKE: _try_bind_fake(),
    }
    chosen = (getattr(s, "payment_provider", None) or "none").lower()
    if chosen in summary and summary[chosen]:
        set_default_provider(chosen)
        _logger.info(
            "payment_default_provider_set", default=chosen,
            bound=list_bound_providers(),
        )
    elif chosen not in ("", "none"):
        _logger.warning(
            "payment_default_provider_unbindable",
            requested=chosen,
            bound=list_bound_providers(),
        )
    return summary


__all__ = ["bind_from_settings", "get_provider"]
