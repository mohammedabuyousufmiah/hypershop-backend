"""Admin endpoints for module_settings + module_feature_flags.

Mounted under ``/api/v1/admin/modules/*``:

  GET    /admin/modules/{module_key}/settings
  PUT    /admin/modules/{module_key}/settings/{setting_key}
  DELETE /admin/modules/{module_key}/settings/{setting_key}

  GET    /admin/modules/{module_key}/flags
  PUT    /admin/modules/{module_key}/flags/{flag_key}
  DELETE /admin/modules/{module_key}/flags/{flag_key}

Read is gated on ``iam.role.read`` (admin-tier inspection; same gate
as the rest of /admin/iam/*). Write is gated on ``module.config.write``
(admin + super_admin only — flipping a flag affects customer flows).

Idempotent upsert: PUT INSERTs on first call, UPDATEs on subsequent.
Returns the resulting row so callers can use ``If-Match`` / ETag-style
flows if they want optimistic concurrency later.
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.audit.service import record_audit
from app.core.db.uow import UnitOfWork, get_uow
from app.core.events.outbox import enqueue_outbox
from app.core.security.deps import get_current_principal
from app.core.security.principal import Principal
from app.core.security.rbac import requires_permission
from app.modules.admin_config.models import ModuleFeatureFlag, ModuleSetting


EVT_CONFIG_CHANGED = "module.config.changed"


async def _emit_changed(*, module_key: str, kind: str, key: str, op: str) -> None:
    """Fan a `module.config.changed` event to:
      1. The outbox — durable, drives ARQ-worker side effects + audit.
      2. The in-process ``sse_bus`` — zero-latency for THIS pod's subs.
      3. The Redis pub/sub bridge — cross-pod fan-out for multi-replica.

    Without (2), dev mode (no ARQ worker) would never see SSE
    invalidations even from the same uvicorn that handled the write.
    Without (3), Tab B on pod B would miss flips made on pod A.
    """
    from app.modules.admin_config import sse_bus, sse_redis_bridge
    payload = {
        "module_key": module_key,
        "kind": kind,    # "setting" | "flag"
        "key": key,
        "op": op,        # "upsert" | "delete"
    }
    event = {"type": EVT_CONFIG_CHANGED, **payload}
    await enqueue_outbox(type=EVT_CONFIG_CHANGED, payload=payload)
    sse_bus.publish(event)
    sse_redis_bridge.publish_config_event(event)

router = APIRouter(prefix="/admin/modules", tags=["admin-modules"])

_READ = "iam.role.read"
_WRITE = "module.config.write"
_REDACTED = "[secret]"


# ─── Pydantic shapes ──────────────────────────────────────────────────
class SettingOut(BaseModel):
    id: UUID
    module_key: str
    setting_key: str
    value: Any
    value_type: str
    description: str | None
    is_secret: bool
    updated_by: UUID | None
    updated_at: datetime
    created_at: datetime


class SettingUpsert(BaseModel):
    value: Any
    value_type: str = Field(default="json", pattern=r"^(string|number|boolean|json)$")
    description: str | None = Field(default=None, max_length=512)
    is_secret: bool = False


class FlagOut(BaseModel):
    id: UUID
    module_key: str
    flag_key: str
    enabled: bool
    rollout_percent: int
    description: str | None
    updated_by: UUID | None
    updated_at: datetime
    created_at: datetime


class FlagUpsert(BaseModel):
    enabled: bool
    rollout_percent: int = Field(default=100, ge=0, le=100)
    description: str | None = Field(default=None, max_length=512)


def _redact(s: ModuleSetting, *, reveal_secrets: bool) -> SettingOut:
    return SettingOut(
        id=s.id,
        module_key=s.module_key,
        setting_key=s.setting_key,
        value=s.value if (reveal_secrets or not s.is_secret) else _REDACTED,
        value_type=s.value_type,
        description=s.description,
        is_secret=s.is_secret,
        updated_by=s.updated_by,
        updated_at=s.updated_at,
        created_at=s.created_at,
    )


# ─── module_settings endpoints ────────────────────────────────────────
@router.get(
    "/{module_key}/settings",
    summary="List all settings for a module.",
    dependencies=[Depends(requires_permission(_READ))],
)
async def list_settings(
    module_key: str,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    reveal_secrets: bool = Query(default=False,
        description="If true, return secret values (requires module.config.write — checked separately)."),
    principal: Principal | None = Depends(get_current_principal),
) -> dict[str, object]:
    if reveal_secrets and not principal.has_permission(_WRITE):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "forbidden", "message": f"reveal_secrets requires {_WRITE}"},
        )
    async with uow.transactional() as session:
        rows = (
            await session.execute(
                select(ModuleSetting)
                .where(ModuleSetting.module_key == module_key)
                .order_by(ModuleSetting.setting_key)
            )
        ).scalars().all()
        return {
            "module_key": module_key,
            "items": [_redact(r, reveal_secrets=reveal_secrets).model_dump(mode="json") for r in rows],
            "total": len(rows),
        }


@router.put(
    "/{module_key}/settings/{setting_key}",
    summary="Upsert a module setting.",
    dependencies=[Depends(requires_permission(_WRITE))],
)
async def upsert_setting(
    module_key: str,
    setting_key: str,
    payload: SettingUpsert,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> dict[str, object]:
    async with uow.transactional() as session:
        stmt = (
            pg_insert(ModuleSetting)
            .values(
                module_key=module_key,
                setting_key=setting_key,
                value=payload.value,
                value_type=payload.value_type,
                description=payload.description,
                is_secret=payload.is_secret,
                updated_by=principal.user_id,
            )
            .on_conflict_do_update(
                index_elements=["module_key", "setting_key"],
                set_=dict(
                    value=payload.value,
                    value_type=payload.value_type,
                    description=payload.description,
                    is_secret=payload.is_secret,
                    updated_by=principal.user_id,
                ),
            )
            .returning(ModuleSetting)
        )
        row = (await session.execute(stmt)).scalar_one()
        await record_audit(
            actor=principal,
            action="module.setting.upsert",
            resource_type="module_settings",
            resource_id=row.id,
            metadata={
                "module_key": module_key,
                "setting_key": setting_key,
                # Don't log secret values in audit metadata.
                "value_logged": (_REDACTED if payload.is_secret else payload.value),
            },
        )
        await _emit_changed(module_key=module_key, kind="setting",
                            key=setting_key, op="upsert")
        return {"item": _redact(row, reveal_secrets=True).model_dump(mode="json")}


@router.delete(
    "/{module_key}/settings/{setting_key}",
    summary="Delete a module setting.",
    dependencies=[Depends(requires_permission(_WRITE))],
)
async def delete_setting(
    module_key: str,
    setting_key: str,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> dict[str, bool]:
    async with uow.transactional() as session:
        row = (
            await session.execute(
                select(ModuleSetting).where(
                    ModuleSetting.module_key == module_key,
                    ModuleSetting.setting_key == setting_key,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            return {"deleted": False}  # idempotent — deleting an absent key is a no-op
        deleted_id = row.id
        await session.delete(row)
        await record_audit(
            actor=principal,
            action="module.setting.delete",
            resource_type="module_settings",
            resource_id=deleted_id,
            metadata={"module_key": module_key, "setting_key": setting_key},
        )
        await _emit_changed(module_key=module_key, kind="setting",
                            key=setting_key, op="delete")
        return {"deleted": True}


# ─── module_feature_flags endpoints ───────────────────────────────────
@router.get(
    "/{module_key}/flags",
    summary="List all feature flags for a module.",
    dependencies=[Depends(requires_permission(_READ))],
)
async def list_flags(
    module_key: str,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict[str, object]:
    async with uow.transactional() as session:
        rows = (
            await session.execute(
                select(ModuleFeatureFlag)
                .where(ModuleFeatureFlag.module_key == module_key)
                .order_by(ModuleFeatureFlag.flag_key)
            )
        ).scalars().all()
        return {
            "module_key": module_key,
            "items": [FlagOut.model_validate(r, from_attributes=True).model_dump(mode="json") for r in rows],
            "total": len(rows),
        }


@router.put(
    "/{module_key}/flags/{flag_key}",
    summary="Upsert a module feature flag.",
    dependencies=[Depends(requires_permission(_WRITE))],
)
async def upsert_flag(
    module_key: str,
    flag_key: str,
    payload: FlagUpsert,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> dict[str, object]:
    async with uow.transactional() as session:
        stmt = (
            pg_insert(ModuleFeatureFlag)
            .values(
                module_key=module_key,
                flag_key=flag_key,
                enabled=payload.enabled,
                rollout_percent=payload.rollout_percent,
                description=payload.description,
                updated_by=principal.user_id,
            )
            .on_conflict_do_update(
                index_elements=["module_key", "flag_key"],
                set_=dict(
                    enabled=payload.enabled,
                    rollout_percent=payload.rollout_percent,
                    description=payload.description,
                    updated_by=principal.user_id,
                ),
            )
            .returning(ModuleFeatureFlag)
        )
        row = (await session.execute(stmt)).scalar_one()
        await record_audit(
            actor=principal,
            action="module.flag.upsert",
            resource_type="module_feature_flags",
            resource_id=row.id,
            metadata={
                "module_key": module_key,
                "flag_key": flag_key,
                "enabled": payload.enabled,
                "rollout_percent": payload.rollout_percent,
            },
        )
        await _emit_changed(module_key=module_key, kind="flag",
                            key=flag_key, op="upsert")
        return {"item": FlagOut.model_validate(row, from_attributes=True).model_dump(mode="json")}


# ─── bulk export / import ────────────────────────────────────────────
class ImportSettingIn(BaseModel):
    module_key: str = Field(..., max_length=64)
    setting_key: str = Field(..., max_length=96)
    value: Any
    value_type: str = Field(default="json", pattern=r"^(string|number|boolean|json)$")
    description: str | None = Field(default=None, max_length=512)
    is_secret: bool = False


class ImportFlagIn(BaseModel):
    module_key: str = Field(..., max_length=64)
    flag_key: str = Field(..., max_length=96)
    enabled: bool
    rollout_percent: int = Field(default=100, ge=0, le=100)
    description: str | None = Field(default=None, max_length=512)


class ImportRequest(BaseModel):
    """Bulk-import payload. Both arrays are optional so callers can
    import only settings or only flags. Same row = same `(module_key,
    key)` upserts; missing rows are NOT deleted (use the explicit
    DELETE endpoint for that).
    """
    settings: list[ImportSettingIn] = Field(default_factory=list)
    flags: list[ImportFlagIn] = Field(default_factory=list)


@router.get(
    "/_export",
    summary="Bulk dump of all module_settings + module_feature_flags.",
    description=(
        "Returns a JSON blob suitable for backup, environment promotion "
        "(dev → staging → prod), or version control. Secret values are "
        "redacted as `'[secret]'` unless `reveal_secrets=true` AND the "
        "caller holds `module.config.write`. Import path at /_import "
        "accepts the same shape (minus the export envelope wrapper)."
    ),
    dependencies=[Depends(requires_permission(_READ))],
)
async def export_config(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    reveal_secrets: bool = Query(default=False),
) -> dict[str, object]:
    if reveal_secrets and not principal.has_permission(_WRITE):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "forbidden",
                    "message": f"reveal_secrets requires {_WRITE}"},
        )
    from datetime import datetime, timezone
    async with uow.transactional() as session:
        s_rows = (
            await session.execute(
                select(ModuleSetting).order_by(
                    ModuleSetting.module_key, ModuleSetting.setting_key,
                )
            )
        ).scalars().all()
        f_rows = (
            await session.execute(
                select(ModuleFeatureFlag).order_by(
                    ModuleFeatureFlag.module_key, ModuleFeatureFlag.flag_key,
                )
            )
        ).scalars().all()
        return {
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "schema_version": 1,
            "settings": [
                {
                    "module_key": s.module_key,
                    "setting_key": s.setting_key,
                    "value": _REDACTED if (s.is_secret and not reveal_secrets) else s.value,
                    "value_type": s.value_type,
                    "description": s.description,
                    "is_secret": s.is_secret,
                }
                for s in s_rows
            ],
            "flags": [
                {
                    "module_key": f.module_key,
                    "flag_key": f.flag_key,
                    "enabled": f.enabled,
                    "rollout_percent": f.rollout_percent,
                    "description": f.description,
                }
                for f in f_rows
            ],
            "counts": {"settings": len(s_rows), "flags": len(f_rows)},
        }


@router.post(
    "/_import",
    summary="Bulk upsert of module settings + flags. Idempotent.",
    description=(
        "Accepts the same shape /_export emits (`{settings: [...], "
        "flags: [...]}`). Each row INSERT … ON CONFLICT UPDATEs by "
        "`(module_key, key)`. Missing rows are NOT deleted — use the "
        "explicit DELETE endpoints for that. One outbox event fired "
        "per row so SSE subscribers refresh. Refuses to import "
        "`value='[secret]'` literals (catches accidental re-import "
        "of redacted exports)."
    ),
    dependencies=[Depends(requires_permission(_WRITE))],
)
async def import_config(
    payload: ImportRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> dict[str, object]:
    settings_in = 0
    flags_in = 0
    skipped_secret_placeholders = 0
    async with uow.transactional() as session:
        for s in payload.settings:
            # Guard against re-importing a redacted export — if the
            # operator dumped without reveal_secrets and now imports
            # back, the redaction would clobber the real value. Refuse.
            if s.is_secret and s.value == _REDACTED:
                skipped_secret_placeholders += 1
                continue
            stmt = (
                pg_insert(ModuleSetting)
                .values(
                    module_key=s.module_key,
                    setting_key=s.setting_key,
                    value=s.value,
                    value_type=s.value_type,
                    description=s.description,
                    is_secret=s.is_secret,
                    updated_by=principal.user_id,
                )
                .on_conflict_do_update(
                    index_elements=["module_key", "setting_key"],
                    set_=dict(
                        value=s.value,
                        value_type=s.value_type,
                        description=s.description,
                        is_secret=s.is_secret,
                        updated_by=principal.user_id,
                    ),
                )
            )
            await session.execute(stmt)
            settings_in += 1
        for f in payload.flags:
            stmt = (
                pg_insert(ModuleFeatureFlag)
                .values(
                    module_key=f.module_key,
                    flag_key=f.flag_key,
                    enabled=f.enabled,
                    rollout_percent=f.rollout_percent,
                    description=f.description,
                    updated_by=principal.user_id,
                )
                .on_conflict_do_update(
                    index_elements=["module_key", "flag_key"],
                    set_=dict(
                        enabled=f.enabled,
                        rollout_percent=f.rollout_percent,
                        description=f.description,
                        updated_by=principal.user_id,
                    ),
                )
            )
            await session.execute(stmt)
            flags_in += 1
        await record_audit(
            actor=principal,
            action="module.config.bulk_import",
            resource_type="module_config",
            resource_id=None,
            metadata={
                "settings_in": settings_in,
                "flags_in": flags_in,
                "skipped_secret_placeholders": skipped_secret_placeholders,
            },
        )
        # Emit ONE coarse-grain change event rather than N row-level
        # events — SSE consumers just refresh cache once after a bulk
        # import. Keeps the event firehose sane.
        await _emit_changed(module_key="*", kind="bulk_import",
                            key="*", op="upsert")
    return {
        "imported_settings": settings_in,
        "imported_flags": flags_in,
        "skipped_secret_placeholders": skipped_secret_placeholders,
    }


# ─── SSE invalidation stream ─────────────────────────────────────────
@router.get(
    "/_stream",
    summary="SSE stream of module.config.changed events for live invalidation.",
    description=(
        "Pushes one JSON event per config mutation across the cluster "
        "(via the outbox → in-process SSE bus bridge). Each event carries "
        "`{type, module_key, kind, key, op}`. FE admin tabs subscribe to "
        "re-fetch /admin/modules/_effective on flag/setting changes without "
        "polling. Gated on `iam.role.read` (same as read endpoints)."
    ),
    dependencies=[Depends(requires_permission(_READ))],
)
async def config_stream() -> StreamingResponse:
    from app.modules.admin_config import sse_bus
    q = sse_bus.subscribe()

    async def gen():
        try:
            async for chunk in sse_bus.event_stream(q):
                yield chunk
        finally:
            sse_bus.unsubscribe(q)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ─── effective config — rollout pre-evaluated for the caller ────────
@router.get(
    "/_effective",
    summary="All modules' settings + flags collapsed to caller-effective values.",
    description=(
        "Same shape as /admin/module-registry but each flag is reduced "
        "to a single bool — `is_flag_enabled(flag, user_id=me)` is "
        "evaluated server-side using the deterministic rollout bucket. "
        "FE doesn't need to know rollout_percent semantics — just read "
        "`flags[key] === true`. Settings flow through unchanged "
        "(secrets stay redacted unless `reveal_secrets=true` + caller "
        "holds `module.config.write`)."
    ),
    dependencies=[Depends(requires_permission(_READ))],
)
async def effective_config(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    reveal_secrets: bool = Query(default=False),
) -> dict[str, object]:
    from app.modules.admin_config.service import ModuleConfigService
    if reveal_secrets and not principal.has_permission(_WRITE):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "forbidden", "message": f"reveal_secrets requires {_WRITE}"},
        )
    async with uow.transactional() as session:
        # Pull every setting + flag in 2 queries, then collapse per module.
        settings_rows = (await session.execute(select(ModuleSetting))).scalars().all()
        flags_rows = (await session.execute(select(ModuleFeatureFlag))).scalars().all()
        svc = ModuleConfigService(session)
        per_module: dict[str, dict[str, object]] = {}
        for s in settings_rows:
            slot = per_module.setdefault(s.module_key, {"settings": {}, "flags": {}})
            v = _REDACTED if (s.is_secret and not reveal_secrets) else s.value
            slot["settings"][s.setting_key] = v  # type: ignore[index]
        for f in flags_rows:
            slot = per_module.setdefault(f.module_key, {"settings": {}, "flags": {}})
            # Re-fetch through the service so rollout bucketing logic
            # stays in one place. Single in-session query; acceptable.
            slot["flags"][f.flag_key] = await svc.is_flag_enabled(  # type: ignore[index]
                f.module_key, f.flag_key, user_id=principal.user_id,
            )
    return {
        "user_id": str(principal.user_id),
        "modules": per_module,
        "schema_version": 1,
    }


@router.delete(
    "/{module_key}/flags/{flag_key}",
    summary="Delete a module feature flag.",
    dependencies=[Depends(requires_permission(_WRITE))],
)
async def delete_flag(
    module_key: str,
    flag_key: str,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> dict[str, bool]:
    async with uow.transactional() as session:
        row = (
            await session.execute(
                select(ModuleFeatureFlag).where(
                    ModuleFeatureFlag.module_key == module_key,
                    ModuleFeatureFlag.flag_key == flag_key,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            return {"deleted": False}
        deleted_id = row.id
        await session.delete(row)
        await record_audit(
            actor=principal,
            action="module.flag.delete",
            resource_type="module_feature_flags",
            resource_id=deleted_id,
            metadata={"module_key": module_key, "flag_key": flag_key},
        )
        await _emit_changed(module_key=module_key, kind="flag",
                            key=flag_key, op="delete")
        return {"deleted": True}
