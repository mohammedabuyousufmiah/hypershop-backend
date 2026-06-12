"""Image storage layer for product media.

Two-mode store, same calling contract from the admin upload endpoint:

* **Local dev / dev fallback** — when ``settings.r2_bucket_name`` is
  empty, the file is written under
  ``settings.product_image_local_dir`` and the response URL is
  ``settings.product_image_local_public_url_prefix + <filename>``. In
  this repo's default setup that folder is the customer-web
  ``public/products/`` directory, so a URL like ``/products/abc.jpg``
  resolves through the storefront's own static serving — no extra
  static mount on the backend needed.

* **R2 production** — when R2 is configured, the file is streamed
  into the configured bucket under
  ``<r2_image_prefix><yyyy>/<mm>/<dd>/<uuid>.<ext>`` and the response
  URL is built from ``r2_public_base_url`` (the Cloudflare CDN that
  fronts the public prefix of the bucket).

Switching modes requires no code change — only that the four R2 env
vars (``R2_ACCOUNT_ID``, ``R2_ACCESS_KEY_ID``, ``R2_SECRET_ACCESS_KEY``,
``R2_BUCKET_NAME``) plus ``R2_PUBLIC_BASE_URL`` are filled in. The
existing video R2 helpers in ``app.modules.product_videos.storage``
build the boto3 client; we reuse that client here.
"""
from __future__ import annotations

import mimetypes
import re
import secrets
from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile

from app.core.config import get_settings
from app.core.errors import ServiceUnavailableError, ValidationError
from app.core.logging import get_logger
from app.core.time import utc_now

_log = get_logger("hypershop.catalog.image_storage")

# Whitelist of accepted MIME types and the suffix to persist on disk.
_ALLOWED_TYPES: dict[str, str] = {
    "image/jpeg": ".jpg",
    "image/pjpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/avif": ".avif",
    "image/gif": ".gif",
}

# Magic-byte sniffing — the client-supplied Content-Type is advisory
# only. We refuse anything whose first bytes don't match a known image
# format. This blocks an attacker who labels an .exe as image/jpeg.
def _sniff_image_kind(prefix: bytes) -> str | None:
    if prefix.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if prefix.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if prefix[:4] == b"RIFF" and prefix[8:12] == b"WEBP":
        return "image/webp"
    if prefix[:4] == b"GIF8":
        return "image/gif"
    if len(prefix) >= 12 and prefix[4:8] == b"ftyp" and prefix[8:12] in (
        b"avif", b"avis", b"mif1", b"msf1",
    ):
        return "image/avif"
    return None


_SLUG_SAFE = re.compile(r"[^a-z0-9._-]+")


def _safe_filename_stem(raw: str | None) -> str:
    """Return a filesystem-safe slug derived from an upload's original
    filename. The slug is *purely* aesthetic — uniqueness is provided
    by the UUID prefix in :func:`_new_object_key`. We keep it so a
    human inspecting the bucket can still tell what each blob was.
    """
    if not raw:
        return "image"
    base = Path(raw).stem.lower()
    base = _SLUG_SAFE.sub("-", base).strip("-")
    return (base or "image")[:60]


def _new_object_key(
    prefix: str,
    *,
    suffix: str,
    name_stem: str | None = None,
) -> str:
    """Pick a unique key under ``prefix``. Date-sharded to keep R2
    listings tractable when the catalog grows large.

    Returned format:  ``<prefix><yyyy>/<mm>/<dd>/<uuid>-<stem><suffix>``
    """
    now = utc_now()
    rand = uuid4().hex
    stem = _safe_filename_stem(name_stem)
    fname = f"{rand}-{stem}{suffix}"
    return f"{prefix}{now.year:04d}/{now.month:02d}/{now.day:02d}/{fname}"


def _local_relative_url(local_root: Path, local_path: Path) -> str:
    """Build a forward-slash URL relative to the local public root
    so it resolves against the storefront origin."""
    settings = get_settings()
    rel = local_path.relative_to(local_root)
    return settings.product_image_local_public_url_prefix.rstrip("/") + "/" + rel.as_posix()


# ---------- payload validation ----------


async def _read_validated_payload(file: UploadFile) -> tuple[bytes, str, str]:
    """Read the upload into memory, validate size + magic bytes, and
    return ``(data, content_type, suffix)``.

    Raises :class:`ValidationError` for any payload that isn't a
    recognised image or exceeds the size limit.
    """
    settings = get_settings()
    max_bytes = settings.product_image_max_size_mb * 1024 * 1024

    # Read up to max+1 so we can detect oversize without buffering
    # unbounded.
    data = await file.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValidationError(
            f"Image exceeds the {settings.product_image_max_size_mb} MB limit.",
            details={"max_size_mb": settings.product_image_max_size_mb},
        )
    if len(data) == 0:
        raise ValidationError("Uploaded image is empty.")

    # Determine MIME from magic bytes (authoritative); fall back to
    # the client-supplied content_type only to disambiguate (e.g.
    # accept text/plain claim if magic bytes say image/png).
    sniffed = _sniff_image_kind(data[:16])
    if sniffed is None:
        raise ValidationError(
            "Uploaded file is not a recognised image format. "
            "Accepted: JPEG, PNG, WebP, GIF, AVIF.",
        )
    if sniffed not in _ALLOWED_TYPES:
        raise ValidationError(
            f"Image type {sniffed} is not allowed. "
            f"Accepted: {', '.join(sorted(_ALLOWED_TYPES))}.",
        )
    suffix = _ALLOWED_TYPES[sniffed]
    return data, sniffed, suffix


# ---------- mode selectors ----------


def r2_enabled() -> bool:
    s = get_settings()
    return bool(
        s.r2_bucket_name
        and s.r2_account_id
        and s.r2_access_key_id
        and s.r2_secret_access_key
    )


def _local_root() -> Path:
    settings = get_settings()
    root = Path(settings.product_image_local_dir)
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise ServiceUnavailableError(
            f"Local product image dir not writable: {root} ({e})",
        ) from e
    return root


# ---------- public api ----------


async def upload_product_image(
    file: UploadFile,
    *,
    product_slug_hint: str | None = None,
) -> tuple[str, str]:
    """Persist ``file`` and return ``(public_url, storage_kind)``.

    ``product_slug_hint`` is folded into the on-disk / R2 key for
    debuggability — it never affects routing or uniqueness.

    ``storage_kind`` is either ``"r2"`` or ``"local"`` so the caller
    can log + surface it in the audit trail.
    """
    data, content_type, suffix = await _read_validated_payload(file)
    settings = get_settings()

    if r2_enabled():
        # Lazy import — keeps the catalog module independent of the
        # video module's heavy boto3 import at boot time.
        from app.modules.product_videos.storage import _r2_client, _bucket

        key = _new_object_key(
            settings.r2_image_prefix,
            suffix=suffix,
            name_stem=product_slug_hint,
        )
        client = _r2_client()
        client.put_object(
            Bucket=_bucket(),
            Key=key,
            Body=data,
            ContentType=content_type,
            # Product imagery is immutable per object — we overwrite by
            # uploading a new object, never by mutating one in place,
            # so aggressive caching is safe.
            CacheControl="public, max-age=31536000, immutable",
        )
        base = settings.r2_public_base_url.rstrip("/")
        public_url = f"{base}/{key}" if base else key
        _log.info(
            "product_image_uploaded_r2",
            key=key,
            bytes=len(data),
            content_type=content_type,
            product_slug=product_slug_hint,
        )
        return public_url, "r2"

    # ---- local dev fallback ----
    root = _local_root()
    # Date-shard the local dir too so the customer-web /public/products
    # folder doesn't end up with thousands of flat files.
    now = utc_now()
    sub = root / f"{now.year:04d}" / f"{now.month:02d}" / f"{now.day:02d}"
    sub.mkdir(parents=True, exist_ok=True)
    rand = secrets.token_hex(8)
    stem = _safe_filename_stem(product_slug_hint or file.filename)
    fname = f"{rand}-{stem}{suffix}"
    target = sub / fname
    target.write_bytes(data)
    public_url = _local_relative_url(root, target)
    _log.info(
        "product_image_uploaded_local",
        path=str(target),
        bytes=len(data),
        content_type=content_type,
        product_slug=product_slug_hint,
    )
    return public_url, "local"


__all__ = ["upload_product_image", "r2_enabled"]
