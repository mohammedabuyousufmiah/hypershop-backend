from __future__ import annotations

from decimal import Decimal

import pytest

from app.core.errors import ConflictError, DomainError, UnauthenticatedError, ValidationError
from app.core.ids import new_id, uuid7
from app.core.money import Money, quantize_money
from app.core.security.jwt import (
    decode_access_token,
    decode_refresh_token,
    issue_access_token,
    issue_refresh_token,
)
from app.core.security.passwords import hash_password, needs_rehash, verify_password
from app.core.time import ensure_utc, utc_now

# ---------------- ids ----------------


def test_uuid7_is_time_ordered() -> None:
    a, b = uuid7(), uuid7()
    assert a.version == 7
    assert b.version == 7
    # The 48-bit timestamp prefix is monotonically non-decreasing across
    # IDs generated in succession. The remaining 80 bits are random per
    # RFC 9562 so the full bytes can sort either way within the same
    # millisecond — compare only the timestamp prefix.
    assert a.bytes[:6] <= b.bytes[:6]


def test_new_id_returns_uuid7() -> None:
    assert new_id().version == 7


# ---------------- time ----------------


def test_utc_now_is_aware() -> None:
    assert utc_now().tzinfo is not None


def test_ensure_utc_rejects_naive() -> None:
    from datetime import datetime

    with pytest.raises(ValueError):
        ensure_utc(datetime(2026, 4, 28, 12, 0, 0))


# ---------------- money ----------------


def test_money_rounds_half_up() -> None:
    m = Money(amount=Decimal("10.005"), currency="bdt")
    assert m.amount == Decimal("10.01")
    assert m.currency == "BDT"


def test_money_rejects_unknown_currency() -> None:
    from pydantic import ValidationError as PydanticValidationError

    with pytest.raises(PydanticValidationError):
        Money(amount=Decimal("1"), currency="XYZ")


def test_money_addition_requires_same_currency() -> None:
    a = Money(amount=Decimal("10.00"), currency="USD")
    b = Money(amount=Decimal("10.00"), currency="BDT")
    with pytest.raises(ValueError):
        a.add(b)


def test_quantize_money_handles_strings_and_ints() -> None:
    assert quantize_money("1.235") == Decimal("1.24")
    assert quantize_money(7) == Decimal("7.00")


# ---------------- errors ----------------


def test_domain_error_subclasses_have_codes() -> None:
    assert ValidationError().code == "validation_error"
    assert ValidationError().status_code == 422
    assert ConflictError().code == "conflict"


def test_domain_error_carries_details() -> None:
    err = DomainError("oops", details={"field": "x"})
    assert err.details == {"field": "x"}


# ---------------- passwords ----------------


def test_password_round_trip() -> None:
    h = hash_password("Hypershop!2026")
    assert verify_password(h, "Hypershop!2026") is True
    assert verify_password(h, "wrong") is False


def test_empty_password_rejected_for_hash() -> None:
    with pytest.raises(ValueError):
        hash_password("")


def test_empty_inputs_for_verify_return_false() -> None:
    assert verify_password("", "x") is False
    assert verify_password("$argon2id$bogus", "") is False


def test_needs_rehash_is_false_for_fresh_hash() -> None:
    h = hash_password("Hypershop!2026")
    assert needs_rehash(h) is False


# ---------------- jwt ----------------


def test_access_token_round_trip() -> None:
    uid, sid = new_id(), new_id()
    token, payload = issue_access_token(
        user_id=uid,
        session_id=sid,
        roles=("customer",),
        permissions=("orders.read",),
    )
    decoded = decode_access_token(token)
    assert decoded.sub == uid
    assert decoded.sid == sid
    assert decoded.kind == "access"
    assert "orders.read" in decoded.permissions
    assert payload.expires_at > payload.issued_at


def test_refresh_token_round_trip() -> None:
    uid, sid = new_id(), new_id()
    token, _ = issue_refresh_token(user_id=uid, session_id=sid)
    decoded = decode_refresh_token(token)
    assert decoded.kind == "refresh"


def test_access_token_rejects_refresh_token_value() -> None:
    uid, sid = new_id(), new_id()
    refresh, _ = issue_refresh_token(user_id=uid, session_id=sid)
    with pytest.raises(UnauthenticatedError):
        decode_access_token(refresh)


def test_invalid_token_rejected() -> None:
    with pytest.raises(UnauthenticatedError):
        decode_access_token("not-a-jwt")
