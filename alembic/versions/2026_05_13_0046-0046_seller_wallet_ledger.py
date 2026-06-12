"""0046 seller_wallet_ledger — append-only ledger for seller liability events.

Used by the marketplace return-liability flow:
- when a customer return is COMPLETED, the seller whose products were
  returned is debited via a ledger row (entry_type=return_debit). The
  seller payout aggregator later subtracts the sum of these rows from
  the seller's gross earnings.

Append-only by design. Idempotency is enforced by a partial unique
index on (return_request_line_id, entry_type) so re-delivery of the
outbox event does not double-post.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0046_seller_wallet_ledger"
down_revision: str | Sequence[str] | None = "0045_funnel_kpi"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "seller_wallet_ledger",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "seller_id",
            UUID(as_uuid=True),
            sa.ForeignKey("sellers.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "entry_type",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column(
            "direction",
            sa.String(length=8),
            nullable=False,
        ),
        sa.Column(
            "amount",
            sa.Numeric(12, 2),
            nullable=False,
        ),
        # Reference fields — nullable because different entry types
        # reference different aggregates. For return_debit we point at
        # the return_request_line_id (the most granular pointer).
        sa.Column(
            "order_id",
            UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "order_line_id",
            UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "return_request_id",
            UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "return_request_line_id",
            UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_by",
            UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "entry_type IN ("
            "'return_debit',"
            "'commission_debit',"
            "'payout_credit',"
            "'manual_adjustment'"
            ")",
            name="ck_seller_wallet_ledger_entry_type",
        ),
        sa.CheckConstraint(
            "direction IN ('debit', 'credit')",
            name="ck_seller_wallet_ledger_direction",
        ),
        sa.CheckConstraint(
            "amount >= 0",
            name="ck_seller_wallet_ledger_amount_nonneg",
        ),
    )
    op.create_index(
        "ix_seller_wallet_ledger_seller_created",
        "seller_wallet_ledger",
        ["seller_id", "created_at"],
    )
    # Idempotency on return-debit posting: never post twice for the
    # same return_request_line.
    op.create_index(
        "uq_seller_wallet_ledger_return_line_entry",
        "seller_wallet_ledger",
        ["return_request_line_id", "entry_type"],
        unique=True,
        postgresql_where=sa.text("return_request_line_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_seller_wallet_ledger_return_line_entry",
        table_name="seller_wallet_ledger",
    )
    op.drop_index(
        "ix_seller_wallet_ledger_seller_created",
        table_name="seller_wallet_ledger",
    )
    op.drop_table("seller_wallet_ledger")
