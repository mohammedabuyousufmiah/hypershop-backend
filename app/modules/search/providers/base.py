"""Reranker port + DTOs.

The reranker takes the local-ranked top N candidates + the user query
and returns a per-document score map. The service layer then blends
those scores with the local scores (see :mod:`search.ranking`).

Adapters return ``{}`` instead of raising when reranking is unavailable
— the service treats an empty map as "no ML signal, keep local order"
and continues. This keeps the search endpoint up even when the ML
provider is degraded.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class RerankCandidate:
    document_id: str  # SearchDocument.id, str-ified
    document_type: str
    title: str
    subtitle: str
    body_excerpt: str  # truncated to ~512 chars to keep the request small
    local_score: float


@dataclass(frozen=True)
class RerankRequest:
    query: str
    candidates: tuple[RerankCandidate, ...]
    limit: int


class RerankerProvider(ABC):
    """Capability port. ``rerank`` returns ``{document_id: score}``.

    Empty dict = "no signal, keep local order". This is the contract
    the service layer relies on — adapters MUST NOT raise on
    transient failures (timeout, 5xx, malformed body); they should
    log + return ``{}``.
    """

    name: str = "abstract"

    @abstractmethod
    async def rerank(self, req: RerankRequest) -> dict[str, float]: ...
