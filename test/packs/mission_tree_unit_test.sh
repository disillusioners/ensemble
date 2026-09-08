#!/usr/bin/env bash
# Test Pack: mission_tree_unit_test — Job-queue mission-tree verification gate
# on `feature/job-queue-mission-tree` @ 708ee7a5.
#
# Included files (NEW pack — covers the affected test files NOT already covered
# by sibling packs missions_api_unit_test / mission_resolver_unit_test /
# jobs_streaming_resolver_unit_test / m2_missions_runtime_contract_integration /
# mission_final_vocab_runtime_integration):
#   tests/unit/routers/test_jobs_mission_id_filter.py               21 (feature's own new GET /api/jobs?mission_id= filter suite)
#   tests/unit/services/test_work_resolver_query_budget.py           6 (query-budget pins)
#   tests/unit/test_phase5_jobs_router.py                          34 (jobs router structure; historical baseline 34 passed)
#   tests/unit/routers/test_jobs_cleanup_endpoint.py                43 (jobs_crud-adjacent)
# Pack total: 104 tests.
#
# Unit pack — 2 min hard limit. Dual-layer timeout.
# Layer 2 (script-internal): 110s global deadline across ALL files — interrupts hung
#   tests, kills the run, prints RESULT: TIMEOUT, exit 124.
# Layer 1 (command-level): caller wraps with `timeout 300`.
# RESULT-echo: `|| EXIT_CODE=$?` list-context capture — under `set -e`, a bare
# `EXIT_CODE=$?` after a failing command never executes (silent exit, no RESULT).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
echo "=== Test Pack: mission_tree_unit_test [$(git rev-parse --abbrev-ref HEAD) @ $(git rev-parse --short HEAD)] ==="
cd "$PROJECT_DIR"

FILES=(
  tests/unit/routers/test_jobs_mission_id_filter.py
  tests/unit/services/test_work_resolver_query_budget.py
  tests/unit/test_phase5_jobs_router.py
  tests/unit/routers/test_jobs_cleanup_endpoint.py
)

# Branch-drift guard (sibling-pack pattern: rev-parse bracket echo + optional EXPECTED_BRANCH).
ACTUAL_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
ACTUAL_COMMIT="$(git rev-parse --short HEAD)"
EXPECTED_BRANCH="feature/job-queue-mission-tree"
EXPECTED_COMMIT="708ee7a5"
if [[ "${ACTUAL_BRANCH}" != "${EXPECTED_BRANCH}" || "${ACTUAL_COMMIT}" != "${EXPECTED_COMMIT}" ]]; then
  echo "RESULT: DRIFT (expected ${EXPECTED_BRANCH} @ ${EXPECTED_COMMIT}, got ${ACTUAL_BRANCH} @ ${ACTUAL_COMMIT})"
  exit 1
fi
echo "RESULT: DRIFT-CHECK (got ${ACTUAL_BRANCH} @ ${ACTUAL_COMMIT})"

# Collect/dry-run mode: `mission_tree_unit_test.sh --collect`
# collects and prints per-file test counts; NEVER executes tests.
if [[ "${1:-}" == "--collect" ]]; then
  COLLECT_EXIT=0
  .venv/bin/pytest --collect-only -q "${FILES[@]}" || COLLECT_EXIT=$?
  if [ "$COLLECT_EXIT" -eq 0 ]; then
    echo "RESULT: PASS (collect-only)"
  else
    echo "RESULT: FAIL (collect-only)"
  fi
  exit "$COLLECT_EXIT"
fi

# Unit pack — 110s internal hard limit across ALL files (global deadline).
INTERNAL_LIMIT=110
START=$SECONDS
FAILURES=0
echo "--- per-file results (pass/fail counts in the pytest tail above each marker) ---"
for f in "${FILES[@]}"; do
  REMAINING=$(( INTERNAL_LIMIT - (SECONDS - START) ))
  if [ "$REMAINING" -le 0 ]; then
    echo "RESULT: TIMEOUT"
    exit 124
  fi
  FILE_EXIT=0
  timeout "${REMAINING}s" .venv/bin/pytest "$f" --tb=short -q -rf || FILE_EXIT=$?
  if [ "$FILE_EXIT" -eq 124 ]; then
    echo "RESULT: TIMEOUT"
    exit 124
  elif [ "$FILE_EXIT" -ne 0 ]; then
    echo ">>> FAIL: $f (exit $FILE_EXIT)"
    FAILURES=$((FAILURES + 1))
  else
    echo ">>> PASS: $f"
  fi
done

echo "--- pack tally: ${FAILURES} of ${#FILES[@]} file(s) failed ---"
if [ "$FAILURES" -eq 0 ]; then
  echo "RESULT: PASS"
  exit 0
else
  echo "RESULT: FAIL"
  exit 1
fi
