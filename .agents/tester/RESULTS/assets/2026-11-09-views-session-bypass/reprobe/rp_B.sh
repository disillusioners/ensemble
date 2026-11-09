#!/usr/bin/env bash
# GROUP B — CELL (i) auth-ON live shape + G1 structural confirm.
# B1: Origin: null, no SFS → 303 + grant (the cell-(i) live fix).
# B2: Origin: null + Sec-Fetch-Site: cross-site → 403 HTML.
# B3: G1 STRUCTURAL CONFIRM — null-allow with an UNVOUCHED Host:
#     Host: evil.example + Origin: null (no SFS) → EXPECT 303 + grant.
#     With evil Host there is NO evil anchor in the trust universe, so a
#     grant here proves the null/absent branches return BEFORE the
#     anchor set is consulted (the G1-closure shape).
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/rp_lib.sh"
load_env
CODE="$(cat "$FENCE/passcode-reprobe")"
GF="$EV/B-cell-i-g1shape.txt"
: > "$GF"
echo "# GROUP B — CELL (i) + G1 SHAPE — $(date -u +%FT%TZ) — view=$PUB_R (correct code, XFP https, browser Accept)" >> "$GF"
V="$BASE_URL$PUB_R"
BACC="Accept: $BROWSER_ACCEPT"

probe "$GF" "B1 CELL(i): Origin: null, NO Sec-Fetch-Site → EXPECT 303 + grant" \
  -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" \
  -H "Origin: null" --data "code=$CODE"
sleep 14

probe "$GF" "B2 CELL(i) discriminator: Origin: null + Sec-Fetch-Site: cross-site → EXPECT 403 HTML, no cookie" \
  -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" \
  -H "Origin: null" -H "Sec-Fetch-Site: cross-site" --data "code=$CODE"
sleep 14
rowpeek "after-B1/B2"

probe "$GF" "B3 G1-SHAPE (null branch BEFORE anchors): Host: evil.example + Origin: null, no SFS → EXPECT 303 + grant (grant with NO evil anchor proves null-allow precedes anchor consult)" \
  -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" \
  -H "Host: evil.example" -H "Origin: null" --data "code=$CODE"
rowpeek "after-B3"
echo "== GROUP B complete: $(grep -c '^####' "$GF") probes"
