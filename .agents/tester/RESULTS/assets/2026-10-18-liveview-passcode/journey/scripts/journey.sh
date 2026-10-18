#!/usr/bin/env bash
# E2E journey J2-J10 stage — curl lane (original-scenario customer journey).
# Transcripts land in the evidence dir; one RESULT line per sub-item.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EV="$(cd "$HERE/.." && pwd)"
WT="$(cd "$HERE/../../../../../../.." && pwd)"
cd "$WT"

[ -f "$EV/.drill-tmp" ] || { echo "no .drill-tmp — boot stage missing"; exit 91; }
DRILL_TMP="$(cat "$EV/.drill-tmp")"
case "$DRILL_TMP" in /tmp/lvjourney.*) ;; *) echo "FENCE FAIL: $DRILL_TMP"; exit 99;; esac
# shellcheck disable=SC1090
source "$DRILL_TMP/state.env"

BASE="http://127.0.0.1:${PORT}"
PUB="$BASE/views/public"
CODE_HELLO="$(cat "$DRILL_TMP/passcode-hello")"
CODE_DIAG="$(cat "$DRILL_TMP/passcode-diagram")"
CODE_IMG="$(cat "$DRILL_TMP/passcode-image")"
IMAGE_ID="$(cat "$DRILL_TMP/image-id")"
IMAGE_SHA="$(cat "$DRILL_TMP/image-sha")"

PASS=0; FAIL=0
ok()   { echo "J$1 RESULT: PASS — $2"; PASS=$((PASS+1)); }
bad()  { echo "J$1 RESULT: FAIL — $2"; FAIL=$((FAIL+1)); }
snap() { # snap <label> <curl args...> → writes $EV/$label.headers / $label.body
  local label="$1"; shift
  timeout 15 curl -sS -D "$EV/$label.headers" -o "$EV/$label.body" --max-time 12 "$@" || true
}
status() { head -1 "$EV/$1.headers" | awk '{print $2}'; }

# ══ J2 — GET minted view unauthenticated → 200 zero-JS interstitial ══════
snap j2 "$PUB/journeyroot1/hello.md"
st="$(status j2)"; body="$EV/j2.body"
if [ "$st" = "200" ] \
   && ! grep -qi "<script" "$body" \
   && grep -q 'name="viewport"' "$body" \
   && grep -q 'action="/views/public/journeyroot1/hello.md"' "$body" \
   && grep -q 'name="code"' "$body" \
   && ! grep -q "$CODE_HELLO" "$body" \
   && ! grep -qiE "attempt|remaining|tries" "$body"; then
  J2A=1; else J2A=0; fi
csp="$(grep -i '^content-security-policy:' "$EV/j2.headers" | head -1 | sed 's/^[Cc]ontent-[Ss]ecurity-[Pp]olicy:[ ]*//' | tr -d '\r')"
if printf '%s' "$csp" | grep -qE "^default-src 'none'; script-src 'none'; style-src 'nonce-[0-9a-f]{32}'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'$" \
   && grep -qi '^x-content-type-options: nosniff' "$EV/j2.headers" \
   && grep -qi '^cache-control: no-store' "$EV/j2.headers" \
   && grep -qi '^referrer-policy: no-referrer' "$EV/j2.headers"; then
  J2B=1; else J2B=0; fi
[ "$J2A" = 1 ] && [ "$J2B" = 1 ] && ok 2 "200 interstitial: zero-JS, viewport, form->verify(path embedded), no passcode/attempt leak; CSP byte-equal to unit-pinned template + nosniff/no-store/no-referrer" \
  || bad 2 "gate surface drift (content asserts=$J2A header asserts=$J2B) — see j2.headers/j2.body"

# ══ J3 — unminted sibling → uniform daemon 404 (no existence leak) ═══════
snap j3 "$PUB/journeyroot1/nonexistent.md"
j3body="$(cat "$EV/j3.body")"
if [ "$(status j3)" = "404" ] && [ "$j3body" = '{"error":"view not found"}' ]; then
  ok 3 "uniform 404 from daemon: body exactly {\"error\":\"view not found\"} (no login redirect, no leak)"
else
  bad 3 "expected uniform 404, got $(status j3) body=$j3body"
fi

# ══ J4 — WRONG code POST (edge-emulated https) → failure page, NO grant ══
snap j4 -X POST --data-urlencode "code=zzzz9z" \
  -H "Origin: $BASE" -H "X-Forwarded-Proto: https" "$PUB/journeyroot1/hello.md"
if [ "$(status j4)" = "200" ] \
   && ! grep -qi '^set-cookie:' "$EV/j4.headers" \
   && grep -q "That code was not recognised." "$EV/j4.body" \
   && ! grep -qiE "attempt|remaining" "$EV/j4.body" \
   && ! grep -qi "<script" "$EV/j4.body"; then
  ok 4 "wrong code: 200 failure re-render (unit-pinned contract), zero Set-Cookie, generic error, no attempt-count leak"
else
  bad 4 "unexpected wrong-code contract: $(status j4) — see j4.headers/j4.body"
fi

# ══ J5 — CORRECT code POST → 303 PRG + fully-attributed grant cookie ═════
snap j5 -X POST --data-urlencode "code=$CODE_HELLO" \
  -H "Origin: $BASE" -H "X-Forwarded-Proto: https" "$PUB/journeyroot1/hello.md"
j5cookie="$(grep -i '^set-cookie:' "$EV/j5.headers" | head -1 | sed 's/^[Ss]et-[Cc]ookie:[ ]*//' | tr -d '\r')"
j5loc="$(grep -i '^location:' "$EV/j5.headers" | head -1 | sed 's/^[Ll]ocation:[ ]*//' | tr -d '\r')"
if [ "$(status j5)" = "303" ] && [ "$j5loc" = "/views/public/journeyroot1/hello.md" ] \
   && printf '%s' "$j5cookie" | grep -qE "^view_grant=[A-Za-z0-9_-]+; Path=/views/public/journeyroot1/hello\.md; HttpOnly; Secure; SameSite=Lax; Max-Age=86400$"; then
  ok 5 "303 PRG + Set-Cookie grant: every attribute present, path-scoped to the exact view path, Max-Age=86400 on the wire"
  COOKIE_HELLO="view_grant=$(printf '%s' "$j5cookie" | sed 's/^view_grant=//;s/;.*//')"
else
  bad 5 "grant contract drift: $(status j5) loc=$j5loc cookie=$j5cookie"
  COOKIE_HELLO=""
fi

# ══ J6 — GET with grant cookie → 200 VIEW content (markdown wrapper) ═════
if [ -n "$COOKIE_HELLO" ]; then
  snap j6 -H "Cookie: $COOKIE_HELLO" -H "X-Forwarded-Proto: https" "$PUB/journeyroot1/hello.md"
  if [ "$(status j6)" = "200" ] \
     && grep -q "<title>hello.md - live view</title>" "$EV/j6.body" \
     && grep -q "Journey Hello" "$EV/j6.body" \
     && ! grep -q "Enter passcode" "$EV/j6.body"; then
    ok 6 "200 view content: markdown wrapper (title + fixture markers), NOT the interstitial"
  else
    bad 6 "view content drift: $(status j6) — see j6.body"
  fi
else bad 6 "skipped — no grant cookie from J5"; fi

# ══ J7 — cookie PERSISTENCE + PATH-SCOPING + mermaid view flow ═══════════
J7DETAIL=""
if [ -n "$COOKIE_HELLO" ]; then
  snap j7a -H "Cookie: $COOKIE_HELLO" -H "X-Forwarded-Proto: https" "$PUB/journeyroot1/hello.md"
  [ "$(status j7a)" = "200" ] && grep -q "Journey Hello" "$EV/j7a.body" \
    && J7DETAIL="$J7DETAIL [7a repeat-GET 200 with same cookie: OK]" \
    || J7DETAIL="$J7DETAIL [7a FAIL: $(status j7a)]"
  # sibling with the hello.md cookie MUST re-gate (path-scoped cookie —
  # a browser would not even send it; curl mirrors that by not sending)
  snap j7b "$PUB/journeyroot1/diagram.mmd"
  if [ "$(status j7b)" = "200" ] && grep -q "Enter passcode" "$EV/j7b.body" && ! grep -qi "<script" "$EV/j7b.body"; then
    J7DETAIL="$J7DETAIL [7b sibling re-gates: OK]"
  else J7DETAIL="$J7DETAIL [7b FAIL: $(status j7b)]"; fi
  # complete diagram's own flow
  snap j7c -X POST --data-urlencode "code=$CODE_DIAG" \
    -H "Origin: $BASE" -H "X-Forwarded-Proto: https" "$PUB/journeyroot1/diagram.mmd"
  j7cookie="$(grep -i '^set-cookie:' "$EV/j7c.headers" | head -1 | sed 's/^[Ss]et-[Cc]ookie:[ ]*//' | tr -d '\r')"
  if [ "$(status j7c)" = "303" ] && printf '%s' "$j7cookie" | grep -q "Path=/views/public/journeyroot1/diagram.mmd"; then
    COOKIE_DIAG="view_grant=$(printf '%s' "$j7cookie" | sed 's/^view_grant=//;s/;.*//')"
    J7DETAIL="$J7DETAIL [7c diagram 303 + own-scope cookie: OK]"
  else
    COOKIE_DIAG=""; J7DETAIL="$J7DETAIL [7c FAIL: $(status j7c)]"
  fi
  if [ -n "$COOKIE_DIAG" ]; then
    snap j7d -H "Cookie: $COOKIE_DIAG" -H "X-Forwarded-Proto: https" "$PUB/journeyroot1/diagram.mmd"
    if [ "$(status j7d)" = "200" ] \
       && grep -q '<title>diagram.mmd - live view</title>' "$EV/j7d.body" \
       && grep -q 'class="mermaid"' "$EV/j7d.body" \
       && grep -q 'mermaid.min.js' "$EV/j7d.body"; then
      J7DETAIL="$J7DETAIL [7d mermaid wrapper served: OK]"
    else J7DETAIL="$J7DETAIL [7d FAIL: $(status j7d)]"; fi
    snap j7e -H "Cookie: $COOKIE_DIAG" -H "X-Forwarded-Proto: https" "$PUB/journeyroot1/diagram.mmd?raw=1"
    if [ "$(status j7e)" = "200" ] && cmp -s "$EV/j7e.body" "$VIEWS_ROOT/diagram.mmd"; then
      J7DETAIL="$J7DETAIL [7e raw=1 byte-identical source: OK]"
    else J7DETAIL="$J7DETAIL [7e FAIL: $(status j7e)]"; fi
  fi
  echo "$J7DETAIL" > "$EV/.j7detail"
  case "$J7DETAIL" in *FAIL*) bad 7 "path-scoping/mermaid flow —$J7DETAIL";; *) ok 7 "persistence + path-scoping + mermaid flow —$J7DETAIL";; esac
else bad 7 "skipped — no grant cookie"; fi

# ══ J8 — tmp-images view: passcode flow → byte-identical sidecar-MIME ════
snap j8a "$PUB/tmp-images/$IMAGE_ID"
if [ "$(status j8a)" = "200" ] && grep -q "Enter passcode" "$EV/j8a.body"; then
  snap j8b -X POST --data-urlencode "code=$CODE_IMG" \
    -H "Origin: $BASE" -H "X-Forwarded-Proto: https" "$PUB/tmp-images/$IMAGE_ID"
  j8cookie="$(grep -i '^set-cookie:' "$EV/j8b.headers" | head -1 | sed 's/^[Ss]et-[Cc]ookie:[ ]*//' | tr -d '\r')"
  if [ "$(status j8b)" = "303" ]; then
    COOKIE_IMG="view_grant=$(printf '%s' "$j8cookie" | sed 's/^view_grant=//;s/;.*//')"
    snap j8c -H "Cookie: $COOKIE_IMG" -H "X-Forwarded-Proto: https" "$PUB/tmp-images/$IMAGE_ID"
    j8sha="$(sha256sum "$EV/j8c.body" | awk '{print $1}')"
    j8ct="$(grep -i '^content-type:' "$EV/j8c.headers" | head -1 | tr -d '\r' | awk '{print $2}')"
    if [ "$(status j8c)" = "200" ] && [ "$j8sha" = "$IMAGE_SHA" ] && [ "$j8ct" = "image/png" ]; then
      ok 8 "tmp-images public lane: interstitial→303→200 bytes sha256-identical to seeded blob, content-type from sidecar (image/png)"
    else
      bad 8 "served drift: $(status j8c) sha=$j8sha want=$IMAGE_SHA ct=$j8ct"
    fi
  else bad 8 "tmp-images verify not 303: $(status j8b)"; fi
else
  bad 8 "tmp-images gate missing (got $(status j8a)) — see j8a.body"
fi

# ══ J9 — control: the rest of the world still gated (State-B shape) ══════
snap j9a "$BASE/api/instances"
snap j9b "$BASE/views/journeyroot1/hello.md"
j9a_loc="$(grep -i '^location:' "$EV/j9a.headers" | head -1 | tr -d '\r' | sed 's/^[Ll]ocation:[ ]*//')"
j9b_loc="$(grep -i '^location:' "$EV/j9b.headers" | head -1 | tr -d '\r' | sed 's/^[Ll]ocation:[ ]*//')"
if [ "$(status j9a)" = "302" ] && printf '%s' "$j9a_loc" | grep -q "^/api/auth/login" \
   && [ "$(status j9b)" = "302" ] && printf '%s' "$j9b_loc" | grep -q "^/api/auth/login?return_to=/views/journeyroot1/hello.md"; then
  ok 9 "controls gated: /api/instances → 302 $j9a_loc ; direct legacy lane → 302 $j9b_loc (public lane served J2-J8 unauthenticated throughout)"
else
  bad 9 "control drift: api=$(status j9a) loc=$j9a_loc direct=$(status j9b) loc=$j9b_loc"
fi

# ══ J10 — TTL: Max-Age=86400 observed on the wire (J5) + unit-proven TTL ══
if printf '%s' "$j5cookie" | grep -q "Max-Age=86400"; then
  ok 10 "grant TTL: Max-Age=86400 observed on the wire (J5 transcript); ~24h expiry logic unit-proven in tests/unit/services/test_view_passcode_service.py:1596 (test_issue_sets_exp_to_now_plus_ttl + expiry-rejection pins) — cited, not waited"
else
  bad 10 "Max-Age=86400 not found in J5 Set-Cookie"
fi

echo "JOURNEY SUMMARY: PASS=$PASS FAIL=$FAIL"
exit $(( FAIL > 0 ? 1 : 0 ))
