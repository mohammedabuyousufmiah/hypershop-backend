from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# Make `app` importable when running via `alembic` from project root.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.core.db.base import Base  # noqa: E402
from app.core.db.registry import import_all_models  # noqa: E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

import_all_models()
target_metadata = Base.metadata


def _sync_database_url() -> str:
    explicit = os.environ.get("DATABASE_SYNC_URL")
    if explicit:
        return explicit
    # Managed platforms (Render/Railway/Heroku) inject only DATABASE_URL,
    # often a driverless DSN. Derive the +psycopg2 sync URL Alembic needs —
    # same normalisation as app.core.config.
    raw = os.environ.get("DATABASE_URL", "")
    if not raw:
        raise RuntimeError(
            "Neither DATABASE_SYNC_URL nor DATABASE_URL is set. Alembic needs a "
            "postgresql+psycopg2 sync URL (or a DATABASE_URL to derive it from).",
        )
    for prefix in ("postgres://", "postgresql+asyncpg://"):
        if raw.startswith(prefix):
            raw = "postgresql://" + raw[len(prefix):]
            break
    if raw.startswith("postgresql://"):
        return "postgresql+psycopg2://" + raw[len("postgresql://"):]
    return raw.replace("+asyncpg", "+psycopg2", 1)


def run_migrations_offline() -> None:
    context.configure(
        url=_sync_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
        include_schemas=False,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    cfg_section = config.get_section(config.config_ini_section, {}) or {}
    cfg_section["sqlalchemy.url"] = _sync_database_url()
    connectable = engine_from_config(
        cfg_section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        future=True,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
            include_schemas=False,
            transaction_per_migration=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
