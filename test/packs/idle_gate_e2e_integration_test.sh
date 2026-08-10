#!/usr/bin/env bash
# Test Pack: idle_gate_e2e_integration_test — E2E integration for idle-gate deadlock fix
# Timeout: 2 minutes (120s)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: idle_gate_e2e_integration_test ==="

cd "$PROJECT_DIR"

timeout 120s .venv/bin/pytest \
  tests/job_queue/test_idle_gate_e2e_integration.py \
  --tb=short -q 2>&1

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
