"""0050 cc_sprint8 — pgvector optional upgrade + final cleanup.

This migration tries to install pgvector and add a native ``vector``
column to ``cc_knowledge_chunks``. Both steps are wrapped in
``DO $$ … EXCEPTION WHEN … $$`` so they no-op gracefully if the
pgvector binary isn't installed at the cluster level (common on
managed Postgres without the extension package).

When pgvector IS available, the column lights up + an IVFFlat
index is created. CC's search code in ``api/kb_csat.py`` already
falls back from cosine to LIKE-search; an ops follow-up will swap
the in-memory cosine to a ``<=>`` operator once this column has
backfilled embeddings.

Schema added on pgvector-equipped clusters:
  cc_knowledge_chunks.embedding_vec vector(1536)
  ix_cc_knowledge_chunks_emb_ivfflat  (vector_cosine_ops, lists=100)
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0050_cc_sprint8"
down_revision: str | Sequence[str] | None = "0049_cc_sprint6_ai"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Step 1: try to install pgvector. Wrap in DO/EXCEPTION so a
    # missing binary returns NOTICE instead of erroring the migration.
    op.execute("""
    DO $$
    BEGIN
        CREATE EXTENSION IF NOT EXISTS vector;
        RAISE NOTICE 'pgvector available — column will be added';
    EXCEPTION WHEN undefined_file OR insufficient_privilege OR feature_not_supported THEN
        RAISE NOTICE 'pgvector binary not installed — skipping vector column';
    END $$;
    """)

    # Step 2: only add the column if the extension landed
    op.execute("""
    DO $$
    BEGIN
        IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector') THEN
            EXECUTE 'ALTER TABLE cc_knowledge_chunks
                     ADD COLUMN IF NOT EXISTS embedding_vec vector(1536)';
            EXECUTE 'CREATE INDEX IF NOT EXISTS ix_cc_knowledge_chunks_emb_ivfflat
                     ON cc_knowledge_chunks USING ivfflat
                     (embedding_vec vector_cosine_ops) WITH (lists = 100)';
            RAISE NOTICE 'cc_knowledge_chunks.embedding_vec + ivfflat index created';
        END IF;
    END $$;
    """)


def downgrade() -> None:
    op.execute("""
    DO $$
    BEGIN
        IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector') THEN
            EXECUTE 'DROP INDEX IF EXISTS ix_cc_knowledge_chunks_emb_ivfflat';
            EXECUTE 'ALTER TABLE cc_knowledge_chunks DROP COLUMN IF EXISTS embedding_vec';
        END IF;
    END $$;
    """)
