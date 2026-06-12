"""Storage layer for product videos — on-disk helpers + Cloudflare R2.

This file owns TWO paths:

1. **On-disk** (local dev / single-VM deploys). The worker writes raw
   uploads + the FFmpeg HLS bundle under ``<root>/<yyyy>/<mm>/<dd>/<id>/``
   and the API streams them through ``/api/v1/catalog/videos/files/``.
   Used when ``settings.r2_bucket_name`` is empty.

2. **Cloudflare R2** (production). The same files live in an R2 bucket
   under two prefixes:

       <r2_private_prefix><id>/original.<ext>     — never public
       <r2_public_prefix><id>/poster.jpg
       <r2_public_prefix><id>/hls/master.m3u8
       <r2_public_prefix><id>/hls/720p/index.m3u8
       <r2_public_prefix><id>/hls/720p/seg*.m4s
       <r2_public_prefix><id>/hls/480p/...

   The PUBLIC prefix is fronted by Cloudflare's CDN at
   ``r2_public_base_url`` and served straight to customers. The
   PRIVATE prefix is reachable only via the boto3 client used by
   the API (upload) and the worker (download) — there is no signed-URL
   path that would let a customer pull a raw original.

   The two prefixes are configurable so operators can keep raw + public
   under the same bucket OR split them across buckets later.

Safety rules enforced by this module:

  * ``upload_private_file`` REQUIRES the key to start with the
    private prefix. ``upload_public_file`` requires the public prefix.
    Mismatch raises :class:`R2ObjectKeyError` (422) — a buggy caller
    can never accidentally publish a raw upload.
  * ``get_public_url`` REFUSES to mint URLs for keys under the private
    prefix. Combined with the schema split (``raw_object_key`` only
    appears on ``AdminProductVideo``, never on ``PublicProductVideo``),
    a private key cannot leak through any code path.
  * Every R2 call validates env config first via :func:`_r2_client`.
    Missing env names are surfaced verbatim in the error details so
    operators don't have to guess.
"""

from __future__ import annotations

import hashlib
import mimetypes
import os
import secrets
from functools import lru_cache
from pathlib import Path
from typing import IO, Any
from uuid import UUID

from app.core.config import get_settings
from app.core.logging import get_logger
from app.modules.product_videos.errors import (
    BunnyNotConfiguredError,
    BunnyUploadError,
    R2NotConfiguredError,
    R2ObjectKeyError,
)

_log = get_logger("hypershop.product_videos.storage")
from app.core.errors import ServiceUnavailableError
from app.core.time import utc_now


def storage_root() -> Path:
    """Resolve + ensure the configured product-video storage directory."""
    base = Path(get_settings().product_video_storage_dir)
    try:
        base.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise ServiceUnavailableError(
            f"Product video storage dir not writable: {base} ({e})",
        ) from e
    return base


def _video_root(video_id: UUID, *, created_at_dir: str | None = None) -> Path:
    """Compute the per-video directory.

    ``created_at_dir`` is the YYYY/MM/DD prefix already chosen at
    upload time. If absent we fall back to today's date — only safe
    when called from the original upload path.
    """
    base = storage_root()
    if created_at_dir is None:
        now = utc_now()
        created_at_dir = f"{now.year:04d}/{now.month:02d}/{now.day:02d}"
    return base / created_at_dir / video_id.hex


def reserve_directory(video_id: UUID) -> tuple[Path, str]:
    """Allocate today's directory for a fresh upload.

    Returns ``(absolute_dir, relative_dir)`` where ``relative_dir`` is
    what we persist on the row (so reads are independent of how the
    storage root is mounted).
    """
    now = utc_now()
    rel = f"{now.year:04d}/{now.month:02d}/{now.day:02d}/{video_id.hex}"
    abs_dir = storage_root() / rel
    abs_dir.mkdir(parents=True, exist_ok=True)
    return abs_dir, rel


def absolute_path(relative: str) -> Path:
    """Resolve a path stored in the DB against the configured root.

    Refuses any traversal outside the root — returns a value that may
    be safely passed to ``open()`` / ``FileResponse`` even if the DB
    column was tampered with.
    """
    base = storage_root().resolve()
    candidate = (base / relative).resolve()
    if not str(candidate).startswith(str(base) + os.sep) \
       and candidate != base:
        raise ServiceUnavailableError("Stored video path escaped storage root.")
    return candidate


def stream_to_disk(
    *,
    target: Path,
    chunks: IO[bytes],
    max_bytes: int,
) -> tuple[int, str]:
    """Stream an upload to disk, computing sha256 + size as we go.

    Aborts if ``max_bytes`` is exceeded — caller is expected to delete
    the partial file. Writes via tmp + os.replace for atomicity (so
    a crash mid-upload never leaves a 0-byte file the worker tries
    to process).
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + f".tmp-{secrets.token_hex(4)}")
    h = hashlib.sha256()
    written = 0
    try:
        with tmp.open("wb") as fh:
            while True:
                chunk = chunks.read(1024 * 256)
                if not chunk:
                    break
                written += len(chunk)
                if written > max_bytes:
                    raise OverflowError("upload exceeded max_bytes")
                h.update(chunk)
                fh.write(chunk)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, target)
    except Exception:
        # tmp may or may not exist; both branches are non-fatal.
        try:
            tmp.unlink(missing_ok=True)  # type: ignore[arg-type]
        except OSError:
            pass
        raise
    return written, h.hexdigest()


# ---------- URL building ----------


def public_base_url() -> str:
    """Public base URL the frontend hits for video bytes.

    Defaults to the API host serving ``/api/v1/catalog/videos/files/``;
    operators can point ``product_video_public_base_url`` at a CDN that
    fronts the same on-disk root for production.
    """
    s = get_settings()
    explicit = s.product_video_public_base_url
    if explicit:
        return explicit.rstrip("/")
    # Fallback: serve through the API. The catalog/videos/files route
    # already streams from disk + verifies approval state.
    return ""


def public_url_for(rel_path: str) -> str:
    """Map a stored relative path to its public URL.

    When ``product_video_public_base_url`` is set we assume the operator
    has mounted the storage root behind that URL (nginx, Caddy, S3+
    CloudFront, etc.). Otherwise we route through the API.
    """
    base = public_base_url()
    if base:
        return f"{base}/{rel_path}"
    # API-served path. The route is wired in api/public.py.
    return f"/api/v1/catalog/videos/files/{rel_path}"


# =====================================================================
#                       Cloudflare R2 (S3-compatible)
# =====================================================================
#
# All R2 calls below are SYNCHRONOUS. The boto3 client is sync; wrapping
# every call in ``asyncio.to_thread`` here would force every caller to
# pay the wrapper cost even when called from a sync context (e.g. CLI
# scripts). The async worker should wrap individual calls — see
# ``app/modules/product_videos/jobs.py``.
#
# The R2 endpoint is derived from the account id:
#
#     https://<R2_ACCOUNT_ID>.r2.cloudflarestorage.com
#
# R2 is API-compatible with S3 but does not support every S3 feature.
# We deliberately avoid: ACLs (R2 doesn't honour them — public access
# is via Cloudflare Worker / public bucket settings), bucket regions
# (R2 always uses ``auto``), versioning, and ObjectLock.

# Content-types for the file types we actually upload. Everything else
# falls through to ``mimetypes.guess_type`` and finally
# ``application/octet-stream``. Hardcoded for HLS because mimetypes
# returns the wrong value for .m3u8 (``audio/x-mpegurl`` instead of
# the apple-blessed manifest type) on some systems.
_HLS_CONTENT_TYPES = {
    ".m3u8": "application/vnd.apple.mpegurl",
    ".m4s": "video/iso.segment",
    ".mp4": "video/mp4",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}


def _missing_r2_env() -> list[str]:
    """Return the list of R2_* env vars that are unset, in env-name form.

    Empty list means R2 is fully configured. Caller decides whether to
    raise: the upload/download functions raise; ``r2_enabled()`` is a
    soft check the api can use to fall back to on-disk.
    """
    s = get_settings()
    pairs = [
        ("R2_ACCOUNT_ID", s.r2_account_id),
        ("R2_ACCESS_KEY_ID", s.r2_access_key_id),
        ("R2_SECRET_ACCESS_KEY", s.r2_secret_access_key),
        ("R2_BUCKET_NAME", s.r2_bucket_name),
    ]
    return [name for name, val in pairs if not val]


def r2_enabled() -> bool:
    """True iff R2 env is fully configured.

    Safe to call from request handlers — does NOT instantiate the boto3
    client. Use this to choose the on-disk vs R2 path at upload time.
    """
    return not _missing_r2_env()


@lru_cache(maxsize=1)
def _r2_client() -> Any:
    """Build (or reuse) the cached boto3 S3 client pointed at R2.

    Cached because boto3 client construction is expensive (TLS context,
    credential chain, schema parsing). We accept the trade-off that
    rotating R2 credentials requires a process restart — same trade-off
    every other secret in this app makes (JWT secret, payment provider
    keys, etc.).

    Raises :class:`R2NotConfiguredError` with the list of missing env
    vars if any are unset.
    """
    missing = _missing_r2_env()
    if missing:
        raise R2NotConfiguredError(
            f"R2 storage requires the following env vars to be set: "
            f"{', '.join(missing)}.",
            details={"missing_env_vars": missing},
        )

    # Lazy import — boto3 is heavy; we don't want to pay the import
    # cost on every backend boot (e.g. local dev with R2 unconfigured).
    import boto3
    from botocore.config import Config

    s = get_settings()
    return boto3.client(
        "s3",
        endpoint_url=f"https://{s.r2_account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=s.r2_access_key_id,
        aws_secret_access_key=s.r2_secret_access_key,
        # R2 always uses "auto" for region. Passing a real AWS region
        # makes boto3 sign requests in a way R2 rejects.
        region_name="auto",
        config=Config(
            signature_version="s3v4",
            retries={"max_attempts": 4, "mode": "standard"},
            connect_timeout=10,
            read_timeout=60,
            user_agent_extra="hypershop/product_videos",
        ),
    )


def _bucket() -> str:
    """Return the configured R2 bucket name, raising if missing."""
    name = get_settings().r2_bucket_name
    if not name:
        # `_r2_client()` would also catch this, but a direct check
        # gives a tighter error when the caller didn't go through the
        # client (e.g. building a key for logging).
        raise R2NotConfiguredError(
            "R2_BUCKET_NAME is not set.",
            details={"missing_env_vars": ["R2_BUCKET_NAME"]},
        )
    return name


def _content_type_for(path: Path, fallback: str | None = None) -> str:
    """Best-effort content-type lookup for a file path."""
    suffix = path.suffix.lower()
    if suffix in _HLS_CONTENT_TYPES:
        return _HLS_CONTENT_TYPES[suffix]
    guess, _ = mimetypes.guess_type(str(path))
    if guess:
        return guess
    return fallback or "application/octet-stream"


# ---------- key helpers ----------


def private_key(suffix: str) -> str:
    """Build a full key under the configured private prefix.

    ``suffix`` is the per-video path, e.g. ``<video_id>/original.mp4``.
    The result is what's stored in ``product_videos.raw_object_key``.
    """
    s = get_settings()
    return f"{s.r2_private_prefix.rstrip('/')}/{suffix.lstrip('/')}"


def public_key(suffix: str) -> str:
    """Build a full key under the configured public prefix."""
    s = get_settings()
    return f"{s.r2_public_prefix.rstrip('/')}/{suffix.lstrip('/')}"


def _ensure_private(object_key: str) -> None:
    s = get_settings()
    pref = s.r2_private_prefix
    if not object_key.startswith(pref):
        raise R2ObjectKeyError(
            f"Object key must start with the private prefix {pref!r}.",
            details={"object_key": object_key, "expected_prefix": pref},
        )


def _ensure_public(object_key: str) -> None:
    s = get_settings()
    pref = s.r2_public_prefix
    if not object_key.startswith(pref):
        raise R2ObjectKeyError(
            f"Object key must start with the public prefix {pref!r}.",
            details={"object_key": object_key, "expected_prefix": pref},
        )


# ---------- the six functions the spec calls out ----------


def upload_private_file(
    local_path: str | Path,
    object_key: str,
    content_type: str,
) -> str:
    """Upload a local file to the PRIVATE prefix of the R2 bucket.

    ``object_key`` MUST be the FULL key (including the private prefix);
    use :func:`private_key` to build it. Returns the same key on
    success — callers store this on ``product_videos.raw_object_key``.

    Raw uploads (the original mp4/mov/etc.) are private by contract:
    they are never accessible through any public endpoint. The worker
    pulls them via :func:`download_private_file` to run FFmpeg.
    """
    _ensure_private(object_key)
    path = Path(local_path)
    if not path.is_file():
        raise FileNotFoundError(f"upload_private_file: {path} is not a file")

    client = _r2_client()
    client.upload_file(
        Filename=str(path),
        Bucket=_bucket(),
        Key=object_key,
        ExtraArgs={
            "ContentType": content_type,
            # Defence-in-depth: even on operator misconfig that gives
            # the bucket public-read by default, this metadata ensures
            # any URL handed out via the SDK is short-lived. R2 itself
            # ignores ACLs but Cloudflare Worker rules respect cache
            # controls, so making private files no-cache is the right
            # signal.
            "CacheControl": "private, no-store, max-age=0",
        },
    )
    return object_key


def upload_public_file(
    local_path: str | Path,
    object_key: str,
    content_type: str,
) -> str:
    """Upload a local file to the PUBLIC prefix of the R2 bucket.

    ``object_key`` MUST be the FULL key (including the public prefix).
    Returns the key on success. Use :func:`get_public_url` to mint the
    customer-facing URL.

    Public files (HLS playlists/segments + thumbnails) are cached
    aggressively — they're immutable for the life of the video, so
    we set ``Cache-Control: public, max-age=31536000, immutable``.
    """
    _ensure_public(object_key)
    path = Path(local_path)
    if not path.is_file():
        raise FileNotFoundError(f"upload_public_file: {path} is not a file")

    client = _r2_client()
    client.upload_file(
        Filename=str(path),
        Bucket=_bucket(),
        Key=object_key,
        ExtraArgs={
            "ContentType": content_type,
            "CacheControl": "public, max-age=31536000, immutable",
        },
    )
    return object_key


def upload_public_folder(
    local_folder: str | Path,
    prefix: str,
) -> list[str]:
    """Recursively upload every file under ``local_folder`` into R2.

    ``prefix`` MUST be the full key prefix (including the public
    prefix), e.g. ``public(_key("abc-def/hls"))``. Each file is uploaded
    at ``<prefix>/<path-relative-to-local_folder>`` with a content-type
    derived from its suffix (HLS extensions are hard-mapped; everything
    else falls through to :mod:`mimetypes`).

    Returns the list of full object keys uploaded — handy for audit
    logs and for building per-segment public URLs.

    Used to push the entire ``hls/`` tree from the worker's scratch
    directory in one call after FFmpeg finishes.
    """
    _ensure_public(prefix.rstrip("/"))
    folder = Path(local_folder)
    if not folder.is_dir():
        raise FileNotFoundError(
            f"upload_public_folder: {folder} is not a directory",
        )

    client = _r2_client()
    bucket = _bucket()
    base_prefix = prefix.rstrip("/")
    uploaded: list[str] = []

    for file_path in sorted(folder.rglob("*")):
        if not file_path.is_file():
            continue
        rel = file_path.relative_to(folder).as_posix()
        key = f"{base_prefix}/{rel}"
        client.upload_file(
            Filename=str(file_path),
            Bucket=bucket,
            Key=key,
            ExtraArgs={
                "ContentType": _content_type_for(file_path),
                "CacheControl": "public, max-age=31536000, immutable",
            },
        )
        uploaded.append(key)

    return uploaded


def download_private_file(
    object_key: str,
    local_path: str | Path,
) -> Path:
    """Download a private R2 object to ``local_path``.

    Used by the worker to fetch the raw upload before FFmpeg runs.
    The function refuses to download anything OUTSIDE the private
    prefix — public assets are handed to customers as URLs, never
    written to a worker's disk.

    Returns the resolved :class:`pathlib.Path` so the caller can
    pass it straight to FFmpeg.
    """
    _ensure_private(object_key)
    target = Path(local_path)
    target.parent.mkdir(parents=True, exist_ok=True)

    client = _r2_client()
    # Atomic-ish write: download to a tmp file then rename. boto3's
    # download_file already retries internally; the tmp+rename guards
    # against a SIGKILL mid-download leaving a half-baked file FFmpeg
    # would happily try to probe.
    tmp = target.with_suffix(target.suffix + f".tmp-{secrets.token_hex(4)}")
    try:
        client.download_file(
            Bucket=_bucket(),
            Key=object_key,
            Filename=str(tmp),
        )
        os.replace(tmp, target)
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return target


def get_public_url(object_key: str) -> str:
    """Build the customer-facing URL for a PUBLIC R2 key.

    Refuses to mint a URL for a private-prefix key — combined with the
    schema split (raw_object_key never appears on PublicProductVideo)
    this enforces "never expose raw_object_key in public API" at the
    storage layer too. A buggy caller will get a 422, not a leaked
    download URL.

    The URL is built as ``<r2_public_base_url>/<object_key>``. If the
    operator has fronted the bucket with a custom domain, the same
    object key resolves under that domain — Cloudflare CDN serves the
    bucket key path-for-path. ``r2_public_base_url`` MUST be set when
    R2 is enabled; otherwise this raises with a clear message.
    """
    _ensure_public(object_key)
    s = get_settings()
    base = s.r2_public_base_url.rstrip("/")
    if not base:
        raise R2NotConfiguredError(
            "R2_PUBLIC_BASE_URL is not set; public R2 URLs cannot be built. "
            "Configure your Cloudflare CDN domain that fronts the bucket.",
            details={"missing_env_vars": ["R2_PUBLIC_BASE_URL"]},
        )
    return f"{base}/{object_key}"


def delete_object(object_key: str) -> None:
    """Delete a single R2 object. Idempotent — a missing key is a no-op.

    Accepts keys under EITHER prefix. Deletion is the one operation
    where touching both halves of the bucket from one call site is
    fine: when an admin deletes a video we want both the raw original
    AND the public bundle gone.

    R2's ``DeleteObject`` returns 204 whether or not the key existed,
    so this function never raises for a not-found.
    """
    client = _r2_client()
    client.delete_object(Bucket=_bucket(), Key=object_key)


# =====================================================================
#                       Bunny.net Storage Zone + Pull Zone CDN
# =====================================================================
#
# Bunny Storage is NOT S3-compatible — it speaks a small custom HTTP
# API. We use ``httpx`` (already in deps) instead of boto3 here:
#
#   PUT    https://<region>.storage.bunnycdn.com/<zone>/<path>
#       headers: AccessKey: <storage_zone_password>
#       body:    raw file bytes
#       returns: 201 on success, 400/401/404 with text body on error
#
#   DELETE https://<region>.storage.bunnycdn.com/<zone>/<path>
#       headers: AccessKey: <storage_zone_password>
#       returns: 200 / 404
#
# The "region" segment is the storage zone's primary region prefix;
# the empty/main DE region uses ``storage.bunnycdn.com`` (no prefix).
#
# Public reads happen through the Pull Zone, NOT this client — we
# only PUT/DELETE here. The Pull Zone is configured in the Bunny
# dashboard to use the Storage Zone as its origin; customers fetch
# HLS via ``https://<pull_zone_hostname>/<path>`` (or whatever custom
# domain you've attached).
#
# Pipeline split (with R2):
#   - raw original (private)  → R2  via upload_private_file()
#   - HLS + thumbnail (public) → Bunny Storage via bunny_upload_*()
#                                 served to customers by Bunny CDN.

_BUNNY_TIMEOUT_SECONDS = 60.0
# Bunny's docs cap a single PUT at ~5 GB; we never come close, but a
# defensive bound on the file size keeps a runaway buffer from chewing
# the worker's RAM if a tampered row points at a giant file.
_BUNNY_MAX_PUT_BYTES = 200 * 1024 * 1024


def _missing_bunny_env() -> list[str]:
    """Return the env vars Bunny needs but doesn't have set."""
    s = get_settings()
    pairs = [
        ("BUNNY_STORAGE_ZONE_NAME", s.bunny_storage_zone_name),
        ("BUNNY_STORAGE_ACCESS_KEY", s.bunny_storage_access_key),
        ("BUNNY_PULL_ZONE_HOSTNAME", s.bunny_pull_zone_hostname),
    ]
    return [name for name, val in pairs if not val]


def bunny_enabled() -> bool:
    """Soft check — does NOT touch httpx/boto3. Safe in hot path."""
    return not _missing_bunny_env()


def _bunny_assert_configured() -> None:
    missing = _missing_bunny_env()
    if missing:
        raise BunnyNotConfiguredError(
            f"Bunny Storage requires: {', '.join(missing)}.",
            details={"missing_env_vars": missing},
        )


def _bunny_storage_endpoint() -> str:
    """Build the regional Bunny Storage base URL.

    The main DE zone uses the bare hostname; every other region adds
    a leading ``<region>.``. Wrong region = signed-url mismatches and
    404s, so we keep the mapping mechanical.
    """
    s = get_settings()
    region = (s.bunny_storage_region or "").strip().lower()
    host = "storage.bunnycdn.com" if not region else f"{region}.storage.bunnycdn.com"
    return f"https://{host}"


def _bunny_object_path(remote_path: str) -> str:
    """Prepend the configured path prefix and clean stray slashes.

    ``remote_path`` is the per-video sub-path the caller chose, e.g.
    ``<video_id>/poster.jpg`` or ``<video_id>/hls/master.m3u8``. The
    full key inside the storage zone is ``<bunny_path_prefix><remote>``.
    """
    s = get_settings()
    prefix = s.bunny_path_prefix.strip("/")
    rel = remote_path.strip("/")
    if prefix:
        return f"{prefix}/{rel}"
    return rel


def _bunny_url_for_storage_op(remote_path: str) -> str:
    """Build the PUT/DELETE URL for a Bunny Storage object.

    NOTE: this is the ORIGIN URL (storage.bunnycdn.com), not the CDN
    URL — never hand this back to the frontend. Public reads go
    through :func:`bunny_public_url` which uses the Pull Zone host.
    """
    s = get_settings()
    return (
        f"{_bunny_storage_endpoint()}/"
        f"{s.bunny_storage_zone_name}/"
        f"{_bunny_object_path(remote_path)}"
    )


def _bunny_headers() -> dict[str, str]:
    s = get_settings()
    # AccessKey here is Bunny's storage-zone password, NOT the global
    # API key. Keep them separate in env so a leaked storage key can
    # be rotated without touching the rest of the Bunny account.
    return {
        "AccessKey": s.bunny_storage_access_key or "",
        "Accept": "application/json",
    }


def bunny_upload_public_file(
    local_path: str | Path,
    remote_path: str,
    content_type: str,
) -> str:
    """Upload a single file to the Bunny Storage zone.

    ``remote_path`` is the path WITHIN the storage zone, AFTER the
    configured ``bunny_path_prefix`` is applied — i.e. callers pass
    ``<video_id>/poster.jpg`` and the helper prepends the prefix.

    Returns the public Pull Zone URL the customer can fetch directly.

    Bunny's ``Content-Type`` is set on the stored object so the CDN
    serves the right MIME (HLS playlists need
    ``application/vnd.apple.mpegurl`` or browsers refuse them).
    """
    _bunny_assert_configured()
    path = Path(local_path)
    if not path.is_file():
        raise FileNotFoundError(f"bunny_upload_public_file: {path} not a file")

    size = path.stat().st_size
    if size > _BUNNY_MAX_PUT_BYTES:
        raise BunnyUploadError(
            f"File {path.name} is {size} bytes; max upload is "
            f"{_BUNNY_MAX_PUT_BYTES}.",
            details={"size_bytes": size, "max_bytes": _BUNNY_MAX_PUT_BYTES},
        )

    url = _bunny_url_for_storage_op(remote_path)
    # Lazy import — keep httpx out of the hot import path. (It's
    # already in deps for httpx-based providers.)
    import httpx
    import time as _time

    from app.modules.product_videos.metrics import (
        bunny_upload_duration_seconds,
    )

    headers = {**_bunny_headers(), "Content-Type": content_type}
    started = _time.perf_counter()
    try:
        with path.open("rb") as fh:
            with httpx.Client(timeout=_BUNNY_TIMEOUT_SECONDS) as client:
                resp = client.put(url, content=fh.read(), headers=headers)
    except Exception:
        # Connection refused / DNS / TLS errors don't return a status
        # code — record under "error" so dashboards still see the
        # latency contribution before re-raising.
        bunny_upload_duration_seconds.labels(outcome="error").observe(
            _time.perf_counter() - started,
        )
        raise

    duration = _time.perf_counter() - started
    outcome = "success" if resp.status_code in (200, 201) else "error"
    bunny_upload_duration_seconds.labels(outcome=outcome).observe(duration)

    if resp.status_code not in (200, 201):
        # Bunny includes the failure reason in the body — surface a
        # short tail so ops can distinguish auth from zone issues
        # without flipping debug logging on.
        body = (resp.text or "")[:500]
        _log.warning(
            "bunny_upload_failed",
            remote_path=remote_path,
            status_code=resp.status_code,
            body=body,
        )
        # Lazy import to avoid prometheus_client cost on dev paths
        # that never touch Bunny.
        from app.modules.product_videos.metrics import bunny_upload_errors_total

        bunny_upload_errors_total.labels(
            status_code=str(resp.status_code),
        ).inc()
        raise BunnyUploadError(
            f"Bunny PUT {remote_path} returned {resp.status_code}.",
            details={
                "status_code": resp.status_code,
                "body": body,
                "remote_path": remote_path,
            },
        )

    return bunny_public_url(remote_path)


def bunny_upload_public_folder(
    local_folder: str | Path,
    remote_prefix: str,
) -> list[str]:
    """Recursively upload every file under ``local_folder`` to Bunny.

    ``remote_prefix`` is the path WITHIN the storage zone (the
    ``bunny_path_prefix`` config is added on top automatically).
    Each file is uploaded at ``<prefix>/<path-relative-to-folder>``.

    Returns the list of CDN URLs uploaded — handy when the caller
    needs the master playlist URL specifically (it's the one ending
    in ``.m3u8`` in the top of the folder).

    Used by the worker to push the entire ``hls/`` tree from its
    scratch directory after FFmpeg finishes.
    """
    _bunny_assert_configured()
    folder = Path(local_folder)
    if not folder.is_dir():
        raise FileNotFoundError(
            f"bunny_upload_public_folder: {folder} is not a directory",
        )

    base = remote_prefix.strip("/")
    urls: list[str] = []
    for file_path in sorted(folder.rglob("*")):
        if not file_path.is_file():
            continue
        rel = file_path.relative_to(folder).as_posix()
        remote = f"{base}/{rel}" if base else rel
        urls.append(
            bunny_upload_public_file(
                file_path,
                remote,
                _content_type_for(file_path),
            ),
        )
    return urls


def bunny_delete_object(remote_path: str) -> None:
    """Delete a single Bunny Storage object. Idempotent.

    Bunny returns 200 on success, 404 if the object was already
    missing — both are fine for our cleanup path.
    """
    _bunny_assert_configured()
    url = _bunny_url_for_storage_op(remote_path)
    import httpx

    with httpx.Client(timeout=_BUNNY_TIMEOUT_SECONDS) as client:
        resp = client.delete(url, headers=_bunny_headers())

    if resp.status_code not in (200, 204, 404):
        body = (resp.text or "")[:500]
        _log.warning(
            "bunny_delete_failed",
            remote_path=remote_path,
            status_code=resp.status_code,
            body=body,
        )
        raise BunnyUploadError(
            f"Bunny DELETE {remote_path} returned {resp.status_code}.",
            details={
                "status_code": resp.status_code,
                "body": body,
                "remote_path": remote_path,
            },
        )


def bunny_public_url(remote_path: str) -> str:
    """Build the customer-facing CDN URL for a Bunny Storage object.

    ``remote_path`` follows the same convention as the upload helpers
    (callers pass the per-video sub-path; the configured
    ``bunny_path_prefix`` is added). The Pull Zone is configured in
    the Bunny dashboard to mirror the Storage Zone path-for-path, so
    the same key resolves under the CDN hostname.
    """
    _bunny_assert_configured()
    s = get_settings()
    host = s.bunny_pull_zone_hostname.rstrip("/")
    if not host.startswith(("http://", "https://")):
        host = f"https://{host}"
    return f"{host}/{_bunny_object_path(remote_path)}"
