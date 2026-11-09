#!/usr/bin/env bash
# rk_B3.sh — SWEEP GROUP 5 (port forms on the vouched anchor) + extra port edges.
#
# Contract anchors (repo pins):
#   test_portless_elision_preserved_under_declared_anchor — a portless anchor
#     elides with ANY origin port ⇒ Host: example.com + Origin:
#     https://example.com:8443 ⇒ 303 (the FIX-3 disclosed behaviour, anchored).
#   test_explicit_vs_explicit_match_preserved_under_declared_anchor — an
#     identical EXPLICIT port on both sides ⇒ 303 (only under a PORT-BEARING
#     declaration; here the declaration is PORTLESS, so the Host anchor's
#     explicit port rides the same elision rule).
#   test_malformed_origin_port_fails_closed — a malformed / out-of-range
#     origin port ⇒ unparseable ⇒ 403.
#
# NOTE: the commission's brief predicted 403 for the :8443 shape ("the anchor
# is portless; only :443/default elision may grant"). The repo's own pins
# assert 303 for exactly that shape — the brief's expectation and the pinned
# contract disagree. The expectation below follows the PINNED contract; the
# divergence is reported in VERDICT-SUMMARY.md.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/rk_lib.sh"
load_env
CODE="$(cat "$FENCE/passcode-view")"
GF="$EV/B3-ports.txt"
: > "$GF"
echo "# GROUP 5 — port forms on the vouched anchor — $(date -u +%FT%TZ) — view=$PUB_R" >> "$GF"
V="$BASE_URL$PUB_R"
BACC="Accept: $BROWSER_ACCEPT"

rowpeek "before-B3"

probe "$GF" "B5.1 Host views.example:8443 + Origin https://views.example:8443 ⇒ EXPECT 303 (pinned elision; brief predicted 403)" \
  303 -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" \
  -H "Host: views.example:8443" -H "Origin: https://views.example:8443" --data "code=$CODE"
pace 13
probe "$GF" "B5.2 Host views.example:443 + Origin https://views.example:443 ⇒ EXPECT 303 (default-port elision)" \
  303 -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" \
  -H "Host: views.example:443" -H "Origin: https://views.example:443" --data "code=$CODE"
pace 13
probe "$GF" "B5.3 Host views.example (portless) + Origin https://views.example:443 ⇒ EXPECT 303 (portless↔explicit elision)" \
  303 -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" \
  -H "Host: views.example" -H "Origin: https://views.example:443" --data "code=$CODE"
pace 13
probe "$GF" "B5.4 Host views.example:443 + Origin https://views.example (portless) ⇒ EXPECT 303 (portless↔explicit elision)" \
  303 -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" \
  -H "Host: views.example:443" -H "Origin: https://views.example" --data "code=$CODE"
pace 13
probe "$GF" "B5.5 Host views.example + Origin https://views.example:0 ⇒ EXPECT 303 (port 0 elides against the portless anchor)" \
  303 -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" \
  -H "Host: views.example" -H "Origin: https://views.example:0" --data "code=$CODE"
pace 13
probe "$GF" "B5.6 Host views.example + Origin https://views.example:65535 ⇒ EXPECT 303 (port elides against the portless anchor)" \
  303 -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" \
  -H "Host: views.example" -H "Origin: https://views.example:65535" --data "code=$CODE"
pace 13
probe "$GF" "B5.7 Host views.example + Origin 'https://views.example:' (trailing colon ⇒ empty port dropped) ⇒ EXPECT 303 (record)" \
  303 -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" \
  -H "Host: views.example" -H "Origin: https://views.example:" --data "code=$CODE"
pace 13
probe "$GF" "B5.8 Host views.example + Origin https://views.example:notaport ⇒ EXPECT 403 (malformed origin port ⇒ unparseable)" \
  403 -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" \
  -H "Host: views.example" -H "Origin: https://views.example:notaport" --data "code=$CODE"
pace 13
probe "$GF" "B5.9 Host views.example + Origin https://views.example:99999 ⇒ EXPECT 403 (out-of-range origin port ⇒ unparseable)" \
  403 -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" \
  -H "Host: views.example" -H "Origin: https://views.example:99999" --data "code=$CODE"
pace 13

rowpeek "after-B3"
echo "== GROUP 5 complete: $(grep -c '^####' "$GF") probes"
