"""Pluggable dashboard router — RBAC-filtered pages + cached module metrics.

Two surfaces:

* ``GET /admin/dashboard/layout/pages`` — flat list of dashboard pages
  the caller can see, derived from the declarative module registry
  at ``app/core/registry/admin_modules.py`` filtered by their permissions.
  This is a *page-shaped* view of `/admin/config/me`'s ``modules`` slice.

* ``GET /admin/dashboard/metrics/{module_key}`` — module-level metrics
  aggregator. Sums every widget owned by ``module_key`` from the
  dashboard widget registry, calls each resolver, and returns the
  rolled-up payload. Cached in Redis for 5 minutes per (module, caller-
  permission-set) tuple. PII fields are masked unless the caller holds
  the ``iam.pii.read`` permission.

Adaptation notes:
    * Hypershop is fully async — uses ``AsyncSession`` + ``get_session``
      not a sync ``Session``. RBAC is enforced via ``get_current_principal``
      (the standard JWT-decoding dep) not a hand-rolled ``CurrentUser``.
    * Pages come from the declarative ``admin_modules.py`` registry,
      not a ``dashboard_pages`` table — adding a page = one entry in
      that Python file. (See ``scripts/MODULE_GOVERNANCE.md``.)
    * Caching uses the existing Redis client via ``get_redis()``. Cache
      key namespace is per-permission-set so callers with different
      visibility don't poison each other's view.
"""
from __future__ import annotations

import json
import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.core.db.session import get_session
from app.core.registry.admin_modules import ADMIN_MODULES
from app.core.registry.dashboard_widgets import DASHBOARD_WIDGETS
from app.core.security.deps import get_current_principal
from app.core.security.principal import Principal

_log = logging.getLogger("hypershop.dashboard.pluggable")

router = APIRouter(prefix="/admin/dashboard", tags=["dashboard:pluggable"])


# ─── Pydantic schemas ────────────────────────────────────────────────


class PageSchema(BaseModel):
    """Dashboard page entry — projection of an AdminModule for the
    pluggable layout endpoint."""

    code: str = Field(..., description="Stable module code (used in URLs).")
    label_en: str
    label_bn: str
    group: str
    href: str
    required_permission: str = Field(
        ..., description="Permission the caller must hold to see this page."
    )
    icon: str | None = None
    order: int = 0


class ModuleMetricsSchema(BaseModel):
    module_key: str
    widget_count: int
    cached: bool
    metrics: dict[str, Any]


# ─── PII masking ──────────────────────────────────────────────────────

# Field-name patterns that look like PII. Conservative — masks anything
# that *could* be PII; the unmask gate is held by ``iam.pii.read``.
_PII_FIELD_PATTERNS: tuple[str, ...] = (
    "email", "phone", "msisdn", "address", "full_name", "nid",
    "passport", "tin", "bkash_number", "card_last4",
)


def _looks_like_pii(field_name: str) -> bool:
    lower = field_name.lower()
    return any(p in lower for p in _PII_FIELD_PATTERNS)


def mask_pii(data: Any, caller_perms: frozenset[str]) -> Any:
    """Walk a JSON-shaped value and replace likely-PII fields with the
    string ``"***"``. No-op if the caller holds ``iam.pii.read`` or the
    ``*`` wildcard.

    Defensive — never raises on unexpected shapes; passes through
    primitives untouched.
    """
    if "*" in caller_perms or "iam.pii.read" in caller_perms:
        return data
    if isinstance(data, dict):
        return {
            k: ("***" if _looks_like_pii(k) and v is not None else mask_pii(v, caller_perms))
            for k, v in data.items()
        }
    if isinstance(data, list):
        return [mask_pii(item, caller_perms) for item in data]
    return data


# ─── Cache (Redis-backed with in-memory fallback) ─────────────────────


async def _get_cached(cache_key: str) -> Any | None:
    try:
        from app.core.cache import get_redis  # local import — optional
        r = get_redis()
        raw = await r.get(cache_key)
        if raw is None:
            return None
        return json.loads(raw)
    except Exception as e:  # noqa: BLE001
        _log.warning("dashboard_cache_get_failed key=%s error=%s", cache_key, e)
        return None


async def _set_cached(cache_key: str, value: Any, ttl: int = 300) -> None:
    try:
        from app.core.cache import get_redis
        r = get_redis()
        await r.set(cache_key, json.dumps(value, default=str), ex=ttl)
    except Exception as e:  # noqa: BLE001
        _log.warning("dashboard_cache_set_failed key=%s error=%s", cache_key, e)


# ─── Routes ──────────────────────────────────────────────────────────


@router.get(
    "/layout/pages",
    response_model=list[PageSchema],
    summary="RBAC-filtered list of accessible dashboard pages",
    description=(
        "Returns the subset of admin pages the caller can see, derived "
        "from the declarative module registry filtered by permission. "
        "Adding a new page = one entry in `admin_modules.py` (no FE deploy)."
    ),
)
async def get_authorized_dashboard_layout(
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> list[PageSchema]:
    perms = principal.permissions
    has_star = "*" in perms
    pages: list[PageSchema] = []
    for m in ADMIN_MODULES:
        if not (has_star or m.required_perm in perms):
            continue
        pages.append(PageSchema(
            code=m.code,
            label_en=m.label_en,
            label_bn=m.label_bn,
            group=m.group,
            href=m.href,
            required_permission=m.required_perm,
            icon=getattr(m, "icon", None),
            order=getattr(m, "order", 0),
        ))
    pages.sort(key=lambda p: (p.group, p.order, p.code))
    return pages


@router.get(
    "/metrics/{module_key}",
    response_model=ModuleMetricsSchema,
    summary="Cached aggregate metrics for one module",
    description=(
        "Rolls up every widget owned by `module_key` from the dashboard "
        "widget registry. Resolved data is cached for 5 minutes per "
        "(module, permission-set) tuple. PII fields are masked unless "
        "the caller holds `iam.pii.read`."
    ),
)
async def get_pluggable_module_metrics(
    module_key: str,
    session: Annotated[Any, Depends(get_session)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> ModuleMetricsSchema:
    # Cache scope includes a hash of the caller's permissions so two
    # callers with different visibility don't share masked vs unmasked
    # entries.
    perm_fingerprint = hash(tuple(sorted(principal.permissions))) & 0xFFFFFFFF
    cache_key = f"hypershop:dashboard:metrics:{module_key}:{perm_fingerprint:08x}"

    cached = await _get_cached(cache_key)
    if cached is not None:
        return ModuleMetricsSchema(
            module_key=module_key,
            widget_count=cached.get("widget_count", 0),
            cached=True,
            metrics=mask_pii(cached.get("metrics", {}), principal.permissions),
        )

    # Find widgets owned by this module + caller can see.
    has_star = "*" in principal.permissions
    widgets = [
        w for w in DASHBOARD_WIDGETS
        if w.module == module_key
        and (has_star or w.required_perm in principal.permissions)
    ]
    if not widgets:
        # Reject unknown modules (404), permission-blocked modules (403).
        any_for_module = any(w.module == module_key for w in DASHBOARD_WIDGETS)
        if not any_for_module:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Unknown module: {module_key}",
            )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No accessible widgets for your permission set in this module.",
        )

    # Resolve each widget independently — a single failing resolver
    # shouldn't poison the bundle.
    metrics: dict[str, Any] = {}
    for w in widgets:
        try:
            metrics[w.key] = await w.resolver(session, principal)
        except Exception as e:  # noqa: BLE001
            _log.warning(
                "module_metrics_resolver_failed module=%s widget=%s error=%s",
                module_key, w.key, e,
            )
            metrics[w.key] = {"error": str(e)}

    payload = {"widget_count": len(widgets), "metrics": metrics}
    await _set_cached(cache_key, payload, ttl=300)

    return ModuleMetricsSchema(
        module_key=module_key,
        widget_count=len(widgets),
        cached=False,
        metrics=mask_pii(metrics, principal.permissions),
    )


__all__ = ["router"]
