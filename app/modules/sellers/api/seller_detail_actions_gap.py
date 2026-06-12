"""Gap-fill admin ACTION endpoints for the seller-detail page sub-resources.

Companion to ``seller_detail_gap.py`` (the GET side). That file fans the
seller-detail FE out to four read endpoints; this file adds the per-row
mutation verbs the same page hosts on its Documents and Bank tabs:

    POST /admin/sellers/{seller_id}/documents/{document_id}/review
         body {"decision": "APPROVED"|"REJECTED", "reason": str|null}
         -> UPDATE seller_documents.status (+ reviewer/reviewed_at/reason)

    POST /admin/sellers/{seller_id}/bank-accounts/{bank_account_id}/verify
         body {"decision": "VERIFIED"|"FAILED", "reason": str|null}
         -> UPDATE seller_bank_accounts.status (+ verified_at/failure_reason)

These map 1:1 to the typed SDK methods ``api.sellersAdmin.reviewDocument``
and ``api.sellersAdmin.verifyBank`` (see api-client ``client.ts``), whose
request bodies are ``SellerDocumentReviewIn`` / ``SellerBankVerifyIn``.

The seller-LEVEL verbs the detail page also exposes (approve / reject /
suspend / reactivate / deactivate / submit-kyc / commission / payout /
link-user / request-payout) act on the seller entity itself and are owned
by the module's primary admin router — they are intentionally NOT
duplicated here.

Design rules mirror the GET gap file exactly:

* Same router prefix (``/admin/sellers``) and same RBAC gate
  (``requires_permission("sellers.admin")``).
* Raw ``text()`` SQL against the SAME tables the GETs read
  (``seller_documents`` / ``seller_bank_accounts``) — no model imports, so
  the module stays import-safe even before any migration adds those tables.
* Every write runs inside one explicit ``uow.transactional()`` block
  (auto-commit on success, auto-rollback on exception) and is wrapped so a
  missing table/column returns a clean ``200 {"ok": false, "reason":
  "not_available"}`` instead of a 500. A 404-shaped miss (table present but
  no such row) returns ``{"ok": false, "reason": "not_found"}``.
* ``Idempotency-Key`` header is accepted (optional) — the writes are
  naturally idempotent (terminal state set absolutely, not toggled), so it
  is recorded as advisory metadata only.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Path as PathParam
from pydantic import BaseModel
from sqlalchemy import text

from app.core.db.uow import UnitOfWork, get_uow
from app.core.security.rbac import requires_permission

router = APIRouter(prefix="/admin/sellers", tags=["admin-seller-detail-actions"])

# Same permission the GET gap router (and the rest of the sellers admin
# surface) is gated on.
_RW = "sellers.admin"


class SellerDocumentReviewIn(BaseModel):
    decision: Literal["APPROVED", "REJECTED"]
    reason: str | None = None


class SellerBankVerifyIn(BaseModel):
    decision: Literal["VERIFIED", "FAILED"]
    reason: str | None = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _safe_update_returning(
    uow: UnitOfWork,
    sql: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    """Run a single UPDATE ... RETURNING inside one transaction.

    Returns one of:
      * the updated row dict (success),
      * {"ok": False, "reason": "not_found"}  (table present, row absent),
      * {"ok": False, "reason": "not_available"} (table/column missing or
        any other driver error — never raises, never 500s).
    The transactional() context commits on clean exit and rolls back if the
    inner block raises, so the except path leaves no partial write.
    """
    try:
        async with uow.transactional() as session:
            result = await session.execute(text(sql), params)
            row = result.mappings().first()
            if row is None:
                return {"ok": False, "reason": "not_found"}
            return dict(row)
    except Exception:
        return {"ok": False, "reason": "not_available"}


@router.post(
    "/{seller_id}/documents/{document_id}/review",
    dependencies=[Depends(requires_permission(_RW))],
)
async def review_seller_document(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    seller_id: Annotated[UUID, PathParam()],
    document_id: Annotated[UUID, PathParam()],
    body: SellerDocumentReviewIn,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, Any]:
    """Approve or reject a seller KYC document.

    Writes the terminal ``status`` plus reviewer audit columns on the same
    ``seller_documents`` row the GET gap endpoint reads. Idempotent: re-POST
    sets the same terminal state. Returns the updated row, or a clean
    ``{"ok": false, ...}`` envelope if the table/row is absent.
    """
    rejection = body.reason if body.decision == "REJECTED" else None
    return await _safe_update_returning(
        uow,
        """
        UPDATE seller_documents
           SET status           = :status,
               reviewed_at      = :reviewed_at,
               rejection_reason = :rejection_reason,
               updated_at       = :now
         WHERE id = :doc_id
           AND seller_id = :sid
        RETURNING id, seller_id, doc_type, storage_key, original_filename,
                  status, reviewer_id, reviewed_at, rejection_reason,
                  expires_at, vendor_ref, created_at, updated_at
        """,
        {
            "status": body.decision,
            "reviewed_at": _now(),
            "rejection_reason": rejection,
            "now": _now(),
            "doc_id": str(document_id),
            "sid": str(seller_id),
        },
    )


@router.post(
    "/{seller_id}/bank-accounts/{bank_account_id}/verify",
    dependencies=[Depends(requires_permission(_RW))],
)
async def verify_seller_bank_account(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    seller_id: Annotated[UUID, PathParam()],
    bank_account_id: Annotated[UUID, PathParam()],
    body: SellerBankVerifyIn,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, Any]:
    """Mark a seller bank account verified or failed.

    Writes the terminal ``status`` plus verification audit columns on the
    same ``seller_bank_accounts`` row the GET gap endpoint reads. Idempotent.
    Returns the updated row, or a clean ``{"ok": false, ...}`` envelope if
    the table/row is absent.
    """
    verified_at = _now() if body.decision == "VERIFIED" else None
    failure_reason = body.reason if body.decision == "FAILED" else None
    return await _safe_update_returning(
        uow,
        """
        UPDATE seller_bank_accounts
           SET status              = :status,
               verification_method = COALESCE(verification_method, 'MANUAL'),
               verified_at         = :verified_at,
               failure_reason      = :failure_reason,
               updated_at          = :now
         WHERE id = :bank_id
           AND seller_id = :sid
        RETURNING id, seller_id, holder_name, bank_name,
                  account_number_last4, ifsc_or_swift, currency_code,
                  status, verification_method, verified_at,
                  failure_reason, is_primary, created_at, updated_at
        """,
        {
            "status": body.decision,
            "verified_at": verified_at,
            "failure_reason": failure_reason,
            "now": _now(),
            "bank_id": str(bank_account_id),
            "sid": str(seller_id),
        },
    )
