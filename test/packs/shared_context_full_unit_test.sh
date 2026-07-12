#!/usr/bin/env bash
# Test Pack: shared_context_full_unit_test — ALL Shared Context unit tests
# Timeout: 3 minutes (180s)
#
# Runs every shared_context unit test:
#   - tests/unit/test_shared_context_metadata_repo.py   (23 tests)
#   - tests/unit/test_shared_context_injection.py       (14 tests)
#   - tests/unit/test_shared_context_tool.py            ( 9 tests)
#   - tests/unit/test_shared_context_concurrency.py     ( 3 tests, NEW)
#   - tests/unit/test_shared_context_prompt_injection.py (3 tests, NEW)
#
# The 3-minute cap covers the extra concurrent + prompt-injection
# coverage on top of the original 2-minute unit pack.
#
# Uses .venv/bin/pytest because the system pytest in /opt/homebrew/bin
# is broken on this host. The project venv (Python 3.13.3, pytest 9.0.2)
# works correctly.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: shared_context_full_unit_test ==="

cd "$PROJECT_DIR"

# Run with timeout - kill if hangs. 180s covers 46 original + 6 new tests.
timeout 180s .venv/bin/pytest \
  tests/unit/test_shared_context_metadata_repo.py \
  tests/unit/test_shared_context_injection.py \
  tests/unit/test_shared_context_tool.py \
  tests/unit/test_shared_context_concurrency.py \
  tests/unit/test_shared_context_prompt_injection.py \
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