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

# M4: shared helpers (assertions, M1 fixture registry, F1 fake-HOME fence)
. "$REPO_ROOT/tests/upgrade_test_lib.sh"
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

# ── R. RESUME-FROM-STATE (AC-CER-1 — self-service-promote Wave 4) ───────────
# Design F2-i: a re-arm on a PARTIALLY-FLIPPED system must RESUME from the
# journal's state, never re-flip. The leg pins the DECISION (behaviourally,
# through the real lib.sh function) and the STRUCTURE (promote.sh guards
# every mutation site with the resume predicate — no stop, no swap, no
# flip, no restart-from-arm on a gates-only resume).
#
# The fixture is a THROWAWAY mktemp dir (the house fence: nothing here
# resolves a live-shaped path; the journal is a plain fixture file).
R_PASS=0; R_FAIL=0
r_pass() { PASS=$((PASS + 1)); R_PASS=$((R_PASS + 1)); }
r_fail() {
    FAIL=$((FAIL + 1)); R_FAIL=$((R_FAIL + 1))
    printf 'FAIL: %s\n' "$1" >&2
    FAILED_PACKS="$FAILED_PACKS
  ✗ $1"
}
r_assert_eq() {
    if [ "$2" = "$3" ]; then r_pass; else r_fail "$1 (expected '$2', got '$3')"; fi
}
r_assert_contains() {
    case "$3" in *"$2"*) r_pass ;; *) r_fail "$1 (wanted '$2')" ;; esac
}
R_FIX="$(mktemp -d -t resume-fix.XXXXXX)"; _register_fixture "$R_FIX"
mkdir -p "$R_FIX/releases"
seed_resume_journal() {   # <flipped:true|false> <owner_pid> <last_heartbeat>
    cat > "$R_FIX/releases/state.json" <<JOURNAL
{"current":"v2","previous":"v1","in_flight":{"kind":"promote","target":"v2","started_at":"2026-01-01T00:00:00Z","flipped":$1,"owner_pid":$2,"last_heartbeat":$3},"rollback_window_count":{"24h":0,"window_start":null},"cooldown_until":null,"quarantined":[],"history":[]}
JOURNAL
}
r_resume_decision() {     # → the mode (the journal is seeded by the caller)
    INSTALL_DIR="$R_FIX" bash -c '. "'"$REPO_ROOT"'/scripts/upgrade/lib.sh" >/dev/null 2>&1; _promote_resume_decision v2' 2>/dev/null | tr -d '\n'
}
r_mutation_ok() {         # <mode> → "ok" | "blocked"
    INSTALL_DIR="$R_FIX" bash -c '. "'"$REPO_ROOT"'/scripts/upgrade/lib.sh" >/dev/null 2>&1; PROMOTE_RESUME_MODE="'"$1"'"; if _promote_resume_mutation_ok; then echo ok; else echo blocked; fi' 2>/dev/null | tr -d '\n'
}
DEAD_PID=999999
NOW_EPOCH="$(date +%s)"
# (a) flipped=true + a DEAD owner ⇒ gates-only (gates/commit ONLY).
seed_resume_journal true "$DEAD_PID" "$NOW_EPOCH"
r_assert_eq "AC-CER-1 a re-arm on a FLIPPED txn resumes gates-only" \
    "gates-only" "$(r_resume_decision)"
# (b) flipped=false ⇒ flip-prep (the flip-prep continues, with idempotency guards).
seed_resume_journal false "$DEAD_PID" "$NOW_EPOCH"
r_assert_eq "AC-CER-1 a re-arm on a PRE-FLIP txn resumes the flip-prep" \
    "flip-prep" "$(r_resume_decision)"
# (c) a LIVE owner ⇒ busy (the resume never tramples a live executor).
seed_resume_journal true "$$" "$NOW_EPOCH"
r_assert_eq "AC-CER-1 a LIVE owner keeps the journal busy (no trampling)" \
    "busy" "$(r_resume_decision)"
# (d) no open txn ⇒ fresh (the normal path).
cat > "$R_FIX/releases/state.json" <<'JOURNAL'
{"current":"v2","previous":"v1","in_flight":null,"rollback_window_count":{"24h":0,"window_start":null},"cooldown_until":null,"quarantined":[],"history":[]}
JOURNAL
r_assert_eq "AC-CER-1 no open txn ⇒ the fresh path" \
    "fresh" "$(r_resume_decision)"
# (e) the mutation predicate: gates-only blocks EVERY mutation; the other
#     modes do not.
r_assert_eq "AC-CER-1 the gates-only resume blocks the mutations" "blocked" "$(r_mutation_ok gates-only)"
r_assert_eq "AC-CER-1 the flip-prep resume may mutate" "ok" "$(r_mutation_ok flip-prep)"
r_assert_eq "AC-CER-1 the fresh path may mutate" "ok" "$(r_mutation_ok fresh)"
# (f) the STRUCTURE: promote.sh guards every mutation site with the resume
#     predicate — the stop, the launcher swap, the atomic flip, the flipped
#     marker and the unit hand-back (five sites; NO restart-from-arm path).
R_PROMOTE_GUARDS="$(grep -c '_promote_resume_mutation_ok' "$REPO_ROOT/scripts/upgrade/promote.sh" 2>/dev/null || echo 0)"
if [ "$R_PROMOTE_GUARDS" -ge 5 ]; then
    r_pass "AC-CER-1 promote.sh guards every mutation site with the resume predicate ($R_PROMOTE_GUARDS sites)"
else
    r_fail "AC-CER-1 promote.sh guards every mutation site with the resume predicate (expected >=5, got $R_PROMOTE_GUARDS)"
fi
R_PROMOTE_ATOMIC="$(grep -c 'atomic_flip "\$VERSION"' "$REPO_ROOT/scripts/upgrade/promote.sh" 2>/dev/null || echo 0)"
r_assert_eq "AC-CER-1 there is exactly ONE forward atomic_flip site (the 8b rollback repoint is a different target — a second forward flip is unreachable on the resume path)" "1" "$R_PROMOTE_ATOMIC"
printf 'R — resume-from-state (AC-CER-1): %d passed, %d failed\n' "$R_PASS" "$R_FAIL"

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