"""Public API contracts for the AI module.

Note the explicit absence of any "approve" / "execute" schema. The AI
endpoints only ever PRODUCE proposals; downstream business actions
(approving an Rx, paying a refund) live in the owning module's API and
need a human Principal with the appropriate permission.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import Field

from app.core.validation import StrictModel


# ---------------- Capability inputs ----------------


class OcrPrescriptionRequest(StrictModel):
    """OCR a prescription image. The image bytes themselves arrive as a
    multipart upload — this body carries metadata only.
    """

    hint: str | None = Field(default=None, max_length=512)
    reference_prescription_id: UUID | None = None  # optional link to existing Rx


class SuggestMedicinesRequest(StrictModel):
    symptoms: str = Field(..., min_length=1, max_length=2048)
    patient_age_years: int | None = Field(default=None, ge=0, le=150)
    patient_sex: str | None = Field(
        default=None, pattern=r"^(male|female|other)$",
    )
    catalog_filter_generic: str | None = Field(default=None, max_length=160)
    rx_only: bool | None = None
    reference_prescription_id: UUID | None = None


class PredictStockRequest(StrictModel):
    variant_id: UUID
    horizon_days: int = Field(default=30, ge=1, le=365)
    history_days: int = Field(default=180, ge=7, le=730)


class DetectFraudRequest(StrictModel):
    order_id: UUID


# ---------------- Proposal review ----------------


class ProposalAcceptRequest(StrictModel):
    """Mark a proposal as ACCEPTED — reviewer agrees with AI as-is. Does
    NOT execute the underlying business action.
    """

    notes: str | None = Field(default=None, max_length=2048)


class ProposalAmendRequest(StrictModel):
    """Mark a proposal as AMENDED — reviewer used AI output as a starting
    point but edited it. The edited body is captured for audit. Still
    does NOT execute the underlying action.
    """

    decision_payload: dict[str, Any]
    notes: str | None = Field(default=None, max_length=2048)


class ProposalRejectRequest(StrictModel):
    reason: str = Field(..., min_length=1, max_length=2048)


# ---------------- Responses ----------------


class AIProposalResponse(StrictModel):
    id: UUID
    kind: str
    status: str
    requested_by: UUID | None
    reference_type: str | None
    reference_id: UUID | None
    provider: str
    model: str | None
    confidence: Decimal
    input_payload: dict[str, Any]
    ai_payload: dict[str, Any]
    decision_payload: dict[str, Any] | None
    reviewed_by: UUID | None
    reviewed_at: datetime | None
    review_notes: str | None
    expires_at: datetime | None
    created_at: datetime
    updated_at: datetime


class AIUsageEventResponse(StrictModel):
    id: UUID
    occurred_at: datetime
    kind: str
    provider: str
    model: str | None
    proposal_id: UUID | None
    requested_by: UUID | None
    success: bool
    error_code: str | None
    error_message: str | None
    cost_units: Decimal | None
    latency_ms: int | None


class AICapabilityStatus(StrictModel):
    """Surfaces which provider is bound, which capabilities it claims to
    support, and any per-key budget remaining (if the provider exposes
    it). Used by the admin UI to show "AI is offline" banners.
    """

    provider: str
    configured: bool
    capabilities: list[str]
    note: str | None = None
