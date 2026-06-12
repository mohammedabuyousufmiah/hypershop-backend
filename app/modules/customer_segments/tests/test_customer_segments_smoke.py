"""Smoke test — module imports, tables register, scoring functions return ints."""
from __future__ import annotations


def test_module_imports_and_scoring():
    from app.modules.customer_segments import (
        codes,
        jobs,
        models,
        repository,
        schemas,
        service,
    )
    # Tables register.
    assert models.HypershopCustomerRfmScore.__tablename__ == (
        "hypershop_customer_rfm_scores"
    )
    assert models.HypershopCustomerSegment.__tablename__ == (
        "hypershop_customer_segments"
    )
    assert models.HypershopCustomerSegmentMembership.__tablename__ == (
        "hypershop_customer_segment_memberships"
    )

    # Modules wire together.
    assert hasattr(repository, "upsert_rfm_score")
    assert hasattr(repository, "replace_memberships")
    assert hasattr(service, "compute_rfm_for_customer")
    assert hasattr(service, "materialize_segment")
    assert hasattr(service, "export_audience")
    assert hasattr(jobs, "recompute_all_rfm_scores_job")
    assert hasattr(jobs, "materialize_all_segments_job")
    assert hasattr(schemas, "SegmentRead")
    assert codes.SEGMENT_VIP == "vip"

    # Scoring functions return valid quintile ints.
    for fn, arg in (
        (service.score_recency, 5),
        (service.score_recency, 200),
        (service.score_recency, 9999),
        (service.score_frequency, 0),
        (service.score_frequency, 50),
        (service.score_monetary, 0),
        (service.score_monetary, 2_000_000),
    ):
        v = fn(arg)
        assert isinstance(v, int)
        assert 1 <= v <= 5

    # Segment assignment surfaces valid codes.
    assert service.assign_segment(5, 5, 5) == "vip"
    assert service.assign_segment(1, 1, 1) == "dormant"
    assert service.assign_segment(5, 1, 1) == "new"
    assert service.assign_segment(1, 1, 5) == "cant_lose"
    assert service.assign_segment(2, 4, 2) == "loyal"
