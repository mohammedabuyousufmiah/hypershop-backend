#!/usr/bin/env python3
"""Production boot preflight — fail-fast environment validator.

Run this BEFORE ``uvicorn`` / ``arq`` start so misconfigured deploys
exit with a clear actionable message instead of booting half-broken.

Exit codes:
  0  — all required checks pass; safe to start api + worker
  1  — at least one hard check failed; DO NOT start the app
  2  — env file unreadable

Usage (compose):
  command: ["sh", "-c", "python boot_preflight.py && uvicorn app.main:app --host 0.0.0.0 --port 8000"]

Usage (systemd):
  ExecStartPre=/opt/hypershop/venv/bin/python /opt/hypershop/backend/boot_preflight.py
  ExecStart=/opt/hypershop/venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000

Hard checks (FAIL → exit 1):
  - ENVIRONMENT == 'production' or 'staging'
  - JWT_SECRET set and >= 32 chars
  - POSTGRES_PASSWORD set and >= 16 chars
  - DATABASE_URL set and looks like a postgresql+asyncpg:// DSN
  - REDIS_URL set and looks like a redis:// DSN
  - CORS_ORIGINS set; in production NO http:// allowed (HTTPS only)

Warn-only checks (WARN → continue, log to stderr):
  - No OTP channel configured (WhatsApp / SMS / SMTP all empty)
  - No payment provider configured (Bkash + SSLCommerz both empty)
  - INITIAL_ADMIN_EMAIL set without INITIAL_ADMIN_PASSWORD
  - OTEL_EXPORTER_OTLP_ENDPOINT unset (tracing off)
"""

from __future__ import annotations

import os
import sys
import urllib.parse


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

RED = "\033[31m"
YELLOW = "\033[33m"
GREEN = "\033[32m"
RESET = "\033[0m"


def _fail(label: str, detail: str) -> bool:
    print(f"{RED}FAIL{RESET}  {label} — {detail}", file=sys.stderr)
    return False


def _warn(label: str, detail: str) -> None:
    print(f"{YELLOW}WARN{RESET}  {label} — {detail}", file=sys.stderr)


def _ok(label: str) -> bool:
    print(f"{GREEN}OK{RESET}    {label}")
    return True


# ---------------------------------------------------------------------------
# hard checks
# ---------------------------------------------------------------------------

def check_environment() -> bool:
    val = os.environ.get("ENVIRONMENT", "").strip().lower()
    if val not in {"production", "staging"}:
        return _fail(
            "ENVIRONMENT",
            f"='{val or '(unset)'}'. Set ENVIRONMENT=production (or 'staging') in .env.",
        )
    return _ok(f"ENVIRONMENT={val}")


def check_jwt_secret() -> bool:
    val = os.environ.get("JWT_SECRET", "")
    if len(val) < 32:
        return _fail(
            "JWT_SECRET",
            f"length={len(val)} chars (need ≥ 32). "
            "Generate: python -c \"import secrets; print(secrets.token_urlsafe(48))\"",
        )
    return _ok(f"JWT_SECRET length={len(val)}")


def _dsn_has_password(dsn: str) -> bool:
    """True if a DSN embeds a non-empty password (``scheme://user:pass@host``)."""
    try:
        authority = dsn.split("://", 1)[1].split("@", 1)[0]
    except IndexError:
        return False
    return "@" in dsn and ":" in authority and authority.split(":", 1)[1] != ""


def check_postgres_password() -> bool:
    val = os.environ.get("POSTGRES_PASSWORD", "")
    if not val:
        # Managed platforms (Render/Railway/Heroku) inject DB credentials via
        # DATABASE_URL and run no self-hosted postgres container, so a separate
        # POSTGRES_PASSWORD isn't used. Accept that — as long as DATABASE_URL
        # itself carries an embedded password.
        if _dsn_has_password(os.environ.get("DATABASE_URL", "")):
            return _ok("POSTGRES_PASSWORD (n/a — managed DB, creds in DATABASE_URL)")
        return _fail(
            "POSTGRES_PASSWORD",
            "unset and DATABASE_URL carries no embedded password. Set one or "
            "use a managed DATABASE_URL that includes credentials.",
        )
    if len(val) < 16:
        return _fail(
            "POSTGRES_PASSWORD",
            f"length={len(val)} chars (need ≥ 16). Use a random ≥ 32-char password.",
        )
    return _ok(f"POSTGRES_PASSWORD length={len(val)}")


def check_database_url() -> bool:
    val = os.environ.get("DATABASE_URL", "")
    if not val:
        return _fail("DATABASE_URL", "unset")
    if "postgresql" not in val:
        return _fail(
            "DATABASE_URL",
            f"does not look like a Postgres DSN: '{val[:40]}…'",
        )
    # A driverless DSN (postgres:// or postgresql://) is fine — config.py's
    # normalizer upgrades it to +asyncpg at load. Only warn on a non-asyncpg
    # *explicit* driver (e.g. +psycopg2), which the async engine can't use.
    if "+" in val.split("://", 1)[0] and "asyncpg" not in val:
        _warn(
            "DATABASE_URL",
            "explicit non-asyncpg driver set — the app's async engine needs "
            "postgresql+asyncpg:// (a driverless postgres:// URL is auto-upgraded)",
        )
    return _ok("DATABASE_URL")


def check_redis_url() -> bool:
    val = os.environ.get("REDIS_URL", "")
    if not val:
        return _fail("REDIS_URL", "unset")
    if not val.startswith(("redis://", "rediss://")):
        return _fail(
            "REDIS_URL",
            f"does not look like a Redis DSN: '{val[:40]}…'",
        )
    return _ok("REDIS_URL")


def check_cors_origins() -> bool:
    val = os.environ.get("CORS_ORIGINS", "").strip()
    if not val:
        return _fail(
            "CORS_ORIGINS",
            "unset — at least one frontend origin must be allow-listed",
        )
    origins = [o.strip() for o in val.split(",") if o.strip()]
    env = os.environ.get("ENVIRONMENT", "").lower()
    if env == "production":
        plain_http = [o for o in origins if o.startswith("http://")]
        if plain_http:
            return _fail(
                "CORS_ORIGINS",
                f"production mode rejects plain http:// origins — found: {plain_http}",
            )
    return _ok(f"CORS_ORIGINS ({len(origins)} origin(s))")


# ---------------------------------------------------------------------------
# soft (warn-only) checks
# ---------------------------------------------------------------------------

def warn_otp_channels() -> None:
    whatsapp = os.environ.get("META_WHATSAPP_ACCESS_TOKEN", "")
    sms = os.environ.get("BULKSMSBD_API_KEY", "")
    smtp = os.environ.get("SMTP_HOST", "")
    if not any((whatsapp, sms, smtp)):
        _warn(
            "OTP channels",
            "NO provider configured (WhatsApp + SMS + SMTP all empty) — "
            "OTP login will return 502 until at least one is set. "
            "See CREDS_FASTPATH.md.",
        )


def warn_payment_providers() -> None:
    bkash = os.environ.get("BKASH_APP_KEY", "")
    sslc = os.environ.get("SSLCOMMERZ_STORE_ID", "")
    if not any((bkash, sslc)):
        _warn(
            "Payment providers",
            "Bkash + SSLCommerz both unset — checkout will only support "
            "cash-on-delivery. See CREDS_FASTPATH.md.",
        )


def warn_initial_admin() -> None:
    email = os.environ.get("INITIAL_ADMIN_EMAIL", "")
    password = os.environ.get("INITIAL_ADMIN_PASSWORD", "")
    if email and not password:
        _warn(
            "Initial admin",
            "INITIAL_ADMIN_EMAIL set but PASSWORD missing — bootstrap will skip.",
        )


def warn_otel() -> None:
    if not os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        _warn(
            "OpenTelemetry",
            "OTEL_EXPORTER_OTLP_ENDPOINT unset — tracing is OFF (acceptable; "
            "wire when observability stack is up).",
        )


def warn_backup_cron() -> None:
    if not os.environ.get("BACKUP_INTERVAL_SECONDS"):
        _warn(
            "Postgres backup",
            "BACKUP_INTERVAL_SECONDS unset — docker-compose.prod.yml backup "
            "service will use its default 24h cadence.",
        )


def warn_seo_agents() -> None:
    """v23 SEO agents — OpenAI key is optional (deterministic fallback
    content ships out-of-the-box). Warn so operators know the AI path
    is dormant when key is unset.
    """
    if not os.environ.get("OPENAI_API_KEY"):
        _warn(
            "SEO agents (M34)",
            "OPENAI_API_KEY unset — KeywordIntelligence / LocalLandingPage / "
            "Schema agents will run deterministic-fallback only (no LLM "
            "calls). Set the key to enable AI-generated SEO copy.",
        )


def warn_seller_pwa() -> None:
    """Seller PWA dist/ is shipped inside the backend image (v25
    fix). If it's missing the /seller/ mount falls back to a build
    placeholder. Catch the case where someone re-built the image
    after deleting the dist artefact.
    """
    dist_index = "/app/app/modules/sellers/_frontend_src/dist/index.html"
    if not os.path.exists(dist_index):
        _warn(
            "Seller PWA",
            "app/modules/sellers/_frontend_src/dist/index.html missing — "
            "/seller/ will serve the build-instruction placeholder until "
            "you run `pnpm build` in _frontend_src and rebuild the image.",
        )


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> int:
    print("=" * 60)
    print("Hypershop production boot preflight")
    print("=" * 60)
    print()

    hard_checks = [
        check_environment,
        check_jwt_secret,
        check_postgres_password,
        check_database_url,
        check_redis_url,
        check_cors_origins,
    ]
    results = [check() for check in hard_checks]
    failed = sum(1 for r in results if not r)

    print()
    print("Soft checks (warn-only)")
    print("-" * 60)
    warn_otp_channels()
    warn_payment_providers()
    warn_initial_admin()
    warn_otel()
    warn_backup_cron()
    warn_seo_agents()
    warn_seller_pwa()

    print()
    print("-" * 60)
    if failed:
        print(
            f"{RED}{failed} hard check(s) failed — refusing to start.{RESET}",
            file=sys.stderr,
        )
        return 1
    print(f"{GREEN}All hard checks passed — safe to start api + worker.{RESET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
