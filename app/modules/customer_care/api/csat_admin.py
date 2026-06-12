"""Admin endpoints — CSAT surveys list + KPI summary."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text as _sa_text

from app.core.db.uow import UnitOfWork, get_uow
from app.core.security.rbac import requires_permission
from app.modules.customer_care import cc_inbox_repository as repo

router = APIRouter(prefix="/admin/cc/csat", tags=["admin-cc-csat"])

_PERM_VIEW = "cc_inbox.view"


def _csat_dict(s: Any) -> dict[str, Any]:
    return {
        "id": str(s.id),
        "thread_id": str(s.thread_id) if s.thread_id else None,
        "voice_call_session_id": (
            str(s.voice_call_session_id) if s.voice_call_session_id else None
        ),
        "customer_user_id": str(s.customer_user_id) if s.customer_user_id else None,
        "channel": s.channel,
        "sent_at": s.sent_at.isoformat() if s.sent_at else None,
        "responded_at": s.responded_at.isoformat() if s.responded_at else None,
        "score": s.score,
        "comment": s.comment,
        "status": s.status,
        "expires_at": s.expires_at.isoformat() if s.expires_at else None,
        "created_at": s.created_at.isoformat(),
    }


@router.get(
    "",
    dependencies=[Depends(requires_permission(_PERM_VIEW))],
    summary="List CSAT surveys",
)
async def list_csat(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    async with uow.transactional() as session:
        rows, total = await repo.list_csat(
            session, status=status_filter, limit=limit, offset=offset,
        )
    return {
        "items": [_csat_dict(r) for r in rows],
        "total": total, "limit": limit, "offset": offset,
    }


@router.get(
    "/summary",
    dependencies=[Depends(requires_permission(_PERM_VIEW))],
    summary="CSAT KPI rollup",
)
async def summary(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    window_days: Annotated[int, Query(ge=1, le=365)] = 30,
) -> dict[str, Any]:
    since = datetime.now(timezone.utc) - timedelta(days=window_days)
    async with uow.transactional() as session:
        totals = (await session.execute(
            _sa_text(
                """
                SELECT
                  COUNT(*) AS total,
                  COUNT(*) FILTER (WHERE status = 'sent') AS sent,
                  COUNT(*) FILTER (WHERE status = 'responded') AS responded,
                  COUNT(*) FILTER (WHERE status = 'expired') AS expired,
                  AVG(score)::float AS avg_score
                  FROM hypershop_cc_csat_surveys
                 WHERE created_at >= :since
                """,
            ),
            {"since": since},
        )).mappings().one()
        by_chan = (await session.execute(
            _sa_text(
                """
                SELECT channel,
                       COUNT(*) AS total,
                       COUNT(*) FILTER (WHERE status='responded') AS responded,
                       AVG(score)::float AS avg_score
                  FROM hypershop_cc_csat_surveys
                 WHERE created_at >= :since
                 GROUP BY channel
                """,
            ),
            {"since": since},
        )).mappings().all()
    total_sent = int(totals["sent"] or 0) + int(totals["responded"] or 0)
    resp = int(totals["responded"] or 0)
    resp_rate = round(resp / total_sent, 4) if total_sent > 0 else 0.0
    return {
        "window_days": window_days,
        "total": int(totals["total"] or 0),
        "sent": int(totals["sent"] or 0),
        "responded": resp,
        "expired": int(totals["expired"] or 0),
        "avg_score": float(totals["avg_score"]) if totals["avg_score"] else None,
        "response_rate": resp_rate,
        "by_channel": [
            {
                "channel": r["channel"],
                "total": int(r["total"]),
                "responded": int(r["responded"]),
                "avg_score": float(r["avg_score"]) if r["avg_score"] else None,
            }
            for r in by_chan
        ],
    }
