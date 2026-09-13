#!/usr/bin/env bash
# Test Pack: mission_tree_fe_targeted_test — targeted gate for the 3 FE suites
# touched by jobs-page closing-bracket e38dea27..670626a0 on
# `feature/jobs-page-improvement`. Pre-authorized worktree-local edit
# (no commits in this worktree; giter lands the pin refresh with the other
# infra files).
#
# Derived suite list (git show --name-only on e38dea27..670626a0, .spec.ts filter):
#   src/app/pages/jobs/jobs-page.bindings.pins.spec.ts
#   src/app/models/work.model.spec.ts
# UNION with prior bracket's set (mission.service + jobs-page.bindings.pins)
# so the closing bracket covers both fix seams:
#   src/app/services/mission.service.spec.ts
# Expected: all 3 suites green on this branch. No known-failure baseline for
# these suites → ANY failure is NEW-suspect (per-suite counts reported
# verbatim from jest output).
#
# Unit pack — estimated < 1 min. Dual-layer timeout:
# Layer 2 (script-internal): 150s global deadline across the single jest run —
#   interrupts hung jest, prints RESULT: TIMEOUT, exit 124.
# Layer 1 (command-level): caller wraps with `timeout 300`.
# RESULT-echo idiom: `|| EXIT_CODE=$?` — under `set -e` a bare EXIT_CODE=$?
# after a failing command never executes (silent exit, no RESULT).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
FRONTEND_DIR="$PROJECT_DIR/frontend"

# --- Layer-1 wrapper self-check: a timeout binary must be resolvable ---
TIMEOUT_BIN="/opt/homebrew/bin/timeout"
if [ ! -x "$TIMEOUT_BIN" ]; then
  TIMEOUT_BIN="$(command -v timeout || true)"
fi
if [ -z "${TIMEOUT_BIN:-}" ]; then
  echo "RESULT: FAIL (no timeout binary found — dual-layer contract unmet)"
  exit 1
fi

# --- Branch-drift guard (sibling-pack pattern: rev-parse bracket + exact pin) ---
ACTUAL_BRANCH="$(git -C "$PROJECT_DIR" rev-parse --abbrev-ref HEAD)"
ACTUAL_COMMIT="$(git -C "$PROJECT_DIR" rev-parse --short HEAD)"
EXPECTED_BRANCH="feature/jobs-page-improvement"
EXPECTED_COMMIT="670626a0"
echo "=== Test Pack: mission_tree_fe_targeted_test [${ACTUAL_BRANCH} @ ${ACTUAL_COMMIT}] ==="
if [[ "${ACTUAL_BRANCH}" != "${EXPECTED_BRANCH}" || "${ACTUAL_COMMIT}" != "${EXPECTED_COMMIT}" ]]; then
  echo "RESULT: DRIFT (expected ${EXPECTED_BRANCH} @ ${EXPECTED_COMMIT}, got ${ACTUAL_BRANCH} @ ${ACTUAL_COMMIT})"
  exit 1
fi

cd "$FRONTEND_DIR"

SPECS=(
  src/app/pages/jobs/jobs-page.bindings.pins.spec.ts
  src/app/services/mission.service.spec.ts
  src/app/models/work.model.spec.ts
)

INTERNAL_LIMIT=150
JEST_EXIT=0
"$TIMEOUT_BIN" "${INTERNAL_LIMIT}s" npx jest "${SPECS[@]}" --no-cache || JEST_EXIT=$?

# Post-run rev-parse bracket (repo convention: rev-parse must bracket the jest run)
echo "--- post-run bracket: $(git -C "$PROJECT_DIR" rev-parse --abbrev-ref HEAD) @ $(git -C "$PROJECT_DIR" rev-parse --short HEAD) ---"

if [ "$JEST_EXIT" -eq 124 ]; then
  echo "RESULT: TIMEOUT (jest exceeded ${INTERNAL_LIMIT}s internal limit; last suite in progress shown above)"
  exit 124
elif [ "$JEST_EXIT" -ne 0 ]; then
  echo "RESULT: FAIL (jest exit ${JEST_EXIT} — failed tests listed above)"
  exit 1
else
  echo "RESULT: PASS"
  exit 0
fi
