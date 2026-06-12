# Customer Mobile App — API Contract

Reference for the iOS / Android / web-app team. All paths assume the
`/api/v1` prefix; example: the literal path for "register a device" is
`POST /api/v1/me/devices`.

Base URL is per environment:
- Local dev: `http://localhost:8000`
- Production: `https://api.dailylifepharmacy.com.bd` (TBD)

---

## 0. Conventions

- **Auth**: every authenticated endpoint expects `Authorization: Bearer <access_token>`.
- **Token lifetime**: access ≈ 15 min (env `JWT_ACCESS_TTL_SECONDS`), refresh ≈ 7 days. Refresh via `POST /auth/refresh` ahead of expiry.
- **Pagination**: `?page=N&size=M` (1-indexed `page`, max `size` per endpoint). Response is `{ items, page, size, total }`.
- **Errors**: always JSON `{ code, message, details, request_id }`. Status codes follow REST: 400 invalid body, 401 missing/expired token, 403 wrong permission, 404 not found, 409 idempotency / unique violation, 422 business rule, 502 upstream provider, 503 timeout.
- **Money**: BDT, returned as `Decimal` strings (`"1430.00"`) — parse with arbitrary-precision libs to avoid float drift.
- **Times**: ISO-8601 UTC (`2026-05-03T14:32:11.123Z`). Convert to BDT (UTC+6) for display.

---

## 1. Login (Auth)

| Endpoint | Notes |
|---|---|
| `POST /auth/register` | Email + password + full_name. Returns `{user_id}`. Triggers a verification code email. |
| `POST /auth/verify-email` | `{email, code}` → 204. Six-digit code from the email. |
| `POST /auth/login` | `{email, password}` → `{user, tokens: {access_token, refresh_token, token_type}}`. |
| `POST /auth/refresh` | `{refresh_token}` → fresh token pair. |
| `POST /auth/logout` | Invalidates the current session (idempotent). |
| `POST /auth/logout-all` | Invalidates every session for the calling user. |
| `POST /auth/password/forgot` | `{email}` → 204. Triggers a password-reset token email. |
| `POST /auth/password/reset` | `{token, new_password}` → 204. |
| `POST /auth/password/change` | `{old_password, new_password}` → 204. Requires auth. |
| `GET  /auth/me` | Returns the calling user. Use after token refresh to confirm validity. |

> **Phone OTP is paused** waiting for SMS provider creds. Email-password is the only login channel today. Once provider is wired (Module 1 unblocked), the mobile app should switch to phone-OTP as the primary identity per the BD-market product decision.

---

## 2. Search (Catalog)

| Endpoint | Notes |
|---|---|
| `GET /catalog/products` | Public. Query params: `q` (full-text), `category`, `brand_id`, `is_medicine`, `requires_prescription`, `sort` (`price`, `-price`, `name`, `-name`, `-created_at`), `page`, `size` (≤100). Returns `Page<ProductSummary>`. |
| `GET /catalog/products/{slug}` | Public. Full product detail including all variants + media + brand. |
| `GET /catalog/categories` | Public. Hierarchical category tree. |
| `GET /catalog/brands` | Public. Flat brand list. |

**Mobile tip**: cache the category tree + brand list locally — they change rarely. The product list is paginated; lazy-load page 2+ on scroll.

---

## 3. Order

| Endpoint | Auth | Notes |
|---|---|---|
| `POST /orders` | required | Body: `{items[{variant_id, quantity}], payment_method ('cod' \| 'online'), delivery_address, currency: 'BDT', notes?}`. Returns the created `OrderResponse` with `code` (e.g. `HSO-XXXXXXX`). |
| `GET /orders` | required | Paginated list of MY orders. |
| `GET /orders/{order_id}` | required | One of MY orders. |
| `POST /orders/{order_id}/cancel` | required | `{reason}`. Allowed only pre-packing. After packing, customer must contact support; admin cancellation is a separate flow. |

**Idempotency**: place_order is **NOT** idempotent today. The mobile app should disable the "Place order" button after first tap and show a spinner until the response arrives. A duplicate post will create a duplicate order.

---

## 4. Prescription

| Endpoint | Auth | Notes |
|---|---|---|
| `POST /prescriptions` | required | `multipart/form-data` with `file` (image or PDF, max 10 MB) + optional `patient_name`, `patient_phone`, `notes`. Returns the created `PrescriptionResponse` with `code`. |
| `GET /prescriptions` | required | Paginated list. |
| `GET /prescriptions/{id}` | required | One. |
| `GET /prescriptions/{id}/file` | required | Streams the original file bytes (image/pdf). Set `Accept: */*`. |

**Status flow** for the customer to surface:
`uploaded → in_review → approved | rejected | partial_approved`. The mobile app should poll on the order detail screen, or rely on the push-notification fan-out (see §7).

---

## 5. Reminder

| Endpoint | Auth | Notes |
|---|---|---|
| `GET /me/reminders` | required | Paginated. Filter by `status` (`pending`, `dispatched`, `sent`, `failed`, `cancelled`). |
| `GET /me/reminders/{id}` | required | One. |
| `POST /me/reminders/{id}/mark-taken` | required | Sets `taken_at = now()`. **Idempotent**: second call returns 422 (already taken). Does NOT alter dispatch status — the cron-driven sender is independent. |
| `POST /me/reminders/{id}/snooze` | required | `{minutes}` (1–360). For pending reminders, rewinds `scheduled_for` to the new time so the dispatcher waits. For sent/failed reminders, only `snoozed_until` is set (informational). Cannot snooze a taken reminder. |

A reminder row carries:
- `slot` (`morning`/`afternoon`/`night`)
- `scheduled_for` (UTC datetime)
- `medicine_label` (display name)
- `status` (dispatch state)
- `taken_at`, `snoozed_until` (customer markers — null until set)

**Mobile UX tip**: the home endpoint already returns the next 5 due reminders for the current 24h, so the home screen doesn't need a separate fetch.

---

## 6. Tracking

| Endpoint | Auth | Notes |
|---|---|---|
| `GET /track/orders/{code}?phone_last4=NNNN` | **public** | Anonymous track. Returns coarse status + timestamps + total. Wrong `phone_last4` → 404 (same as missing code) so the endpoint cannot be enumerated. |
| `GET /orders/{order_id}` | required | The signed-in version with full line detail + history. |

Mobile flow: signed-in users always use `/orders/{id}`. The anonymous `/track` endpoint exists for share links sent to recipients (e.g. a husband orders for his wife and forwards her the tracking link).

---

## 7. Mobile-only endpoints

### Profile
| Endpoint | Notes |
|---|---|
| `GET  /me/profile` | The signed-in user's row (subset of `/auth/me`). |
| `PATCH /me/profile` | `{full_name?, phone?}`. Changing `phone` resets `phone_verified_at` — phone-OTP rebinds when SMS provider is live. |

### Push-notification device tokens
| Endpoint | Notes |
|---|---|
| `POST /me/devices` | `{kind: 'fcm' \| 'apns' \| 'web', token, app_version?, locale?}`. Idempotent on `(user_id, token)` — call this on every app start so the latest `app_version` + `last_seen_at` stays fresh. |
| `GET /me/devices` | Lists active devices for the user (e.g. for a "log out from other devices" screen). |
| `DELETE /me/devices/{id}` | Soft-deactivates. Backend retains the row for past-delivery telemetry. |

### Saved addresses
| Endpoint | Notes |
|---|---|
| `GET /me/addresses` | Paginated. Default address (if any) appears first. |
| `POST /me/addresses` | Create. Setting `is_default: true` atomically demotes the previous default. |
| `PATCH /me/addresses/{id}` | Same default-promotion semantics. |
| `DELETE /me/addresses/{id}` | Hard delete. |

### Aggregated home
| Endpoint | Notes |
|---|---|
| `GET /mobile/home` | One round-trip payload for the home screen: `profile`, `default_address`, `recent_orders[5]`, `due_reminders[5]` (next 24h), `pending_prescriptions[5]`, `counters{active_orders, pending_prescriptions, due_reminders_24h}`. Drill-down screens hit the per-feature endpoints. |

---

## 8. Recommended app startup sequence

```
splash → token in keychain?
  ├─ no  → /auth/login → store tokens → continue
  └─ yes → GET /auth/me  (validate)
            ├─ 401  → drop tokens, go to /auth/login
            └─ 200  → continue

after sign-in:
  POST /me/devices         (refresh push token, every cold start)
  GET  /mobile/home        (home screen renders from this single payload)
```

## 9. What the backend does NOT do for mobile (pending)

- **Phone-OTP login** — backend has the schema (`users.phone`, `users.phone_verified_at`) but no SMS provider is wired. Email-password is the only credential today.
- **Online payment redirect** — checkout accepts `payment_method: 'online'` but the backend doesn't yet redirect to Bkash / SSLCommerz. Use `cod` for working orders today; payment-gateway integration is the next module after creds arrive.
- **Real-time push** — the device-token table exists but the FCM/APNs sender daemon isn't wired. Mobile app should poll for now (e.g. `/mobile/home` every 30s on the home screen).
- **WebSocket / SSE** — none. Polling only.
