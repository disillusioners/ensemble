#!/usr/bin/env bash
# Test Pack: ladder_durable_sqlite_test
# Ladder Phase-1b THE RESTART SCENARIO on a REAL file-backed AsyncSqliteSaver:
# durable repair survives checkpoint restore/revive.
#   - T-3 canary mirror: return-carried repair lands in the task commit
#   - T-2 durability: restart/revive (fresh graph over the same saver) —
#     no re-trip, durable budget, exactly one repair doc
#   - T-2b revive hardening: TRUE restart (new connection + new saver on the
#     same file) — stripped degenerate block NOT replayed, ORIGINAL-id
#     evidence tail retained, explicit detector walk over the restored tail
#     finds no live loop
#
# Dual-layer timeout: inner 180s (pack-internal hard ceiling) + outer 300s
# (command-level cap). Run ONLY this pack; do not run any other pack or suite.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: ladder_durable_sqlite_test ==="

cd "$PROJECT_DIR"

# Pack-internal timer (Layer 2). SIGTERM the pytest tree at 180s so the
# outer 300s cap is a true backstop, not the only line of defense.
( sleep 180; kill -TERM -$$ 2>/dev/null ) &
INTERNAL_WATCHDOG_PID=$!

cleanup_internal() {
  if kill -0 "$INTERNAL_WATCHDOG_PID" 2>/dev/null; then
    kill "$INTERNAL_WATCHDOG_PID" 2>/dev/null || true
  fi
}
trap cleanup_internal EXIT

# Layer 1: command-level cap. Tests ONLY via `uv run python -m pytest`
# from the worktree root.
timeout 300s uv run python -m pytest \
  tests/unit/test_symptom_repair_mid_superstep_canary.py \
  --tb=short -q -rs \
  --override-ini="addopts=" \
  2>&1

EXIT_CODE=$?

cleanup_internal

if [ $EXIT_CODE -eq 0 ]; then
  echo "RESULT: PASS"
  exit 0
elif [ $EXIT_CODE -eq 124 ]; then
  echo "RESULT: TIMEOUT"
  exit 124
else
  echo "RESULT: FAIL"
  exit 1
fi
