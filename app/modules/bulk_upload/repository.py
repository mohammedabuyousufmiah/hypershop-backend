"""Async CRUD helpers for bulk_upload."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.bulk_upload.codes import STATUS_QUEUED
from app.modules.bulk_upload.models import (
    HypershopBulkUploadJob,
    HypershopBulkUploadRow,
)


async def create_job(
    session: AsyncSession,
    *,
    seller_id: UUID,
    uploaded_by_user_id: UUID,
    **fields: Any,
) -> HypershopBulkUploadJob:
    row = HypershopBulkUploadJob(
        seller_id=seller_id,
        uploaded_by_user_id=uploaded_by_user_id,
        **fields,
    )
    session.add(row)
    await session.flush()
    return row


async def get_job(
    session: AsyncSession, job_id: UUID,
) -> HypershopBulkUploadJob | None:
    return await session.get(HypershopBulkUploadJob, job_id)


async def lock_job_for_update(
    session: AsyncSession, job_id: UUID,
) -> HypershopBulkUploadJob | None:
    stmt = (
        select(HypershopBulkUploadJob)
        .where(HypershopBulkUploadJob.id == job_id)
        .with_for_update()
    )
    return (await session.execute(stmt)).scalars().first()


async def update_job(
    session: AsyncSession, job_id: UUID, **fields: Any,
) -> HypershopBulkUploadJob | None:
    fields["updated_at"] = datetime.now(timezone.utc)
    await session.execute(
        update(HypershopBulkUploadJob)
        .where(HypershopBulkUploadJob.id == job_id)
        .values(**fields)
    )
    return await session.get(HypershopBulkUploadJob, job_id)


async def list_jobs_for_seller(
    session: AsyncSession,
    seller_id: UUID,
    *,
    status: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[HypershopBulkUploadJob], int]:
    base = select(HypershopBulkUploadJob).where(
        HypershopBulkUploadJob.seller_id == seller_id,
    )
    if status is not None:
        base = base.where(HypershopBulkUploadJob.status == status)
    total = (
        await session.execute(
            select(func.count()).select_from(base.subquery()),
        )
    ).scalar_one()
    stmt = (
        base.order_by(HypershopBulkUploadJob.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return list(rows), int(total)


async def list_jobs_admin(
    session: AsyncSession,
    *,
    status: str | None = None,
    seller_id: UUID | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[HypershopBulkUploadJob], int]:
    base = select(HypershopBulkUploadJob)
    if status is not None:
        base = base.where(HypershopBulkUploadJob.status == status)
    if seller_id is not None:
        base = base.where(HypershopBulkUploadJob.seller_id == seller_id)
    total = (
        await session.execute(
            select(func.count()).select_from(base.subquery()),
        )
    ).scalar_one()
    stmt = (
        base.order_by(HypershopBulkUploadJob.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return list(rows), int(total)


async def list_pending_jobs_for_ingest(
    session: AsyncSession, *, limit: int = 10,
) -> list[HypershopBulkUploadJob]:
    stmt = (
        select(HypershopBulkUploadJob)
        .where(HypershopBulkUploadJob.status == STATUS_QUEUED)
        .order_by(HypershopBulkUploadJob.created_at.asc())
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    return list((await session.execute(stmt)).scalars().all())


async def add_error_row(
    session: AsyncSession,
    *,
    job_id: UUID,
    row_number: int,
    raw_row: dict,
    error_code: str,
    error_message: str,
) -> HypershopBulkUploadRow:
    row = HypershopBulkUploadRow(
        job_id=job_id,
        row_number=row_number,
        raw_row=raw_row,
        error_code=error_code,
        error_message=error_message[:512],
    )
    session.add(row)
    await session.flush()
    return row


async def list_error_rows(
    session: AsyncSession,
    job_id: UUID,
    *,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[HypershopBulkUploadRow], int]:
    base = select(HypershopBulkUploadRow).where(
        HypershopBulkUploadRow.job_id == job_id,
    )
    total = (
        await session.execute(
            select(func.count()).select_from(base.subquery()),
        )
    ).scalar_one()
    stmt = (
        base.order_by(HypershopBulkUploadRow.row_number.asc())
        .limit(limit)
        .offset(offset)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return list(rows), int(total)
