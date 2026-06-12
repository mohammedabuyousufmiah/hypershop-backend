"""Tests for the ``ingest`` payload walker — pure-Python, fakes the repo.

We can't pull in the SQLA model on Python 3.14 (project pins 2.0.36 +
Python 3.12), so the tests stub the repository with an in-memory fake
that records calls. The walker logic itself is exhaustively covered.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest


# ---- Load only the pure walker out of service.py without dragging
#      models in. The whole service.py module would normally import
#      WhatsAppMessageStatusRepository at module level — we stub that
#      class and exec the source so the walker is callable.
def _load_service_walker():
    src = (
        Path(__file__).parent.parent / "service.py"
    ).read_text(encoding="utf-8")
    src = src.replace(
        "from app.modules.whatsapp_webhook.repository import (\n"
        "    WhatsAppMessageStatusRepository,\n)",
        "class WhatsAppMessageStatusRepository:\n"
        "    def __init__(self, session): self.session = session\n"
        "    async def upsert(self, **kw): return self.session.record(kw)\n",
    )
    ns: dict[str, Any] = {"__name__": "_ingest_test"}
    exec(src, ns)
    return ns["ingest"]


_ingest = _load_service_walker()


class _FakeSession:
    """Minimal session — captures every upsert kwargs payload."""

    def __init__(self, *, on_record: bool = True) -> None:
        self.records: list[dict[str, Any]] = []
        self._on_record = on_record

    def record(self, kwargs: dict[str, Any]) -> bool:
        self.records.append(kwargs)
        return self._on_record


def _statuses_envelope(statuses: list[dict[str, Any]]) -> bytes:
    return json.dumps({
        "object": "whatsapp_business_account",
        "entry": [{
            "id": "WABA1",
            "changes": [{
                "field": "messages",
                "value": {
                    "messaging_product": "whatsapp",
                    "metadata": {"phone_number_id": "PNI"},
                    "statuses": statuses,
                },
            }],
        }],
    }).encode()


# ---------------- happy paths ----------------


@pytest.mark.asyncio
async def test_ingest_single_delivered_status() -> None:
    sess = _FakeSession()
    body = _statuses_envelope([{
        "id": "wamid.AA",
        "status": "delivered",
        "timestamp": "1714780800",
        "recipient_id": "8801911740672",
    }])
    out = await _ingest(session=sess, body_bytes=body)
    assert out.inserted == 1
    assert out.duplicate == 0
    assert out.skipped == 0
    assert sess.records[0]["wamid"] == "wamid.AA"
    assert sess.records[0]["status"] == "delivered"
    assert sess.records[0]["recipient_msisdn"] == "8801911740672"
    assert isinstance(sess.records[0]["status_timestamp"], datetime)


@pytest.mark.asyncio
async def test_ingest_walks_multiple_statuses_in_one_envelope() -> None:
    sess = _FakeSession()
    body = _statuses_envelope([
        {"id": "wamid.A1", "status": "sent", "timestamp": "1714780800",
         "recipient_id": "8801911740672"},
        {"id": "wamid.A1", "status": "delivered", "timestamp": "1714780805",
         "recipient_id": "8801911740672"},
        {"id": "wamid.A1", "status": "read", "timestamp": "1714780810",
         "recipient_id": "8801911740672"},
    ])
    out = await _ingest(session=sess, body_bytes=body)
    assert out.inserted == 3
    assert [r["status"] for r in sess.records] == ["sent", "delivered", "read"]


@pytest.mark.asyncio
async def test_ingest_walks_multiple_entries_and_changes() -> None:
    """Realistic Meta payload: multiple entries, each with multiple changes."""
    sess = _FakeSession()
    body = json.dumps({
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "WABA1",
                "changes": [
                    {"field": "messages", "value": {"statuses": [{
                        "id": "wamid.E1C1",
                        "status": "delivered",
                        "timestamp": "1714780800",
                        "recipient_id": "8801",
                    }]}},
                    {"field": "messages", "value": {"statuses": [{
                        "id": "wamid.E1C2",
                        "status": "read",
                        "timestamp": "1714780801",
                        "recipient_id": "8802",
                    }]}},
                ],
            },
            {
                "id": "WABA2",
                "changes": [
                    {"field": "messages", "value": {"statuses": [{
                        "id": "wamid.E2C1",
                        "status": "sent",
                        "timestamp": "1714780802",
                        "recipient_id": "8803",
                    }]}},
                ],
            },
        ],
    }).encode()
    out = await _ingest(session=sess, body_bytes=body)
    assert out.inserted == 3
    assert {r["wamid"] for r in sess.records} == {
        "wamid.E1C1", "wamid.E1C2", "wamid.E2C1",
    }


# ---------------- failure / edge cases ----------------


@pytest.mark.asyncio
async def test_ingest_extracts_failed_status_with_error() -> None:
    sess = _FakeSession()
    body = _statuses_envelope([{
        "id": "wamid.FAIL",
        "status": "failed",
        "timestamp": "1714780800",
        "recipient_id": "8801",
        "errors": [{
            "code": 131026,
            "title": "Receiver not on WhatsApp",
            "message": "Message could not be delivered.",
        }],
    }])
    out = await _ingest(session=sess, body_bytes=body)
    assert out.inserted == 1
    rec = sess.records[0]
    assert rec["status"] == "failed"
    assert rec["error_code"] == "131026"
    assert "WhatsApp" in (rec["error_title"] or "")


@pytest.mark.asyncio
async def test_ingest_skips_status_without_id() -> None:
    sess = _FakeSession()
    body = _statuses_envelope([{
        "status": "delivered",
        "timestamp": "1714780800",
        "recipient_id": "8801",
    }])
    out = await _ingest(session=sess, body_bytes=body)
    assert out.inserted == 0
    assert out.skipped == 1


@pytest.mark.asyncio
async def test_ingest_skips_unknown_status_value() -> None:
    sess = _FakeSession()
    body = _statuses_envelope([{
        "id": "wamid.W",
        "status": "weird_meta_status_we_dont_know",
        "timestamp": "1714780800",
        "recipient_id": "8801",
    }])
    out = await _ingest(session=sess, body_bytes=body)
    assert out.inserted == 0
    assert out.skipped == 1


@pytest.mark.asyncio
async def test_ingest_returns_duplicate_on_repo_no_op() -> None:
    """Repository upsert returns False on conflict-no-op."""
    sess = _FakeSession(on_record=False)
    body = _statuses_envelope([{
        "id": "wamid.D",
        "status": "delivered",
        "timestamp": "1714780800",
        "recipient_id": "8801",
    }])
    out = await _ingest(session=sess, body_bytes=body)
    assert out.inserted == 0
    assert out.duplicate == 1


@pytest.mark.asyncio
async def test_ingest_handles_bad_body() -> None:
    sess = _FakeSession()
    out = await _ingest(session=sess, body_bytes=b"not json at all")
    assert out.inserted == 0
    assert any("bad_body" in e for e in out.errors)


@pytest.mark.asyncio
async def test_ingest_handles_empty_envelope() -> None:
    sess = _FakeSession()
    out = await _ingest(session=sess, body_bytes=b"{}")
    assert out.inserted == 0
    assert out.duplicate == 0
    assert out.skipped == 0


@pytest.mark.asyncio
async def test_ingest_handles_invalid_timestamp_falls_back_to_now() -> None:
    sess = _FakeSession()
    body = _statuses_envelope([{
        "id": "wamid.T",
        "status": "delivered",
        "timestamp": "not-a-number",
        "recipient_id": "8801",
    }])
    out = await _ingest(session=sess, body_bytes=body)
    assert out.inserted == 1
    assert isinstance(sess.records[0]["status_timestamp"], datetime)
