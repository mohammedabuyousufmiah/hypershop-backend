"""Top-level orchestration for the supplier-payment approval engine.

One file, four logical services co-located because they share the
approval state heavily:

  ApprovalEngine          — submit / approve-level-N / reject / return /
                            hold / resume. Enforces separation of duties.
  RecommendationEngineRunner — wraps the pure recommendation.recommend()
                            with DB I/O and persistence.
  PaymentExecutionService  — mark-ready / execute-payment / upload-proof
                            (uses existing finance.SupplierPayment row).
  ReconciliationService    — verify / reconcile / dispute on the
                            payment row.
  SupplierBankAccountService — verified payout destinations.
  DuplicateInvoiceCheckService — pre-submit + post-submit checks.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_audit
from app.core.errors import BusinessRuleError, ValidationError
from app.core.logging import get_logger
from app.core.security.principal import Principal, SystemPrincipal
from app.core.time import utc_now
from app.modules.finance.models import SupplierBill, SupplierPayment
from app.modules.supplier_payments import codes, recommendation
from app.modules.supplier_payments.errors import (
    ApprovalNotFoundError,
    BankAccountNotFoundError,
    BillApprovalStateNotFoundError,
    BillNotApprovedForPaymentError,
    BillNotFoundError,
    BillOnHoldError,
    DuplicateInvoiceError,
    IllegalApprovalTransitionError,
    SeparationOfDutiesError,
    SupplierBankAccountUnverifiedError,
    WorkflowNotFoundError,
    WrongApprovalLevelError,
)
from app.modules.supplier_payments.recommendation import BillFacts
from app.modules.supplier_payments.repository import (
    ApprovalWorkflowRepository,
    SupplierBankAccountRepository,
    SupplierBillApprovalRepository,
    SupplierBillApprovalStateRepository,
    SupplierPaymentRecommendationRepository,
    find_duplicate_bill,
    get_bill_or_none,
)
from app.modules.supplier_payments.state import (
    ApprovalDecision,
    ApprovalLevel,
    APPROVAL_LOCKED_STATUSES,
    BillApprovalStatus,
    PaymentPriority,
    PaymentVerificationStatus,
    SupplierBankAccountType,
)

_log = get_logger("hypershop.supplier_payments.service")


# Standard approval flow per status. Used by `_assert_can_advance` to
# validate that an attempted level-N approval is the next step.
_NEXT_AFTER_LEVEL = {
    1: BillApprovalStatus.LEVEL_1_VERIFIED.value,
    2: BillApprovalStatus.LEVEL_2_APPROVED.value,
    3: BillApprovalStatus.LEVEL_3_APPROVED.value,
    4: BillApprovalStatus.APPROVED_FINAL.value,
}
_VALID_FROM_LEVEL = {
    1: {BillApprovalStatus.SUBMITTED.value},
    2: {BillApprovalStatus.LEVEL_1_VERIFIED.value},
    3: {BillApprovalStatus.LEVEL_2_APPROVED.value},
    4: {
        BillApprovalStatus.LEVEL_3_APPROVED.value,
        BillApprovalStatus.SUPER_ADMIN_REQUIRED.value,
    },
}


# ============================================================
#  ApprovalEngine
# ============================================================
class ApprovalEngine:
    """Drives a SupplierBill through the approval workflow."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.states = SupplierBillApprovalStateRepository(session)
        self.approvals = SupplierBillApprovalRepository(session)
        self.workflows = ApprovalWorkflowRepository(session)

    # ------------------------------------------------------------------
    # Submit (move DRAFT → SUBMITTED + assign workflow)
    # ------------------------------------------------------------------
    async def submit(
        self, *,
        bill_id: UUID,
        principal: Principal,
        notes: str | None = None,
    ):
        bill = await get_bill_or_none(self.session, bill_id)
        if bill is None:
            raise BillNotFoundError("Supplier bill not found.")

        # Ensure approval state exists.
        state = await self.states.get_by_bill(bill_id)
        if state is None:
            state = await self.states.add(supplier_bill_id=bill_id)
        if state.approval_status not in (
            BillApprovalStatus.DRAFT.value,
            BillApprovalStatus.RETURNED_FOR_CORRECTION.value,
        ):
            raise IllegalApprovalTransitionError(
                f"Bill is in {state.approval_status}, cannot submit.",
            )

        # Resubmit-after-return: wipe stale approval rows so the new
        # cycle isn't blocked by UNIQUE(bill_id, level) on prior
        # decisions. audit_log retains the history of those decisions.
        if state.approval_status == BillApprovalStatus.RETURNED_FOR_CORRECTION.value:
            cleared = await self.approvals.clear_for_bill(bill_id)
            if cleared > 0:
                _log.info(
                    "supplier_bill_resubmit_cleared_approvals",
                    bill_id=str(bill_id),
                    cleared_rows=cleared,
                )

        # Pick the workflow that fits this bill's grand_total.
        workflow = await self._pick_workflow(grand_total=bill.grand_total)

        # Soft duplicate-check at submit time (the DB UNIQUE prevents
        # an actual dup; this just sets the flag for the UI).
        dup = await find_duplicate_bill(
            self.session,
            supplier_id=bill.supplier_id,
            bill_number=bill.bill_number,
            exclude_bill_id=bill_id,
        )

        await self.states.update(
            state_id=state.id,
            approval_status=BillApprovalStatus.SUBMITTED.value,
            workflow_code=workflow.workflow_code,
            submitted_at=utc_now(),
            submitted_by=principal.user_id,
            duplicate_check_flag=dup is not None,
            duplicate_of_bill_id=dup.id if dup is not None else None,
        )
        await record_audit(
            actor=principal,
            action=codes.ACTION_BILL_SUBMITTED,
            resource_type="supplier_bill",
            resource_id=bill_id,
            metadata={
                "workflow_code": workflow.workflow_code,
                "duplicate_suspect": dup is not None,
                "notes": notes,
            },
        )
        return await self.states.get_by_bill(bill_id)

    async def _pick_workflow(self, *, grand_total: Decimal):
        """Find the smallest qualifying high-value workflow, else
        fall back to the standard one.
        """
        flows = list(await self.workflows.list_active())
        if not flows:
            raise WorkflowNotFoundError(
                "No active approval workflows configured. "
                "Run workflow_seed.seed_default_workflows.",
            )
        # First pass: pick the smallest threshold_amount that's still
        # <= grand_total (high-value bills bind the matching threshold
        # workflow). list_active returns NULL-threshold first then
        # ascending threshold.
        chosen = flows[0]  # default = first non-threshold workflow
        for f in flows:
            if f.threshold_amount is not None and grand_total >= f.threshold_amount:
                chosen = f
        return chosen

    # ------------------------------------------------------------------
    # Approve / reject / return  for a specific level
    # ------------------------------------------------------------------
    async def approve_level(
        self, *,
        bill_id: UUID,
        level: int,
        principal: Principal,
        approver_role: str,
        decision_note: str | None = None,
    ):
        return await self._record_decision(
            bill_id=bill_id,
            level=level,
            decision=ApprovalDecision.APPROVED,
            principal=principal,
            approver_role=approver_role,
            decision_note=decision_note,
        )

    async def reject(
        self, *,
        bill_id: UUID,
        level: int,
        principal: Principal,
        approver_role: str,
        decision_note: str,
    ):
        return await self._record_decision(
            bill_id=bill_id,
            level=level,
            decision=ApprovalDecision.REJECTED,
            principal=principal,
            approver_role=approver_role,
            decision_note=decision_note,
        )

    async def return_for_correction(
        self, *,
        bill_id: UUID,
        level: int,
        principal: Principal,
        approver_role: str,
        decision_note: str,
    ):
        return await self._record_decision(
            bill_id=bill_id,
            level=level,
            decision=ApprovalDecision.RETURNED_FOR_CORRECTION,
            principal=principal,
            approver_role=approver_role,
            decision_note=decision_note,
        )

    async def hold(
        self, *,
        bill_id: UUID,
        principal: Principal,
        reason: str,
    ):
        state = await self.states.get_by_bill(bill_id)
        if state is None:
            raise BillApprovalStateNotFoundError("Approval state not found.")
        if state.approval_status in (
            BillApprovalStatus.PAID.value,
            BillApprovalStatus.RECONCILED.value,
            BillApprovalStatus.REJECTED.value,
        ):
            raise IllegalApprovalTransitionError(
                f"Cannot hold a bill in {state.approval_status}.",
            )
        await self.states.update(
            state_id=state.id,
            approval_status=BillApprovalStatus.ON_HOLD.value,
            payment_priority=PaymentPriority.ON_HOLD.value,
        )
        await record_audit(
            actor=principal,
            action=codes.ACTION_BILL_HELD,
            resource_type="supplier_bill",
            resource_id=bill_id,
            metadata={"reason": reason},
        )
        return await self.states.get_by_bill(bill_id)

    async def resume(
        self, *,
        bill_id: UUID,
        principal: Principal,
        new_status: str = BillApprovalStatus.SUBMITTED.value,
    ):
        state = await self.states.get_by_bill(bill_id)
        if state is None:
            raise BillApprovalStateNotFoundError("Approval state not found.")
        if state.approval_status != BillApprovalStatus.ON_HOLD.value:
            raise IllegalApprovalTransitionError(
                "Bill is not on hold.",
            )
        await self.states.update(
            state_id=state.id,
            approval_status=new_status,
            payment_priority=PaymentPriority.NORMAL.value,
        )
        await record_audit(
            actor=principal,
            action=codes.ACTION_BILL_RESUMED,
            resource_type="supplier_bill",
            resource_id=bill_id,
            metadata={"resumed_to": new_status},
        )
        return await self.states.get_by_bill(bill_id)

    # ------------------------------------------------------------------
    # Decision recording — the heart of the engine
    # ------------------------------------------------------------------
    async def _record_decision(
        self, *,
        bill_id: UUID,
        level: int,
        decision: ApprovalDecision,
        principal: Principal,
        approver_role: str,
        decision_note: str | None,
    ):
        if level not in ApprovalLevel.ALL:
            raise ValidationError(
                f"Invalid approval level {level}; allowed: 1..4.",
            )
        state = await self.states.get_by_bill(bill_id)
        if state is None:
            raise BillApprovalStateNotFoundError(
                "Bill has no approval state. Submit it first.",
            )
        if state.approval_status == BillApprovalStatus.ON_HOLD.value:
            raise BillOnHoldError(
                "Bill is on hold; resume before recording decisions.",
            )

        # Separation of duties — closes two paths to the same user
        # acting twice on one bill:
        #   1. The submitter can't also be a level-1/2/3/4 approver.
        #      submission doesn't write to ``supplier_bill_approvals``,
        #      so the prior-approval check below wouldn't catch this
        #      on its own — we must check ``state.submitted_by`` too.
        #   2. The same user can't approve two different levels (e.g.
        #      level-1 then level-2). Check the structured table.
        if state.submitted_by is not None and state.submitted_by == principal.user_id:
            raise SeparationOfDutiesError(
                "You submitted this bill; you cannot also approve it.",
            )
        already = await self.approvals.has_user_approved_any_level(
            bill_id=bill_id, user_id=principal.user_id,
        )
        if already:
            raise SeparationOfDutiesError(
                "You have already acted on this bill at another level.",
            )

        # Level must be the *next* expected one.
        valid_from = _VALID_FROM_LEVEL.get(level, set())
        if state.approval_status not in valid_from:
            raise WrongApprovalLevelError(
                f"Bill is in {state.approval_status}; level-{level} "
                f"requires status in {sorted(valid_from)}.",
            )

        # Persist the decision row.
        existing_row = await self.approvals.get_for_level(
            bill_id=bill_id, level=level,
        )
        if existing_row is not None:
            raise IllegalApprovalTransitionError(
                f"Level-{level} already decided for this bill.",
            )
        await self.approvals.add(
            supplier_bill_id=bill_id,
            level=level,
            approver_user_id=principal.user_id,
            approver_role=approver_role,
            decision=decision.value,
            decision_note=decision_note,
        )

        # Compute new state.
        if decision == ApprovalDecision.REJECTED:
            new_status = BillApprovalStatus.REJECTED.value
        elif decision == ApprovalDecision.RETURNED_FOR_CORRECTION:
            new_status = BillApprovalStatus.RETURNED_FOR_CORRECTION.value
        else:  # APPROVED
            new_status = _NEXT_AFTER_LEVEL[level]
            # If level 3 cleared but workflow demands lvl 4 → SUPER_ADMIN_REQUIRED
            if level == 3:
                workflow = await self.workflows.get_by_code(
                    state.workflow_code or "standard",
                )
                if workflow is not None and workflow.requires_super_admin:
                    new_status = BillApprovalStatus.SUPER_ADMIN_REQUIRED.value

        update_fields: dict[str, Any] = {"approval_status": new_status}
        if new_status == BillApprovalStatus.APPROVED_FINAL.value:
            update_fields["final_approved_at"] = utc_now()
        await self.states.update(state_id=state.id, **update_fields)

        action_map = {
            (ApprovalDecision.APPROVED, 1): codes.ACTION_LEVEL_1_APPROVED,
            (ApprovalDecision.APPROVED, 2): codes.ACTION_LEVEL_2_APPROVED,
            (ApprovalDecision.APPROVED, 3): codes.ACTION_LEVEL_3_APPROVED,
            (ApprovalDecision.APPROVED, 4): codes.ACTION_SUPER_ADMIN_APPROVED,
            (ApprovalDecision.REJECTED, 1): codes.ACTION_APPROVAL_REJECTED,
            (ApprovalDecision.REJECTED, 2): codes.ACTION_APPROVAL_REJECTED,
            (ApprovalDecision.REJECTED, 3): codes.ACTION_APPROVAL_REJECTED,
            (ApprovalDecision.REJECTED, 4): codes.ACTION_APPROVAL_REJECTED,
            (ApprovalDecision.RETURNED_FOR_CORRECTION, 1): codes.ACTION_BILL_RETURNED,
            (ApprovalDecision.RETURNED_FOR_CORRECTION, 2): codes.ACTION_BILL_RETURNED,
            (ApprovalDecision.RETURNED_FOR_CORRECTION, 3): codes.ACTION_BILL_RETURNED,
            (ApprovalDecision.RETURNED_FOR_CORRECTION, 4): codes.ACTION_BILL_RETURNED,
        }
        await record_audit(
            actor=principal,
            action=action_map.get(
                (decision, level), codes.ACTION_LEVEL_1_APPROVED,
            ),
            resource_type="supplier_bill",
            resource_id=bill_id,
            metadata={
                "level": level,
                "decision": decision.value,
                "approver_role": approver_role,
                "new_status": new_status,
                "note": decision_note,
            },
        )

        # Outbox notify the right people. We resolve the bill row + the
        # supplier name lazily so payload carries enough context for
        # the email handler to render without re-querying.
        from app.core.events.outbox import enqueue_outbox
        from app.modules.supplier_payments.events import (
            EVT_APPROVAL_NEEDED,
            EVT_BILL_FULLY_APPROVED,
            EVT_BILL_REJECTED,
            EVT_BILL_RETURNED,
        )
        bill = await get_bill_or_none(self.session, bill_id)
        from sqlalchemy import select as sa_select
        from app.modules.inventory.models import Supplier as _Supplier
        supplier_label = str(bill.supplier_id) if bill else "unknown"
        if bill is not None:
            row = (
                await self.session.execute(
                    sa_select(_Supplier.name)
                    .where(_Supplier.id == bill.supplier_id),
                )
            ).first()
            if row:
                supplier_label = row[0]

        common = {
            "bill_id": str(bill_id),
            "bill_code": bill.code if bill else "",
            "supplier_id": str(bill.supplier_id) if bill else "",
            "supplier_name": supplier_label,
            "grand_total": str(bill.grand_total) if bill else "0",
            "currency": bill.currency if bill else "BDT",
            "workflow_code": state.workflow_code,
            "decided_by_user_id": str(principal.user_id),
            "decision": decision.value,
            "level": level,
            "note": decision_note or "",
        }
        if decision == ApprovalDecision.APPROVED:
            if new_status == BillApprovalStatus.APPROVED_FINAL.value:
                # All gates cleared → finance can mark-ready + pay.
                await enqueue_outbox(
                    type=EVT_BILL_FULLY_APPROVED, payload=common,
                )
            else:
                # Hand off to the next-level approver.
                next_level = level + 1
                await enqueue_outbox(
                    type=EVT_APPROVAL_NEEDED,
                    payload={**common, "next_level": next_level},
                )
        elif decision == ApprovalDecision.REJECTED:
            await enqueue_outbox(
                type=EVT_BILL_REJECTED, payload=common,
            )
        elif decision == ApprovalDecision.RETURNED_FOR_CORRECTION:
            await enqueue_outbox(
                type=EVT_BILL_RETURNED, payload=common,
            )
        return await self.states.get_by_bill(bill_id)

    # ------------------------------------------------------------------
    # Mark ready for payment
    # ------------------------------------------------------------------
    async def mark_ready_for_payment(
        self, *,
        bill_id: UUID,
        bank_account_id: UUID,
        principal: Principal,
    ):
        state = await self.states.get_by_bill(bill_id)
        if state is None:
            raise BillApprovalStateNotFoundError("Approval state not found.")
        if state.approval_status != BillApprovalStatus.APPROVED_FINAL.value:
            raise BillNotApprovedForPaymentError(
                f"Bill must be APPROVED_FINAL to mark ready; current "
                f"status: {state.approval_status}.",
            )
        # Load bill so we can cross-check the bank account's supplier.
        bill = await get_bill_or_none(self.session, bill_id)
        if bill is None:
            raise BillNotFoundError("Supplier bill not found.")

        bank_repo = SupplierBankAccountRepository(self.session)
        account = await bank_repo.get(bank_account_id)
        if account is None or not account.is_active:
            raise BankAccountNotFoundError(
                "Bank account not found or inactive.",
            )
        # CRITICAL: the chosen bank account MUST belong to the bill's
        # supplier. Without this, an admin could attach Supplier B's
        # account to Supplier A's bill and route the money wrong.
        if account.supplier_id != bill.supplier_id:
            raise ValidationError(
                f"Bank account belongs to supplier {account.supplier_id}, "
                f"not the bill's supplier {bill.supplier_id}.",
            )
        # Cash-type accounts don't need verification (handed in person).
        if account.account_type != SupplierBankAccountType.CASH.value and not account.is_verified:
            raise SupplierBankAccountUnverifiedError(
                "Bank account must be verified before payment can be released.",
            )
        await self.states.update(
            state_id=state.id,
            approval_status=BillApprovalStatus.READY_FOR_PAYMENT.value,
            selected_bank_account_id=bank_account_id,
        )
        await record_audit(
            actor=principal,
            action=codes.ACTION_BILL_MARKED_READY,
            resource_type="supplier_bill",
            resource_id=bill_id,
            metadata={"bank_account_id": str(bank_account_id)},
        )
        return await self.states.get_by_bill(bill_id)


# ============================================================
#  RecommendationEngineRunner
# ============================================================
class RecommendationEngineRunner:
    """Orchestrates a recommendation run for a bill: pulls facts, calls
    the pure engine, persists the result + state mirror.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.states = SupplierBillApprovalStateRepository(session)
        self.history = SupplierPaymentRecommendationRepository(session)

    async def recompute_for_bill(
        self,
        *,
        bill_id: UUID,
        principal: Principal | SystemPrincipal,
        is_critical_supplier: bool = False,
        has_early_pay_discount: bool = False,
        has_recent_payment: bool = False,
        cash_ready: bool = True,
    ):
        bill = await get_bill_or_none(self.session, bill_id)
        if bill is None:
            raise BillNotFoundError("Bill not found.")
        state = await self.states.get_by_bill(bill_id)
        # Don't recompute for already-paid bills.
        if state is not None and state.approval_status in (
            BillApprovalStatus.PAID.value,
            BillApprovalStatus.RECONCILED.value,
            BillApprovalStatus.REJECTED.value,
        ):
            return state

        facts = BillFacts(
            bill_id=str(bill.id),
            grand_total=bill.grand_total - bill.amount_paid,
            due_date=bill.due_date,
            bill_date=bill.bill_date,
            is_disputed=bool(state and state.dispute_flag),
            is_duplicate_suspect=bool(state and state.duplicate_check_flag),
            is_on_hold=bool(
                state and state.approval_status ==
                BillApprovalStatus.ON_HOLD.value,
            ),
            is_critical_supplier=is_critical_supplier,
            has_early_pay_discount=has_early_pay_discount,
            has_recent_payment=has_recent_payment,
            cash_ready=cash_ready,
        )
        rec = recommendation.recommend(facts)

        # Persist history row.
        await self.history.add(
            supplier_bill_id=bill_id,
            recommended_payment_date=rec.recommended_date,
            recommended_payment_amount=rec.recommended_amount,
            recommended_payment_method=rec.recommended_method,
            priority_level=rec.priority,
            score=rec.score,
            payload_json={"factors": rec.factors},
            engine_version=recommendation.ENGINE_VERSION,
        )

        # Mirror to state for fast list rendering.
        if state is None:
            state = await self.states.add(supplier_bill_id=bill_id)
        await self.states.update(
            state_id=state.id,
            recommended_payment_date=rec.recommended_date,
            recommended_payment_amount=rec.recommended_amount,
            recommended_payment_method=rec.recommended_method,
            recommendation_score=rec.score,
            payment_priority=rec.priority,
            last_recommended_at=utc_now(),
        )
        await record_audit(
            actor=principal,
            action=codes.ACTION_RECOMMENDATION_GENERATED,
            resource_type="supplier_bill",
            resource_id=bill_id,
            metadata={
                "score": rec.score,
                "priority": rec.priority,
                "date": rec.recommended_date.isoformat(),
            },
        )
        return await self.states.get_by_bill(bill_id)


# ============================================================
#  PaymentExecutionService
# ============================================================
class PaymentExecutionService:
    """Wraps the existing FinanceService.pay_supplier_bill flow with
    the approval-state checks + payment proof + verification
    metadata writes.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.states = SupplierBillApprovalStateRepository(session)

    async def execute_payment(
        self, *,
        bill_id: UUID,
        amount: Decimal,
        method: str,
        paid_on: date,
        bank_reference: str | None,
        principal: Principal,
    ):
        """Executes the actual payment via FinanceService.

        Wraps the existing journal-entry write path so we don't
        duplicate JE bookkeeping. Augments the resulting
        fin_supplier_payments row with executed_by + bank_account_id
        + verification_status='pending'.
        """
        from app.modules.finance.service import FinanceService

        state = await self.states.get_by_bill(bill_id)
        if state is None:
            raise BillApprovalStateNotFoundError("Approval state not found.")
        if state.approval_status != BillApprovalStatus.READY_FOR_PAYMENT.value:
            raise BillNotApprovedForPaymentError(
                f"Bill must be READY_FOR_PAYMENT; current: "
                f"{state.approval_status}.",
            )

        finance = FinanceService(self.session)
        payment = await finance.pay_supplier_bill(
            principal=principal,
            bill_id=bill_id,
            paid_on=paid_on,
            amount=amount,
            method=method,
            bank_reference=bank_reference,
            notes=None,
        )

        # Stamp the verification metadata directly (FinanceService.pay
        # doesn't know about these new columns).
        from sqlalchemy import update as sa_update

        await self.session.execute(
            sa_update(SupplierPayment)
            .where(SupplierPayment.id == payment.id)
            .values(
                verification_status=PaymentVerificationStatus.PENDING.value,
                executed_by=principal.user_id,
                bank_account_id=state.selected_bank_account_id,
            ),
        )

        # Move approval state to PAID.
        await self.states.update(
            state_id=state.id,
            approval_status=BillApprovalStatus.PAID.value,
        )
        await record_audit(
            actor=principal,
            action=codes.ACTION_PAYMENT_EXECUTED,
            resource_type="supplier_payment",
            resource_id=payment.id,
            metadata={
                "bill_id": str(bill_id),
                "amount": str(amount),
                "method": method,
            },
        )
        return await self.session.get(SupplierPayment, payment.id)

    async def upload_proof(
        self, *,
        payment_id: UUID,
        proof_file_url: str,
        principal: Principal,
    ):
        from sqlalchemy import update as sa_update

        payment = await self.session.get(SupplierPayment, payment_id)
        if payment is None:
            raise BillNotFoundError("Payment not found.")
        await self.session.execute(
            sa_update(SupplierPayment)
            .where(SupplierPayment.id == payment_id)
            .values(
                proof_file_url=proof_file_url,
                verification_status=PaymentVerificationStatus.PROOF_UPLOADED.value,
            ),
        )
        await record_audit(
            actor=principal,
            action=codes.ACTION_PAYMENT_PROOF_UPLOADED,
            resource_type="supplier_payment",
            resource_id=payment_id,
            metadata={"url": proof_file_url[:200]},
        )
        return await self.session.get(SupplierPayment, payment_id)


# ============================================================
#  ReconciliationService
# ============================================================
class ReconciliationService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.states = SupplierBillApprovalStateRepository(session)

    async def verify(
        self, *,
        payment_id: UUID,
        principal: Principal,
    ):
        from sqlalchemy import update as sa_update

        payment = await self.session.get(SupplierPayment, payment_id)
        if payment is None:
            raise BillNotFoundError("Payment not found.")
        if payment.executed_by == principal.user_id:
            raise SeparationOfDutiesError(
                "Cannot verify a payment you executed.",
            )
        await self.session.execute(
            sa_update(SupplierPayment)
            .where(SupplierPayment.id == payment_id)
            .values(
                verification_status=PaymentVerificationStatus.VERIFIED.value,
                verified_by=principal.user_id,
                verified_at=utc_now(),
            ),
        )
        await record_audit(
            actor=principal,
            action=codes.ACTION_PAYMENT_VERIFIED,
            resource_type="supplier_payment",
            resource_id=payment_id,
            metadata={"bill_id": str(payment.bill_id)},
        )
        return await self.session.get(SupplierPayment, payment_id)

    async def reconcile(
        self, *,
        payment_id: UUID,
        principal: Principal,
    ):
        from sqlalchemy import update as sa_update

        payment = await self.session.get(SupplierPayment, payment_id)
        if payment is None:
            raise BillNotFoundError("Payment not found.")
        if payment.verification_status not in (
            PaymentVerificationStatus.VERIFIED.value,
            PaymentVerificationStatus.PROOF_UPLOADED.value,
        ):
            raise BusinessRuleError(
                "Payment must be VERIFIED before reconciliation.",
            )
        await self.session.execute(
            sa_update(SupplierPayment)
            .where(SupplierPayment.id == payment_id)
            .values(
                verification_status=PaymentVerificationStatus.RECONCILED.value,
            ),
        )
        # Move approval state to RECONCILED.
        state = await self.states.get_by_bill(payment.bill_id)
        if state is not None:
            await self.states.update(
                state_id=state.id,
                approval_status=BillApprovalStatus.RECONCILED.value,
            )
        await record_audit(
            actor=principal,
            action=codes.ACTION_PAYMENT_RECONCILED,
            resource_type="supplier_payment",
            resource_id=payment_id,
            metadata={"bill_id": str(payment.bill_id)},
        )
        return await self.session.get(SupplierPayment, payment_id)

    async def dispute(
        self, *,
        payment_id: UUID,
        reason: str,
        principal: Principal,
    ):
        from sqlalchemy import update as sa_update

        payment = await self.session.get(SupplierPayment, payment_id)
        if payment is None:
            raise BillNotFoundError("Payment not found.")
        await self.session.execute(
            sa_update(SupplierPayment)
            .where(SupplierPayment.id == payment_id)
            .values(
                verification_status=PaymentVerificationStatus.DISPUTED.value,
            ),
        )
        # Mirror to bill's dispute_flag.
        state = await self.states.get_by_bill(payment.bill_id)
        if state is not None:
            await self.states.update(
                state_id=state.id,
                dispute_flag=True,
                dispute_reason=reason,
            )
        await record_audit(
            actor=principal,
            action=codes.ACTION_PAYMENT_DISPUTED,
            resource_type="supplier_payment",
            resource_id=payment_id,
            metadata={"bill_id": str(payment.bill_id), "reason": reason},
        )
        return await self.session.get(SupplierPayment, payment_id)


# ============================================================
#  SupplierBankAccountService
# ============================================================
class SupplierBankAccountService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = SupplierBankAccountRepository(session)

    async def add_account(
        self, *,
        supplier_id: UUID,
        account_type: str,
        account_name: str,
        principal: Principal,
        bank_name: str | None = None,
        account_number_masked: str | None = None,
        iban_or_branch: str | None = None,
        mfs_number: str | None = None,
        is_default: bool = False,
    ):
        if is_default:
            await self.repo.clear_default_for_supplier(supplier_id)
        row = await self.repo.add(
            supplier_id=supplier_id,
            account_type=account_type,
            account_name=account_name,
            bank_name=bank_name,
            account_number_masked=account_number_masked,
            iban_or_branch=iban_or_branch,
            mfs_number=mfs_number,
            is_default=is_default,
            is_verified=False,
            is_active=True,
        )
        await record_audit(
            actor=principal,
            action=codes.ACTION_BANK_ACCOUNT_CREATED,
            resource_type="supplier_bank_account",
            resource_id=row.id,
            metadata={
                "supplier_id": str(supplier_id),
                "account_type": account_type,
            },
        )
        return row

    async def verify_account(
        self, *,
        account_id: UUID,
        principal: Principal,
    ):
        account = await self.repo.get(account_id)
        if account is None:
            raise BankAccountNotFoundError("Bank account not found.")
        await self.repo.update(
            account_id=account_id,
            is_verified=True,
            verified_at=utc_now(),
            verified_by=principal.user_id,
        )
        await record_audit(
            actor=principal,
            action=codes.ACTION_BANK_ACCOUNT_VERIFIED,
            resource_type="supplier_bank_account",
            resource_id=account_id,
            metadata={"supplier_id": str(account.supplier_id)},
        )
        return await self.repo.get(account_id)

    async def deactivate_account(
        self, *,
        account_id: UUID,
        principal: Principal,
    ):
        account = await self.repo.get(account_id)
        if account is None:
            raise BankAccountNotFoundError("Bank account not found.")
        await self.repo.update(
            account_id=account_id,
            is_active=False,
            is_default=False,
        )
        await record_audit(
            actor=principal,
            action=codes.ACTION_BANK_ACCOUNT_DEACTIVATED,
            resource_type="supplier_bank_account",
            resource_id=account_id,
            metadata={"supplier_id": str(account.supplier_id)},
        )
        return await self.repo.get(account_id)


# ============================================================
#  DuplicateInvoiceCheckService
# ============================================================
class DuplicateInvoiceCheckService:
    """Pre-submit + post-submit checks for duplicate invoice numbers
    against the same supplier.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def check(
        self, *,
        supplier_id: UUID,
        bill_number: str,
        exclude_bill_id: UUID | None = None,
    ) -> dict[str, Any]:
        dup = await find_duplicate_bill(
            self.session,
            supplier_id=supplier_id,
            bill_number=bill_number,
            exclude_bill_id=exclude_bill_id,
        )
        if dup is None:
            return {"is_duplicate": False, "match_bill_id": None}
        return {
            "is_duplicate": True,
            "match_bill_id": dup.id,
            "match_bill_status": dup.status,
            "match_grand_total": str(dup.grand_total),
            "match_bill_date": dup.bill_date.isoformat(),
        }
