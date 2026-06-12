"""Firebase Cloud Messaging (FCM) HTTP v1 adapter.

Reference:
  - https://firebase.google.com/docs/cloud-messaging/migrate-v1
  - POST https://fcm.googleapis.com/v1/projects/{PROJECT_ID}/messages:send
  - Auth: OAuth2 access token issued from a service-account JSON key.
  - Body: {"message": {"token": "...", "notification": {...}, "data": {...},
                       "android": {"priority":"high"}, "webpush": {...}}}
  - Success: 200 + {"name": "projects/.../messages/0:..."}
  - Errors:
      403 SenderIdMismatch         → likely token from another project
      400 InvalidArgument           → malformed request (we caused it)
      404 NOT_FOUND                 → token unregistered (treat as INVALID_TOKEN)
      404 UNREGISTERED              → token expired (INVALID_TOKEN)
      404 INVALID_REGISTRATION       → bad token (INVALID_TOKEN)
      429 QuotaExceeded              → TRANSIENT
      5xx Internal/Unavailable       → TRANSIENT

Auth model:
  - We sign a JWT (RS256) asserting the service account's email +
    scope=https://www.googleapis.com/auth/firebase.messaging, exchange
    it at https://oauth2.googleapis.com/token for a Bearer access token,
    cache the token until expiry-60s.
  - Service account JSON is loaded from env as a JSON string (so a
    single SecretStr holds the whole key file).
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

_logger = get_logger("hypershop.push.fcm")

# FCM error codes that indicate a permanently dead token.
_INVALID_TOKEN_CODES = {
    "UNREGISTERED",
    "INVALID_ARGUMENT",
    "SENDER_ID_MISMATCH",
    "NOT_FOUND",
    "INVALID_REGISTRATION",
}


class FcmHttpV1Transport(PushTransport):
    name = "fcm_http_v1"
    kind = "fcm"

    DEFAULT_TIMEOUT_S = 15.0

    def __init__(
        self, *,
        service_account_json: str,
        project_id: str | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        try:
            sa = json.loads(service_account_json)
        except json.JSONDecodeError as e:
            raise IntegrationError(
                "FCM_SERVICE_ACCOUNT_JSON is not parseable JSON.",
                details={"missing_setting": "FCM_*", "error": str(e)},
            ) from e
        client_email = sa.get("client_email")
        private_key_pem = sa.get("private_key")
        sa_project_id = sa.get("project_id")
        if not client_email or not private_key_pem:
            raise IntegrationError(
                "FCM service account JSON missing client_email / private_key.",
                details={"missing_setting": "FCM_*"},
            )
        try:
            from cryptography.hazmat.primitives.serialization import (
                load_pem_private_key,
            )
            self._priv = load_pem_private_key(
                private_key_pem.encode(), password=None,
            )
        except Exception as e:
            raise IntegrationError(
                f"FCM private key load failed: {type(e).__name__}: {e}",
                details={"missing_setting": "FCM_*"},
            ) from e

        self._client_email = client_email
        self._project_id = project_id or sa_project_id
        if not self._project_id:
            raise IntegrationError(
                "FCM project_id missing (set FCM_PROJECT_ID or include "
                "project_id in the service account JSON).",
                details={"missing_setting": "FCM_PROJECT_ID"},
            )
        self._timeout_s = timeout_s

        self._token_lock = asyncio.Lock()
        self._token: str | None = None
        self._token_expires_at: float = 0.0

    # ---------------- OAuth2 ----------------

    @staticmethod
    def _b64url(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

    def _make_assertion_jwt(self) -> str:
        """Build + sign the OAuth2 service-account JWT (RS256)."""
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding

        now = int(time.time())
        header = {"alg": "RS256", "typ": "JWT"}
        payload = {
            "iss": self._client_email,
            "scope": "https://www.googleapis.com/auth/firebase.messaging",
            "aud": "https://oauth2.googleapis.com/token",
            "exp": now + 3600,
            "iat": now,
        }
        signing_input = (
            self._b64url(json.dumps(header, separators=(",", ":")).encode())
            + "."
            + self._b64url(json.dumps(payload, separators=(",", ":")).encode())
        )
        signature = self._priv.sign(
            signing_input.encode(), padding.PKCS1v15(), hashes.SHA256(),
        )
        return signing_input + "." + self._b64url(signature)

    async def _get_access_token(self) -> str:
        if self._token and time.time() < self._token_expires_at:
            return self._token
        async with self._token_lock:
            if self._token and time.time() < self._token_expires_at:
                return self._token
            assertion = self._make_assertion_jwt()
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(self._timeout_s)) as c:
                    resp = await c.post(
                        "https://oauth2.googleapis.com/token",
                        headers={"Content-Type": "application/x-www-form-urlencoded"},
                        data={
                            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                            "assertion": assertion,
                        },
                    )
            except httpx.HTTPError as e:
                raise IntegrationError(
                    f"FCM token exchange HTTP error: {type(e).__name__}.",
                    details={"error": str(e)[:256]},
                ) from e
            if resp.status_code != 200:
                raise IntegrationError(
                    f"FCM token exchange failed (HTTP {resp.status_code}).",
                    details={"body": resp.text[:512]},
                )
            data = resp.json()
            self._token = str(data.get("access_token") or "")
            ttl = int(data.get("expires_in") or 3600)
            self._token_expires_at = time.time() + max(60, ttl - 60)
            if not self._token:
                raise IntegrationError(
                    "FCM token exchange returned no access_token.",
                    details={"body": data},
                )
            return self._token

    # ---------------- Send ----------------

    def _build_message(
        self, *, token: str, notification: Notification,
    ) -> dict[str, Any]:
        msg: dict[str, Any] = {
            "token": token,
            "notification": {
                "title": notification.title,
                "body": notification.body,
            },
            "android": {"priority": "HIGH"},
        }
        if notification.data:
            # FCM HTTP v1 requires data values to be strings.
            msg["data"] = {k: str(v) for k, v in notification.data.items()}
        if notification.badge is not None:
            # Web push surface uses badge; Android ignores.
            msg.setdefault("webpush", {}).setdefault("notification", {})["badge"] = str(
                notification.badge,
            )
        return {"message": msg}

    async def send(
        self, *, token: str, notification: Notification,
    ) -> PushSendResult:
        try:
            access_token = await self._get_access_token()
        except IntegrationError as e:
            return PushSendResult(
                outcome=PushOutcome.TRANSIENT_FAILURE,
                error_code=e.code,
                error_message=e.message[:512],
            )

        body = self._build_message(token=token, notification=notification)
        url = f"https://fcm.googleapis.com/v1/projects/{self._project_id}/messages:send"
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(self._timeout_s)) as c:
                resp = await c.post(
                    url,
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Content-Type": "application/json",
                    },
                    json=body,
                )
        except httpx.TimeoutException:
            return PushSendResult(
                outcome=PushOutcome.TRANSIENT_FAILURE,
                error_code="timeout",
                error_message=f"FCM timed out after {self._timeout_s}s.",
            )
        except httpx.HTTPError as e:
            return PushSendResult(
                outcome=PushOutcome.TRANSIENT_FAILURE,
                error_code=f"http_{type(e).__name__}",
                error_message=str(e)[:256],
            )

        try:
            data = resp.json() if resp.text else {}
        except json.JSONDecodeError:
            return PushSendResult(
                outcome=PushOutcome.TRANSIENT_FAILURE,
                error_code="bad_json",
                error_message=resp.text[:256],
            )

        if 200 <= resp.status_code < 300:
            return PushSendResult(
                outcome=PushOutcome.DELIVERED,
                message_id=str(data.get("name", "")),
            )

        # FCM v1 error envelope:
        # {"error":{"code":404,"message":"...","status":"NOT_FOUND",
        #           "details":[{"@type":".../FcmError","errorCode":"UNREGISTERED"},
        #                      {"@type":".../OAuthError","errorCode":"..."}]}}
        # Walk ALL details: prefer an INVALID_TOKEN signal over any other
        # error code in the same response (otherwise the first detail
        # might be a generic OAuth/quota error and we'd miss the token-
        # is-dead signal that the SECOND detail carries).
        err = data.get("error") if isinstance(data, dict) else None
        status_str = ""
        fcm_code = ""
        all_codes: list[str] = []
        if isinstance(err, dict):
            status_str = str(err.get("status") or "")
            for detail in err.get("details") or []:
                if isinstance(detail, dict) and "errorCode" in detail:
                    code = str(detail["errorCode"])
                    all_codes.append(code)
                    if code.upper() in _INVALID_TOKEN_CODES:
                        fcm_code = code
                        break
            if not fcm_code and all_codes:
                fcm_code = all_codes[0]
        composite = (fcm_code or status_str).upper()
        if composite in _INVALID_TOKEN_CODES or resp.status_code == 404:
            _logger.info(
                "fcm_invalid_token",
                token_prefix=token[:8],
                code=composite,
            )
            return PushSendResult(
                outcome=PushOutcome.INVALID_TOKEN,
                error_code=composite or "NOT_FOUND",
                error_message=(err or {}).get("message", "")[:512],
            )

        return PushSendResult(
            outcome=PushOutcome.TRANSIENT_FAILURE,
            error_code=composite or f"http_{resp.status_code}",
            error_message=(err or {}).get("message", resp.text)[:512],
        )
