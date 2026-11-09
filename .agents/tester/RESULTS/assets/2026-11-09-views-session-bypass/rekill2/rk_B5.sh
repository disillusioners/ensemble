#!/usr/bin/env bash
# rk_B5.sh — SWEEP GROUP 9 (board lane — the SECOND gate surface) +
#            GROUP 10 (declared-base parse edges + XFH userinfo / www / scheme /
#            port-elision edges) + the BOOT-1 session-control probe.
#
# Group 9: the board verify endpoint (/views/public/board/index.html) routes
# through the SAME _public_post gate → the same _same_host_origin_check →
# the same _views_trusted_hosts. Re-kill shape against it.
#
# Group 10 probes are READ-FROM-CODE selections (the commission: "wire-probe
# only what the code suggests could wobble", 1-2 decisive probes — 6 are run
# because each targets a distinct parse seam of the FIX-10 veto).
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/rk_lib.sh"
load_env
CODE="$(cat "$FENCE/passcode-view")"
BCODE="$(cat "$FENCE/passcode-board")"
SID="$(cat "$FENCE/session-id" 2>/dev/null || echo '')"
GF="$EV/B5-board-and-parse-edges.txt"
: > "$GF"
echo "# GROUP 9+10 + session control — $(date -u +%FT%TZ) — view=$PUB_R board=$BOARD_PUB_R" >> "$GF"
V="$BASE_URL$PUB_R"
BV="$BASE_URL$BOARD_PUB_R"
BACC="Accept: $BROWSER_ACCEPT"
HOP="Host: internal-svc:8079"

rowpeek "before-B5"

# ── GROUP 9: board lane (second gate surface) ────────────────────────────
probe "$GF" "B9.1 BOARD re-kill: forged XFH evil + Origin evil ⇒ EXPECT 403" \
  403 -X POST "$BV" -H "$BACC" -H "X-Forwarded-Proto: https" -H "$HOP" \
  -H "X-Forwarded-Host: evil.example" -H "Origin: https://evil.example" --data "code=$BCODE"
pace 13
probe "$GF" "B9.2 BOARD control: XFH views.example + Origin views.example ⇒ EXPECT 303 + grant" \
  303 -X POST "$BV" -H "$BACC" -H "X-Forwarded-Proto: https" -H "$HOP" \
  -H "X-Forwarded-Host: views.example" -H "Origin: https://views.example" --data "code=$BCODE"
pace 13
probe "$GF" "B9.3 BOARD no-XFH + evil Origin ⇒ EXPECT 403" \
  403 -X POST "$BV" -H "$BACC" -H "X-Forwarded-Proto: https" -H "$HOP" \
  -H "Origin: https://evil.example" --data "code=$BCODE"
pace 13

# ── GROUP 10: declared-base / veto parse edges ───────────────────────────
probe "$GF" "B10.1 XFH 'evil.example@views.example' (userinfo) + Origin evil ⇒ EXPECT 403 (XFH parses to views.example ⇒ NO veto, but the Origin is unvouched)" \
  403 -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" -H "$HOP" \
  -H "X-Forwarded-Host: evil.example@views.example" -H "Origin: https://evil.example" --data "code=$CODE"
pace 13
probe "$GF" "B10.2 XFH 'evil.example@views.example' (userinfo) + Origin views ⇒ EXPECT 303 (no veto; grant from the DECLARED anchor)" \
  303 -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" -H "$HOP" \
  -H "X-Forwarded-Host: evil.example@views.example" -H "Origin: https://views.example" --data "code=$CODE"
pace 13
probe "$GF" "B10.3 XFH 'views.example@evil.example' (userinfo reversed) + Origin evil ⇒ EXPECT 403 (parses to evil.example ⇒ veto)" \
  403 -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" -H "$HOP" \
  -H "X-Forwarded-Host: views.example@evil.example" -H "Origin: https://evil.example" --data "code=$CODE"
pace 13
probe "$GF" "B10.4 XFH 'www.views.example' + Origin views ⇒ EXPECT 303 (the www fold applies INSIDE the veto)" \
  303 -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" -H "$HOP" \
  -H "X-Forwarded-Host: www.views.example" -H "Origin: https://views.example" --data "code=$CODE"
pace 13
probe "$GF" "B10.5 Origin http://views.example (declared base is https) ⇒ EXPECT 303 (scheme never participates)" \
  303 -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" -H "$HOP" \
  -H "Origin: http://views.example" --data "code=$CODE"
pace 13
probe "$GF" "B10.6 XFH 'views.example:9999' + Origin views ⇒ EXPECT 303 (the veto's own port elision)" \
  303 -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" -H "$HOP" \
  -H "X-Forwarded-Host: views.example:9999" -H "Origin: https://views.example" --data "code=$CODE"
pace 13

# ── session CONTROL in the auth-OFF boot ─────────────────────────────────
if [ -n "$SID" ]; then
  probe "$GF" "B8.1 CONTROL (auth OFF): a REAL ensemble_sid cookie + XFH evil + Origin evil ⇒ EXPECT 403 (auth OFF ⇒ no session bypass ⇒ the anonymous origin gate applies)" \
    403 -X POST "$V" -H "$BACC" -H "X-Forwarded-Proto: https" -H "$HOP" \
    -H "Cookie: ensemble_sid=$SID" \
    -H "X-Forwarded-Host: evil.example" -H "Origin: https://evil.example" --data "code=$CODE"
  pace 13
else
  echo "NO SESSION ID — skipping the BOOT-1 session control" >> "$GF"
fi

rowpeek "after-B5"
echo "== GROUP 9+10 complete: $(grep -c '^####' "$GF") probes"
