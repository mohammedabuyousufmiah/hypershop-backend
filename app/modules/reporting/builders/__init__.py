"""Report builders.

Importing this module registers every built-in report into the
process-local ``report_registry``. The api process imports this in
``main.py`` lifespan; the worker imports it in ``worker._startup``
so scheduled runs find their builder.

Each builder is a thin async callable that:
  1. Reads filters from a dict (with sensible defaults — never crash
     on missing keys; the API merges definition.default_filters_json
     before calling).
  2. Runs ONE or a handful of SQL queries via the existing repositories
     (no new query layer — reuse what dashboard/finance already wrote).
  3. Returns a list of plain-dict rows where every key matches a
     ``columns_json`` entry. Decimals/dates/UUIDs are kept native so
     the exporters can format them correctly per format.

Adding a new report = drop a builder file in this directory + register
in ``register_all()`` + add a row in ``bootstrap.py``.
"""

from __future__ import annotations

from app.modules.reporting.builders import (
    cod_per_rider,
    delivery_throughput,
    expiry_batches,
    finance_daily_close,
    finance_profit_loss,
    finance_trial_balance,
    low_stock,
    refund_pipeline,
    sales_daily,
    sales_payment_method,
    stock_buckets,
)
from app.modules.reporting.codes import (
    REPORT_COD_PER_RIDER,
    REPORT_DELIVERY_THROUGHPUT,
    REPORT_EXPIRY_BATCHES,
    REPORT_FINANCE_DAILY_CLOSE,
    REPORT_FINANCE_PROFIT_LOSS,
    REPORT_FINANCE_TRIAL_BALANCE,
    REPORT_REFUND_PIPELINE,
    REPORT_SALES_DAILY,
    REPORT_SALES_PAYMENT_METHOD,
    REPORT_STOCK_BUCKETS,
    REPORT_STOCK_LOW,
)
from app.modules.reporting.registry import RegisteredBuilder, report_registry
from app.modules.reporting.state import ExportFormat


def _format_set(*formats: str) -> tuple[str, ...]:
    return formats


def register_all() -> None:
    """Register every built-in report. Idempotent: re-registration of
    the same code raises, so callers must guard against double-import.
    """
    if report_registry.get(REPORT_SALES_DAILY) is not None:
        # Already registered (e.g. worker + api in same process).
        return

    report_registry.register(RegisteredBuilder(
        code=REPORT_SALES_DAILY,
        builder=sales_daily.build,
        default_columns=sales_daily.COLUMNS,
        default_category="sales",
        default_name="Sales — Daily revenue trend",
        default_allowed_roles=("admin", "super_admin", "finance"),
        default_export_formats=_format_set(
            ExportFormat.CSV, ExportFormat.XLSX, ExportFormat.PDF,
        ),
    ))
    report_registry.register(RegisteredBuilder(
        code=REPORT_SALES_PAYMENT_METHOD,
        builder=sales_payment_method.build,
        default_columns=sales_payment_method.COLUMNS,
        default_category="sales",
        default_name="Sales — Payment method split",
        default_allowed_roles=("admin", "super_admin", "finance"),
        default_export_formats=_format_set(
            ExportFormat.CSV, ExportFormat.XLSX,
        ),
    ))
    report_registry.register(RegisteredBuilder(
        code=REPORT_STOCK_BUCKETS,
        builder=stock_buckets.build,
        default_columns=stock_buckets.COLUMNS,
        default_category="inventory",
        default_name="Inventory — Stock by bucket",
        default_allowed_roles=("admin", "super_admin", "ops", "packer"),
        default_export_formats=_format_set(
            ExportFormat.CSV, ExportFormat.XLSX,
        ),
    ))
    report_registry.register(RegisteredBuilder(
        code=REPORT_STOCK_LOW,
        builder=low_stock.build,
        default_columns=low_stock.COLUMNS,
        default_category="inventory",
        default_name="Inventory — Low-stock variants",
        default_allowed_roles=("admin", "super_admin", "ops", "packer"),
        default_export_formats=_format_set(
            ExportFormat.CSV, ExportFormat.XLSX,
        ),
    ))
    report_registry.register(RegisteredBuilder(
        code=REPORT_EXPIRY_BATCHES,
        builder=expiry_batches.build,
        default_columns=expiry_batches.COLUMNS,
        default_category="inventory",
        default_name="Inventory — Batches expired or near expiry",
        default_allowed_roles=("admin", "super_admin", "ops", "compliance"),
        default_export_formats=_format_set(
            ExportFormat.CSV, ExportFormat.XLSX, ExportFormat.PDF,
        ),
    ))
    report_registry.register(RegisteredBuilder(
        code=REPORT_DELIVERY_THROUGHPUT,
        builder=delivery_throughput.build,
        default_columns=delivery_throughput.COLUMNS,
        default_category="operations",
        default_name="Operations — Delivery throughput",
        default_allowed_roles=("admin", "super_admin", "ops"),
        default_export_formats=_format_set(
            ExportFormat.CSV, ExportFormat.XLSX,
        ),
    ))
    report_registry.register(RegisteredBuilder(
        code=REPORT_COD_PER_RIDER,
        builder=cod_per_rider.build,
        default_columns=cod_per_rider.COLUMNS,
        default_category="operations",
        default_name="Operations — COD outstanding per rider",
        default_allowed_roles=("admin", "super_admin", "ops", "finance"),
        default_export_formats=_format_set(
            ExportFormat.CSV, ExportFormat.XLSX,
        ),
    ))
    report_registry.register(RegisteredBuilder(
        code=REPORT_REFUND_PIPELINE,
        builder=refund_pipeline.build,
        default_columns=refund_pipeline.COLUMNS,
        default_category="finance",
        default_name="Finance — Refund pipeline",
        default_allowed_roles=("admin", "super_admin", "finance"),
        default_export_formats=_format_set(
            ExportFormat.CSV, ExportFormat.XLSX,
        ),
    ))
    report_registry.register(RegisteredBuilder(
        code=REPORT_FINANCE_TRIAL_BALANCE,
        builder=finance_trial_balance.build,
        default_columns=finance_trial_balance.COLUMNS,
        default_category="finance",
        default_name="Finance — Trial balance",
        default_allowed_roles=("admin", "super_admin", "finance"),
        default_export_formats=_format_set(
            ExportFormat.CSV, ExportFormat.XLSX, ExportFormat.PDF,
        ),
    ))
    report_registry.register(RegisteredBuilder(
        code=REPORT_FINANCE_PROFIT_LOSS,
        builder=finance_profit_loss.build,
        default_columns=finance_profit_loss.COLUMNS,
        default_category="finance",
        default_name="Finance — Profit & Loss",
        default_allowed_roles=("admin", "super_admin", "finance"),
        default_export_formats=_format_set(
            ExportFormat.CSV, ExportFormat.XLSX, ExportFormat.PDF,
        ),
    ))
    report_registry.register(RegisteredBuilder(
        code=REPORT_FINANCE_DAILY_CLOSE,
        builder=finance_daily_close.build,
        default_columns=finance_daily_close.COLUMNS,
        default_category="finance",
        default_name="Finance — Daily-close history",
        default_allowed_roles=("admin", "super_admin", "finance"),
        default_export_formats=_format_set(
            ExportFormat.CSV, ExportFormat.XLSX,
        ),
    ))
