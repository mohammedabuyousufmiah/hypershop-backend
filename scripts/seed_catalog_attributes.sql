-- ===========================================================================
-- Catalog attribute catalog — tables + comprehensive category-wise seed.
-- Idempotent: CREATE TABLE IF NOT EXISTS + ON CONFLICT DO NOTHING. Safe to
-- re-run. No alembic chain touch (gap-style; tables read by catalog gap router).
-- ===========================================================================

CREATE TABLE IF NOT EXISTS attribute_definitions (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    slug        text UNIQUE NOT NULL,
    name        text NOT NULL,
    description text,
    data_type   text NOT NULL DEFAULT 'STRING',
    is_active   boolean NOT NULL DEFAULT true,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS attribute_options (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    attribute_id  uuid NOT NULL REFERENCES attribute_definitions(id) ON DELETE CASCADE,
    value_code    text NOT NULL,
    display_label text NOT NULL,
    position      int NOT NULL DEFAULT 0,
    is_active     boolean NOT NULL DEFAULT true,
    UNIQUE (attribute_id, value_code)
);

CREATE TABLE IF NOT EXISTS category_attributes (
    id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    category_id            uuid NOT NULL,
    attribute_id           uuid NOT NULL REFERENCES attribute_definitions(id) ON DELETE CASCADE,
    is_required            boolean NOT NULL DEFAULT false,
    is_variant_axis        boolean NOT NULL DEFAULT false,
    inherit_to_descendants boolean NOT NULL DEFAULT true,
    created_at             timestamptz NOT NULL DEFAULT now(),
    UNIQUE (category_id, attribute_id)
);

-- ---------------------------------------------------------------------------
-- 1) Attribute definitions
-- ---------------------------------------------------------------------------
INSERT INTO attribute_definitions (slug, name, data_type, description) VALUES
  -- Universal
  ('brand',              'Brand',                'STRING',  'Manufacturer / brand name'),
  ('color',              'Color',                'ENUM',    'Primary product color'),
  ('weight-kg',          'Weight (kg)',          'DECIMAL', 'Net product weight in kilograms'),
  ('country-of-origin',  'Country of Origin',    'ENUM',    'Where the product was made'),
  ('warranty',           'Warranty',             'ENUM',    'Manufacturer warranty period'),
  ('pack-count',         'Pack / Piece Count',   'INTEGER', 'Units per pack'),
  ('material',           'Material',             'ENUM',    'Primary material'),
  -- Fashion
  ('clothing-size',      'Clothing Size',        'ENUM',    'Apparel size'),
  ('shoe-size',          'Shoe Size (EU)',       'ENUM',    'Footwear size (EU)'),
  ('fabric',             'Fabric',               'ENUM',    'Apparel fabric'),
  ('fit',                'Fit',                  'ENUM',    'Garment fit'),
  ('sleeve-length',      'Sleeve Length',        'ENUM',    'Sleeve length'),
  ('pattern',            'Pattern',              'ENUM',    'Surface pattern'),
  ('neck-style',         'Neck Style',           'ENUM',    'Neck / collar style'),
  ('gender',             'Gender',               'ENUM',    'Target gender'),
  -- Electronics / gadgets
  ('model',              'Model',                'STRING',  'Model name / number'),
  ('ram',                'RAM',                  'ENUM',    'Memory'),
  ('storage',            'Storage',              'ENUM',    'Internal storage'),
  ('battery-mah',        'Battery (mAh)',        'INTEGER', 'Battery capacity'),
  ('screen-size-inch',   'Screen Size (inch)',   'DECIMAL', 'Display diagonal'),
  ('operating-system',   'Operating System',     'ENUM',    'Device OS'),
  ('network',            'Network',              'ENUM',    'Cellular / network support'),
  ('sim-slots',          'SIM Slots',            'ENUM',    'SIM configuration'),
  ('connectivity',       'Connectivity',         'ENUM',    'Connectivity options'),
  ('power-watt',         'Power (W)',            'DECIMAL', 'Power rating in watts'),
  ('voltage',            'Voltage',              'ENUM',    'Operating voltage'),
  -- Home & kitchen
  ('capacity-litre',     'Capacity (L)',         'DECIMAL', 'Capacity in litres'),
  -- Beauty
  ('skin-type',          'Skin Type',            'ENUM',    'Suitable skin type'),
  ('volume-ml',          'Volume (ml)',          'DECIMAL', 'Volume in millilitres'),
  -- Health / grocery
  ('net-weight-g',       'Net Weight (g)',       'DECIMAL', 'Net weight in grams'),
  ('form',               'Form',                 'ENUM',    'Physical form'),
  ('flavor',             'Flavor',               'ENUM',    'Flavor variant'),
  ('organic',            'Organic',              'BOOLEAN', 'Certified organic'),
  ('best-before',        'Best Before',          'STRING',  'Best-before / expiry note'),
  -- Baby / toys
  ('age-group',          'Age Group',            'ENUM',    'Suitable age range'),
  ('battery-required',   'Battery Required',     'BOOLEAN', 'Needs batteries'),
  ('piece-count',        'Number of Pieces',     'INTEGER', 'Pieces in set'),
  -- Books & media
  ('author',             'Author',               'STRING',  'Author / creator'),
  ('language',           'Language',             'ENUM',    'Content language'),
  ('book-format',        'Format',               'ENUM',    'Book format'),
  ('pages',              'Pages',                'INTEGER', 'Page count'),
  ('publisher',          'Publisher',            'STRING',  'Publishing house'),
  -- Sports
  ('sport-type',         'Sport Type',           'ENUM',    'Associated sport'),
  -- Automotive
  ('compatibility',      'Vehicle Compatibility','STRING',  'Compatible vehicle make/model')
ON CONFLICT (slug) DO NOTHING;

-- ---------------------------------------------------------------------------
-- 2) Options for ENUM attributes (dropdown values)
-- ---------------------------------------------------------------------------
INSERT INTO attribute_options (attribute_id, value_code, display_label, position)
SELECT a.id, v.code, v.label, v.pos
FROM attribute_definitions a
JOIN (VALUES
  -- color
  ('color','black','Black',1),('color','white','White',2),('color','red','Red',3),
  ('color','blue','Blue',4),('color','green','Green',5),('color','yellow','Yellow',6),
  ('color','grey','Grey',7),('color','pink','Pink',8),('color','purple','Purple',9),
  ('color','brown','Brown',10),('color','beige','Beige',11),('color','navy','Navy',12),
  ('color','gold','Gold',13),('color','silver','Silver',14),('color','maroon','Maroon',15),
  ('color','olive','Olive',16),('color','multicolor','Multicolor',17),
  -- country-of-origin
  ('country-of-origin','bd','Bangladesh',1),('country-of-origin','cn','China',2),
  ('country-of-origin','in','India',3),('country-of-origin','vn','Vietnam',4),
  ('country-of-origin','us','USA',5),('country-of-origin','jp','Japan',6),
  ('country-of-origin','kr','South Korea',7),('country-of-origin','de','Germany',8),
  ('country-of-origin','th','Thailand',9),('country-of-origin','my','Malaysia',10),
  -- warranty
  ('warranty','none','No Warranty',1),('warranty','7d','7 Days',2),
  ('warranty','1m','1 Month',3),('warranty','6m','6 Months',4),
  ('warranty','1y','1 Year',5),('warranty','2y','2 Years',6),('warranty','3y','3 Years',7),
  -- material
  ('material','plastic','Plastic',1),('material','stainless-steel','Stainless Steel',2),
  ('material','glass','Glass',3),('material','ceramic','Ceramic',4),
  ('material','wood','Wood',5),('material','aluminium','Aluminium',6),
  ('material','silicone','Silicone',7),('material','rubber','Rubber',8),
  ('material','fabric','Fabric',9),('material','leather','Leather',10),
  -- clothing-size
  ('clothing-size','xs','XS',1),('clothing-size','s','S',2),('clothing-size','m','M',3),
  ('clothing-size','l','L',4),('clothing-size','xl','XL',5),('clothing-size','xxl','XXL',6),
  ('clothing-size','xxxl','XXXL',7),('clothing-size','free','Free Size',8),
  -- shoe-size
  ('shoe-size','36','36',1),('shoe-size','37','37',2),('shoe-size','38','38',3),
  ('shoe-size','39','39',4),('shoe-size','40','40',5),('shoe-size','41','41',6),
  ('shoe-size','42','42',7),('shoe-size','43','43',8),('shoe-size','44','44',9),
  ('shoe-size','45','45',10),('shoe-size','46','46',11),
  -- fabric
  ('fabric','cotton','Cotton',1),('fabric','polyester','Polyester',2),
  ('fabric','denim','Denim',3),('fabric','silk','Silk',4),('fabric','wool','Wool',5),
  ('fabric','linen','Linen',6),('fabric','leather','Leather',7),('fabric','rayon','Rayon',8),
  ('fabric','nylon','Nylon',9),('fabric','viscose','Viscose',10),('fabric','blend','Blend',11),
  -- fit
  ('fit','slim','Slim',1),('fit','regular','Regular',2),('fit','loose','Loose',3),
  ('fit','oversized','Oversized',4),('fit','tailored','Tailored',5),
  -- sleeve-length
  ('sleeve-length','sleeveless','Sleeveless',1),('sleeve-length','short','Short',2),
  ('sleeve-length','three-quarter','Three-Quarter',3),('sleeve-length','full','Full',4),
  -- pattern
  ('pattern','solid','Solid',1),('pattern','striped','Striped',2),
  ('pattern','checked','Checked',3),('pattern','printed','Printed',4),
  ('pattern','floral','Floral',5),('pattern','graphic','Graphic',6),
  -- neck-style
  ('neck-style','round','Round Neck',1),('neck-style','v-neck','V-Neck',2),
  ('neck-style','collar','Collar',3),('neck-style','polo','Polo',4),('neck-style','hooded','Hooded',5),
  -- gender
  ('gender','men','Men',1),('gender','women','Women',2),('gender','unisex','Unisex',3),
  ('gender','boys','Boys',4),('gender','girls','Girls',5),
  -- ram
  ('ram','2gb','2 GB',1),('ram','3gb','3 GB',2),('ram','4gb','4 GB',3),
  ('ram','6gb','6 GB',4),('ram','8gb','8 GB',5),('ram','12gb','12 GB',6),('ram','16gb','16 GB',7),
  -- storage
  ('storage','16gb','16 GB',1),('storage','32gb','32 GB',2),('storage','64gb','64 GB',3),
  ('storage','128gb','128 GB',4),('storage','256gb','256 GB',5),('storage','512gb','512 GB',6),
  ('storage','1tb','1 TB',7),
  -- operating-system
  ('operating-system','android','Android',1),('operating-system','ios','iOS',2),
  ('operating-system','windows','Windows',3),('operating-system','harmonyos','HarmonyOS',4),
  ('operating-system','other','Other',5),
  -- network
  ('network','2g','2G',1),('network','3g','3G',2),('network','4g','4G',3),
  ('network','5g','5G',4),('network','wifi-only','WiFi Only',5),
  -- sim-slots
  ('sim-slots','single','Single SIM',1),('sim-slots','dual','Dual SIM',2),('sim-slots','esim','eSIM',3),
  -- connectivity
  ('connectivity','wifi','WiFi',1),('connectivity','bluetooth','Bluetooth',2),
  ('connectivity','usb-c','USB-C',3),('connectivity','hdmi','HDMI',4),
  ('connectivity','nfc','NFC',5),('connectivity','aux','3.5mm Aux',6),
  -- voltage
  ('voltage','110v','110V',1),('voltage','220v','220V',2),('voltage','110-240v','110-240V',3),
  -- skin-type
  ('skin-type','normal','Normal',1),('skin-type','dry','Dry',2),('skin-type','oily','Oily',3),
  ('skin-type','combination','Combination',4),('skin-type','sensitive','Sensitive',5),
  ('skin-type','all','All Skin Types',6),
  -- form
  ('form','tablet','Tablet',1),('form','capsule','Capsule',2),('form','powder','Powder',3),
  ('form','liquid','Liquid',4),('form','gel','Gel',5),('form','syrup','Syrup',6),
  -- flavor
  ('flavor','unflavored','Unflavored',1),('flavor','chocolate','Chocolate',2),
  ('flavor','vanilla','Vanilla',3),('flavor','strawberry','Strawberry',4),
  ('flavor','mango','Mango',5),('flavor','orange','Orange',6),('flavor','mixed','Mixed Berry',7),
  -- age-group
  ('age-group','0-6m','0-6 Months',1),('age-group','6-12m','6-12 Months',2),
  ('age-group','1-3y','1-3 Years',3),('age-group','3-5y','3-5 Years',4),
  ('age-group','5-8y','5-8 Years',5),('age-group','8plus','8+ Years',6),
  -- language
  ('language','bn','Bangla',1),('language','en','English',2),('language','ar','Arabic',3),
  ('language','hi','Hindi',4),('language','other','Other',5),
  -- book-format
  ('book-format','paperback','Paperback',1),('book-format','hardcover','Hardcover',2),
  ('book-format','ebook','eBook',3),('book-format','audiobook','Audiobook',4),
  -- sport-type
  ('sport-type','cricket','Cricket',1),('sport-type','football','Football',2),
  ('sport-type','gym','Gym / Fitness',3),('sport-type','cycling','Cycling',4),
  ('sport-type','running','Running',5),('sport-type','badminton','Badminton',6),
  ('sport-type','swimming','Swimming',7)
) AS v(attr_slug, code, label, pos) ON v.attr_slug = a.slug
ON CONFLICT (attribute_id, value_code) DO NOTHING;

-- ---------------------------------------------------------------------------
-- 3) Category <-> attribute links (category-wise grouping)
--    axis=true marks variant-defining dropdowns (size/color/ram/storage...).
-- ---------------------------------------------------------------------------
INSERT INTO category_attributes (category_id, attribute_id, is_required, is_variant_axis)
SELECT c.id, a.id, l.req, l.axis
FROM categories c
JOIN (VALUES
  -- Men's / Women's / Kids' Fashion
  ('mens-fashion','clothing-size',true,true),('mens-fashion','color',true,true),
  ('mens-fashion','fabric',false,false),('mens-fashion','fit',false,false),
  ('mens-fashion','sleeve-length',false,false),('mens-fashion','pattern',false,false),
  ('mens-fashion','neck-style',false,false),('mens-fashion','gender',false,false),
  ('mens-fashion','brand',false,false),('mens-fashion','country-of-origin',false,false),
  ('womens-fashion','clothing-size',true,true),('womens-fashion','color',true,true),
  ('womens-fashion','fabric',false,false),('womens-fashion','fit',false,false),
  ('womens-fashion','sleeve-length',false,false),('womens-fashion','pattern',false,false),
  ('womens-fashion','neck-style',false,false),('womens-fashion','gender',false,false),
  ('womens-fashion','brand',false,false),('womens-fashion','country-of-origin',false,false),
  ('kids-fashion','clothing-size',true,true),('kids-fashion','color',true,true),
  ('kids-fashion','fabric',false,false),('kids-fashion','age-group',false,false),
  ('kids-fashion','gender',false,false),('kids-fashion','pattern',false,false),
  ('kids-fashion','brand',false,false),
  -- Electronics (+ gadgets)
  ('electronics','brand',true,false),('electronics','model',false,false),
  ('electronics','color',false,true),('electronics','ram',false,true),
  ('electronics','storage',false,true),('electronics','battery-mah',false,false),
  ('electronics','screen-size-inch',false,false),('electronics','operating-system',false,false),
  ('electronics','network',false,false),('electronics','sim-slots',false,false),
  ('electronics','connectivity',false,false),('electronics','power-watt',false,false),
  ('electronics','voltage',false,false),('electronics','warranty',true,false),
  ('electronics','weight-kg',false,false),('electronics','country-of-origin',false,false),
  -- Home & Kitchen
  ('home-kitchen','material',false,false),('home-kitchen','color',false,true),
  ('home-kitchen','capacity-litre',false,false),('home-kitchen','power-watt',false,false),
  ('home-kitchen','weight-kg',false,false),('home-kitchen','brand',false,false),
  ('home-kitchen','warranty',false,false),('home-kitchen','country-of-origin',false,false),
  -- Beauty & Fragrance
  ('beauty-fragrance','skin-type',false,false),('beauty-fragrance','volume-ml',false,false),
  ('beauty-fragrance','color',false,true),('beauty-fragrance','brand',false,false),
  ('beauty-fragrance','best-before',false,false),('beauty-fragrance','country-of-origin',false,false),
  -- Health & Nutrition
  ('health-nutrition','form',false,false),('health-nutrition','flavor',false,true),
  ('health-nutrition','net-weight-g',false,false),('health-nutrition','pack-count',false,false),
  ('health-nutrition','brand',false,false),('health-nutrition','best-before',false,false),
  ('health-nutrition','organic',false,false),
  -- Grocery
  ('grocery','net-weight-g',false,false),('grocery','pack-count',false,false),
  ('grocery','flavor',false,true),('grocery','organic',false,false),
  ('grocery','best-before',false,false),('grocery','brand',false,false),
  -- Sports & Outdoors
  ('sports-outdoors','sport-type',false,false),('sports-outdoors','clothing-size',false,true),
  ('sports-outdoors','shoe-size',false,true),('sports-outdoors','color',false,true),
  ('sports-outdoors','material',false,false),('sports-outdoors','weight-kg',false,false),
  ('sports-outdoors','brand',false,false),
  -- Baby
  ('baby','age-group',true,false),('baby','clothing-size',false,true),
  ('baby','material',false,false),('baby','color',false,true),
  ('baby','weight-kg',false,false),('baby','brand',false,false),
  -- Toys
  ('toys','age-group',true,false),('toys','material',false,false),
  ('toys','battery-required',false,false),('toys','piece-count',false,false),
  ('toys','color',false,true),('toys','brand',false,false),
  -- Books & Media
  ('books-media','author',false,false),('books-media','language',false,true),
  ('books-media','book-format',false,true),('books-media','pages',false,false),
  ('books-media','publisher',false,false),
  -- Stationery
  ('stationery','color',false,true),('stationery','material',false,false),
  ('stationery','pack-count',false,false),('stationery','brand',false,false),
  -- Automotive
  ('automotive','compatibility',false,false),('automotive','brand',false,false),
  ('automotive','material',false,false),('automotive','weight-kg',false,false),
  ('automotive','warranty',false,false)
) AS l(cat_slug, attr_slug, req, axis) ON l.cat_slug = c.slug
JOIN attribute_definitions a ON a.slug = l.attr_slug
ON CONFLICT (category_id, attribute_id) DO NOTHING;
