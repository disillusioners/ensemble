#!/usr/bin/env bash
# G4 — STALE/INVALID SESSION → anonymous flow (fail-toward-anonymous).
# G5 — ANONYMOUS FLOW byte-identical semantics.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/lib.sh"
load_env
CODE_A="$(cat "$FENCE/passcode-a")"
SID_EXP="$(cat "$FENCE/sid-expired")"
GF4="$EV/G4.txt"; GF5="$EV/G5.txt"
: > "$GF4"; : > "$GF5"
echo "# G4 STALE/INVALID SESSION — $(date -u +%FT%TZ) — expired sid minted with expires_at=now-1h" >> "$GF4"
echo "# G5 ANONYMOUS FLOW — $(date -u +%FT%TZ)" >> "$GF5"

V="$BASE_URL$PUB_A"
BACC="Accept: $BROWSER_ACCEPT"

probe "$GF4" "G4a GET public viewA with EXPIRED ensemble_sid → expect 200 interstitial HTML (anonymous), never 403/JSON" \
  -H "$BACC" -H "Cookie: ensemble_sid=$SID_EXP" "$V"

probe "$GF4" "G4b GET public viewA with GARBAGE ensemble_sid → expect 200 interstitial HTML (anonymous)" \
  -H "$BACC" -H "Cookie: ensemble_sid=garbage-nonsense-value-12345" "$V"

probe "$GF4" "G4c POST verify with EXPIRED sid + correct code → expect anonymous chain (303+grant; sid contributes nothing)" \
  -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" -H "Cookie: ensemble_sid=$SID_EXP" --data "code=$CODE_A"

sleep 14

probe "$GF5" "G5a anonymous correct code + XFP https → expect 303 + Set-Cookie view_grant (capture raw attrs)" \
  -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" --data "code=$CODE_A"

GRANT_COOKIE="$(awk '/#### G5a/,/^$/' "$GF5" | grep -i '^   set-cookie' | head -1 | sed 's/^   set-cookie: //; s/; Max-Age=.*//')"
probe "$GF5" "G5b GET with the fresh grant cookie → expect 200 view HTML" \
  -H "$BACC" -H "Cookie: $GRANT_COOKIE" "$V"

probe "$GF5" "G5c WRONG code (one strike) → expect HTML form re-render, 200 text/html, never JSON" \
  -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" --data "code=WRONG1"

sleep 14

probe "$GF5" "G5d plain-HTTP leg (NO X-Forwarded-Proto) correct code → expect secure-context page ~200 HTML, NO grant" \
  -X POST "$V" -H "$BACC" --data "code=$CODE_A"

echo "== G4+G5 complete"
