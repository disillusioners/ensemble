#!/usr/bin/env bash
# Test Pack: restore_preserve_version_tag_unit_test
# Tests: tests/unit/test_restore_preserve_version_tag.py
# Timeout: 2 minutes (120s)
#
# Covers S5 — Restore preserves original agent_tag:
#   - Restore fallback: versioned dir missing → original tag saved in instance_metadata
#   - Successful restore: original_agent_tag cleared from instance_metadata
#   - set_metadata/delete_metadata are atomic (no race)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: restore_preserve_version_tag_unit_test ==="

cd "$PROJECT_DIR"

timeout 120s .venv/bin/pytest \
  tests/unit/test_restore_preserve_version_tag.py \
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
