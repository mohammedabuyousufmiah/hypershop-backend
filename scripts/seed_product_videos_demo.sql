-- Demo product videos in 'ready_for_review' so the admin moderation queue
-- (default filter) isn't empty. Idempotent (fixed UUIDs).
DO $$
DECLARE
  prods uuid[];
  v_seller uuid;
BEGIN
  SELECT array_agg(id) INTO prods FROM (SELECT id FROM products ORDER BY created_at LIMIT 3) p;
  SELECT id INTO v_seller FROM sellers WHERE slug <> 'hypershop-direct' ORDER BY created_at LIMIT 1;
  IF prods IS NULL OR array_length(prods,1) < 3 THEN RETURN; END IF;

  INSERT INTO product_videos
    (id, product_id, seller_id, title, status, hls_url, thumbnail_url,
     duration_seconds, file_size_bytes, created_at)
  VALUES
    ('eeee0001-0000-4000-8000-000000000001', prods[1], v_seller, 'Unboxing & fit demo',
     'ready_for_review', '/videos/demo1/master.m3u8', '/videos/demo1/thumb.jpg', 42, 8388608, now() - interval '2 hours'),
    ('eeee0002-0000-4000-8000-000000000002', prods[2], v_seller, 'Fabric close-up',
     'ready_for_review', '/videos/demo2/master.m3u8', '/videos/demo2/thumb.jpg', 28, 5242880, now() - interval '1 hour'),
    ('eeee0003-0000-4000-8000-000000000003', prods[3], v_seller, '360 product spin',
     'ready_for_review', '/videos/demo3/master.m3u8', '/videos/demo3/thumb.jpg', 35, 6291456, now() - interval '30 min')
  ON CONFLICT (id) DO NOTHING;
END $$;
