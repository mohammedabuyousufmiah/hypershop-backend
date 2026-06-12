"""Default SMS transport — fails loud.

Bound at startup when ``settings.sms_provider`` is unset (or set to
``none``). Any handler that tries to dispatch falls through to a clear
``ServiceUnavailableError`` that the outbox dispatcher catches and
schedules for retry. Operators see the missing-setting reason in logs.
"""

from __future__ import annotations

from app.core.errors import ServiceUnavailableError


class NotConfiguredSmsTransport:
    name = "not_configured"

    async def send(self, *, to: str, text: str) -> None:
        raise ServiceUnavailableError(
            "SMS provider is not configured. Set SMS_PROVIDER + the "
            "matching credentials in env (e.g. SMS_PROVIDER=bulksmsbd + "
            "BULKSMSBD_API_KEY=... + BULKSMSBD_SENDER_ID=...) and "
            "restart so the lifespan rebinds the transport.",
            details={
                "missing_setting": "SMS_PROVIDER",
                "to_prefix": to[:6] if to else "",
            },
        )
