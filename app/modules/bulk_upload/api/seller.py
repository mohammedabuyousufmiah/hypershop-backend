"""Seller-facing bulk-upload endpoints."""
from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse

from app.core.db.uow import UnitOfWork, get_uow
from app.core.security.deps import get_current_principal
from app.core.security.principal import Principal
from app.modules.bulk_upload import repository as repo
from app.modules.bulk_upload import service
from app.modules.bulk_upload.codes import (
    OPTIONAL_COLUMNS,
    REQUIRED_COLUMNS,
)
from app.modules.bulk_upload.schemas import (
    BulkUploadJobCreate,
    BulkUploadJobListResponse,
    BulkUploadJobRead,
    BulkUploadRowListResponse,
    BulkUploadRowRead,
)
from app.modules.sellers.deps import get_current_seller_id

router = APIRouter(prefix="/seller/bulk-upload", tags=["seller-bulk-upload"])


@router.post("/jobs", summary="Register a bulk-upload job")
async def create_job(
    body: BulkUploadJobCreate,
    seller_id: Annotated[UUID, Depends(get_current_seller_id)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict:
    async with uow.transactional() as session:
        try:
            return await service.create_upload_job(
                session,
                seller_id=seller_id,
                uploaded_by_user_id=principal.user_id,
                file_url=body.file_url,
                file_size_bytes=body.file_size_bytes,
                original_filename=body.original_filename,
                file_format=body.file_format,
            )
        except service.FileTooLarge as e:
            raise HTTPException(status_code=413, detail=str(e)) from e
        except service.UnsupportedFormat as e:
            raise HTTPException(status_code=415, detail=str(e)) from e


@router.get("/jobs", summary="List my upload jobs")
async def list_jobs(
    seller_id: Annotated[UUID, Depends(get_current_seller_id)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    async with uow.transactional() as session:
        rows, total = await repo.list_jobs_for_seller(
            session, seller_id,
            status=status_filter, limit=limit, offset=offset,
        )
    return BulkUploadJobListResponse(
        items=[BulkUploadJobRead.model_validate(r) for r in rows],
        total=total, limit=limit, offset=offset,
    ).model_dump(mode="json")


@router.get("/jobs/{job_id}", summary="Read one of my jobs")
async def read_job(
    job_id: UUID,
    seller_id: Annotated[UUID, Depends(get_current_seller_id)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict:
    async with uow.transactional() as session:
        job = await repo.get_job(session, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found.")
        if job.seller_id != seller_id:
            raise HTTPException(status_code=403, detail="Not your job.")
    return BulkUploadJobRead.model_validate(job).model_dump(mode="json")


@router.get("/jobs/{job_id}/errors", summary="List failed rows for fix-and-retry")
async def list_errors(
    job_id: UUID,
    seller_id: Annotated[UUID, Depends(get_current_seller_id)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    async with uow.transactional() as session:
        job = await repo.get_job(session, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found.")
        if job.seller_id != seller_id:
            raise HTTPException(status_code=403, detail="Not your job.")
        rows, total = await repo.list_error_rows(
            session, job_id, limit=limit, offset=offset,
        )
    return BulkUploadRowListResponse(
        items=[BulkUploadRowRead.model_validate(r) for r in rows],
        total=total, limit=limit, offset=offset,
    ).model_dump(mode="json")


@router.post("/jobs/{job_id}/cancel", summary="Cancel a queued/running job")
async def cancel_job(
    job_id: UUID,
    seller_id: Annotated[UUID, Depends(get_current_seller_id)],
    principal: Annotated[Principal, Depends(get_current_principal)],
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> dict:
    async with uow.transactional() as session:
        job = await repo.get_job(session, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found.")
        if job.seller_id != seller_id:
            raise HTTPException(status_code=403, detail="Not your job.")
        try:
            return await service.cancel_job(
                session, job_id=job_id, by_user_id=principal.user_id,
            )
        except service.BulkUploadError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e


_TEMPLATE_COMMENT = (
    "# Hypershop bulk product upload template\n"
    "# Required: sku,title,brand,category,price_minor,stock_qty\n"
    "# Optional: description,image_url,variant_attrs_json,"
    "weight_grams,barcode,compare_at_price_minor\n"
    "# price_minor + compare_at_price_minor are in PAISA (1/100 of BDT).\n"
    "# brand = brand name (case-insensitive); category = category slug.\n"
)


@router.get(
    "/template.csv",
    summary="Download a sample CSV template",
    response_class=PlainTextResponse,
)
async def download_template() -> PlainTextResponse:
    header = ",".join(REQUIRED_COLUMNS + OPTIONAL_COLUMNS) + "\n"
    sample = (
        "DEMO-SKU-001,Example Product,Hypershop,electronics,29900,100,"
        "Sample description,https://cdn.example.com/img1.jpg,{},250,"
        "8901234567890,39900\n"
    )
    body = _TEMPLATE_COMMENT + header + sample
    return PlainTextResponse(
        body,
        headers={
            "Content-Disposition": (
                'attachment; filename="bulk_upload_template.csv"'
            ),
        },
    )
