#!/usr/bin/env bash
# rk_E.sh — GROUP E: the last untested seams of the ORIGIN parser
#            (BOOT 2 — auth ON, audience https://op.example).
#
# These are NON-BROWSER-reachable Origin spellings (a browser serializes an
# Origin as scheme://host[:port] — never scheme-less, never with userinfo,
# never with a path). The probes document whether the gate's origin parser
# is lenient about them; the security question is whether the leniency is
# REACHABLE from a browser (it is not — these are not browser-reachable
# spellings), not whether the parser is strict.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/rk_lib.sh"
load_env
CODE="$(cat "$FENCE/passcode-view")"
GF="$EV/E-origin-parser-edges.txt"
: > "$GF"
echo "# GROUP E — Origin-parser leniency edges — $(date -u +%FT%TZ)" >> "$GF"
V="$BASE_URL$PUB_R"
BACC="Accept: $BROWSER_ACCEPT"

probe "$GF" "E1 Origin '//op.example' (scheme-less) ⇒ RECORD (the parser's hostname wins ⇒ expect 303)" \
  RECORD -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" -H "Host: op.example" \
  -H "Origin: //op.example" --data "code=$CODE"
sleep 13
probe "$GF" "E2 Origin 'https://evil.example@op.example' (userinfo) ⇒ RECORD (hostname wins ⇒ expect 303)" \
  RECORD -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" -H "Host: op.example" \
  -H "Origin: https://evil.example@op.example" --data "code=$CODE"
sleep 13
probe "$GF" "E3 Origin 'https://op.example/some/path' (path on an Origin) ⇒ RECORD (expect 303)" \
  RECORD -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" -H "Host: op.example" \
  -H "Origin: https://op.example/some/path" --data "code=$CODE"
sleep 13
probe "$GF" "E4 Origin 'https://evil.example@op.example' with a forged XFH evil ⇒ RECORD (expect 303 — audience anchor, no declared front ⇒ no veto)" \
  RECORD -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" -H "Host: op.example" \
  -H "X-Forwarded-Host: evil.example" -H "Origin: https://evil.example@op.example" --data "code=$CODE"
sleep 13

echo "== GROUP E complete: $(grep -c '^####' "$GF") probes"
