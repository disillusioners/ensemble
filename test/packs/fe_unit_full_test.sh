#!/usr/bin/env bash
# Test Pack: fe_unit_full_test
# FULL frontend Jest unit suite — every *.spec.ts under frontend/ per
# frontend/jest.config.js (testMatch: **/*.spec.ts). Ad-hoc pack prepped for
# the FE source-edit-modal verification round (feature/fix-source-edit-agent).
#
# Invocation:
#   EXPECTED_BRANCH=feature/fix-source-edit-agent bash test/packs/fe_unit_full_test.sh
#
# KNOWN TRAP (same shape as fe_static_typecheck_build_test.sh, which defaults
# to feature/mission-class): EXPECTED_BRANCH defaults to
# feature/fix-source-edit-agent here. Any non-default branch MUST pass
# EXPECTED_BRANCH=<branch> explicitly or Stage 0 hard-fails DRIFT spuriously.
#
# What runs : CI=1 npx jest --silent   (from frontend/; package.json "test"="jest")
# Self-cap  : internal `timeout 240` around jest (full suite ~12.3s on
#             2026-09-14, so 240s is generous). Outer callers should
#             additionally wrap with `timeout 300`.
# Output    : === Test Pack: fe_unit_full_test === / [jest output] /
#             final "Tests:" counts line / RESULT: PASS|FAIL|TIMEOUT
# Exit codes: 0 = PASS, 1 = FAIL (incl. DRIFT), 124 = TIMEOUT.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
JEST_LOG="/tmp/fe_unit_full_jest.log"
INTERNAL_TIMEOUT_S=240

echo "=== Test Pack: fe_unit_full_test ==="

# ─── Stage 0: rev-parse bracket (worktree drift gate) ──────────────────────
EXPECTED_BRANCH="${EXPECTED_BRANCH:-feature/fix-source-edit-agent}"
BRANCH="$(git -C "$PROJECT_DIR" rev-parse --abbrev-ref HEAD 2>/dev/null || echo UNKNOWN)"
SHA_BEFORE="$(git -C "$PROJECT_DIR" rev-parse --short HEAD 2>/dev/null || echo UNKNOWN)"
echo "BRANCH=$BRANCH"
echo "GIT_SHA=$SHA_BEFORE"
if [ "$BRANCH" != "$EXPECTED_BRANCH" ]; then
    echo "RESULT: FAIL (DRIFT) — branch=$BRANCH expected=$EXPECTED_BRANCH"
    exit 1
fi

cd "$PROJECT_DIR/frontend"
export CI=1

# ─── Stage 1: full jest suite under an internal subprocess timer ───────────
# set +e guard: jest's non-zero exit must be CAPTURED, not fatal, so the
# RESULT line always prints. Stream via tee; timeout's exit lands in
# PIPESTATUS[0] (124 = internal self-cap breached).
JEST_EXIT=0
set +e
timeout "$INTERNAL_TIMEOUT_S" npx jest --silent 2>&1 | tee "$JEST_LOG"
JEST_EXIT=${PIPESTATUS[0]}
set -e

# Final counts line — adjudication data printed verbatim into pack output.
COUNTS_LINE="$(grep -E '^[[:space:]]*Tests:' "$JEST_LOG" | tail -1 || true)"
if [ -n "$COUNTS_LINE" ]; then
    echo "$COUNTS_LINE"
else
    echo "(no 'Tests:' summary line — suite did not complete)"
fi

# ─── Mid-run SHA drift check ────────────────────────────────────────────────
SHA_AFTER="$(git -C "$PROJECT_DIR" rev-parse --short HEAD 2>/dev/null || echo UNKNOWN)"
echo "GIT_SHA_AFTER=$SHA_AFTER"
if [ "$SHA_AFTER" != "$SHA_BEFORE" ]; then
    echo "RESULT: FAIL (DRIFT-MID-RUN: $SHA_BEFORE -> $SHA_AFTER)"
    exit 1
fi

# ─── Verdict ────────────────────────────────────────────────────────────────
if [ "$JEST_EXIT" -eq 124 ]; then
    echo "RESULT: TIMEOUT"
    exit 124
elif [ "$JEST_EXIT" -eq 0 ]; then
    echo "RESULT: PASS"
    exit 0
else
    echo "RESULT: FAIL (jest exit=$JEST_EXIT)"
    exit 1
fi
