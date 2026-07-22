#!/usr/bin/env bash
# Test Pack: workspace_api_integration_test — Workspace API + SSE integration tests
# Scope: GET /tree, GET /file, GET /diff, GET /events (SSE), error handling, security integration
# Timeout: 5 minutes (300s)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: workspace_api_integration_test ==="

cd "$PROJECT_DIR"

timeout 300s .venv/bin/pytest \
  tests/test_workspace_api.py \
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
