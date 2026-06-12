"""Admin write endpoints for the SEO agents sub-module.

Mounted under /api/v1/admin/seo/agents/* by ``seo_api_router``.

Permissions: ``catalog.write`` (same perm the rest of the SEO admin
surface uses — SEO is owned by the merchandising team).

Audit:
  - seo.agent.keyword_created
  - seo.agent.keyword_analysed
  - seo.agent.task_approved / rejected
  - seo.agent.rank_snapshot_added
  - seo.agent.page_audited
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.core.audit import record_audit
from app.core.db.uow import UnitOfWork, get_uow
from app.core.security.deps import get_current_principal
from app.core.security.principal import Principal
from app.core.security.rbac import requires_permission
from app.modules.seo.agents import service as svc
from app.modules.seo.agents.models import (
    SeoAgentKeyword,
    SeoAgentPageAudit,
    SeoAgentRankSnapshot,
    SeoAgentTask,
)
from app.modules.seo.agents.schemas import (
    SeoAgentAnalyseResponse,
    SeoAgentApprovalRequest,
    SeoAgentDashboardResponse,
    SeoAgentKeywordCreate,
    SeoAgentKeywordListResponse,
    SeoAgentKeywordResponse,
    SeoAgentPageAuditRequest,
    SeoAgentPageAuditResponse,
    SeoAgentRankListResponse,
    SeoAgentRankSnapshotCreate,
    SeoAgentRankSnapshotResponse,
    SeoAgentTaskListResponse,
    SeoAgentTaskResponse,
)

router = APIRouter(prefix="/admin/seo/agents", tags=["admin-seo-agents"])

_WRITE = "catalog.write"


# ============================================================
#  Keywords
# ============================================================
@router.post(
    "/keywords",
    response_model=SeoAgentKeywordResponse,
    status_code=201,
    summary="Register a keyword for SEO tracking + analysis",
    dependencies=[Depends(requires_permission(_WRITE))],
)
async def create_keyword(
    body: SeoAgentKeywordCreate,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> SeoAgentKeywordResponse:
    async with uow.transactional() as session:
        row = await svc.create_keyword(
            session,
            keyword=body.keyword,
            target_location=body.target_location,
            priority=body.priority,
            keyword_type=body.keyword_type,
            target_url=body.target_url,
            user_id=getattr(principal, "user_id", None),
        )
        await record_audit(
            actor=principal,
            action="seo.agent.keyword_created",
            resource_type="seo_agent_keyword",
            resource_id=row.id,
            metadata={
                "keyword": row.keyword,
                "target_location": row.target_location,
            },
        )
    return _keyword_to_response(row)


@router.get(
    "/keywords",
    response_model=SeoAgentKeywordListResponse,
    summary="List tracked SEO keywords (newest first)",
    dependencies=[Depends(requires_permission(_WRITE))],
)
async def list_keywords(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> SeoAgentKeywordListResponse:
    async with uow.transactional() as session:
        rows = await svc.list_keywords(session, limit=limit)
    items = [_keyword_to_response(r) for r in rows]
    return SeoAgentKeywordListResponse(items=items, total=len(items))


@router.post(
    "/keywords/{keyword_id}/analyse",
    response_model=SeoAgentAnalyseResponse,
    summary="Run the 5-agent orchestrator on a keyword",
    dependencies=[Depends(requires_permission(_WRITE))],
)
async def analyse_keyword(
    keyword_id: UUID,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> SeoAgentAnalyseResponse:
    """Three-phase orchestration so the OpenAI HTTP calls (up to 3 of
    them, each 5-30s) DON'T sit inside an open DB transaction.

    1. Short txn — load keyword + check for a recent run.
       If a run within 5 min exists → return its cached output (no
       new OpenAI calls, no duplicate task rows). Same audit emit.
    2. NO txn — run the 5 agents. OpenAI / fallback only.
    3. Short txn — write run row + 3 tasks + status flip + audit.
    """
    # --- Phase 1: prepare (short txn) ---
    async with uow.transactional() as session:
        prep_or_cache = await svc.prepare_analysis(session, keyword_id)

    if isinstance(prep_or_cache, svc.CachedAnalysis):
        # Recent-run dedup hit — no OpenAI, no new tasks.
        async with uow.transactional() as session:
            await record_audit(
                actor=principal,
                action="seo.agent.keyword_analyse_deduped",
                resource_type="seo_agent_keyword",
                resource_id=keyword_id,
                metadata={
                    "cached_run_id": str(prep_or_cache.run_id),
                    "reused_task_ids": [
                        str(t) for t in prep_or_cache.task_ids
                    ],
                },
            )
        return SeoAgentAnalyseResponse(
            keyword_id=prep_or_cache.keyword_id,
            **prep_or_cache.output_payload,
            created_task_ids=prep_or_cache.task_ids,
        )

    # --- Phase 2: run agents OUTSIDE any DB txn ---
    agent_output = svc.run_agents(prep_or_cache)

    # --- Phase 3: persist (short txn) ---
    async with uow.transactional() as session:
        output = await svc.persist_analysis(
            session, prep_or_cache, agent_output,
        )
        await record_audit(
            actor=principal,
            action="seo.agent.keyword_analysed",
            resource_type="seo_agent_keyword",
            resource_id=keyword_id,
            metadata={
                "created_task_ids": [
                    str(t) for t in output["created_task_ids"]
                ],
            },
        )
    return SeoAgentAnalyseResponse(**output)


# ============================================================
#  Tasks
# ============================================================
@router.get(
    "/tasks",
    response_model=SeoAgentTaskListResponse,
    summary="List agent-generated tasks (optionally filtered by status)",
    dependencies=[Depends(requires_permission(_WRITE))],
)
async def list_tasks(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    status: Annotated[str | None, Query(max_length=30)] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 300,
) -> SeoAgentTaskListResponse:
    async with uow.transactional() as session:
        rows = await svc.list_tasks(session, status=status, limit=limit)
    items = [_task_to_response(r) for r in rows]
    return SeoAgentTaskListResponse(items=items, total=len(items))


@router.post(
    "/tasks/{task_id}/approve",
    response_model=SeoAgentTaskResponse,
    summary="Approve a pending agent task",
    dependencies=[Depends(requires_permission(_WRITE))],
)
async def approve_task(
    task_id: UUID,
    body: SeoAgentApprovalRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> SeoAgentTaskResponse:
    async with uow.transactional() as session:
        row = await svc.approve_task(
            session,
            task_id=task_id,
            actor_id=getattr(principal, "user_id", None),
            comment=body.comment,
        )
        await record_audit(
            actor=principal,
            action="seo.agent.task_approved",
            resource_type="seo_agent_task",
            resource_id=row.id,
        )
    return _task_to_response(row)


@router.post(
    "/tasks/{task_id}/reject",
    response_model=SeoAgentTaskResponse,
    summary="Reject a pending agent task",
    dependencies=[Depends(requires_permission(_WRITE))],
)
async def reject_task(
    task_id: UUID,
    body: SeoAgentApprovalRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> SeoAgentTaskResponse:
    async with uow.transactional() as session:
        row = await svc.reject_task(
            session,
            task_id=task_id,
            actor_id=getattr(principal, "user_id", None),
            comment=body.comment,
        )
        await record_audit(
            actor=principal,
            action="seo.agent.task_rejected",
            resource_type="seo_agent_task",
            resource_id=row.id,
        )
    return _task_to_response(row)


# ============================================================
#  Rank snapshots
# ============================================================
@router.post(
    "/rank-snapshots",
    response_model=SeoAgentRankSnapshotResponse,
    status_code=201,
    summary="Record a rank tracking snapshot for a keyword",
    dependencies=[Depends(requires_permission(_WRITE))],
)
async def add_rank_snapshot(
    body: SeoAgentRankSnapshotCreate,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> SeoAgentRankSnapshotResponse:
    async with uow.transactional() as session:
        row = await svc.add_rank_snapshot(
            session,
            keyword_id=body.keyword_id,
            target_url=body.target_url,
            position=body.position,
            location=body.location,
            device=body.device,
            impressions=body.impressions,
            clicks=body.clicks,
            ctr=body.ctr,
            source=body.source,
        )
        await record_audit(
            actor=principal,
            action="seo.agent.rank_snapshot_added",
            resource_type="seo_agent_rank_snapshot",
            resource_id=row.id,
            metadata={
                "keyword_id": str(body.keyword_id),
                "position": body.position,
                "source": body.source,
            },
        )
    return _rank_to_response(row)


@router.get(
    "/rank-tracking",
    response_model=SeoAgentRankListResponse,
    summary="List recent rank snapshots (newest first)",
    dependencies=[Depends(requires_permission(_WRITE))],
)
async def rank_tracking(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    limit: Annotated[int, Query(ge=1, le=2000)] = 500,
) -> SeoAgentRankListResponse:
    async with uow.transactional() as session:
        rows = await svc.list_rank_snapshots(session, limit=limit)
    items = [_rank_to_response(r) for r in rows]
    return SeoAgentRankListResponse(items=items, total=len(items))


# ============================================================
#  Technical audit
# ============================================================
@router.post(
    "/technical-audit",
    response_model=SeoAgentPageAuditResponse,
    status_code=201,
    summary="Run a technical SEO audit on a single URL (HTML inspection)",
    dependencies=[Depends(requires_permission(_WRITE))],
)
async def technical_audit(
    body: SeoAgentPageAuditRequest,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    principal: Annotated[Principal, Depends(get_current_principal)],
) -> SeoAgentPageAuditResponse:
    async with uow.transactional() as session:
        row = await svc.technical_audit(
            session, url=body.url, html=body.html,
        )
        await record_audit(
            actor=principal,
            action="seo.agent.page_audited",
            resource_type="seo_agent_page_audit",
            resource_id=row.id,
            metadata={"url": body.url, "score": row.score},
        )
    return _audit_to_response(row)


# ============================================================
#  Dashboard
# ============================================================
@router.get(
    "/dashboard",
    response_model=SeoAgentDashboardResponse,
    summary="Aggregate SEO agent KPIs (keywords + tasks + rank + audit)",
    dependencies=[Depends(requires_permission(_WRITE))],
)
async def dashboard(
    uow: Annotated[UnitOfWork, Depends(get_uow)],
) -> SeoAgentDashboardResponse:
    async with uow.transactional() as session:
        data = await svc.dashboard(session)
    return SeoAgentDashboardResponse(**data)


# ============================================================
#  Response shapers
# ============================================================
def _keyword_to_response(r: SeoAgentKeyword) -> SeoAgentKeywordResponse:
    return SeoAgentKeywordResponse(
        id=r.id,
        keyword=r.keyword,
        target_location=r.target_location,
        priority=r.priority,
        keyword_type=r.keyword_type,
        target_url=r.target_url,
        status=r.status,
        created_by=r.created_by,
        created_at=r.created_at,
        updated_at=r.updated_at,
    )


def _task_to_response(r: SeoAgentTask) -> SeoAgentTaskResponse:
    return SeoAgentTaskResponse(
        id=r.id,
        keyword_id=r.keyword_id,
        task_type=r.task_type,
        title=r.title,
        description=r.description,
        payload=r.payload or {},
        priority=r.priority,
        status=r.status,
        approved_by=r.approved_by,
        approved_at=r.approved_at,
        created_at=r.created_at,
    )


def _rank_to_response(
    r: SeoAgentRankSnapshot,
) -> SeoAgentRankSnapshotResponse:
    return SeoAgentRankSnapshotResponse(
        id=r.id,
        keyword_id=r.keyword_id,
        target_url=r.target_url,
        position=r.position,
        location=r.location,
        device=r.device,
        impressions=r.impressions,
        clicks=r.clicks,
        ctr=r.ctr,
        source=r.source,
        captured_at=r.captured_at,
    )


def _audit_to_response(r: SeoAgentPageAudit) -> SeoAgentPageAuditResponse:
    return SeoAgentPageAuditResponse(
        id=r.id,
        url=r.url,
        score=r.score,
        issues=r.issues or [],
        recommendations=r.recommendations or [],
        created_at=r.created_at,
    )
