# Hypershop — Multi-Seller / Marketplace Scoping

**Status:** scoping document. **No code work has happened.** This is a roadmap proposal that needs product + business sign-off before any implementation.

**Why this is a doc, not code:** the codebase has zero existing seller infrastructure (verified by grep — the only mention is the nullable `seller_id` column on `product_videos`, added defensively in turn 4 but unused). Building marketplace functionality requires architectural decisions that only the business owner can make.

---

## Current state (audit)

| Surface | Status |
|---|---|
| `Seller` / `Merchant` / `Vendor` table | ❌ does not exist |
| Per-product ownership (`products.seller_id`) | ❌ no column |
| Seller role in IAM | ❌ no role / no permissions |
| Seller authentication / onboarding flow | ❌ none |
| Seller dashboard frontend | ❌ none |
| Seller payouts / commission engine | ❌ none |
| `product_videos.seller_id` column | ✅ exists, nullable, never populated |

The single existing `seller_id` column was forward-compatibility insurance from turn 4 — when sellers exist, Module 35 doesn't need a migration to start filling it. But everything else is greenfield.

---

## Cross-cutting decisions needed BEFORE any code

| Decision | Options | Impact if wrong |
|---|---|---|
| **Single-seller per product** vs **multi-seller per product (offer model)** | (a) one-to-many products→seller (Daraz-style), (b) many-to-many via `seller_offers` (Amazon-style) | option (b) is 3× the schema + UI complexity; choose based on actual catalog model |
| **First-party (Hypershop-as-merchant) vs full marketplace** | (a) Hypershop sells direct + lets sellers list as exception, (b) every product has a seller | (a) simpler tax + accounting, slower seller acquisition; (b) cleaner long-term |
| **KYC requirements** | TIN + NID + bank details + trade license? | Bangladesh regulatory minimum is TIN + NID + bank for VAT-registered sellers; below VAT threshold may be NID-only. Check with legal |
| **Payout cadence + method** | weekly / bi-weekly / monthly via bKash / bank transfer / Nagad | impacts cashflow runway + ops headcount |
| **Commission structure** | flat % per category / tiered by GMV / hybrid | core business model — needs CFO sign-off |
| **Seller onboarding gate** | self-serve + admin approve / admin-invite-only | self-serve scales, admin-invite controls quality; pick based on seller acquisition strategy |
| **Returns / disputes who eats** | seller bears cost / Hypershop bears / split per category | affects supplier_payments module integration + customer trust |

These aren't engineering decisions — they're product / finance / legal decisions. Without them, any code I write would be guesswork.

---

## Phased build plan

### Phase 1 — Data model + admin onboarding (1 week)

**Scope:**
- New module `app/modules/sellers/` (mirrors `app/modules/supplier_payments/` structure)
- Tables: `sellers` (id, business_name, status, kyc fields, payout fields, audit timestamps), `seller_users` (link N IAM users → 1 seller, with role: owner/manager/staff)
- IAM: new role `seller`, new permission scopes `sellers.read`, `sellers.write`, `sellers.admin.*`
- Admin endpoints: `/admin/sellers/{create, list, approve, reject, suspend}` (mirrors `/admin/supplier-payments/` pattern)
- Migration `0032_sellers.py`
- ~10 unit tests (lifecycle: create → submit kyc → admin approve → first product)

**Out of scope this phase:** seller-self-serve registration UI, public seller pages, payouts.

**Estimated effort:** 1 dev-week.

### Phase 2 — Product ownership uplift (3-5 days)

**Scope:**
- Migration: `products.seller_id UUID NULL` (nullable so existing first-party catalog isn't broken)
- Backfill script: existing products → "Hypershop Direct" seller (one row in `sellers` representing first-party)
- Service-layer guard in catalog write endpoints: when caller is non-admin, only allow operating on products where `seller_id == caller's seller`
- Module 35 `register_upload`: read seller_id from caller's principal automatically (currently the column exists but never populated)
- Backwards compat: admin-created products get `seller_id` set to "Hypershop Direct"

**Estimated effort:** 3-5 days incl. testing the new authz path against every catalog endpoint.

### Phase 3 — Seller dashboard API + auth (1 week)

**Scope:**
- Seller-scoped JWT enrichment: token includes `seller_id` claim when user has the seller role
- New endpoint set under `/api/v1/seller/*`: list MY products, MY orders, MY videos (Module 35 already supports filter by seller_id), MY payouts, MY KYC status
- Object-level authz: every seller endpoint asserts `principal.seller_id == row.seller_id` (mirrors patterns from rider/customer wallet)
- Tests: ~15 (object-level authz, can't-see-other-seller's-data, etc.)

**Estimated effort:** 1 dev-week.

### Phase 4 — Self-serve onboarding flow (1 week)

**Scope:**
- Public registration endpoint `/api/v1/sellers/register` (rate-limited, captcha-gated)
- KYC document upload (reuses `delivery_pod_dir` storage pattern OR R2 if configured)
- State machine: `registered → kyc_submitted → admin_review → approved | rejected`
- Email/WhatsApp notifications via existing transports on each transition
- Backend tests + admin-side review flow

**Estimated effort:** 1 dev-week.

### Phase 5 — Payouts + commission (2 weeks)

**Scope:**
- New module `app/modules/seller_payouts/` (mirrors `rider_wallet`)
- Commission ledger tables, payout cycles, MFS settlement integration (reuses Bkash provider from Module 22)
- Admin payout approval workflow (mirrors supplier_payments)
- Seller-side payout history endpoint

**Estimated effort:** 2 dev-weeks.

### Phase 6 — Frontend seller portal (2-3 weeks)

Separate Next.js app OR new section in customer-web. Out of scope for this scoping doc — needs its own design.

---

## Total estimate

**6 phases × ~1.3 weeks average = ~8 dev-weeks** for backend, +2-3 weeks frontend portal, before launch.

This is a quarter-sized initiative, not a single sprint.

---

## Recommended starting point

If business says "go":

1. **Run a 30-min decision meeting** with product + finance + legal on the 7 cross-cutting decisions above. Write the answers down.
2. **Phase 1 only** in the first sprint. Don't pre-build phases 2+; the answers from step 1 shape them.
3. **Phase 2 in the second sprint** — only after phase 1 is in production and at least one "Hypershop Direct" seller has been created + verified.
4. Phases 3-5 in subsequent sprints. **Do NOT** parallelise — each builds on the previous.

If business says "wait" or "uncertain":

- **Don't build anything.** The `seller_id` placeholder on `product_videos` already exists and costs nothing. When the business is ready, start with phase 1.

---

## What I'd ship in ONE turn if you say "yes, start phase 1"

- `alembic/versions/2026_05_NN-0032_sellers.py` — migration
- `app/modules/sellers/{models,schemas,errors,codes,repository,service}.py` — module skeleton
- `app/modules/sellers/api/admin.py` — admin endpoints (create / list / approve / reject / suspend)
- `app/core/db/registry.py` + `app/main.py` wiring
- `app/modules/iam/permissions.py` — new seller perm scopes
- `app/modules/sellers/tests/` — 8-10 unit tests on the lifecycle
- Memory entry

That's ~600 LOC + a migration. Doable in one focused turn.

---

## Decision needed

**Tell me:**
- **A** "Phase 1 — start now" (I'll build the 12-file module skeleton next turn)
- **B** "Schedule a product meeting first" (no code; I'll move to D / E / G / H)
- **C** "Skip multi-seller entirely" (close this scoping doc, move on)

Without an answer, I'll treat this as **C** by default and continue down the list to D.
