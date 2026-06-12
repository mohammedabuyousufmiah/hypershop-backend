"""Smoke test — imports + table registration + row validation."""
from __future__ import annotations

from uuid import uuid4


def test_bulk_upload_smoke() -> None:
    from app.core.db.base import Base
    from app.modules.bulk_upload import models  # noqa: F401
    from app.modules.bulk_upload.parser import detect_columns
    from app.modules.bulk_upload.service import _validate_row

    table_names = set(Base.metadata.tables.keys())
    assert "hypershop_bulk_upload_jobs" in table_names
    assert "hypershop_bulk_upload_rows" in table_names

    col_map = detect_columns(
        ["SKU", "Title", "Brand", "Category", "Price (paisa)", "Stock"],
    )
    assert col_map["sku"] == 0
    assert col_map["title"] == 1
    assert col_map["brand"] == 2
    assert col_map["category"] == 3
    assert col_map["price_minor"] == 4
    assert col_map["stock_qty"] == 5

    brand_id = uuid4()
    cat_id = uuid4()
    brand_map = {"hypershop": brand_id}
    cat_map = {"electronics": cat_id}

    good_row = {
        "sku": "DEMO-001",
        "title": "Sample",
        "brand": "Hypershop",
        "category": "electronics",
        "price_minor": "29900",
        "stock_qty": "10",
    }
    ok, code, msg = _validate_row(good_row, set(), brand_map, cat_map)
    assert ok is True
    assert code is None
    assert msg is None

    bad_row = {
        "sku": "??",
        "title": "Sample",
        "brand": "Hypershop",
        "category": "electronics",
        "price_minor": "100",
        "stock_qty": "1",
    }
    ok2, code2, _ = _validate_row(bad_row, set(), brand_map, cat_map)
    assert ok2 is False
    assert code2 == "invalid_sku"
