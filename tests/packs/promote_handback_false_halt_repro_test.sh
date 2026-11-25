#!/usr/bin/env bash
# ============================================================================
# tests/packs/promote_handback_false_halt_repro_test.sh — the RED-at-base /
# GREEN-at-merge harness for the promote hand-back FALSE-HALT cure.
#
# WHAT IT PINS (the cure, per the refresh-conflict ledger
#   .agents/shared/planning/promote-handback-false-halt/
#   refresh-conflict-ledger-2026-11-25.md §4 H1/H2/H3, §6, §7, §8, §10):
#
#   (a) mechanism i  — the is-active rc taxonomy: rc=3 (systemctl's ROUTINE
#       pre-start answer) is INFO-class + carries a `kind=state` row, and
#       NO failure-shaped row (no `timeout(`, no `kind=budget`, no
#       `kind=nonzero_exit`) is written for it.
#   (b) mechanism iv + ii — the attribution gate's TRANSIENT class: a port
#       holder that is NOT the supervised unit's pid and NOT in the
#       argv-owned set (the MCP-mid-spawn shape) must NOT produce a
#       halt-shaped write; the caller keeps polling and the hand-back
#       reaches green inside the budget.
#   (c) the FALSE-GREEN GUARD — a genuinely foreign (uncontained) holder
#       MUST still halt at the merge.  PIN-ONLY (green at base by design).
#   (d) the is-active BOUNDED-WAIT budget (rc=124) must still be a REAL
#       timeout.  PIN-ONLY.
#   (d2) the hand-back START budget (rc=124, the H3 site) must still
#       write its `kind=budget` row with `reason=child: timeout(...)`.
#       This is the NOUNSET-OFF (observability) regime: the row is written
#       with an EMPTY detail and the payload executes as a command.
#   (d3) the SAME start-timeout path under the REAL caller's NOUNSET-ON
#       regime (promote.sh:69 / restart.sh:63 / rollback.sh:32 all set -u
#       before sourcing lib.sh).  With the severed continuation the
#       unguarded `$2` in journal_history_append (lib.sh:1889-1891) dies
#       with `$2: unbound variable` BEFORE any row is written and the
#       abort PROPAGATES through `if ! restart_via_launcher; then ...`
#       to the shell exit — BYPASSING the clean-halt arms and their side-
#       channel cleanup.  d3 proves nounset took effect in the SAME shell
#       and asserts the caller still reaches its post-hand-back code.
#   (e) mechanism ii END-TO-END — the ladder's `return 2` producer → the
#       probe's `case "$__attr_rc"` arm → the caller's `case "$hsp_rc"`
#       keep-polling arm.
#   (f) mechanism iv's REASON handling — the dynamic reason only for the
#       right rc classes; a SINGLE reason writer; a transient must NOT
#       poison the reason channel.
#   (g) ROW 20 / T7e — `scripts/upgrade/status.sh` on a transient rc: the
#       transient note is PRESENT and --verify does not fail on it.
#   (h) a soak tick must treat an rc=2 (deferred/transient) gate result as
#       NON-fatal (INCONCLUSIVE), never as a version drift.
#
# THE SHAPE (house rules):
#   * ONE new pack script (the landed packs are NOT modified — the two
#     landed readers of the rc=3 row, tests/test_bounded_subprocess_waits.sh
#     and tests/packs/promote_handback_halt_gate_legs_test.sh, are untouched
#     and must stay green).
#   * The REAL scripts/upgrade/lib.sh + scripts/upgrade/status.sh are
#     DRIVEN, with STUBBED externals only (systemctl / lsof / curl).  No
#     copy of lib.sh is ever taken.
#   * The stub patterns are REUSED from the landed packs: the systemctl
#     stub with the injectable SC_IS_ACTIVE_RC (tests/test_bounded_subprocess_waits.sh
#     :106-113), the assignment-form 3-field state probe
#     (SC_ACTIVESTATE / SC_SUBSTATE / SC_MAINPID), the port-holder lsof
#     stub, the curl stub, the owned-set seam
#     (`_supervision_owned_pids() { printf "%s\n" 424242; }`), the
#     journal-row greps against $INSTALL_DIR/releases/state.json and the
#     FAKE_HOME sandbox (tests/upgrade_test_lib.sh).
#   * RUNNABLE AT BOTH REVS: every call of a merge-only symbol is guarded
#     (`declare -F`), so the BASE run yields a CLEAN, MEANINGFUL RED —
#     never a shell "command not found".
#   * FENCES: mktemp FAKE_HOME + the shared escape guard (two live installs
#     were deleted on 2026-10-12 by an unfenced HOME-derived fixture);
#     PER-VARIABLE env scrubs (`env -u POSTGRES_*` is a SILENT NO-OP in
#     bash — it does not glob env-var names); ports > 10000 only (9797 /
#     8079 untouched); nothing binds, nothing is installed, the live
#     install is never resolved.
#   * Dual-layer timeout: the caller wraps the run in `timeout 300`; this
#     script self-caps at 280 s (layer 2) and prints
#     `RESULT: PASS|FAIL|TIMEOUT` (124 on the cap).
#
# Run:
#   cd <worktree> && timeout 300 bash tests/packs/promote_handback_false_halt_repro_test.sh
# ============================================================================
set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
UPGRADE_DIR="$REPO_ROOT/scripts/upgrade"
LIBSH="$UPGRADE_DIR/lib.sh"
STATUS_SH="$UPGRADE_DIR/status.sh"

# ── LAYER 2 (script-internal) self-cap ──────────────────────────────────────
PHH_CAP_S=280
PHH_T0="$(date +%s)"
phh_cap_check() {
    local now; now="$(date +%s)"
    if [ $(( now - PHH_T0 )) -ge "$PHH_CAP_S" ]; then
        printf '\nRESULT: TIMEOUT\n'
        exit 124
    fi
}

# ── FENCE 1 — per-variable env scrub (a `env -u POSTGRES_*` GLOB is a ───────
# ── SILENT NO-OP in bash: it does not glob env-var names) ───────────────────
unset POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD POSTGRES_HOST POSTGRES_PORT \
      POSTGRES_URL PGPORT PGHOST PGDATABASE PGUSER PGPASSWORD DATABASE_URL \
      DATABASE_URL_POSTGRES ENSEMBLE_DB_DSN SSL_CERT_FILE SSL_CERT_DIR \
      OPENAI_API_KEY 2>/dev/null || true
if [ "${POSTGRES_DB:-}" = "ensemble_prod" ]; then
    printf 'FENCE ABORT: ambient POSTGRES_DB=ensemble_prod (prod-poisoned env) — scrub per-variable and re-run\n' >&2
    exit 78
fi
export PG_TEST_ALLOWED_DBS=ensemble_sandbox
export ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0   # pin EXPLICITLY (never `env -u`)

# ── counters + the shared assertion helpers ─────────────────────────────────
PASS=0
FAIL=0
FAILED_TESTS=""
_pass() { PASS=$(( PASS + 1 )); }
_fail() {
    FAIL=$(( FAIL + 1 ))
    FAILED_TESTS="$FAILED_TESTS
  x $1"
    printf 'FAIL: %s\n' "$1" >&2
    [ $# -gt 1 ] && printf '      expected: %s\n      actual:   %s\n' "$2" "$3" >&2
    return 0
}
assert_eq() {
    local name="$1" expected="$2" actual="$3"
    if [ "$expected" = "$actual" ]; then _pass "$name"; else _fail "$name" "$expected" "$actual"; fi
}
assert_contains() {
    local name="$1" needle="$2" haystack="$3"
    case "$haystack" in
        *"$needle"*) _pass "$name" ;;
        *) _fail "$name" "contains '$needle'" "$(printf '%s' "$haystack" | head -c 400)" ;;
    esac
}
assert_not_contains() {
    local name="$1" needle="$2" haystack="$3"
    case "$haystack" in
        *"$needle"*) _fail "$name" "absent '$needle'" "$(printf '%s' "$haystack" | head -c 400)" ;;
        *) _pass "$name" ;;
    esac
}
# assert_grep <name> <ERE> <haystack> — a regex assertion
assert_grep() {
    local name="$1" ere="$2" haystack="$3"
    if printf '%s' "$haystack" | grep -qE "$ere"; then _pass "$name"
    else _fail "$name" "matches /$ere/" "$(printf '%s' "$haystack" | head -c 400)"; fi
}
assert_not_grep() {
    local name="$1" ere="$2" haystack="$3"
    if printf '%s' "$haystack" | grep -qE "$ere"; then _fail "$name" "NOT matches /$ere/" "$(printf '%s' "$haystack" | head -c 400)"
    else _pass "$name"; fi
}

# The shared prologue helpers — the ONE canonical home for the fixture
# registry + its ONE EXIT trap and the F1 fake-HOME escape guard.
#   . "$(dirname "${BASH_SOURCE[0]}")/upgrade_test_lib.sh"
. "$REPO_ROOT/tests/upgrade_test_lib.sh"

setup_fake_home
preflight_guard

# The sandbox install root takes a DIFFERENT name under the SAME FAKE_HOME:
# the journal splice helper refuses a journal path under "$HOME/agents-ensemble"
# (the live-install guard) unless the pipeline lock is held, so a sandbox
# rooted exactly there would refuse EVERY journal row.
INSTALL_DIR="$HOME/installs/agents-ensemble"
mkdir -p "$INSTALL_DIR/releases"

# A port that is > 10000 (1-9999 reserved for prod/dev; 20000+ reserved),
# never 9797 (the live daemon) and never 8079 (the repo dev daemon).
PHH_PORT=18377

STUBBIN="$(mktemp -d -t phh-stub.XXXXXX)"
_register_fixture "$STUBBIN"
SC_LOG="$INSTALL_DIR/sc.log"
: > "$SC_LOG"

# The stub bin must NOT leak into the real PATH for the pack's own helpers.
# ── the externals ───────────────────────────────────────────────────────────
# systemctl — the proven stub shape from tests/test_bounded_subprocess_waits.sh
#   (the injectable SC_IS_ACTIVE_RC, the assignment-form 3-field state probe)
#   + the ladder's ctx resolver (the SINGLE-PROP `show -p MainPID --value`
#   form, counted separately so the containment ladder's "the deciding
#   tooling could not answer" class is drivable).
cat > "$STUBBIN/systemctl" <<'PHHSTUB'
#!/bin/bash
echo "$*" >> "${SC_LOG:-/dev/null}"
sub="$1"; shift
case "$sub" in
    is-active)
        if [ "${SC_HANG:-0}" = "1" ] && [ "${SC_HANG_SUB:-is-active}" = "is-active" ]; then
            exec sleep 999
        fi
        echo "${SC_IS_ACTIVE:-inactive}"
        exit "${SC_IS_ACTIVE_RC:-0}" ;;
    show)
        prop=""; props=""
        while [ $# -gt 0 ]; do
            case "$1" in
                -p) prop="$2"; props="${props} $2"; shift 2 ;;
                --value) shift ;;
                *) shift ;;
            esac
        done
        case "$props" in
            *" ActiveState "*)
                n="$(cat "${SC_LOG}.mp.n" 2>/dev/null || echo 0)"
                n=$((n + 1)); printf '%s' "$n" > "${SC_LOG}.mp.n"
                if [ -n "${SC_ACTIVESTATE_N:-}" ] && [ "$n" -gt "${SC_ACTIVESTATE_N}" ] \
                   && [ -n "${SC_ACTIVESTATE2:-}" ]; then
                    echo "ActiveState=${SC_ACTIVESTATE2}"
                else
                    echo "ActiveState=${SC_ACTIVESTATE:-active}"
                fi
                echo "SubState=${SC_SUBSTATE:-running}"
                echo "MainPID=${SC_MAINPID:-0}"
                exit 0 ;;
        esac
        # the SINGLE-PROP form — the containment ladder's ctx resolver.
        # SC_CTX_UNANSWERABLE_CALLS = how many ctx resolves answer MainPID=0
        # (the "the deciding tooling could not answer" class ⇒ the ladder
        # DEFERS instead of deciding).
        case "$prop" in
            MainPID)
                n="$(cat "${SC_LOG}.ctx.n" 2>/dev/null || echo 0)"
                n=$((n + 1)); printf '%s' "$n" > "${SC_LOG}.ctx.n"
                if [ "$n" -le "${SC_CTX_UNANSWERABLE_CALLS:-0}" ]; then
                    echo "0"; exit 0
                fi
                echo "${SC_CTX_MAINPID:-424242}"; exit 0 ;;
            ControlGroup) echo "${SC_CONTROLGROUP:-}" ;;
            *) echo "" ;;
        esac
        exit 0 ;;
    reset-failed)
        if [ "${SC_HANG:-0}" = "1" ] && [ "${SC_HANG_SUB:-reset-failed}" = "reset-failed" ]; then
            exec sleep 999
        fi
        exit 0 ;;
    start)
        if [ "${SC_HANG:-0}" = "1" ] && [ "${SC_HANG_SUB:-start}" = "start" ]; then
            exec sleep 999
        fi
        exit "${SC_START_RC:-0}" ;;
    stop) exit "${SC_STOP_RC:-0}" ;;
    *) exit 0 ;;
esac
PHHSTUB
chmod +x "$STUBBIN/systemctl"

# lsof — the port-holder census.  PHH_HOLDER is the pid that holds the
# LISTEN socket (424242 = the owned daemon; the real spawned pid = the
# "MCP child" that inherited the LISTEN fd).
cat > "$STUBBIN/lsof" <<'PHHSTUB'
#!/bin/bash
printf '%s\n' "${PHH_HOLDER:-424242}"
exit 0
PHHSTUB
chmod +x "$STUBBIN/lsof"

# curl — the /livez + /readyz green answer (the proven stub shape).
cat > "$STUBBIN/curl" <<'PHHSTUB'
#!/bin/bash
n="$(cat "${SC_LOG}.curl.n" 2>/dev/null || echo 0)"
n=$((n + 1)); printf '%s' "$n" > "${SC_LOG}.curl.n"
out=""
while [ $# -gt 0 ]; do
    case "$1" in
        -o) out="$2"; shift 2 ;;
        -w) shift 2 ;;
        --max-time) shift 2 ;;
        -sS|-s|-S) shift ;;
        *) shift ;;
    esac
done
[ -n "$out" ] && printf '{"status":"alive","version":"%s"}\n' "${PHH_VERSION:-0.28.5}" > "$out"
printf '200'
PHHSTUB
chmod +x "$STUBBIN/curl"

# ── fixture state helpers ───────────────────────────────────────────────────
seed_journal() {
    cat > "$INSTALL_DIR/releases/state.json" <<'JSON'
{"current":null,"previous":null,"in_flight":null,"rollback_window_count":{"24h":0,"window_start":null},"cooldown_until":null,"quarantined":[],"history":[]}
JSON
}
journal_text() { cat "$INSTALL_DIR/releases/state.json" 2>/dev/null || true; }
reset_stub_state() {
    : > "$SC_LOG"
    rm -f "$SC_LOG".* 2>/dev/null || true
    seed_journal
}
# phh_run <extra-shell-setup> — drive the REAL lib.sh in a scrubbed shell.
#   The owned-set seam is the landed packs' own seam
#   (`_supervision_owned_pids() { printf "%s\n" 424242; }`).
phh_run() {
    PATH="$STUBBIN:$PATH" INSTALL_DIR="$INSTALL_DIR" \
    SYSTEMCTL_BIN="$STUBBIN/systemctl" SC_LOG="$SC_LOG" PORT="$PHH_PORT" \
    PROMOTE_EXPECTED_VERSION="${PHH_EXPECTED_VERSION:-}" \
    HANDBACK_VERIFY_BUDGET_S="${PHH_VERIFY_BUDGET:-12}" \
    HANDBACK_VERIFY_GRACE_S="${PHH_VERIFY_GRACE:-0}" \
    _GATE_ATTR_DEFER_S="${PHH_DEFER_S:-2}" ENSEMBLE_TEST_GATE_DEFER=1 \
    PHH_HOLDER="${PHH_HOLDER:-424242}" PHH_VERSION="${PHH_VERSION:-0.28.5}" \
    bash -c '
        unset ENSEMBLE_SUPERVISION ENSEMBLE_RESTART_UNIT 2>/dev/null || true
        . '"$LIBSH"' >/dev/null 2>&1
        _supervision_owned_pids() { printf "%s\n" 424242; }
        SUPERVISION_MODE=unit; SUPERVISION_STATE=UNIT_MANAGED
        SUPERVISION_UNIT=ensemble-phh.service; SUPERVISION_PRESTOP_MAINPID=7777
        '"$1"'
    ' 2>&1
}
hb_rc() { printf '%s' "$1" | grep -oE 'hb-rc=[0-9]+' | head -1 | sed 's/hb-rc=//'; }

# phh_run_nounset <extra-shell-setup> — the REAL caller regime: `set -u` is
# EXPLICITLY ON in the SAME shell that sources lib.sh and runs the
# hand-back (promote.sh:69 + :123, restart.sh:63/:88, rollback.sh:32/:58;
# no caller sets `set +u`).  The nounset proof is printed from INSIDE that
# shell — the two regimes are otherwise indistinguishable from the output.
phh_run_nounset() {
    PATH="$STUBBIN:$PATH" INSTALL_DIR="$INSTALL_DIR" \
    SYSTEMCTL_BIN="$STUBBIN/systemctl" SC_LOG="$SC_LOG" PORT="$PHH_PORT" \
    PROMOTE_EXPECTED_VERSION="${PHH_EXPECTED_VERSION:-}" \
    HANDBACK_VERIFY_BUDGET_S="${PHH_VERIFY_BUDGET:-12}" \
    HANDBACK_VERIFY_GRACE_S="${PHH_VERIFY_GRACE:-0}" \
    _GATE_ATTR_DEFER_S="${PHH_DEFER_S:-2}" ENSEMBLE_TEST_GATE_DEFER=1 \
    PHH_HOLDER="${PHH_HOLDER:-424242}" PHH_VERSION="${PHH_VERSION:-0.28.5}" \
    bash -c '
        set -u
        printf "NOUNSET-PROOF: %s\n" "$(set -o | grep nounset | tr -s " ")"
        unset ENSEMBLE_SUPERVISION ENSEMBLE_RESTART_UNIT 2>/dev/null || true
        . '"$LIBSH"' >/dev/null 2>&1
        _supervision_owned_pids() { printf "%s\n" 424242; }
        SUPERVISION_MODE=unit; SUPERVISION_STATE=UNIT_MANAGED
        SUPERVISION_UNIT=ensemble-phh.service; SUPERVISION_PRESTOP_MAINPID=7777
        '"$1"'
    ' 2>&1
}

# the "MCP child" — a REAL pid (so /proc/<pid>/cgroup is readable) that is
# NOT the supervised unit's pid and NOT in the argv-owned set.
MCP_CHILD=""
MCP_CHILD_CG=""
start_mcp_child() {
    sleep 300 &
    MCP_CHILD=$!
    # the SAME parse lib.sh uses (_supervision_pid_cgroup_path): strip up to
    # the LAST colon (the cgroup-v2 line is "0::/path").
    MCP_CHILD_CG="$(tail -n 1 "/proc/$MCP_CHILD/cgroup" 2>/dev/null | sed 's/^.*://')"
}
stop_mcp_child() {
    [ -n "$MCP_CHILD" ] && kill "$MCP_CHILD" 2>/dev/null
    MCP_CHILD=""
}

# ── per-repro bookkeeping ───────────────────────────────────────────────────
REPRO_NAME=""
REPRO_PASS=0
REPRO_FAIL=0
# The two gates this pack carries, kept APART so the summary is unambiguous:
#   CURE GATE      — the false-halt cure itself (repros a,b,c,d,e,f,g,h)
#   MERGE-DEFECT   — repros (d2) + (d3): the H3 hand-back-start budget row,
#                    the merge's own C1 defect, in BOTH severity regimes
#                    (d2 nounset-OFF = observability; d3 nounset-ON = the
#                    real caller's abort)
CURE_REPROS="a b c d e f g h"
DEFECT_REPROS="d2 d3"
declare -A REPRO_PASS_MAP
declare -A REPRO_FAIL_MAP
repro_begin() {
    REPRO_NAME="$1"; REPRO_PASS=0; REPRO_FAIL=0
    printf '\n== REPRO (%s) %s ==\n' "$1" "$2"
}
repro_end() {
    REPRO_PASS_MAP["$REPRO_NAME"]="$REPRO_PASS"
    REPRO_FAIL_MAP["$REPRO_NAME"]="$REPRO_FAIL"
    if [ "$REPRO_FAIL" -eq 0 ]; then
        printf 'REPRO (%s): PASS — %d assertion(s)\n' "$REPRO_NAME" "$REPRO_PASS"
    else
        printf 'REPRO (%s): FAIL — %d passed, %d FAILED\n' "$REPRO_NAME" "$REPRO_PASS" "$REPRO_FAIL"
    fi
}
# rp_pass / rp_fail — per-repro counters (they also feed the pack totals).
rp_pass() { REPRO_PASS=$(( REPRO_PASS + 1 )); _pass "$1"; }
rp_fail() {
    REPRO_FAIL=$(( REPRO_FAIL + 1 ))
    _fail "$1" "${2:-}" "${3:-}"
}
rp_assert_eq() {
    local name="$1" expected="$2" actual="$3"
    if [ "$expected" = "$actual" ]; then rp_pass "$name"
    else rp_fail "$name" "$expected" "$actual"; fi
}
rp_assert_contains() {
    local name="$1" needle="$2" haystack="$3"
    case "$haystack" in
        *"$needle"*) rp_pass "$name" ;;
        *) rp_fail "$name" "contains '$needle'" "$(printf '%s' "$haystack" | head -c 400)" ;;
    esac
}
rp_assert_not_contains() {
    local name="$1" needle="$2" haystack="$3"
    case "$haystack" in
        *"$needle"*) rp_fail "$name" "absent '$needle'" "$(printf '%s' "$haystack" | head -c 400)" ;;
        *) rp_pass "$name" ;;
    esac
}
rp_assert_not_grep() {
    local name="$1" ere="$2" haystack="$3"
    if printf '%s' "$haystack" | grep -qE "$ere"; then
        rp_fail "$name" "NOT matches /$ere/" "$(printf '%s' "$haystack" | grep -E "$ere" | head -2)"
    else
        rp_pass "$name"
    fi
}
rp_assert_grep() {
    local name="$1" ere="$2" haystack="$3"
    if printf '%s' "$haystack" | grep -qE "$ere"; then rp_pass "$name"
    else rp_fail "$name" "matches /$ere/" "$(printf '%s' "$haystack" | head -c 400)"; fi
}

printf '=== promote hand-back false-halt — RED-at-base / GREEN-at-merge harness ===\n'
printf 'repo=%s\n' "$REPO_ROOT"
printf 'lib=%s\n' "$LIBSH"

# ===========================================================================
# REPRO (a) — mechanism i: the is-active rc taxonomy
# ===========================================================================
phh_cap_check
repro_begin "a" "is-active rc=3 is INFO-class + a kind=state row, never a failure-shaped row"
reset_stub_state
A_OUT="$(SC_IS_ACTIVE=inactive SC_IS_ACTIVE_RC=3 SC_MAINPID=9999 SC_ACTIVESTATE=active \
    phh_run '
        SYSTEMCTL_PROBE_BUDGET_S=2
        _supervision_handback_unit "ensemble-phh.service" "7777" "0" "/dev/null"
        echo "hb-rc=$?"
    ')"
A_J="$(journal_text)"
printf '   [a] hb-rc=%s\n' "$(hb_rc "$A_OUT")"
printf '   [a] rc-3 journal row: %s\n' "$(printf '%s' "$A_J" | grep -o 'handback_is_active_state[^"]*' | head -1 | head -c 200)"
# a1 — the rc-3 answer is INFO-class (the merge's `_log` line).
if printf '%s' "$A_OUT" | grep -q 'is-active: site=handback_is_active state=.* rc=3'; then
    rp_pass "a1 rc=3 is handled at INFO class (the _log line, not a WARN)"
else
    rp_fail "a1 rc=3 is handled at INFO class (the _log line, not a WARN)" \
        "an INFO line 'is-active: site=handback_is_active state=... rc=3'" \
        "$(printf '%s' "$A_OUT" | grep -i 'is-active' | head -2 | tr '\n' '|')"
fi
# a2 — the WARN-class shape (the base's "answered rc=... NOT a timeout" warn) is GONE.
if printf '%s' "$A_OUT" | grep -q 'WARN: is-active probe answered rc='; then
    rp_fail "a2 the rc=3 answer is NOT warned (INFO class only)" \
        "no 'WARN: is-active probe answered rc=' line" \
        "$(printf '%s' "$A_OUT" | grep -o 'WARN: is-active probe answered[^|]*' | head -1 | head -c 200)"
else
    rp_pass "a2 the rc=3 answer is NOT warned (INFO class only)"
fi
# a3 — the additive `handback_is_active_state` row is present (LEADER-RATIFIED:
#       two LANDED tests read this row — they must stay green).
if printf '%s' "$A_J" | grep -q 'handback_is_active_state'; then
    rp_pass "a3 a handback_is_active_state row is journaled for the rc=3 answer"
else
    rp_fail "a3 a handback_is_active_state row is journaled for the rc=3 answer" \
        "a handback_is_active_state row" "$(printf '%s' "$A_J" | head -c 300)"
fi
# a4 — NO failure-shaped row for the rc=3 answer (the leader's acceptance
#       criterion: never timeout / budget / nonzero_exit on the rc-3 row).
A_RC3_ROW="$(printf '%s' "$A_J" | grep -o 'site=handback_is_active[^"]*' | head -1)"
if printf '%s' "$A_RC3_ROW" | grep -q 'kind=state'; then
    rp_pass "a4 the rc=3 row carries kind=state (not a failure kind)"
else
    rp_fail "a4 the rc=3 row carries kind=state (not a failure kind)" \
        "kind=state" "$A_RC3_ROW"
fi
if printf '%s' "$A_RC3_ROW" | grep -qE 'timeout\(|kind=budget|kind=nonzero_exit'; then
    rp_fail "a5 the rc=3 row carries NO failure shape (no timeout(/kind=budget/kind=nonzero_exit)" \
        "no failure-shaped token" "$A_RC3_ROW"
else
    rp_pass "a5 the rc=3 row carries NO failure shape (no timeout(/kind=budget/kind=nonzero_exit)"
fi
# a6 — the rc=3 row carries the explicit state reason.
if printf '%s' "$A_RC3_ROW" | grep -q 'reason=state:'; then
    rp_pass "a6 the rc=3 row carries the explicit reason=state:<state>"
else
    rp_fail "a6 the rc=3 row carries the explicit reason=state:<state>" \
        "reason=state:" "$A_RC3_ROW"
fi
# a7 — no halt: the hand-back continued into the start attempt and the
#       MainPID/port verify owns the verdict.
rp_assert_eq "a7 the rc=3 probe does NOT halt the hand-back (the verify owns the verdict)" \
    "0" "$(hb_rc "$A_OUT")"
# a8 — a NON-3 answered rc (rc=4) carries the honest nonzero_exit taxonomy.
reset_stub_state
A4_OUT="$(SC_IS_ACTIVE=failed SC_IS_ACTIVE_RC=4 SC_MAINPID=9999 SC_ACTIVESTATE=active \
    phh_run '
        SYSTEMCTL_PROBE_BUDGET_S=2
        _supervision_handback_unit "ensemble-phh.service" "7777" "0" "/dev/null"
        echo "hb-rc=$?"
    ')"
A4_J="$(journal_text)"
A4_ROW="$(printf '%s' "$A4_J" | grep -o 'site=handback_is_active[^"]*' | head -1)"
if printf '%s' "$A4_ROW" | grep -q 'kind=nonzero_exit' && printf '%s' "$A4_ROW" | grep -q 'rc=4'; then
    rp_pass "a8 a NON-3 answered rc (rc=4) carries kind=nonzero_exit with the true rc"
else
    rp_fail "a8 a NON-3 answered rc (rc=4) carries kind=nonzero_exit with the true rc" \
        "kind=nonzero_exit ... rc=4" "$A4_ROW"
fi
repro_end

# ===========================================================================
# REPRO (b) — the attribution TRANSIENT (the MCP mid-spawn shape)
# ===========================================================================
phh_cap_check
repro_begin "b" "a transient attribution does NOT halt — the caller keeps polling and the boot goes green"
start_mcp_child
reset_stub_state
PHH_HOLDER="$MCP_CHILD"
export PHH_HOLDER
B_OUT="$(SC_CTX_UNANSWERABLE_CALLS=2 SC_CTX_MAINPID=424242 \
    SC_CONTROLGROUP="$MCP_CHILD_CG" SC_MAINPID=9999 SC_ACTIVESTATE=active \
    phh_run '
        SYSTEMCTL_PROBE_BUDGET_S=2
        _supervision_handback_unit "ensemble-phh.service" "7777" "0" "/dev/null"
        echo "hb-rc=$?"
        echo "REASON=[$(_handback_verify_reason_text)]"
    ')"
B_J="$(journal_text)"
printf '   [b] hb-rc=%s  probes=%s\n' "$(hb_rc "$B_OUT")" "$(cat "$SC_LOG.curl.n" 2>/dev/null || echo 0)"
# b1 — no halt-shaped write (the reason channel).
if printf '%s' "$B_OUT" | grep -q 'attribution-refused'; then
    rp_fail "b1 the transient attribution writes NO 'attribution-refused' reason" \
        "no attribution-refused" "$(printf '%s' "$B_OUT" | grep -o 'attribution-refused[^|]*' | head -1 | head -c 200)"
else
    rp_pass "b1 the transient attribution writes NO 'attribution-refused' reason"
fi
# b2 — no halt-shaped diagnostic.
if printf '%s' "$B_OUT" | grep -q 'attribution REFUSED'; then
    rp_fail "b2 the transient attribution writes NO 'attribution REFUSED' diagnostic" \
        "no 'attribution REFUSED'" "$(printf '%s' "$B_OUT" | grep -o 'attribution REFUSED[^|]*' | head -1 | head -c 200)"
else
    rp_pass "b2 the transient attribution writes NO 'attribution REFUSED' diagnostic"
fi
# b3 — the boot reaches GREEN inside the budget.
rp_assert_eq "b3 the transient attribution does NOT halt the hand-back (green inside the budget)" \
    "0" "$(hb_rc "$B_OUT")"
# b4 — the deferral was actually taken (the ladder's transient rung fired).
if printf '%s' "$B_J" | grep -q 'kind=attr_deferred'; then
    rp_pass "b4 the ladder took its TRANSIENT rung (a kind=attr_deferred row is journaled)"
else
    rp_fail "b4 the ladder took its TRANSIENT rung (a kind=attr_deferred row is journaled)" \
        "a kind=attr_deferred row" "$(printf '%s' "$B_J" | head -c 300)"
fi
# b5 — the containment ladder ACCEPTED the contained holder (the MCP child
#      inside the unit's cgroup is NOT a foreign answerer).
if printf '%s' "$B_OUT" | grep -q 'contained in the supervised tree'; then
    rp_pass "b5 the containment ladder ACCEPTED the cgroup-contained holder"
else
    rp_fail "b5 the containment ladder ACCEPTED the cgroup-contained holder" \
        "'contained in the supervised tree'" "$(printf '%s' "$B_OUT" | grep -i 'attribution' | head -2 | tr '\n' '|')"
fi
# b6 — the caller KEPT POLLING (more than one serving probe attempt).
B_PROBES="$(cat "$SC_LOG.curl.n" 2>/dev/null || echo 0)"
if [ "${B_PROBES:-0}" -ge 2 ] 2>/dev/null; then
    rp_pass "b6 the caller kept polling after the transient (>=2 serving probes, saw $B_PROBES)"
else
    rp_fail "b6 the caller kept polling after the transient (>=2 serving probes)" \
        ">=2 serving probes" "$B_PROBES"
fi
repro_end
stop_mcp_child
unset PHH_HOLDER

# ===========================================================================
# REPRO (c) — the FALSE-GREEN GUARD: a genuinely foreign holder MUST halt
#             (PIN-ONLY: green at base by design)
# ===========================================================================
phh_cap_check
repro_begin "c" "a genuinely foreign (uncontained) port holder STILL halts (PIN-ONLY)"
start_mcp_child
reset_stub_state
PHH_HOLDER="$MCP_CHILD"
export PHH_HOLDER
C_OUT="$(SC_CTX_UNANSWERABLE_CALLS=0 SC_CTX_MAINPID=424242 \
    SC_CONTROLGROUP="/system.slice/ensemble-phh-foreign.service" \
    SC_MAINPID=9999 SC_ACTIVESTATE=active \
    phh_run '
        SYSTEMCTL_PROBE_BUDGET_S=2
        _supervision_handback_unit "ensemble-phh.service" "7777" "0" "/dev/null"
        echo "hb-rc=$?"
    ')"
printf '   [c] hb-rc=%s\n' "$(hb_rc "$C_OUT")"
rp_assert_eq "c1 the foreign holder halts the hand-back (rc=1) at every rev" \
    "1" "$(hb_rc "$C_OUT")"
rp_assert_contains "c2 the halt-shaped reason names the attribution refusal" \
    "attribution-refused" "$C_OUT"
rp_assert_contains "c3 the refusal diagnostic is emitted" \
    "attribution REFUSED" "$C_OUT"
repro_end
stop_mcp_child
unset PHH_HOLDER

# ===========================================================================
# REPRO (d) — the is-active BOUNDED-WAIT budget (rc=124) is still a REAL
#             timeout (PIN-ONLY)
# ===========================================================================
phh_cap_check
repro_begin "d" "the is-active bounded-wait budget (rc=124) is still a REAL timeout (PIN-ONLY)"
reset_stub_state
D_OUT="$(SC_HANG=1 SC_HANG_SUB=is-active SC_MAINPID=9999 SC_ACTIVESTATE=active \
    phh_run '
        SYSTEMCTL_PROBE_BUDGET_S=2
        _supervision_handback_unit "ensemble-phh.service" "7777" "0" "/dev/null"
        echo "hb-rc=$?"
    ')"
D_J="$(journal_text)"
D_ROW="$(printf '%s' "$D_J" | grep -o 'site=handback_is_active[^"]*' | head -1)"
printf '   [d] is-active budget row: %s\n' "$(printf '%s' "$D_ROW" | head -c 200)"
rp_assert_contains "d1 the is-active budget timeout writes a subprocess_wait_timeout row" \
    "subprocess_wait_timeout" "$D_J"
rp_assert_contains "d2 the budget row names the is-active site" \
    "site=handback_is_active" "$D_J"
rp_assert_contains "d3 the budget row carries kind=budget" \
    "kind=budget" "$D_ROW"
rp_assert_contains "d4 the budget row carries reason=child: timeout(SYSTEMCTL_PROBE_BUDGET_S=2)" \
    "reason=child: timeout(SYSTEMCTL_PROBE_BUDGET_S=2)" "$D_ROW"
rp_assert_contains "d5 the budget row carries the true rc=124" \
    "rc=124" "$D_ROW"
repro_end

# ===========================================================================
# REPRO (d2) — the hand-back START budget (rc=124) — the H3 site
# ===========================================================================
phh_cap_check
repro_begin "d2" "the hand-back START budget (rc=124) still writes its kind=budget row"
reset_stub_state
D2_OUT="$(SC_HANG=1 SC_HANG_SUB=start SC_MAINPID=9999 SC_ACTIVESTATE=active \
    phh_run '
        HANDBACK_START_BUDGET_S=2
        SYSTEMCTL_PROBE_BUDGET_S=2
        _supervision_handback_unit "ensemble-phh.service" "7777" "0" "/dev/null"
        echo "hb-rc=$?"
    ')"
D2_J="$(journal_text)"
D2_ROW="$(printf '%s' "$D2_J" | grep -o 'site=handback_start[^"]*' | head -1)"
printf '   [d2] start budget row: %s\n' "$(printf '%s' "$D2_ROW" | head -c 200)"
rp_assert_contains "d2.1 the start budget timeout writes its subprocess_wait_timeout row" \
    "site=handback_start" "$D2_J"
rp_assert_contains "d2.2 the start budget row carries kind=budget" \
    "kind=budget" "$D2_ROW"
rp_assert_contains "d2.3 the start budget row carries reason=child: timeout(HANDBACK_START_BUDGET_S=2)" \
    "reason=child: timeout(HANDBACK_START_BUDGET_S=2)" "$D2_ROW"
rp_assert_not_contains "d2.4 the start budget row's payload is NOT executed as a command" \
    "command not found" "$D2_OUT"
rp_assert_not_contains "d2.5 no spurious 'journal event append FAILED' warn" \
    "journal event append FAILED" "$D2_OUT"
repro_end

# ===========================================================================
# REPRO (d3) — the NOUNSET-ON (real caller) regime of the H3 start-timeout
# ===========================================================================
phh_cap_check
repro_begin "d3" "the start-timeout path under the REAL caller's nounset-ON regime does NOT abort"
reset_stub_state
# The REAL caller shape: promote.sh / restart.sh / rollback.sh all `set -u`
# BEFORE sourcing lib.sh, and call the hand-back as `if ! restart_via_launcher;
# then ...`.  The sentinel arms prove whether the caller actually completed.
D3_OUT="$(SC_HANG=1 SC_HANG_SUB=start SC_MAINPID=9999 SC_ACTIVESTATE=active \
    phh_run_nounset '
        SYSTEMCTL_PROBE_BUDGET_S=2
        HANDBACK_START_BUDGET_S=2
        if ! restart_via_launcher; then
            echo "D3-HALT-ARM-REACHED"
        else
            echo "D3-SUCCESS-ARM-REACHED"
        fi
        echo "D3-POST-HANDBACK-REACHED"
        echo "d3-rc=$?"
    ')"
D3_J="$(journal_text)"
printf '   [d3] %s\n' "$(printf '%s' "$D3_OUT" | grep -E 'NOUNSET-PROOF|D3-|d3-rc=|unbound variable|command not found' | tr '\n' '|' | head -c 400)"
# d3.0 — the regime is PROVEN, not assumed: nounset is on in the SAME shell
#        that sourced lib.sh and ran the hand-back.
# `set -o` separates the name and value with a TAB on this host — match
# whitespace generically so the regime proof is portable.
rp_assert_grep "d3.0 nounset is EXPLICITLY on in the hand-back shell (regime proof)" \
    'NOUNSET-PROOF: nounset[[:space:]]+on' "$D3_OUT"
# d3.1 / d3.2 — the abort signature is absent.
rp_assert_not_contains "d3.1 NO '\$2: unbound variable' abort" \
    '$2: unbound variable' "$D3_OUT"
rp_assert_not_grep "d3.2 NO lib.sh:1890 reference in the output" \
    'lib\.sh: (line )?1890' "$D3_OUT"
# d3.3 / d3.4 — the surrounding function COMPLETED: the caller reached its
#        post-hand-back code and one of the real arms ran.  At the merge the
#        shell aborts inside journal_history_append and NEITHER prints.
rp_assert_contains "d3.3 the caller reaches its post-hand-back code (no shell abort)" \
    "D3-POST-HANDBACK-REACHED" "$D3_OUT"
rp_assert_grep "d3.4 one of the caller's real arms ran (halt or success)" \
    'D3-(HALT|SUCCESS)-ARM-REACHED' "$D3_OUT"
# d3.5-d3.8 — the journal row is written AND carries its detail + the true rc.
D3_ROW="$(printf '%s' "$D3_J" | grep -o 'site=handback_start[^"]*' | head -1)"
rp_assert_contains "d3.5 the start-timeout row is journaled with site=handback_start" \
    "site=handback_start" "$D3_J"
rp_assert_contains "d3.6 the row carries kind=budget" \
    "kind=budget" "$D3_ROW"
rp_assert_contains "d3.7 the row carries reason=child: timeout(HANDBACK_START_BUDGET_S=2)" \
    "reason=child: timeout(HANDBACK_START_BUDGET_S=2)" "$D3_ROW"
rp_assert_contains "d3.8 the row carries the TRUE rc=124" \
    "rc=124" "$D3_ROW"
# d3.9 / d3.10 — no command-execution symptom and no spurious best-effort
#        warn.  NOTE (measured): under nounset-ON the unbound-$2 abort fires
#        BEFORE the payload string can execute, so these two symptoms belong
#        to d2's nounset-OFF regime; they are asserted here for completeness.
rp_assert_not_contains "d3.9 NO 'command not found' (the payload never executes as a command)" \
    "command not found" "$D3_OUT"
rp_assert_not_contains "d3.10 NO spurious 'journal event append FAILED (best-effort)' warn" \
    "journal event append FAILED" "$D3_OUT"
repro_end

# ===========================================================================
# REPRO (e) — mechanism ii END-TO-END: ladder rc=2 → probe case → caller
# ===========================================================================
phh_cap_check
repro_begin "e" "mechanism ii end-to-end: the ladder's rc=2 producer reaches the probe and the caller"
start_mcp_child
reset_stub_state
PHH_HOLDER="$MCP_CHILD"
export PHH_HOLDER
PHH_DEFER_S=0
E_OUT="$(SC_CTX_UNANSWERABLE_CALLS=9999 SC_CTX_MAINPID=424242 \
    SC_CONTROLGROUP="$MCP_CHILD_CG" SC_MAINPID=9999 SC_ACTIVESTATE=active \
    phh_run '
        _gate_serving_attribution "hand-back verify"; echo "attr-rc=$?"
        _handback_serving_probe; echo "probe-rc=$?"
    ')"
E_J="$(journal_text)"
printf '   [e] %s\n' "$(printf '%s' "$E_OUT" | grep -E 'attr-rc=|probe-rc=' | tr '\n' ' ')"
rp_assert_eq "e1 the containment ladder PRODUCES rc=2 (the transient class)" \
    "2" "$(printf '%s' "$E_OUT" | grep -oE 'attr-rc=[0-9]+' | head -1 | sed 's/attr-rc=//')"
rp_assert_eq "e2 the serving probe maps the transient to rc=2 (its case arm keeps polling)" \
    "2" "$(printf '%s' "$E_OUT" | grep -oE 'probe-rc=[0-9]+' | head -1 | sed 's/probe-rc=//')"
rp_assert_contains "e3 the deferral rung journals its kind=attr_deferred row" \
    "kind=attr_deferred" "$E_J"
rp_assert_not_contains "e4 the transient rung does NOT refuse" \
    "attribution-refused" "$E_OUT"
# e5 — the CALLER's `case "$hsp_rc"` keep-polling arm: the verify loop
#      re-probes after the transient instead of halting.
reset_stub_state
PHH_DEFER_S=2
E5_OUT="$(SC_CTX_UNANSWERABLE_CALLS=2 SC_CTX_MAINPID=424242 \
    SC_CONTROLGROUP="$MCP_CHILD_CG" SC_MAINPID=9999 SC_ACTIVESTATE=active \
    phh_run '
        SYSTEMCTL_PROBE_BUDGET_S=2
        _supervision_handback_unit "ensemble-phh.service" "7777" "0" "/dev/null"
        echo "hb-rc=$?"
    ')"
E5_PROBES="$(cat "$SC_LOG.curl.n" 2>/dev/null || echo 0)"
printf '   [e5] hb-rc=%s probes=%s\n' "$(hb_rc "$E5_OUT")" "$E5_PROBES"
rp_assert_eq "e5 the caller's keep-polling arm drives the hand-back to green" \
    "0" "$(hb_rc "$E5_OUT")"
if [ "${E5_PROBES:-0}" -ge 2 ] 2>/dev/null; then
    rp_pass "e6 the caller's case-hsp_rc arm re-probes after the transient (saw $E5_PROBES probes)"
else
    rp_fail "e6 the caller's case-hsp_rc arm re-probes after the transient" \
        ">=2 serving probes" "$E5_PROBES"
fi
repro_end
stop_mcp_child
unset PHH_HOLDER
PHH_DEFER_S=2

# ===========================================================================
# REPRO (f) — mechanism iv's REASON handling
# ===========================================================================
phh_cap_check
repro_begin "f" "the dynamic reason, the single reason writer, and the un-poisoned reason channel"
F_FN_OUT="$(phh_run '
    if declare -F _bounded_rc_kind >/dev/null 2>&1; then
        printf "kind124=%s\n" "$(_bounded_rc_kind 124)"
        printf "kind3=%s\n" "$(_bounded_rc_kind 3)"
        printf "reason124=%s\n" "$(_bounded_rc_reason 124 "" "BUDGET=2")"
        printf "reason3=%s\n" "$(_bounded_rc_reason 3 "" "")"
        printf "reason4h=%s\n" "$(_bounded_rc_reason 4 "failed" "")"
    else
        printf "ABSENT\n"
    fi
    if declare -F _gate_consume >/dev/null 2>&1 && declare -F _gate_outcome >/dev/null 2>&1 \
       && declare -F _gate_defer_reset >/dev/null 2>&1 && declare -F _gate_attr_containment >/dev/null 2>&1; then
        printf "defer-family=present\n"
    else
        printf "defer-family=absent\n"
    fi
')"
printf '   [f] %s\n' "$(printf '%s' "$F_FN_OUT" | grep -E 'kind1|kind3|reason|defer-family' | tr '\n' '|')"
if printf '%s' "$F_FN_OUT" | grep -q 'ABSENT'; then
    rp_fail "f1 the honest rc taxonomy (_bounded_rc_kind / _bounded_rc_reason) is landed" \
        "the taxonomy functions are present" "ABSENT"
else
    rp_pass "f1 the honest rc taxonomy (_bounded_rc_kind / _bounded_rc_reason) is landed"
fi
rp_assert_eq "f2 rc=124 classifies as the BUDGET kind" \
    "kind124=budget" "$(printf '%s' "$F_FN_OUT" | grep -o 'kind124=[^ ]*' | head -1)"
rp_assert_eq "f3 an answered rc≠124 classifies as nonzero_exit" \
    "kind3=nonzero_exit" "$(printf '%s' "$F_FN_OUT" | grep -o 'kind3=[^ ]*' | head -1)"
rp_assert_eq "f4 the dynamic reason for a budget rc is timeout(<descriptor>)" \
    "reason124=timeout(BUDGET=2)" "$(printf '%s' "$F_FN_OUT" | grep -o 'reason124=[^ ]*' | head -1)"
rp_assert_eq "f5 the dynamic reason for an answered rc is nonzero rc=<rc>" \
    "reason3=nonzero rc=3" "$(printf '%s' "$F_FN_OUT" | grep -o 'reason3=nonzero rc=3' | head -1)"
rp_assert_eq "f6 the dynamic reason carries the state hint when one is supplied" \
    "reason4h=nonzero rc=4 state=failed" "$(printf '%s' "$F_FN_OUT" | grep -o 'reason4h=nonzero rc=4 state=failed' | head -1)"
rp_assert_eq "f7 the gate_defer_reason carrier family is landed" \
    "defer-family=present" "$(printf '%s' "$F_FN_OUT" | grep -o 'defer-family=[^ ]*' | head -1)"
# f8 — the reason channel has a SINGLE writer (one definition).
F_WRITERS="$(grep -c '^_handback_verify_reason_write()' "$LIBSH" 2>/dev/null || echo 0)"
rp_assert_eq "f8 the verifier-reason channel has exactly ONE writer definition" \
    "1" "$F_WRITERS"
# f9 — a transient must NOT poison the reason channel (no reason survives it).
start_mcp_child
reset_stub_state
PHH_HOLDER="$MCP_CHILD"
export PHH_HOLDER
F9_OUT="$(SC_CTX_UNANSWERABLE_CALLS=2 SC_CTX_MAINPID=424242 \
    SC_CONTROLGROUP="$MCP_CHILD_CG" SC_MAINPID=9999 SC_ACTIVESTATE=active \
    phh_run '
        SYSTEMCTL_PROBE_BUDGET_S=2
        _supervision_handback_unit "ensemble-phh.service" "7777" "0" "/dev/null"
        echo "hb-rc=$?"
        echo "REASON=[$(_handback_verify_reason_text)]"
    ')"
F9_REASON="$(printf '%s' "$F9_OUT" | grep -o 'REASON=\[.*\]' | head -1)"
printf '   [f9] %s\n' "$F9_REASON"
rp_assert_eq "f9 a transient leaves the reason channel EMPTY (it never poisons a later halt)" \
    "REASON=[]" "$F9_REASON"
repro_end
stop_mcp_child
unset PHH_HOLDER

# ===========================================================================
# REPRO (g) — ROW 20 / T7e: scripts/upgrade/status.sh on a transient rc
# ===========================================================================
phh_cap_check
repro_begin "g" "ROW 20 / T7e: status.sh on a transient rc notes the deferral and does not fail"
start_mcp_child
reset_stub_state
PHH_HOLDER="$MCP_CHILD"
export PHH_HOLDER
G_OUT="$(PATH="$STUBBIN:$PATH" INSTALL_DIR="$INSTALL_DIR" PORT="$PHH_PORT" \
    TARGET=sandbox SUPERVISION_MODE=unit SUPERVISION_UNIT=ensemble-phh.service \
    _GATE_ATTR_DEFER_S=0 ENSEMBLE_TEST_GATE_DEFER=1 \
    SC_CTX_UNANSWERABLE_CALLS=9999 SC_CTX_MAINPID=424242 SC_CONTROLGROUP="$MCP_CHILD_CG" \
    SC_LOG="$SC_LOG" PHH_HOLDER="$MCP_CHILD" \
    bash "$STATUS_SH" sandbox 2>&1)"
G_RC=$?
printf '   [g] status rc=%s\n' "$G_RC"
rp_assert_contains "g1 the transient attribution is NOTED (the deferral note is present)" \
    "attribution DEFERRED (transient" "$G_OUT"
rp_assert_not_contains "g2 the transient is NOT reported as an attribution MISMATCH" \
    "attribution MISMATCH" "$G_OUT"
rp_assert_eq "g3 status.sh exits 0 on a transient attribution (plain mode)" \
    "0" "$G_RC"
# g4 — the same in --verify mode: the transient must NOT harden into a
#      verify failure.  (The sandbox has no `current` release, so the
#      verify section's own rc is 1 at BOTH revs — the discriminating
#      assertion is that the ATTRIBUTION never contributes to it.)
G_V_OUT="$(PATH="$STUBBIN:$PATH" INSTALL_DIR="$INSTALL_DIR" PORT="$PHH_PORT" \
    TARGET=sandbox SUPERVISION_MODE=unit SUPERVISION_UNIT=ensemble-phh.service \
    _GATE_ATTR_DEFER_S=0 ENSEMBLE_TEST_GATE_DEFER=1 \
    SC_CTX_UNANSWERABLE_CALLS=9999 SC_CTX_MAINPID=424242 SC_CONTROLGROUP="$MCP_CHILD_CG" \
    SC_LOG="$SC_LOG" PHH_HOLDER="$MCP_CHILD" \
    bash "$STATUS_SH" sandbox --verify 2>&1)"
rp_assert_not_contains "g4 the transient does NOT harden into a verify failure" \
    "attribution MISMATCH is a verify failure" "$G_V_OUT"
rp_assert_contains "g5 the verify mode still notes the transient deferral" \
    "attribution DEFERRED (transient" "$G_V_OUT"
repro_end
stop_mcp_child
unset PHH_HOLDER

# ===========================================================================
# REPRO (h) — a soak tick treats an rc=2 (deferred/transient) as NON-fatal
# ===========================================================================
phh_cap_check
repro_begin "h" "a soak tick treats an rc=2 (deferred/transient) gate result as NON-fatal"
start_mcp_child
reset_stub_state
PHH_HOLDER="$MCP_CHILD"
export PHH_HOLDER
PHH_DEFER_S=2
H_OUT="$(SC_CTX_UNANSWERABLE_CALLS=9999 SC_CTX_MAINPID=424242 \
    SC_CONTROLGROUP="$MCP_CHILD_CG" SC_MAINPID=9999 SC_ACTIVESTATE=active \
    PHH_EXPECTED_VERSION="0.28.5" \
    phh_run '
        gate_version "0.28.5" >/dev/null 2>&1; echo "gaterc=$?"
        ENSEMBLE_SOAK_CONSERVATIVE=1
        gate_soak 1 "0.28.5"; echo "soak-rc=$?"
    ')"
printf '   [h] %s\n' "$(printf '%s' "$H_OUT" | grep -E 'gaterc=|soak-rc=' | tr '\n' ' ')"
rp_assert_eq "h1 gate_version propagates the TRANSIENT rc (2), never laundering it into 1" \
    "2" "$(printf '%s' "$H_OUT" | grep -oE 'gaterc=[0-9]+' | head -1 | sed 's/gaterc=//')"
rp_assert_eq "h2 the soak tick treats the rc=2 as NON-fatal (the soak still returns 0)" \
    "0" "$(printf '%s' "$H_OUT" | grep -oE 'soak-rc=[0-9]+' | head -1 | sed 's/soak-rc=//')"
rp_assert_contains "h3 the soak tick reports the transient as INCONCLUSIVE (streak frozen)" \
    "INCONCLUSIVE" "$H_OUT"
rp_assert_not_contains "h4 the soak tick does NOT report a version drift on a transient" \
    "soak FAILURE: version drifted" "$H_OUT"
repro_end
stop_mcp_child
unset PHH_HOLDER
PHH_DEFER_S=2

# ===========================================================================
# summary
# ===========================================================================
printf '\n=== summary ===\n'
_cure_p=0; _cure_f=0; _def_p=0; _def_f=0
for r in $CURE_REPROS; do
    _cure_p=$(( _cure_p + ${REPRO_PASS_MAP[$r]:-0} ))
    _cure_f=$(( _cure_f + ${REPRO_FAIL_MAP[$r]:-0} ))
done
for r in $DEFECT_REPROS; do
    _def_p=$(( _def_p + ${REPRO_PASS_MAP[$r]:-0} ))
    _def_f=$(( _def_f + ${REPRO_FAIL_MAP[$r]:-0} ))
done
printf 'CURE GATE (repros %s): %d passed, %d failed\n' "$CURE_REPROS" "$_cure_p" "$_cure_f"
if [ "$_cure_f" -eq 0 ]; then printf '  -> CURE GATE: GREEN\n'; else printf '  -> CURE GATE: RED\n'; fi
printf 'MERGE-DEFECT GATE (repros %s — the H3 hand-back-start budget row, both nounset regimes): %d passed, %d failed\n' "$DEFECT_REPROS" "$_def_p" "$_def_f"
if [ "$_def_f" -eq 0 ]; then printf '  -> MERGE-DEFECT GATE: GREEN\n'; else printf '  -> MERGE-DEFECT GATE: RED (the merge\047s own C1 defect — see the d2 evidence above)\n'; fi
printf 'passed: %d\n' "$PASS"
printf 'failed: %d\n' "$FAIL"
if [ "$FAIL" -gt 0 ]; then
    printf 'failed assertions:%s\n' "$FAILED_TESTS"
    printf 'RESULT: FAIL\n'
    exit 1
fi
printf 'RESULT: PASS\n'
exit 0
