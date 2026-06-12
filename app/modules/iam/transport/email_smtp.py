from __future__ import annotations

from email.message import EmailMessage as MimeMessage
from email.utils import formataddr, formatdate, make_msgid

import aiosmtplib

from app.core.config import get_settings
from app.core.errors import IntegrationError, ServiceUnavailableError


class SmtpEmailTransport:
    """Real SMTP transport using ``aiosmtplib``.

    Connection settings come from env vars exclusively (``SMTP_HOST``,
    ``SMTP_PORT``, ``SMTP_USERNAME``, ``SMTP_PASSWORD``, ``SMTP_USE_TLS``).
    Misconfiguration raises ``ServiceUnavailableError`` at send-time so the
    outbox marks the message for retry rather than dead-letter on first miss.
    """

    async def send(
        self,
        *,
        to: str,
        subject: str,
        text_body: str,
        html_body: str | None = None,
    ) -> None:
        cfg = get_settings()
        if not cfg.smtp_host or not cfg.smtp_sender:
            raise ServiceUnavailableError(
                "SMTP is not configured. Set SMTP_HOST and SMTP_SENDER.",
                details={"missing": "smtp_host_or_smtp_sender"},
            )

        msg = MimeMessage()
        msg["From"] = formataddr(("Hypershop", cfg.smtp_sender))
        msg["To"] = to
        msg["Subject"] = subject
        msg["Date"] = formatdate(localtime=False, usegmt=True)
        msg["Message-ID"] = make_msgid(domain="hypershop.local")
        msg.set_content(text_body)
        if html_body is not None:
            msg.add_alternative(html_body, subtype="html")

        password = cfg.smtp_password.get_secret_value() if cfg.smtp_password is not None else None

        try:
            await aiosmtplib.send(
                msg,
                hostname=cfg.smtp_host,
                port=cfg.smtp_port,
                start_tls=cfg.smtp_use_tls,
                username=cfg.smtp_username,
                password=password,
                timeout=15,
            )
        except aiosmtplib.SMTPAuthenticationError as e:
            raise IntegrationError(
                "SMTP authentication failed.",
                details={"smtp_host": cfg.smtp_host},
            ) from e
        except (
            aiosmtplib.SMTPConnectError,
            aiosmtplib.SMTPServerDisconnected,
            aiosmtplib.SMTPTimeoutError,
        ) as e:
            raise ServiceUnavailableError(
                "SMTP server unreachable.",
                details={"smtp_host": cfg.smtp_host},
            ) from e
        except aiosmtplib.SMTPException as e:
            raise IntegrationError(
                "SMTP send failed.",
                details={"smtp_host": cfg.smtp_host},
            ) from e
