"""Mother-QR canonical state machine — transition table + role gates.

Sourced verbatim from the
``hypershop-warehouse-mother-qr-updated`` package
(``backend/app/domain.py:TRANSITIONS``). Each row is

  ScanAction → (allowed_source_statuses, target_status, allowed_roles)

The role gate is the FIRST line of defence — even before the matrix
red-line — because rider-only finality (CONFIRM_DELIVERED) is a
hard rule with no Admin override.
"""

from __future__ import annotations

from enum import StrEnum


class MotherQrStatus(StrEnum):
    """42 status values covering the full lifecycle + exceptions."""

    GATE_IN = "GATE_IN"
    RECEIVED = "RECEIVED"
    QC_PENDING = "QC_PENDING"
    QC_PASSED = "QC_PASSED"
    QC_FAILED = "QC_FAILED"
    HOLD = "HOLD"
    HOLD_FOR_REVIEW = "HOLD_FOR_REVIEW"
    DAMAGED_AT_RECEIVING = "DAMAGED_AT_RECEIVING"
    WRONG_ITEM_RECEIVED = "WRONG_ITEM_RECEIVED"
    SHORT_QUANTITY = "SHORT_QUANTITY"
    EXCESS_QUANTITY = "EXCESS_QUANTITY"
    RECEIVING_DISCREPANCY = "RECEIVING_DISCREPANCY"
    SHELVED = "SHELVED"
    SHELF_ASSIGNED = "SHELF_ASSIGNED"
    SELLABLE_STOCK = "SELLABLE_STOCK"
    AVAILABLE = "AVAILABLE"
    DAMAGED_STOCK = "DAMAGED_STOCK"
    RESERVED = "RESERVED"
    PICKED = "PICKED"
    PACKED = "PACKED"
    DISPATCH_READY = "DISPATCH_READY"
    RIDER_HANDOVER = "RIDER_HANDOVER"
    HANDED_TO_RIDER = "HANDED_TO_RIDER"
    OUT_FOR_DELIVERY = "OUT_FOR_DELIVERY"
    FAILED_DELIVERY_REVIEW = "FAILED_DELIVERY_REVIEW"
    RESCHEDULED_DELIVERY = "RESCHEDULED_DELIVERY"
    FAILED_DELIVERY_SUSPICIOUS = "FAILED_DELIVERY_SUSPICIOUS"
    FAILED_DELIVERY_MANAGER_REVIEW = "FAILED_DELIVERY_MANAGER_REVIEW"
    DELIVERED = "DELIVERED"
    QUARANTINED = "QUARANTINED"
    RETURN_REQUESTED = "RETURN_REQUESTED"
    RETURNED_TO_WAREHOUSE = "RETURNED_TO_WAREHOUSE"
    RETURN_RECEIVED = "RETURN_RECEIVED"
    RETURN_QC_PENDING = "RETURN_QC_PENDING"
    RETURNED_TO_STOCK = "RETURNED_TO_STOCK"
    DAMAGED = "DAMAGED"
    LOST = "LOST"
    DISPOSED = "DISPOSED"
    SELLER_RETURN = "SELLER_RETURN"
    RETURN_RECEIVED_AT_HUB = "RETURN_RECEIVED_AT_HUB"
    RETURN_INVENTORY_REVIEW = "RETURN_INVENTORY_REVIEW"
    RETURN_FINANCE_REVIEW = "RETURN_FINANCE_REVIEW"


class ScanAction(StrEnum):
    """27 scan actions — one per state transition trigger."""

    GATE_IN = "GATE_IN"
    RECEIVE = "RECEIVE"
    CREATE_RECEIVING_BATCH = "CREATE_RECEIVING_BATCH"
    PRINT_MOTHER_QR_LABEL = "PRINT_MOTHER_QR_LABEL"
    START_QC = "START_QC"
    PASS_QC = "PASS_QC"
    FAIL_QC = "FAIL_QC"
    HOLD_FOR_REVIEW = "HOLD_FOR_REVIEW"
    MARK_DAMAGED_AT_RECEIVING = "MARK_DAMAGED_AT_RECEIVING"
    MARK_WRONG_ITEM_RECEIVED = "MARK_WRONG_ITEM_RECEIVED"
    SHELF = "SHELF"
    MARK_SELLABLE = "MARK_SELLABLE"
    RESERVE_FOR_ORDER = "RESERVE_FOR_ORDER"
    PICK = "PICK"
    PACK = "PACK"
    MARK_DISPATCH_READY = "MARK_DISPATCH_READY"
    RIDER_HANDOVER = "RIDER_HANDOVER"
    START_DELIVERY = "START_DELIVERY"
    MARK_FAILED_DELIVERY = "MARK_FAILED_DELIVERY"
    REVIEW_FAILED_DELIVERY = "REVIEW_FAILED_DELIVERY"
    CONFIRM_DELIVERED = "CONFIRM_DELIVERED"
    QUARANTINE = "QUARANTINE"
    RETURN_TO_WAREHOUSE = "RETURN_TO_WAREHOUSE"
    RECEIVE_RETURN_AT_HUB = "RECEIVE_RETURN_AT_HUB"
    START_RETURN_QC = "START_RETURN_QC"
    REVIEW_RETURN_INVENTORY = "REVIEW_RETURN_INVENTORY"
    REVIEW_RETURN_FINANCE = "REVIEW_RETURN_FINANCE"


# Role-string sets (string names — the warehouse package uses string
# role IDs, not the Phase A role catalog enum).
_RECEIVING_ROLES = frozenset({"warehouse_staff", "warehouse_receiving_staff",
                              "warehouse_receiver"})
_QC_ROLES = frozenset({"qc_staff", "inventory_supervisor", "qc_agent"})
_SHELF_ROLES = frozenset({"warehouse_staff", "inventory_supervisor",
                          "shelf_operator"})
_INV_SYSTEM_ROLES = frozenset({"inventory_system", "inventory_engine"})
_ORDER_ENGINE = frozenset({"order_engine"})
_PICKER_ROLES = frozenset({"warehouse_staff", "inventory_supervisor", "picker"})
_PACKER_ROLES = frozenset({"packing_staff", "packing_supervisor",
                           "warehouse_staff", "inventory_supervisor", "packer"})
_DISPATCH_ROLES = frozenset({"dispatcher", "fulfillment_supervisor"})
_RIDER_ROLE = frozenset({"rider"})
_FULFIL_SUP_ROLE = frozenset({"fulfillment_supervisor"})
_WAREHOUSE_MGR = frozenset({"warehouse_manager"})
_RETURN_OPS = frozenset({"fulfillment_supervisor", "operations_manager"})
_RETURN_TEAM = frozenset({"return_team", "warehouse_staff"})
_INV_MGR_ROLE = frozenset({"inventory_manager", "inventory_supervisor"})
_FIN_MGR_ROLE = frozenset({"finance_manager"})


# (allowed_source_statuses, target_status, allowed_roles)
TRANSITIONS: dict[
    ScanAction,
    tuple[frozenset[MotherQrStatus], MotherQrStatus, frozenset[str]],
] = {
    ScanAction.RECEIVE: (
        frozenset({MotherQrStatus.GATE_IN}),
        MotherQrStatus.RECEIVED,
        _RECEIVING_ROLES,
    ),
    ScanAction.START_QC: (
        frozenset({MotherQrStatus.RECEIVED}),
        MotherQrStatus.QC_PENDING,
        _QC_ROLES,
    ),
    ScanAction.PASS_QC: (
        frozenset({MotherQrStatus.QC_PENDING}),
        MotherQrStatus.QC_PASSED,
        _QC_ROLES,
    ),
    ScanAction.FAIL_QC: (
        frozenset({MotherQrStatus.QC_PENDING}),
        MotherQrStatus.QC_FAILED,
        _QC_ROLES,
    ),
    ScanAction.HOLD_FOR_REVIEW: (
        frozenset({MotherQrStatus.QC_PENDING}),
        MotherQrStatus.HOLD,
        _QC_ROLES,
    ),
    ScanAction.MARK_DAMAGED_AT_RECEIVING: (
        frozenset({MotherQrStatus.QC_PENDING}),
        MotherQrStatus.DAMAGED_AT_RECEIVING,
        _QC_ROLES,
    ),
    ScanAction.MARK_WRONG_ITEM_RECEIVED: (
        frozenset({MotherQrStatus.QC_PENDING}),
        MotherQrStatus.WRONG_ITEM_RECEIVED,
        _QC_ROLES,
    ),
    ScanAction.SHELF: (
        frozenset({MotherQrStatus.QC_PASSED}),
        MotherQrStatus.SHELF_ASSIGNED,
        _SHELF_ROLES,
    ),
    ScanAction.MARK_SELLABLE: (
        frozenset({MotherQrStatus.SHELF_ASSIGNED, MotherQrStatus.AVAILABLE}),
        MotherQrStatus.AVAILABLE,
        _INV_SYSTEM_ROLES,
    ),
    ScanAction.RESERVE_FOR_ORDER: (
        frozenset({MotherQrStatus.AVAILABLE}),
        MotherQrStatus.RESERVED,
        _ORDER_ENGINE,
    ),
    ScanAction.PICK: (
        frozenset({MotherQrStatus.RESERVED}),
        MotherQrStatus.PICKED,
        _PICKER_ROLES,
    ),
    ScanAction.PACK: (
        frozenset({MotherQrStatus.PICKED}),
        MotherQrStatus.PACKED,
        _PACKER_ROLES,
    ),
    ScanAction.MARK_DISPATCH_READY: (
        frozenset({MotherQrStatus.PACKED}),
        MotherQrStatus.DISPATCH_READY,
        _DISPATCH_ROLES,
    ),
    ScanAction.RIDER_HANDOVER: (
        frozenset({MotherQrStatus.DISPATCH_READY}),
        MotherQrStatus.HANDED_TO_RIDER,
        _DISPATCH_ROLES,
    ),
    ScanAction.START_DELIVERY: (
        frozenset({
            MotherQrStatus.HANDED_TO_RIDER,
            MotherQrStatus.RIDER_HANDOVER,
            MotherQrStatus.RESCHEDULED_DELIVERY,
        }),
        MotherQrStatus.OUT_FOR_DELIVERY,
        _RIDER_ROLE,
    ),
    ScanAction.CONFIRM_DELIVERED: (
        frozenset({MotherQrStatus.OUT_FOR_DELIVERY}),
        MotherQrStatus.DELIVERED,
        _RIDER_ROLE,
    ),
    ScanAction.MARK_FAILED_DELIVERY: (
        frozenset({MotherQrStatus.OUT_FOR_DELIVERY}),
        MotherQrStatus.FAILED_DELIVERY_REVIEW,
        _RIDER_ROLE,
    ),
    ScanAction.REVIEW_FAILED_DELIVERY: (
        frozenset({MotherQrStatus.FAILED_DELIVERY_REVIEW}),
        MotherQrStatus.FAILED_DELIVERY_REVIEW,
        _FULFIL_SUP_ROLE,
    ),
    ScanAction.QUARANTINE: (
        frozenset({
            MotherQrStatus.RECEIVED,
            MotherQrStatus.QC_PENDING,
            MotherQrStatus.QC_FAILED,
            MotherQrStatus.SHELVED,
            MotherQrStatus.PICKED,
            MotherQrStatus.PACKED,
        }),
        MotherQrStatus.QUARANTINED,
        _WAREHOUSE_MGR,
    ),
    ScanAction.RETURN_TO_WAREHOUSE: (
        frozenset({
            MotherQrStatus.HANDED_TO_RIDER,
            MotherQrStatus.RIDER_HANDOVER,
            MotherQrStatus.OUT_FOR_DELIVERY,
            MotherQrStatus.FAILED_DELIVERY_MANAGER_REVIEW,
            MotherQrStatus.FAILED_DELIVERY_SUSPICIOUS,
        }),
        MotherQrStatus.RETURN_REQUESTED,
        _RETURN_OPS,
    ),
    ScanAction.RECEIVE_RETURN_AT_HUB: (
        frozenset({
            MotherQrStatus.RETURN_REQUESTED,
            MotherQrStatus.RETURNED_TO_WAREHOUSE,
        }),
        MotherQrStatus.RETURN_RECEIVED,
        _RETURN_TEAM,
    ),
    ScanAction.START_RETURN_QC: (
        frozenset({MotherQrStatus.RETURN_RECEIVED}),
        MotherQrStatus.RETURN_QC_PENDING,
        _QC_ROLES,
    ),
    ScanAction.REVIEW_RETURN_INVENTORY: (
        frozenset({MotherQrStatus.RETURN_QC_PENDING}),
        MotherQrStatus.RETURNED_TO_STOCK,
        _INV_MGR_ROLE,
    ),
    ScanAction.REVIEW_RETURN_FINANCE: (
        frozenset({MotherQrStatus.RETURNED_TO_STOCK,
                   MotherQrStatus.RETURN_QC_PENDING}),
        MotherQrStatus.RETURN_FINANCE_REVIEW,
        _FIN_MGR_ROLE,
    ),
}
