"""Bulk upload service — registers jobs and runs the ingest worker.

Soft-fail per row; one bad row never kills the whole job. Successful
rows commit in BATCH_SIZE batches with savepoints so a mid-batch crash
doesn't lose progress on earlier batches.
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db.uow import UnitOfWork
from app.core.logging import get_logger
from app.modules.bulk_upload import repository as repo
from app.modules.bulk_upload.codes import (
    ALLOWED_FORMATS,
    BATCH_SIZE,
    ERR_DUPLICATE_SKU,
    ERR_INVALID_PRICE,
    ERR_INVALID_SKU,
    ERR_INVALID_STOCK,
    ERR_MISSING_BRAND,
    ERR_MISSING_CATEGORY,
    ERR_MISSING_COL,
    ERR_OTHER,
    FAIL_RATIO_THRESHOLD,
    MAX_FILE_SIZE_BYTES,
    MAX_ROWS_PER_FILE,
    REQUIRED_COLUMNS,
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_INGESTING,
    STATUS_QUEUED,
    STATUS_VALIDATING,
)
from app.modules.bulk_upload.parser import XlsxNotSupported, parse_file
from app.modules.catalog.models import (
    Brand,
    Category,
    Product,
    ProductStatus,
    ProductVariant,
)
from app.modules.catalog.slugify import slugify

_log = get_logger("hypershop.bulk_upload")

_SKU_RE = re.compile(r"^[A-Za-z0-9_\-]{3,64}$")
_DEFAULT_CURRENCY = "BDT"


class BulkUploadError(Exception):
    pass


class FileTooLarge(BulkUploadError):
    pass


class UnsupportedFormat(BulkUploadError):
    pass


class TooManyRows(BulkUploadError):
    pass


async def create_upload_job(
    session: AsyncSession,
    *,
    seller_id: UUID,
    uploaded_by_user_id: UUID,
    file_url: str,
    file_size_bytes: int,
    original_filename: str,
    file_format: str,
) -> dict:
    fmt = (file_format or "").lower()
    if fmt not in ALLOWED_FORMATS:
        raise UnsupportedFormat(
            f"file_format must be one of {ALLOWED_FORMATS}, got {file_format!r}",
        )
    if file_size_bytes <= 0 or file_size_bytes > MAX_FILE_SIZE_BYTES:
        raise FileTooLarge(
            f"file_size_bytes must be 1..{MAX_FILE_SIZE_BYTES}",
        )
    job = await repo.create_job(
        session,
        seller_id=seller_id,
        uploaded_by_user_id=uploaded_by_user_id,
        original_filename=original_filename[:256],
        file_url=file_url,
        file_size_bytes=file_size_bytes,
        file_format=fmt,
        status=STATUS_QUEUED,
    )
    return {
        "id": str(job.id),
        "status": job.status,
        "created_at": job.created_at.isoformat(),
    }


async def cancel_job(
    session: AsyncSession, *, job_id: UUID, by_user_id: UUID,
) -> dict:
    job = await repo.lock_job_for_update(session, job_id)
    if job is None:
        raise BulkUploadError("job not found")
    if job.status in (STATUS_COMPLETED, STATUS_FAILED, STATUS_CANCELLED):
        return {"id": str(job.id), "status": job.status, "noop": True}
    await repo.update_job(
        session,
        job_id,
        status=STATUS_CANCELLED,
        finished_at=datetime.now(timezone.utc),
    )
    return {"id": str(job_id), "status": STATUS_CANCELLED, "noop": False}


def _to_int(v: Any) -> int | None:
    if v is None or v == "":
        return None
    try:
        return int(str(v).strip())
    except (ValueError, TypeError):
        return None


def _to_decimal(v: Any) -> Decimal | None:
    if v is None or v == "":
        return None
    try:
        return Decimal(str(v).strip())
    except (InvalidOperation, ValueError):
        return None


def _validate_row(
    row: dict,
    existing_skus: set[str],
    brand_map: dict[str, UUID],
    cat_map: dict[str, UUID],
) -> tuple[bool, str | None, str | None]:
    """Returns (is_valid, error_code, error_message)."""
    for col in REQUIRED_COLUMNS:
        val = row.get(col)
        if val is None or str(val).strip() == "":
            return False, ERR_MISSING_COL, f"missing required column: {col}"

    sku = str(row["sku"]).strip()
    if not _SKU_RE.match(sku):
        return False, ERR_INVALID_SKU, (
            f"sku must match ^[A-Za-z0-9_-]{{3,64}}$, got {sku!r}"
        )
    if sku in existing_skus:
        return False, ERR_DUPLICATE_SKU, f"sku already exists: {sku}"

    brand_key = str(row["brand"]).strip().lower()
    if brand_key not in brand_map:
        return False, ERR_MISSING_BRAND, f"brand not found: {row['brand']!r}"

    cat_key = str(row["category"]).strip().lower()
    if cat_key not in cat_map:
        return False, ERR_MISSING_CATEGORY, (
            f"category not found: {row['category']!r}"
        )

    price_minor = _to_int(row["price_minor"])
    if price_minor is None or price_minor < 0:
        return False, ERR_INVALID_PRICE, (
            f"price_minor must be non-negative integer, got {row['price_minor']!r}"
        )

    stock = _to_int(row["stock_qty"])
    if stock is None or stock < 0:
        return False, ERR_INVALID_STOCK, (
            f"stock_qty must be non-negative integer, got {row['stock_qty']!r}"
        )

    cmp_raw = row.get("compare_at_price_minor")
    if cmp_raw not in (None, ""):
        cmp_minor = _to_int(cmp_raw)
        if cmp_minor is None or cmp_minor < price_minor:
            return False, ERR_INVALID_PRICE, (
                "compare_at_price_minor must be >= price_minor"
            )

    return True, None, None


async def _load_brand_map(session: AsyncSession) -> dict[str, UUID]:
    rows = (await session.execute(select(Brand.id, Brand.name))).all()
    return {name.strip().lower(): bid for bid, name in rows}


async def _load_category_map(session: AsyncSession) -> dict[str, UUID]:
    rows = (await session.execute(select(Category.id, Category.slug))).all()
    return {slug.strip().lower(): cid for cid, slug in rows}


async def _load_existing_skus(session: AsyncSession) -> set[str]:
    rows = (await session.execute(select(ProductVariant.sku))).scalars().all()
    return set(rows)


async def _insert_product_and_variant(
    session: AsyncSession,
    *,
    seller_id: UUID,
    row: dict,
    brand_id: UUID,
    cat_id: UUID,
) -> None:
    sku = str(row["sku"]).strip()
    title = str(row["title"]).strip()[:200]
    price_minor = int(row["price_minor"])
    price = (Decimal(price_minor) / Decimal(100)).quantize(Decimal("0.01"))
    cmp_at = None
    if row.get("compare_at_price_minor") not in (None, ""):
        cmp_at = (
            Decimal(int(row["compare_at_price_minor"])) / Decimal(100)
        ).quantize(Decimal("0.01"))

    slug_base = slugify(f"{title}-{sku}")
    product = Product(
        slug=slug_base[:160],
        name=title,
        short_description=(row.get("description") or "")[:512] or None,
        description=(row.get("description") or "") or None,
        brand_id=brand_id,
        category_id=cat_id,
        seller_id=seller_id,
        status=ProductStatus.ACTIVE,
        base_currency=_DEFAULT_CURRENCY,
        mother_sku=f"HS-{sku.upper()[:30]}",
        is_medicine=False,
        requires_prescription=False,
    )
    session.add(product)
    await session.flush()

    weight = _to_int(row.get("weight_grams"))
    barcode_raw = (row.get("barcode") or "").strip() or None
    variant = ProductVariant(
        product_id=product.id,
        sku=sku,
        barcode=barcode_raw,
        name=title,
        price=price,
        compare_at_price=cmp_at,
        weight_grams=weight,
    )
    session.add(variant)
    await session.flush()


async def _download_file(url: str) -> bytes:
    async with httpx.AsyncClient(timeout=60.0) as client:
        r = await client.get(url)
        r.raise_for_status()
        return r.content


async def process_job(job_id: UUID) -> dict:
    """Run one bulk upload job end-to-end. Caller owns no transaction;
    this function opens its own UoWs (one for header transitions, then
    one per BATCH_SIZE rows for savepoint isolation).
    """
    async with UnitOfWork().transactional() as session:
        job = await repo.lock_job_for_update(session, job_id)
        if job is None:
            return {"id": str(job_id), "skipped": "not_found"}
        if job.status != STATUS_QUEUED:
            return {"id": str(job_id), "skipped": f"status={job.status}"}
        await repo.update_job(
            session,
            job_id,
            status=STATUS_VALIDATING,
            started_at=datetime.now(timezone.utc),
        )
        seller_id = job.seller_id
        file_url = job.file_url
        file_format = job.file_format

    # ----- download + parse outside the long transaction -----
    try:
        file_bytes = await _download_file(file_url)
    except Exception as e:  # noqa: BLE001
        _log.exception("bulk_upload_download_failed", job_id=str(job_id))
        async with UnitOfWork().transactional() as session:
            await repo.update_job(
                session,
                job_id,
                status=STATUS_FAILED,
                finished_at=datetime.now(timezone.utc),
                error_summary={"download_error": str(e)[:300]},
            )
        return {"id": str(job_id), "status": STATUS_FAILED, "reason": "download"}

    try:
        parsed_rows = list(parse_file(file_bytes, file_format))
    except XlsxNotSupported as e:
        async with UnitOfWork().transactional() as session:
            await repo.update_job(
                session,
                job_id,
                status=STATUS_FAILED,
                finished_at=datetime.now(timezone.utc),
                error_summary={"format_error": str(e)},
            )
        return {"id": str(job_id), "status": STATUS_FAILED, "reason": "xlsx"}
    except Exception as e:  # noqa: BLE001
        async with UnitOfWork().transactional() as session:
            await repo.update_job(
                session,
                job_id,
                status=STATUS_FAILED,
                finished_at=datetime.now(timezone.utc),
                error_summary={"parse_error": str(e)[:300]},
            )
        return {"id": str(job_id), "status": STATUS_FAILED, "reason": "parse"}

    total_rows = len(parsed_rows)
    if total_rows > MAX_ROWS_PER_FILE:
        async with UnitOfWork().transactional() as session:
            await repo.update_job(
                session,
                job_id,
                status=STATUS_FAILED,
                finished_at=datetime.now(timezone.utc),
                total_rows=total_rows,
                error_summary={
                    "too_many_rows": (
                        f"file has {total_rows} rows > max {MAX_ROWS_PER_FILE}"
                    ),
                },
            )
        return {
            "id": str(job_id), "status": STATUS_FAILED, "reason": "too_many",
        }

    # ----- per-job lookup tables -----
    async with UnitOfWork().transactional() as session:
        brand_map = await _load_brand_map(session)
        cat_map = await _load_category_map(session)
        existing_skus = await _load_existing_skus(session)
        await repo.update_job(
            session,
            job_id,
            status=STATUS_INGESTING,
            total_rows=total_rows,
        )

    file_skus: set[str] = set()
    succeeded = 0
    failed = 0
    processed = 0
    error_counter: Counter[str] = Counter()

    # ----- ingest in batches with one UoW per batch -----
    for batch_start in range(0, total_rows, BATCH_SIZE):
        batch = parsed_rows[batch_start: batch_start + BATCH_SIZE]
        async with UnitOfWork().transactional() as session:
            # check cancellation before each batch
            current = await repo.get_job(session, job_id)
            if current is None or current.status == STATUS_CANCELLED:
                break

            for row_num, row in batch:
                processed += 1
                ok, code, msg = _validate_row(
                    row, existing_skus | file_skus, brand_map, cat_map,
                )
                if not ok:
                    failed += 1
                    error_counter[code or ERR_OTHER] += 1
                    await repo.add_error_row(
                        session,
                        job_id=job_id,
                        row_number=row_num,
                        raw_row=dict(row),
                        error_code=code or ERR_OTHER,
                        error_message=msg or "validation failed",
                    )
                    continue

                sku = str(row["sku"]).strip()
                brand_id = brand_map[str(row["brand"]).strip().lower()]
                cat_id = cat_map[str(row["category"]).strip().lower()]
                try:
                    sp = await session.begin_nested()
                    try:
                        await _insert_product_and_variant(
                            session,
                            seller_id=seller_id,
                            row=row,
                            brand_id=brand_id,
                            cat_id=cat_id,
                        )
                        await sp.commit()
                    except Exception:
                        await sp.rollback()
                        raise
                    file_skus.add(sku)
                    succeeded += 1
                except Exception as e:  # noqa: BLE001
                    failed += 1
                    error_counter[ERR_OTHER] += 1
                    await repo.add_error_row(
                        session,
                        job_id=job_id,
                        row_number=row_num,
                        raw_row=dict(row),
                        error_code=ERR_OTHER,
                        error_message=(
                            f"{type(e).__name__}: {e}"
                        )[:512],
                    )

            await repo.update_job(
                session,
                job_id,
                processed_rows=processed,
                succeeded_rows=succeeded,
                failed_rows=failed,
            )

    # ----- finalise -----
    final_status = STATUS_COMPLETED
    if total_rows > 0 and failed / total_rows > FAIL_RATIO_THRESHOLD:
        final_status = STATUS_FAILED

    async with UnitOfWork().transactional() as session:
        current = await repo.get_job(session, job_id)
        if current is not None and current.status == STATUS_CANCELLED:
            return {
                "id": str(job_id),
                "status": STATUS_CANCELLED,
                "processed": processed,
            }
        await repo.update_job(
            session,
            job_id,
            status=final_status,
            finished_at=datetime.now(timezone.utc),
            processed_rows=processed,
            succeeded_rows=succeeded,
            failed_rows=failed,
            error_summary=dict(error_counter) if error_counter else None,
        )
    return {
        "id": str(job_id),
        "status": final_status,
        "total": total_rows,
        "succeeded": succeeded,
        "failed": failed,
    }
