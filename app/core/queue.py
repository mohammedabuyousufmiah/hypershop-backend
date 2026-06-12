"""ARQ enqueue pool — used by the API process to dispatch background jobs.

The worker process owns the ARQ ``WorkerSettings`` (cron + functions
list). The API process never runs jobs; it just **enqueues** them.

This module provides a single, lazily-initialised :class:`ArqRedis`
connection pool for the API process. Lazy because not every API
container actually enqueues jobs (e.g. health probes), so we don't
pay the connection cost on boot.

Usage from a request handler::

    from app.core.queue import get_arq_pool

    pool = await get_arq_pool()
    await pool.enqueue_job("process_product_video", str(video_id))

The function name string MUST match a name registered in
``app.worker.WorkerSettings.functions``. ARQ silently dead-letters
unknown function names, so a typo here is debuggable only via the
``arq:dead`` Redis key — keep names in sync with ``worker.py``.
"""

from __future__ import annotations

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from app.core.config import get_settings

_pool: ArqRedis | None = None


async def get_arq_pool() -> ArqRedis:
    """Return the cached :class:`ArqRedis` pool, creating it on first call."""
    global _pool
    if _pool is None:
        s = get_settings()
        _pool = await create_pool(
            RedisSettings.from_dsn(str(s.redis_url)),
            default_queue_name=s.arq_queue_name,
        )
    return _pool


async def close_arq_pool() -> None:
    """Close the pool. Called by the FastAPI lifespan on shutdown."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
