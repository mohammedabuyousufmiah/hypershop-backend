# Hypershop FastAPI Backend — Session Log

**Date:** 2026-05-03
**Scope:** Foundation audit (Module 0) + Authentication system kickoff (Module 1)

---

## Workflow Rules (locked in for this project)

1. **One prompt = One module.** Never bundle modules.
2. **Never build everything together.** Each module ships in isolation.
3. **Every prompt ends with:** Tests (unit + integration) + Migrations (Alembic) + API docs (OpenAPI/Swagger annotations).
4. **No skipping sequence.** Modules build in dependency order.

Combined with existing project rules:
- **No placeholders, stubs, or fake adapters.** Pause for provider creds instead.
- Work happens in `hypershop_fastapi_backend/`. `hypershop_python_backend_v4/` is reference-only.

---

## Module 0 — Foundation (DELIVERED)

### Spec
- PostgreSQL + SQLAlchemy + Alembic
- Redis setup
- Modular folder structure
- Global error handler
- Standard API response format
- Request ID middleware
- Audit log base service
- JWT auth base
- Environment config
- Health endpoints (`GET /api/v1/health`, `GET /api/v1/ready`)

### Result — Audit Map

| Requirement | Location | Status |
|---|---|---|
| PostgreSQL + SQLAlchemy + Alembic | `app/core/db/`, `alembic/` | Async engine, sessionmaker, UoW, mixins, type registry |
| Redis setup | `app/core/cache.py` | Async Redis singleton + lifespan close |
| Modular folder structure | `app/core/`, `app/modules/` | Kernel vs feature split |
| Global error handler | `app/core/exception_handlers.py` | DomainError → envelope; SQLAlchemy → 409/503/500 |
| Standard API response format | `app/core/validation.py` | `ErrorEnvelope`, `StrictModel` (`extra=forbid`) |
| Request ID middleware | `app/core/middleware/request_id.py` | Mint or echo (regex-validated), bound to structlog |
| Audit log base service | `app/core/audit/` | In-transaction insert, PII redaction, REVOKE UPDATE/DELETE |
| JWT auth base | `app/core/security/jwt.py` | HS256, access/refresh kinds, sid/jti tracking |
| Environment config | `app/core/config.py` | pydantic-settings, secret-length validator |
| Health endpoints | `app/core/health/api.py` | Realigned to spec |

### Endpoints Shipped
- `GET /api/v1/health` → `{"status":"live"}` (200)
- `GET /api/v1/ready` → checks Postgres + Redis, 200 ready / 503 degraded
- `GET /api/v1/health/live`, `GET /api/v1/health/ready` — kept as hidden aliases for K8s probes

### Migrations
- `0001_init_audit_outbox_idem` — pgcrypto + `audit_log` + `outbox_messages` + `idempotency_keys`

### Docker
- `Dockerfile` — multi-stage, non-root user, gunicorn+uvicorn workers, healthcheck → `/api/v1/health`
- `docker-compose.yml` — postgres-16, redis-7, mailpit, api, worker
- `.env.example` — full env catalog with secret-generation hint

### Tests
- `tests/test_health.py` — `/health`, `/ready`, alias paths, request-id mint/echo, security headers, 404 envelope
- `tests/test_core_kernel.py` — ids/time/money/errors/passwords/JWT
- `tests/test_uow_audit_outbox.py` — transactional audit + outbox
- `tests/conftest.py` — Testcontainers OR external Postgres/Redis URL bootstrap

### Notes
- IAM (migration 0002) and catalog (migration 0003) modules already exist in the repo from prior sessions, technically violating the "one prompt = one module" cadence going forward. Foundation itself is now spec-compliant.

---

## Module 1 — Authentication System (PAUSED — awaiting decisions)

### Spec
- Mobile OTP login
- Admin password login
- JWT + refresh token rotation
- Role-based access control
- Permission middleware
- Audit log for login/logout
- Roles: `customer, admin, pharmacist, doctor, rider, supplier, finance`
- **Hard rule:** Customer mobile number = primary identity

### Already in place (from prior IAM scaffolding)
- JWT access + refresh with rotation + reuse detection
- RBAC middleware (`requires_permission`, `requires_role`)
- Audit logs for login/logout/register
- Argon2 passwords
- Email-based register/login/verify
- OTP infrastructure (currently email-only, used for verify and password reset)
- Sessions with theft detection

### Blockers (must be answered before code)

#### Blocker 1 — SMS provider for Mobile OTP
Email/SMTP transport exists. SMS does not. Need provider choice before writing the adapter.

Options:
| Provider | Notes |
|---|---|
| **SSL Wireless** | BD-domestic, common for BD e-commerce |
| **BulkSMSBD / SMSQ** | BD-domestic, cheap |
| **Twilio** | Global, BD coverage |
| **AWS SNS** | Global, AWS-native |
| **Vonage / MessageBird** | Global alternatives |

Best guess given BD market: **SSL Wireless**. Need: API base URL, sender ID, env var names for creds.

#### Blocker 2 — Schema change to make phone the primary identity
| Field | Now | Proposed |
|---|---|---|
| `users.email` | `NOT NULL UNIQUE` | `NULL`-able, `UNIQUE` |
| `users.phone` | `NULL`-able, `UNIQUE` | `NULL`-able, `UNIQUE` |
| `users.password_hash` | `NOT NULL` | `NULL`-able |
| Check constraint | none | `email IS NOT NULL OR phone IS NOT NULL` |
| Customer registration | email + password | phone-only → OTP issues `customer` role on first verify |
| Admin/staff login | email + password | unchanged |

#### Blocker 3 — Role-permission scope for new roles
Drop `staff`/`manager`. Add five new roles. Proposed default scopes:

- **customer** — own profile/cart/orders, browse catalog (unchanged)
- **admin** — wildcard `*` (unchanged)
- **pharmacist** — read products, validate prescriptions, fulfill Rx orders, read patient orders
- **doctor** — issue prescriptions, read own patients' orders
- **rider** — read assigned deliveries, update delivery status
- **supplier** — read/write own products, read own POs, read inventory of own products
- **finance** — read all orders/payments, issue refunds, read audit log, no write to catalog/inventory

Permission constants for prescriptions / deliveries / suppliers / finance will be **declared** (forward references) in this prompt and **enforced** in their respective module prompts later.

### What ships once Blockers 1–3 are resolved
- Migration `0004_iam_phone_identity` — schema change + role re-seed
- `app/modules/iam/models.py` — nullability changes, optional password_hash
- `app/modules/iam/permissions.py` — 7-role catalog + new permission constants
- `app/modules/iam/transport/sms_<provider>.py` — concrete SMS adapter
- `app/modules/iam/api/auth.py` — `POST /auth/otp/request`, `POST /auth/otp/verify`
- Updated services: phone-OTP login path that auto-creates a customer record on first verify
- Audit emission for the new flows
- Updated tests in `app/modules/iam/tests/` covering phone-OTP register/login, admin password login still works, role-permission map is correct

---

## Decisions still required from the user

1. SMS provider name + API base URL + sender ID + creds delivery channel
2. Confirm or push back on the phone-as-primary schema change above
3. Confirm or rewrite the proposed role scopes

### SMS readiness — staged for handoff

User confirmed test mobile **+8801911740672** (BD, Robi/Airtel) on 2026-05-03 and asked to "keep ready" for SMS provider integration. Stored in user memory (`user_phone.md`) for cross-session reference; never hardcoded into tests/fixtures/seeds.

Scaffolded interface (architecture, not a stub) at [`app/modules/iam/transport/sms_base.py`](../app/modules/iam/transport/sms_base.py):

- `SmsTransport` Protocol with one method: `async send(to, text)`
- Adapter rules documented in the module docstring (real HTTP only, env-only creds, failure→domain-error mapping, E.164 input contract, worker-safe)
- No default / no-op adapter — IAM service must raise `ServiceUnavailableError` if no real adapter is wired

When the user picks a provider, the next prompt should ship:

1. `app/modules/iam/transport/sms_<provider>.py` — concrete adapter implementing `SmsTransport`
2. Settings additions in `app/core/config.py` (provider-specific env vars)
3. `.env.example` block for the chosen provider
4. The schema change for "phone = primary identity" + role catalog updates (still blocked on confirmation)
5. `POST /auth/otp/request` and `POST /auth/otp/verify` endpoints, audit emission, and tests using a recorded-cassette HTTP fixture (no live network in CI)

---

## Module 2 — Medicine / Product Catalog (DELIVERED)

### Spec
- SKU auto-generate
- Mother SKU + variant SKU
- Generic + brand + strength required for medicine
- Prescription-required flag mandatory
- Minimum 3 images rule
- Barcode support
- Expired/blocked products hidden

### Result
Layered medicine/pharma rules onto the existing brand/category/product/variant/media scaffold without breaking it.

| Requirement | Location | Notes |
|---|---|---|
| SKU auto-generate | `app/modules/catalog/sku.py` | `HS-XXXXXXXX` mother SKU, scan-safe alphabet (no I/O/0/1) |
| Mother SKU + variant SKU | `Product.mother_sku`, `variant_sku_for()` | Variants default to `{mother_sku}-V{nnn}`; explicit SKUs preserved |
| Medicine required fields | `ProductCreate.model_validator` + DB CHECK `ck_products_medicine_required_fields` | Schema + DB defence in depth |
| Prescription flag mandatory | Pydantic + service-layer guard | `requires_prescription` must be set explicitly when `is_medicine=true` |
| Minimum 3 images | `CatalogService._enforce_min_images` + `update_product` activate path | Enforced on create-active and draft→active |
| Barcode support | `is_valid_barcode()` + variant `UNIQUE(barcode)` | Accepts EAN/UPC/GTIN/Code128, 8–64 alnum |
| Expired/blocked hidden | `_public_visibility_filter()` + `block_at`/`expires_at` on `Product` | Single filter applied to public list + slug detail |

### New columns on `products` (migration `0004_catalog_medicine`)
- `mother_sku` VARCHAR(40) NOT NULL UNIQUE — backfilled via `gen_random_bytes` for legacy rows
- `is_medicine` BOOLEAN NOT NULL
- `requires_prescription` BOOLEAN NOT NULL
- `generic_name` VARCHAR(200), `strength` VARCHAR(64), `dosage_form` VARCHAR(64)
- `expires_at`, `blocked_at` TIMESTAMPTZ, `blocked_reason` VARCHAR(255)
- CHECK `is_medicine = false OR (generic+strength+brand_id all set)`
- CHECK `(blocked_at IS NULL) = (blocked_reason IS NULL)`

### New endpoints
- `POST /api/v1/admin/catalog/products/{id}/block` — body: `{reason}`
- `POST /api/v1/admin/catalog/products/{id}/unblock`
- `PUT  /api/v1/admin/catalog/products/{id}/expiry` — body: `{expires_at}` (or null to clear)

### Tests added
- `app/modules/catalog/tests/test_sku.py` — SKU/barcode pure-function tests
- `app/modules/catalog/tests/test_catalog_medicine.py` — end-to-end:
  - Mother SKU format + variant SKU derivation + explicit-SKU passthrough
  - Medicine missing generic/strength/brand → 422
  - Medicine missing `requires_prescription` → 422
  - Active product with <3 images → 422
  - Draft→active promotion with <3 images → 422
  - Barcode invalid chars → 422; duplicate barcode → 409
  - Block hides from public list + detail; unblock restores
  - Past `expires_at` hides from public; rejected on active create
  - Block/unblock emit audit rows

### Existing tests updated
- `_make_product_payload` now provides 3 base images (was 1) so existing active-product tests stay green under the new rule.

### Audit emissions added
- `catalog.product.block`, `catalog.product.unblock`, `catalog.product.set_expiry`
- `catalog.product.create` metadata now includes `mother_sku`, `is_medicine`, `requires_prescription`

---

## Module 3 — Supplier Purchase + Batch Inventory (DELIVERED)

### Spec
- No stock without invoice
- Batch mandatory
- Expiry mandatory
- Expired stock auto-block
- Near-expiry alert
- Inventory buckets: `available`, `reserved`, `damaged`, `expired`, `blocked`

### Architecture
- **Stock ledger** (`stock_ledger`) is append-only with `REVOKE UPDATE, DELETE` from PUBLIC. Source of truth.
- **Stock balances** (`stock_balances`) is a per-(variant, batch, warehouse, bucket) cache. Updated inside the same transaction as the ledger insert; CHECK `quantity >= 0` is the safety net. Service layer locks the balance row with `SELECT ... FOR UPDATE` during reservation and consumption to prevent overselling.
- **Reservation algorithm** is FEFO across active non-blocked batches, expiry-soonest-first, ties broken by `created_at`.

### Tables added (migration `0005_inventory`)
| Table | Purpose |
|---|---|
| `suppliers` | Trading partners; optionally linked to a `users` row via `linked_user_id` |
| `warehouses` | Physical locations; default `MAIN` seeded in migration |
| `purchase_orders` + `purchase_order_lines` | Optional PO before receipt; line-level `quantity_received` tracks fulfillment |
| `goods_receipts` | The invoice gate. UNIQUE `(supplier_id, supplier_invoice_number)` to prevent duplicate booking |
| `goods_receipt_lines` | Per-variant per-batch receipt rows; `quantity > 0`, `unit_cost >= 0` |
| `batches` | UNIQUE `(variant_id, batch_number)`; `expiry_date` NOT NULL; CHECK `expiry_date >= manufacture_date` |
| `stock_ledger` | Append-only; `quantity_delta != 0`; bucket+kind enums; revoked UPDATE/DELETE |
| `stock_balances` | UNIQUE per `(variant, batch, warehouse, bucket)`; CHECK `quantity >= 0` |

### Hard-rule enforcement
| Rule | Where |
|---|---|
| No stock without invoice | `InventoryService.receive_goods` is the only code path that emits `kind=receipt` ledger rows. Adjustments use `adjust_in/adjust_out` and demand a written reason audited separately |
| Batch mandatory | `batch_id` NOT NULL on `goods_receipt_lines`, `stock_ledger`, `stock_balances` (DB-enforced) |
| Expiry mandatory | `batches.expiry_date` NOT NULL (DB) + `_validate_gr_line` rejects new-batch lines without expiry (service) |
| Expired auto-block | `expire_overdue_batches_job` ARQ cron (runs hourly at +5min) walks overdue batches, moves `available` + `reserved` → `expired`, marks `batch.status='expired'`, emits `inventory.batch.expired` outbox event. Reservation re-checks at lock time |
| Near-expiry alert | `near_expiry_scan_job` ARQ cron (daily 02:00 UTC) emits `inventory.batch.near_expiry` outbox events for batches expiring inside `INVENTORY_NEAR_EXPIRY_DAYS` (default 30). Notifications module registers the delivery handler |

### Endpoints added (`/api/v1/admin/inventory/...`)
- Suppliers: `POST /suppliers`, `GET /suppliers`, `PATCH /suppliers/{id}`
- Warehouses: `GET /warehouses`
- Purchase orders: `POST /purchase-orders`, `GET /purchase-orders`, `GET /purchase-orders/{id}`
- Goods receipts: `POST /goods-receipts`, `GET /goods-receipts`, `GET /goods-receipts/{id}`
- Batches: `GET /batches/{id}`, `POST /batches/{id}/block?reason=...`, `POST /batches/{id}/unblock`
- Stock query: `GET /stock/{variant_id}` (per-bucket summary), `GET /stock/{variant_id}/balances`
- Stock movements: `POST /stock/{variant_id}/reserve`, `POST /stock/release`, `POST /stock/consume`, `POST /stock/{variant_id}/damage`, `POST /stock/{variant_id}/adjust`
- Manual job triggers: `POST /jobs/expire-overdue`, `POST /jobs/near-expiry-scan`

RBAC: read endpoints require `inventory.read`; mutations require `inventory.receive` or `inventory.adjust`. These permission constants are already declared in `app/modules/iam/permissions.py` and seeded for the manager role; admin's wildcard covers them.

### Outbox event types declared (handlers register elsewhere)
- `inventory.batch.near_expiry`
- `inventory.batch.expired`
- `inventory.stock.received`

### Audit emissions
- `inventory.supplier.create`, `inventory.supplier.update`
- `inventory.purchase_order.create`
- `inventory.goods_receipt.create`
- `inventory.stock.reserve`, `inventory.stock.release`, `inventory.stock.consume`, `inventory.stock.damage`, `inventory.stock.adjust`
- `inventory.batch.block`, `inventory.batch.unblock`, `inventory.batch.expire`

### Tests
[app/modules/inventory/tests/test_inventory.py](../app/modules/inventory/tests/test_inventory.py) covers:
- Supplier CRUD + uniqueness
- Goods receipt creates `available` stock, duplicate (supplier, invoice_number) rejected with 409
- Receive without batch fields → 422
- Receive without expiry → 422
- Receive into already-expired batch → 422
- FEFO chooses earliest-expiry batch first
- Reserve refused when insufficient
- Release returns to `available`; consume drains `reserved`
- Damage moves to `damaged` bucket
- Block/unblock toggles `available` ↔ `blocked`
- Blocked batch excluded from FEFO reservation
- Auto-expire moves stock to `expired` bucket and marks batch
- Near-expiry scan emits an outbox event
- Receipt writes one ledger row + one audit row
- Customer (non-admin) blocked from supplier create (403); anonymous blocked (401)

### Files
- [app/modules/inventory/models.py](../app/modules/inventory/models.py)
- [app/modules/inventory/schemas.py](../app/modules/inventory/schemas.py)
- [app/modules/inventory/repository.py](../app/modules/inventory/repository.py)
- [app/modules/inventory/service.py](../app/modules/inventory/service.py)
- [app/modules/inventory/codes.py](../app/modules/inventory/codes.py)
- [app/modules/inventory/api/admin.py](../app/modules/inventory/api/admin.py)
- [app/modules/inventory/jobs.py](../app/modules/inventory/jobs.py) (ARQ cron entrypoints)
- [alembic/versions/0005_inventory.py](../alembic/versions/2026_05_03_0005-0005_inventory.py)
- [app/worker.py](../app/worker.py) — cron entries added
- [app/main.py](../app/main.py) — router included
- [app/core/db/registry.py](../app/core/db/registry.py) — model imports added

---

## Module 4 — Transaction-safe Stock Reservation (DELIVERED)

### Spec
- FEFO (first expiry first out)
- PostgreSQL row-level locking
- No overselling
- Payment success → reserve / Cancel → release / Delivery → deduct
- Concurrency tests

### What this prompt added on top of Module 3
The reservation core (FEFO + `SELECT ... FOR UPDATE` + `quantity >= 0` CHECK) was already in [InventoryService](../app/modules/inventory/service.py). This prompt adds:

1. **Caller-supplied `correlation_id`** on `reserve_stock` for idempotent retries (the orders/payments modules will pass `order_id` here).
2. **Order-keyed orchestrator** at [orchestrator.py](../app/modules/inventory/orchestrator.py) — `OrderStockOrchestrator` with three lifecycle methods:
   - `reserve_for_order(order_id, items)` — call on payment success. Idempotent on retry. All-or-nothing per call (rolls back if any line is short).
   - `release_for_order(order_id)` — call on cancel/refund. Idempotent.
   - `consume_for_order(order_id)` — call on delivery. Drains `reserved` so units leave the system.
3. **Concurrency-hole fix** in [repository.apply_movement](../app/modules/inventory/repository.py) — replaced the "lock-or-create" pattern with `INSERT ... ON CONFLICT DO NOTHING` followed by `SELECT ... FOR UPDATE`. Closes the race where two concurrent first-reservations into the same `(variant, batch, RESERVED)` would collide on the UNIQUE constraint and surface as raw `IntegrityError` instead of clean `ConflictError`.

### Locking model (PostgreSQL semantics)
- `apply_movement` upserts a balance row with `ON CONFLICT DO NOTHING` (returns immediately whether the row was new or existing).
- Then `SELECT ... FOR UPDATE` acquires an exclusive row lock for this transaction.
- All concurrent transactions touching the same `(variant_id, batch_id, warehouse_id, bucket)` serialize on this lock.
- The `CHECK quantity >= 0` constraint on `stock_balances` is the last-line guarantee — if a service-layer bug ever tried to decrement past zero, the COMMIT itself would fail.

### Concurrency tests — [test_reservation_concurrency.py](../app/modules/inventory/tests/test_reservation_concurrency.py)
| Test | What it proves |
|---|---|
| `test_concurrent_reserves_never_oversell` | 100 stock, 15× concurrent `reserve(10)` → exactly 10 succeed, 5 fail with insufficient. End: available=0, reserved=100 |
| `test_concurrent_reserve_with_unequal_demand_drains_available` | 50 stock, 10× concurrent `reserve(7)` → exactly 7 succeed, 3 fail. End: available=1, reserved=49 |
| `test_idempotent_reserve_for_same_order_id` | Payment-success retry calls `reserve_for_order(order_id=X)` twice → second returns existing reservation, no double-booking |
| `test_reserve_release_consume_lifecycle` | Reserve → cancel→release → re-reserve different order → deliver→consume. End-state matches expected math |
| `test_overselling_is_blocked_by_db_check_under_race` | 30× concurrent `reserve(1)` against 20 stock → 20 succeed, 10 fail; no `stock_balances` row ever has `quantity < 0` |
| `test_fefo_under_contention` | Two batches (early + late expiry), 6× concurrent reserves → early batch fully drained before late, even under contention |
| `test_release_writes_paired_ledger_legs` | Release emits 2 ledger rows: `(reserve, available, -N)`, `(reserve, reserved, +N)`, `(release, reserved, -N)`, `(release, available, +N)` — net zero across buckets |

Tests run via Testcontainers Postgres (each test gets a clean DB after `_truncate_between_tests`). Pool size 10 + max_overflow 5 = 15 concurrent connections, matching the largest fan-out test.

### Cross-module lifecycle wiring — end-to-end
The inventory side of the bus is now real. Handlers are registered on import (mirrors `iam/handlers.py`) and call the orchestrator inside their own UoW. The moment orders/payments emit a matching event, reservation/release/consume happen automatically.

**Event contracts** ([events.py](../app/modules/inventory/events.py)) — typed Pydantic schemas producers must honor:

| Event type | Payload | Effect |
|---|---|---|
| `payments.payment.succeeded` | `{order_id, items: [{variant_id, quantity}], warehouse_code?}` | FEFO reserve via orchestrator |
| `orders.order.cancelled` | `{order_id, reason?}` | Release reservation |
| `orders.order.delivered` | `{order_id, delivered_at?}` | Consume reservation (drain `reserved`) |

**Handlers** ([handlers.py](../app/modules/inventory/handlers.py)) — concrete, no stubs:
1. Validate payload via the Pydantic schema (malformed → typed `ValidationError` → dispatcher retries → dead-letter at attempt 8).
2. Open own `UnitOfWork.transactional()` scope (handlers run outside the dispatcher's tx).
3. Call into `OrderStockOrchestrator`, which is idempotent on `order_id`.
4. Insufficient-stock surfaces as `ConflictError` → retry with backoff. Never silently succeeds — ops sees the gap via `outbox_messages.last_error`.

**Registration** — auto-registered on import in both processes:
- `app/main.py` imports `app.modules.inventory.handlers` during `create_app()` — covers the API process.
- `app/worker.py` `_startup` imports the handler modules — covers the ARQ worker process. Without this, the worker would dead-letter every event since dispatcher routes by registered type.

**Tests** ([test_lifecycle_handlers.py](../app/modules/inventory/tests/test_lifecycle_handlers.py)) — six end-to-end proofs that round-trip through the real outbox:

| Test | What it proves |
|---|---|
| `test_payment_succeeded_event_drives_reservation` | Enqueue `payments.payment.succeeded` → `dispatch_once` → reservation booked, FEFO honoured |
| `test_payment_succeeded_redelivery_is_idempotent` | Duplicate event delivery → only one reservation lands; second is a no-op |
| `test_order_cancelled_event_drives_release` | Cancel after reserve → stock fully back to `available` |
| `test_order_delivered_event_drives_consume` | Deliver after reserve → reserved drained, total stock decreases |
| `test_malformed_payload_dead_letters_after_retries` | Bad UUID in payload → dispatcher captures `last_error`, schedules retry; never silently consumed |
| `test_payment_succeeded_with_insufficient_stock_is_retried` | Reserve > available → `ConflictError` → retry with backoff (visible in `last_error`), stock unchanged |

This isn't a placeholder — it's a fully-functional consumer with a real schema, a real DB transaction boundary, and real test coverage. The orders/payments modules just need to emit a matching event and the inventory side will do its job. No further wiring required.

---

## Module 5 — Order System (DELIVERED)

### Spec
Full e-commerce flow with state machine: `CART → CHECKOUT → PAYMENT/COD → STOCK_RESERVED → PRESCRIPTION_REVIEW → APPROVED → PACKING → DELIVERY → COMPLETE`.

Plus: status validation, audit log per transition, notification trigger (outbox events).

### State machine — [state.py](../app/modules/orders/state.py)
10 statuses with strict transition table:
```
PENDING_PAYMENT → PAYMENT_CONFIRMED → STOCK_RESERVED →
    (PRESCRIPTION_REVIEW →) APPROVED → PACKING → OUT_FOR_DELIVERY → COMPLETED
                                                                  ↓ CANCELLED (terminal)
                                                                  ↓ FAILED   (insufficient stock)
```
- `CUSTOMER_CANCELLABLE_STATES` — customers can self-cancel before packing; admin override required afterwards.
- `assert_can_transition()` raises `TransitionError` → service maps to `BusinessRuleError` (422) with `from`/`to` details.

### Tables ([migration 0006](../alembic/versions/2026_05_03_0006-0006_orders.py))
- **`orders`** — header. Money columns, currency CHECK (ISO-3 upper), status CHECK enum, `requires_prescription` snapshot bool, denormalized `delivery_address` JSONB, milestone timestamps (`placed_at`, `payment_confirmed_at`, `approved_at`, `dispatched_at`, `completed_at`, `cancelled_at`), `assigned_pharmacist_id`.
- **`order_lines`** — per-variant snapshot (`product_name`, `variant_sku`, `requires_prescription`, `unit_price`, `line_total`). Snapshot survives later product edits.
- **`order_status_history`** — append-only transition log. `REVOKE UPDATE, DELETE` from PUBLIC. Same defence pattern as `audit_log` and `stock_ledger`.

### Service flow — [service.py](../app/modules/orders/service.py)
**Place (COD):** `_snapshot_variants` → create order in `payment_confirmed` → emit `orders.order.created` + `orders.order.payment_confirmed` → `_post_payment_confirmed_work`.

**Place (online):** `_snapshot_variants` → create order in `pending_payment` → emit `orders.order.created`.

**`_post_payment_confirmed_work`:** opens a SAVEPOINT, calls `OrderStockOrchestrator.reserve_for_order` inline. On `ConflictError` (insufficient stock) → savepoint rolls back → transition to `failed`, emit `orders.order.reservation_failed`. On success → transition to `stock_reserved`, then to `approved` (no Rx) or `prescription_review` (any Rx line).

**Online `confirm_payment`:** transitions `pending_payment → payment_confirmed`, emits event, then runs `_post_payment_confirmed_work`.

### Endpoints
**Customer** ([api/customer.py](../app/modules/orders/api/customer.py)):
- `POST /api/v1/orders` — place order (COD or online)
- `GET  /api/v1/orders` — list own orders
- `GET  /api/v1/orders/{id}` — own order detail (403 if not owner)
- `POST /api/v1/orders/{id}/cancel` — self-cancel (only in cancellable states)

**Admin/staff** ([api/admin.py](../app/modules/orders/api/admin.py)):
- `GET  /api/v1/admin/orders` — paginated, filterable
- `GET  /api/v1/admin/orders/{id}` — detail
- `POST /api/v1/admin/orders/{id}/confirm-payment` — online-payment hook
- `POST /api/v1/admin/orders/{id}/approve-prescription` — pharmacist gate
- `POST /api/v1/admin/orders/{id}/start-packing`
- `POST /api/v1/admin/orders/{id}/dispatch`
- `POST /api/v1/admin/orders/{id}/complete` — emits `orders.order.completed` → inventory consumes
- `POST /api/v1/admin/orders/{id}/cancel` — admin override

### Outbox events emitted
`orders.order.created`, `orders.order.payment_confirmed`, `orders.order.stock_reserved`, `orders.order.reservation_failed`, `orders.order.prescription_review_required`, `orders.order.approved`, `orders.order.packing_started`, `orders.order.dispatched`, `orders.order.completed`, `orders.order.cancelled`.

### Cross-module wiring (no placeholders)
- **Reserve on payment**: orders module calls `OrderStockOrchestrator.reserve_for_order` *inline* inside its own SAVEPOINT — not via outbox event — because we need synchronous failure handling (must transition to FAILED if insufficient stock). The inventory handlers DO NOT subscribe to `orders.order.payment_confirmed` (would race the inline call); they remain subscribed to `payments.payment.succeeded` for hypothetical direct payment-module producers.
- **Release on cancel**: orders emits `orders.order.cancelled`. Inventory handler picks it up and releases. Idempotent.
- **Consume on completion**: orders emits `orders.order.completed`. Inventory handler picks it up and consumes. Renamed from `orders.order.delivered` in module 4 to align producer/consumer naming.
- **Consumer payload schemas** ([inventory/events.py](../app/modules/inventory/events.py)) switched from `extra="forbid"` to `extra="ignore"` so producers can include extra metadata (codes, transition info, notification context) without breaking strict consumers.

### Audit + notification
Every transition writes:
1. A row in `order_status_history` (append-only).
2. A row in `audit_log` with action `orders.order.transition.<status>`.
3. An outbox message of the corresponding event type.

Consumers (notifications module when shipped) subscribe to the outbox events; nothing in this prompt assumes the notifications module exists.

### Tests — [test_orders.py](../app/modules/orders/tests/test_orders.py) (16 integration tests)
| Test | Proves |
|---|---|
| `test_cod_order_auto_advances_to_approved` | COD with no Rx → goes straight to `approved`, stock reserved |
| `test_cod_full_lifecycle_to_completed` | COD → packing → dispatch → complete; inventory consume runs via outbox handler |
| `test_online_order_starts_pending_payment` | Online order stays in `pending_payment` until confirmation; no early reservation |
| `test_online_payment_confirmation_reserves_and_approves` | `confirm-payment` advances and reserves atomically |
| `test_cod_insufficient_stock_marks_failed` | Savepoint rolls back inventory writes; order ends in `failed`; stock untouched |
| `test_prescription_order_routes_to_review` | Rx product → `prescription_review` instead of auto-approve |
| `test_pharmacist_approves_prescription` | Pharmacist endpoint advances `prescription_review → approved` and assigns pharmacist id |
| `test_cannot_dispatch_before_packing` | State-machine guard returns 422 with `from`/`to` details |
| `test_cannot_complete_from_approved` | Same — skipping packing rejected |
| `test_customer_can_cancel_before_packing` | Cancel emits event; outbox release returns stock to `available` |
| `test_customer_cannot_cancel_after_packing` | 422 — customer past packing must use admin |
| `test_admin_can_cancel_anywhere` | Admin override works from `packing` |
| `test_customer_cannot_view_others_order` | Cross-customer fetch → 403 |
| `test_anon_cannot_place_order` | 401 |
| `test_customer_cannot_use_admin_endpoints` | 403 |
| `test_each_transition_writes_history_and_audit` | Every status change has both a history row and an audit row |
| `test_failed_path_writes_history_and_no_reservation` | FAILED order has no inventory ledger rows (savepoint rolled back) |
| `test_rejects_inactive_variant`, `test_rejects_zero_quantity` | Validation 422s |

### Files
- [app/modules/orders/state.py](../app/modules/orders/state.py)
- [app/modules/orders/codes.py](../app/modules/orders/codes.py)
- [app/modules/orders/events.py](../app/modules/orders/events.py)
- [app/modules/orders/models.py](../app/modules/orders/models.py)
- [app/modules/orders/schemas.py](../app/modules/orders/schemas.py)
- [app/modules/orders/repository.py](../app/modules/orders/repository.py)
- [app/modules/orders/service.py](../app/modules/orders/service.py)
- [app/modules/orders/api/_serializers.py](../app/modules/orders/api/_serializers.py)
- [app/modules/orders/api/customer.py](../app/modules/orders/api/customer.py)
- [app/modules/orders/api/admin.py](../app/modules/orders/api/admin.py)
- [alembic/versions/0006_orders.py](../alembic/versions/2026_05_03_0006-0006_orders.py)
- Updated [app/main.py](../app/main.py), [app/core/db/registry.py](../app/core/db/registry.py)
- Updated [inventory/events.py](../app/modules/inventory/events.py), [inventory/handlers.py](../app/modules/inventory/handlers.py): renamed `orders.order.delivered` → `orders.order.completed`, switched consumer schemas to `extra="ignore"`

---

## Module 6 — Delivery Pricing (DELIVERED)

### Spec
- Service area = 50 BDT (own delivery, flat)
- COD charge = 0 (no surcharge for cash-on-delivery)
- 3PL = 70–150 BDT range (third-party logistics)

### What's in
- `delivery_zones` table (migration 0007) seeded with Dhaka Metro (service_area, 50), Greater Dhaka (3pl, 100), Outside Dhaka (3pl, 130).
- DB CHECK constraints: `kind ∈ {service_area, 3pl}`; `price ≥ 0`; `kind='3pl' → 70 ≤ price ≤ 150`. Service-area exact value (50) enforced at the schema layer for evolvability.
- Partial unique index `WHERE is_default = true` so only one zone is the fallback at any time. Repository auto-demotes the previous default when promoting a new one.
- Matching algorithm (first-match-wins): postal code → city (case-insensitive) → default zone → 404.
- COD surcharge surfaced as a separate `cod_fee` field in the quote (always 0 today; rule change becomes a one-line edit).

### Endpoints
**Public**:
- `POST /api/v1/delivery/quote` — body: `{address: {city, postal_code?, country}, payment_method}` → returns `{zone_code, zone_name, kind, base_fee, cod_fee, total, currency}`. No auth.
- `GET /api/v1/delivery/zones` — list active zones.

**Admin** (gated on `catalog.product.write` — when 7-role IAM lands this becomes a dedicated `delivery.zone.write` permission):
- `GET    /api/v1/admin/delivery/zones`
- `POST   /api/v1/admin/delivery/zones`
- `PATCH  /api/v1/admin/delivery/zones/{id}`
- `DELETE /api/v1/admin/delivery/zones/{id}`

### Audit
`delivery.zone.create`, `delivery.zone.update`, `delivery.zone.delete`.

### Tests — [test_delivery.py](../app/modules/delivery/tests/test_delivery.py)
| Test | Proves |
|---|---|
| `test_quote_for_dhaka_metro_returns_50` | Service-area rule: flat 50 BDT |
| `test_quote_for_3pl_zone_returns_band_price` | 3PL zone returns price within 70–150 |
| `test_quote_cod_charge_is_zero_regardless_of_zone` | COD fee = 0 across both kinds |
| `test_quote_falls_back_to_default_zone` | Unknown city → default zone |
| `test_quote_postal_code_overrides_city` | Postal-code match wins over city match |
| `test_public_zones_lists_active_only` | Inactive zones hidden from public list |
| `test_service_area_must_be_50` | Pricing rule rejects non-50 service-area |
| `test_3pl_must_be_in_band` | Rejects 60 (too low) and 200 (too high) |
| `test_3pl_at_band_edges_accepted` | Exactly 70 and exactly 150 accepted |
| `test_setting_new_default_unsets_previous` | Default-zone uniqueness enforced |
| `test_zone_create_writes_audit` | Audit row written |
| `test_customer_cannot_create_zone` (403), `test_anon_cannot_create_zone` (401), `test_quote_is_public` | RBAC |

A delivery-tests conftest re-seeds zones + IAM reference rows on each test (the shared truncate fixture wipes them between tests, breaking dependent fixtures otherwise).

### Files
- [app/modules/delivery/models.py](../app/modules/delivery/models.py)
- [app/modules/delivery/schemas.py](../app/modules/delivery/schemas.py) — pricing rule centralized in `_enforce_kind_price`
- [app/modules/delivery/repository.py](../app/modules/delivery/repository.py)
- [app/modules/delivery/service.py](../app/modules/delivery/service.py)
- [app/modules/delivery/api/](../app/modules/delivery/api/) — public + admin
- [alembic/versions/0007_delivery.py](../alembic/versions/2026_05_03_0007-0007_delivery.py)
- [app/modules/delivery/tests/conftest.py](../app/modules/delivery/tests/conftest.py), [test_delivery.py](../app/modules/delivery/tests/test_delivery.py)
- Wired into [main.py](../app/main.py) and [registry.py](../app/core/db/registry.py)

### Sequence note
The orders module (Module 5) has a `shipping_total` column but doesn't yet call the delivery service to populate it — orders ship today with `shipping_total = 0`. Wiring orders → delivery is a small follow-up that fits a future prompt; it doesn't block this module's contract.

### Payments — gateways confirmed, blocked on remaining details
User confirmed **Bkash + SSLCommerz** as online gateways on 2026-05-03 (saved to memory: [project_payment_gateways.md](../../../.claude/projects/.../memory/project_payment_gateways.md)). Still need before payment module ships:
1. Sandbox vs production endpoint URLs
2. Credentials delivery (env vars `BKASH_*`, `SSLCOMMERZ_*` or secrets manager)
3. Webhook URL pattern the providers will POST to

The double-entry ledger half is provider-agnostic and could be specced in advance, but per the no-placeholders rule a concrete adapter cannot ship without these.

---

## Module 7 — Prescription System (DELIVERED)

### Spec
- Customer uploads prescription (multipart)
- AI OCR optional (interface only — no concrete provider yet)
- Pharmacist approval mandatory
- "No approval → no packing" — linked order can't advance from `prescription_review` without it
- Flow: `upload → review → approve | reject | partial_approve`

### State machine ([state.py](../app/modules/prescriptions/state.py))
| From | To | Notes |
|---|---|---|
| `uploaded` | `in_review` | Pharmacist picks up |
| `uploaded` | `rejected` | Obvious junk — early reject |
| `in_review` | `approved` | Linked order advances to `approved` inline |
| `in_review` | `rejected` | Linked order is cancelled inline |
| `in_review` | `partial_approved` | Linked order stays in `prescription_review` for support follow-up |
| Terminal | — | `approved`, `rejected`, `partial_approved` are all terminal |

### File storage
[storage.py](../app/modules/prescriptions/storage.py) — `PrescriptionStorage` writes to `settings.prescription_storage_dir`. Atomic per-file (`.tmp` + rename), path-traversal protection, MIME allowlist (`jpg/png/webp/pdf`), max-bytes config. SHA-256 captured for dedup. **No remote storage** (S3/CDN) shipped — the dir must be a shared persistent volume in multi-pod deploys.

### OCR — interface only
[ocr.py](../app/modules/prescriptions/ocr.py) ships an `OcrProvider` Protocol with adapter rules in the docstring. **No concrete adapter and no no-op fallback** — per the no-placeholders rule, `ocr_status` simply stays at `skipped` for every prescription until a provider is wired. Pharmacist review is mandatory regardless of OCR; OCR is an assist, not a gate.

### Tables (migration 0008)
- `prescriptions` — header with file metadata (path, name, mime, size, sha256), OCR fields (`ocr_status`, `ocr_text`, `ocr_confidence`, `ocr_provider`), patient context (`patient_name`, `patient_age_years`, `doctor_name`), review tracking (`assigned_pharmacist_id`, `reviewed_at`, `review_notes`, `rejected_reason`), CHECK constraints on enums + numeric ranges, indexed by `(customer_user_id, status)`, `order_id`, `status`, `file_sha256` (for future dedup).

### Endpoints
**Customer** ([api/customer.py](../app/modules/prescriptions/api/customer.py)):
- `POST /api/v1/prescriptions` — multipart upload (file + optional `order_id`/patient/doctor metadata)
- `GET /api/v1/prescriptions` — list own
- `GET /api/v1/prescriptions/{id}` — own detail (403 if not owner)
- `GET /api/v1/prescriptions/{id}/file` — download own file

**Admin/Pharmacist** ([api/admin.py](../app/modules/prescriptions/api/admin.py)):
- `GET /api/v1/admin/prescriptions` — paginated, filterable
- `GET /api/v1/admin/prescriptions/{id}` — detail
- `GET /api/v1/admin/prescriptions/{id}/file` — download
- `POST /api/v1/admin/prescriptions/{id}/start-review` — `uploaded → in_review`
- `POST /api/v1/admin/prescriptions/{id}/approve` — `in_review → approved`; **advances linked order**
- `POST /api/v1/admin/prescriptions/{id}/reject` — `in_review → rejected`; **cancels linked order**
- `POST /api/v1/admin/prescriptions/{id}/partial-approve` — `in_review → partial_approved`; order untouched

RBAC: customer endpoints use `order.place`/`order.read.self`. Admin endpoints use `order.fulfill`/`order.read.any`. When the IAM 7-role catalog ships, these can split into dedicated `prescription.upload` and `prescription.review` permissions.

### Cross-module wiring (no placeholders)
The prescription service calls `OrderService.approve_prescription` / `OrderService.cancel_by_admin` **inline** in the same transaction as the prescription transition. Either both succeed or both roll back — no half-state where the prescription is approved but the order is stuck.

### Outbox events emitted
`prescriptions.prescription.uploaded`, `review_started`, `approved`, `rejected`, `partial_approved`. Notifications module will subscribe.

### Audit
`prescriptions.prescription.upload` + `prescriptions.prescription.transition.<status>` per transition.

### Tests — [test_prescriptions.py](../app/modules/prescriptions/tests/test_prescriptions.py) (16 integration tests)
| Test | Proves |
|---|---|
| `test_customer_uploads_prescription` | Multipart upload accepted; row created in `uploaded`; `ocr_status="skipped"`; SHA-256 captured |
| `test_upload_rejects_bad_mime` | 422 on `text/plain` |
| `test_upload_rejects_empty_file` | 422 on empty bytes |
| `test_anon_cannot_upload` | 401 without auth |
| `test_customer_lists_own_prescriptions_only` | List filters by owner |
| `test_customer_cannot_view_others` | 403 on cross-customer detail fetch |
| `test_customer_can_download_own_file` | File round-trips byte-perfect |
| `test_review_lifecycle_uploaded_to_in_review_to_approved` | Full happy path; pharmacist id captured |
| `test_cannot_approve_uploaded_directly` | State machine guard returns 422 |
| `test_cannot_re_approve_terminal` | 422 on re-approving an already-approved Rx |
| `test_approve_advances_linked_order` | "No approval → no packing": approve advances order from `prescription_review` to `approved` |
| `test_reject_cancels_linked_order` | Reject cancels the linked order with reason |
| `test_partial_does_not_advance_order` | Partial leaves order in `prescription_review` for manual follow-up |
| `test_upload_with_others_order_id_rejected` | 403 on linking another customer's order |
| `test_customer_cannot_approve` | RBAC denial |
| `test_lifecycle_writes_audit` | Audit row per transition |
| `test_upload_emits_outbox_event` | `prescriptions.prescription.uploaded` outbox row written |
| `test_uploaded_file_is_stored_and_hash_matches` | SHA-256 in DB matches the actual stored bytes |

A [conftest.py](../app/modules/prescriptions/tests/conftest.py) re-seeds IAM reference rows and points `PRESCRIPTION_STORAGE_DIR` at a per-test tempdir.

### Files
- [app/modules/prescriptions/state.py](../app/modules/prescriptions/state.py)
- [app/modules/prescriptions/codes.py](../app/modules/prescriptions/codes.py)
- [app/modules/prescriptions/storage.py](../app/modules/prescriptions/storage.py)
- [app/modules/prescriptions/ocr.py](../app/modules/prescriptions/ocr.py)
- [app/modules/prescriptions/events.py](../app/modules/prescriptions/events.py)
- [app/modules/prescriptions/models.py](../app/modules/prescriptions/models.py)
- [app/modules/prescriptions/schemas.py](../app/modules/prescriptions/schemas.py)
- [app/modules/prescriptions/repository.py](../app/modules/prescriptions/repository.py)
- [app/modules/prescriptions/service.py](../app/modules/prescriptions/service.py)
- [app/modules/prescriptions/api/](../app/modules/prescriptions/api/)
- [alembic/versions/0008_prescriptions.py](../alembic/versions/2026_05_03_0008-0008_prescriptions.py)
- Updated [main.py](../app/main.py), [registry.py](../app/core/db/registry.py), [config.py](../app/core/config.py), [.env.example](../.env.example)

---

## Module 8 — Doctor Prescription System (DELIVERED)

### Spec
- Link with customer mobile (auto-link to user account when phone matches)
- App → profile add (event-driven)
- No app → SMS PDF — **email-PDF only for now**, SMS stub deferred until provider chosen
- Print fallback (PDF rendered + persisted at issue time)
- AI suggest from inventory only
- Doctor selects manually
- Per-line dosage: morning / afternoon / night booleans + duration_days + notes
- APIs, monthly reset job, wallet logic = **customer credit wallet** (Option A)

### Architecture choices (locked in this prompt)
- **Wallet shape = customer credit** — doctor optionally grants credit at issue; customer can view balance/credits/transactions
- **Doctor identity = `doctors` table independent of `users`** with optional `linked_user_id`
- **Patient identity = `patient_phone` plain text**, auto-link to existing user account when `users.phone` matches (works today for users who registered with a phone; becomes universal once Auth Module 1 phone-as-primary lands)
- **PDF library = `fpdf2`** (added to `pyproject.toml` and `Dockerfile`)
- **Customer wallet rollover = configurable** via `WALLET_ROLLOVER_PERCENT` env var (default 0)

### Tables (migration 0009)
| Table | Purpose |
|---|---|
| `doctors` | Doctor identity. UNIQUE on `code`, `license_number`, `linked_user_id` |
| `doctor_prescriptions` | Header — `patient_phone`, optional `patient_user_id`, `pdf_path`, optional `wallet_credit_id` |
| `doctor_prescription_lines` | M/A/N booleans + `duration_days` + notes; CHECK ensures at least one slot per line |
| `customer_wallets` | 1:1 with `users` |
| `wallet_credits` | Per-grant tracking with `remaining_amount` (mutable), `expires_at`, status enum |
| `wallet_transactions` | Append-only audit ledger; REVOKE UPDATE/DELETE |

### Endpoints
**Doctor** ([api/doctor.py](../app/modules/doctor_rx/api/doctor.py)):
- `POST /api/v1/doctor-rx/suggest` — inventory-backed search; returns only variants with available stock > 0
- `POST /api/v1/doctor-rx/prescriptions` — issue prescription (with optional credit grant)
- `GET /api/v1/doctor-rx/prescriptions` — list own
- `GET /api/v1/doctor-rx/prescriptions/{id}` — detail
- `GET /api/v1/doctor-rx/prescriptions/{id}/pdf` — print/download
- `POST /api/v1/doctor-rx/prescriptions/{id}/cancel`

All doctor endpoints check that the caller is linked to an active `Doctor` row (gated *in addition to* `order.fulfill` RBAC).

**Customer** ([api/customer.py](../app/modules/doctor_rx/api/customer.py)):
- `GET /api/v1/me/doctor-prescriptions` — list own
- `GET /api/v1/me/doctor-prescriptions/{id}` — detail
- `GET /api/v1/me/doctor-prescriptions/{id}/pdf` — download
- `GET /api/v1/me/wallet` — balance + active credit count
- `GET /api/v1/me/wallet/credits` — active credits with their expiries (FEFO order)
- `GET /api/v1/me/wallet/transactions` — paginated ledger view

**Admin** ([api/admin.py](../app/modules/doctor_rx/api/admin.py)):
- `POST /api/v1/admin/doctor-rx/doctors` — onboard a doctor
- `GET /api/v1/admin/doctor-rx/doctors` — list
- `PATCH /api/v1/admin/doctor-rx/doctors/{id}`
- `POST /api/v1/admin/doctor-rx/wallets/{customer_user_id}/adjust` — positive grants, negative redeems
- `POST /api/v1/admin/doctor-rx/jobs/expire-wallets` — manual wallet expiry sweep

### Wallet mechanics
- Each credit has `expires_at = today + WALLET_CREDIT_LIFETIME_DAYS` (default 30)
- Redemption is FEFO with `SELECT ... FOR UPDATE` row-locking on credits
- Balance = `SUM(remaining_amount) WHERE status='active'`
- Monthly cron ([jobs.py](../app/modules/doctor_rx/jobs.py)) runs daily at 01:00 UTC, processes overdue credits:
  - Marks `status='expired'` (or `'rolled_over'`), wipes `remaining_amount`
  - Writes an `expire` ledger row
  - If `WALLET_ROLLOVER_PERCENT > 0`, grants a fresh credit equal to that percent of the unused remainder with a new expiry; writes a `rollover` ledger row

### Outbox events emitted
- `doctor_rx.prescription.issued_app` — patient has Hypershop account; notifications module handles in-app push
- `doctor_rx.prescription.issued_no_app` — patient phone has no account; notifications dispatches email-PDF (and SMS-PDF when provider lands)
- `doctor_rx.prescription.cancelled`
- `wallet.credit_granted` (declared but not emitted in this prompt — wallet-grant audit happens via the audit_log)
- `wallet.credit_expired` (same — surfaced via audit + ledger)

### PDF generation
[pdf.py](../app/modules/doctor_rx/pdf.py) — A5 layout with patient block, diagnosis, line list (M/A/N + duration), advice, footer with prescription code. Atomic write (`.tmp` + rename), path-traversal protection. Storage dir = `settings.doctor_rx_pdf_dir` — must be a shared persistent volume in multi-pod deploys.

### Cross-module wiring (no placeholders)
- Issuance calls `WalletService.grant` inline if `credit_amount` provided
- No outbox-driven side effects on inventory or orders — doctor prescriptions are advisory
- `notifications` module (Phase 6) will consume the issued events to dispatch app push / email / SMS

### Tests — [test_doctor_rx.py](../app/modules/doctor_rx/tests/test_doctor_rx.py) (16 integration tests)
| Test | Proves |
|---|---|
| `test_admin_creates_doctor` | Doctor onboarding works; auto-codes generated |
| `test_doctor_license_unique` | UNIQUE constraint enforced |
| `test_customer_cannot_admin_doctors` | RBAC denial 403 |
| `test_suggest_returns_in_stock_variants_only` | Inventory-backed filter excludes 0-stock variants |
| `test_suggest_requires_doctor_record` | Caller must be linked to an active Doctor |
| `test_issue_prescription_renders_pdf_and_emits_event` | PDF generated; outbox event matches no-app path |
| `test_issue_requires_at_least_one_dose_slot` | DB CHECK + service guard reject empty dosage |
| `test_issue_links_to_existing_user_by_phone` | Auto-link on `users.phone` match; `issued_app` event emitted |
| `test_pdf_is_downloadable_after_issue` | Round-trips real PDF bytes (`%PDF-` magic) |
| `test_doctor_can_cancel_own` | Cancel transition + reason captured |
| `test_grant_credit_via_prescription` | `credit_amount` flows into wallet; balance reflects 500 BDT |
| `test_grant_credit_refused_when_no_account` | 422 when patient has no Hypershop account |
| `test_admin_adjust_wallet_grant_and_redeem` | Positive grants, negative redeems; balance arithmetic correct |
| `test_redeem_refused_when_insufficient` | 409 when balance below redeem amount |
| `test_monthly_expire_job_clears_overdue_credits` | Force-aged credit expires via the cron; balance drops to 0 |
| `test_wallet_transactions_listed` | Audit-style ledger view available to customer |
| `test_doctor_rx_lifecycle_emits_audit` | `doctor_rx.issue` + `doctor_rx.cancel` audit rows |

A [conftest.py](../app/modules/doctor_rx/tests/conftest.py) re-seeds IAM reference rows and points `DOCTOR_RX_PDF_DIR` at a per-test tempdir.

### Files
- [app/modules/doctor_rx/state-less, model files](../app/modules/doctor_rx/) — codes, models, schemas, repository, service, wallet, pdf, jobs, events, api/
- [alembic/versions/0009_doctor_rx.py](../alembic/versions/2026_05_03_0009-0009_doctor_rx.py)
- Updated [pyproject.toml](../pyproject.toml), [Dockerfile](../Dockerfile), [config.py](../app/core/config.py), [.env.example](../.env.example), [main.py](../app/main.py), [worker.py](../app/worker.py), [registry.py](../app/core/db/registry.py)

### Still pending
- **SMS provider** — needed for SMS-PDF delivery (the "no app → SMS PDF" path)
- **Phone-as-primary auth** (Module 1) — needed for the patient auto-link to be universal rather than opt-in
- **Notifications module** — to actually deliver the outbox events as app push / email / SMS

---

## Module 9 — Medication Reminder System (DELIVERED)

### Spec
- Auto-create from prescription
- Morning/afternoon/night
- Push → SMS fallback (consumer-side, in notifications module — see below)
- Stop after duration

### What landed
- **Auto-create**: handler subscribed to `doctor_rx.prescription.issued_app` and `_no_app` walks every line × slot × day, decides channel (push if patient has account, else SMS), and bulk-inserts the reminder rows. Idempotent on `prescription_id` so outbox redelivery is safe.
- **Cancel propagation**: handler subscribed to `doctor_rx.prescription.cancelled` flips every still-pending or still-dispatched reminder to `cancelled`.
- **Slot timing**: configurable via `REMINDER_MORNING_HHMM` / `REMINDER_AFTERNOON_HHMM` / `REMINDER_NIGHT_HHMM` + `REMINDER_LOCAL_TZ_OFFSET_HOURS` (default BDT = UTC+6). Stored as UTC timestamps; conversion happens once at schedule-creation time.
- **Stop after duration**: natural consequence of generating exactly `duration_days` worth of rows up-front; no row exists past day N.
- **Cron dispatcher**: ARQ cron every 5 minutes claims a batch (`FOR UPDATE SKIP LOCKED`), emits one `reminders.reminder.due` outbox event per row with full delivery context (channel, body, recipient), flips status to `dispatched`.
- **Push → SMS fallback**: implemented at the contract level. The dispatch event payload carries the chosen channel; the notifications module (Phase 6) delivers and, on push failure, emits a feedback event the reminder service will subscribe to and use to switch channel + re-enqueue. Documented in [events.py](../app/modules/reminders/events.py); not built in this prompt because the notifications module owns the failure feedback.

### Tables (migration 0010)
- `medication_reminders` — one row per (prescription_line, slot, day). CHECK constraints on the three enums; indexed by `(status, scheduled_for)` for the cron poll, `(patient_user_id, scheduled_for)` for the customer view, and `prescription_id` for the cancel-by-prescription path.

### Endpoints
**Customer** ([api/customer.py](../app/modules/reminders/api/customer.py)):
- `GET /api/v1/me/reminders` — paginated, filterable by status
- `GET /api/v1/me/reminders/{id}` — detail (403 if not the patient)

**Admin** ([api/admin.py](../app/modules/reminders/api/admin.py)):
- `GET /api/v1/admin/reminders` — paginated, filter by status / prescription
- `POST /api/v1/admin/reminders/by-prescription/{id}/cancel`
- `POST /api/v1/admin/reminders/jobs/dispatch` — manual cron trigger

### Outbox events emitted
- `reminders.reminder.due` — payload includes `channel`, `body`, `medicine_label`, `slot`, `patient_phone`, `patient_user_id?`. Notifications module subscribes and delivers.
- `reminders.reminder.cancelled` — bulk cancel notification.

### Tests — [test_reminders.py](../app/modules/reminders/tests/test_reminders.py) (13 tests)
| Test | Proves |
|---|---|
| `test_schedule_for_line_emits_correct_count` | Pure-function: 3 days × 2 slots → 6 rows |
| `test_schedule_no_slots_returns_empty` | All-false dosage → no rows |
| `test_slot_utc_translates_local_to_utc` | BDT 08:00 → 02:00 UTC, 21:00 → 15:00 UTC |
| `test_issuing_prescription_creates_reminders_via_outbox` | End-to-end auto-create; channel=push for accounted patient |
| `test_no_account_patient_gets_sms_channel` | No-account → channel=sms |
| `test_stop_after_duration_no_extra_reminders` | Day-1 to day-N inclusive; nothing past N |
| `test_prescription_cancel_cancels_pending_reminders` | Cancel propagation through outbox |
| `test_dispatch_picks_only_due_reminders` | Cron only claims `scheduled_for <= now()`, leaves future rows alone |
| `test_dispatch_payload_includes_channel_and_body` | Outbox payload carries channel + body + slot for downstream |
| `test_dispatch_does_not_redispatch_dispatched_rows` | Already-dispatched rows are not re-claimed |
| `test_customer_lists_own_reminders` | Customer view returns own rows; counts match |
| `test_customer_cannot_view_others_reminders` | 403 on cross-patient fetch |
| `test_anon_cannot_view_reminders` / `test_customer_cannot_run_dispatch_job` | 401 / 403 RBAC denial |

### Files
- [app/modules/reminders/](../app/modules/reminders/) — models, schedule, repository, service, handlers, jobs, events, schemas, api/
- [alembic/versions/0010_reminders.py](../alembic/versions/2026_05_03_0010-0010_reminders.py)
- Updated [config.py](../app/core/config.py), [.env.example](../.env.example), [main.py](../app/main.py), [worker.py](../app/worker.py), [registry.py](../app/core/db/registry.py)

### Still pending (deliberate)
- **Notifications module** (Phase 6) — to consume `reminders.reminder.due` and actually deliver via push/SMS. Until then events fan out to dead-letter (no handler registered) — same architecture-not-stub pattern as the inventory `payments.payment.succeeded` consumer.
- **Push → SMS fallback feedback loop** — requires the notifications module to emit a delivery-failure event the reminder service subscribes to.
- **SMS provider** — same blocker affecting auth, doctor-rx-no-app, and reminders.

---

## Module 10 — Packing System (DELIVERED)

### Spec
- Barcode scan required to pack
- Wrong item → blocked
- Expired stock → blocked
- Batch mismatch → escalate to supervisor (override path)
- Flow: `scan → verify → pack`

### Tables (migration 0011)
- `packing_sessions` — one open session per order at a time (partial unique on `WHERE status='open'`); `open → completed | cancelled`.
- `packing_session_lines` — expected items snapshotted at session-open time from the order's reserved-batch ledger. Carries `expected_batch_id` (FEFO-chosen by inventory), `accepted_batch_id` (filled when supervisor overrides), CHECK `scanned_quantity ≤ expected_quantity`.
- `packing_scans` — append-only audit ledger of every scan attempt, accepted or rejected. REVOKE UPDATE/DELETE applied at the migration level.

### Service flow
- **Open session** ([service.py](../app/modules/packing/service.py)) — order must be in `packing` state. Reads inventory `stock_ledger` filtered by `correlation_id = order_id`, sums net `+reserved` deltas per `(variant_id, batch_id)` to learn which batches FEFO chose, and snapshots one session line per `(variant, batch)` pair. Refuses to open if reserved totals don't match order line quantities.
- **Scan** — picker posts `{barcode, batch_id}`. Sequential checks:
  1. **Unknown barcode** → outcome `unknown_barcode`, blocked.
  2. **Wrong item** (variant doesn't match any open line) → outcome `wrong_item`, blocked, **never overrideable**.
  3. **Expired** (batch.expiry_date past, or status='expired') → outcome `expired`, blocked, **never overrideable** (rule: never pack expired).
  4. **Batch mismatch** (variant matches but no open line expects this batch) → outcome `batch_mismatch`, blocked. Response includes `can_supervisor_override = true`.
  5. **Over quantity** (line already at expected) → outcome `over_quantity`, blocked.
  6. **Accepted** → increment `scanned_quantity`, mark line `complete` if full. When all lines are complete/overridden the session transitions to `completed`.
- **Override scan** — supervisor-only endpoint. Validates the substitute batch (variant match + non-expired + non-blocked) before accepting. Records `accepted_batch_id` on the line. Refuses an expired substitute even on override (hard rule: never pack expired).
- **Cancel session** — supervisor voids an open session with reason.

### Rule enforcement summary
| Rule | Mechanism |
|---|---|
| Barcode scan required | All increments go through the scan endpoint; no manual `scanned_quantity` mutation API |
| Wrong item blocked | `wrong_item` outcome short-circuits with no line update; recorded in `packing_scans` |
| Expired blocked | `batch.expiry_date <= today` AND `batch.status == EXPIRED` both checked at scan + override |
| Batch mismatch → supervisor | `batch_mismatch` outcome returns `can_supervisor_override = true`; the override endpoint requires a separate permission |

### Endpoints (`/api/v1/admin/packing/...`)
- `POST /sessions?order_id=...` — open session (RBAC: `order.fulfill`)
- `GET /sessions` — list (paginated, filter by status)
- `GET /sessions/{id}` — detail
- `POST /sessions/{id}/scan` — picker scan
- `POST /sessions/{id}/override-scan` — supervisor batch substitution (RBAC: `iam.role.assign`)
- `POST /sessions/{id}/cancel` — supervisor cancel

### Outbox events
- `packing.session.opened`, `packing.session.completed`, `packing.session.cancelled`
- `packing.scan.supervisor_override` — for ops alerting/audit
- `packing.scan.blocked` — for supervisor dashboards (every blocked scan emits this so a supervisor can intervene if patterns appear)

### Tests — [test_packing.py](../app/modules/packing/tests/test_packing.py) (15 integration tests)
| Test | Proves |
|---|---|
| `test_open_session_snapshots_reserved_batch` | Session lines carry the FEFO-chosen reserved batch from inventory |
| `test_open_session_refuses_non_packing_order` | 422 unless order is in `packing` state |
| `test_cannot_open_two_concurrent_sessions_for_same_order` | Partial unique enforces 1 open session per order |
| `test_scan_accepted_increments_line_and_completes_session` | Accepted scans tick `scanned_quantity`; final scan flips session to `completed` |
| `test_scan_wrong_item_blocked` | Unrelated variant → 200 with `wrong_item`; no line changed; not overrideable |
| `test_scan_unknown_barcode_blocked` | Unknown barcode → 200 with `unknown_barcode` |
| `test_scan_expired_blocked` | Expired batch → 200 with `expired`; not overrideable |
| `test_scan_batch_mismatch_blocks_with_override_hint` | Mismatch returns `can_supervisor_override = true` |
| `test_supervisor_override_accepts_substitute_batch` | Supervisor accepts; `accepted_batch_id` recorded; line goes `overridden` |
| `test_override_refuses_expired_substitute` | Override path also refuses expired (hard rule) |
| `test_scan_over_quantity_blocked` | Once full / completed, no further scans allowed |
| `test_cancel_session` | Open session → cancelled with reason |
| `test_blocked_scans_are_logged_in_packing_scans` | Append-only ledger captures every attempt |
| `test_customer_cannot_open_packing_session` | 403 RBAC denial |

### Files
- [app/modules/packing/](../app/modules/packing/) — state, models, schemas, repository, service, events, api/, tests/
- [alembic/versions/0011_packing.py](../alembic/versions/2026_05_03_0011-0011_packing.py)
- Updated [main.py](../app/main.py), [registry.py](../app/core/db/registry.py)

### Sequence note
Module 10 closes the loop on the order lifecycle: `pending_payment → ... → APPROVED → PACKING → (packing session) → OUT_FOR_DELIVERY → COMPLETED`. The packing session doesn't yet auto-call `dispatch` when complete — staff still hits the orders endpoint manually. That auto-advance is a small follow-up if desired.

---

## Module 11 — Delivery Operations (DELIVERED)

### Spec
Flow: `assign → pickup → deliver → POD → complete`.
Rules:
- POD mandatory
- COD reconciliation
- Delivery → stock deduct

Module name: `deliveries` (plural) to avoid colliding with the existing `delivery` (zones/pricing) module.

### State machine
```
ASSIGNED → PICKED_UP → DELIVERED → COMPLETED (terminal)
        ↘ CANCELLED               (terminal — admin pre-pickup)
                    ↘ FAILED      (terminal — rider couldn't deliver)
```
COD-aware completion: `DELIVERED → COMPLETED` is automatic when `cod_status` is `n/a` or `reconciled` at delivery time. If `discrepancy`, the assignment sits in `delivered` until a supervisor calls `/admin/deliveries/{id}/reconcile-cod`.

### Tables (migration 0012)
- `riders` — rider roster, optional `linked_user_id` (rider authenticates via the user record). Same pattern as `doctors`.
- `delivery_assignments` — header carrying COD reconciliation columns + POD evidence (photo path, signature path, otp_verified_at, recipient_name). Partial unique on `WHERE status IN ('assigned','picked_up','delivered')` so an order can only have one *active* delivery at a time; cancelled/failed/completed coexist as history.
- `delivery_status_history` — append-only transition log; REVOKE UPDATE/DELETE applied.

### Hard-rule mappings
| Rule | Where |
|---|---|
| POD mandatory | `deliver` raises `BusinessRuleError` unless `pod_photo_path`, `pod_signature_path`, OR `pod_otp_verified_at` is present |
| COD reconciliation | `deliver` requires `cod_collected` for COD orders; auto-reconciles within `DELIVERY_COD_AUTO_RECONCILE_TOLERANCE_CENTS`; supervisor closes any discrepancy via `reconcile-cod` |
| Delivery → stock deduct | `_complete` inline-calls `OrderService.complete(order_id)` → emits `orders.order.completed` → inventory handler consumes reserved stock. **End-to-end exercised in test `test_completion_deducts_stock_via_outbox`** |

### Endpoints
**Admin** (`/api/v1/admin/deliveries/...`):
- Riders: `POST/GET/PATCH /riders`
- Assignments: `POST /assignments`, `GET /`, `GET /{id}`, `POST /{id}/cancel`, `POST /{id}/reconcile-cod`

**Rider** (`/api/v1/rider/me/deliveries/...`):
- `GET /` — list mine, `GET /{id}` — detail
- `POST /{id}/pickup` — assigned → picked_up
- `POST /{id}/upload-pod` — multipart photo upload (jpg/png/webp)
- `POST /{id}/deliver` — POD + COD entry; auto-completes if reconciled
- `POST /{id}/fail` — undeliverable

All rider endpoints additionally enforce that the caller is linked to an active `Rider` row (same pattern as doctor-rx). RBAC piggybacks on `order.fulfill`/`iam.role.assign` until the IAM 7-role catalog ships dedicated `delivery.rider` / `delivery.supervisor` permissions.

### Outbox events
`deliveries.delivery.assigned`, `picked_up`, `delivered`, `completed`, `cancelled`, `failed`, `cod_discrepancy`. Notifications module (Phase 6) will subscribe.

### Tests — [test_deliveries.py](../app/modules/deliveries/tests/test_deliveries.py) (17 integration tests)
| Test | Proves |
|---|---|
| `test_admin_creates_rider` / `test_customer_cannot_create_rider` | Rider admin + RBAC |
| `test_assign_requires_out_for_delivery` | Order must be in OFD state |
| `test_assign_cod_pre_fills_cod_expected` | COD expected = order's grand_total |
| `test_cannot_assign_twice_for_same_order` | Partial unique enforces 1 active per order |
| `test_rider_pickup_transitions_to_picked_up` | State machine |
| `test_deliver_without_pod_blocked` | POD mandatory rule |
| `test_deliver_with_otp_evidence_succeeds` / `test_deliver_with_photo_evidence_succeeds` | OTP and photo POD both work; auto-completes on reconcile |
| `test_cod_exact_match_auto_reconciles_and_completes` | Exact COD → reconciled → completed |
| `test_cod_discrepancy_blocks_completion_until_reconcile` | Mismatch → discrepancy → supervisor reconcile → complete |
| `test_cod_collected_required_for_cod_orders` / `test_cod_collected_rejected_for_online_orders` | COD field gating |
| `test_online_order_completes_without_cod` | Online path skips COD entirely |
| `test_completion_deducts_stock_via_outbox` | **End-to-end stock deduct rule**: complete → order.completed event → inventory consume → reserved drained |
| `test_cannot_deliver_before_pickup` / `test_admin_cancel_marks_cancelled` / `test_rider_fails_after_pickup` | State machine guards |
| `test_rider_endpoints_require_doctor_record` | Active-rider gate (doctor pattern) |
| `test_anon_cannot_assign` | 401 |
| `test_lifecycle_writes_history_and_audit` | Full ASSIGNED→PICKED_UP→DELIVERED→COMPLETED history rows |

### Files
- [app/modules/deliveries/](../app/modules/deliveries/) — state, codes, storage, models, schemas, repository, service, events, api/, tests/
- [alembic/versions/0012_deliveries.py](../alembic/versions/2026_05_03_0012-0012_deliveries.py)
- Updated [config.py](../app/core/config.py), [.env.example](../.env.example), [main.py](../app/main.py), [registry.py](../app/core/db/registry.py)

### Sequence wrap-up
With Module 11 the order → packing → delivery → completion → stock deduction flow is end-to-end real:
```
checkout → reserve (Module 4) → packing scan-verify (Module 10) →
    dispatch (Module 5) → assign rider (Module 11) → pickup → POD → COD reconcile →
    delivery COMPLETED → orders.order.completed event → inventory consume (Module 4)
```

---

## Module 12 — Return System (DELIVERED)

### Spec
- Sealed → return to stock
- Opened → block
- Cold-chain broken → block
- Expired → disposal

### Flow
`requested → received → inspected → completed` (terminal). Plus terminal `rejected` (admin refuses) and `cancelled` (customer/admin voids).

### Tables (migration 0013)
- `return_requests` — header per request, code-coded `RR-YYYYMMDD-XXXXX`. References the order; only orders in COMPLETED state can be returned.
- `return_request_lines` — per-order-line snapshot with `requested_quantity`, plus inspection columns set later (`condition`, `inspected_quantity`, `target_batch_id`, `target_warehouse_id`, `inspection_notes`, `applied_action`, `applied_at`).
- `return_status_history` — append-only transition log; REVOKE UPDATE/DELETE.

### Condition → action mapping ([state.py](../app/modules/returns/state.py))
| Condition           | Action  | Bucket the qty lands in |
|---------------------|---------|-------------------------|
| sealed              | restock | available               |
| opened              | block   | blocked                 |
| cold_chain_broken   | block   | blocked                 |
| expired             | dispose | expired                 |

The mapping lives in `action_for_condition` + `bucket_for_action`. A single `LedgerKind.RETURN` ledger row per line is written at completion.

### Endpoints
**Customer** (`/api/v1/returns/...`):
- `POST /` — open a return for a completed order
- `GET /` / `GET /{id}` — list/detail (own only)
- `POST /{id}/cancel` — only while in `requested`

**Admin** (`/api/v1/admin/returns/...`):
- `GET /` / `GET /{id}` — list/detail
- `POST /{id}/receive` — package arrived
- `POST /{id}/inspect` — body lists per-line condition + target batch/warehouse; **must cover every line**
- `POST /{id}/complete` — applies inventory movements per condition
- `POST /{id}/reject` / `POST /{id}/cancel`

### Tests — [test_returns.py](../app/modules/returns/tests/test_returns.py) (15 integration tests)
| Test | Proves |
|---|---|
| `test_customer_can_only_return_completed_order` | 422 when order not yet COMPLETED |
| `test_customer_creates_return_for_completed_order` | Happy path request creation |
| `test_return_quantity_cannot_exceed_ordered` | 422 |
| `test_customer_cannot_return_others_order` | 403 |
| `test_sealed_condition_restocks_to_available` | **Hard rule**: sealed → available bucket increments |
| `test_opened_condition_blocks_stock` | **Hard rule**: opened → blocked bucket |
| `test_cold_chain_broken_blocks_stock` | **Hard rule**: cold_chain_broken → blocked bucket |
| `test_expired_condition_routes_to_disposal_bucket` | **Hard rule**: expired → expired bucket (write-off captured) |
| `test_inspect_requires_received_state` | State guard 422 |
| `test_inspect_must_cover_every_line` | Inspection covers every line exactly once |
| `test_customer_cancel_in_requested_state` | Customer self-cancel works |
| `test_admin_reject` | Admin reject sets reason + terminal |
| `test_completion_writes_inventory_ledger_with_return_kind` | One `LedgerKind.RETURN` ledger row per applied line |
| `test_anon_cannot_create_return` / `test_customer_cannot_inspect` | RBAC denial |
| `test_full_lifecycle_writes_history` | All four transitions captured in `return_status_history` |

### Files
- [app/modules/returns/](../app/modules/returns/) — state, codes, models, schemas, repository, service, events, api/, tests/
- [alembic/versions/0013_returns.py](../alembic/versions/2026_05_03_0013-0013_returns.py)
- Updated [main.py](../app/main.py), [registry.py](../app/core/db/registry.py)

### Out of scope (deliberate)
- **Refund / wallet credit** — refunding the customer's payment is a separate concern. Today the return touches inventory only; refund money flow waits for the payments module (still pending Bkash/SSLCommerz creds) plus a wallet-grant follow-up.
- **Return shipping / pickup** — physically getting the package back to the warehouse is owned by the deliveries module; integration is a small follow-up that would call into `deliveries` to create a return-pickup assignment.

---

## Module 13 — Compliance Lock System (DELIVERED)

> **Scope change (same session, after first delivery):** the original spec had
> *"no valid license → block all sales"* as a hard gate. User then directed:
> *"remove this compliance License না থাকলে কেউ কিছু কিনতে পারবে না"*. License
> tracking, admin endpoints, expiry status, and audit ledger all stay — but
> license is **no longer a sales gate**. Only the pharmacist-on-duty rule
> still hard-blocks orders, and only for Rx lines. Section below reflects the
> post-change state.

### Final spec
- **License**: tracked + admin-managed + visible in status snapshot, but does **not** block any sale.
- **Pharmacist on duty**: required only when an order contains at least one Rx line.
- **Non-Rx orders**: skip the compliance module entirely (no check, no log row).
- **Full audit trail**: every Rx gate evaluation (pass + fail) writes to `compliance_check_log`.

### Tables (migration 0014) — unchanged by the scope change
- `compliance_licenses` — operating licenses (drug / trade / GST). Status enum (`active/suspended/revoked`); CHECK `expires_on >= issued_on`. Now informational only.
- `pharmacists` — registered pharmacists with `council_registration_no` UNIQUE; optional `linked_user_id` for self-service login.
- `pharmacist_shifts` — open + closed shift records. Partial UNIQUE on `WHERE closed_at IS NULL` so a single pharmacist cannot have two open shifts simultaneously (defence against double check-in).
- `compliance_check_log` — append-only audit ledger (REVOKE UPDATE/DELETE) of every Rx gate check.

### Hard-rule enforcement (current)
| Rule | Mechanism |
|---|---|
| Rx blocked without pharmacist on duty | `assert_can_process_rx` requires `count(open_shifts) > 0`; called from `OrderService.place_order` only when any line has `requires_prescription=true`; failure → 422 + transaction rollback |
| Audit trail for Rx checks | Every Rx evaluation (pass + fail) writes a `compliance_check_log` row in the same transaction as the order outcome |
| License gate | **Removed.** `check_can_sell` still exists in the service for status reporting, but is no longer wired into any order path |

### Endpoints (`/api/v1/admin/compliance/...`) — unchanged
- `GET /status` — live snapshot: `sales_allowed` (always `True` now — informational), `rx_allowed`, `license_count`, `pharmacists_on_duty`
- Licenses: `GET /licenses`, `POST /licenses`, `PATCH /licenses/{id}`, `POST /licenses/{id}/{suspend|revoke|reactivate}`
- Pharmacists: `POST /pharmacists`, `GET /pharmacists`, `PATCH /pharmacists/{id}`, `POST /pharmacists/{id}/force-close-shift`
- `GET /check-log` — paginated audit feed (Rx checks only)

**Pharmacist self-service** (`/api/v1/pharmacist/me/...`):
- `POST /check-in` (idempotent), `POST /check-out`, `GET /shifts`

### Cross-module wiring
[orders/service.py](../app/modules/orders/service.py) `place_order` no longer calls `assert_can_sell`. It conditionally calls `assert_can_process_rx` only when `any_rx` is true. Non-Rx orders never touch the compliance module — no DB read, no ledger write.

### Tests — [test_compliance.py](../app/modules/compliance/tests/test_compliance.py) (17 integration tests)
Removed the two "license blocks sales" tests. Added:
- `test_status_sales_always_allowed` — snapshot reports `sales_allowed=true` regardless of license state
- `test_status_rx_allowed_when_pharmacist_on_duty`
- `test_revoked_license_does_not_block_non_rx_sales`
- `test_non_rx_sale_passes_with_no_license_at_all`
- `test_rx_passes_even_without_license` — only the pharmacist matters for Rx
- `test_non_rx_orders_skip_compliance_check_log` — guarantees zero ledger noise
- Renamed `test_check_log_records_pass_and_fail` → `test_rx_check_log_records_pass_and_fail`

Coverage still includes: license CRUD + status transitions, pharmacist roster, idempotent check-in, force-close, audit ledger immutability, RBAC.

### Shared test infra change
[app/conftest.py](../app/conftest.py) autouse fixture re-seeds IAM reference rows on every test. **No license seed** here — license is no longer a sales gate, so order-placing tests in other modules (catalog, orders, packing, deliveries, returns, doctor_rx, prescriptions, reminders) need nothing seeded. Compliance's own [tests/conftest.py](../app/modules/compliance/tests/conftest.py) seeds a `DGDA-DEFAULT` license for the license-tracking assertions in that module's tests.

### Files
- [app/modules/compliance/](../app/modules/compliance/) — state, codes, models, schemas, repository, service, api/, tests/
- [alembic/versions/0014_compliance.py](../alembic/versions/2026_05_03_0014-0014_compliance.py)
- Updated [main.py](../app/main.py), [registry.py](../app/core/db/registry.py), [orders/service.py](../app/modules/orders/service.py), [app/conftest.py](../app/conftest.py), [app/modules/compliance/tests/conftest.py](../app/modules/compliance/tests/conftest.py)

---

## Module 14 — Finance: Accounting + AP/AR + Reports (DELIVERED)

### Spec
- Double-entry accounting (debits = credits per entry, append-only lines)
- VAT/tax handling (configurable rate, treated as inclusive on order grand_total)
- Supplier settlement (AP subledger: bill → partial/full payment → status)
- Refund reconciliation (return → accrued payable → settle)
- COD control (rider cash → bank deposit, with discrepancy expense)
- Daily closing (locks JEs ≤ closing_date, snapshot row)
- P&L + Balance Sheet + Trial Balance + VAT ledger reports

### Tables (migration 0015)
- `fin_accounts` — chart of accounts. 5 types (asset/liability/equity/revenue/expense). Code is fixed for the life of the system; new accounts append, deprecated ones flip `is_active=False`.
- `fin_accounting_periods` — calendar-month grain; `status='locked'` blocks posting into the period.
- `fin_journal_entries` — header. `source` enum tags the producer (manual / order_revenue / order_cogs / supplier_bill / cod_deposit / refund_accrual / …). Status: draft / posted / reversed.
- `fin_journal_lines` — debit-or-credit per row. CHECK `(debit > 0 AND credit = 0) OR (debit = 0 AND credit > 0)` — malformed lines can't be inserted at all. **REVOKE UPDATE/DELETE** on PUBLIC.
- `fin_supplier_bills` + `fin_supplier_payments` — AP subledger.
- `fin_cod_deposits` — rider deposit reconciliation with discrepancy column.
- `fin_refund_records` — UNIQUE on `return_request_id` (one refund per return).
- `fin_daily_closes` — immutable EOD snapshot. **REVOKE UPDATE/DELETE**.

### Canonical chart of accounts (auto-seeded on first call)
| Code | Name | Type |
|---|---|---|
| 1010 | Cash in Bank | asset |
| 1020 | Cash on Hand — Riders | asset |
| 1100 | AR — COD Customers | asset |
| 1110 | AR — Gateway Pending | asset |
| 1300 | Inventory | asset |
| 2010 | AP — Suppliers | liability |
| 2100 | VAT Payable | liability |
| 2200 | Refunds Payable | liability |
| 3010 | Opening Balance Equity | equity |
| 4010 | Product Sales Revenue | revenue |
| 4020 | Shipping Revenue | revenue |
| 4910 | Sales Returns (contra) | revenue |
| 4920 | Sales Discounts (contra) | revenue |
| 5010 | Cost of Goods Sold | expense |
| 5020 | Inventory Loss / Writedown | expense |
| 6010 | COD Cash Short / Over | expense |

### Hard-rule enforcement
| Rule | Mechanism |
|---|---|
| Journal entry must balance | `post_entry` validates `sum(debits) == sum(credits)` to 2dp; rejects with 422 |
| Each line is debit XOR credit | DB-level CHECK constraint — malformed line never lands |
| Append-only journal lines | `REVOKE UPDATE, DELETE ON fin_journal_lines FROM PUBLIC` in the migration; reversal works by inserting an offsetting entry |
| Period lock | `_assert_open_for_post(date)` rejects posts whose date is ≤ last DailyClose OR inside a `status='locked'` period |
| Daily close immutable | `REVOKE UPDATE, DELETE ON fin_daily_closes FROM PUBLIC`; `closing_date` UNIQUE so a date can be closed only once |
| Re-reversal blocked | `reverse_entry` checks for any existing entry with `reverses_entry_id == original.id` |

### Reversal semantics (industry-standard, additive)
Reversal does NOT mutate the original. Both entries stay `status='posted'`, the new one's `reverses_entry_id` points back, and net balances reflect the offset (original lines + mirror lines = zero). The `JournalEntryStatus.REVERSED` value exists for future "voided" semantics but is not used by the standard reverse path.

### Cross-module wiring (outbox-driven, idempotent on `(source, reference_id)`)
| Producer event | Finance handler | Posting |
|---|---|---|
| `orders.order.payment_confirmed` | `_handle_order_payment_confirmed` | Dr AR-COD or AR-Gateway / Cr Sales / Cr VAT-Payable (VAT split if `VAT_RATE > 0`) |
| `orders.order.completed` | `_handle_order_completed` | Dr COGS / Cr Inventory (cost computed from `stock_ledger` CONSUME rows × goods_receipt_lines avg unit_cost) |
| `orders.order.cancelled` | `_handle_order_cancelled` | Reverses the order_revenue entry if posted; otherwise no-op |
| `deliveries.delivery.completed` | `_handle_delivery_completed` | Looks up the assignment; if COD with `cod_collected > 0`: Dr Cash-on-Hand-Rider / Cr AR-COD |
| `returns.return.completed` | `_handle_return_completed` | Computes refund = sum(unit_price × inspected_quantity); Dr Sales-Returns / Cr Refund-Payable |

**Dispatcher change**: extended [app/core/events/dispatcher.py](../app/core/events/dispatcher.py) to support **multiple handlers per event type** — both inventory and finance subscribe to `orders.order.cancelled` and `orders.order.completed`. Handlers fire in registration order; finance is registered after inventory so COGS reads the CONSUME rows that inventory's handler just wrote.

### VAT (BD pharmacy default = 0%)
Setting `VAT_RATE` env var (e.g. `0.15`) flips revenue posting to VAT-inclusive split: `net = grand_total / (1 + rate)`, `vat = grand_total - net`. Default `0` → revenue gets the full grand_total, no VAT line. The VAT ledger report (`/reports/vat-ledger`) lists every line touching the VAT-Payable account in a date range.

### Endpoints (`/api/v1/admin/finance/...`)
- **Accounts**: `GET /accounts`, `POST /accounts/seed` (idempotent), `PATCH /accounts/{id}`
- **Journal**: `POST /journal-entries` (manual), `GET /journal-entries`, `GET /journal-entries/{id}`, `POST /journal-entries/{id}/reverse`
- **Periods + close**: `GET /periods`, `POST /periods/close`, `POST /daily-close`, `GET /daily-close`
- **Reports**: `GET /reports/trial-balance`, `GET /reports/profit-and-loss`, `GET /reports/balance-sheet`, `GET /reports/vat-ledger`
- **Supplier bills**: `POST /supplier-bills` (books JE), `GET /supplier-bills`, `POST /supplier-bills/{id}/payments`, `GET /supplier-bills/{id}/payments`
- **COD**: `POST /cod-deposits`, `GET /cod-deposits/rider/{id}`, `GET /riders/{id}/cash-on-hand`
- **Refunds**: `GET /refunds`, `POST /refunds/pay`

### IAM permissions added
`finance.read` (granted to manager + admin), `finance.post`, `finance.settle`, `finance.close`, `finance.adjust` (admin-only via `*` wildcard).

### Tests — [test_finance.py](../app/modules/finance/tests/test_finance.py) (~22 integration tests)
Three layers:
1. **Pure double-entry mechanics** — balanced post, unbalanced reject, both-sides reject, unknown account, reverse + double-reverse block, daily close + post-close reject, future-date reject, period lock, trial balance sums, balance sheet balances.
2. **Subledger workflows** — supplier bill book + partial pay → paid status, overpay reject, COD deposit with discrepancy posts to short/over expense, exact-match deposit.
3. **Cross-module wiring** — COD order revenue JE via outbox, cancellation reversal nets to zero, VAT inclusive split (15% rate test), full COGS posting via complete flow, return completion accrues refund + settle nets out, RBAC reject for non-finance role.

### Test infra
- [app/modules/finance/tests/conftest.py](../app/modules/finance/tests/conftest.py) — autouse fixture seeds the chart of accounts before every finance test (truncate-between-tests wipes it). Also explicitly registers inventory + finance handlers in that order so COGS reads stock_ledger CONSUME rows after inventory writes them.
- [tests/conftest.py](../tests/conftest.py) — added `VAT_RATE=0` to default test env.

### Files
- [app/modules/finance/](../app/modules/finance/) — state, codes, accounts, events, models, schemas, repository, service, handlers, api/, tests/
- [alembic/versions/0015_finance.py](../alembic/versions/2026_05_03_0015-0015_finance.py)
- Updated [main.py](../app/main.py), [registry.py](../app/core/db/registry.py), [iam/permissions.py](../app/modules/iam/permissions.py), [config.py](../app/core/config.py) (+ `vat_rate`), [events/dispatcher.py](../app/core/events/dispatcher.py) (multi-handler support), [tests/conftest.py](../tests/conftest.py)

### Out of scope (deliberate)
- **Input VAT recoverable on supplier bills** — BD pharmacy is mostly VAT-exempt on inputs; tax_total on supplier bills currently rolls into Inventory cost rather than a recoverable asset account. Add when a supplier issues a VAT-recoverable invoice.
- **Customer wallet ↔ refund integration** — refund payments currently hit Cash-in-Bank only. Wallet credit as a refund destination is a one-line addition once needed.
- **Multi-currency** — single-currency BDT only; the `currency` columns exist but the service treats everything as BDT.
- **Bkash / SSLCommerz settlement** — the `1110 AR — Gateway Pending` account is the placeholder; the payments module (still pending creds) will move balances Dr Bank / Cr AR-Gateway-Pending on settlement webhooks.

---

## Module 15 — Operations Dashboard (DELIVERED)

### Spec
Read-only aggregations across every module. Seven metric blocks (sales, stock, expiry, delivery, COD, refund, doctor sales) plus a combined overview endpoint. Date range + warehouse filters where meaningful.

### Tables
None. The dashboard module owns no schema — every metric is computed on demand from the operational tables of other modules. No migration shipped with this module.

### Filter conventions
- `starts_on` / `ends_on` (date, inclusive). Default = last 30 days (rolling, ending today).
- `warehouse_code` (str). Stock + expiry endpoints only; ignored elsewhere.
- Hard cap `MAX_RANGE_DAYS = 366` rejects accidental full-table scans with 422.
- `low_stock_threshold` (int, default 10), `horizon_days` (int, default 60), `rider_limit` (int, default 25), `leaderboard_limit` (int, default 20).

### Endpoints (`/api/v1/admin/dashboard/...`)
| Endpoint | Surfaces |
|---|---|
| `GET /sales` | order_count, revenue, avg_order_value, cancelled_count + revenue, rx_order_count, by_payment_method[], daily[] |
| `GET /stock` | per-bucket totals, available_units_total, distinct_variants_in_stock, low_stock_variants[] |
| `GET /expiry` | expired_batches, expiring_within_horizon_batches, units_at_risk, batches[] (sorted by soonest expiry) |
| `GET /delivery` | per-status counts, in_transit, awaiting_assignment, completion_rate, avg_minutes_assignment_to_completion |
| `GET /cod` | cod_collected_total, cod_deposited_total, cod_outstanding_total, discrepancy_count + total, riders[] (per-rider outstanding leaderboard) |
| `GET /refund` | pending_count + amount, paid_count + amount, cancelled_count, refund_rate (refunds / completed orders) |
| `GET /doctor-sales` | doctors_active, prescriptions_issued, credits_granted_total, credits_redeemed_total, leaderboard[] |
| `GET /overview` | All seven blocks composed in one call with a shared date range — for the home page |

### Doctor → sales attribution chain
`Doctor → DoctorPrescription → WalletCredit (source_type='doctor_prescription', source_id=prescription_id) → WalletTransaction (kind='redeem', credit_id)`. The `credits_redeemed_total` aggregates redemption transactions whose underlying credit traces back to a prescription **issued in the date range** — i.e. attribution is to the granting prescription, not the spend date. Flip the join if business prefers the opposite.

### IAM permissions added
`dashboard.read` (granted to manager + admin via wildcard).

### Tests — [test_dashboard.py](../app/modules/dashboard/tests/test_dashboard.py) (~13 tests)
- Sales: revenue + AOV computation, default range = last 30 days, range cap (>366) rejected, bad date order rejected
- Stock: per-bucket totals, low-stock threshold filter, unknown warehouse → empty (not 404)
- Expiry: near-expiry vs far-future batches separated by horizon
- Delivery: assigned-count after assign endpoint
- COD: outstanding per rider after a reconciled COD delivery (no deposit yet)
- Refund: pending_count + amount visible after seeded refund record
- Doctor sales: leaderboard ordered by prescription count
- Overview: all 7 blocks present in one response
- RBAC: customer (no `dashboard.read`) → 403

### Test infra
[app/modules/dashboard/tests/conftest.py](../app/modules/dashboard/tests/conftest.py) re-seeds the finance chart of accounts before every dashboard test (refund + COD blocks read finance tables) and explicitly registers inventory + finance handlers in that order so end-to-end flows see the same handler chain as production.

### Files
- [app/modules/dashboard/](../app/modules/dashboard/) — schemas, repository, service, api/, tests/
- Updated [main.py](../app/main.py), [iam/permissions.py](../app/modules/iam/permissions.py)

### Out of scope (deliberate)
- **Materialized snapshots** — every request recomputes from base tables. At current data volumes this is fine; if reports get slow, the natural next step is a daily-snapshot job that pre-aggregates `dashboard_daily` rows during the existing finance daily close.
- **CSV / PDF export** — JSON-only. Export endpoints belong to a reports module, not the dashboard.
- **Real-time push** — no websockets / SSE. Front-end polls the overview endpoint.
- **Time-zone math** — all date filters are interpreted as UTC dates. Bangladesh is UTC+6; the front-end is responsible for date-bucket alignment if the user wants "yesterday in BDT".

---

## Module 16 — AI Services (DELIVERED, provider unbound)

### Spec
- AI **can**: OCR prescription, suggest medicines, predict stock, detect fraud
- AI **cannot**: approve a prescription, approve / pay a refund, cancel an order
- Deliver: service layer

### Hard-rule enforcement (the "AI cannot" rule, structurally)
| Mechanism | What it guarantees |
|---|---|
| `HUMAN_ONLY_ACTIONS` frozenset in [state.py](../app/modules/ai/state.py) | Names every human-only action by its dotted action key. Service code calls `assert_ai_cannot_decide(action)` to refuse to even *enter* an approval flow |
| AI service has **no method** that approves an Rx, pays a refund, or cancels an order — there's no public surface that could be used | Approval can only be done by calling the owning module's endpoint (e.g. `POST /admin/prescriptions/{id}/approve`) which requires its own permission |
| AI module does NOT import `PrescriptionService.approve`, `FinanceService.pay_refund`, `OrderService.complete`, `OrderService.cancel_*` — checked by [test_ai_module_does_not_import_approval_endpoints](../app/modules/ai/tests/test_ai.py) (AST-style scan over `app/modules/ai/`) | A future refactor that pipes AI output into an approval call fails the test, breaking the build |
| Every AI output is an `AIProposal` row with `status='draft'`. Reviewer endpoints (`accept`, `amend`, `reject`) are *bookmarks* — they **never** flip the underlying business resource | A pharmacist accepting an OCR proposal still has to call the prescription approve endpoint separately |

### Provider boundary (per the project's "no fakes in prod" rule)
- All capability calls go through the [`AIProvider`](../app/modules/ai/providers/base.py) port.
- Default binding is [`NotConfiguredProvider`](../app/modules/ai/providers/not_configured.py) which raises `IntegrationError` (502) on every call. **No stubbed adapter ships with the service** — silently invented OCR text or fraud scores would be worse than failing loud.
- `AIUsageEvent` rows are still written for failed calls (in a *separate* transaction so they survive the parent rollback) — vendor-cost reconciliation works even before a provider is wired.
- To wire a real provider (OpenAI / Anthropic / Azure / Vertex): implement `AIProvider`, call `bind_provider()` at startup once `AI_PROVIDER` + the vendor's API key are set in env. The service layer is unchanged.

### Tables (migration 0016)
- `ai_proposals` — durable record per AI call. Holds `kind`, `status` (draft/accepted/amended/rejected/expired), `confidence` (0–1 CHECK-bounded), the redacted `input_payload`, the full `ai_payload`, and an optional `decision_payload` set when a reviewer accepts/amends. Indexed by `kind+status`, `reference_type+reference_id`, `requested_by`, `created_at`.
- `ai_usage_events` — append-only ledger of every provider call. Fields: kind, provider, model, success, error_code+message, cost_units (Numeric(16,6)), latency_ms. **REVOKE UPDATE/DELETE** so vendor invoice reconciliation can't be tampered with.

### Endpoints (`/api/v1/admin/ai/...`)
- **Capabilities** (require `ai.use`):
  - `POST /ocr-prescription` — multipart upload, returns draft proposal
  - `POST /suggest-medicines`
  - `POST /predict-stock`
  - `POST /detect-fraud`
- **Reviewer flow** (require `ai.use`):
  - `POST /proposals/{id}/accept` — record agreement, status → `accepted`
  - `POST /proposals/{id}/amend` — accept with edits, status → `amended`
  - `POST /proposals/{id}/reject` — discard, status → `rejected`
- **Read** (require `ai.read`): `GET /proposals` (filters: kind, status, requested_by, reference_type+id), `GET /proposals/{id}`, `GET /usage`, `GET /status` (which provider is bound)

### Input redaction
OCR's `input_payload` JSONB stores **metadata only** — `image_bytes` is replaced by `image_bytes__size`. The actual file lives on the prescription module's storage root, addressable by `reference_id`. This keeps the audit ledger small and avoids storing PHI twice.

### IAM permissions added
`ai.use` (trigger capability + review proposals), `ai.read` (read proposals + usage). Both granted to manager + admin.

### Tests — [test_ai.py](../app/modules/ai/tests/test_ai.py) (~17 tests)
1. **Provider not configured**: status endpoint reports `configured=false`; each of the 4 capabilities returns 502; failed call still writes a usage event in a separate transaction.
2. **Capabilities work with test-bound fake**: each of the 4 capabilities creates a draft proposal with confidence ≤ 1.0; OCR redacts image bytes from `input_payload`.
3. **Reviewer flow**: accept / amend / reject move status correctly; double-action on terminal status returns 422.
4. **Hard policy**: AST-style scan proves no production file under `app/modules/ai/` references the forbidden approval methods; `assert_ai_cannot_decide` blocks human-only actions with `AIPolicyError`.
5. **Listing + filtering**: kind filter narrows results.
6. **RBAC**: customer (no `ai.use` / `ai.read`) → 403 on capability + read.

### Test infra
- [tests/_fakes.py](../app/modules/ai/tests/_fakes.py) — `FakeAIProvider` lives **under tests/**, not under providers/. Tests bind it explicitly via `bind_provider()`. The autouse `_reset_ai_provider_binding` fixture in [tests/conftest.py](../app/modules/ai/tests/conftest.py) restores `NotConfiguredProvider` after every test so a fake binding cannot leak.

### Files
- [app/modules/ai/](../app/modules/ai/) — state, schemas, models, repository, service, providers/, api/, tests/
- [alembic/versions/0016_ai.py](../alembic/versions/2026_05_03_0016-0016_ai.py)
- Updated [main.py](../app/main.py), [registry.py](../app/core/db/registry.py), [iam/permissions.py](../app/modules/iam/permissions.py)

### Out of scope (deliberate, awaiting decisions)
- **Choice of provider** — OpenAI vs Anthropic vs Azure OpenAI vs Vertex. No adapter shipped; the boundary is ready. Decision needed: (a) provider, (b) account / API key, (c) per-capability model (e.g. gpt-4o-mini for fraud, gpt-4o for OCR).
- **Fraud feature engineering** — `detect_fraud` currently sends only `order_id` to the provider. The provider adapter is responsible for fetching whatever signals it needs (customer history, address mismatch, COD discrepancy history) before composing its prompt. Once a provider is chosen, that fetching code lives in the adapter, not in the service.
- **Cost budgets / per-user quotas** — the usage ledger captures cost_units but there is no enforcement (no daily cap, no rate-limit per requester). Add a `BudgetGuard` at the entry point of `_run_capability` once we have real cost data to set thresholds against.
- **Prompt versioning** — for reproducibility, every adapter should embed a prompt-version string into `ai_payload.model` or similar. Convention to be set with the first real adapter.
- **Offline / batch capabilities** — current surface is synchronous request/response. A queue-driven mode (e.g. nightly stock prediction sweep) can be added by emitting an outbox event that an AI worker consumes and writes proposals into the same `ai_proposals` table.

---

## Module 17 — Customer Mobile API Surface (DELIVERED)

### Spec
Prepare APIs for the customer app. Features:
- login, search, order, prescription, reminder, tracking

### Audit of pre-existing surface
| Feature | Status before this module |
|---|---|
| Login | ✅ email + password (`/auth/*`). Phone-OTP paused on SMS provider. |
| Search | ✅ `GET /catalog/products` with `q`, filters, paging. |
| Order | ✅ `POST /orders`, `GET /orders`, `POST /orders/{id}/cancel`. |
| Prescription | ✅ multipart `POST /prescriptions`, list/detail/file. |
| Reminder | ⚠️ list + detail only. **No mark-taken, no snooze** — blockers for a mobile reminder UX. |
| Tracking | ❌ no public/customer track endpoint. |

Plus mobile-only gaps that any customer app needs: **push-notification device tokens**, **saved addresses**, **profile update**, and an **aggregated home-screen** to avoid 5 round trips on app open.

### Tables (migration 0017)
- `device_tokens` — `(kind: fcm|apns|web, token, app_version, locale, last_seen_at, is_active)`. UNIQUE `(user_id, token)` so re-launching the same handset upserts cleanly. CASCADE on user delete. Soft-deactivated rows kept for past-delivery telemetry.
- `customer_addresses` — full BD-shaped address (line1, line2, city, district, division, postal_code, country). Partial unique `WHERE is_default = true` enforces "at most one default per user" at the DB level; the service demotes the previous default in the same transaction.
- `medication_reminders.taken_at` + `medication_reminders.snoozed_until` — added via `ALTER TABLE`. Customer markers; the dispatch state machine (`pending → dispatched → sent`) is unchanged.

### Reminder customer actions (extension to existing module)
- `POST /me/reminders/{id}/mark-taken` — sets `taken_at = now()`. Idempotent: second call → 422. Does NOT alter dispatch status.
- `POST /me/reminders/{id}/snooze` — `{minutes: 1..360}`. For pending reminders, rewinds `scheduled_for` so the dispatcher waits. For sent/failed reminders, sets `snoozed_until` only.

### New customer endpoints (`mobile` module)
| Endpoint | Purpose |
|---|---|
| `GET  /me/profile` / `PATCH /me/profile` | Read + update self. Phone change resets `phone_verified_at`. |
| `POST /me/devices` | Register/refresh push token (idempotent on user+token). |
| `GET /me/devices` / `DELETE /me/devices/{id}` | List active + soft-deactivate. |
| `GET /me/addresses` / `POST` / `PATCH /{id}` / `DELETE /{id}` | Saved-address CRUD with one-default invariant. |
| `GET /mobile/home` | Aggregated payload: profile + default address + recent orders[5] + due reminders[5] + pending Rx[5] + counters. One round trip for the home screen. |
| `GET /track/orders/{code}?phone_last4=NNNN` | **Public** anonymous track. Wrong phone → 404 (same as missing code) so the endpoint cannot be enumerated. |

### Permissions
Reuses existing `iam.user.read.self` and `iam.user.update.self` (granted to `customer` role). No new permissions added — the customer mobile surface is purely "self" operations on the calling user's own data.

### Tests — [test_mobile.py](../app/modules/mobile/tests/test_mobile.py) (~14 tests)
- Profile read + update + auth gate
- Device register (idempotent upsert), list, deactivate, kind validation
- Address CRUD + one-default invariant + cross-user 404 (not 403, to avoid leaking existence)
- Anonymous tracking: correct phone → 200, wrong phone → 404 (same as missing code), bad format → 422
- Aggregated home returns all blocks + counters
- Reminder mark-taken sets `taken_at`; second call → 422
- Reminder snooze rewinds pending `scheduled_for`; bad minutes → 422
- Cross-user reminder action → 403

### Files
- [app/modules/mobile/](../app/modules/mobile/) — state, models, schemas, repository, service, api/, tests/
- [alembic/versions/0017_mobile.py](../alembic/versions/2026_05_03_0017-0017_mobile.py)
- [docs/customer-mobile-api.md](customer-mobile-api.md) — full contract reference for the mobile dev team
- Updated [main.py](../app/main.py), [registry.py](../app/core/db/registry.py), [reminders/models.py](../app/modules/reminders/models.py) (taken_at + snoozed_until), [reminders/schemas.py](../app/modules/reminders/schemas.py) + serializers, [reminders/api/customer.py](../app/modules/reminders/api/customer.py) + service (mark_taken, snooze)

### Out of scope (deliberate)
- **Phone-OTP login** — backend schema is ready (`users.phone`, `phone_verified_at`); SMS provider integration is paused. Mobile app uses email-password until the auth module unblocks.
- **Push fan-out** — device tokens are stored, but the FCM/APNs sender daemon is not wired. Mobile app polls for now.
- **Online payment redirect** — checkout accepts `online` payment method but doesn't yet redirect to a gateway. COD-only until Bkash/SSLCommerz module lands.
- **WebSocket / SSE** — none; polling only.
- **Per-device opt-out for specific notification kinds** — single global `is_active` flag today.

---

## Module 18 — Rider Mobile API Surface (DELIVERED)

### Spec
Prepare APIs for the rider app. Features:
- task, scan, delivery, COD, POD

### Audit before this module
The deliveries module already shipped `pickup`, `upload-pod` (photo), `deliver` (with COD reconciliation + POD-mandatory rule), `fail`, plus list/get for the rider's own assignments. Missing for a real rider mobile UX:

| Feature | Gap |
|---|---|
| Task | List + detail existed but no "today" / "next" view for the route flow |
| Scan | No parcel-scan verification — rider could pick up the wrong parcel |
| Delivery | Worked end-to-end |
| COD | `deliver` accepted collected amount but no rider-side cash-on-hand summary |
| POD | Photo upload + OTP-attest existed; **signature upload missing** |
| (Bonus) | No rider availability toggle, despite the column existing |

### New endpoints (extension of `deliveries/api/rider.py`)

| Endpoint | Behaviour |
|---|---|
| `POST /rider/me/deliveries/availability` | `{status: 'offline'\|'available'\|'busy'}`. Going offline with any active assignment → 422. |
| `GET /rider/me/deliveries/tasks` | Today's queue ordered: in-flight pickups → pending pickups → delivered (awaiting reconcile) → terminal. Returns lightweight `RiderTaskItem` rows + per-status counts. |
| `GET /rider/me/deliveries/tasks/next` | Single next open task, or `null` when idle. |
| `POST /rider/me/deliveries/{id}/scan` | Body: `{scanned_code, intent: 'pickup'\|'delivery'}`. **Always returns 200** with `ok: bool` so the rider app can show a red toast and re-scan immediately on a noisy code. Case-insensitive + trim. Cross-rider 403; mismatch is audited as `outcome=failure`. |
| `POST /rider/me/deliveries/{id}/upload-signature` | Companion to `/upload-pod`. Same allowed mimes (jpg/png/webp). Stored under `kind='signature'` in the existing POD storage layout. Either photo OR signature OR `pod_otp_verified=true` satisfies the POD-mandatory rule on `/deliver`. |
| `GET /rider/me/deliveries/cod-summary` | `{rider_id, expected_total, deposited_total, outstanding, today_collected_amount, today_collected_count}`. Same numbers admin sees on the finance dashboard — both go through `FinanceService.rider_cash_on_hand`. |

### Service layer additions (`deliveries/service.py`)
- `set_rider_availability` — guards offline transition while any assignment is in ASSIGNED / PICKED_UP / DELIVERED.
- `scan_verify` — case-insensitive compare against `order.code`. Always returns a result dict (never raises on mismatch); audits both pass and fail.
- `upload_pod_signature` — mirrors `upload_pod_photo`, writes via existing `PodStorage` with `kind='signature'`.
- `list_today_tasks` — single SQL query with `CASE`-driven status ordering.
- `get_next_task` — picks first open row from `list_today_tasks`.
- `rider_cod_summary` — delegates to `FinanceService.rider_cash_on_hand` and adds today's COD-collection count + amount from `delivery_assignments`.

### Migrations
**None.** Every new endpoint reuses existing columns (`riders.current_status`, `delivery_assignments.pod_signature_path`, `delivery_assignments.cod_collected`).

### Permissions
Reuses `order.fulfill` (already granted to `staff`, `manager`, `admin` roles). No new permissions added.

### Tests — [test_rider_app.py](../app/modules/deliveries/tests/test_rider_app.py) (~10 integration tests)
- Availability toggle works; can't go offline with active assignment (422)
- Today's tasks returns the assigned row with order code + recipient details
- Next task returns the first open assignment; null when idle
- Scan correct code → ok=true; wrong code → 200 ok=false; case-insensitive + trim works; cross-rider → 403
- Signature upload succeeds in PICKED_UP state; rejected in ASSIGNED state (422)
- COD summary after a 200 BDT delivery shows `today_collected_amount=200, today_collected_count=1`

### Reference doc
[docs/rider-app-api.md](rider-app-api.md) — full contract for the rider app team: every endpoint, the POD-mandatory rule, the recommended screen → endpoint mapping, and an explicit list of what's not shipped yet (POD-OTP issue/verify, rider-initiated cash deposit, live GPS guidance, push delivery).

### Files
- Updated [deliveries/service.py](../app/modules/deliveries/service.py) (+5 service methods, ~200 lines)
- Updated [deliveries/schemas.py](../app/modules/deliveries/schemas.py) (+5 mobile schemas)
- Updated [deliveries/api/rider.py](../app/modules/deliveries/api/rider.py) (+6 endpoints)
- New [tests/test_rider_app.py](../app/modules/deliveries/tests/test_rider_app.py)
- New [docs/rider-app-api.md](rider-app-api.md)

### Out of scope (deliberate)
- **POD-OTP issue + verify** — needs SMS provider. `pod_otp_verified` on `/deliver` is rider attestation today.
- **Rider-initiated cash deposit** — cashier records via `/admin/finance/cod-deposits`. Add a rider-initiated submission flow when the workflow demands it.
- **GPS / route guidance** — assignment carries address text only. Rider app constructs its own map link.
- **Push notification fan-out** — device tokens stored (Module 17), sender daemon not wired. Polling `/tasks` is fine for now.
- **Rider-to-rider hand-off** — admin reassigns via `/admin/deliveries/assignments`. Direct rider hand-off is a future endpoint.

---

## Module 19 — Doctor Mobile API Surface (DELIVERED)

### Spec
Prepare APIs for the doctor app. Features:
- AI suggestion, prescription, wallet, report
- (Mid-build addition) capture **patient age + weight** for medicine dose calculation

### Audit before this module
| Feature | Pre-existing | Action |
|---|---|---|
| AI suggestion | ❌ Module 16 had `suggest_medicines` capability but no doctor-flow endpoint | Added `/doctor-rx/ai-suggest` that delegates to AIService with the doctor as the actor |
| Prescription | ✅ issue / list / get / PDF / cancel — full | Extended to capture **patient_weight_kg** + per-line **dose_per_administration** + **dose_form** |
| Wallet (doctor side) | ❌ no doctor view; per-customer wallet only | Added `/wallet/credits-granted` (paginated) + `/wallet/summary` (headline) — joins WalletCredit ↔ DoctorPrescription via `source_id` |
| Report | ❌ none | Added `/reports/activity` (date-range counts + top medicines) |
| _Bonus_ | — | `/age-band/{years}` UI hint endpoint (neonate / infant / child / adolescent / adult / senior) |

### Migration 0018 — `doctor_dose`
- `doctor_prescriptions.patient_weight_kg` — `Numeric(5, 2)`, nullable, CHECK 0.5 ≤ x ≤ 500
- `doctor_prescription_lines.dose_per_administration` — `String(64)`, free-text ("1 tablet", "5 ml", "0.5 mg/kg")
- `doctor_prescription_lines.dose_form` — `String(32)` ("tablet", "syrup", "drop", "puff")

The backend does **not** interpret dose strings or compute totals from them. Real dose calculation lives with either:
1. The AI provider (Module 16's `suggest_medicines` capability — already accepts age + weight)
2. A future BNF / BD-formulary integration

The columns capture the doctor's chosen dose for the audit + PDF + patient-side display.

### Hard rule: AI cannot prescribe (Module 16 boundary)
The `/ai-suggest` endpoint returns a draft `AIProposal` (status=`draft`). The doctor reviews and either:
- Accepts the proposal as a bookmark via `POST /admin/ai/proposals/{id}/accept` (records the agreement), then
- Composes a real prescription manually via `POST /doctor-rx/prescriptions`

There is no path for the AI suggestion to auto-create a prescription. Module 16's AST-scan test guards this from regression.

### New endpoints (`/doctor-rx/*`)

| Endpoint | Notes |
|---|---|
| `POST /ai-suggest` | Body forwards `symptoms`, `patient_age_years`, `patient_weight_kg`, `patient_sex` to the AI provider. Weight folded into the symptoms text since the AI port doesn't carry weight as a structured field yet. |
| `GET /age-band/{age_years}` | UI hint: returns `{age_years, band, notes}`. Bounds follow WHO + ICH E11 (neonate=0, infant<2, child<12, adolescent<18, adult<65, senior 65+). |
| `GET /wallet/credits-granted` | Paginated. Each row: credit + originating Rx code + patient phone + redeemed amount (sum of redeem transactions) + status + expiry. |
| `GET /wallet/summary` | Totals: granted, redeemed, expired, distinct patients, redemption rate (0–1). |
| `GET /reports/activity?starts_on=…&ends_on=…&top_limit=N` | Date-range counts + top-N medicines. Credits redeemed counted by the redeem transaction's `occurred_at` (not the prescription's `issued_at`). |

### Service additions (`doctor_rx/service.py`)
- `ai_suggest_for_doctor` — delegates to `AIService.suggest_medicines` with the doctor as actor; folds weight into symptoms text.
- `wallet_credits_for_doctor` — paginated SQL with a pre-aggregated redeemed-amount subquery so each row is O(1) per join.
- `wallet_summary_for_doctor` — three small aggregates (granted, redeemed, expired) + distinct patient count + computed rate.
- `activity_report_for_doctor` — date-range queries; top-medicines via `GROUP BY product_name ORDER BY count DESC`.
- `infer_age_band(years)` — pure helper, six bands per WHO/ICH E11.

### Permissions
Reuses existing `order.fulfill` (granted to `staff`, `manager`, `admin`). The service-layer `doctor_for_user` check additionally requires the caller to be linked to an active `Doctor` row. No new permissions added.

### Tests — [test_doctor_app.py](../app/modules/doctor_rx/tests/test_doctor_app.py) (~14 tests)
- AI suggest: 502 without provider; works with FakeAIProvider (Module 16 test fake); customer role → 403
- Dose persistence: `patient_weight_kg`, `dose_per_administration`, `dose_form` round-trip on issue
- Weight validation: 0.1 kg → 422 (CHECK constraint enforced via Pydantic field range)
- Age band: parametrized over (0, 1, 5, 15, 35, 70) → expected band; out-of-range 200 → 422
- Wallet summary after a 500 BDT credit grant
- Wallet credits-granted listing with the right amount + patient phone + active status
- Activity report: 2 issued today → counts + top medicine appears with `times_prescribed=2`
- Activity report: bad date range → 422

### Reference doc
[docs/doctor-app-api.md](doctor-app-api.md) — full contract: every endpoint, the dose-fields explanation, the age-band table, recommended screen → endpoint mapping, and explicit out-of-scope (real dose calculator needs a regulated formulary or AI provider).

### Files
- [alembic/versions/0018_doctor_dose.py](../alembic/versions/2026_05_03_0018-0018_doctor_dose.py) — patient_weight_kg + dose columns
- Updated [doctor_rx/models.py](../app/modules/doctor_rx/models.py) (3 new columns)
- Updated [doctor_rx/schemas.py](../app/modules/doctor_rx/schemas.py) (+5 schemas; weight + dose on existing)
- Updated [doctor_rx/service.py](../app/modules/doctor_rx/service.py) (+5 service methods + age-band helper)
- Updated [doctor_rx/api/doctor.py](../app/modules/doctor_rx/api/doctor.py) (+5 endpoints)
- Updated [doctor_rx/api/_serializers.py](../app/modules/doctor_rx/api/_serializers.py) (surface new fields)
- New [tests/test_doctor_app.py](../app/modules/doctor_rx/tests/test_doctor_app.py)
- New [docs/doctor-app-api.md](doctor-app-api.md)

### Out of scope (deliberate)
- **Real dose calculator** — needs a regulated drug database (BNF, BD National Formulary). The schema persists what the doctor chose; AI suggest is the assistive surface.
- **Drug-interaction checks** — same constraint.
- **AI provider** — `NotConfiguredProvider` is default; `/ai-suggest` returns 502 until `bind_provider()` runs at startup with a real adapter.
- **SMS-PDF delivery to no-account patients** — paused on SMS provider creds. Doctor downloads PDF from existing `/prescriptions/{id}/pdf` for now.
- **Doctor commission/payout scheme** — the wallet view here is about credits granted to *patients*; a doctor-commission % of redemptions would be a separate finance module.

---

## Module 20 — Provider Boundaries (DELIVERED, no live creds yet)

### Spec
"Ghost / add all in app — I'll add API later":
- Real dose calculator (BNF / BD National Formulary)
- Drug-interaction checks (same source)
- Live AI provider — primary + backups: **OpenAI primary, Anthropic + Gemini as backup**

### Hard rule preserved
**No fake responses, no invented dose tables.** Every adapter is real HTTP shape against the actual provider's API; default binding is "not configured" which returns 502 with a clear `missing_setting` field naming the env var to populate.

### What ships

#### AI provider chain (extends Module 16)
| File | Purpose |
|---|---|
| `app/modules/ai/providers/openai.py` | `POST /v1/chat/completions` JSON-mode; vision via base64 `image_url`; all 4 capabilities |
| `app/modules/ai/providers/anthropic.py` | `POST /v1/messages` with vision via `inline_data`; system prompt under `system` key |
| `app/modules/ai/providers/gemini.py` | `POST /v1beta/models/{model}:generateContent` with `responseMimeType: application/json` |
| `app/modules/ai/providers/_http.py` | Shared httpx wrapper: 401/403 → IntegrationError, 429 → RateLimitedError, 5xx → ServiceUnavailableError, JSON-block extraction tolerates ```json fences``` |
| `app/modules/ai/providers/fallback.py` | `FallbackAIProvider` retries the next backup on retryable errors; **does NOT retry** on `missing_setting` sentinels (operator misconfig stays visible) |
| `app/modules/ai/providers/factory.py` | Reads `AI_PROVIDER` + `AI_BACKUP_PROVIDERS` from settings, builds + binds the chain. Skips unconfigured backups silently |

Composed name on the bound provider reveals the chain: `"openai+anthropic+gemini"`.

#### Formulary module (new)
- `state.py`, `schemas.py`, `service.py`, `api/doctor.py` — `GET /formulary/status`, `POST /formulary/dose-lookup`, `POST /formulary/interaction-check`
- `providers/base.py` (`FormularyProvider` ABC), `not_configured.py` (default 502)
- `providers/bnf.py` — `api.bnf.nice.org.uk/v2` shape, refuses without `FORMULARY_API_KEY`
- `providers/bd_formulary.py` — operator-hosted internal HTTP service, refuses without API key + `FORMULARY_BASE_URL` (no public default)
- `providers/factory.py` — env-driven binding via `FORMULARY_PROVIDER`

All formulary calls audit-log with action `formulary.dose_lookup` / `formulary.interaction_check` capturing inputs (not invented results).

#### Settings (`app/core/config.py`)
13 new optional fields: `ai_provider`, `ai_backup_providers`, `{openai,anthropic,gemini}_{api_key,base_url,model_default}`, `formulary_{provider,api_key,base_url}`. Defaults all `"none"` so existing tests / dev environments behave unchanged.

#### App lifespan (`main.py`)
Startup binds providers via `bind_from_settings()` for both AI + formulary, logs the bound provider names. Binding errors don't prevent boot — app comes up with NotConfigured (502 on capability calls).

### Tests
- [ai/tests/test_providers_module20.py](../app/modules/ai/tests/test_providers_module20.py) — 11 tests covering: adapter constructors refuse empty key, factory NotConfigured/unknown/builds-OpenAI/builds-fallback-chain/skips-unconfigured-backups, fallback retries on 5xx + 429, fallback respects `missing_setting` sentinel, chain exhausted → last error
- [formulary/tests/test_formulary.py](../app/modules/formulary/tests/test_formulary.py) — 8 tests: status reports `not_configured`; capability endpoints → 502 with missing_setting; customer role → 403; BNF/BD constructors refuse empty creds; factory wiring (none / unknown / empty-key fallback)

### Reference doc
[docs/provider-bindings.md](provider-bindings.md) — every env var, recommended setup, fallback semantics table, list of adapter files, and hard rules.

### Files
- New `app/modules/formulary/` (state, schemas, providers/, service, api/, tests/)
- New `app/modules/ai/providers/{openai,anthropic,gemini,fallback,factory,_http}.py`
- Updated `app/modules/ai/providers/__init__.py` (export factory + fallback)
- Updated [main.py](../app/main.py) (lifespan binds providers + includes formulary router)
- Updated [config.py](../app/core/config.py) (+13 settings, all optional)

### Out of scope (deliberate)
- **Live API testing** — adapters tested for shape + error handling only. Real provider calls require live keys and are flaky in CI; do them in staging.
- **Streaming responses** — Chat Completions only. Streaming requires a different consumer pattern at the AI service layer.
- **Per-tenant API keys** — single set per deployment. Multi-tenant rotation would need a `KeyResolver` indirection.
- **Cost rate limiting** — tokens are recorded in `AIUsageEvent.cost_units`; a budget guard at the AI service entry point is left for when real cost data flows.
- **Local LLM adapters** (Ollama, vLLM) — not in this round. The port is provider-agnostic so adding `LocalAdapter` is straightforward.

---

## Module 21 — Doctor Offline-Sync (DELIVERED)

### Spec
"If device lost internet, software opens offline mode and continues creating prescriptions; once internet returns, prescriptions go into the pharmacist pipeline."

User clarification mid-build: **"there should be no fail option here, should be sent to pharmacist"** — bad payloads must NOT bounce back to the doctor's local queue; pharmacist resolves them manually.

### Hard contract enforced
Every prescription the doctor app pushes lands as an intake row. **The endpoint never returns a 4xx for individual items.** Status is one of:
- `received` (transient, pre-validation)
- `issued` — auto-issue succeeded → linked `doctor_prescriptions` row
- `needs_review` — auto-issue failed for ANY reason; pharmacist sees it in their queue
- `cancelled` — pharmacist cancelled with a reason

### Migration 0019 — `doctor_rx_intakes`
- `(doctor_id, client_uuid)` UNIQUE for idempotency — same client_uuid retried returns the same row
- `client_created_at` from the device clock (offline write timestamp)
- `raw_payload` JSONB — verbatim copy so pharmacist sees what the doctor wrote
- `error_code` / `error_message` / `error_details` populated when status='needs_review'
- `processed_by` + `processed_at` + `process_notes` set when pharmacist resolves
- `issued_prescription_id` FK to the resulting `doctor_prescriptions` row when auto-issue succeeded

### Service contract (`intake_one`)
- Idempotency check first — existing row returned without modification
- Intake row written immediately with `status='received'`
- Auto-issue runs inside a SAVEPOINT (`begin_nested()`) so a failure rolls back ONLY partial Rx state, leaving the intake row writable
- Any exception (Pydantic ValidationError, DomainError, unforeseen) → `status='needs_review'` + error captured
- Audit logged with `doctor_rx.intake.{status}` action

### Endpoints

#### Doctor side
- `POST /doctor-rx/sync/prescriptions` — batch (1–200 items). Returns `{accepted, issued, needs_review, items: [per-item status]}`. **Always 200**; per-item status is the contract.
- `POST /doctor-rx/sync/status` — body `{client_uuids: [...]}`; returns intake views for the ones the server knows. Missing UUIDs imply the client should re-push.

#### Pharmacist side (admin router)
- `GET /admin/doctor-rx/intakes?status=needs_review` — paginated review queue, default `needs_review`.
- `POST /admin/doctor-rx/intakes/{id}/issue` — optional `payload` body for pharmacist edits. Resulting Rx's `doctor_id` stays the original prescriber; pharmacist recorded as actor on audit.
- `POST /admin/doctor-rx/intakes/{id}/cancel` — body `{reason}`. Refuses on terminal status (issued/cancelled).

### Permissions
Reuses `order.fulfill` for both doctor + pharmacist surfaces. No new permissions added.

### Tests — [test_offline_sync.py](../app/modules/doctor_rx/tests/test_offline_sync.py) (~12 tests)
- Single valid Rx → status=`issued` + linked Rx
- Bad variant_id → status=`needs_review` (NOT 422 — the never-fail contract)
- Garbage payload (Pydantic fails) → status=`needs_review`
- Same client_uuid pushed twice → same intake_id (idempotency)
- Mixed batch (3 items, 2 issue, 1 needs review) → all 3 land
- Sync status lookup returns known UUIDs only
- Pharmacist queue lists needs_review intakes
- Pharmacist issues with edits → status=`issued`, original doctor_id preserved
- Pharmacist cancels with reason → status=`cancelled`, process_notes captured
- Double-resolve refused (terminal status → 422)
- Customer role → 403 on sync

### Reference doc
[docs/doctor-offline-sync.md](doctor-offline-sync.md) — full contract for the doctor app team: state machine, endpoints, idempotency, recommended client-side queue (IndexedDB / SQLite) algorithm, retry policy, and explicit out-of-scope items.

### Files
- [alembic/versions/0019_doctor_rx_intakes.py](../alembic/versions/2026_05_03_0019-0019_doctor_rx_intakes.py)
- Updated [doctor_rx/models.py](../app/modules/doctor_rx/models.py) (+`DoctorRxIntake`, +`DoctorRxIntakeStatus`)
- Updated [doctor_rx/schemas.py](../app/modules/doctor_rx/schemas.py) (+8 schemas: OfflineRxItem, OfflineRxBatch{Request,Response}, OfflineRxItemResult, OfflineRxStatusQuery, OfflineRxIntakeView, IntakePharmacistResolveRequest, IntakePharmacistCancelRequest)
- Updated [doctor_rx/repository.py](../app/modules/doctor_rx/repository.py) (+`DoctorRxIntakeRepository`)
- Updated [doctor_rx/service.py](../app/modules/doctor_rx/service.py) (+`intake_one`, +`intake_batch`, +`pharmacist_issue_intake`, +`pharmacist_cancel_intake`)
- Updated [doctor_rx/api/doctor.py](../app/modules/doctor_rx/api/doctor.py) (+`POST /sync/prescriptions`, +`POST /sync/status`)
- Updated [doctor_rx/api/admin.py](../app/modules/doctor_rx/api/admin.py) (+`GET /intakes`, +`POST /intakes/{id}/issue`, +`POST /intakes/{id}/cancel`)
- New [tests/test_offline_sync.py](../app/modules/doctor_rx/tests/test_offline_sync.py)
- New [docs/doctor-offline-sync.md](doctor-offline-sync.md)

### Out of scope (deliberate)
- **Conflict resolution** — same client_uuid edited twice offline = first sync wins. Treat client_uuid as immutable per Rx.
- **Client-side queue implementation** — that's the doctor app's responsibility. The doc describes the recommended IndexedDB / SQLite shape but the backend doesn't ship that code.
- **Push notification when pharmacist resolves** — doctor app polls `/sync/status` for now. Ties into the device-token table (Module 17) once the FCM/APNs sender is wired.
- **End-to-end encryption of the offline queue** — client concern; use OS keychain / Keystore for the IndexedDB key.
- **Out-of-order sync** — `client_created_at` is captured but the server treats sync order as authoritative. Reordering by `client_created_at` for display is a client concern.

---

## Full-system E2E test (DELIVERED, 2026-05-04)

### Spec
"Test full system: Admin → stock → customer order → prescription → approval → packing → delivery → finance → doctor wallet"

### One authoritative test file
[tests/e2e/test_full_pipeline.py](../tests/e2e/test_full_pipeline.py) — single test that walks every module's main happy path in sequence. **If this passes, the whole pipeline is wired correctly.** If it fails, the assertion message + stage label pinpoints the broken module.

### Stages (each verified by assertion)

| # | Stage | Verifies |
|---|---|---|
| 1 | Admin onboards | Brand, supplier, doctor, pharmacist, rider rows; customer phone set so wallet credit auto-links |
| 2 | Receive Rx-required medicine, 50 units @ ৳20 unit_cost | Inventory module + goods receipt + batch creation |
| 3 | Doctor issues Rx with ৳200 wallet credit grant | Doctor-rx + wallet auto-link via phone match (Modules 8 + 9) |
| 4 | Customer places COD order, 2 units of the Rx medicine | Order routes to `prescription_review`; compliance gate passes (pharmacist on duty); EVT_ORDER_PAYMENT_CONFIRMED → finance posts revenue JE |
| 5 | Customer uploads prescription file linked to order | Multipart upload + storage |
| 6 | Pharmacist start-review → approve | Order auto-advances `prescription_review → approved` |
| 7 | Admin start-packing → dispatch | State machine `approved → packing → out_for_delivery` |
| 8 | Admin assigns delivery to rider | `cod_expected = grand_total` snapshot |
| 9 | Rider scans (ok=true), pickup, upload-pod, deliver with exact COD | `assigned → picked_up → completed` (auto-reconcile within 0-cent tolerance) |
| 10 | Outbox drains | Inventory consumes 2 units (50→48 available); finance posts COGS JE |
| 11 | Verify finance journal entries | revenue Cr Sales 200 + Dr AR-COD 200; COGS Dr 40 (2 × 20); cod_collection Dr Cash-on-Hand-Rider 200 + Cr AR-COD 200 |
| 12 | Verify rider COD summary | expected_total=200, deposited=0, outstanding=200 |
| 13 | Verify doctor wallet summary | granted=200, redeemed=0, distinct_patients=1 |
| 14 | Verify customer sees completed order | `/orders` list shows status=completed |

### Test infra
[tests/e2e/conftest.py](../tests/e2e/conftest.py) — three autouse fixtures: re-seed IAM perms/roles, seed finance chart of accounts, register inventory + finance outbox handlers in that order (so EVT_ORDER_COMPLETED triggers consume → COGS in sequence).

### Why one big test instead of many small
This is the **integration** check, not unit coverage. Every module already has its own test suite. The E2E is the one that catches **wiring** bugs: a handler that didn't register, an event payload field renamed, a state-machine transition that broke an upstream module's assumption. One sequence with named stages + targeted assertions is the right shape for that.
