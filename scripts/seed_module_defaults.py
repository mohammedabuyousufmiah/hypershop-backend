"""Seed sensible defaults for module_settings + module_feature_flags.

Idempotent — uses Postgres INSERT … ON CONFLICT DO NOTHING so re-runs
never overwrite operator-tuned values. To force an update, edit the
row via PUT /admin/modules/{key}/settings/{k} (or flags/{k}).

Run:
    python -m scripts.seed_module_defaults
"""
from __future__ import annotations

import asyncio
import sys

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.db.session import get_sessionmaker
from app.modules.admin_config.models import ModuleFeatureFlag, ModuleSetting


SETTINGS: tuple[tuple[str, str, object, str, str | None, bool], ...] = (
    # (module_key, setting_key, value, value_type, description, is_secret)
    ("orders", "max_page_size", 200, "number",
     "Hard cap on /admin/orders pagination size.", False),
    ("orders", "default_status_filter", "all", "string",
     "Default status filter when admin opens the orders list.", False),
    ("payments", "refund_window_days", 30, "number",
     "Days after capture during which refunds are allowed.", False),
    ("payments", "gateway_drift_tolerance_minor", 100, "number",
     "Drift threshold (minor units) before flagging reconciliation lines.", False),
    ("voice-calls", "ring_timeout_seconds", 30, "number",
     "Time before a ringing call auto-transitions to missed.", False),
    ("voice-calls", "softphone_enabled", True, "boolean",
     "Master switch for the in-browser softphone widget.", False),
    ("audit-log", "default_page_size", 100, "number",
     "Default page size on /admin/audit-log.", False),
    ("audit-log", "retention_days", 365, "number",
     "Days to retain audit_logs rows before archival (no enforcement job yet).", False),
    ("rider-routing", "max_stops_per_run", 25, "number",
     "Cap on stops in a single rider run sheet.", False),
    ("inventory", "low_stock_threshold", 10, "number",
     "Global low-stock warning threshold (overridable per SKU).", False),
    ("dashboard", "refresh_interval_seconds", 30, "number",
     "Auto-refresh interval for /admin/dashboard widgets. 0 = manual only.", False),
)

FLAGS: tuple[tuple[str, str, bool, int, str | None], ...] = (
    # (module_key, flag_key, enabled, rollout_percent, description)
    ("orders", "auto_refund_enabled", False, 0,
     "Auto-issue refunds on cancellation (instead of accruing to wallet)."),
    ("orders", "show_seller_column", True, 100,
     "Show the seller column on the admin orders list."),
    ("voice-calls", "show_dispatcher_widget", True, 100,
     "Render the dispatch console pane on /admin/voice-calls."),
    ("voice-calls", "auto_assign_ringing", False, 0,
     "Auto-route incoming calls to the agent with the fewest active calls."),
    ("payments", "show_reconcile_button", True, 100,
     "Show the 'Run reconciliation' button on /admin/payments."),
    ("audit-log", "show_metadata_column", False, 100,
     "Render the audit_log.metadata JSON column inline (verbose)."),
    ("reporting", "enable_csv_export", True, 100,
     "Allow CSV export of report tables."),
    ("loyalty", "tier_expiry_enabled", False, 0,
     "Enforce tier expiry based on rolling 12-month spend."),
)


async def run() -> int:
    sm = get_sessionmaker()
    seeded_s = seeded_f = 0
    async with sm() as s, s.begin():
        for module_key, setting_key, value, value_type, description, is_secret in SETTINGS:
            stmt = (
                pg_insert(ModuleSetting)
                .values(
                    module_key=module_key,
                    setting_key=setting_key,
                    value=value,
                    value_type=value_type,
                    description=description,
                    is_secret=is_secret,
                )
                .on_conflict_do_nothing(index_elements=["module_key", "setting_key"])
            )
            r = await s.execute(stmt)
            if r.rowcount:
                seeded_s += 1
        for module_key, flag_key, enabled, rollout, description in FLAGS:
            stmt = (
                pg_insert(ModuleFeatureFlag)
                .values(
                    module_key=module_key,
                    flag_key=flag_key,
                    enabled=enabled,
                    rollout_percent=rollout,
                    description=description,
                )
                .on_conflict_do_nothing(index_elements=["module_key", "flag_key"])
            )
            r = await s.execute(stmt)
            if r.rowcount:
                seeded_f += 1
    print(f"seeded settings: {seeded_s} new (of {len(SETTINGS)} total)")
    print(f"seeded flags:    {seeded_f} new (of {len(FLAGS)} total)")
    print("re-run is idempotent (ON CONFLICT DO NOTHING).")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
