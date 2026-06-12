"""Dashboard widgets endpoint.

  GET /api/v1/admin/dashboard/widgets

Returns the caller's visible widgets, each with type + metadata + the
resolved data payload. FE renders one of 8 widget types per entry
(KPI_CARD, LINE_CHART, BAR_CHART, PIE_CHART, TABLE, ALERT_LIST,
QUICK_ACTION, STATUS_BADGE).

Per-widget data resolvers run in a single shared transaction. If one
resolver throws the widget gets ``error=true`` in its payload but the
rest still return — one bad query doesn't blank the dashboard.
"""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.core.db.uow import UnitOfWork, get_uow
from app.core.logging import get_logger
from app.core.registry.dashboard_widgets import (
    DASHBOARD_WIDGETS,
    WIDGET_GROUP_ORDER,
    visible_widgets_for,
)
from app.core.security.deps import get_current_principal
from app.core.security.principal import Principal


_log = get_logger("hypershop.admin_config.dashboard_widgets")


class WidgetOut(BaseModel):
    key: str
    type: str
    title_en: str
    title_bn: str
    group: str
    span: int
    order: int
    data: dict[str, Any]
    error: bool = False
    error_message: str | None = None


class WidgetListOut(BaseModel):
    widgets: list[WidgetOut]
    groups: list[str]
    total: int


router = APIRouter(prefix="/admin/dashboard", tags=["admin-dashboard"])


@router.get(
    "/widgets",
    response_model=WidgetListOut,
    summary="Caller-visible dashboard widgets with resolved data.",
    description=(
        "One fetch returns every widget the caller has permission to "
        "see, each pre-populated with its data payload. Resolvers run "
        "in a single shared transaction. A resolver crash returns the "
        "widget with `error=true` rather than failing the whole list "
        "— one bad query doesn't blank the dashboard."
    ),
)
async def list_widgets(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> WidgetListOut:
    from sqlalchemy import select as _select
    from app.modules.admin_config.layouts import DashboardLayout

    perms = list(principal.permissions)
    visible = visible_widgets_for(perms)

    # Apply per-user layout overrides (if any). Hidden widgets are dropped;
    # custom `order` takes precedence over the registry's declared order.
    # Use a separate transactional scope from the per-widget loop below so
    # a widget resolver crashing doesn't poison the layout read.
    async with uow.transactional() as layout_session:
        row = (
            await layout_session.execute(
                _select(DashboardLayout).where(
                    DashboardLayout.user_id == principal.user_id,
                )
            )
        ).scalar_one_or_none()
        overrides: dict[str, dict] = (row.layout if row else {}) or {}

    # Drop widgets the user hid.
    visible = [w for w in visible if not (overrides.get(w.key, {}).get("hidden") is True)]

    # Sort: group rank → effective order (override-first) → catalog position.
    rank = {g: i for i, g in enumerate(WIDGET_GROUP_ORDER)}
    catalog_order = {w.key: i for i, w in enumerate(DASHBOARD_WIDGETS)}
    def _effective_order(w):
        ov = overrides.get(w.key, {})
        return ov.get("order") if isinstance(ov.get("order"), int) else w.order
    visible.sort(key=lambda w: (rank.get(w.group, 99), _effective_order(w), catalog_order[w.key]))

    out: list[WidgetOut] = []
    # Per-widget transactional session — a failing query poisons its own
    # transaction without bleeding into the next widget. Slightly more
    # connection overhead than a single shared txn but the isolation is
    # required (a typo-column resolver was cascading failure to all 7
    # other widgets in the same txn).
    for w in visible:
        try:
            async with uow.transactional() as session:
                data = await w.resolver(session, principal)
            out.append(WidgetOut(
                key=w.key, type=w.type.value,
                title_en=w.title_en, title_bn=w.title_bn,
                group=w.group, span=w.span, order=w.order,
                data=data,
            ))
        except Exception as e:  # noqa: BLE001
            _log.warning(
                "dashboard_widget_resolve_failed",
                key=w.key, error=str(e),
            )
            out.append(WidgetOut(
                key=w.key, type=w.type.value,
                title_en=w.title_en, title_bn=w.title_bn,
                group=w.group, span=w.span, order=w.order,
                data={}, error=True, error_message=str(e)[:240],
            ))

    return WidgetListOut(
        widgets=out,
        groups=[g for g in WIDGET_GROUP_ORDER if any(w.group == g for w in visible)],
        total=len(out),
    )


# ─── catalog mode + per-widget data endpoint ───────────────────────────
# Catalog returns metadata only — no resolved data inline. FE polls
# `data_api` for each widget on its own `refresh_interval`. This is the
# preferred shape for new FE shells; the bundle endpoint above stays
# for backwards compat.

_UNIVERSAL_DATA_API = "/api/v1/admin/dashboard/widget"


class WidgetCatalogItemOut(BaseModel):
    id: str
    type: str
    title: str
    title_bn: str
    module: str
    group: str
    span: int
    order: int
    permission: str
    data_api: str
    refresh_interval: int


class WidgetCatalogOut(BaseModel):
    widgets: list[WidgetCatalogItemOut]
    groups: list[str]
    total: int


@router.get(
    "/widgets/catalog",
    response_model=WidgetCatalogOut,
    summary="Caller-visible widget catalog (metadata only — no resolved data).",
    description=(
        "Metadata-only mode of /admin/dashboard/widgets. Each entry "
        "carries the URL the FE must call to fetch its data plus the "
        "refresh interval. FE polls each widget on its own schedule "
        "instead of one bundle call. Preferred for new FE shells; the "
        "data-bundled endpoint at /admin/dashboard/widgets stays for "
        "backwards compat."
    ),
)
async def list_widgets_catalog(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> WidgetCatalogOut:
    from sqlalchemy import select as _select
    from app.modules.admin_config.layouts import DashboardLayout
    from app.modules.admin_config.service import ModuleConfigService

    perms = list(principal.permissions)
    visible = visible_widgets_for(perms)

    async with uow.transactional() as session:
        row = (
            await session.execute(
                _select(DashboardLayout).where(
                    DashboardLayout.user_id == principal.user_id,
                )
            )
        ).scalar_one_or_none()
        overrides: dict[str, dict] = (row.layout if row else {}) or {}
        svc = ModuleConfigService(session)
        global_refresh = await svc.get_int(
            "dashboard", "refresh_interval_seconds", default=30,
        )

    visible = [w for w in visible if not (overrides.get(w.key, {}).get("hidden") is True)]
    rank = {g: i for i, g in enumerate(WIDGET_GROUP_ORDER)}
    catalog_order = {w.key: i for i, w in enumerate(DASHBOARD_WIDGETS)}
    def _effective_order(w):
        ov = overrides.get(w.key, {})
        return ov.get("order") if isinstance(ov.get("order"), int) else w.order
    visible.sort(key=lambda w: (rank.get(w.group, 99), _effective_order(w), catalog_order[w.key]))

    items: list[WidgetCatalogItemOut] = []
    for w in visible:
        items.append(WidgetCatalogItemOut(
            id=w.key,
            type=w.type.value,
            title=w.title_en,
            title_bn=w.title_bn,
            module=w.module,
            group=w.group,
            span=w.span,
            order=_effective_order(w) or 0,
            permission=w.required_perm,
            data_api=w.data_api or f"{_UNIVERSAL_DATA_API}/{w.key}/data",
            refresh_interval=(
                w.refresh_interval
                if w.refresh_interval is not None
                else global_refresh
            ),
        ))
    return WidgetCatalogOut(
        widgets=items,
        groups=[g for g in WIDGET_GROUP_ORDER if any(w.group == g for w in visible)],
        total=len(items),
    )


@router.get(
    "/widget/{widget_id}/data",
    summary="Resolve a single widget's data payload.",
    description=(
        "FE shell polls this on each widget's `refresh_interval`. Returns "
        "the resolver's typed payload (matches the data shape for the "
        "widget's `type`). 404 if the widget doesn't exist, 403 if the "
        "caller lacks the widget's `required_perm`. Errors during "
        "resolution surface as `{error: true, error_message}` rather "
        "than HTTP 500 — the FE then renders an error placeholder."
    ),
)
async def get_widget_data(
    widget_id: str,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> dict[str, object]:
    widget = next((w for w in DASHBOARD_WIDGETS if w.key == widget_id), None)
    if widget is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "widget_not_found", "widget_id": widget_id},
        )
    if not (
        "*" in principal.permissions
        or widget.required_perm in principal.permissions
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "forbidden",
                "message": f"Missing required permission(s): {widget.required_perm}",
            },
        )
    try:
        async with uow.transactional() as session:
            data = await widget.resolver(session, principal)
        return {"id": widget_id, "type": widget.type.value, "data": data}
    except Exception as e:  # noqa: BLE001
        _log.warning(
            "dashboard_widget_resolve_failed",
            key=widget_id, error=str(e),
        )
        return {
            "id": widget_id,
            "type": widget.type.value,
            "data": {},
            "error": True,
            "error_message": str(e)[:240],
        }
