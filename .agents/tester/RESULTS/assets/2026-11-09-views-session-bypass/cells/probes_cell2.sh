#!/usr/bin/env bash
# CELL (ii) PROBES — auth-OFF + declared base https://views.example.
# All POSTs carry the CORRECT code; grant-expecting POSTs use browser
# Accept + X-Forwarded-Proto: https. Paced >=14s (bucket cap 5/5min).
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/lib.sh"
load_env
set -a; source "$FENCE/state.env"; set +a

GF="$EV/cell2-transcript.txt"
SUM="$EV/cell2-verdicts.txt"
: > "$GF"; : > "$SUM"
CODE="$(cat "$FENCE/passcode-a")"
URL="$BASE_URL$PUB_A"

{
  echo "=== CELL (ii) rowpeek BEFORE ==="
  "$WT/.venv/bin/python" "$HERE/rowpeek_cells.py"
} | tee "$EV/rowpeek-cell2-before.txt"
cp "$EV/rowpeek-cell2-before.txt" "$MIRROR/" 2>/dev/null || true

BASEARGS=(-H "Accept: $BROWSER_ACCEPT" -H "X-Forwarded-Proto: https" --data-urlencode "code=$CODE")

paced_post "$GF" "cell2-p1 EXPECT-303+grant [declared-base vouch] Host: views.example + Origin: https://views.example" \
  "${BASEARGS[@]}" -H "Host: views.example" -H "Origin: https://views.example" "$URL" | tee -a "$SUM"

paced_post "$GF" "cell2-p2 EXPECT-303+grant [www=apex on vouched anchor] Host: views.example + Origin: https://www.views.example" \
  "${BASEARGS[@]}" -H "Host: views.example" -H "Origin: https://www.views.example" "$URL" | tee -a "$SUM"

paced_post "$GF" "cell2-p3 EXPECT-403+zero-strikes [evil origin, no vouch] Host: views.example + Origin: https://evil.example" \
  "${BASEARGS[@]}" -H "Host: views.example" -H "Origin: https://evil.example" "$URL" | tee -a "$SUM"

paced_post "$GF" "cell2-p4 EXPECT-303+grant [declared front -> XFH leg active, XFH == declared] Host: internal-svc:8079 + XFH: views.example + Origin: https://views.example" \
  "${BASEARGS[@]}" -H "Host: internal-svc:8079" -H "X-Forwarded-Host: views.example" -H "Origin: https://views.example" "$URL" | tee -a "$SUM"

paced_post "$GF" "cell2-p5 EXPECT-403 per commission [XFH != declared creates no vouch] Host: internal-svc:8079 + XFH: evil.example + Origin: https://evil.example" \
  "${BASEARGS[@]}" -H "Host: internal-svc:8079" -H "X-Forwarded-Host: evil.example" -H "Origin: https://evil.example" "$URL" | tee -a "$SUM"

{
  echo "=== CELL (ii) rowpeek AFTER ==="
  "$WT/.venv/bin/python" "$HERE/rowpeek_cells.py"
} | tee "$EV/rowpeek-cell2-after.txt"
cp "$EV/rowpeek-cell2-after.txt" "$MIRROR/" 2>/dev/null || true
cp "$GF" "$MIRROR/" 2>/dev/null || true
echo "== cell (ii) probes complete"
