#!/usr/bin/env bash
# Test Pack: integration_test — Integration tests (require OPENAI_API_KEY)
# Timeout: 5 minutes (300s)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: integration_test ==="

cd "$PROJECT_DIR"

timeout 300s .venv/bin/python -m pytest \
  tests/integration/ \
  --override-ini="addopts=" \
  -m integration \
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
