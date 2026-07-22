#!/usr/bin/env bash
# Test Pack: workspace_guard_unit_test — WorkspaceGuard security tests
# Scope: Path resolution, path traversal, symlinks, null bytes, temp dir bypass, deleted workdirs, ignore patterns, depth limits
# Timeout: 2 minutes (120s)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: workspace_guard_unit_test ==="

cd "$PROJECT_DIR"

timeout 120s .venv/bin/pytest \
  tests/test_workspace_guard.py \
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
