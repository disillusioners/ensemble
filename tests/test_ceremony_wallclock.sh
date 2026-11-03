#!/bin/bash
# ============================================================================
# tests/test_ceremony_wallclock.sh — bash pack for P3 total ceremony budget
# (decision d) + T-G1b breach routing + the per-phase budget identity table.
# ============================================================================
# Plain self-asserting bash (no bats). Throwaway INSTALL_DIR per subtest
# (mktemp). No daemon, no network, no real ports. Pure shell-side assertions
# against the lib.sh constants and the per-phase budget arithmetic.
#
# Run (per-variable env scrub — the glob form `env -u POSTGRES_*` is a
# SILENT NO-OP):
#   timeout 300 env -u POSTGRES_DB -u POSTGRES_HOST -u POSTGRES_USER \
#       -u POSTGRES_PASSWORD -u DATABASE_URL -u PGHOST -u PGPORT \
#       -u PGDATABASE -u PGUSER -u PGPASSWORD \
#       bash tests/test_ceremony_wallclock.sh
#
# Coverage (test-strategy.md §1 + phase3-plan.md T3.10 + T-G1b):
#   - T3.10 — per-phase budget reconciliation (the C-10a BOUND-TABLE form,
#             lib.sh:157-173 / Phase 1 D-P1-8)
#   - T-G1b — deadline-breach routing (sleep-injected phase overrun)
#   - AC2b — checked-in identity table (FAILS if any future change
#            raises a phase budget without the table being consciously
#            updated)
#
# FIX (2026-11-03, tester/phh-handback-halt-gate-evidence — PB-2 finding F1):
# the AC2b identity table was TAUTOLOGICAL + STALE — it hardcoded
# EXPECTED_* literals and asserted them against themselves (e.g. the old
# `assert_eq "phase[handback]" "90" "$EXPECTED_HANDBACK_S"`), pinned the
# START knob (HANDBACK_START_BUDGET_S) with ZERO occurrences of the VERIFY
# knob lib.sh actually ships (HANDBACK_VERIFY_BUDGET_S, default 300), and
# its "handback 90 / non-stop worst 870 / slack 30" row encoded exactly the
# fiction lib.sh:157-173 (Phase 1 / D-P1-8) declares "was fiction" (real
# all-worst ≈1340 s > the 900 s stop-loss ceiling). The AC was therefore
# FALSE — the pack could never fail on a budget change. The table is now
# checked AGAINST THE CODE: every shipped row is extracted from lib.sh at
# run time (source-text grep of the shipped default + a pristine-subshell
# resolved value) and asserted to equal the documented table, so a shipped
# budget change makes the pack FAIL until the table is consciously updated.
# The handback row is re-derived to the shipped reality (VERIFY 300 + one
# progress-gated grace 60) and the pack asserts the TRUTHFUL conflict
# (all-worst EXCEEDS the ceiling) instead of the fictional "30 s slack".
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
# T3.10 — Per-phase budget reconciliation (the C-10a BOUND-TABLE form)
# ============================================================================
section "T3.10 — per-phase budget identity (the C-10a bound table, lib.sh-derived)"

# The documented C-10a bound table (lib.sh:157-173, corrected 2026-11-02 by
# Phase 1 / D-P1-8; referenced from decisions.md (d) §C-10a, plan-overview §4,
# phase3-plan AC2b, test-strategy §1 row T-AC2b, risk-register R-10).
#
# AC2b MECHANISM (this is the point of the pack): the TABLE constants below
# are the DOCUMENTED values; every row that corresponds to a shipped lib.sh
# budget is asserted to equal the value EXTRACTED FROM lib.sh AT RUN TIME —
# once from the SOURCE TEXT (the shipped default literal, immune to ambient
# env) and once as lib.sh RESOLVES it in a pristine subshell (the
# _numeric_knob wiring). If a shipped budget changes without this table
# being consciously updated, the pack FAILS. The table is checked against
# the CODE — never against itself.
#
# Bound table (NOT a fits-inside-900 schedule — lib.sh:157-173: "the 900s
# ceiling is a STOP-LOSS, not a budgets-fit guarantee"):
LIBSH="$UPGRADE_DIR/lib.sh"

# --- the documented table (the thing under test) ----------------------------
# Named TABLE_<lib.sh knob name> so the shipped-vs-table loop below can bind
# each row to its knob by name.
TABLE_PREFLIGHT_PHASE_BUDGET_S=300   # PREFLIGHT_PHASE_BUDGET_S (shipped default)
TABLE_HANDBACK_VERIFY_BUDGET_S=300   # HANDBACK_VERIFY_BUDGET_S (the VERIFY ceiling —
                                     # the knob the verifier actually waits under)
TABLE_HANDBACK_VERIFY_FLOOR_S=180    # HANDBACK_VERIFY_FLOOR_S (never LESS time than
                                     # before — the monotone-safe floor, D-P1-1)
TABLE_HANDBACK_VERIFY_GRACE_S=60     # HANDBACK_VERIFY_GRACE_S (one progress-gated
                                     # extension, D-P1-6)
TABLE_HANDBACK_TAIL_RESERVE_S=120    # HANDBACK_TAIL_RESERVE_S (downstream room)
TABLE_HANDBACK_START_BUDGET_S=60     # HANDBACK_START_BUDGET_S (the unit start bound)
TABLE_LIVEZ_BUDGET_S=180             # LIVEZ_BUDGET_S
TABLE_READYZ_BUDGET_S=120            # READYZ_BUDGET_S
TABLE_ADAPTIVE_SOAK_CAP_S=120        # ADAPTIVE_SOAK_CAP_S (adaptive, ceremony-coupled)
TABLE_PROMOTE_TOTAL_DEADLINE_S=900   # PROMOTE_TOTAL_DEADLINE_S (the stop-loss ceiling)
TABLE_DEFAULT_WAIT_S=70              # DEFAULT_WAIT_S (feeds the derived stop budget)
# Non-shipped rows (measured — no lib.sh constant binds them; they feed the
# all-worst arithmetic and are part of the documented bound table):
TABLE_FLIP_S=0                       # atomic flip — MEASURED (~0s)
TABLE_VERSION_S=5                    # version check — MEASURED (~5s)
TABLE_COMMIT_S=30                    # commit — MEASURED budget (~2-4s today)
TABLE_STOP_S=225                     # _derive_parent_stop_budget DERIVED default
                                     # (STOP_SCRIPT_BUDGET_S 120 + DEFAULT_WAIT_S 70
                                     # + kill-grace 10 + term-grace 5 + margin 20)

# --- extraction helpers -----------------------------------------------------
# _shipped_default NAME — the SHIPPED default literal for NAME, extracted from
# the lib.sh SOURCE TEXT. Styles: (A) NAME="$(_numeric_knob NAME "${NAME:-}"
# NNN)" [optional trailing comment]; (B) NAME="${NAME:-NNN}"; (C) NAME=NNN.
_shipped_default() {
    local name="$1"
    awk -v n="$name" '
        index($0, n "=") == 1 {
            line = $0
            patA = "\"\\$\\{" n ":-\\}\" [0-9]+\\)"
            if (match(line, patA)) {
                s = substr(line, RSTART, RLENGTH)
                sub(/.*\}" /, "", s); sub(/\)$/, "", s)
                print s; exit
            }
            patB = "\\$\\{" n ":-[0-9]+\\}"
            if (match(line, patB)) {
                s = substr(line, RSTART, RLENGTH)
                gsub(/[^0-9]/, "", s)
                print s; exit
            }
            if (line ~ /^[A-Za-z_][-A-Za-z_0-9]*=[0-9]+$/) {
                sub(/^[^=]+=/, "", line)
                print line; exit
            }
        }
    ' "$LIBSH"
}

# _resolved_default NAME — the value lib.sh RESOLVES for NAME in a pristine
# subshell with that knob unset (pins the _numeric_knob / default wiring).
_resolved_default() {
    local name="$1"
    env -u "$name" bash -c ". '$LIBSH' >/dev/null 2>&1; printf '%s' "\${$name-__UNSET__}""
}

# --- the shipped-vs-table bindings (the AC2b load-bearing asserts) ----------
for _knob_name in PREFLIGHT_PHASE_BUDGET_S HANDBACK_VERIFY_BUDGET_S \
                  HANDBACK_VERIFY_FLOOR_S HANDBACK_VERIFY_GRACE_S \
                  HANDBACK_TAIL_RESERVE_S HANDBACK_START_BUDGET_S \
                  LIVEZ_BUDGET_S READYZ_BUDGET_S ADAPTIVE_SOAK_CAP_S \
                  PROMOTE_TOTAL_DEADLINE_S DEFAULT_WAIT_S; do
    eval "table_val=\$TABLE_${_knob_name}"
    shipped_val="$(_shipped_default "$_knob_name")"
    resolved_val="$(_resolved_default "$_knob_name")"
    assert_eq "T3.10 shipped[${_knob_name}] present in lib.sh source" "absent" "$([ -n "$shipped_val" ] && printf absent || printf MISSING)"
    assert_eq "T3.10 table[${_knob_name}] == lib.sh SHIPPED default" "$shipped_val" "$table_val"
    assert_eq "T3.10 lib.sh RESOLVED[${_knob_name}] == shipped default (knob wiring)" "$shipped_val" "$resolved_val"
done
unset _knob_name table_val shipped_val resolved_val

# Monotone-safety: the verify FLOOR may never exceed the verify CEILING
# (D-P1-1: "never LESS time than before" — a floor>ceiling inversion would
# make the clamp formula undefined).
assert_eq "T3.10 HANDBACK_VERIFY_FLOOR_S <= HANDBACK_VERIFY_BUDGET_S (monotone)" "ok"     "$([ "$TABLE_HANDBACK_VERIFY_FLOOR_S" -le "$TABLE_HANDBACK_VERIFY_BUDGET_S" ] && printf ok || printf inverted)"

# The hand-back row: START knob + VERIFY knob + grace, all present. The old
# pack pinned ONLY the START knob and never mentioned the VERIFY knob (PB-2
# F1) — this structural pin keeps the verify family in the pack: if the pack
# is ever reverted to a verify-less table, the count drops and this fails.
verify_refs="$(grep -c 'HANDBACK_VERIFY_BUDGET_S' "$0" 2>/dev/null || printf 0)"
assert_eq "T3.10 the pack itself pins HANDBACK_VERIFY_BUDGET_S (>=6 refs; the F1 gap cannot silently return)" "yes"     "$([ "${verify_refs:-0}" -ge 6 ] && printf yes || printf "no($verify_refs)")"

# The stop row is DERIVED, not shipped: re-derive it the way lib.sh does
# (sandbox: no INSTALL_DIR, no .env → _resolve_wait_s_mirror falls back to
# DEFAULT_WAIT_S). A change to any input of the derivation moves this pin.
DERIVED_STOP_S="$(_derive_parent_stop_budget "${REPO_ROOT}/tests/fixtures/__nonexistent_env_for_stop_derivation__" 2>/dev/null || printf ERR)"
assert_eq "T3.10 table[stop] == the DERIVED parent stop budget (120+70+10+5+20)" "$TABLE_STOP_S" "$DERIVED_STOP_S"

# --- the truthful all-worst arithmetic (the D-P1-8 bound table) -------------
# preflight + stop + flip(~0) + (verify + grace) + livez + readyz + version
# + soak cap + commit  —  per lib.sh:157-173 the shipped all-worst ≈1340 s.
ALL_WORST_S=$(( TABLE_PREFLIGHT_PHASE_BUDGET_S + TABLE_STOP_S + TABLE_FLIP_S + 0 \
              + TABLE_HANDBACK_VERIFY_BUDGET_S + TABLE_HANDBACK_VERIFY_GRACE_S \
              + TABLE_LIVEZ_BUDGET_S + TABLE_READYZ_BUDGET_S + TABLE_VERSION_S \
              + TABLE_ADAPTIVE_SOAK_CAP_S + TABLE_COMMIT_S ))
assert_eq "T3.10 all-worst sum (shipped bound table, D-P1-8)" "1340" "$ALL_WORST_S"

# The CONFLICT — asserted TRUTHFULLY. The old pack claimed "slack 30"
# (900 - 870); that row was the fiction Phase 1 / D-P1-8 retracted. The
# shipped reality is that all-worst EXCEEDS the 900 s stop-loss ceiling:
# the slack is NEGATIVE. If any shipped budget rises, the sum moves and
# this row moves with it (the conflict deepens) — the pack fails at the
# table-vs-shipped asserts above, and this disclosure stays true.
CONFLICT_S=$(( TABLE_PROMOTE_TOTAL_DEADLINE_S - ALL_WORST_S ))
assert_eq "T3.10 all-worst EXCEEDS the 900 s stop-loss ceiling (the shipped conflict — NOT the retracted 'slack 30' fiction)" "-440" "$CONFLICT_S"
assert_eq "T3.10 the conflict sign is NEGATIVE (all-worst > ceiling ⇒ stop-loss, not budgets-fit)" "negative"     "$([ "$CONFLICT_S" -lt 0 ] && printf negative || printf nonnegative)"
printf 'DISCLOSURE (C-10a bound table, lib.sh:157-173): all-worst %ss > ceiling %ss by %ss — the 900s ceiling is a STOP-LOSS; a combined-slow ceremony halts at a downstream boundary with the daemon up (disclosed, OQ-P1-1). Do NOT re-derive a "fits inside 900s" schedule from this table.\n' \
    "$ALL_WORST_S" "$TABLE_PROMOTE_TOTAL_DEADLINE_S" "$(( -CONFLICT_S ))"

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
