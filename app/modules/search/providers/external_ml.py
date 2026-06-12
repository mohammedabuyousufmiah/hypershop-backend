"""External ML reranker adapter — generic configurable HTTP client.

Made provider-agnostic on purpose: this is the same shape as the
Django zip's ``ml_api.py``, but FastAPI-async + with the
"never-raise" contract documented in :mod:`base`. Customers BYO their
reranking endpoint (Cohere Rerank, Voyage Rerank, internal model,
SageMaker endpoint, anything that takes
``{"query": ..., "candidates": [...]}`` and returns
``{"results": [{"id": "...", "score": 0.91}]}`` OR
``{"scores": {"id1": 0.91, "id2": 0.42}}``).

Refusal contract: ``__init__`` raises ``IntegrationError`` if the
URL is missing — that's caught by the factory which falls back to
NotConfigured. Per-call failures (network, 5xx, parse) are swallowed
+ logged + return ``{}`` so the search endpoint stays up.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from app.core.errors import IntegrationError
from app.core.logging import get_logger
from app.modules.search.providers.base import (
    RerankerProvider,
    RerankRequest,
)

_logger = get_logger("hypershop.search.reranker.external_ml")


class ExternalMlReranker(RerankerProvider):
    name = "external_ml"

    DEFAULT_TIMEOUT_S = 8.0

    def __init__(
        self, *,
        api_url: str,
        api_token: str | None = None,
        auth_header: str = "Authorization",
        auth_scheme: str = "Bearer",
        method: str = "POST",
        static_headers_json: str | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        if not api_url or not api_url.strip():
            raise IntegrationError(
                "ExternalMlReranker requires SEARCH_RERANK_API_URL.",
                details={"missing_setting": "SEARCH_RERANK_API_URL"},
            )
        self._url = api_url.strip()
        self._token = (api_token or "").strip()
        self._auth_header = (auth_header or "").strip()
        self._auth_scheme = (auth_scheme or "").strip()
        self._method = (method or "POST").upper().strip()
        self._timeout_s = max(2.0, float(timeout_s))

        # Optional static headers (e.g. {"X-Tenant": "hypershop"})
        self._static_headers: dict[str, str] = {}
        if static_headers_json:
            try:
                parsed = json.loads(static_headers_json)
                if not isinstance(parsed, dict):
                    raise IntegrationError(
                        "SEARCH_RERANK_API_STATIC_HEADERS_JSON must be a JSON object.",
                        details={"missing_setting": "SEARCH_RERANK_API_STATIC_HEADERS_JSON"},
                    )
                self._static_headers = {str(k): str(v) for k, v in parsed.items()}
            except json.JSONDecodeError as e:
                raise IntegrationError(
                    "SEARCH_RERANK_API_STATIC_HEADERS_JSON is not valid JSON.",
                    details={"error": str(e)},
                ) from e

    def _build_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        headers.update(self._static_headers)
        if self._token and self._auth_header:
            if self._auth_scheme.lower() in ("", "none"):
                headers[self._auth_header] = self._token
            else:
                headers[self._auth_header] = f"{self._auth_scheme} {self._token}"
        return headers

    @staticmethod
    def _parse_score_map(body: Any) -> dict[str, float]:
        """Tolerate both response shapes: {results: [...]} and {scores: {...}}."""
        if not isinstance(body, dict):
            return {}
        # Shape 1: {"results": [{"id": "...", "score": 0.91}, ...]}
        results = body.get("results")
        if isinstance(results, list):
            out: dict[str, float] = {}
            for item in results:
                if not isinstance(item, dict):
                    continue
                doc_id = item.get("id")
                score = item.get("score")
                if doc_id is None or score is None:
                    continue
                try:
                    out[str(doc_id)] = float(score)
                except (TypeError, ValueError):
                    continue
            return out
        # Shape 2: {"scores": {"id1": 0.91, ...}}
        scores = body.get("scores")
        if isinstance(scores, dict):
            out = {}
            for k, v in scores.items():
                try:
                    out[str(k)] = float(v)
                except (TypeError, ValueError):
                    continue
            return out
        return {}

    async def rerank(self, req: RerankRequest) -> dict[str, float]:
        if not req.candidates:
            return {}
        body = {
            "query": req.query,
            "limit": req.limit,
            "candidates": [
                {
                    "id": c.document_id,
                    "document_type": c.document_type,
                    "title": c.title,
                    "subtitle": c.subtitle,
                    "body_excerpt": c.body_excerpt,
                    "local_score": c.local_score,
                }
                for c in req.candidates
            ],
        }
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(self._timeout_s),
            ) as c:
                resp = await c.request(
                    self._method, self._url,
                    headers=self._build_headers(),
                    json=body,
                )
        except httpx.TimeoutException:
            _logger.warning(
                "search_reranker_timeout",
                url=self._url, timeout_s=self._timeout_s,
            )
            return {}
        except httpx.HTTPError as e:
            _logger.warning(
                "search_reranker_http_error",
                url=self._url, error=type(e).__name__,
            )
            return {}

        if resp.status_code >= 400:
            _logger.warning(
                "search_reranker_bad_status",
                url=self._url,
                status=resp.status_code,
                body=resp.text[:256],
            )
            return {}

        try:
            data = resp.json() if resp.text else {}
        except json.JSONDecodeError:
            _logger.warning(
                "search_reranker_bad_json",
                url=self._url, body=resp.text[:256],
            )
            return {}

        scores = self._parse_score_map(data)
        _logger.info(
            "search_reranker_ok",
            url=self._url,
            candidates_in=len(req.candidates),
            scores_out=len(scores),
        )
        return scores
