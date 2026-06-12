# Reviews — Frontend Wiring Spec (Phases 1 + 2)

This is the contract the customer-web frontend implements to render reviews on the PDP. Today `components/ReviewsSection.tsx` shows static testimonials; this doc maps every static element to the live API.

**Backend status:** code-complete on phases 1 + 2 (see `docs/SCOPING_REVIEWS.md`). Frontend wiring is the remaining gap.

---

## API surface

All endpoints are mounted under `${NEXT_PUBLIC_API_BASE_URL}/api/v1`.

| Path | Method | Auth | Purpose |
|---|---|---|---|
| `/products/{id}/reviews?sort=helpful\|newest&offset=&limit=` | GET | none | Public list with media |
| `/products/{id}/rating` | GET | none | Cached aggregate (avg + count) |
| `/products/{id}/reviews` | POST | bearer (`reviews.write`) | Create — verified-purchase enforced |
| `/reviews/{review_id}` | PATCH | bearer (own review, ≤24h after create) | Edit |
| `/reviews/{review_id}/helpful` | POST | bearer | Idempotent upvote (no self-vote) |
| `/reviews/{review_id}/media` | POST `multipart` | bearer (own review) | **Phase 2** — attach photo (≤5 MB, JPEG/PNG/WebP, max 4 per review) |

---

## Wire shapes

### `GET /products/{id}/reviews` response (phase-2 shape)

```json
{
  "items": [
    {
      "id": "uuid",
      "product_id": "uuid",
      "rating": 5,
      "title": "Great pharmacy experience",
      "body": "Fast delivery, original product, fair price.",
      "helpful_count": 12,
      "created_at": "2026-05-08T14:30:00Z",
      "customer_display_name": "Yousuf",
      "is_verified_purchaser": true,
      "media": [
        {
          "id": "uuid",
          "review_id": "uuid",
          "kind": "image",
          "url": "/media/review_media/<review_id>/<hex>.jpg",
          "content_type": "image/jpeg",
          "file_size_bytes": 124530,
          "position": 0
        }
      ]
    }
  ],
  "total": 47
}
```

The `media` array is empty when no photos have been attached. The order is `position ASC, created_at ASC`.

### `GET /products/{id}/rating` response

```json
{
  "product_id": "uuid",
  "avg_rating": "4.62",
  "review_count": 47
}
```

`avg_rating` is a string because Pydantic `Decimal` serialises that way — the frontend should `parseFloat` for display.

### `POST /products/{id}/reviews` request

```json
{ "rating": 5, "title": "optional", "body": "min 10 chars" }
```

**Errors:**
- 403 `review_not_verified_purchaser` — show "Only customers who bought this product can review it"
- 409 `review_already_exists` — show "You've already reviewed this product" + link to edit
- 422 (Pydantic) — field validation (rating range, body length)

### `POST /reviews/{review_id}/media` request

```
Content-Type: multipart/form-data
file: <binary>   (JPEG / PNG / WebP, ≤ 5 MB)
```

**Errors:**
- 422 `review_media_unsupported_type`
- 422 `review_media_too_large`
- 409 `review_media_too_many` — review already has 4 photos
- 409 `review_bad_state` — review is rejected/disabled
- 404 `review_not_found` — caller doesn't own the review

---

## Frontend changes (concrete)

### `lib/api/reviews.ts` (new)

```typescript
export interface ReviewMedia {
  id: string
  review_id: string
  kind: 'image'
  url: string
  content_type: string
  file_size_bytes: number
  position: number
}

export interface PublicReview {
  id: string
  product_id: string
  rating: number
  title: string | null
  body: string
  helpful_count: number
  created_at: string
  customer_display_name: string | null
  is_verified_purchaser: boolean
  media: ReviewMedia[]
}

export interface ReviewListResponse {
  items: PublicReview[]
  total: number
}

export interface ProductRating {
  product_id: string
  avg_rating: string  // serialised Decimal — parseFloat at render
  review_count: number
}

export const reviewsApi = {
  listForProduct: (productId: string, sort: 'helpful' | 'newest' = 'helpful') =>
    api.get<ReviewListResponse>(
      `/products/${productId}/reviews?sort=${sort}`,
      { anonymous: true },
    ),

  getRating: (productId: string) =>
    api.get<ProductRating>(
      `/products/${productId}/rating`,
      { anonymous: true },
    ),

  create: (productId: string, body: { rating: number; title?: string; body: string }) =>
    api.post<PublicReview>(`/products/${productId}/reviews`, body),

  voteHelpful: (reviewId: string) =>
    api.post<{ review_id: string; helpful_count: number; voted: boolean }>(
      `/reviews/${reviewId}/helpful`,
      {},
    ),

  attachPhoto: (reviewId: string, file: File) => {
    const fd = new FormData()
    fd.append('file', file)
    return api.postMultipart<ReviewMedia>(
      `/reviews/${reviewId}/media`,
      fd,
    )
  },
}
```

### `components/ReviewsSection.tsx` rewrite

Replace the static `REVIEWS` array with a `useEffect` fetch:

```tsx
const [data, setData] = useState<ReviewListResponse | null>(null)
useEffect(() => {
  reviewsApi.listForProduct(productId).then(setData).catch(() => setData({ items: [], total: 0 }))
}, [productId])
```

Then render each `item.media[]` as a clickable thumbnail rail above the body:

```tsx
{review.media.length > 0 && (
  <div className="flex gap-2 mt-2">
    {review.media.map(m => (
      <img
        key={m.id}
        src={resolveVideoUrl(m.url)}
        alt=""
        loading="lazy"
        className="w-20 h-20 object-cover rounded cursor-pointer"
        onClick={() => openLightbox(m.url)}
      />
    ))}
  </div>
)}
```

`resolveVideoUrl` from `lib/api/videos.ts` works for review media too — same relative-path-or-CDN-URL contract.

### Add a "Write a review" CTA on the PDP

Below the existing reviews section, render a form when the customer is logged in AND has the product in a `completed` order. The simplest approach: optimistically render the form, let the 403 surface "you can't review until you've purchased" inline.

Form fields:
- 1–5 star picker
- Title (optional, max 160)
- Body (required, 10–4000 chars)
- File picker for up to 4 photos (after create succeeds, attach photos sequentially via `attachPhoto`)

### Add a "rating" widget at the top of the PDP

```tsx
const [rating, setRating] = useState<ProductRating | null>(null)
useEffect(() => {
  reviewsApi.getRating(productId).then(setRating)
}, [productId])

// render: ★★★★☆ 4.6 (47 reviews)  — or hide entirely if review_count === 0
```

---

## Tests to add

1. `__tests__/reviews.test.tsx` — Vitest + RTL — renders mocked review list, verifies media thumbnails appear, verifies aggregate widget reads from `getRating`
2. `__tests__/reviewSubmission.test.tsx` — verifies the 403 path renders "verified purchaser only" copy and the 201 path closes the form

---

## What's NOT in phase 2

- Q&A tab (phase 3 — separate `/questions` + `/answers` API)
- AI moderation indicator on the customer side (phase 4 — backend hooks Module 20)
- Search reranker integration ("sort by best-rated") on the listing page (phase 5)
- Photo editing / deletion by the customer (admin-only purge for now)

These all keep the phase-1/2 contract above stable; later phases only add fields, never remove.
