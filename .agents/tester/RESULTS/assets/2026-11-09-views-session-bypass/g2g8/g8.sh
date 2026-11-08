#!/usr/bin/env bash
# G8 — LOCKOUT SAFETY on viewB (run AFTER G2-G5; viewA stays unlocked).
# 10 wrong strikes (paced ≥14s vs bucket) → lock → locked short-circuit →
# hammer to 429 → refill-window recovery probe (≈13s).
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/lib.sh"
load_env
CODE_B="$(cat "$FENCE/passcode-b")"
GF="$EV/G8.txt"
: > "$GF"
echo "# G8 LOCKOUT SAFETY — $(date -u +%FT%TZ) — viewB=$PUB_B (gen=1)" >> "$GF"

V="$BASE_URL$PUB_B"
BACC="Accept: $BROWSER_ACCEPT"

for i in $(seq 1 9); do
  probe "$GF" "G8 strike $i/10 (wrong code) → expect failure re-render 200 HTML" \
    -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" --data "code=WRONGG8"
  sleep 14
done
probe "$GF" "G8 strike 10/10 (wrong code) → expect LOCKED page 200 HTML (lock flips this strike)" \
  -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" --data "code=WRONGG8"
sleep 14

probe "$GF" "G8 post-lock attempt 11 → expect locked short-circuit 200 HTML (no compare, no increment)" \
  -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" --data "code=$CODE_B"

probe "$GF" "G8 hammer 1 → expect locked page 200 (tokens remain)" \
  -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" --data "code=WRONGG8"
probe "$GF" "G8 hammer 2" \
  -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" --data "code=WRONGG8"
probe "$GF" "G8 hammer 3" \
  -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" --data "code=WRONGG8"
probe "$GF" "G8 hammer 4 → expect 429 (bucket exhausted)" \
  -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" --data "code=WRONGG8"
probe "$GF" "G8 hammer 5 → expect 429" \
  -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" --data "code=WRONGG8"

echo "== G8 phase 1 complete (sleeping 13s for one refill token)"
sleep 13
probe "$GF" "G8 recovery after ≈13s refill → expect locked page 200 (NOT 429; one token returned)" \
  -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" --data "code=WRONGG8"

echo "== G8 complete"
