"""Admin video routes — upload, moderate, disable, delete.

Permission used: ``catalog.product.write`` (existing). We deliberately
do NOT introduce a new ``catalog.video.*`` permission for the MVP —
the people who can edit a product can manage its videos.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID, uuid4

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Path as PathParam,
    Query,
    UploadFile,
    status,
)

from app.core.config import get_settings
from app.core.db.uow import UnitOfWork, get_uow
from app.core.security.deps import get_current_principal
from app.core.security.principal import Principal
from app.core.security.rbac import requires_permission
from app.modules.product_videos.codes import ALL_STATUSES
from app.modules.product_videos.errors import (
    ProductVideoFileTooLargeError,
    ProductVideoUnsupportedTypeError,
)
from app.modules.product_videos.schemas import (
    AdminProductVideo,
    AdminVideoDisable,
    AdminVideoList,
    AdminVideoReject,
    AdminVideoUpdate,
)
from app.modules.product_videos.service import ProductVideoService
from app.modules.product_videos.storage import (
    private_key,
    r2_enabled,
    reserve_directory,
    stream_to_disk,
    upload_private_file,
)

router = APIRouter(prefix="/admin/catalog", tags=["admin-product-videos"])

_RW = "catalog.product.write"

# Generous list of MIME types browsers commonly emit on <input type=file>
# for video. We additionally check the extension as a second line of
# defense against mislabeled MIME.
_ALLOWED_MIME_PREFIX = "video/"
_ALLOWED_EXTS = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}


def _ext_from_filename(filename: str) -> str:
    dot = filename.rfind(".")
    return filename[dot:].lower() if dot >= 0 else ""


@router.post(
    "/products/{product_id}/videos",
    response_model=AdminProductVideo,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(requires_permission(_RW))],
)
async def upload_product_video(
    product_id: Annotated[UUID, PathParam(...)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    file: Annotated[UploadFile, File(...)],
    title: Annotated[str | None, Form()] = None,
    seller_id: Annotated[UUID | None, Form()] = None,
) -> AdminProductVideo:
    """Streaming upload — bytes spool to a tmpfile, get sha+size verified,
    then either land on R2 (production) or stay on disk (local dev).

    Whichever path is taken, the resulting ``raw_object_key`` is stored
    on the row; the worker reads it back via the matching download
    helper on its next 30-second tick.
    """
    settings = get_settings()
    max_bytes = settings.product_video_max_size_mb * 1024 * 1024
    mime = (file.content_type or "").lower()
    if not mime.startswith(_ALLOWED_MIME_PREFIX):
        raise ProductVideoUnsupportedTypeError(
            f"MIME {mime!r} is not a video container.",
        )
    ext = _ext_from_filename(file.filename or "")
    if ext not in _ALLOWED_EXTS:
        raise ProductVideoUnsupportedTypeError(
            f"Extension {ext!r} not allowed; expected one of {sorted(_ALLOWED_EXTS)}.",
        )

    # Pre-allocate the row id so the directory name = video id, which
    # makes ops triage trivial ("which folder belongs to that row?").
    video_id = uuid4()
    abs_dir, rel_dir = reserve_directory(video_id)
    target = abs_dir / f"original{ext}"
    use_r2 = r2_enabled()

    # Always spool to disk first so we can compute size/sha before
    # we either ship to R2 or leave on disk. Bunny doesn't enter the
    # picture here — Bunny only ever sees the FFmpeg outputs the
    # worker produces. Raw originals NEVER leave R2 (or local disk
    # in dev) once they're written.
    try:
        size, _sha = stream_to_disk(
            target=target,
            chunks=file.file,
            max_bytes=max_bytes,
        )
    except OverflowError as e:
        try:
            target.unlink(missing_ok=True)
        except OSError:
            pass
        raise ProductVideoFileTooLargeError(
            f"Upload exceeded {max_bytes} bytes.",
            details={"max_bytes": max_bytes},
        ) from e
    finally:
        await file.close()

    if use_r2:
        # Build the R2 private key from the same per-video sub-path so
        # the storage layout matches across providers (debugging).
        raw_object_key = private_key(f"{rel_dir}/{target.name}")
        try:
            upload_private_file(
                local_path=target,
                object_key=raw_object_key,
                content_type=mime,
            )
        except Exception:
            # Tmpfile is local-only — best-effort cleanup either way.
            try:
                target.unlink(missing_ok=True)
                abs_dir.rmdir()
            except OSError:
                pass
            raise
        # R2 has the bytes now; the on-disk tmpfile is no longer needed.
        try:
            target.unlink(missing_ok=True)
            abs_dir.rmdir()
        except OSError:
            pass
    else:
        # Local-dev fallback: keep the file on disk; the worker reads
        # it via :func:`absolute_path`. ``raw_object_key`` here is the
        # disk-relative path (NOT prefixed with the R2 private prefix).
        raw_object_key = f"{rel_dir}/{target.name}"

    try:
        async with uow.transactional() as session:
            svc = ProductVideoService(session)
            return await svc.register_upload(
                video_id=video_id,
                product_id=product_id,
                seller_id=seller_id,
                title=title,
                raw_object_key=raw_object_key,
                file_size_bytes=size,
                principal=principal,
            )
    except Exception:
        # Validation failure (missing product) leaves bytes either on
        # R2 or on disk — clean both paths so the storage doesn't
        # accumulate orphans pointing at deleted/never-created rows.
        try:
            if use_r2:
                from app.modules.product_videos.storage import delete_object
                delete_object(raw_object_key)
            else:
                target.unlink(missing_ok=True)
                abs_dir.rmdir()
        except Exception:  # noqa: BLE001 — cleanup is best-effort
            pass
        raise


@router.get(
    "/videos",
    response_model=AdminVideoList,
    dependencies=[Depends(requires_permission(_RW))],
)
async def list_admin_videos(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    product_id: Annotated[UUID | None, Query()] = None,
    seller_id: Annotated[UUID | None, Query()] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> AdminVideoList:
    if status_filter is not None and status_filter not in ALL_STATUSES:
        # Treat unknown status as "no rows" rather than a 422 — easier
        # for the admin UI which lets users type free-form.
        return AdminVideoList(items=[], total=0)

    async with uow.transactional() as session:
        svc = ProductVideoService(session)
        items, total = await svc.list_admin(
            status=status_filter,
            product_id=product_id,
            seller_id=seller_id,
            offset=offset,
            limit=limit,
        )
    return AdminVideoList(items=items, total=total)


@router.get(
    "/videos/{video_id}",
    response_model=AdminProductVideo,
    dependencies=[Depends(requires_permission(_RW))],
)
async def get_admin_video(
    video_id: Annotated[UUID, PathParam(...)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> AdminProductVideo:
    async with uow.transactional() as session:
        svc = ProductVideoService(session)
        return await svc.get_admin(video_id)


@router.patch(
    "/videos/{video_id}",
    response_model=AdminProductVideo,
    dependencies=[Depends(requires_permission(_RW))],
)
async def update_admin_video(
    video_id: Annotated[UUID, PathParam(...)],
    payload: AdminVideoUpdate,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> AdminProductVideo:
    async with uow.transactional() as session:
        svc = ProductVideoService(session)
        return await svc.update_title(video_id=video_id, title=payload.title)


@router.post(
    "/videos/{video_id}/approve",
    response_model=AdminProductVideo,
    dependencies=[Depends(requires_permission(_RW))],
)
async def approve_admin_video(
    video_id: Annotated[UUID, PathParam(...)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> AdminProductVideo:
    async with uow.transactional() as session:
        svc = ProductVideoService(session)
        return await svc.approve(video_id=video_id, principal=principal)


@router.post(
    "/videos/{video_id}/reject",
    response_model=AdminProductVideo,
    dependencies=[Depends(requires_permission(_RW))],
)
async def reject_admin_video(
    video_id: Annotated[UUID, PathParam(...)],
    payload: AdminVideoReject,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> AdminProductVideo:
    async with uow.transactional() as session:
        svc = ProductVideoService(session)
        return await svc.reject(
            video_id=video_id,
            reason=payload.reason,
            principal=principal,
        )


@router.post(
    "/videos/{video_id}/disable",
    response_model=AdminProductVideo,
    dependencies=[Depends(requires_permission(_RW))],
)
async def disable_admin_video(
    video_id: Annotated[UUID, PathParam(...)],
    payload: AdminVideoDisable,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> AdminProductVideo:
    async with uow.transactional() as session:
        svc = ProductVideoService(session)
        return await svc.disable(
            video_id=video_id,
            reason=payload.reason,
            principal=principal,
        )


@router.post(
    "/videos/{video_id}/reenable",
    response_model=AdminProductVideo,
    dependencies=[Depends(requires_permission(_RW))],
)
async def reenable_admin_video(
    video_id: Annotated[UUID, PathParam(...)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> AdminProductVideo:
    async with uow.transactional() as session:
        svc = ProductVideoService(session)
        return await svc.reenable(video_id=video_id, principal=principal)


@router.delete(
    "/videos/{video_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    dependencies=[Depends(requires_permission(_RW))],
)
async def delete_admin_video(
    video_id: Annotated[UUID, PathParam(...)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> None:
    async with uow.transactional() as session:
        svc = ProductVideoService(session)
        await svc.delete(video_id=video_id, principal=principal)
