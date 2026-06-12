"""Cheap, no-DB sanity tests for the reporting registry + codes."""

from __future__ import annotations

from app.modules.reporting.builders import register_all
from app.modules.reporting.codes import ALL_BUILTIN_REPORT_CODES
from app.modules.reporting.registry import report_registry


def test_register_all_is_idempotent():
    register_all()
    first_codes = sorted(report_registry.codes())
    register_all()  # second call should NOT raise
    second_codes = sorted(report_registry.codes())
    assert first_codes == second_codes


def test_every_builtin_code_has_a_builder():
    register_all()
    missing = [
        c for c in ALL_BUILTIN_REPORT_CODES
        if report_registry.get(c) is None
    ]
    assert missing == [], f"codes without builders: {missing}"


def test_builder_metadata_is_self_consistent():
    register_all()
    for entry in report_registry.all():
        assert entry.code, "empty code"
        assert entry.default_name, f"empty name for {entry.code}"
        assert entry.default_columns, f"no columns for {entry.code}"
        for col in entry.default_columns:
            assert "key" in col, f"{entry.code}: column missing key"
            assert "label" in col, f"{entry.code}: column missing label"
        assert entry.default_export_formats, (
            f"{entry.code}: must allow at least one export format"
        )
