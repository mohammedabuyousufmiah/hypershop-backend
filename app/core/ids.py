from __future__ import annotations

import os
import time
from uuid import UUID


def uuid7() -> UUID:
    """Time-ordered UUIDv7 (RFC 9562). 48-bit unix-ms timestamp + 74 random bits.

    Time-ordering keeps B-tree indexes append-mostly, avoiding the random-insert
    bloat of UUIDv4 on large tables.
    """
    timestamp_ms = int(time.time() * 1000)
    rand = os.urandom(10)
    b = bytearray(16)
    b[0] = (timestamp_ms >> 40) & 0xFF
    b[1] = (timestamp_ms >> 32) & 0xFF
    b[2] = (timestamp_ms >> 24) & 0xFF
    b[3] = (timestamp_ms >> 16) & 0xFF
    b[4] = (timestamp_ms >> 8) & 0xFF
    b[5] = timestamp_ms & 0xFF
    b[6] = 0x70 | (rand[0] & 0x0F)
    b[7] = rand[1]
    b[8] = 0x80 | (rand[2] & 0x3F)
    b[9] = rand[3]
    b[10] = rand[4]
    b[11] = rand[5]
    b[12] = rand[6]
    b[13] = rand[7]
    b[14] = rand[8]
    b[15] = rand[9]
    return UUID(bytes=bytes(b))


def new_id() -> UUID:
    return uuid7()
