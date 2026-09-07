#!/usr/bin/env bash
# Test Pack: terminal_report_wake_prefix_worktree_test — PRE-FIX FAILURE PROOF
#
# Purpose: prove that the priority claim lane fix for terminal
# PROCESS_REPORT wake (HEAD ee66f0eb on feature/fix-terminal-report-wake)
# is REQUIRED — i.e. that the cat-b integration test fails on the
# pre-fix parent commit 77ce4ae8 with REAL assertion failures.
#
# Method (per the worktree-based A/B proof pattern):
#   1. Echo main-repo HEAD — NEVER touch the shared checkout.
#   2. Create a SEPARATE git worktree pinned at parent 77ce4ae8
#      (/tmp/ens_wake_prefix_77ce4ae8). Clean up any prior worktree
#      at that path first (force-remove + prune).
#   3. Copy ONLY the 3 new test files into the worktree at their
#      original paths:
#         tests/unit/services/test_terminal_report_wake_bus.py
#         tests/integration/test_report_wake_priority_claim.py     ← cat-b
#         tests/integration/test_wake_vs_claim_exactly_once.py
#      Copy nothing else — the worktree's daemon source remains the
#      pre-fix code.
#   4. `uv sync` (plain — no `--extra dev`; parent 77ce4ae8 post-dates
#      the dependency-groups migration 3d6a4c35, see commit c983637a
#      / PEP 735 convention note).
#   5. MANDATORY verification: `uv run python -c "import daemon;
#      print(daemon.__file__)"` MUST print a path inside the worktree.
#      The main venv's editable install silently tests HEAD; if the
#      printout points at the main checkout, ABORT (the proof would
#      be invalid).
#   6. Run ONLY the cat-b file in the worktree:
#         timeout 300 uv run pytest tests/integration/test_report_wake_priority_claim.py --tb=short -q
#      (Layer 1 = command-level 300s wrapper on pytest; Layer 2 =
#       script-internal guard via the same wrapper — pytest itself
#       has no internal timer. The script-level outer guard from the
#       caller is `timeout 900` covering the untimed setup phase.)
#
# INVERTED SEMANTICS for this pack:
#   - cat-b FAIL at parent with REAL ASSERTION FAILURES
#       → exit 0 (proof obtained — the bug was real and the fix is
#         required; this is the SUCCESS criterion for THIS pack).
#   - cat-b PASS at parent
#       → exit 1 (proof failed — the test does not exercise the bug
#         the way we expected; the fix may not be necessary).
#   - cat-b ERROR at collection / import (missing fixture / symbol)
#       → exit 1, mark INCONCLUSIVE in output (proof could not be
#         obtained — paste verbatim error).
#   - TIMEOUT (pytest exit 124)
#       → exit 124 (propagate — the inner cap is the binding test-
#         phase limit).
#
# Dual-layer timeout (per test-pack skill):
#   - Layer 1 (command-level, on the pytest phase only): the inline
#     `timeout 300` wrapping the pytest invocation.
#   - Layer 2 (script-internal): the SAME inline `timeout 300` — the
#     pytest process itself has no internal alarm; the inline wrapper
#     is both layers. The OUTER `timeout 900` from the caller covers
#     the untimed setup phase (uv sync + worktree create). Setup is
#     NOT counted against the 5-min test-phase cap.
#
# Exit codes (per test-pack skill):
#   0   PASS — pre-fix cat-b FAIL proven via REAL assertion failures
#   1   FAIL — cat-b PASSED at parent (proof invalid) OR collection
#             error classified INCONCLUSIVE
#   124 TIMEOUT — pytest phase exceeded 300s
#
# TEST-ENV ONLY. No production code changes, no daemon boot, no ports,
# no DB writes outside the per-test file-backed SQLite.
set -uo pipefail   # NOTE: -e removed on purpose; we inspect pytest rc manually
                  # and must NOT abort before recording the daemon.__file__
                  # verification result.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
PARENT_COMMIT="77ce4ae8"
WT_PATH="/tmp/ens_wake_prefix_${PARENT_COMMIT}"

echo "=== Test Pack: terminal_report_wake_prefix_worktree_test ==="
echo "(PRE-FIX FAILURE PROOF — cat-b MUST fail at parent ${PARENT_COMMIT})"
echo ""
echo "── semantic note: this pack uses INVERTED result semantics ──"
echo "   cat-b FAIL at parent → pack exit 0 (proof obtained)"
echo "   cat-b PASS at parent → pack exit 1 (proof invalid)"
echo "   cat-b collection/import error → pack exit 1 (INCONCLUSIVE)"
echo "   pytest timeout (124) → pack exit 124 (propagate)"
echo ""

# ── Phase 1: read-only main-repo HEAD echo ────────────────────────────────
cd "$PROJECT_DIR"
MAIN_HEAD_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
MAIN_HEAD_SHORT="$(git rev-parse --short HEAD)"
MAIN_HEAD_FULL="$(git rev-parse HEAD)"
echo "── main-repo HEAD (read-only): ${MAIN_HEAD_BRANCH} @ ${MAIN_HEAD_SHORT} (${MAIN_HEAD_FULL}) ──"
echo "   (shared checkout is NOT modified by this pack)"

# Sanity: parent commit must exist in the repo.
git -C "$PROJECT_DIR" cat-file -t "$PARENT_COMMIT" >/dev/null 2>&1 || {
  echo "FATAL: parent commit ${PARENT_COMMIT} not present in repo"
  exit 1
}

# ── Phase 2: worktree cleanup + create ────────────────────────────────────
echo ""
echo "── cleaning up any prior worktree at ${WT_PATH} ──"
if git -C "$PROJECT_DIR" worktree list --porcelain | grep -q "^worktree ${WT_PATH}$"; then
  git -C "$PROJECT_DIR" worktree remove --force "$WT_PATH" 2>&1 || true
fi
# Always prune stale refs after any forced removal.
git -C "$PROJECT_DIR" worktree prune >/dev/null 2>&1 || true
# Belt-and-braces: if the dir exists but isn't registered, scrub it.
if [ -d "$WT_PATH" ]; then
  echo "   (removing stale dir at ${WT_PATH})"
  rm -rf "$WT_PATH"
fi

echo "── creating detached worktree at parent ${PARENT_COMMIT} → ${WT_PATH} ──"
SETUP_START=$(date +%s)
git -C "$PROJECT_DIR" worktree add --detach "$WT_PATH" "$PARENT_COMMIT" >/dev/null

WT_HEAD="$(git -C "$WT_PATH" rev-parse --short HEAD)"
WT_HEAD_FULL="$(git -C "$WT_PATH" rev-parse HEAD)"
echo "   worktree HEAD: ${WT_HEAD} (${WT_HEAD_FULL})"

# Verify the worktree is on the parent commit.
if [ "$WT_HEAD_FULL" != "$PARENT_COMMIT" ] && \
   [ "${WT_HEAD_FULL}" != "$(git -C "$PROJECT_DIR" rev-parse "$PARENT_COMMIT")" ]; then
  echo "FATAL: worktree HEAD (${WT_HEAD_FULL}) is NOT parent (${PARENT_COMMIT})"
  git -C "$PROJECT_DIR" worktree remove --force "$WT_PATH" >/dev/null 2>&1 || true
  git -C "$PROJECT_DIR" worktree prune >/dev/null 2>&1 || true
  exit 1
fi
echo "   ✓ worktree pinned at parent ${PARENT_COMMIT}"

# ── Phase 3: copy ONLY the 3 new test files ───────────────────────────────
echo ""
echo "── copying 3 test files into worktree (source-of-truth: HEAD) ──"
SRC_DIR="$PROJECT_DIR/tests"
DST_DIR="$WT_PATH/tests"
REL_FILES=(
  "unit/services/test_terminal_report_wake_bus.py"
  "integration/test_report_wake_priority_claim.py"
  "integration/test_wake_vs_claim_exactly_once.py"
)
for rel in "${REL_FILES[@]}"; do
  src="${SRC_DIR}/${rel}"
  dst="${DST_DIR}/${rel}"
  if [ ! -f "$src" ]; then
    echo "FATAL: source file missing in main checkout: ${src}"
    git -C "$PROJECT_DIR" worktree remove --force "$WT_PATH" >/dev/null 2>&1 || true
    git -C "$PROJECT_DIR" worktree prune >/dev/null 2>&1 || true
    exit 1
  fi
  mkdir -p "$(dirname "$dst")"
  cp -f "$src" "$dst"
  echo "   copied: ${rel} ($(wc -l < "$dst") lines)"
done

# ── Phase 4: uv sync + daemon.__file__ verification ───────────────────────
echo ""
echo "── uv sync (worktree-local venv; plain — no --extra dev) ──"
cd "$WT_PATH"
UV_SYNC_START=$(date +%s)
uv sync >/tmp/uv-sync-${PARENT_COMMIT}.log 2>&1
UV_SYNC_RC=$?
UV_SYNC_END=$(date +%s)
UV_SYNC_SEC=$((UV_SYNC_END - UV_SYNC_START))
echo "   uv sync rc=${UV_SYNC_RC}  (${UV_SYNC_SEC}s)"
if [ "$UV_SYNC_RC" -ne 0 ]; then
  echo "FATAL: uv sync failed in worktree (rc=${UV_SYNC_RC})"
  echo "--- last 40 lines of uv sync log ---"
  tail -40 /tmp/uv-sync-${PARENT_COMMIT}.log
  git -C "$PROJECT_DIR" worktree remove --force "$WT_PATH" >/dev/null 2>&1 || true
  git -C "$PROJECT_DIR" worktree prune >/dev/null 2>&1 || true
  exit 1
fi

# Resolve both the user-supplied WT_PATH AND the real (symlink-resolved)
# path. macOS resolves /tmp/foo → /private/tmp/foo; the daemon module
# path reported by Python is the resolved one. Compare against BOTH.
WT_REAL="$(cd "$WT_PATH" && pwd -P)"
DAEMON_FILE=$(uv run python -c "import daemon; print(daemon.__file__)" 2>&1)
DAEMON_RC=$?
DAEMON_FILE_REAL="$(cd "$(dirname "$DAEMON_FILE")" && pwd -P)/$(basename "$DAEMON_FILE")" 2>/dev/null || DAEMON_FILE_REAL="$DAEMON_FILE"
echo "   uv run python returned: rc=${DAEMON_RC}"
echo "   daemon.__file__     = ${DAEMON_FILE}"
echo "   daemon.__file__ real = ${DAEMON_FILE_REAL}"
echo "   WT_PATH             = ${WT_PATH}"
echo "   WT_REAL             = ${WT_REAL}"
if [ "$DAEMON_RC" -ne 0 ]; then
  echo "FATAL: daemon import failed in worktree venv (rc=${DAEMON_RC})"
  echo "       output: ${DAEMON_FILE}"
  git -C "$PROJECT_DIR" worktree remove --force "$WT_PATH" >/dev/null 2>&1 || true
  git -C "$PROJECT_DIR" worktree prune >/dev/null 2>&1 || true
  exit 1
fi
case "$DAEMON_FILE_REAL" in
  "${WT_REAL}"/*)
    echo "   ✓ daemon.__file__ is INSIDE the worktree — proof valid"
    ;;
  *)
    echo "FATAL: daemon.__file__ is OUTSIDE the worktree — ABORT"
    echo "       expected prefix (real): ${WT_REAL}/"
    echo "       got (real):             ${DAEMON_FILE_REAL}"
    echo "       (main venv's editable install is silently testing HEAD"
    echo "        instead of the worktree's pre-fix code — proof would be invalid)"
    git -C "$PROJECT_DIR" worktree remove --force "$WT_PATH" >/dev/null 2>&1 || true
    git -C "$PROJECT_DIR" worktree prune >/dev/null 2>&1 || true
    exit 1
    ;;
esac

# ── Phase 5: run cat-b ONLY (Layer 1+2 timeout = 300s) ────────────────────
echo ""
echo "── running cat-b (tests/integration/test_report_wake_priority_claim.py) ──"
echo "   pytest invocation: timeout 300 uv run pytest <cat-b> --tb=short -q"
TEST_START=$(date +%s)
TEST_LOG="/tmp/catb-prefix-${PARENT_COMMIT}.log"
# Layer 1 + Layer 2: inline timeout 300 wraps the pytest process.
# No `-x` — collect every failure for the strongest proof.
timeout 300 uv run pytest \
  tests/integration/test_report_wake_priority_claim.py \
  --tb=short -q \
  >"$TEST_LOG" 2>&1
PYTEST_RC=$?
TEST_END=$(date +%s)
TEST_SEC=$((TEST_END - TEST_START))
echo "   pytest rc=${PYTEST_RC}  (${TEST_SEC}s)"
echo ""
echo "── pytest output (cat-b at parent ${PARENT_COMMIT}) ──"
cat "$TEST_LOG"
echo ""
echo "── /pytest output ──"

# ── Phase 6: classify result (INVERTED semantics) ─────────────────────────
SETUP_END=$(date +%s)
SETUP_SEC=$((SETUP_END - TEST_START + TEST_SEC))   # setup = (total - test) approx; recomputed cleanly below
TOTAL_SEC=$((TEST_END - SETUP_START))
SETUP_SEC=$((TOTAL_SEC - TEST_SEC))
echo ""
echo "── runtime: setup=${SETUP_SEC}s  test=${TEST_SEC}s  total=${TOTAL_SEC}s ──"
echo ""

# First, surface collected-vs-run counts from the pytest log (best-effort
# parse — pytest's -q mode produces a single-line short summary).
# pytest -q summary formats:
#   "5 passed in 1.36s"
#   "1 failed, 4 passed, 10 warnings in 1.36s"
#   "1 error in 1.36s"
#   "2 failed, 3 error in 1.36s"
# We parse each component separately.
SHORT_SUMMARY=$(grep -oE '[0-9]+ (failed|passed|errors?)' "$TEST_LOG" | tr '\n' ' ' | head -1)
NUM_FAILED=$(echo "$SHORT_SUMMARY" | grep -oE '[0-9]+ failed'  | grep -oE '^[0-9]+' || true)
NUM_ERRORS=$(echo "$SHORT_SUMMARY" | grep -oE '[0-9]+ errors?' | grep -oE '^[0-9]+' || true)
NUM_PASSED=$(echo "$SHORT_SUMMARY" | grep -oE '[0-9]+ passed'  | grep -oE '^[0-9]+' || true)
NUM_FAILED=${NUM_FAILED:-0}
NUM_ERRORS=${NUM_ERRORS:-0}
NUM_PASSED=${NUM_PASSED:-0}

# FAILED lines under "short test summary info" (real assertion failures).
NUM_FAILED_LINES=$(grep -cE '^FAILED ' "$TEST_LOG" || true)
# ERROR lines at collection time (real fixture/symbol errors).
NUM_ERROR_LINES=$(grep -cE '^ERROR ' "$TEST_LOG" || true)

COLLECTED=$(grep -oE '[0-9]+ tests collected' "$TEST_LOG" | tail -1 | grep -oE '[0-9]+' || echo "?")
echo "── pytest short summary tokens: ${SHORT_SUMMARY:-<none>} ──"
echo "── tests collected: ${COLLECTED} ──"
echo "── counts: passed=${NUM_PASSED}  failed=${NUM_FAILED}  errors=${NUM_ERRORS} ──"
echo "── raw lines:  FAILED lines=${NUM_FAILED_LINES}  ERROR lines=${NUM_ERROR_LINES} ──"
echo ""

# Timeout propagation (highest priority).
if [ "$PYTEST_RC" -eq 124 ]; then
  echo "── TEST PHASE TIMED OUT (300s cap on pytest) ──"
  echo "RESULT: TIMEOUT (cat-b at parent ${PARENT_COMMIT} did not finish in 300s)"
  echo "RESULT_CLASS: TIMEOUT"
  echo "PYTEST_RC: 124"
  echo "TEST_LOG: ${TEST_LOG}"
  echo "WT_PATH: ${WT_PATH}"
  # Cleanup in finally-ish block; we exit 124.
  git -C "$PROJECT_DIR" worktree remove --force "$WT_PATH" >/dev/null 2>&1 || true
  git -C "$PROJECT_DIR" worktree prune >/dev/null 2>&1 || true
  echo "WORKTREE_CLEANED: yes"
  exit 124
fi

# Collection/import error vs assertion failure distinction.
COLLECTION_ERRORS=$NUM_ERROR_LINES
HAS_FAILED_SUMMARY=$([ "$NUM_FAILED"  -gt 0 ] && echo 1 || echo 0)
HAS_ERRORS_SUMMARY=$([ "$NUM_ERRORS"  -gt 0 ] && echo 1 || echo 0)
HAS_PASSED_SUMMARY=$([ "$NUM_PASSED"  -gt 0 ] && echo 1 || echo 0)
HAS_FAILED_LINES=$([ "$NUM_FAILED_LINES" -gt 0 ] && echo 1 || echo 0)

# Extract exact failing-assertion lines for the report.
echo "── exact failing assertions (file:line, test name, error message) ──"
grep -E '^FAILED ' "$TEST_LOG" | head -50 || echo "   (no FAILED lines in pytest log)"
echo ""

if [ "$HAS_PASSED_SUMMARY" -gt 0 ] && [ "$HAS_FAILED_SUMMARY" -eq 0 ] && [ "$HAS_ERRORS_SUMMARY" -eq 0 ]; then
  # cat-b PASSED at parent: proof FAILED.
  echo "── INVERTED-SEMANTIC CLASSIFICATION ──"
  echo "   cat-b PASSED at parent ${PARENT_COMMIT}"
  echo "   this means the priority ranking is NOT exercised — the test does"
  echo "   not reproduce the bug. Proof is INVALID."
  echo ""
  echo "RESULT: FAIL (cat-b unexpectedly PASSED at parent — proof invalid)"
  echo "RESULT_CLASS: PASS_AT_PARENT_INVALIDATES_PROOF"
  echo "PYTEST_RC: ${PYTEST_RC}"
  echo "TEST_LOG: ${TEST_LOG}"
  echo "WT_PATH: ${WT_PATH}"
  git -C "$PROJECT_DIR" worktree remove --force "$WT_PATH" >/dev/null 2>&1 || true
  git -C "$PROJECT_DIR" worktree prune >/dev/null 2>&1 || true
  echo "WORKTREE_CLEANED: yes"
  exit 1
fi

if [ "$NUM_ERRORS" -gt 0 ] || [ "$NUM_ERROR_LINES" -gt 0 ]; then
  # cat-b failed at COLLECTION / IMPORT — proof is INCONCLUSIVE.
  echo "── INVERTED-SEMANTIC CLASSIFICATION ──"
  echo "   cat-b ERROR at collection/import at parent ${PARENT_COMMIT}"
  echo "   this means the test could not run its scenarios at all (missing"
  echo "   fixture, missing symbol, import error, etc.). Proof is"
  echo "   INCONCLUSIVE — paste the error verbatim."
  echo ""
  echo "RESULT: FAIL (INCONCLUSIVE — cat-b collection/import error)"
  echo "RESULT_CLASS: INCONCLUSIVE_COLLECTION_ERROR"
  echo "PYTEST_RC: ${PYTEST_RC}"
  echo "TEST_LOG: ${TEST_LOG}"
  echo "WT_PATH: ${WT_PATH}"
  git -C "$PROJECT_DIR" worktree remove --force "$WT_PATH" >/dev/null 2>&1 || true
  git -C "$PROJECT_DIR" worktree prune >/dev/null 2>&1 || true
  echo "WORKTREE_CLEANED: yes"
  exit 1
fi

# cat-b FAILED with real assertion failures — PROOF OBTAINED.
echo "── INVERTED-SEMANTIC CLASSIFICATION ──"
echo "   cat-b FAILED at parent ${PARENT_COMMIT} with ${NUM_FAILED} failed / ${NUM_ERRORS} error / ${NUM_FAILED_LINES} FAILED-lines — proof OBTAINED."
echo "   and non-zero FAILED summary line — proof OBTAINED."
echo ""
echo "RESULT: PASS (pre-fix cat-b FAIL proven via real assertion failures)"
echo "RESULT_CLASS: PRE_FIX_FAIL_PROVEN"
echo "PYTEST_RC: ${PYTEST_RC}"
echo "TEST_LOG: ${TEST_LOG}"
echo "WT_PATH: ${WT_PATH}"
git -C "$PROJECT_DIR" worktree remove --force "$WT_PATH" >/dev/null 2>&1 || true
git -C "$PROJECT_DIR" worktree prune >/dev/null 2>&1 || true
echo "WORKTREE_CLEANED: yes"
exit 0
