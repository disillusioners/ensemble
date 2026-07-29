#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
echo "=== Test Pack: initiative_message_pg_test ==="
cd "$PROJECT_DIR"

# Postgres pack — 5 min hard limit. Dual-layer timeout.
# Requires -m postgres marker (conftest auto-applies via pytest_collection_modifyitems).
timeout 300s .venv/bin/pytest \
  tests/postgres/test_initiative_message_pg.py \
  -m postgres --override-ini="addopts=" -v --tb=short -q 2>&1
EXIT_CODE=$?
if [ $EXIT_CODE -eq 124 ]; then
  echo "RESULT: TIMEOUT"
  exit 124
elif [ $EXIT_CODE -eq 0 ]; then
  echo "RESULT: PASS"
  exit 0
else
  echo "RESULT: FAIL"
  exit 1
fi
