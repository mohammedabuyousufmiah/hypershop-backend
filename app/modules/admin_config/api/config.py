"""Admin config endpoints.

  GET /api/v1/admin/config/me
    → per-caller filtered nav (drives FE shell)

  GET /api/v1/admin/module-registry
    → full unfiltered catalog (ops/audit inspection)

  GET /api/v1/admin/dashboard-config
    → unified shell bootstrap: principal + nav + effective config + groups +
      server-time + schema versions, in ONE fetch on admin app mount.

The FE admin shell calls /me once on mount, then renders nav from
``modules`` instead of a hardcoded list. Adding a new admin module =
one entry in `app/core/registry/admin_modules.py` and this endpoint
exposes it on the next /me fetch.

Security:
  - /me requires authentication (any logged-in user). The `permissions`
    array in the response is the caller's own perm set — used by the
    FE to gate sub-nav and conditional buttons. Not a secret leak:
    admin tokens already carry these in the JWT.
  - /module-registry is gated on `iam.role.read` — admin-tier inspection
    only. The full catalog leaks the org's privilege topology (every
    module's required_perm) and shouldn't be customer-facing.
"""
from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.core.db.uow import UnitOfWork, get_uow
from app.core.registry import (
    ADMIN_MODULES,
    AdminModuleGroup,
    visible_modules_for,
)
from app.core.security.deps import get_current_principal
from app.core.security.principal import Principal
from app.core.security.rbac import requires_permission
from app.modules.admin_config.models import ModuleFeatureFlag, ModuleSetting

router = APIRouter(prefix="/admin/config", tags=["admin-config"])


class PrincipalSummary(BaseModel):
    user_id: str
    roles: list[str]
    permissions: list[str]
    is_admin: bool


class ConfigModuleOut(BaseModel):
    code: str
    label_en: str
    label_bn: str
    group: str
    href: str
    version: int
    notes: str


class AdminConfigOut(BaseModel):
    principal: PrincipalSummary
    modules: list[ConfigModuleOut]
    groups: list[str]
    schema_version: Literal[1] = 1


# Group rendering order — keep in sync with AdminModuleGroup constants.
GROUP_ORDER: tuple[str, ...] = (
    AdminModuleGroup.OVERVIEW,
    AdminModuleGroup.MERCHANT_OPS,
    AdminModuleGroup.OPS_QUEUE,
    AdminModuleGroup.WAREHOUSE,
    AdminModuleGroup.FINANCE,
    AdminModuleGroup.GROWTH,
    AdminModuleGroup.CONTENT,
    AdminModuleGroup.SYSTEM,
)


@router.get(
    "/me",
    response_model=AdminConfigOut,
    summary="Per-caller admin nav + capabilities",
)
async def get_my_config(
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> AdminConfigOut:
    perms = list(principal.permissions)
    is_admin = "*" in set(perms)

    visible = visible_modules_for(perms)

    # Order modules by (group order, original catalog order). The catalog
    # already lists modules in display order within their group, so we
    # just stable-sort by group rank.
    rank = {g: i for i, g in enumerate(GROUP_ORDER)}
    catalog_order = {m.code: i for i, m in enumerate(ADMIN_MODULES)}
    visible.sort(key=lambda m: (rank.get(m.group, 99), catalog_order[m.code]))

    return AdminConfigOut(
        principal=PrincipalSummary(
            user_id=str(principal.user_id),
            roles=list(principal.roles),
            permissions=perms,
            is_admin=is_admin,
        ),
        modules=[
            ConfigModuleOut(
                code=m.code,
                label_en=m.label_en,
                label_bn=m.label_bn,
                group=m.group,
                href=m.href,
                version=m.version,
                notes=m.notes,
            )
            for m in visible
        ],
        # Groups present in the visible set, preserved in render order.
        groups=[g for g in GROUP_ORDER if any(m.group == g for m in visible)],
    )


class RegistryMenuItem(BaseModel):
    label: str
    path: str
    permission: str


class RegistryFlagOut(BaseModel):
    enabled: bool
    rollout_percent: int


class RegistryModuleOut(BaseModel):
    """Module entry shape consumed by the FE admin shell.

    Keys named per the FE's contract (``key`` not ``code``, ``name`` not
    ``label_en``, ``base_path`` not ``href``, ``permissions`` array not
    single ``required_perm``). Backend dataclass uses internal names;
    this DTO does the rename so the FE doesn't have to.

    Effective config (``settings`` + ``flags``) is merged in from the
    module_settings + module_feature_flags tables on every fetch.
    Secret settings are redacted as ``"[secret]"``.
    """
    key: str
    name: str
    name_bn: str
    enabled: bool
    base_path: str
    api_prefix: str | None
    icon: str | None
    order: int
    permissions: list[str]
    menu: list[RegistryMenuItem]
    group: str
    notes: str
    version: int
    settings: dict[str, object] = {}
    flags: dict[str, RegistryFlagOut] = {}


class ModuleRegistryOut(BaseModel):
    modules: list[RegistryModuleOut]
    groups: list[str]
    total: int
    schema_version: Literal[1] = 1


# Standalone router for the full-catalog endpoint. Mounted separately
# in app/main.py so the prefix is exactly /api/v1/admin/module-registry
# (not nested under /admin/config).
registry_router = APIRouter(prefix="/admin/module-registry", tags=["admin-config"])


@registry_router.get(
    "",
    response_model=ModuleRegistryOut,
    summary="Full admin module registry catalog (unfiltered).",
    description=(
        "Returns every entry in `app/core/registry/admin_modules.py` "
        "with its required_perm — useful for ops audits + privilege "
        "topology review. Per-caller filtered nav is at /admin/config/me. "
        "Gated on `iam.role.read` since the required_perm list reveals "
        "the org's RBAC structure."
    ),
    dependencies=[Depends(requires_permission("iam.role.read"))],
)
async def get_module_registry(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> ModuleRegistryOut:
    rank = {g: i for i, g in enumerate(GROUP_ORDER)}
    catalog_order = {m.code: i for i, m in enumerate(ADMIN_MODULES)}
    # Sort: group rank → declared `order` field → catalog position. Modules
    # that don't set `order` (==0) fall back to declaration order within
    # the group, which matches the pre-2026-05-16 behavior.
    sorted_modules = sorted(
        ADMIN_MODULES,
        key=lambda m: (rank.get(m.group, 99), m.order, catalog_order[m.code]),
    )

    # Fetch all settings + flags in 2 queries (not N+1). Index by
    # module_key for O(1) lookup during the per-module assembly below.
    from sqlalchemy import select as _select
    settings_by_module: dict[str, dict[str, object]] = {}
    flags_by_module: dict[str, dict[str, RegistryFlagOut]] = {}
    async with uow.transactional() as session:
        for s in (await session.execute(_select(ModuleSetting))).scalars().all():
            v = "[secret]" if s.is_secret else s.value
            settings_by_module.setdefault(s.module_key, {})[s.setting_key] = v
        for f in (await session.execute(_select(ModuleFeatureFlag))).scalars().all():
            flags_by_module.setdefault(f.module_key, {})[f.flag_key] = RegistryFlagOut(
                enabled=f.enabled, rollout_percent=f.rollout_percent,
            )

    modules_out: list[RegistryModuleOut] = []
    for m in sorted_modules:
        # Synthesize a single-item menu when the module didn't declare
        # sub-nav. This keeps the FE rendering one nav entry without
        # special-casing empty arrays.
        if m.menu:
            menu = [
                RegistryMenuItem(label=mi.label, path=mi.path, permission=mi.permission)
                for mi in m.menu
            ]
        else:
            menu = [
                RegistryMenuItem(
                    label=m.label_en, path=m.href, permission=m.required_perm,
                )
            ]
        modules_out.append(RegistryModuleOut(
            key=m.code,
            name=m.label_en,
            name_bn=m.label_bn,
            enabled=m.enabled,
            base_path=m.href,
            api_prefix=m.api_prefix,
            icon=m.icon,
            order=m.order,
            permissions=[m.required_perm],
            menu=menu,
            group=m.group,
            notes=m.notes,
            version=m.version,
            settings=settings_by_module.get(m.code, {}),
            flags=flags_by_module.get(m.code, {}),
        ))
    return ModuleRegistryOut(
        modules=modules_out,
        groups=[g for g in GROUP_ORDER if any(m.group == g for m in sorted_modules)],
        total=len(sorted_modules),
    )


# ─── Unified admin shell bootstrap endpoint ────────────────────────────
# One fetch on admin app mount returns everything the shell needs:
# principal + filtered nav + effective module config (rollout-evaluated)
# + groups + server-time + schema versions + SSE stream URLs. Avoids
# the 3-4 fan-out round-trips a cold admin shell would otherwise make.
class DashboardConfigOut(BaseModel):
    principal: PrincipalSummary
    modules: list[ConfigModuleOut]
    groups: list[str]
    module_config: dict[str, dict[str, object]]
    sse_streams: dict[str, str]
    server_time: str
    schema_version: Literal[1] = 1


dashboard_router = APIRouter(prefix="/admin/dashboard-config", tags=["admin-config"])


@dashboard_router.get(
    "",
    response_model=DashboardConfigOut,
    summary="Single-fetch admin shell bootstrap (principal + nav + effective config + meta).",
    description=(
        "Combines the per-caller payloads from /admin/config/me + "
        "/admin/modules/_effective into one response so the admin SPA "
        "renders the shell from a single mount-time fetch. Subsequent "
        "live updates arrive via the SSE streams listed in `sse_streams`."
    ),
)
async def get_dashboard_config(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> DashboardConfigOut:
    from datetime import datetime, timezone

    from app.modules.admin_config.service import ModuleConfigService

    perms = list(principal.permissions)
    is_admin = "*" in set(perms)

    # ── nav (same logic as /admin/config/me) ──────────────────────
    visible = visible_modules_for(perms)
    rank = {g: i for i, g in enumerate(GROUP_ORDER)}
    catalog_order = {m.code: i for i, m in enumerate(ADMIN_MODULES)}
    visible.sort(key=lambda m: (rank.get(m.group, 99), catalog_order[m.code]))
    modules = [
        ConfigModuleOut(
            code=m.code, label_en=m.label_en, label_bn=m.label_bn,
            group=m.group, href=m.href, version=m.version, notes=m.notes,
        )
        for m in visible
    ]
    groups = [g for g in GROUP_ORDER if any(m.group == g for m in visible)]

    # ── effective module config (same logic as /admin/modules/_effective
    # but secrets always redacted here; if FE needs them it must hit the
    # dedicated /_effective?reveal_secrets=true path with the perm gate).
    per_module: dict[str, dict[str, object]] = {}
    from sqlalchemy import select as _select
    async with uow.transactional() as session:
        for s in (await session.execute(_select(ModuleSetting))).scalars().all():
            slot = per_module.setdefault(s.module_key, {"settings": {}, "flags": {}})
            slot["settings"][s.setting_key] = (  # type: ignore[index]
                "[secret]" if s.is_secret else s.value
            )
        svc = ModuleConfigService(session)
        for f in (await session.execute(_select(ModuleFeatureFlag))).scalars().all():
            slot = per_module.setdefault(f.module_key, {"settings": {}, "flags": {}})
            slot["flags"][f.flag_key] = await svc.is_flag_enabled(  # type: ignore[index]
                f.module_key, f.flag_key, user_id=principal.user_id,
            )

    return DashboardConfigOut(
        principal=PrincipalSummary(
            user_id=str(principal.user_id),
            roles=list(principal.roles),
            permissions=perms,
            is_admin=is_admin,
        ),
        modules=modules,
        groups=groups,
        module_config=per_module,
        sse_streams={
            "module_config": "/api/v1/admin/modules/_stream",
            "voice_calls":   "/api/v1/customer-care/voice-calls/stream",
        },
        server_time=datetime.now(timezone.utc).isoformat(),
    )


