#!/usr/bin/env bash
# Test Pack: skill_capture_killswitch_unit_test — ENSEMBLE_SKILL_CAPTURE_ENABLED kill-switch suite + ON-pin regression set
#
# Purpose: Validate branch `feature/disable-skill-capture` @ 54a34199:
#   - tests/unit/test_skill_capture_killswitch.py — the NEW dedicated kill-switch suite
#     (default-OFF short-circuit semantics across the capture gates).
#   - The 4 pre-existing ON-pin suites that carry autouse fixtures
#     (`_enable_skill_capture_killswitch` → setenv("ENSEMBLE_SKILL_CAPTURE_ENABLED", "1"))
#     pinning the flag ON so legacy capture-flow expectations keep holding.
#
# File set (grep -rl ENSEMBLE_SKILL_CAPTURE_ENABLED tests/ --include='*.py' | sort -u, deduped):
#   tests/unit/test_skill_capture_killswitch.py         (NEW kill-switch suite)
#   tests/integration/test_skill_capture.py             (ON-pin: class-scoped autouse ×2)
#   tests/integration/test_skill_cross_phase_flow_c.py  (ON-pin: module-level autouse)
#   tests/services/test_skill_job_dispatcher.py         (ON-pin: class-scoped autouse ×2)
#   tests/tools/test_skill_evolution_tools.py           (ON-pin: module-level autouse)
#
# NOTE on addopts: repo default addopts = `-m 'not integration and not postgres'`
# would silently deselect the two integration-marked ON-pin files. This pack
# overrides addopts (precedent: skill_captured_regression_unit_test, PACKS.md
# row: mixed unit+integration pack) so ALL 5 files execute. Verified 0
# postgres-marked tests in the set → no live PG required.
#
# Expected test count: 95 (26 + 12 + 12 + 22 + 23)
# Timeout: internal 240s (this script); caller applies outer `timeout 300`.
set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: skill_capture_killswitch_unit_test ==="

cd "$PROJECT_DIR"

EXIT_CODE=0
timeout 240 uv run python -m pytest \
  tests/unit/test_skill_capture_killswitch.py \
  tests/integration/test_skill_capture.py \
  tests/integration/test_skill_cross_phase_flow_c.py \
  tests/services/test_skill_job_dispatcher.py \
  tests/tools/test_skill_evolution_tools.py \
  -q --tb=short --override-ini="addopts=" || EXIT_CODE=$?

if [ "$EXIT_CODE" -eq 124 ]; then
  echo "RESULT: TIMEOUT"
  exit 124
elif [ "$EXIT_CODE" -eq 0 ]; then
  echo "RESULT: PASS"
  exit 0
else
  echo "RESULT: FAIL"
  exit 1
fi
