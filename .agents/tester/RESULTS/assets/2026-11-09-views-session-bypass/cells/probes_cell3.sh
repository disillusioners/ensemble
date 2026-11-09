#!/usr/bin/env bash
# CELL (iii) PROBES — unconfigured loopback carve (auth-OFF, NO declared
# base). Connect from 127.0.0.1; carve = peer loopback + EXACT loopback
# name (localhost / 127.0.0.1 / ::1 per _views_exact_loopback_name).
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/lib.sh"
load_env
set -a; source "$FENCE/state.env"; set +a

GF="$EV/cell3-transcript.txt"
SUM="$EV/cell3-verdicts.txt"
: > "$GF"; : > "$SUM"
CODE="$(cat "$FENCE/passcode-a")"
URL="$BASE_URL$PUB_A"

BASEARGS=(-H "Accept: $BROWSER_ACCEPT" -H "X-Forwarded-Proto: https" --data-urlencode "code=$CODE")

paced_post "$GF" "cell3-p1 EXPECT-303+grant [carve: peer loopback + exact 127.0.0.1] Host: 127.0.0.1:${PORT} + Origin: https://127.0.0.1:${PORT}" \
  "${BASEARGS[@]}" -H "Host: 127.0.0.1:${PORT}" -H "Origin: https://127.0.0.1:${PORT}" "$URL" | tee -a "$SUM"

paced_post "$GF" "cell3-p2 EXPECT-303+grant [carve: exact name localhost] Host: localhost:${PORT} + Origin: https://localhost:${PORT}" \
  "${BASEARGS[@]}" -H "Host: localhost:${PORT}" -H "Origin: https://localhost:${PORT}" "$URL" | tee -a "$SUM"

paced_post "$GF" "cell3-p3 EXPECT-403+zero-strikes [lookalike self-match] Host: 127.0.0.1.evil.example + Origin: https://127.0.0.1.evil.example" \
  "${BASEARGS[@]}" -H "Host: 127.0.0.1.evil.example" -H "Origin: https://127.0.0.1.evil.example" "$URL" | tee -a "$SUM"

paced_post "$GF" "cell3-p4 EXPECT-403+zero-strikes [lookalike] Host: localhost.evil.example + Origin: https://localhost.evil.example" \
  "${BASEARGS[@]}" -H "Host: localhost.evil.example" -H "Origin: https://localhost.evil.example" "$URL" | tee -a "$SUM"

paced_post "$GF" "cell3-p5 EXPECT-403+zero-strikes [127.x range != canonical name; record actual] Host: 127.0.0.2:${PORT} + Origin: https://127.0.0.2:${PORT}" \
  "${BASEARGS[@]}" -H "Host: 127.0.0.2:${PORT}" -H "Origin: https://127.0.0.2:${PORT}" "$URL" | tee -a "$SUM"

paced_post "$GF" "cell3-p6 EXPECT-403+zero-strikes [cross-name: vouched Host localhost vs Origin 127.0.0.1] Host: localhost:${PORT} + Origin: https://127.0.0.1:${PORT}" \
  "${BASEARGS[@]}" -H "Host: localhost:${PORT}" -H "Origin: https://127.0.0.1:${PORT}" "$URL" | tee -a "$SUM"

{
  echo "=== CELL (iii) rowpeek AFTER (before-shot = rowpeek-cell2-after.txt, same row) ==="
  "$WT/.venv/bin/python" "$HERE/rowpeek_cells.py"
} | tee "$EV/rowpeek-cell3-after.txt"
cp "$EV/rowpeek-cell3-after.txt" "$MIRROR/" 2>/dev/null || true
cp "$GF" "$MIRROR/" 2>/dev/null || true
echo "== cell (iii) probes complete"
