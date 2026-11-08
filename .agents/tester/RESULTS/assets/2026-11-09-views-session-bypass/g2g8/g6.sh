#!/usr/bin/env bash
# G6 — NEVER-JSON-ON-HUMAN-PAGES + R3 byte-identity.
# Surfaces: unminted(404), locked(viewB), rate-limited(429), origin-reject(403).
# Each twice (Accept text/html → MUST be HTML; Accept application/json → JSON)
# plus text/plain leg + sha256 byte-identity where both non-HTML accepts yield JSON.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/lib.sh"
load_env
CODE_B="$(cat "$FENCE/passcode-b")"
GF="$EV/G6.txt"
: > "$GF"
echo "# G6 NEVER-JSON-ON-HUMAN-PAGES + R3 byte-identity — $(date -u +%FT%TZ)" >> "$GF"

VA="$BASE_URL$PUB_A"; VB="$BASE_URL$PUB_B"; UNMINTED="$BASE_URL/views/public/$ROOT/never-minted.md"
probe "$GF" "S1-unminted Accept:text/html → MUST be HTML 404" -H "Accept: text/html" "$UNMINTED"
probe "$GF" "S1-unminted Accept:application/json → JSON envelope 404" -H "Accept: application/json" "$UNMINTED"
probe_sha "$GF" "S1-unminted Accept:application/json (sha)" -H "Accept: application/json" "$UNMINTED"
probe_sha "$GF" "S1-unminted Accept:text/plain (sha; expect same JSON bytes as application/json)" -H "Accept: text/plain" "$UNMINTED"

probe "$GF" "S2-locked GET viewB Accept:text/html → locked HTML 200" -H "Accept: text/html" "$VB"
probe "$GF" "S2-locked GET viewB Accept:application/json → JSON envelope 200" -H "Accept: application/json" "$VB"
probe "$GF" "S2-locked GET viewB Accept:text/plain → observe (explicit-json-only flip per R4)" -H "Accept: text/plain" "$VB"

# S3 rate-limited: burn the bucket with default-Accept POSTs on locked viewB.
for i in 1 2 3 4 5 6 7; do
  probe "$GF" "S3-burn $i (default */* accept; consume/observe)" \
    -X POST "$VB" -H "X-Forwarded-Proto: https" --data "code=WRONGG6"
done
probe "$GF" "S3-ratelimited POST viewB Accept:text/html → MUST be HTML 429" \
  -X POST "$VB" -H "Accept: text/html" -H "X-Forwarded-Proto: https" --data "code=WRONGG6"
probe "$GF" "S3-ratelimited POST viewB Accept:application/json → JSON 429" \
  -X POST "$VB" -H "Accept: application/json" -H "X-Forwarded-Proto: https" --data "code=WRONGG6"
probe_sha "$GF" "S3-ratelimited POST viewB Accept:text/plain (sha; observe shape)" \
  -X POST "$VB" -H "Accept: text/plain" -H "X-Forwarded-Proto: https" --data "code=WRONGG6"

# S4 origin-reject on viewA (needs tokens; pace).
sleep 26
probe "$GF" "S4-origin POST viewA evil origin Accept:text/html → MUST be HTML 403" \
  -X POST "$VA" -H "Accept: text/html" -H "X-Forwarded-Proto: https" \
  -H "Origin: https://evil.example" --data "code=x"
sleep 14
probe "$GF" "S4-origin POST viewA evil origin Accept:application/json → JSON 403" \
  -X POST "$VA" -H "Accept: application/json" -H "X-Forwarded-Proto: https" \
  -H "Origin: https://evil.example" --data "code=x"
probe_sha "$GF" "S4-origin POST viewA evil origin Accept:application/json (sha)" \
  -X POST "$VA" -H "Accept: application/json" -H "X-Forwarded-Proto: https" \
  -H "Origin: https://evil.example" --data "code=x"
sleep 14
probe_sha "$GF" "S4-origin POST viewA evil origin Accept:text/plain (sha; expect same JSON bytes)" \
  -X POST "$VA" -H "Accept: text/plain" -H "X-Forwarded-Proto: https" \
  -H "Origin: https://evil.example" --data "code=x"

echo "== G6 path-disclosure grep (expect NO hits in any body):"
grep -iE "matrix-a|matrix-b|g2g8matrix|never-minted|evil\.example.*path" "$GF" | grep -v "^#" | grep -vE "^\\\$ curl|Origin:" && echo "!! DISCLOSURE HITS ABOVE" || echo "CLEAN: no path disclosure in any recorded body"
echo "== G6 complete"
