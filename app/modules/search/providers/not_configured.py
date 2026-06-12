"""Default reranker — graceful no-op.

Returns ``{}`` so the service keeps the local-ranked order unchanged.
This is the right default for a feature where ML is enhancement, not
requirement (unlike payments where NotConfigured = 502).
"""

from __future__ import annotations

from app.modules.search.providers.base import RerankerProvider, RerankRequest


class NotConfiguredReranker(RerankerProvider):
    name = "not_configured"

    async def rerank(self, req: RerankRequest) -> dict[str, float]:
        return {}
