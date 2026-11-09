#!/usr/bin/env bash
# CELL (iv) PROBES — unconfigured NON-LOOPBACK peer. Daemon bound to the
# LAN IP only; curl connects to http://<LAN_IP>:8083 so the TCP peer is
# non-loopback. Degradation contract: real-Origin POST with correct code
# => 403 AND failed_attempts stays 0 (origin gate precedes compare);
# absent/null Origin => 303 + grant preserved (R2 fail-open semantics).
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/lib.sh"
load_env
set -a; source "$FENCE/state.env"; set +a

LAN_IP="$(sed "s/^CELL4_LAN_IP=//" "$EV/.cell4-ip")"
LANURL="http://${LAN_IP}:${PORT}"
GF="$EV/cell4-transcript.txt"
SUM="$EV/cell4-verdicts.txt"
: > "$GF"; : > "$SUM"
CODE="$(cat "$FENCE/passcode-a")"

{
  echo "=== CELL (iv) rowpeek BEFORE ==="
  "$WT/.venv/bin/python" "$HERE/rowpeek_cells.py"
} | tee "$EV/rowpeek-cell4-before.txt"
cp "$EV/rowpeek-cell4-before.txt" "$MIRROR/" 2>/dev/null || true

BASEARGS=(-H "Accept: $BROWSER_ACCEPT" -H "X-Forwarded-Proto: https" --data-urlencode "code=$CODE")

paced_post "$GF" "cell4-p1 EXPECT-403+zero-strikes [non-loopback peer, no vouch universe: real Origin rejected] Host: ${LAN_IP}:${PORT} + Origin: https://${LAN_IP}:${PORT}" \
  "${BASEARGS[@]}" -H "Host: ${LAN_IP}:${PORT}" -H "Origin: https://${LAN_IP}:${PORT}" "$LANURL$PUB_A" | tee -a "$SUM"

paced_post "$GF" "cell4-p2 EXPECT-303+grant [DEGRADATION ALLOWANCE: absent Origin => fail-open R2] Host: ${LAN_IP}:${PORT}, no Origin" \
  "${BASEARGS[@]}" -H "Host: ${LAN_IP}:${PORT}" "$LANURL$PUB_A" | tee -a "$SUM"

paced_post "$GF" "cell4-p3 EXPECT-303+grant [DEGRADATION ALLOWANCE: Origin: null (no Sec-Fetch-Site) => fail-open R2] Host: ${LAN_IP}:${PORT} + Origin: null" \
  "${BASEARGS[@]}" -H "Host: ${LAN_IP}:${PORT}" -H "Origin: null" "$LANURL$PUB_A" | tee -a "$SUM"

{
  echo "=== CELL (iv) rowpeek AFTER — p1's 403 must NOT have counted a strike ==="
  "$WT/.venv/bin/python" "$HERE/rowpeek_cells.py"
} | tee "$EV/rowpeek-cell4-after.txt"
cp "$EV/rowpeek-cell4-after.txt" "$MIRROR/" 2>/dev/null || true
cp "$GF" "$MIRROR/" 2>/dev/null || true
echo "== cell (iv) probes complete"
