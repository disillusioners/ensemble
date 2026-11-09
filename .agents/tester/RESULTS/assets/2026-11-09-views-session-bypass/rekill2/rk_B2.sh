#!/usr/bin/env bash
# rk_B2.sh — SWEEP GROUP 3 (RFC 7239 Forwarded variants) + GROUP 4 (Referer fallback).
#
# Contract anchors: K5 (RFC 7239 equivalents of K1-K3) + K7 (Referer judged
# against the anchor set only). The forwarded value may only VETO (FIX-10):
# the FIRST Forwarded ELEMENT's host= parameter is read.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/rk_lib.sh"
load_env
CODE="$(cat "$FENCE/passcode-view")"
GF="$EV/B2-forwarded-referer.txt"
: > "$GF"
echo "# GROUP 3+4 — Forwarded variants + Referer fallback — $(date -u +%FT%TZ) — view=$PUB_R" >> "$GF"
V="$BASE_URL$PUB_R"
BACC="Accept: $BROWSER_ACCEPT"
HOP="Host: internal-svc:8079"

rowpeek "before-B2"

# ── GROUP 3: Forwarded header variants ───────────────────────────────────
probe "$GF" "B3.1 Forwarded 'host=views.example, host=evil.example' + Origin evil ⇒ EXPECT 403 (first element host= rules)" \
  403 -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" -H "$HOP" \
  -H "Forwarded: host=views.example, host=evil.example" -H "Origin: https://evil.example" --data "code=$CODE"
pace 13
probe "$GF" "B3.2 Forwarded 'host=views.example, host=evil.example' + Origin views ⇒ EXPECT 303 (first element agrees)" \
  303 -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" -H "$HOP" \
  -H "Forwarded: host=views.example, host=evil.example" -H "Origin: https://views.example" --data "code=$CODE"
pace 13
probe "$GF" "B3.3 Forwarded 'host=evil.example, host=views.example' + Origin evil ⇒ EXPECT 403 (first element host= contradicts)" \
  403 -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" -H "$HOP" \
  -H "Forwarded: host=evil.example, host=views.example" -H "Origin: https://evil.example" --data "code=$CODE"
pace 13
probe "$GF" "B3.4 Forwarded 'host=evil.example, host=views.example' + Origin views ⇒ EXPECT 403 (K2-equivalent: a mismatching forwarded value ALONE vetoes)" \
  403 -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" -H "$HOP" \
  -H "Forwarded: host=evil.example, host=views.example" -H "Origin: https://views.example" --data "code=$CODE"
pace 13
probe "$GF" "B3.5 Forwarded 'for=1.2.3.4;host=evil.example' + Origin evil ⇒ EXPECT 403 (K1-equivalent)" \
  403 -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" -H "$HOP" \
  -H "Forwarded: for=1.2.3.4;host=evil.example" -H "Origin: https://evil.example" --data "code=$CODE"
pace 13
probe "$GF" "B3.6 Forwarded quoted form 'for=1.2.3.4;host=\"views.example\"' + Origin views ⇒ EXPECT 303 (quoted legit form vouches)" \
  303 -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" -H "$HOP" \
  -H 'Forwarded: for=1.2.3.4;host="views.example"' -H "Origin: https://views.example" --data "code=$CODE"
pace 13
probe "$GF" "B3.7 Forwarded port form 'host=views.example:443' + Origin views ⇒ EXPECT 303 (port elides against the portless declared anchor)" \
  303 -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" -H "$HOP" \
  -H "Forwarded: host=views.example:443" -H "Origin: https://views.example" --data "code=$CODE"
pace 13

# ── GROUP 4: Referer fallback under the declared front ───────────────────
probe "$GF" "B4.1 NO Origin + Referer https://evil.example/x ⇒ EXPECT 403 (K7: judged against the anchor set only)" \
  403 -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" -H "$HOP" \
  -H "Referer: https://evil.example/x" --data "code=$CODE"
pace 13
probe "$GF" "B4.2 NO Origin + Referer https://evil.example/x + forged XFH evil ⇒ EXPECT 403 (veto)" \
  403 -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" -H "$HOP" \
  -H "X-Forwarded-Host: evil.example" -H "Referer: https://evil.example/x" --data "code=$CODE"
pace 13
probe "$GF" "B4.3 NO Origin + Referer https://views.example/x ⇒ EXPECT 303 (K7: the declared front's own Referer is accepted)" \
  303 -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" -H "$HOP" \
  -H "Referer: https://views.example/x" --data "code=$CODE"
pace 13

rowpeek "after-B2"
echo "== GROUP 3+4 complete: $(grep -c '^####' "$GF") probes"
