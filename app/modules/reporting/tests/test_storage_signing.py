"""Signed URL token round-trip + tampering tests."""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest

from app.core.time import utc_now
from app.modules.reporting.errors import (
    ReportFileExpiredError,
    ReportSignatureInvalidError,
)
from app.modules.reporting.storage import (
    make_signed_token,
    verify_signed_token,
)


def _sample(user_owned: bool = True):
    user_id = uuid4() if user_owned else None
    file_id = uuid4()
    exp = utc_now() + timedelta(minutes=10)
    token = make_signed_token(
        file_id=file_id, user_id=user_id, expires_at=exp,
    )
    return file_id, user_id, token


def test_round_trip_user_owned_token():
    fid, uid, tok = _sample()
    payload = verify_signed_token(
        token=tok, expected_file_id=fid, caller_user_id=uid,
    )
    assert payload["file_id"] == fid
    assert payload["system_owned"] is False


def test_token_for_other_user_is_rejected():
    fid, uid, tok = _sample()
    other = uuid4()
    with pytest.raises(ReportSignatureInvalidError):
        verify_signed_token(
            token=tok, expected_file_id=fid, caller_user_id=other,
        )


def test_tampered_token_is_rejected():
    fid, uid, tok = _sample()
    # Flip a couple of base64 chars — will break HMAC.
    tampered = tok[:-4] + ("AAAA" if tok[-4:] != "AAAA" else "BBBB")
    with pytest.raises(ReportSignatureInvalidError):
        verify_signed_token(
            token=tampered, expected_file_id=fid, caller_user_id=uid,
        )


def test_token_for_different_file_is_rejected():
    fid, uid, tok = _sample()
    other_file = uuid4()
    with pytest.raises(ReportSignatureInvalidError):
        verify_signed_token(
            token=tok, expected_file_id=other_file, caller_user_id=uid,
        )


def test_expired_token_raises_gone():
    fid = uuid4()
    uid = uuid4()
    past = utc_now() - timedelta(minutes=1)
    tok = make_signed_token(
        file_id=fid, user_id=uid, expires_at=past,
    )
    with pytest.raises(ReportFileExpiredError):
        verify_signed_token(
            token=tok, expected_file_id=fid, caller_user_id=uid,
        )


def test_system_owned_token_does_not_require_user():
    fid, _, tok = _sample(user_owned=False)
    payload = verify_signed_token(
        token=tok, expected_file_id=fid, caller_user_id=None,
    )
    assert payload["system_owned"] is True


def test_malformed_token_is_rejected():
    fid = uuid4()
    with pytest.raises(ReportSignatureInvalidError):
        verify_signed_token(
            token="not-a-token", expected_file_id=fid, caller_user_id=uuid4(),
        )
