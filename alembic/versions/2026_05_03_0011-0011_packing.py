"""Packing: sessions, session_lines, scans

Revision ID: 0011_packing
Revises: 0010_reminders
Create Date: 2026-05-03

``packing_scans`` is REVOKEd UPDATE/DELETE from PUBLIC so a service bug
cannot rewrite scan history. Mirrors the audit_log / stock_ledger /
order_status_history defence pattern.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011_packing"
down_revision: str | Sequence[str] | None = "0010_reminders"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "packing_sessions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "order_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orders.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column(
            "opened_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.Column(
            "opened_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "completed_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancellation_reason", sa.String(512), nullable=True),
        sa.Column("notes", sa.String(2048), nullable=True),
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
            "status IN ('open','completed','cancelled')",
            name="ck_packing_sessions_status_enum",
        ),
    )
    op.create_index("ix_packing_sessions_order_id", "packing_sessions", ["order_id"])
    op.create_index("ix_packing_sessions_status", "packing_sessions", ["status"])

    # Partial unique: at most one OPEN session per order at any time. Cancelled
    # / completed sessions can coexist (history) and a new open session can
    # start after a previous one closes.
    op.execute(
        """
        CREATE UNIQUE INDEX uq_packing_sessions_one_open_per_order
        ON packing_sessions (order_id)
        WHERE status = 'open'
        """,
    )

    op.create_table(
        "packing_session_lines",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("packing_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "order_line_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("order_lines.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "variant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("product_variants.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "expected_batch_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("batches.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("expected_quantity", sa.Integer(), nullable=False),
        sa.Column(
            "scanned_quantity",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column(
            "accepted_batch_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("batches.id", ondelete="RESTRICT"),
            nullable=True,
        ),
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
            "status IN ('open','complete','overridden')",
            name="ck_packing_lines_status_enum",
        ),
        sa.CheckConstraint(
            "expected_quantity > 0",
            name="ck_packing_lines_expected_qty_positive",
        ),
        sa.CheckConstraint(
            "scanned_quantity >= 0 AND scanned_quantity <= expected_quantity",
            name="ck_packing_lines_scanned_qty_in_bounds",
        ),
    )
    op.create_index(
        "ix_packing_lines_session_id", "packing_session_lines", ["session_id"],
    )
    op.create_index(
        "ix_packing_lines_variant_id", "packing_session_lines", ["variant_id"],
    )

    op.create_table(
        "packing_scans",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("packing_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "session_line_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("packing_session_lines.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("scanned_barcode", sa.String(64), nullable=False),
        sa.Column(
            "matched_variant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("product_variants.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "scanned_batch_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("batches.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("outcome", sa.String(24), nullable=False),
        sa.Column(
            "is_supervisor_override",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "supervisor_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "scanned_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "scanned_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.Column("notes", sa.String(512), nullable=True),
        sa.CheckConstraint(
            "outcome IN ('accepted','wrong_item','expired','batch_mismatch',"
            "'overridden','over_quantity','unknown_barcode')",
            name="ck_packing_scans_outcome_enum",
        ),
    )
    op.create_index(
        "ix_packing_scans_session_id", "packing_scans", ["session_id", "scanned_at"],
    )
    op.create_index(
        "ix_packing_scans_outcome", "packing_scans", ["outcome"],
    )

    # Append-only ledger.
    op.execute(
        """
        DO $$
        BEGIN
          REVOKE UPDATE, DELETE ON TABLE packing_scans FROM PUBLIC;
        EXCEPTION WHEN insufficient_privilege THEN
          NULL;
        END$$;
        """,
    )


def downgrade() -> None:
    op.drop_index("ix_packing_scans_outcome", table_name="packing_scans")
    op.drop_index("ix_packing_scans_session_id", table_name="packing_scans")
    op.drop_table("packing_scans")

    op.drop_index("ix_packing_lines_variant_id", table_name="packing_session_lines")
    op.drop_index("ix_packing_lines_session_id", table_name="packing_session_lines")
    op.drop_table("packing_session_lines")

    op.execute("DROP INDEX IF EXISTS uq_packing_sessions_one_open_per_order")
    op.drop_index("ix_packing_sessions_status", table_name="packing_sessions")
    op.drop_index("ix_packing_sessions_order_id", table_name="packing_sessions")
    op.drop_table("packing_sessions")
