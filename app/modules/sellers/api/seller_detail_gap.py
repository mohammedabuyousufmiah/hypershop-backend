"""Gap-fill admin endpoints for the seller-detail page sub-resource tabs.

The admin seller-detail FE (``AdminSellerDetailClient.tsx``) fans out to
four admin-scoped GET endpoints to populate its Documents / Bank / People
tabs:

    GET /admin/sellers/{id}/agreements
    GET /admin/sellers/{id}/bank-accounts
    GET /admin/sellers/{id}/documents
    GET /admin/sellers/{id}/team

Each is read by the SDK as a ``{ items: [...], total: number }`` wire shape
(``SellerAgreementsListWire`` / ``SellerBankAccountsListWire`` /
``SellerDocumentsListWire`` / ``SellerTeamMembersListWire``). The response
envelope middleware wraps the returned object as ``{success, data, ...}``,
so each handler returns the raw ``{items, total}`` dict.

In this backend build the sellers module only ships the ``sellers`` and
``seller_users`` tables (see ``models.py``) — there are NO dedicated
``seller_agreements`` / ``seller_bank_accounts`` / ``seller_documents`` /
``seller_team_members`` tables yet. So these handlers each defensively
probe for a backing table with raw SQL and, when absent (the normal case
today), return an empty list of the CORRECT shape. That lets the FE
connect and render its empty states instead of 404-ing the whole page.

The probe auto-upgrades: if a future migration adds one of those tables
with the expected columns, the handler will start returning real rows.

This file is self-contained and import-safe: it only imports symbols that
exist in the module's ``admin.py`` router, and every query is wrapped so a
missing table/column returns ``[]`` rather than a 500.
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Path as PathParam, Query
from sqlalchemy import text

from app.core.db.uow import UnitOfWork, get_uow
from app.core.security.rbac import requires_permission

router = APIRouter(prefix="/admin/sellers", tags=["admin-seller-detail"])

# Same permission the rest of the sellers admin surface is gated on; any
# admin who can view the seller-detail page already holds it.
_RW = "sellers.admin"


async def _safe_rows(
    uow: UnitOfWork, sql: str, params: dict[str, Any]
) -> list[dict[str, Any]]:
    """Run a read-only query, returning row dicts.

    Any failure (missing table, missing column, driver error) is swallowed
    and yields an empty list — these gap endpoints must never 500.
    """
    try:
        async with uow.transactional() as session:
            result = await session.execute(text(sql), params)
            return [dict(m) for m in result.mappings().all()]
    except Exception:
        return []


@router.get(
    "/{seller_id}/agreements",
    dependencies=[Depends(requires_permission(_RW))],
)
async def list_seller_agreements(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    seller_id: Annotated[UUID, PathParam()],
) -> dict[str, Any]:
    """Agreements accepted by the seller. Empty when no backing table."""
    rows = await _safe_rows(
        uow,
        """
        SELECT id, seller_id, agreement_code, version,
               accepted_at, accepted_by
        FROM seller_agreements
        WHERE seller_id = :sid
        ORDER BY accepted_at DESC
        """,
        {"sid": str(seller_id)},
    )
    return {"items": rows, "total": len(rows)}


@router.get(
    "/{seller_id}/bank-accounts",
    dependencies=[Depends(requires_permission(_RW))],
)
async def list_seller_bank_accounts(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    seller_id: Annotated[UUID, PathParam()],
) -> dict[str, Any]:
    """Bank accounts on file for the seller. Empty when no backing table."""
    rows = await _safe_rows(
        uow,
        """
        SELECT id, seller_id, holder_name, bank_name,
               account_number_last4, ifsc_or_swift, currency_code,
               status, verification_method, verified_at,
               failure_reason, is_primary, created_at, updated_at
        FROM seller_bank_accounts
        WHERE seller_id = :sid
        ORDER BY is_primary DESC, created_at DESC
        """,
        {"sid": str(seller_id)},
    )
    return {"items": rows, "total": len(rows)}


@router.get(
    "/{seller_id}/documents",
    dependencies=[Depends(requires_permission(_RW))],
)
async def list_seller_documents(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    seller_id: Annotated[UUID, PathParam()],
    status_q: Annotated[
        str | None,
        Query(alias="status", description="Filter by status; omit for all."),
    ] = None,
) -> dict[str, Any]:
    """KYC documents for the seller. Empty when no backing table."""
    sql = """
        SELECT id, seller_id, doc_type, storage_key, original_filename,
               status, reviewer_id, reviewed_at, rejection_reason,
               expires_at, vendor_ref, created_at, updated_at
        FROM seller_documents
        WHERE seller_id = :sid
    """
    params: dict[str, Any] = {"sid": str(seller_id)}
    if status_q:
        sql += " AND status = :status"
        params["status"] = status_q
    sql += " ORDER BY created_at DESC"
    rows = await _safe_rows(uow, sql, params)
    return {"items": rows, "total": len(rows)}


@router.get(
    "/{seller_id}/team",
    dependencies=[Depends(requires_permission(_RW))],
)
async def list_seller_team(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    seller_id: Annotated[UUID, PathParam()],
    status_q: Annotated[
        str | None,
        Query(alias="status", description="Filter by status; omit for all."),
    ] = None,
) -> dict[str, Any]:
    """Team members linked to the seller.

    Prefers a dedicated ``seller_team_members`` table; when that's absent
    (the case in this build) falls back to the real ``seller_users``
    linkage rows, projecting them into the team-member wire shape. Returns
    an empty list if neither is available.
    """
    sql = """
        SELECT id, seller_id, user_id, role_code, status,
               invited_by, invited_at, accepted_at, removed_at,
               created_at, updated_at
        FROM seller_team_members
        WHERE seller_id = :sid
    """
    params: dict[str, Any] = {"sid": str(seller_id)}
    if status_q:
        sql += " AND status = :status"
        params["status"] = status_q
    sql += " ORDER BY created_at DESC"
    rows = await _safe_rows(uow, sql, params)

    if not rows:
        # Fallback: project the real seller_users linkage into the
        # team-member wire shape so the People tab shows owners/managers.
        fallback = await _safe_rows(
            uow,
            """
            SELECT
                user_id              AS id,
                seller_id,
                user_id,
                role                 AS role_code,
                'ACTIVE'             AS status,
                user_id              AS invited_by,
                created_at           AS invited_at,
                created_at           AS accepted_at,
                NULL                 AS removed_at,
                created_at,
                updated_at
            FROM seller_users
            WHERE seller_id = :sid
            ORDER BY created_at DESC
            """,
            {"sid": str(seller_id)},
        )
        if status_q:
            fallback = [r for r in fallback if r.get("status") == status_q]
        rows = fallback

    return {"items": rows, "total": len(rows)}
