"""Shared test helpers for the IAM module — imported as a normal module
(not auto-discovered the way conftest fixtures are).
"""

from __future__ import annotations

from sqlalchemy import select

from app.core.db.session import get_sessionmaker
from app.core.events.models import OutboxMessage
from app.modules.iam.handlers import (
    EVT_OTP_EMAIL_SEND,
    EVT_PASSWORD_RESET_EMAIL_SEND,
)


async def _read_latest_outbox(event_type: str) -> OutboxMessage | None:
    sm = get_sessionmaker()
    async with sm() as s:
        stmt = (
            select(OutboxMessage)
            .where(OutboxMessage.type == event_type)
            .order_by(OutboxMessage.created_at.desc())
            .limit(1)
        )
        return (await s.execute(stmt)).scalar_one_or_none()


async def get_latest_otp_code() -> str:
    msg = await _read_latest_outbox(EVT_OTP_EMAIL_SEND)
    assert msg is not None, "no OTP email enqueued"
    return str(msg.payload["code"])


async def get_latest_password_reset_token() -> str:
    msg = await _read_latest_outbox(EVT_PASSWORD_RESET_EMAIL_SEND)
    assert msg is not None, "no password-reset email enqueued"
    return str(msg.payload["token"])
