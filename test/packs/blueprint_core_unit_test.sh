#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
echo "=== Test Pack: blueprint_core_unit_test ==="
cd "$PROJECT_DIR"

# Unit pack — 2 min hard limit. Dual-layer timeout.
# Layer 2 (script-internal): 110s — interrupts hung tests
# Layer 1 (command-level): 120s via `timeout` wrapper below
timeout 110s .venv/bin/pytest \
  tests/unit/test_blueprint_repository.py \
  tests/unit/test_blueprint_matcher.py \
  tests/unit/test_blueprint_rate_limiter.py \
  -v --override-ini="addopts=" --tb=short -q 2>&1
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
