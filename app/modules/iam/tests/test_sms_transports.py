"""Unit tests for the SMS transport adapters.

These don't make real HTTP calls. They cover:
  - Constructor refusal when creds are missing (every adapter)
  - E.164 phone-number validation in BulkSMSBD + SSL Wireless
  - NotConfigured raises ServiceUnavailableError with the
    documented missing_setting sentinel
  - The factory returns NotConfigured when SMS_PROVIDER is unset/none
  - Registry binding round-trips
"""

from __future__ import annotations

import pytest

from app.core.errors import (
    IntegrationError,
    ServiceUnavailableError,
    ValidationError,
)
from app.modules.iam.transport.sms_bulksmsbd import (
    BulkSmsBdTransport,
    _to_msisdn as _bulk_to_msisdn,
)
from app.modules.iam.transport.sms_not_configured import (
    NotConfiguredSmsTransport,
)
from app.modules.iam.transport.sms_registry import (
    bind_transport,
    get_transport,
    reset_transport_binding,
)
from app.modules.iam.transport.sms_ssl_wireless import (
    SslWirelessTransport,
    _to_msisdn as _ssl_to_msisdn,
)
from app.modules.iam.transport.sms_twilio import TwilioTransport, _check_e164


# ---------------- Construction refusal ----------------


def test_bulksmsbd_refuses_without_creds() -> None:
    with pytest.raises(IntegrationError) as exc:
        BulkSmsBdTransport(api_key="", sender_id="")
    assert exc.value.details.get("missing_setting") == "BULKSMSBD_*"


def test_ssl_wireless_refuses_without_creds() -> None:
    with pytest.raises(IntegrationError) as exc:
        SslWirelessTransport(api_token="", sid="")
    assert exc.value.details.get("missing_setting") == "SSL_WIRELESS_*"


def test_twilio_refuses_without_creds() -> None:
    with pytest.raises(IntegrationError) as exc:
        TwilioTransport(account_sid="", auth_token="", from_number="")
    assert exc.value.details.get("missing_setting") == "TWILIO_*"


def test_twilio_refuses_with_invalid_from_number() -> None:
    with pytest.raises(ValidationError):
        TwilioTransport(
            account_sid="AC123",
            auth_token="abc",
            from_number="not-a-phone",
        )


# ---------------- E.164 helpers ----------------


@pytest.mark.parametrize(
    ("e164", "expected_msisdn"),
    [
        ("+8801911740672", "8801911740672"),
        ("+13105551212", "13105551212"),
        ("+447700900123", "447700900123"),
    ],
)
def test_bulksmsbd_to_msisdn_strips_plus(e164: str, expected_msisdn: str) -> None:
    assert _bulk_to_msisdn(e164) == expected_msisdn


@pytest.mark.parametrize("bad", ["8801911740672", "+0", "+abc", "", "not-a-number"])
def test_bulksmsbd_rejects_non_e164(bad: str) -> None:
    with pytest.raises(ValidationError):
        _bulk_to_msisdn(bad)


@pytest.mark.parametrize("bad", ["8801911740672", "+0", "+abc", ""])
def test_ssl_wireless_rejects_non_e164(bad: str) -> None:
    with pytest.raises(ValidationError):
        _ssl_to_msisdn(bad)


@pytest.mark.parametrize("bad", ["8801911740672", "+0", "+abc", ""])
def test_twilio_check_rejects_non_e164(bad: str) -> None:
    with pytest.raises(ValidationError):
        _check_e164(bad)


# ---------------- NotConfigured behaviour ----------------


@pytest.mark.asyncio
async def test_not_configured_raises_with_missing_setting() -> None:
    t = NotConfiguredSmsTransport()
    with pytest.raises(ServiceUnavailableError) as exc:
        await t.send(to="+8801911740672", text="hello")
    assert exc.value.details.get("missing_setting") == "SMS_PROVIDER"


# ---------------- Registry binding ----------------


def test_registry_binding_round_trip() -> None:
    reset_transport_binding()
    # Default is NotConfigured.
    assert get_transport().__class__.__name__ == "NotConfiguredSmsTransport"
    # Bind a concrete adapter (constructor needs valid args).
    t = BulkSmsBdTransport(api_key="k", sender_id="HYPERSHOP")
    bind_transport(t)
    assert get_transport() is t
    reset_transport_binding()
    assert get_transport().__class__.__name__ == "NotConfiguredSmsTransport"
