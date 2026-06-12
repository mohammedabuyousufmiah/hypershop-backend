#!/bin/sh
# Mobile-app screen→endpoint live probe (run inside the api container).
B=http://localhost:8000/api/v1
CJ=/tmp/c.jar; RJ=/tmp/r.jar
i=0; while [ $i -lt 36 ]; do
  R=$(curl -s -m4 -o /dev/null -w "%{http_code}" "$B/health")
  [ "$R" = "200" ] && break
  i=$((i+1)); sleep 5
done
echo "api ready after $((i*5))s"

echo "=== CUSTOMER APP ==="
curl -s -c "$CJ" -X POST -H 'Content-Type: application/json' \
  -d '{"email":"customer@hypershop.dev","password":"customerlocal12"}' \
  -o /dev/null -w "login: %{http_code}\n" "$B/auth/login"
for p in /auth/me "/catalog/products?limit=2" /inventory/products /sales/orders /me/orders /orders /cart /me/notifications/unread-count /customers/preferences /health/version; do
  code=$(curl -s -b "$CJ" -m6 -o /dev/null -w "%{http_code}" "$B$p")
  printf '  %-38s -> %s\n' "$p" "$code"
done

echo "=== RIDER APP ==="
curl -s -c "$RJ" -X POST -H 'Content-Type: application/json' \
  -d '{"email":"rider@hypershop.dev","password":"riderlocal12"}' \
  -o /dev/null -w "login: %{http_code}\n" "$B/auth/login"
for p in /auth/me /logistics/deliveries /rider/deliveries /rider/assignments /rider/earnings; do
  code=$(curl -s -b "$RJ" -m6 -o /dev/null -w "%{http_code}" "$B$p")
  printf '  %-38s -> %s\n' "$p" "$code"
done
