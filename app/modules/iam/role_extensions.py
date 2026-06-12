"""Fine-grained role + permission additions sourced from the 4
role-rule packages (2026-05-26).

Why a sidecar file?
  - The canonical ``permissions.py`` already weighs ~1100 lines and
    holds the original 17 roles.
  - The bootstrap CLI seeds both ``permissions.ALL_ROLES`` and the
    extensions module below — so we add new roles / perms without
    rewriting the historical file (smaller diffs, fewer merge
    conflicts).

What is here
  1. New fine-grained permission constants (Finance / Inventory /
     Warehouse / Manager / Supervisor surfaces).
  2. Four NEW roles that the matrix relies on but ``permissions.py``
     did not yet define: ``inventory_manager``, ``operations_manager_lm``
     (last-mile flavour distinct from the existing ``rider_manager``),
     ``warehouse_receiver``, ``warehouse_packer``.
  3. ``ROLE_PERMISSION_PATCHES`` — extra perms to graft onto the
     existing roles in ``permissions.py`` (frozen dataclasses can't be
     mutated, so the seeder reads from this dict and unions on insert).

See ``docs/AUTHORITY_MATRIX.md`` for the human-readable rules.
"""

from __future__ import annotations

from app.modules.iam.permissions import RoleSpec

# ============================================================
#  New permission constants
# ============================================================
# Finance — granular fan-out of the legacy ``finance.*`` four-perm
# set (read / post / settle / close / adjust). Each new perm represents
# an individual workflow that the Finance Manager dashboard exposes.
P_FIN_DASHBOARD_VIEW = "finance.dashboard.view"
P_FIN_REPORTS_VIEW = "finance.reports.view"
P_FIN_PAYMENT_STATUS_VIEW = "finance.payment.status.view"
P_FIN_RECONCILE_GATEWAY = "finance.reconciliation.gateway"
P_FIN_RECONCILE_COD = "finance.reconciliation.cod"
P_FIN_COD_SETTLE_VERIFY = "finance.cod.settlement.verify"
P_FIN_COD_SETTLE_REJECT = "finance.cod.settlement.reject"
P_FIN_COD_MISMATCH_RECONCILE = "finance.cod.mismatch.reconcile"
P_FIN_REFUND_VIEW = "finance.refund.view"
P_FIN_REFUND_APPROVE = "finance.refund.approve"
P_FIN_REFUND_REJECT = "finance.refund.reject"
P_FIN_REFUND_HOLD = "finance.refund.hold"
P_FIN_COMPENSATION_APPROVE = "finance.compensation.approve"
P_FIN_DELIVERY_FEE_RECONCILE = "finance.delivery_fee.reconcile"
P_FIN_FULFILLMENT_LOSS_REVIEW = "finance.fulfillment.loss.review"
P_FIN_DAMAGE_LOSS_IMPACT_APPROVE = "finance.damage_lost.financial_impact.approve"
P_FIN_RIDER_COD_LIABILITY_RECONCILE = "finance.rider.cod_liability.reconcile"
P_FIN_SELLER_LEDGER_VIEW = "finance.seller.ledger.view"
P_FIN_SELLER_STATEMENT_RECONCILE = "finance.seller.statement.reconcile"
P_FIN_SELLER_PAYOUT_APPROVE = "finance.seller.payout.approve"
P_FIN_SELLER_PAYOUT_HOLD = "finance.seller.payout.hold"
P_FIN_SELLER_PAYOUT_RELEASE = "finance.seller.payout.release"
P_FIN_SELLER_PAYOUT_HOLD_RELEASE = "finance.seller.payout.hold.release"
P_FIN_SELLER_CHARGEBACK_APPROVE = "finance.seller.chargeback.approve"
P_FIN_RIDER_PAYOUT_APPROVE = "finance.rider.payout.approve"
P_FIN_RIDER_PAYOUT_HOLD = "finance.rider.payout.hold"
P_FIN_RIDER_PAYOUT_RELEASE = "finance.rider.payout.release"
P_FIN_RIDER_PAYOUT_VERIFY = "finance.rider.payout.verify"
P_FIN_WALLET_LEDGER_VIEW = "finance.wallet.ledger.view"
P_FIN_WALLET_ADJUSTMENT_CREATE = "finance.wallet.adjustment.create"
P_FIN_WALLET_ADJUSTMENT_APPROVE = "finance.wallet.adjustment.approve"
P_FIN_WALLET_MISMATCH_INVESTIGATE = "finance.wallet.mismatch.investigate"
P_FIN_PAYOUT_FREEZE = "finance.payout.freeze"
P_FIN_DISPUTE_INVESTIGATE = "finance.dispute.investigate"
P_FIN_TRANSACTION_ESCALATE = "finance.transaction.escalate"
P_FIN_DAILY_CLOSING_SUBMIT = "finance.daily_closing.submit"
P_FIN_REPORT_EXPORT = "finance.report.export"
P_FIN_AUDIT_EXPORT = "finance.audit.export"
P_FIN_NOTE_ADD = "finance.note.add"

# Inventory — fan-out from coarse ``inventory.read / receive / adjust /
# count.approve`` to per-workflow perms.
P_INV_DASHBOARD_VIEW = "inventory.dashboard.view"
P_INV_STOCK_VIEW = "inventory.stock.view"
P_INV_RESERVED_VIEW = "inventory.reserved_stock.view"
P_INV_MOVEMENT_VIEW = "inventory.stock_movement.view"
P_INV_LOW_STOCK_ALERT = "inventory.low_stock.alert"
P_INV_OUT_OF_STOCK_BLOCK = "inventory.out_of_stock.block"
P_INV_SELLER_STOCK_MONITOR = "inventory.seller_stock.monitor"
P_INV_SELLER_STOCK_AUDIT_REQUEST = "inventory.seller_stock.audit.request"
P_INV_STOCK_ADJUST_APPROVE = "inventory.stock.adjust.approve"
P_INV_STOCK_ADJUST_REJECT = "inventory.stock.adjust.reject"
P_INV_STOCK_DISCREPANCY_REVIEW = "inventory.stock.discrepancy.review"
P_INV_RETURN_TO_STOCK_APPROVE = "inventory.return_to_stock.approve"
P_INV_RETURN_TO_STOCK_REJECT = "inventory.return_to_stock.reject"
P_INV_DAMAGED_LOST_MARK = "inventory.damaged_lost.mark"
P_INV_WAREHOUSE_STOCK_VIEW = "inventory.warehouse_stock.view"
P_INV_AUDIT_EXPORT = "inventory.audit.export"

# Warehouse — Mother-QR lifecycle. Sub-roles consume different subsets.
P_WH_GATE_IN_CREATE = "warehouse.gate_in.create"
P_WH_MOTHER_QR_GENERATE = "warehouse.mother_qr.generate"
P_WH_MOTHER_QR_PRINT = "warehouse.mother_qr.print"
P_WH_RECEIVING_CONFIRM = "warehouse.receiving.confirm"
P_WH_QC_START = "warehouse.qc.start"
P_WH_QC_PASS = "warehouse.qc.pass"
P_WH_QC_FAIL = "warehouse.qc.fail"
P_WH_SHELF_ASSIGN = "warehouse.shelf.assign"
P_WH_PICK_EXECUTE = "warehouse.pick.execute"
P_WH_PICK_OVERRIDE_REQUEST = "warehouse.pick.override.request"
P_WH_PICK_OVERRIDE_APPROVE = "warehouse.pick.override.approve"
P_WH_PACK_EXECUTE = "warehouse.pack.execute"
P_WH_PARCEL_QR_GENERATE = "warehouse.parcel_qr.generate"
P_WH_DISPATCH_READY = "warehouse.dispatch.ready"
P_WH_RIDER_HANDOVER = "warehouse.rider.handover"
P_WH_RIDER_HANDOVER_HIGH_VALUE_APPROVE = "warehouse.rider.handover.approve_high_value"
P_WH_DELIVERY_OFD = "warehouse.delivery.out_for_delivery"
P_WH_DELIVERY_FAILED_MARK = "warehouse.delivery.failed.mark"
P_WH_DELIVERY_FAILED_REVIEW = "warehouse.delivery.failed.review"
P_WH_DELIVERY_COMPLETE = "warehouse.delivery.complete"
P_WH_RETURN_RECEIVE = "warehouse.return.receive"
P_WH_RETURN_QC = "warehouse.return.qc"
P_WH_RETURN_DISPOSITION = "warehouse.return.disposition"
P_WH_QUARANTINE_REQUEST = "warehouse.quarantine.request"
P_WH_QUARANTINE_APPROVE = "warehouse.quarantine.approve"
P_WH_MANUAL_AVAILABLE_REQUEST = "warehouse.manual_available.request"
P_WH_MANUAL_AVAILABLE_APPROVE = "warehouse.manual_available.approve"
P_WH_SCAN_LOG_VIEW = "warehouse.scan_log.view"
P_WH_SCAN_LOG_EXPORT = "warehouse.scan_log.export"

# Manager (last-mile / operations) — decision verbs the supervisor lacks.
P_MGR_APPROVAL_QUEUE_VIEW = "manager.approval.queue.view"
P_MGR_APPROVAL_DECIDE = "manager.approval.decide"
P_MGR_FAILED_DELIVERY_DISPUTE_DECIDE = "manager.failed_delivery.dispute.decide"
P_MGR_MANUAL_HOLD_RELEASE = "manager.manual_hold.release"
P_MGR_SUPERVISOR_ESCALATION_DECIDE = "manager.supervisor_escalation.decide"
P_MGR_SHIFT_REPORT_REVIEW = "manager.shift_report.review"

# Supervisor — flag / escalate verbs.
P_SUP_DASHBOARD_VIEW = "supervisor.dashboard.view"
P_SUP_COMPLAINT_ESCALATE = "supervisor.complaint.escalate"
P_SUP_COD_RISK_FLAG = "supervisor.cod_risk.flag"
P_SUP_SHIFT_REPORT_SUBMIT = "supervisor.shift_report.submit"

# Audit — read + export only; never edit / delete.
P_AUDIT_LOG_VIEW = "audit.log.view"
P_AUDIT_LOG_EXPORT = "audit.log.export"


# ============================================================
#  NEW roles — wire into the seeder via ``EXTENSION_ROLES`` below.
# ============================================================
ROLE_INVENTORY_MANAGER = RoleSpec(
    name="inventory_manager",
    description=(
        "Stock truth owner. Approves stock adjustments, return-to-stock, "
        "damaged/lost marks, warehouse manual-available increases. Cannot "
        "touch money, payments, payouts, system settings, or delete "
        "history. Escalates high-value to Admin."
    ),
    permissions=(
        # Read
        P_INV_DASHBOARD_VIEW,
        P_INV_STOCK_VIEW,
        P_INV_RESERVED_VIEW,
        P_INV_MOVEMENT_VIEW,
        P_INV_WAREHOUSE_STOCK_VIEW,
        P_INV_LOW_STOCK_ALERT,
        # Write — Inventory truth
        P_INV_OUT_OF_STOCK_BLOCK,
        P_INV_SELLER_STOCK_MONITOR,
        P_INV_SELLER_STOCK_AUDIT_REQUEST,
        P_INV_STOCK_ADJUST_APPROVE,
        P_INV_STOCK_ADJUST_REJECT,
        P_INV_STOCK_DISCREPANCY_REVIEW,
        P_INV_RETURN_TO_STOCK_APPROVE,
        P_INV_RETURN_TO_STOCK_REJECT,
        P_INV_DAMAGED_LOST_MARK,
        # Warehouse-scoped approvals (Mother-QR overrides)
        P_WH_PICK_OVERRIDE_APPROVE,
        P_WH_QUARANTINE_APPROVE,
        P_WH_MANUAL_AVAILABLE_APPROVE,
        P_WH_SCAN_LOG_VIEW,
        # Audit
        P_INV_AUDIT_EXPORT,
        P_AUDIT_LOG_VIEW,
    ),
)

ROLE_OPERATIONS_MANAGER_LM = RoleSpec(
    name="operations_manager_lm",
    description=(
        "Last-mile / delivery exception owner. Approves operational "
        "exceptions, decides supervisor escalations, decides failed-"
        "delivery disputes, releases manual holds, reviews shift "
        "reports. Cannot touch money, change stock truth, confirm "
        "delivery, or start OUT_FOR_DELIVERY (Rider-only)."
    ),
    permissions=(
        # Approval queue
        P_MGR_APPROVAL_QUEUE_VIEW,
        P_MGR_APPROVAL_DECIDE,
        P_MGR_FAILED_DELIVERY_DISPUTE_DECIDE,
        P_MGR_MANUAL_HOLD_RELEASE,
        P_MGR_SUPERVISOR_ESCALATION_DECIDE,
        P_MGR_SHIFT_REPORT_REVIEW,
        # Visibility
        P_WH_SCAN_LOG_VIEW,
        P_AUDIT_LOG_VIEW,
    ),
)

ROLE_WAREHOUSE_RECEIVER = RoleSpec(
    name="warehouse_receiver",
    description=(
        "Inbound dock + receiving + Mother-QR generation + QC + shelf "
        "assignment. Cannot pack, dispatch, hand-over to riders, or "
        "approve disposition decisions."
    ),
    permissions=(
        P_WH_GATE_IN_CREATE,
        P_WH_MOTHER_QR_GENERATE,
        P_WH_MOTHER_QR_PRINT,
        P_WH_RECEIVING_CONFIRM,
        P_WH_QC_START,
        P_WH_QC_PASS,
        P_WH_QC_FAIL,
        P_WH_SHELF_ASSIGN,
        P_WH_RETURN_RECEIVE,
        P_WH_RETURN_QC,
        P_WH_QUARANTINE_REQUEST,
        P_WH_MANUAL_AVAILABLE_REQUEST,
        P_WH_SCAN_LOG_VIEW,
    ),
)

ROLE_WAREHOUSE_PACKER = RoleSpec(
    name="warehouse_packer",
    description=(
        "Pick + pack + parcel-QR generation + dispatch-ready scan + "
        "rider handover scan. No money, no stock-truth decisions, no "
        "delivery finality (Rider-only)."
    ),
    permissions=(
        P_WH_PICK_EXECUTE,
        P_WH_PICK_OVERRIDE_REQUEST,
        P_WH_PACK_EXECUTE,
        P_WH_PARCEL_QR_GENERATE,
        P_WH_DISPATCH_READY,
        P_WH_RIDER_HANDOVER,
        P_WH_DELIVERY_FAILED_REVIEW,
        P_WH_SCAN_LOG_VIEW,
    ),
)


EXTENSION_ROLES: tuple[RoleSpec, ...] = (
    ROLE_INVENTORY_MANAGER,
    ROLE_OPERATIONS_MANAGER_LM,
    ROLE_WAREHOUSE_RECEIVER,
    ROLE_WAREHOUSE_PACKER,
)


# ============================================================
#  Patches — extra perms to graft onto EXISTING roles in
#  ``permissions.py``. The seeder MUST union these with each role's
#  ``RoleSpec.permissions`` tuple before insert.
# ============================================================
#
# Order matters: union (deduped) appends only the perms not already
# in the original spec. Use the role's ``name`` (string) as the key
# so we never hold the frozen ``RoleSpec`` object directly.
ROLE_PERMISSION_PATCHES: dict[str, tuple[str, ...]] = {
    "finance_manager": (
        # All the new fine-grained finance perms — replaces the coarse
        # ``finance.*`` four-perm grant in the next migration window.
        P_FIN_DASHBOARD_VIEW, P_FIN_REPORTS_VIEW, P_FIN_PAYMENT_STATUS_VIEW,
        P_FIN_RECONCILE_GATEWAY, P_FIN_RECONCILE_COD,
        P_FIN_COD_SETTLE_VERIFY, P_FIN_COD_SETTLE_REJECT,
        P_FIN_COD_MISMATCH_RECONCILE,
        P_FIN_REFUND_VIEW, P_FIN_REFUND_APPROVE, P_FIN_REFUND_REJECT,
        P_FIN_REFUND_HOLD,
        P_FIN_COMPENSATION_APPROVE,
        P_FIN_DELIVERY_FEE_RECONCILE, P_FIN_FULFILLMENT_LOSS_REVIEW,
        P_FIN_DAMAGE_LOSS_IMPACT_APPROVE,
        P_FIN_RIDER_COD_LIABILITY_RECONCILE,
        P_FIN_SELLER_LEDGER_VIEW, P_FIN_SELLER_STATEMENT_RECONCILE,
        P_FIN_SELLER_PAYOUT_APPROVE, P_FIN_SELLER_PAYOUT_HOLD,
        P_FIN_SELLER_PAYOUT_RELEASE, P_FIN_SELLER_PAYOUT_HOLD_RELEASE,
        P_FIN_SELLER_CHARGEBACK_APPROVE,
        P_FIN_RIDER_PAYOUT_APPROVE, P_FIN_RIDER_PAYOUT_HOLD,
        P_FIN_RIDER_PAYOUT_RELEASE, P_FIN_RIDER_PAYOUT_VERIFY,
        P_FIN_WALLET_LEDGER_VIEW, P_FIN_WALLET_ADJUSTMENT_CREATE,
        P_FIN_WALLET_ADJUSTMENT_APPROVE,
        P_FIN_WALLET_MISMATCH_INVESTIGATE,
        P_FIN_PAYOUT_FREEZE,
        P_FIN_DISPUTE_INVESTIGATE, P_FIN_TRANSACTION_ESCALATE,
        P_FIN_DAILY_CLOSING_SUBMIT,
        P_FIN_REPORT_EXPORT, P_FIN_AUDIT_EXPORT, P_FIN_NOTE_ADD,
        P_AUDIT_LOG_VIEW,
    ),
    "supervisor": (
        P_SUP_DASHBOARD_VIEW,
        P_SUP_COMPLAINT_ESCALATE,
        P_SUP_COD_RISK_FLAG,
        P_SUP_SHIFT_REPORT_SUBMIT,
        P_AUDIT_LOG_VIEW,
        # Read-only finance visibility for monitoring (no write/approve).
        P_FIN_DASHBOARD_VIEW,
        P_FIN_REPORTS_VIEW,
    ),
    "admin": (
        # Manager-tier decisions — admin needs them as fallback.
        P_MGR_APPROVAL_QUEUE_VIEW,
        P_MGR_APPROVAL_DECIDE,
        P_MGR_FAILED_DELIVERY_DISPUTE_DECIDE,
        P_MGR_MANUAL_HOLD_RELEASE,
        P_MGR_SUPERVISOR_ESCALATION_DECIDE,
        P_MGR_SHIFT_REPORT_REVIEW,
        # Inventory + finance read for cross-team admin visibility.
        P_INV_DASHBOARD_VIEW,
        P_FIN_DASHBOARD_VIEW,
        P_AUDIT_LOG_VIEW,
        P_AUDIT_LOG_EXPORT,
    ),
    "rider_manager": (
        # Last-mile operations overlap — same approval surface.
        P_MGR_APPROVAL_QUEUE_VIEW,
        P_MGR_APPROVAL_DECIDE,
        P_MGR_FAILED_DELIVERY_DISPUTE_DECIDE,
        P_MGR_MANUAL_HOLD_RELEASE,
        P_MGR_SHIFT_REPORT_REVIEW,
        P_WH_RIDER_HANDOVER_HIGH_VALUE_APPROVE,
    ),
}
