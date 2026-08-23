#!/usr/bin/env bash
# test/packs/drill_ledger_unit_test.sh
#
# Pack: drill_ledger_unit_test
# Scope: Self-Restart/Self-Upgrade Phase 2 (P2.3 B2) — journal-derived
#   N-clean-cycles ledger checker unit suite (scripts/upgrade/ledger_check.py,
#   stdlib-only Python): cycle derivation from journal history commit events,
#   clean/violation classification (rollback/sweep_rollback/halt windows;
#   restart/sweep/quarantine are NOT violations), staleness reset on version
#   change (older cycles marked SUPERSEDED, §4.3), trailing consecutive-clean
#   count (violation ends the streak, history retained — conservative ADR-021
#   reading), F2 hard-block gate (open ⇒ BLOCKED regardless of count; closed +
#   ≥3 ⇒ ELIGIBLE; else NOT-READY with needed-count), live-path refusal
#   (exit 78 on resolved-path compare vs the literal live install root —
#   before any read, nothing created; demo dir + repo-named dir + symlink
#   cases), invalid-journal exit 1, --help contract, --json parity, and the
#   zero-live-port-literal self-check on both new files.
#   Fixtures: HOME-isolated mktemp dirs with hand-built releases/state.json
#   (JSON shapes per tests/test_release_journal.sh fixtures). No daemon, no
#   DB, no network, zero live contact (the one real-home-path assertion is a
#   path-shape refusal that fires BEFORE any read and creates nothing).
# Internal watchdog (Layer 2): 120s — unit-type limit per test-pack skill.
# Layer 1 (outer) is the dispatcher's `timeout 300` wrap.
# Exit codes: 0=PASS, 1=FAIL, 124=TIMEOUT.
#
# Self-contained battery (no separate tests/ file): the wrapper re-invokes
# itself with --battery under the Layer-2 watchdog.
# Ref: agents-ensemble test-pack skill (dual-layer timeout, explicit RESULT).

set -u

# ─── wrapper (Layer 2) ───────────────────────────────────────────────────────
if [ "${1:-}" != "--battery" ]; then
    cd "$(dirname "$0")/../.." || {
        echo "FAIL: cannot cd to repo root"
        echo "RESULT: FAIL"
        exit 1
    }

    PACK_NAME="drill_ledger_unit_test"
    echo "=== Test Pack: ${PACK_NAME} ==="
    echo "Repo:    $(pwd)"
    echo "Started: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo

    OUT="$(mktemp)"
    set -o pipefail
    timeout 120s bash "$PWD/test/packs/drill_ledger_unit_test.sh" --battery 2>&1 | tee "$OUT"
    RC=$?

    SUMMARY="$(grep -E '== summary: [0-9]+ passed' "$OUT" | tail -1)"
    rm -f "$OUT"
    if [ -n "$SUMMARY" ]; then echo "SUMMARY: $SUMMARY"; fi

    echo
    if [ "$RC" -eq 124 ]; then
        echo "RESULT: TIMEOUT"
        exit 124
    elif [ "$RC" -eq 0 ]; then
        echo "RESULT: PASS"
        exit 0
    else
        echo "RESULT: FAIL (exit=${RC})"
        exit 1
    fi
fi

# ─── battery ─────────────────────────────────────────────────────────────────
cd "$(dirname "$0")/../.." || { echo "FAIL: cannot cd to repo root"; exit 1; }

REPO_ROOT="$(pwd)"
CHECKER="$REPO_ROOT/scripts/upgrade/ledger_check.py"
PACK_SELF="$REPO_ROOT/test/packs/drill_ledger_unit_test.sh"
REAL_HOME="$HOME"

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

assert_contains() {
    local name="$1" needle="$2" haystack="$3"
    case "$haystack" in
        *"$needle"*) _pass ;;
        *) _fail "$name" "contains '$needle'" "$haystack" ;;
    esac
}

assert_not_contains() {
    local name="$1" needle="$2" haystack="$3"
    case "$haystack" in
        *"$needle"*) _fail "$name" "absent '$needle'" "$haystack" ;;
        *) _pass ;;
    esac
}

section() { printf '\n== %s ==\n' "$1"; }

# ─── fixture helpers (HOME-isolated; JSON shapes per test_release_journal.sh)
FIXTURE="$(mktemp -d "${TMPDIR:-/tmp}/ledger-test.XXXXXX")"
FIXTURE="$(cd "$FIXTURE" && pwd)"
FAKE_HOME="$FIXTURE/home"
mkdir -p "$FAKE_HOME"
cleanup() { rm -rf "$FIXTURE"; }
trap cleanup EXIT

# event builders — same shapes lib.sh journal_history_append writes
cj() {  # cj <ts> <ver> — committed promote txn (cycle start marker)
    printf '{"ts":"%s","event":"commit","detail":"promote %s committed (gate+soak green; previous=none)"}' "$1" "$2"
}
rb() {  # rb <ts> — auto-rollback event (violation)
    printf '{"ts":"%s","event":"rollback","detail":"auto-rollback vX (gate fail: readyz-timeout; re-gate green)"}' "$1"
}
srb() { # srb <ts> — launcher sweep rollback event (violation)
    printf '{"ts":"%s","event":"sweep_rollback","detail":"sweep: stale flipped txn rolled back to previous"}' "$1"
}
hl() {  # hl <ts> — halt-for-human event (violation)
    printf '{"ts":"%s","event":"halt","detail":"gate fail with no previous release — halt-for-human"}' "$1"
}
rs() {  # rs <ts> — intentional restart event (NOT a violation)
    printf '{"ts":"%s","event":"restart","detail":"intentional restart run_id=r1 complete (reason: drill)"}' "$1"
}
sw() {  # sw <ts> — sweep clear event (NOT a violation)
    printf '{"ts":"%s","event":"sweep","detail":"stale pre-flip txn cleared"}' "$1"
}
qn() {  # qn <ts> — quarantine event alone (NOT a violation per §4.1 journal subset)
    printf '{"ts":"%s","event":"quarantine","detail":"vX quarantined after gate failure"}' "$1"
}

write_journal() {  # write_journal <install-dir> <history-json>
    mkdir -p "$1/releases"
    printf '{"current":null,"previous":null,"in_flight":null,"rollback_window_count":{"24h":0,"window_start":null},"cooldown_until":null,"quarantined":[],"history":[%s]}' "$2" \
        > "$1/releases/state.json"
}

run_ckpt() {  # run_ckpt <home> <journal> <f2-state> [extra...] → OUT/rc_ckpt (no pipes: rc is the checker's)
    RUN_HOME="$1"; shift
    RUN_JOURNAL="$1"; shift
    RUN_F2="$1"; shift
    OUT="$(HOME="$RUN_HOME" python3 "$CHECKER" --journal "$RUN_JOURNAL" --f2-state "$RUN_F2" "$@" 2>"$FIXTURE/stderr.txt")"
    rc_ckpt=$?
    ERR="$(cat "$FIXTURE/stderr.txt")"
}

json_field() {  # json_field <json-text> <py-expr over d>
    printf '%s' "$1" | python3 -c 'import json,sys; d=json.load(sys.stdin); print('"$2"')'
}

# ═══ T1: staleness — version change resets the run; older cycles SUPERSEDED ═══
section "T1 staleness reset"
T1D="$FIXTURE/t1"
write_journal "$T1D" "$(cj 2026-08-23T01:00:00Z v1.0.0), $(cj 2026-08-23T02:00:00Z v1.0.0), $(cj 2026-08-23T03:00:00Z v2.0.0)"
run_ckpt "$FAKE_HOME" "$T1D/releases/state.json" closed
assert_eq "t1 exit 0 (NOT-READY is data)" "0" "$rc_ckpt"
assert_contains "t1 count reset to 1" "consecutive clean: 1 " "$OUT"
assert_contains "t1 cycle 3 CLEAN at new version" "cycle 3: version=v2.0.0" "$OUT"
assert_contains "t1 cycle 3 verdict CLEAN" "txn=2026-08-23T03:00:00Z verdict=CLEAN" "$OUT"
assert_contains "t1 cycle 1 superseded" "cycle 1: version=v1.0.0 txn=2026-08-23T01:00:00Z verdict=SUPERSEDED" "$OUT"
assert_contains "t1 cycle 2 superseded" "cycle 2: version=v1.0.0 txn=2026-08-23T02:00:00Z verdict=SUPERSEDED" "$OUT"
assert_contains "t1 staleness reset line" "staleness: reset — count re-entered at cycle 3" "$OUT"

# ═══ T2: accumulation — 3 clean at same version + F2 closed ⇒ ELIGIBLE ═══
section "T2 accumulation"
T2D="$FIXTURE/t2"
write_journal "$T2D" "$(cj 2026-08-23T01:00:00Z v1.0.0), $(cj 2026-08-23T02:00:00Z v1.0.0), $(cj 2026-08-23T03:00:00Z v1.0.0)"
run_ckpt "$FAKE_HOME" "$T2D/releases/state.json" closed
assert_eq "t2 exit 0" "0" "$rc_ckpt"
assert_contains "t2 count 3" "consecutive clean: 3 " "$OUT"
assert_contains "t2 verdict ELIGIBLE" "gate verdict: ELIGIBLE" "$OUT"
assert_contains "t2 reason names ADR-021" "ADR-021" "$OUT"
assert_contains "t2 reason names F2 closed" "F2 closed" "$OUT"
assert_not_contains "t2 no superseded cycles" "SUPERSEDED" "$OUT"

# ═══ T3: violation classification — rollback/sweep_rollback/halt in window ═══
section "T3 violation classification"
T3A="$FIXTURE/t3a"
write_journal "$T3A" "$(cj 2026-08-23T01:00:00Z v1.0.0), $(cj 2026-08-23T02:00:00Z v1.0.0), $(cj 2026-08-23T03:00:00Z v1.0.0), $(rb 2026-08-23T03:30:00Z)"
run_ckpt "$FAKE_HOME" "$T3A/releases/state.json" closed
assert_contains "t3a cycle 3 VIOLATION with cause" "cycle 3: version=v1.0.0 txn=2026-08-23T03:00:00Z verdict=VIOLATION cause=rollback@2026-08-23T03:30:00Z" "$OUT"
assert_contains "t3a trailing count 0" "consecutive clean: 0 " "$OUT"
assert_contains "t3a verdict NOT-READY" "gate verdict: NOT-READY" "$OUT"
assert_contains "t3a no-clean-credited reason" "no clean cycle credited" "$OUT"

T3B="$FIXTURE/t3b"   # cycles 1-3 clean, 4 dirty (halt), 5 clean → count = 1
write_journal "$T3B" "$(cj 2026-08-23T01:00:00Z v1.0.0), $(cj 2026-08-23T02:00:00Z v1.0.0), $(cj 2026-08-23T03:00:00Z v1.0.0), $(cj 2026-08-23T04:00:00Z v1.0.0), $(hl 2026-08-23T04:30:00Z), $(cj 2026-08-23T05:00:00Z v1.0.0)"
run_ckpt "$FAKE_HOME" "$T3B/releases/state.json" closed
assert_contains "t3b cycle 4 VIOLATION halt cause" "cycle 4: version=v1.0.0 txn=2026-08-23T04:00:00Z verdict=VIOLATION cause=halt@2026-08-23T04:30:00Z" "$OUT"
assert_contains "t3b violation breaks streak → count 1" "consecutive clean: 1 " "$OUT"
assert_contains "t3b history retained (cycle 1 still CLEAN)" "cycle 1: version=v1.0.0 txn=2026-08-23T01:00:00Z verdict=CLEAN" "$OUT"

T3C="$FIXTURE/t3c"   # sweep_rollback classifies as violation
write_journal "$T3C" "$(cj 2026-08-23T01:00:00Z v1.0.0), $(srb 2026-08-23T01:30:00Z)"
run_ckpt "$FAKE_HOME" "$T3C/releases/state.json" closed
assert_contains "t3c sweep_rollback cause" "verdict=VIOLATION cause=sweep_rollback@2026-08-23T01:30:00Z" "$OUT"

T3D="$FIXTURE/t3d"   # restart + sweep(clear) + quarantine alone are NOT violations
write_journal "$T3D" "$(cj 2026-08-23T01:00:00Z v1.0.0), $(rs 2026-08-23T01:10:00Z), $(sw 2026-08-23T01:20:00Z), $(qn 2026-08-23T01:30:00Z), $(cj 2026-08-23T02:00:00Z v1.0.0), $(rs 2026-08-23T02:10:00Z)"
run_ckpt "$FAKE_HOME" "$T3D/releases/state.json" closed
assert_contains "t3d cycle 1 stays CLEAN" "cycle 1: version=v1.0.0 txn=2026-08-23T01:00:00Z verdict=CLEAN" "$OUT"
assert_contains "t3d cycle 2 stays CLEAN (trailing restart not a violation)" "cycle 2: version=v1.0.0 txn=2026-08-23T02:00:00Z verdict=CLEAN" "$OUT"
assert_contains "t3d count 2" "consecutive clean: 2 " "$OUT"

# ═══ T4: F2 hard-block — open ⇒ BLOCKED regardless of count ═══
section "T4 F2 hard-block"
run_ckpt "$FAKE_HOME" "$T2D/releases/state.json" open
assert_eq "t4a exit 0 (BLOCKED is data)" "0" "$rc_ckpt"
assert_contains "t4a 3 clean + F2 open ⇒ BLOCKED" "gate verdict: BLOCKED" "$OUT"
assert_contains "t4a reason F2-open" "F2-open" "$OUT"
assert_contains "t4a reason hard-block wording" "regardless of cycle count" "$OUT"

T4B="$FIXTURE/t4b"   # zero cycles + F2 open ⇒ still BLOCKED
write_journal "$T4B" ""
run_ckpt "$FAKE_HOME" "$T4B/releases/state.json" open
assert_contains "t4b 0 cycles + F2 open ⇒ BLOCKED" "gate verdict: BLOCKED" "$OUT"
assert_eq "t4b exit 0" "0" "$rc_ckpt"

# ═══ T5: F2 closed but count < 3 ⇒ NOT-READY with needed-count ═══
section "T5 NOT-READY needed-count"
T5D="$FIXTURE/t5"
write_journal "$T5D" "$(cj 2026-08-23T01:00:00Z v1.0.0), $(cj 2026-08-23T02:00:00Z v1.0.0)"
run_ckpt "$FAKE_HOME" "$T5D/releases/state.json" closed
assert_contains "t5 verdict NOT-READY" "gate verdict: NOT-READY" "$OUT"
assert_contains "t5 needed-count 1" "1 more clean cycle(s) at version v1.0.0 needed" "$OUT"
assert_contains "t5 count shown" "consecutive clean: 2 " "$OUT"
run_ckpt "$FAKE_HOME" "$T4B/releases/state.json" closed   # 0 cycles, f2 closed
assert_contains "t5b needed-count 3" "3 more clean cycle(s)" "$OUT"

# ═══ T6: live-path refusal (78) — resolve+compare vs literal live install root
# ZERO writes anywhere: refusal fires on path shape BEFORE any read. The
# real-home assertion creates/reads nothing (path-shape only); everything
# else runs under the isolated fake home. ═══
section "T6 live-path refusal"
run_ckpt "$FAKE_HOME" "$FAKE_HOME/agents-ensemble/nonexistent-1/state.json" closed
assert_eq "t6a live-shaped path refused 78" "78" "$rc_ckpt"
assert_contains "t6a refusal message" "REFUSED" "$ERR"
if [ -e "$FAKE_HOME/agents-ensemble" ]; then
    _fail "t6a nothing created under the live-shaped path" "absent" "present"
else
    _pass
fi

run_ckpt "$REAL_HOME" "$REAL_HOME/agents-ensemble/nonexistent-1/state.json" closed
assert_eq "t6b real-home path-shape refusal (task-specified)" "78" "$rc_ckpt"
assert_contains "t6b refusal precedes any read" "no read attempted" "$ERR"

T6C="$FAKE_HOME/agents-ensemble-demo"   # demo dir name CONTAINS live dir name
write_journal "$T6C" "$(cj 2026-08-23T01:00:00Z v1.0.0)"
run_ckpt "$FAKE_HOME" "$T6C/releases/state.json" closed
assert_eq "t6c demo path accepted (substring non-match)" "0" "$rc_ckpt"
assert_contains "t6c demo journal read" "cycle 1: version=v1.0.0" "$OUT"

T6D="$FIXTURE/checkout/agents-ensemble"  # dir merely NAMED like the live root
write_journal "$T6D" "$(cj 2026-08-23T01:00:00Z v1.0.0)"
run_ckpt "$FAKE_HOME" "$T6D/releases/state.json" closed
assert_eq "t6d repo-named dir accepted (name ≠ live root)" "0" "$rc_ckpt"

ln -s "$FAKE_HOME/agents-ensemble" "$FIXTURE/live-alias"   # symlink smuggling
run_ckpt "$FAKE_HOME" "$FIXTURE/live-alias/x/state.json" closed
assert_eq "t6e symlink into live root refused 78" "78" "$rc_ckpt"

# ═══ T7: zero live-port literals in both new files (constructed literal —
# this pack never contains the contiguous digits either) ═══
section "T7 zero-live-port-literal self-check"
LIVE_PORT_LITERAL="$(printf '%s%s' 9 797)"
if grep -n -- "$LIVE_PORT_LITERAL" "$CHECKER" >/dev/null 2>&1; then
    _fail "t7a checker free of live-port literals" "absent" "$(grep -n -- "$LIVE_PORT_LITERAL" "$CHECKER")"
else
    _pass
fi
if grep -n -- "$LIVE_PORT_LITERAL" "$PACK_SELF" >/dev/null 2>&1; then
    _fail "t7b pack free of live-port literals" "absent" "$(grep -n -- "$LIVE_PORT_LITERAL" "$PACK_SELF")"
else
    _pass
fi
HELP_OUT="$(python3 "$CHECKER" --help 2>&1)"
assert_contains "t7c checker prose says 'live port' (runbook rule)" "live port" "$HELP_OUT"

# ═══ T8: --json output parses and carries the same verdict ═══
section "T8 --json parity"
run_ckpt "$FAKE_HOME" "$T2D/releases/state.json" closed --json
assert_eq "t8a json run exit 0" "0" "$rc_ckpt"
assert_eq "t8a json verdict ELIGIBLE" "ELIGIBLE" "$(json_field "$OUT" 'd["gate"]["verdict"]')"
assert_eq "t8a json count 3" "3" "$(json_field "$OUT" 'd["consecutive_clean_count"]')"
assert_eq "t8a json current version" "v1.0.0" "$(json_field "$OUT" 'd["current_version"]')"
assert_eq "t8a json f2 closed" "closed" "$(json_field "$OUT" 'd["f2_state"]')"
assert_eq "t8a json cycles 3 rows" "3" "$(json_field "$OUT" 'len(d["cycles"])')"
assert_eq "t8a json row carries txn_id" "2026-08-23T01:00:00Z" "$(json_field "$OUT" 'd["cycles"][0]["txn_id"]')"
assert_eq "t8a json row verdict CLEAN" "CLEAN" "$(json_field "$OUT" 'd["cycles"][0]["verdict"]')"

run_ckpt "$FAKE_HOME" "$T2D/releases/state.json" open --json
assert_eq "t8b json BLOCKED parity" "BLOCKED" "$(json_field "$OUT" 'd["gate"]["verdict"]')"

run_ckpt "$FAKE_HOME" "$T3B/releases/state.json" closed --json
assert_eq "t8c json violation cause parity" "halt@2026-08-23T04:30:00Z" "$(json_field "$OUT" 'd["cycles"][3]["causes"][0]')"
assert_eq "t8c json count-after-break parity" "1" "$(json_field "$OUT" 'd["consecutive_clean_count"]')"

run_ckpt "$FAKE_HOME" "$T1D/releases/state.json" closed --json
assert_eq "t8d json staleness parity" "True" "$(json_field "$OUT" 'str(d["staleness"]["reset"])')"
assert_eq "t8d json superseded cycles" "1,2" "$(json_field "$OUT" '",".join(str(c) for c in d["superseded_cycles"])')"
assert_contains "t8d json coverage note present" "clauses 3-5" "$OUT"

# ═══ T9: invalid journals → exit 1; CLI contract ═══
section "T9 invalid journal + CLI contract"
run_ckpt "$FAKE_HOME" "$FIXTURE/missing/state.json" closed
assert_eq "t9a missing journal exit 1" "1" "$rc_ckpt"
assert_contains "t9a error names the path" "cannot read journal" "$ERR"

mkdir -p "$FIXTURE/torn/releases"
printf '{"current":"v1","previous":null,"in_flight":null,"history":[{"ts":"2026-08-23T01:00:00Z","event":"comm' \
    > "$FIXTURE/torn/releases/state.json"
run_ckpt "$FAKE_HOME" "$FIXTURE/torn/releases/state.json" closed
assert_eq "t9b torn JSON exit 1" "1" "$rc_ckpt"
assert_contains "t9b torn-write wording" "not valid JSON" "$ERR"

mkdir -p "$FIXTURE/nohist/releases"
printf '{"current":"v1","previous":null}' > "$FIXTURE/nohist/releases/state.json"
run_ckpt "$FAKE_HOME" "$FIXTURE/nohist/releases/state.json" closed
assert_eq "t9c history-not-a-list exit 1" "1" "$rc_ckpt"

OUT="$(python3 "$CHECKER" --journal x 2>"$FIXTURE/stderr.txt")"; rc_ckpt=$?
assert_eq "t9d missing --f2-state rejected (usage)" "2" "$rc_ckpt"
OUT="$(HOME="$FAKE_HOME" python3 "$CHECKER" --journal x --f2-state maybe 2>"$FIXTURE/stderr.txt")"; rc_ckpt=$?
assert_eq "t9e invalid f2 choice rejected (usage)" "2" "$rc_ckpt"
assert_contains "t9f help documents §4.1 clauses 3" "clauses 3" "$HELP_OUT"
assert_contains "t9f help documents ADR-021" "ADR-021" "$HELP_OUT"
assert_contains "t9f help documents live refusal" "exit 78" "$HELP_OUT"
assert_contains "t9f help says f2-state is caller-supplied" "caller" "$HELP_OUT"

# ─── summary ────────────────────────────────────────────────────────────────
printf '\n== summary: %d passed, %d failed ==\n' "$PASS" "$FAIL"
if [ "$FAIL" -gt 0 ]; then
    printf 'failed:%s\n' "$FAILED_TESTS"
    exit 1
fi
exit 0
