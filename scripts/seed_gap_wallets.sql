-- ============================================================================
-- seed_gap_wallets.sql
--
-- Idempotent seed for the admin Growth panel "Wallets" tab. Creates (if absent)
-- the two tables the read gap router (app/modules/wallet/api/wallets_gap.py)
-- SELECTs from, then inserts a handful of realistic Bangladeshi demo rows so
-- the existing GET endpoints return REAL data unchanged:
--
--   GET /wallets                    -> reads hypershop_wallets
--   GET /wallets/{id}               -> reads hypershop_wallets
--   GET /wallets/{id}/transactions  -> reads hypershop_wallet_txns
--
-- Column names MUST match exactly what those SELECTs reference. Money is stored
-- in minor units (poisha) as the router converts minor -> '12.34'.
--
-- Pure Postgres. Safe to run repeatedly (CREATE ... IF NOT EXISTS +
-- INSERT ... ON CONFLICT DO NOTHING with fixed UUIDs).
-- ============================================================================

-- --- Wallets ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS hypershop_wallets (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    customer_user_id uuid NOT NULL,
    currency         text NOT NULL DEFAULT 'BDT',
    balance_minor    bigint NOT NULL DEFAULT 0,
    status           text NOT NULL DEFAULT 'ACTIVE',
    last_activity_at timestamptz,
    created_at       timestamptz NOT NULL DEFAULT now()
);

-- --- Wallet transactions (ledger) -------------------------------------------
CREATE TABLE IF NOT EXISTS hypershop_wallet_txns (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    wallet_id           uuid NOT NULL,
    kind                text NOT NULL,            -- 'credit' | 'debit' | 'adjust'
    amount_minor        bigint NOT NULL DEFAULT 0,
    balance_after_minor bigint NOT NULL DEFAULT 0,
    source_type         text,
    source_id           uuid,
    memo                text,
    created_at          timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_hypershop_wallet_txns_wallet_id
    ON hypershop_wallet_txns (wallet_id);

-- --- Demo wallets -----------------------------------------------------------
INSERT INTO hypershop_wallets
    (id, customer_user_id, currency, balance_minor, status,
     last_activity_at, created_at)
VALUES
    ('a1111111-1111-4111-8111-111111111111',
     'c1111111-1111-4111-8111-111111111111', 'BDT', 152000, 'ACTIVE',
     now() - interval '2 hours',  now() - interval '120 days'),
    ('a2222222-2222-4222-8222-222222222222',
     'c2222222-2222-4222-8222-222222222222', 'BDT', 48050,  'ACTIVE',
     now() - interval '1 day',    now() - interval '90 days'),
    ('a3333333-3333-4333-8333-333333333333',
     'c3333333-3333-4333-8333-333333333333', 'BDT', 0,      'FROZEN',
     now() - interval '6 days',   now() - interval '200 days'),
    ('a4444444-4444-4444-8444-444444444444',
     'c4444444-4444-4444-8444-444444444444', 'BDT', 999900, 'ACTIVE',
     now() - interval '30 minutes', now() - interval '15 days'),
    ('a5555555-5555-4555-8555-555555555555',
     'c5555555-5555-4555-8555-555555555555', 'BDT', 25000,  'CLOSED',
     now() - interval '45 days',  now() - interval '365 days'),
    ('a6666666-6666-4666-8666-666666666666',
     'c6666666-6666-4666-8666-666666666666', 'BDT', 73450,  'ACTIVE',
     now() - interval '3 days',   now() - interval '60 days')
ON CONFLICT (id) DO NOTHING;

-- --- Demo ledger transactions -----------------------------------------------
-- Wallet a1 (Tk 1,520.00): refund credit, checkout debit, goodwill credit.
INSERT INTO hypershop_wallet_txns
    (id, wallet_id, kind, amount_minor, balance_after_minor,
     source_type, source_id, memo, created_at)
VALUES
    ('b1111111-1111-4111-8111-111111111101',
     'a1111111-1111-4111-8111-111111111111', 'credit', 200000, 200000,
     'order_refund', NULL, 'Refund for cancelled order — Dhaka', now() - interval '40 days'),
    ('b1111111-1111-4111-8111-111111111102',
     'a1111111-1111-4111-8111-111111111111', 'debit', 80000, 120000,
     'checkout', NULL, 'Applied at checkout', now() - interval '20 days'),
    ('b1111111-1111-4111-8111-111111111103',
     'a1111111-1111-4111-8111-111111111111', 'adjust', 32000, 152000,
     'goodwill', NULL, 'Goodwill credit — late delivery, Gulshan', now() - interval '2 hours'),
    -- Wallet a2 (Tk 480.50): cashback + small checkout debit.
    ('b2222222-2222-4222-8222-222222222201',
     'a2222222-2222-4222-8222-222222222222', 'credit', 60000, 60000,
     'cashback', NULL, 'Eid campaign cashback', now() - interval '12 days'),
    ('b2222222-2222-4222-8222-222222222202',
     'a2222222-2222-4222-8222-222222222222', 'debit', 11950, 48050,
     'checkout', NULL, 'Applied at checkout — Chattogram', now() - interval '1 day'),
    -- Wallet a4 (Tk 9,999.00): promo credit (corporate top-up).
    ('b4444444-4444-4444-8444-444444444401',
     'a4444444-4444-4444-8444-444444444444', 'credit', 999900, 999900,
     'promo', NULL, 'Corporate gift voucher load', now() - interval '15 days'),
    -- Wallet a6 (Tk 734.50): referral credit + adjust.
    ('b6666666-6666-4666-8666-666666666601',
     'a6666666-6666-4666-8666-666666666666', 'credit', 50000, 50000,
     'referral', NULL, 'Referral reward — invited friend', now() - interval '20 days'),
    ('b6666666-6666-4666-8666-666666666602',
     'a6666666-6666-4666-8666-666666666666', 'adjust', 23450, 73450,
     'goodwill', NULL, 'Service recovery credit — Sylhet', now() - interval '3 days')
ON CONFLICT (id) DO NOTHING;
