#!/usr/bin/env bash
# GROUP C part 2 — GENERALIZATION HUNT (C7–C10 + own extras CX1–CX5).
# C7b/C7d/C10 are GRANT-expected BY THE CARVE (peer loopback + EXACT
# loopback name); each grant must be attributable to the carve (exact
# name), proven by the 127.0.0.2 + lookalike negatives around it.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/rp_lib.sh"
load_env
CODE="$(cat "$FENCE/passcode-reprobe")"
GF="$EV/C7-CX-hunt.txt"
: > "$GF"
echo "# GROUP C7-CX — GENERALIZATION HUNT part 2 — $(date -u +%FT%TZ) — view=$PUB_R (correct code, XFP https, browser Accept)" >> "$GF"
V="$BASE_URL$PUB_R"
BACC="Accept: $BROWSER_ACCEPT"

probe "$GF" "C7a loopback cross-name: Host: localhost + Origin: https://127.0.0.1 → EXPECT 403 (names differ; carve is exact-name)" \
  -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" \
  -H "Host: localhost" -H "Origin: https://127.0.0.1" --data "code=$CODE"
sleep 14
probe "$GF" "C7b loopback EXACT self-match: Host: 127.0.0.1 + Origin: https://127.0.0.1 → GRANT-EXPECTED via carve (peer loopback + exact name). Attributed to carve, not capture, by C7c/C7d/C6 negatives" \
  -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" \
  -H "Host: 127.0.0.1" -H "Origin: https://127.0.0.1" --data "code=$CODE"
sleep 14
rowpeek "after-C7b"
probe "$GF" "C7c loopback RANGE non-canonical: Host: 127.0.0.2 + Origin: https://127.0.0.2 → EXPECT 403 (127.0.0.0/8 blanket must NOT anchor)" \
  -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" \
  -H "Host: 127.0.0.2" -H "Origin: https://127.0.0.2" --data "code=$CODE"
sleep 14
probe "$GF" "C7d IPv6 loopback self-match: Host: [::1] + Origin: https://[::1] → RECORD (expect GRANT via carve: ::1 exact)" \
  -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" \
  -H "Host: [::1]" -H "Origin: https://[::1]" --data "code=$CODE"
sleep 14
rowpeek "after-C7d"
probe "$GF" "C8a XFH revival (NO declared front): Host: evil.example + X-Forwarded-Host: evil.example + Origin: https://evil.example → EXPECT 403 (leg b INERT undeclared)" \
  -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" \
  -H "Host: evil.example" -H "X-Forwarded-Host: evil.example" -H "Origin: https://evil.example" --data "code=$CODE"
sleep 14
probe "$GF" "C8b Forwarded revival (NO declared front): Host: evil.example + Forwarded: host=evil.example + Origin: https://evil.example → EXPECT 403 (leg c INERT undeclared)" \
  -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" \
  -H "Host: evil.example" -H "Forwarded: host=evil.example" -H "Origin: https://evil.example" --data "code=$CODE"
sleep 14
rowpeek "after-C8"
probe "$GF" "C9a ABSENT-Origin with foreign Host: Host: evil.example, NO Origin/Referer → GRANT-EXPECTED (by design: absent Origin allows BEFORE anchors; FLAG if regressed)" \
  -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" \
  -H "Host: evil.example" --data "code=$CODE"
sleep 14
rowpeek "after-C9a"
probe "$GF" "C9b EMPTY-Origin header (treated absent): Host: evil.example + empty Origin → RECORD (expect 303 same as absent)" \
  -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" \
  -H "Host: evil.example" -H "Origin;" --data "code=$CODE"
sleep 14
probe "$GF" "C10a IPv6 bracket+port: Host: [::1]:8082 + Origin: https://[::1]:8082 → RECORD (expect GRANT via carve: ::1 exact + ports equal)" \
  -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" \
  -H "Host: [::1]:8082" -H "Origin: https://[::1]:8082" --data "code=$CODE"
sleep 14
rowpeek "after-C10a"
probe "$GF" "C10b IPv6 port-elision: Host: [::1]:8082 + Origin: https://[::1] → RECORD (expect GRANT: portless origin vs ported anchor is port-compatible by FIX-3)" \
  -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" \
  -H "Host: [::1]:8082" -H "Origin: https://[::1]" --data "code=$CODE"
sleep 14
probe "$GF" "CX1 carve case-insensitivity: Host: LOCALHOST + Origin: https://localhost → RECORD (expect GRANT via carve: vouch check lowercases; consistent with C4a host-case posture)" \
  -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" \
  -H "Host: LOCALHOST" -H "Origin: https://localhost" --data "code=$CODE"
sleep 14
rowpeek "after-CX1"
probe "$GF" "CX2 mapped-v4 spelling: Host: ::ffff:127.0.0.1 + Origin: https://::ffff:127.0.0.1 → EXPECT 403 (peer-side normalization must NOT leak into the NAME compare)" \
  -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" \
  -H "Host: ::ffff:127.0.0.1" -H "Origin: https://::ffff:127.0.0.1" --data "code=$CODE"
sleep 14
probe "$GF" "CX3 carve asymmetry (Host carved, origin foreign): Host: 127.0.0.1 + Origin: https://evil.example → EXPECT 403 (carve vouches the HOST name only; attacker origin must still match it)" \
  -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" \
  -H "Host: 127.0.0.1" -H "Origin: https://evil.example" --data "code=$CODE"
sleep 14
rowpeek "after-CX3"
probe "$GF" "CX4 Referer-fallback self-match: Host: evil.example + Referer: https://evil.example/x (NO Origin) → EXPECT 403 (Referer judged by the same vouched anchors)" \
  -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" \
  -H "Host: evil.example" -H "Referer: https://evil.example/whatever" --data "code=$CODE"
sleep 14
probe "$GF" "CX5 carve port participation: Host: 127.0.0.1:8443 + Origin: https://127.0.0.1:8443 → RECORD (expect GRANT via carve: exact name; ports equal — loopback dev on any port is carve by-design)" \
  -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" \
  -H "Host: 127.0.0.1:8443" -H "Origin: https://127.0.0.1:8443" --data "code=$CODE"
rowpeek "after-CX5"
echo "== GROUP C7-CX complete: $(grep -c '^####' "$GF") probes"
