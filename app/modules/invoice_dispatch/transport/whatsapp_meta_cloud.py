"""Meta WhatsApp Cloud API adapter.

Reference:
  - https://developers.facebook.com/docs/whatsapp/cloud-api
  - POST https://graph.facebook.com/<API_VERSION>/<PHONE_NUMBER_ID>/messages
  - Auth: Bearer <ACCESS_TOKEN>
  - Body for template: {
        "messaging_product": "whatsapp",
        "to": "<E.164 minus +>",
        "type": "template",
        "template": {
            "name": "<template_name>",
            "language": {"code": "en"},
            "components": [
                {"type": "header", "parameters": [{"type":"text","text":"..."}]},
                {"type": "body",   "parameters": [{"type":"text","text":"..."}, ...]}
            ]
        }
    }
  - Success response: {"messaging_product":"whatsapp",
                       "contacts":[{"input":"<to>","wa_id":"<id>"}],
                       "messages":[{"id":"wamid.HBg..."}]}
  - Error response: {"error":{"message":"...","type":"...","code":N,
                              "error_subcode":N,"fbtrace_id":"...",
                              "error_data":{"messaging_product":"...",
                                            "details":"..."}}}

Critical error codes for fallback decisions:
  131026 — Receiver is not a valid WhatsApp user → trigger SMS fallback
  131047 — 24-hour conversation window expired (only applies to
           free-form text; templates bypass this) → unlikely for us
  132000 — Template name not found → operator config issue;
           treat as TRANSIENT_FAILURE so SMS fallback rescues the user
  132001 — Template parameter mismatch → same handling
  131056 — Pair rate-limit hit on this phone pair → TRANSIENT, retry

Phone format:
  Meta wants E.164 WITHOUT the leading + (e.g. 8801911740672).

Cold-message constraint:
  Outside an active 24h conversation window, ONLY pre-approved template
  messages can be sent. We always send templates from this adapter, so
  cold messaging works.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

import httpx

from app.core.errors import ValidationError
from app.core.logging import get_logger
from app.modules.invoice_dispatch.transport.whatsapp_base import (
    WhatsAppOutcome,
    WhatsAppSendResult,
    WhatsAppTemplateMessage,
)

_logger = get_logger("hypershop.invoice_dispatch.whatsapp.meta_cloud")

_E164_RE = re.compile(r"^\+[1-9]\d{6,14}$")

# Meta error codes → our outcome enum.
# Any code NOT in either set falls through to TRANSIENT_FAILURE so the
# outbox dispatcher schedules a retry rather than dropping the message.
_NOT_ON_WHATSAPP_CODES = {131026, 131051, 131045}
_PERMANENT_TEMPLATE_CODES = {132000, 132001, 132005, 132012, 132015, 132016, 132069}


def _strip_plus(e164: str) -> str:
    if not _E164_RE.match(e164):
        raise ValidationError(
            f"Phone {e164!r} is not a valid E.164 number.",
            details={"phone": e164},
        )
    return e164[1:]


class MetaCloudWhatsAppTransport:
    name = "meta_cloud"

    DEFAULT_BASE_URL = "https://graph.facebook.com"
    DEFAULT_TIMEOUT_S = 20.0

    def __init__(
        self, *,
        access_token: str,
        phone_number_id: str,
        api_version: str = "v21.0",
        base_url: str | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        if not access_token or not phone_number_id:
            from app.core.errors import IntegrationError
            raise IntegrationError(
                "MetaCloudWhatsAppTransport requires access_token and "
                "phone_number_id.",
                details={"missing_setting": "META_WHATSAPP_*"},
            )
        self._access_token = access_token
        self._phone_number_id = phone_number_id
        self._api_version = api_version.lstrip("v")  # we add the "v" back
        self._base_url = (base_url or self.DEFAULT_BASE_URL).rstrip("/")
        self._timeout_s = timeout_s

    async def send_template(
        self,
        *,
        to: str,
        template: WhatsAppTemplateMessage,
    ) -> WhatsAppSendResult:
        msisdn = _strip_plus(to)

        components: list[dict[str, Any]] = []
        if template.header_parameter is not None:
            components.append({
                "type": "header",
                "parameters": [{"type": "text", "text": template.header_parameter}],
            })
        if template.body_parameters:
            components.append({
                "type": "body",
                "parameters": [
                    {"type": "text", "text": p} for p in template.body_parameters
                ],
            })

        body: dict[str, Any] = {
            "messaging_product": "whatsapp",
            "to": msisdn,
            "type": "template",
            "template": {
                "name": template.name,
                "language": {"code": template.language_code},
            },
        }
        if components:
            body["template"]["components"] = components

        path = f"/v{self._api_version}/{self._phone_number_id}/messages"
        t0 = time.monotonic()
        try:
            async with httpx.AsyncClient(
                base_url=self._base_url,
                timeout=httpx.Timeout(self._timeout_s),
            ) as c:
                resp = await c.post(
                    path,
                    headers={
                        "Authorization": f"Bearer {self._access_token}",
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                    },
                    json=body,
                )
        except httpx.TimeoutException:
            return WhatsAppSendResult(
                outcome=WhatsAppOutcome.TRANSIENT_FAILURE,
                error_code="timeout",
                error_message=f"Meta Cloud timed out after {self._timeout_s}s.",
            )
        except httpx.HTTPError as e:
            return WhatsAppSendResult(
                outcome=WhatsAppOutcome.TRANSIENT_FAILURE,
                error_code=f"http_{type(e).__name__}",
                error_message=str(e)[:512],
            )

        elapsed_ms = int((time.monotonic() - t0) * 1000)
        try:
            data = resp.json() if resp.text else {}
        except json.JSONDecodeError:
            return WhatsAppSendResult(
                outcome=WhatsAppOutcome.TRANSIENT_FAILURE,
                error_code="bad_json",
                error_message=resp.text[:256],
            )

        if 200 <= resp.status_code < 300:
            messages = data.get("messages") or []
            wamid = messages[0].get("id") if messages else None
            _logger.info(
                "whatsapp_sent",
                provider="meta_cloud",
                to_prefix=to[:6],
                template=template.name,
                wamid=wamid,
                elapsed_ms=elapsed_ms,
            )
            return WhatsAppSendResult(
                outcome=WhatsAppOutcome.DELIVERED,
                message_id=wamid,
            )

        # Error path — extract Meta's error code.
        err = data.get("error") if isinstance(data, dict) else None
        code = err.get("code") if isinstance(err, dict) else None
        message = (err.get("message") if isinstance(err, dict) else resp.text)[:512]

        if isinstance(code, int) and code in _NOT_ON_WHATSAPP_CODES:
            _logger.info(
                "whatsapp_recipient_not_on_whatsapp",
                provider="meta_cloud",
                to_prefix=to[:6],
                code=code,
            )
            return WhatsAppSendResult(
                outcome=WhatsAppOutcome.NOT_ON_WHATSAPP,
                error_code=str(code),
                error_message=str(message),
            )

        if isinstance(code, int) and code in _PERMANENT_TEMPLATE_CODES:
            _logger.warning(
                "whatsapp_template_problem",
                provider="meta_cloud",
                template=template.name,
                code=code,
                message=message,
            )

        # Auth failures live in 401/403; treat as TRANSIENT (likely token
        # rotated mid-deploy — operator should be alerted via 5xx alarm).
        return WhatsAppSendResult(
            outcome=WhatsAppOutcome.TRANSIENT_FAILURE,
            error_code=str(code) if code is not None else f"http_{resp.status_code}",
            error_message=str(message),
        )
