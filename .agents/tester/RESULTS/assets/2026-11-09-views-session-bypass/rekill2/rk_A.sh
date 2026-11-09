#!/usr/bin/env bash
# rk_A.sh — GROUP A: THE EXPLOIT MUST DIE (re-kill ×3, spaced) + controls.
#
# Shape: BOOT 1 — auth-OFF, declared front ENSEMBLE_LIVE_VIEWS_EXTERNAL_BASE_URL
#        =https://views.example (the FIX-7 knob). No audience.
# Topology: realistic fronting proxy — Host = the INTERNAL hop
#        (internal-svc:8079, NOT vouched, NOT loopback), X-Forwarded-Host =
#        the client-facing host. Browser Accept + X-Forwarded-Proto: https.
# Expect: forged XFH + evil Origin ⇒ 403 text/html, NO Set-Cookie,
#        failed_attempts UNCHANGED (the gate precedes the compare).
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/rk_lib.sh"
load_env
CODE="$(cat "$FENCE/passcode-view")"
GF="$EV/A-rekill.txt"
: > "$GF"
{
  echo "# GROUP A — RE-KILL 2 (FIX-10 / ac5f024fc) — $(date -u +%FT%TZ)"
  echo "# view=$PUB_R  correct code, XFP https, browser Accept"
  echo "# Host=internal-svc:8079 (internal hop) unless stated"
} >> "$GF"
V="$BASE_URL$PUB_R"
BACC="Accept: $BROWSER_ACCEPT"
HOP="Host: internal-svc:8079"

rowpeek "before-A-cluster"

probe "$GF" "A1 EXPLOIT re-kill #1: XFH evil.example + Origin https://evil.example ⇒ EXPECT 403, NO set-cookie" \
  403 -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" \
  -H "$HOP" -H "X-Forwarded-Host: evil.example" -H "Origin: https://evil.example" --data "code=$CODE"
pace 14
probe "$GF" "A2 EXPLOIT re-kill #2 (stability): same shape ⇒ EXPECT 403 again" \
  403 -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" \
  -H "$HOP" -H "X-Forwarded-Host: evil.example" -H "Origin: https://evil.example" --data "code=$CODE"
pace 14
probe "$GF" "A3 EXPLOIT re-kill #3 (stability): same shape ⇒ EXPECT 403 again" \
  403 -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" \
  -H "$HOP" -H "X-Forwarded-Host: evil.example" -H "Origin: https://evil.example" --data "code=$CODE"
pace 14
rowpeek "after-A3-rekill-cluster"

probe "$GF" "A4 CONTROL (legit proxied shape still works): XFH views.example + Origin https://views.example ⇒ EXPECT 303 + grant cookie" \
  303 -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" \
  -H "$HOP" -H "X-Forwarded-Host: views.example" -H "Origin: https://views.example" --data "code=$CODE"
pace 14
probe "$GF" "A5 CONTROL (no XFH, evil Origin): Origin https://evil.example ⇒ EXPECT 403" \
  403 -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" \
  -H "$HOP" -H "Origin: https://evil.example" --data "code=$CODE"
pace 14
rowpeek "after-A-cluster"

echo "== GROUP A complete: $(grep -c '^####' "$GF") probes"
