-- Demo data A2: homepage banners, static pages, loyalty accounts,
-- marketing campaigns, accounting periods. Idempotent.
DO $$
DECLARE
  u1 uuid; u2 uuid; v_seller uuid;
  aud uuid := 'f5550001-0000-4000-8000-000000000001';
BEGIN
  SELECT id INTO u1 FROM users ORDER BY created_at LIMIT 1;
  SELECT id INTO u2 FROM users ORDER BY created_at OFFSET 1 LIMIT 1;
  SELECT id INTO v_seller FROM sellers WHERE slug <> 'hypershop-direct' ORDER BY created_at LIMIT 1;

  -- 6) Ad campaigns (powers /admin/growth/campaigns; FK seller_id)
  IF v_seller IS NOT NULL THEN
    INSERT INTO hypershop_ad_campaigns (id, seller_id, name, status, daily_budget_minor, total_spent_minor, today_spent_minor)
    VALUES
      ('f6660001-0000-4000-8000-000000000001', v_seller, 'Eid Sponsored Products', 'active', 500000, 1240000, 38000),
      ('f6660002-0000-4000-8000-000000000002', v_seller, 'New Arrivals Boost',      'paused', 200000, 90000,  0),
      ('f6660003-0000-4000-8000-000000000003', v_seller, 'Clearance Push',          'ended',  100000, 100000, 0)
    ON CONFLICT (id) DO NOTHING;
  END IF;

  -- marketing campaigns need an audience (FK -> marketing_audiences)
  INSERT INTO marketing_audiences (id, name, rules, estimated_count, is_active)
  VALUES (aud, 'All active customers', '{"type":"all"}'::jsonb, 1240, true)
  ON CONFLICT (id) DO NOTHING;

  -- 1) Homepage banners
  INSERT INTO homepage_banners (id, title, subtitle, image_url, target_url, alt_text, is_active, sort_order)
  VALUES
    ('f1110001-0000-4000-8000-000000000001','Eid Mega Sale','Up to 60% off','/banners/eid.jpg','/sale/eid','Eid sale', true, 1),
    ('f1110002-0000-4000-8000-000000000002','New Arrivals','Fresh drops weekly','/banners/new.jpg','/new', 'New arrivals', true, 2),
    ('f1110003-0000-4000-8000-000000000003','Free Delivery','Orders over ৳1000','/banners/free.jpg','/info/delivery','Free delivery', false, 3)
  ON CONFLICT (id) DO NOTHING;

  -- 2) Static pages
  INSERT INTO storefront_static_pages (id, slug, title_en, title_bn, body_md_en, body_md_bn, is_published, show_in_footer, sort_order)
  VALUES
    ('f2220001-0000-4000-8000-000000000001','about-us','About Us','আমাদের সম্পর্কে','# About Hypershop\nBangladesh''s marketplace.','# হাইপারশপ','t', true, 1),
    ('f2220002-0000-4000-8000-000000000002','return-policy','Return Policy','রিটার্ন নীতি','# Returns\n7-day returns.','# রিটার্ন','t', true, 2),
    ('f2220003-0000-4000-8000-000000000003','privacy','Privacy Policy','গোপনীয়তা','# Privacy','# গোপনীয়তা', false, false, 3)
  ON CONFLICT (slug) DO NOTHING;

  -- 3) Loyalty accounts
  IF u1 IS NOT NULL THEN
    INSERT INTO loyalty_accounts (user_id, balance_points, lifetime_earned_points, tier)
    VALUES (u1, 2450, 8200, 'GOLD') ON CONFLICT (user_id) DO NOTHING;
  END IF;
  IF u2 IS NOT NULL THEN
    INSERT INTO loyalty_accounts (user_id, balance_points, lifetime_earned_points, tier)
    VALUES (u2, 320, 320, 'BRONZE') ON CONFLICT (user_id) DO NOTHING;
  END IF;

  -- 4) Marketing campaigns (audience_id FK -> customer_segments)
  IF aud IS NOT NULL THEN
    INSERT INTO marketing_campaigns (id, name, audience_id, channel, template_subject, template_body, status, sent_count, delivered_count, failed_count)
    VALUES
      ('f3330001-0000-4000-8000-000000000001','Eid Blast — VIP', aud,'email','Eid offers inside','Dear {{name}}, enjoy 20% off this Eid.','sent',    420, 410, 10),
      ('f3330002-0000-4000-8000-000000000002','Win-back SMS',    aud,'sms', NULL,'We miss you! ৳200 off your next order.','scheduled', 0, 0, 0),
      ('f3330003-0000-4000-8000-000000000003','App push promo',  aud,'in_app',NULL,'Flash sale live now 🔥','draft', 0, 0, 0)
    ON CONFLICT (id) DO NOTHING;
  END IF;

  -- 5) Accounting periods (status: open/locked)
  INSERT INTO fin_accounting_periods (id, year, month, starts_on, ends_on, status)
  VALUES
    ('f4440001-0000-4000-8000-000000000001', 2026, 4, '2026-04-01','2026-04-30','locked'),
    ('f4440002-0000-4000-8000-000000000002', 2026, 5, '2026-05-01','2026-05-31','locked'),
    ('f4440003-0000-4000-8000-000000000003', 2026, 6, '2026-06-01','2026-06-30','open')
  ON CONFLICT (id) DO NOTHING;
END $$;
