#!/usr/bin/env bash
# Test Pack: council_tools_unit_test
# Tests: tests/test_council_tools.py
# Timeout: 2 minutes (120s)
#
# Covers governor council tools: spawn_councilor, clear_councilor_errors,
# convene_council, convene_council_with_skill.
# F6 fix: convene_council and convene_council_with_skill resolve governor
# default version via _resolve_default_version_tag().
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: council_tools_unit_test ==="

cd "$PROJECT_DIR"

timeout 120s .venv/bin/pytest \
  tests/test_council_tools.py \
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
