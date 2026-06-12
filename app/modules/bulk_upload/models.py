"""ORM for bulk_upload — 2 tables (hypershop_bulk_upload_jobs +
hypershop_bulk_upload_rows).
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db.base import Base


class HypershopBulkUploadJob(Base):
    __tablename__ = "hypershop_bulk_upload_jobs"

    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    seller_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("sellers.id", ondelete="CASCADE"),
        nullable=False,
    )
    uploaded_by_user_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), nullable=False,
    )
    original_filename: Mapped[str] = mapped_column(String(256), nullable=False)
    file_url: Mapped[str] = mapped_column(Text, nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    file_format: Mapped[str] = mapped_column(String(8), nullable=False)
    total_rows: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0"),
    )
    processed_rows: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0"),
    )
    succeeded_rows: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0"),
    )
    failed_rows: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0"),
    )
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=text("'queued'"),
    )
    error_summary: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("(now() AT TIME ZONE 'UTC')"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("(now() AT TIME ZONE 'UTC')"),
    )

    __table_args__ = (
        CheckConstraint(
            "file_size_bytes > 0",
            name="ck_hypershop_bulk_upload_jobs_size_pos",
        ),
        CheckConstraint(
            "file_format IN ('csv','xlsx','tsv')",
            name="ck_hypershop_bulk_upload_jobs_format",
        ),
        CheckConstraint(
            "status IN ('queued','validating','ingesting','completed','failed','cancelled')",
            name="ck_hypershop_bulk_upload_jobs_status",
        ),
        Index(
            "ix_hypershop_bulk_upload_jobs_seller_created",
            "seller_id",
            text("created_at DESC"),
        ),
        Index(
            "ix_hypershop_bulk_upload_jobs_pending",
            "status",
            "created_at",
            postgresql_where=text(
                "status IN ('queued','validating','ingesting')",
            ),
        ),
    )


class HypershopBulkUploadRow(Base):
    __tablename__ = "hypershop_bulk_upload_rows"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("hypershop_bulk_upload_jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_row: Mapped[dict] = mapped_column(JSONB, nullable=False)
    error_code: Mapped[str] = mapped_column(String(64), nullable=False)
    error_message: Mapped[str] = mapped_column(String(512), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("(now() AT TIME ZONE 'UTC')"),
    )

    __table_args__ = (
        Index(
            "ix_hypershop_bulk_upload_rows_job_row",
            "job_id",
            "row_number",
        ),
    )
