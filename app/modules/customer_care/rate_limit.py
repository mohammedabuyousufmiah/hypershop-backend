"""Redis-backed sliding-window rate limiter (multi-pod safe).

If Redis is unavailable in development the limiter falls back to a per-process
counter with a warning. In production the limiter requires Redis and fails closed
(returns 503 if Redis is down) — never silently disabled.
"""
from __future__ import annotations

import itertools
import logging
import os
import time
import uuid
from collections import defaultdict, deque

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.config import settings

logger = logging.getLogger(__name__)

# Process-unique seed + monotonically increasing counter so ZADD members are
# never duplicated within or across requests in the same process.
_PID = os.getpid()
_INSTANCE = uuid.uuid4().hex[:8]
_COUNTER = itertools.count()


class RedisSlidingWindowLimiter:
    def __init__(self, redis, capacity: int, window_seconds: int):
        self.redis = redis
        self.capacity = capacity
        self.window = window_seconds

    def hit(self, key: str) -> tuple[bool, int]:
        # Each request gets a unique ZSET member so concurrent calls within the
        # same millisecond don't collapse into a single entry (which would
        # silently undercount the rate).
        member = f"{int(time.time() * 1000)}-{_PID}-{_INSTANCE}-{next(_COUNTER)}"
        now_ms = int(time.time() * 1000)
        window_start = now_ms - self.window * 1000
        rkey = f"rl:{key}"
        try:
            pipe = self.redis.pipeline()
            pipe.zremrangebyscore(rkey, 0, window_start)
            pipe.zadd(rkey, {member: now_ms})
            pipe.zcard(rkey)
            pipe.expire(rkey, self.window + 1)
            _, _, count, _ = pipe.execute()
        except Exception:
            # Redis hiccup: log and fail open in non-production, fail closed in
            # production (caller will get a 503 from the middleware wrapper).
            logger.exception("rate_limit_redis_hit_failed key=%s", key)
            raise
        remaining = max(0, self.capacity - int(count))
        return int(count) <= self.capacity, remaining


class _MemoryLimiter:
    def __init__(self, capacity: int, window_seconds: int):
        self.capacity = capacity
        self.window = window_seconds
        self.hits: dict[str, deque] = defaultdict(deque)

    def hit(self, key: str) -> tuple[bool, int]:
        now = time.time()
        q = self.hits[key]
        cutoff = now - self.window
        while q and q[0] < cutoff:
            q.popleft()
        q.append(now)
        return len(q) <= self.capacity, max(0, self.capacity - len(q))


def _build_limiter(capacity: int, window: int):
    cfg = settings()
    if cfg.redis_url:
        try:
            from redis import Redis
            r = Redis.from_url(cfg.redis_url)
            r.ping()
            return RedisSlidingWindowLimiter(r, capacity, window)
        except Exception:
            if cfg.is_production:
                logger.exception("rate_limiter_redis_unavailable_in_production")
                raise
            logger.warning("rate_limiter_redis_unavailable_falling_back_to_memory")
    return _MemoryLimiter(capacity, window)


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, capacity: int, window_seconds: int = 60):
        super().__init__(app)
        self.limiter = _build_limiter(capacity, window_seconds)
        self.capacity = capacity

    async def dispatch(self, request: Request, call_next):
        ip = (
            request.headers.get("x-forwarded-for", request.client.host if request.client else "-")
            .split(",")[0]
            .strip()
        )
        path_bucket = request.url.path.split("/")[1] if request.url.path else "root"
        key = f"{ip}:{path_bucket}"
        cfg = settings()
        try:
            allowed, remaining = self.limiter.hit(key)
        except Exception:
            logger.exception("rate_limiter_unavailable key=%s", key)
            if cfg.is_production:
                # Fail closed in production: better to 503 some traffic than to
                # silently disable rate-limiting under attack.
                return JSONResponse(
                    {"detail": "Rate limit unavailable"},
                    status_code=503,
                )
            # Dev: fail open so a flaky local Redis doesn't break testing.
            allowed, remaining = True, self.capacity
        if not allowed:
            return JSONResponse(
                {"detail": "Rate limit exceeded"},
                status_code=429,
                headers={"Retry-After": "60", "X-RateLimit-Limit": str(self.capacity)},
            )
        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(self.capacity)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        return response
