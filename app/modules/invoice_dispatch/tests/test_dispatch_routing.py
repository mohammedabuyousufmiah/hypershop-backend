"""Routing-rule tests for the invoice/OTP dispatcher.

Covers the BD vs non-BD branching + WhatsApp → SMS fallback shape
without making any real HTTP calls. The WhatsApp + SMS transports are
swapped for in-memory fakes.
"""

from __future__ import annotations

import pytest

from app.core.errors import ServiceUnavailableError
from app.modules.invoice_dispatch.service import (
    DispatchOutcome,
    dispatch_otp,
    is_bd_phone,
)
from app.modules.invoice_dispatch.transport.whatsapp_base import (
    WhatsAppOutcome,
    WhatsAppSendResult,
)
from app.modules.invoice_dispatch.transport.whatsapp_registry import (
    bind_transport as bind_whatsapp,
    reset_transport_binding as reset_whatsapp_binding,
)


class _FakeWhatsApp:
    name = "fake"

    def __init__(self, outcome: WhatsAppOutcome, error_code: str | None = None) -> None:
        self._outcome = outcome
        self._error_code = error_code
        self.calls: list[tuple[str, str]] = []

    async def send_template(self, *, to, template):
        self.calls.append((to, template.name))
        return WhatsAppSendResult(
            outcome=self._outcome,
            message_id="wamid.fake" if self._outcome == WhatsAppOutcome.DELIVERED else None,
            error_code=self._error_code,
        )


class _FakeSms:
    name = "fake_sms"

    def __init__(self, *, raise_on_send: bool = False) -> None:
        self._raise = raise_on_send
        self.calls: list[tuple[str, str]] = []

    async def send(self, *, to, text):
        self.calls.append((to, text))
        if self._raise:
            raise RuntimeError("SMS provider down")


@pytest.fixture(autouse=True)
def _reset_bindings():
    yield
    reset_whatsapp_binding()


# ---------------- is_bd_phone ----------------


@pytest.mark.parametrize(
    ("phone", "is_bd"),
    [
        ("+8801911740672", True),
        ("+880171234567", True),
        ("+13105551212", False),
        ("+447700900123", False),
        ("+8809999", True),  # any +880 prefix counts
    ],
)
def test_is_bd_phone(phone: str, is_bd: bool) -> None:
    assert is_bd_phone(phone) is is_bd


# ---------------- OTP dispatch routing ----------------


@pytest.mark.asyncio
async def test_otp_bd_whatsapp_succeeds_no_sms_call(monkeypatch) -> None:
    fake_wa = _FakeWhatsApp(WhatsAppOutcome.DELIVERED)
    bind_whatsapp(fake_wa)
    fake_sms = _FakeSms()
    from app.modules.iam.transport import sms_registry as sms_reg
    monkeypatch.setattr(sms_reg, "_active", fake_sms)

    result = await dispatch_otp(
        phone="+8801911740672", code="123456", purpose="login", ttl_seconds=600,
    )
    assert result.via == "whatsapp"
    assert result.delivered is True
    assert fake_wa.calls and fake_wa.calls[0][0] == "+8801911740672"
    assert fake_sms.calls == []  # SMS NOT called


@pytest.mark.asyncio
async def test_otp_bd_whatsapp_not_on_wa_falls_back_to_sms(monkeypatch) -> None:
    fake_wa = _FakeWhatsApp(WhatsAppOutcome.NOT_ON_WHATSAPP, error_code="131026")
    bind_whatsapp(fake_wa)
    fake_sms = _FakeSms()
    from app.modules.iam.transport import sms_registry as sms_reg
    monkeypatch.setattr(sms_reg, "_active", fake_sms)

    result = await dispatch_otp(
        phone="+8801911740672", code="123456", purpose="login", ttl_seconds=600,
    )
    assert result.via == "sms"
    assert result.delivered is True
    assert fake_wa.calls and fake_wa.calls[0][0] == "+8801911740672"
    assert fake_sms.calls and fake_sms.calls[0][0] == "+8801911740672"
    # SMS body contains the code
    assert "123456" in fake_sms.calls[0][1]


@pytest.mark.asyncio
async def test_otp_bd_whatsapp_transient_falls_back_to_sms(monkeypatch) -> None:
    fake_wa = _FakeWhatsApp(WhatsAppOutcome.TRANSIENT_FAILURE, error_code="timeout")
    bind_whatsapp(fake_wa)
    fake_sms = _FakeSms()
    from app.modules.iam.transport import sms_registry as sms_reg
    monkeypatch.setattr(sms_reg, "_active", fake_sms)

    result = await dispatch_otp(
        phone="+8801911740672", code="123456", purpose="login", ttl_seconds=600,
    )
    assert result.via == "sms"
    assert result.delivered is True
    assert fake_sms.calls  # SMS WAS called


@pytest.mark.asyncio
async def test_otp_bd_both_fail_raises_service_unavailable(monkeypatch) -> None:
    fake_wa = _FakeWhatsApp(WhatsAppOutcome.TRANSIENT_FAILURE, error_code="timeout")
    bind_whatsapp(fake_wa)
    fake_sms = _FakeSms(raise_on_send=True)
    from app.modules.iam.transport import sms_registry as sms_reg
    monkeypatch.setattr(sms_reg, "_active", fake_sms)

    with pytest.raises(ServiceUnavailableError) as exc:
        await dispatch_otp(
            phone="+8801911740672", code="123456", purpose="login", ttl_seconds=600,
        )
    assert "WhatsApp" in exc.value.message and "SMS" in exc.value.message


# ---------------- Non-BD policy: WhatsApp ONLY ----------------


@pytest.mark.asyncio
async def test_otp_non_bd_whatsapp_succeeds_no_sms(monkeypatch) -> None:
    fake_wa = _FakeWhatsApp(WhatsAppOutcome.DELIVERED)
    bind_whatsapp(fake_wa)
    fake_sms = _FakeSms()
    from app.modules.iam.transport import sms_registry as sms_reg
    monkeypatch.setattr(sms_reg, "_active", fake_sms)

    result = await dispatch_otp(
        phone="+13105551212", code="123456", purpose="login", ttl_seconds=600,
    )
    assert result.via == "whatsapp"
    assert result.delivered is True
    assert fake_sms.calls == []


@pytest.mark.asyncio
async def test_otp_non_bd_not_on_whatsapp_raises_no_sms_attempt(monkeypatch) -> None:
    """Critical policy: non-BD recipient + not on WhatsApp →
    DO NOT pay for international SMS. Raise so the outbox eventually
    dead-letters and ops can intervene.
    """
    fake_wa = _FakeWhatsApp(WhatsAppOutcome.NOT_ON_WHATSAPP, error_code="131026")
    bind_whatsapp(fake_wa)
    fake_sms = _FakeSms()
    from app.modules.iam.transport import sms_registry as sms_reg
    monkeypatch.setattr(sms_reg, "_active", fake_sms)

    with pytest.raises(ServiceUnavailableError) as exc:
        await dispatch_otp(
            phone="+13105551212", code="123456", purpose="login", ttl_seconds=600,
        )
    assert exc.value.details.get("reason") == "non_bd_not_on_whatsapp"
    assert fake_sms.calls == []  # SMS NEVER called for non-BD


@pytest.mark.asyncio
async def test_otp_non_bd_transient_raises_no_sms_attempt(monkeypatch) -> None:
    """Non-BD + WhatsApp transient also doesn't fall back to intl SMS —
    outbox should retry instead.
    """
    fake_wa = _FakeWhatsApp(WhatsAppOutcome.TRANSIENT_FAILURE, error_code="timeout")
    bind_whatsapp(fake_wa)
    fake_sms = _FakeSms()
    from app.modules.iam.transport import sms_registry as sms_reg
    monkeypatch.setattr(sms_reg, "_active", fake_sms)

    with pytest.raises(ServiceUnavailableError) as exc:
        await dispatch_otp(
            phone="+13105551212", code="123456", purpose="login", ttl_seconds=600,
        )
    assert exc.value.details.get("reason") == "non_bd_whatsapp_transient"
    assert fake_sms.calls == []
