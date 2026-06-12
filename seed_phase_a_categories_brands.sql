-- Hypershop Phase A — Step 1a: Categories + Brands seed
-- Matches CURRENT alembic schema (no path/depth/type columns).
-- Idempotent: uses WHERE NOT EXISTS for categories (composite unique
-- on (parent_id, slug) doesn't catch duplicate NULL parents), and
-- ON CONFLICT for brands (which has uq_brands_slug).
-- Bangladesh marketplace, BDT, narrow 8-root tree.

BEGIN;

-- =================================================================
-- 8 ROOT CATEGORIES
-- =================================================================
INSERT INTO categories (slug, name, description, sort_order, is_active)
SELECT v.slug, v.name, v.description, v.sort_order, true
FROM (VALUES
  ('electronics',   'Electronics & Gadgets',  'Laptops, TVs, audio, cameras and smart devices', 10),
  ('fashion',       'Fashion & Lifestyle',    'Men''s, women''s wear, footwear, bags and accessories', 20),
  ('home-kitchen',  'Home & Kitchen',         'Kitchenware, bedding, decor and home essentials', 30),
  ('beauty',        'Beauty & Personal Care', 'Skincare, haircare, makeup and grooming', 40),
  ('grocery',       'Grocery & Daily',        'Rice, atta, oil, spices, beverages and snacks', 50),
  ('baby-kids',     'Baby & Kids',            'Diapers, baby food, toys and kids fashion', 60),
  ('mobile',        'Mobile & Accessories',   'Smartphones, tablets and mobile accessories', 70),
  ('health',        'Health & Wellness',      'Supplements, fitness gear and wellness devices', 80)
) AS v(slug, name, description, sort_order)
WHERE NOT EXISTS (
  SELECT 1 FROM categories c
  WHERE c.parent_id IS NULL AND c.slug = v.slug
);

-- =================================================================
-- ~40 SUBCATEGORIES (5 per root)
-- =================================================================

-- 1) Electronics & Gadgets
INSERT INTO categories (parent_id, slug, name, sort_order, is_active)
SELECT (SELECT id FROM categories WHERE slug='electronics' AND parent_id IS NULL),
       v.slug, v.name, v.sort_order, true
FROM (VALUES
  ('laptops',        'Laptops',          10),
  ('televisions',    'Televisions',      20),
  ('audio-speakers', 'Audio & Speakers', 30),
  ('cameras',        'Cameras',          40),
  ('smart-watches',  'Smart Watches',    50)
) AS v(slug, name, sort_order)
ON CONFLICT (parent_id, slug) DO NOTHING;

-- 2) Fashion & Lifestyle
INSERT INTO categories (parent_id, slug, name, sort_order, is_active)
SELECT (SELECT id FROM categories WHERE slug='fashion' AND parent_id IS NULL),
       v.slug, v.name, v.sort_order, true
FROM (VALUES
  ('mens-wear',     'Men''s Wear',     10),
  ('womens-wear',   'Women''s Wear',   20),
  ('footwear',      'Footwear',        30),
  ('bags-luggage',  'Bags & Luggage',  40),
  ('watches',       'Watches',         50)
) AS v(slug, name, sort_order)
ON CONFLICT (parent_id, slug) DO NOTHING;

-- 3) Home & Kitchen
INSERT INTO categories (parent_id, slug, name, sort_order, is_active)
SELECT (SELECT id FROM categories WHERE slug='home-kitchen' AND parent_id IS NULL),
       v.slug, v.name, v.sort_order, true
FROM (VALUES
  ('kitchenware', 'Kitchenware', 10),
  ('bedding',     'Bedding',     20),
  ('cleaning',    'Cleaning',    30),
  ('storage',     'Storage',     40),
  ('home-decor',  'Home Decor',  50)
) AS v(slug, name, sort_order)
ON CONFLICT (parent_id, slug) DO NOTHING;

-- 4) Beauty & Personal Care
INSERT INTO categories (parent_id, slug, name, sort_order, is_active)
SELECT (SELECT id FROM categories WHERE slug='beauty' AND parent_id IS NULL),
       v.slug, v.name, v.sort_order, true
FROM (VALUES
  ('skincare',       'Skincare',         10),
  ('haircare',       'Haircare',         20),
  ('makeup',         'Makeup',           30),
  ('fragrance',      'Fragrance',        40),
  ('mens-grooming',  'Men''s Grooming',  50)
) AS v(slug, name, sort_order)
ON CONFLICT (parent_id, slug) DO NOTHING;

-- 5) Grocery & Daily
INSERT INTO categories (parent_id, slug, name, sort_order, is_active)
SELECT (SELECT id FROM categories WHERE slug='grocery' AND parent_id IS NULL),
       v.slug, v.name, v.sort_order, true
FROM (VALUES
  ('rice-atta',    'Rice & Atta',    10),
  ('cooking-oil',  'Cooking Oil',    20),
  ('snacks',       'Snacks',         30),
  ('beverages',    'Beverages',      40),
  ('spices',       'Spices',         50)
) AS v(slug, name, sort_order)
ON CONFLICT (parent_id, slug) DO NOTHING;

-- 6) Baby & Kids
INSERT INTO categories (parent_id, slug, name, sort_order, is_active)
SELECT (SELECT id FROM categories WHERE slug='baby-kids' AND parent_id IS NULL),
       v.slug, v.name, v.sort_order, true
FROM (VALUES
  ('diapers-wipes', 'Diapers & Wipes', 10),
  ('baby-food',     'Baby Food',       20),
  ('toys',          'Toys',            30),
  ('kids-clothing', 'Kids Clothing',   40),
  ('school',        'School Supplies', 50)
) AS v(slug, name, sort_order)
ON CONFLICT (parent_id, slug) DO NOTHING;

-- 7) Mobile & Accessories
INSERT INTO categories (parent_id, slug, name, sort_order, is_active)
SELECT (SELECT id FROM categories WHERE slug='mobile' AND parent_id IS NULL),
       v.slug, v.name, v.sort_order, true
FROM (VALUES
  ('smartphones', 'Smartphones',       10),
  ('tablets',     'Tablets',           20),
  ('power-banks', 'Power Banks',       30),
  ('earbuds',     'Earbuds',           40),
  ('chargers',    'Chargers & Cables', 50)
) AS v(slug, name, sort_order)
ON CONFLICT (parent_id, slug) DO NOTHING;

-- 8) Health & Wellness
INSERT INTO categories (parent_id, slug, name, sort_order, is_active)
SELECT (SELECT id FROM categories WHERE slug='health' AND parent_id IS NULL),
       v.slug, v.name, v.sort_order, true
FROM (VALUES
  ('supplements',      'Vitamins & Supplements', 10),
  ('fitness-gear',     'Fitness Gear',           20),
  ('personal-hygiene', 'Personal Hygiene',       30),
  ('first-aid',        'First Aid',              40),
  ('wellness-devices', 'Wellness Devices',       50)
) AS v(slug, name, sort_order)
ON CONFLICT (parent_id, slug) DO NOTHING;

-- =================================================================
-- 12 BRANDS (BD-friendly mix)
-- =================================================================
INSERT INTO brands (slug, name, description, is_active) VALUES
  ('walton',           'Walton',           'Bangladesh''s largest home-grown electronics manufacturer', true),
  ('samsung',          'Samsung',          'Global electronics and mobile leader', true),
  ('xiaomi',           'Xiaomi',           'Smart electronics with value-for-money positioning', true),
  ('lg',               'LG',               'Korean electronics and home appliances brand', true),
  ('pran',             'PRAN',             'Bangladesh''s leading food and beverage brand', true),
  ('aci',              'ACI',              'Bangladesh consumer goods and pharmaceuticals', true),
  ('square-toiletries','Square Toiletries','Bangladesh personal care manufacturer', true),
  ('dabur',            'Dabur',            'Ayurveda-rooted personal care and wellness', true),
  ('unilever',         'Unilever',         'Global FMCG brand with deep BD presence', true),
  ('nestle',           'Nestlé',           'Global food, beverage and nutrition brand', true),
  ('marico',           'Marico',           'Personal care and haircare leader in South Asia', true),
  ('aarong',           'Aarong',           'Premium Bangladeshi lifestyle and fashion brand', true)
ON CONFLICT (slug) DO NOTHING;

COMMIT;

-- Summary counts
SELECT 'category_roots' AS k, COUNT(*) AS n FROM categories WHERE parent_id IS NULL
UNION ALL SELECT 'category_subs',  COUNT(*) FROM categories WHERE parent_id IS NOT NULL
UNION ALL SELECT 'brands',         COUNT(*) FROM brands;
