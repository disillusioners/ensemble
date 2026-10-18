#!/usr/bin/env bash
# PHASE B2-B5 — locked posture, grant survival, refresh/generation bump, re-lock
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/brute-lib.sh"
fence_load
EV="$BRUTE_HERE"
PASSCODE="$(cat "$FENCE/passcode.txt")"
ORIGIN="Origin: http://127.0.0.1:${PORT}"
XFP="X-Forwarded-Proto: https"
G1B="$(grep -oE 'view_grant=[^;]+' "$EV/a3-headers.txt" | head -1)"

echo "== B2: locked row — WRONG code and CORRECT code BOTH render the locked page"
for tag in wrong correct; do
  CODE="wrongZ1"; [ "$tag" = correct ] && CODE="$PASSCODE"
  S="$(curl -sS -o "$EV/b2-$tag-body.html" -D "$EV/b2-$tag-headers.txt" --max-time 10 -w '%{http_code}' \
    -X POST --data-urlencode "code=$CODE" -H "$ORIGIN" -H "$XFP" "$BASE_URL$PUB")"
  echo "B2 $tag-code status=$S" | tee "$EV/b2-verdict.txt"
done
DISClos=""
grep -qiE "attempt|strike|remaining|tr(y|ies)" "$EV/b2-correct-body.html" && DISClos="$DISClos attempt-wording"
grep -qE "[0-9]" "$EV/b2-correct-body.html" && DISClos="$DISClos digit-present"
if grep -q "This view is locked" "$EV/b2-wrong-body.html" && grep -q "This view is locked" "$EV/b2-correct-body.html"; then
  echo "B2: PASS both render locked page (no compare on locked row)" >> "$EV/b2-verdict.txt"
else
  echo "B2: FAIL locked page missing" >> "$EV/b2-verdict.txt"
fi
if [ -n "$DISClos" ]; then
  echo "B2 disclosure scan FLAGS:$DISClos" >> "$EV/b2-verdict.txt"
  grep -inE "attempt|strike|remaining|tr(y|ies)|[0-9]" "$EV/b2-correct-body.html" >> "$EV/b2-disclosure-hits.txt" || true
else
  echo "B2 disclosure scan: clean" >> "$EV/b2-verdict.txt"
fi
cat "$EV/b2-verdict.txt"

echo ""
echo "== B3: PRE-LOCK grant cookie G1B (from A3) still renders content"
curl -sS -o "$EV/b3-body.html" -D "$EV/b3-headers.txt" --max-time 10 \
  -H "Cookie: $G1B" -H "$XFP" "$BASE_URL$PUB"
grep -q "SECRET-CONTENT-marker-target-md" "$EV/b3-body.html" \
  && echo "B3: PASS pre-lock grant renders view content through lockout" | tee "$EV/b3-verdict.txt" \
  || echo "B3: FAIL grant blocked after lock" | tee "$EV/b3-verdict.txt"

echo ""
echo "== B4: refresh (service seam — view_link(refresh=True) equivalent) -> generation bump"
REFRESH_OUT="$(pgsvc refresh "$VIEW_ROOT_NAME" target.md brute-drill)" || { echo "REFRESH FAILED"; exit 96; }
echo "$REFRESH_OUT" | tee "$EV/b4-refresh.txt"
NEWPASS="$(echo "$REFRESH_OUT" | grep -oE 'passcode=[A-Za-z0-9]{6}' | cut -d= -f2)"
echo "$NEWPASS" > "$FENCE/passcode2.txt"
pgsvc rowpeek "$VIEW_ROOT_NAME" target.md | tee "$EV/b4-rowpeek-after-refresh.txt"

echo "-- B4a: OLD grant G1B now DEAD (generation mismatch -> gate/interstitial, not content)"
curl -sS -o "$EV/b4a-old-grant-body.html" -D "$EV/b4a-old-grant-headers.txt" --max-time 10 \
  -H "Cookie: $G1B" -H "$XFP" "$BASE_URL$PUB"
if grep -q "SECRET-CONTENT-marker-target-md" "$EV/b4a-old-grant-body.html"; then
  echo "B4a: FAIL old grant still renders" | tee "$EV/b4a-verdict.txt"
elif grep -q "Enter passcode" "$EV/b4a-old-grant-body.html"; then
  echo "B4a: PASS old grant dead -> interstitial gate (not content)" | tee "$EV/b4a-verdict.txt"
else
  echo "B4a: UNEXPECTED body" | tee "$EV/b4a-verdict.txt"; head -c 300 "$EV/b4a-old-grant-body.html"
fi

echo "-- B4b: OLD passcode REJECTED (failure page + strike 1)"
S="$(curl -sS -o "$EV/b4b-old-pass-body.html" -D "$EV/b4b-old-pass-headers.txt" --max-time 10 -w '%{http_code}' \
  -X POST --data-urlencode "code=$PASSCODE" -H "$ORIGIN" -H "$XFP" "$BASE_URL$PUB")"
echo "B4b old-passcode status=$S body=$(grep -o 'not recognised\|locked' "$EV/b4b-old-pass-body.html" | head -1)" | tee "$EV/b4b-verdict.txt"

echo "-- B4c: NEW passcode works -> 303 + new grant G2"
S="$(curl -sS -o "$EV/b4c-new-pass-body.html" -D "$EV/b4c-new-pass-headers.txt" --max-time 10 -w '%{http_code}' \
  -X POST --data-urlencode "code=$NEWPASS" -H "$ORIGIN" -H "$XFP" "$BASE_URL$PUB")"
G2="$(grep -oE 'view_grant=[^;]+' "$EV/b4c-new-pass-headers.txt" | head -1)"
echo "B4c new-passcode status=$S grant_captured=$([ -n "$G2" ] && echo yes || echo NO)" | tee "$EV/b4c-verdict.txt"
curl -sS -o "$EV/b4c-get-body.html" -D "$EV/b4c-get-headers.txt" --max-time 10 \
  -H "Cookie: $G2" -H "$XFP" "$BASE_URL$PUB"
grep -q "SECRET-CONTENT-marker-target-md" "$EV/b4c-get-body.html" \
  && echo "B4c: PASS new grant renders" >> "$EV/b4c-verdict.txt" || echo "B4c: FAIL new grant does not render" >> "$EV/b4c-verdict.txt"
cat "$EV/b4c-verdict.txt"

echo ""
echo "== B5: mint PRE-LOCK grant G3, then re-drive 10 fresh wrongs -> locked again"
S="$(curl -sS -o "$EV/b5-prelock-grant-body.html" -D "$EV/b5-prelock-grant-headers.txt" --max-time 10 -w '%{http_code}' \
  -X POST --data-urlencode "code=$NEWPASS" -H "$ORIGIN" -H "$XFP" "$BASE_URL$PUB")"
G3="$(grep -oE 'view_grant=[^;]+' "$EV/b5-prelock-grant-headers.txt" | head -1)"
echo "G3 (pre-lock, pre-restart) captured=$([ -n "$G3" ] && echo yes || echo NO) status=$S" | tee "$EV/b5-prelock-grant.txt"
echo "$G3" > "$FENCE/grant3.txt"
pgsvc rowpeek "$VIEW_ROOT_NAME" target.md | tee "$EV/b5-rowpeek-before-relock.txt"
: > "$EV/b5-seq.txt"
for i in $(seq 1 11); do
  S="$(curl -sS -o "$EV/b5-r$i-body.html" -D "$EV/b5-r$i-headers.txt" --max-time 10 -w '%{http_code}' \
    -X POST --data-urlencode "code=wrongR$i" -H "$ORIGIN" -H "$XFP" "$BASE_URL$PUB")"
  if grep -q "This view is locked" "$EV/b5-r$i-body.html"; then TAG="LOCKED-PAGE"; else TAG="failure-page"; fi
  echo "rewrong#$i: $S ($TAG)" >> "$EV/b5-seq.txt"
done
echo "B5 re-lock sequence:"; cat "$EV/b5-seq.txt" | tee "$EV/b5-seq-final.txt"
pgsvc rowpeek "$VIEW_ROOT_NAME" target.md | tee "$EV/b5-rowpeek-after-relock.txt"
