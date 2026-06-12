"""Smoke test: module imports + 2 ORM tables register + scoring formula sane."""
from __future__ import annotations


def test_module_imports_and_tables_register() -> None:
    from app.core.db.base import Base
    from app.modules.seller_rating import (
        codes,
        jobs,
        models,
        repository,
        schemas,
        service,
    )
    from app.modules.seller_rating.api import admin_router, public_router

    assert codes.TIER_PLATINUM == "platinum"
    assert hasattr(models, "HypershopSellerRating")
    assert hasattr(models, "HypershopSellerRatingSnapshot")
    assert hasattr(repository, "upsert_rating")
    assert hasattr(service, "compute_overall_score")
    assert hasattr(service, "compute_rating_for_seller")
    assert hasattr(jobs, "recompute_all_seller_ratings_job")
    assert admin_router.prefix == "/admin/seller-ratings"
    assert public_router.prefix == "/seller-ratings"
    for s in schemas.SellerRatingRead.model_fields:
        assert s

    tables = set(Base.metadata.tables.keys())
    assert {
        "hypershop_seller_ratings",
        "hypershop_seller_rating_snapshots",
    }.issubset(tables)


def test_scoring_formula_returns_valid_float() -> None:
    from app.modules.seller_rating.service import (
        compute_overall_score,
        score_to_tier,
    )

    perfect = compute_overall_score({
        "on_time": 1.0, "return": 0.0, "dispute": 1.0,
        "response": 1.0, "review": 5.0, "orders": 100,
    })
    assert 90.0 <= perfect <= 100.0
    assert score_to_tier(perfect) == "platinum"

    middling = compute_overall_score({
        "on_time": 0.95, "return": 0.05, "dispute": 0.90,
        "response": 2.0, "review": 4.5, "orders": 50,
    })
    assert 0.0 <= middling <= 100.0

    poor = compute_overall_score({
        "on_time": 0.30, "return": 0.40, "dispute": 0.20,
        "response": 40.0, "review": 1.5, "orders": 2,
    })
    assert poor < 40.0
    assert score_to_tier(poor) == "poor"

    none_score = compute_overall_score({
        "on_time": None, "return": None, "dispute": None,
        "response": None, "review": None, "orders": 0,
    })
    assert 0.0 <= none_score <= 100.0
