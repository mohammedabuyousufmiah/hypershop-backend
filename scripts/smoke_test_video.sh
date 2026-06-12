#!/usr/bin/env bash
# scripts/smoke_test_video.sh
# ─────────────────────────────────────────────────────────────────────
# End-to-end smoke test for the product-video pipeline (Module 35).
#
# What it verifies, in order:
#   1. API health
#   2. Generates a 3-second test video using the worker container's ffmpeg
#   3. Upload endpoint accepts it (POST /product-videos/products/{id}/upload)
#   4. Job is dispatched and the worker picks it up (status moves
#      uploaded → processing → ready_for_review within 60 s)
#   5. FFmpeg outputs are populated on the row (hls_url, thumbnail_url,
#      duration_seconds)
#   6. Admin /pending queue lists the row
#   7. Admin /approve flips the row to status=approved
#   8. Public GET /products/{id}/videos returns the approved video
#   9. The HLS master playlist URL returns HTTP 200 (CDN mode = Bunny;
#      disk-fallback mode = the api's /catalog/videos/files route)
#
# Required runtime tools on the HOST: bash, curl, jq, docker compose.
# ffmpeg is NOT required on the host — we shell out to the worker
# container's binary so the same version that runs in production
# generates the test fixture.
#
# Required env (or fail-fast with a useful message):
#   SMOKE_PRODUCT_ID         existing product UUID to attach the video to
#
# One of the following auth modes:
#   SMOKE_ADMIN_TOKEN        bearer token (preferred for CI)
#   SMOKE_ADMIN_EMAIL +      credentials → script does /auth/login
#   SMOKE_ADMIN_PASSWORD
#
# Optional env:
#   API_BASE_URL             default http://localhost:8000
#   COMPOSE_FILE             default docker-compose.yml
#   POLL_TIMEOUT_S           default 60 (max wait for ready_for_review)
#
# Exit codes:
#   0  all checks passed
#   1  a check failed (see stderr for which one)
#   2  pre-flight (env / tooling) failed before any test ran

set -euo pipefail

API="${API_BASE_URL:-http://localhost:8000}"
PREFIX="/api/v1"
POLL_TIMEOUT_S="${POLL_TIMEOUT_S:-60}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.yml}"
TEST_FIXTURE="/tmp/smoke-product-video.mp4"

# ─────────── ANSI helpers ───────────
if [[ -t 1 ]]; then
    BOLD=$(tput bold || true); GREEN=$(tput setaf 2 || true)
    RED=$(tput setaf 1 || true); RESET=$(tput sgr0 || true)
else
    BOLD=""; GREEN=""; RED=""; RESET=""
fi

step() { printf '\n%s▶ %s%s\n' "$BOLD" "$*" "$RESET"; }
ok()   { printf '%s  ✓ %s%s\n' "$GREEN" "$*" "$RESET"; }
fail() { printf '%s  ✗ FAIL: %s%s\n' "$RED" "$*" "$RESET" >&2; exit 1; }
preflight_fail() {
    printf '%s  ✗ PRE-FLIGHT: %s%s\n' "$RED" "$*" "$RESET" >&2
    exit 2
}

# ─────────── Pre-flight ───────────
step "0. Pre-flight checks"

for cmd in curl jq docker; do
    command -v "$cmd" >/dev/null 2>&1 \
        || preflight_fail "missing required tool: $cmd"
done
ok "host tools (curl, jq, docker) present"

[[ -n "${SMOKE_PRODUCT_ID:-}" ]] \
    || preflight_fail "SMOKE_PRODUCT_ID is not set; need an existing product UUID"
ok "SMOKE_PRODUCT_ID = $SMOKE_PRODUCT_ID"

if [[ -n "${SMOKE_ADMIN_TOKEN:-}" ]]; then
    TOKEN="$SMOKE_ADMIN_TOKEN"
    ok "Using SMOKE_ADMIN_TOKEN (skip /auth/login)"
elif [[ -n "${SMOKE_ADMIN_EMAIL:-}" && -n "${SMOKE_ADMIN_PASSWORD:-}" ]]; then
    LOGIN_RESP=$(curl -fsS -X POST "$API$PREFIX/auth/login" \
        -H "Content-Type: application/json" \
        -d "{\"email\":\"$SMOKE_ADMIN_EMAIL\",\"password\":\"$SMOKE_ADMIN_PASSWORD\"}" \
        ) || preflight_fail "/auth/login failed for $SMOKE_ADMIN_EMAIL"
    TOKEN=$(echo "$LOGIN_RESP" | jq -r '.access_token // .access // empty')
    [[ -n "$TOKEN" ]] \
        || preflight_fail "/auth/login response had no access_token: $LOGIN_RESP"
    ok "Logged in as $SMOKE_ADMIN_EMAIL"
else
    preflight_fail "set SMOKE_ADMIN_TOKEN OR (SMOKE_ADMIN_EMAIL + SMOKE_ADMIN_PASSWORD)"
fi

AUTH_HEADER="Authorization: Bearer $TOKEN"

# ─────────── 1. API health ───────────
step "1. API health"
HEALTH=$(curl -sS -o /dev/null -w "%{http_code}" "$API$PREFIX/health")
[[ "$HEALTH" == "200" ]] || fail "GET /health returned $HEALTH"
ok "GET $PREFIX/health → 200"

# ─────────── 2. Generate test fixture inside worker container ───────────
step "2. Generate 3-second test video via worker container's ffmpeg"
docker compose -f "$COMPOSE_FILE" exec -T worker bash -c '
    ffmpeg -nostdin -loglevel error -y \
        -f lavfi -i testsrc=duration=3:size=854x480:rate=24 \
        -f lavfi -i sine=frequency=440:duration=3 \
        -c:v libx264 -preset ultrafast -t 3 -pix_fmt yuv420p \
        -c:a aac -b:a 64k -ac 1 \
        -movflags +faststart \
        /tmp/smoke-test.mp4
' || fail "ffmpeg generation in worker failed"
docker compose -f "$COMPOSE_FILE" cp worker:/tmp/smoke-test.mp4 "$TEST_FIXTURE"
[[ -s "$TEST_FIXTURE" ]] || fail "fixture file empty after copy"
SIZE=$(stat -c '%s' "$TEST_FIXTURE" 2>/dev/null || stat -f '%z' "$TEST_FIXTURE")
ok "fixture written ($SIZE bytes)"

# ─────────── 3. Upload ───────────
step "3. Upload to /product-videos/products/$SMOKE_PRODUCT_ID/upload"
UPLOAD_RESP=$(curl -fsS -X POST \
    "$API$PREFIX/product-videos/products/$SMOKE_PRODUCT_ID/upload" \
    -H "$AUTH_HEADER" \
    -F "file=@$TEST_FIXTURE;type=video/mp4" \
    -F "title=smoke-test-$(date +%s)" \
    ) || fail "upload returned non-2xx"
VIDEO_ID=$(echo "$UPLOAD_RESP" | jq -r .video_id)
UPLOAD_STATUS=$(echo "$UPLOAD_RESP" | jq -r .status)
[[ "$VIDEO_ID" =~ ^[0-9a-f-]+$ ]] || fail "no video_id in upload response: $UPLOAD_RESP"
[[ "$UPLOAD_STATUS" == "uploaded" ]] || fail "expected status=uploaded, got: $UPLOAD_STATUS"
ok "upload accepted, video_id=$VIDEO_ID, initial status=uploaded"

# ─────────── 4. Wait for ffmpeg processing ───────────
step "4. Poll for processing → ready_for_review (timeout: ${POLL_TIMEOUT_S}s)"
DEADLINE=$(($(date +%s) + POLL_TIMEOUT_S))
STATUS="(unknown)"
while [[ $(date +%s) -lt $DEADLINE ]]; do
    ROW=$(curl -fsS -H "$AUTH_HEADER" \
        "$API$PREFIX/admin/catalog/videos/$VIDEO_ID") \
        || fail "admin GET row failed"
    STATUS=$(echo "$ROW" | jq -r .status)
    case "$STATUS" in
        ready_for_review) break ;;
        failed)
            ERR=$(echo "$ROW" | jq -r '.processing_error // "(no error)"')
            fail "processing failed: $ERR"
            ;;
        uploaded|processing) sleep 2 ;;
        *) fail "unexpected status during polling: $STATUS" ;;
    esac
done
[[ "$STATUS" == "ready_for_review" ]] \
    || fail "did not reach ready_for_review within ${POLL_TIMEOUT_S}s (last: $STATUS)"
ok "status reached ready_for_review"

# ─────────── 5. FFmpeg outputs populated ───────────
step "5. Verify hls_url + thumbnail_url + duration_seconds on row"
ROW=$(curl -fsS -H "$AUTH_HEADER" "$API$PREFIX/admin/catalog/videos/$VIDEO_ID")
HLS_URL=$(echo "$ROW" | jq -r .hls_url)
THUMB_URL=$(echo "$ROW" | jq -r .thumbnail_url)
DURATION=$(echo "$ROW" | jq -r .duration_seconds)
[[ "$HLS_URL"  != "null" && -n "$HLS_URL"  ]] || fail "hls_url not populated"
[[ "$THUMB_URL" != "null" && -n "$THUMB_URL" ]] || fail "thumbnail_url not populated"
[[ "$DURATION" =~ ^[0-9]+$ && "$DURATION" -gt 0 ]] \
    || fail "duration_seconds invalid: $DURATION"
ok "hls_url        = $HLS_URL"
ok "thumbnail_url  = $THUMB_URL"
ok "duration       = ${DURATION}s"

# ─────────── 6. Admin pending queue includes this video ───────────
step "6. Pending queue contains video"
PENDING=$(curl -fsS -H "$AUTH_HEADER" "$API$PREFIX/admin/product-videos/pending")
PENDING_HIT=$(echo "$PENDING" \
    | jq -r --arg id "$VIDEO_ID" '.items[] | select(.id == $id) | .id')
[[ "$PENDING_HIT" == "$VIDEO_ID" ]] \
    || fail "video $VIDEO_ID not in /admin/product-videos/pending"
ok "video appears in pending queue"

# ─────────── 7. Approve ───────────
step "7. POST /admin/product-videos/$VIDEO_ID/approve"
APPROVE_RESP=$(curl -fsS -X POST \
    "$API$PREFIX/admin/product-videos/$VIDEO_ID/approve" \
    -H "$AUTH_HEADER") || fail "approve returned non-2xx"
APPROVED_STATUS=$(echo "$APPROVE_RESP" | jq -r .status)
APPROVED_AT=$(echo "$APPROVE_RESP" | jq -r .approved_at)
[[ "$APPROVED_STATUS" == "approved" ]] \
    || fail "expected status=approved, got: $APPROVED_STATUS"
[[ "$APPROVED_AT" != "null" ]] \
    || fail "approved_at not stamped"
ok "status=approved, approved_at=$APPROVED_AT"

# ─────────── 8. Public list returns the approved video ───────────
step "8. Public GET /products/$SMOKE_PRODUCT_ID/videos"
PUBLIC=$(curl -fsS "$API$PREFIX/products/$SMOKE_PRODUCT_ID/videos")
PUBLIC_ROW=$(echo "$PUBLIC" \
    | jq -r --arg id "$VIDEO_ID" '.items[] | select(.id == $id)')
[[ -n "$PUBLIC_ROW" ]] \
    || fail "approved video missing from public list"
PUBLIC_HLS=$(echo "$PUBLIC_ROW" | jq -r .hls_url)
PUBLIC_THUMB=$(echo "$PUBLIC_ROW" | jq -r .thumbnail_url)
PUBLIC_PRODUCT=$(echo "$PUBLIC_ROW" | jq -r .product_id)
[[ "$PUBLIC_PRODUCT" == "$SMOKE_PRODUCT_ID" ]] \
    || fail "public row product_id mismatch: $PUBLIC_PRODUCT"
[[ "$PUBLIC_HLS"  == "$HLS_URL"  ]] || fail "public hls_url drift: $PUBLIC_HLS"
[[ "$PUBLIC_THUMB" == "$THUMB_URL" ]] || fail "public thumbnail_url drift"
ok "public list contains row with matching hls + thumbnail URLs"

# ─────────── 9. HLS playlist actually fetchable ───────────
step "9. HLS master playlist is HTTP 200"
# CDN mode: hls_url is absolute (https://...b-cdn.net/...)
# Disk fallback: hls_url is /api/v1/catalog/videos/files/... — needs $API prefix
if [[ "$PUBLIC_HLS" == http* ]]; then
    PLAYLIST_URL="$PUBLIC_HLS"
else
    PLAYLIST_URL="$API$PUBLIC_HLS"
fi
HLS_CODE=$(curl -sS -o /tmp/smoke-master.m3u8 -w "%{http_code}" "$PLAYLIST_URL")
[[ "$HLS_CODE" == "200" ]] || fail "HLS GET $PLAYLIST_URL → $HLS_CODE"
grep -q '^#EXTM3U' /tmp/smoke-master.m3u8 \
    || fail "fetched file is not a valid HLS playlist (missing #EXTM3U)"
ok "HLS master playlist reachable + valid"

printf '\n%s═══════════════════════════════════════════════════%s\n' "$GREEN" "$RESET"
printf '%s  All smoke checks passed ✓%s\n' "$GREEN" "$RESET"
printf '  video_id     = %s\n' "$VIDEO_ID"
printf '  product_id   = %s\n' "$SMOKE_PRODUCT_ID"
printf '  duration_s   = %s\n' "$DURATION"
printf '  hls_url      = %s\n' "$HLS_URL"
printf '  thumbnail    = %s\n' "$THUMB_URL"
printf '%s═══════════════════════════════════════════════════%s\n' "$GREEN" "$RESET"
