#!/usr/bin/env bash
# PHASE B6-B7 — restart durability: TERM, reboot SAME key/data/DB/port+overrides, assert persistence
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/brute-lib.sh"
fence_load
EV="$BRUTE_HERE"
NEWPASS="$(cat "$FENCE/passcode2.txt")"
G3="$(cat "$FENCE/grant3.txt")"
ORIGIN="Origin: http://127.0.0.1:${PORT}"
XFP="X-Forwarded-Proto: https"

echo "== B6: rowpeek BEFORE restart"
pgsvc rowpeek "$VIEW_ROOT_NAME" target.md | tee "$EV/b6-rowpeek-before-restart.txt"

echo "== B6: TERM daemon (verified-bound-pid kill only)"
bash "$HERE/brute-lib.sh" stop

echo "== B6: reboot SAME fence (key/data/DB/port + same overrides)"
export ENVS_EXTRA="ENSEMBLE_LIVE_VIEWS_VERIFY_RATE_LIMIT_CAPACITY=100 ENSEMBLE_LIVE_VIEWS_VERIFY_RATE_LIMIT_REFILL_PER_MINUTE=600"
boot_daemon "B6" "$EV/boot-b6.log" || exit 97
unset ENVS_EXTRA
curl -sS -o /dev/null -D "$EV/b7-livez-after-reboot.txt" --max-time 5 "$BASE_URL/views/livez"
echo "B7 livez after reboot: $(head -1 "$EV/b7-livez-after-reboot.txt")" | tee "$EV/b7-livez-summary.txt"

echo "-- B6a: lock PERSISTED — wrong code renders LOCKED page (not a reset)"
S="$(curl -sS -o "$EV/b6a-wrong-body.html" -D "$EV/b6a-wrong-headers.txt" --max-time 10 -w '%{http_code}' \
  -X POST --data-urlencode "code=wrongPost1" -H "$ORIGIN" -H "$XFP" "$BASE_URL$PUB")"
grep -q "This view is locked" "$EV/b6a-wrong-body.html" \
  && echo "B6a: PASS lock persisted across restart ($S)" | tee "$EV/b6a-verdict.txt" \
  || echo "B6a: FAIL — lock did NOT persist ($S)" | tee "$EV/b6a-verdict.txt"

echo "-- B6b: correct code ALSO locked-page (no compare post-restart)"
S="$(curl -sS -o "$EV/b6b-correct-body.html" -D "$EV/b6b-correct-headers.txt" --max-time 10 -w '%{http_code}' \
  -X POST --data-urlencode "code=$NEWPASS" -H "$ORIGIN" -H "$XFP" "$BASE_URL$PUB")"
grep -q "This view is locked" "$EV/b6b-correct-body.html" \
  && echo "B6b: PASS correct code locked-page post-restart ($S)" | tee "$EV/b6b-verdict.txt" \
  || echo "B6b: FAIL ($S)" | tee "$EV/b6b-verdict.txt"

echo "-- B6c: PRE-RESTART grant G3 STILL renders (grant + strike state durable in PG)"
curl -sS -o "$EV/b6c-grant3-body.html" -D "$EV/b6c-grant3-headers.txt" --max-time 10 \
  -H "Cookie: $G3" -H "$XFP" "$BASE_URL$PUB"
grep -q "SECRET-CONTENT-marker-target-md" "$EV/b6c-grant3-body.html" \
  && echo "B6c: PASS pre-restart grant renders (grants + lock durable)" | tee "$EV/b6c-verdict.txt" \
  || echo "B6c: FAIL pre-restart grant blocked" | tee "$EV/b6c-verdict.txt"

echo "-- B6d: rowpeek AFTER restart (expect same failed_attempts/locked/generation as before)"
pgsvc rowpeek "$VIEW_ROOT_NAME" target.md | tee "$EV/b6-rowpeek-after-restart.txt"

echo "== B7: 5xx scan across ALL transcripts"
grep -lE "^HTTP/1.1 5[0-9][0-9]" "$EV"/*-headers.txt "$EV"/*.txt 2>/dev/null | grep -v "5xx-scan" > "$EV/b7-5xx-scan.txt" || true
if [ -s "$EV/b7-5xx-scan.txt" ]; then echo "B7: FAIL — 5xx found in:"; cat "$EV/b7-5xx-scan.txt";
else echo "B7: PASS — zero 5xx in any transcript across all boots" | tee "$EV/b7-verdict.txt"; fi
