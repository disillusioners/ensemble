#!/usr/bin/env bash
# rk_C1.sh — BOOT 2 (auth-ON, audience https://op.example, NO declared base):
#            group C apex/www mixed wire-confirms + regression guards +
#            the undeclared-front forwarded-inertness hunt.
#
# Contract anchors: council-verified in code (apex/www ≡ on a vouched name —
# _www_equivalent folds www.<d> ≡ <d> on the COMPARISON side; the audience
# anchor is portless ⇒ explicit-vs-portless elision); the auth-lane
# degradation guards (absent-Origin ⇒ allow, null-Origin ⇒ allow unless
# Sec-Fetch-Site: cross-site).
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/rk_lib.sh"
load_env
CODE="$(cat "$FENCE/passcode-view")"
GF="$EV/C1-boot2-audience.txt"
: > "$GF"
echo "# GROUP C — BOOT 2 (auth-ON, audience https://op.example, NO declared base) — $(date -u +%FT%TZ) — view=$PUB_R" >> "$GF"
V="$BASE_URL$PUB_R"
BACC="Accept: $BROWSER_ACCEPT"

rowpeek "before-C"

# ── C: apex/www mixed wire-confirms ──────────────────────────────────────
probe "$GF" "C1 Origin https://op.example ⇒ EXPECT 303 (audience-vouched apex)" \
  303 -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" -H "Host: op.example" \
  -H "Origin: https://op.example" --data "code=$CODE"
pace 13
probe "$GF" "C2 Origin https://www.op.example ⇒ EXPECT 303 (www ≡ apex on a vouched name)" \
  303 -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" -H "Host: op.example" \
  -H "Origin: https://www.op.example" --data "code=$CODE"
pace 13
probe "$GF" "C3 Origin https://WWW.OP.EXAMPLE (mixed case) ⇒ EXPECT 303 (case-normalized)" \
  303 -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" -H "Host: op.example" \
  -H "Origin: https://WWW.OP.EXAMPLE" --data "code=$CODE"
pace 13
probe "$GF" "C4 Origin https://op.example. (trailing dot) ⇒ RECORD (brief predicted 403)" \
  RECORD -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" -H "Host: op.example" \
  -H "Origin: https://op.example." --data "code=$CODE"
pace 13

# ── regression guards ────────────────────────────────────────────────────
probe "$GF" "C5 NO Origin ⇒ EXPECT 303 (absent-Origin allow — unchanged)" \
  303 -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" -H "Host: op.example" \
  --data "code=$CODE"
pace 13
probe "$GF" "C6 Origin null, NO Sec-Fetch-Site ⇒ EXPECT 303 (null-allow, unchanged)" \
  303 -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" -H "Host: op.example" \
  -H "Origin: null" --data "code=$CODE"
pace 13
probe "$GF" "C7 Origin null + Sec-Fetch-Site: cross-site ⇒ EXPECT 403 (the null-allow is cross-site-guarded)" \
  403 -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" -H "Host: op.example" \
  -H "Origin: null" -H "Sec-Fetch-Site: cross-site" --data "code=$CODE"
pace 13
probe "$GF" "C8 Origin https://evil.example ⇒ EXPECT 403 (cross-site)" \
  403 -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" -H "Host: op.example" \
  -H "Origin: https://evil.example" --data "code=$CODE"
pace 13

# ── undeclared-front forwarded-inertness hunt (FIX-1) ────────────────────
probe "$GF" "C9 forged XFH evil.example + Origin evil + Host op.example ⇒ EXPECT 403 (no declared front ⇒ forwarded inert; the audience never vouches evil)" \
  403 -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" -H "Host: op.example" \
  -H "X-Forwarded-Host: evil.example" -H "Origin: https://evil.example" --data "code=$CODE"
pace 13
probe "$GF" "C10 forged XFH evil.example + Origin op.example ⇒ EXPECT 303 (grant source = the AUDIENCE anchor, not the forwarded value)" \
  303 -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" -H "Host: op.example" \
  -H "X-Forwarded-Host: evil.example" -H "Origin: https://op.example" --data "code=$CODE"
pace 13
probe "$GF" "C11 Host evil.example (unvouched) + Origin op.example ⇒ EXPECT 303 (the audience anchor vouches the Origin; the Host is not needed)" \
  303 -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" -H "Host: evil.example" \
  -H "Origin: https://op.example" --data "code=$CODE"
pace 13
probe "$GF" "C12 Origin https://op.example:8443 + Host op.example ⇒ RECORD (port elision on the portless audience anchor)" \
  RECORD -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" -H "Host: op.example" \
  -H "Origin: https://op.example:8443" --data "code=$CODE"
pace 13
probe "$GF" "C13 Host op.example:8443 + Origin https://op.example:8443 ⇒ RECORD" \
  RECORD -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" -H "Host: op.example:8443" \
  -H "Origin: https://op.example:8443" --data "code=$CODE"
pace 13
probe "$GF" "C14 Origin https://[::1] + Host 127.0.0.1:8082 ⇒ EXPECT 403 (the loopback anchor is 127.0.0.1:8082, not ::1)" \
  403 -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" -H "Host: 127.0.0.1:8082" \
  -H "Origin: https://[::1]" --data "code=$CODE"
pace 13
probe "$GF" "C15 Origin https://127.0.0.1:8082 + Host 127.0.0.1:8082 ⇒ EXPECT 303 (exact-loopback carve — allowed source)" \
  303 -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" -H "Host: 127.0.0.1:8082" \
  -H "Origin: https://127.0.0.1:8082" --data "code=$CODE"
pace 13
probe "$GF" "C16 Host op.example.evil.com + Origin https://op.example.evil.com ⇒ EXPECT 403 (suffix lookalike)" \
  403 -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" -H "Host: op.example.evil.com" \
  -H "Origin: https://op.example.evil.com" --data "code=$CODE"
pace 13

rowpeek "after-C1"
echo "== GROUP C (anonymous) complete: $(grep -c '^####' "$GF") probes"
