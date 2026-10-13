#!/usr/bin/env bash
# Test Pack: opencode_native_tools_unit_test — OpenCode native tools unit tests
# Timeout: 3 minutes (180s)
# Excludes tests/opencode/test_integration.py (requires live server, marked @pytest.mark.integration)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: opencode_native_tools_unit_test ==="

cd "$PROJECT_DIR"

# Run all unit tests in tests/opencode/ except integration tests.
# Internal timeout: 180s (under 5-min cap).
timeout 180s .venv/bin/python -m pytest tests/opencode/ --ignore=tests/opencode/test_integration.py -x --tb=short -q 2>&1

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