"""OpenAI text-embedding wrapper.

- Batched: up to `RAG_EMBEDDING_BATCH_SIZE` inputs per API call.
- Dry-run: returns deterministic stub vectors when `OPENAI_API_KEY` is missing
  (so dev/test work without making real API calls). Production refuses dry-run
  if `RAG_REQUIRED=true`.
- Token estimation: rough char/4 heuristic (precise tokenisation would need
  tiktoken; this is good enough for chunking decisions).
"""
from __future__ import annotations

import hashlib
import logging
import struct
from dataclasses import dataclass

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


@dataclass
class EmbeddingResult:
    vector: list[float]
    model: str
    dim: int


def _stub_vector(text: str, dim: int) -> list[float]:
    """Deterministic pseudo-embedding for dry-run mode.

    Same text → same vector across calls (so retrieval still 'works' against
    documents embedded the same way). NOT semantic — only useful for tests
    and offline dev demos."""
    # Use SHA-256 to seed a fixed-length pseudo-random walk.
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    out: list[float] = []
    for i in range(dim):
        # 4 bytes per float, looping the 32-byte digest as needed.
        offset = (i * 4) % 28
        word = digest[offset : offset + 4]
        if len(word) < 4:
            word = (word + b"\x00\x00\x00\x00")[:4]
        (val,) = struct.unpack("<I", word)
        out.append((val / 0xFFFFFFFF) * 2.0 - 1.0)
    # L2-normalise so cosine == dot product
    norm = sum(x * x for x in out) ** 0.5 or 1.0
    return [x / norm for x in out]


async def embed_batch(texts: list[str]) -> list[EmbeddingResult]:
    """Embed a list of texts. Returns same length, same order."""
    if not texts:
        return []
    cfg = settings()
    model = cfg.rag_embedding_model or "text-embedding-3-small"
    dim = int(cfg.rag_embedding_dim or 1536)

    if not cfg.openai_api_key:
        if cfg.is_production and cfg.rag_required:
            raise RuntimeError(
                "RAG_REQUIRED=true in production but OPENAI_API_KEY missing."
            )
        logger.warning("rag_embeddings_dry_run reason=no_openai_key count=%d", len(texts))
        return [EmbeddingResult(vector=_stub_vector(t, dim), model="stub-deterministic", dim=dim) for t in texts]

    batch_size = max(1, int(cfg.rag_embedding_batch_size or 64))
    results: list[EmbeddingResult] = []

    async with httpx.AsyncClient(timeout=cfg.rag_embedding_timeout_seconds or 30.0) as client:
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            resp = await client.post(
                "https://api.openai.com/v1/embeddings",
                headers={
                    "Authorization": f"Bearer {cfg.openai_api_key}",
                    "Content-Type": "application/json",
                },
                json={"model": model, "input": batch},
            )
            resp.raise_for_status()
            body = resp.json()
            for entry in body.get("data", []):
                vec = entry.get("embedding") or []
                results.append(EmbeddingResult(vector=vec, model=model, dim=len(vec)))
    return results


async def embed_one(text: str) -> EmbeddingResult:
    out = await embed_batch([text])
    return out[0]


def estimate_tokens(text: str) -> int:
    """Rough heuristic: ~4 chars per token for English, ~2.5 for Bangla.
    Use the lower of the two so we don't undercount Bangla."""
    if not text:
        return 0
    # Detect Bangla characters
    has_bangla = any("ঀ" <= ch <= "৿" for ch in text)
    divisor = 2.5 if has_bangla else 4.0
    return max(1, int(len(text) / divisor))
