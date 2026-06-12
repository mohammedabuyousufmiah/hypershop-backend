-- Demo return requests so the admin "Returns (RMA)" queue isn't empty.
-- Returns normally appear only after customers file RMAs. Idempotent
-- (fixed codes + ON CONFLICT). Attaches to real orders.
DO $$
DECLARE
  r RECORD;
  i int := 0;
  statuses text[] := ARRAY['requested','requested','received','inspected'];
  codes    text[] := ARRAY['RMA-DEMO01','RMA-DEMO02','RMA-DEMO03','RMA-DEMO04'];
  reasons  text[] := ARRAY[
    'Item arrived damaged.',
    'Wrong size delivered.',
    'Changed my mind — unused.',
    'Defective on arrival.'];
BEGIN
  FOR r IN
    SELECT id AS order_id, customer_user_id
    FROM orders
    WHERE customer_user_id IS NOT NULL
    ORDER BY placed_at DESC
    LIMIT 4
  LOOP
    i := i + 1;
    INSERT INTO return_requests
      (id, code, order_id, customer_user_id, status, reason, requested_at,
       received_at, inspected_at)
    VALUES (
      gen_random_uuid(), codes[i], r.order_id, r.customer_user_id,
      statuses[i], reasons[i], now() - (i || ' hours')::interval,
      CASE WHEN statuses[i] IN ('received','inspected') THEN now() - interval '30 min' ELSE NULL END,
      CASE WHEN statuses[i] = 'inspected' THEN now() - interval '15 min' ELSE NULL END
    )
    ON CONFLICT (code) DO NOTHING;
  END LOOP;
END $$;
