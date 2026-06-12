"""AI proposals + usage events.

Revision ID: 0016_ai
Revises: 0015_finance
Create Date: 2026-05-03

Defence-in-depth notes
- ``ai_usage_events`` is REVOKEd UPDATE/DELETE — vendor cost
  reconciliation requires the usage ledger to be append-only.
- Proposal status is enforced via CHECK; the service layer additionally
  validates allowed transitions (draft → accepted/amended/rejected/expired).
- Confidence is bound to [0, 1] at the DB level so a buggy adapter that
  forwards a >1 score gets rejected at insert time.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0016_ai"
down_revision: str | Sequence[str] | None = "0015_finance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_proposals",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column(
            "status", sa.String(16), nullable=False, server_default="draft",
        ),
        sa.Column(
            "requested_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("reference_type", sa.String(48), nullable=True),
        sa.Column("reference_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model", sa.String(96), nullable=True),
        sa.Column(
            "confidence",
            sa.Numeric(4, 3),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "input_payload",
            postgresql.JSONB(),
            nullable=False,
        ),
        sa.Column(
            "ai_payload",
            postgresql.JSONB(),
            nullable=False,
        ),
        sa.Column(
            "decision_payload",
            postgresql.JSONB(),
            nullable=True,
        ),
        sa.Column(
            "reviewed_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_notes", sa.String(2048), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.CheckConstraint(
            "kind IN ('ocr_prescription','suggest_medicines',"
            "'predict_stock','detect_fraud')",
            name="ck_ai_proposals_kind_enum",
        ),
        sa.CheckConstraint(
            "status IN ('draft','accepted','amended','rejected','expired')",
            name="ck_ai_proposals_status_enum",
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_ai_proposals_confidence_unit_interval",
        ),
    )
    op.create_index(
        "ix_ai_proposals_kind_status", "ai_proposals", ["kind", "status"],
    )
    op.create_index(
        "ix_ai_proposals_reference",
        "ai_proposals",
        ["reference_type", "reference_id"],
    )
    op.create_index(
        "ix_ai_proposals_requested_by", "ai_proposals", ["requested_by"],
    )
    op.create_index(
        "ix_ai_proposals_created_at", "ai_proposals", ["created_at"],
    )

    op.create_table(
        "ai_usage_events",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model", sa.String(96), nullable=True),
        sa.Column(
            "proposal_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ai_proposals.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "requested_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("error_code", sa.String(96), nullable=True),
        sa.Column("error_message", sa.String(2048), nullable=True),
        sa.Column("cost_units", sa.Numeric(16, 6), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "kind IN ('ocr_prescription','suggest_medicines',"
            "'predict_stock','detect_fraud')",
            name="ck_ai_usage_events_kind_enum",
        ),
    )
    op.create_index(
        "ix_ai_usage_events_kind_at",
        "ai_usage_events",
        ["kind", "occurred_at"],
    )
    op.create_index(
        "ix_ai_usage_events_provider", "ai_usage_events", ["provider"],
    )

    op.execute(
        """
        DO $$
        BEGIN
          REVOKE UPDATE, DELETE ON TABLE ai_usage_events FROM PUBLIC;
        EXCEPTION WHEN insufficient_privilege THEN
          NULL;
        END$$;
        """,
    )


def downgrade() -> None:
    op.drop_index("ix_ai_usage_events_provider", table_name="ai_usage_events")
    op.drop_index("ix_ai_usage_events_kind_at", table_name="ai_usage_events")
    op.drop_table("ai_usage_events")

    op.drop_index("ix_ai_proposals_created_at", table_name="ai_proposals")
    op.drop_index("ix_ai_proposals_requested_by", table_name="ai_proposals")
    op.drop_index("ix_ai_proposals_reference", table_name="ai_proposals")
    op.drop_index("ix_ai_proposals_kind_status", table_name="ai_proposals")
    op.drop_table("ai_proposals")
