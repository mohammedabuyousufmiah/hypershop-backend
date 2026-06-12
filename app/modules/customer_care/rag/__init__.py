"""RAG (Retrieval-Augmented Generation) subsystem.

- `embeddings` — OpenAI text-embedding-3-small wrapper (batched + dry-run)
- `chunker` — text → token-bounded chunks with overlap
- `store` — vector store abstraction (pgvector for Postgres, in-memory cosine for SQLite)
- `retrieval` — query → embed → top-k → format context for LLM
- `ingest` — document upload → chunk → embed → persist
"""
from app.rag.embeddings import EmbeddingResult, embed_batch, embed_one  # noqa: F401
from app.rag.retrieval import RetrievedChunk, retrieve  # noqa: F401
from app.rag.ingest import ingest_text  # noqa: F401
