#!/usr/bin/env bash
# rk_C2.sh — BOOT 2 session-path interaction (R1 session bypass).
#
# A valid operator session is the FOURTH allowed grant source per the
# commission's escalation rule. The session bypass is BY DESIGN: it runs
# BEFORE the rate limiter and BEFORE the origin gate ("never origin-checked"),
# so a grant here is the R1 session bypass, NOT a defect.
# The regression that WOULD be a defect: an origin-reject breaking a valid
# session through a LEGIT vouched shape ⇒ the session must still grant.
# Session probes do not consume the verify rate-limiter bucket (the bypass
# precedes gate #1) ⇒ no 13s pacing needed.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/rk_lib.sh"
load_env
SID="$(cat "$FENCE/session-id")"
CODE="unused-dummy-code (the session bypass never reaches the compare)"
GF="$EV/C2-boot2-session.txt"
: > "$GF"
echo "# GROUP C2 — BOOT 2 session path (auth ON ⇒ R1 session bypass live) — $(date -u +%FT%TZ)" >> "$GF"
V="$BASE_URL$PUB_R"
BACC="Accept: $BROWSER_ACCEPT"

rowpeek "before-C2"

probe "$GF" "C17 VALID SESSION + forged XFH evil + Origin evil ⇒ EXPECT 303 (BY-DESIGN R1 session bypass — the session precedes the origin gate)" \
  303 -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" -H "Host: op.example" \
  -H "Cookie: ensemble_sid=$SID" \
  -H "X-Forwarded-Host: evil.example" -H "Origin: https://evil.example" --data "code=dummy-not-used"
sleep 2
probe "$GF" "C18 VALID SESSION + vouched shape (XFH op.example + Origin op.example) ⇒ EXPECT 303 (regression guard: the legit proxied shape must grant)" \
  303 -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" -H "Host: op.example" \
  -H "Cookie: ensemble_sid=$SID" \
  -H "X-Forwarded-Host: op.example" -H "Origin: https://op.example" --data "code=dummy-not-used"
sleep 2
probe "$GF" "C19 VALID SESSION + NO Origin ⇒ EXPECT 303" \
  303 -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" -H "Host: op.example" \
  -H "Cookie: ensemble_sid=$SID" --data "code=dummy-not-used"
sleep 2
probe "$GF" "C20 VALID SESSION + Origin null + Sec-Fetch-Site: cross-site ⇒ EXPECT 303 (RECORD — the session bypass is total: never origin-checked)" \
  RECORD -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" -H "Host: op.example" \
  -H "Cookie: ensemble_sid=$SID" \
  -H "Origin: null" -H "Sec-Fetch-Site: cross-site" --data "code=dummy-not-used"
sleep 2
probe "$GF" "C21 GARBAGE session cookie + forged XFH evil + Origin evil ⇒ EXPECT 403 (a stale/garbage session falls through to the anonymous chain)" \
  403 -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" -H "Host: op.example" \
  -H "Cookie: ensemble_sid=not-a-real-session-id" \
  -H "X-Forwarded-Host: evil.example" -H "Origin: https://evil.example" --data "code=dummy-not-used"
sleep 2

rowpeek "after-C2"
echo "== GROUP C2 (session) complete: $(grep -c '^####' "$GF") probes"
