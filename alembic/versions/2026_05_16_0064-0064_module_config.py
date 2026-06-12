"""0064 module_config — per-module runtime settings + feature flags.

Two tables that layer runtime configuration on top of the declarative
``app/core/registry/admin_modules.py`` catalog:

  module_settings        — typed key-value store scoped to a module
                           (max page size, default currency, rate
                           limit thresholds, etc.). Values stored as
                           JSONB so all primitive types + lists +
                           objects work uniformly. ``is_secret`` flag
                           hides the value from list endpoints.

  module_feature_flags   — boolean toggles with optional gradual rollout
                           (0-100 %). Cheaper than redeploying when a
                           module needs a kill-switch or A/B gate.

Module identity:
  ``module_key`` is a free-form VARCHAR(64) — NOT an FK to anything,
  because the registry lives in code (not the DB). The convention is
  to match ``AdminModule.code``; if no matching code exists the row
  still works (forward-compat for modules that aren't in the catalog
  yet, e.g. an experimental backend feature).

Audit + RBAC:
  All writes funnel through ``app/modules/iam/api/users.py`` companion
  endpoints which call ``record_audit`` — that's how `(updated_by,
  updated_at)` get populated. The endpoints gate on ``iam.role.read``
  for reads and a new perm ``module.config.write`` for mutations
  (added 2026-05-16 to permissions catalog).

Idempotency: ``(module_key, setting_key)`` and ``(module_key, flag_key)``
are uniquely-indexed, so PUT becomes an INSERT … ON CONFLICT UPDATE
without race conditions.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID


revision: str = "0064_module_config"
down_revision: str | Sequence[str] | None = "0063_audit_log_rename"
branch_labels: Sequence[str] | str | None = None
depends_on: Sequence[str] | str | None = None


def upgrade() -> None:
    # ─── module_settings ─────────────────────────────────────────
    op.create_table(
        "module_settings",
        sa.Column(
            "id", PgUUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("module_key", sa.String(64), nullable=False),
        sa.Column("setting_key", sa.String(96), nullable=False),
        sa.Column("value", JSONB, nullable=False,
                  server_default=sa.text("'null'::jsonb")),
        sa.Column("value_type", sa.String(16), nullable=False,
                  server_default="json"),
        sa.Column("description", sa.String(512), nullable=True),
        sa.Column("is_secret", sa.Boolean, nullable=False,
                  server_default=sa.text("false")),
        sa.Column(
            "updated_by", PgUUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.CheckConstraint(
            "value_type IN ('string','number','boolean','json')",
            name="module_settings_value_type_enum",
        ),
        sa.UniqueConstraint(
            "module_key", "setting_key",
            name="uq_module_settings_module_setting",
        ),
    )
    op.create_index(
        "ix_module_settings_module_key",
        "module_settings", ["module_key"],
    )

    # ─── module_feature_flags ────────────────────────────────────
    op.create_table(
        "module_feature_flags",
        sa.Column(
            "id", PgUUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("module_key", sa.String(64), nullable=False),
        sa.Column("flag_key", sa.String(96), nullable=False),
        sa.Column("enabled", sa.Boolean, nullable=False,
                  server_default=sa.text("false")),
        sa.Column("rollout_percent", sa.Integer, nullable=False,
                  server_default=sa.text("100")),
        sa.Column("description", sa.String(512), nullable=True),
        sa.Column(
            "updated_by", PgUUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.CheckConstraint(
            "rollout_percent >= 0 AND rollout_percent <= 100",
            name="module_feature_flags_rollout_range",
        ),
        sa.UniqueConstraint(
            "module_key", "flag_key",
            name="uq_module_feature_flags_module_flag",
        ),
    )
    op.create_index(
        "ix_module_feature_flags_module_key",
        "module_feature_flags", ["module_key"],
    )


def downgrade() -> None:
    op.drop_index("ix_module_feature_flags_module_key",
                  table_name="module_feature_flags")
    op.drop_table("module_feature_flags")
    op.drop_index("ix_module_settings_module_key",
                  table_name="module_settings")
    op.drop_table("module_settings")
