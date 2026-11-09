#!/usr/bin/env bash
# G2 — ORIGIN MATRIX on viewA. POST verify, CORRECT code, XFP https,
# browser Accept. All probes pace ≥14s (bucket: capacity 5, refill ~1/12s).
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/lib.sh"
load_env
CODE_A="$(cat "$FENCE/passcode-a")"
GF="$EV/G2.txt"
: > "$GF"
echo "# G2 ORIGIN MATRIX — $(date -u +%FT%TZ) — viewA=$PUB_A (correct code, XFP https, browser Accept)" >> "$GF"

V="$BASE_URL$PUB_A"
BACC="Accept: $BROWSER_ACCEPT"

probe "$GF" "G2.1 Origin: null (no Sec-Fetch-Site) → expect 303+grant" \
  -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" \
  -H "Origin: null" --data "code=$CODE_A"
sleep 14

probe "$GF" "G2.2 Host: matrix.example + Origin: https://www.matrix.example (www origin vs apex host) → expect 303" \
  -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" \
  -H "Host: matrix.example" -H "Origin: https://www.matrix.example" --data "code=$CODE_A"
sleep 14

probe "$GF" "G2.3 Host: www.matrix.example + Origin: https://matrix.example (apex origin vs www host) → expect 303" \
  -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" \
  -H "Host: www.matrix.example" -H "Origin: https://matrix.example" --data "code=$CODE_A"
sleep 14

probe "$GF" "G2.4 Origin: null + Sec-Fetch-Site: cross-site → expect graceful REJECT (HTML; report status)" \
  -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" \
  -H "Origin: null" -H "Sec-Fetch-Site: cross-site" --data "code=$CODE_A"
sleep 14

probe "$GF" "G2.5 No Origin header at all → expect 303" \
  -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" --data "code=$CODE_A"
sleep 14

probe "$GF" "G2.6 Origin: https://evil.example (Host default 127.0.0.1) → expect REJECT (HTML)" \
  -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" \
  -H "Origin: https://evil.example" --data "code=$CODE_A"
sleep 14

probe "$GF" "G2.7 TAUTOLOGY: Host: evil.example + Origin: https://evil.example (attacker self-match) — task expects FAIL-CLOSED" \
  -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" \
  -H "Host: evil.example" -H "Origin: https://evil.example" --data "code=$CODE_A"
sleep 14

probe "$GF" "G2.8a XFH negative (NO declared proxy front): X-Forwarded-Host: evil.example + Origin: https://evil.example → expect REJECT" \
  -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" \
  -H "X-Forwarded-Host: evil.example" -H "Origin: https://evil.example" --data "code=$CODE_A"
sleep 14

probe "$GF" "G2.8b Forwarded negative (NO declared proxy front): Forwarded: host=evil.example + Origin: https://evil.example → expect REJECT" \
  -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" \
  -H 'Forwarded: host=evil.example' -H "Origin: https://evil.example" --data "code=$CODE_A"

echo "== G2 complete: $(grep -c '^####' "$GF") probes"
