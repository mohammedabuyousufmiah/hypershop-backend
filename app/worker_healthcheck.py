"""Worker liveness probe used by the container HEALTHCHECK.

ARQ writes a heartbeat key to Redis on every cycle while the event loop
is running. We simply check the key exists and has a non-stale TTL —
that is, "the worker has reported in recently". Container exits 0 if
healthy, 1 otherwise; the orchestrator restarts the container on 1.

Why not just check the process? Docker's restart policy already covers
crashes. This catches the worse failure: process alive but event loop
wedged (stuck coroutine, deadlocked DB pool, etc.) — those would never
update the heartbeat even though the PID is up.
"""

from __future__ import annotations

import asyncio
import os
import sys

from redis.asyncio import from_url


async def _check() -> int:
    redis_url = os.environ.get("REDIS_URL")
    queue = os.environ.get("ARQ_QUEUE_NAME", "hypershop:jobs")
    if not redis_url:
        print("REDIS_URL unset", file=sys.stderr)
        return 1
    client = from_url(redis_url)
    try:
        # Key format matches arq.constants.health_check_key_suffix (":health-check")
        # appended to the queue name. ARQ writes this on every cycle.
        key = f"{queue}:health-check"
        value = await client.get(key)
        if value is None:
            print(f"no heartbeat at {key}", file=sys.stderr)
            return 1
        return 0
    finally:
        await client.aclose()


if __name__ == "__main__":
    sys.exit(asyncio.run(_check()))
