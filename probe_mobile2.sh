#!/bin/sh
# Round 2: register demo users, then probe every authed mobile endpoint.
B=http://localhost:8000/api/v1
CJ=/tmp/c2.jar
i=0; while [ $i -lt 36 ]; do
  R=$(curl -s -m4 -o /dev/null -w "%{http_code}" "$B/health"); [ "$R" = "200" ] && break
  i=$((i+1)); sleep 5
done
echo "api ready after $((i*5))s"

echo "=== register customer demo ==="
curl -s -X POST -H 'Content-Type: application/json' \
  -d '{"email":"customer@hypershop.dev","password":"customerlocal12","full_name":"Demo Customer","phone":"+8801712740672"}' \
  -o /tmp/reg.json -w "register: %{http_code}\n" "$B/auth/register"
head -c 200 /tmp/reg.json; echo

echo "=== login customer ==="
curl -s -c "$CJ" -X POST -H 'Content-Type: application/json' \
  -d '{"email":"customer@hypershop.dev","password":"customerlocal12"}' \
  -o /dev/null -w "login: %{http_code}\n" "$B/auth/login"

echo "=== CUSTOMER screens (authed) ==="
for p in /auth/me /orders /cart /me/notifications/unread-count /me/notifications /customers/preferences /me/devices /wishlist /me/addresses; do
  code=$(curl -s -b "$CJ" -m6 -o /dev/null -w "%{http_code}" "$B$p")
  printf '  %-38s -> %s\n' "$p" "$code"
done

echo "=== rider-shaped public routes on THIS backend ==="
python3 - <<'PY' 2>/dev/null || true
import json, urllib.request
spec = json.load(urllib.request.urlopen("http://localhost:8000/api/v1/openapi.json"))
paths = [p for p in spec["paths"] if ("rider" in p or "deliver" in p or "otp" in p) and "/admin/" not in p]
for p in sorted(paths)[:14]:
    print("  ", p)
PY
