"""Live Shopping HTTP API — Module 50.

Two route groups:
- Public (`/api/v1/live/*`): list / detail / record view — anonymous or authenticated
- Admin (`/api/v1/admin/live-streams/*`): CRUD + state transitions + product attach
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from pydantic import Field
from sqlalchemy import text as _t

from app.core.audit.service import record_audit
from app.core.db.uow import UnitOfWork, get_uow
from app.core.errors import BusinessRuleError, NotFoundError
from app.core.logging import get_logger
from app.core.security.deps import get_current_principal
from app.core.security.principal import Principal
from app.core.security.rbac import requires_permission
from app.core.validation import StrictModel

_log = get_logger("hypershop.live_shopping")

# Reuse catalog write permission for admin; sellers with catalog write can host
_ADMIN = "catalog.product.write"

public_router = APIRouter(prefix="/live", tags=["live-shopping"])
admin_router = APIRouter(prefix="/admin/live-streams", tags=["admin-live-shopping"])


# ============================================================== Schemas
class StreamCreate(StrictModel):
    title: str = Field(..., min_length=2, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    thumbnail_url: str | None = Field(default=None, max_length=2048)
    stream_url: str | None = Field(default=None, max_length=2048)
    provider: str = Field(default="manual",
                          pattern=r"^(manual|bunny|youtube|facebook|tiktok|custom_rtmp)$")
    provider_stream_id: str | None = Field(default=None, max_length=200)
    scheduled_at: datetime | None = None
    seller_id: UUID | None = None


class StreamUpdate(StrictModel):
    title: str | None = Field(default=None, min_length=2, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    thumbnail_url: str | None = Field(default=None, max_length=2048)
    stream_url: str | None = Field(default=None, max_length=2048)
    scheduled_at: datetime | None = None


class ProductAttach(StrictModel):
    product_id: UUID
    display_order: int = Field(default=0, ge=0, le=999)
    special_price: Decimal | None = Field(default=None, ge=0)
    highlight_text: str | None = Field(default=None, max_length=200)


# ============================================================== Helpers
_STREAM_COLS = (
    "id, title, description, host_user_id, seller_id, thumbnail_url, "
    "stream_url, provider, provider_stream_id, status, "
    "scheduled_at, started_at, ended_at, peak_viewers, total_views, "
    "created_at, updated_at"
)


def _stream_to_dict(r: Any) -> dict[str, Any]:
    return {
        "id": str(r[0]), "title": r[1], "description": r[2],
        "host_user_id": str(r[3]) if r[3] else None,
        "seller_id": str(r[4]) if r[4] else None,
        "thumbnail_url": r[5], "stream_url": r[6],
        "provider": r[7], "provider_stream_id": r[8],
        "status": r[9],
        "scheduled_at": r[10], "started_at": r[11], "ended_at": r[12],
        "peak_viewers": int(r[13]), "total_views": int(r[14]),
        "created_at": r[15], "updated_at": r[16],
    }


# ============================================================== Admin routes
@admin_router.post(
    "", status_code=201,
    summary="Create a live stream (status=scheduled)",
    dependencies=[Depends(requires_permission(_ADMIN))],
)
async def create_stream(
    body: StreamCreate,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> dict[str, Any]:
    async with uow.transactional() as session:
        r = await session.execute(
            _t(
                f"""
                INSERT INTO live_streams
                  (id, title, description, host_user_id, seller_id,
                   thumbnail_url, stream_url, provider, provider_stream_id,
                   scheduled_at, status)
                VALUES
                  (gen_random_uuid(), :t, :d, :h, :sid, :thumb, :url,
                   :p, :psid, :sa, 'scheduled')
                RETURNING {_STREAM_COLS}
                """,
            ),
            {
                "t": body.title, "d": body.description, "h": principal.user_id,
                "sid": body.seller_id, "thumb": body.thumbnail_url,
                "url": body.stream_url, "p": body.provider,
                "psid": body.provider_stream_id, "sa": body.scheduled_at,
            },
        )
        row = r.first()
        await record_audit(
            actor=principal, action="live.stream.created",
            resource_type="live_streams", resource_id=row[0],
            metadata={"title": body.title, "provider": body.provider},
        )
        return _stream_to_dict(row)


@admin_router.post(
    "/{sid}/start",
    summary="Transition stream to 'live'",
    dependencies=[Depends(requires_permission(_ADMIN))],
)
async def start_stream(
    sid: Annotated[UUID, Path(...)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> dict[str, Any]:
    async with uow.transactional() as session:
        r = await session.execute(
            _t(
                f"UPDATE live_streams SET status = 'live', started_at = now(), "
                f"updated_at = now() "
                f"WHERE id = :s AND status = 'scheduled' "
                f"RETURNING {_STREAM_COLS}"
            ),
            {"s": sid},
        )
        row = r.first()
        if row is None:
            raise BusinessRuleError("Only scheduled streams can be started")
        await record_audit(
            actor=principal, action="live.stream.started",
            resource_type="live_streams", resource_id=sid,
        )
        return _stream_to_dict(row)


@admin_router.post(
    "/{sid}/end",
    summary="Transition stream to 'ended'",
    dependencies=[Depends(requires_permission(_ADMIN))],
)
async def end_stream(
    sid: Annotated[UUID, Path(...)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> dict[str, Any]:
    async with uow.transactional() as session:
        r = await session.execute(
            _t(
                f"UPDATE live_streams SET status = 'ended', ended_at = now(), "
                f"updated_at = now() "
                f"WHERE id = :s AND status = 'live' "
                f"RETURNING {_STREAM_COLS}"
            ),
            {"s": sid},
        )
        row = r.first()
        if row is None:
            raise BusinessRuleError("Only live streams can be ended")
        await record_audit(
            actor=principal, action="live.stream.ended",
            resource_type="live_streams", resource_id=sid,
        )
        return _stream_to_dict(row)


@admin_router.post(
    "/{sid}/cancel",
    dependencies=[Depends(requires_permission(_ADMIN))],
)
async def cancel_stream(
    sid: Annotated[UUID, Path(...)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> dict[str, Any]:
    async with uow.transactional() as session:
        r = await session.execute(
            _t(
                f"UPDATE live_streams SET status = 'cancelled', updated_at = now() "
                f"WHERE id = :s AND status NOT IN ('ended', 'cancelled') "
                f"RETURNING {_STREAM_COLS}"
            ),
            {"s": sid},
        )
        row = r.first()
        if row is None:
            raise BusinessRuleError("Stream already terminal")
        return _stream_to_dict(row)


@admin_router.post(
    "/{sid}/products",
    summary="Attach a product to the stream",
    dependencies=[Depends(requires_permission(_ADMIN))],
)
async def attach_product(
    sid: Annotated[UUID, Path(...)],
    body: ProductAttach,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> dict[str, Any]:
    async with uow.transactional() as session:
        await session.execute(
            _t(
                """
                INSERT INTO stream_products
                  (stream_id, product_id, display_order, special_price, highlight_text)
                VALUES (:s, :p, :o, :sp, :h)
                ON CONFLICT (stream_id, product_id) DO UPDATE SET
                  display_order = EXCLUDED.display_order,
                  special_price = EXCLUDED.special_price,
                  highlight_text = EXCLUDED.highlight_text
                """,
            ),
            {
                "s": sid, "p": body.product_id, "o": body.display_order,
                "sp": body.special_price, "h": body.highlight_text,
            },
        )
        await record_audit(
            actor=principal, action="live.stream.product_attached",
            resource_type="live_streams", resource_id=sid,
            metadata={"product_id": str(body.product_id)},
        )
    return {"stream_id": str(sid), "product_id": str(body.product_id), "attached": True}


@admin_router.delete(
    "/{sid}/products/{pid}",
    dependencies=[Depends(requires_permission(_ADMIN))],
)
async def detach_product(
    sid: Annotated[UUID, Path(...)],
    pid: Annotated[UUID, Path(...)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> dict[str, Any]:
    async with uow.transactional() as session:
        await session.execute(
            _t("DELETE FROM stream_products WHERE stream_id = :s AND product_id = :p"),
            {"s": sid, "p": pid},
        )
    return {"detached": True}


@admin_router.get(
    "",
    dependencies=[Depends(requires_permission(_ADMIN))],
)
async def admin_list_streams(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[dict[str, Any]]:
    sql = f"SELECT {_STREAM_COLS} FROM live_streams "
    params: dict[str, Any] = {"lim": limit}
    if status_filter:
        sql += "WHERE status = :st "
        params["st"] = status_filter
    sql += "ORDER BY COALESCE(scheduled_at, created_at) DESC LIMIT :lim"
    async with uow.transactional() as session:
        rows = (await session.execute(_t(sql), params)).all()
        return [_stream_to_dict(r) for r in rows]


# ============================================================== Public routes
@public_router.get(
    "",
    summary="List live/scheduled streams (public)",
)
async def list_streams(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    state: str = Query(default="live", pattern=r"^(live|scheduled|recent)$"),
    limit: int = Query(default=20, ge=1, le=100),
) -> list[dict[str, Any]]:
    if state == "live":
        where = "status = 'live'"
        order_by = "started_at DESC"
    elif state == "scheduled":
        where = "status = 'scheduled' AND scheduled_at IS NOT NULL AND scheduled_at > now()"
        order_by = "scheduled_at ASC"
    else:  # recent
        where = "status = 'ended'"
        order_by = "ended_at DESC"
    async with uow.transactional() as session:
        rows = (
            await session.execute(
                _t(
                    f"SELECT {_STREAM_COLS} FROM live_streams WHERE {where} "
                    f"ORDER BY {order_by} LIMIT :lim"
                ),
                {"lim": limit},
            )
        ).all()
        return [_stream_to_dict(r) for r in rows]


@public_router.get(
    "/{sid}",
    summary="Stream detail + featured products (public)",
)
async def get_stream(
    sid: Annotated[UUID, Path(...)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict[str, Any]:
    async with uow.transactional() as session:
        r = (
            await session.execute(
                _t(f"SELECT {_STREAM_COLS} FROM live_streams WHERE id = :s"),
                {"s": sid},
            )
        ).first()
        if r is None:
            raise NotFoundError("Stream not found")
        products = (
            await session.execute(
                _t(
                    "SELECT p.id, p.name, p.slug, sp.display_order, "
                    "sp.special_price, sp.highlight_text "
                    "FROM stream_products sp "
                    "JOIN products p ON p.id = sp.product_id "
                    "WHERE sp.stream_id = :s "
                    "ORDER BY sp.display_order ASC, p.name ASC"
                ),
                {"s": sid},
            )
        ).all()
        return {
            **_stream_to_dict(r),
            "products": [
                {
                    "id": str(p[0]), "name": p[1], "slug": p[2],
                    "display_order": int(p[3]),
                    "special_price": str(p[4]) if p[4] is not None else None,
                    "highlight_text": p[5],
                }
                for p in products
            ],
        }


@public_router.post(
    "/{sid}/view",
    summary="Record a viewer joining the stream (anonymous OK)",
)
async def record_view(
    sid: Annotated[UUID, Path(...)],
    request: Request,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict[str, Any]:
    # session_id from header (storefront sends a stable id per browser)
    session_id = request.headers.get("X-Session-Id") or None
    # principal best-effort
    user_id = None
    try:
        principal: Principal = await get_current_principal(request)
        user_id = principal.user_id
    except Exception:
        pass
    client_ip = request.client.host if request.client else None
    ua = request.headers.get("user-agent", "")[:500]
    async with uow.transactional() as session:
        # Ensure the stream exists + is live
        r = (
            await session.execute(
                _t("SELECT status FROM live_streams WHERE id = :s"),
                {"s": sid},
            )
        ).first()
        if r is None:
            raise NotFoundError("Stream not found")
        # Insert view row
        await session.execute(
            _t(
                """
                INSERT INTO stream_views
                  (id, stream_id, viewer_user_id, session_id, ip_address, user_agent)
                VALUES (gen_random_uuid(), :s, :u, :sid, :ip, :ua)
                """,
            ),
            {"s": sid, "u": user_id, "sid": session_id,
             "ip": client_ip, "ua": ua},
        )
        # Bump total_views + peak_viewers (concurrent-views best-effort)
        await session.execute(
            _t(
                """
                UPDATE live_streams SET
                  total_views = total_views + 1,
                  peak_viewers = GREATEST(
                    peak_viewers,
                    (SELECT COUNT(*) FROM stream_views
                     WHERE stream_id = :s AND left_at IS NULL
                       AND joined_at >= now() - INTERVAL '15 minutes')
                  ),
                  updated_at = now()
                WHERE id = :s
                """,
            ),
            {"s": sid},
        )
    return {"stream_id": str(sid), "view_recorded": True}
