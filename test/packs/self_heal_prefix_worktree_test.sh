#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Pre-fix failure proof pack for ensure-deferred self-heal.
#
# Pin: parent bb052fce (BEFORE the fix at HEAD e9aac370).
# Worktree: separate /tmp git worktree — main checkout MUST stay untouched.
# Target: tests/unit/test_report_delivery_self_heal_zero_row.py
#
# Inverted semantics: this test file at parent bb052fce (without the fix)
# is EXPECTED to fail with real assertion failures. A failing self-heal
# suite at parent is the proof (exit 0). Unexpected PASS at parent means
# the proof failed (exit 1). Collection/import errors (missing fixtures
# / symbols at parent) are INCONCLUSIVE (exit 1). Timeout = exit 124.
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail

# Layer 1: outer 15-min budget (covers untimed setup; pytest keeps its 300s cap).
SCRIPT_START=$(date +%s)
OUTER_BUDGET_SEC=900
WT_PATH="/tmp/ensdefer_prefix_bb052fce"
WT_REAL_PATH=""
PARENT_PIN="bb052fce"
MAIN_REPO="/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble"

# ─── Layer 1 outer timeout enforcement ──────────────────────────────────────
elapsed() { date +%s; }
budget_remaining() { echo $(( OUTER_BUDGET_SEC - ( $(elapsed) - SCRIPT_START ) )); }

if (( $(budget_remaining) <= 0 )); then
    echo "OUTER BUDGET EXHAUSTED BEFORE START"
    exit 124
fi

cleanup_worktree() {
    if [[ -d "$WT_PATH" ]]; then
        git worktree remove --force "$WT_PATH" 2>&1 || true
    fi
    git worktree prune 2>&1 || true
}
trap cleanup_worktree EXIT

# ─── Step 0: Verify main repo state (read-only) ────────────────────────────
cd "$MAIN_REPO" || { echo "ERROR: cannot cd to main repo"; exit 1; }

MAIN_BRANCH=$(git rev-parse --abbrev-ref HEAD)
MAIN_SHORT=$(git rev-parse --short HEAD)
MAIN_FULL=$(git rev-parse HEAD)
PARENT_FULL=$(git rev-parse "$PARENT_PIN" 2>/dev/null || echo "MISSING")
PARENT_SHORT=$(git rev-parse --short "$PARENT_PIN" 2>/dev/null || echo "MISSING")

echo "=== Pre-Fix Failure Proof Pack ==="
echo "main_repo=$MAIN_REPO"
echo "main_branch=$MAIN_BRANCH"
echo "main_short=$MAIN_SHORT"
echo "main_full=$MAIN_FULL"
echo "parent_pin=$PARENT_PIN"
echo "parent_short=$PARENT_SHORT"
echo "parent_full=$PARENT_FULL"
echo "worktree_path=$WT_PATH"
echo "outer_budget_sec=$OUTER_BUDGET_SEC"
echo

if [[ "$PARENT_FULL" == "MISSING" ]]; then
    echo "ERROR: parent pin $PARENT_PIN not resolvable in main repo"
    exit 1
fi

# ─── Step 1: Clean prior worktree (if any) ────────────────────────────────
echo "[step 1] Cleaning prior worktree (if any)..."
if [[ -d "$WT_PATH" ]]; then
    git worktree remove --force "$WT_PATH" 2>&1 || true
fi
git worktree prune 2>&1 || true
echo

# ─── Step 2: Create detached worktree at parent pin ────────────────────────
echo "[step 2] Creating worktree at $WT_PATH pinned at $PARENT_PIN..."
if ! git worktree add --detach "$WT_PATH" "$PARENT_PIN" 2>&1; then
    echo "ERROR: worktree add failed"
    exit 1
fi
echo

# Resolve real path (macOS /tmp → /private/tmp symlink trap)
WT_REAL_PATH=$(cd "$WT_PATH" && pwd -P)
echo "[step 2b] worktree real path: $WT_REAL_PATH"

# Verify worktree HEAD equals parent pin (defense in depth)
WT_HEAD=$(git -C "$WT_PATH" rev-parse HEAD)
if [[ "$WT_HEAD" != "$PARENT_FULL" ]]; then
    echo "ERROR: worktree HEAD $WT_HEAD != parent $PARENT_FULL"
    exit 1
fi
echo "[step 2b] worktree HEAD verified == $PARENT_SHORT"
echo

# ─── Step 3: Copy ONLY the 3 new test files into worktree at original paths
# Paths below are the ACTUAL paths from fix commit e9aac370 (not paraphrased).
SETUP_START=$(date +%s)
echo "[step 3] Copying 3 new test files from fix HEAD to worktree at original paths..."
declare -a TEST_FILES=(
    "tests/unit/test_ensure_deferred_insert_on_missing.py"
    "tests/integration/test_pause_resume_watcher_rearm.py"
    "tests/unit/test_report_delivery_self_heal_zero_row.py"
)
for f in "${TEST_FILES[@]}"; do
    src="$MAIN_REPO/$f"
    dst="$WT_REAL_PATH/$f"
    if [[ ! -f "$src" ]]; then
        echo "ERROR: source test file missing: $src"
        exit 1
    fi
    mkdir -p "$(dirname "$dst")"
    if ! cp "$src" "$dst"; then
        echo "ERROR: copy failed for $f"
        exit 1
    fi
    echo "  copied: $f ($(wc -l < "$src") lines)"
done
echo

# ─── Step 4: uv sync in worktree (untimed setup) ──────────────────────────
echo "[step 4] Running 'uv sync' in worktree (untimed; outer budget)..."
if (( $(budget_remaining) <= 60 )); then
    echo "ERROR: insufficient budget for uv sync (need > 60s)"
    exit 124
fi
if ! (cd "$WT_REAL_PATH" && uv sync 2>&1 | tail -20); then
    echo "ERROR: uv sync failed"
    exit 1
fi
echo

# ─── Step 5: Verify daemon.__file__ resolves INSIDE worktree (not main) ────
echo "[step 5] Verifying daemon.__file__ resolves inside worktree..."
DAEMON_FILE=$(cd "$WT_REAL_PATH" && uv run python -c "import daemon; print(daemon.__file__)" 2>&1)
DAEMON_RC=$?
echo "  daemon.__file__ = $DAEMON_FILE"
echo "  rc=$DAEMON_RC"
if (( DAEMON_RC != 0 )); then
    echo "ERROR: daemon import failed in worktree"
    echo "$DAEMON_FILE"
    exit 1
fi
# Resolve to real path for comparison
DAEMON_FILE_REAL=$(python3 -c "import os; print(os.path.realpath('$DAEMON_FILE'))" 2>/dev/null || echo "$DAEMON_FILE")
echo "  daemon.__file__ real = $DAEMON_FILE_REAL"
# Check the daemon package is inside the worktree
if [[ "$DAEMON_FILE_REAL" != "$WT_REAL_PATH"/* ]]; then
    echo "ERROR: daemon.__file__ ($DAEMON_FILE_REAL) is NOT inside worktree ($WT_REAL_PATH)"
    echo "  → main checkout contamination detected; aborting"
    exit 1
fi
echo "  [OK] daemon resolves inside worktree"
echo
SETUP_END=$(date +%s)
SETUP_DURATION=$(( SETUP_END - SETUP_START ))
echo "setup_duration_sec=$SETUP_DURATION (untimed; outer budget)"
echo

# ─── Step 6: Run ONLY the self-heal file in worktree ──────────────────────
# Layer 1 = 300s on the pytest command (script-internal watchdog too).
TEST_START=$(date +%s)
TEST_BUDGET=300
echo "[step 6] Running self-heal test at parent bb052fce (no fix)..."
echo "  cmd: timeout $TEST_BUDGET uv run pytest tests/unit/test_report_delivery_self_heal_zero_row.py --tb=short -q"
echo

# Use timeout command for Layer 1 (interrupts hung pytest).
# Capture both stdout and stderr.
PYTEST_OUT=$(cd "$WT_REAL_PATH" && timeout $TEST_BUDGET uv run pytest tests/unit/test_report_delivery_self_heal_zero_row.py --tb=short -q 2>&1)
PYTEST_RC=$?
TEST_END=$(date +%s)
TEST_DURATION=$(( TEST_END - TEST_START ))

echo "=== PYTEST OUTPUT ==="
echo "$PYTEST_OUT"
echo "=== END PYTEST OUTPUT ==="
echo
echo "pytest_rc=$PYTEST_RC"
echo "test_duration_sec=$TEST_DURATION (capped at $TEST_BUDGET)"
echo

TOTAL_DURATION=$(( TEST_END - SCRIPT_START ))
echo "total_duration_sec=$TOTAL_DURATION"
echo

# ─── Step 7: Inverted-semantics classification ────────────────────────────
# Timeout = 124 (Layer 1 fired)
# PASS at parent = proof FAILED (the bug would have been caught = bad; expected fail)
# FAIL at parent = proof SUCCEEDED (bug reproduces at parent = good; this is the inverted expectation)
# Collection/import error (errors/failures during collection) = INCONCLUSIVE
# Other non-zero = INCONCLUSIVE

# Heuristics for classification (inspect pytest output):
HAS_ERROR_LINE=$(echo "$PYTEST_OUT" | grep -cE "^E   |^ERROR " || true)
HAS_COLLECTION_ERROR=$(echo "$PYTEST_OUT" | grep -ciE "error during collection|failed to import|cannot import|name '.*' is not defined|module '.*' has no attribute" || true)
HAS_ASSERTION_FAIL=$(echo "$PYTEST_OUT" | grep -cE "AssertionError|assert " || true)
PASS_LINE=$(echo "$PYTEST_OUT" | grep -E "^[0-9]+ passed" | tail -1)
FAIL_LINE=$(echo "$PYTEST_OUT" | grep -E "^[0-9]+ failed" | tail -1)
ERROR_LINE=$(echo "$PYTEST_OUT" | grep -E "^[0-9]+ error" | tail -1)
COLLECTED=$(echo "$PYTEST_OUT" | grep -oE "[0-9]+ collected" | tail -1)

echo "─── Classification Inputs ───"
echo "  has_assertion_fail_count=$HAS_ASSERTION_FAIL"
echo "  has_collection_error_count=$HAS_COLLECTION_ERROR"
echo "  pass_line=$PASS_LINE"
echo "  fail_line=$FAIL_LINE"
echo "  error_line=$ERROR_LINE"
echo "  collected=$COLLECTED"
echo

CLASSIFICATION=""
PROOF_RESULT=""

if (( PYTEST_RC == 124 )); then
    CLASSIFICATION="TIMEOUT"
    PROOF_RESULT="INCONCLUSIVE"
    echo "─── Classification: TIMEOUT (Layer 1 fired at $TEST_BUDGET s) ───"
    echo "  pytest did not finish within $TEST_BUDGET s; cannot prove pre-fix failure"
elif (( PYTEST_RC == 0 )); then
    CLASSIFICATION="UNEXPECTED_PASS"
    PROOF_RESULT="FAIL"
    echo "─── Classification: UNEXPECTED PASS at parent ───"
    echo "  The self-heal test PASSED at parent bb052fce — proof FAILED."
    echo "  Either the fix is not necessary, or the test does not catch the bug."
elif (( HAS_COLLECTION_ERROR > 0 )) && (( HAS_ASSERTION_FAIL == 0 )); then
    CLASSIFICATION="INCONCLUSIVE"
    PROOF_RESULT="INCONCLUSIVE"
    echo "─── Classification: INCONCLUSIVE (collection/import error) ───"
    echo "  The test file at parent cannot be collected or imported — proof is WEAK."
    echo "  Collection error(s) detected: $HAS_COLLECTION_ERROR"
else
    # Non-zero exit AND not a pure collection error AND has assertion failure lines
    CLASSIFICATION="ASSERTION_FAILURE"
    PROOF_RESULT="PASS"
    echo "─── Classification: ASSERTION FAILURE (proof SUCCESSFUL) ───"
    echo "  The self-heal test FAILED at parent bb052fce with real assertion failures —"
    echo "  this is the EXPECTED inverted-semantics outcome. The fix is necessary."
fi

echo
echo "═══════════════════════════════════════════════════════════════"
echo "PROOF RESULT: $PROOF_RESULT"
echo "CLASSIFICATION: $CLASSIFICATION"
echo "MAIN HEAD: $MAIN_BRANCH @ $MAIN_SHORT"
echo "PARENT PIN: $PARENT_SHORT ($PARENT_FULL)"
echo "WORKTREE: $WT_PATH (cleaned: see trap)"
echo "SETUP DURATION: ${SETUP_DURATION}s"
echo "TEST DURATION:  ${TEST_DURATION}s"
echo "TOTAL DURATION: ${TOTAL_DURATION}s"
echo "═══════════════════════════════════════════════════════════════"

# Exit code per inverted semantics:
#   PROOF_RESULT=PASS  (assertion failure at parent) → exit 0
#   PROOF_RESULT=FAIL  (unexpected pass at parent)   → exit 1
#   PROOF_RESULT=INCONCLUSIVE + TIMEOUT               → exit 124
#   PROOF_RESULT=INCONCLUSIVE (collection/import)     → exit 1
if [[ "$PROOF_RESULT" == "PASS" ]]; then
    exit 0
elif [[ "$PROOF_RESULT" == "INCONCLUSIVE" ]] && [[ "$CLASSIFICATION" == "TIMEOUT" ]]; then
    exit 124
else
    exit 1
fi
