"""ORM models for the search module.

Two tables:

  search_documents — the denormalised index. One row per (type, entity_id).
    Stores: human display fields (title, subtitle, body), the
    normalized lower-cased text used for substring matching, AND a
    Postgres ``tsvector`` column with a GIN index for fast full-text
    search. The tsvector is auto-maintained by a trigger (see migration
    0023) so re-indexing only requires writing the source columns.

  search_query_logs — analytics row per executed query. Records the
    raw + normalized query, latency, result count, who searched,
    optional filters. Used by ops to find:
      - Queries that returned 0 results (catalog gaps)
      - Slow queries (> 200ms)
      - Most-frequent queries (cache candidates)
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db.base import Base
from app.core.db.mixins import TimestampMixin


class SearchDocument(Base, TimestampMixin):
    __tablename__ = "search_documents"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    # 'product' | 'brand' | 'category' — see search.state.SearchDocumentType
    document_type: Mapped[str] = mapped_column(String(24), nullable=False)
    # The source entity's UUID — references the row in catalog tables.
    entity_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False,
    )
    # Stable key for UPSERT — '<type>:<entity_id>'
    document_key: Mapped[str] = mapped_column(
        String(80), nullable=False, unique=True,
    )

    # Display fields shown in the search-result list — kept verbatim so
    # we don't have to rejoin the source table on each query.
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    subtitle: Mapped[str] = mapped_column(
        String(255), nullable=False, server_default="",
    )
    body: Mapped[str] = mapped_column(
        String(4096), nullable=False, server_default="",
    )

    # Lower-cased + tokenised + de-accented version of (title + subtitle
    # + body). Used for cheap LIKE-substring matching when ts_query
    # returns nothing (e.g. very short queries that ts_query rejects).
    normalized_text: Mapped[str] = mapped_column(
        String(8192), nullable=False, server_default="",
    )
    # Postgres tsvector — auto-maintained by trigger from
    # (title, subtitle, body). GIN-indexed for sub-millisecond lookups
    # over millions of rows.
    search_vector: Mapped[str | None] = mapped_column(
        TSVECTOR, nullable=True,
    )

    # Free-form metadata: deep links, prices, image URLs, anything the
    # frontend wants to render with the result. Frontend treats it as
    # an opaque object.
    metadata_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb"),
    )

    # Quick filters
    is_active: Mapped[bool] = mapped_column(
        nullable=False, server_default=text("true"),
    )
    # Boost factor (1.0 = neutral; 1.5 = boost; 0.5 = bury). Lets ops
    # promote/demote individual entities without changing the indexer.
    boost: Mapped[float] = mapped_column(
        nullable=False, server_default=text("1.0"),
    )

    # When the source entity was last updated. Indexer compares this
    # against ``Product.updated_at`` to skip already-fresh rows in
    # the incremental cron.
    source_updated_at: Mapped[datetime | None] = mapped_column(nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "document_type", "entity_id",
            name="uq_search_documents_type_entity",
        ),
        CheckConstraint(
            "document_type IN ('product','brand','category')",
            name="ck_search_documents_type_enum",
        ),
        CheckConstraint("boost >= 0", name="ck_search_documents_boost_nonneg"),
        Index(
            "ix_search_documents_type_active",
            "document_type", "is_active",
        ),
        Index(
            "ix_search_documents_source_updated",
            "source_updated_at",
        ),
        # GIN index on the tsvector — created in migration 0023 with
        # USING GIN syntax.
    )


class SearchQueryLog(Base, TimestampMixin):
    __tablename__ = "search_query_logs"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    raw_query: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_query: Mapped[str] = mapped_column(String(255), nullable=False)
    result_count: Mapped[int] = mapped_column(
        nullable=False, server_default=text("0"),
    )
    latency_ms: Mapped[int] = mapped_column(
        nullable=False, server_default=text("0"),
    )
    request_id: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default="",
    )
    # Filters supplied with the query (types, limit, etc.).
    filters_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb"),
    )
    # Was the result set re-ranked by the ML provider?
    used_ml_rerank: Mapped[bool] = mapped_column(
        nullable=False, server_default=text("false"),
    )

    __table_args__ = (
        Index(
            "ix_search_query_logs_normalized_created",
            "normalized_query", "created_at",
        ),
        Index(
            "ix_search_query_logs_zero_result",
            "result_count", "created_at",
            postgresql_where=text("result_count = 0"),
        ),
    )
