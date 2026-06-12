"""Fraud wire shapes."""
from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class FraudAssessmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    subject_type: str
    subject_id: UUID
    risk_score: int
    decision: str
    signals: list
    decided_by: UUID | None
    decision_reason: str | None
    created_at: datetime


class FraudAssessmentListOut(BaseModel):
    items: list[FraudAssessmentOut]
    total: int


class FraudCreateIn(BaseModel):
    subject_type: str = Field(..., min_length=1, max_length=32)
    subject_id: UUID
    risk_score: int = Field(..., ge=0, le=100)
    decision: Literal["CLEAR", "CHALLENGE", "BLOCK"]
    signals: list = Field(default_factory=list)
    decision_reason: str | None = Field(default=None, max_length=500)


class FraudDecisionIn(BaseModel):
    decision: Literal["CLEAR", "CHALLENGE", "BLOCK"]
    decision_reason: str | None = Field(default=None, max_length=500)
