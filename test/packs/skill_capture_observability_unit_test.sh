#!/usr/bin/env bash
# Test Pack: skill_capture_observability_unit_test — capture-lane
# observability (skill-system-revival Phase 1, workstream D).
#
# Registered 2026-11-09 (see .agents/tester/PACKS.md).
#
# Scope: the D pins — M-D1 (every refusal site increments ITS counter),
# M-D2 (the DAEMON announces a keeper deviation), M-D3 (the new
# observability fields carry a SHAPE, never the raw prompt — the F7
# credential lesson) and M-D4 (the admission-refusal and
# execution-refusal signals are NEVER merged).
#
# Timeout: 2 minutes (120s).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: skill_capture_observability_unit_test ==="

cd "$PROJECT_DIR"

# RESULT-echo discipline: `|| EXIT_CODE=$?` puts the assignment in a
# list-context, so `set -e` does not abort before the RESULT line.
EXIT_CODE=0

timeout 120s .venv/bin/pytest \
  tests/services/test_skill_capture_observability.py \
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
