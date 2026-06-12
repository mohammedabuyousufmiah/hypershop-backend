-- Demo product reviews so the admin "Reviews moderation" queue isn't empty.
-- Mix of pending (actionable) + approved/rejected. Idempotent (fixed UUIDs).
DO $$
DECLARE
  v_cust uuid;
  prods uuid[];
BEGIN
  SELECT customer_user_id INTO v_cust FROM orders WHERE customer_user_id IS NOT NULL LIMIT 1;
  -- One review per (customer, product) — use 5 DISTINCT products.
  SELECT array_agg(id) INTO prods FROM (SELECT id FROM products ORDER BY created_at LIMIT 5) p;
  IF v_cust IS NULL OR prods IS NULL OR array_length(prods,1) < 5 THEN RETURN; END IF;

  INSERT INTO product_reviews (id, product_id, customer_id, rating, title, body, status, created_at)
  VALUES
    ('cccc0001-0000-4000-8000-000000000001', prods[1], v_cust, 5, 'Excellent quality', 'Fabric is premium, fits perfectly. Highly recommend.', 'pending',  now() - interval '3 hours'),
    ('cccc0002-0000-4000-8000-000000000002', prods[2], v_cust, 2, 'Color faded',       'Color faded after first wash. Disappointed.',          'pending',  now() - interval '2 hours'),
    ('cccc0003-0000-4000-8000-000000000003', prods[3], v_cust, 4, 'Good value',         'Solid for the price. Delivery was quick.',             'pending',  now() - interval '1 hour'),
    ('cccc0004-0000-4000-8000-000000000004', prods[4], v_cust, 5, 'Bought again',       'Second purchase — consistent quality.',                'approved', now() - interval '2 days'),
    ('cccc0005-0000-4000-8000-000000000005', prods[5], v_cust, 1, 'Spam link inside',   'Visit my-spam-site dot com for cheaper!!!',            'rejected', now() - interval '1 day')
  ON CONFLICT (id) DO NOTHING;
END $$;
