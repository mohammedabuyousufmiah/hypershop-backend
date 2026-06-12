# Provider bindings — AI + Formulary

How to wire real external providers when credentials arrive. The
backend ships with the integration **boundary** + adapter skeletons in
place; the operator only sets env vars and restarts.

---

## AI providers

Three real adapters ship: **OpenAI** (primary), **Anthropic** + **Gemini**
(backups). Plus a `FallbackAIProvider` wrapper that retries the next
provider on a retryable failure.

### Env vars

| Var | Default | Notes |
|---|---|---|
| `AI_PROVIDER` | `none` | Primary. One of `openai`, `anthropic`, `gemini`, `none`. |
| `AI_BACKUP_PROVIDERS` | `""` | Comma-sep failover order, e.g. `anthropic,gemini`. |
| `OPENAI_API_KEY` | — | Required when `openai` is in the chain. |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | Override for proxies / Azure-OpenAI compat. |
| `OPENAI_MODEL_DEFAULT` | `gpt-4o-mini` | |
| `ANTHROPIC_API_KEY` | — | Required when `anthropic` is in the chain. |
| `ANTHROPIC_BASE_URL` | `https://api.anthropic.com/v1` | |
| `ANTHROPIC_MODEL_DEFAULT` | `claude-sonnet-4-5` | |
| `GEMINI_API_KEY` | — | Required when `gemini` is in the chain. |
| `GEMINI_BASE_URL` | `https://generativelanguage.googleapis.com/v1beta` | |
| `GEMINI_MODEL_DEFAULT` | `gemini-2.0-flash` | |

### Recommended setup (primary + 2 backups)

```bash
AI_PROVIDER=openai
AI_BACKUP_PROVIDERS=anthropic,gemini
OPENAI_API_KEY=sk-…
ANTHROPIC_API_KEY=sk-ant-…
GEMINI_API_KEY=…
```

App startup binds the provider chain (see `main.py` lifespan). Log
line: `providers_bound ai_provider=openai+anthropic+gemini`.

### Fallback semantics

The `FallbackAIProvider` wrapper tries the primary first. On a
**retryable** failure it tries the next backup, in order:

| Exception | Retry? | Why |
|---|---|---|
| `ServiceUnavailableError` (5xx, timeout) | ✅ | Provider may be temporarily down |
| `RateLimitedError` (429) | ✅ | Quota hit, backup probably has capacity |
| `IntegrationError` (provider returned 4xx) | ✅ | Bad response shape — try a different provider |
| `IntegrationError` with `details.missing_setting` | ❌ | "Not configured" sentinel — fail loud, don't disguise misconfig |
| Anything else (Pydantic, code bugs) | ❌ | Surface immediately |

Chain exhausted → re-raises the LAST error so the AI usage ledger
records the most informative cause.

### Adapters

Each adapter's source file is the spec — real HTTP shape, no fake
responses, refuses to construct without an API key:

- [openai.py](../app/modules/ai/providers/openai.py) — `POST /v1/chat/completions` with `response_format: json_object`
- [anthropic.py](../app/modules/ai/providers/anthropic.py) — `POST /v1/messages` with vision via `inline_data` base64
- [gemini.py](../app/modules/ai/providers/gemini.py) — `POST /v1beta/models/{model}:generateContent` with `responseMimeType: application/json`

All four AI capabilities (`ocr_prescription`, `suggest_medicines`,
`predict_stock`, `detect_fraud`) are implemented for each adapter.

---

## Formulary providers

Two adapter skeletons: **BNF** (UK British National Formulary, paid
licence) and **BD National Formulary** (TBD — internal HTTP service the
operator hosts). No fallback chain — only one formulary is bound at
a time.

### Env vars

| Var | Default | Notes |
|---|---|---|
| `FORMULARY_PROVIDER` | `none` | One of `bnf`, `bd_formulary`, `none`. |
| `FORMULARY_API_KEY` | — | Required when a provider is selected. |
| `FORMULARY_BASE_URL` | provider-default | BNF defaults to the public API URL; `bd_formulary` has no public default — operator must set. |

### Endpoints

| Endpoint | Notes |
|---|---|
| `GET /formulary/status` | Which provider is bound, capabilities, configured? |
| `POST /formulary/dose-lookup` | Body: `DoseLookupRequest` (generic, age, weight, indication). Returns `DoseRecommendation`. |
| `POST /formulary/interaction-check` | Body: `{generic_names: [a, b, …]}`. Returns warnings list. |

All audit-logged with action `formulary.dose_lookup` / `formulary.interaction_check`.

### Adapters

- [bnf.py](../app/modules/formulary/providers/bnf.py) — REST against `api.bnf.nice.org.uk/v2`
- [bd_formulary.py](../app/modules/formulary/providers/bd_formulary.py) — operator-hosted internal HTTP service

---

## Hard rules preserved

1. **No fake responses.** Default `none` → 502 with clear missing-setting message on every call.
2. **Adapters refuse to construct** with empty API key — IntegrationError at startup.
3. **Factory swallows construction errors** so app boot never fails — the relevant endpoints just return 502.
4. **AI cannot prescribe** (Module 16 boundary still applies — the AI proposal is a draft the doctor reviews; AST-scan test guards against regression).
5. **Formulary lookups are assistive** — doctor still owns the prescribing decision; the dose recommendation is a hint, not a prescription.
