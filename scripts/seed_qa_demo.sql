-- Demo product Q&A so the admin "Product Q&A" queue isn't empty.
-- Questions (pending/approved) + answers (pending/seller). Idempotent.
DO $$
DECLARE
  v_cust uuid;
  prods uuid[];
  q1 uuid := 'dddd0001-0000-4000-8000-000000000001';
  q2 uuid := 'dddd0002-0000-4000-8000-000000000002';
  q3 uuid := 'dddd0003-0000-4000-8000-000000000003';
BEGIN
  SELECT customer_user_id INTO v_cust FROM orders WHERE customer_user_id IS NOT NULL LIMIT 1;
  SELECT array_agg(id) INTO prods FROM (SELECT id FROM products ORDER BY created_at LIMIT 3) p;
  IF v_cust IS NULL OR prods IS NULL OR array_length(prods,1) < 3 THEN RETURN; END IF;

  INSERT INTO product_questions (id, product_id, customer_id, body, status, created_at)
  VALUES
    (q1, prods[1], v_cust, 'Is this fabric machine-washable?',        'pending',  now() - interval '3 hours'),
    (q2, prods[2], v_cust, 'Does it come in size XL?',                 'pending',  now() - interval '2 hours'),
    (q3, prods[3], v_cust, 'What is the warranty period?',            'approved', now() - interval '1 day')
  ON CONFLICT (id) DO NOTHING;

  INSERT INTO product_answers (id, question_id, customer_id, body, status, is_seller_answer, created_at)
  VALUES
    ('dddd1001-0000-4000-8000-000000000001', q3, v_cust, 'Yes — 1 year manufacturer warranty.', 'pending',  true,  now() - interval '20 hours'),
    ('dddd1002-0000-4000-8000-000000000002', q3, v_cust, 'Mine came with a warranty card.',      'approved', false, now() - interval '18 hours')
  ON CONFLICT (id) DO NOTHING;
END $$;
