-- House first-party seller ("Hypershop Direct", slug hypershop-direct).
-- Normally seeded by migration 0033; demo DBs that skipped it 404 on
-- admin "Create product" (resolve_owner_seller_id needs this row).
-- Idempotent.
INSERT INTO sellers (business_name, slug, status, commission_percent)
VALUES ('Hypershop Direct', 'hypershop-direct', 'approved', 0.00)
ON CONFLICT (slug) DO UPDATE SET status = 'approved';
