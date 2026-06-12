#!/bin/sh
# Does the backend already serve every page a 24-page customer app needs?
B=http://localhost:8000/api/v1
CJ=/tmp/c3.jar
i=0; while [ $i -lt 36 ]; do
  R=$(curl -s -m4 -o /dev/null -w "%{http_code}" "$B/health"); [ "$R" = "200" ] && break
  i=$((i+1)); sleep 5
done
echo "api ready (${i}x5s)"
# fresh login (cookie may be stale after restarts)
curl -s -c "$CJ" -X POST -H 'Content-Type: application/json' \
  -d '{"email":"mobiledemo@hypershop.dev","password":"Customer@Local12"}' \
  -o /dev/null -w "login: %{http_code}\n" "$B/auth/login"

probe() { printf '  %-44s -> %s\n' "$1" "$(curl -s -b "$CJ" -m6 -o /dev/null -w "%{http_code}" "$B$1")"; }

echo "--- pages 9-24 candidates ---"
probe "/search?q=phone"
probe "/catalog/search?q=phone"
probe "/wishlist"
probe "/loyalty/me"
probe "/loyalty/me/ledger"
probe "/referrals/me"
probe "/gift-cards/me"
probe "/reviews/products/mine"
probe "/me/reviews"
probe "/coupons/available"
probe "/promotions"
probe "/returns"
probe "/me/returns"
probe "/me/addresses"
probe "/me/notifications"
probe "/cc/threads"
probe "/support/threads"
probe "/checkout/quote"
probe "/delivery/zones"
probe "/payment-methods"
probe "/payments/methods"
probe "/me/wallet"
probe "/wallet/me"
probe "/brands"
probe "/catalog/brands"
