"""Inventory Manager operational action catalog.

Sourced from the Inventory Manager Rules package (2026-05-26). Same
shape as ``finance/operations_actions.py`` so the service layer can
mirror the validation cascade verbatim.
"""

from __future__ import annotations

from enum import StrEnum


class InventoryAction(StrEnum):
    """All distinct Inventory Manager action verbs."""

    # ----- Read / dashboard -----
    VIEW_INVENTORY_DASHBOARD = "VIEW_INVENTORY_DASHBOARD"
    VIEW_STOCK_STATUS = "VIEW_STOCK_STATUS"
    VIEW_WAREHOUSE_BRANCH_STOCK = "VIEW_WAREHOUSE_BRANCH_STOCK"
    VIEW_RESERVED_STOCK = "VIEW_RESERVED_STOCK"
    VIEW_STOCK_MOVEMENT_LEDGER = "VIEW_STOCK_MOVEMENT_LEDGER"
    MONITOR_SELLER_STOCK_ISSUE = "MONITOR_SELLER_STOCK_ISSUE"
    MONITOR_SELLER_STOCK_ACCURACY = "MONITOR_SELLER_STOCK_ACCURACY"

    # ----- Reservations / control -----
    CONTROL_WAREHOUSE_BRANCH_INVENTORY = "CONTROL_WAREHOUSE_BRANCH_INVENTORY"
    VERIFY_STOCK_RESERVATION = "VERIFY_STOCK_RESERVATION"
    AUDIT_STOCK_DEDUCTION = "AUDIT_STOCK_DEDUCTION"

    # ----- Adjustments -----
    APPROVE_STOCK_ADJUSTMENT = "APPROVE_STOCK_ADJUSTMENT"
    REJECT_STOCK_ADJUSTMENT = "REJECT_STOCK_ADJUSTMENT"

    # ----- Return-to-stock (post-return QC) -----
    APPROVE_RETURN_TO_STOCK = "APPROVE_RETURN_TO_STOCK"
    REJECT_RETURN_TO_STOCK = "REJECT_RETURN_TO_STOCK"

    # ----- Damaged / lost -----
    APPROVE_DAMAGED_LOST_INVENTORY_ADJUSTMENT = "APPROVE_DAMAGED_LOST_INVENTORY_ADJUSTMENT"
    MARK_DAMAGED_LOST_STOCK_AFTER_EVIDENCE = "MARK_DAMAGED_LOST_STOCK_AFTER_EVIDENCE"

    # ----- Low-stock alerts -----
    CREATE_LOW_STOCK_ALERT = "CREATE_LOW_STOCK_ALERT"
    SET_LOW_STOCK_ALERT_THRESHOLD = "SET_LOW_STOCK_ALERT_THRESHOLD"

    # ----- Discrepancy / control -----
    INVESTIGATE_INVENTORY_DISCREPANCY = "INVESTIGATE_INVENTORY_DISCREPANCY"
    REVIEW_STOCK_DISCREPANCY = "REVIEW_STOCK_DISCREPANCY"
    CONTROL_PRODUCT_AVAILABILITY = "CONTROL_PRODUCT_AVAILABILITY"
    BLOCK_UNAVAILABLE_STOCK_FROM_SELLING = "BLOCK_UNAVAILABLE_STOCK_FROM_SELLING"
    UNBLOCK_STOCK = "UNBLOCK_STOCK"

    # ----- Seller-side audits -----
    REQUEST_SELLER_STOCK_AUDIT = "REQUEST_SELLER_STOCK_AUDIT"

    # ----- Notes / export -----
    ADD_INVENTORY_NOTE = "ADD_INVENTORY_NOTE"
    EXPORT_INVENTORY_AUDIT = "EXPORT_INVENTORY_AUDIT"


VIEW_ACTIONS: frozenset[InventoryAction] = frozenset(
    {
        InventoryAction.VIEW_INVENTORY_DASHBOARD,
        InventoryAction.VIEW_STOCK_STATUS,
        InventoryAction.VIEW_WAREHOUSE_BRANCH_STOCK,
        InventoryAction.VIEW_RESERVED_STOCK,
        InventoryAction.VIEW_STOCK_MOVEMENT_LEDGER,
        InventoryAction.MONITOR_SELLER_STOCK_ISSUE,
        InventoryAction.MONITOR_SELLER_STOCK_ACCURACY,
    }
)

# Actions that always require an evidence URL (damage / loss / blocks).
EVIDENCE_REQUIRED_ACTIONS: frozenset[InventoryAction] = frozenset(
    {
        InventoryAction.APPROVE_STOCK_ADJUSTMENT,
        InventoryAction.APPROVE_RETURN_TO_STOCK,
        InventoryAction.APPROVE_DAMAGED_LOST_INVENTORY_ADJUSTMENT,
        InventoryAction.MARK_DAMAGED_LOST_STOCK_AFTER_EVIDENCE,
        InventoryAction.BLOCK_UNAVAILABLE_STOCK_FROM_SELLING,
        InventoryAction.EXPORT_INVENTORY_AUDIT,
    }
)

# Actions that always require a reference_id (request / movement / order).
REFERENCE_REQUIRED_ACTIONS: frozenset[InventoryAction] = frozenset(
    {
        InventoryAction.APPROVE_STOCK_ADJUSTMENT,
        InventoryAction.REJECT_STOCK_ADJUSTMENT,
        InventoryAction.APPROVE_RETURN_TO_STOCK,
        InventoryAction.REJECT_RETURN_TO_STOCK,
        InventoryAction.APPROVE_DAMAGED_LOST_INVENTORY_ADJUSTMENT,
        InventoryAction.MARK_DAMAGED_LOST_STOCK_AFTER_EVIDENCE,
        InventoryAction.INVESTIGATE_INVENTORY_DISCREPANCY,
        InventoryAction.REQUEST_SELLER_STOCK_AUDIT,
        InventoryAction.BLOCK_UNAVAILABLE_STOCK_FROM_SELLING,
    }
)

# Inventory Manager cannot self-approve their own request (requester
# ≠ approver).
NO_SELF_APPROVE_ACTIONS: frozenset[InventoryAction] = frozenset(
    {
        InventoryAction.APPROVE_STOCK_ADJUSTMENT,
        InventoryAction.APPROVE_RETURN_TO_STOCK,
        InventoryAction.APPROVE_DAMAGED_LOST_INVENTORY_ADJUSTMENT,
    }
)
