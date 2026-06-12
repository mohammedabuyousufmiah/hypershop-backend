"""Catalog: medicine fields + mother SKU + block/expiry visibility

Revision ID: 0004_catalog_medicine
Revises: 0003_catalog
Create Date: 2026-05-03

Adds pharma-specific columns to ``products`` and the mother-SKU column.
For non-medicine rows the new fields are optional. For ``is_medicine = true``
a CHECK constraint forces ``generic_name``, ``strength``, and ``brand_id`` to
be present, plus ``requires_prescription`` is always non-null (boolean).

``mother_sku`` is backfilled for any pre-existing rows using ``gen_random_bytes``
so the NOT NULL constraint can be added without dropping data.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_catalog_medicine"
down_revision: str | Sequence[str] | None = "0003_catalog"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ---------- new columns (nullable first so we can backfill) ----------
    op.add_column(
        "products",
        sa.Column(
            "is_medicine",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "products",
        sa.Column(
            "requires_prescription",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column("products", sa.Column("generic_name", sa.String(200), nullable=True))
    op.add_column("products", sa.Column("strength", sa.String(64), nullable=True))
    op.add_column("products", sa.Column("dosage_form", sa.String(64), nullable=True))
    op.add_column("products", sa.Column("mother_sku", sa.String(40), nullable=True))
    op.add_column(
        "products",
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "products",
        sa.Column("blocked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("products", sa.Column("blocked_reason", sa.String(255), nullable=True))

    # ---------- backfill mother_sku for pre-existing rows ----------
    # 8 hex chars uppercased → matches the runtime format closely enough
    # for legacy rows. New rows always go through the application generator.
    op.execute(
        """
        UPDATE products
           SET mother_sku = 'HS-' || upper(encode(gen_random_bytes(4), 'hex'))
         WHERE mother_sku IS NULL
        """,
    )

    op.alter_column("products", "mother_sku", nullable=False)
    op.create_unique_constraint("uq_products_mother_sku", "products", ["mother_sku"])
    op.create_index("ix_products_blocked_at", "products", ["blocked_at"])
    op.create_index("ix_products_expires_at", "products", ["expires_at"])

    op.create_check_constraint(
        "ck_products_medicine_required_fields",
        "products",
        (
            "is_medicine = false OR ("
            "generic_name IS NOT NULL AND generic_name <> '' AND "
            "strength IS NOT NULL AND strength <> '' AND "
            "brand_id IS NOT NULL"
            ")"
        ),
    )
    op.create_check_constraint(
        "ck_products_block_pair",
        "products",
        "(blocked_at IS NULL) = (blocked_reason IS NULL)",
    )

    # Drop the server defaults — we want the application to be explicit per row.
    op.alter_column("products", "is_medicine", server_default=None)
    op.alter_column("products", "requires_prescription", server_default=None)


def downgrade() -> None:
    op.drop_constraint("ck_products_block_pair", "products", type_="check")
    op.drop_constraint("ck_products_medicine_required_fields", "products", type_="check")
    op.drop_index("ix_products_expires_at", table_name="products")
    op.drop_index("ix_products_blocked_at", table_name="products")
    op.drop_constraint("uq_products_mother_sku", "products", type_="unique")
    op.drop_column("products", "blocked_reason")
    op.drop_column("products", "blocked_at")
    op.drop_column("products", "expires_at")
    op.drop_column("products", "mother_sku")
    op.drop_column("products", "dosage_form")
    op.drop_column("products", "strength")
    op.drop_column("products", "generic_name")
    op.drop_column("products", "requires_prescription")
    op.drop_column("products", "is_medicine")
