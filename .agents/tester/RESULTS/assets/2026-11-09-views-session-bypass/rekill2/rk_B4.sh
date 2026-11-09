#!/usr/bin/env bash
# rk_B4.sh — SWEEP GROUP 6 (case / trailing-dot on the vouched anchor) +
#            GROUP 7 (IPv6 / IDN / suffix lookalikes).
#
# Contract anchors: K6 (test_k6_normalization_equivalence_no_escape) — the
# agreement check normalizes case, the trailing dot and ports on BOTH sides:
#   ("VIEWS.EXAMPLE.COM", "https://views.example.com") => 303
#   ("views.example.com.", "https://views.example.com.") => 303   <-- the
#        trailing-dot (FQDN root-dot) form of the DECLARED host GRANTS; the
#        brief predicted 403 for it — the pinned contract says 303.
#   ("evil.example.", "https://evil.example.") => 403  (no escape)
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/rk_lib.sh"
load_env
CODE="$(cat "$FENCE/passcode-view")"
GF="$EV/B4-case-dot-idn.txt"
: > "$GF"
echo "# GROUP 6+7 — case / trailing-dot / IPv6 / IDN lookalikes — $(date -u +%FT%TZ) — view=$PUB_R" >> "$GF"
V="$BASE_URL$PUB_R"
BACC="Accept: $BROWSER_ACCEPT"

rowpeek "before-B4"

# ── GROUP 6: case + trailing dot ─────────────────────────────────────────
probe "$GF" "B6.1 Host VIEWS.EXAMPLE + Origin https://views.example ⇒ EXPECT 303 (case-normalized)" \
  303 -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" \
  -H "Host: VIEWS.EXAMPLE" -H "Origin: https://views.example" --data "code=$CODE"
pace 13
probe "$GF" "B6.2 Host views.example. + Origin https://views.example. ⇒ EXPECT 303 (pinned FQDN root-dot equivalence; brief predicted 403)" \
  303 -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" \
  -H "Host: views.example." -H "Origin: https://views.example." --data "code=$CODE"
pace 13
probe "$GF" "B6.3 cross-form: Host views.example. + Origin https://views.example ⇒ EXPECT 303" \
  303 -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" \
  -H "Host: views.example." -H "Origin: https://views.example" --data "code=$CODE"
pace 13
probe "$GF" "B6.4 cross-form: Host views.example + Origin https://views.example. ⇒ EXPECT 303" \
  303 -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" \
  -H "Host: views.example" -H "Origin: https://views.example." --data "code=$CODE"
pace 13

# ── GROUP 7: IPv6 / IDN / suffix lookalikes ──────────────────────────────
probe "$GF" "B7.1 Host [::1]:8082 + Origin https://[::1] ⇒ EXPECT 303 (exact-loopback carve — allowed source)" \
  303 -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" \
  -H "Host: [::1]:8082" -H "Origin: https://[::1]" --data "code=$CODE"
pace 13
probe "$GF" "B7.2 Host xn--views-4ua.example + Origin https://xn--views-4ua.example ⇒ EXPECT 403 (punycode lookalike)" \
  403 -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" \
  -H "Host: xn--views-4ua.example" -H "Origin: https://xn--views-4ua.example" --data "code=$CODE"
pace 13
probe "$GF" "B7.3 Host 'vıews.example' (raw UTF-8 confusable) + Origin https://vıews.example ⇒ RECORD (expect 403; a 400 means the HTTP layer rejected the header)" \
  RECORD -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" \
  -H "Host: vıews.example" -H "Origin: https://vıews.example" --data "code=$CODE"
pace 13
probe "$GF" "B7.4 Host views.example.evil.com + Origin https://views.example.evil.com ⇒ EXPECT 403 (suffix lookalike)" \
  403 -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" \
  -H "Host: views.example.evil.com" -H "Origin: https://views.example.evil.com" --data "code=$CODE"
pace 13

rowpeek "after-B4"
echo "== GROUP 6+7 complete: $(grep -c '^####' "$GF") probes"
