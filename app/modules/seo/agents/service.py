"""Service layer for the SEO agents sub-module.

Async + UnitOfWork-style: every public function takes an AsyncSession,
does its DB work, and returns ORM rows. The HTTP layer (api/agents.py)
wraps each call inside ``async with uow.transactional()``.

The orchestrator (``analyze_keyword``) runs 5 agents and writes:
  - 1 row in ``seo_agent_runs`` (audit log with full output)
  - 3 rows in ``seo_agent_tasks`` (pending_approval — landing-page,
    schema, review-trust)
  - 1 status update on the keyword (new → analyzed)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import func as sa_func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.core.logging import get_logger
from app.core.time import utc_now
from app.modules.seo.agents.agents import (
    ImprovementAgent,
    KeywordIntelligenceAgent,
    LocalLandingPageAgent,
    ReviewTrustAgent,
    SchemaAgent,
)
from app.modules.seo.agents.models import (
    SeoAgentApprovalLog,
    SeoAgentKeyword,
    SeoAgentPageAudit,
    SeoAgentRankSnapshot,
    SeoAgentRun,
    SeoAgentTask,
)

_log = get_logger("hypershop.seo.agents.service")


# ============================================================
#  Keywords
# ============================================================
async def create_keyword(
    session: AsyncSession,
    *,
    keyword: str,
    target_location: str,
    priority: str,
    keyword_type: str | None,
    target_url: str | None,
    user_id: UUID | None,
) -> SeoAgentKeyword:
    row = SeoAgentKeyword(
        keyword=keyword,
        target_location=target_location,
        priority=priority,
        keyword_type=keyword_type,
        target_url=target_url,
        status="new",
        created_by=user_id,
    )
    session.add(row)
    await session.flush()
    return row


async def list_keywords(
    session: AsyncSession, *, limit: int = 200,
) -> list[SeoAgentKeyword]:
    rows = (
        await session.execute(
            select(SeoAgentKeyword)
            .order_by(SeoAgentKeyword.created_at.desc())
            .limit(limit),
        )
    ).scalars().all()
    return list(rows)


async def get_keyword(
    session: AsyncSession, keyword_id: UUID,
) -> SeoAgentKeyword:
    row = await session.get(SeoAgentKeyword, keyword_id)
    if row is None:
        raise NotFoundError("SEO keyword not found.")
    return row


# ============================================================
#  Orchestrator — three-phase split (v24 fix)
#
#  The orchestrator was originally one async function that held a DB
#  transaction open across 3 OpenAI HTTP calls. Under load that pinned
#  pooled connections for 5-30 seconds per request. The fix splits it
#  into three explicit phases the API route stitches together:
#
#    1. prepare_analysis(session, keyword_id)
#         - reads the keyword row
#         - checks for a recent (<5 min) run → returns CachedAnalysis
#           if found, so a double-click doesn't fire OpenAI twice and
#           doesn't create duplicate tasks (Item 2 fix)
#         - returns AnalysisPrep dataclass otherwise
#       This phase runs INSIDE a short read-only txn.
#
#    2. run_agents(prep)
#         - pure-Python; 3 OpenAI HTTP calls; no DB session needed
#         - runs OUTSIDE any txn (Item 1 fix)
#
#    3. persist_analysis(session, prep, agent_output)
#         - writes seo_agent_runs + 3 seo_agent_tasks + status flip
#       This phase runs INSIDE a second short txn.
# ============================================================


@dataclass(frozen=True)
class AnalysisPrep:
    """Materialised input snapshot for the agents — passed between
    phases so phase 2 (no-txn) doesn't need to touch SQLAlchemy.
    """
    keyword_id: UUID
    keyword: str
    target_location: str
    priority: str


@dataclass(frozen=True)
class CachedAnalysis:
    """Returned by ``prepare_analysis`` when a recent run is found.

    The route detects this and short-circuits — no OpenAI call, no new
    DB writes. ``output_payload`` is whatever the prior run wrote.
    """
    keyword_id: UUID
    output_payload: dict[str, Any]
    task_ids: list[UUID]
    run_id: UUID


# Recent-run window — controls idempotency of `analyse`. Two
# operator clicks within this window dedupe to a single agent run.
ANALYSE_RECENT_WINDOW = timedelta(minutes=5)


async def prepare_analysis(
    session: AsyncSession, keyword_id: UUID,
) -> AnalysisPrep | CachedAnalysis:
    """Phase 1 — read the keyword + check for a recent run.

    Returns:
      - ``AnalysisPrep``   when no recent run exists (caller proceeds
                            to phase 2 + 3)
      - ``CachedAnalysis`` when a run within ANALYSE_RECENT_WINDOW
                            exists — caller returns this directly,
                            skipping OpenAI calls and DB writes
    """
    keyword = await get_keyword(session, keyword_id)

    recent_cutoff = utc_now() - ANALYSE_RECENT_WINDOW
    recent_run = (
        await session.execute(
            select(SeoAgentRun)
            .where(
                SeoAgentRun.keyword_id == keyword_id,
                SeoAgentRun.status == "success",
                SeoAgentRun.created_at >= recent_cutoff,
            )
            .order_by(SeoAgentRun.created_at.desc())
            .limit(1),
        )
    ).scalar_one_or_none()
    if recent_run is not None:
        # Find the tasks created alongside this run (any pending /
        # approved task for this keyword created at-or-after the run).
        task_rows = (
            await session.execute(
                select(SeoAgentTask.id)
                .where(
                    SeoAgentTask.keyword_id == keyword_id,
                    SeoAgentTask.created_at >= recent_run.created_at,
                ),
            )
        ).all()
        _log.info(
            "seo_orchestrator_dedup_hit",
            keyword_id=str(keyword_id),
            recent_run_id=str(recent_run.id),
            age_seconds=int(
                (utc_now() - recent_run.created_at).total_seconds(),
            ),
        )
        return CachedAnalysis(
            keyword_id=keyword_id,
            output_payload=recent_run.output_payload or {},
            task_ids=[t[0] for t in task_rows],
            run_id=recent_run.id,
        )

    return AnalysisPrep(
        keyword_id=keyword.id,
        keyword=keyword.keyword,
        target_location=keyword.target_location,
        priority=keyword.priority,
    )


def run_agents(prep: AnalysisPrep) -> dict[str, Any]:
    """Phase 2 — pure Python, runs the 5 agents.

    ⚠ This function makes up to 3 outbound HTTP calls to OpenAI. The
    caller MUST invoke it OUTSIDE any open DB transaction.

    Returns the combined output dict. Never raises on agent errors —
    the OpenAI client catches exceptions internally and returns the
    fallback dict tagged with ``openai_error``.
    """
    keyword_intelligence = KeywordIntelligenceAgent().run(
        prep.keyword, prep.target_location,
    )
    local_page = LocalLandingPageAgent().run(
        prep.keyword, prep.target_location,
    )
    schema = SchemaAgent().run(
        keyword_intelligence.get("target_page_type", "local_landing_page"),
        prep.keyword,
    )
    trust = ReviewTrustAgent().run(prep.keyword)
    improvement = ImprovementAgent().run(prep.keyword)
    return {
        "keyword_intelligence": keyword_intelligence,
        "local_page": local_page,
        "schema": schema,
        "trust": trust,
        "improvement": improvement,
    }


async def persist_analysis(
    session: AsyncSession,
    prep: AnalysisPrep,
    agent_output: dict[str, Any],
) -> dict[str, Any]:
    """Phase 3 — write run audit row + 3 approval-gated tasks +
    flip keyword status. Returns the same shape the original
    ``analyze_keyword`` did so the API contract is unchanged.
    """
    run_row = SeoAgentRun(
        agent_name="seo_orchestrator",
        keyword_id=prep.keyword_id,
        input_payload={
            "keyword": prep.keyword,
            "target_location": prep.target_location,
        },
        output_payload=agent_output,
        status="success",
        error=None,
    )
    session.add(run_row)

    task_rows: list[SeoAgentTask] = [
        SeoAgentTask(
            keyword_id=prep.keyword_id,
            task_type="local_landing_page",
            title=f"Create/optimize page for {prep.keyword}",
            description=(
                "Use generated local landing page brief; admin approval "
                "required before publishing."
            ),
            payload=agent_output["local_page"],
            priority=prep.priority,
            status="pending_approval",
        ),
        SeoAgentTask(
            keyword_id=prep.keyword_id,
            task_type="schema",
            title=f"Add schema for {prep.keyword}",
            description="Add and validate JSON-LD schema on target page.",
            payload=agent_output["schema"],
            priority="high",
            status="pending_approval",
        ),
        SeoAgentTask(
            keyword_id=prep.keyword_id,
            task_type="review_trust",
            title=f"Build trust signals for {prep.keyword}",
            description=(
                "Request verified reviews and add policy/trust elements."
            ),
            payload=agent_output["trust"],
            priority="medium",
            status="pending_approval",
        ),
    ]
    for t in task_rows:
        session.add(t)

    await session.execute(
        update(SeoAgentKeyword)
        .where(SeoAgentKeyword.id == prep.keyword_id)
        .values(status="analyzed"),
    )
    await session.flush()
    _log.info(
        "seo_orchestrator_run",
        keyword_id=str(prep.keyword_id),
        keyword=prep.keyword,
        tasks_created=len(task_rows),
    )
    return {
        "keyword_id": prep.keyword_id,
        **agent_output,
        "created_task_ids": [t.id for t in task_rows],
    }


# Backwards-compat shim: keep the single-call signature for any
# existing callers that took the previous behaviour. Internally
# routes to the three-phase split inside ONE transaction (old
# behaviour) so the call still works — but the recommended path is
# the route-level orchestration in api/agents.py which splits the
# txn correctly around the LLM calls.
async def analyze_keyword(
    session: AsyncSession, keyword_id: UUID,
) -> dict[str, Any]:
    """Legacy single-call orchestrator. Prefer the three-phase split
    (``prepare_analysis`` → ``run_agents`` → ``persist_analysis``)
    invoked from the route layer so the LLM calls don't sit inside an
    open DB transaction. Retained for compatibility.
    """
    prep_or_cache = await prepare_analysis(session, keyword_id)
    if isinstance(prep_or_cache, CachedAnalysis):
        return {
            "keyword_id": prep_or_cache.keyword_id,
            **prep_or_cache.output_payload,
            "created_task_ids": prep_or_cache.task_ids,
        }
    agent_output = run_agents(prep_or_cache)
    return await persist_analysis(session, prep_or_cache, agent_output)


# ============================================================
#  Tasks
# ============================================================
async def list_tasks(
    session: AsyncSession, *, status: str | None = None, limit: int = 300,
) -> list[SeoAgentTask]:
    stmt = select(SeoAgentTask)
    if status:
        stmt = stmt.where(SeoAgentTask.status == status)
    stmt = stmt.order_by(SeoAgentTask.created_at.desc()).limit(limit)
    rows = (await session.execute(stmt)).scalars().all()
    return list(rows)


async def get_task(
    session: AsyncSession, task_id: UUID,
) -> SeoAgentTask:
    row = await session.get(SeoAgentTask, task_id)
    if row is None:
        raise NotFoundError("SEO task not found.")
    return row


async def approve_task(
    session: AsyncSession,
    *,
    task_id: UUID,
    actor_id: UUID | None,
    comment: str | None,
) -> SeoAgentTask:
    task = await get_task(session, task_id)
    now = utc_now()
    await session.execute(
        update(SeoAgentTask)
        .where(SeoAgentTask.id == task_id)
        .values(status="approved", approved_by=actor_id, approved_at=now),
    )
    session.add(SeoAgentApprovalLog(
        task_id=task_id,
        action="approved",
        actor_id=actor_id,
        comment=comment,
    ))
    await session.flush()
    await session.refresh(task)
    return task


async def reject_task(
    session: AsyncSession,
    *,
    task_id: UUID,
    actor_id: UUID | None,
    comment: str | None,
) -> SeoAgentTask:
    task = await get_task(session, task_id)
    await session.execute(
        update(SeoAgentTask)
        .where(SeoAgentTask.id == task_id)
        .values(status="rejected", approved_by=None, approved_at=None),
    )
    session.add(SeoAgentApprovalLog(
        task_id=task_id,
        action="rejected",
        actor_id=actor_id,
        comment=comment,
    ))
    await session.flush()
    await session.refresh(task)
    return task


# ============================================================
#  Rank snapshots
# ============================================================
async def add_rank_snapshot(
    session: AsyncSession,
    *,
    keyword_id: UUID,
    target_url: str | None,
    position: int | None,
    location: str,
    device: str,
    impressions: int | None,
    clicks: int | None,
    ctr: float | None,
    source: str,
) -> SeoAgentRankSnapshot:
    # Validate keyword exists before insert (CASCADE FK enforces it at
    # DB level too; this gives a friendly 404 instead of an FK error).
    await get_keyword(session, keyword_id)
    row = SeoAgentRankSnapshot(
        keyword_id=keyword_id,
        target_url=target_url,
        position=position,
        location=location,
        device=device,
        impressions=impressions,
        clicks=clicks,
        ctr=ctr,
        source=source,
    )
    session.add(row)
    await session.flush()
    return row


async def list_rank_snapshots(
    session: AsyncSession, *, limit: int = 500,
) -> list[SeoAgentRankSnapshot]:
    rows = (
        await session.execute(
            select(SeoAgentRankSnapshot)
            .order_by(SeoAgentRankSnapshot.captured_at.desc())
            .limit(limit),
        )
    ).scalars().all()
    return list(rows)


# ============================================================
#  Technical audit
# ============================================================
async def technical_audit(
    session: AsyncSession,
    *,
    url: str,
    html: str | None,
) -> SeoAgentPageAudit:
    issues: list[dict[str, Any]] = []
    score = 100
    if html:
        h = html.lower()
        if "<title" not in h:
            issues.append({"type": "missing_title", "priority": "high"})
            score -= 20
        if 'name="description"' not in h and "name='description'" not in h:
            issues.append(
                {"type": "missing_meta_description", "priority": "high"},
            )
            score -= 15
        if 'rel="canonical"' not in h and "rel='canonical'" not in h:
            issues.append(
                {"type": "missing_canonical", "priority": "medium"},
            )
            score -= 10
        if "application/ld+json" not in h:
            issues.append(
                {"type": "missing_schema", "priority": "medium"},
            )
            score -= 10
    else:
        issues.append({
            "type": "html_not_provided",
            "priority": "info",
            "message": "Crawler integration required for live audit.",
        })
        score = 70

    recommendations = [
        {
            "action": f"fix_{issue['type']}",
            "priority": issue.get("priority", "medium"),
        }
        for issue in issues
    ]
    row = SeoAgentPageAudit(
        url=url,
        score=max(score, 0),
        issues=issues,
        recommendations=recommendations,
    )
    session.add(row)
    await session.flush()
    return row


# ============================================================
#  Dashboard
# ============================================================
async def dashboard(session: AsyncSession) -> dict[str, Any]:
    total = (
        await session.execute(select(sa_func.count(SeoAgentKeyword.id)))
    ).scalar_one()
    pending = (
        await session.execute(
            select(sa_func.count(SeoAgentTask.id))
            .where(SeoAgentTask.status == "pending_approval"),
        )
    ).scalar_one()
    approved = (
        await session.execute(
            select(sa_func.count(SeoAgentTask.id))
            .where(SeoAgentTask.status == "approved"),
        )
    ).scalar_one()
    average_position = (
        await session.execute(
            select(sa_func.avg(SeoAgentRankSnapshot.position)),
        )
    ).scalar()
    top3 = (
        await session.execute(
            select(sa_func.count(SeoAgentRankSnapshot.id))
            .where(SeoAgentRankSnapshot.position <= 3),
        )
    ).scalar_one()
    top10 = (
        await session.execute(
            select(sa_func.count(SeoAgentRankSnapshot.id))
            .where(SeoAgentRankSnapshot.position <= 10),
        )
    ).scalar_one()
    latest_audits = (
        await session.execute(
            select(SeoAgentPageAudit)
            .order_by(SeoAgentPageAudit.created_at.desc())
            .limit(20),
        )
    ).scalars().all()
    issues_total = sum(len(a.issues or []) for a in latest_audits)
    return {
        "total_keywords": int(total or 0),
        "top3_keywords": int(top3 or 0),
        "top10_keywords": int(top10 or 0),
        "pending_tasks": int(pending or 0),
        "approved_tasks": int(approved or 0),
        "average_position": (
            float(average_position) if average_position is not None else None
        ),
        "technical_issues": issues_total,
    }
