#!/usr/bin/env bash
# Test Pack: question_validation_targeted_unit_test — ask_questions
# input-format hardening acceptance (feature/ask-questions-format-validation @ 5e4e33b9).
#
# Included files (question-primary, unit-type; counts are ground truth from
# `.venv/bin/pytest --collect-only -q` at 5e4e33b9):
#   tests/test_question_tools_validation.py                     48  (the feature's own suite)
#   tests/test_question_tools.py                                 4
#   tests/test_question_manager.py                              17
#   tests/test_question_api.py                                   4
#   tests/test_question_dismiss.py                              15  (dismiss endpoint — ask_questions surface)
#   tests/test_question_untested_paths.py                        9  (answer endpoint + cleanup — surface)
#   tests/unit/test_question_graph.py                           10  (question_pause_node graph wiring)
#   tests/unit/test_question_deferred_pause_callback.py          6
#   tests/unit/test_question_deferred_pause_edge_cases.py        5
#   tests/unit/services/test_question_pause_completion_guard.py  8
# Pack total: 126 tests.
#
# Discovered-but-EXCLUDED (scope rule: primary subject must be the ask_questions surface):
#   tests/unit/repositories/test_message_metadata_paused_question_flow.py (3) — primary subject
#     is message-metadata created_at persistence; the paused-question flow is only the vehicle.
#   tests/e2e/test_answer_dismiss_flow.py (3) — question-surface but e2e-type, not unit; also
#     carries a QUARANTINE.md node (M2-gate 12-node row 2026-09-03, T0-mirror timing ×1).
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
echo "=== Test Pack: question_validation_targeted_unit_test [$(git rev-parse --abbrev-ref HEAD) @ $(git rev-parse --short HEAD)] ==="
cd "$PROJECT_DIR"

FILES=(
  tests/test_question_tools_validation.py
  tests/test_question_tools.py
  tests/test_question_manager.py
  tests/test_question_api.py
  tests/test_question_dismiss.py
  tests/test_question_untested_paths.py
  tests/unit/test_question_graph.py
  tests/unit/test_question_deferred_pause_callback.py
  tests/unit/test_question_deferred_pause_edge_cases.py
  tests/unit/services/test_question_pause_completion_guard.py
)

# Sanity guard — optionally fail fast when EXPECTED_BRANCH is set.
# Shared-worktree hazard: an external `git checkout` mid-run invalidates
# dispatched test results. Set EXPECTED_BRANCH to a concrete branch to
# enable this check; the merged pack otherwise runs branch-agnostic.
ACTUAL_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
EXPECTED_BRANCH="${EXPECTED_BRANCH:-}"
if [[ -n "${EXPECTED_BRANCH}" ]]; then
  if [[ "${ACTUAL_BRANCH}" != "${EXPECTED_BRANCH}" ]]; then
    echo "RESULT: BRANCH-DRIFT (expected ${EXPECTED_BRANCH}, got ${ACTUAL_BRANCH})"
    exit 1
  fi
  echo "RESULT: BRANCH-CHECK (expected ${EXPECTED_BRANCH}, got ${ACTUAL_BRANCH})"
else
  echo "RESULT: SKIP (set EXPECTED_BRANCH to enforce branch guard)"
fi

# Collect/dry-run mode: `question_validation_targeted_unit_test.sh --collect`
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
