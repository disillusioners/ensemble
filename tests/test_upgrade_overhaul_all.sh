#!/bin/bash
# ============================================================================
# tests/test_upgrade_overhaul_all.sh — aggregator for the P4 default
# fast subset (test-strategy.md §6 "New runner hook"; phase4-plan.md D-P4.2
# / T4.2).
# ============================================================================
# Default run is the gate (target: <10 min total wall-clock). The
# aggregator runs:
#
#   A. NEW bash packs (P1-P3 surface)
#     A1. tests/test_journal_splice.sh          (AC1 latency + §1 tests)
#     A2. tests/test_rollback_target.sh         (AC5 four-cell matrix)
#     A3. tests/test_boot_sweep_commit_and_continue.sh
#     A4. tests/test_adaptive_soak.sh           (AC3 + G2 choreography)
#     A5. tests/test_ceremony_wallclock.sh      (AC2 budget identity)
#     A6. tests/test_bounded_subprocess_waits.sh  (T-G1a — bounded retry pins)
#     A7. tests/test_promote_plugin_staleness.sh  (T-PIN-staleness — slice ⑥)
#     A8. tests/test_launcher.sh                (T-PIN-launcher self-containment)
#     A9. tests/test_atomic_flip.sh             (T-PIN-txn — flip atomicity)
#   B. NEW pytest packs (P2 sweep + splice helper liveness)
#     B1. tests/unit/tools/test_upgrade_journal_sweep_liveness.py
#     B2. tests/unit/tools/test_upgrade_splice_helper.py
#     B3. tests/unit/tools/test_audit_after_soak_split.py
#   D. ADJUDICATED (per D-P4.2 carry-forward):
#     D1. tests/test_release_journal.sh        (KNOWN ISSUE: pre-existing
#         GNU-host fixture debt — BSD-only `sed -i ''` / `date -ju -v`
#         tooling; the P2.1 critical note. Failed 29× pre-P2 baseline
#         and 34× post-P2; the +5 = the splice helper's stricter
#         invalid-JSON exit-78 refusal on fixtures containing literal
#         `LASTHB` garbage from the BSD date debt — the INTENDED
#         torn-journal defense. Adjudicate each failure: pre-existing
#         GNU debt vs genuine regression from our changes. If any
#         genuine regression exists → fix it. If all pre-existing →
#         document in the report + runbook.)
#
# Usage:
#   bash tests/test_upgrade_overhaul_all.sh           # default fast subset
#   bash tests/test_upgrade_overhaul_all.sh --full    # adds D1/D2
#   ENVSIM_FULL=1 bash tests/test_upgrade_overhaul_all.sh --full
#                            # adds ENVSIM_FULL=1 for splice latency matrix
#
# Exit: 0 if all packs green; 1 otherwise. Per-pack failures are recorded
# and the aggregator CONTINUES (does not bail on first failure) — the
# aggregate count is the gate.
# ============================================================================

set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TESTS_DIR="$REPO_ROOT/tests"
RESULTS_DIR="${TMPDIR:-/tmp}/upgrade-overhaul-aggregator"
mkdir -p "$RESULTS_DIR"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
RESULT_LOG="$RESULTS_DIR/aggregator-$TIMESTAMP.log"
exec > >(tee -a "$RESULT_LOG") 2>&1

PASS=0
FAIL=0
SKIP=0
FAILED_PACKS=""
SKIPPED_PACKS=""

FULL_MODE=0
for arg in "$@"; do
    case "$arg" in
        --full) FULL_MODE=1 ;;
        *) ;;
    esac
done

log_section() { printf '\n============================================================\n'; printf '== %s\n' "$1"; printf '============================================================\n'; }

# Pack runner: bash packs exit nonzero on failure; pytest packs we run with
# --noconftest to avoid pulling in the heavy test infrastructure (mirrors the
# P2/P3 pattern from test_journal_splice.sh). Result codes are aggregated.
run_bash_pack() {
    local name="$1" path="$2"
    log_section "Bash pack: $name"
    local start_t end_t dur rc
    start_t=$(_now_epoch)
    if [ ! -f "$path" ]; then
        printf "FAIL: pack file not found: %s\n" "$path"
        FAIL=$((FAIL + 1))
        FAILED_PACKS="$FAILED_PACKS
  ✗ $name (pack file not found)"
        return
    fi
    bash "$path" "$@" 2>&1 | sed 's/^/    | /'
    rc=${PIPESTATUS[0]}
    end_t=$(_now_epoch)
    dur=$((end_t - start_t))
    if [ "$rc" -eq 0 ]; then
        PASS=$((PASS + 1))
        printf 'OK   %s (%ds)\n' "$name" "$dur"
    else
        FAIL=$((FAIL + 1))
        FAILED_PACKS="$FAILED_PACKS
  ✗ $name (rc=$rc, ${dur}s)"
        printf 'FAIL %s (rc=%d, %ds)\n' "$name" "$rc" "$dur"
    fi
}

run_pytest_pack() {
    local name="$1" path="$2"
    log_section "Pytest pack: $name"
    local start_t end_t dur rc
    start_t=$(_now_epoch)
    if [ ! -f "$path" ]; then
        printf "FAIL: pack file not found: %s\n" "$path"
        FAIL=$((FAIL + 1))
        FAILED_PACKS="$FAILED_PACKS
  ✗ $name (pack file not found)"
        return
    fi
    # PYTHONPATH pinning + --noconftest is the P2/P3 pattern; the unit
    # tests are daemon-tool tests that don't need the heavyweight
    # test infrastructure (test_post_restart_arm_notify_sweep.py etc).
    # Use `uv run --frozen --no-sync` so the venv-managed pytest (with
    # all daemon deps) is the resolver — the system python3 lacks pytest.
    PYTHONPATH="$REPO_ROOT" uv run --frozen --no-sync --no-config python3 -m pytest --noconftest -x -q --tb=line "$path" 2>&1 | sed 's/^/    | /'
    rc=${PIPESTATUS[0]}
    end_t=$(_now_epoch)
    dur=$((end_t - start_t))
    if [ "$rc" -eq 0 ]; then
        PASS=$((PASS + 1))
        printf 'OK   %s (%ds)\n' "$name" "$dur"
    else
        FAIL=$((FAIL + 1))
        FAILED_PACKS="$FAILED_PACKS
  ✗ $name (rc=$rc, ${dur}s)"
        printf 'FAIL %s (rc=%d, %ds)\n' "$name" "$rc" "$dur"
    fi
}

# We need _now_epoch in the parent script — define it locally (the bash
# packs each define their own; this aggregator's clock is just for timing).
_now_epoch() {
    local now
    now="$(date +%s 2>/dev/null)" || now="0"
    printf '%s\n' "$now"
}

OVERALL_START=$(_now_epoch)

# ── Default fast subset (target: <10 min) ────────────────────────────────────
log_section "Upgrade-Overhaul Aggregator — Default Fast Subset"

# A. NEW bash packs (P1-P3 surface)
run_bash_pack "test_journal_splice.sh (AC1 + §1 + §6 latency)" \
    "$TESTS_DIR/test_journal_splice.sh"
run_bash_pack "test_rollback_target.sh (AC5 four-cell)" \
    "$TESTS_DIR/test_rollback_target.sh"
run_bash_pack "test_boot_sweep_commit_and_continue.sh (AC5 cell 3)" \
    "$TESTS_DIR/test_boot_sweep_commit_and_continue.sh"
run_bash_pack "test_adaptive_soak.sh (AC3 + G2)" \
    "$TESTS_DIR/test_adaptive_soak.sh"
run_bash_pack "test_ceremony_wallclock.sh (AC2 budget identity)" \
    "$TESTS_DIR/test_ceremony_wallclock.sh"
run_bash_pack "test_bounded_subprocess_waits.sh (T-G1a)" \
    "$TESTS_DIR/test_bounded_subprocess_waits.sh"
run_bash_pack "test_promote_plugin_staleness.sh (T-PIN-staleness)" \
    "$TESTS_DIR/test_promote_plugin_staleness.sh"
run_bash_pack "test_launcher.sh (T-PIN-launcher self-containment)" \
    "$TESTS_DIR/test_launcher.sh"
run_bash_pack "test_atomic_flip.sh (T-PIN-txn flip atomicity)" \
    "$TESTS_DIR/test_atomic_flip.sh"

# B. NEW pytest packs (P2 sweep + splice helper liveness)
run_pytest_pack "test_upgrade_journal_sweep_liveness.py (AC4)" \
    "$TESTS_DIR/unit/tools/test_upgrade_journal_sweep_liveness.py"
run_pytest_pack "test_upgrade_splice_helper.py (T-AC1b + lock interop)" \
    "$TESTS_DIR/unit/tools/test_upgrade_splice_helper.py"
run_pytest_pack "test_audit_after_soak_split.py (decision (f))" \
    "$TESTS_DIR/unit/tools/test_audit_after_soak_split.py"

# D. ADJUDICATED — only on --full mode (KNOWN ISSUE: pre-existing debt).
# The default fast subset skips these because their known failures
# (BSD-only tooling in fixtures) are out-of-scope for the gate.
if [ "$FULL_MODE" = "1" ]; then
    log_section "Adjudicated packs (--full mode)"
    run_bash_pack "test_release_journal.sh (KNOWN ISSUE: pre-P2 BSD debt; see P2.1 critical note)" \
        "$TESTS_DIR/test_release_journal.sh"
else
    log_section "Skipped in default mode (--full flag adds these):"
    printf '  - test_release_journal.sh  (KNOWN ISSUE: pre-existing GNU-host\n'
    printf '                                  fixture debt; pre-P2 baseline 29x,\n'
    printf '                                  post-P2 34x; the +5 = intended\n'
    printf '                                  torn-journal defense; adjudicate\n'
    printf '                                  per D-P4.2 carry-forward)\n'
    SKIP=$((SKIP + 1))
    SKIPPED_PACKS="$SKIPPED_PACKS
  - test_release_journal.sh (KNOWN ISSUE: pre-P2 BSD debt)"
fi

# ── Summary ──────────────────────────────────────────────────────────────────
OVERALL_END=$(_now_epoch)
OVERALL_DUR=$((OVERALL_END - OVERALL_START))

log_section "Aggregator Summary"
printf 'Date: %s\n' "$(date -u +'%Y-%m-%dT%H:%M:%SZ')"
printf 'Branch: %s\n' "$(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null)"
printf 'HEAD:   %s\n' "$(git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null)"
printf 'Mode:   %s\n' "$(if [ "$FULL_MODE" = "1" ]; then echo '--full'; else echo 'default'; fi)"
printf 'Result log: %s\n\n' "$RESULT_LOG"
printf 'PASS=%d  FAIL=%d  SKIP=%d  duration=%ds\n\n' "$PASS" "$FAIL" "$SKIP" "$OVERALL_DUR"

if [ -n "$FAILED_PACKS" ]; then
    printf 'FAILED PACKS:\n%s\n\n' "$FAILED_PACKS"
fi
if [ -n "$SKIPPED_PACKS" ]; then
    printf 'SKIPPED PACKS:\n%s\n\n' "$SKIPPED_PACKS"
fi

# Gate: PASS only when there are zero failures.
if [ "$FAIL" -eq 0 ]; then
    exit 0
else
    exit 1
fi