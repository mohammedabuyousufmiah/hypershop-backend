# Hypershop Payments — Ops Runbook

**Module:** `app/modules/payments/`
**Status:** code-complete; **operational gap = sandbox + production credentials + webhook URL**.

This doc is the bridge from "code is shipped" to "real customer money moves through the system". The code itself (Bkash Tokenized Checkout, SSLCommerz IPN, Nagad, Rocket) is already built — see `app/modules/payments/providers/`. What's missing is environment-specific config that only the operator can supply.

---

## ⚡ Fast-path checklist — when creds arrive

Linear sequence to go from "creds in hand" to "first sandbox transaction green". ~1–2 hours total.

- [ ] **1. Drop creds into `.env`** — `BKASH_APP_KEY`, `BKASH_APP_SECRET`, `BKASH_USERNAME`, `BKASH_PASSWORD`, `BKASH_BASE_URL` (sandbox URL in §Bkash sandbox setup); same for SSLCommerz: `SSLCOMMERZ_STORE_ID`, `SSLCOMMERZ_STORE_PASSWD`, `SSLCOMMERZ_BASE_URL`. Set `PAYMENT_PROVIDER=bkash` (or `sslcommerz`).
- [ ] **2. Set `PAYMENT_WEBHOOK_BASE_URL`** to the publicly-reachable host the providers will POST to. Local dev = ngrok tunnel; staging = your staging API host.
- [ ] **3. Register webhook URL** in each provider's developer dashboard:
  - Bkash: `<base>/api/v1/payments/webhooks/bkash`
  - SSLCommerz: `<base>/api/v1/payments/webhooks/sslcommerz` (set as the IPN URL in the merchant panel)
- [ ] **4. Restart api + worker** so the providers/factory rebinds with the new env (`docker compose restart api worker`).
- [ ] **5. Run `bash scripts/smoke_test_payments.sh`** with the env block from §Smoke test. Pass = both flows reach `status=succeeded`.
- [ ] **6. Watch the audit log + Prometheus `/metrics`** for the next 24h before flipping `PAYMENT_PROVIDER` in production. SQL queries in §Audit + monitoring.

If any step fails, see §Common failures — the symptom-to-cause mapping covers ~90% of first-run issues.

**Production cutover** (after sandbox is stable for 3 days): see §Production cutover. Plan 2–3 weeks for the KYC + signed merchant agreements that prod creds require.

---

## What's already in the code (no work needed)

| Provider | Adapter | Lines | Webhook | Refund | Status |
|---|---|---|---|---|---|
| Bkash Tokenized Checkout v1.2.0-beta | `providers/bkash.py` | 320 | ✅ | ✅ | ready |
| SSLCommerz multi-rail | `providers/sslcommerz.py` | 355 | ✅ (IPN + signature) | ✅ | ready |
| Nagad direct | `providers/nagad.py` | 441 | ✅ | ✅ | ready |
| Rocket direct | `providers/rocket.py` | 333 | ✅ (HMAC-SHA256) | ✅ | ready |

API surface (already mounted in `app/main.py`):

```
POST /api/v1/payments/initiate       (customer, requires order_id + provider)
GET  /api/v1/payments/{intent_id}    (customer, owner-scoped)

POST /api/v1/payments/webhooks/bkash       (public, signature-verified)
POST /api/v1/payments/webhooks/sslcommerz  (public, IPN form-encoded)
POST /api/v1/payments/webhooks/nagad
POST /api/v1/payments/webhooks/rocket

GET  /api/v1/admin/payments              (admin list with filters)
GET  /api/v1/admin/payments/{intent_id}  (admin detail)
POST /api/v1/admin/payments/{intent_id}/refund
```

Tests: `app/modules/payments/tests/` — 14 unit tests covering construction refusal, signature builders, HMAC verification, status mapping, webhook parser. Run via `make test-int`.

---

## Operational gap — what YOU need to do

### Bkash sandbox setup

1. **Get sandbox credentials.** Email `developer@bkash.com` with company name + use case. Turnaround typically 2–5 business days. They issue:
   - `app_key`
   - `app_secret`
   - `username`
   - `password`
2. **Set env vars** (in `.env` for dev, `.env.prod` for prod):
   ```
   BKASH_APP_KEY=<from-bkash>
   BKASH_APP_SECRET=<from-bkash>
   BKASH_USERNAME=<from-bkash>
   BKASH_PASSWORD=<from-bkash>
   BKASH_BASE_URL=https://tokenized.sandbox.bka.sh/v1.2.0-beta
   ```
3. **Register your webhook URL** in Bkash's developer dashboard:
   - Local dev: spin `ngrok http 8000`, register `https://<your-tunnel>.ngrok.app/api/v1/payments/webhooks/bkash`
   - Staging/prod: register `https://<api-host>/api/v1/payments/webhooks/bkash`
4. **Test wallet** for the sandbox payment flow:
   - Wallet number: `01770618567`
   - OTP: `123456`
   - PIN: `12121`

### SSLCommerz sandbox setup

1. **Self-register** at https://developer.sslcommerz.com/registration/ — instant approval.
2. **Set env vars**:
   ```
   SSLCOMMERZ_STORE_ID=<from-dashboard>
   SSLCOMMERZ_STORE_PASSWD=<from-dashboard>
   SSLCOMMERZ_BASE_URL=https://sandbox.sslcommerz.com
   SSLCOMMERZ_IS_SANDBOX=true
   ```
3. **Register your IPN URL** in the SSLCommerz merchant panel:
   - URL: `https://<api-host>/api/v1/payments/webhooks/sslcommerz`
   - SSLCommerz will POST form-encoded data here on every transaction.
4. **Test cards** for the sandbox flow:
   - Card: `4111-1111-1111-1111` (Visa test)
   - CVV: `123`
   - Expiry: any future date
   - Name: any

### Common to all providers

```
PAYMENT_DEFAULT_REDIRECT_BASE_URL=https://app.hypershop.bd
PAYMENT_WEBHOOK_BASE_URL=https://api.hypershop.bd
```

These determine the URLs Hypershop builds for "land back here after payment" and "providers POST webhooks here". The webhook URL MUST be publicly reachable — it's how Bkash/SSLCommerz tells us the customer paid.

---

## Smoke test

`scripts/smoke_test_payments.sh` exercises the full sandbox flow end-to-end. It's interactive — pauses for the operator to complete the payment in a browser, then polls the intent until it reaches `succeeded`.

```bash
export API_BASE_URL=http://localhost:8000
export SMOKE_PAYMENT_CUSTOMER_EMAIL=customer@hypershop.local
export SMOKE_PAYMENT_CUSTOMER_PASSWORD=<password>
export SMOKE_PAYMENT_VARIANT_ID=<UUID of a cheap test variant>
export SMOKE_PAYMENT_PROVIDER=bkash    # or sslcommerz or both

bash scripts/smoke_test_payments.sh
```

The script flow:
1. Health check
2. Customer login → bearer token
3. POST /orders → draft order ID
4. POST /payments/initiate → checkout URL
5. **Manual:** open URL in browser, complete sandbox payment with the test wallet/card above
6. Poll /payments/{intent_id} until `status=succeeded` (timeout 5 min)
7. Repeat for the second provider if `SMOKE_PAYMENT_PROVIDER=both`

Pass = both flows reach `succeeded`. Fail = the script exits non-zero with the last seen status.

---

## Production cutover

After sandbox is fully green for at least 3 days of normal volume:

1. **Get production credentials.** Bkash + SSLCommerz both require KYC + signed merchant agreements. Plan 2–3 weeks for this.
2. **Switch base URLs** in `.env`:
   ```
   BKASH_BASE_URL=https://tokenized.pay.bka.sh/v1.2.0-beta
   SSLCOMMERZ_BASE_URL=https://securepay.sslcommerz.com
   SSLCOMMERZ_IS_SANDBOX=false
   ```
3. **Re-register the production webhook URLs** in each provider's prod dashboard. Sandbox + prod use separate dashboards — DO NOT assume the sandbox webhook carries over.
4. **Run a single live transaction with the smallest possible amount** (1 BDT for Bkash, 1.5 BDT for SSLCommerz minimum). Verify it shows in the audit log + intent succeeds.
5. **Monitor** the `payments_*` log fields and DB `payment_intents.status` distribution for the first 24h.

---

## Common failures

| Symptom | Likely cause | Fix |
|---|---|---|
| `IntegrationError: missing_setting=BKASH_*` on `/payments/initiate` | env vars not set or app reloaded with stale cache | check `.env`, restart api container |
| Customer reaches Bkash, pays, but intent stays in `initiated` | webhook URL not reachable from Bkash | ngrok tunnel down OR firewall blocks Bkash IPs OR webhook URL wrong in Bkash dashboard |
| `signature_invalid` on webhook | SSLCommerz: wrong `store_passwd` in env. Bkash: wrong `app_secret`. Rocket: clock skew > 5 min | rotate creds; check NTP on api host |
| `intent expired` | `PAYMENT_INTENT_TTL_SECONDS` (default 30 min) elapsed before customer paid | extend TTL OR ask customer to retry |
| `400` from sandbox Bkash on `create` | sandbox lifecycle reset (Bkash resets sandbox state weekly) — old cached token invalid | restart api to clear adapter token cache |

---

## Audit + monitoring

Every payment transition writes to `audit_logs` with action codes:

```
payments.intent_created
payments.intent_redirected
payments.intent_captured       (success path)
payments.intent_failed
payments.intent_cancelled
payments.intent_refunded
payments.webhook_received
payments.webhook_ignored       (signature failed / unknown intent)
```

Useful SQL:

```sql
-- Today's payment outcomes by provider
SELECT
  metadata_->>'provider' AS provider,
  count(*) FILTER (WHERE action = 'payments.intent_captured') AS succeeded,
  count(*) FILTER (WHERE action = 'payments.intent_failed') AS failed,
  count(*) FILTER (WHERE action = 'payments.intent_cancelled') AS cancelled
FROM audit_logs
WHERE action LIKE 'payments.%'
  AND created_at > date_trunc('day', now())
GROUP BY 1;

-- Stuck intents — initiated > 30 min ago, no webhook received
SELECT id, provider, amount, created_at,
       extract(epoch FROM (now() - created_at)) / 60 AS age_min
FROM payment_intents
WHERE status = 'initiated'
  AND created_at < now() - interval '30 minutes'
ORDER BY created_at;
```

For dashboards: the existing project Prometheus exporter already emits `http_requests_total{route="/api/v1/payments/...", status=...}`. Wire a Grafana panel similar to the Module 35 dashboard pattern for payments.

---

## Out of scope

- **Multi-currency** — Hypershop is BDT-only by design. Accepting USD/INR/etc. is a separate, much larger project.
- **Saved cards** — neither Bkash nor SSLCommerz support saved-card on the customer side without PCI scope; we don't take card numbers, the gateway hosts the page.
- **Apple Pay / Google Pay** — out of scope for BD market.
- **Refunds via customer self-service** — admin-only by design (refund creates accounting + reconciliation work).
