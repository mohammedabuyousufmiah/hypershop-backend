from __future__ import annotations

import redis.asyncio as redis_async

from app.core.config import get_settings

_redis: redis_async.Redis | None = None


def get_redis() -> redis_async.Redis:
    global _redis
    if _redis is None:
        settings = get_settings()
        _redis = redis_async.from_url(
            str(settings.redis_url),
            encoding="utf-8",
            decode_responses=True,
            health_check_interval=15,
        )
    return _redis


async def close_redis() -> None:
    global _redis
    if _redis is not None:
        await _redis.aclose()
        _redis = None
