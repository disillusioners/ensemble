#!/usr/bin/env bash
# Test Pack: c2_core_regression_unit_test
# Timeout: 5 minutes (300s)
#
# Tier 3 core regression pack for C2 fix verification: covers manager,
# paused-instance TTL, context-usage emission, dispatcher path
# equivalence, phase-4 manager decomposition, and title-generation trigger.
#
# Uses .venv/bin/pytest because the system pytest in /opt/homebrew/bin
# is broken on this host. The project venv (Python 3.13.3, pytest 9.0.2)
# works correctly.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: c2_core_regression_unit_test ==="

cd "$PROJECT_DIR"

# Run with timeout - kill if hangs. 300s is the command-level hard cap.
timeout 300s .venv/bin/pytest \
  tests/test_manager.py \
  tests/unit/test_paused_instance_ttl.py \
  tests/services/test_context_usage_emission.py \
  tests/test_dispatcher_path_equivalence.py \
  tests/unit/test_phase4_manager_decomposition.py \
  tests/unit/services/test_title_generation_trigger.py \
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
