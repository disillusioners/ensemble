#!/usr/bin/env bash
# tests/packs/plane_shell_scrub_content.sh — the P10 static-content pack
#
# Lane plane-pm-issue-tools Phase 4 (phase4-plan.md §2.5 / §4 / §5 pin P10 /
# §6 exit criterion X4.12). The lane does NOT modify scripts/deploy.sh or
# scripts/run_tests_scrubbed.sh (NR2 — byte-identical to base); the PROOF
# that the shell-layer scrubs stay intact is this pack: it READS the two
# shell files and asserts the doctrine-bearing lines still exist.
#
# Assertions:
#   (a) scripts/deploy.sh still contains the PLANE_MCP_WORKSPACE_SLUG=e2e
#       sed-pin AND its grep-assert (the demo e2e pin).
#   (b) scripts/run_tests_scrubbed.sh still contains the per-variable unset
#       of PLANE_MCP_WORKSPACE_SLUG/PLANE_BASE_URL AND the leak-gate case
#       arm AND the leak-gate `exit 78` (at :101 per the G2 fold anchor).
#   (c) positive control — the intact files themselves go green (inherent
#       in (a)+(b); stated for the record).
#
# RED leg (mutation, in a SCRATCH copy only — the real files are never
# touched): the asserted lines are deleted from scratch copies and the
# SAME containment assertions are re-run against them EXPECTING to fail.
# A pack whose RED side cannot fire is vacuous — the mutation legs keep
# it alive. Expected-RED firings are tallied as PASS, never as FAIL.
#
# Usage: bash tests/packs/plane_shell_scrub_content.sh
# Exit:  0 = repo assertions green AND both mutation legs fired RED as
#        expected; 1 = any repo assertion failed OR a mutation leg went
#        unexpectedly green (both are defects).

set -u

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
DEPLOY="$REPO_ROOT/scripts/deploy.sh"
SCRUB="$REPO_ROOT/scripts/run_tests_scrubbed.sh"

PASS=0
FAIL=0
log_pass() { PASS=$((PASS + 1)); printf 'PASS: %s\n' "$*"; }
log_fail() { FAIL=$((FAIL + 1)); printf 'FAIL: %s\n' "$*" >&2; }

# assert_contains <file> <label> <needle> — fixed-string containment,
# used ONLY against the real repo files (green tally).
assert_contains() {
    local f="$1" label="$2" needle="$3"
    if grep -qF -- "$needle" "$f" 2>/dev/null; then
        log_pass "[$label] contains: $needle"
        return 0
    else
        log_fail "[$label] MISSING: $needle"
        return 1
    fi
}

# expect_assertion_fires <file> <label> <needle> — the mutation proof:
# the SAME containment assertion that guards the real file must FAIL on
# the weakened scratch copy. A SUCCESS here means the pack cannot detect
# the weakening — vacuous — and is tallied as a defect.
expect_assertion_fires() {
    local f="$1" label="$2" needle="$3"
    if grep -qF -- "$needle" "$f" 2>/dev/null; then
        log_fail "[mutation $label] weakened copy STILL PASSED (pack vacuous): $needle"
        return 1
    fi
    log_pass "[mutation $label] weakened copy correctly RED: $needle"
    return 0
}

echo "== P10: shell-layer scrub content assertions (repo files, read-only) =="

# (a) deploy.sh — the demo e2e pin: sed replacement + grep assert.
assert_contains "$DEPLOY" deploy.sh \
    '-e "s/^PLANE_MCP_WORKSPACE_SLUG=.*/PLANE_MCP_WORKSPACE_SLUG=e2e/"'
assert_contains "$DEPLOY" deploy.sh \
    "grep -q '^PLANE_MCP_WORKSPACE_SLUG=e2e\$'"

# (b) run_tests_scrubbed.sh — the per-variable unset, the leak-gate case
# arm, and the leak-gate exit 78 (at :101 per the G2 fold anchor).
assert_contains "$SCRUB" run_tests_scrubbed.sh \
    "unset PLANE_MCP_WORKSPACE_SLUG PLANE_BASE_URL"
assert_contains "$SCRUB" run_tests_scrubbed.sh \
    'PLANE_MCP_WORKSPACE_SLUG|PLANE_BASE_URL)'
assert_contains "$SCRUB" run_tests_scrubbed.sh \
    'exit 78  # EX_CONFIG'

echo "== P10: mutation legs (SCRATCH copies — the repo files are never modified) =="

# Scratch copies under mktemp (never a live-shaped/HOME-derived install path).
SCRATCH_DIR="$(mktemp -d)"
trap 'rm -rf "$SCRATCH_DIR"' EXIT

# Mutation 1 — weaken deploy.sh's e2e pin in the copy: drop the sed-pin and
# the grep-assert lines; the same assertions must go RED on the copy.
sed -e '/s\/\^PLANE_MCP_WORKSPACE_SLUG=.*\/PLANE_MCP_WORKSPACE_SLUG=e2e\/"/d' \
    -e '/grep -q .\^PLANE_MCP_WORKSPACE_SLUG=e2e\$./d' \
    "$DEPLOY" > "$SCRATCH_DIR/deploy.mutated"
expect_assertion_fires "$SCRATCH_DIR/deploy.mutated" deploy.sh \
    '-e "s/^PLANE_MCP_WORKSPACE_SLUG=.*/PLANE_MCP_WORKSPACE_SLUG=e2e/"'
expect_assertion_fires "$SCRATCH_DIR/deploy.mutated" deploy.sh \
    "grep -q '^PLANE_MCP_WORKSPACE_SLUG=e2e\$'"

# Mutation 2 — weaken run_tests_scrubbed.sh in the copy: drop the unset +
# the leak-gate case arm + the exit-78 line; each assertion must go RED.
sed -e '/^unset PLANE_MCP_WORKSPACE_SLUG PLANE_BASE_URL$/d' \
    -e '/PLANE_MCP_WORKSPACE_SLUG|PLANE_BASE_URL)/d' \
    -e '/^    exit 78  # EX_CONFIG/d' \
    "$SCRUB" > "$SCRATCH_DIR/scrub.mutated"
expect_assertion_fires "$SCRATCH_DIR/scrub.mutated" run_tests_scrubbed.sh \
    "unset PLANE_MCP_WORKSPACE_SLUG PLANE_BASE_URL"
expect_assertion_fires "$SCRATCH_DIR/scrub.mutated" run_tests_scrubbed.sh \
    'PLANE_MCP_WORKSPACE_SLUG|PLANE_BASE_URL)'
expect_assertion_fires "$SCRATCH_DIR/scrub.mutated" run_tests_scrubbed.sh \
    'exit 78  # EX_CONFIG'

echo ""
echo "== P10 summary: $PASS passed, $FAIL failed (repo assertions green + mutation legs RED as expected) =="
if [ "$FAIL" -eq 0 ]; then
    echo "P10: GREEN — shell scrubs intact (NR2) and the pack's RED legs fire."
    exit 0
fi
echo "P10: RED — see the FAIL lines above." >&2
exit 1
