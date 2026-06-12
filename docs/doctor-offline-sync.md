# Doctor offline-sync — server contract

How the doctor mobile/web app keeps writing prescriptions when the
device loses network, then auto-flushes them to the pharmacist
pipeline when connectivity returns.

> **Hard rule (server side)**: every prescription the doctor app pushes
> reaches the pharmacist. If auto-issue fails for any reason, the
> intake lands in `needs_review` for manual resolution — it is
> **never** bounced back to the doctor. The doctor's local queue
> empties as soon as the server says "received".

---

## 1. State machine

| Status | Set when | Where it shows |
|---|---|---|
| `received` | Intake row created, before auto-issue runs | rare transient state |
| `issued` | Auto-issue succeeded → real prescription created | doctor sees the Rx code; pharmacist sees nothing |
| `needs_review` | Auto-issue failed (validation, missing variant, etc.) | pharmacist's review queue |
| `cancelled` | Pharmacist cancelled the intake with a reason | terminal |

`issued` and `cancelled` are **terminal** — the pharmacist endpoints
refuse to mutate them.

---

## 2. Endpoints

### Doctor side

| Endpoint | Body |
|---|---|
| `POST /doctor-rx/sync/prescriptions` | `{items: [{client_uuid, client_created_at, payload}]}` (1–200 items per batch) |
| `POST /doctor-rx/sync/status` | `{client_uuids: [uuid, ...]}` — returns intake views for the ones the server knows about; missing UUIDs imply the client should re-push |

Both gated on `order.fulfill` + the service-layer `doctor_for_user` check (caller must be linked to an active `Doctor` row).

### Pharmacist side (admin router)

| Endpoint | Notes |
|---|---|
| `GET /admin/doctor-rx/intakes?status=needs_review` | Paginated review queue. Default `status=needs_review`. |
| `POST /admin/doctor-rx/intakes/{id}/issue` | Optional `payload` body replaces the doctor's payload with the pharmacist's edits. |
| `POST /admin/doctor-rx/intakes/{id}/cancel` | Body: `{reason}`. |

Pharmacist endpoints stay on `order.fulfill`. The issued prescription's `doctor_id` stays as the original prescriber; the pharmacist is recorded as the actor on the audit row.

---

## 3. Idempotency

The intake row is UNIQUE on `(doctor_id, client_uuid)`. The doctor app generates a UUID v4 per prescription **once** when it's first written offline, and reuses that UUID on every retry. So:

- First sync after reconnect → row created, status set
- Retry on flaky network → returns the same row, no duplicate Rx
- Retry months later → still returns the same row

The doctor app should treat `intake_id` (server-generated) as the authoritative server reference, but `client_uuid` (client-generated) as the local lookup key.

---

## 4. Recommended client-side queue contract

This part lives in the doctor app code, not the backend. Outline so both sides stay aligned.

### Local storage

- IndexedDB (web) / SQLite (Flutter / React Native) table:
  ```
  pending_prescriptions(
      client_uuid TEXT PRIMARY KEY,
      client_created_at TEXT,
      payload JSON,
      sync_status TEXT,      -- 'queued' | 'in_flight' | 'synced'
      last_sync_attempt TEXT,
      last_known_server_status TEXT  -- nullable until first sync
  )
  ```
- On "save Rx" tap: write a new row with `sync_status='queued'` and a fresh `client_uuid`.
- On `navigator.onLine` / connectivity event change → trigger flush.

### Flush algorithm

```
batch = SELECT * FROM pending_prescriptions WHERE sync_status='queued' LIMIT 100
mark each row sync_status='in_flight'

resp = POST /doctor-rx/sync/prescriptions {items: batch}

for item in resp.items:
    UPDATE pending_prescriptions
        SET sync_status = 'synced',
            last_known_server_status = item.status
        WHERE client_uuid = item.client_uuid

# Optional cleanup: a synced row whose server status is 'issued' or
# 'cancelled' can be deleted from the local queue.
```

### What the app should DISPLAY

- Offline write → "queued (will sync)" badge on the Rx
- After sync → server status: `issued` ✓ / `needs_review` ⚠ / `cancelled` ✗
- For `needs_review` → show the `error_message` so the doctor knows what the pharmacist will be looking at

### Retry policy

- Exponential backoff on full-network failure (sync endpoint not 200).
- **Per-item failures don't exist** — the endpoint is contractually 200 with per-item statuses. The client never has to "retry one row" — it only retries the whole batch on a 5xx / network error.

---

## 5. What this module does NOT do

- **Conflict resolution** — if the doctor edits the same Rx offline twice (same client_uuid), the **first sync wins**. Treat `client_uuid` as immutable per Rx.
- **End-to-end encryption** of the offline queue — that's a client-side concern (use OS keychain / Keystore for the IndexedDB key).
- **Push to pharmacist** — pharmacist polls the queue; once notifications are wired (FCM), this could be a real push.
- **Telephony / SMS to doctor** — when a needs_review intake is resolved by the pharmacist, the doctor app sees the new status only on the next `/sync/status` poll.
