-- =============================================================================
-- seed_gap_referrals.sql  —  idempotent demo seed for the Phase-4 Referrals
-- admin surface (AdminGrowthClient -> Referrals tab).
--
-- The Phase-4 referral shape the FE/GET-gap router expects (referrer/invitee +
-- 8-state status + reward fanout) is not backed by a table in this build. This
-- script creates the two backing tables with columns matching EXACTLY what
-- app/modules/referrals/api/referrals_gap.py SELECTs, then inserts realistic
-- Bangladeshi demo rows so the existing GET endpoints return real data.
--
-- Pure Postgres, fully idempotent, safe to run repeatedly:
--   psql "$DATABASE_URL" -f scripts/seed_gap_referrals.sql
-- =============================================================================

-- ---------------------------------------------------------------------------
-- referrals  (columns mirror referrals_gap.py list/detail SELECTs)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS referrals (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    referrer_customer_id uuid NOT NULL,
    code                 text NOT NULL,
    invitee_email        text,
    invitee_phone        text,
    invitee_customer_id  uuid,
    status               text NOT NULL DEFAULT 'CREATED',
    qualifying_order_id  uuid,
    invited_at           timestamptz,
    signed_up_at         timestamptz,
    qualified_at         timestamptz,
    rewarded_at          timestamptz,
    rejected_at          timestamptz,
    reversed_at          timestamptz,
    rejection_reason     text,
    actor_id             uuid,
    created_at           timestamptz NOT NULL DEFAULT now(),
    updated_at           timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_referrals_code ON referrals (code);
CREATE INDEX IF NOT EXISTS ix_referrals_status ON referrals (status);
CREATE INDEX IF NOT EXISTS ix_referrals_referrer ON referrals (referrer_customer_id);

-- ---------------------------------------------------------------------------
-- referral_rewards  (columns mirror referrals_gap.py reward fanout SELECT;
-- idempotency_key supports the actions router's ON CONFLICT grant)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS referral_rewards (
    id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    referral_id             uuid NOT NULL,
    beneficiary             text NOT NULL,          -- REFERRER | INVITEE
    kind                    text NOT NULL,          -- WALLET_CREDIT | LOYALTY_POINTS | COUPON
    beneficiary_customer_id uuid NOT NULL,
    amount                  numeric(18, 2),
    currency                text,
    points                  integer,
    coupon_id               uuid,
    status                  text NOT NULL DEFAULT 'PENDING',  -- PENDING | GRANTED | REVERSED
    ledger_reference_type   text,
    ledger_reference_id     uuid,
    granted_at              timestamptz,
    reversed_at             timestamptz,
    reversal_reason         text,
    actor_id                uuid,
    idempotency_key         text,
    created_at              timestamptz NOT NULL DEFAULT now(),
    updated_at              timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_referral_rewards_idem
    ON referral_rewards (idempotency_key)
    WHERE idempotency_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_referral_rewards_referral
    ON referral_rewards (referral_id);

-- ---------------------------------------------------------------------------
-- Demo referrals — stable UUIDs so re-running is a no-op (ON CONFLICT).
-- Bangladeshi customers / codes / phone numbers (+8801…) / BDT rewards.
-- Covers the full 8-state status spectrum the FE filters by.
-- ---------------------------------------------------------------------------
INSERT INTO referrals (
    id, referrer_customer_id, code, invitee_email, invitee_phone,
    invitee_customer_id, status, qualifying_order_id, invited_at, signed_up_at,
    qualified_at, rewarded_at, rejected_at, reversed_at, rejection_reason,
    actor_id, created_at, updated_at
) VALUES
    -- REWARDED — full happy path, Dhaka
    ('a1000000-0000-4000-8000-000000000001',
     'c1000000-0000-4000-8000-000000000001', 'RAFI2026',
     'tahmina.akter@gmail.com', '+8801711000111',
     'c2000000-0000-4000-8000-000000000001', 'REWARDED',
     '03000000-0000-4000-8000-000000000001',
     now() - interval '20 days', now() - interval '18 days',
     now() - interval '15 days', now() - interval '14 days',
     NULL, NULL, NULL, NULL,
     now() - interval '21 days', now() - interval '14 days'),

    -- QUALIFIED_ORDER — awaiting reward grant, Chattogram
    ('a1000000-0000-4000-8000-000000000002',
     'c1000000-0000-4000-8000-000000000002', 'SUMAIYA50',
     'arif.hossain@yahoo.com', '+8801812000222',
     'c2000000-0000-4000-8000-000000000002', 'QUALIFIED_ORDER',
     '03000000-0000-4000-8000-000000000002',
     now() - interval '9 days', now() - interval '7 days',
     now() - interval '2 days', NULL, NULL, NULL, NULL, NULL,
     now() - interval '10 days', now() - interval '2 days'),

    -- REWARD_PENDING — grant in flight, Sylhet
    ('a1000000-0000-4000-8000-000000000003',
     'c1000000-0000-4000-8000-000000000003', 'NADIASHARE',
     'mizan.rahman@outlook.com', '+8801913000333',
     'c2000000-0000-4000-8000-000000000003', 'REWARD_PENDING',
     '03000000-0000-4000-8000-000000000003',
     now() - interval '6 days', now() - interval '5 days',
     now() - interval '1 day', NULL, NULL, NULL, NULL, NULL,
     now() - interval '6 days', now() - interval '1 day'),

    -- SIGNED_UP — invitee joined, no qualifying order yet, Khulna
    ('a1000000-0000-4000-8000-000000000004',
     'c1000000-0000-4000-8000-000000000004', 'JOYBD100',
     'farhana.islam@gmail.com', '+8801614000444',
     'c2000000-0000-4000-8000-000000000004', 'SIGNED_UP',
     NULL, now() - interval '4 days', now() - interval '3 days',
     NULL, NULL, NULL, NULL, NULL, NULL,
     now() - interval '4 days', now() - interval '3 days'),

    -- INVITED — invite sent, not yet signed up, Rajshahi
    ('a1000000-0000-4000-8000-000000000005',
     'c1000000-0000-4000-8000-000000000005', 'SHOPKORO',
     'rakib.ahmed@gmail.com', '+8801515000555',
     NULL, 'INVITED', NULL, now() - interval '2 days', NULL,
     NULL, NULL, NULL, NULL, NULL, NULL,
     now() - interval '2 days', now() - interval '2 days'),

    -- CREATED — code minted, no invite sent, Barishal
    ('a1000000-0000-4000-8000-000000000006',
     'c1000000-0000-4000-8000-000000000006', 'NOTUNUSER',
     NULL, NULL, NULL, 'CREATED', NULL, NULL, NULL,
     NULL, NULL, NULL, NULL, NULL, NULL,
     now() - interval '1 day', now() - interval '1 day'),

    -- REJECTED — self-referral abuse caught, Rangpur
    ('a1000000-0000-4000-8000-000000000007',
     'c1000000-0000-4000-8000-000000000007', 'FAKEINVITE',
     'suspicious.user@tempmail.com', '+8801716000666',
     'c2000000-0000-4000-8000-000000000007', 'REJECTED',
     NULL, now() - interval '12 days', now() - interval '11 days',
     NULL, NULL, now() - interval '10 days', NULL,
     'Self-referral detected: same device fingerprint and bKash number',
     'c9000000-0000-4000-8000-0000000000aa',
     now() - interval '12 days', now() - interval '10 days'),

    -- REVERSED — reward clawed back after refund, Mymensingh
    ('a1000000-0000-4000-8000-000000000008',
     'c1000000-0000-4000-8000-000000000008', 'CASHBACK25',
     'naimur.kabir@gmail.com', '+8801817000777',
     'c2000000-0000-4000-8000-000000000008', 'REVERSED',
     '03000000-0000-4000-8000-000000000008',
     now() - interval '30 days', now() - interval '28 days',
     now() - interval '25 days', now() - interval '24 days',
     NULL, now() - interval '8 days',
     'Qualifying order fully refunded — reward reversed',
     'c9000000-0000-4000-8000-0000000000aa',
     now() - interval '30 days', now() - interval '8 days')
ON CONFLICT (id) DO NOTHING;

-- ---------------------------------------------------------------------------
-- Demo reward fanout rows for the REWARDED / REVERSED referrals.
-- ---------------------------------------------------------------------------
INSERT INTO referral_rewards (
    id, referral_id, beneficiary, kind, beneficiary_customer_id, amount,
    currency, points, coupon_id, status, ledger_reference_type,
    ledger_reference_id, granted_at, reversed_at, reversal_reason, actor_id,
    idempotency_key, created_at, updated_at
) VALUES
    -- REWARDED referral #1 — referrer wallet credit (granted)
    ('b1000000-0000-4000-8000-000000000001',
     'a1000000-0000-4000-8000-000000000001', 'REFERRER', 'WALLET_CREDIT',
     'c1000000-0000-4000-8000-000000000001', 100.00, 'BDT', NULL, NULL,
     'GRANTED', 'WALLET_TXN', 'd1000000-0000-4000-8000-000000000001',
     now() - interval '14 days', NULL, NULL, NULL,
     'seed-reward-a1-referrer',
     now() - interval '14 days', now() - interval '14 days'),

    -- REWARDED referral #1 — invitee loyalty points (granted)
    ('b1000000-0000-4000-8000-000000000002',
     'a1000000-0000-4000-8000-000000000001', 'INVITEE', 'LOYALTY_POINTS',
     'c2000000-0000-4000-8000-000000000001', NULL, NULL, 500, NULL,
     'GRANTED', 'LOYALTY_TXN', 'd1000000-0000-4000-8000-000000000002',
     now() - interval '14 days', NULL, NULL, NULL,
     'seed-reward-a1-invitee',
     now() - interval '14 days', now() - interval '14 days'),

    -- REVERSED referral #8 — referrer wallet credit (reversed)
    ('b1000000-0000-4000-8000-000000000003',
     'a1000000-0000-4000-8000-000000000008', 'REFERRER', 'WALLET_CREDIT',
     'c1000000-0000-4000-8000-000000000008', 250.00, 'BDT', NULL, NULL,
     'REVERSED', 'WALLET_TXN', 'd1000000-0000-4000-8000-000000000003',
     now() - interval '24 days', now() - interval '8 days',
     'Qualifying order refunded', 'c9000000-0000-4000-8000-0000000000aa',
     'seed-reward-a8-referrer',
     now() - interval '24 days', now() - interval '8 days'),

    -- REVERSED referral #8 — invitee coupon (reversed)
    ('b1000000-0000-4000-8000-000000000004',
     'a1000000-0000-4000-8000-000000000008', 'INVITEE', 'COUPON',
     'c2000000-0000-4000-8000-000000000008', NULL, NULL, NULL,
     'e1000000-0000-4000-8000-000000000001',
     'REVERSED', 'COUPON', 'e1000000-0000-4000-8000-000000000001',
     now() - interval '24 days', now() - interval '8 days',
     'Qualifying order refunded', 'c9000000-0000-4000-8000-0000000000aa',
     'seed-reward-a8-invitee',
     now() - interval '24 days', now() - interval '8 days')
ON CONFLICT (id) DO NOTHING;
