"""Customer-authored review endpoints — auth required.

  * POST /products/{product_id}/reviews        — create
  * PATCH /reviews/{review_id}                 — edit (within 24h)
  * POST /reviews/{review_id}/helpful          — upvote (idempotent)
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    Path as PathParam,
    UploadFile,
    status,
)

from app.core.db.uow import UnitOfWork, get_uow
from app.core.security.deps import get_current_principal
from app.core.security.principal import Principal
from app.core.security.rbac import requires_permission
from app.modules.reviews.api.public import _serialize_public
from app.modules.reviews.codes import (
    ALLOWED_IMAGE_EXTS,
    ALLOWED_IMAGE_MIMES,
    MAX_IMAGE_BYTES,
)
from app.modules.reviews.errors import (
    ReviewMediaTooLargeError,
    ReviewMediaUnsupportedTypeError,
)
from app.modules.reviews.media import store_image
from app.modules.reviews.schemas import (
    HelpfulVoteOut,
    PublicReviewOut,
    ReviewCreateIn,
    ReviewMediaOut,
    ReviewUpdateIn,
)
from app.modules.reviews.service import ReviewService

router = APIRouter(tags=["reviews-customer"])

_W = "reviews.write"


@router.post(
    "/products/{product_id}/reviews",
    response_model=PublicReviewOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(requires_permission(_W))],
)
async def create_review(
    product_id: Annotated[UUID, PathParam(...)],
    body: ReviewCreateIn,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> PublicReviewOut:
    async with uow.transactional() as session:
        svc = ReviewService(session)
        review = await svc.create(
            product_id=product_id,
            customer_id=principal.user_id,
            rating=body.rating,
            title=body.title,
            body=body.body,
            principal=principal,
        )
        items = await _serialize_public(session, [review])
    return items[0]


@router.patch(
    "/reviews/{review_id}",
    response_model=PublicReviewOut,
    dependencies=[Depends(requires_permission(_W))],
)
async def edit_review(
    review_id: Annotated[UUID, PathParam(...)],
    body: ReviewUpdateIn,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> PublicReviewOut:
    async with uow.transactional() as session:
        svc = ReviewService(session)
        review = await svc.edit(
            review_id=review_id,
            customer_id=principal.user_id,
            rating=body.rating,
            title=body.title,
            body=body.body,
        )
        items = await _serialize_public(session, [review])
    return items[0]


@router.post(
    "/reviews/{review_id}/helpful",
    response_model=HelpfulVoteOut,
    dependencies=[Depends(requires_permission(_W))],
)
async def vote_helpful(
    review_id: Annotated[UUID, PathParam(...)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> HelpfulVoteOut:
    async with uow.transactional() as session:
        svc = ReviewService(session)
        new_count, voted = await svc.vote_helpful(
            review_id=review_id,
            customer_id=principal.user_id,
            principal=principal,
        )
    return HelpfulVoteOut(
        review_id=review_id,
        helpful_count=new_count,
        voted=voted,
    )


def _ext_from_filename(filename: str) -> str:
    dot = filename.rfind(".")
    return filename[dot:].lower() if dot >= 0 else ""


@router.post(
    "/reviews/{review_id}/media",
    response_model=ReviewMediaOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(requires_permission(_W))],
)
async def upload_review_photo(
    review_id: Annotated[UUID, PathParam(...)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    file: Annotated[UploadFile, File(...)],
) -> ReviewMediaOut:
    """Attach a photo to a review the caller owns. Phase-2 only — JPEG /
    PNG / WebP, ≤ 5 MB, max 4 photos per review.

    The photo is stored under ``{storage_root}/review_media/{review_id}/``.
    Visibility is gated by the parent review's status; the public list
    endpoint only surfaces media for ``approved`` reviews.
    """
    ext = _ext_from_filename(file.filename or "")
    if ext not in ALLOWED_IMAGE_EXTS:
        raise ReviewMediaUnsupportedTypeError(
            f"Extension {ext!r} not allowed; expected {list(ALLOWED_IMAGE_EXTS)}.",
            details={"allowed_exts": list(ALLOWED_IMAGE_EXTS)},
        )
    mime = (file.content_type or "").lower()
    # Browsers occasionally lie on .heic/.webp — accept any image/* but
    # reject obviously wrong types like text/csv.
    if mime not in ALLOWED_IMAGE_MIMES and not mime.startswith("image/"):
        raise ReviewMediaUnsupportedTypeError(
            f"Content-Type {mime!r} is not an accepted image format.",
            details={"allowed_mimes": list(ALLOWED_IMAGE_MIMES)},
        )

    # Stream to disk first; if the size cap is exceeded we'll surface a
    # 422 and never write the row. The service's per-review cap is
    # checked AFTER bytes land — fine because the next caller's check
    # will see the just-completed insert and reject the over-cap upload.
    try:
        url, object_key, size = store_image(
            review_id=review_id,
            fobj=file.file,
            ext=ext,
            max_bytes=MAX_IMAGE_BYTES,
        )
    except OverflowError as e:
        raise ReviewMediaTooLargeError(
            f"Upload exceeded {MAX_IMAGE_BYTES} bytes.",
            details={"max_bytes": MAX_IMAGE_BYTES},
        ) from e
    finally:
        await file.close()

    async with uow.transactional() as session:
        svc = ReviewService(session)
        media = await svc.attach_photo(
            review_id=review_id,
            customer_id=principal.user_id,
            url=url,
            object_key=object_key,
            content_type=mime or "image/jpeg",
            file_size_bytes=size,
            principal=principal,
        )
    return ReviewMediaOut.model_validate(media)
