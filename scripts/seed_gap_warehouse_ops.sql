-- =============================================================================
-- seed_gap_warehouse_ops.sql
--
-- Idempotent seed for the admin "warehouse-ops" feature (AdminWarehouseClient).
-- The READ gap router (app/modules/inventory/api/warehouse_ops_gap.py) and the
-- ACTION gap router (warehouse_ops_actions_gap.py) read/write these tables, but
-- nothing in the live build creates them yet. This script:
--
--   * CREATE TABLE IF NOT EXISTS for each backing table, with columns whose
--     names match exactly what the GET endpoints SELECT (so the existing GETs
--     return these rows unchanged once they read the real tables), and
--   * INSERT ... ON CONFLICT DO NOTHING demo rows with realistic Bangladeshi
--     data (Dhaka / Chattogram / Sylhet warehouses, BD courier codes, etc.).
--
-- Pure Postgres, no model imports, safe to run repeatedly (idempotent).
-- Run with:  psql "$DATABASE_URL" -f scripts/seed_gap_warehouse_ops.sql
-- =============================================================================

-- gen_random_uuid() lives in pgcrypto on older PG; harmless if already present.
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- -----------------------------------------------------------------------------
-- warehouses  (WarehouseWire)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS warehouses (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    code            text NOT NULL UNIQUE,
    name            text NOT NULL,
    type            text NOT NULL DEFAULT 'PLATFORM',
    country_code    text NOT NULL DEFAULT 'BD',
    owner_seller_id uuid,
    address         jsonb NOT NULL DEFAULT '{}'::jsonb,
    is_active       boolean NOT NULL DEFAULT true,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

INSERT INTO warehouses (id, code, name, type, country_code, address, is_active)
VALUES
  ('11111111-1111-1111-1111-111111111101', 'WH-DHK-CENTRAL',
   'Hypershop Central Fulfilment — Tejgaon', 'PLATFORM', 'BD',
   '{"line1":"Tejgaon I/A","city":"Dhaka","postcode":"1208","division":"Dhaka"}'::jsonb,
   true),
  ('11111111-1111-1111-1111-111111111102', 'WH-DHK-SAVAR',
   'Savar Distribution Hub', 'PLATFORM', 'BD',
   '{"line1":"Hemayetpur","city":"Savar","postcode":"1340","division":"Dhaka"}'::jsonb,
   true),
  ('11111111-1111-1111-1111-111111111103', 'WH-CTG-PORT',
   'Chattogram Port Warehouse', 'PLATFORM', 'BD',
   '{"line1":"Agrabad C/A","city":"Chattogram","postcode":"4100","division":"Chattogram"}'::jsonb,
   true),
  ('11111111-1111-1111-1111-111111111104', 'WH-SYL-ZINDA',
   'Sylhet Zindabazar Store', 'QC_STORE', 'BD',
   '{"line1":"Zindabazar","city":"Sylhet","postcode":"3100","division":"Sylhet"}'::jsonb,
   true),
  ('11111111-1111-1111-1111-111111111105', 'WH-KHL-SELLER',
   'Khulna Seller Consignment Depot', 'SELLER', 'BD',
   '{"line1":"Khan Jahan Ali Rd","city":"Khulna","postcode":"9100","division":"Khulna"}'::jsonb,
   false)
ON CONFLICT (code) DO NOTHING;

-- -----------------------------------------------------------------------------
-- warehouse_locations  (LocationWire)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS warehouse_locations (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    warehouse_id uuid NOT NULL,
    code         text NOT NULL,
    kind         text NOT NULL DEFAULT 'STOCK',
    zone         text,
    aisle        text,
    bin          text,
    is_active    boolean NOT NULL DEFAULT true,
    created_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (warehouse_id, code)
);

INSERT INTO warehouse_locations
    (id, warehouse_id, code, kind, zone, aisle, bin, is_active)
VALUES
  ('22222222-2222-2222-2222-222222222201',
   '11111111-1111-1111-1111-111111111101', 'RCV-01', 'RECEIVE', 'Z1', 'A1', 'B01', true),
  ('22222222-2222-2222-2222-222222222202',
   '11111111-1111-1111-1111-111111111101', 'STK-A1-01', 'STOCK', 'Z2', 'A1', 'B01', true),
  ('22222222-2222-2222-2222-222222222203',
   '11111111-1111-1111-1111-111111111101', 'STK-A1-02', 'STOCK', 'Z2', 'A1', 'B02', true),
  ('22222222-2222-2222-2222-222222222204',
   '11111111-1111-1111-1111-111111111101', 'PCK-01', 'PACK', 'Z3', 'P1', 'B01', true),
  ('22222222-2222-2222-2222-222222222205',
   '11111111-1111-1111-1111-111111111101', 'STG-OUT-01', 'STAGING', 'Z4', 'S1', 'B01', true),
  ('22222222-2222-2222-2222-222222222206',
   '11111111-1111-1111-1111-111111111103', 'STK-CTG-01', 'STOCK', 'Z1', 'C1', 'B01', true)
ON CONFLICT (warehouse_id, code) DO NOTHING;

-- -----------------------------------------------------------------------------
-- receiving_notes  (ReceivingNoteWire) + receiving_items (ReceivingItemWire)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS receiving_notes (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    code         text NOT NULL UNIQUE,
    warehouse_id uuid NOT NULL,
    source_type  text NOT NULL DEFAULT 'SELLER_INBOUND',
    source_ref   text,
    seller_id    uuid,
    status       text NOT NULL DEFAULT 'OPEN',
    expected_at  timestamptz,
    received_at  timestamptz,
    received_by  uuid,
    closed_at    timestamptz,
    created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS receiving_items (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    receiving_note_id  uuid NOT NULL,
    offer_id           uuid NOT NULL,
    qty_expected       integer NOT NULL DEFAULT 0,
    qty_received       integer NOT NULL DEFAULT 0,
    qty_damaged        integer NOT NULL DEFAULT 0,
    target_location_id uuid
);

INSERT INTO receiving_notes
    (id, code, warehouse_id, source_type, source_ref, seller_id, status, expected_at)
VALUES
  ('33333333-3333-3333-3333-333333333301', 'RN-DHK-0001',
   '11111111-1111-1111-1111-111111111101', 'SELLER_INBOUND', 'PO-7781',
   '99999999-9999-9999-9999-999999999901', 'OPEN', now() + interval '1 day'),
  ('33333333-3333-3333-3333-333333333302', 'RN-DHK-0002',
   '11111111-1111-1111-1111-111111111101', 'PLATFORM_PURCHASE', 'INV-5520',
   NULL, 'RECEIVED', now() - interval '2 day'),
  ('33333333-3333-3333-3333-333333333303', 'RN-CTG-0001',
   '11111111-1111-1111-1111-111111111103', 'CUSTOMER_RETURN', 'RMA-3310',
   '99999999-9999-9999-9999-999999999902', 'OPEN', now() + interval '2 hour'),
  ('33333333-3333-3333-3333-333333333304', 'RN-DHK-0003',
   '11111111-1111-1111-1111-111111111101', 'TRANSFER', 'TRF-2201',
   NULL, 'CLOSED', now() - interval '5 day')
ON CONFLICT (code) DO NOTHING;

INSERT INTO receiving_items
    (id, receiving_note_id, offer_id, qty_expected, qty_received, qty_damaged, target_location_id)
VALUES
  ('33aa3333-3333-3333-3333-3333333330a1', '33333333-3333-3333-3333-333333333301',
   '44444444-4444-4444-4444-444444444401', 100, 0, 0,
   '22222222-2222-2222-2222-222222222202'),
  ('33aa3333-3333-3333-3333-3333333330a2', '33333333-3333-3333-3333-333333333301',
   '44444444-4444-4444-4444-444444444402', 50, 0, 0,
   '22222222-2222-2222-2222-222222222203'),
  ('33aa3333-3333-3333-3333-3333333330a3', '33333333-3333-3333-3333-333333333302',
   '44444444-4444-4444-4444-444444444403', 200, 198, 2,
   '22222222-2222-2222-2222-222222222202')
ON CONFLICT (id) DO NOTHING;

-- -----------------------------------------------------------------------------
-- pick_tasks  (PickTaskWire) + pick_task_items (PickTaskItemWire)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS pick_tasks (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    warehouse_id uuid NOT NULL,
    order_id     uuid NOT NULL,
    seller_id    uuid NOT NULL,
    status       text NOT NULL DEFAULT 'AVAILABLE',
    priority     integer NOT NULL DEFAULT 0,
    claimed_by   uuid,
    claimed_at   timestamptz,
    started_at   timestamptz,
    completed_at timestamptz,
    created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS pick_task_items (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    pick_task_id uuid NOT NULL,
    offer_id     uuid NOT NULL,
    qty_requested integer NOT NULL DEFAULT 0,
    qty_picked   integer NOT NULL DEFAULT 0,
    location_id  uuid
);

INSERT INTO pick_tasks
    (id, warehouse_id, order_id, seller_id, status, priority)
VALUES
  ('55555555-5555-5555-5555-555555555501',
   '11111111-1111-1111-1111-111111111101',
   '66666666-6666-6666-6666-666666666601',
   '99999999-9999-9999-9999-999999999901', 'AVAILABLE', 10),
  ('55555555-5555-5555-5555-555555555502',
   '11111111-1111-1111-1111-111111111101',
   '66666666-6666-6666-6666-666666666602',
   '99999999-9999-9999-9999-999999999902', 'AVAILABLE', 5),
  ('55555555-5555-5555-5555-555555555503',
   '11111111-1111-1111-1111-111111111101',
   '66666666-6666-6666-6666-666666666603',
   '99999999-9999-9999-9999-999999999901', 'CLAIMED', 8)
ON CONFLICT (id) DO NOTHING;

INSERT INTO pick_task_items
    (id, pick_task_id, offer_id, qty_requested, qty_picked, location_id)
VALUES
  ('55aa5555-5555-5555-5555-5555555550a1', '55555555-5555-5555-5555-555555555501',
   '44444444-4444-4444-4444-444444444401', 2, 0,
   '22222222-2222-2222-2222-222222222202'),
  ('55aa5555-5555-5555-5555-5555555550a2', '55555555-5555-5555-5555-555555555501',
   '44444444-4444-4444-4444-444444444402', 1, 0,
   '22222222-2222-2222-2222-222222222203'),
  ('55aa5555-5555-5555-5555-5555555550a3', '55555555-5555-5555-5555-555555555502',
   '44444444-4444-4444-4444-444444444403', 3, 0,
   '22222222-2222-2222-2222-222222222202')
ON CONFLICT (id) DO NOTHING;

-- -----------------------------------------------------------------------------
-- pack_tasks  (PackTaskWire)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS pack_tasks (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    warehouse_id uuid NOT NULL,
    pick_task_id uuid NOT NULL,
    order_id     uuid NOT NULL,
    status       text NOT NULL DEFAULT 'AVAILABLE',
    claimed_by   uuid,
    claimed_at   timestamptz,
    started_at   timestamptz,
    completed_at timestamptz,
    package_code text,
    weight_grams integer,
    length_mm    integer,
    width_mm     integer,
    height_mm    integer,
    created_at   timestamptz NOT NULL DEFAULT now()
);

INSERT INTO pack_tasks
    (id, warehouse_id, pick_task_id, order_id, status, package_code,
     weight_grams, length_mm, width_mm, height_mm)
VALUES
  ('77777777-7777-7777-7777-777777777701',
   '11111111-1111-1111-1111-111111111101',
   '55555555-5555-5555-5555-555555555503',
   '66666666-6666-6666-6666-666666666603', 'CLAIMED', NULL, NULL, NULL, NULL, NULL),
  ('77777777-7777-7777-7777-777777777702',
   '11111111-1111-1111-1111-111111111101',
   '55555555-5555-5555-5555-555555555501',
   '66666666-6666-6666-6666-666666666601', 'COMPLETED', 'PKG-DHK-0001',
   850, 300, 200, 150)
ON CONFLICT (id) DO NOTHING;

-- -----------------------------------------------------------------------------
-- dispatch_batches  (DispatchBatchWire) + dispatch_items (DispatchItemWire)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS dispatch_batches (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    code           text NOT NULL UNIQUE,
    warehouse_id   uuid NOT NULL,
    courier_code   text NOT NULL,
    status         text NOT NULL DEFAULT 'OPEN',
    handed_over_at timestamptz,
    handed_over_by uuid,
    manifest_key   text,
    created_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS dispatch_items (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    dispatch_batch_id uuid NOT NULL,
    pack_task_id     uuid,
    package_code     text NOT NULL,
    order_id         uuid,
    created_at       timestamptz NOT NULL DEFAULT now()
);

INSERT INTO dispatch_batches
    (id, code, warehouse_id, courier_code, status, manifest_key)
VALUES
  ('88888888-8888-8888-8888-888888888801', 'DB-DHK-0001',
   '11111111-1111-1111-1111-111111111101', 'PATHAO', 'OPEN', NULL),
  ('88888888-8888-8888-8888-888888888802', 'DB-DHK-0002',
   '11111111-1111-1111-1111-111111111101', 'STEADFAST', 'HANDED_OVER',
   'MANIFEST-SF-20260604-001'),
  ('88888888-8888-8888-8888-888888888803', 'DB-CTG-0001',
   '11111111-1111-1111-1111-111111111103', 'REDX', 'OPEN', NULL)
ON CONFLICT (code) DO NOTHING;

INSERT INTO dispatch_items
    (id, dispatch_batch_id, pack_task_id, package_code, order_id)
VALUES
  ('88aa8888-8888-8888-8888-8888888880a1', '88888888-8888-8888-8888-888888888802',
   '77777777-7777-7777-7777-777777777702', 'PKG-DHK-0001',
   '66666666-6666-6666-6666-666666666601')
ON CONFLICT (id) DO NOTHING;

-- -----------------------------------------------------------------------------
-- cycle_counts  (CycleCountWire)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS cycle_counts (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    warehouse_id  uuid NOT NULL,
    location_id   uuid,
    offer_id      uuid NOT NULL,
    expected_qty  integer NOT NULL DEFAULT 0,
    counted_qty   integer,
    variance_qty  integer,
    status        text NOT NULL DEFAULT 'PENDING',
    performed_by  uuid,
    performed_at  timestamptz,
    reconciled_by uuid,
    reconciled_at timestamptz,
    notes         text,
    created_at    timestamptz NOT NULL DEFAULT now()
);

INSERT INTO cycle_counts
    (id, warehouse_id, location_id, offer_id, expected_qty, counted_qty,
     variance_qty, status, notes)
VALUES
  ('99aa9999-9999-9999-9999-9999999990a1',
   '11111111-1111-1111-1111-111111111101',
   '22222222-2222-2222-2222-222222222202',
   '44444444-4444-4444-4444-444444444401', 100, NULL, NULL, 'PENDING', NULL),
  ('99aa9999-9999-9999-9999-9999999990a2',
   '11111111-1111-1111-1111-111111111101',
   '22222222-2222-2222-2222-222222222203',
   '44444444-4444-4444-4444-444444444402', 50, 48, -2, 'COUNTED',
   'Two units found damaged during count'),
  ('99aa9999-9999-9999-9999-9999999990a3',
   '11111111-1111-1111-1111-111111111103',
   '22222222-2222-2222-2222-222222222206',
   '44444444-4444-4444-4444-444444444403', 200, 200, 0, 'RECONCILED',
   'No variance — reconciled clean')
ON CONFLICT (id) DO NOTHING;

-- =============================================================================
-- End of seed.
-- =============================================================================
