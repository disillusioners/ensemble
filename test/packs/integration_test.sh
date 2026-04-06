#!/usr/bin/env bash
# Test Pack: integration_test
# Scope: tests/integration/ - 69 tests
# Timeout: 5 minutes (300s)

set -euo pipefail

PACK_NAME="integration_test"
TIMEOUT_SECS=300
PROJECT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"

echo "=== Test Pack: $PACK_NAME ==="
echo "Scope: tests/integration/"
echo "Timeout: ${TIMEOUT_SECS}s"
echo ""

cd "$PROJECT_DIR"

RESULT_FILE="/tmp/test_pack_${PACK_NAME}_result.txt"

timeout "${TIMEOUT_SECS}s" python -m pytest tests/integration/ -v --tb=short --no-header 2>&1 | tee "$RESULT_FILE" || EXIT_CODE=$?

EXIT_CODE=${EXIT_CODE:-0}

echo ""
echo "=== Test Pack: $PACK_NAME ==="
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
