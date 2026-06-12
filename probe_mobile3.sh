#!/bin/sh
B=http://localhost:8000/api/v1
CJ=/tmp/c3.jar
i=0; while [ $i -lt 36 ]; do
  R=$(curl -s -m4 -o /dev/null -w "%{http_code}" "$B/health"); [ "$R" = "200" ] && break
  i=$((i+1)); sleep 5
done
echo "api ready after $((i*5))s"

echo "=== register (strong pw) ==="
curl -s -X POST -H 'Content-Type: application/json' \
  -d '{"email":"mobiledemo@hypershop.dev","password":"Customer@Local12","full_name":"Mobile Demo","phone":"+8801712740699"}' \
  -o /tmp/reg.json -w "register: %{http_code}\n" "$B/auth/register"
head -c 160 /tmp/reg.json; echo

echo "=== login ==="
curl -s -c "$CJ" -X POST -H 'Content-Type: application/json' \
  -d '{"email":"mobiledemo@hypershop.dev","password":"Customer@Local12"}' \
  -o /dev/null -w "login: %{http_code}\n" "$B/auth/login"

echo "=== CUSTOMER screens (authed) ==="
for p in /auth/me /orders /cart /me/notifications/unread-count /me/notifications /customers/preferences /me/devices /wishlist /me/addresses; do
  code=$(curl -s -b "$CJ" -m6 -o /dev/null -w "%{http_code}" "$B$p")
  printf '  %-38s -> %s\n' "$p" "$code"
done

echo "=== rider/delivery/otp public routes ==="
curl -s -m8 http://localhost:8000/openapi.json -o /tmp/oas.json
python3 -c "
import json
spec = json.load(open('/tmp/oas.json'))
paths = [p for p in spec['paths'] if ('rider' in p or 'deliver' in p or 'otp' in p) and '/admin/' not in p]
print('\n'.join('   '+p for p in sorted(paths)[:16]) or '  (none found)')
" 2>&1 | head -18
