"""SQLA wrappers for the supplier_payments tables."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import and_, desc, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.finance.models import SupplierBill
from app.modules.supplier_payments.models import (
    ApprovalWorkflow,
    SupplierBankAccount,
    SupplierBillApproval,
    SupplierBillApprovalState,
    SupplierPaymentRecommendation,
)


# ============================================================
#  SupplierBillApprovalStateRepository
# ============================================================
class SupplierBillApprovalStateRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_bill(self, bill_id: UUID) -> SupplierBillApprovalState | None:
        return (
            await self.session.execute(
                select(SupplierBillApprovalState)
                .where(SupplierBillApprovalState.supplier_bill_id == bill_id),
            )
        ).scalar_one_or_none()

    async def add(self, **fields: Any) -> SupplierBillApprovalState:
        row = SupplierBillApprovalState(**fields)
        self.session.add(row)
        await self.session.flush()
        return row

    async def update(self, *, state_id: UUID, **fields: Any) -> int:
        if not fields:
            return 0
        result = await self.session.execute(
            update(SupplierBillApprovalState)
            .where(SupplierBillApprovalState.id == state_id)
            .values(**fields),
        )
        return int(result.rowcount or 0)

    async def list_by_status(
        self,
        statuses: Sequence[str],
        *,
        limit: int = 200,
    ) -> Sequence[SupplierBillApprovalState]:
        return (
            (
                await self.session.execute(
                    select(SupplierBillApprovalState)
                    .where(
                        SupplierBillApprovalState.approval_status.in_(
                            list(statuses),
                        ),
                    )
                    .order_by(SupplierBillApprovalState.payment_priority,
                              desc(SupplierBillApprovalState.recommendation_score))
                    .limit(limit),
                )
            )
            .scalars()
            .all()
        )

    async def list_recommended(
        self,
        *,
        on_or_before: date,
        limit: int = 200,
    ) -> Sequence[SupplierBillApprovalState]:
        """List bills whose recommended_payment_date <= ``on_or_before``
        AND status in (approved_final, ready_for_payment), ordered by
        priority then score.
        """
        return (
            (
                await self.session.execute(
                    select(SupplierBillApprovalState)
                    .where(
                        and_(
                            SupplierBillApprovalState.recommended_payment_date.isnot(None),
                            SupplierBillApprovalState.recommended_payment_date <= on_or_before,
                            SupplierBillApprovalState.approval_status.in_([
                                "approved_final", "ready_for_payment",
                            ]),
                        ),
                    )
                    .order_by(
                        SupplierBillApprovalState.payment_priority,
                        desc(SupplierBillApprovalState.recommendation_score),
                    )
                    .limit(limit),
                )
            )
            .scalars()
            .all()
        )

    async def list_active_for_recompute(
        self, *, limit: int = 500,
    ) -> Sequence[SupplierBillApprovalState]:
        """All non-terminal bills — fed to the daily recommendation
        sweep job.
        """
        return (
            (
                await self.session.execute(
                    select(SupplierBillApprovalState)
                    .where(
                        SupplierBillApprovalState.approval_status.notin_([
                            "rejected", "reconciled", "draft",
                        ]),
                    )
                    .limit(limit),
                )
            )
            .scalars()
            .all()
        )


# ============================================================
#  SupplierBillApprovalRepository (append-only)
# ============================================================
class SupplierBillApprovalRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, **fields: Any) -> SupplierBillApproval:
        row = SupplierBillApproval(**fields)
        self.session.add(row)
        await self.session.flush()
        return row

    async def list_for_bill(
        self, bill_id: UUID,
    ) -> Sequence[SupplierBillApproval]:
        return (
            (
                await self.session.execute(
                    select(SupplierBillApproval)
                    .where(SupplierBillApproval.supplier_bill_id == bill_id)
                    .order_by(SupplierBillApproval.level),
                )
            )
            .scalars()
            .all()
        )

    async def get_for_level(
        self, *, bill_id: UUID, level: int,
    ) -> SupplierBillApproval | None:
        return (
            await self.session.execute(
                select(SupplierBillApproval)
                .where(
                    and_(
                        SupplierBillApproval.supplier_bill_id == bill_id,
                        SupplierBillApproval.level == level,
                    ),
                ),
            )
        ).scalar_one_or_none()

    async def has_user_approved_any_level(
        self, *, bill_id: UUID, user_id: UUID,
    ) -> bool:
        """Separation-of-duties check — has this user approved ANY
        level on this bill already?
        """
        result = (
            await self.session.execute(
                select(SupplierBillApproval.id)
                .where(
                    and_(
                        SupplierBillApproval.supplier_bill_id == bill_id,
                        SupplierBillApproval.approver_user_id == user_id,
                        SupplierBillApproval.decision.in_([
                            "approved", "returned_for_correction",
                            "rejected",
                        ]),
                    ),
                )
                .limit(1),
            )
        ).first()
        return result is not None

    async def clear_for_bill(self, bill_id: UUID) -> int:
        """Delete all approval rows for a bill — used when a bill is
        resubmitted after RETURNED_FOR_CORRECTION so the new cycle
        isn't blocked by UNIQUE(bill_id, level) on stale rows.

        The audit_log retains the prior decisions (every approve/reject/
        return wrote an audit row), so deleting the structured table
        rows doesn't lose history. We delete rather than soft-delete
        because the UNIQUE constraint can't be made conditional
        without a partial index, and the audit_log is the source of
        truth for "what happened on the prior cycle".
        """
        from sqlalchemy import delete as sa_delete
        result = await self.session.execute(
            sa_delete(SupplierBillApproval)
            .where(SupplierBillApproval.supplier_bill_id == bill_id),
        )
        return int(result.rowcount or 0)


# ============================================================
#  SupplierPaymentRecommendationRepository
# ============================================================
class SupplierPaymentRecommendationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, **fields: Any) -> SupplierPaymentRecommendation:
        row = SupplierPaymentRecommendation(**fields)
        self.session.add(row)
        await self.session.flush()
        return row

    async def list_for_bill(
        self, bill_id: UUID, *, limit: int = 30,
    ) -> Sequence[SupplierPaymentRecommendation]:
        return (
            (
                await self.session.execute(
                    select(SupplierPaymentRecommendation)
                    .where(SupplierPaymentRecommendation.supplier_bill_id == bill_id)
                    .order_by(desc(SupplierPaymentRecommendation.generated_at))
                    .limit(limit),
                )
            )
            .scalars()
            .all()
        )


# ============================================================
#  SupplierBankAccountRepository
# ============================================================
class SupplierBankAccountRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, account_id: UUID) -> SupplierBankAccount | None:
        return await self.session.get(SupplierBankAccount, account_id)

    async def list_for_supplier(
        self, supplier_id: UUID, *, active_only: bool = True,
    ) -> Sequence[SupplierBankAccount]:
        stmt = (
            select(SupplierBankAccount)
            .where(SupplierBankAccount.supplier_id == supplier_id)
        )
        if active_only:
            stmt = stmt.where(SupplierBankAccount.is_active.is_(True))
        return (
            await self.session.execute(
                stmt.order_by(
                    desc(SupplierBankAccount.is_default),
                    SupplierBankAccount.account_name,
                ),
            )
        ).scalars().all()

    async def get_default_for_supplier(
        self, supplier_id: UUID,
    ) -> SupplierBankAccount | None:
        return (
            await self.session.execute(
                select(SupplierBankAccount)
                .where(
                    and_(
                        SupplierBankAccount.supplier_id == supplier_id,
                        SupplierBankAccount.is_default.is_(True),
                        SupplierBankAccount.is_active.is_(True),
                    ),
                )
                .limit(1),
            )
        ).scalar_one_or_none()

    async def add(self, **fields: Any) -> SupplierBankAccount:
        row = SupplierBankAccount(**fields)
        self.session.add(row)
        await self.session.flush()
        return row

    async def update(self, *, account_id: UUID, **fields: Any) -> int:
        if not fields:
            return 0
        result = await self.session.execute(
            update(SupplierBankAccount)
            .where(SupplierBankAccount.id == account_id)
            .values(**fields),
        )
        return int(result.rowcount or 0)

    async def clear_default_for_supplier(
        self, supplier_id: UUID,
    ) -> None:
        await self.session.execute(
            update(SupplierBankAccount)
            .where(
                and_(
                    SupplierBankAccount.supplier_id == supplier_id,
                    SupplierBankAccount.is_default.is_(True),
                ),
            )
            .values(is_default=False),
        )


# ============================================================
#  ApprovalWorkflowRepository
# ============================================================
class ApprovalWorkflowRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_code(self, code: str) -> ApprovalWorkflow | None:
        return (
            await self.session.execute(
                select(ApprovalWorkflow)
                .where(ApprovalWorkflow.workflow_code == code),
            )
        ).scalar_one_or_none()

    async def list_active(self) -> Sequence[ApprovalWorkflow]:
        return (
            (
                await self.session.execute(
                    select(ApprovalWorkflow)
                    .where(ApprovalWorkflow.is_active.is_(True))
                    .order_by(
                        # NULL thresholds first (default workflow), then
                        # ascending threshold so we pick the smallest
                        # qualifying high-value workflow.
                        ApprovalWorkflow.threshold_amount.asc().nullsfirst(),
                    ),
                )
            )
            .scalars()
            .all()
        )

    async def upsert(
        self, *,
        workflow_code: str,
        name: str,
        description: str = "",
        threshold_amount: Decimal | None = None,
        requires_super_admin: bool = False,
        min_approval_steps: int = 3,
        is_active: bool = True,
    ) -> ApprovalWorkflow:
        existing = await self.get_by_code(workflow_code)
        if existing is not None:
            await self.session.execute(
                update(ApprovalWorkflow)
                .where(ApprovalWorkflow.id == existing.id)
                .values(
                    name=name,
                    description=description,
                    threshold_amount=threshold_amount,
                    requires_super_admin=requires_super_admin,
                    min_approval_steps=min_approval_steps,
                    is_active=is_active,
                ),
            )
            return await self.get_by_code(workflow_code)  # type: ignore[return-value]
        row = ApprovalWorkflow(
            workflow_code=workflow_code,
            name=name,
            description=description,
            threshold_amount=threshold_amount,
            requires_super_admin=requires_super_admin,
            min_approval_steps=min_approval_steps,
            is_active=is_active,
        )
        self.session.add(row)
        await self.session.flush()
        return row


# ============================================================
#  Helper: load a SupplierBill (existing finance model)
# ============================================================
async def get_bill_or_none(
    session: AsyncSession, bill_id: UUID,
) -> SupplierBill | None:
    return await session.get(SupplierBill, bill_id)


async def find_duplicate_bill(
    session: AsyncSession,
    *,
    supplier_id: UUID,
    bill_number: str,
    exclude_bill_id: UUID | None = None,
) -> SupplierBill | None:
    """The duplicate-invoice check: same supplier + same bill_number.

    Existing UNIQUE constraint on (supplier_id, bill_number) in the
    finance schema means a true duplicate can't be inserted; this
    method exists for the soft "user is about to submit a duplicate"
    pre-check before they hit insert.
    """
    stmt = select(SupplierBill).where(
        and_(
            SupplierBill.supplier_id == supplier_id,
            SupplierBill.bill_number == bill_number,
        ),
    )
    if exclude_bill_id is not None:
        stmt = stmt.where(SupplierBill.id != exclude_bill_id)
    return (await session.execute(stmt.limit(1))).scalar_one_or_none()
