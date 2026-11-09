#!/usr/bin/env bash
# GROUP D — REGRESSION GUARD (fix must not break legit flows).
# D1: audience-vouched origin (operator.example) on Host default → 303+grant.
# D2: plain no-Origin/no-SFS correct code → 303+grant.
# D3: explicit scheme-default port on the vouched origin → 303+grant
#     (port-elision preserved on vouched anchors).
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/rp_lib.sh"
load_env
CODE="$(cat "$FENCE/passcode-reprobe")"
GF="$EV/D-regression-guard.txt"
: > "$GF"
echo "# GROUP D — REGRESSION GUARD — $(date -u +%FT%TZ) — view=$PUB_R (correct code, XFP https, browser Accept)" >> "$GF"
V="$BASE_URL$PUB_R"
BACC="Accept: $BROWSER_ACCEPT"

probe "$GF" "D1 audience leg: Origin: https://operator.example (Host default 127.0.0.1:8082) → EXPECT 303 + grant" \
  -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" \
  -H "Origin: https://operator.example" --data "code=$CODE"
sleep 14
rowpeek "after-D1"
probe "$GF" "D2 plain flow: NO Origin, NO Sec-Fetch-Site, correct code → EXPECT 303 + grant" \
  -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" --data "code=$CODE"
sleep 14
probe "$GF" "D3 audience leg port-elision: Origin: https://operator.example:443 → EXPECT 303 + grant (scheme-default explicit port vs portless anchor)" \
  -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" \
  -H "Origin: https://operator.example:443" --data "code=$CODE"
rowpeek "after-D3"
echo "== GROUP D complete: $(grep -c '^####' "$GF") probes"
