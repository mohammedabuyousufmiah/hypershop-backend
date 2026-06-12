"""Pydantic schemas for the product Q&A module — phase 3."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.modules.product_qa.codes import (
    ANSWER_MAX,
    ANSWER_MIN,
    QUESTION_MAX,
    QUESTION_MIN,
)


# ─── inbound ───


class QuestionCreateIn(BaseModel):
    body: str = Field(..., min_length=QUESTION_MIN, max_length=QUESTION_MAX)


class QuestionUpdateIn(BaseModel):
    body: str = Field(..., min_length=QUESTION_MIN, max_length=QUESTION_MAX)


class AnswerCreateIn(BaseModel):
    body: str = Field(..., min_length=ANSWER_MIN, max_length=ANSWER_MAX)


class AnswerUpdateIn(BaseModel):
    body: str = Field(..., min_length=ANSWER_MIN, max_length=ANSWER_MAX)


class ModerationRejectIn(BaseModel):
    reason: str = Field(..., min_length=3, max_length=300)


# ─── outbound (public) ───


class PublicAnswerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    question_id: UUID
    body: str
    helpful_count: int
    is_seller_answer: bool
    customer_display_name: str | None = None
    created_at: datetime


class PublicQuestionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    product_id: UUID
    body: str
    customer_display_name: str | None = None
    created_at: datetime
    answers: list[PublicAnswerOut] = []


class PublicQuestionListOut(BaseModel):
    items: list[PublicQuestionOut]
    total: int


# ─── outbound (admin) ───


class AdminQuestionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    product_id: UUID
    customer_id: UUID
    body: str
    status: str
    rejection_reason: str | None
    moderated_by: UUID | None
    moderated_at: datetime | None
    created_at: datetime
    updated_at: datetime


class AdminAnswerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    question_id: UUID
    customer_id: UUID
    body: str
    status: str
    helpful_count: int
    is_seller_answer: bool
    rejection_reason: str | None
    moderated_by: UUID | None
    moderated_at: datetime | None
    created_at: datetime
    updated_at: datetime


class AdminQuestionListOut(BaseModel):
    items: list[AdminQuestionOut]
    total: int


class AdminAnswerListOut(BaseModel):
    items: list[AdminAnswerOut]
    total: int


class AnswerHelpfulOut(BaseModel):
    answer_id: UUID
    helpful_count: int
    voted: bool
