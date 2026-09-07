#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Pre-fix discrimination proof pack — ensure-deferred (unit) + watcher
# rearm (integration) at parent bb052fce (BEFORE the fix at HEAD e9aac370).
#
# Worktree: separate /tmp git worktree — main checkout MUST stay untouched.
#
# LEG A (GATING, inverted semantics):
#   tests/unit/test_ensure_deferred_insert_on_missing.py
#   EXPECT ≥1 real ASSERTION failure at parent (the phantom-IntegrityError
#   insert-on-missing and/or concurrent-convergence tests must fail at
#   parent: pre-fix ensure_deferred returns None on IntegrityError+zero-rows
#   instead of inserting). Pack PASS (= proof obtained) requires ≥1 assertion
#   failure AND zero collection/import errors. Unexpected all-pass = proof
#   failed (exit 1).
#
# LEG B (INFORMATIONAL, not gating):
#   tests/integration/test_pause_resume_watcher_rearm.py
#   Expected shape: re-arm ON-path tests FAIL at parent (feature absent
#   pre-fix = corroboration); kill-switch OFF / legacy-behavior pin PASSES
#   at parent (legacy contract held — no legacy regression).
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail

SCRIPT_START=$(date +%s)
OUTER_BUDGET_SEC=900
WT_PATH="/tmp/ensdefer_prefix2_bb052fce"
WT_REAL_PATH=""
PARENT_PIN="bb052fce"
MAIN_REPO="/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble"

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

echo "=== Pre-Fix Discrimination Proof Pack (Round 2) ==="
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

# Verify worktree HEAD equals parent pin
WT_HEAD=$(git -C "$WT_PATH" rev-parse HEAD)
if [[ "$WT_HEAD" != "$PARENT_FULL" ]]; then
    echo "ERROR: worktree HEAD $WT_HEAD != parent $PARENT_FULL"
    exit 1
fi
echo "[step 2b] worktree HEAD verified == $PARENT_SHORT"
echo

# ─── Step 3: Copy ONLY the 3 ACTUAL-path new test files into worktree ─────
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
if ! (cd "$WT_REAL_PATH" && uv sync 2>&1 | tail -5); then
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

# Helper: parse pytest -v output to per-test pass/fail table
parse_pytest_v() {
    local log="$1"
    # pytest -v lines look like: "tests/.../test_foo.py::test_bar[param] PASSED" or "FAILED"
    # Use a robust regex over the captured log.
    echo "$log" \
        | grep -E "PASSED|FAILED|ERROR" \
        | grep -E "::" \
        | sed -E 's/^([^[:space:]]+::[^[:space:]]+).* (PASSED|FAILED|ERROR).*$/\1 \2/' \
        | sort -u
}

# Helper: short summary line
short_summary() {
    local log="$1"
    echo "$log" | grep -E "^[0-9]+ (passed|failed|error)" | tail -3
}

# ─── LEG A: ensure_deferred (gating) ──────────────────────────────────────
LEG_A_FILE="tests/unit/test_ensure_deferred_insert_on_missing.py"
LEG_A_BUDGET=300
TEST_A_START=$(date +%s)
echo "═══════════════════════════════════════════════════════════════"
echo "[LEG A — GATING] Running $LEG_A_FILE at parent bb052fce (no fix)"
echo "  cmd: timeout $LEG_A_BUDGET uv run pytest $LEG_A_FILE -v --tb=short -q"
echo "═══════════════════════════════════════════════════════════════"
echo

# Capture both stdout and stderr; -v for per-test table.
PYTEST_A_OUT=$(cd "$WT_REAL_PATH" && timeout $LEG_A_BUDGET uv run pytest "$LEG_A_FILE" -v --tb=short 2>&1)
PYTEST_A_RC=$?
TEST_A_END=$(date +%s)
TEST_A_DURATION=$(( TEST_A_END - TEST_A_START ))

echo "=== LEG A PYTEST OUTPUT ==="
echo "$PYTEST_A_OUT"
echo "=== END LEG A PYTEST OUTPUT ==="
echo
echo "leg_a_pytest_rc=$PYTEST_A_RC"
echo "leg_a_test_duration_sec=$TEST_A_DURATION (capped at $LEG_A_BUDGET)"
echo

# ─── LEG A: classification (inverted semantics) ──────────────────────────
# Count failure / error / pass signals.
LEG_A_PASS_COUNT=$(echo "$PYTEST_A_OUT" | grep -cE "::.* PASSED" || true)
LEG_A_FAIL_COUNT=$(echo "$PYTEST_A_OUT" | grep -cE "::.* FAILED" || true)
LEG_A_ERROR_COUNT=$(echo "$PYTEST_A_OUT" | grep -cE "::.* ERROR" || true)
LEG_A_SHORT=$(short_summary "$PYTEST_A_OUT")

# Collect any "no tests ran" / "errors during collection" / "could not import" lines.
LEG_A_COLLECTION_ERROR=$(echo "$PYTEST_A_OUT" | grep -ciE "errors during collection|failed to import|cannot import name|module .* has no attribute|no module named" || true)

# Build per-test table.
LEG_A_TABLE=$(parse_pytest_v "$PYTEST_A_OUT")

echo "─── LEG A Classification Inputs ───"
echo "  pass_count=$LEG_A_PASS_COUNT"
echo "  fail_count=$LEG_A_FAIL_COUNT"
echo "  error_count=$LEG_A_ERROR_COUNT"
echo "  collection_error_count=$LEG_A_COLLECTION_ERROR"
echo "  short_summary=$LEG_A_SHORT"
echo
echo "─── LEG A Per-Test Table ───"
if [[ -n "$LEG_A_TABLE" ]]; then
    echo "$LEG_A_TABLE" | sed 's/^/  /'
else
    echo "  (no per-test table parsed)"
fi
echo

LEG_A_CLASSIFICATION=""
LEG_A_PROOF=""

if (( PYTEST_A_RC == 124 )); then
    LEG_A_CLASSIFICATION="TIMEOUT"
    LEG_A_PROOF="INCONCLUSIVE"
    echo "─── LEG A Classification: TIMEOUT (Layer 1 fired at $LEG_A_BUDGET s) ───"
elif (( LEG_A_COLLECTION_ERROR > 0 )); then
    LEG_A_CLASSIFICATION="INCONCLUSIVE_COLLECTION"
    LEG_A_PROOF="INCONCLUSIVE"
    echo "─── LEG A Classification: INCONCLUSIVE (collection/import error) ───"
elif (( PYTEST_A_RC == 0 )) && (( LEG_A_FAIL_COUNT == 0 )); then
    LEG_A_CLASSIFICATION="UNEXPECTED_ALL_PASS"
    LEG_A_PROOF="FAIL"
    echo "─── LEG A Classification: UNEXPECTED ALL-PASS at parent ───"
    echo "  The ensure_deferred unit tests ALL PASS at parent bb052fce — proof FAILED."
elif (( LEG_A_FAIL_COUNT > 0 )); then
    LEG_A_CLASSIFICATION="ASSERTION_FAILURE"
    LEG_A_PROOF="PASS"
    echo "─── LEG A Classification: ASSERTION FAILURE (proof SUCCESSFUL) ───"
    echo "  ≥1 test FAILED at parent bb052fce with real assertion failure —"
    echo "  this is the EXPECTED inverted-semantics outcome. The fix is necessary."
else
    LEG_A_CLASSIFICATION="INCONCLUSIVE_OTHER"
    LEG_A_PROOF="INCONCLUSIVE"
    echo "─── LEG A Classification: INCONCLUSIVE (other) ───"
    echo "  rc=$PYTEST_A_RC  fails=$LEG_A_FAIL_COUNT  errors=$LEG_A_ERROR_COUNT  coll_err=$LEG_A_COLLECTION_ERROR"
fi
echo

# ─── LEG B: pause_resume_watcher_rearm (informational) ────────────────────
LEG_B_FILE="tests/integration/test_pause_resume_watcher_rearm.py"
LEG_B_BUDGET=300
TEST_B_START=$(date +%s)
echo "═══════════════════════════════════════════════════════════════"
echo "[LEG B — INFORMATIONAL] Running $LEG_B_FILE at parent bb052fce (no fix)"
echo "  cmd: timeout $LEG_B_BUDGET uv run pytest $LEG_B_FILE -v --tb=short -q"
echo "═══════════════════════════════════════════════════════════════"
echo

PYTEST_B_OUT=$(cd "$WT_REAL_PATH" && timeout $LEG_B_BUDGET uv run pytest "$LEG_B_FILE" -v --tb=short 2>&1)
PYTEST_B_RC=$?
TEST_B_END=$(date +%s)
TEST_B_DURATION=$(( TEST_B_END - TEST_B_START ))

echo "=== LEG B PYTEST OUTPUT ==="
echo "$PYTEST_B_OUT"
echo "=== END LEG B PYTEST OUTPUT ==="
echo
echo "leg_b_pytest_rc=$PYTEST_B_RC"
echo "leg_b_test_duration_sec=$TEST_B_DURATION (capped at $LEG_B_BUDGET)"
echo

LEG_B_PASS_COUNT=$(echo "$PYTEST_B_OUT" | grep -cE "::.* PASSED" || true)
LEG_B_FAIL_COUNT=$(echo "$PYTEST_B_OUT" | grep -cE "::.* FAILED" || true)
LEG_B_ERROR_COUNT=$(echo "$PYTEST_B_OUT" | grep -cE "::.* ERROR" || true)
LEG_B_SHORT=$(short_summary "$PYTEST_B_OUT")
LEG_B_COLLECTION_ERROR=$(echo "$PYTEST_B_OUT" | grep -ciE "errors during collection|failed to import|cannot import name|module .* has no attribute|no module named" || true)
LEG_B_TABLE=$(parse_pytest_v "$PYTEST_B_OUT")

echo "─── LEG B Classification Inputs ───"
echo "  pass_count=$LEG_B_PASS_COUNT"
echo "  fail_count=$LEG_B_FAIL_COUNT"
echo "  error_count=$LEG_B_ERROR_COUNT"
echo "  collection_error_count=$LEG_B_COLLECTION_ERROR"
echo "  short_summary=$LEG_B_SHORT"
echo
echo "─── LEG B Per-Test Table ───"
if [[ -n "$LEG_B_TABLE" ]]; then
    echo "$LEG_B_TABLE" | sed 's/^/  /'
else
    echo "  (no per-test table parsed)"
fi
echo

LEG_B_CLASSIFICATION=""
if (( PYTEST_B_RC == 124 )); then
    LEG_B_CLASSIFICATION="TIMEOUT"
elif (( LEG_B_COLLECTION_ERROR > 0 )); then
    LEG_B_CLASSIFICATION="INCONCLUSIVE_COLLECTION"
elif (( LEG_B_FAIL_COUNT > 0 )); then
    LEG_B_CLASSIFICATION="EXPECTED_CORROBORATION (re-arm ON-path tests fail = feature absent pre-fix)"
else
    LEG_B_CLASSIFICATION="ALL_PASS (no re-arm corroboration — unexpected)"
fi
echo "─── LEG B Classification: $LEG_B_CLASSIFICATION ───"
echo

# ─── Final report ─────────────────────────────────────────────────────────
TOTAL_DURATION=$(( TEST_B_END - SCRIPT_START ))
echo "═══════════════════════════════════════════════════════════════"
echo "PROOF RESULT (LEG A gating): $LEG_A_PROOF"
echo "LEG A CLASSIFICATION:        $LEG_A_CLASSIFICATION"
echo "LEG B CLASSIFICATION:        $LEG_B_CLASSIFICATION"
echo
echo "MAIN HEAD:    $MAIN_BRANCH @ $MAIN_SHORT ($MAIN_FULL)"
echo "PARENT PIN:   $PARENT_SHORT ($PARENT_FULL)"
echo "WORKTREE:     $WT_PATH (real: $WT_REAL_PATH; cleaned: see trap)"
echo
echo "RUNTIMES:"
echo "  setup (untimed):   ${SETUP_DURATION}s"
echo "  LEG A (≤300s):     ${TEST_A_DURATION}s"
echo "  LEG B (≤300s):     ${TEST_B_DURATION}s"
echo "  total:             ${TOTAL_DURATION}s"
echo "  outer budget:      ${OUTER_BUDGET_SEC}s (remaining: $(( OUTER_BUDGET_SEC - TOTAL_DURATION ))s)"
echo
echo "LEG A COUNTS: pass=$LEG_A_PASS_COUNT fail=$LEG_A_FAIL_COUNT error=$LEG_A_ERROR_COUNT coll_err=$LEG_A_COLLECTION_ERROR"
echo "LEG B COUNTS: pass=$LEG_B_PASS_COUNT fail=$LEG_B_FAIL_COUNT error=$LEG_B_ERROR_COUNT coll_err=$LEG_B_COLLECTION_ERROR"
echo "═══════════════════════════════════════════════════════════════"

# Exit code:
#   PROOF=PASS  (LEG A assertion failure) → exit 0
#   PROOF=FAIL  (LEG A all-pass / unexpected) → exit 1
#   PROOF=INCONCLUSIVE + TIMEOUT → exit 124
#   PROOF=INCONCLUSIVE (collection) → exit 1
if [[ "$LEG_A_PROOF" == "PASS" ]]; then
    exit 0
elif [[ "$LEG_A_PROOF" == "INCONCLUSIVE" ]] && [[ "$LEG_A_CLASSIFICATION" == "TIMEOUT" ]]; then
    exit 124
else
    exit 1
fi
