"""PII redaction (for logs/audit) and Fernet symmetric encryption (for DB columns).

Encryption design
-----------------
- AES-128 via Fernet (cryptography library).
- Key sourced from `PII_ENCRYPTION_KEYS` env (json: {"k1": "<base64-fernet-key>"}).
- `PII_ACTIVE_KID` selects the key for new writes.
- Stored value format:  `kid$ciphertext`  (so old keys still decrypt).

Redaction
---------
- Phone:  +880XXXXXXXX        → +880******72 (mask middle)
- Email:  user@host           → u***@host
- Address: arbitrary length   → first 4 chars + "***"
- Names:  first letter only
- Free-text body: capped at 32 chars + "..."
"""
from __future__ import annotations

import json
import logging
import re
from functools import lru_cache

from cryptography.fernet import Fernet, MultiFernet, InvalidToken

from app.config import settings

logger = logging.getLogger(__name__)


@lru_cache
def _fernet_for_kid(kid: str) -> Fernet | None:
    cfg = settings()
    if not cfg.pii_encryption_keys_json:
        return None
    try:
        keys = json.loads(cfg.pii_encryption_keys_json)
    except json.JSONDecodeError:
        logger.error("pii_encryption_keys_json_invalid")
        return None
    raw = keys.get(kid)
    return Fernet(raw.encode()) if raw else None


@lru_cache
def _fernet_all() -> MultiFernet | None:
    cfg = settings()
    if not cfg.pii_encryption_keys_json:
        return None
    try:
        keys = json.loads(cfg.pii_encryption_keys_json)
    except json.JSONDecodeError:
        return None
    fernets = [Fernet(v.encode()) for v in keys.values()]
    if not fernets:
        return None
    return MultiFernet(fernets)


def encrypt_pii(plaintext: str | None) -> str | None:
    if plaintext is None or plaintext == "":
        return plaintext
    cfg = settings()
    kid = cfg.pii_active_kid
    f = _fernet_for_kid(kid) if kid else None
    if not f:
        # Encryption disabled — store as-is; warn if production
        if cfg.is_production and cfg.pii_encryption_required:
            raise RuntimeError("PII encryption required in production but not configured")
        return plaintext
    return f"{kid}${f.encrypt(plaintext.encode()).decode()}"


_KID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,32}$")


def decrypt_pii(stored: str | None) -> str | None:
    """Decrypt a `kid$ciphertext` value. Returns None if the kid looks like an
    encryption marker but the cipher cannot be decoded — NEVER returns the raw
    ciphertext as if it were plaintext (that would silently leak ciphertext to
    UI / API clients on key rotation mistakes).
    """
    if stored is None or stored == "":
        return stored
    if "$" not in stored:
        return stored  # plaintext (legacy or encryption disabled)
    kid, _, ciphertext = stored.partition("$")
    if not _KID_RE.match(kid):
        # Not our format — could be a plaintext value containing '$'.
        return stored
    f = _fernet_for_kid(kid)
    if f is not None:
        try:
            return f.decrypt(ciphertext.encode()).decode()
        except InvalidToken:
            logger.error("pii_decrypt_invalid_token kid=%s", kid)
            return None
    all_f = _fernet_all()
    if all_f is None:
        # Stored looks encrypted but no keys available → don't leak ciphertext.
        logger.error("pii_decrypt_no_keys_available kid=%s", kid)
        return None
    try:
        return all_f.decrypt(ciphertext.encode()).decode()
    except InvalidToken:
        logger.error("pii_decrypt_invalid_token_all_keys kid=%s", kid)
        return None


# ---------- redaction ----------

PHONE_RE = re.compile(r"(?<!\d)(\+?\d{6,15})(?!\d)")
EMAIL_RE = re.compile(r"([A-Za-z0-9._%+-])([A-Za-z0-9._%+-]*)@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")


def redact_phone(phone: str | None) -> str:
    if not phone:
        return ""
    digits = re.sub(r"\D", "", phone)
    if len(digits) <= 4:
        return "*" * len(digits)
    keep = 2
    return phone[:3] + "*" * max(0, len(phone) - keep - 3) + phone[-keep:]


def redact_email(email: str | None) -> str:
    if not email or "@" not in email:
        return ""
    local, _, domain = email.partition("@")
    return f"{local[0]}***@{domain}"


def redact_address(addr: str | None) -> str:
    if not addr:
        return ""
    if len(addr) <= 4:
        return "***"
    return f"{addr[:4]}***"


def redact_name(name: str | None) -> str:
    if not name:
        return ""
    return f"{name[0]}***"


def redact_text(text: str | None, max_len: int = 32) -> str:
    if not text:
        return ""
    masked = PHONE_RE.sub(lambda m: redact_phone(m.group(0)), text)
    masked = EMAIL_RE.sub(lambda m: f"{m.group(1)}***@{m.group(3)}", masked)
    if len(masked) > max_len:
        return masked[:max_len] + "..."
    return masked


def redact_path(path: str) -> str:
    """Strip likely PII from URL paths (phone numbers, emails, long ids)."""
    masked = PHONE_RE.sub("[phone]", path)
    masked = EMAIL_RE.sub("[email]", masked)
    return masked
