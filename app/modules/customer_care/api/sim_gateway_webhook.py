"""SIM-gateway webhook receiver — Android SIM gateway POSTs call events here.

The Android gateway (`cc-automation/android-gateway-src/`) runs on a
shop's phone with a SIM card inserted; it bridges inbound + outbound
calls to this backend by POSTing JSON events:

  ringing  → create row, status='ringing'
  answered → status='live'
  ended    → status='completed', set duration + recording_url
  missed   → status='missed'

HMAC-SHA256 signature with shared secret in header `X-SIM-Gateway-Signature`.
The shared secret lives in `settings.sim_gateway_webhook_secret` (env var
`SIM_GATEWAY_WEBHOOK_SECRET`). When the secret is unset (dev), signature
verification is skipped + a warning is logged.

Idempotency: `channel_call_id` is UNIQUE on `hypershop_voice_call_sessions`,
so re-deliveries of the same `ringing` event upsert. Subsequent state
events look up by channel_call_id + update.
"""
from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse

from app.core.config import get_settings
from app.core.db.uow import UnitOfWork, get_uow
from app.core.logging import get_logger
from app.modules.customer_care import cc_inbox_repository as repo
from app.modules.customer_care import cc_inbox_service as svc

_log = get_logger("hypershop.cc.sim_gateway")

router = APIRouter(
    prefix="/customer-care/webhooks/sim-gateway",
    tags=["cc-sim-gateway"],
)


def _verify_signature(body: bytes, signature: str | None) -> bool:
    cfg = get_settings()
    secret = getattr(cfg, "sim_gateway_webhook_secret", None)
    if not secret:
        # Dev mode: no secret configured → skip verification + log.
        _log.warning("sim_gateway_signature_skipped_no_secret")
        return True
    if not signature:
        return False
    expected = hmac.new(
        secret.encode("utf-8"), body, hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def _parse_dt(raw: Any) -> datetime:
    if raw is None:
        return datetime.now(timezone.utc)
    if isinstance(raw, (int, float)):
        return datetime.fromtimestamp(float(raw), tz=timezone.utc)
    if isinstance(raw, str):
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now(timezone.utc)


@router.post(
    "",
    status_code=200,
    summary="SIM-gateway call event ingest (idempotent)",
)
async def receive_sim_gateway_event(
    request: Request,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    x_sim_gateway_signature: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    raw = await request.body()
    if not _verify_signature(raw, x_sim_gateway_signature):
        _log.warning("sim_gateway_signature_invalid")
        raise HTTPException(status_code=401, detail="signature mismatch")

    try:
        payload = await request.json()
    except Exception as e:  # noqa: BLE001
        _log.warning("sim_gateway_parse_error", error=str(e)[:200])
        return JSONResponse(
            {"received": False, "reason": "invalid_json"}, status_code=400,
        )

    event_type = str(payload.get("event") or "").lower()
    channel_call_id = str(payload.get("call_id") or "").strip()
    if not channel_call_id:
        return JSONResponse(
            {"received": False, "reason": "missing_call_id"}, status_code=400,
        )

    try:
        async with uow.transactional() as session:
            existing = await repo.get_voice_call_by_channel_id(
                session, channel_call_id,
            )

            if event_type == "ringing":
                if existing is not None:
                    # Idempotent re-delivery of ringing — no-op.
                    return JSONResponse(
                        {"received": True, "resolution": "duplicate"},
                        status_code=200,
                    )
                await svc.record_inbound_voice_call(
                    session,
                    channel_call_id=channel_call_id,
                    direction=str(payload.get("direction") or "inbound"),
                    caller_phone=str(payload.get("caller_phone") or ""),
                    callee_phone=payload.get("callee_phone"),
                    started_at=_parse_dt(payload.get("started_at")),
                    status="ringing",
                )
                return JSONResponse(
                    {"received": True, "resolution": "created"},
                    status_code=200,
                )

            if existing is None:
                _log.info(
                    "sim_gateway_event_for_unknown_call",
                    call_id=channel_call_id,
                    event=event_type,
                )
                return JSONResponse(
                    {"received": True, "resolution": "no_matching_call"},
                    status_code=200,
                )

            if event_type == "answered":
                await repo.update_voice_call(
                    session, existing.id,
                    status="live",
                    answered_at=_parse_dt(payload.get("answered_at")),
                )
                return JSONResponse(
                    {"received": True, "resolution": "answered"},
                    status_code=200,
                )

            if event_type == "ended":
                ended_at = _parse_dt(payload.get("ended_at"))
                started = existing.answered_at or existing.started_at
                duration = int(payload.get("duration_seconds") or max(
                    0, int((ended_at - started).total_seconds()),
                ))
                await svc.complete_voice_call(
                    session,
                    call_id=existing.id,
                    ended_at=ended_at,
                    duration_seconds=duration,
                    recording_url=payload.get("recording_url"),
                )
                return JSONResponse(
                    {"received": True, "resolution": "completed"},
                    status_code=200,
                )

            if event_type in ("missed", "voicemail", "failed"):
                await repo.update_voice_call(
                    session, existing.id,
                    status=event_type,
                    ended_at=_parse_dt(payload.get("ended_at")),
                )
                return JSONResponse(
                    {"received": True, "resolution": event_type},
                    status_code=200,
                )

            _log.info(
                "sim_gateway_unknown_event",
                event=event_type,
                call_id=channel_call_id,
            )
            return JSONResponse(
                {"received": True, "resolution": "unknown_event"},
                status_code=200,
            )

    except Exception as e:  # noqa: BLE001
        # Swallow internal errors so the gateway doesn't retry-storm us.
        _log.error(
            "sim_gateway_internal_error",
            error=str(e)[:200],
            call_id=channel_call_id,
            event=event_type,
        )
        return JSONResponse(
            {"received": True, "resolution": "deferred"},
            status_code=200,
        )
