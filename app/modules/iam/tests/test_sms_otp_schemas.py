"""Pure-Python tests for SMS-OTP schema validators (no DB needed)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError as PydanticValidationError

from app.modules.iam.schemas import (
    OtpRequestSmsRequest,
    OtpVerifySmsRequest,
)


# ---------------- OtpRequestSmsRequest ----------------


@pytest.mark.parametrize(
    "phone",
    [
        "+8801911740672",      # BD
        "+13105551212",        # US
        "+447700900123",       # UK
        "+447700900123 ",      # trailing whitespace stripped
    ],
)
def test_request_sms_accepts_valid_e164(phone: str) -> None:
    req = OtpRequestSmsRequest(phone=phone)
    # Validator strips whitespace
    assert req.phone == phone.strip()


@pytest.mark.parametrize(
    "phone",
    [
        "8801911740672",       # missing +
        "+0",                  # too short
        "+abc",                # not digits
        "01911740672",         # local format, no +
        "++8801911740672",     # double +
        "+8801911740672X",     # trailing letter
        "",                    # empty
        "x",                   # garbage
    ],
)
def test_request_sms_rejects_non_e164(phone: str) -> None:
    with pytest.raises(PydanticValidationError):
        OtpRequestSmsRequest(phone=phone)


# ---------------- OtpVerifySmsRequest ----------------


def test_verify_sms_accepts_valid() -> None:
    req = OtpVerifySmsRequest(phone="+8801911740672", code="123456")
    assert req.phone == "+8801911740672"
    assert req.code == "123456"


@pytest.mark.parametrize("code", ["abc123", "12 34", "12.34", "abcdef", "12-34"])
def test_verify_sms_rejects_non_digit_code(code: str) -> None:
    with pytest.raises(PydanticValidationError):
        OtpVerifySmsRequest(phone="+8801911740672", code=code)


@pytest.mark.parametrize("code", ["123", "1234567890123"])  # too short / too long
def test_verify_sms_rejects_extreme_code_lengths(code: str) -> None:
    with pytest.raises(PydanticValidationError):
        OtpVerifySmsRequest(phone="+8801911740672", code=code)


def test_verify_sms_rejects_extra_field() -> None:
    """StrictModel forbids extras — defends against payload injection."""
    with pytest.raises(PydanticValidationError):
        OtpVerifySmsRequest.model_validate({
            "phone": "+8801911740672",
            "code": "123456",
            "admin": True,
        })
