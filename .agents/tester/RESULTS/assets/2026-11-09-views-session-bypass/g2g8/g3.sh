#!/usr/bin/env bash
# G3 — SESSION BYPASS matrix (a-d). Real ensemble_sid minted in-process.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/lib.sh"
load_env
CODE_A="$(cat "$FENCE/passcode-a")"
SID="$(cat "$FENCE/sid-valid")"
GF="$EV/G3.txt"
: > "$GF"
echo "# G3 SESSION BYPASS — $(date -u +%FT%TZ) — sid minted via AuthSessionRepository.insert_session (issue_session field-mirror)" >> "$GF"

V="$BASE_URL$PUB_A"
DIRECT="$BASE_URL/views/$ROOT/$VIEW_A"
BACC="Accept: $BROWSER_ACCEPT"

probe "$GF" "G3a public GET /views/public/<viewA> with valid ensemble_sid → expect 200 view HTML, NO interstitial" \
  -H "$BACC" -H "Cookie: ensemble_sid=$SID" "$V"

probe "$GF" "G3a-control public GET with NO session → expect 200 interstitial (passcode form)" \
  -H "$BACC" "$V"

probe "$GF" "G3b direct (non-public) lane GET with valid session → observe" \
  -H "$BACC" -H "Cookie: ensemble_sid=$SID" "$DIRECT"

probe "$GF" "G3b-control direct lane GET with NO session → observe (middleware lane)" \
  -H "$BACC" "$DIRECT"

# G3c: gate order. Exhaust the per-IP bucket via UNMINTED-path POSTs
# (limiter is gate #1, row-404 is gate #2 — no strikes created), then
# session POST (bypass should precede limiter → 303), then a no-session
# correct-code POST (limiter still empty → 429 proves the 303 was bypass,
# not limiter slack).
UNMINTED="$BASE_URL/views/public/$ROOT/never-minted.md"
probe "$GF" "G3c-prep POST unminted #1 (burn token; expect 404)" \
  -X POST "$UNMINTED" -H "$BACC" -H "X-Forwarded-Proto: https" --data "code=$CODE_A"
probe "$GF" "G3c-prep POST unminted #2" \
  -X POST "$UNMINTED" -H "$BACC" -H "X-Forwarded-Proto: https" --data "code=$CODE_A"
probe "$GF" "G3c-prep POST unminted #3" \
  -X POST "$UNMINTED" -H "$BACC" -H "X-Forwarded-Proto: https" --data "code=$CODE_A"
probe "$GF" "G3c-prep POST unminted #4" \
  -X POST "$UNMINTED" -H "$BACC" -H "X-Forwarded-Proto: https" --data "code=$CODE_A"
probe "$GF" "G3c-prep POST unminted #5" \
  -X POST "$UNMINTED" -H "$BACC" -H "X-Forwarded-Proto: https" --data "code=$CODE_A"
probe "$GF" "G3c-prep POST unminted #6 (bucket now empty → expect 429)" \
  -X POST "$UNMINTED" -H "$BACC" -H "X-Forwarded-Proto: https" --data "code=$CODE_A"

probe "$GF" "G3c-1 POST viewA WITH session, bucket EXHAUSTED → 303 here proves session bypass precedes rate limiter" \
  -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" -H "Cookie: ensemble_sid=$SID" --data "code=$CODE_A"

probe "$GF" "G3c-2 POST viewA NO session, correct code, bucket STILL exhausted → expect 429 (proves G3c-1 was bypass)" \
  -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" --data "code=$CODE_A"

probe "$GF" "G3d plain GET with session — Set-Cookie audit (expect NO set-cookie: GET mints nothing)" \
  -H "$BACC" -H "Cookie: ensemble_sid=$SID" "$V?probe=g3d-nocookie"

echo "== G3 complete"
