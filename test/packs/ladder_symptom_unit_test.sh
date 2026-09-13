#!/usr/bin/env bash
# Test Pack: ladder_symptom_unit_test — P1a durable loop-breaker rung coverage
#
# Tests the hallucination-recovery ladder Phase 1 contract: continuous
# identical toolcalls → durable repair → agent continues differently.
#
# Covering files (exactly 4):
#   1. tests/unit/test_symptom_repair_engine.py        (25 tests)
#   2. tests/unit/test_symptom_repair_ladder.py        (31 tests)
#   3. tests/unit/test_loop_repairer_regression.py     (11 tests)
#   4. tests/test_loop_breaker_integration.py          (20 tests)
#                                                       ----
#                                                       87 tests total
#
# Branch: feature/hallucination-recovery-ladder
# Engine: daemon/services/symptom_repair_engine.py
#   - class SymptomRepairEngine(:202)
#   - SYMPTOM_REPAIR_BUDGET=3 (:84)
#   - REPAIR_DOC_ID_PREFIX="repair-" (:104)
# Wiring: daemon/graph.py
#   - engine instantiate :2059
#   - repair_budget_used field :2919
#   - [SYMPTOM] telemetry emit :1771-1823
# Kill-switches: ENSEMBLE_SYMPTOM_REPAIR_LADDER / ENSEMBLE_REPAIR_LOOP_DURABLE
#                default ON (resolvers in daemon/config.py)
#
# Script-internal timeout (Layer 2): 120s
# Command-level timeout (Layer 1):  300s
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: ladder_symptom_unit_test ==="
echo "Branch: $(git -C "$PROJECT_DIR" rev-parse --abbrev-ref HEAD) @ $(git -C "$PROJECT_DIR" rev-parse --short HEAD)"
echo ""

cd "$PROJECT_DIR"

CANDIDATE_FILES=(
  "tests/unit/test_symptom_repair_engine.py"
  "tests/unit/test_symptom_repair_ladder.py"
  "tests/unit/test_loop_repairer_regression.py"
  "tests/test_loop_breaker_integration.py"
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

echo ""
echo "=== Running tests (dual-layer: 120s internal + 300s outer) ==="
timeout 300s uv run python -m pytest \
  "${EXISTING_FILES[@]}" \
  --override-ini="addopts=" --tb=short -q \
  --timeout=120 \
  2>&1

EXIT_CODE=$?

echo ""
echo "=== Post-run drift check ==="
POST_BRANCH=$(git -C "$PROJECT_DIR" rev-parse --abbrev-ref HEAD)
POST_COMMIT=$(git -C "$PROJECT_DIR" rev-parse --short HEAD)
echo "Post-run branch: $POST_BRANCH"
echo "Post-run commit: $POST_COMMIT"

if [ $EXIT_CODE -eq 0 ]; then
  echo "RESULT: PASS"
  exit 0
elif [ $EXIT_CODE -eq 124 ]; then
  echo "RESULT: TIMEOUT"
  exit 124
else
  echo "RESULT: FAIL"
  exit 1
fi
