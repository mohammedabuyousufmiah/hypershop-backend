"""Search repositories — thin SQLA wrappers + raw FTS query."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import and_, delete, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import utc_now
from app.modules.search.models import SearchDocument, SearchQueryLog
from app.modules.search.normalizer import normalize_search_text


class SearchDocumentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def upsert(
        self, *,
        document_type: str,
        entity_id: UUID,
        title: str,
        subtitle: str = "",
        body: str = "",
        metadata: dict[str, Any] | None = None,
        is_active: bool = True,
        boost: float = 1.0,
        source_updated_at: datetime | None = None,
    ) -> bool:
        """Upsert one document. Returns True if a new row was inserted."""
        document_key = f"{document_type}:{entity_id}"
        normalized = normalize_search_text(" ".join([title, subtitle, body]))[:8192]
        values = {
            "document_type": document_type,
            "entity_id": entity_id,
            "document_key": document_key,
            "title": (title or "")[:255],
            "subtitle": (subtitle or "")[:255],
            "body": (body or "")[:4096],
            "normalized_text": normalized,
            "metadata_json": metadata or {},
            "is_active": is_active,
            "boost": float(boost),
            "source_updated_at": source_updated_at,
        }
        stmt = (
            pg_insert(SearchDocument.__table__)
            .values(**values)
            .on_conflict_do_update(
                index_elements=["document_key"],
                set_={k: v for k, v in values.items() if k != "document_key"},
            )
            .returning(SearchDocument.__table__.c.id, SearchDocument.__table__.c.created_at)
        )
        row = (await self.session.execute(stmt)).first()
        # Heuristic: if created_at was just now, this was an insert,
        # otherwise an update. Not 100% accurate (created_at can equal
        # now() on an update too within microseconds) but good enough
        # for the indexer's "I added N new" log line.
        if row is None:
            return False
        created_at = row[1]
        return created_at is not None and (utc_now() - created_at).total_seconds() < 1.0

    async def deactivate(self, *, document_type: str, entity_id: UUID) -> int:
        result = await self.session.execute(
            update(SearchDocument)
            .where(SearchDocument.document_type == document_type)
            .where(SearchDocument.entity_id == entity_id)
            .values(is_active=False),
        )
        return int(result.rowcount or 0)

    async def delete_all(self) -> int:
        result = await self.session.execute(delete(SearchDocument))
        return int(result.rowcount or 0)

    async def count_by_type(self) -> dict[str, int]:
        from sqlalchemy import func
        rows = (
            await self.session.execute(
                select(SearchDocument.document_type, func.count())
                .group_by(SearchDocument.document_type),
            )
        ).all()
        return {str(t): int(c) for t, c in rows}

    async def search_fts(
        self, *,
        tsquery: str,
        document_types: Sequence[str] | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Execute the Postgres FTS query.

        Returns a list of dicts with: id, document_type, entity_id,
        title, subtitle, body, metadata, score, boost. Ordered by
        local score DESC. Caller may rerank.

        Uses ts_rank_cd with weights [0, 0.4, 0.7, 1.0] (matching
        weight letters D < C < B < A; A is title in our trigger).
        """
        # We use a raw text query because ts_rank_cd composition with
        # to_tsquery is awkward in SQLA core.
        types_filter_sql = ""
        params: dict[str, Any] = {
            "tsq": tsquery,
            "lim": int(limit),
        }
        if document_types:
            placeholders = ", ".join(f":dt{i}" for i in range(len(document_types)))
            types_filter_sql = f" AND document_type IN ({placeholders})"
            for i, dt in enumerate(document_types):
                params[f"dt{i}"] = dt

        stmt = text(f"""
            SELECT
                id::text                    AS id,
                document_type,
                entity_id::text             AS entity_id,
                title, subtitle, body,
                metadata_json               AS metadata,
                boost,
                ts_rank_cd(
                    '{{0, 0.4, 0.7, 1.0}}'::float4[],
                    search_vector,
                    to_tsquery('simple', :tsq),
                    32
                ) AS ts_rank
            FROM search_documents
            WHERE is_active = true
              AND search_vector @@ to_tsquery('simple', :tsq)
              {types_filter_sql}
            ORDER BY ts_rank DESC
            LIMIT :lim
        """)
        rows = (await self.session.execute(stmt, params)).mappings().all()
        return [dict(r) for r in rows]

    async def search_like_fallback(
        self, *,
        normalized_query: str,
        document_types: Sequence[str] | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Fallback for very short queries that ts_query rejects.

        Uses the trigram GIN index on normalized_text — slower than
        the tsvector path but still indexed. Falls back to a constant
        score of 0.1 (worse than any FTS hit) so reranker still
        considers them but local order shows them last.
        """
        if not normalized_query:
            return []
        types_filter_sql = ""
        params: dict[str, Any] = {
            "q": f"%{normalized_query}%",
            "lim": int(limit),
        }
        if document_types:
            placeholders = ", ".join(f":dt{i}" for i in range(len(document_types)))
            types_filter_sql = f" AND document_type IN ({placeholders})"
            for i, dt in enumerate(document_types):
                params[f"dt{i}"] = dt
        stmt = text(f"""
            SELECT
                id::text          AS id,
                document_type,
                entity_id::text   AS entity_id,
                title, subtitle, body,
                metadata_json     AS metadata,
                boost,
                0.1::float        AS ts_rank
            FROM search_documents
            WHERE is_active = true
              AND normalized_text LIKE :q
              {types_filter_sql}
            ORDER BY length(normalized_text) ASC, title ASC
            LIMIT :lim
        """)
        rows = (await self.session.execute(stmt, params)).mappings().all()
        return [dict(r) for r in rows]


class SearchQueryLogRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def log(
        self, *,
        user_id: UUID | None,
        raw_query: str,
        normalized_query: str,
        result_count: int,
        latency_ms: int,
        request_id: str,
        filters: dict[str, Any],
        used_ml_rerank: bool,
    ) -> SearchQueryLog:
        row = SearchQueryLog(
            user_id=user_id,
            raw_query=raw_query[:255],
            normalized_query=normalized_query[:255],
            result_count=result_count,
            latency_ms=latency_ms,
            request_id=(request_id or "")[:64],
            filters_json=filters or {},
            used_ml_rerank=used_ml_rerank,
        )
        self.session.add(row)
        await self.session.flush()
        return row
