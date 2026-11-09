#!/usr/bin/env bash
# GROUP A — THE EXPLOIT must DIE (3x spaced, repeat-stability).
# Post-fix expectation: 403 text/html, NO Set-Cookie, failed_attempts
# UNCHANGED (reject at CSRF gate #4 — before compare; correct code sent,
# so any strike would itself be an anomaly).
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/rp_lib.sh"
load_env
CODE="$(cat "$FENCE/passcode-reprobe")"
GF="$EV/A-exploit-dies.txt"
: > "$GF"
echo "# GROUP A — G2.7 EXPLOIT RE-PROBE (fix 6398a29a3) — $(date -u +%FT%TZ) — view=$PUB_R (correct code, XFP https, browser Accept)" >> "$GF"
V="$BASE_URL$PUB_R"
BACC="Accept: $BROWSER_ACCEPT"

probe "$GF" "A1 EXPLOIT (must DIE): Host: evil.example + Origin: https://evil.example → EXPECT 403 text/html, NO set-cookie" \
  -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" \
  -H "Host: evil.example" -H "Origin: https://evil.example" --data "code=$CODE"
sleep 14
rowpeek "after-A1"

probe "$GF" "A2 EXPLOIT repeat (stability): Host: evil.example + Origin: https://evil.example → EXPECT 403 again" \
  -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" \
  -H "Host: evil.example" -H "Origin: https://evil.example" --data "code=$CODE"
sleep 14

probe "$GF" "A3 EXPLOIT repeat-3: Host: evil.example + Origin: https://evil.example → EXPECT 403 again" \
  -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" \
  -H "Host: evil.example" -H "Origin: https://evil.example" --data "code=$CODE"
rowpeek "after-A3"
echo "== GROUP A complete: $(grep -c '^####' "$GF") probes"
