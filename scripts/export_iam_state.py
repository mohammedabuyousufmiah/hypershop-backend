"""Export IAM state to JSON for backup / seed-replay.

Dumps the live database state of the 5 IAM tables into a single JSON
file. Two modes:

    python -m scripts.export_iam_state              # → ./iam_export_<ts>.json
    python -m scripts.export_iam_state --out FILE   # → FILE

Output shape::

    {
      "exported_at": "2026-05-16T...",
      "table_counts": {"users": 17, "roles": 17, ...},
      "users":            [{id, email, full_name, status, ...}],
      "roles":            [{id, name, description, is_system,
                             permissions: ["perm.name", ...]}],
      "permissions":      [{id, name, description}],
      "user_roles":       [{user_email, role_name}],
      "audit_log_tail":   [last 200 audit entries],
    }

Passwords are NEVER exported. ``password_hash`` is omitted from the
users dump so the file is safe to commit to ops-secrets vaults but
NOT usable to log in as the exported users. To re-seed credentials,
re-run ``scripts.seed_dev_logins`` after restoring the schema.

The file is plain JSON — no encryption. Treat it as PII-bearing
(contains emails + IP-source audit records) and store accordingly.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone

from sqlalchemy import text

from app.core.db.session import get_sessionmaker


async def _fetch(s, sql: str, params: dict | None = None) -> list[dict]:
    rows = (await s.execute(text(sql), params or {})).mappings().all()
    return [dict(r) for r in rows]


def _default(obj):
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    return str(obj)


async def run(out_path: str) -> int:
    sm = get_sessionmaker()
    async with sm() as s:
        users = await _fetch(s,
            "SELECT id::text, email::text, full_name, status, "
            "       email_verified_at, phone, phone_verified_at, "
            "       last_login_at, failed_login_count, locked_until, "
            "       created_at, updated_at "
            "FROM users ORDER BY created_at"
        )
        roles_raw = await _fetch(s,
            "SELECT id::text, name, description, is_system FROM roles ORDER BY name"
        )
        # Inline each role's permission list.
        for r in roles_raw:
            perms = (await s.execute(
                text(
                    "SELECT p.name FROM permissions p "
                    "JOIN role_permissions rp ON rp.permission_id = p.id "
                    "WHERE rp.role_id = :rid ORDER BY p.name"
                ),
                {"rid": r["id"]},
            )).all()
            r["permissions"] = [p[0] for p in perms]

        permissions = await _fetch(s,
            "SELECT id::text, name, description FROM permissions ORDER BY name"
        )
        user_roles = await _fetch(s,
            "SELECT u.email::text AS user_email, r.name AS role_name "
            "FROM user_roles ur "
            "JOIN users u ON u.id = ur.user_id "
            "JOIN roles r ON r.id = ur.role_id "
            "ORDER BY u.email, r.name"
        )
        audit_tail = await _fetch(s,
            "SELECT id::text, occurred_at, actor_id::text, actor_kind, "
            "       action, resource_type, resource_id::text, outcome, "
            "       request_id::text, ip_address, metadata "
            "FROM audit_logs ORDER BY occurred_at DESC LIMIT 200"
        )

        out = {
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "table_counts": {
                "users": len(users),
                "roles": len(roles_raw),
                "permissions": len(permissions),
                "user_roles": len(user_roles),
                "audit_log_tail": len(audit_tail),
            },
            "users": users,
            "roles": roles_raw,
            "permissions": permissions,
            "user_roles": user_roles,
            "audit_log_tail": audit_tail,
        }

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, default=_default, ensure_ascii=False, indent=2)
    print(f"wrote {out_path}")
    print(f"  users={len(users)} roles={len(roles_raw)} "
          f"perms={len(permissions)} user_roles={len(user_roles)} "
          f"audit_tail={len(audit_tail)}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--out",
        default=None,
        help="Output file. Defaults to ./iam_export_<UTC timestamp>.json",
    )
    args = p.parse_args()
    out_path = args.out or (
        f"iam_export_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    )
    return asyncio.run(run(out_path))


if __name__ == "__main__":
    sys.exit(main())
