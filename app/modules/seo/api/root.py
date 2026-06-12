"""Root-mounted SEO endpoints (no /api/v1 prefix).

These three live at the public domain root because crawlers + browsers
hit them at fixed paths:

  GET /robots.txt    — text/plain
  GET /sitemap.xml   — application/xml
  GET /r/{path}      — issues a 301/302 redirect from the URL map

All three are public (unauth). For the redirect endpoint, ``{path}``
is the part AFTER the leading "/r/" in the request URL — frontends
should reroute their old URLs through this prefix only if they
actually use this module's redirect map.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Response
from fastapi.responses import PlainTextResponse, RedirectResponse

from app.core.db.uow import UnitOfWork, get_uow
from app.modules.seo.errors import RedirectNotFoundError
from app.modules.seo.repository import UrlRedirectRepository
from app.modules.seo.service import SitemapService
from app.modules.seo.state import REDIRECT_STATUS_CODE

router = APIRouter(tags=["seo-root"])


@router.get(
    "/robots.txt",
    response_class=PlainTextResponse,
    summary="Crawler-facing robots.txt",
)
async def robots_txt(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> PlainTextResponse:
    async with uow.transactional() as session:
        body = await SitemapService(session).build_robots_txt()
    return PlainTextResponse(content=body, media_type="text/plain")


@router.get(
    "/sitemap.xml",
    summary="Sitemap index pointing at per-type child sitemaps",
)
async def sitemap_xml(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> Response:
    async with uow.transactional() as session:
        body = await SitemapService(session).build_sitemap_index()
    return Response(content=body, media_type="application/xml")


@router.get(
    "/sitemap-{kind}-{page}.xml",
    summary="Child sitemap slice for one section (products/categories/...)",
)
async def child_sitemap(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    kind: Annotated[
        str,
        Path(pattern="^(static|products|categories|brands|blog)$"),
    ],
    page: Annotated[int, Path(ge=0)],
) -> Response:
    async with uow.transactional() as session:
        body = await SitemapService(session).build_child_sitemap(kind, page)
    return Response(content=body, media_type="application/xml")


@router.get(
    "/{key}.txt",
    response_class=PlainTextResponse,
    summary="IndexNow key verification — returns the key when its path matches",
)
async def indexnow_key_file(
    key: Annotated[str, Path(min_length=8, max_length=128, pattern="^[a-zA-Z0-9]+$")],
) -> PlainTextResponse:
    """IndexNow verification — Bing/Yandex/Naver fetch this URL to
    confirm we control the host before honouring our submissions.

    Returns 404 when the requested path doesn't match the configured
    key (so we can't be used as an arbitrary text echo endpoint).
    """
    from fastapi import HTTPException
    from app.core.config import get_settings
    configured = (getattr(get_settings(), "seo_indexnow_key", "") or "").strip()
    if not configured or key != configured:
        raise HTTPException(status_code=404, detail="Not found")
    return PlainTextResponse(content=configured, media_type="text/plain")


@router.get(
    "/r/{path:path}",
    summary="301/302 redirect lookup using the URL map",
    response_class=RedirectResponse,
)
async def follow_redirect(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    path: Annotated[str, Path(min_length=1)],
) -> RedirectResponse:
    full_path = "/" + path.lstrip("/")
    async with uow.transactional() as session:
        repo = UrlRedirectRepository(session)
        row = await repo.get_by_from_path(full_path)
        if row is None:
            raise RedirectNotFoundError("No redirect for that path.")
        await repo.bump_hit(row.id)
    status_code = REDIRECT_STATUS_CODE.get(row.redirect_type, 301)
    return RedirectResponse(url=row.to_path, status_code=status_code)
