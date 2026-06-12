"""DashboardLayout model + endpoints (per-user customization).

Each user gets one row in ``dashboard_layouts`` with a JSONB document
of per-widget overrides. The widget endpoint
(``GET /admin/dashboard/widgets``) reads this on render — hidden
widgets are dropped, custom orders take precedence over the registry's
declared order. Unknown widget keys in the layout are silently ignored
(forward-compat).
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, Depends
from pydantic import BaseModel, Field
from sqlalchemy import DateTime, ForeignKey, func, select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Mapped, mapped_column

from app.core.audit.service import record_audit
from app.core.db.base import Base
from app.core.db.uow import UnitOfWork, get_uow
from app.core.security.deps import get_current_principal
from app.core.security.principal import Principal


class DashboardLayout(Base):
    __tablename__ = "dashboard_layouts"

    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    layout: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
        onupdate=func.now(),
    )


class WidgetOverride(BaseModel):
    hidden: bool = False
    order: int | None = Field(default=None, ge=0, le=10_000)


class LayoutBody(BaseModel):
    """The full layout doc: `{widget_key: {hidden, order}}`. Send the
    complete map on PUT — partial updates aren't supported (saves a
    full-document diff diff cost; the doc is tiny).
    """
    overrides: dict[str, WidgetOverride] = Field(default_factory=dict)


router = APIRouter(prefix="/admin/dashboard/layout", tags=["admin-dashboard"])


@router.get(
    "/me",
    summary="Get current user's dashboard layout.",
    description=(
        "Returns the caller's per-widget overrides. Empty `{}` if the "
        "user hasn't customized — FE shell then renders the default."
    ),
)
async def get_my_layout(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> dict[str, object]:
    async with uow.transactional() as session:
        row = (
            await session.execute(
                select(DashboardLayout).where(
                    DashboardLayout.user_id == principal.user_id,
                )
            )
        ).scalar_one_or_none()
        return {
            "user_id": str(principal.user_id),
            "overrides": (row.layout if row else {}),
        }


@router.put(
    "/me",
    summary="Save the current user's dashboard layout (full document).",
    description=(
        "Upserts the full layout document. Empty `overrides={}` resets "
        "to default (same as DELETE). Unknown widget_keys are stored "
        "but silently ignored on render — they don't fail validation."
    ),
)
async def save_my_layout(
    payload: LayoutBody,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> dict[str, object]:
    layout_dict = {
        k: v.model_dump(exclude_none=True)
        for k, v in payload.overrides.items()
    }
    async with uow.transactional() as session:
        stmt = (
            pg_insert(DashboardLayout)
            .values(user_id=principal.user_id, layout=layout_dict)
            .on_conflict_do_update(
                index_elements=["user_id"],
                set_={"layout": layout_dict},
            )
        )
        await session.execute(stmt)
        await record_audit(
            actor=principal,
            action="dashboard.layout.save",
            resource_type="dashboard_layouts",
            resource_id=principal.user_id,
            metadata={"widget_count": len(layout_dict)},
        )
        return {
            "user_id": str(principal.user_id),
            "overrides": layout_dict,
        }


@router.delete(
    "/me",
    summary="Reset the current user's layout to default.",
)
async def delete_my_layout(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> dict[str, bool]:
    async with uow.transactional() as session:
        row = (
            await session.execute(
                select(DashboardLayout).where(
                    DashboardLayout.user_id == principal.user_id,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            return {"deleted": False}
        await session.delete(row)
        await record_audit(
            actor=principal,
            action="dashboard.layout.reset",
            resource_type="dashboard_layouts",
            resource_id=principal.user_id,
        )
        return {"deleted": True}
