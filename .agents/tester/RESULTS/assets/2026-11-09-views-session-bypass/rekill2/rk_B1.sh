#!/usr/bin/env bash
# rk_B1.sh — SWEEP GROUP 1 (XFH multi-value order games) + GROUP 2 (XFH multi-LINE).
#
# Contract anchors (repo pins):
#   K4 (test_k4_multi_value_first_value_rules) — FIRST-value semantics:
#     XFH "evil, views" ⇒ the first contradicts the declared front ⇒ 403 for
#     ANY Origin; XFH "views, evil" ⇒ first agrees ⇒ the request proceeds and
#     the APPENDED value is ignored (it never anchors) ⇒ Origin views ⇒ 303,
#     Origin evil ⇒ 403.
#   Multi-LINE (two separate header lines): the FIRST LINE wins (Starlette
#     Headers.get returns the first occurrence) — recorded here, and the
#     validation is checked to follow the winning line.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/rk_lib.sh"
load_env
CODE="$(cat "$FENCE/passcode-view")"
GF="$EV/B1-xfh-multi.txt"
: > "$GF"
echo "# GROUP 1+2 — XFH multi-value / multi-LINE — $(date -u +%FT%TZ) — view=$PUB_R" >> "$GF"
V="$BASE_URL$PUB_R"
BACC="Accept: $BROWSER_ACCEPT"
HOP="Host: internal-svc:8079"

rowpeek "before-B1"

# ── GROUP 1: multi-value order games ─────────────────────────────────────
probe "$GF" "B1.1 XFH 'evil.example, views.example' + Origin evil ⇒ EXPECT 403 (first value contradicts)" \
  403 -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" -H "$HOP" \
  -H "X-Forwarded-Host: evil.example, views.example" -H "Origin: https://evil.example" --data "code=$CODE"
pace 13
probe "$GF" "B1.2 XFH 'evil.example, views.example' + Origin views ⇒ EXPECT 403 (first value contradicts; K4)" \
  403 -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" -H "$HOP" \
  -H "X-Forwarded-Host: evil.example, views.example" -H "Origin: https://views.example" --data "code=$CODE"
pace 13
probe "$GF" "B1.3 XFH 'views.example, evil.example' + Origin views ⇒ EXPECT 303 (first agrees; appended evil ignored)" \
  303 -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" -H "$HOP" \
  -H "X-Forwarded-Host: views.example, evil.example" -H "Origin: https://views.example" --data "code=$CODE"
pace 13
probe "$GF" "B1.4 XFH 'views.example, evil.example' + Origin evil ⇒ EXPECT 403 (appended evil never anchors)" \
  403 -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" -H "$HOP" \
  -H "X-Forwarded-Host: views.example, evil.example" -H "Origin: https://evil.example" --data "code=$CODE"
pace 13

# ── GROUP 2: multi-LINE (two separate header lines) ──────────────────────
probe "$GF" "B2.1 two XFH lines [evil / views] + Origin evil ⇒ RECORD which line wins (expect 403)" \
  403 -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" -H "$HOP" \
  -H "X-Forwarded-Host: evil.example" -H "X-Forwarded-Host: views.example" \
  -H "Origin: https://evil.example" --data "code=$CODE"
pace 13
probe "$GF" "B2.2 two XFH lines [evil / views] + Origin views ⇒ RECORD (expect 403 if the FIRST line rules)" \
  403 -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" -H "$HOP" \
  -H "X-Forwarded-Host: evil.example" -H "X-Forwarded-Host: views.example" \
  -H "Origin: https://views.example" --data "code=$CODE"
pace 13
probe "$GF" "B2.3 two XFH lines [views / evil] + Origin views ⇒ RECORD (expect 303 if the FIRST line rules)" \
  303 -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" -H "$HOP" \
  -H "X-Forwarded-Host: views.example" -H "X-Forwarded-Host: evil.example" \
  -H "Origin: https://views.example" --data "code=$CODE"
pace 13
probe "$GF" "B2.4 two XFH lines [views / evil] + Origin evil ⇒ RECORD (expect 403: appended/second line never anchors)" \
  403 -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" -H "$HOP" \
  -H "X-Forwarded-Host: views.example" -H "X-Forwarded-Host: evil.example" \
  -H "Origin: https://evil.example" --data "code=$CODE"
pace 13

rowpeek "after-B1"
echo "== GROUP 1+2 complete: $(grep -c '^####' "$GF") probes"
