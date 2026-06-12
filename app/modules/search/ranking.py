"""Local ranking — combine Postgres ts_rank with per-document boost
and a per-type prior so products outrank brands/categories by default
when otherwise tied.

The ML reranker (if bound) overrides this. Local rank is the fallback
+ the input the reranker sees + the tiebreaker when the reranker
returns equal scores.
"""

from __future__ import annotations

from app.modules.search.state import SearchDocumentType


# Type prior: products are usually what the customer wants;
# brands/categories rank slightly lower so a search for "panadol" puts
# the product first even though "panadol" is also the brand name.
_TYPE_PRIOR: dict[str, float] = {
    SearchDocumentType.PRODUCT: 1.0,
    SearchDocumentType.BRAND: 0.7,
    SearchDocumentType.CATEGORY: 0.5,
}


def combined_score(*, ts_rank: float, boost: float, document_type: str) -> float:
    """Final local score = ts_rank * boost * type_prior.

    ts_rank from Postgres is in roughly [0, 1] for typical queries.
    boost is operator-controlled per document (default 1.0).
    Type prior nudges by document_type.

    All factors multiplicative so a single zero kills the score (used
    by the indexer to bury archived documents — set boost=0).
    """
    prior = _TYPE_PRIOR.get(document_type, 0.5)
    return float(ts_rank) * float(boost) * prior


def merge_with_ml_scores(
    *,
    rows: list[dict],
    ml_scores: dict[str, float],
    ml_weight: float = 0.6,
) -> list[dict]:
    """Re-score ``rows`` by blending the local score with ML scores.

    ``ml_scores`` is keyed by document_id (str). Documents missing
    from ml_scores keep their local score (no ML signal = trust local).
    ``ml_weight`` is the share of the final score the ML signal owns;
    must be in [0, 1].

    Mutates the rows' ``score`` field in place AND returns the list
    sorted descending by the new score.
    """
    ml_weight = max(0.0, min(1.0, float(ml_weight)))
    local_weight = 1.0 - ml_weight
    for row in rows:
        local = float(row.get("score", 0.0))
        ml = ml_scores.get(str(row["id"]))
        if ml is None:
            row["local_score"] = local
            row["ml_score"] = None
            # Keep score as-is — no ML signal for this doc.
            continue
        row["local_score"] = local
        row["ml_score"] = float(ml)
        row["score"] = local * local_weight + float(ml) * ml_weight
    rows.sort(key=lambda r: r.get("score", 0.0), reverse=True)
    return rows
