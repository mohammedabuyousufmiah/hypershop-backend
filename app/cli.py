from __future__ import annotations

import asyncio
from typing import Annotated

import typer

from app.core.events.dispatcher import dispatch_once
from app.core.logging import configure_logging, get_logger

app = typer.Typer(help="Hypershop admin CLI", no_args_is_help=True)


@app.command("dispatch-outbox")
def dispatch_outbox(
    rounds: Annotated[int, typer.Option(min=1, max=1000)] = 1,
) -> None:
    """Dispatch one or more rounds of pending outbox messages.

    Useful in CI, manual triage, and one-off catch-up after a worker outage.
    """
    configure_logging()
    log = get_logger("hypershop.cli")

    async def _run() -> int:
        total = 0
        for _ in range(rounds):
            total += await dispatch_once()
        return total

    handled = asyncio.run(_run())
    log.info("dispatch_outbox_done", rounds=rounds, handled=handled)
    typer.echo(f"dispatched: {handled}")


@app.command("iam-bootstrap")
def iam_bootstrap() -> None:
    """Re-seed roles + permissions to match the canonical catalog.

    Idempotent: re-runs are safe. Insert-on-conflict-do-nothing for new
    permissions, replace-on-conflict for role descriptions, and a full
    rewrite of role_permissions per role so removing a permission from
    code removes it from the DB on the next bootstrap.
    """
    configure_logging()

    from sqlalchemy import text

    from app.core.db.session import get_sessionmaker
    from app.modules.iam.permissions import ALL_PERMISSIONS, ALL_ROLES

    async def _run() -> None:
        sm = get_sessionmaker()
        async with sm() as s, s.begin():
            for perm_name in (*ALL_PERMISSIONS, "*"):
                await s.execute(
                    text(
                        "INSERT INTO permissions (name) VALUES (:n) ON CONFLICT (name) DO NOTHING"
                    ),
                    {"n": perm_name},
                )
            for role_spec in ALL_ROLES:
                await s.execute(
                    text(
                        """
                        INSERT INTO roles (name, description, is_system)
                        VALUES (:n, :d, :s)
                        ON CONFLICT (name) DO UPDATE SET
                            description = EXCLUDED.description,
                            is_system = EXCLUDED.is_system
                        """
                    ),
                    {"n": role_spec.name, "d": role_spec.description, "s": role_spec.is_system},
                )
            for role_spec in ALL_ROLES:
                role_id = (
                    await s.execute(
                        text("SELECT id FROM roles WHERE name = :n"),
                        {"n": role_spec.name},
                    )
                ).scalar_one()
                await s.execute(
                    text("DELETE FROM role_permissions WHERE role_id = :r"),
                    {"r": role_id},
                )
                for perm_name in role_spec.permissions:
                    perm_id = (
                        await s.execute(
                            text("SELECT id FROM permissions WHERE name = :n"),
                            {"n": perm_name},
                        )
                    ).scalar_one()
                    await s.execute(
                        text(
                            "INSERT INTO role_permissions (role_id, permission_id) "
                            "VALUES (:r, :p) ON CONFLICT DO NOTHING"
                        ),
                        {"r": role_id, "p": perm_id},
                    )

    asyncio.run(_run())
    typer.echo("iam-bootstrap: roles + permissions synced")


@app.command("create-superuser")
def create_superuser(
    email: Annotated[str, typer.Option(help="email address")],
    password: Annotated[
        str, typer.Option(help="password (>= 12 chars)", prompt=True, hide_input=True)
    ],
    full_name: Annotated[str, typer.Option(help="display name")] = "Hypershop Admin",
) -> None:
    """Create or upgrade a user to the ``admin`` role and mark email verified.

    For initial deployment / break-glass account provisioning.
    """
    configure_logging()

    from sqlalchemy import text

    from app.core.db.session import get_sessionmaker
    from app.core.security.passwords import hash_password
    from app.core.time import utc_now

    if len(password) < 12:
        raise typer.BadParameter("password must be at least 12 characters")

    async def _run() -> None:
        sm = get_sessionmaker()
        async with sm() as s, s.begin():
            row = (
                await s.execute(
                    text("SELECT id FROM users WHERE email = :e"),
                    {"e": email.lower().strip()},
                )
            ).scalar_one_or_none()
            if row is None:
                user_id = (
                    await s.execute(
                        text(
                            """
                            INSERT INTO users (
                                email, full_name, password_hash, status, email_verified_at
                            )
                            VALUES (:e, :n, :p, 'active', :t)
                            RETURNING id
                            """
                        ),
                        {
                            "e": email.lower().strip(),
                            "n": full_name,
                            "p": hash_password(password),
                            "t": utc_now(),
                        },
                    )
                ).scalar_one()
            else:
                user_id = row
                await s.execute(
                    text(
                        """
                        UPDATE users
                        SET password_hash = :p,
                            status = 'active',
                            email_verified_at = COALESCE(email_verified_at, :t)
                        WHERE id = :id
                        """
                    ),
                    {"p": hash_password(password), "t": utc_now(), "id": user_id},
                )
            admin_role_id = (
                await s.execute(text("SELECT id FROM roles WHERE name = 'admin'"))
            ).scalar_one()
            await s.execute(
                text(
                    "INSERT INTO user_roles (user_id, role_id) VALUES (:u, :r) "
                    "ON CONFLICT DO NOTHING"
                ),
                {"u": user_id, "r": admin_role_id},
            )

    asyncio.run(_run())
    typer.echo(f"superuser ready: {email}")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
