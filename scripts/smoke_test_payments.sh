#!/usr/bin/env bash
# scripts/smoke_test_payments.sh
# ─────────────────────────────────────────────────────────────────────
# End-to-end payments smoke test — exercises the Bkash + SSLCommerz
# integration against their respective SANDBOX environments.
#
# What it verifies, in order:
#   1. API health
#   2. Customer login + bearer token
#   3. Create a draft order with one cheap item
#   4. POST /payments/initiate?provider=bkash → returns bkashURL
#   5. GET /payments/{intent_id} → status=initiated
#   6. (Manual) operator follows bkashURL in a browser, completes the
#      sandbox flow with Bkash test wallet credentials
#   7. Wait for webhook → poll until intent.status=succeeded (timeout 5min)
#   8. Repeat steps 3-7 for sslcommerz
#
# Required env (or fail-fast with a useful message):
#   API_BASE_URL                http://localhost:8000 default
#   SMOKE_PAYMENT_CUSTOMER_EMAIL    customer email (test account)
#   SMOKE_PAYMENT_CUSTOMER_PASSWORD password
#   SMOKE_PAYMENT_VARIANT_ID        UUID of a cheap test variant in the catalog
#   SMOKE_PAYMENT_PROVIDER          bkash | sslcommerz (default: both)
#   POLL_TIMEOUT_S                  default 300 (5 min for webhook arrival)
#
# Pre-requisites in the running stack's .env:
#   BKASH_APP_KEY=...
#   BKASH_APP_SECRET=...
#   BKASH_USERNAME=...
#   BKASH_PASSWORD=...
#   BKASH_BASE_URL=https://tokenized.sandbox.bka.sh/v1.2.0-beta
#   PAYMENT_WEBHOOK_BASE_URL=<publicly-reachable URL — use ngrok in dev>
#
#   SSLCOMMERZ_STORE_ID=...
#   SSLCOMMERZ_STORE_PASSWD=...
#   SSLCOMMERZ_BASE_URL=https://sandbox.sslcommerz.com
#   SSLCOMMERZ_IS_SANDBOX=true
#
# The webhook MUST be reachable from the public internet. In local
# dev: spin up `ngrok http 8000` and set
# PAYMENT_WEBHOOK_BASE_URL=https://<your-tunnel>.ngrok.app.
#
# Exit codes:
#   0  all checks passed
#   1  a check failed
#   2  pre-flight failed (env / tooling missing)

set -euo pipefail

API="${API_BASE_URL:-http://localhost:8000}"
PREFIX="/api/v1"
POLL_TIMEOUT_S="${POLL_TIMEOUT_S:-300}"
PROVIDER="${SMOKE_PAYMENT_PROVIDER:-both}"

# ─────────── colour ───────────
if [[ -t 1 ]]; then
    BOLD=$(tput bold || true); GREEN=$(tput setaf 2 || true)
    RED=$(tput setaf 1 || true); YELLOW=$(tput setaf 3 || true)
    RESET=$(tput sgr0 || true)
else
    BOLD=""; GREEN=""; RED=""; YELLOW=""; RESET=""
fi

step() { printf '\n%s▶ %s%s\n' "$BOLD" "$*" "$RESET"; }
ok()   { printf '%s  ✓ %s%s\n' "$GREEN" "$*" "$RESET"; }
warn() { printf '%s  ⚠ %s%s\n' "$YELLOW" "$*" "$RESET"; }
fail() { printf '%s  ✗ FAIL: %s%s\n' "$RED" "$*" "$RESET" >&2; exit 1; }
preflight_fail() {
    printf '%s  ✗ PRE-FLIGHT: %s%s\n' "$RED" "$*" "$RESET" >&2; exit 2
}

# ─────────── pre-flight ───────────
step "0. Pre-flight"
for cmd in curl jq; do
    command -v "$cmd" >/dev/null 2>&1 || preflight_fail "missing tool: $cmd"
done
ok "host tools present"

[[ -n "${SMOKE_PAYMENT_CUSTOMER_EMAIL:-}" ]] \
    || preflight_fail "SMOKE_PAYMENT_CUSTOMER_EMAIL unset"
[[ -n "${SMOKE_PAYMENT_CUSTOMER_PASSWORD:-}" ]] \
    || preflight_fail "SMOKE_PAYMENT_CUSTOMER_PASSWORD unset"
[[ -n "${SMOKE_PAYMENT_VARIANT_ID:-}" ]] \
    || preflight_fail "SMOKE_PAYMENT_VARIANT_ID unset (a cheap catalog variant UUID)"
ok "env vars set"

# ─────────── login ───────────
step "1. API health + login"
curl -fsS -m 5 "$API$PREFIX/health" | grep -q '"status"' \
    || fail "health endpoint did not return 200/ok"
ok "health 200"

LOGIN_RESP=$(curl -fsS -X POST "$API$PREFIX/auth/login" \
    -H "Content-Type: application/json" \
    -d "{\"email\":\"$SMOKE_PAYMENT_CUSTOMER_EMAIL\",\"password\":\"$SMOKE_PAYMENT_CUSTOMER_PASSWORD\"}")
TOKEN=$(echo "$LOGIN_RESP" | jq -r '.tokens.access_token // .access_token // empty')
[[ -n "$TOKEN" ]] || fail "no access_token in login response"
AUTH="Authorization: Bearer $TOKEN"
ok "logged in as $SMOKE_PAYMENT_CUSTOMER_EMAIL"

# ─────────── per-provider runner ───────────
run_provider() {
    local PROV=$1
    step "── PROVIDER: $PROV ──"

    # Step A: Create draft order
    step "$PROV.A — place order"
    ORDER_RESP=$(curl -fsS -X POST "$API$PREFIX/orders" \
        -H "$AUTH" \
        -H "Content-Type: application/json" \
        -d "{\"items\":[{\"variant_id\":\"$SMOKE_PAYMENT_VARIANT_ID\",\"quantity\":1}],\"shipping_address\":{\"line1\":\"123 Smoke St\",\"city\":\"Dhaka\",\"postal_code\":\"1207\",\"phone\":\"+8801711111111\"}}")
    ORDER_ID=$(echo "$ORDER_RESP" | jq -r .id)
    [[ "$ORDER_ID" =~ ^[0-9a-f-]+$ ]] \
        || fail "no order id in response: $ORDER_RESP"
    ok "order created: $ORDER_ID"

    # Step B: Initiate payment
    step "$PROV.B — POST /payments/initiate"
    INIT_RESP=$(curl -fsS -X POST "$API$PREFIX/payments/initiate" \
        -H "$AUTH" \
        -H "Content-Type: application/json" \
        -d "{\"order_id\":\"$ORDER_ID\",\"provider\":\"$PROV\"}")
    INTENT_ID=$(echo "$INIT_RESP" | jq -r .intent_id)
    CHECKOUT_URL=$(echo "$INIT_RESP" | jq -r .checkout_url)
    INIT_STATUS=$(echo "$INIT_RESP" | jq -r .status)
    [[ "$INTENT_ID" =~ ^[0-9a-f-]+$ ]] \
        || fail "no intent_id: $INIT_RESP"
    [[ -n "$CHECKOUT_URL" ]] \
        || fail "no checkout_url returned (provider: $PROV)"
    [[ "$INIT_STATUS" == "initiated" ]] \
        || fail "expected status=initiated, got: $INIT_STATUS"
    ok "intent $INTENT_ID, status=initiated"
    ok "checkout URL: $CHECKOUT_URL"

    # Step C: MANUAL — operator completes sandbox flow
    printf '\n%s%s — MANUAL STEP%s\n' "$BOLD" "$YELLOW" "$RESET"
    cat <<MANUAL

        Open this URL in a browser:
            $CHECKOUT_URL

        Complete the sandbox payment flow:
          - Bkash sandbox wallet: 01770618567 / OTP: 123456 / PIN: 12121
          - SSLCommerz sandbox: any test card (4111-1111-1111-1111 / 123 / future date)

        The smoke script will poll for webhook arrival
        for up to ${POLL_TIMEOUT_S}s after you press Enter.

MANUAL
    if [[ -t 0 ]]; then
        printf '%s  Press Enter once you have completed the sandbox flow…%s' "$YELLOW" "$RESET"
        read -r
    else
        warn "not a TTY — skipping interactive prompt; sleeping 60s before polling"
        sleep 60
    fi

    # Step D: Poll for webhook → intent status
    step "$PROV.D — poll intent status (timeout ${POLL_TIMEOUT_S}s)"
    DEADLINE=$(($(date +%s) + POLL_TIMEOUT_S))
    LAST_STATUS="(unknown)"
    while [[ $(date +%s) -lt $DEADLINE ]]; do
        STATUS_RESP=$(curl -fsS -H "$AUTH" "$API$PREFIX/payments/$INTENT_ID")
        LAST_STATUS=$(echo "$STATUS_RESP" | jq -r .status)
        case "$LAST_STATUS" in
            succeeded) break ;;
            failed|cancelled|expired)
                fail "$PROV intent ended in terminal non-success: $LAST_STATUS"
                ;;
        esac
        sleep 5
    done
    [[ "$LAST_STATUS" == "succeeded" ]] \
        || fail "$PROV did not reach succeeded within ${POLL_TIMEOUT_S}s (last: $LAST_STATUS)"
    ok "$PROV intent succeeded"
}

# ─────────── execute ───────────
case "$PROVIDER" in
    bkash) run_provider bkash ;;
    sslcommerz) run_provider sslcommerz ;;
    both) run_provider bkash; run_provider sslcommerz ;;
    *) preflight_fail "unknown SMOKE_PAYMENT_PROVIDER: $PROVIDER (use bkash | sslcommerz | both)" ;;
esac

printf '\n%s═══════════════════════════════════════════════════%s\n' "$GREEN" "$RESET"
printf '%s  All payments smoke checks passed ✓%s\n' "$GREEN" "$RESET"
printf '%s═══════════════════════════════════════════════════%s\n' "$GREEN" "$RESET"
