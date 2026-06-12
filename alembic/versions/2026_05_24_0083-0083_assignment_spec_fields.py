"""assignment tables 10-field spec match

Revision ID: 0083_assignment_spec_fields
Revises: 0082_dispatch_tasks_spec_match
Create Date: 2026-05-24

Adds 5 canonical spec fields to BOTH ``marketplace_pickup_assignments``
and ``marketplace_delivery_assignments``:

    assigned_by         UUID FK users(id)  who created the assignment
    assignment_type     VARCHAR(20)         pickup / delivery / return
                                            (defaults to the table's natural
                                            type — set explicitly so a
                                            UNION ALL across both tables
                                            preserves dimension)
    reassignment_count  INTEGER             default 0; bumped on every
                                            rider-swap
    reassignment_reason VARCHAR(400)        last reassignment note
                                            (nullable)
    proof_required      BOOLEAN             default TRUE; mirror of the
                                            21-state STAGE_REQUIRED_META
                                            gate at the assignment level
                                            so dispatch logic can decide
                                            up-front whether the rider
                                            must scan/photograph

10-field canonical spec covered:
    id, order_id, rider_id, assigned_by, assignment_type, assigned_at,
    status, reassignment_count, reassignment_reason, proof_required
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision = "0083_assignment_spec_fields"
down_revision = "0082_dispatch_tasks_spec_match"
branch_labels = None
depends_on = None

_UUID = pg.UUID(as_uuid=True)
_TYPE_CHECK = "assignment_type IN ('pickup','delivery','return')"


def _add_spec_columns(table: str, default_type: str) -> None:
    op.add_column(
        table,
        sa.Column(
            "assigned_by", _UUID,
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        table,
        sa.Column(
            "assignment_type", sa.String(20), nullable=False,
            server_default=sa.text(f"'{default_type}'"),
        ),
    )
    op.add_column(
        table,
        sa.Column(
            "reassignment_count", sa.Integer, nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        table,
        sa.Column("reassignment_reason", sa.String(400), nullable=True),
    )
    op.add_column(
        table,
        sa.Column(
            "proof_required", sa.Boolean, nullable=False,
            server_default=sa.text("true"),
        ),
    )
    op.create_check_constraint(
        f"{table}_type_enum", table, _TYPE_CHECK,
    )
    op.create_check_constraint(
        f"{table}_reass_nonneg", table,
        "reassignment_count >= 0",
    )
    # Indexes — dispatch dashboard queries:
    #   "show me all open assignments where reassignment_count > 2"
    #   "show me who assigned what in the last hour"
    op.create_index(
        f"ix_{table.split('_')[1]}_assigned_by",
        table, ["assigned_by"],
    )
    op.create_index(
        f"ix_{table.split('_')[1]}_reass_count",
        table, ["reassignment_count"],
    )


def _drop_spec_columns(table: str) -> None:
    op.drop_index(
        f"ix_{table.split('_')[1]}_reass_count", table_name=table,
    )
    op.drop_index(
        f"ix_{table.split('_')[1]}_assigned_by", table_name=table,
    )
    op.drop_constraint(f"{table}_reass_nonneg", table, type_="check")
    op.drop_constraint(f"{table}_type_enum", table, type_="check")
    for col in ("proof_required", "reassignment_reason",
                "reassignment_count", "assignment_type", "assigned_by"):
        op.drop_column(table, col)


def upgrade() -> None:
    _add_spec_columns("marketplace_pickup_assignments", "pickup")
    _add_spec_columns("marketplace_delivery_assignments", "delivery")


def downgrade() -> None:
    _drop_spec_columns("marketplace_delivery_assignments")
    _drop_spec_columns("marketplace_pickup_assignments")
