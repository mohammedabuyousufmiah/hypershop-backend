# HYPERSHOP HARDENED — 2026-05-13 build changelog

This build closes the last 2 items of the 9-item production audit
(items #8 and #9) and fixes a critical transaction bug that was
silently rolling back every order placement.

## Critical bug fix — checkout transaction poisoning

**Symptom**: orders looked successful at the API layer but never
committed; order rows never appeared in the database.

**Root cause**: `app/modules/checkout/api/router.py` was running a
customer-address UPSERT inside the order-confirm transaction. The
UPSERT blindly set `is_default=True`, which violated the partial
unique constraint `uq_customer_addresses_one_default` on the second
call. The `except Exception: pass` swallowed the IntegrityError but
left the SQLAlchemy session in an aborted state, so the outer
transaction rolled back on commit.

**Fix**: check for an existing default address before insert; set
`is_default=not has_default`. Capture session IDs up front so we
never lazy-load on a poisoned session. Added
`CheckoutService.verify_totals_integrity` as a defensive guard.

Files: `app/modules/checkout/api/router.py`,
`app/modules/checkout/service.py`.

## #8 — COD → rider → wallet (end-to-end verified)

Already wired; was blocked by the txn bug above plus two smaller
gaps. With those fixed the full lifecycle now passes:

| Step                                | Evidence                                  |
|-------------------------------------|-------------------------------------------|
| Order placed (COD, BDT 28,550)      | `orders` row, status=completed            |
| Rider deliver with `cod_collected`  | assignment row, status=completed          |
| Outbox `deliveries.delivery.delivered` | dispatched via `dispatch_once()`       |
| Wallet `cod_collection` debit       | `rider_wallet_ledger`, balance=28,550     |
| MFS settlement submit               | `wallet_pending_settlement=28,550`        |
| Admin verify                        | `wallet_status=clear`, all balances 0     |

Files changed:
- `app/modules/iam/permissions.py` — `ROLE_RIDER` now includes
  `P_ORDER_FULFILL` (was missing — blocked rider pickup/deliver
  endpoints).
- `app/modules/orders/schemas.py` — `DeliveryAddress` now accepts
  `address_line1`/`line1` and `address_line2`/`line2` via Pydantic
  `AliasChoices`, plus `extra="ignore"` so historical rows with extra
  keys (e.g. `country_code` from checkout) deserialise cleanly.

## #9 — Return → refund → seller liability (new feature)

When a customer return is COMPLETED, the seller who owned the variant
is automatically debited the refunded amount via the new
`seller_wallet_ledger`. The payout aggregator reads from this ledger
when computing per-period payouts.

**Files (new)**:
- `alembic/versions/2026_05_13_0046-0046_seller_wallet_ledger.py` —
  append-only ledger table with partial unique index on
  `(return_request_line_id, entry_type)` for idempotency.
- `app/modules/sellers/wallet_models.py` — `SellerWalletLedger` ORM.
- `app/modules/sellers/wallet_service.py` —
  `SellerWalletService.debit_for_return(...)`. Uses `INSERT ... ON
  CONFLICT DO NOTHING` so outbox at-least-once redelivery is safe.
- `app/modules/sellers/handlers.py` — outbox handler on
  `returns.return.completed`. Walks every return line, joins to
  `products.seller_id`, posts one debit row per seller-owned line.
  First-party (NULL `seller_id`) lines silently skipped.

**Files (changed)**:
- `app/main.py` — imports the new handlers module on startup so
  registration runs.
- `app/modules/sellers/payout_service.py` — `return_debit` now reads
  from `seller_wallet_ledger` (single source of truth). The prior
  pattern-matching queries against a non-existent `returns` table
  always returned 0.

**E2E proof** (see `scripts/test_return_seller_debit.py`):
- Customer requests return on completed order
- Admin: receive → inspect (sealed → restock) → complete
- Outbox drained → seller debited 28,490 BDT in ledger
- Payout preview for the period now shows `return_debit: 28,490` and
  net_payable correctly negative (commission still owed)
- Re-drain → ledger row count stays at 1 (idempotency confirmed)

## Outbox dispatcher resilience fix

`app/core/events/dispatcher.py` — `_process()` previously
short-circuited on the first handler failure, which meant one bad
handler (e.g. finance account-not-found) blocked all other
subscribers from running on the same event. Now collects errors
across all handlers and only marks the message for retry if any
failed; successful handlers don't re-run their effects (relies on
each handler being idempotent, which they already are).

## Dev / verification scripts (new)

- `scripts/test_cod_settlement.py` — end-to-end driver: rider login,
  MFS submit, admin verify, ledger inspection.
- `scripts/test_return_seller_debit.py` — end-to-end driver: customer
  return request, admin receive/inspect/complete, outbox drain,
  seller ledger verification.
- `scripts/trace_txn_close.py` — SQLAlchemy txn-event tracer used
  while diagnosing the checkout transaction bug; kept for future
  txn-lifecycle investigations.

## Remaining items from the original 9-item audit

| # | Item                                   | State        |
|---|----------------------------------------|--------------|
| 1 | Remove/disable admin stubs in production | done        |
| 2 | Complete seller payout/settlement       | done        |
| 3 | Payment success → order confirmation    | done        |
| 4 | Frozen + audit-safe checkout totals     | done        |
| 5 | Stop ignoring TS/ESLint errors          | done        |
| 6 | Remove signing keys from release bundle | done        |
| 7 | Use only real products + real images    | deferred — user will upload real images |
| 8 | COD → rider → wallet                    | **done this build** |
| 9 | Return → refund → seller liability      | **done this build** |
