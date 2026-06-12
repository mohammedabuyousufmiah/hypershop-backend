"""Wire schemas (Pydantic v2) for the search module."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field, field_validator

from app.core.validation import StrictModel


class SearchHit(StrictModel):
    id: UUID
    document_type: str
    entity_id: UUID
    title: str
    subtitle: str = ""
    body: str = ""
    score: float
    local_score: float | None = None
    ml_score: float | None = None
    metadata: dict = Field(default_factory=dict)


class SearchResponse(StrictModel):
    query: str
    normalized_query: str
    types: list[str]
    limit: int
    total_hits: int
    used_ml_rerank: bool
    latency_ms: int
    hits: list[SearchHit]


class ReindexResponse(StrictModel):
    documents_indexed: int
    by_type: dict[str, int]
    seconds: float
