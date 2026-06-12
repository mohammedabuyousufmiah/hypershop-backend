"""Delivery: zones table + seed data

Revision ID: 0007_delivery
Revises: 0006_orders
Create Date: 2026-05-03

Seeds the rule-of-thumb starter zones:
- DHAKA-METRO (service_area, 50 BDT) — own delivery, default fallback.
- DHAKA-OUTER (3pl, 100 BDT) — third-party for greater Dhaka.
- OUTSIDE-DHAKA (3pl, 130 BDT) — third-party for outside Dhaka.

Admin can rename / re-price these or add more via the admin API.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_delivery"
down_revision: str | Sequence[str] | None = "0006_orders"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "delivery_zones",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("code", sa.String(48), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("price", sa.Numeric(14, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="BDT"),
        sa.Column(
            "cities",
            postgresql.ARRAY(sa.String(120)),
            nullable=False,
            server_default=sa.text("ARRAY[]::varchar[]"),
        ),
        sa.Column(
            "postal_codes",
            postgresql.ARRAY(sa.String(16)),
            nullable=False,
            server_default=sa.text("ARRAY[]::varchar[]"),
        ),
        sa.Column(
            "is_default", sa.Boolean(), nullable=False, server_default=sa.text("false"),
        ),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.text("true"),
        ),
        sa.Column(
            "sort_order", sa.Integer(), nullable=False, server_default=sa.text("0"),
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
        sa.UniqueConstraint("code", name="uq_delivery_zones_code"),
        sa.CheckConstraint(
            "kind IN ('service_area','3pl')",
            name="ck_delivery_zones_kind_enum",
        ),
        sa.CheckConstraint("price >= 0", name="ck_delivery_zones_price_nonneg"),
        sa.CheckConstraint(
            "kind <> '3pl' OR (price >= 70 AND price <= 150)",
            name="ck_delivery_zones_3pl_price_band",
        ),
        sa.CheckConstraint(
            "currency = upper(currency) AND length(currency) = 3",
            name="ck_delivery_zones_currency_iso",
        ),
    )
    op.create_index(
        "ix_delivery_zones_kind_active", "delivery_zones", ["kind", "is_active"],
    )
    op.create_index(
        "ix_delivery_zones_is_default", "delivery_zones", ["is_default"],
    )

    # Only one row may be the default at a time. Partial unique index over
    # WHERE is_default = true.
    op.execute(
        """
        CREATE UNIQUE INDEX uq_delivery_zones_single_default
        ON delivery_zones ((is_default))
        WHERE is_default = true
        """,
    )

    # Seed starter zones.
    op.execute(
        """
        INSERT INTO delivery_zones (code, name, kind, price, currency, cities, is_default, sort_order)
        VALUES
          ('DHAKA-METRO', 'Dhaka Metro', 'service_area', 50.00, 'BDT',
           ARRAY['Dhaka','Mirpur','Dhanmondi','Gulshan','Banani','Uttara','Mohammadpur'], true, 10),
          ('DHAKA-OUTER', 'Greater Dhaka (3PL)', '3pl', 100.00, 'BDT',
           ARRAY['Savar','Tongi','Narayanganj','Gazipur','Keraniganj'], false, 20),
          ('OUTSIDE-DHAKA', 'Outside Dhaka (3PL)', '3pl', 130.00, 'BDT',
           ARRAY[]::varchar[], false, 30)
        """,
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_delivery_zones_single_default")
    op.drop_index("ix_delivery_zones_is_default", table_name="delivery_zones")
    op.drop_index("ix_delivery_zones_kind_active", table_name="delivery_zones")
    op.drop_table("delivery_zones")
