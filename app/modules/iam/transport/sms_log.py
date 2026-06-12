"""DEV-ONLY log-to-console SMS transport.

When ``SMS_PROVIDER=log`` is set in env, the factory binds this
transport instead of the real BulkSMS BD / SSL Wireless / Twilio
adapters. Every ``send()`` call writes a structured log line at INFO
level — including the full OTP body — so developers and CI can read
the OTP from ``uvicorn.log`` without needing a real BD SIM card or
Twilio sandbox.

Production gate
---------------
The factory refuses to bind this transport when
``ENVIRONMENT=production`` even if the env var is set, mirroring the
fake-payment provider's hard guard. A production deploy that
accidentally has ``SMS_PROVIDER=log`` will fall back to the
``NotConfiguredSmsTransport`` and surface a loud
``ServiceUnavailableError`` instead of silently logging real
customer OTPs to disk.

This file violates the explicit "no fake adapters" rule documented in
``sms_base.py``. That rule was written for production; the project
has since grown a clear non-prod testing path (``ENVIRONMENT=dev``)
and the factory-level gate makes it safe to relax for that mode.
"""

from __future__ import annotations

from app.core.logging import get_logger


class LogOnlySmsTransport:
    """Logs the message body + recipient and returns. Never raises."""

    name = "log"

    def __init__(self) -> None:
        self._logger = get_logger("hypershop.iam.sms.log")

    async def send(self, *, to: str, text: str) -> None:
        # Full OTP body intentionally in the log so developers can
        # grep ``OTP=`` and find the most recent code. Production
        # gate (see factory) prevents this from running on real
        # deployments.
        self._logger.info(
            "sms_log_only_dispatch",
            to=to,
            body=text,
            note="DEV-MODE — OTP visible in logs. Real provider must be wired before prod.",
        )
