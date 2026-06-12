"""Pydantic v2 wire schemas for the SEO agents sub-module."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import Field

from app.core.validation import StrictModel


# ============================================================
#  Keywords
# ============================================================
class SeoAgentKeywordCreate(StrictModel):
    keyword: str = Field(min_length=2, max_length=255)
    target_location: str = Field(default="Bangladesh", max_length=120)
    priority: Literal["low", "medium", "high", "urgent"] = "medium"
    keyword_type: str | None = Field(default=None, max_length=50)
    target_url: str | None = Field(default=None, max_length=500)


class SeoAgentKeywordResponse(StrictModel):
    id: UUID
    keyword: str
    target_location: str
    priority: str
    keyword_type: str | None
    target_url: str | None
    status: str
    created_by: UUID | None
    created_at: datetime
    updated_at: datetime


class SeoAgentKeywordListResponse(StrictModel):
    items: list[SeoAgentKeywordResponse]
    total: int


# ============================================================
#  Tasks
# ============================================================
class SeoAgentTaskResponse(StrictModel):
    id: UUID
    keyword_id: UUID | None
    task_type: str
    title: str
    description: str
    payload: dict[str, Any]
    priority: str
    status: str
    approved_by: UUID | None
    approved_at: datetime | None
    created_at: datetime


class SeoAgentTaskListResponse(StrictModel):
    items: list[SeoAgentTaskResponse]
    total: int


class SeoAgentApprovalRequest(StrictModel):
    comment: str | None = Field(default=None, max_length=2000)


# ============================================================
#  Rank snapshots
# ============================================================
class SeoAgentRankSnapshotCreate(StrictModel):
    keyword_id: UUID
    target_url: str | None = Field(default=None, max_length=500)
    position: int | None = Field(default=None, ge=1)
    location: str = Field(default="Bangladesh", max_length=120)
    device: Literal["mobile", "desktop", "tablet"] = "mobile"
    impressions: int | None = Field(default=None, ge=0)
    clicks: int | None = Field(default=None, ge=0)
    ctr: float | None = Field(default=None, ge=0)
    source: str = Field(default="manual_or_api", max_length=80)


class SeoAgentRankSnapshotResponse(StrictModel):
    id: UUID
    keyword_id: UUID
    target_url: str | None
    position: int | None
    location: str
    device: str
    impressions: int | None
    clicks: int | None
    ctr: float | None
    source: str
    captured_at: datetime


class SeoAgentRankListResponse(StrictModel):
    items: list[SeoAgentRankSnapshotResponse]
    total: int


# ============================================================
#  Technical audit
# ============================================================
class SeoAgentPageAuditRequest(StrictModel):
    url: str = Field(min_length=1, max_length=500)
    html: str | None = Field(default=None, max_length=2_000_000)


class SeoAgentPageAuditResponse(StrictModel):
    id: UUID
    url: str
    score: int
    issues: list[dict[str, Any]]
    recommendations: list[dict[str, Any]]
    created_at: datetime


# ============================================================
#  Analyse (orchestrator)
# ============================================================
class SeoAgentAnalyseResponse(StrictModel):
    """The orchestrator runs 5 agents and returns all their outputs as
    one envelope, plus the IDs of the auto-created tasks."""

    keyword_id: UUID
    keyword_intelligence: dict[str, Any]
    local_page: dict[str, Any]
    schema: dict[str, Any]
    trust: dict[str, Any]
    improvement: dict[str, Any]
    created_task_ids: list[UUID]


# ============================================================
#  Dashboard
# ============================================================
class SeoAgentDashboardResponse(StrictModel):
    total_keywords: int
    top3_keywords: int
    top10_keywords: int
    pending_tasks: int
    approved_tasks: int
    average_position: float | None
    technical_issues: int
