#!/usr/bin/env bash
# Test Pack: registry_validation_unit_test
# Tests: tests/test_registry.py + tests/test_tools.py
# Timeout: 2 minutes (120s)
#
# Registry + tool validator regression for commit 4f326f8d:
# AgentRegistry discovery/validation behavior and the tool modules the
# validator consumes. daemon/registry.py validation logic was untouched
# by the fix — this pack pins that fact.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: registry_validation_unit_test ==="

cd "$PROJECT_DIR"

# Unit pack — 2 min hard limit. Dual-layer timeout.
# Layer 2 (script-internal): 110s — interrupts hung tests
# Layer 1 (command-level): 120s via `timeout` wrapper below
EXIT_CODE=0
timeout 110s .venv/bin/pytest \
  tests/test_registry.py \
  tests/test_tools.py \
  --tb=short -q 2>&1 || EXIT_CODE=$?

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
