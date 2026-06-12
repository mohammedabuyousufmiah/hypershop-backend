from __future__ import annotations

from typing import Protocol


class EmailMessage(Protocol):
    to: str
    subject: str
    text: str
    html: str | None


class EmailTransport(Protocol):
    """Outgoing transactional email transport.

    Implementations are concrete (real SMTP / real provider HTTP API) — never
    no-op stubs. The outbox dispatcher hands a message to the transport; the
    transport raises on failure so the dispatcher can retry with backoff.
    """

    async def send(
        self,
        *,
        to: str,
        subject: str,
        text_body: str,
        html_body: str | None = None,
    ) -> None: ...
