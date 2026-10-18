#!/usr/bin/env bash
# PHASE B1 — restart with DOCUMENTED rate-limit overrides, drive strikes 6..10 to lockout
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
bash "$HERE/brute-lib.sh" stop   # stop any prior boot; FENCE (key/data) is KEPT
source "$HERE/brute-lib.sh"
fence_load
EV="$BRUTE_HERE"
ORIGIN="Origin: http://127.0.0.1:${PORT}"
XFP="X-Forwarded-Proto: https"

export ENVS_EXTRA="ENSEMBLE_LIVE_VIEWS_VERIFY_RATE_LIMIT_CAPACITY=100 ENSEMBLE_LIVE_VIEWS_VERIFY_RATE_LIMIT_REFILL_PER_MINUTE=600"
boot_daemon "B1" "$EV/boot-b1.log" || exit 97
unset ENVS_EXTRA
curl -sS -o /dev/null -D "$EV/b1-livez.txt" --max-time 5 "$BASE_URL/views/livez"
echo "B1 livez: $(head -1 "$EV/b1-livez.txt")" | tee "$EV/b1-livez-summary.txt"

echo "== B1 rowpeek (expect failed_attempts=5 carried in PG from phase A)"
pgsvc rowpeek "$VIEW_ROOT_NAME" target.md | tee "$EV/b1-rowpeek-before.txt"

echo "== B1: wrong-code verifies until locked (overrides: bucket 100/600 — no 429s expected)"
: > "$EV/b1-seq.txt"
LOCKED_AT=""
for i in $(seq 6 16); do
  S="$(curl -sS -o "$EV/b1-r$i-body.html" -D "$EV/b1-r$i-headers.txt" --max-time 10 -w '%{http_code}' \
    -X POST --data-urlencode "code=wrongB$i" -H "$ORIGIN" -H "$XFP" "$BASE_URL$PUB")"
  if grep -q "This view is locked" "$EV/b1-r$i-body.html"; then TAG="LOCKED-PAGE"; LOCKED_AT="${LOCKED_AT:-$i}"; else TAG="failure-page"; fi
  echo "wrong#$i: $S ($TAG)" >> "$EV/b1-seq.txt"
  [ -n "$LOCKED_AT" ] && break
done
echo "B1 sequence:"; cat "$EV/b1-seq.txt" | tee "$EV/b1-seq-final.txt"
pgsvc rowpeek "$VIEW_ROOT_NAME" target.md | tee "$EV/b1-rowpeek-after.txt"
