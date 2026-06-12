"""Twilio SMS adapter (international fallback for non-BD numbers).

Reference:
  - https://www.twilio.com/docs/sms/api/message-resource
  - POST https://api.twilio.com/2010-04-01/Accounts/{ACCOUNT_SID}/Messages.json
  - Auth: HTTP Basic (account_sid : auth_token)
  - Form-encoded body: To, From, Body
  - Response: JSON with sid, status (queued|sent|delivered|failed|...).

Phone format: Twilio accepts E.164 directly (with leading +).
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

_logger = get_logger("hypershop.iam.sms.twilio")

_E164_RE = re.compile(r"^\+[1-9]\d{6,14}$")


def _check_e164(phone: str) -> str:
    if not _E164_RE.match(phone):
        raise ValidationError(
            f"Phone {phone!r} is not a valid E.164 number.",
            details={"phone": phone},
        )
    return phone


class TwilioTransport:
    name = "twilio"

    DEFAULT_BASE_URL = "https://api.twilio.com"

    def __init__(
        self, *,
        account_sid: str,
        auth_token: str,
        from_number: str,
        base_url: str | None = None,
        timeout_s: float = 15.0,
    ) -> None:
        if not account_sid or not auth_token or not from_number:
            raise IntegrationError(
                "TwilioTransport requires account_sid, auth_token, "
                "from_number.",
                details={"missing_setting": "TWILIO_*"},
            )
        # Validate from_number once; saves a per-call check.
        _check_e164(from_number)
        self._account_sid = account_sid
        self._auth_token = auth_token
        self._from = from_number
        self._base_url = (base_url or self.DEFAULT_BASE_URL).rstrip("/")
        self._timeout_s = timeout_s

    async def send(self, *, to: str, text: str) -> None:
        _check_e164(to)
        path = f"/2010-04-01/Accounts/{self._account_sid}/Messages.json"
        try:
            async with httpx.AsyncClient(
                base_url=self._base_url,
                timeout=httpx.Timeout(self._timeout_s),
                auth=(self._account_sid, self._auth_token),
            ) as c:
                resp = await c.post(
                    path,
                    headers={"Accept": "application/json"},
                    data={
                        "To": to,
                        "From": self._from,
                        "Body": text,
                    },
                )
        except httpx.TimeoutException as e:
            raise ServiceUnavailableError(
                f"Twilio timed out after {self._timeout_s}s.",
                details={"to_prefix": to[:6]},
            ) from e
        except httpx.HTTPError as e:
            raise ServiceUnavailableError(
                f"Twilio HTTP error: {type(e).__name__}.",
                details={"to_prefix": to[:6], "error": str(e)[:256]},
            ) from e

        if resp.status_code in (401, 403):
            raise IntegrationError(
                "Twilio rejected credentials.",
                details={"status": resp.status_code, "body": resp.text[:512]},
            )
        if resp.status_code >= 500:
            raise ServiceUnavailableError(
                f"Twilio server error {resp.status_code}.",
                details={"status": resp.status_code, "body": resp.text[:512]},
            )
        try:
            data = resp.json()
        except json.JSONDecodeError as e:
            raise IntegrationError(
                "Twilio returned non-JSON.",
                details={"body": resp.text[:512]},
            ) from e

        if resp.status_code >= 400:
            raise IntegrationError(
                f"Twilio rejected the SMS ({resp.status_code}).",
                details={
                    "status": resp.status_code,
                    "code": data.get("code"),
                    "message": data.get("message"),
                    "to_prefix": to[:6],
                },
            )

        status = data.get("status")
        # Twilio uses 'queued' / 'accepted' / 'sending' / 'sent' / 'delivered'
        # / 'undelivered' / 'failed'. Anything not in the success path (and
        # also not the queued path which means accepted) is a failure.
        if status in ("undelivered", "failed"):
            raise IntegrationError(
                f"Twilio reported delivery failure (status={status!r}).",
                details={
                    "status": status,
                    "error_code": data.get("error_code"),
                    "error_message": data.get("error_message"),
                    "to_prefix": to[:6],
                },
            )
        _logger.info(
            "sms_sent",
            provider="twilio",
            to_prefix=to[:6],
            sid=data.get("sid"),
            status=status,
        )
