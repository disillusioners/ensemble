#!/usr/bin/env bash
# Test Pack: version_tag_tool_resolution_unit_test
# Tests: tests/unit/tools/test_version_tag_tool_resolution.py
# Timeout: 2 minutes (120s)
#
# Covers the version-tag aware tool resolution fix (C1):
# create_instance_tools(), _apply_tool_filter(), _check_team_membership(),
# and load_tools_doc_for_agent() now accept version_tag and use
# registry.get_version() or get_resolved() fallback.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: version_tag_tool_resolution_unit_test ==="

cd "$PROJECT_DIR"

timeout 120s .venv/bin/pytest \
  tests/unit/tools/test_version_tag_tool_resolution.py \
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
