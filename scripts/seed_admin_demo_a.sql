-- Demo data for 6 admin pages (Option A): coupons, gift-cards, feature-flags,
-- disputes, support tickets, customer segments. Idempotent.
DO $$
DECLARE
  v_user uuid; v_order uuid; v_seller uuid; v_oitem uuid;
BEGIN
  SELECT customer_user_id INTO v_user  FROM orders WHERE customer_user_id IS NOT NULL ORDER BY placed_at DESC LIMIT 1;
  SELECT id INTO v_order FROM orders WHERE customer_user_id IS NOT NULL ORDER BY placed_at DESC LIMIT 1;
  SELECT id INTO v_seller FROM sellers WHERE slug <> 'hypershop-direct' ORDER BY created_at LIMIT 1;

  -- 1) Coupons (discount_value_minor: PERCENT = whole-pct×100; FIXED = minor units)
  INSERT INTO coupons (code, description, discount_type, discount_value_minor, min_subtotal_minor, max_discount_minor, max_total_uses, max_uses_per_customer, valid_until, is_active)
  VALUES
    ('EID2026',   'Eid 15% off',            'PERCENT', 1500,  50000,  300000, 1000, 1, now()+interval '60 days', true),
    ('FLAT200',   '৳200 off over ৳1500',    'FIXED',   20000, 150000, NULL,   500,  1, now()+interval '30 days', true),
    ('WELCOME10', 'New customer 10% off',   'PERCENT', 1000,  0,      100000, NULL, 1, now()+interval '90 days', true),
    ('EXPIRED5',  'Expired test 5%',        'PERCENT', 500,   0,      NULL,   100,  1, now()-interval '5 days',  false)
  ON CONFLICT (code) DO NOTHING;

  -- 2) Gift cards
  INSERT INTO gift_cards (code, face_value_minor, currency, status, expires_at)
  VALUES
    ('GC-DEMO-AAAA1111', 100000, 'BDT', 'active',   now()+interval '1 year'),
    ('GC-DEMO-BBBB2222', 50000,  'BDT', 'active',   now()+interval '1 year'),
    ('GC-DEMO-CCCC3333', 200000, 'BDT', 'active', now()+interval '1 year')
  ON CONFLICT (code) DO NOTHING;

  -- 3) Feature flags
  INSERT INTO feature_flags (key, description, is_enabled, rollout_percent)
  VALUES
    ('checkout_v2',        'New checkout flow',          true,  100),
    ('ai_search',          'AI semantic search ranking', true,  50),
    ('live_video_shopping','Live video shopping beta',   false, 0),
    ('one_click_reorder',  'One-click reorder button',   true,  100)
  ON CONFLICT (key) DO NOTHING;

  -- 4) Support tickets
  IF v_user IS NOT NULL THEN
    INSERT INTO support_tickets (id, customer_user_id, subject, body, category, priority, status, order_id)
    VALUES
      ('aaaa1111-0000-4000-8000-000000000001', v_user, 'Where is my order?',        'Order placed 3 days ago, no update.', 'delivery', 'high',   'open',        v_order),
      ('aaaa1111-0000-4000-8000-000000000002', v_user, 'Refund not received',       'Refund approved but not credited.',   'billing',  'urgent', 'open',        v_order),
      ('aaaa1111-0000-4000-8000-000000000003', v_user, 'How to change address?',    'Need to update delivery address.',    'general',  'normal', 'waiting_customer', NULL),
      ('aaaa1111-0000-4000-8000-000000000004', v_user, 'App crashes on checkout',   'Android app crashes at payment.',     'technical','high',   'resolved',    NULL)
    ON CONFLICT (id) DO NOTHING;
  END IF;

  -- 5) Disputes
  IF v_user IS NOT NULL AND v_order IS NOT NULL AND v_seller IS NOT NULL THEN
    INSERT INTO hypershop_disputes (id, order_id, opened_by_user_id, seller_id, dispute_type, status, amount_disputed_minor, subject, description)
    VALUES
      ('bbbb1111-0000-4000-8000-000000000001', v_order, v_user, v_seller, 'not_received',  'open',          250000, 'Package never arrived',     'Tracking shows delivered but I got nothing.'),
      ('bbbb1111-0000-4000-8000-000000000002', v_order, v_user, v_seller, 'damaged',       'under_review',  120000, 'Item arrived broken',       'Screen cracked in transit.'),
      ('bbbb1111-0000-4000-8000-000000000003', v_order, v_user, v_seller, 'quality_issue','awaiting_seller', 80000, 'Different color than listed', 'Ordered navy, got black.')
    ON CONFLICT (id) DO NOTHING;
  END IF;

  -- 6) Customer segments (rule jsonb required)
  INSERT INTO hypershop_customer_segments (code, name_en, name_bn, description, rule, estimated_size, is_active)
  VALUES
    ('vip',     'VIP',          'ভিআইপি',        'Top spenders',        '{"type":"rfm","r":5,"f":5,"m":5}'::jsonb, 42,  true),
    ('at_risk', 'At Risk',      'ঝুঁকিতে',        'Lapsing customers',   '{"type":"rfm","r":2,"f":4,"m":4}'::jsonb, 128, true),
    ('new',     'New Customers', 'নতুন ক্রেতা',    'First 30 days',       '{"type":"rfm","r":5,"f":1,"m":1}'::jsonb, 310, true),
    ('dormant', 'Dormant',      'নিষ্ক্রিয়',      'No order in 90 days', '{"type":"rfm","r":1,"f":2,"m":2}'::jsonb, 95,  true)
  ON CONFLICT (code) DO NOTHING;
END $$;
