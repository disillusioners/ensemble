#!/usr/bin/env bash
# === Test Pack: fe_jest_full_clipgate ===
# Ad-hoc pack — clipboard-image-chat FINAL MERGE GATE, FE FULL jest census (2026-09-19).
#
# Scope: FULL jest suite from frontend/ exactly as jest resolves it —
#        NO spec selection, NO deselection, NO coverage flags, NO watch mode.
#
# Dual-layer timeout (both layers required):
#   Layer 1 (outer): timeout 300 bash test/packs/fe_jest_full_clipgate_test.sh
#   Layer 2 (inner): timeout -k 10 240 around jest (TERM at 240s, KILL +10s)
#
# Output contract:
#   === Test Pack: fe_jest_full_clipgate ===
#   [jest output tail — summary + failures]
#   RESULT: PASS|FAIL|TIMEOUT        exit 0 / 1 / 124

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
FRONTEND_DIR="$REPO_ROOT/frontend"

INNER_TIMEOUT=240
KILL_AFTER=10
LOG_FILE="${TMPDIR:-/tmp}/fe_jest_full_clipgate_$(date +%Y%m%d_%H%M%S).log"

echo "=== Test Pack: fe_jest_full_clipgate ==="
echo "[pack] repo_root:  $REPO_ROOT"
echo "[pack] workdir:    frontend/"
echo "[pack] command:    npx jest --no-cache   (full census, no filters)"
echo "[pack] timeouts:   inner ${INNER_TIMEOUT}s (kill-after ${KILL_AFTER}s) | outer 300s"
echo "[pack] full log:   $LOG_FILE"

if [ ! -d "$FRONTEND_DIR" ]; then
  echo "[pack] FATAL: frontend/ directory not found at $FRONTEND_DIR"
  echo "RESULT: FAIL"
  exit 1
fi

if [ ! -x "$FRONTEND_DIR/node_modules/.bin/jest" ]; then
  echo "[pack] FATAL: frontend/node_modules/.bin/jest missing (fresh worktree?)."
  echo "[pack] NOT auto-running npm install — outside this pack's mandate."
  echo "RESULT: FAIL"
  exit 1
fi

cd "$FRONTEND_DIR" || { echo "[pack] FATAL: cannot cd frontend/"; echo "RESULT: FAIL"; exit 1; }

# Layer 2 — subprocess-based timeout that actually interrupts a hung run
# (TERM at INNER_TIMEOUT, escalated KILL after KILL_AFTER if still alive).
timeout -k "$KILL_AFTER" "$INNER_TIMEOUT" npx jest --no-cache >"$LOG_FILE" 2>&1
JEST_EXIT=$?

echo "[pack] jest exit code: $JEST_EXIT"
echo "[pack] --- jest output tail (summary + failures) ---"
tail -n 150 "$LOG_FILE"
echo "[pack] --- end jest output tail ---"

case "$JEST_EXIT" in
  0)
    echo "RESULT: PASS"
    exit 0
    ;;
  124)
    echo "[pack] inner ${INNER_TIMEOUT}s cap hit — jest terminated by timeout"
    echo "RESULT: TIMEOUT"
    exit 124
    ;;
  *)
    echo "RESULT: FAIL"
    exit 1
    ;;
esac
