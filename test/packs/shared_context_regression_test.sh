#!/usr/bin/env bash
# Test Pack: shared_context_regression_test — Core daemon regression gate
# Timeout: 5 minutes (300s) outer guard, delegates to core_unit_test.sh
#
# Runs the existing 673-test core unit pack (tools, agents, persistence,
# queue, registry) to validate that introducing the shared_meta_kv
# feature did not regress any core daemon behavior.
#
# This is a SEPARATE pack from shared_context_unit_test.sh so each
# pack stays under its own 5-min cap. No additional pytest calls
# happen inside this script — ``core_unit_test.sh`` owns them all.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: shared_context_regression_test ==="

cd "$PROJECT_DIR"

# Outer timeout guard: core_unit_test.sh already has its own 120s
# internal timeout, but this 300s guard catches the case where the
# inner pack script never executes at all (e.g. permission issue).
timeout 300s bash test/packs/core_unit_test.sh

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