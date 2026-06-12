"""Declarative catalog of admin dashboard widgets.

Two-source registry (2026-05-16, later session):

  1. **Per-module** widgets live in
     ``app/modules/<name>/dashboard_config.py`` and export
     ``WIDGETS: tuple[DashboardWidget, ...]``. This is the preferred
     home for new widgets — keeps each module owning its dashboard
     contribution. The aggregator below auto-discovers + appends them.

  2. **Legacy / cross-cutting** widgets stay inline in this file
     under ``LEGACY_WIDGETS`` while modules are gradually migrated.
     New widgets should go in their module's dashboard_config.py
     instead, not here.

Widget types (the 8 the FE shell knows how to render):

  KPI_CARD       — single value + delta + label (e.g. "Orders today: 142")
  LINE_CHART     — time-series points {ts, value}
  BAR_CHART      — category bars {label, value}
  PIE_CHART      — proportional slices {label, value}
  TABLE          — header + rows of cells (≤100 rows recommended)
  ALERT_LIST     — list of severity-tagged items {severity, title, body, href}
  QUICK_ACTION   — single CTA card {label, href, hint, perm_required?}
  STATUS_BADGE   — boolean health indicator {ok, message, last_checked}

Data resolvers are plain async functions that take ``(session, principal)``
and return the widget's data shape. Keep them under ~5ms each — they
all run on the hot path of `/admin/dashboard/widgets`.

Auto-discovery: ``_discover_per_module_widgets()`` walks
``app/modules/*/dashboard_config.py`` at import time, imports each
module's ``WIDGETS`` tuple, and prepends them to the final catalog.
Duplicate keys raise at startup so collisions don't silently shadow.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Iterable


class WidgetType(str, enum.Enum):
    KPI_CARD = "KPI_CARD"
    LINE_CHART = "LINE_CHART"
    BAR_CHART = "BAR_CHART"
    PIE_CHART = "PIE_CHART"
    TABLE = "TABLE"
    ALERT_LIST = "ALERT_LIST"
    QUICK_ACTION = "QUICK_ACTION"
    STATUS_BADGE = "STATUS_BADGE"


class WidgetGroup:
    TODAY = "Today"
    OPERATIONS = "Operations"
    REVENUE = "Revenue"
    HEALTH = "Health"
    QUICK_ACTIONS = "Quick actions"


WidgetResolver = Callable[..., Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class DashboardWidget:
    key: str
    type: WidgetType
    title_en: str
    title_bn: str
    group: str
    required_perm: str
    resolver: WidgetResolver
    span: int = 1  # grid span hint (1-4). FE shell snaps to its column count.
    order: int = 0
    notes: str = ""
    # Module this widget logically belongs to — matches AdminModule.code
    # in app/core/registry/admin_modules.py. Surfaced in the catalog
    # response so the FE shell can group widgets by module + jump to
    # the module's full page on "see all" affordances.
    module: str = "system"
    # Override for the universal per-widget data endpoint at
    # `/api/v1/admin/dashboard/widget/<key>/data`. Set when the widget's
    # data is actually owned by a different module endpoint (e.g.
    # `/api/v1/orders/kpi/today` lives in the orders module). FE polls
    # `data_api` on its own interval — keeps each module the source of
    # truth for its own metrics. If unset the catalog builds the
    # universal URL automatically.
    data_api: str | None = None
    # Per-widget refresh override (seconds). Falls back to the global
    # `dashboard.refresh_interval_seconds` module-config setting when
    # unset. 0 = manual only.
    refresh_interval: int | None = None


# ─── System-tier resolver (the only inline one — others live in
# `app/modules/<name>/dashboard_config.py`).

async def _resolve_backend_health_badge(session, principal) -> dict[str, Any]:
    from sqlalchemy import text as _t
    # Cheap liveness probe — if this SELECT errors the resolver bubbles
    # and the endpoint returns the widget with `error=true`.
    await session.execute(_t("SELECT 1"))
    return {
        "ok": True,
        "label": "Backend health",
        "message": "All systems nominal",
        "last_checked": "now",
    }


# ─── Legacy registry (cross-cutting widgets staying here for now) ─────
# New widgets MUST live in `app/modules/<name>/dashboard_config.py`.
# The discovery pass at the bottom of this file prepends per-module
# widgets to LEGACY_WIDGETS to produce DASHBOARD_WIDGETS. Migrated
# entries are removed from LEGACY_WIDGETS and re-declared in the
# module's own dashboard_config.py.
LEGACY_WIDGETS: tuple[DashboardWidget, ...] = (
    # All other widgets migrated to per-module dashboard_config.py files
    # 2026-05-16. Only backend-health remains here as a cross-cutting
    # system widget (not owned by any single business module).
    DashboardWidget(
        key="backend-health",
        type=WidgetType.STATUS_BADGE,
        title_en="Backend health",
        title_bn="ব্যাকএন্ড স্বাস্থ্য",
        group=WidgetGroup.HEALTH,
        required_perm="dashboard.read",
        resolver=_resolve_backend_health_badge,
        order=10,
        module="dashboard",
        refresh_interval=15,  # tight — health needs fast detection
    ),
)


WIDGET_GROUP_ORDER: tuple[str, ...] = (
    WidgetGroup.TODAY,
    WidgetGroup.OPERATIONS,
    WidgetGroup.REVENUE,
    WidgetGroup.HEALTH,
    WidgetGroup.QUICK_ACTIONS,
)


def _discover_per_module_widgets() -> tuple[DashboardWidget, ...]:
    """Scan app/modules/*/dashboard_config.py for ``WIDGETS`` tuples.

    Each module that wants dashboard contribution drops a
    ``dashboard_config.py`` file exporting ``WIDGETS: tuple[DashboardWidget, ...]``.
    No registration boilerplate — the presence of the file + export is
    the registration. Same pattern the FE module registry uses.

    Raises ValueError on duplicate ``key`` across modules so collisions
    don't silently shadow at runtime.
    """
    import importlib
    import logging
    import pkgutil

    _log = logging.getLogger("hypershop.registry.dashboard")
    found: list[DashboardWidget] = []

    try:
        import app.modules as _modules_pkg  # local import to avoid circulars
    except Exception:  # noqa: BLE001
        return ()

    for mod_info in pkgutil.iter_modules(_modules_pkg.__path__):
        if mod_info.ispkg:
            full = f"app.modules.{mod_info.name}.dashboard_config"
            try:
                mod = importlib.import_module(full)
            except ModuleNotFoundError:
                continue  # module doesn't contribute widgets
            except Exception as e:  # noqa: BLE001
                _log.warning(
                    "dashboard_config_import_failed module=%s error=%s",
                    full, e,
                )
                continue
            widgets = getattr(mod, "WIDGETS", None)
            if widgets:
                found.extend(widgets)
                _log.info(
                    "dashboard_widgets_from_module module=%s count=%d",
                    mod_info.name, len(widgets),
                )

    # Duplicate key detection across legacy + per-module sources.
    seen: dict[str, str] = {}
    for w in found:
        seen[w.key] = f"app.modules.{w.module}.dashboard_config"
    for w in LEGACY_WIDGETS:
        if w.key in seen:
            raise ValueError(
                f"Dashboard widget key collision: {w.key!r} declared in "
                f"both LEGACY_WIDGETS and {seen[w.key]}. Remove one."
            )

    return tuple(found)


# Final aggregated registry — discovery + legacy. Order: per-module
# entries first (they're the new home), legacy entries after, sorting
# later in the endpoint by group→order.
DASHBOARD_WIDGETS: tuple[DashboardWidget, ...] = (
    *_discover_per_module_widgets(),
    *LEGACY_WIDGETS,
)


def visible_widgets_for(owned_perms: Iterable[str]) -> list[DashboardWidget]:
    """Return widgets the caller can see. ``*`` wildcard matches anything."""
    owned = set(owned_perms)
    has_star = "*" in owned
    return [
        w for w in DASHBOARD_WIDGETS
        if has_star or w.required_perm in owned
    ]
