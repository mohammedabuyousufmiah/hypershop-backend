-- ============================================================================
-- seed_gap_wms.sql — idempotent demo data for the WMS (Noon-CFC) console.
--
-- Backs the GET endpoints in app/modules/admin_v3_stubs/api/wms_gap.py and
-- the action endpoints in wms_actions_gap.py. This build ships NO dedicated
-- WMS schema, so the GETs fall back to empty. This script creates the exact
-- tables/columns those SELECTs reference and seeds realistic Bangladeshi
-- warehouse rows so the console renders live data.
--
-- Columns MUST match what wms_gap.py selects:
--   wms_asn        : id, asn_no, vendor_name, status, expected_qty, received_qty
--   wms_shipments  : id, shipment_no, courier, dest_city, weight_g, status
--   wms_ndr        : id, reason_code, attempt_no, action
--   wms_bins       : id, bin_code, zone, bin_type, capacity, is_active
--   wms_putaway    : status            (dashboard COUNT)
--   wms_pick_jobs  : status            (dashboard COUNT)
--
-- Postgres dialect. Pure SQL, idempotent, safe to run repeatedly.
-- Run e.g.:  psql "$DATABASE_URL" -f backend/scripts/seed_gap_wms.sql
-- ============================================================================

-- ── Inbound: Advance Shipment Notices ──────────────────────────────────────
CREATE TABLE IF NOT EXISTS wms_asn (
    id            text PRIMARY KEY DEFAULT gen_random_uuid()::text,
    asn_no        text NOT NULL,
    vendor_name   text,
    status        text NOT NULL DEFAULT 'scheduled',
    expected_qty  integer NOT NULL DEFAULT 0,
    received_qty  integer NOT NULL DEFAULT 0,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_wms_asn_asn_no ON wms_asn (asn_no);

INSERT INTO wms_asn (asn_no, vendor_name, status, expected_qty, received_qty) VALUES
    ('ASN-DHK-24001', 'Pran-RFL Group',            'scheduled', 480, 0),
    ('ASN-DHK-24002', 'Square Consumer Products',  'gated_in',  300, 0),
    ('ASN-DHK-24003', 'Akij Food & Beverage',      'receiving', 720, 410),
    ('ASN-CTG-24004', 'Bashundhara Paper Mills',   'received',  600, 600),
    ('ASN-DHK-24005', 'Walton Hi-Tech Industries', 'scheduled', 150, 0),
    ('ASN-CTG-24006', 'Meghna Group of Industries','gated_in',  900, 0),
    ('ASN-DHK-24007', 'ACI Logistics',             'received',  240, 238)
ON CONFLICT (asn_no) DO NOTHING;

-- ── Outbound: Shipments ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS wms_shipments (
    id           text PRIMARY KEY DEFAULT gen_random_uuid()::text,
    shipment_no  text NOT NULL,
    courier      text,
    dest_city    text,
    weight_g     integer NOT NULL DEFAULT 0,
    status       text NOT NULL DEFAULT 'created',
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_wms_shipments_shipment_no
    ON wms_shipments (shipment_no);

INSERT INTO wms_shipments (shipment_no, courier, dest_city, weight_g, status) VALUES
    ('SHP-25001', 'Pathao Courier',  'Dhaka',      1850, 'dispatched'),
    ('SHP-25002', 'Steadfast',       'Chattogram', 3200, 'manifested'),
    ('SHP-25003', 'RedX',            'Sylhet',      640, 'awb_generated'),
    ('SHP-25004', 'Sundarban',       'Khulna',     5400, 'dispatched'),
    ('SHP-25005', 'Paperfly',        'Rajshahi',   1200, 'measured'),
    ('SHP-25006', 'eCourier',        'Barishal',    980, 'created'),
    ('SHP-25007', 'Pathao Courier',  'Dhaka',      2300, 'manifested')
ON CONFLICT (shipment_no) DO NOTHING;

-- ── Exceptions: Non-Delivery Reports ────────────────────────────────────────
CREATE TABLE IF NOT EXISTS wms_ndr (
    id           text PRIMARY KEY DEFAULT gen_random_uuid()::text,
    reason_code  text NOT NULL,
    attempt_no   integer NOT NULL DEFAULT 1,
    action       text NOT NULL DEFAULT 'pending',
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now()
);
-- Stable synthetic key so re-runs do not duplicate the demo NDRs.
CREATE UNIQUE INDEX IF NOT EXISTS uq_wms_ndr_seed
    ON wms_ndr (reason_code, attempt_no, action);

INSERT INTO wms_ndr (reason_code, attempt_no, action) VALUES
    ('customer_unreachable', 1, 'pending'),
    ('address_incomplete',   2, 'pending'),
    ('cash_not_ready',       1, 'pending'),
    ('refused_by_customer',  3, 'rto'),
    ('reschedule_requested', 1, 'reattempt'),
    ('outside_delivery_zone',1, 'pending')
ON CONFLICT (reason_code, attempt_no, action) DO NOTHING;

-- ── Storage bins ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS wms_bins (
    id          text PRIMARY KEY DEFAULT gen_random_uuid()::text,
    bin_code    text NOT NULL,
    zone        text,
    bin_type    text,
    capacity    integer NOT NULL DEFAULT 0,
    is_active   boolean NOT NULL DEFAULT true,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_wms_bins_bin_code ON wms_bins (bin_code);

INSERT INTO wms_bins (bin_code, zone, bin_type, capacity, is_active) VALUES
    ('A-01-01', 'Zone A (Fast-Moving)', 'shelf',   120, true),
    ('A-01-02', 'Zone A (Fast-Moving)', 'shelf',   120, true),
    ('B-02-05', 'Zone B (Bulk)',        'pallet',   48, true),
    ('B-02-06', 'Zone B (Bulk)',        'pallet',   48, true),
    ('C-03-11', 'Zone C (Cold Chain)',  'chiller',  30, true),
    ('D-04-09', 'Zone D (Returns)',     'shelf',    90, true),
    ('E-05-02', 'Zone E (Hazmat)',      'cage',     20, false),
    ('F-06-07', 'Zone F (Oversize)',    'floor',    12, true)
ON CONFLICT (bin_code) DO NOTHING;

-- ── Putaway queue (dashboard COUNT only) ────────────────────────────────────
CREATE TABLE IF NOT EXISTS wms_putaway (
    id          text PRIMARY KEY DEFAULT gen_random_uuid()::text,
    status      text NOT NULL DEFAULT 'pending',
    created_at  timestamptz NOT NULL DEFAULT now()
);
-- Seed a fixed count of pending putaway tasks (idempotent via guard).
INSERT INTO wms_putaway (status)
SELECT 'pending' FROM generate_series(1, 3)
WHERE NOT EXISTS (SELECT 1 FROM wms_putaway WHERE status = 'pending');

-- ── Pick jobs (dashboard COUNT only) ────────────────────────────────────────
CREATE TABLE IF NOT EXISTS wms_pick_jobs (
    id          text PRIMARY KEY DEFAULT gen_random_uuid()::text,
    status      text NOT NULL DEFAULT 'open',
    created_at  timestamptz NOT NULL DEFAULT now()
);
INSERT INTO wms_pick_jobs (status)
SELECT 'open' FROM generate_series(1, 4)
WHERE NOT EXISTS (SELECT 1 FROM wms_pick_jobs WHERE status = 'open');
