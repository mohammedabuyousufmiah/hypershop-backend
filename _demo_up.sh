#!/usr/bin/env bash
cd /mnt/c/hs_wh/backend
F="-f docker-compose.yml -f docker-compose.override.yml -f docker-compose.localdemo.yml"
docker compose $F up -d --force-recreate postgres redis api
echo "== waiting for api =="
for i in $(seq 1 50); do
  code=$(curl -s -o /dev/null -w '%{http_code}' -m 4 "http://localhost:8002/api/v1/health" 2>/dev/null)
  echo "try $i -> ${code}"
  [ "$code" = "200" ] && break
  sleep 3
done
# Ensure seed scripts are present in the container (image bakes them in after a
# rebuild via the Dockerfile COPY; this cp makes it work pre-rebuild too).
docker compose $F cp scripts api:/app/scripts 2>/dev/null || true
echo "== iam-bootstrap (roles + permissions) =="
docker compose $F exec -T api python -m app.cli iam-bootstrap || echo "iam-bootstrap skipped"
echo "== seeding catalog (only if empty) =="
PCOUNT=$(curl -s -m 8 "http://localhost:8002/api/v1/catalog/products?page=1&page_size=1" | grep -oE '"total":[0-9]+' | grep -oE '[0-9]+' | head -1)
if [ "${PCOUNT:-0}" = "0" ]; then
  docker compose $F exec -T api python -m scripts.seed_catalog_demo || echo "catalog seed skipped"
fi
echo "== seeding storefront demo (buyer login + product videos) =="
docker compose $F exec -T api python -m scripts.seed_storefront_demo || echo "storefront demo seed skipped"
echo "== seeding fulfillment (warehouse + delivery zones) =="
docker compose $F exec -T api python -m scripts.seed_fulfillment_demo || echo "fulfillment seed skipped"
echo "== seeding gap-feature demo tables (wms / admin-lite / referrals / warehouse-ops) =="
for g in wms admin_lite referrals warehouse_ops; do
  docker compose $F exec -T postgres psql -U hypershop -d hypershop -v ON_ERROR_STOP=0 \
    -f "/app/scripts/seed_gap_${g}.sql" >/dev/null 2>&1 \
    || docker compose $F exec -T -i postgres psql -U hypershop -d hypershop -v ON_ERROR_STOP=0 < "scripts/seed_gap_${g}.sql" >/dev/null 2>&1 \
    || echo "gap seed ${g} skipped"
done
echo "== seeding demo admin data A2 (banners/pages/loyalty/campaigns/periods/ads) =="
docker compose $F exec -T postgres psql -U hypershop -d hypershop -v ON_ERROR_STOP=0 \
  -f "/app/scripts/seed_admin_demo_a2.sql" >/dev/null 2>&1 \
  || docker compose $F exec -T -i postgres psql -U hypershop -d hypershop -v ON_ERROR_STOP=0 < "scripts/seed_admin_demo_a2.sql" >/dev/null 2>&1 \
  || echo "admin demo A2 seed skipped"
echo "== seeding demo admin data A (coupons/gift-cards/flags/disputes/support/segments) =="
docker compose $F exec -T postgres psql -U hypershop -d hypershop -v ON_ERROR_STOP=0 \
  -f "/app/scripts/seed_admin_demo_a.sql" >/dev/null 2>&1 \
  || docker compose $F exec -T -i postgres psql -U hypershop -d hypershop -v ON_ERROR_STOP=0 < "scripts/seed_admin_demo_a.sql" >/dev/null 2>&1 \
  || echo "admin demo A seed skipped"
echo "== seeding demo product videos (Product Videos moderation page) =="
docker compose $F exec -T postgres psql -U hypershop -d hypershop -v ON_ERROR_STOP=0 \
  -f "/app/scripts/seed_product_videos_demo.sql" >/dev/null 2>&1 \
  || docker compose $F exec -T -i postgres psql -U hypershop -d hypershop -v ON_ERROR_STOP=0 < "scripts/seed_product_videos_demo.sql" >/dev/null 2>&1 \
  || echo "product videos demo seed skipped"
echo "== seeding demo product Q&A (Product Q&A admin page) =="
docker compose $F exec -T postgres psql -U hypershop -d hypershop -v ON_ERROR_STOP=0 \
  -f "/app/scripts/seed_qa_demo.sql" >/dev/null 2>&1 \
  || docker compose $F exec -T -i postgres psql -U hypershop -d hypershop -v ON_ERROR_STOP=0 < "scripts/seed_qa_demo.sql" >/dev/null 2>&1 \
  || echo "qa demo seed skipped"
echo "== seeding demo product reviews (Reviews moderation admin page) =="
docker compose $F exec -T postgres psql -U hypershop -d hypershop -v ON_ERROR_STOP=0 \
  -f "/app/scripts/seed_reviews_demo.sql" >/dev/null 2>&1 \
  || docker compose $F exec -T -i postgres psql -U hypershop -d hypershop -v ON_ERROR_STOP=0 < "scripts/seed_reviews_demo.sql" >/dev/null 2>&1 \
  || echo "reviews demo seed skipped"
echo "== seeding demo return requests (Returns RMA admin page) =="
docker compose $F exec -T postgres psql -U hypershop -d hypershop -v ON_ERROR_STOP=0 \
  -f "/app/scripts/seed_returns_demo.sql" >/dev/null 2>&1 \
  || docker compose $F exec -T -i postgres psql -U hypershop -d hypershop -v ON_ERROR_STOP=0 < "scripts/seed_returns_demo.sql" >/dev/null 2>&1 \
  || echo "returns demo seed skipped"
echo "== seeding demo bulk-upload jobs (Bulk Upload Jobs admin page) =="
docker compose $F exec -T postgres psql -U hypershop -d hypershop -v ON_ERROR_STOP=0 \
  -f "/app/scripts/seed_bulk_upload_demo.sql" >/dev/null 2>&1 \
  || docker compose $F exec -T -i postgres psql -U hypershop -d hypershop -v ON_ERROR_STOP=0 < "scripts/seed_bulk_upload_demo.sql" >/dev/null 2>&1 \
  || echo "bulk-upload demo seed skipped"
echo "== computing seller ratings (Seller Ratings admin page) =="
docker compose $F exec -T api python -m scripts.recompute_seller_ratings || echo "seller ratings recompute skipped"
echo "== seeding house seller (Hypershop Direct) for admin Create-product =="
docker compose $F exec -T postgres psql -U hypershop -d hypershop -v ON_ERROR_STOP=0 \
  -f "/app/scripts/seed_house_seller.sql" >/dev/null 2>&1 \
  || docker compose $F exec -T -i postgres psql -U hypershop -d hypershop -v ON_ERROR_STOP=0 < "scripts/seed_house_seller.sql" >/dev/null 2>&1 \
  || echo "house seller seed skipped"
echo "== seeding catalog attribute catalog (category-wise dropdowns) =="
docker compose $F exec -T postgres psql -U hypershop -d hypershop -v ON_ERROR_STOP=0 \
  -f "/app/scripts/seed_catalog_attributes.sql" >/dev/null 2>&1 \
  || docker compose $F exec -T -i postgres psql -U hypershop -d hypershop -v ON_ERROR_STOP=0 < "scripts/seed_catalog_attributes.sql" >/dev/null 2>&1 \
  || echo "catalog attributes seed skipped"
echo "== HEALTH =="
curl -s -m 6 "http://localhost:8002/api/v1/health"; echo
echo "== PRODUCTS =="
curl -s -m 8 "http://localhost:8002/api/v1/catalog/products?page=1&page_size=3"; echo
echo "== CATEGORIES =="
curl -s -m 6 "http://localhost:8002/api/v1/catalog/categories"; echo
echo "== DEMO_UP_DONE =="
