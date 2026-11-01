#!/bin/bash
# usage: run_mutation.sh <name> <file> <python-edit-file> <test-node-id>
set -u
NAME="$1"; TARGET="$2"; EDITPY="$3"; TEST="$4"
WT=/home/nea/ensemble-src-wt-obligation-mut
PY=/home/nea/ensemble-src/.venv/bin/python
cp "$WT/$TARGET" "/tmp/wi1fbee2ec-mut/backup.py"
"$PY" "$EDITPY" || { echo "$NAME: EDIT FAILED"; exit 1; }
cd "$WT"
"$PY" -m pytest "$TEST" -q --no-header > /tmp/wi1fbee2ec-mut/red.log 2>&1
RED_RC=$?
cp "/tmp/wi1fbee2ec-mut/backup.py" "$WT/$TARGET"
"$PY" -m pytest "$TEST" -q --no-header > /tmp/wi1fbee2ec-mut/green.log 2>&1
GREEN_RC=$?
if [ $RED_RC -ne 0 ] && [ $GREEN_RC -eq 0 ]; then
  echo "$NAME: RED proven (rc=$RED_RC) -> restore -> GREEN (rc=$GREEN_RC)  ✅"
else
  echo "$NAME: FAILED PROOF (red_rc=$RED_RC green_rc=$GREEN_RC)  ❌"; tail -3 /tmp/wi1fbee2ec-mut/red.log
fi
