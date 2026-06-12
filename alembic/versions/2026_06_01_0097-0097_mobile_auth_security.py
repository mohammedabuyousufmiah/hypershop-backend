"""Mobile auth security — per-device PIN / biometric quick-login sessions.

Backs the rider/customer MobileAuthService:
  POST /api/v1/auth/pin/setup, /auth/pin/verify
  POST /api/v1/auth/biometric/enable, /disable, /unlock
  GET  /api/v1/auth/devices
  POST /api/v1/auth/logout-device, /auth/reauth/check

One row per (user_id, device_id, app_type). Stores a hashed PIN (argon2),
biometric-enabled flag, failed-attempt counter + lockout, and last-unlock
timestamp used by reauth/check.

Social login (POST /auth/google, /auth/huawei) needs NO table — it verifies
the provider id_token and reuses the existing users/sessions tables.

Revision: 0097_mobile_auth_security
Down revision: 0096_customer_preferences
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0097_mobile_auth_security"
down_revision = "0096_customer_preferences"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mobile_device_sessions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("device_id", sa.String(128), nullable=False),
        sa.Column("app_type", sa.String(24), nullable=False, server_default="rider_app"),
        sa.Column("platform", sa.String(16), nullable=False, server_default="android"),
        sa.Column("device_name", sa.String(160), nullable=True),
        sa.Column("app_version", sa.String(32), nullable=True),
        sa.Column("pin_hash", sa.Text, nullable=True),
        sa.Column(
            "pin_enabled", sa.Boolean, nullable=False, server_default=sa.text("false"),
        ),
        sa.Column(
            "biometric_enabled", sa.Boolean, nullable=False, server_default=sa.text("false"),
        ),
        sa.Column(
            "failed_attempts", sa.Integer, nullable=False, server_default="0",
        ),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_unlock_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "is_active", sa.Boolean, nullable=False, server_default=sa.text("true"),
        ),
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
        sa.UniqueConstraint(
            "user_id", "device_id", "app_type",
            name="uq_mobile_device_sessions_user_device_app",
        ),
    )
    op.create_index(
        "ix_mobile_device_sessions_user",
        "mobile_device_sessions",
        ["user_id", "app_type"],
    )


def downgrade() -> None:
    op.drop_index("ix_mobile_device_sessions_user", table_name="mobile_device_sessions")
    op.drop_table("mobile_device_sessions")
