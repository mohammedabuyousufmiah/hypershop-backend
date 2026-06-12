#!/usr/bin/env bash
# scripts/run_readiness.sh
# ─────────────────────────────────────────────────────────────────────
# Runs the AUTO-CHECKABLE subset of docs/PRODUCTION_READINESS.md gates
# against the current workstation / stack. Prints a clear
# pass/fail/skip/manual summary at the end and exits non-zero if any
# automatable gate failed.
#
# What it covers
#   ✅ Gate 2 — backend integration tests (pytest)
#   ✅ Gate 3 — frontend Vitest tests
#   ✅ Gate 9 — rollback runbook file present + has expected tokens
#   ⚙ Gate 1 — smoke test (only if SMOKE_* env is set)
#   ⚙ Gate 4 — compose health check (only if a stack is running)
#   ⚙ Gate 8 — upload validation curl probes (only if stack + admin token set)
#
# What it CANNOT cover (always printed as MANUAL at the end)
#   ☐ Gate 5 — real R2 + Bunny credential roundtrip (requires creds)
#   ☐ Gate 6 — real-browser HLS playback (Chrome / Safari iOS / Chrome Android)
#   ☐ Gate 7 — audit log inspection on the DB
#   ☐ Gate 10 — monitoring / log signal verification on the aggregator
#
# Required env (depending on which gates you want to auto-run):
#   API_BASE_URL          default http://localhost:8000
#   FRONTEND_DIR          default ../../hypershop-Frontend\ final
#   SMOKE_PRODUCT_ID      enables Gate 1
#   SMOKE_ADMIN_TOKEN     enables Gate 1 (or _EMAIL + _PASSWORD)
#   READINESS_BEARER      admin bearer token for Gate 8 curl probes
#   READINESS_PRODUCT_ID  product UUID for Gate 8 (can equal SMOKE_PRODUCT_ID)
#
# Exit codes:
#   0 — every AUTO-checked gate passed (manual gates are informational only)
#   1 — one or more auto-checked gates failed
#   2 — pre-flight tooling missing

set -uo pipefail   # NOTE: not -e — we want to keep checking after a failure

# ─────────── colour helpers ───────────
if [[ -t 1 ]]; then
    BOLD=$(tput bold || true); GREEN=$(tput setaf 2 || true)
    RED=$(tput setaf 1 || true); YELLOW=$(tput setaf 3 || true)
    BLUE=$(tput setaf 4 || true); RESET=$(tput sgr0 || true)
else
    BOLD=""; GREEN=""; RED=""; YELLOW=""; BLUE=""; RESET=""
fi

# Track every gate's outcome for the summary block at the end.
# Each entry is "STATUS|GATE|MESSAGE".
declare -a RESULTS=()

step()   { printf '\n%s━━━ %s%s\n' "$BOLD" "$*" "$RESET"; }
record() { RESULTS+=("$1|$2|$3"); }
pass()   { printf '  %s✓%s %s\n'  "$GREEN"  "$RESET" "$2"; record PASS   "$1" "$2"; }
fail()   { printf '  %s✗%s %s\n'  "$RED"    "$RESET" "$2"; record FAIL   "$1" "$2"; }
skip()   { printf '  %s⤳%s %s\n'  "$YELLOW" "$RESET" "$2"; record SKIP   "$1" "$2"; }
manual() { printf '  %s☐%s %s\n'  "$BLUE"   "$RESET" "$2"; record MANUAL "$1" "$2"; }

# ─────────── pre-flight ───────────
step "Pre-flight tooling"
for cmd in pytest curl jq; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
        printf '  %s✗%s missing required tool: %s\n' "$RED" "$RESET" "$cmd" >&2
        exit 2
    fi
done
pass preflight "host tools (pytest, curl, jq) available"

API_BASE_URL="${API_BASE_URL:-http://localhost:8000}"
FRONTEND_DIR="${FRONTEND_DIR:-../../hypershop-Frontend final}"

# ─────────── Gate 2 — backend integration tests ───────────
step "Gate 2 — backend integration tests"
if pytest -q --no-header --no-summary -x \
        app/modules/product_videos/tests/; then
    pass G2 "pytest app/modules/product_videos/tests/ — all green"
else
    fail G2 "pytest module tests failed (see output above)"
fi

# ─────────── Gate 3 — frontend Vitest ───────────
step "Gate 3 — frontend Vitest tests"
if [[ -d "$FRONTEND_DIR" ]]; then
    if (
        cd "$FRONTEND_DIR" || exit 1
        if [[ ! -d "node_modules" ]]; then
            echo "  installing node_modules (first run only)…"
            npm install --silent || exit 1
        fi
        npm test --silent
    ); then
        pass G3 "npm test in $FRONTEND_DIR — all green"
    else
        fail G3 "Vitest run failed in $FRONTEND_DIR"
    fi
else
    skip G3 "frontend dir not found at $FRONTEND_DIR (set FRONTEND_DIR to override)"
fi

# ─────────── Gate 1 — smoke test (optional) ───────────
step "Gate 1 — end-to-end smoke test"
if [[ -n "${SMOKE_PRODUCT_ID:-}" ]] && \
   { [[ -n "${SMOKE_ADMIN_TOKEN:-}" ]] || \
     { [[ -n "${SMOKE_ADMIN_EMAIL:-}" ]] && [[ -n "${SMOKE_ADMIN_PASSWORD:-}" ]]; }; }; then
    if bash scripts/smoke_test_video.sh; then
        pass G1 "smoke test exit 0"
    else
        fail G1 "smoke test failed (see output above)"
    fi
else
    skip G1 "SMOKE_PRODUCT_ID + (SMOKE_ADMIN_TOKEN | SMOKE_ADMIN_EMAIL+PASSWORD) not set"
fi

# ─────────── Gate 4 — compose health check (if stack is up) ───────────
step "Gate 4 — docker compose health check"
if docker compose ps --status running 2>/dev/null | grep -q -E "api\s|worker\s"; then
    health=$(curl -fsS -m 5 "$API_BASE_URL/api/v1/health" 2>/dev/null || echo "")
    if [[ "$health" == *'"status"'* ]] && [[ "$health" == *"ok"* ]]; then
        pass G4 "GET /api/v1/health returned {status:ok}"
    else
        fail G4 "GET /api/v1/health did not return a healthy body"
    fi

    # /metrics endpoint sanity (closes the turn-28 wiring check)
    metrics=$(curl -fsS -m 5 "$API_BASE_URL/metrics" 2>/dev/null || echo "")
    if [[ "$metrics" == *"product_video_status_count"* ]]; then
        pass G4-metrics "GET /metrics serves Module 35 family"
    else
        fail G4-metrics "GET /metrics did not include product_video_status_count"
    fi
else
    skip G4 "no running api/worker container — start with 'make compose-up' first"
fi

# ─────────── Gate 8 — validation curl probes (optional) ───────────
step "Gate 8 — upload validation curl probes"
if [[ -n "${READINESS_BEARER:-}" ]] && [[ -n "${READINESS_PRODUCT_ID:-}" ]]; then
    BAD_TOKEN_CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST \
        "$API_BASE_URL/api/v1/product-videos/products/$READINESS_PRODUCT_ID/upload" \
        -F "file=@/etc/hostname" 2>/dev/null || echo 0)
    if [[ "$BAD_TOKEN_CODE" == "401" ]]; then
        pass G8a "no auth → 401"
    else
        fail G8a "no auth → expected 401, got $BAD_TOKEN_CODE"
    fi

    BAD_EXT=$(curl -s -X POST \
        "$API_BASE_URL/api/v1/product-videos/products/$READINESS_PRODUCT_ID/upload" \
        -H "Authorization: Bearer $READINESS_BEARER" \
        -F "file=@/etc/hostname" 2>/dev/null \
        | jq -r '.code // ""')
    if [[ "$BAD_EXT" == "product_video_unsupported_type" ]]; then
        pass G8b "wrong extension → product_video_unsupported_type"
    else
        fail G8b "wrong extension → expected product_video_unsupported_type, got '$BAD_EXT'"
    fi

    NOPE_CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST \
        "$API_BASE_URL/api/v1/product-videos/products/00000000-0000-0000-0000-000000000000/upload" \
        -H "Authorization: Bearer $READINESS_BEARER" \
        -F "file=@/etc/hostname" 2>/dev/null || echo 0)
    if [[ "$NOPE_CODE" == "404" ]] || [[ "$NOPE_CODE" == "422" ]]; then
        pass G8e "missing product → $NOPE_CODE (404 or 422)"
    else
        fail G8e "missing product → expected 404/422, got $NOPE_CODE"
    fi
else
    skip G8 "READINESS_BEARER + READINESS_PRODUCT_ID not set"
fi

# ─────────── Gate 9 — rollback runbook present ───────────
step "Gate 9 — rollback runbook"
runbook="docs/ROLLBACK_MODULE_35.md"
if [[ -f "$runbook" ]]; then
    missing_tokens=()
    for token in "alembic downgrade" "R2" "Bunny" "TTR"; do
        if ! grep -q -F "$token" "$runbook"; then
            missing_tokens+=("$token")
        fi
    done
    if [[ ${#missing_tokens[@]} -eq 0 ]]; then
        pass G9 "$runbook present + contains required tokens"
    else
        fail G9 "$runbook missing tokens: ${missing_tokens[*]}"
    fi
    manual G9-review "non-author has read $runbook end-to-end (sign in PRODUCTION_READINESS.md)"
else
    fail G9 "$runbook not found"
fi

# ─────────── Manual-only gates ───────────
step "Manual-only gates (cannot be automated)"
manual G5  "Gate 5 — real R2 + Bunny credential roundtrip (PRODUCTION_READINESS.md §Gate 5)"
manual G6  "Gate 6 — real-browser HLS playback (Chrome desktop / Safari iOS / Chrome Android)"
manual G7  "Gate 7 — audit log row inspection (psql query in PRODUCTION_READINESS.md §Gate 7)"
manual G10 "Gate 10 — monitoring signal verified on aggregator + on-call rotation aware"

# ─────────── Summary ───────────
printf '\n%s════════════ Summary ════════════%s\n' "$BOLD" "$RESET"

PASS_N=0; FAIL_N=0; SKIP_N=0; MANUAL_N=0
for r in "${RESULTS[@]:-}"; do
    [[ -z "${r:-}" ]] && continue
    status="${r%%|*}"
    case "$status" in
        PASS)   PASS_N=$((PASS_N+1)) ;;
        FAIL)   FAIL_N=$((FAIL_N+1)) ;;
        SKIP)   SKIP_N=$((SKIP_N+1)) ;;
        MANUAL) MANUAL_N=$((MANUAL_N+1)) ;;
    esac
done

printf '%s  ✓ Auto-passed: %s%s\n' "$GREEN"  "$PASS_N"   "$RESET"
printf '%s  ✗ Auto-failed: %s%s\n' "$RED"    "$FAIL_N"   "$RESET"
printf '%s  ⤳ Skipped (env not set / stack not up): %s%s\n' "$YELLOW" "$SKIP_N" "$RESET"
printf '%s  ☐ Need manual sign-off: %s%s\n' "$BLUE"  "$MANUAL_N" "$RESET"

echo ""
if [[ "$FAIL_N" -gt 0 ]]; then
    printf '%sNOT READY — %d auto-checks failed.%s See output above.\n' "$RED" "$FAIL_N" "$RESET"
    exit 1
fi

if [[ "$SKIP_N" -gt 0 ]] || [[ "$MANUAL_N" -gt 0 ]]; then
    printf '%sAuto-checks green.%s Sign off the %d manual / %d skipped gates in PRODUCTION_READINESS.md before customer-live.\n' \
        "$YELLOW" "$RESET" "$MANUAL_N" "$SKIP_N"
else
    printf '%sAll automatable gates green.%s Manual gates already signed off → cleared per PRODUCTION_READINESS.md.\n' \
        "$GREEN" "$RESET"
fi
exit 0
