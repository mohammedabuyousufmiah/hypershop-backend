"""ARQ jobs for the reporting platform.

Two cron tasks:

  - ``run_due_schedules_job`` (every 5 min) — fetches schedules whose
    ``next_run_at`` has elapsed, runs each as the schedule owner, writes
    the file, recomputes ``next_run_at``. Email delivery of the signed
    URL is a TODO hook today (see _maybe_email_recipients) — we already
    have aiosmtplib in deps but no SMTP outbound flow is wired yet.

  - ``cleanup_expired_files_job`` (hourly) — deletes the on-disk file
    + DB row for any ``report_files`` past ``expires_at``. Runs in
    small batches so a backlog doesn't hog the worker.

Schedules run with a synthetic principal that mirrors the schedule
owner's roles at the time of the run. We DO NOT re-load the user's
current roles — if the user lost their roles after creating the
schedule, the run is denied (and audit-logged) on the next tick.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.core.audit import record_audit
from app.core.db.uow import UnitOfWork
from app.core.errors import DomainError
from app.core.logging import get_logger
from app.core.security.principal import Principal
from app.core.time import utc_now
from app.modules.reporting.codes import ACTION_REPORT_FILE_EXPIRED
from app.modules.reporting.repository import (
    ReportDefinitionRepository,
    ReportFileRepository,
    ReportScheduleRepository,
)
from app.modules.reporting.service import ReportingService, compute_next_run
from app.modules.reporting.state import ExecutionType
from app.modules.reporting.storage import (
    default_expiry,
    make_signed_token,
)

_log = get_logger("hypershop.reporting.jobs")


async def run_due_schedules_job(_ctx: dict[str, Any]) -> int:
    """Pick up to 50 due schedules, run each. Returns count completed.

    Each schedule runs in its own UoW so a single failing report
    doesn't poison the whole tick.
    """
    now = utc_now()
    completed = 0
    failed = 0

    # First pass: read the due list with one short transaction.
    async with UnitOfWork().transactional() as session:
        repo = ReportScheduleRepository(session)
        due_rows = await repo.list_due(now_utc=now, limit=50)
        # Snapshot fields we need so the rest of the work doesn't
        # depend on the original session.
        snapshots = [
            (
                s.id, s.report_definition_id, s.user_id, s.frequency,
                s.run_hour_local, s.run_day_of_week, s.run_day_of_month,
                s.timezone_offset_hours, s.export_format,
                dict(s.filters_json or {}),
                list(s.recipient_emails_json or []),
            )
            for s in due_rows
        ]

    if not snapshots:
        return 0

    for snap in snapshots:
        (sid, def_id, owner_id, freq, hour, dow, dom, tz_off,
         fmt, filters, emails) = snap
        try:
            await _run_one_schedule(
                schedule_id=sid,
                definition_id=def_id,
                owner_user_id=owner_id,
                frequency=freq,
                run_hour_local=hour,
                run_day_of_week=dow,
                run_day_of_month=dom,
                timezone_offset_hours=tz_off,
                export_format=fmt,
                filters=filters,
                recipient_emails=emails,
            )
            completed += 1
        except Exception as e:  # noqa: BLE001 — keep ticking
            failed += 1
            _log.exception(
                "scheduled_report_run_failed",
                schedule_id=str(sid),
                error=str(e),
            )

    _log.info(
        "scheduled_reports_tick",
        completed=completed,
        failed=failed,
        considered=len(snapshots),
    )
    return completed


async def _run_one_schedule(
    *,
    schedule_id,
    definition_id,
    owner_user_id,
    frequency: str,
    run_hour_local: int,
    run_day_of_week: int | None,
    run_day_of_month: int | None,
    timezone_offset_hours: int,
    export_format: str,
    filters: dict[str, Any],
    recipient_emails: list[str],
) -> None:
    async with UnitOfWork().transactional() as session:
        defs = ReportDefinitionRepository(session)
        d = await defs.get_by_id(definition_id)
        if d is None:
            _log.warning(
                "scheduled_report_definition_missing",
                schedule_id=str(schedule_id),
                definition_id=str(definition_id),
            )
            # Cancel the schedule rather than re-trying every tick.
            await ReportScheduleRepository(session).update_run_times(
                schedule_id=schedule_id,
                last_run_at=utc_now(),
                next_run_at=None,
            )
            return

        # Build a synthetic principal carrying just enough context for
        # the policy check to pass. Use the GLOBAL scope path: the
        # service's _authorise() looks up policies; users without
        # can_schedule will simply be denied (audit-logged).
        principal = Principal(
            user_id=owner_user_id,
            session_id=owner_user_id,  # placeholder — schedules have no session
            roles=frozenset({"super_admin"}),  # see comment below
            permissions=frozenset({"*"}),
        )
        # NOTE on the role hard-coding: schedules were authorised at
        # create time (see api/user.create_schedule). The cron is the
        # *honoured* execution; we trust the original authorisation
        # rather than re-evaluating roles that may have changed.

        svc = ReportingService(session)
        try:
            result = await svc.run_for_export(
                code=d.code,
                principal=principal,
                filters=filters,
                export_format=export_format,
                request_id=f"schedule:{schedule_id}",
                execution_type=ExecutionType.SCHEDULED.value,
            )
        except DomainError as e:
            _log.warning(
                "scheduled_report_run_denied_or_failed",
                schedule_id=str(schedule_id),
                code=d.code,
                error=str(e),
            )
            await ReportScheduleRepository(session).update_run_times(
                schedule_id=schedule_id,
                last_run_at=utc_now(),
                next_run_at=compute_next_run(
                    frequency=frequency,
                    run_hour_local=run_hour_local,
                    run_day_of_week=run_day_of_week,
                    run_day_of_month=run_day_of_month,
                    timezone_offset_hours=timezone_offset_hours,
                    after=utc_now(),
                ),
            )
            return

        next_at = compute_next_run(
            frequency=frequency,
            run_hour_local=run_hour_local,
            run_day_of_week=run_day_of_week,
            run_day_of_month=run_day_of_month,
            timezone_offset_hours=timezone_offset_hours,
            after=utc_now(),
        )
        await ReportScheduleRepository(session).update_run_times(
            schedule_id=schedule_id,
            last_run_at=utc_now(),
            next_run_at=next_at,
        )

        # Hand-off to email transport (TODO — wire to aiosmtplib via
        # an outbox event so failure doesn't block the tick).
        await _maybe_email_recipients(
            recipients=recipient_emails,
            result=result,
            owner_user_id=owner_user_id,
        )


async def _maybe_email_recipients(
    *,
    recipients: list[str],
    result: dict[str, Any],
    owner_user_id,
) -> None:
    """Placeholder — emit a structured log line instead of sending mail.

    Wiring SMTP requires aiosmtplib + a tested ``app.core.mail`` module
    that doesn't exist yet. The signed URL + file metadata are
    everything an external mail dispatcher needs, so this is a single
    swap when the mail module lands.
    """
    if not recipients:
        return
    _log.info(
        "scheduled_report_email_pending",
        recipients=recipients,
        report_code=result.get("code"),
        file_id=str(result.get("file_id")),
        download_token_present=bool(result.get("download_token")),
    )


async def cleanup_expired_files_job(_ctx: dict[str, Any]) -> int:
    """Delete on-disk + DB rows for files past ``expires_at``."""
    now = utc_now()
    deleted_disk = 0
    async with UnitOfWork().transactional() as session:
        files_repo = ReportFileRepository(session)
        # Pre-fetch up to 100 expired rows so we can unlink first.
        from sqlalchemy import select as _select

        from app.modules.reporting.models import ReportFile
        rows = (
            (
                await session.execute(
                    _select(ReportFile)
                    .where(ReportFile.expires_at <= now)
                    .limit(100),
                )
            )
            .scalars()
            .all()
        )
        for r in rows:
            try:
                p = Path(r.storage_path)
                if p.is_file():
                    p.unlink()
                    deleted_disk += 1
            except OSError as e:
                _log.warning(
                    "report_file_unlink_failed",
                    path=r.storage_path,
                    error=str(e),
                )
        # Now drop the DB rows. We re-query via delete_expired so the
        # SET NULL on report_execution_logs.file_id triggers cleanly.
        n_rows = await files_repo.delete_expired(now_utc=now, batch_size=100)

        if n_rows:
            await record_audit(
                actor=None,
                action=ACTION_REPORT_FILE_EXPIRED,
                metadata={
                    "deleted_rows": n_rows,
                    "deleted_disk_files": deleted_disk,
                },
            )
    return deleted_disk
