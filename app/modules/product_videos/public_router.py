"""Public, customer-facing product-video listing.

This router exposes the single endpoint customer-web hits to render
the player rail on a product detail page::

    GET /api/v1/products/{product_id}/videos
        anonymous OK; CDN-cacheable
        returns: { "items": [PublicProductVideo, ...] }

The endpoint is deliberately separate from the legacy
``GET /api/v1/catalog/products/{id}/videos`` route (still wired for
backwards compatibility). Both paths share the same service method,
so the rule set — only ``approved`` rows, never ``raw_object_key``,
sorted newest-uploaded first — is enforced exactly once.

What the response NEVER contains (defence-in-depth):

* ``raw_object_key`` — only the ``AdminProductVideo`` schema carries
  it; the public schema doesn't even have the field.
* Direct R2 private URLs — :func:`storage.get_public_url` rejects
  any object key starting with ``r2_private_prefix``.
* Disabled / rejected / failed rows — :meth:`ProductVideoRepository.
  list_for_product_public` filters strictly on ``status='approved'``.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path as PathParam

from app.core.db.uow import UnitOfWork, get_uow
from app.modules.product_videos.schemas import PublicProductVideoList
from app.modules.product_videos.service import ProductVideoService

router = APIRouter(prefix="/products", tags=["product-videos-public"])


@router.get(
    "/{product_id}/videos",
    response_model=PublicProductVideoList,
)
async def list_product_videos_public(
    product_id: Annotated[UUID, PathParam(...)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> PublicProductVideoList:
    """Return up to 5 approved videos for a product, newest first.

    The cap of 5 is defensive — the per-product approval cap
    (``product_video_max_approved_per_product``) is 3 in the
    default config, so the list will normally be ≤ 3 items. We
    keep the query limit slightly above the cap so a config bump
    doesn't silently truncate.
    """
    async with uow.transactional() as session:
        svc = ProductVideoService(session)
        items = await svc.list_public(product_id=product_id, limit=5)
    return PublicProductVideoList(items=items)
