from __future__ import annotations

import time as _time
from dataclasses import dataclass

from app.core.cache import get_redis
from app.core.errors import RateLimitedError

# Atomic Lua bucket: refill by elapsed time, attempt to spend ``cost`` tokens,
# return (allowed, retry_after_ms). Single Redis round-trip, race-free.
_BUCKET_SCRIPT = """
local key = KEYS[1]
local now_ms = tonumber(ARGV[1])
local capacity = tonumber(ARGV[2])
local refill_per_ms = tonumber(ARGV[3])
local cost = tonumber(ARGV[4])

local data = redis.call('HMGET', key, 'tokens', 'ts')
local tokens = tonumber(data[1])
local ts = tonumber(data[2])
if tokens == nil then
  tokens = capacity
  ts = now_ms
end
local elapsed = math.max(0, now_ms - ts)
tokens = math.min(capacity, tokens + elapsed * refill_per_ms)
local allowed = 0
local retry_after_ms = 0
if tokens >= cost then
  tokens = tokens - cost
  allowed = 1
else
  retry_after_ms = math.ceil((cost - tokens) / refill_per_ms)
end
redis.call('HMSET', key, 'tokens', tokens, 'ts', now_ms)
redis.call('PEXPIRE', key, math.ceil(capacity / refill_per_ms))
return {allowed, retry_after_ms}
"""


@dataclass(frozen=True, slots=True)
class RateLimit:
    capacity: int
    window_seconds: int

    @property
    def refill_per_ms(self) -> float:
        return self.capacity / (self.window_seconds * 1000)


class RateLimiter:
    """Redis-backed atomic token bucket.

    Each ``check`` call deducts one token from the bucket identified by
    ``(scope, identity)`` and raises ``RateLimitedError`` if the bucket is empty.
    Buckets are per-scope so different limits (login vs. OTP vs. register) do
    not share state.
    """

    def __init__(self) -> None:
        self._redis = get_redis()

    async def check(self, scope: str, identity: str, limit: RateLimit) -> None:
        key = f"rl:{scope}:{identity}"
        now_ms = int(_time.time() * 1000)
        result = await self._redis.eval(
            _BUCKET_SCRIPT,
            1,
            key,
            now_ms,
            limit.capacity,
            f"{limit.refill_per_ms:.10f}",
            1,
        )
        allowed, retry_after_ms = int(result[0]), int(result[1])
        if not allowed:
            raise RateLimitedError(
                "Too many requests.",
                details={"retry_after_ms": retry_after_ms, "scope": scope},
            )
