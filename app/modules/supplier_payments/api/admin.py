"""Admin endpoints for the supplier-payment approval engine.

Mounted at ``/admin/supplier-payments``. Permissions:
  - ``finance.read``    — list bills, view recommendations
  - ``finance.post``    — submit, level-1/2 approve, return for correction
  - ``finance.settle``  — level-3 approve, mark-ready, execute payment,
                          verify, reconcile (existing perm from Module 15)
  - ``*`` (super-admin) — level-4 approve, deactivate workflows

Per-route ``requires_permission`` reflects the minimum to call. The
service layer additionally enforces separation-of-duties (one user,
one approval level).
"""

from __future__ import annotations

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.core.db.uow import UnitOfWork, get_uow
from app.core.security.deps import get_current_principal
from app.core.security.principal import Principal
from app.core.security.rbac import requires_permission
from app.modules.finance.models import SupplierPayment
from app.modules.supplier_payments.errors import (
    ApprovalNotFoundError,
    BankAccountNotFoundError,
    BillNotFoundError,
)
from app.modules.supplier_payments.repository import (
    ApprovalWorkflowRepository,
    SupplierBankAccountRepository,
    SupplierBillApprovalRepository,
    SupplierBillApprovalStateRepository,
    SupplierPaymentRecommendationRepository,
)
from app.modules.supplier_payments.schemas import (
    ApprovalActionRequest,
    ApprovalListResponse,
    ApprovalRowResponse,
    ApprovalStateListResponse,
    ApprovalStateResponse,
    BankAccountCreateRequest,
    BankAccountListResponse,
    BankAccountResponse,
    DisputeRequest,
    DuplicateCheckRequest,
    DuplicateCheckResponse,
    ExecutePaymentRequest,
    HoldRequest,
    MarkReadyRequest,
    PaymentResponse,
    RecommendationHistoryResponse,
    RecommendationHistoryRow,
    RecommendationRecomputeRequest,
    ResumeRequest,
    SubmitRequest,
    UploadProofRequest,
    WorkflowListResponse,
    WorkflowResponse,
)
from app.modules.supplier_payments.service import (
    ApprovalEngine,
    DuplicateInvoiceCheckService,
    PaymentExecutionService,
    ReconciliationService,
    RecommendationEngineRunner,
    SupplierBankAccountService,
)

router = APIRouter(
    prefix="/admin/supplier-payments",
    tags=["admin-supplier-payments"],
)

_READ = "finance.read"
_POST = "finance.post"
_SETTLE = "finance.settle"


# ----------------------------------------------------------------------
# Approval queue + per-bill state
# ----------------------------------------------------------------------
@router.get(
    "/queue",
    response_model=ApprovalStateListResponse,
    summary="Approval queue, filterable by status",
    dependencies=[Depends(requires_permission(_READ))],
)
async def list_approval_queue(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    status: Annotated[
        str | None,
        Query(description="Comma-separated status list"),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> ApprovalStateListResponse:
    statuses = (
        [s.strip() for s in status.split(",") if s.strip()]
        if status else
        ["submitted", "level_1_verified", "level_2_approved",
         "level_3_approved", "super_admin_required"]
    )
    async with uow.transactional() as session:
        repo = SupplierBillApprovalStateRepository(session)
        rows = await repo.list_by_status(statuses, limit=limit)
    return ApprovalStateListResponse(items=[
        _state_to_response(r) for r in rows
    ])


@router.get(
    "/recommended",
    response_model=ApprovalStateListResponse,
    summary="Bills recommended for payment by ``on_or_before`` date",
    dependencies=[Depends(requires_permission(_READ))],
)
async def list_recommended(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    on_or_before: Annotated[date | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> ApprovalStateListResponse:
    target = on_or_before or date.today()
    async with uow.transactional() as session:
        repo = SupplierBillApprovalStateRepository(session)
        rows = await repo.list_recommended(on_or_before=target, limit=limit)
    return ApprovalStateListResponse(items=[
        _state_to_response(r) for r in rows
    ])


@router.get(
    "/bills/{bill_id}/state",
    response_model=ApprovalStateResponse,
    summary="Get the approval state for a bill",
    dependencies=[Depends(requires_permission(_READ))],
)
async def get_bill_state(
    bill_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> ApprovalStateResponse:
    async with uow.transactional() as session:
        repo = SupplierBillApprovalStateRepository(session)
        row = await repo.get_by_bill(bill_id)
        if row is None:
            raise BillNotFoundError("Bill has no approval state.")
    return _state_to_response(row)


@router.get(
    "/bills/{bill_id}/approvals",
    response_model=ApprovalListResponse,
    summary="Per-level approval rows for a bill",
    dependencies=[Depends(requires_permission(_READ))],
)
async def list_bill_approvals(
    bill_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> ApprovalListResponse:
    async with uow.transactional() as session:
        repo = SupplierBillApprovalRepository(session)
        rows = await repo.list_for_bill(bill_id)
    return ApprovalListResponse(items=[
        ApprovalRowResponse(
            id=r.id,
            supplier_bill_id=r.supplier_bill_id,
            level=r.level,
            approver_user_id=r.approver_user_id,
            approver_role=r.approver_role,
            decision=r.decision,
            decision_note=r.decision_note,
            decided_at=r.decided_at,
        )
        for r in rows
    ])


# ----------------------------------------------------------------------
# Workflow steps (submit / approve / reject / return / hold / resume)
# ----------------------------------------------------------------------
@router.post(
    "/bills/{bill_id}/submit",
    response_model=ApprovalStateResponse,
    summary="Submit a draft bill into the approval workflow",
    dependencies=[Depends(requires_permission(_POST))],
)
async def submit_bill(
    bill_id: UUID,
    body: SubmitRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> ApprovalStateResponse:
    async with uow.transactional() as session:
        engine = ApprovalEngine(session)
        row = await engine.submit(
            bill_id=bill_id, principal=principal, notes=body.notes,
        )
    return _state_to_response(row)


@router.post(
    "/bills/{bill_id}/approve-level-1",
    response_model=ApprovalStateResponse,
    summary="Level-1 approval (AP verification)",
    dependencies=[Depends(requires_permission(_POST))],
)
async def approve_level_1(
    bill_id: UUID,
    body: ApprovalActionRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> ApprovalStateResponse:
    return await _approve(
        bill_id=bill_id, level=1, body=body, uow=uow, principal=principal,
    )


@router.post(
    "/bills/{bill_id}/approve-level-2",
    response_model=ApprovalStateResponse,
    summary="Level-2 approval (Procurement / Ops / Warehouse)",
    dependencies=[Depends(requires_permission(_POST))],
)
async def approve_level_2(
    bill_id: UUID,
    body: ApprovalActionRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> ApprovalStateResponse:
    return await _approve(
        bill_id=bill_id, level=2, body=body, uow=uow, principal=principal,
    )


@router.post(
    "/bills/{bill_id}/approve-level-3",
    response_model=ApprovalStateResponse,
    summary="Level-3 approval (Finance final)",
    dependencies=[Depends(requires_permission(_SETTLE))],
)
async def approve_level_3(
    bill_id: UUID,
    body: ApprovalActionRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> ApprovalStateResponse:
    return await _approve(
        bill_id=bill_id, level=3, body=body, uow=uow, principal=principal,
    )


@router.post(
    "/bills/{bill_id}/approve-super-admin",
    response_model=ApprovalStateResponse,
    summary="Level-4 super-admin approval (high-value bills only)",
    dependencies=[Depends(requires_permission("*"))],
)
async def approve_super_admin(
    bill_id: UUID,
    body: ApprovalActionRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> ApprovalStateResponse:
    return await _approve(
        bill_id=bill_id, level=4, body=body, uow=uow, principal=principal,
    )


@router.post(
    "/bills/{bill_id}/reject",
    response_model=ApprovalStateResponse,
    summary="Reject a bill at a given level — terminal",
    dependencies=[Depends(requires_permission(_POST))],
)
async def reject_bill(
    bill_id: UUID,
    body: ApprovalActionRequest,
    level: Annotated[int, Query(ge=1, le=4)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> ApprovalStateResponse:
    if not body.decision_note:
        from app.core.errors import ValidationError
        raise ValidationError("decision_note is required when rejecting.")
    async with uow.transactional() as session:
        engine = ApprovalEngine(session)
        row = await engine.reject(
            bill_id=bill_id, level=level,
            principal=principal,
            approver_role=body.approver_role,
            decision_note=body.decision_note,
        )
    return _state_to_response(row)


@router.post(
    "/bills/{bill_id}/return",
    response_model=ApprovalStateResponse,
    summary="Return for correction — bill goes back to draft state",
    dependencies=[Depends(requires_permission(_POST))],
)
async def return_for_correction(
    bill_id: UUID,
    body: ApprovalActionRequest,
    level: Annotated[int, Query(ge=1, le=4)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> ApprovalStateResponse:
    if not body.decision_note:
        from app.core.errors import ValidationError
        raise ValidationError("decision_note is required when returning.")
    async with uow.transactional() as session:
        engine = ApprovalEngine(session)
        row = await engine.return_for_correction(
            bill_id=bill_id, level=level,
            principal=principal,
            approver_role=body.approver_role,
            decision_note=body.decision_note,
        )
    return _state_to_response(row)


@router.post(
    "/bills/{bill_id}/hold",
    response_model=ApprovalStateResponse,
    summary="Put a bill on hold (e.g. supplier dispute)",
    dependencies=[Depends(requires_permission(_POST))],
)
async def hold_bill(
    bill_id: UUID,
    body: HoldRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> ApprovalStateResponse:
    async with uow.transactional() as session:
        engine = ApprovalEngine(session)
        row = await engine.hold(
            bill_id=bill_id, principal=principal, reason=body.reason,
        )
    return _state_to_response(row)


@router.post(
    "/bills/{bill_id}/resume",
    response_model=ApprovalStateResponse,
    summary="Resume a held bill back into the workflow",
    dependencies=[Depends(requires_permission(_POST))],
)
async def resume_bill(
    bill_id: UUID,
    body: ResumeRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> ApprovalStateResponse:
    async with uow.transactional() as session:
        engine = ApprovalEngine(session)
        row = await engine.resume(
            bill_id=bill_id, principal=principal, new_status=body.new_status,
        )
    return _state_to_response(row)


# ----------------------------------------------------------------------
# Recommendation engine
# ----------------------------------------------------------------------
@router.post(
    "/bills/{bill_id}/recompute-recommendation",
    response_model=ApprovalStateResponse,
    summary="Run the recommendation engine for a single bill",
    dependencies=[Depends(requires_permission(_POST))],
)
async def recompute_recommendation(
    bill_id: UUID,
    body: RecommendationRecomputeRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> ApprovalStateResponse:
    async with uow.transactional() as session:
        runner = RecommendationEngineRunner(session)
        row = await runner.recompute_for_bill(
            bill_id=bill_id,
            principal=principal,
            is_critical_supplier=body.is_critical_supplier,
            has_early_pay_discount=body.has_early_pay_discount,
            has_recent_payment=body.has_recent_payment,
            cash_ready=body.cash_ready,
        )
    return _state_to_response(row)


@router.get(
    "/bills/{bill_id}/recommendation-history",
    response_model=RecommendationHistoryResponse,
    summary="Recent recommendation engine runs for a bill",
    dependencies=[Depends(requires_permission(_READ))],
)
async def recommendation_history(
    bill_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    limit: Annotated[int, Query(ge=1, le=200)] = 30,
) -> RecommendationHistoryResponse:
    async with uow.transactional() as session:
        repo = SupplierPaymentRecommendationRepository(session)
        rows = await repo.list_for_bill(bill_id, limit=limit)
    return RecommendationHistoryResponse(items=[
        RecommendationHistoryRow(
            id=r.id,
            supplier_bill_id=r.supplier_bill_id,
            recommended_payment_date=r.recommended_payment_date,
            recommended_payment_amount=r.recommended_payment_amount,
            recommended_payment_method=r.recommended_payment_method,
            priority_level=r.priority_level,
            score=r.score,
            payload=r.payload_json,
            engine_version=r.engine_version,
            generated_at=r.generated_at,
        )
        for r in rows
    ])


# ----------------------------------------------------------------------
# Mark ready + execute payment
# ----------------------------------------------------------------------
@router.post(
    "/bills/{bill_id}/mark-ready",
    response_model=ApprovalStateResponse,
    summary="Move APPROVED_FINAL bill to READY_FOR_PAYMENT (verified bank account required)",
    dependencies=[Depends(requires_permission(_SETTLE))],
)
async def mark_ready(
    bill_id: UUID,
    body: MarkReadyRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> ApprovalStateResponse:
    async with uow.transactional() as session:
        engine = ApprovalEngine(session)
        row = await engine.mark_ready_for_payment(
            bill_id=bill_id,
            bank_account_id=body.bank_account_id,
            principal=principal,
        )
    return _state_to_response(row)


@router.post(
    "/bills/{bill_id}/pay",
    response_model=PaymentResponse,
    summary="Execute payment against a READY_FOR_PAYMENT bill",
    dependencies=[Depends(requires_permission(_SETTLE))],
)
async def execute_payment(
    bill_id: UUID,
    body: ExecutePaymentRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> PaymentResponse:
    async with uow.transactional() as session:
        svc = PaymentExecutionService(session)
        payment = await svc.execute_payment(
            bill_id=bill_id,
            amount=body.amount,
            method=body.method,
            paid_on=body.paid_on,
            bank_reference=body.bank_reference,
            principal=principal,
        )
    return _payment_to_response(payment)


@router.post(
    "/payments/{payment_id}/upload-proof",
    response_model=PaymentResponse,
    summary="Upload payment proof URL (e.g. screenshot, bank slip)",
    dependencies=[Depends(requires_permission(_SETTLE))],
)
async def upload_proof(
    payment_id: UUID,
    body: UploadProofRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> PaymentResponse:
    async with uow.transactional() as session:
        svc = PaymentExecutionService(session)
        payment = await svc.upload_proof(
            payment_id=payment_id,
            proof_file_url=body.proof_file_url,
            principal=principal,
        )
    return _payment_to_response(payment)


@router.post(
    "/payments/{payment_id}/verify",
    response_model=PaymentResponse,
    summary="Finance verifies payment (cannot be the executor)",
    dependencies=[Depends(requires_permission(_SETTLE))],
)
async def verify_payment(
    payment_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> PaymentResponse:
    async with uow.transactional() as session:
        svc = ReconciliationService(session)
        payment = await svc.verify(
            payment_id=payment_id, principal=principal,
        )
    return _payment_to_response(payment)


@router.post(
    "/payments/{payment_id}/reconcile",
    response_model=PaymentResponse,
    summary="Reconcile a verified payment (bank statement matched)",
    dependencies=[Depends(requires_permission(_SETTLE))],
)
async def reconcile_payment(
    payment_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> PaymentResponse:
    async with uow.transactional() as session:
        svc = ReconciliationService(session)
        payment = await svc.reconcile(
            payment_id=payment_id, principal=principal,
        )
    return _payment_to_response(payment)


@router.post(
    "/payments/{payment_id}/dispute",
    response_model=PaymentResponse,
    summary="Mark payment disputed; bill's dispute_flag is set",
    dependencies=[Depends(requires_permission(_SETTLE))],
)
async def dispute_payment(
    payment_id: UUID,
    body: DisputeRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> PaymentResponse:
    async with uow.transactional() as session:
        svc = ReconciliationService(session)
        payment = await svc.dispute(
            payment_id=payment_id, reason=body.reason, principal=principal,
        )
    return _payment_to_response(payment)


# ----------------------------------------------------------------------
# Bank accounts
# ----------------------------------------------------------------------
@router.get(
    "/suppliers/{supplier_id}/bank-accounts",
    response_model=BankAccountListResponse,
    summary="List a supplier's bank/MFS accounts",
    dependencies=[Depends(requires_permission(_READ))],
)
async def list_bank_accounts(
    supplier_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    active_only: Annotated[bool, Query()] = True,
) -> BankAccountListResponse:
    async with uow.transactional() as session:
        repo = SupplierBankAccountRepository(session)
        rows = await repo.list_for_supplier(supplier_id, active_only=active_only)
    return BankAccountListResponse(items=[
        _bank_to_response(r) for r in rows
    ])


@router.post(
    "/bank-accounts",
    response_model=BankAccountResponse,
    status_code=201,
    summary="Add a supplier bank/MFS account (starts unverified)",
    dependencies=[Depends(requires_permission(_POST))],
)
async def add_bank_account(
    body: BankAccountCreateRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> BankAccountResponse:
    async with uow.transactional() as session:
        svc = SupplierBankAccountService(session)
        row = await svc.add_account(
            supplier_id=body.supplier_id,
            account_type=body.account_type,
            account_name=body.account_name,
            principal=principal,
            bank_name=body.bank_name,
            account_number_masked=body.account_number_masked,
            iban_or_branch=body.iban_or_branch,
            mfs_number=body.mfs_number,
            is_default=body.is_default,
        )
    return _bank_to_response(row)


@router.post(
    "/bank-accounts/{account_id}/verify",
    response_model=BankAccountResponse,
    summary="Verify a bank account (required before payment release)",
    dependencies=[Depends(requires_permission(_SETTLE))],
)
async def verify_bank_account(
    account_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> BankAccountResponse:
    async with uow.transactional() as session:
        svc = SupplierBankAccountService(session)
        row = await svc.verify_account(
            account_id=account_id, principal=principal,
        )
    return _bank_to_response(row)


@router.post(
    "/bank-accounts/{account_id}/deactivate",
    response_model=BankAccountResponse,
    summary="Deactivate a bank account (cannot be selected for new payments)",
    dependencies=[Depends(requires_permission(_POST))],
)
async def deactivate_bank_account(
    account_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> BankAccountResponse:
    async with uow.transactional() as session:
        svc = SupplierBankAccountService(session)
        row = await svc.deactivate_account(
            account_id=account_id, principal=principal,
        )
    return _bank_to_response(row)


# ----------------------------------------------------------------------
# Workflows + duplicate check
# ----------------------------------------------------------------------
@router.get(
    "/workflows",
    response_model=WorkflowListResponse,
    summary="List active approval workflows (config table)",
    dependencies=[Depends(requires_permission(_READ))],
)
async def list_workflows(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> WorkflowListResponse:
    async with uow.transactional() as session:
        repo = ApprovalWorkflowRepository(session)
        rows = await repo.list_active()
    return WorkflowListResponse(items=[
        WorkflowResponse(
            id=r.id,
            workflow_code=r.workflow_code,
            name=r.name,
            description=r.description,
            threshold_amount=r.threshold_amount,
            requires_super_admin=r.requires_super_admin,
            min_approval_steps=r.min_approval_steps,
            is_active=r.is_active,
        )
        for r in rows
    ])


@router.post(
    "/duplicate-check",
    response_model=DuplicateCheckResponse,
    summary="Pre-submit check for duplicate (supplier × invoice_number)",
    dependencies=[Depends(requires_permission(_READ))],
)
async def check_duplicate(
    body: DuplicateCheckRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> DuplicateCheckResponse:
    async with uow.transactional() as session:
        svc = DuplicateInvoiceCheckService(session)
        result = await svc.check(
            supplier_id=body.supplier_id, bill_number=body.bill_number,
        )
    return DuplicateCheckResponse(**result)


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
async def _approve(
    *,
    bill_id: UUID,
    level: int,
    body: ApprovalActionRequest,
    uow: UnitOfWork,
    principal: Principal,
) -> ApprovalStateResponse:
    async with uow.transactional() as session:
        engine = ApprovalEngine(session)
        row = await engine.approve_level(
            bill_id=bill_id, level=level,
            principal=principal,
            approver_role=body.approver_role,
            decision_note=body.decision_note,
        )
    return _state_to_response(row)


def _state_to_response(row) -> ApprovalStateResponse:
    return ApprovalStateResponse(
        id=row.id,
        supplier_bill_id=row.supplier_bill_id,
        approval_status=row.approval_status,
        workflow_code=row.workflow_code,
        recommended_payment_date=row.recommended_payment_date,
        recommended_payment_method=row.recommended_payment_method,
        recommended_payment_amount=row.recommended_payment_amount,
        recommendation_score=row.recommendation_score,
        payment_priority=row.payment_priority,
        last_recommended_at=row.last_recommended_at,
        dispute_flag=row.dispute_flag,
        dispute_reason=row.dispute_reason,
        duplicate_check_flag=row.duplicate_check_flag,
        duplicate_of_bill_id=row.duplicate_of_bill_id,
        selected_bank_account_id=row.selected_bank_account_id,
        submitted_at=row.submitted_at,
        final_approved_at=row.final_approved_at,
    )


def _payment_to_response(p) -> PaymentResponse:
    return PaymentResponse(
        id=p.id,
        bill_id=p.bill_id,
        code=p.code,
        paid_on=p.paid_on,
        amount=p.amount,
        method=p.method,
        bank_reference=p.bank_reference,
        verification_status=p.verification_status,
        proof_file_url=p.proof_file_url,
        executed_by=p.executed_by,
        verified_by=p.verified_by,
        verified_at=p.verified_at,
        bank_account_id=p.bank_account_id,
    )


def _bank_to_response(b) -> BankAccountResponse:
    return BankAccountResponse(
        id=b.id,
        supplier_id=b.supplier_id,
        account_type=b.account_type,
        account_name=b.account_name,
        bank_name=b.bank_name,
        account_number_masked=b.account_number_masked,
        iban_or_branch=b.iban_or_branch,
        mfs_number=b.mfs_number,
        is_default=b.is_default,
        is_verified=b.is_verified,
        verified_at=b.verified_at,
        is_active=b.is_active,
    )
