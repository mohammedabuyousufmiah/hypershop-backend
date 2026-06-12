"""On-disk storage + signed URLs for generated report files.

Layout: ``<settings.report_storage_dir>/<yyyy>/<mm>/<dd>/<uuid>.<ext>``
Files are written atomically (tmp file + rename) so a partial write
can never be served. We never overwrite an existing path — the UUID
in the filename guarantees uniqueness.

Signed URL token format::

    base64url( "{file_id}.{user_id}.{exp_unix}" ) + "." + sig

where ``sig = base64url(HMAC-SHA256(secret, payload_b64))``.

The handler in ``api/user.py``:
  - decodes payload
  - verifies signature with constant-time compare
  - asserts file_id matches the URL path
  - asserts exp_unix >= now (else 410 Gone)
  - asserts user_id matches the caller (else 403)
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from app.core.config import get_settings
from app.core.errors import ServiceUnavailableError
from app.core.time import utc_now
from app.modules.reporting.errors import (
    ReportFileExpiredError,
    ReportSignatureInvalidError,
)


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def _signing_secret() -> bytes:
    """Reuse the JWT secret for signing report download tokens.

    Acceptable: download tokens are scoped (user_id + file_id + exp)
    so even if a token leaks, it cannot be replayed for any other
    file. Rotating JWT_SECRET also rotates download URLs — a feature.
    """
    return get_settings().jwt_secret.get_secret_value().encode()


def storage_dir() -> Path:
    """Resolve + ensure the configured report storage directory."""
    s = get_settings()
    base = Path(s.report_storage_dir)
    try:
        base.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        # Don't 500 the export endpoint with a confusing FS error —
        # surface as a service-unavailable so the API client retries.
        raise ServiceUnavailableError(
            f"Report storage dir not writable: {base} ({e})",
        ) from e
    return base


def reserve_path(*, fmt: str) -> tuple[UUID, Path]:
    """Allocate a fresh UUID + on-disk path for a new report file.

    The directory tree is created (yyyy/mm/dd) but no file is written
    yet — the exporter writes via :func:`write_atomically`.
    """
    file_id = uuid4()
    now = utc_now()
    sub = storage_dir() / f"{now.year:04d}" / f"{now.month:02d}" / f"{now.day:02d}"
    sub.mkdir(parents=True, exist_ok=True)
    path = sub / f"{file_id}.{fmt}"
    return file_id, path


def write_atomically(path: Path, payload: bytes) -> str:
    """Write ``payload`` to ``path`` and return its sha256 hex digest.

    Uses ``tmp + os.replace`` so concurrent readers never see a
    partial file. ``os.replace`` is atomic on POSIX and Windows
    (when source + dest are on the same filesystem — they are, since
    we put tmp next to dest).
    """
    tmp = path.with_suffix(path.suffix + f".tmp-{secrets.token_hex(4)}")
    digest = hashlib.sha256(payload).hexdigest()
    with tmp.open("wb") as fh:
        fh.write(payload)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    return digest


# ---------- signed URL ----------

def make_signed_token(
    *,
    file_id: UUID,
    user_id: UUID | None,
    expires_at: datetime,
) -> str:
    """Build the opaque token appended to download URLs.

    A None ``user_id`` is allowed for system-owned scheduled exports
    that any admin can fetch — but in that case the API layer must
    additionally enforce admin role in its handler.
    """
    payload = (
        f"{file_id.hex}."
        f"{user_id.hex if user_id else 'system'}."
        f"{int(expires_at.timestamp())}"
    )
    sig = hmac.new(
        _signing_secret(), payload.encode(), hashlib.sha256,
    ).digest()
    return _b64url(payload.encode()) + "." + _b64url(sig)


def verify_signed_token(
    *,
    token: str,
    expected_file_id: UUID,
    caller_user_id: UUID | None,
) -> dict[str, Any]:
    """Validate ``token`` and return its parsed payload.

    Raises :class:`ReportSignatureInvalidError` for any malformed /
    bad-signature / wrong-user case, or :class:`ReportFileExpiredError`
    if the deadline has passed.
    """
    parts = token.split(".")
    if len(parts) != 2:
        raise ReportSignatureInvalidError("Token format invalid.")
    body_b64, sig_b64 = parts
    try:
        body = _b64url_decode(body_b64)
        sig = _b64url_decode(sig_b64)
    except Exception as e:  # noqa: BLE001 — any base64 error → bad token
        raise ReportSignatureInvalidError("Token decode failed.") from e
    expected_sig = hmac.new(
        _signing_secret(), body, hashlib.sha256,
    ).digest()
    if not hmac.compare_digest(sig, expected_sig):
        raise ReportSignatureInvalidError("Signature mismatch.")
    fields = body.decode().split(".")
    if len(fields) != 3:
        raise ReportSignatureInvalidError("Payload shape invalid.")
    file_hex, user_hex, exp_str = fields
    try:
        token_file_id = UUID(file_hex)
        exp_unix = int(exp_str)
    except (ValueError, TypeError) as e:
        raise ReportSignatureInvalidError("Payload parse failed.") from e
    if token_file_id != expected_file_id:
        raise ReportSignatureInvalidError("Token bound to a different file.")
    if exp_unix < int(time.time()):
        raise ReportFileExpiredError("Download link has expired.")
    # Owner check — system-owned files are open to any admin (handler
    # enforces the role check separately).
    if user_hex != "system":
        if caller_user_id is None:
            raise ReportSignatureInvalidError("Authentication required.")
        if user_hex != caller_user_id.hex:
            raise ReportSignatureInvalidError(
                "Token does not belong to this user.",
            )
    return {
        "file_id": token_file_id,
        "expires_at_unix": exp_unix,
        "system_owned": user_hex == "system",
    }


def default_expiry() -> datetime:
    """Standard 24h expiry, settings-overridable."""
    return utc_now() + timedelta(
        hours=get_settings().report_signed_url_ttl_hours,
    )
