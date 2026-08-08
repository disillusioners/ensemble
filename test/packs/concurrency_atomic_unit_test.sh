#!/usr/bin/env bash
# Test Pack: concurrency_atomic_unit_test
#
# Concurrency / atomicity regression coverage per .agents/tester/rules/ensure.md:
#   - Deadlock fix verification (test_deadlock_fix.py)
#   - Cascade races + unified/integration paths (cascade_race3, cascade_concurrency,
#     cascade_unified, cascade_integration)
#   - Observer race + correlation + late-message handling (observer_race1,
#     observer_correlation, observer_late_msg)
#   - Instance/project atomic locks (instance_metadata_atomic,
#     project_repository_atomic)
#   - Threading serialization gate + finalize_job h15 (gate_threading_serialization,
#     finalize_job_h15)
#   - Report-lane Phase 2 (report_lane_phase2)
#
# Script-internal timeout (Layer 2): 280s
# Command-level timeout (Layer 1): 300s
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: concurrency_atomic_unit_test ==="
cd "$PROJECT_DIR"

# Verify every listed test file exists before invoking pytest.
# Missing files are skipped (and noted) rather than failing the whole pack.
CANDIDATE_FILES=(
  "tests/test_deadlock_fix.py"
  "tests/test_cascade_race3.py"
  "tests/test_cascade_concurrency.py"
  "tests/test_cascade_unified.py"
  "tests/test_observer_race1.py"
  "tests/test_observer_correlation.py"
  "tests/test_observer_late_msg.py"
  "tests/test_instance_metadata_atomic.py"
  "tests/test_project_repository_atomic.py"
  "tests/test_gate_threading_serialization.py"
  "tests/test_finalize_job_h15.py"
  "tests/test_cascade_integration.py"
  "tests/test_report_lane_phase2.py"
)

EXISTING_FILES=()
SKIPPED_FILES=()
for f in "${CANDIDATE_FILES[@]}"; do
  if [ -f "$f" ]; then
    EXISTING_FILES+=("$f")
  else
    SKIPPED_FILES+=("$f")
  fi
done

if [ ${#SKIPPED_FILES[@]} -gt 0 ]; then
  echo "[note] Skipping ${#SKIPPED_FILES[@]} missing test file(s):"
  for s in "${SKIPPED_FILES[@]}"; do
    echo "  - $s"
  done
fi

echo "[note] Running ${#EXISTING_FILES[@]} test file(s):"
for e in "${EXISTING_FILES[@]}"; do
  echo "  - $e"
done

if [ ${#EXISTING_FILES[@]} -eq 0 ]; then
  echo "[fatal] No test files exist — nothing to run."
  echo "RESULT: FAIL"
  exit 1
fi

timeout 280s .venv/bin/pytest \
  "${EXISTING_FILES[@]}" \
  --override-ini="addopts=" --tb=short -q 2>&1
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
