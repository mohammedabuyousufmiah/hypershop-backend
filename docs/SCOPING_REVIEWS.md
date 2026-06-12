# Hypershop — Reviews + Ratings + Q&A Scoping

**Status:** scoping document. **Zero backend infrastructure exists** (verified by grep). Frontend `components/ReviewsSection.tsx` renders 6 hardcoded testimonials from a static array — pretty UI, fake data.

Covers D (Customer reviews + ratings) + F (PDP reviews + Q&A) from the turn-34 list — same domain.

---

## Current state

| Surface | Status |
|---|---|
| Reviews / ratings table | ❌ none |
| Reviews API | ❌ none |
| Q&A table | ❌ none |
| Frontend `ReviewsSection.tsx` | ✅ exists; static `REVIEWS` array; not API-wired |
| AI moderation (Module 20) | ✅ exists — could auto-flag toxic / spam reviews |
| Audit log infra | ✅ exists — moderation actions can hook in |
| Search reranker (Module 28) | ✅ exists — could feed average rating into ranking |

---

## Cross-cutting decisions

Fewer than multi-seller, but still real:

| Decision | Options |
|---|---|
| **Verified-purchase only?** | (a) anyone with an account can review, (b) only customers who bought the product, (c) (b) but with a "guest reviewer" notation | most BD competitors do (b) — anti-spam, builds trust |
| **Star scale** | 1-5 (industry standard) / 1-10 / thumbs up-down / multi-dimensional (delivery / quality / price) | 1-5 + optional photo. Multi-dimensional confuses BD users at this scale |
| **Photo / video reviews** | yes / no / yes-but-admin-approved | Yes-with-approval — reuses Module 35 pipeline if approved + reuses moderation patterns |
| **Q&A separate from reviews?** | combined / separate tables | separate. Different flows — Q&A is 1-question-N-answers, reviews are 1-customer-1-review |
| **Reply / nested comments?** | none / 1-level (seller responds to review) / threaded | 1-level for v1. Threaded is forum complexity |
| **Moderation gate** | self-publish + reactive takedown / pre-publish admin approval / AI-flag-then-human | AI-flag-then-human (reuses Module 20). Pre-approval doesn't scale |
| **Edits + deletes** | customer can edit forever / 24h window / never | 24h window after publish. Permanent edit trail in audit log |
| **Display sort** | newest / most-helpful (votes) / verified-buyer-first / rating-aware | most-helpful + verified-buyer pin, fallback newest |
| **Rating aggregate visibility** | recompute on every read / cached on product / computed nightly | cached field on `products` updated via outbox event on every review write |

---

## Phased build plan

### Phase 1 — Reviews core (1 week)

**Scope:**
- New module `app/modules/reviews/`
- Tables: `product_reviews` (customer_id, product_id, rating 1-5, title, body, status enum, helpful_count, created_at, updated_at, moderated_by, rejection_reason)
- Verified-purchase derivation: query `orders` for shipped+delivered orders containing the product variant within last 90 days
- Endpoints:
  - `POST /api/v1/products/{id}/reviews` — customer creates (auth required, verified-purchase check)
  - `GET /api/v1/products/{id}/reviews?sort=helpful|newest&offset=&limit=` — public
  - `GET /api/v1/products/{id}/rating` — aggregate (cached)
  - `POST /api/v1/reviews/{id}/helpful` — customer upvote
  - `POST /api/v1/admin/reviews/{id}/{approve,reject,disable}` — admin moderation (mirrors Module 35 patterns)
- Aggregate maintenance via outbox event on every review status transition
- ~12 tests

**Estimated effort:** 1 dev-week.

### Phase 2 — Photo / video review uplift (3 days)

- `review_media` table (review_id, kind, url) reusing Module 35 storage adapter
- Customer review submission accepts attached photos (admin moderation flow already covers media via Module 35 pipeline)
- Frontend wiring in `ReviewsSection.tsx` to render media

### Phase 3 — Q&A surface (1 week)

- Separate module `app/modules/product_qa/`
- Tables: `product_questions` (customer_id, product_id, body, status, created_at), `product_answers` (question_id, customer_id, body, status, helpful_count, created_at, is_seller_answer)
- Endpoints mirror reviews but Q+A shape (1 question N answers)
- Same moderation pipeline as reviews

### Phase 4 — AI-assisted moderation (3 days)

- Hook Module 20 (AI providers) — submit new reviews + answers to AI for spam / toxicity / off-topic scoring
- High-risk content auto-routed to `pending_admin_review`; low-risk auto-published
- Threshold tunable per product category

### Phase 5 — Search rank integration (2 days)

- Add `avg_rating` + `review_count` to Module 28's search reranker feature set
- Backfill script computes both for every existing product
- Periodic recompute job (daily) for accuracy drift

---

## Total estimate

**5 phases × ~5 days = ~5 dev-weeks**, plus frontend wiring (separate). Smaller initiative than multi-seller, but still a real chunk.

---

## Recommended starting point

Phases 1 + 2 are the customer-visible feature; ship those first (~10 days). Phase 3 (Q&A) is independent and can land in parallel. Phases 4 + 5 are quality-of-life enhancements that should wait until phase 1 has 2 weeks of real-world usage data to inform thresholds.

---

## What I'd ship in ONE turn if you say "yes, start phase 1"

- `alembic/versions/2026_05_NN-0033_reviews.py`
- `app/modules/reviews/{models,schemas,errors,codes,repository,service}.py`
- `app/modules/reviews/api/{customer,admin,public}.py`
- IAM permissions: `reviews.write`, `reviews.admin.*`
- ~12 unit tests
- `Header.tsx` ratings widget integration spec
- Memory entry

~700 LOC + migration. Tight but doable.

---

## Decision

- **A** "Phase 1 + 2 — start now" (ships customer-visible reviews this sprint)
- **B** "Phase 1 only — defer photo media to next sprint" (smaller delta)
- **C** "Schedule product meeting on the 9 cross-cutting decisions first"
- **D** "Skip reviews entirely / different priority" (close this doc)

Default = D (continue down the original list to E).
