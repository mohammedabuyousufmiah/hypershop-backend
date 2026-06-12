"""Redis cache wrapper for KPI dashboard responses.

Each tier × filter combination is cached for ``DEFAULT_TTL_SECONDS``.
The key embeds a schema version so a deploy that changes the response
shape automatically invalidates the cache.

Failure mode: any Redis exception falls through to a live compute. The
dashboard is read-only and must never 500 because the cache is sick.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from typing import Any

from app.core.cache import get_redis
from app.core.logging import get_logger

_log = get_logger("hypershop.kpi_dashboard.cache")

# Bump this when the response shape changes — old entries are then
# treated as misses because their key no longer matches.
SCHEMA_VERSION = "v1"
DEFAULT_TTL_SECONDS = 60


def _fingerprint(filters: dict[str, Any]) -> str:
    """Stable hash of the filter dict. Sorted to make order irrelevant.

    ``usedforsecurity=False`` — SHA1 here builds a cache lookup key, not
    a security primitive. Tells bandit + FIPS-restricted runtimes the
    use is non-cryptographic.
    """
    serialized = json.dumps(filters, sort_keys=True, default=str)
    return hashlib.sha1(serialized.encode("utf-8"), usedforsecurity=False).hexdigest()[:16]


def cache_key(*, tier: str, filters: dict[str, Any]) -> str:
    return f"kpi_dashboard:{SCHEMA_VERSION}:{tier}:{_fingerprint(filters)}"


async def get_or_compute(
    *,
    tier: str,
    filters: dict[str, Any],
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    compute: Callable[[], Awaitable[dict[str, Any]]],
) -> tuple[dict[str, Any], bool]:
    """Return (payload, was_cached). ``compute`` runs on miss / failure."""
    key = cache_key(tier=tier, filters=filters)
    redis = get_redis()
    try:
        hit = await redis.get(key)
    except Exception as e:
        _log.warning("kpi_cache_get_failed", error=str(e))
        hit = None
    if hit:
        try:
            return json.loads(hit), True
        except json.JSONDecodeError:
            # Corrupt entry — fall through to recompute.
            _log.warning("kpi_cache_corrupt", key=key)

    payload = await compute()
    try:
        await redis.set(key, json.dumps(payload, default=str), ex=ttl_seconds)
    except Exception as e:
        _log.warning("kpi_cache_set_failed", error=str(e))
    return payload, False


async def invalidate(*, tier: str, filters: dict[str, Any]) -> None:
    """Convenience purger; not wired to any write path yet."""
    redis = get_redis()
    try:
        await redis.delete(cache_key(tier=tier, filters=filters))
    except Exception as e:
        _log.warning("kpi_cache_delete_failed", error=str(e))
