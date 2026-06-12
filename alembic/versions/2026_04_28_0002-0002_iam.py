"""IAM: users, roles, permissions, sessions, OTP, password-reset tokens

Revision ID: 0002_iam
Revises: 0001_init_audit_outbox_idem
Create Date: 2026-04-28

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_iam"
down_revision: str | Sequence[str] | None = "0001_init_audit_outbox_idem"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute('CREATE EXTENSION IF NOT EXISTS "citext";')

    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("email", postgresql.CITEXT(), nullable=False),
        sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("phone", sa.String(32), nullable=True),
        sa.Column("phone_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("full_name", sa.String(120), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default="pending_verify",
        ),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_login_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.CheckConstraint(
            "status IN ('pending_verify','active','suspended','deleted')",
            name="ck_users_status_enum",
        ),
        sa.UniqueConstraint("email", name="uq_users_email"),
        sa.UniqueConstraint("phone", name="uq_users_phone"),
    )
    op.create_index("ix_users_status", "users", ["status"])

    op.create_table(
        "roles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(48), nullable=False),
        sa.Column("description", sa.String(255), nullable=True),
        sa.Column("is_system", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.UniqueConstraint("name", name="uq_roles_name"),
    )

    op.create_table(
        "permissions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(96), nullable=False),
        sa.Column("description", sa.String(255), nullable=True),
        sa.UniqueConstraint("name", name="uq_permissions_name"),
    )

    op.create_table(
        "role_permissions",
        sa.Column(
            "role_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("roles.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "permission_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("permissions.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )

    op.create_table(
        "user_roles",
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "role_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("roles.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column(
            "assigned_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.Column(
            "assigned_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )

    op.create_table(
        "sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("current_refresh_jti", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("prev_refresh_jti", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("user_agent", sa.String(512), nullable=True),
        sa.Column("ip_address", postgresql.INET(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.Column(
            "last_used_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_reason", sa.String(64), nullable=True),
    )
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])
    op.create_index("ix_sessions_active", "sessions", ["user_id", "revoked_at"])
    op.create_index("ix_sessions_current_refresh_jti", "sessions", ["current_refresh_jti"])

    op.create_table(
        "otp_codes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("email", postgresql.CITEXT(), nullable=True),
        sa.Column("phone", sa.String(32), nullable=True),
        sa.Column("purpose", sa.String(32), nullable=False),
        sa.Column("code_hash", sa.String(255), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.CheckConstraint(
            "purpose IN ('email_verify','login','password_reset')",
            name="ck_otp_codes_purpose_enum",
        ),
        sa.CheckConstraint(
            "user_id IS NOT NULL OR email IS NOT NULL OR phone IS NOT NULL",
            name="ck_otp_codes_otp_target_present",
        ),
    )
    op.create_index("ix_otp_codes_email_purpose", "otp_codes", ["email", "purpose"])
    op.create_index("ix_otp_codes_user_purpose", "otp_codes", ["user_id", "purpose"])
    op.create_index("ix_otp_codes_expires_at", "otp_codes", ["expires_at"])

    op.create_table(
        "password_reset_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.LargeBinary(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("requested_ip", postgresql.INET(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("(now() AT TIME ZONE 'UTC')"),
        ),
        sa.UniqueConstraint("token_hash", name="uq_password_reset_tokens_token_hash"),
    )
    op.create_index("ix_password_reset_tokens_user_id", "password_reset_tokens", ["user_id"])
    op.create_index(
        "ix_password_reset_tokens_expires_at", "password_reset_tokens", ["expires_at"],
    )

    # ---------- seed roles + permissions ----------
    # We seed inline so a fresh `alembic upgrade head` produces a working DB
    # without needing a separate "seed" command. The CLI `iam-bootstrap`
    # re-runs the same seed idempotently for upgrades.
    from app.modules.iam.permissions import ALL_PERMISSIONS, ALL_ROLES

    bind = op.get_bind()
    perms_table = sa.table(
        "permissions",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("name", sa.String),
        sa.column("description", sa.String),
    )
    roles_table = sa.table(
        "roles",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("name", sa.String),
        sa.column("description", sa.String),
        sa.column("is_system", sa.Boolean),
    )
    role_perms_table = sa.table(
        "role_permissions",
        sa.column("role_id", postgresql.UUID(as_uuid=True)),
        sa.column("permission_id", postgresql.UUID(as_uuid=True)),
    )

    # Insert permissions
    for perm_name in ALL_PERMISSIONS:
        bind.execute(
            sa.text(
                "INSERT INTO permissions (name) VALUES (:n) ON CONFLICT (name) DO NOTHING"
            ),
            {"n": perm_name},
        )

    # Insert roles
    for role_spec in ALL_ROLES:
        bind.execute(
            sa.text(
                """
                INSERT INTO roles (name, description, is_system)
                VALUES (:n, :d, :s)
                ON CONFLICT (name) DO UPDATE SET description = EXCLUDED.description,
                                                 is_system = EXCLUDED.is_system
                """
            ),
            {"n": role_spec.name, "d": role_spec.description, "s": role_spec.is_system},
        )

    # Wildcard "*" permission for the admin role.
    bind.execute(
        sa.text("INSERT INTO permissions (name) VALUES ('*') ON CONFLICT (name) DO NOTHING")
    )

    for role_spec in ALL_ROLES:
        role_id = bind.execute(
            sa.text("SELECT id FROM roles WHERE name = :n"),
            {"n": role_spec.name},
        ).scalar_one()
        bind.execute(
            sa.text("DELETE FROM role_permissions WHERE role_id = :r"),
            {"r": role_id},
        )
        for perm_name in role_spec.permissions:
            perm_id = bind.execute(
                sa.text("SELECT id FROM permissions WHERE name = :n"),
                {"n": perm_name},
            ).scalar_one()
            bind.execute(
                sa.text(
                    "INSERT INTO role_permissions (role_id, permission_id) "
                    "VALUES (:r, :p) ON CONFLICT DO NOTHING"
                ),
                {"r": role_id, "p": perm_id},
            )
    _ = perms_table, roles_table, role_perms_table  # silence "unused" if linted


def downgrade() -> None:
    op.drop_index(
        "ix_password_reset_tokens_expires_at", table_name="password_reset_tokens",
    )
    op.drop_index("ix_password_reset_tokens_user_id", table_name="password_reset_tokens")
    op.drop_table("password_reset_tokens")

    op.drop_index("ix_otp_codes_expires_at", table_name="otp_codes")
    op.drop_index("ix_otp_codes_user_purpose", table_name="otp_codes")
    op.drop_index("ix_otp_codes_email_purpose", table_name="otp_codes")
    op.drop_table("otp_codes")

    op.drop_index("ix_sessions_current_refresh_jti", table_name="sessions")
    op.drop_index("ix_sessions_active", table_name="sessions")
    op.drop_index("ix_sessions_user_id", table_name="sessions")
    op.drop_table("sessions")

    op.drop_table("user_roles")
    op.drop_table("role_permissions")
    op.drop_table("permissions")
    op.drop_table("roles")

    op.drop_index("ix_users_status", table_name="users")
    op.drop_table("users")
