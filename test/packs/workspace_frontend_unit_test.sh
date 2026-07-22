#!/usr/bin/env bash
# Test Pack: workspace_frontend_unit_test — Frontend workspace Jest tests
# Scope: Workspace page, workspace service, code-viewer, codemirror directive, diff-viewer, file-tree
# Timeout: 5 minutes (300s)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: workspace_frontend_unit_test ==="

cd "$PROJECT_DIR/frontend"

timeout 300s npx jest \
  --testPathPatterns="workspace|codemirror|code-viewer|diff-viewer|file-tree" \
  --no-coverage 2>&1

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
