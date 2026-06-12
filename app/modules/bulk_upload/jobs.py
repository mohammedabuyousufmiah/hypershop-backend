"""ARQ jobs for bulk_upload."""
from __future__ import annotations

from typing import Any

from app.core.db.uow import UnitOfWork
from app.core.logging import get_logger
from app.modules.bulk_upload import repository as repo
from app.modules.bulk_upload import service

_log = get_logger("hypershop.bulk_upload.jobs")

_MAX_PER_TICK = 3


async def process_bulk_upload_jobs_job(_ctx: dict[str, Any]) -> int:
    """Pick up to MAX queued bulk upload jobs and run them serially.

    Concurrency is intentionally low (3) because each job parses an
    in-memory CSV and runs catalog INSERTs in batches; spawning many
    parallel ingests would thrash the DB connection pool.
    """
    job_ids: list = []
    async with UnitOfWork().transactional() as session:
        pending = await repo.list_pending_jobs_for_ingest(
            session, limit=_MAX_PER_TICK,
        )
        job_ids = [j.id for j in pending]

    handled = 0
    for jid in job_ids:
        try:
            await service.process_job(jid)
            handled += 1
        except Exception as e:  # noqa: BLE001
            _log.exception(
                "bulk_upload_process_job_failed",
                job_id=str(jid),
                error=str(e),
            )
    return handled


async def process_bulk_upload_one(
    _ctx: dict[str, Any], job_id_hex: str,
) -> bool:
    """ARQ direct-dispatch entry point for a single job."""
    from uuid import UUID
    try:
        jid = UUID(job_id_hex)
    except ValueError:
        return False
    await service.process_job(jid)
    return True
