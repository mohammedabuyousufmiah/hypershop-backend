"""Social-login id_token verification (Google / Huawei).

Verifies the provider's OpenID Connect id_token: signature against the
provider JWKS, issuer, expiry, and audience (`aud`) against our configured
client IDs. Returns the verified (email, name).

Disabled-by-default: if no client IDs are configured for a provider the
endpoint raises ``SocialLoginDisabled`` → HTTP 503, so the feature stays off
until real credentials are set (no insecure stub-accept in production).
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import httpx
import jwt
from jwt import PyJWKClient

from app.core.config import get_settings

GOOGLE_ISSUERS = {"https://accounts.google.com", "accounts.google.com"}
GOOGLE_JWKS = "https://www.googleapis.com/oauth2/v3/certs"
# Huawei Account Kit OpenID config.
HUAWEI_ISSUERS = {"https://accounts.huawei.com", "https://oauth-login.cloud.huawei.com"}
HUAWEI_JWKS = "https://oauth-login.cloud.huawei.com/oauth2/v3/certs"


class SocialLoginDisabled(Exception):
    """No client IDs configured for the provider."""


class SocialLoginInvalid(Exception):
    """id_token failed verification (signature / aud / issuer / expiry)."""


@dataclass
class SocialIdentity:
    email: str
    name: str


def _client_ids(raw: str) -> list[str]:
    return [c.strip() for c in (raw or "").split(",") if c.strip()]


def _verify(id_token: str, *, jwks_url: str, issuers: set[str], audiences: list[str]) -> SocialIdentity:
    if not audiences:
        raise SocialLoginDisabled("provider not configured")
    try:
        signing_key = PyJWKClient(jwks_url).get_signing_key_from_jwt(id_token)
        claims = jwt.decode(
            id_token,
            signing_key.key,
            algorithms=["RS256"],
            audience=audiences,
            options={"require": ["exp", "iat", "aud", "iss"]},
        )
    except Exception as e:  # noqa: BLE001 — any jwt/jwk failure → invalid
        raise SocialLoginInvalid(str(e)) from e
    if claims.get("iss") not in issuers:
        raise SocialLoginInvalid("untrusted issuer")
    if claims.get("exp", 0) < int(time.time()):
        raise SocialLoginInvalid("token expired")
    email = (claims.get("email") or "").strip().lower()
    if not email:
        raise SocialLoginInvalid("id_token has no email claim")
    name = claims.get("name") or claims.get("given_name") or email.split("@", 1)[0]
    return SocialIdentity(email=email, name=name)


def verify_google(id_token: str) -> SocialIdentity:
    cfg = get_settings()
    return _verify(
        id_token,
        jwks_url=GOOGLE_JWKS,
        issuers=GOOGLE_ISSUERS,
        audiences=_client_ids(cfg.google_oauth_client_ids),
    )


def verify_huawei(id_token: str) -> SocialIdentity:
    cfg = get_settings()
    return _verify(
        id_token,
        jwks_url=HUAWEI_JWKS,
        issuers=HUAWEI_ISSUERS,
        audiences=_client_ids(cfg.huawei_oauth_client_ids),
    )
