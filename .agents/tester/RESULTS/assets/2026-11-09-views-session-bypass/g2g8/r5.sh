#!/usr/bin/env bash
# R5 — SANITY (≤5 min): view_write-adjacent handlers ride the same
# origin/session helpers? grep-level + one HTTP probe on the closest
# session-adjacent surface (the live board lane).
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/lib.sh"
load_env
SID="$(cat "$FENCE/sid-valid")"
GF="$EV/R5.txt"
: > "$GF"
echo "# R5 SANITY — $(date -u +%FT%TZ)" >> "$GF"

echo "== grep 1: resolve_views_session callsites (the R1 seam) ==" | tee -a "$GF"
grep -n "resolve_views_session(request)" "$WT/daemon/routers/live_views.py" | tee -a "$GF"

echo "== grep 2: view_write surface — HTTP handlers with write verbs on /views? ==" | tee -a "$GF"
grep -nE "@router\.(post|put|delete|patch)" "$WT/daemon/routers/live_views.py" | tee -a "$GF"

echo "== grep 3: view_write TOOL authz seam (agent tool, NOT HTTP) ==" | tee -a "$GF"
grep -nE "def create_live_view_tools|calling_instance|instance_id" "$WT/daemon/tools/live_views.py" | head -8 | tee -a "$GF"

BACC="Accept: $BROWSER_ACCEPT"
BOARD="$BASE_URL/views/public/board/index.html"
probe "$GF" "R5-board GET board WITHOUT session (anonymous posture; observe)" -H "$BACC" "$BOARD"
probe "$GF" "R5-board GET board WITH valid ensemble_sid (session bypass on board lane; observe)" -H "$BACC" -H "Cookie: ensemble_sid=$SID" "$BOARD"
echo "== R5 complete"
