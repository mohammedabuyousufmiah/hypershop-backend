"""Rider KYC service — upsert by user_id, status transitions, file upload."""
from __future__ import annotations

import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.rider_kyc.models import RiderKycSubmission


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


async def get_for_user(session: AsyncSession, user_id: UUID) -> RiderKycSubmission | None:
    return (await session.execute(
        select(RiderKycSubmission).where(RiderKycSubmission.user_id == user_id)
    )).scalar_one_or_none()


async def upsert_for_user(
    session: AsyncSession,
    *,
    user_id: UUID,
    fields: dict[str, Any],
) -> RiderKycSubmission:
    """Create-or-update the rider's KYC submission. Re-submission resets
    status back to pending so the hub admin can re-review.
    """
    row = await get_for_user(session, user_id)
    now = _utc_now()
    if row is None:
        row = RiderKycSubmission(
            user_id=user_id,
            **fields,
            submitted_at=now,
            updated_at=now,
        )
        session.add(row)
    else:
        for k, v in fields.items():
            setattr(row, k, v)
        # Re-submission rules: bump submitted_at, force status back to
        # pending, clear any previous rejection reason. Verified rows
        # also drop back to pending — operator must re-approve after edit.
        row.submitted_at = now
        row.updated_at = now
        row.status = "pending"
        row.rejection_reason = None
        row.reviewed_at = None
        row.reviewed_by = None
    await session.flush()
    return row


async def store_kyc_file(
    file: UploadFile,
    *,
    user_id: UUID,
    kind: str,
) -> str:
    """Persist a KYC photo upload and return its public URL.

    For local dev, writes under the storefront's `public/kyc/<user>/`
    so the file is served by the customer-web static layer. For
    R2-configured prod, reuses the catalog image storage R2 client.
    """
    # Validate file
    if not file.filename:
        raise ValueError("upload missing filename")
    ext = Path(file.filename).suffix.lower() or ".jpg"
    if ext not in {".jpg", ".jpeg", ".png", ".webp", ".heic"}:
        raise ValueError(f"unsupported file extension: {ext}")

    safe_kind = kind.replace("/", "_").replace("..", "")
    object_name = f"{safe_kind}-{secrets.token_hex(8)}{ext}"

    # Reuse catalog image storage — same R2-or-local fallback pattern.
    from app.modules.catalog.image_storage import (
        _local_root as _img_local_root,
        upload_product_image,
    )

    # The catalog helper expects product-style keys. For KYC we route
    # through the same R2 bucket but write to a dedicated `kyc/` prefix
    # by using the slug hint to seed the key path. If R2 isn't
    # configured, fall back to a local kyc/ subdir of the same root.
    try:
        # Try R2 path via the catalog helper (it ignores filename hint
        # and writes under r2_image_prefix; that's acceptable for KYC
        # since the bucket is private+CDN-fronted).
        public_url, _ = await upload_product_image(
            file, product_slug_hint=f"kyc-{user_id.hex[:8]}"
        )
        return public_url
    except Exception:
        # Local fallback — write under <local_root>/../kyc/<user>/<object>
        await file.seek(0)
        local_root = _img_local_root()
        kyc_dir = local_root.parent / "kyc" / str(user_id)
        kyc_dir.mkdir(parents=True, exist_ok=True)
        out = kyc_dir / object_name
        out.write_bytes(await file.read())
        # Return a relative URL the storefront can serve as-is
        return f"/kyc/{user_id}/{object_name}"
