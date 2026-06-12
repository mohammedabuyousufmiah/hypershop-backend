"""SMS transport contract.

This file ships the *interface* only — concrete adapters (SSL Wireless,
Twilio, AWS SNS, BulkSMSBD, …) are added one at a time, each as its own
sibling module (``sms_ssl_wireless.py``, ``sms_twilio.py``, …) once the
provider is chosen and credentials are available.

By design there is **no** default / no-op / fake adapter. If the IAM
service is asked to dispatch an SMS without a real adapter wired, it must
raise ``ServiceUnavailableError`` so the outbox marks the message for retry
rather than silently dropping it.

Adapter implementation rules (per the project's no-placeholders rule):

1. Real HTTP/SDK calls only. No ``print``, no ``pass``-body adapters.
2. All credentials come from env (``SMS_<PROVIDER>_*``) — never hardcoded,
   never committed.
3. Failures map to domain errors:
   - 4xx auth/quota                → ``IntegrationError``
   - Network/timeouts/5xx          → ``ServiceUnavailableError``
   - Anything else                 → ``IntegrationError``
4. Phone numbers arrive in E.164 (``+8801911740672`` shape). Adapter is
   responsible for any provider-specific formatting (e.g. dropping the
   leading ``+`` for SSL Wireless's REST API).
5. Adapters MUST be safe to call from the ARQ worker — no FastAPI request
   state, no DB session capture, no global mutables.
"""

from __future__ import annotations

from typing import Protocol


class SmsTransport(Protocol):
    """Outgoing transactional SMS transport.

    Single method by design — SMS is fire-and-acknowledge, not a thread.
    """

    async def send(
        self,
        *,
        to: str,
        text: str,
    ) -> None:
        """Send ``text`` to E.164 number ``to``.

        Raises:
            ValidationError: ``to`` is not a valid E.164 number.
            IntegrationError: provider rejected the request (auth, quota,
                payload).
            ServiceUnavailableError: network failure, timeout, provider 5xx.
        """
        ...
