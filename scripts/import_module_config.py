"""Bulk-import module_settings + module_feature_flags from a JSON file.

Usage::

    python -m scripts.import_module_config FILE
    python -m scripts.import_module_config FILE --dry-run

Accepts the shape produced by ``scripts.export_module_config`` (and
the ``POST /admin/modules/_import`` endpoint). Each row is upserted
via INSERT … ON CONFLICT UPDATE; missing rows are NOT deleted (use
the explicit DELETE endpoints for that).

Refuses to import `value='[secret]'` literals on `is_secret=true` rows —
catches the common foot-gun of re-importing a redacted export. Use
``--reveal-secrets`` when exporting from the source env if you need
secrets to flow.

Does NOT enqueue outbox events (offline path). For online imports
with SSE invalidation, hit the HTTP endpoint.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys

from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.db.session import get_sessionmaker
from app.modules.admin_config.models import ModuleFeatureFlag, ModuleSetting

_REDACTED = "[secret]"


async def run(file_path: str, dry_run: bool) -> int:
    with open(file_path, encoding="utf-8") as f:
        data = json.load(f)
    settings = data.get("settings") or []
    flags = data.get("flags") or []
    if not settings and not flags:
        print("file contains no settings + no flags; nothing to do")
        return 0
    if dry_run:
        print(f"DRY RUN — would upsert:")
        print(f"  {len(settings)} settings, {len(flags)} flags")
        for s in settings[:5]:
            print(f"    setting  {s['module_key']}.{s['setting_key']} = {s['value']!r}")
        for f in flags[:5]:
            print(f"    flag     {f['module_key']}.{f['flag_key']} = "
                  f"enabled={f['enabled']} rollout={f.get('rollout_percent', 100)}")
        return 0

    sm = get_sessionmaker()
    skipped_secret_placeholders = 0
    s_in = f_in = 0
    async with sm() as s, s.begin():
        for spec in settings:
            if spec.get("is_secret") and spec.get("value") == _REDACTED:
                skipped_secret_placeholders += 1
                continue
            stmt = (
                pg_insert(ModuleSetting)
                .values(
                    module_key=spec["module_key"],
                    setting_key=spec["setting_key"],
                    value=spec["value"],
                    value_type=spec.get("value_type", "json"),
                    description=spec.get("description"),
                    is_secret=spec.get("is_secret", False),
                )
                .on_conflict_do_update(
                    index_elements=["module_key", "setting_key"],
                    set_=dict(
                        value=spec["value"],
                        value_type=spec.get("value_type", "json"),
                        description=spec.get("description"),
                        is_secret=spec.get("is_secret", False),
                    ),
                )
            )
            await s.execute(stmt)
            s_in += 1
        for spec in flags:
            stmt = (
                pg_insert(ModuleFeatureFlag)
                .values(
                    module_key=spec["module_key"],
                    flag_key=spec["flag_key"],
                    enabled=spec["enabled"],
                    rollout_percent=spec.get("rollout_percent", 100),
                    description=spec.get("description"),
                )
                .on_conflict_do_update(
                    index_elements=["module_key", "flag_key"],
                    set_=dict(
                        enabled=spec["enabled"],
                        rollout_percent=spec.get("rollout_percent", 100),
                        description=spec.get("description"),
                    ),
                )
            )
            await s.execute(stmt)
            f_in += 1
    print(f"imported {s_in} settings, {f_in} flags")
    if skipped_secret_placeholders:
        print(f"  skipped {skipped_secret_placeholders} redacted secret rows "
              f"(re-export with --reveal-secrets if you need them)")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("file", help="JSON file to import")
    p.add_argument("--dry-run", action="store_true",
                   help="Parse + summarise without writing.")
    args = p.parse_args()
    return asyncio.run(run(args.file, args.dry_run))


if __name__ == "__main__":
    sys.exit(main())
