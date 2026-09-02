#!/usr/bin/env bash
# Test Pack: fe_static_typecheck_build_test
# Static typecheck + production build pack for frontend-only feature branches.
# Used by the FE-liveness full test gates (job-queue-fe-liveness pattern).
#
# Three stages:
#   Stage 0: rev-parse bracket — worktree drift gate (branch + short SHA).
#   Stage 1: `npx tsc --noEmit` — type-check only, no JS emit.
#   Stage 2: `npm run build`    — Angular production build.
#
# PASS iff Stage 1 exit 0 AND Stage 2 exit 0. SCSS_WARNING_COUNT is
# ADJUDICATION DATA (printed verbatim), NOT a pass/fail gate — pre-existing
# bundle-budget warnings are baseline-expected on Angular 21 production builds.
#
# Self-cap: 290s wall-clock SECONDS guard. Outer callers may wrap with
# `timeout 300`. Outer callers MUST verify branch + SHA drift independently
# if they have a different EXPECTED_* pair than this script.
set -uo pipefail

# ─── Self-cap (SECONDS auto-increments inside bash) ────────────────────────
PACK_START=${SECONDS:-0}
PACK_BUDGET_S=290

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
FRONTEND_DIR="$PROJECT_DIR/frontend"
BUILD_LOG="/tmp/fe_static_build.log"

EXPECTED_BRANCH="feature/job-queue-fe-liveness"
EXPECTED_SHORT_SHA="de493472"

echo "=== Test Pack: fe_static_typecheck_build_test ==="

# ─── Stage 0: rev-parse bracket (worktree drift gate) ──────────────────────
echo "--- Stage 0: worktree bracket ---"
CURRENT_BRANCH=$(git -C "$PROJECT_DIR" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "UNKNOWN")
CURRENT_SHORT_SHA=$(git -C "$PROJECT_DIR" rev-parse --short HEAD 2>/dev/null || echo "UNKNOWN")
echo "Branch: $CURRENT_BRANCH  SHA: $CURRENT_SHORT_SHA"
echo "Expected: $EXPECTED_BRANCH @ $EXPECTED_SHORT_SHA"

if [ "$CURRENT_BRANCH" != "$EXPECTED_BRANCH" ] || [ "$CURRENT_SHORT_SHA" != "$EXPECTED_SHORT_SHA" ]; then
    echo "RESULT: FAIL (DRIFT)"
    exit 1
fi

cd "$FRONTEND_DIR"

# Helper: check the wall-clock self-cap. Prints TIMEOUT + exits 124 on breach.
check_cap() {
    local now=${SECONDS:-0}
    local elapsed=$(( now - PACK_START ))
    if [ "$elapsed" -ge "$PACK_BUDGET_S" ]; then
        echo "RESULT: TIMEOUT (self-cap ${PACK_BUDGET_S}s breached at ${elapsed}s)"
        exit 124
    fi
}

# ─── Stage 1: tsc --noEmit ─────────────────────────────────────────────────
echo ""
echo "--- Stage 1: npx tsc --noEmit ---"
TSC_EXIT=0
set +e
timeout 150 npx tsc --noEmit
TSC_EXIT=$?
set -e
echo "TSC_EXIT=$TSC_EXIT"
check_cap

# ─── Stage 2: npm run build ────────────────────────────────────────────────
echo ""
echo "--- Stage 2: npm run build ---"
BUILD_EXIT=0
set +e
timeout 150 npm run build 2>&1 | tee "$BUILD_LOG"
BUILD_EXIT=${PIPESTATUS[0]}
set -e
echo "BUILD_EXIT=$BUILD_EXIT"
check_cap

# ─── SCSS / bundle-budget warnings: adjudication data only ─────────────────
echo ""
echo "--- SCSS / bundle-budget warnings (verbatim, adjudication data) ---"
if [ -f "$BUILD_LOG" ]; then
    # Capture the warning lines verbatim (Angular ng build emits WARNING and
    # the bundle budget keyword in warning lines).
    grep -E "WARNING|budget" "$BUILD_LOG" 2>/dev/null || echo "(no WARNING/budget lines)"
    SCSS_WARNING_COUNT=$(grep -cE "WARNING|budget" "$BUILD_LOG" 2>/dev/null || echo 0)
    SCSS_WARNING_COUNT=${SCSS_WARNING_COUNT:-0}
    echo "SCSS_WARNING_COUNT=$SCSS_WARNING_COUNT"
else
    echo "(build log missing: $BUILD_LOG)"
    SCSS_WARNING_COUNT=0
    echo "SCSS_WARNING_COUNT=$SCSS_WARNING_COUNT"
fi

# ─── Verdict ───────────────────────────────────────────────────────────────
echo ""
if [ "$TSC_EXIT" -eq 0 ] && [ "$BUILD_EXIT" -eq 0 ]; then
    echo "RESULT: PASS"
    exit 0
else
    echo "RESULT: FAIL (tsc=$TSC_EXIT build=$BUILD_EXIT)"
    exit 1
fi
