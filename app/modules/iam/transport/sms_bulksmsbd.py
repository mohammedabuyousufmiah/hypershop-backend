"""BulkSMSBD adapter (BD-native SMS aggregator).

Reference:
  - http://api.bulksmsbd.net/api/smsapi
  - GET / POST with params:
      api_key=<api_key>&type=text&number=<msisdn,msisdn,...>&senderid=<sid>&message=<text>
  - Response is plain text JSON: {"response_code":202,"message_id":...,"success_message":"SMS Submitted Successfully","error_message":""}
  - response_code 202 = accepted; anything else is an error.

Phone format: BulkSMSBD wants MSISDN without leading + (e.g. 8801911740672).
"""

from __future__ import annotations

import json
import re

import httpx

from app.core.errors import (
    IntegrationError,
    ServiceUnavailableError,
    ValidationError,
)
from app.core.logging import get_logger

_logger = get_logger("hypershop.iam.sms.bulksmsbd")

_E164_RE = re.compile(r"^\+[1-9]\d{6,14}$")


def _to_msisdn(e164: str) -> str:
    """Strip the leading '+' BulkSMSBD wants."""
    if not _E164_RE.match(e164):
        raise ValidationError(
            f"Phone {e164!r} is not a valid E.164 number.",
            details={"phone": e164},
        )
    return e164[1:]


class BulkSmsBdTransport:
    name = "bulksmsbd"

    DEFAULT_BASE_URL = "http://bulksmsbd.net/api"

    def __init__(
        self, *,
        api_key: str,
        sender_id: str,
        base_url: str | None = None,
        timeout_s: float = 15.0,
    ) -> None:
        if not api_key or not sender_id:
            raise IntegrationError(
                "BulkSmsBdTransport requires api_key and sender_id.",
                details={"missing_setting": "BULKSMSBD_*"},
            )
        self._api_key = api_key
        self._sender_id = sender_id
        self._base_url = (base_url or self.DEFAULT_BASE_URL).rstrip("/")
        self._timeout_s = timeout_s

    async def send(self, *, to: str, text: str) -> None:
        msisdn = _to_msisdn(to)
        params = {
            "api_key": self._api_key,
            "type": "text",
            "number": msisdn,
            "senderid": self._sender_id,
            "message": text,
        }
        try:
            async with httpx.AsyncClient(
                base_url=self._base_url,
                timeout=httpx.Timeout(self._timeout_s),
            ) as c:
                resp = await c.get("/smsapi", params=params)
        except httpx.TimeoutException as e:
            raise ServiceUnavailableError(
                f"BulkSMSBD timed out after {self._timeout_s}s.",
                details={"to_prefix": to[:6]},
            ) from e
        except httpx.HTTPError as e:
            raise ServiceUnavailableError(
                f"BulkSMSBD HTTP error: {type(e).__name__}.",
                details={"to_prefix": to[:6], "error": str(e)[:256]},
            ) from e

        if resp.status_code >= 500:
            raise ServiceUnavailableError(
                f"BulkSMSBD server error {resp.status_code}.",
                details={"status": resp.status_code, "body": resp.text[:512]},
            )
        try:
            data = resp.json()
        except json.JSONDecodeError as e:
            raise IntegrationError(
                "BulkSMSBD returned non-JSON.",
                details={"body": resp.text[:512]},
            ) from e

        rc = data.get("response_code")
        if rc != 202:
            raise IntegrationError(
                f"BulkSMSBD rejected the SMS (response_code={rc}).",
                details={
                    "response_code": rc,
                    "error_message": data.get("error_message") or data.get("message"),
                    "to_prefix": to[:6],
                },
            )
        _logger.info(
            "sms_sent",
            provider="bulksmsbd",
            to_prefix=to[:6],
            message_id=data.get("message_id"),
        )
