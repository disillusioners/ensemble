#!/usr/bin/env bash
# Test Pack: services_orchestration_regression_test — original services tests
# Timeout: 2 minutes (120s)
#
# Regression pack for the original services orchestration tests:
#   - tests/services/test_instance_lifecycle_h10_l14.py   (lifecycle hooks H10/L14)
#   - tests/services/test_instance_lifecycle_terminate.py (terminate path)
#   - tests/services/test_context_usage_emission.py       (context-usage emission)
#
# Deliberately EXCLUDES the newer skill and shared-context injection
# tests (covered by ``instance_messaging_regression_test.sh``) so this
# pack stays focused on the orchestration primitives it owns. Running
# the original three together gives a fast smoke gate for the
# lifecycle/terminate/emission surface while leaving hook-level
# injection coverage to the dedicated pack.
#
# Uses .venv/bin/pytest because the system pytest in /opt/homebrew/bin
# is broken on this host. The project venv (Python 3.13.3, pytest 9.0.2)
# works correctly.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: services_orchestration_regression_test ==="

cd "$PROJECT_DIR"

# Run with timeout - kill if hangs. 120s is the services-test hard cap.
timeout 120s .venv/bin/pytest \
  tests/services/test_instance_lifecycle_h10_l14.py \
  tests/services/test_instance_lifecycle_terminate.py \
  tests/services/test_context_usage_emission.py \
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
