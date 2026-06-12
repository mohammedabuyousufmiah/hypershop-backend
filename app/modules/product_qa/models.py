"""ORM models for the product Q&A module — phase 3.

Two tables:

- ``product_questions`` — one question per (customer, product) is NOT
  enforced (a customer can ask multiple things about the same product).
  Status governs visibility on the public surface.
- ``product_answers`` — many answers per question. ``is_seller_answer``
  is set automatically by the service when the answering user is
  linked to the product's owning seller; the frontend shows a
  "Seller" badge to give that voice extra weight.
- ``answer_helpful_votes`` — composite-PK upvote, idempotent.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db.base import Base
from app.core.db.mixins import TimestampMixin
from app.modules.product_qa.codes import ALL_STATUSES, STATUS_PENDING


_STATUS_CHECK = "status IN (" + ",".join(f"'{s}'" for s in ALL_STATUSES) + ")"


class ProductQuestion(Base, TimestampMixin):
    __tablename__ = "product_questions"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    product_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
    )
    customer_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(length=16), nullable=False, server_default=STATUS_PENDING,
    )
    rejection_reason: Mapped[str | None] = mapped_column(
        String(length=300), nullable=True,
    )
    moderated_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    moderated_at: Mapped[datetime | None] = mapped_column(nullable=True)

    __table_args__ = (
        CheckConstraint(_STATUS_CHECK, name="ck_question_status"),
        Index("ix_questions_product_status", "product_id", "status"),
        Index("ix_questions_customer", "customer_id"),
    )


class ProductAnswer(Base, TimestampMixin):
    __tablename__ = "product_answers"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    question_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("product_questions.id", ondelete="CASCADE"),
        nullable=False,
    )
    customer_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(length=16), nullable=False, server_default=STATUS_PENDING,
    )
    helpful_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0"),
    )
    is_seller_answer: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"),
    )
    rejection_reason: Mapped[str | None] = mapped_column(
        String(length=300), nullable=True,
    )
    moderated_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    moderated_at: Mapped[datetime | None] = mapped_column(nullable=True)

    __table_args__ = (
        CheckConstraint(_STATUS_CHECK, name="ck_answer_status"),
        Index("ix_answers_question_status", "question_id", "status"),
        Index("ix_answers_question_helpful", "question_id", "helpful_count"),
        Index("ix_answers_customer", "customer_id"),
    )


class AnswerHelpfulVote(Base):
    __tablename__ = "answer_helpful_votes"

    answer_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("product_answers.id", ondelete="CASCADE"),
        primary_key=True,
    )
    customer_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    voted_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=text("now()"),
    )

    __table_args__ = (
        Index("ix_answer_votes_answer", "answer_id"),
    )
