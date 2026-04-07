#!/usr/bin/env bash
# Test Pack: sources_unit_test — Sources subsystem unit tests
# Timeout: 2 minutes (120s)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: sources_unit_test ==="

cd "$PROJECT_DIR"

timeout 120s pytest \
  tests/test_sources_circuit_breaker.py \
  tests/test_sources_dispatcher.py \
  tests/test_sources_mapper.py \
  tests/test_sources_persistence.py \
  tests/test_sources_rate_limiter.py \
  tests/test_sources_registry.py \
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
