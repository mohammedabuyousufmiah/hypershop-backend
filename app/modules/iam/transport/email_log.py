"""Dev/test email transport that logs the message instead of sending.

Used when SMTP env vars are empty (local dev, CI). Keeps the
register/OTP/password flows working without a real SMTP server.
Production must bind ``SmtpEmailTransport`` via env config.
"""
from __future__ import annotations

from app.core.logging import get_logger

_logger = get_logger("hypershop.iam.email.log")


class LogOnlyEmailTransport:
    async def send(
        self,
        *,
        to: str,
        subject: str,
        text_body: str,
        html_body: str | None = None,
    ) -> None:
        _logger.info(
            "email_log_only_dispatch",
            extra={
                "to": to,
                "subject": subject,
                "preview": text_body[:200],
                "has_html": html_body is not None,
            },
        )
