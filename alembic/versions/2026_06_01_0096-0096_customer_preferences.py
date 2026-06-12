"""Customer preferences — locale/currency + marketing opt-ins + categories.

Backs `/api/v1/customers/preferences` (GET + PATCH) used by the customer
mobile apps' ProfileService. One row per customer user (unique user_id);
auto-created on first GET with sensible defaults.

Revision: 0096_customer_preferences
Down revision: 0095_rider_kyc
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0096_customer_preferences"
down_revision = "0095_rider_kyc"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "customer_preferences",
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
            unique=True,  # one preferences row per customer
        ),
        sa.Column("locale", sa.String(16), nullable=False, server_default="en-BD"),
        sa.Column("currency", sa.String(8), nullable=False, server_default="BDT"),
        sa.Column(
            "email_marketing", sa.Boolean, nullable=False, server_default=sa.text("false"),
        ),
        sa.Column(
            "sms_marketing", sa.Boolean, nullable=False, server_default=sa.text("false"),
        ),
        sa.Column(
            "push_marketing", sa.Boolean, nullable=False, server_default=sa.text("false"),
        ),
        sa.Column(
            "preferred_categories",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
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
    )


def downgrade() -> None:
    op.drop_table("customer_preferences")
