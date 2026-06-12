"""Seed fulfillment prerequisites so checkout→confirm can place real orders.

Without these the order-confirm path fails:
  - no delivery_zones  -> "No delivery available to this address."
  - no warehouse 'MAIN'-> "Warehouse 'MAIN' not found."
Also defensively reconciles orders.fulfillment_stage DEFAULT (some demo DBs
drifted: column added NOT NULL without the model's server_default).

Idempotent (ON CONFLICT DO NOTHING). Run: python -m scripts.seed_fulfillment_demo
"""
from __future__ import annotations

import asyncio
import sys

from sqlalchemy import text

from app.core.db.session import get_sessionmaker


async def _run() -> int:
    sm = get_sessionmaker()
    async with sm() as s, s.begin():
        # warehouse 'MAIN' (stock reservation target on confirm)
        await s.execute(text(
            "INSERT INTO warehouses (id,code,name,is_active,created_at,updated_at) "
            "VALUES (gen_random_uuid(),'MAIN','Main Warehouse Dhaka',true,now(),now()) "
            "ON CONFLICT (code) DO NOTHING"
        ))
        # delivery zones: Dhaka metro + nationwide default (kind ∈ service_area|3pl)
        await s.execute(text(
            "INSERT INTO delivery_zones (id,code,name,kind,price,currency,cities,postal_codes,is_default,is_active,sort_order,created_at,updated_at) VALUES "
            "(gen_random_uuid(),'DHK','Dhaka Metro','service_area',60.00,'BDT','{Dhaka}','{}',false,true,10,now(),now()),"
            "(gen_random_uuid(),'BD-STD','Bangladesh Standard','service_area',120.00,'BDT','{}','{}',true,true,100,now(),now()) "
            "ON CONFLICT (code) DO NOTHING"
        ))
        # defensive: ensure fulfillment_stage has a server default (model declares it;
        # reconcile drifted DBs so order insert never NULL-violates)
        await s.execute(text(
            "ALTER TABLE orders ALTER COLUMN fulfillment_stage SET DEFAULT 'ORDER_PLACED'"
        ))
        # batch stock so checkout-confirm can RESERVE (FEFO reads batch
        # stock_balances, not the inventory_stocks mirror). One active batch +
        # 1000 available units per variant in MAIN. Idempotent via NOT EXISTS.
        await s.execute(text(
            "WITH ib AS ("
            "  INSERT INTO batches (id,variant_id,batch_number,expiry_date,status,created_at,updated_at) "
            "  SELECT gen_random_uuid(), v.id, 'DEMO-'||left(v.id::text,8), (now()+interval '365 days')::date,'active',now(),now() "
            "  FROM product_variants v "
            "  WHERE NOT EXISTS (SELECT 1 FROM batches b WHERE b.variant_id=v.id AND b.batch_number LIKE 'DEMO-%') "
            "  RETURNING id, variant_id) "
            "INSERT INTO stock_balances (id,variant_id,batch_id,warehouse_id,bucket,quantity,created_at,updated_at) "
            "SELECT gen_random_uuid(), ib.variant_id, ib.id, (SELECT id FROM warehouses WHERE code='MAIN'),'available',1000,now(),now() FROM ib"
        ))
        # inventory_stocks mirror (denormalised availability used by dashboards)
        await s.execute(text(
            "INSERT INTO inventory_stocks (id,sku,warehouse_id,available_qty,reserved_qty,damaged_qty,lost_qty,quarantine_qty,low_stock_threshold,is_blocked,created_at,updated_at) "
            "SELECT gen_random_uuid(), v.sku,'MAIN',1000,0,0,0,0,5,false,now(),now() FROM product_variants v "
            "WHERE NOT EXISTS (SELECT 1 FROM inventory_stocks i WHERE i.sku=v.sku AND i.warehouse_id='MAIN')"
        ))
        wz = (await s.execute(text("SELECT count(*) FROM warehouses"))).scalar()
        dz = (await s.execute(text("SELECT count(*) FROM delivery_zones"))).scalar()
    print(f"seed_fulfillment_demo: warehouses={wz} delivery_zones={dz} · stage default set")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_run()))
