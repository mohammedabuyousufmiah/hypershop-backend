from __future__ import annotations

from enum import StrEnum


class PackingSessionStatus(StrEnum):
    OPEN = "open"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class PackingLineStatus(StrEnum):
    OPEN = "open"  # not yet fully scanned
    COMPLETE = "complete"  # scanned_quantity == expected_quantity
    OVERRIDDEN = "overridden"  # supervisor approved a batch substitution


class ScanOutcome(StrEnum):
    """Every scan attempt — accepted or rejected — writes a ledger row with
    one of these outcomes. The ``packing_scans`` table is append-only; we
    keep rejections so wrong-item rates and supervisor-override frequency
    can be audited later.
    """

    ACCEPTED = "accepted"
    WRONG_ITEM = "wrong_item"  # variant doesn't match any open session line
    EXPIRED = "expired"  # batch is past expiry_date
    BATCH_MISMATCH = "batch_mismatch"  # scanned batch ≠ reserved batch (need supervisor)
    OVERRIDDEN = "overridden"  # supervisor approved a batch_mismatch
    OVER_QUANTITY = "over_quantity"  # already at expected_quantity, picker scanned extra
    UNKNOWN_BARCODE = "unknown_barcode"  # no variant has this barcode
