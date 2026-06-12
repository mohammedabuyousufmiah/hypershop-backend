from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db.uow import current_session
from app.core.errors import IdempotencyConflictError
from app.core.idempotency_models import IdempotencyKey
from app.core.time import utc_in, utc_now

_DEFAULT_TTL_SECONDS = 24 * 3600


def _hash_body(body: bytes | dict[str, Any] | None) -> bytes:
    if body is None:
        return hashlib.sha256(b"").digest()
    if isinstance(body, bytes):
        return hashlib.sha256(body).digest()
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(canonical).digest()


async def get_or_lock(
    *,
    key: str,
    method: str,
    path: str,
    actor_id: Any,
    body: bytes | dict[str, Any] | None,
    session: AsyncSession | None = None,
) -> tuple[IdempotencyKey | None, bytes]:
    """Returns ``(existing, body_hash)``. If existing is None, the caller is
    the first to use this key; insert the row with the eventual response.
    If existing is not None and its hash matches, the caller must replay
    the stored response. If hashes differ, raise IdempotencyConflictError.
    """
    sess = session or current_session()
    body_hash = _hash_body(body)

    stmt = select(IdempotencyKey).where(
        IdempotencyKey.actor_id == actor_id,
        IdempotencyKey.method == method,
        IdempotencyKey.path == path,
        IdempotencyKey.key == key,
        IdempotencyKey.expires_at > utc_now(),
    )
    existing = (await sess.execute(stmt)).scalar_one_or_none()
    if existing is not None and existing.request_hash != body_hash:
        raise IdempotencyConflictError(
            "Idempotency-Key reused with a different request body.",
        )
    return existing, body_hash


async def store(
    *,
    key: str,
    method: str,
    path: str,
    actor_id: Any,
    body_hash: bytes,
    response_status: int,
    response_body: dict[str, Any] | None,
    ttl_seconds: int = _DEFAULT_TTL_SECONDS,
    session: AsyncSession | None = None,
) -> None:
    sess = session or current_session()
    record = IdempotencyKey(
        key=key,
        method=method,
        path=path,
        actor_id=actor_id,
        request_hash=body_hash,
        response_status=response_status,
        response_body=response_body,
        expires_at=utc_in(ttl_seconds),
    )
    sess.add(record)
    try:
        await sess.flush()
    except IntegrityError as e:
        # A concurrent request just inserted the same key — surface as conflict.
        raise IdempotencyConflictError("Concurrent request with same Idempotency-Key.") from e
