"""Pydantic schemas for bulk_upload API."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

FormatLiteral = Literal["csv", "xlsx", "tsv"]
StatusLiteral = Literal[
    "queued", "validating", "ingesting", "completed", "failed", "cancelled",
]


class BulkUploadJobCreate(BaseModel):
    file_url: str = Field(min_length=1, max_length=4096)
    original_filename: str = Field(min_length=1, max_length=256)
    file_size_bytes: int = Field(gt=0)
    file_format: FormatLiteral


class BulkUploadJobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    seller_id: UUID
    uploaded_by_user_id: UUID
    original_filename: str
    file_url: str
    file_size_bytes: int
    file_format: str
    total_rows: int
    processed_rows: int
    succeeded_rows: int
    failed_rows: int
    status: str
    error_summary: dict[str, Any] | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime


class BulkUploadJobListResponse(BaseModel):
    items: list[BulkUploadJobRead]
    total: int
    limit: int
    offset: int


class BulkUploadRowRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    job_id: UUID
    row_number: int
    raw_row: dict[str, Any]
    error_code: str
    error_message: str
    created_at: datetime


class BulkUploadRowListResponse(BaseModel):
    items: list[BulkUploadRowRead]
    total: int
    limit: int
    offset: int
