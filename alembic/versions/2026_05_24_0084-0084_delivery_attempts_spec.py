"""marketplace_delivery_attempts canonical 12-field spec match

Revision ID: 0084_delivery_attempts_spec
Revises: 0083_assignment_spec_fields
Create Date: 2026-05-24

Aligns ``marketplace_delivery_attempts`` to canonical 12-field spec:

    id, order_id, rider_id, attempt_no, attempt_status, failure_reason,
    customer_contacted, call_attempt_count, proof_photo_url,
    gps_location, note, created_at

Renames:
    outcome        -> attempt_status
    pod_photo_url  -> proof_photo_url
    notes          -> note
    attempted_at   -> created_at

Adds:
    customer_contacted   BOOLEAN default FALSE  did rider talk to customer
                                                 before logging the attempt?
    call_attempt_count   INTEGER default 0      how many call tries before
                                                 contact (NDR escalation
                                                 uses this — >=3 unanswered
                                                 calls before flagging
                                                 "customer_unreachable")
    gps_location         VARCHAR(40)            "lat,lng" combined string
                                                 (kept alongside gps_lat /
                                                 gps_lng nullable for
                                                 back-compat; service writes
                                                 both)

Drops nothing — gps_lat / gps_lng / signature_url / cod_collected_minor
stay (they're useful operational extras beyond the canonical 12).
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0084_delivery_attempts_spec"
down_revision = "0083_assignment_spec_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. Rename outcome -> attempt_status; drop old CHECK first.
    # ------------------------------------------------------------------
    op.drop_constraint(
        "attempt_outcome_enum", "marketplace_delivery_attempts", type_="check",
    )
    op.alter_column(
        "marketplace_delivery_attempts", "outcome",
        new_column_name="attempt_status",
    )
    op.create_check_constraint(
        "attempt_status_enum", "marketplace_delivery_attempts",
        "attempt_status IN ('delivered','failed','rescheduled',"
        "'customer_unreachable','address_issue','cod_refused','partial')",
    )

    # ------------------------------------------------------------------
    # 2. Rename POD/photo + note + timestamp columns.
    # ------------------------------------------------------------------
    op.alter_column(
        "marketplace_delivery_attempts", "pod_photo_url",
        new_column_name="proof_photo_url",
    )
    op.alter_column(
        "marketplace_delivery_attempts", "notes",
        new_column_name="note",
    )
    op.alter_column(
        "marketplace_delivery_attempts", "attempted_at",
        new_column_name="created_at",
    )
    # Composite idx referenced old col name — drop + re-create.
    op.drop_index(
        "ix_attempts_order_time", table_name="marketplace_delivery_attempts",
    )
    op.create_index(
        "ix_attempts_order_time",
        "marketplace_delivery_attempts",
        ["order_id", "created_at"],
    )

    # ------------------------------------------------------------------
    # 3. Add 3 new spec columns.
    # ------------------------------------------------------------------
    op.add_column(
        "marketplace_delivery_attempts",
        sa.Column(
            "customer_contacted", sa.Boolean, nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "marketplace_delivery_attempts",
        sa.Column(
            "call_attempt_count", sa.Integer, nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "marketplace_delivery_attempts",
        sa.Column("gps_location", sa.String(40), nullable=True),
    )
    op.create_check_constraint(
        "call_attempt_count_nonneg", "marketplace_delivery_attempts",
        "call_attempt_count >= 0",
    )

    # ------------------------------------------------------------------
    # 4. Backfill gps_location from existing lat/lng if both present.
    # ------------------------------------------------------------------
    op.execute(
        """
        UPDATE marketplace_delivery_attempts
        SET gps_location = gps_lat::text || ',' || gps_lng::text
        WHERE gps_lat IS NOT NULL AND gps_lng IS NOT NULL
          AND gps_location IS NULL
        """
    )

    # ------------------------------------------------------------------
    # 5. Index on (customer_contacted, attempt_status) for NDR queries
    #    "show me failed attempts where rider never reached customer".
    # ------------------------------------------------------------------
    op.create_index(
        "ix_attempts_contact_status",
        "marketplace_delivery_attempts",
        ["customer_contacted", "attempt_status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_attempts_contact_status",
        table_name="marketplace_delivery_attempts",
    )
    op.drop_constraint(
        "call_attempt_count_nonneg",
        "marketplace_delivery_attempts", type_="check",
    )
    op.drop_column("marketplace_delivery_attempts", "gps_location")
    op.drop_column("marketplace_delivery_attempts", "call_attempt_count")
    op.drop_column("marketplace_delivery_attempts", "customer_contacted")

    op.drop_index(
        "ix_attempts_order_time", table_name="marketplace_delivery_attempts",
    )
    op.alter_column(
        "marketplace_delivery_attempts", "created_at",
        new_column_name="attempted_at",
    )
    op.alter_column(
        "marketplace_delivery_attempts", "note",
        new_column_name="notes",
    )
    op.alter_column(
        "marketplace_delivery_attempts", "proof_photo_url",
        new_column_name="pod_photo_url",
    )
    op.create_index(
        "ix_attempts_order_time",
        "marketplace_delivery_attempts",
        ["order_id", "attempted_at"],
    )

    op.drop_constraint(
        "attempt_status_enum", "marketplace_delivery_attempts", type_="check",
    )
    op.alter_column(
        "marketplace_delivery_attempts", "attempt_status",
        new_column_name="outcome",
    )
    op.create_check_constraint(
        "attempt_outcome_enum", "marketplace_delivery_attempts",
        "outcome IN ('delivered','failed','rescheduled',"
        "'customer_unreachable','address_issue','cod_refused','partial')",
    )
