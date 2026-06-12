"""Text → retrieval-sized chunks.

Strategy:
1. Split on paragraph (\\n\\n) boundaries first to preserve semantic units.
2. If a paragraph exceeds `max_tokens`, split it on sentence boundaries
   (Bangla `।` + English `.!?`).
3. Pack consecutive paragraphs/sentences into chunks until adding another
   would exceed `max_tokens`.
4. Add an `overlap_tokens` tail of the previous chunk to the next chunk so
   queries that straddle a boundary still hit relevant context.

This is intentionally simple — no nltk/spacy dependency. Bangla `।` (U+0964)
is treated as a sentence terminator alongside ASCII punctuation.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from app.rag.embeddings import estimate_tokens


@dataclass
class Chunk:
    text: str
    position: int
    token_count: int
    text_hash: str


# Sentence boundary: any of `.!?` (English) or `।` (Bangla danda),
# optionally followed by quote/bracket then whitespace.
_SENT_RE = re.compile(r"(?<=[.!?।])[\"')\]]?\s+")


def _split_sentences(paragraph: str) -> list[str]:
    pieces = _SENT_RE.split(paragraph.strip())
    return [p.strip() for p in pieces if p.strip()]


def _split_paragraphs(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


def chunk_text(
    text: str,
    *,
    max_tokens: int = 400,
    overlap_tokens: int = 60,
) -> list[Chunk]:
    """Split text into chunks. Returns empty list for empty input."""
    text = (text or "").strip()
    if not text:
        return []
    if max_tokens <= 0:
        raise ValueError("max_tokens must be positive")
    if overlap_tokens < 0 or overlap_tokens >= max_tokens:
        raise ValueError("overlap_tokens must be 0 <= overlap < max_tokens")

    # Step 1: paragraphs → sentences (only when paragraph > max_tokens)
    units: list[str] = []
    for para in _split_paragraphs(text):
        if estimate_tokens(para) <= max_tokens:
            units.append(para)
        else:
            sents = _split_sentences(para)
            if not sents:
                # Fallback: hard-wrap by char count
                step = max(1, max_tokens * 3)
                for i in range(0, len(para), step):
                    units.append(para[i : i + step])
            else:
                units.extend(sents)

    # Step 2: pack with overlap
    chunks: list[Chunk] = []
    cur_parts: list[str] = []
    cur_tokens = 0
    overlap_buffer = ""

    def flush():
        nonlocal cur_parts, cur_tokens, overlap_buffer
        if not cur_parts:
            return
        body = " ".join(cur_parts).strip()
        chunks.append(
            Chunk(
                text=body,
                position=len(chunks),
                token_count=estimate_tokens(body),
                text_hash=hashlib.sha256(body.encode("utf-8")).hexdigest(),
            )
        )
        # Build overlap from the END of this chunk
        if overlap_tokens > 0:
            tail = body
            while estimate_tokens(tail) > overlap_tokens and " " in tail:
                tail = tail.split(" ", 1)[1]
            overlap_buffer = tail
        else:
            overlap_buffer = ""
        cur_parts = []
        cur_tokens = 0

    for unit in units:
        unit_tokens = estimate_tokens(unit)
        # If a single unit is bigger than max_tokens, hard-wrap it
        if unit_tokens > max_tokens:
            flush()
            step = max(1, max_tokens * 3)
            for i in range(0, len(unit), step):
                piece = unit[i : i + step]
                cur_parts = [piece]
                cur_tokens = estimate_tokens(piece)
                flush()
            continue

        projected = cur_tokens + unit_tokens
        if projected > max_tokens and cur_parts:
            flush()
            if overlap_buffer:
                cur_parts.append(overlap_buffer)
                cur_tokens = estimate_tokens(overlap_buffer)

        cur_parts.append(unit)
        cur_tokens += unit_tokens

    flush()
    return chunks
