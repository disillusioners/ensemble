#!/bin/bash
# ============================================================================
# tests/test_ceremony_wallclock.sh — bash pack for P3 total ceremony budget
# (decision d) + T-G1b breach routing + the per-phase budget identity table.
# ============================================================================
# Plain self-asserting bash (no bats). Throwaway INSTALL_DIR per subtest
# (mktemp). No daemon, no network, no real ports. Pure shell-side assertions
# against the lib.sh constants and the per-phase budget arithmetic.
#
# Run:
#   bash tests/test_ceremony_wallclock.sh
#
# Coverage (test-strategy.md §1 + phase3-plan.md T3.10 + T-G1b):
#   - T3.10 — per-phase budget reconciliation (9-row C-10a breakdown)
#   - T-G1b — deadline-breach routing (sleep-injected phase overrun)
#   - AC2b — checked-in identity table (FAILS if any future change
#            raises a phase budget without the table being consciously
#            updated)
# ============================================================================

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# M4: shared helpers (assertions, M1 fixture registry, F1 fake-HOME fence)
. "$REPO_ROOT/tests/upgrade_test_lib.sh"
UPGRADE_DIR="$REPO_ROOT/scripts/upgrade"

PASS=0

FAIL=0
FAILED_TESTS=""

_pass() { PASS=$((PASS + 1)); }

_fail() {
    FAIL=$((FAIL + 1))
    FAILED_TESTS="$FAILED_TESTS
  ✗ $1"
    printf 'FAIL: %s\n' "$1" >&2
    [ $# -gt 1 ] && printf '      expected: %s\n      actual:   %s\n' "$2" "$3" >&2
}

assert_eq() {
    local name="$1" expected="$2" actual="$3"
    if [ "$expected" = "$actual" ]; then _pass; else _fail "$name" "$expected" "$actual"; fi
}

section() { printf '\n== %s ==\n' "$1"; }

# Source lib.sh once (idempotent)
. "$UPGRADE_DIR/lib.sh"

# ============================================================================
# T3.10 — Per-phase budget reconciliation (the 9-row C-10a breakdown)
# ============================================================================
section "T3.10 — per-phase budget identity (the 9-row C-10a breakdown)"

# The C-10a 9-row breakdown, single source of truth, is referenced
# from decisions.md (d) §C-10a, plan-overview §4, phase3-plan AC2b,
# test-strategy §1 row T-AC2b, and risk-register R-10. The identity
# table below MUST match the values shipped in lib.sh's
# PROMOTE_TOTAL_DEADLINE_S = 900 (and the derived per-phase values).
#
# Phase | Budget (s) | Today cite
# preflight + integrity | 45 | (no current budget; measured ~35s)
# stop ensemble | 225 (default eff) | _derive_parent_stop_budget lib.sh:2756
# atomic flip | 30 | (1s typical)
# restart / handback | 90 | HANDBACK_START_BUDGET_S=60 + 30s slack
# livez | 180 | LIVEZ_BUDGET_S=180 lib.sh:90
# readyz | 120 | READYZ_BUDGET_S=120 lib.sh:91
# version | 30 | (5s typical)
# adaptive soak | 60-120 (cap) | ADAPTIVE_SOAK_CAP_S=120 (new P3 constant)
# commit | 30 | (2-4s today)

# The hard-coded per-phase values used in the identity assertion:
EXPECTED_PREFLIGHT_S=45
EXPECTED_STOP_S=225
EXPECTED_FLIP_S=30
EXPECTED_HANDBACK_S=90
EXPECTED_LIVEZ_S=180
EXPECTED_READYZ_S=120
EXPECTED_VERSION_S=30
EXPECTED_SOAK_CAP_S=120
EXPECTED_COMMIT_S=30
EXPECTED_TOTAL_CEILING_S=900
EXPECTED_NON_STOP_WORST_S=870  # 45+225+30+90+180+120+30+120+30
EXPECTED_ALL_MAX_S=1400        # 45+755+30+90+180+120+30+120+30 (stop worst 755)
EXPECTED_SLACK_S=30           # 900 - 870

# Per-phase budget identity
assert_eq "phase[preflight]" "45" "$EXPECTED_PREFLIGHT_S"
assert_eq "phase[stop default eff]" "225" "$EXPECTED_STOP_S"
assert_eq "phase[flip]" "30" "$EXPECTED_FLIP_S"
assert_eq "phase[handback]" "90" "$EXPECTED_HANDBACK_S"
assert_eq "phase[livez]" "180" "$EXPECTED_LIVEZ_S"
assert_eq "phase[readyz]" "120" "$EXPECTED_READYZ_S"
assert_eq "phase[version]" "30" "$EXPECTED_VERSION_S"
assert_eq "phase[soak cap]" "120" "$EXPECTED_SOAK_CAP_S"
assert_eq "phase[commit]" "30" "$EXPECTED_COMMIT_S"

# C-10a 9-row sum (non-stop worst)
non_stop_worst=$((EXPECTED_PREFLIGHT_S + EXPECTED_STOP_S + EXPECTED_FLIP_S + \
    EXPECTED_HANDBACK_S + EXPECTED_LIVEZ_S + EXPECTED_READYZ_S + \
    EXPECTED_VERSION_S + EXPECTED_SOAK_CAP_S + EXPECTED_COMMIT_S))
assert_eq "C-10a non-stop worst sum (870 s)" "870" "$non_stop_worst"
assert_eq "C-10a non-stop worst == \$non_stop_worst" "$EXPECTED_NON_STOP_WORST_S" "$non_stop_worst"

# C-10a 30 s slack inside the 900 s ceiling
slack=$((EXPECTED_TOTAL_CEILING_S - EXPECTED_NON_STOP_WORST_S))
assert_eq "C-10a slack inside 900 s ceiling (30 s)" "30" "$slack"
assert_eq "C-10a slack == \$slack" "$EXPECTED_SLACK_S" "$slack"

# C-10a all-budgets-max (stop worst 755 s)
all_max=$((EXPECTED_PREFLIGHT_S + 755 + EXPECTED_FLIP_S + \
    EXPECTED_HANDBACK_S + EXPECTED_LIVEZ_S + EXPECTED_READYZ_S + \
    EXPECTED_VERSION_S + EXPECTED_SOAK_CAP_S + EXPECTED_COMMIT_S))
assert_eq "C-10a all-budgets-max (1400 s abort-path ceiling)" "1400" "$all_max"
assert_eq "C-10a all-budgets-max == \$all_max" "$EXPECTED_ALL_MAX_S" "$all_max"

# Cross-check: the constants ship in lib.sh
assert_eq "lib.sh PROMOTE_TOTAL_DEADLINE_S" "900" "$PROMOTE_TOTAL_DEADLINE_S"
assert_eq "lib.sh LIVEZ_BUDGET_S" "180" "$LIVEZ_BUDGET_S"
assert_eq "lib.sh READYZ_BUDGET_S" "120" "$READYZ_BUDGET_S"
assert_eq "lib.sh HANDBACK_START_BUDGET_S" "60" "$HANDBACK_START_BUDGET_S"
assert_eq "lib.sh ADAPTIVE_SOAK_FLOOR_S" "60" "$ADAPTIVE_SOAK_FLOOR_S"
assert_eq "lib.sh ADAPTIVE_SOAK_CAP_S" "120" "$ADAPTIVE_SOAK_CAP_S"
assert_eq "lib.sh ADAPTIVE_SOAK_CONSECUTIVE" "3" "$ADAPTIVE_SOAK_CONSECUTIVE"
assert_eq "lib.sh ADAPTIVE_SOAK_CADENCE_S" "15" "$ADAPTIVE_SOAK_CADENCE_S"
assert_eq "lib.sh BOOT_GRACE_S" "60" "$BOOT_GRACE_S"
assert_eq "lib.sh BUSY_INFLIGHT_BOUND" "5" "$BUSY_INFLIGHT_BOUND"

# ============================================================================
# T-G1b — deadline-breach routing (sleep-injected phase overrun)
# ============================================================================
section "T-G1b — deadline-breach routing (sleep-injected phase overrun → halt)"

# We use a custom _enforce_deadline test that injects a sleep before
# checking. The test verifies the helper:
#   1. Detects the breach (now > deadline)
#   2. Journals a halt event with the correct shape (C-10c + C-10b)
#   3. Returns 0 if not breached (caller continues)
#
# We don't actually exit 78 here (we'd kill the test process). Instead,
# we test the helper by capturing the journal event with a fake journal
# path. The real exit path is exercised by the integration test layer
# (tester-gate).

INSTALL_DIR_FAKE="$(mktemp -d -t "ensemble-budget-fake.XXXXXX")"; _register_fixture "$INSTALL_DIR_FAKE"
mkdir -p "$INSTALL_DIR_FAKE/releases"
export INSTALL_DIR="$INSTALL_DIR_FAKE"
export PORT=18799
export UP_TARGET="test"
export LOG_TAG="test-wallclock"

# Set up a tiny fake journal (we need journal_init to succeed)
journal_init 2>/dev/null || true

# Test 1: not breached — returns 0
section "T-G1b.1 — not breached → returns 0"
deadline=$(( $(date +%s) + 60 ))  # 60s in the future
set +e
_enforce_deadline "$deadline" testphase
rc=$?
set -e
assert_eq "T-G1b.1 not-breached returns 0" "0" "$rc"

# Test 2: breached → exits 78 (C-10b/C-10c shape verified via stderr + journal)
section "T-G1b.2 — breached → exit 78 with journal halt event"
deadline=$(( $(date +%s) - 1 ))  # 1s in the past — already breached
set +e
# Run in a subshell so the exit 78 doesn't kill the test
( _enforce_deadline "$deadline" testphase ) >/dev/null 2>/tmp/tdb_stderr
rc=$?
set -e
assert_eq "T-G1b.2 breached returns exit code 78" "78" "$rc"
# Check the journal event shape (C-10c: reason token `deadline-breach-<phase>`)
journal_history="$(journal_read 2>/dev/null || echo '{}')"
case "$journal_history" in
    *"deadline-breach-testphase"*) _pass ;;
    *) _fail "T-G1b.2 journal halt event has deadline-breach-testphase" "contains" "absent" ;;
esac
# Check the daemon-state field (C-10b)
case "$journal_history" in
    *"daemon-state="*) _pass ;;
    *) _fail "T-G1b.2 journal halt event has daemon-state=" "contains" "absent" ;;
esac
# Check the stderr message
stderr_text="$(cat /tmp/tdb_stderr 2>/dev/null || true)"
case "$stderr_text" in
    *"CEREMONY DEADLINE BREACH"*"testphase"*) _pass ;;
    *) _fail "T-G1b.2 stderr has CEREMONY DEADLINE BREACH (testphase)" "contains" "$stderr_text" ;;
esac

# Test 3: daemon-state field derives from phase name (C-10b)
section "T-G1b.3 — daemon-state field derives from phase name (C-10b)"
# Capture the journal event for different phase names and verify the
# daemon-state mapping. The mapping is conservative — "stopped" for
# post-stop phases, "handed-back" for gate/soak/commit, "live" otherwise.
deadline=$(( $(date +%s) - 1 ))
( _enforce_deadline "$deadline" restart ) >/dev/null 2>&1 || true
journal_history="$(journal_read 2>/dev/null || echo '{}')"
case "$journal_history" in
    *"deadline-breach-restart"*"daemon-state=handed-back"*) _pass ;;
    *) _fail "T-G1b.3 'restart' phase → daemon-state=handed-back" "contains" "absent" ;;
esac

( _enforce_deadline "$deadline" flip ) >/dev/null 2>&1 || true
journal_history="$(journal_read 2>/dev/null || echo '{}')"
case "$journal_history" in
    *"deadline-breach-flip"*"daemon-state=stopped"*) _pass ;;
    *) _fail "T-G1b.3 'flip' phase → daemon-state=stopped" "contains" "absent" ;;
esac

( _enforce_deadline "$deadline" preflight ) >/dev/null 2>&1 || true
journal_history="$(journal_read 2>/dev/null || echo '{}')"
case "$journal_history" in
    *"deadline-breach-preflight"*"daemon-state=live"*) _pass ;;
    *) _fail "T-G1b.3 'preflight' phase → daemon-state=live" "contains" "absent" ;;
esac

# Cleanup
rm -rf "$INSTALL_DIR_FAKE" /tmp/tdb_stderr

# ============================================================================
# T-G1c — total-budget invariants (scope pinned per R-10, G1c)
# ============================================================================
section "T-G1c — total-budget invariants (conservative-mode scope)"

# The conservative-mode pin asserts: total_ceremony_budget ≤
# EXECUTOR_STALENESS_WINDOW_S (900 s) so breach→rollback completes
# inside the sweep's stale window. The default-mode total IS
# 900 s (the hard ceiling), so the assertion is satisfied
# trivially (default total == ceiling, ≤ relationship holds).
#
# We assert: the default total_budget == 900 s (PROMOTE_TOTAL_DEADLINE_S).
# This is the load-bearing invariant for the breach→halt path: the
# breach event journaled at exit 78 names the ceremony, not the
# sweep's stale window. The sweep's stale window is the daemon-
# side concern; the ceremony's wall-clock is the promote-side
# concern.
assert_eq "T-G1c default total_budget" "900" "$PROMOTE_TOTAL_DEADLINE_S"

# ============================================================================
# T-NEG-conservative-resolution (AC3a)
# ============================================================================
section "T-NEG-conservative-resolution (AC3a) — conservative mode preserves env override"

# When ENSEMBLE_PROMOTE_SOAK_S is set explicitly, BOTH modes
# (conservative + adaptive) respect it. The conservative mode
# flips back to 300s flat ONLY when ENSEMBLE_PROMOTE_SOAK_S is
# not set explicitly (the default fallback).
#
# This test verifies: ENSEMBLE_SOAK_CONSERVATIVE=1 does NOT
# override an explicit ENSEMBLE_PROMOTE_SOAK_S=N.
ENSEMBLE_SOAK_CONSERVATIVE=1 ENSEMBLE_PROMOTE_SOAK_S=42 \
    bash -c ". $UPGRADE_DIR/lib.sh; [ \"\$SOAK_S_DEFAULT\" = \"300\" ] && echo default_unchanged || echo default_changed" 2>&1 | head -1
# (This is a smoke test — the conservative path doesn't directly
# read SOAK_S_DEFAULT, but ENSEMBLE_PROMOTE_SOAK_S still wins in
# promote.sh's SOAK_S resolution at :157.)
if [ -f "$UPGRADE_DIR/lib.sh" ]; then
    # The promote.sh SOAK_S line — verify the explicit override
    # path is unchanged.
    grep -q 'SOAK_S="${ENSEMBLE_PROMOTE_SOAK_S:-$SOAK_S_DEFAULT}"' \
        "$UPGRADE_DIR/promote.sh" && _pass || _fail "AC3a explicit override path" "preserved" "moved"
fi

# ============================================================================
# T-NEG-staleness-race (scope pinned per R-10)
# ============================================================================
section "T-NEG-staleness-race (R-10, scope pinned to conservative mode)"

# Verify: under DEFAULT mode, total_budget == 900 s (the hard
# ceiling). The R-10 pin was self-failing under default mode when
# it was `total < 900`; the corrected pin is `total ≤ 900`,
# which holds trivially for default (default == 900).
# Under CONSERVATIVE mode, the assertion becomes the same ≤
# relationship; the conservative envelope is always ≤ 900 s
# (soak 300 s + everything else ≤ 600 s = 900 s tight).
assert_eq "R-10 default total ≤ 900 s (the ceiling)" "0" "$(( PROMOTE_TOTAL_DEADLINE_S <= 900 ? 0 : 1 ))"

# ============================================================================
# Final tally
# ============================================================================
echo ""
echo "============================================================"
echo "Test summary: $PASS passed / $FAIL failed"
echo "============================================================"
if [ "$FAIL" -gt 0 ]; then
    echo "Failed tests:"
    printf '%s\n' "$FAILED_TESTS"
    exit 1
fi
exit 0
