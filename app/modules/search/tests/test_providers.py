"""Reranker provider tests — construction + score-map parser."""

from __future__ import annotations

import pytest

from app.core.errors import IntegrationError
from app.modules.search.providers.base import RerankRequest
from app.modules.search.providers.external_ml import ExternalMlReranker
from app.modules.search.providers.not_configured import NotConfiguredReranker
from app.modules.search.providers.registry import (
    bind_reranker,
    get_reranker,
    reset_reranker_binding,
)


# ---------------- Construction ----------------


def test_external_ml_refuses_without_url() -> None:
    with pytest.raises(IntegrationError) as exc:
        ExternalMlReranker(api_url="")
    assert exc.value.details.get("missing_setting") == "SEARCH_RERANK_API_URL"


def test_external_ml_refuses_with_blank_url() -> None:
    with pytest.raises(IntegrationError):
        ExternalMlReranker(api_url="   ")


def test_external_ml_refuses_with_bad_static_headers_json() -> None:
    with pytest.raises(IntegrationError):
        ExternalMlReranker(
            api_url="https://x.example/rerank",
            static_headers_json="not-valid-json",
        )


def test_external_ml_refuses_with_static_headers_not_object() -> None:
    with pytest.raises(IntegrationError):
        ExternalMlReranker(
            api_url="https://x.example/rerank",
            static_headers_json='["array", "not", "object"]',
        )


def test_external_ml_constructs_with_minimum() -> None:
    r = ExternalMlReranker(api_url="https://x.example/rerank")
    assert r.name == "external_ml"


def test_external_ml_constructs_with_full_config() -> None:
    r = ExternalMlReranker(
        api_url="https://x.example/rerank",
        api_token="secret-token",
        auth_header="X-API-Key",
        auth_scheme="none",
        method="PUT",
        static_headers_json='{"X-Tenant": "hypershop"}',
        timeout_s=30,
    )
    headers = r._build_headers()
    # 'none' scheme means raw token, not "Bearer xxx"
    assert headers["X-API-Key"] == "secret-token"
    assert headers["X-Tenant"] == "hypershop"


def test_external_ml_with_bearer_scheme() -> None:
    r = ExternalMlReranker(
        api_url="https://x.example/rerank",
        api_token="secret-token",
        auth_header="Authorization",
        auth_scheme="Bearer",
    )
    headers = r._build_headers()
    assert headers["Authorization"] == "Bearer secret-token"


# ---------------- Score-map parser (the resilient bit) ----------------


def test_score_map_parses_results_array_shape() -> None:
    body = {
        "results": [
            {"id": "doc1", "score": 0.91},
            {"id": "doc2", "score": 0.42},
        ],
    }
    out = ExternalMlReranker._parse_score_map(body)
    assert out == {"doc1": 0.91, "doc2": 0.42}


def test_score_map_parses_scores_object_shape() -> None:
    body = {"scores": {"doc1": 0.91, "doc2": 0.42}}
    out = ExternalMlReranker._parse_score_map(body)
    assert out == {"doc1": 0.91, "doc2": 0.42}


def test_score_map_skips_missing_id_or_score() -> None:
    body = {
        "results": [
            {"id": "doc1", "score": 0.91},
            {"id": "doc2"},                      # no score
            {"score": 0.5},                       # no id
            {"id": "doc3", "score": "not-a-num"}, # bad score
            {"id": "doc4", "score": 0.42},
        ],
    }
    out = ExternalMlReranker._parse_score_map(body)
    assert out == {"doc1": 0.91, "doc4": 0.42}


def test_score_map_handles_unknown_shape() -> None:
    # Random JSON the customer's adapter shouldn't have produced.
    assert ExternalMlReranker._parse_score_map({"unexpected": "field"}) == {}
    assert ExternalMlReranker._parse_score_map([1, 2, 3]) == {}
    assert ExternalMlReranker._parse_score_map(None) == {}
    assert ExternalMlReranker._parse_score_map("just a string") == {}


# ---------------- NotConfigured behaviour ----------------


@pytest.mark.asyncio
async def test_not_configured_returns_empty_dict() -> None:
    r = NotConfiguredReranker()
    out = await r.rerank(RerankRequest(query="x", candidates=(), limit=10))
    assert out == {}
    assert r.name == "not_configured"


# ---------------- Registry ----------------


def test_registry_default_is_not_configured() -> None:
    reset_reranker_binding()
    assert get_reranker().name == "not_configured"


def test_registry_round_trip() -> None:
    reset_reranker_binding()
    r = ExternalMlReranker(api_url="https://x.example/rerank")
    bind_reranker(r)
    assert get_reranker() is r
    reset_reranker_binding()
    assert get_reranker().name == "not_configured"
