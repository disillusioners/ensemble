#!/usr/bin/env bash
# Test Pack: tools_suite_unit_test
# Tests: tests/unit/tools/ (entire directory)
# Timeout: 2 minutes (120s)
#
# Regression sweep across the whole tools unit suite, anchoring the
# frozen tool-name discovery fix (commit 4f326f8d) against sibling
# tool tests (registry resolution, spawn defaults, system/context/
# rag/critical-notes tools, inner-soul suites, ...).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: tools_suite_unit_test ==="

cd "$PROJECT_DIR"

# Unit pack — 2 min hard limit. Dual-layer timeout.
# Layer 2 (script-internal): 110s — interrupts hung tests
# Layer 1 (command-level): 120s via `timeout` wrapper below
EXIT_CODE=0
timeout 110s .venv/bin/pytest \
  tests/unit/tools/ \
  --tb=short -q 2>&1 || EXIT_CODE=$?

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
