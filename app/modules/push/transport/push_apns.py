"""Apple Push Notification service (APNS) adapter — token-based auth.

Reference:
  - https://developer.apple.com/documentation/usernotifications/setting_up_a_remote_notification_server
  - HTTP/2 POST to https://api.push.apple.com/3/device/<device_token>
    (sandbox: https://api.sandbox.push.apple.com)
  - Auth: ``Authorization: bearer <provider_jwt>`` — JWT signed with
    ES256 using the Apple-issued .p8 private key (kid = key id, iss =
    team id, iat = now). Token is reusable for ~60 minutes and MUST be
    rotated AT LEAST every 60 min (we cache for 50 min).
  - Body: {"aps": {"alert": {"title":"...","body":"..."}, "badge":N, "sound":"default"},
           "<custom_keys>": "..."}
  - Required header: ``apns-topic: <bundle_id>`` for alert
    notifications.
  - Errors: HTTP status + JSON body {"reason": "BadDeviceToken" | ...}

INVALID_TOKEN reasons (we mark device row inactive):
  BadDeviceToken, Unregistered, DeviceTokenNotForTopic,
  TopicDisallowed, MissingDeviceToken

TRANSIENT (outbox retries):
  ExpiredProviderToken (we re-mint on next call), TooManyRequests,
  ServiceUnavailable, InternalServerError

HTTP/2 requirement:
  Apple ONLY accepts HTTP/2. Requires the ``h2`` package; the
  Dockerfiles add it explicitly. If not installed, httpx silently
  uses HTTP/1.1 and APNS replies with HTTP 400 — we surface this
  as a clear bind-time error.
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
from typing import Any

import httpx

from app.core.errors import IntegrationError
from app.core.logging import get_logger
from app.modules.push.transport.push_base import (
    Notification,
    PushOutcome,
    PushSendResult,
    PushTransport,
)

_logger = get_logger("hypershop.push.apns")

_INVALID_TOKEN_REASONS = {
    "BadDeviceToken",
    "Unregistered",
    "DeviceTokenNotForTopic",
    "TopicDisallowed",
    "MissingDeviceToken",
}


class ApnsTransport(PushTransport):
    name = "apns"
    kind = "apns"

    DEFAULT_TIMEOUT_S = 15.0

    def __init__(
        self, *,
        team_id: str,
        key_id: str,
        private_key_p8: str,
        bundle_id: str,
        is_sandbox: bool = False,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        if not all([team_id, key_id, private_key_p8, bundle_id]):
            raise IntegrationError(
                "ApnsTransport requires team_id, key_id, private_key_p8, "
                "bundle_id.",
                details={"missing_setting": "APNS_*"},
            )
        try:
            from cryptography.hazmat.primitives.serialization import (
                load_pem_private_key,
            )
            self._priv = load_pem_private_key(
                private_key_p8.encode(), password=None,
            )
        except Exception as e:
            raise IntegrationError(
                f"APNS private key load failed: {type(e).__name__}: {e}",
                details={"missing_setting": "APNS_PRIVATE_KEY_P8"},
            ) from e

        # Eagerly fail at bind time if h2 isn't installed — APNS
        # requires HTTP/2.
        try:
            import h2  # noqa: F401
        except ImportError as e:
            raise IntegrationError(
                "APNS requires the 'h2' package for HTTP/2. "
                "Add it to your image (already in Dockerfile + Dockerfile.worker).",
                details={"missing_dep": "h2"},
            ) from e

        self._team_id = team_id
        self._key_id = key_id
        self._bundle_id = bundle_id
        self._base_url = (
            "https://api.sandbox.push.apple.com" if is_sandbox
            else "https://api.push.apple.com"
        )
        self._timeout_s = timeout_s

        self._token_lock = asyncio.Lock()
        self._token: str | None = None
        self._token_expires_at: float = 0.0

    @staticmethod
    def _b64url(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

    def _make_provider_token(self) -> str:
        """Sign the APNS provider JWT (ES256). Cached for ~50 min."""
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec, utils

        now = int(time.time())
        header = {"alg": "ES256", "kid": self._key_id, "typ": "JWT"}
        payload = {"iss": self._team_id, "iat": now}
        signing_input = (
            self._b64url(json.dumps(header, separators=(",", ":")).encode())
            + "."
            + self._b64url(json.dumps(payload, separators=(",", ":")).encode())
        )
        # ES256 = ECDSA with P-256 + SHA256. cryptography returns DER
        # (r,s); APNS wants the raw concatenated 64-byte (r||s) form.
        der_sig = self._priv.sign(
            signing_input.encode(), ec.ECDSA(hashes.SHA256()),
        )
        r, s = utils.decode_dss_signature(der_sig)
        raw_sig = r.to_bytes(32, "big") + s.to_bytes(32, "big")
        return signing_input + "." + self._b64url(raw_sig)

    async def _get_provider_token(self) -> str:
        if self._token and time.time() < self._token_expires_at:
            return self._token
        async with self._token_lock:
            if self._token and time.time() < self._token_expires_at:
                return self._token
            self._token = self._make_provider_token()
            # Cache 50 min; APNS rejects tokens older than ~60 min
            # (ExpiredProviderToken).
            self._token_expires_at = time.time() + 3000
            return self._token

    def _build_payload(self, notification: Notification) -> dict[str, Any]:
        aps: dict[str, Any] = {
            "alert": {
                "title": notification.title,
                "body": notification.body,
            },
            "sound": "default",
        }
        if notification.badge is not None:
            aps["badge"] = int(notification.badge)
        body: dict[str, Any] = {"aps": aps}
        # Apple convention: custom keys live alongside "aps", as
        # top-level keys.
        if notification.data:
            for k, v in notification.data.items():
                if k == "aps":
                    continue
                body[k] = str(v)
        return body

    async def send(
        self, *, token: str, notification: Notification,
    ) -> PushSendResult:
        try:
            jwt_token = await self._get_provider_token()
        except IntegrationError as e:
            return PushSendResult(
                outcome=PushOutcome.TRANSIENT_FAILURE,
                error_code=e.code, error_message=e.message[:512],
            )

        url = f"{self._base_url}/3/device/{token}"
        body = self._build_payload(notification)
        try:
            async with httpx.AsyncClient(
                http2=True, timeout=httpx.Timeout(self._timeout_s),
            ) as c:
                resp = await c.post(
                    url,
                    headers={
                        "authorization": f"bearer {jwt_token}",
                        "apns-topic": self._bundle_id,
                        "apns-push-type": "alert",
                        "apns-priority": "10",
                        "content-type": "application/json",
                    },
                    json=body,
                )
        except httpx.TimeoutException:
            return PushSendResult(
                outcome=PushOutcome.TRANSIENT_FAILURE,
                error_code="timeout",
                error_message=f"APNS timed out after {self._timeout_s}s.",
            )
        except httpx.HTTPError as e:
            return PushSendResult(
                outcome=PushOutcome.TRANSIENT_FAILURE,
                error_code=f"http_{type(e).__name__}",
                error_message=str(e)[:256],
            )

        # APNS returns 200 with EMPTY body on success. The apns-id
        # header carries the message id.
        if resp.status_code == 200:
            return PushSendResult(
                outcome=PushOutcome.DELIVERED,
                message_id=resp.headers.get("apns-id") or None,
            )

        # Failure body: {"reason": "BadDeviceToken"} (sometimes empty).
        try:
            data = resp.json() if resp.text else {}
        except json.JSONDecodeError:
            data = {"raw": resp.text[:256]}
        reason = str(data.get("reason") or "")

        # ExpiredProviderToken — invalidate our cached jwt so the next
        # send re-mints. The current message is TRANSIENT (outbox retry).
        if reason == "ExpiredProviderToken":
            async with self._token_lock:
                self._token = None
                self._token_expires_at = 0.0
            return PushSendResult(
                outcome=PushOutcome.TRANSIENT_FAILURE,
                error_code=reason,
                error_message="APNS provider token expired; will re-mint.",
            )

        if reason in _INVALID_TOKEN_REASONS or resp.status_code in (404, 410):
            _logger.info("apns_invalid_token", token_prefix=token[:8], reason=reason)
            return PushSendResult(
                outcome=PushOutcome.INVALID_TOKEN,
                error_code=reason or "NOT_FOUND",
                error_message=resp.text[:256],
            )

        return PushSendResult(
            outcome=PushOutcome.TRANSIENT_FAILURE,
            error_code=reason or f"http_{resp.status_code}",
            error_message=resp.text[:256],
        )
