"""sellers phase 2 — products.seller_id + backfill to Hypershop Direct

Revision ID: 0034_products_seller_id
Revises: 0033_sellers
Create Date: 2026-05-10

Phase 2 of the multi-seller initiative. Adds ``products.seller_id``
(nullable + FK to sellers), backfills every existing row to the
``hypershop-direct`` seed seller created in 0033, and indexes the
column for the seller dashboard's "my products" filter (phase 3).

Why nullable + soft FK ondelete=SET NULL:

  - Existing prod data has zero rows with a seller — backfilling in
    a single statement is safe but the column stays nullable for one
    release so a downstream migration can flip it to NOT NULL after
    the operator confirms no row leaked through.
  - SET NULL on delete keeps catalog data alive if a seller row is
    purged for compliance reasons; the product becomes "unowned"
    rather than disappearing from the customer-facing surface.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0034_products_seller_id"
down_revision: str | Sequence[str] | None = "0033_sellers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "products",
        sa.Column(
            "seller_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sellers.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_products_seller_id", "products", ["seller_id"])

    # Backfill — every product without a seller_id gets pinned to
    # Hypershop Direct. The seed row was created by migration 0033;
    # if for any reason it's missing, fail loudly rather than leave
    # products unpinned.
    op.execute(
        """
        UPDATE products
           SET seller_id = (SELECT id FROM sellers WHERE slug = 'hypershop-direct')
         WHERE seller_id IS NULL
        """,
    )

    # Sanity — every row should now be pinned. If a future seller
    # row insertion outraced this migration's backfill (highly
    # unlikely on a single-host stack), surface the issue.
    bind = op.get_bind()
    unpinned = bind.execute(
        sa.text("SELECT count(*) FROM products WHERE seller_id IS NULL"),
    ).scalar_one()
    if unpinned and unpinned > 0:
        raise RuntimeError(
            f"Backfill incomplete: {unpinned} product(s) still have "
            f"NULL seller_id after migration 0034.",
        )


def downgrade() -> None:
    op.drop_index("ix_products_seller_id", table_name="products")
    op.drop_column("products", "seller_id")
