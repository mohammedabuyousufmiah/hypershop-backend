"""Verify the webhook handshake + HMAC signature logic — pure-Python."""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest

from app.modules.whatsapp_webhook.service import (
    verify_signature,
    verify_subscription,
)


SECRET = "ultra-secret-app-secret"


def _sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


# ---------------- handshake ----------------


def test_verify_subscription_returns_challenge_on_match() -> None:
    out = verify_subscription(
        expected_token="my-token", mode="subscribe",
        token="my-token", challenge="abc123",
    )
    assert out == "abc123"


def test_verify_subscription_rejects_mode_mismatch() -> None:
    out = verify_subscription(
        expected_token="t", mode="unsubscribe",
        token="t", challenge="abc",
    )
    assert out is None


def test_verify_subscription_rejects_token_mismatch() -> None:
    out = verify_subscription(
        expected_token="t1", mode="subscribe",
        token="t2", challenge="abc",
    )
    assert out is None


@pytest.mark.parametrize("expected,token", [("", "t"), ("t", ""), ("", "")])
def test_verify_subscription_rejects_empty(expected: str, token: str) -> None:
    out = verify_subscription(
        expected_token=expected, mode="subscribe",
        token=token, challenge="abc",
    )
    assert out is None


# ---------------- signature ----------------


def test_verify_signature_accepts_correct_hmac() -> None:
    body = b'{"object":"whatsapp_business_account","entry":[]}'
    sig = _sign(SECRET, body)
    assert verify_signature(app_secret=SECRET, body_bytes=body, header_value=sig) is True


def test_verify_signature_rejects_tampered_body() -> None:
    body = b'{"object":"whatsapp_business_account","entry":[]}'
    sig = _sign(SECRET, body)
    tampered = body + b"X"
    assert verify_signature(app_secret=SECRET, body_bytes=tampered, header_value=sig) is False


def test_verify_signature_rejects_wrong_secret() -> None:
    body = b"hello"
    sig = _sign("other-secret", body)
    assert verify_signature(app_secret=SECRET, body_bytes=body, header_value=sig) is False


@pytest.mark.parametrize("header", [
    None,
    "",
    "abc123",                                # missing 'sha256=' prefix
    "sha1=abc",                              # wrong algo prefix
    "sha256=" + ("aa" * 32),                 # right shape, wrong digest
])
def test_verify_signature_rejects_malformed_header(header: str | None) -> None:
    body = b"hello"
    assert verify_signature(app_secret=SECRET, body_bytes=body, header_value=header) is False


def test_verify_signature_rejects_empty_secret() -> None:
    body = b"hello"
    sig = _sign(SECRET, body)
    assert verify_signature(app_secret="", body_bytes=body, header_value=sig) is False


# ---------------- shape parsing helpers ----------------


def test_signature_against_realistic_meta_payload() -> None:
    """Sanity: a realistic Meta status payload signs + verifies cleanly."""
    payload = {
        "object": "whatsapp_business_account",
        "entry": [{
            "id": "1234567890",
            "changes": [{
                "field": "messages",
                "value": {
                    "messaging_product": "whatsapp",
                    "metadata": {
                        "display_phone_number": "8801911740672",
                        "phone_number_id": "abc",
                    },
                    "statuses": [{
                        "id": "wamid.HBgLODgwMTk=",
                        "status": "delivered",
                        "timestamp": "1714780800",
                        "recipient_id": "8801911740672",
                    }],
                },
            }],
        }],
    }
    body = json.dumps(payload).encode()
    sig = _sign(SECRET, body)
    assert verify_signature(app_secret=SECRET, body_bytes=body, header_value=sig) is True
