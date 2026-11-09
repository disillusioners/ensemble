#!/usr/bin/env bash
# GROUP C part 1 — GENERALIZATION HUNT (C1–C6): all EXPECT 403-no-strike.
# Correct code everywhere: a 403 at CSRF gate #4 never touches strikes;
# an unexpected pass would surface as 303 + Set-Cookie (DEFECT).
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/rp_lib.sh"
load_env
CODE="$(cat "$FENCE/passcode-reprobe")"
GF="$EV/C1-C6-hunt.txt"
: > "$GF"
echo "# GROUP C1-C6 — GENERALIZATION HUNT — $(date -u +%FT%TZ) — view=$PUB_R (correct code, XFP https, browser Accept)" >> "$GF"
V="$BASE_URL$PUB_R"
BACC="Accept: $BROWSER_ACCEPT"

probe "$GF" "C1 old-pin-shape on foreign host: Host: evil.example:8443 + Origin: https://evil.example:8443 → EXPECT 403" \
  -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" \
  -H "Host: evil.example:8443" -H "Origin: https://evil.example:8443" --data "code=$CODE"
sleep 14
probe "$GF" "C2a www-elision (origin www vs apex host): Host: evil.example + Origin: https://www.evil.example → EXPECT 403 (www≡apex only under VOUCHED anchors)" \
  -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" \
  -H "Host: evil.example" -H "Origin: https://www.evil.example" --data "code=$CODE"
sleep 14
probe "$GF" "C2b www-elision (apex origin vs www host): Host: www.evil.example + Origin: https://evil.example → EXPECT 403" \
  -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" \
  -H "Host: www.evil.example" -H "Origin: https://evil.example" --data "code=$CODE"
sleep 14
rowpeek "after-C2"
probe "$GF" "C3a subdomain self-match: Host: sub.evil.example + Origin: https://sub.evil.example → EXPECT 403" \
  -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" \
  -H "Host: sub.evil.example" -H "Origin: https://sub.evil.example" --data "code=$CODE"
sleep 14
probe "$GF" "C3b sibling self-match: Host: sub.evil.example + Origin: https://other.evil.example → EXPECT 403" \
  -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" \
  -H "Host: sub.evil.example" -H "Origin: https://other.evil.example" --data "code=$CODE"
sleep 14
probe "$GF" "C4a host-case game (semantically fine, still unvouched): Host: EVIL.EXAMPLE + Origin: https://evil.example → EXPECT 403 (vouch check lowercases; name not vouched)" \
  -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" \
  -H "Host: EVIL.EXAMPLE" -H "Origin: https://evil.example" --data "code=$CODE"
sleep 14
rowpeek "after-C4a"
probe "$GF" "C4b crafted origin-case: Host: evil.example + Origin: https://EVIL.EXAMPLE → EXPECT 403" \
  -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" \
  -H "Host: evil.example" -H "Origin: https://EVIL.EXAMPLE" --data "code=$CODE"
sleep 14
probe "$GF" "C5a trailing dot both sides: Host: evil.example. + Origin: https://evil.example. → EXPECT 403" \
  -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" \
  -H "Host: evil.example." -H "Origin: https://evil.example." --data "code=$CODE"
sleep 14
rowpeek "after-C5"
probe "$GF" "C5b trailing dot cross-form: Host: evil.example + Origin: https://evil.example. → EXPECT 403" \
  -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" \
  -H "Host: evil.example" -H "Origin: https://evil.example." --data "code=$CODE"
sleep 14
probe "$GF" "C6a loopback lookalike (pinned): Host: 127.0.0.1.evil.example + Origin: https://127.0.0.1.evil.example → EXPECT 403 (peer IS loopback; exact-name gate must still reject)" \
  -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" \
  -H "Host: 127.0.0.1.evil.example" -H "Origin: https://127.0.0.1.evil.example" --data "code=$CODE"
sleep 14
rowpeek "after-C6a"
probe "$GF" "C6b loopback lookalike: Host: localhost.evil.example + Origin: https://localhost.evil.example → EXPECT 403" \
  -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" \
  -H "Host: localhost.evil.example" -H "Origin: https://localhost.evil.example" --data "code=$CODE"
sleep 14
probe "$GF" "C6c octal loopback: Host: 0177.0.0.1 + Origin: https://0177.0.0.1 → EXPECT 403 (not an exact loopback SPELLING)" \
  -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" \
  -H "Host: 0177.0.0.1" -H "Origin: https://0177.0.0.1" --data "code=$CODE"
sleep 14
rowpeek "after-C6c"
probe "$GF" "C6d octal cross-form: Host: 0177.0.0.1 + Origin: https://127.0.0.1 → EXPECT 403 (no 0177 anchor; 127.0.0.1 origin matches nothing)" \
  -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" \
  -H "Host: 0177.0.0.1" -H "Origin: https://127.0.0.1" --data "code=$CODE"
rowpeek "after-C6d"
echo "== GROUP C1-C6 complete: $(grep -c '^####' "$GF") probes"
