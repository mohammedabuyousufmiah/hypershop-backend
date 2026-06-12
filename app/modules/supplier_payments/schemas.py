"""Pydantic v2 schemas for the supplier-payment approval engine."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import Field

from app.core.validation import StrictModel


# ============================================================
#  Bill approval state
# ============================================================
class ApprovalStateResponse(StrictModel):
    id: UUID
    supplier_bill_id: UUID
    approval_status: str
    workflow_code: str | None
    recommended_payment_date: date | None
    recommended_payment_method: str | None
    recommended_payment_amount: Decimal | None
    recommendation_score: int | None
    payment_priority: str
    last_recommended_at: datetime | None
    dispute_flag: bool
    dispute_reason: str | None
    duplicate_check_flag: bool
    duplicate_of_bill_id: UUID | None
    selected_bank_account_id: UUID | None
    submitted_at: datetime | None
    final_approved_at: datetime | None


class ApprovalStateListResponse(StrictModel):
    items: list[ApprovalStateResponse]


# ============================================================
#  Per-level approval rows
# ============================================================
class ApprovalRowResponse(StrictModel):
    id: UUID
    supplier_bill_id: UUID
    level: int
    approver_user_id: UUID
    approver_role: str
    decision: str
    decision_note: str | None
    decided_at: datetime


class ApprovalListResponse(StrictModel):
    items: list[ApprovalRowResponse]


class ApprovalActionRequest(StrictModel):
    """Body for /approve-level-N + /reject + /return."""

    approver_role: str = Field(min_length=1, max_length=64)
    decision_note: str | None = Field(default=None, max_length=2000)


class HoldRequest(StrictModel):
    reason: str = Field(min_length=1, max_length=500)


class ResumeRequest(StrictModel):
    new_status: Literal["submitted", "level_1_verified",
                        "level_2_approved", "level_3_approved"] = "submitted"


class SubmitRequest(StrictModel):
    notes: str | None = Field(default=None, max_length=2000)


# ============================================================
#  Recommendation
# ============================================================
class RecommendationRecomputeRequest(StrictModel):
    is_critical_supplier: bool = False
    has_early_pay_discount: bool = False
    has_recent_payment: bool = False
    cash_ready: bool = True


class RecommendationHistoryRow(StrictModel):
    id: UUID
    supplier_bill_id: UUID
    recommended_payment_date: date
    recommended_payment_amount: Decimal
    recommended_payment_method: str | None
    priority_level: str
    score: int
    payload: dict[str, Any]
    engine_version: str
    generated_at: datetime


class RecommendationHistoryResponse(StrictModel):
    items: list[RecommendationHistoryRow]


# ============================================================
#  Mark ready / payment / verify
# ============================================================
class MarkReadyRequest(StrictModel):
    bank_account_id: UUID


class ExecutePaymentRequest(StrictModel):
    amount: Decimal = Field(gt=0)
    method: str = Field(min_length=1, max_length=32)
    paid_on: date
    bank_reference: str | None = Field(default=None, max_length=160)


class UploadProofRequest(StrictModel):
    proof_file_url: str = Field(min_length=1, max_length=512)


class DisputeRequest(StrictModel):
    reason: str = Field(min_length=1, max_length=500)


class PaymentResponse(StrictModel):
    id: UUID
    bill_id: UUID
    code: str
    paid_on: date
    amount: Decimal
    method: str
    bank_reference: str | None
    verification_status: str
    proof_file_url: str | None
    executed_by: UUID | None
    verified_by: UUID | None
    verified_at: datetime | None
    bank_account_id: UUID | None


# ============================================================
#  Bank accounts
# ============================================================
class BankAccountCreateRequest(StrictModel):
    supplier_id: UUID
    account_type: Literal["bank", "mfs", "cash"]
    account_name: str = Field(min_length=1, max_length=160)
    bank_name: str | None = Field(default=None, max_length=120)
    account_number_masked: str | None = Field(default=None, max_length=64)
    iban_or_branch: str | None = Field(default=None, max_length=255)
    mfs_number: str | None = Field(default=None, max_length=32)
    is_default: bool = False


class BankAccountResponse(StrictModel):
    id: UUID
    supplier_id: UUID
    account_type: str
    account_name: str
    bank_name: str | None
    account_number_masked: str | None
    iban_or_branch: str | None
    mfs_number: str | None
    is_default: bool
    is_verified: bool
    verified_at: datetime | None
    is_active: bool


class BankAccountListResponse(StrictModel):
    items: list[BankAccountResponse]


# ============================================================
#  Workflows
# ============================================================
class WorkflowResponse(StrictModel):
    id: UUID
    workflow_code: str
    name: str
    description: str
    threshold_amount: Decimal | None
    requires_super_admin: bool
    min_approval_steps: int
    is_active: bool


class WorkflowListResponse(StrictModel):
    items: list[WorkflowResponse]


# ============================================================
#  Duplicate check
# ============================================================
class DuplicateCheckRequest(StrictModel):
    supplier_id: UUID
    bill_number: str = Field(min_length=1, max_length=96)


class DuplicateCheckResponse(StrictModel):
    is_duplicate: bool
    match_bill_id: UUID | None = None
    match_bill_status: str | None = None
    match_grand_total: str | None = None
    match_bill_date: str | None = None
