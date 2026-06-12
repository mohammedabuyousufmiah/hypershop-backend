"""Promote existing ``admin`` users to ``super_admin`` for the
2026-05-16 17-role expansion.

**Why this script exists:** before the expansion, ``admin`` held the
sole ``("*",)`` wildcard, so admin users could delete other users,
assign roles, revoke sessions, ingest the RAG corpus, export funnel
PII to ad networks, and access the feature-flags console. After the
expansion, ``admin`` is scoped (39 explicit perms) and ``super_admin``
holds the sole wildcard. Existing admin users who relied on those
removed capabilities will hit 403s without remediation.

**What this script does:** lists every user currently holding the
``admin`` role and (optionally) attaches the ``super_admin`` role to
each. Supports three modes:

* ``--list-only`` — read-only audit (default if no --emails / --all).
* ``--emails a@x.com b@y.com`` — promote a specific subset by email.
* ``--all`` — promote every current admin user. Use with care.

**Idempotent.** Re-runs are safe (``ON CONFLICT DO NOTHING`` on the
user_roles insert). The script does NOT remove the admin role from the
user — they keep both. Removing admin would cut their access to all
the modules admin sees, which is rarely what you want.

**Pre-req:** ``hypershop-cli iam-bootstrap`` must have run at least once
so the ``super_admin`` role exists.

Usage::

    python -m scripts.promote_admin_to_super_admin --list-only
    python -m scripts.promote_admin_to_super_admin --emails ceo@hypershop.com cto@hypershop.com
    python -m scripts.promote_admin_to_super_admin --all --confirm

Exit codes: 0 on success, 1 on argument error, 2 if super_admin role
missing from DB (run iam-bootstrap first).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass

from sqlalchemy import text

from app.core.db.session import get_sessionmaker


@dataclass(frozen=True, slots=True)
class AdminRow:
    user_id: str
    email: str
    full_name: str
    is_already_super_admin: bool


async def _list_admins(s) -> list[AdminRow]:
    """Return every user currently holding the ``admin`` role + whether
    they also hold ``super_admin``."""
    rows = (
        await s.execute(
            text(
                """
                SELECT
                    u.id::text                                  AS user_id,
                    u.email                                     AS email,
                    u.full_name                                 AS full_name,
                    EXISTS (
                        SELECT 1 FROM user_roles ur2
                        JOIN roles r2 ON r2.id = ur2.role_id
                        WHERE ur2.user_id = u.id AND r2.name = 'super_admin'
                    )                                            AS has_super_admin
                FROM users u
                JOIN user_roles ur ON ur.user_id = u.id
                JOIN roles r       ON r.id = ur.role_id
                WHERE r.name = 'admin'
                ORDER BY u.email
                """
            )
        )
    ).all()
    return [
        AdminRow(
            user_id=row.user_id,
            email=row.email,
            full_name=row.full_name,
            is_already_super_admin=bool(row.has_super_admin),
        )
        for row in rows
    ]


async def _ensure_super_admin_role_exists(s) -> str:
    """Look up super_admin role id; raise if missing (instructs the
    operator to run iam-bootstrap)."""
    role_id = (
        await s.execute(text("SELECT id::text FROM roles WHERE name = 'super_admin'"))
    ).scalar_one_or_none()
    if role_id is None:
        raise RuntimeError(
            "super_admin role does not exist in DB. "
            "Run `hypershop-cli iam-bootstrap` first to seed the catalog."
        )
    return role_id


async def _promote(s, user_id: str, super_admin_role_id: str) -> None:
    """Attach super_admin role to user. Idempotent."""
    await s.execute(
        text(
            "INSERT INTO user_roles (user_id, role_id) VALUES (:u, :r) "
            "ON CONFLICT DO NOTHING"
        ),
        {"u": user_id, "r": super_admin_role_id},
    )


async def _run(args: argparse.Namespace) -> int:
    sm = get_sessionmaker()
    async with sm() as s, s.begin():
        admins = await _list_admins(s)

        if not admins:
            print("No users hold the `admin` role. Nothing to do.")
            return 0

        # Always print the audit table first so operator can verify the
        # universe of admins before promoting.
        width = max(len(a.email) for a in admins)
        print(f"\nFound {len(admins)} user(s) with the `admin` role:\n")
        for a in admins:
            tag = " [already super_admin]" if a.is_already_super_admin else ""
            print(f"  {a.email:<{width}}  {a.full_name}{tag}")
        print()

        if args.list_only:
            print("--list-only: no changes made.")
            return 0

        # Resolve the target subset of admin rows.
        if args.all:
            if not args.confirm:
                print(
                    "--all is dangerous: every current admin gets the wildcard. "
                    "Re-run with --all --confirm if you actually mean it."
                )
                return 1
            targets = admins
        elif args.emails:
            wanted = {e.lower().strip() for e in args.emails}
            targets = [a for a in admins if a.email.lower() in wanted]
            missing = wanted - {a.email.lower() for a in admins}
            if missing:
                print(
                    f"WARNING: these emails were requested but are not admin: {sorted(missing)}"
                )
            if not targets:
                print("No matching admin users to promote.")
                return 1
        else:
            print(
                "Provide --list-only, --emails <addr ...>, or --all --confirm. "
                "Nothing to do."
            )
            return 1

        super_admin_role_id = await _ensure_super_admin_role_exists(s)

        promoted: list[AdminRow] = []
        skipped: list[AdminRow] = []
        for a in targets:
            if a.is_already_super_admin:
                skipped.append(a)
                continue
            await _promote(s, a.user_id, super_admin_role_id)
            promoted.append(a)

        print()
        if promoted:
            print(f"Promoted {len(promoted)} user(s) to super_admin:")
            for a in promoted:
                print(f"  + {a.email}")
        if skipped:
            print(f"Skipped {len(skipped)} (already super_admin):")
            for a in skipped:
                print(f"  = {a.email}")
        if not promoted and not skipped:
            print("Nothing changed.")
        return 0


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Promote existing admin users to super_admin after the "
            "2026-05-16 17-role expansion."
        )
    )
    p.add_argument(
        "--list-only",
        action="store_true",
        help="Audit only: list current admin users + their super_admin status.",
    )
    p.add_argument(
        "--emails",
        nargs="+",
        metavar="EMAIL",
        help="Specific admin emails to promote (case-insensitive).",
    )
    p.add_argument(
        "--all",
        action="store_true",
        help="Promote every current admin. Requires --confirm.",
    )
    p.add_argument(
        "--confirm",
        action="store_true",
        help="Required acknowledgement when using --all.",
    )
    return p.parse_args()


if __name__ == "__main__":
    sys.exit(asyncio.run(_run(_parse_args())))
