"""Unit tests for push transports + registry — no real HTTP calls."""

from __future__ import annotations

import json

import pytest

from app.core.errors import IntegrationError
from app.modules.push.transport.push_apns import ApnsTransport
from app.modules.push.transport.push_base import (
    Notification,
    PushOutcome,
    PushSendResult,
)
from app.modules.push.transport.push_fcm import FcmHttpV1Transport
from app.modules.push.transport.push_not_configured import (
    NotConfiguredPushTransport,
)
from app.modules.push.transport.push_registry import (
    bind_transport,
    get_transport,
    list_bound_kinds,
    reset_transport_binding,
)


# ---------------- Construction refusal ----------------


def test_fcm_refuses_with_invalid_json() -> None:
    with pytest.raises(IntegrationError) as exc:
        FcmHttpV1Transport(service_account_json="not-json")
    assert exc.value.details.get("missing_setting") == "FCM_*"


def test_fcm_refuses_with_missing_keys() -> None:
    sa = json.dumps({"client_email": "x@y.iam.gserviceaccount.com"})  # no private_key
    with pytest.raises(IntegrationError) as exc:
        FcmHttpV1Transport(service_account_json=sa)
    assert exc.value.details.get("missing_setting") == "FCM_*"


def test_apns_refuses_without_creds() -> None:
    with pytest.raises(IntegrationError) as exc:
        ApnsTransport(
            team_id="", key_id="", private_key_p8="", bundle_id="",
        )
    assert exc.value.details.get("missing_setting") == "APNS_*"


# ---------------- NotConfigured ----------------


@pytest.mark.asyncio
async def test_not_configured_returns_transient_failure() -> None:
    t = NotConfiguredPushTransport()
    result = await t.send(
        token="fake-token",
        notification=Notification(title="t", body="b"),
    )
    assert result.outcome == PushOutcome.TRANSIENT_FAILURE
    assert result.error_code == "not_configured"


# ---------------- Registry binding ----------------


def test_registry_per_kind_binding() -> None:
    reset_transport_binding()
    assert list_bound_kinds() == []
    # When unbound, get_transport returns NotConfigured (never None)
    assert get_transport("fcm").__class__.__name__ == "NotConfiguredPushTransport"
    assert get_transport("apns").__class__.__name__ == "NotConfiguredPushTransport"

    # Bind a fake transport for one kind only
    class FakeFcm:
        name = "fake_fcm"
        kind = "fcm"
        async def send(self, *, token, notification):
            return PushSendResult(outcome=PushOutcome.DELIVERED, message_id="x")
    fake = FakeFcm()
    bind_transport("fcm", fake)
    assert get_transport("fcm") is fake
    assert get_transport("apns").__class__.__name__ == "NotConfiguredPushTransport"
    assert "fcm" in list_bound_kinds()
    reset_transport_binding()
