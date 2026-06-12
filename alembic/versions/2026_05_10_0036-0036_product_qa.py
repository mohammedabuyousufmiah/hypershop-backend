"""product Q&A — questions + answers + helpful votes (reviews phase 3)

Revision ID: 0036_product_qa
Revises: 0035_review_media
Create Date: 2026-05-10

Phase 3 of the reviews initiative — Q&A surface. Three tables:
  - product_questions  (1 question per row)
  - product_answers    (N answers per question)
  - answer_helpful_votes (idempotent upvote, composite PK)
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0036_product_qa"
down_revision: str | Sequence[str] | None = "0035_review_media"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_STATUSES = ("pending", "approved", "rejected", "disabled")
_STATUS_CHECK = "status IN (" + ",".join(f"'{s}'" for s in _STATUSES) + ")"


def upgrade() -> None:
    op.create_table(
        "product_questions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column(
            "product_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("products.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "customer_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "status", sa.String(length=16),
            nullable=False, server_default="pending",
        ),
        sa.Column("rejection_reason", sa.String(length=300), nullable=True),
        sa.Column(
            "moderated_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("moderated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(_STATUS_CHECK, name="ck_question_status"),
    )
    op.create_index(
        "ix_questions_product_status", "product_questions",
        ["product_id", "status"],
    )
    op.create_index(
        "ix_questions_customer", "product_questions", ["customer_id"],
    )

    op.create_table(
        "product_answers",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column(
            "question_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("product_questions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "customer_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "status", sa.String(length=16),
            nullable=False, server_default="pending",
        ),
        sa.Column(
            "helpful_count", sa.Integer(),
            nullable=False, server_default=sa.text("0"),
        ),
        sa.Column(
            "is_seller_answer", sa.Boolean(),
            nullable=False, server_default=sa.text("false"),
        ),
        sa.Column("rejection_reason", sa.String(length=300), nullable=True),
        sa.Column(
            "moderated_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("moderated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(_STATUS_CHECK, name="ck_answer_status"),
    )
    op.create_index(
        "ix_answers_question_status", "product_answers",
        ["question_id", "status"],
    )
    op.create_index(
        "ix_answers_question_helpful", "product_answers",
        ["question_id", "helpful_count"],
    )
    op.create_index(
        "ix_answers_customer", "product_answers", ["customer_id"],
    )

    op.create_table(
        "answer_helpful_votes",
        sa.Column(
            "answer_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("product_answers.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "customer_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "voted_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_answer_votes_answer", "answer_helpful_votes", ["answer_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_answer_votes_answer", table_name="answer_helpful_votes")
    op.drop_table("answer_helpful_votes")
    op.drop_index("ix_answers_customer", table_name="product_answers")
    op.drop_index("ix_answers_question_helpful", table_name="product_answers")
    op.drop_index("ix_answers_question_status", table_name="product_answers")
    op.drop_table("product_answers")
    op.drop_index("ix_questions_customer", table_name="product_questions")
    op.drop_index("ix_questions_product_status", table_name="product_questions")
    op.drop_table("product_questions")
