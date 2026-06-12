# Doctor Mobile App — API Contract

Reference for the doctor app team. All paths assume the `/api/v1`
prefix. Doctors auth via the same `/auth/*` endpoints as everyone else;
they're identified by having a `doctors` row with `linked_user_id =
<their user>` AND the `order.fulfill` permission.

---

## 1. AI suggestion

| Endpoint | Notes |
|---|---|
| `POST /doctor-rx/ai-suggest` | Body: `{symptoms, patient_age_years?, patient_weight_kg?, patient_sex?, catalog_filter_generic?, rx_only?}`. Returns the AI module's standard `AIProposalResponse` (status=`draft`). |

**Hard rule (Module 16)**: AI cannot prescribe. The proposal is a
suggestion the doctor reviews + composes a real prescription from. The
doctor's signature is on `POST /prescriptions`, not on the proposal.

**Why age + weight matter**: paediatric and adult dose schedules differ
significantly; many medicines are mg/kg. Always pass `patient_age_years`
and `patient_weight_kg` for minors. The provider receives both as
inputs.

When no AI provider is bound (default state), this endpoint returns
**HTTP 502** with code `integration_error` — fail-loud, no fake responses.

---

## 2. Prescription

| Endpoint | Notes |
|---|---|
| `POST /doctor-rx/suggest` | Catalog-backed match (in-stock products). Use this for in-formulary lookups; doctor selects manually. |
| `POST /doctor-rx/prescriptions` | Issue. Body covers patient (phone, name, age, **weight**), diagnosis, advice, optional credit grant (`credit_amount`), and an array of lines. Each line carries variant_id, slot booleans (morning/afternoon/night), `duration_days`, free-text `notes`, and the new dose fields **`dose_per_administration`** + **`dose_form`**. |
| `GET /doctor-rx/prescriptions` | Paginated list of MY prescriptions. |
| `GET /doctor-rx/prescriptions/{id}` | One. |
| `GET /doctor-rx/prescriptions/{id}/pdf` | Streams the rendered PDF. |
| `POST /doctor-rx/prescriptions/{id}/cancel` | Soft cancel with reason. |

### Dose fields (added in Module 19, migration 0018)

| Field | Type | Where |
|---|---|---|
| `patient_weight_kg` | Decimal(5,2), 0.5–500 | header |
| `dose_per_administration` | str ≤ 64 ("1 tablet", "5 ml", "0.5 mg/kg") | per line |
| `dose_form` | str ≤ 32 ("tablet", "syrup", "drop", "puff") | per line |

The backend does **not** interpret dose strings or compute totals from
them. Real dose calculation is the doctor's responsibility — the AI
suggest endpoint is the assistive surface; this column persists what
the doctor chose.

### Age-band hint (UI only)

| Endpoint | Notes |
|---|---|
| `GET /doctor-rx/age-band/{age_years}` | Returns `{age_years, band, notes}` where band ∈ {neonate, infant, child, adolescent, adult, senior}. Pure UI affordance — no medical claim. |

| Age | Band | Note |
|---|---|---|
| 0 | neonate | weight-based dosing essential; many adult medicines contraindicated |
| 1 | infant | weight-based dosing essential (mg/kg) |
| 2–11 | child | weight-based dosing recommended (mg/kg) |
| 12–17 | adolescent | weight-based or adult dosing per medicine |
| 18–64 | adult | standard adult dosing |
| 65+ | senior | review for renal/hepatic adjustments and drug interactions |

---

## 3. Wallet

| Endpoint | Notes |
|---|---|
| `GET /doctor-rx/wallet/credits-granted` | Paginated. Each row = one wallet credit issued from one of MY prescriptions, with the redeemed amount, status (`active`/`exhausted`/`expired`/`rolled_over`), expiry date, and the originating prescription code. |
| `GET /doctor-rx/wallet/summary` | Headline numbers: `{credits_granted_total, credits_granted_count, credits_redeemed_total, credits_expired_total, distinct_patients, redemption_rate}`. `redemption_rate` = redeemed/granted (0.0–1.0). |

Credits are granted only when the patient has a Hypershop account
linked to the same phone (the issue endpoint refuses to grant otherwise
with a 422). Redemptions happen when the patient checks out and the
order applies the credit.

---

## 4. Report

| Endpoint | Notes |
|---|---|
| `GET /doctor-rx/reports/activity?starts_on=…&ends_on=…&top_limit=N` | Date-range report. Returns `{prescriptions_issued, prescriptions_cancelled, distinct_patients, credits_granted_total, credits_redeemed_total, top_medicines: [{product_name, times_prescribed}]}`. |

**Date-range semantics:**
- Prescriptions counted by `issued_at` in range.
- Credits granted counted by the originating prescription's `issued_at` in range.
- Credits redeemed counted by the redeem transaction's `occurred_at` in range
  (so a credit issued last month and redeemed today shows up in today's report).

`top_limit` defaults to 5, max 20.

---

## 5. Recommended app screens

```
splash → /auth/login
home    ↔ /doctor-rx/wallet/summary           (compact metrics card)
        ↔ /doctor-rx/reports/activity?range=7d (sparkline)
new Rx  → /doctor-rx/age-band/{age}            (band hint chip)
        → /doctor-rx/ai-suggest                (assistive)
        → /doctor-rx/suggest                   (catalog match)
        → /doctor-rx/prescriptions             (POST to issue)
history → /doctor-rx/prescriptions             (paginated)
detail  → /doctor-rx/prescriptions/{id}/pdf
wallet  → /doctor-rx/wallet/credits-granted
```

---

## 6. What's intentionally NOT shipped yet

- **Real dose calculator** — needs a regulated drug database (BNF,
  BD National Formulary, or equivalent) OR the AI provider. The
  schema captures the doctor's chosen dose; the assist comes from the
  AI suggest endpoint.
- **Drug-interaction checks** — same constraint. Will land with the
  same dose database.
- **AI provider** — `NotConfiguredProvider` is the default binding
  (Module 16). All AI endpoints return 502 until a real provider is
  wired (`bind_provider()` at startup).
- **SMS-PDF delivery to no-account patients** — the issue flow renders
  a PDF and stores it; SMS delivery is paused on SMS provider creds.
  Today the doctor downloads the PDF from `/prescriptions/{id}/pdf`.
- **Doctor-to-doctor commission/payout** — the wallet view here is
  about credits granted to *patients*. A doctor commission scheme
  (e.g. % of redemptions back to the doctor) would be a separate
  finance module.
