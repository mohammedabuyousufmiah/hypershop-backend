-- Demo bulk-upload jobs so the admin "Bulk Upload Jobs" page isn't empty.
-- Jobs normally appear only after real seller CSV uploads. Idempotent
-- (fixed UUIDs + ON CONFLICT). Attaches to first non-house seller.
DO $$
DECLARE
  v_seller uuid;
  v_user   uuid;
  j_done   uuid := 'bbbb0001-0000-4000-8000-000000000001';
  j_fail   uuid := 'bbbb0002-0000-4000-8000-000000000002';
  j_queue  uuid := 'bbbb0003-0000-4000-8000-000000000003';
BEGIN
  SELECT id INTO v_seller FROM sellers WHERE slug <> 'hypershop-direct' ORDER BY created_at LIMIT 1;
  SELECT id INTO v_user FROM users ORDER BY created_at LIMIT 1;
  IF v_seller IS NULL OR v_user IS NULL THEN RETURN; END IF;

  INSERT INTO hypershop_bulk_upload_jobs
    (id, seller_id, uploaded_by_user_id, original_filename, file_url, file_size_bytes,
     file_format, total_rows, processed_rows, succeeded_rows, failed_rows, status,
     error_summary, started_at, finished_at)
  VALUES
    (j_done, v_seller, v_user, 'spring_catalog.csv', '/uploads/bulk/spring_catalog.csv',
     184320, 'csv', 120, 120, 120, 0, 'completed', NULL,
     now() - interval '2 hours', now() - interval '1 hour 55 min'),
    (j_fail, v_seller, v_user, 'winter_drop.xlsx', '/uploads/bulk/winter_drop.xlsx',
     262144, 'xlsx', 80, 80, 60, 20, 'failed',
     '{"top_errors": [{"code": "missing_price", "count": 12}, {"code": "bad_category", "count": 8}]}'::jsonb,
     now() - interval '40 min', now() - interval '36 min'),
    (j_queue, v_seller, v_user, 'restock_june.tsv', '/uploads/bulk/restock_june.tsv',
     98304, 'tsv', 0, 0, 0, 0, 'queued', NULL, NULL, NULL)
  ON CONFLICT (id) DO NOTHING;

  -- A few error rows for the failed job (powers "inspect error rows").
  INSERT INTO hypershop_bulk_upload_rows (job_id, row_number, raw_row, error_code, error_message)
  VALUES
    (j_fail, 7,  '{"name":"Cotton Kurta","price":""}'::jsonb,        'missing_price', 'Price is required.'),
    (j_fail, 12, '{"name":"Silk Scarf","category":"unknown-cat"}'::jsonb, 'bad_category',  'Category "unknown-cat" not found.'),
    (j_fail, 19, '{"name":"Wool Cap","price":"-50"}'::jsonb,         'invalid_price', 'Price must be >= 0.'),
    (j_fail, 23, '{"name":"","price":"300"}'::jsonb,                 'missing_name',  'Product name is required.')
  ON CONFLICT DO NOTHING;
END $$;
