"""0066 drop prescription_review from orders.status — Hypershop is a pure
e-commerce marketplace, not pharmacy.

Migrates any existing rows in ``prescription_review`` to ``approved`` so
they continue down the fulfilment pipeline (any in-flight orders that
were sitting in the obsolete state get auto-approved on migrate). Then
tightens the ``status_enum`` CHECK constraint to exclude the value.

``order_status_history.to_status`` CHECK stays permissive so old audit
rows for orders that historically transited through ``prescription_review``
are preserved.

Revision ID: 0066_drop_prescription_review
Revises: 0065_dashboard_layouts
Create Date: 2026-05-17

"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0066_drop_prescription_review"
down_revision: str | Sequence[str] | None = "0065_dashboard_layouts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_OLD_STATUS_ENUM = (
    "'pending_payment','payment_confirmed','stock_reserved',"
    "'prescription_review','approved','packing','out_for_delivery',"
    "'completed','cancelled','failed'"
)
_NEW_STATUS_ENUM = (
    "'pending_payment','payment_confirmed','stock_reserved',"
    "'approved','packing','out_for_delivery',"
    "'completed','cancelled','failed'"
)


def upgrade() -> None:
    # 1) Auto-approve any in-flight rows still sitting in the obsolete
    #    state. They'd be wedged otherwise since the API surface that
    #    moved them onward (``/approve-prescription``) is gone.
    op.execute(
        """
        UPDATE orders
        SET status = 'approved',
            approved_at = COALESCE(approved_at, now() AT TIME ZONE 'UTC')
        WHERE status = 'prescription_review'
        """,
    )

    # 2) Tighten the CHECK constraint on orders.status.
    op.execute("ALTER TABLE orders DROP CONSTRAINT IF EXISTS status_enum")
    op.execute(
        f"ALTER TABLE orders ADD CONSTRAINT status_enum "
        f"CHECK (status IN ({_NEW_STATUS_ENUM}))",
    )


def downgrade() -> None:
    # Restore the original permissive CHECK. Does NOT attempt to move
    # rows back to ``prescription_review`` — that data is lost on
    # purpose during the upgrade (the state itself is meaningless to
    # an e-commerce marketplace).
    op.execute("ALTER TABLE orders DROP CONSTRAINT IF EXISTS status_enum")
    op.execute(
        f"ALTER TABLE orders ADD CONSTRAINT status_enum "
        f"CHECK (status IN ({_OLD_STATUS_ENUM}))",
    )
