#!/usr/bin/env bash
# Test Pack: image_regression_test — Regression tests for image-reader feature impact
# Timeout: 2 minutes (120s)
# Internal timer: 110s (leaves 10s buffer for cleanup)
#
# Background: A new image-reader agent and explain_image tool were implemented on
# branch feature/image-reader-agent. This pack exercises tests most likely to be
# affected by:
#   1. New "image" category registration in daemon/tools/_tool_registry.py
#   2. Changes to daemon/instance.py (tool creation wiring for image tools)
#   3. invoke_agent_and_wait signature change (added `images` parameter)
#   4. 11 agent meta.json files updated (added "image" to tools.allow)
#
# Tests excluded: tests/test_image_tools.py — handled by a separate pack.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

INTERNAL_TIMEOUT=110
PACK_NAME="image_regression_test"

echo "=== Test Pack: ${PACK_NAME} ==="

cd "$PROJECT_DIR"

# NOTE: Use --override-ini="addopts=" to clear the default
#       "-m 'not integration and not postgres'" filter so we can run tests
#       that might be marked otherwise. Re-apply -m "not integration and not
#       postgres" to keep the pack scoped to unit tests.
timeout ${INTERNAL_TIMEOUT}s .venv/bin/pytest \
  tests/test_chart_tools.py \
  tests/unit/services/test_invoked_as_tool.py \
  tests/services/test_skill_phase2_integration.py \
  tests/test_tool_filter.py \
  tests/test_help_tool.py \
  --tb=short -q \
  --override-ini="addopts=" \
  -m "not integration and not postgres" \
  2>&1
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