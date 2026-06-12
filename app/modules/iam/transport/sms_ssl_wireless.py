"""SSL Wireless adapter (BD enterprise SMS aggregator).

Reference:
  - https://smsplus.sslwireless.com/api-documentation
  - JSON POST to /api/v3/send-sms with body:
      {
        "api_token": "<api_token>",
        "sid": "<approved_sender_id>",
        "msisdn": "8801XXXXXXXXX",
        "sms": "<text>",
        "csms_id": "<unique_per_request>"
      }
  - Successful response: {"status":"SUCCESS","status_code":200,"smsinfo":[{...}]}
  - Failure responses set status to "FAILED" or "ERROR" with explanatory message.

Phone format: SSL Wireless wants MSISDN without leading + (8801XXXXXXXXX).
"""

from __future__ import annotations

import json
import re
import secrets

import httpx

from app.core.errors import (
    IntegrationError,
    ServiceUnavailableError,
    ValidationError,
)
from app.core.logging import get_logger

_logger = get_logger("hypershop.iam.sms.ssl_wireless")

_E164_RE = re.compile(r"^\+[1-9]\d{6,14}$")


def _to_msisdn(e164: str) -> str:
    if not _E164_RE.match(e164):
        raise ValidationError(
            f"Phone {e164!r} is not a valid E.164 number.",
            details={"phone": e164},
        )
    return e164[1:]


class SslWirelessTransport:
    name = "ssl_wireless"

    DEFAULT_BASE_URL = "https://smsplus.sslwireless.com"

    def __init__(
        self, *,
        api_token: str,
        sid: str,
        base_url: str | None = None,
        timeout_s: float = 15.0,
    ) -> None:
        if not api_token or not sid:
            raise IntegrationError(
                "SslWirelessTransport requires api_token and sid.",
                details={"missing_setting": "SSL_WIRELESS_*"},
            )
        self._api_token = api_token
        self._sid = sid
        self._base_url = (base_url or self.DEFAULT_BASE_URL).rstrip("/")
        self._timeout_s = timeout_s

    async def send(self, *, to: str, text: str) -> None:
        msisdn = _to_msisdn(to)
        body = {
            "api_token": self._api_token,
            "sid": self._sid,
            "msisdn": msisdn,
            "sms": text,
            "csms_id": secrets.token_hex(16),  # idempotency key per send
        }
        try:
            async with httpx.AsyncClient(
                base_url=self._base_url,
                timeout=httpx.Timeout(self._timeout_s),
            ) as c:
                resp = await c.post(
                    "/api/v3/send-sms",
                    headers={
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                    },
                    json=body,
                )
        except httpx.TimeoutException as e:
            raise ServiceUnavailableError(
                f"SSL Wireless timed out after {self._timeout_s}s.",
                details={"to_prefix": to[:6]},
            ) from e
        except httpx.HTTPError as e:
            raise ServiceUnavailableError(
                f"SSL Wireless HTTP error: {type(e).__name__}.",
                details={"to_prefix": to[:6], "error": str(e)[:256]},
            ) from e

        if resp.status_code >= 500:
            raise ServiceUnavailableError(
                f"SSL Wireless server error {resp.status_code}.",
                details={"status": resp.status_code, "body": resp.text[:512]},
            )
        if resp.status_code in (401, 403):
            raise IntegrationError(
                "SSL Wireless rejected credentials.",
                details={"status": resp.status_code, "body": resp.text[:512]},
            )

        try:
            data = resp.json()
        except json.JSONDecodeError as e:
            raise IntegrationError(
                "SSL Wireless returned non-JSON.",
                details={"body": resp.text[:512]},
            ) from e

        status = (data.get("status") or "").upper()
        if status != "SUCCESS":
            raise IntegrationError(
                f"SSL Wireless rejected the SMS (status={status!r}).",
                details={
                    "status": status,
                    "status_code": data.get("status_code"),
                    "error_message": data.get("error_message") or data.get("message"),
                    "to_prefix": to[:6],
                },
            )
        sms_info = data.get("smsinfo") or []
        ref_id = (sms_info[0].get("reference_id") if sms_info else None)
        _logger.info(
            "sms_sent",
            provider="ssl_wireless",
            to_prefix=to[:6],
            reference_id=ref_id,
        )
