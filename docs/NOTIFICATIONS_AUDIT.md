# Hypershop — Customer Notifications Fan-out Audit

**Audit date:** turn 35.
**Source-of-truth:** every `enqueue_outbox(type=...)` call across `app/modules/*/service.py` cross-referenced with every `register_handler(type, ...)` call across `app/modules/*/handlers.py`.

---

## Already wired (notifications customers receive)

### Email + SMS (IAM module)
| Event | Channel | Trigger |
|---|---|---|
| `iam.otp.email.send` | email | OTP login flow |
| `iam.otp.sms.send` | SMS (BD only) | OTP login flow |
| `iam.password_reset.email.send` | email | password forgot |
| `iam.password_changed.email.send` | email | password changed |

### WhatsApp + SMS fallback (invoice_dispatch module)
| Event | Channel | Trigger |
|---|---|---|
| `prescriptions.prescription.approved` | WhatsApp → SMS fallback | pharmacist approves Rx |
| `payment.captured` | WhatsApp → SMS fallback | online payment succeeds |

### Push (push module)
| Event | Title | Trigger |
|---|---|---|
| `orders.order.created` ✨ | "Order received" | NEW turn 35 — fills COD ack gap |
| `orders.order.payment_confirmed` | "Payment received" | online payment captured |
| `orders.order.approved` | "Order approved" | pharmacist greenlight |
| `orders.order.dispatched` | "Out for delivery" | rider picked up |
| `orders.order.completed` | "Order delivered" | POD captured |
| `orders.order.cancelled` | "Order cancelled" | admin / customer / system |
| `payment.failed` ✨ | "Payment didn't go through" | NEW turn 35 — gateway error |
| `payment.cancelled` ✨ | "Payment cancelled" | NEW turn 35 — customer abandoned at gateway |

### Reminders (reminders module)
- `doctor_rx.prescription.issued_app/no_app` → schedule per-medication reminders
- `doctor_rx.prescription.cancelled` → cancel pending reminders

---

## Gaps — events emitted but NOT triggering customer notification

### P1 — ✅ CLOSED in turn 36

Re-audit at wire-up time found that **all 4 events emit `order_id` directly in their payload** — the earlier "needs new resolver handler" assumption was wrong. Verified by reading `app/modules/prescriptions/service.py::_transition`, `app/modules/deliveries/service.py::_transition`, and the refund path in `app/modules/payments/service.py`. So the existing generic `_dispatch_for_event` handler routes them without code changes — just template additions.

| Event | Push title | Status |
|---|---|---|
| `payment.refund.succeeded` | "Refund issued" | ✅ wired turn 36 |
| `prescriptions.prescription.rejected` | "Prescription needs another look" | ✅ wired turn 36 |
| `prescriptions.prescription.partial_approved` | "Some Rx items couldn't be approved" | ✅ wired turn 36 |
| `deliveries.delivery.failed` | "Delivery attempt failed" | ✅ wired turn 36 |

Total change: 4 entries added to `_PUSH_TEMPLATES` in `app/modules/push/handlers.py`, ~16 lines, no new handlers, no resolver lookups, no DB joins. `py_compile` clean.

**Lesson:** always re-verify the payload contract at wire-up time before scoping handler refactors. The first audit assumed payload shape from event names; the second audit read the actual emitter code and found the work was 4× simpler.

### P2 — medium customer impact, defer until P1 is shipped

| Event | Why it matters | Notes |
|---|---|---|
| `prescriptions.prescription.uploaded` | acknowledgment that "we got your Rx" | redundant if customer is on a pharmacist-fast-review SLA; valuable if review takes hours |
| `orders.order.packing_started` | "your order is being packed" | feels noisy unless packing → dispatched takes > 1h |
| `orders.order.prescription_review_required` | "we need a prescription before we can fulfil" | needs a deep_link to Rx upload screen — implement when Rx upload flow exists in customer-web |
| `deliveries.delivery.assigned` | "rider [name] assigned, on the way" | only valuable if rider photo + ETA are included — otherwise add anxiety, not value |
| `deliveries.delivery.cod_discrepancy` | rider reported COD mismatch | internal only — customer doesn't need this; ops + finance do |
| `payment.expired` | intent timed out, customer needs to re-initiate | already covered indirectly by `orders.order.cancelled` if the system auto-cancels stale orders |

### P3 — out of scope without a marketing/CRM tier

| Event |
|---|
| Cart abandonment | not emitted today; would need a separate "cart" module in the backend |
| Welcome series after registration | beyond `iam.email_verify` |
| Re-engagement campaigns ("we miss you") | needs marketing tooling, not a transactional fan-out |
| Product back-in-stock alerts | needs subscription model |

---

## Recommended next pass (P1 closure)

Single ~80 LOC commit:

1. New module helper `_resolve_order_id_from_prescription(prescription_id)` and `_resolve_order_id_from_delivery(delivery_id)` in `app/modules/push/handlers.py`. Both ~10 LOC each (1 SQL lookup + UUID coerce).
2. Three new templates added to `_PUSH_TEMPLATES`:
   - `prescriptions.prescription.rejected` → "Prescription needs another look"
   - `prescriptions.prescription.partial_approved` → "Some items couldn't be approved"
   - `deliveries.delivery.failed` → "Delivery attempt failed"
3. Three new lightweight handlers (one per event type) that call the resolver and then defer to `_dispatch_for_event`-shaped logic.
4. `payment.refund.succeeded` added to existing `_PUSH_TEMPLATES` (zero new code, payload already has `order_id`).
5. Tests: add 4 unit tests covering each new handler's payload-shape branch.

---

## Channel selection rationale

Why push (not email/SMS) for order lifecycle:

- All BD customers have the app installed (assumption — verify via Mixpanel-equivalent if you have one)
- Push is free; SMS costs ~০.৩ BDT per send per BulkSMS BD
- Push has rich deep-link → tap goes to the relevant order
- Email is not a routine channel for BD e-commerce customers

Why WhatsApp (with SMS fallback) for invoice + Rx approval:

- WhatsApp has near-100% open rate in BD; receipts are reference material customers want forever
- Rx approval requires the customer to forward the document to family / doctor → WhatsApp is the destination
- SMS fallback covers customers who don't use WhatsApp (rare but real, esp. older demographic)

Why email for OTP + password:

- Email is the recovery channel of last resort — if SMS / WhatsApp is the only path and the customer's phone is lost, they can't get back in
- BD spam filters on transactional email are reasonable

These are choices reflected in the existing wiring; this audit doesn't second-guess them.

---

## Honest framing

| | Status |
|---|---|
| Existing fan-out fully audited | ✅ |
| 3 new push templates added (turn 35) | ✅ — `orders.order.created`, `payment.failed`, `payment.cancelled` |
| 4 P1 events wired (turn 36) | ✅ — `payment.refund.succeeded`, `prescriptions.prescription.rejected`, `prescriptions.prescription.partial_approved`, `deliveries.delivery.failed` |
| End-to-end tested in staging | ❌ — needs FCM dev token + test customer device |

The new push templates compile clean (`py_compile` verified), but I haven't fired a real notification through FCM to verify it lands on a real device. That's a Gate-6-style real-device check.
