#!/usr/bin/env bash
# Stage PREP: fence + boot A (default limits) + mint target.md + rowpeek
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
bash "$HERE/brute-lib.sh" fence-init || exit 99
source "$HERE/brute-lib.sh"   # sourced — dispatcher skipped via BASH_SOURCE guard
fence_load
preboot_asserts || exit 98

EV="$BRUTE_HERE"
boot_daemon "A" "$EV/boot-a.log" || exit 97
curl -sS -o "$EV/a0-livez-initial.txt" -D - --max-time 5 "$BASE_URL/views/livez" | head -1

echo "== minting $VIEW_ROOT_NAME/target.md (service seam — noted)"
MINT_OUT="$(pgsvc mint "$VIEW_ROOT_NAME" target.md)" || { echo "MINT FAILED: $MINT_OUT"; stop_daemon; exit 96; }
echo "$MINT_OUT" | tee "$EV/mint-target.md.txt"
PASSCODE="$(echo "$MINT_OUT" | grep -oE 'passcode=[A-Za-z0-9]{6}' | cut -d= -f2)"
echo "$PASSCODE" > "$FENCE/passcode.txt"
echo "== minting $VIEW_ROOT_NAME/target5.md (A5 dedicated view)"
MINT5_OUT="$(pgsvc mint "$VIEW_ROOT_NAME" target5.md)" || { echo "MINT5 FAILED: $MINT5_OUT"; stop_daemon; exit 96; }
echo "$MINT5_OUT" | tee "$EV/mint-target5.md.txt"
pgsvc rowpeek "$VIEW_ROOT_NAME" target.md | tee "$EV/rowpeek-initial.txt"
echo "== PREP COMPLETE passcode=${PASSCODE:0:1}** (len ${#PASSCODE})"
