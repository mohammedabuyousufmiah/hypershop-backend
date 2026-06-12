"""0076 bulk_upload — seller CSV/XLSX bulk product upload.

Two tables (both ``hypershop_`` prefixed):
  hypershop_bulk_upload_jobs   — one row per uploaded file
  hypershop_bulk_upload_rows   — per-row failures only (success rows
                                  don't write an audit row; the Catalog
                                  product/variant/inventory inserts ARE
                                  the audit trail for successes).
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID

revision: str = "0076_bulk_upload"
down_revision: str | Sequence[str] | None = "0075_customer_segments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "hypershop_bulk_upload_jobs",
        sa.Column(
            "id",
            PgUUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "seller_id",
            PgUUID(as_uuid=True),
            sa.ForeignKey("sellers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("uploaded_by_user_id", PgUUID(as_uuid=True), nullable=False),
        sa.Column("original_filename", sa.String(256), nullable=False),
        sa.Column("file_url", sa.Text, nullable=False),
        sa.Column("file_size_bytes", sa.BigInteger, nullable=False),
        sa.Column("file_format", sa.String(8), nullable=False),
        sa.Column(
            "total_rows", sa.Integer, nullable=False, server_default=sa.text("0"),
        ),
        sa.Column(
            "processed_rows", sa.Integer, nullable=False, server_default=sa.text("0"),
        ),
        sa.Column(
            "succeeded_rows", sa.Integer, nullable=False, server_default=sa.text("0"),
        ),
        sa.Column(
            "failed_rows", sa.Integer, nullable=False, server_default=sa.text("0"),
        ),
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'queued'"),
        ),
        sa.Column("error_summary", JSONB, nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
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
            "file_size_bytes > 0",
            name="ck_hypershop_bulk_upload_jobs_size_pos",
        ),
        sa.CheckConstraint(
            "file_format IN ('csv','xlsx','tsv')",
            name="ck_hypershop_bulk_upload_jobs_format",
        ),
        sa.CheckConstraint(
            "status IN ('queued','validating','ingesting','completed','failed','cancelled')",
            name="ck_hypershop_bulk_upload_jobs_status",
        ),
    )
    op.create_index(
        "ix_hypershop_bulk_upload_jobs_seller_created",
        "hypershop_bulk_upload_jobs",
        ["seller_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "ix_hypershop_bulk_upload_jobs_pending",
        "hypershop_bulk_upload_jobs",
        ["status", "created_at"],
        postgresql_where=sa.text(
            "status IN ('queued','validating','ingesting')",
        ),
    )

    op.create_table(
        "hypershop_bulk_upload_rows",
        sa.Column(
            "id",
            sa.BigInteger,
            sa.Identity(always=False),
            primary_key=True,
        ),
        sa.Column(
            "job_id",
            PgUUID(as_uuid=True),
            sa.ForeignKey("hypershop_bulk_upload_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("row_number", sa.Integer, nullable=False),
        sa.Column("raw_row", JSONB, nullable=False),
        sa.Column("error_code", sa.String(64), nullable=False),
        sa.Column("error_message", sa.String(512), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
    )
    op.create_index(
        "ix_hypershop_bulk_upload_rows_job_row",
        "hypershop_bulk_upload_rows",
        ["job_id", "row_number"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_hypershop_bulk_upload_rows_job_row",
        table_name="hypershop_bulk_upload_rows",
    )
    op.drop_table("hypershop_bulk_upload_rows")
    op.drop_index(
        "ix_hypershop_bulk_upload_jobs_pending",
        table_name="hypershop_bulk_upload_jobs",
    )
    op.drop_index(
        "ix_hypershop_bulk_upload_jobs_seller_created",
        table_name="hypershop_bulk_upload_jobs",
    )
    op.drop_table("hypershop_bulk_upload_jobs")
