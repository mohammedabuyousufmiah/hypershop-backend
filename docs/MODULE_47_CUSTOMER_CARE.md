# Module 47 — AI Customer Care + Sales Automation

Integrated from the standalone `CUSTOMER_CARE_AUTOMATION_DATABASE_READY_v1.6.4`
package on 2026-05-13.

## Surface

- **Backend** — `app/modules/customer_care/`
  - HTTP API mounted under `/api/v1/customer-care/*`
  - WhatsApp Cloud API webhook at `/api/v1/customer-care/webhooks/whatsapp`
- **Agent PWA** — served at `/customercare` once the Vite build runs
  (see "Building the dashboard" below)
- **Schema** — 14 `cc_*` tables (migration `0047_customer_care`)
- **Roles** — `customercare_agent`, `customercare_admin`

## Integration decisions

1. **CC's User/Customer/Product/Order tables were dropped.** CC reads
   from Hypershop's existing `users`, `products`, `orders` tables —
   one source of truth, no parallel customer records.
2. **Auth uses Hypershop IAM** (no separate CC login). Grant a user
   the `customercare_agent` or `customercare_admin` role and they can
   log in at `/auth/login`, then access `/customercare` and the
   `/api/v1/customer-care/*` API.
3. **All CC tables prefixed `cc_`** so the names don't collide with
   Hypershop's existing schema.
4. **Multi-tenant `tenant_id` columns dropped.** Hypershop is
   single-tenant.
5. **Voice module deferred** (separate sub-zip `sprint54_voice_module.zip`).
6. **Frontend served as static PWA** at `/customercare`. Vite `base`
   set to `/customercare/` so assets resolve correctly behind the path.

## Tables

| Table | Purpose | FK targets |
|---|---|---|
| `cc_agent_profile` | per-user agent state (PK=user_id) | `users.id` |
| `cc_customer_profile` | per-customer CC state (PK=customer_id) | `users.id` |
| `cc_conversations` | agent↔customer threads | `users.id` × 2, `orders.id` |
| `cc_messages` | message log | `cc_conversations.id` |
| `cc_followups` | drip campaign rows | `users.id`, `products.id` |
| `cc_dead_letters` | failed background ops | — |
| `cc_webhook_idempotency` | dedupe `(channel, message_id)` | — |
| `cc_csat_surveys` | CSAT scoring | `cc_conversations.id`, `users.id` × 2 |
| `cc_sla_policies` | SLA timers | — |
| `cc_gdpr_deletion_requests` | privacy delete queue | `users.id` × 2 |
| `cc_knowledge_documents` | RAG sources | — |
| `cc_knowledge_chunks` | RAG chunks + embeddings | `cc_knowledge_documents.id` |
| `cc_checkout_events` | external storefront webhooks | — |
| `cc_payment_events` | external payment gateway webhooks | — |

## Permissions

| Permission | Granted to | Use |
|---|---|---|
| `customercare.agent` | agent + admin roles | inbox read/reply, status |
| `customercare.admin` | admin role | follow-up campaigns, SLA config |
| `customercare.rag.admin` | admin role | KB ingest / reindex |

## API endpoints (13 routes, all under `/api/v1/customer-care/`)

| Method | Path | Perm | Purpose |
|---|---|---|---|
| GET | `/me` | agent | Get my agent profile (auto-provisions on first call) |
| PATCH | `/me/status` | agent | Update my availability |
| GET | `/dashboard/summary` | agent | Top-of-page counters |
| GET | `/conversations` | agent | List `mine` / `unassigned` / `all` |
| GET | `/conversations/{id}` | agent | Full thread with messages |
| POST | `/conversations/{id}/messages` | agent | Send agent reply (auto-claims if unassigned) |
| POST | `/conversations/{id}/transfer` | agent | Transfer to another agent |
| POST | `/conversations/{id}/resolve` | agent | Mark resolved + decrement agent load |
| GET | `/customers/{id}` | agent | Customer profile (joins Hypershop users + cc_customer_profile) |
| POST | `/followups` | admin | Create drip campaign row |
| GET | `/followups` | admin | List campaigns |
| GET | `/webhooks/whatsapp` | (public) | Meta verify-token challenge handshake |
| POST | `/webhooks/whatsapp` | (public) | Inbound message ingestion |

## E2E smoke-test (verified 2026-05-13)

```
1. WhatsApp webhook POST → 1 message ingested
2. Synthetic Hypershop customer user auto-created (phone +8801911740672)
3. Conversation auto-created and auto-assigned to online agent
4. Agent reply via POST /conversations/{id}/messages → first_response_at populated
5. Dashboard counters update: open=1, online_agents=1
```

## Operational env vars

| Var | Default | Use |
|---|---|---|
| `CC_WHATSAPP_VERIFY_TOKEN` | `hypershop-cc` | Meta verify-token (set on Meta Cloud webhook page too) |

The other CC env vars (WhatsApp creds, OpenAI key, Google Sheets, etc.)
are still consumed by the ported CC adapters in
`app/modules/customer_care/external_adapters/`. Add them to `.env` as
needed; the module degrades gracefully if any are missing (e.g. AI
auto-reply skipped, outbound WhatsApp logs only).

## Building the dashboard PWA

The Vite/React source lives at
`app/modules/customer_care/_frontend_src/`. On deployment:

```bash
cd app/modules/customer_care/_frontend_src
pnpm install
pnpm build
```

This emits `dist/`. The Hypershop backend's
`_mount_customer_care_pwa()` auto-detects `dist/` and serves it as
static files at `/customercare`. If `dist/` is missing, the route
returns a clear placeholder page with the build instructions instead
of a 404.

Vite is configured with `base: "/customercare/"` so all asset URLs
emit with the correct prefix.

## Deferred / next-phase

1. **Voice module** — `sprint54_voice_module.zip` not integrated this
   pass. Would add Whisper transcription + voice-note storage to the
   inbox.
2. **AI auto-reply** — the original CC service has Bangla-first AI
   rules with English fallback (in `services.py::ai_reply`). Wired
   models are present but the OpenAI integration adapter is not yet
   plugged into the new router's webhook path. Add it inside
   `whatsapp_inbound` after `append_message(... sender_type='customer')`.
3. **RAG ingest** — `rag/` directory ported but routes for
   `/kb/documents` and `/kb/search` not yet exposed in the new
   router. Add them gated on `customercare.rag.admin`.
4. **CSAT survey send** — model + token field present;
   `/conversations/{id}/csat/start` route not yet ported.
5. **Outbox subscribers** — when Hypershop emits `orders.order.completed`,
   CC should post a thank-you + CSAT prompt. Add a handler module
   under `customer_care/handlers.py` similar to
   `sellers/handlers.py`.
6. **SLA scanner cron** — `sla.py` ported; needs an ARQ cron entry to
   actually scan `cc_conversations` for breach.

These are clean follow-ups — the foundation (schema + auth + router +
dashboard mount + webhook ingestion + reply) is in place and verified.
