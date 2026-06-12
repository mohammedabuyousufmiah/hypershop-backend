"""Unit tests for the payment provider adapters.

These don't make real HTTP calls. They cover:
  - Constructor refusal when creds are missing (every adapter)
  - SSLCommerz signature builder produces the format the gateway expects
  - Rocket HMAC signature verifies symmetrically + rejects stale ts
  - Bkash status mapping
  - WebhookEvent.parse rejects bad bodies / bad signatures with the
    documented IntegrationError reasons
"""

from __future__ import annotations

import hashlib
import json
import time

import pytest

from app.core.errors import IntegrationError
from app.modules.payments.providers.bkash import BkashProvider, _map_status as _bkash_map
from app.modules.payments.providers.rocket import RocketProvider
from app.modules.payments.providers.sslcommerz import SSLCommerzProvider


# ---------------- Construction refusal ----------------


def test_bkash_refuses_without_creds() -> None:
    with pytest.raises(IntegrationError) as exc:
        BkashProvider(
            app_key="", app_secret="", username="", password="", base_url="",
        )
    assert exc.value.details.get("missing_setting") == "BKASH_*"


def test_sslcommerz_refuses_without_creds() -> None:
    with pytest.raises(IntegrationError) as exc:
        SSLCommerzProvider(store_id="", store_passwd="", base_url="")
    assert exc.value.details.get("missing_setting") == "SSLCOMMERZ_*"


def test_rocket_refuses_without_creds() -> None:
    with pytest.raises(IntegrationError) as exc:
        RocketProvider(
            merchant_id="", app_key="", app_secret="", base_url="",
        )
    assert exc.value.details.get("missing_setting") == "ROCKET_*"


# ---------------- Bkash status mapping ----------------


@pytest.mark.parametrize(
    ("bkash_in", "internal_out"),
    [
        ("Initiated", "initiated"),
        ("Authorized", "authorized"),
        ("Completed", "captured"),
        ("Cancelled", "cancelled"),
        ("Failed", "failed"),
        ("Refunded", "refunded"),
        ("Reversed", "refunded"),
        ("UnknownStatus", "failed"),  # unknown maps to failed
    ],
)
def test_bkash_status_map(bkash_in: str, internal_out: str) -> None:
    assert _bkash_map(bkash_in) == internal_out


# ---------------- SSLCommerz signature ----------------


def test_sslcommerz_signature_round_trip() -> None:
    p = SSLCommerzProvider(
        store_id="testbox",
        store_passwd="mySecret",
        base_url="https://sandbox.sslcommerz.com",
    )
    fields = {
        "tran_id": "abc-123",
        "amount": "100.00",
        "status": "VALID",
        "verify_key": "amount,status,tran_id",
    }
    expected = SSLCommerzProvider._verify_signature_for_test(
        store_passwd="mySecret", fields=fields,
    )
    fields["verify_sign"] = expected
    # Build a fake form-encoded body and run parse_webhook through it.
    body = "&".join(f"{k}={v}" for k, v in fields.items()).encode()
    headers = {"content-type": "application/x-www-form-urlencoded"}
    event = p.parse_webhook(body=body, headers=headers)
    # Signature accepted → we got an event back
    assert event.provider_payment_id == "abc-123"
    assert event.status == "captured"  # VALID maps to captured


def test_sslcommerz_signature_failure_raises() -> None:
    p = SSLCommerzProvider(
        store_id="testbox",
        store_passwd="mySecret",
        base_url="https://sandbox.sslcommerz.com",
    )
    fields = {
        "tran_id": "abc-123",
        "amount": "100.00",
        "status": "VALID",
        "verify_key": "amount,status,tran_id",
        "verify_sign": "deadbeef" * 4,  # wrong
    }
    body = "&".join(f"{k}={v}" for k, v in fields.items()).encode()
    headers = {"content-type": "application/x-www-form-urlencoded"}
    with pytest.raises(IntegrationError) as exc:
        p.parse_webhook(body=body, headers=headers)
    assert exc.value.details.get("reason") == "signature_failed"


# ---------------- Rocket HMAC ----------------


def test_rocket_hmac_round_trip() -> None:
    p = RocketProvider(
        merchant_id="MID-1",
        app_key="ak",
        app_secret="topsecret",
        base_url="https://sandbox.rocket.example",
    )
    body = json.dumps({"rocketTxnId": "RT123", "status": "SUCCESS", "amount": "100"})
    ts = str(int(time.time()))
    nonce = "abc"
    sig = p._sign(body, ts, nonce)
    headers = {
        "x-timestamp": ts,
        "x-nonce": nonce,
        "x-signature": sig,
    }
    event = p.parse_webhook(body=body.encode(), headers=headers)
    assert event.provider_payment_id == "RT123"
    assert event.status == "captured"


def test_rocket_rejects_stale_timestamp() -> None:
    p = RocketProvider(
        merchant_id="MID-1",
        app_key="ak",
        app_secret="topsecret",
        base_url="https://sandbox.rocket.example",
    )
    body = json.dumps({"rocketTxnId": "RT123", "status": "SUCCESS"})
    stale_ts = str(int(time.time()) - 600)  # 10 min ago
    nonce = "abc"
    sig = p._sign(body, stale_ts, nonce)
    with pytest.raises(IntegrationError) as exc:
        p.parse_webhook(
            body=body.encode(),
            headers={"x-timestamp": stale_ts, "x-nonce": nonce, "x-signature": sig},
        )
    assert exc.value.details.get("reason") == "stale_timestamp"


def test_rocket_rejects_bad_signature() -> None:
    p = RocketProvider(
        merchant_id="MID-1",
        app_key="ak",
        app_secret="topsecret",
        base_url="https://sandbox.rocket.example",
    )
    body = json.dumps({"rocketTxnId": "RT123", "status": "SUCCESS"})
    ts = str(int(time.time()))
    with pytest.raises(IntegrationError) as exc:
        p.parse_webhook(
            body=body.encode(),
            headers={
                "x-timestamp": ts,
                "x-nonce": "abc",
                "x-signature": "deadbeef" * 8,
            },
        )
    assert exc.value.details.get("reason") == "signature_failed"


# ---------------- Bkash webhook parsing ----------------


def test_bkash_webhook_event_id_is_stable() -> None:
    p = BkashProvider(
        app_key="ak",
        app_secret="as",
        username="u",
        password="pw",
        base_url="https://tokenized.sandbox.bka.sh/v1.2.0-beta",
    )
    body = json.dumps({"paymentID": "PAY-123", "transactionStatus": "Completed", "amount": "150.00"}).encode()
    e1 = p.parse_webhook(body=body, headers={})
    e2 = p.parse_webhook(body=body, headers={})
    # Same body → same event_id (idempotent dedup works)
    assert e1.event_id == e2.event_id
    assert e1.event_id == hashlib.sha256(body).hexdigest()[:64]
    assert e1.provider_payment_id == "PAY-123"
    assert e1.status == "captured"


def test_bkash_webhook_missing_payment_id_raises() -> None:
    p = BkashProvider(
        app_key="ak", app_secret="as", username="u", password="pw",
        base_url="https://tokenized.sandbox.bka.sh/v1.2.0-beta",
    )
    body = json.dumps({"transactionStatus": "Completed"}).encode()
    with pytest.raises(IntegrationError) as exc:
        p.parse_webhook(body=body, headers={})
    assert exc.value.details.get("reason") == "missing_payment_id"
