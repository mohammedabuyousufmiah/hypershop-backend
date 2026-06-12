"""Audit action codes + canonical report codes."""

from __future__ import annotations

# ---------- audit actions ----------
ACTION_REPORT_RUN = "reporting.report.run"
ACTION_REPORT_EXPORT = "reporting.report.export"
ACTION_REPORT_DENIED = "reporting.report.denied"
ACTION_REPORT_DEFINITION_UPSERTED = "reporting.definition.upserted"
ACTION_REPORT_POLICY_UPDATED = "reporting.policy.updated"
ACTION_REPORT_SCHEDULE_CREATED = "reporting.schedule.created"
ACTION_REPORT_SCHEDULE_DELETED = "reporting.schedule.deleted"
ACTION_REPORT_FILE_DOWNLOADED = "reporting.file.downloaded"
ACTION_REPORT_FILE_EXPIRED = "reporting.file.expired"

# ---------- canonical report codes (registered at boot) ----------
# Keep in sync with builders/__init__.py and bootstrap.py.
REPORT_SALES_DAILY = "sales.daily"
REPORT_SALES_PAYMENT_METHOD = "sales.payment_method_split"
REPORT_STOCK_BUCKETS = "inventory.stock_buckets"
REPORT_STOCK_LOW = "inventory.low_stock"
REPORT_EXPIRY_BATCHES = "inventory.expiry_batches"
REPORT_DELIVERY_THROUGHPUT = "operations.delivery_throughput"
REPORT_COD_PER_RIDER = "operations.cod_per_rider"
REPORT_REFUND_PIPELINE = "finance.refund_pipeline"
REPORT_FINANCE_TRIAL_BALANCE = "finance.trial_balance"
REPORT_FINANCE_PROFIT_LOSS = "finance.profit_and_loss"
REPORT_FINANCE_DAILY_CLOSE = "finance.daily_close_history"

ALL_BUILTIN_REPORT_CODES: tuple[str, ...] = (
    REPORT_SALES_DAILY,
    REPORT_SALES_PAYMENT_METHOD,
    REPORT_STOCK_BUCKETS,
    REPORT_STOCK_LOW,
    REPORT_EXPIRY_BATCHES,
    REPORT_DELIVERY_THROUGHPUT,
    REPORT_COD_PER_RIDER,
    REPORT_REFUND_PIPELINE,
    REPORT_FINANCE_TRIAL_BALANCE,
    REPORT_FINANCE_PROFIT_LOSS,
    REPORT_FINANCE_DAILY_CLOSE,
)
