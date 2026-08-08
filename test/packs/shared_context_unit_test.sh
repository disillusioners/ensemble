#!/usr/bin/env bash
# Test Pack: shared_context_unit_test — Shared Context Metadata KV unit tests
# Timeout: 2 minutes (120s)
#
# Runs the 46 existing unit tests for the shared_context_metadata system:
#   - tests/unit/test_shared_context_metadata_repo.py  (23 tests)
#   - tests/unit/test_shared_context_injection.py      (14 tests)
#   - tests/unit/test_shared_context_tool.py           ( 9 tests)
#
# Uses .venv/bin/pytest because the system pytest in /opt/homebrew/bin
# is broken on this host (ImportError on _pytest.config._console_main).
# The project venv (Python 3.13.3, pytest 9.0.2) works correctly.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: shared_context_unit_test ==="

cd "$PROJECT_DIR"

# Run with timeout - kill if hangs. 120s is the unit-test hard cap.
# NOTE: test_shared_context_injection.py was deleted in eeef8845.
timeout 120s .venv/bin/pytest \
  tests/unit/test_shared_context_metadata_repo.py \
  tests/unit/services/test_context_injection.py \
  tests/unit/test_shared_context_tool.py \
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