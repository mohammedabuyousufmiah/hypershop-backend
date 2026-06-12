"""Export every module_settings + module_feature_flags row to JSON.

Usage::

    python -m scripts.export_module_config                 # → ./module_config_<ts>.json
    python -m scripts.export_module_config --out FILE      # → FILE
    python -m scripts.export_module_config --reveal-secrets # include plaintext secrets

Pair with ``scripts.import_module_config`` for env-promotion workflows
(dev → staging → prod). The JSON shape is the same one the
``POST /admin/modules/_import`` endpoint accepts, so a hand-rolled
script can also POST the file directly via curl::

    curl -X POST -H "Authorization: Bearer $T" -H "Content-Type: application/json" \\
      "$BASE/admin/modules/_import" -d @module_config_<ts>.json

Secrets default to redacted (`"[secret]"`). Pass ``--reveal-secrets``
to dump plaintext — file then contains live credentials, treat as
sensitive (chmod 600, gitignore, etc.).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone

from sqlalchemy import select

from app.core.db.session import get_sessionmaker
from app.modules.admin_config.models import ModuleFeatureFlag, ModuleSetting

_REDACTED = "[secret]"


async def run(out_path: str, reveal_secrets: bool) -> int:
    sm = get_sessionmaker()
    async with sm() as s:
        settings = (await s.execute(
            select(ModuleSetting).order_by(
                ModuleSetting.module_key, ModuleSetting.setting_key,
            )
        )).scalars().all()
        flags = (await s.execute(
            select(ModuleFeatureFlag).order_by(
                ModuleFeatureFlag.module_key, ModuleFeatureFlag.flag_key,
            )
        )).scalars().all()

    out = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "schema_version": 1,
        "settings": [
            {
                "module_key": s.module_key,
                "setting_key": s.setting_key,
                "value": _REDACTED if (s.is_secret and not reveal_secrets) else s.value,
                "value_type": s.value_type,
                "description": s.description,
                "is_secret": s.is_secret,
            }
            for s in settings
        ],
        "flags": [
            {
                "module_key": f.module_key,
                "flag_key": f.flag_key,
                "enabled": f.enabled,
                "rollout_percent": f.rollout_percent,
                "description": f.description,
            }
            for f in flags
        ],
        "counts": {"settings": len(settings), "flags": len(flags)},
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False, default=str)
    print(f"wrote {out_path}")
    print(f"  settings={len(settings)} flags={len(flags)} "
          f"reveal_secrets={reveal_secrets}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", default=None,
                   help="Output file. Defaults to ./module_config_<UTC ts>.json")
    p.add_argument("--reveal-secrets", action="store_true",
                   help="Include plaintext secret values (treat output as PII).")
    args = p.parse_args()
    out_path = args.out or (
        f"module_config_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    )
    return asyncio.run(run(out_path, args.reveal_secrets))


if __name__ == "__main__":
    sys.exit(main())
