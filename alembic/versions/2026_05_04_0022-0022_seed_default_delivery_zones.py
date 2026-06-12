"""Seed default Bangladesh delivery zones.

Revision ID: 0022_seed_default_delivery_zones
Revises: 0021_whatsapp_message_statuses
Create Date: 2026-05-04

Without at least one zone (or a default), ``orders.service.place_order``
cannot quote a delivery fee and every customer order errors out with
``no delivery available``. This migration ships a sane starting set
that admins can edit / extend via the existing admin endpoints:

  DHAKA-METRO       service_area  50  BDT  (default)
                    Dhaka, Mirpur, Dhanmondi, Gulshan, Banani, Uttara, Mohammadpur
  DHAKA-OUTER       3pl           100 BDT
                    Savar, Tongi, Narayanganj, Gazipur, Keraniganj
  OUTSIDE-DHAKA     3pl           130 BDT
                    catch-all (no city list — only matches via
                    fallback because nothing more specific hit)

All idempotent on the unique ``code`` index — re-running this migration
is a no-op.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0022_seed_default_delivery_zones"
down_revision: str | Sequence[str] | None = "0021_whatsapp_message_statuses"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO delivery_zones
            (code, name, kind, price, currency, cities, is_default, sort_order)
        VALUES
            ('DHAKA-METRO', 'Dhaka Metro', 'service_area', 50.00, 'BDT',
             ARRAY['Dhaka','Mirpur','Dhanmondi','Gulshan','Banani','Uttara','Mohammadpur'], true, 10),
            ('DHAKA-OUTER', 'Greater Dhaka (3PL)', '3pl', 100.00, 'BDT',
             ARRAY['Savar','Tongi','Narayanganj','Gazipur','Keraniganj'], false, 20),
            ('OUTSIDE-DHAKA', 'Outside Dhaka (3PL)', '3pl', 130.00, 'BDT',
             ARRAY[]::varchar[], false, 30)
        ON CONFLICT (code) DO NOTHING
        """,
    )


def downgrade() -> None:
    # Surgical: only delete the codes we inserted. Don't wipe admin-added
    # zones.
    op.execute(
        """
        DELETE FROM delivery_zones
         WHERE code IN ('DHAKA-METRO','DHAKA-OUTER','OUTSIDE-DHAKA')
        """,
    )
