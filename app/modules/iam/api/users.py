from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import Response

from app.core.db.uow import UnitOfWork, get_uow
from app.core.pagination import Page, PageParams
from app.core.security.deps import get_current_principal
from app.core.security.principal import Principal
from app.core.security.rbac import requires_permission
from app.modules.iam.api.deps import request_context
from app.modules.iam.models import UserStatus
from app.modules.iam.permissions import (
    P_USER_CREATE,
    P_USER_DELETE_ANY,
    P_USER_LIST,
    P_USER_READ_ANY,
    P_USER_UPDATE_ANY,
)
from app.modules.iam.schemas import (
    AdminUserCreate,
    AdminUserUpdate,
    RoleAssignRequest,
    UserResponse,
    UserUpdateSelf,
)
from app.modules.iam.service import IamService, RequestContext, _user_response_dict

router = APIRouter(tags=["users"])


# ---------------- self ----------------


@router.get("/users/me", response_model=UserResponse)
async def get_me(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> UserResponse:
    async with uow.transactional() as session:
        service = IamService(session)
        user = await service.get_self(principal)
        return UserResponse(**_user_response_dict(user))


@router.patch("/users/me", response_model=UserResponse)
async def update_me(
    payload: UserUpdateSelf,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    ctx: Annotated[RequestContext, Depends(request_context)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> UserResponse:
    async with uow.transactional() as session:
        service = IamService(session)
        user = await service.update_self(
            principal=principal,
            full_name=payload.full_name,
            phone=payload.phone,
            ctx=ctx,
        )
        return UserResponse(**_user_response_dict(user))


# ---------------- admin ----------------


@router.get(
    "/admin/users",
    response_model=Page[UserResponse],
    dependencies=[Depends(requires_permission(P_USER_LIST))],
)
async def admin_list_users(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    page: int = Query(default=1, ge=1, le=10_000),
    size: int = Query(default=20, ge=1, le=100),
) -> Page[UserResponse]:
    params = PageParams(page=page, size=size)
    async with uow.transactional() as session:
        service = IamService(session)
        rows, total = await service.admin_list_users(
            principal=principal,
            offset=params.offset,
            limit=params.limit,
        )
        items = [UserResponse(**_user_response_dict(u)) for u in rows]
        return Page.build(items=items, total=total, params=params)


@router.get(
    "/admin/users/{user_id}",
    response_model=UserResponse,
    dependencies=[Depends(requires_permission(P_USER_READ_ANY))],
)
async def admin_get_user(
    user_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> UserResponse:
    async with uow.transactional() as session:
        service = IamService(session)
        user = await service.admin_get_user(principal=principal, user_id=user_id)
        return UserResponse(**_user_response_dict(user))


@router.patch(
    "/admin/users/{user_id}",
    response_model=UserResponse,
    dependencies=[Depends(requires_permission(P_USER_UPDATE_ANY))],
)
async def admin_update_user(
    user_id: UUID,
    payload: AdminUserUpdate,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    ctx: Annotated[RequestContext, Depends(request_context)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> UserResponse:
    status_value = UserStatus(payload.status) if payload.status else None
    async with uow.transactional() as session:
        service = IamService(session)
        user = await service.admin_update_user(
            principal=principal,
            user_id=user_id,
            full_name=payload.full_name,
            phone=payload.phone,
            status=status_value,
            ctx=ctx,
        )
        return UserResponse(**_user_response_dict(user))


@router.post(
    "/admin/users/{user_id}/roles",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    dependencies=[Depends(requires_permission("iam.role.assign"))],
)
async def admin_assign_role(
    user_id: UUID,
    payload: RoleAssignRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    ctx: Annotated[RequestContext, Depends(request_context)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> None:
    async with uow.transactional() as session:
        service = IamService(session)
        await service.admin_assign_role(
            principal=principal,
            user_id=user_id,
            role_name=payload.role,
            ctx=ctx,
        )


@router.delete(
    "/admin/users/{user_id}/roles/{role_name}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    dependencies=[Depends(requires_permission("iam.role.assign"))],
)
async def admin_revoke_role(
    user_id: UUID,
    role_name: str,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    ctx: Annotated[RequestContext, Depends(request_context)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> None:
    async with uow.transactional() as session:
        service = IamService(session)
        await service.admin_revoke_role(
            principal=principal,
            user_id=user_id,
            role_name=role_name,
            ctx=ctx,
        )


@router.delete(
    "/admin/users/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    dependencies=[Depends(requires_permission(P_USER_DELETE_ANY))],
)
async def admin_delete_user(
    user_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    ctx: Annotated[RequestContext, Depends(request_context)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> None:
    async with uow.transactional() as session:
        service = IamService(session)
        await service.admin_delete_user(principal=principal, user_id=user_id, ctx=ctx)


@router.post(
    "/admin/iam/users",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an internal admin user + assign exactly one role (RBAC).",
    dependencies=[Depends(requires_permission(P_USER_CREATE))],
)
async def admin_create_user(
    payload: AdminUserCreate,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    ctx: Annotated[RequestContext, Depends(request_context)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> UserResponse:
    """Super-admin / system-admin creates a staff user and binds one role.

    Powers the admin-panel ``/admin/users/new`` page. The new account is
    ACTIVE + email-verified so the user can sign in immediately at their
    role's login door. Granting ``super_admin`` requires a wildcard holder.
    """
    async with uow.transactional() as session:
        service = IamService(session)
        user = await service.admin_create_user(
            principal=principal,
            email=payload.email,
            full_name=payload.full_name,
            password=payload.password,
            role_name=payload.role,
            phone=payload.phone,
            force_password_reset=payload.force_password_reset,
            ctx=ctx,
        )
        return UserResponse(**_user_response_dict(user))


# ─── IAM read-only inventory endpoints (added 2026-05-16) ──────────
# Powers the admin IAM dashboard (roles/permissions matrix view). All
# gated on `iam.role.read` since this is admin-tier inventory data —
# the role catalog leaks the org's privilege topology and shouldn't
# be customer-facing.

from app.modules.iam.permissions import P_ROLE_READ
from sqlalchemy import text as _text


@router.get(
    "/admin/iam/roles",
    summary="List all roles + their bound permissions + assigned-user counts.",
    dependencies=[Depends(requires_permission(P_ROLE_READ))],
)
async def admin_list_roles(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict[str, object]:
    """Returns:
        {
          "roles": [
            {"id", "name", "description", "is_system",
             "permission_count", "user_count",
             "permissions": ["perm.name", ...]},
            ...
          ]
        }

    One query per role for permissions; acceptable since the role
    catalog is tiny (~17 rows).
    """
    async with uow.transactional() as session:
        rows = (
            await session.execute(
                _text(
                    "SELECT r.id, r.name, r.description, r.is_system, "
                    "       (SELECT count(*) FROM role_permissions WHERE role_id = r.id) AS perm_count, "
                    "       (SELECT count(*) FROM user_roles WHERE role_id = r.id) AS user_count "
                    "FROM roles r ORDER BY r.name"
                )
            )
        ).all()
        out: list[dict] = []
        for r in rows:
            perms = (
                await session.execute(
                    _text(
                        "SELECT p.name FROM permissions p "
                        "JOIN role_permissions rp ON rp.permission_id = p.id "
                        "WHERE rp.role_id = :rid ORDER BY p.name"
                    ),
                    {"rid": r[0]},
                )
            ).all()
            out.append({
                "id": str(r[0]),
                "name": r[1],
                "description": r[2],
                "is_system": bool(r[3]),
                "permission_count": int(r[4]),
                "user_count": int(r[5]),
                "permissions": [p[0] for p in perms],
            })
        return {"roles": out}


@router.get(
    "/admin/iam/permissions",
    summary="List all permissions in the catalog + how many roles hold each.",
    dependencies=[Depends(requires_permission(P_ROLE_READ))],
)
async def admin_list_permissions(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict[str, object]:
    """Returns:
        {
          "permissions": [
            {"id", "name", "description",
             "role_count", "roles": ["role.name", ...]},
            ...
          ]
        }
    """
    async with uow.transactional() as session:
        rows = (
            await session.execute(
                _text(
                    "SELECT p.id, p.name, p.description, "
                    "       (SELECT count(*) FROM role_permissions WHERE permission_id = p.id) "
                    "FROM permissions p ORDER BY p.name"
                )
            )
        ).all()
        out: list[dict] = []
        for p in rows:
            roles = (
                await session.execute(
                    _text(
                        "SELECT r.name FROM roles r "
                        "JOIN role_permissions rp ON rp.role_id = r.id "
                        "WHERE rp.permission_id = :pid ORDER BY r.name"
                    ),
                    {"pid": p[0]},
                )
            ).all()
            out.append({
                "id": str(p[0]),
                "name": p[1],
                "description": p[2],
                "role_count": int(p[3]),
                "roles": [r[0] for r in roles],
            })
        return {"permissions": out}


@router.get(
    "/admin/audit-log",
    summary="Filtered audit-log query for compliance + ops investigation.",
    dependencies=[Depends(requires_permission("iam.audit.read"))],
)
async def admin_audit_log(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    actor_id: UUID | None = Query(default=None, description="Filter to a specific actor"),
    action: str | None = Query(default=None, max_length=80, description="Substring match on action"),
    resource_type: str | None = Query(default=None, max_length=80),
    outcome: str | None = Query(default=None, pattern=r"^(success|failure)$"),
    since: str | None = Query(default=None, description="ISO 8601 timestamp lower bound"),
    until: str | None = Query(default=None, description="ISO 8601 timestamp upper bound"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, object]:
    """Reads ``audit_logs`` (renamed from ``audit_log`` in alembic 0063). Returns paginated results sorted newest-first.
    """
    where: list[str] = []
    params: dict[str, object] = {"lim": limit, "off": offset}
    if actor_id is not None:
        where.append("actor_id = :aid")
        params["aid"] = actor_id
    if action:
        where.append("action ILIKE :act")
        params["act"] = f"%{action}%"
    if resource_type:
        where.append("resource_type = :rt")
        params["rt"] = resource_type
    if outcome:
        where.append("outcome = :oc")
        params["oc"] = outcome
    if since:
        where.append("occurred_at >= :since")
        params["since"] = since
    if until:
        where.append("occurred_at <= :until")
        params["until"] = until
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    async with uow.transactional() as session:
        total = (
            await session.execute(
                _text(f"SELECT count(*) FROM audit_logs {where_sql}"), params,
            )
        ).scalar_one()
        rows = (
            await session.execute(
                _text(
                    f"SELECT id, occurred_at, actor_id, actor_kind, action, "
                    f"       resource_type, resource_id, outcome, request_id, "
                    f"       ip_address, user_agent, metadata "
                    f"FROM audit_logs {where_sql} "
                    f"ORDER BY occurred_at DESC LIMIT :lim OFFSET :off"
                ),
                params,
            )
        ).all()
        items = [
            {
                "id": str(r[0]),
                "occurred_at": r[1].isoformat() if r[1] else None,
                "actor_id": str(r[2]) if r[2] else None,
                "actor_kind": r[3],
                "action": r[4],
                "resource_type": r[5],
                "resource_id": str(r[6]) if r[6] else None,
                "outcome": r[7],
                "request_id": str(r[8]) if r[8] else None,
                "ip_address": str(r[9]) if r[9] is not None else None,
                "user_agent": r[10],
                "metadata": r[11],
            }
            for r in rows
        ]
    return {
        "items": items,
        "total": int(total),
        "limit": limit,
        "offset": offset,
    }
