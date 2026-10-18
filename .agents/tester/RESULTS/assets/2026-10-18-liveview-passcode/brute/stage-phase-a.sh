#!/usr/bin/env bash
# PHASE A — rate limit BEFORE lockout, DEFAULT limits (5 burst / 5 per min)
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/brute-lib.sh"   # sourced — dispatcher skipped via BASH_SOURCE guard
fence_load
EV="$BRUTE_HERE"
PASSCODE="$(cat "$FENCE/passcode.txt")"
ORIGIN="Origin: http://127.0.0.1:${PORT}"
XFP="X-Forwarded-Proto: https"

echo "== A1: valid grant FIRST (correct code -> 303 + cookie), stash as G1"
curl -sS -o "$EV/a1-grant-body.html" -D "$EV/a1-grant-headers.txt" --max-time 10 \
  -X POST --data-urlencode "code=$PASSCODE" -H "$ORIGIN" -H "$XFP" "$BASE_URL$PUB"
A1_STATUS="$(head -1 "$EV/a1-grant-headers.txt" | awk '{print $2}')"
G1="$(grep -oE 'view_grant=[^;]+' "$EV/a1-grant-headers.txt" | head -1)"
echo "A1 status=$A1_STATUS cookie_len=${#G1}"
echo "A1: status=$A1_STATUS cookie_captured=$([ -n "$G1" ] && echo yes || echo NO)" > "$EV/a1-verdict.txt"
[ "$A1_STATUS" = "303" ] && [ -n "$G1" ] && echo "A1: PASS" >> "$EV/a1-verdict.txt" || echo "A1: FAIL" >> "$EV/a1-verdict.txt"
# prove G1 renders content
curl -sS -o "$EV/a1-get-with-grant-body.html" -D "$EV/a1-get-with-grant-headers.txt" --max-time 10 \
  -H "Cookie: $G1" -H "$XFP" "$BASE_URL$PUB"
grep -q "SECRET-CONTENT-marker-target-md" "$EV/a1-get-with-grant-body.html" \
  && echo "A1-content: grant renders view content (PASS)" >> "$EV/a1-verdict.txt" \
  || echo "A1-content: FAIL grant did not render" >> "$EV/a1-verdict.txt"
cat "$EV/a1-verdict.txt"

echo ""
echo "== A2: 12 fast WRONG-code posts from 127.0.0.1 (no XFF) — record exact sequence"
: > "$EV/a2-seq.txt"
for i in $(seq 1 12); do
  S="$(curl -sS -o "$EV/a2-r$i-body.html" -D "$EV/a2-r$i-headers.txt" --max-time 10 -w '%{http_code}' \
    -X POST --data-urlencode "code=wrong$i" -H "$ORIGIN" -H "$XFP" "$BASE_URL$PUB")"
  echo "req$i: $S" >> "$EV/a2-seq.txt"
done
SEQ="$(grep -oE '[0-9]{3}$' "$EV/a2-seq.txt" | tr '\n' ' ')"
echo "A2 sequence: $SEQ" | tee "$EV/a2-seq-summary.txt"
cat "$EV/a2-seq.txt"
pgsvc rowpeek "$VIEW_ROOT_NAME" target.md | tee "$EV/a2-rowpeek.txt"

echo ""
echo "== A3: wait 13s (1 token refill @ 5/min), then CORRECT code -> expect 303 (429s consumed no strikes: 5+7=12 would be locked)"
sleep 13
curl -sS -o "$EV/a3-body.html" -D "$EV/a3-headers.txt" --max-time 10 -w 'a3_status=%{http_code}\n' \
  -X POST --data-urlencode "code=$PASSCODE" -H "$ORIGIN" -H "$XFP" "$BASE_URL$PUB" | tee "$EV/a3-verdict.txt"
G1B="$(grep -oE 'view_grant=[^;]+' "$EV/a3-headers.txt" | head -1)"
curl -sS -o "$EV/a3-get-body.html" -D "$EV/a3-get-headers.txt" --max-time 10 \
  -H "Cookie: $G1B" -H "$XFP" "$BASE_URL$PUB"
grep -q "SECRET-CONTENT-marker-target-md" "$EV/a3-get-body.html" \
  && echo "A3: PASS correct-code verify succeeded post-429 + new grant renders" >> "$EV/a3-verdict.txt" \
  || echo "A3: FAIL" >> "$EV/a3-verdict.txt"
cat "$EV/a3-verdict.txt"
pgsvc rowpeek "$VIEW_ROOT_NAME" target.md | tee "$EV/a3-rowpeek.txt"

echo ""
echo "== A4: livez health + 5xx scan on the verify lane"
curl -sS -o /dev/null -D "$EV/a4-livez-headers.txt" --max-time 5 "$BASE_URL/views/livez"
head -1 "$EV/a4-livez-headers.txt" | tee "$EV/a4-livez.txt"
grep -lE "^HTTP/1.1 5[0-9][0-9]" "$EV"/a1-*.txt "$EV"/a2-r*-headers.txt "$EV"/a3-*.txt 2>/dev/null > "$EV/a4-5xx-scan.txt" || true
if [ -s "$EV/a4-5xx-scan.txt" ]; then echo "A4: FAIL — 5xx found:"; cat "$EV/a4-5xx-scan.txt";
else echo "A4: PASS — zero 5xx across all phase-A verify-lane transcripts" | tee "$EV/a4-verdict.txt"; fi

echo ""
echo "== A5: per-IP keying on dedicated view target5.md — XFF A burst throttles, XFF B processes"
ORIG_PUB="$PUB"; PUB="$PUB5"   # A5 hits target5.md only
# A5a: 7 fast wrongs from XFF 203.0.113.10 (fresh bucket A: 5 process -> strikes, then 429s)
: > "$EV/a5-xff-a-seq.txt"
for i in $(seq 1 7); do
  S="$(curl -sS -o "$EV/a5a-r$i-body.html" -D "$EV/a5a-r$i-headers.txt" --max-time 10 -w '%{http_code}' \
    -X POST --data-urlencode "code=wrongA$i" -H "$ORIGIN" -H "$XFP" \
    -H "X-Forwarded-For: 203.0.113.10" "$BASE_URL$PUB5")"
  echo "xffA-req$i: $S" >> "$EV/a5-xff-a-seq.txt"
done
echo "A5a XFF-A sequence: $(grep -oE '[0-9]{3}$' "$EV/a5-xff-a-seq.txt" | tr '\n' ' ')" | tee "$EV/a5a-summary.txt"
# A5b: immediately 1 wrong from XFF 203.0.113.11 (fresh bucket B) -> must PROCESS (200 failure page, not 429)
SB="$(curl -sS -o "$EV/a5b-body.html" -D "$EV/a5b-headers.txt" --max-time 10 -w '%{http_code}' \
  -X POST --data-urlencode "code=wrongB1" -H "$ORIGIN" -H "$XFP" \
  -H "X-Forwarded-For: 203.0.113.11" "$BASE_URL$PUB5")"
echo "A5b XFF-B status: $SB" | tee "$EV/a5b-summary.txt"
grep -q "not recognised" "$EV/a5b-body.html" && echo "A5b body: failure page (processed)" >> "$EV/a5b-summary.txt"
grep -q "Too many attempts" "$EV/a5b-body.html" && echo "A5b body: THROTTLED (bucket B was NOT independent!)" >> "$EV/a5b-summary.txt"
pgsvc rowpeek "$VIEW_ROOT_NAME" target5.md | tee "$EV/a5-rowpeek.txt"
