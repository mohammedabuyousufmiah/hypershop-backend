"""Sellers phase 4 — self-serve registration application flow.

Surface:
- Customer-facing (any authenticated user):
    POST /seller-applications           — start a new application
    POST /seller-applications/{id}/kyc  — upload KYC details + docs
    GET  /seller-applications/me        — list my applications
    GET  /seller-applications/{id}      — get application detail

- Admin-facing (customercare_admin or sellers.admin):
    GET  /admin/seller-applications                 — review queue
    GET  /admin/seller-applications/{id}            — full detail
    POST /admin/seller-applications/{id}/request-info
    POST /admin/seller-applications/{id}/approve    — creates Seller + SellerUser
    POST /admin/seller-applications/{id}/reject

Lifecycle:
    pending → kyc_submitted → approved (terminal: creates Seller row)
                            ↘ rejected (terminal)
                            ↘ info_requested (admin asked for more; → kyc_submitted on next upload)
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import EmailStr, Field
from sqlalchemy import text as _t

from app.core.audit.service import record_audit
from app.core.db.uow import UnitOfWork, get_uow
from app.core.errors import BusinessRuleError, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.core.security.deps import get_current_principal
from app.core.security.principal import Principal
from app.core.security.rbac import requires_permission
from app.core.validation import StrictModel

_log = get_logger("hypershop.sellers.applications")

# Customer-facing: any authenticated user can apply
_CUSTOMER = "iam.user.read.self"
# Admin-facing: reuse the existing sellers admin permission
_ADMIN = "sellers.admin"


# ============================================================== Schemas
class ApplicationStart(StrictModel):
    business_name: str = Field(..., min_length=2, max_length=200)
    contact_email: EmailStr | None = None
    contact_phone: str | None = Field(default=None, min_length=6, max_length=32)


class KycSubmit(StrictModel):
    nid: str = Field(..., min_length=8, max_length=32)
    tin: str | None = Field(default=None, max_length=32)
    trade_license_no: str | None = Field(default=None, max_length=64)
    trade_license_url: str | None = Field(default=None, max_length=2048)
    nid_front_url: str = Field(..., max_length=2048)
    nid_back_url: str = Field(..., max_length=2048)
    bank_account_name: str = Field(..., min_length=2, max_length=200)
    bank_account_number: str = Field(..., min_length=4, max_length=32)
    bank_name: str = Field(..., min_length=2, max_length=120)
    bank_branch: str | None = Field(default=None, max_length=120)


class ApplicationResponse(StrictModel):
    id: UUID
    applicant_user_id: UUID
    business_name: str
    contact_email: str | None
    contact_phone: str | None
    status: str
    nid: str | None
    tin: str | None
    trade_license_no: str | None
    admin_note: str | None
    rejection_reason: str | None
    info_requested_text: str | None
    approved_seller_id: UUID | None
    kyc_submitted_at: datetime | None
    moderated_at: datetime | None
    created_at: datetime
    updated_at: datetime


class InfoRequestBody(StrictModel):
    note: str = Field(..., min_length=1, max_length=2000)


class RejectBody(StrictModel):
    reason: str = Field(..., min_length=1, max_length=500)


class ApproveBody(StrictModel):
    commission_percent: float | None = Field(default=None, ge=0, le=100)
    admin_note: str | None = Field(default=None, max_length=2000)


# ============================================================== Helpers
def _row_to_response(r: Any) -> ApplicationResponse:
    return ApplicationResponse(
        id=r[0], applicant_user_id=r[1], business_name=r[2],
        contact_email=r[3], contact_phone=r[4], status=r[5],
        nid=r[6], tin=r[7], trade_license_no=r[8],
        admin_note=r[9], rejection_reason=r[10], info_requested_text=r[11],
        approved_seller_id=r[12], kyc_submitted_at=r[13],
        moderated_at=r[14], created_at=r[15], updated_at=r[16],
    )


_SELECT_COLS = (
    "id, applicant_user_id, business_name, contact_email, contact_phone, "
    "status, nid, tin, trade_license_no, admin_note, rejection_reason, "
    "info_requested_text, approved_seller_id, kyc_submitted_at, "
    "moderated_at, created_at, updated_at"
)


# ============================================================== Customer router
customer_router = APIRouter(prefix="/seller-applications", tags=["seller-applications"])


@customer_router.post(
    "",
    response_model=ApplicationResponse,
    status_code=201,
    summary="Start a new seller application",
    dependencies=[Depends(requires_permission(_CUSTOMER))],
)
async def start_application(
    body: ApplicationStart,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> ApplicationResponse:
    async with uow.transactional() as session:
        # Reject if user already has an open application
        existing = (
            await session.execute(
                _t(
                    "SELECT id FROM seller_applications WHERE applicant_user_id = :u "
                    "AND status IN ('pending','kyc_submitted','info_requested') LIMIT 1"
                ),
                {"u": principal.user_id},
            )
        ).first()
        if existing:
            raise BusinessRuleError(
                "You already have an open application — submit KYC or wait for review",
            )
        r = await session.execute(
            _t(
                f"""
                INSERT INTO seller_applications
                    (id, applicant_user_id, business_name, contact_email,
                     contact_phone, status)
                VALUES
                    (gen_random_uuid(), :u, :bn, :em, :ph, 'pending')
                RETURNING {_SELECT_COLS}
                """,
            ),
            {
                "u": principal.user_id, "bn": body.business_name,
                "em": body.contact_email, "ph": body.contact_phone,
            },
        )
        row = r.first()
        await record_audit(
            actor=principal,
            action="sellers.application.started",
            resource_type="seller_applications",
            resource_id=row[0],
            metadata={"business_name": body.business_name},
        )
        return _row_to_response(row)


@customer_router.post(
    "/{app_id}/kyc",
    response_model=ApplicationResponse,
    summary="Submit KYC details + document URLs — transitions to kyc_submitted",
    dependencies=[Depends(requires_permission(_CUSTOMER))],
)
async def submit_kyc(
    app_id: Annotated[UUID, Path(...)],
    body: KycSubmit,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> ApplicationResponse:
    async with uow.transactional() as session:
        cur = (
            await session.execute(
                _t(
                    "SELECT status FROM seller_applications "
                    "WHERE id = :a AND applicant_user_id = :u FOR UPDATE"
                ),
                {"a": app_id, "u": principal.user_id},
            )
        ).first()
        if cur is None:
            raise NotFoundError("Application not found or not yours")
        if cur[0] not in ("pending", "info_requested"):
            raise BusinessRuleError(
                f"Cannot submit KYC from status {cur[0]}",
            )
        r = await session.execute(
            _t(
                f"""
                UPDATE seller_applications SET
                    nid = :nid, tin = :tin,
                    trade_license_no = :tln, trade_license_url = :tlu,
                    nid_front_url = :nfu, nid_back_url = :nbu,
                    bank_account_name = :ban, bank_account_number = :bnum,
                    bank_name = :bnm, bank_branch = :bbr,
                    status = 'kyc_submitted',
                    kyc_submitted_at = now(),
                    info_requested_text = NULL,
                    updated_at = now()
                WHERE id = :a AND applicant_user_id = :u
                RETURNING {_SELECT_COLS}
                """,
            ),
            {
                "a": app_id, "u": principal.user_id,
                "nid": body.nid, "tin": body.tin,
                "tln": body.trade_license_no, "tlu": body.trade_license_url,
                "nfu": body.nid_front_url, "nbu": body.nid_back_url,
                "ban": body.bank_account_name, "bnum": body.bank_account_number,
                "bnm": body.bank_name, "bbr": body.bank_branch,
            },
        )
        row = r.first()
        await record_audit(
            actor=principal,
            action="sellers.application.kyc_submitted",
            resource_type="seller_applications", resource_id=app_id,
        )
        return _row_to_response(row)


@customer_router.get(
    "/me",
    response_model=list[ApplicationResponse],
    summary="List my applications (newest first)",
    dependencies=[Depends(requires_permission(_CUSTOMER))],
)
async def list_mine(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> list[ApplicationResponse]:
    async with uow.transactional() as session:
        rows = (
            await session.execute(
                _t(
                    f"SELECT {_SELECT_COLS} FROM seller_applications "
                    f"WHERE applicant_user_id = :u ORDER BY created_at DESC LIMIT 50"
                ),
                {"u": principal.user_id},
            )
        ).all()
        return [_row_to_response(r) for r in rows]


@customer_router.get(
    "/{app_id}",
    response_model=ApplicationResponse,
    dependencies=[Depends(requires_permission(_CUSTOMER))],
)
async def get_mine(
    app_id: Annotated[UUID, Path(...)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> ApplicationResponse:
    async with uow.transactional() as session:
        r = (
            await session.execute(
                _t(
                    f"SELECT {_SELECT_COLS} FROM seller_applications "
                    f"WHERE id = :a AND applicant_user_id = :u"
                ),
                {"a": app_id, "u": principal.user_id},
            )
        ).first()
        if r is None:
            raise NotFoundError("Application not found")
        return _row_to_response(r)


# ============================================================== Admin router
admin_router = APIRouter(
    prefix="/admin/seller-applications", tags=["admin-seller-applications"],
)


@admin_router.get(
    "",
    response_model=list[ApplicationResponse],
    summary="Review queue (kyc_submitted first)",
    dependencies=[Depends(requires_permission(_ADMIN))],
)
async def admin_list(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[ApplicationResponse]:
    sql = f"SELECT {_SELECT_COLS} FROM seller_applications "
    params: dict[str, Any] = {"lim": limit}
    if status_filter:
        sql += "WHERE status = :st "
        params["st"] = status_filter
    sql += (
        "ORDER BY CASE WHEN status = 'kyc_submitted' THEN 0 "
        "WHEN status = 'pending' THEN 1 ELSE 2 END, "
        "created_at DESC LIMIT :lim"
    )
    async with uow.transactional() as session:
        rows = (await session.execute(_t(sql), params)).all()
        return [_row_to_response(r) for r in rows]


@admin_router.get(
    "/{app_id}",
    response_model=ApplicationResponse,
    dependencies=[Depends(requires_permission(_ADMIN))],
)
async def admin_get(
    app_id: Annotated[UUID, Path(...)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> ApplicationResponse:
    async with uow.transactional() as session:
        r = (
            await session.execute(
                _t(f"SELECT {_SELECT_COLS} FROM seller_applications WHERE id = :a"),
                {"a": app_id},
            )
        ).first()
        if r is None:
            raise NotFoundError("Application not found")
        return _row_to_response(r)


@admin_router.post(
    "/{app_id}/request-info",
    response_model=ApplicationResponse,
    summary="Admin asks the applicant for more info / cleaner docs",
    dependencies=[Depends(requires_permission(_ADMIN))],
)
async def admin_request_info(
    app_id: Annotated[UUID, Path(...)],
    body: InfoRequestBody,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> ApplicationResponse:
    async with uow.transactional() as session:
        r = await session.execute(
            _t(
                f"""
                UPDATE seller_applications
                SET status = 'info_requested',
                    info_requested_text = :note,
                    moderated_by = :mod, moderated_at = now(),
                    updated_at = now()
                WHERE id = :a AND status IN ('pending', 'kyc_submitted', 'info_requested')
                RETURNING {_SELECT_COLS}
                """,
            ),
            {"a": app_id, "note": body.note, "mod": principal.user_id},
        )
        row = r.first()
        if row is None:
            raise NotFoundError("Application not found or not in reviewable state")
        await record_audit(
            actor=principal,
            action="sellers.application.info_requested",
            resource_type="seller_applications", resource_id=app_id,
        )
        return _row_to_response(row)


@admin_router.post(
    "/{app_id}/reject",
    response_model=ApplicationResponse,
    dependencies=[Depends(requires_permission(_ADMIN))],
)
async def admin_reject(
    app_id: Annotated[UUID, Path(...)],
    body: RejectBody,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> ApplicationResponse:
    async with uow.transactional() as session:
        r = await session.execute(
            _t(
                f"""
                UPDATE seller_applications
                SET status = 'rejected',
                    rejection_reason = :rsn,
                    moderated_by = :mod, moderated_at = now(),
                    updated_at = now()
                WHERE id = :a AND status NOT IN ('approved', 'rejected')
                RETURNING {_SELECT_COLS}
                """,
            ),
            {"a": app_id, "rsn": body.reason, "mod": principal.user_id},
        )
        row = r.first()
        if row is None:
            raise NotFoundError("Application not found or already terminal")
        await record_audit(
            actor=principal,
            action="sellers.application.rejected",
            resource_type="seller_applications", resource_id=app_id,
            metadata={"reason": body.reason},
        )
        return _row_to_response(row)


@admin_router.post(
    "/{app_id}/approve",
    response_model=ApplicationResponse,
    summary="Approve the application — creates a Seller row and links the applicant as owner",
    dependencies=[Depends(requires_permission(_ADMIN))],
)
async def admin_approve(
    app_id: Annotated[UUID, Path(...)],
    body: ApproveBody,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> ApplicationResponse:
    """Atomic 3-step:
    1. Read the application (must be in kyc_submitted or info_requested state)
    2. Create a ``sellers`` row with status='approved' carrying the KYC data
    3. Insert ``seller_users(seller_id, user_id, role='owner')``
    4. Stamp the application: status='approved', approved_seller_id=<new>
    All in one transaction so a partial failure rolls back cleanly.
    """
    async with uow.transactional() as session:
        app_row = (
            await session.execute(
                _t(
                    "SELECT applicant_user_id, business_name, contact_email, "
                    "       contact_phone, nid, tin, trade_license_no, "
                    "       bank_account_name, bank_account_number, "
                    "       bank_name, bank_branch, status "
                    "FROM seller_applications WHERE id = :a FOR UPDATE"
                ),
                {"a": app_id},
            )
        ).first()
        if app_row is None:
            raise NotFoundError("Application not found")
        if app_row[11] not in ("kyc_submitted", "info_requested"):
            raise BusinessRuleError(
                f"Application must be kyc_submitted before approval (currently: {app_row[11]})",
            )
        # Generate a slug from business name
        slug_base = "".join(
            c.lower() if c.isalnum() else "-" for c in app_row[1]
        ).strip("-")[:60] or "seller"
        # Make slug unique by appending the app_id prefix
        slug = f"{slug_base}-{str(app_id)[:8]}"
        commission = body.commission_percent if body.commission_percent is not None else 15.00
        seller_row = await session.execute(
            _t(
                """
                INSERT INTO sellers
                  (id, business_name, slug, contact_email, contact_phone,
                   status, tin, nid, trade_license_no,
                   bank_account_name, bank_account_number,
                   bank_name, bank_branch,
                   commission_percent, payout_cadence, payout_method,
                   moderated_by, moderated_at)
                VALUES
                  (gen_random_uuid(), :name, :slug, :em, :ph,
                   'approved', :tin, :nid, :tln,
                   :ban, :bnum, :bnm, :bbr,
                   :comm, 'biweekly', 'bank_transfer',
                   :mod, now())
                RETURNING id
                """,
            ),
            {
                "name": app_row[1], "slug": slug,
                "em": app_row[2], "ph": app_row[3],
                "tin": app_row[5], "nid": app_row[4],
                "tln": app_row[6],
                "ban": app_row[7], "bnum": app_row[8],
                "bnm": app_row[9], "bbr": app_row[10],
                "comm": commission, "mod": principal.user_id,
            },
        )
        new_seller_id = seller_row.scalar_one()
        # Link applicant as owner
        await session.execute(
            _t(
                "INSERT INTO seller_users (seller_id, user_id, role) "
                "VALUES (:sid, :uid, 'owner')"
            ),
            {"sid": new_seller_id, "uid": app_row[0]},
        )
        # Finalise application
        r = await session.execute(
            _t(
                f"""
                UPDATE seller_applications SET
                    status = 'approved',
                    approved_seller_id = :sid,
                    admin_note = :note,
                    moderated_by = :mod, moderated_at = now(),
                    updated_at = now()
                WHERE id = :a
                RETURNING {_SELECT_COLS}
                """,
            ),
            {"a": app_id, "sid": new_seller_id,
             "note": body.admin_note, "mod": principal.user_id},
        )
        out_row = r.first()
        await record_audit(
            actor=principal,
            action="sellers.application.approved",
            resource_type="seller_applications",
            resource_id=app_id,
            metadata={
                "new_seller_id": str(new_seller_id),
                "applicant_user_id": str(app_row[0]),
            },
        )
    _log.info(
        "seller_application_approved",
        app_id=str(app_id),
        seller_id=str(new_seller_id),
        applicant_user_id=str(app_row[0]),
    )
    return _row_to_response(out_row)
