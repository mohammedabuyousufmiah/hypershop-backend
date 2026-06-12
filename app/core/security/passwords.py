from __future__ import annotations

from functools import lru_cache

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import get_settings


@lru_cache(maxsize=1)
def _hasher() -> PasswordHasher:
    s = get_settings()
    return PasswordHasher(
        time_cost=s.argon2_time_cost,
        memory_cost=s.argon2_memory_cost_kib,
        parallelism=s.argon2_parallelism,
        hash_len=32,
        salt_len=16,
        type=Type.ID,
    )


def hash_password(plaintext: str) -> str:
    if not plaintext:
        raise ValueError("password must not be empty")
    return _hasher().hash(plaintext)


def verify_password(stored_hash: str, candidate: str) -> bool:
    if not stored_hash or not candidate:
        return False
    try:
        return _hasher().verify(stored_hash, candidate)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(stored_hash: str) -> bool:
    """Returns True when the stored hash uses old parameters and should be re-hashed
    on next successful verification (call this on login success and rotate quietly).
    """
    if not stored_hash:
        return False
    try:
        return _hasher().check_needs_rehash(stored_hash)
    except InvalidHashError:
        return True
