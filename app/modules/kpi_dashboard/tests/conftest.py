"""KPI dashboard tests share a Redis instance with the rest of the
suite. The dashboard caches every (tier, filter) combination, so a
prior test run could leak a `cached: true` flag into a fresh test.
Flush the keyspace before each test that touches the cache.
"""
from __future__ import annotations

from collections.abc import AsyncIterator

import pytest


@pytest.fixture
async def flush_kpi_cache() -> AsyncIterator[None]:
    from app.core.cache import get_redis

    redis = get_redis()
    # Delete only this module's keys so unrelated tests' Redis state
    # isn't disturbed (the helper falls back to a no-op on any error).
    try:
        async for key in redis.scan_iter(match="kpi_dashboard:*"):
            await redis.delete(key)
    except Exception:
        pass
    yield
