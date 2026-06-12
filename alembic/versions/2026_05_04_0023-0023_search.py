"""Search index + query log + tsvector trigger.

Revision ID: 0023_search
Revises: 0022_seed_default_delivery_zones
Create Date: 2026-05-04

Module 28 — denormalised search index for catalog products + brands +
categories. Uses Postgres ``tsvector`` + GIN index for sub-millisecond
full-text search; auto-maintained by a trigger so the indexer just
writes (title, subtitle, body) and the vector follows.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0023_search"
down_revision: str | Sequence[str] | None = "0022_seed_default_delivery_zones"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # pg_trgm provides ``gin_trgm_ops`` used by the GIN index on
    # normalized_text below. The extension is usually pre-installed
    # on the Postgres 16-alpine image; for vanilla clusters we
    # create it idempotently here BEFORE the index that needs it.
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # ---------------- search_documents ----------------
    op.create_table(
        "search_documents",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("document_type", sa.String(24), nullable=False),
        sa.Column(
            "entity_id", postgresql.UUID(as_uuid=True), nullable=False,
        ),
        sa.Column("document_key", sa.String(80), nullable=False, unique=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column(
            "subtitle", sa.String(255), nullable=False, server_default="",
        ),
        sa.Column(
            "body", sa.String(4096), nullable=False, server_default="",
        ),
        sa.Column(
            "normalized_text", sa.String(8192), nullable=False, server_default="",
        ),
        sa.Column("search_vector", postgresql.TSVECTOR(), nullable=True),
        sa.Column(
            "metadata_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "boost",
            sa.Float(),
            nullable=False,
            server_default=sa.text("1.0"),
        ),
        sa.Column(
            "source_updated_at", sa.DateTime(timezone=True), nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.UniqueConstraint(
            "document_type", "entity_id",
            name="uq_search_documents_type_entity",
        ),
        sa.CheckConstraint(
            "document_type IN ('product','brand','category')",
            name="ck_search_documents_type_enum",
        ),
        sa.CheckConstraint("boost >= 0", name="ck_search_documents_boost_nonneg"),
    )
    op.create_index(
        "ix_search_documents_type_active",
        "search_documents",
        ["document_type", "is_active"],
    )
    op.create_index(
        "ix_search_documents_source_updated",
        "search_documents",
        ["source_updated_at"],
    )
    # GIN index on the tsvector — the magic that makes search fast.
    op.execute(
        "CREATE INDEX ix_search_documents_search_vector "
        "ON search_documents USING GIN (search_vector)"
    )
    # Btree index on the lower-cased normalized_text for cheap LIKE
    # fallback when the tsquery returns nothing (very short queries).
    op.execute(
        "CREATE INDEX ix_search_documents_normalized_text_trgm "
        "ON search_documents USING GIN (normalized_text gin_trgm_ops)"
    )

    # Trigger: keep search_vector in sync with title/subtitle/body.
    # Uses 'simple' config (language-agnostic; doesn't strip stopwords)
    # to handle Bengali + English mixed content gracefully. Title gets
    # weight A, subtitle B, body C — ts_rank() respects these.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION search_documents_tsv_update() RETURNS trigger AS $$
        BEGIN
            NEW.search_vector :=
                setweight(to_tsvector('simple', coalesce(NEW.title,    '')), 'A') ||
                setweight(to_tsvector('simple', coalesce(NEW.subtitle, '')), 'B') ||
                setweight(to_tsvector('simple', coalesce(NEW.body,     '')), 'C');
            RETURN NEW;
        END
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER search_documents_tsv_update_trigger
        BEFORE INSERT OR UPDATE OF title, subtitle, body ON search_documents
        FOR EACH ROW EXECUTE FUNCTION search_documents_tsv_update();
        """
    )

    # ---------------- search_query_logs ----------------
    op.create_table(
        "search_query_logs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("raw_query", sa.String(255), nullable=False),
        sa.Column("normalized_query", sa.String(255), nullable=False),
        sa.Column(
            "result_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "latency_ms",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "request_id", sa.String(64), nullable=False, server_default="",
        ),
        sa.Column(
            "filters_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "used_ml_rerank",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
    )
    op.create_index(
        "ix_search_query_logs_normalized_created",
        "search_query_logs",
        ["normalized_query", "created_at"],
    )
    # Partial index — zero-result queries are the catalog-gap report.
    op.execute(
        "CREATE INDEX ix_search_query_logs_zero_result "
        "ON search_query_logs (result_count, created_at) "
        "WHERE result_count = 0"
    )

    # (CREATE EXTENSION pg_trgm moved to top of upgrade() — was a
    # bug: the GIN-trgm index above tried to use ``gin_trgm_ops``
    # before this statement ran on first-time apply.)


def downgrade() -> None:
    op.drop_index(
        "ix_search_query_logs_zero_result",
        table_name="search_query_logs",
    )
    op.drop_index(
        "ix_search_query_logs_normalized_created",
        table_name="search_query_logs",
    )
    op.drop_table("search_query_logs")

    op.execute("DROP TRIGGER IF EXISTS search_documents_tsv_update_trigger ON search_documents")
    op.execute("DROP FUNCTION IF EXISTS search_documents_tsv_update()")
    op.drop_index(
        "ix_search_documents_normalized_text_trgm",
        table_name="search_documents",
    )
    op.drop_index(
        "ix_search_documents_search_vector",
        table_name="search_documents",
    )
    op.drop_index(
        "ix_search_documents_source_updated",
        table_name="search_documents",
    )
    op.drop_index(
        "ix_search_documents_type_active",
        table_name="search_documents",
    )
    op.drop_table("search_documents")
