"""Pure-Python tests for ranking + ML score merging."""

from __future__ import annotations

from app.modules.search.ranking import combined_score, merge_with_ml_scores
from app.modules.search.state import SearchDocumentType


# ---------------- combined_score ----------------


def test_combined_score_product_unboosted() -> None:
    # ts_rank 0.8 * boost 1.0 * product prior 1.0 = 0.8
    assert combined_score(
        ts_rank=0.8, boost=1.0, document_type=SearchDocumentType.PRODUCT,
    ) == 0.8


def test_combined_score_brand_lower_prior() -> None:
    # ts_rank 0.8 * boost 1.0 * brand prior 0.7 = 0.56
    assert combined_score(
        ts_rank=0.8, boost=1.0, document_type=SearchDocumentType.BRAND,
    ) == 0.8 * 0.7


def test_combined_score_category_lowest_prior() -> None:
    assert combined_score(
        ts_rank=1.0, boost=1.0, document_type=SearchDocumentType.CATEGORY,
    ) == 0.5


def test_combined_score_boost_applied() -> None:
    assert combined_score(
        ts_rank=0.5, boost=2.0, document_type=SearchDocumentType.PRODUCT,
    ) == 1.0


def test_combined_score_zero_boost_buries_doc() -> None:
    assert combined_score(
        ts_rank=0.99, boost=0.0, document_type=SearchDocumentType.PRODUCT,
    ) == 0.0


def test_combined_score_unknown_type_falls_back_to_low_prior() -> None:
    # Unknown types get prior 0.5
    assert combined_score(
        ts_rank=1.0, boost=1.0, document_type="unknown_type",
    ) == 0.5


# ---------------- merge_with_ml_scores ----------------


def test_merge_blends_scores_with_default_weight() -> None:
    rows = [
        {"id": "a", "score": 1.0},
        {"id": "b", "score": 0.5},
    ]
    ml_scores = {"a": 0.0, "b": 1.0}
    out = merge_with_ml_scores(rows=rows, ml_scores=ml_scores, ml_weight=0.6)
    # 'b' should now win: a = 1.0*0.4 + 0.0*0.6 = 0.4
    #                     b = 0.5*0.4 + 1.0*0.6 = 0.8
    assert out[0]["id"] == "b"
    assert abs(out[0]["score"] - 0.8) < 1e-9
    assert abs(out[1]["score"] - 0.4) < 1e-9


def test_merge_keeps_local_score_for_missing_ml_signal() -> None:
    rows = [
        {"id": "a", "score": 0.9},
        {"id": "b", "score": 0.1},  # ML missing for b
    ]
    ml_scores = {"a": 0.0}
    out = merge_with_ml_scores(rows=rows, ml_scores=ml_scores, ml_weight=0.6)
    # b's score is unchanged; a is blended down
    b = next(r for r in out if r["id"] == "b")
    a = next(r for r in out if r["id"] == "a")
    assert b["score"] == 0.1
    assert b["ml_score"] is None
    assert abs(a["score"] - 0.9 * 0.4) < 1e-9
    assert a["ml_score"] == 0.0


def test_merge_weight_clamping() -> None:
    rows = [{"id": "a", "score": 1.0}]
    # Out-of-range ml_weight clamped to [0, 1]
    out = merge_with_ml_scores(rows=rows, ml_scores={"a": 0.5}, ml_weight=2.0)
    # ml_weight clamped to 1.0 → score = 0.5 fully
    assert abs(out[0]["score"] - 0.5) < 1e-9


def test_merge_returns_sorted_descending() -> None:
    rows = [
        {"id": "low", "score": 0.1},
        {"id": "high", "score": 0.9},
        {"id": "mid", "score": 0.5},
    ]
    out = merge_with_ml_scores(rows=rows, ml_scores={}, ml_weight=0.6)
    assert [r["id"] for r in out] == ["high", "mid", "low"]


def test_merge_empty_inputs() -> None:
    assert merge_with_ml_scores(rows=[], ml_scores={}, ml_weight=0.5) == []
