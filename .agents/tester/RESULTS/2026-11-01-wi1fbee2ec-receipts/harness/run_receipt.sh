#!/bin/bash
# wi-1fbee2ec mutation-verification harness (review cycle 1, item 4):
# per-leg receipt files — nothing depends on /tmp overwrite order.
# usage: run_receipt.sh <leg-name> <target-file> <edit-script> <test-node-id>
set -u
LEG="$1"; TARGET="$2"; EDITPY="$3"; TEST="$4"
WT=/home/nea/ensemble-src-wt-obligation-mut
PY=/home/nea/ensemble-src/.venv/bin/python
RDIR="/tmp/wi1fbee2ec-receipts/$LEG"
mkdir -p "$RDIR"
date -u +"%Y-%m-%dT%H:%M:%SZ" > "$RDIR/started_at.txt"
cp "$WT/$TARGET" "$RDIR/original.$(basename $TARGET)"

# Plant the defect.
if ! "$PY" "$EDITPY" > "$RDIR/edit.log" 2>&1; then
  echo "$LEG: EDIT FAILED" | tee "$RDIR/verdict.txt"
  exit 1
fi
# Receipt: the planted diff.
diff -u "$RDIR/original.$(basename $TARGET)" "$WT/$TARGET" > "$RDIR/planted-mutation.diff" || true

cd "$WT"
"$PY" -m pytest "$TEST" -q --no-header --timeout=300 > "$RDIR/red.log" 2>&1
RED_RC=$?

# Restore + prove GREEN.
cp "$RDIR/original.$(basename $TARGET)" "$WT/$TARGET"
"$PY" -m pytest "$TEST" -q --no-header --timeout=300 > "$RDIR/green.log" 2>&1
GREEN_RC=$?
date -u +"%Y-%m-%dT%H:%M:%SZ" > "$RDIR/finished_at.txt"

if [ $RED_RC -ne 0 ] && [ $GREEN_RC -eq 0 ]; then
  VERDICT="RED proven (rc=$RED_RC) -> restore -> GREEN (rc=$GREEN_RC)"
  STATUS="PASS"
else
  VERDICT="FAILED PROOF (red_rc=$RED_RC green_rc=$GREEN_RC)"
  STATUS="FAIL"
fi
printf '%s  %s\n' "$STATUS" "$VERDICT" | tee "$RDIR/verdict.txt"
[ "$STATUS" = "PASS" ]
