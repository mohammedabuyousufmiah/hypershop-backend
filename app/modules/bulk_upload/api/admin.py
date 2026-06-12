"""Admin bulk-upload endpoints."""
from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.db.uow import UnitOfWork, get_uow
from app.core.security.rbac import requires_permission
from app.modules.bulk_upload import repository as repo
from app.modules.bulk_upload.codes import (
    PERM_MANAGE,
    PERM_VIEW,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_QUEUED,
)
from app.modules.bulk_upload.schemas import (
    BulkUploadJobListResponse,
    BulkUploadJobRead,
    BulkUploadRowListResponse,
    BulkUploadRowRead,
)

router = APIRouter(prefix="/admin/bulk-upload", tags=["admin-bulk-upload"])


@router.get(
    "/jobs",
    dependencies=[Depends(requires_permission(PERM_VIEW))],
    summary="List all bulk-upload jobs",
)
async def list_all(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    seller_id: Annotated[UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    async with uow.transactional() as session:
        rows, total = await repo.list_jobs_admin(
            session,
            status=status_filter,
            seller_id=seller_id,
            limit=limit,
            offset=offset,
        )
    return BulkUploadJobListResponse(
        items=[BulkUploadJobRead.model_validate(r) for r in rows],
        total=total, limit=limit, offset=offset,
    ).model_dump(mode="json")


@router.post(
    "/jobs/{job_id}/retry",
    dependencies=[Depends(requires_permission(PERM_MANAGE))],
    summary="Admin force-retry a job (sets status=queued)",
)
async def retry_job(
    job_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict:
    async with uow.transactional() as session:
        job = await repo.get_job(session, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found.")
        if job.status not in (STATUS_COMPLETED, STATUS_FAILED):
            raise HTTPException(
                status_code=409,
                detail=f"cannot retry job in status {job.status}",
            )
        await repo.update_job(
            session,
            job_id,
            status=STATUS_QUEUED,
            processed_rows=0,
            succeeded_rows=0,
            failed_rows=0,
            error_summary=None,
            started_at=None,
            finished_at=None,
        )
    return {"id": str(job_id), "status": STATUS_QUEUED}


@router.get(
    "/jobs/{job_id}/errors",
    dependencies=[Depends(requires_permission(PERM_VIEW))],
    summary="List failed rows of any job (admin — no ownership check)",
)
async def list_job_errors(
    job_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    async with uow.transactional() as session:
        job = await repo.get_job(session, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found.")
        rows, total = await repo.list_error_rows(
            session, job_id, limit=limit, offset=offset,
        )
    return BulkUploadRowListResponse(
        items=[BulkUploadRowRead.model_validate(r) for r in rows],
        total=total, limit=limit, offset=offset,
    ).model_dump(mode="json")
