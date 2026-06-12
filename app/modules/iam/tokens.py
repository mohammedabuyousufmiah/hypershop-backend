from __future__ import annotations

import hashlib
import secrets


def new_password_reset_token() -> tuple[str, bytes]:
    """Generate a URL-safe random token and its SHA-256 hash.

    Plaintext is what we e-mail the user. Hash is what we store. No one ever
    sees the plaintext after the email is sent — including us.
    """
    plaintext = secrets.token_urlsafe(48)
    digest = hashlib.sha256(plaintext.encode("utf-8")).digest()
    return plaintext, digest


def hash_password_reset_token(plaintext: str) -> bytes:
    return hashlib.sha256(plaintext.encode("utf-8")).digest()
