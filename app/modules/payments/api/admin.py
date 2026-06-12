"""Admin payment endpoints — list, inspect, refund."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.core.db.uow import UnitOfWork, get_uow
from app.core.pagination import Page, PageParams
from app.core.security.deps import get_current_principal
from app.core.security.principal import Principal
from app.core.security.rbac import requires_permission
from app.modules.payments.api._serializers import (
    intent_to_response,
    refund_to_response,
)
from app.modules.payments.repository import PaymentIntentRepository
from app.modules.payments.schemas import (
    PaymentIntentResponse,
    ReconcileResponse,
    RefundRequest,
    RefundResponse,
    SettlementMatchResponse,
    SettlementReconcileRequest,
    SettlementReportResponse,
)
from app.modules.payments.service import (
    PaymentService,
    SettlementReconciliationService,
)

router = APIRouter(prefix="/admin/payments", tags=["admin-payments"])

# Split 2026-05-16 (later session): admin reads use the broader
# `payments.view` (4 roles: admin/manager/supervisor/finance_manager) so
# day-to-day ops + audit oversight can inspect intents without holding
# refund authority. Refund mutation stays on `finance.adjust` (2 roles:
# admin + finance_manager) — issuing a refund moves money.
_READ = "payments.view"
_REFUND = "finance.adjust"


@router.get(
    "/intents",
    response_model=Page[PaymentIntentResponse],
    summary="Admin list of payment intents (newest first)",
    dependencies=[Depends(requires_permission(_READ))],
)
async def list_intents(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    page: int = Query(default=1, ge=1, le=10_000),
    size: int = Query(default=50, ge=1, le=200),
    status_filter: str | None = Query(default=None, alias="status"),
    provider: str | None = Query(default=None),
) -> Page[PaymentIntentResponse]:
    params = PageParams(page=page, size=size)
    async with uow.transactional() as session:
        repo = PaymentIntentRepository(session)
        rows, total = await repo.list_for_admin(
            status=status_filter,
            provider=provider,
            offset=params.offset,
            limit=params.limit,
        )
    return Page.build(
        items=[intent_to_response(r) for r in rows],
        total=total,
        params=params,
    )


@router.get(
    "/intents/{intent_id}",
    response_model=PaymentIntentResponse,
    dependencies=[Depends(requires_permission(_READ))],
)
async def get_intent(
    intent_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> PaymentIntentResponse:
    async with uow.transactional() as session:
        svc = PaymentService(session)
        intent = await svc.get_intent_admin(intent_id)
    return intent_to_response(intent)


@router.post(
    "/refund",
    response_model=RefundResponse,
    summary="Issue a refund against a captured intent",
    description=(
        "Calls the originating gateway's refund API. Partial refunds "
        "allowed up to the un-refunded captured balance. On success, "
        "emits ``payment.refund.succeeded`` to the outbox so finance "
        "can post the refund journal entry. On failure, returns the "
        "PaymentRefund row with status='failed' and error_code/message — "
        "no exception, so the operator UI can render the error."
    ),
    dependencies=[Depends(requires_permission(_REFUND))],
)
async def refund(
    payload: RefundRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> RefundResponse:
    async with uow.transactional() as session:
        svc = PaymentService(session)
        refund_row = await svc.refund(
            principal=principal,
            intent_id=payload.intent_id,
            amount=payload.amount,
            reason=payload.reason,
            return_request_id=payload.return_request_id,
        )
    return refund_to_response(refund_row)


# Permission for the gateway-settlement reconciliation endpoint.
# Distinct from `_REFUND` (`finance.adjust`, which gates refunds) — the
# reconcile sweep is its own verb in finance ops + held by finance_manager
# without granting refund authority.
_RECONCILE = "payments.reconcile"


def _report_to_response(r) -> SettlementReportResponse:
    return SettlementReportResponse(
        id=r.id,
        provider=r.provider,
        report_date=r.report_date,
        currency=r.currency,
        submitted_total=r.submitted_total,
        submitted_count=r.submitted_count,
        status=r.status,
        matched_count=r.matched_count,
        drift_count=r.drift_count,
        orphan_count=r.orphan_count,
        drift_total=r.drift_total,
        processed_at=r.processed_at,
        created_at=r.created_at,
    )


def _match_to_response(m) -> SettlementMatchResponse:
    return SettlementMatchResponse(
        id=m.id,
        payment_intent_id=m.payment_intent_id,
        provider_ref=m.provider_ref,
        provider_amount=m.provider_amount,
        intent_amount=m.intent_amount,
        drift_amount=m.drift_amount,
        status=m.status,
    )


@router.post(
    "/reconcile",
    response_model=ReconcileResponse,
    summary="Run a gateway-settlement reconciliation sweep.",
    description=(
        "Match a provider's daily settlement report against captured "
        "payment intents. Each line is matched by provider_ref against "
        "``PaymentIntent.provider_payment_id``; amounts within 1 unit "
        "of the report currency are recorded as ``matched``, larger "
        "deltas as ``drift``, and refs we can't find as ``orphan``. "
        "On success, emits the ``payment.reconciliation.completed`` "
        "outbox event so the finance worker can post the drift "
        "summary as a JournalEntry. Idempotent on (provider, "
        "report_date) — a second submission for the same business day "
        "returns 409. Gated 2026-05-16 on the new `payments.reconcile` "
        "perm (admin + finance_manager)."
    ),
    dependencies=[Depends(requires_permission(_RECONCILE))],
)
async def reconcile_settlements(
    payload: SettlementReconcileRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> ReconcileResponse:
    async with uow.transactional() as session:
        svc = SettlementReconciliationService(session)
        report, matches = await svc.reconcile(
            principal=principal,
            provider=payload.provider,
            report_date=payload.report_date,
            currency=payload.currency,
            lines=payload.lines,
        )
    return ReconcileResponse(
        report=_report_to_response(report),
        matches=[_match_to_response(m) for m in matches],
    )
