# Rider Mobile App — API Contract

Reference for the rider app team. All paths assume the `/api/v1` prefix.
The rider auth flow reuses the **same** `/auth/*` surface as the customer
app (email + password); differentiation is by RBAC permission, not by a
separate auth domain.

For the rider to be able to call any of these endpoints, the operator
must have:

1. A `users` row with an active session.
2. A `riders` row with `linked_user_id = users.id` and `is_active = true`.
3. The `order.fulfill` permission (granted to roles `staff`, `manager`,
   `admin`).

The rider's `id` is **not** the same as the user `id`; the rider id is
returned by `/me/profile` indirectly via the `cod-summary` and `tasks`
endpoints.

---

## 0. Conventions

Same as the customer app: `Authorization: Bearer <access_token>`,
ISO-8601 UTC timestamps, BDT money as decimal strings.

---

## 1. Task

| Endpoint | Notes |
|---|---|
| `GET /rider/me/deliveries` | Paginated full list. Filter by `?status=...`. Use this for history; the screens below cover the day-of view. |
| `GET /rider/me/deliveries/{id}` | One assignment with full state-history + POD evidence paths. |
| `GET /rider/me/deliveries/tasks` | **Today's queue** — route-friendly ordering: in-flight pickups first, then pending pickups, then delivered (awaiting COD reconciliation), then anything terminal that closed today. Returns lightweight `RiderTaskItem` rows (assignment + order code + recipient name/phone/address). Includes a `counts{status: int}` map for the badge UI. |
| `GET /rider/me/deliveries/tasks/next` | The single next task to act on, or `null` when idle. Use this on the rider home screen to render a single big "Next" card. |

`RiderTaskItem` carries: `assignment_id`, `order_id`, `order_code`,
`status`, `payment_method`, `cod_expected`, `cod_collected`,
`cod_status`, `recipient_name`, `recipient_phone`, `address_line1`,
`city`, `assigned_at`, `picked_up_at`, `delivered_at`.

---

## 2. Scan

| Endpoint | Notes |
|---|---|
| `POST /rider/me/deliveries/{id}/scan` | Body: `{scanned_code, intent: 'pickup' \| 'delivery'}`. Returns `{ok, expected_code, scanned_code, assignment_status, intent}`. |

**Behaviour worth knowing:**

- The scan is **case-insensitive** and trims whitespace, so a noisy
  scan that produces `  hso-abcd1234 \n` still matches `HSO-ABCD1234`.
- Wrong scan returns **HTTP 200 with `ok: false`**, not 4xx — so the
  rider app can show a red toast and let the rider re-scan immediately
  without an error-state UI dance.
- Both pass and fail scans are written to the audit log (`action =
  delivery.scan.{intent}`, `outcome = success|failure`) so a future
  fraud / training review can see every scan attempt.
- The `intent` is informational; both compare against the same printed
  order code.

---

## 3. Delivery (lifecycle)

State machine: `assigned → picked_up → delivered → completed`. Failure
branches: `→ failed` and `→ cancelled` (admin-side).

| Endpoint | When |
|---|---|
| `POST /rider/me/deliveries/{id}/pickup` | At the warehouse. After an `intent='pickup'` scan that returned `ok:true`. |
| `POST /rider/me/deliveries/{id}/upload-pod` | After arrival; uploads a photo (JPG/PNG/WEBP). Multipart `file` field. |
| `POST /rider/me/deliveries/{id}/upload-signature` | Companion to `/upload-pod` — uploads a signature image. Either photo OR signature OR `pod_otp_verified=true` satisfies the POD-mandatory rule on `/deliver`. Only callable while in `PICKED_UP` or `DELIVERED`. |
| `POST /rider/me/deliveries/{id}/deliver` | Body: `{recipient_name, pod_otp_verified, cod_collected, notes}`. POD evidence MUST be present. For COD orders `cod_collected` is required. Auto-transitions to `COMPLETED` if COD reconciles within tolerance; otherwise leaves it in `DELIVERED` for supervisor reconciliation. |
| `POST /rider/me/deliveries/{id}/fail` | Body: `{reason}`. Use for "customer not home", "address wrong", etc. |

**POD-mandatory rule** (enforced server-side on `/deliver`):

```
photo_attached OR signature_attached OR pod_otp_verified == True
```

If none → 422.

> **POD OTP issuance/verification is intentionally NOT exposed yet.** The
> backend has `pod_otp_verified` as an attest-only boolean; full
> issue/verify endpoints land once the SMS provider creds arrive (same
> blocker as customer phone-OTP login). Today the rider can attest to
> having confirmed identity out-of-band by setting
> `pod_otp_verified=true` on `/deliver`.

---

## 4. COD

| Endpoint | Notes |
|---|---|
| `GET /rider/me/deliveries/cod-summary` | Returns `{rider_id, expected_total, deposited_total, outstanding, today_collected_amount, today_collected_count}`. The `expected_total` − `deposited_total` is the cash the rider should physically have on them right now. Same numbers the admin sees on the finance dashboard — they stay in sync because both go through `FinanceService.rider_cash_on_hand`. |

**Cash deposit flow** (today): the rider hands cash to the cashier; the
cashier (admin) records the deposit via
`POST /admin/finance/cod-deposits`. A rider-initiated "I'm depositing X"
endpoint is not yet wired — extend the finance module when the workflow
needs it.

---

## 5. POD (Proof of Delivery)

POD evidence types (any one satisfies the rule):

1. **Photo** — `POST /rider/me/deliveries/{id}/upload-pod` (multipart `file`).
2. **Signature** — `POST /rider/me/deliveries/{id}/upload-signature`
   (multipart `file`).
3. **OTP attestation** — `pod_otp_verified=true` on `/deliver`. The
   actual SMS-OTP issue + verify is paused on SMS provider creds; today
   this is rider self-attestation only.

All three persist to the same `delivery_assignments` row:
- `pod_photo_path` — relative path under `delivery_pod_dir`
- `pod_signature_path` — same
- `pod_otp_verified_at` — timestamp set when `/deliver` is called with
  `pod_otp_verified=true`
- `pod_recipient_name`, `pod_notes` — recipient-side metadata

The rider app should render whichever of the three the rider has
captured (UI checkboxes are fine; the server is permissive about which
combination).

---

## 6. Availability

| Endpoint | Notes |
|---|---|
| `POST /rider/me/deliveries/availability` | Body: `{status: 'offline' \| 'available' \| 'busy'}`. Returns `{rider_id, status}`. Going offline while any assignment is in `assigned`, `picked_up`, or `delivered` state is rejected with 422 — the rider must hand the assignment back to dispatch first. `busy` is set automatically by `assign`; the rider rarely needs to set it manually. |

---

## 7. Recommended app screens → endpoints

```
splash → /auth/login (same as customer app)
        → /rider/me/deliveries/tasks/next  (single big "Next" card)
home    ↔ /rider/me/deliveries/tasks       (queue + counters badge)
        ↔ /rider/me/deliveries/cod-summary (cash widget)
detail  → /rider/me/deliveries/{id}
scan    → /rider/me/deliveries/{id}/scan
pickup  → /rider/me/deliveries/{id}/pickup
deliver → /upload-pod  AND/OR  /upload-signature
        → /deliver
fail    → /fail
profile → /rider/me/deliveries/availability  (toggle switch)
```

---

## 8. What's intentionally NOT shipped yet

- **POD-OTP issue + verify** — needs SMS provider creds.
- **Rider-initiated cash-deposit submission** — admin records deposits today via `/admin/finance/cod-deposits`. Add a rider-initiated request flow when the cash workflow demands it.
- **Live GPS / route guidance** — the assignment carries the address but no map link. The rider app should construct its own `geo:` / `https://maps.google.com/?q=...` link from `address_line1 + city`.
- **Push notifications** — the device-token table from Module 17 covers riders too (same `users` table), but the FCM/APNs sender is not wired. Polling `/tasks` every 30s on the home screen is fine.
- **Hand-off between riders** — only admin can reassign via `/admin/deliveries/assignments`. Rider-to-rider hand-off would be a new endpoint.
