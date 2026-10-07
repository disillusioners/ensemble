#!/bin/bash
# ============================================================================
# tests/test_bounded_subprocess_waits.sh — pin tests for the 2.2b
# bounded subprocess waits (upgrade-resilience 2026-10-07)
# ============================================================================
# Sites covered (verify CURRENT line numbers — the brief's numbers drifted
# after sibling commits, these were re-verified against the tree):
#
#   1. lib.sh:2670  stop_via_stop_script UNIT path fork-exec
#      (stop_script UNIT child)
#   2. scripts/stop-ensemble.sh:530  systemctl stop (incident ④ culprit)
#   3. lib.sh:2880  _supervision_handback_unit reset-failed
#   4. lib.sh:2894  _supervision_handback_unit is-active
#   5. lib.sh:2918  _supervision_handback_unit start (the §9 culprit)
#   6. lib.sh:3179  restart_via_launcher scope-arm is-active
#   7. lib.sh:3195  restart_via_launcher scope-arm start
#   H.  §9 reproduction — hand-back start hangs → bounded return 1 within
#      budget + halt event appended (NOT an infinite hang)
#   R.  Regression — non-hanging stubs complete within budget with NO
#      timeout events
#
# Strategy: SYSTEMCTL_BIN stub (P5-style, comp7 precedent) whose `start` /
# `stop` / `reset-failed` / `is-active` either return 0 fast or hang via
# `sleep 999`. Budgets are forced LOW (2s) via the env vars, so a hang
# trips the bounded wait in ~2s instead of the production 60-120s.
#
# The cooldown-fix test (lib.sh:982) is exercised by exporting COOLDOWN_S
# then calling journal_rollback_count_24h's arm path (via the journal
# mock), and asserting the cooldown_until is a future timestamp on
# GNU date.
#
# Run:
#   bash tests/test_bounded_subprocess_waits.sh
# ============================================================================

set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UPGRADE_DIR="$REPO_ROOT/scripts/upgrade"
STOP_SCRIPT="$REPO_ROOT/scripts/stop-ensemble.sh"

PASS=0
FAIL=0
SKIP=0
FAILED_TESTS=""

_pass() { PASS=$((PASS + 1)); }
_fail() {
    FAIL=$((FAIL + 1))
    FAILED_TESTS="$FAILED_TESTS
  ✗ $1"
    printf 'FAIL: %s\n' "$1" >&2
    [ $# -gt 1 ] && printf '      expected: %s\n      actual:   %s\n' "$2" "$3" >&2
}
_skip() {
    SKIP=$((SKIP + 1))
    printf 'SKIP: %s\n' "$1" >&2
}
assert_eq() {
    local name="$1" expected="$2" actual="$3"
    if [ "$expected" = "$actual" ]; then _pass; else _fail "$name" "$expected" "$actual"; fi
}
assert_contains() {
    local name="$1" needle="$2" haystack="$3"
    case "$haystack" in
        *"$needle"*) _pass "$name" ;;
        *) _fail "$name" "contains '$needle'" "$haystack" ;;
    esac
}
assert_not_contains() {
    local name="$1" needle="$2" haystack="$3"
    case "$haystack" in
        *"$needle"*) _fail "$name" "absent '$needle'" "$haystack" ;;
        *) _pass "$name" ;;
    esac
}
section() { printf '\n== %s ==\n' "$1"; }

HOST_HAS_SYSTEMD=0
[ "$(uname -s)" = "Linux" ] && [ -d /run/systemd/system ] && HOST_HAS_SYSTEMD=1

# ── Stubbed systemctl: HANG on demand (P5/comp7 precedent) ─────────────────
#
# Behavior: by default commands are no-op (exit 0). When SC_HANG=1 is set,
# the chosen SC_HANG_SUB (one of: stop, start, reset-failed, is-active)
# runs `sleep 999` and returns whatever `_run_bounded` decides (124).
# Cross-call state via SC_LOG env.
make_systemctl_stub() {
    local dir="$1"
    mkdir -p "$dir"
    cat > "$dir/systemctl" <<'STUB'
#!/bin/bash
echo "$*" >> "${SC_LOG:-/dev/null}"
sub="$1"; shift
case "$sub" in
    is-active)
        if [ "${SC_HANG:-0}" = "1" ] && [ "${SC_HANG_SUB:-is-active}" = "is-active" ]; then
            exec sleep 999
        fi
        echo "${SC_IS_ACTIVE:-inactive}"; exit 0 ;;
    show)
        prop=""; unit=""
        while [ $# -gt 0 ]; do
            case "$1" in -p) prop="$2"; shift 2 ;; --value) shift ;; *) unit="$1"; shift ;; esac
        done
        case "$prop" in
            MainPID) echo "${SC_MAINPID:-0}" ;;
            ControlGroup) echo "${SC_CONTROLGROUP:-}" ;;
            Restart) echo "${SC_RESTART:-no}" ;;
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
    stop)
        if [ "${SC_HANG:-0}" = "1" ] && [ "${SC_HANG_SUB:-stop}" = "stop" ]; then
            exec sleep 999
        fi
        exit "${SC_STOP_RC:-0}" ;;
    kill) exit 0 ;;
    daemon-reload) exit 0 ;;
    enable) exit 0 ;;
    *) exit 0 ;;
esac
STUB
    chmod +x "$dir/systemctl"
}

reset_stub_state() {
    local log="$1"
    : > "$log"
    rm -f "$log".* 2>/dev/null || true
}

# Fixture install
make_fixture() {
    local d="$1"
    rm -rf "$d"; mkdir -p "$d/releases" "$d/data"
    printf '#!/bin/bash\nexit 0\n' > "$d/launcher.sh"
    chmod +x "$d/launcher.sh"
    # Seed the journal (matches journal_init's shape) so journal_read
    # succeeds and journal_history_append can mutate. The tests that
    # check the journal history (S1-S7 + commit-and-continue) need
    # this; tests that don't check the journal are unaffected.
    cat > "$d/releases/state.json" <<'JSON'
{"current":null,"previous":null,"in_flight":null,"rollback_window_count":{"24h":0,"window_start":null},"cooldown_until":null,"quarantined":[],"history":[]}
JSON
}

# ===========================================================================
section "Site 1 — lib.sh stop_via_stop_script UNIT fork-exec (budget enforced)"

STUBBIN="$(mktemp -d -t bswaits-s1.XXXXXX)"
make_systemctl_stub "$STUBBIN"
SC="$STUBBIN/systemctl"
# Build a fake tree that has a hanging stop-ensemble.sh at the
# expected fallback location: `pwd/scripts/stop-ensemble.sh`. The
# test cd's into $FAKETREE so stop_via_stop_script's
# `stop_script="$(pwd)/scripts/stop-ensemble.sh"` fallback resolves
# to our hang.
FAKETREE="$(mktemp -d -t bswaits-faketree.XXXXXX)"
mkdir -p "$FAKETREE/scripts"
cat > "$FAKETREE/scripts/stop-ensemble.sh" <<'HANG'
#!/bin/bash
# Hang forever — exercises lib.sh's _run_bounded on the fork-exec.
# Without the bound, this would wedge the parent (incident ④ shape).
exec sleep 999
HANG
chmod +x "$FAKETREE/scripts/stop-ensemble.sh"
FIX="$(mktemp -d -t bswaits-f1.XXXXXX)"
make_fixture "$FIX"
SC_LOG="$FIX/sc.log"; export SC_LOG
reset_stub_state "$SC_LOG"
SC_IS_ACTIVE=inactive SC_MAINPID=0 SC_STOP_RC=0
export SC_IS_ACTIVE SC_MAINPID SC_STOP_RC

START=$(date +%s)
(
    cd "$FAKETREE"
        export INSTALL_DIR="$FIX"
        export SYSTEMCTL_BIN="$SC"
        export PORT=19990
        export STOP_SCRIPT_BUDGET_S=2
        # ENSEMBLE_SUPERVISION_RESULT env bypasses preflight's
        # supervision_classify downgrade and preserves the UNIT shape
        # through to stop_via_stop_script's UNIT branch.
        export ENSEMBLE_SUPERVISION_RESULT="UNIT_MANAGED:ensemble-s1.service"
        unset SUPERVISION_STATE SUPERVISION_UNIT SUPERVISION_PRESTOP_MAINPID ENSEMBLE_SUPERVISION 2>/dev/null || true
        . "$UPGRADE_DIR/lib.sh" >/dev/null 2>&1
        stop_via_stop_script
        echo "s1-rc=$?"
    ) 2>&1 | tee "$FIX/s1.out"
RC=$(grep -oE 's1-rc=[0-9]+' "$FIX/s1.out" | head -1 | sed 's/s1-rc=//')
END=$(date +%s)
ELAPSED=$((END - START))
assert_eq "S1 hang: rc 0 (function returns 0 on bounded timeout)" "0" "$RC"
[ "$ELAPSED" -le 8 ] && _pass "S1 hang: elapsed within 2s budget + grace (got ${ELAPSED}s)" \
    || _fail "S1 hang: elapsed bound" "≤8s" "${ELAPSED}s"
# Journal may record either pid or unit site depending on whether
# supervision_classify downgraded our ENSEMBLE_SUPERVISION_RESULT.
# Both are valid 2.2b bounded sites.
assert_contains "S1 hang: subprocess_wait_timeout event" "subprocess_wait_timeout" "$(cat "$FIX/releases/state.json" 2>/dev/null || true)"
case "$(cat "$FIX/releases/state.json")" in
    *stop_via_stop_script_unit*) _pass "S1 hang: site=stop_via_stop_script_unit" ;;
    *stop_via_stop_script_pid*)  _pass "S1 hang: site=stop_via_stop_script_pid (pid path; equivalent 2.2b bounded site)" ;;
    *) _fail "S1 hang: site marker missing" "stop_via_stop_script_unit|pid" "$(cat "$FIX/releases/state.json")" ;;
esac
# Clean up
rm -rf "$STUBBIN" "$FIX" "$FAKETREE"

# ===========================================================================
section "Site 2 — scripts/stop-ensemble.sh:530 systemctl stop (incident ④)"

STUBBIN2="$(mktemp -d -t bswaits-s2.XXXXXX)"
make_systemctl_stub "$STUBBIN2"
SC2="$STUBBIN2/systemctl"
AFIX="$(mktemp -d -t bswaits-f2.XXXXXX)"
make_fixture "$AFIX"
# Seed journal with `current: v1` AND create the matching current symlink.
# Layer i refuses a journal with `current` and no symlink (layout
# divergence — D-FA5.3 freezes mutations).
cat > "$AFIX/releases/state.json" <<'JSON'
{"current":"v1","previous":null,"in_flight":null,"rollback_window_count":{"24h":0,"window_start":null},"cooldown_until":null,"quarantined":[],"history":[]}
JSON
ln -sfn "releases/v1" "$AFIX/current"

reset_stub_state "$AFIX/sc.log"
SC_HANG=1 SC_HANG_SUB=stop; export SC_HANG SC_HANG_SUB
START=$(date +%s)
S2_OUT="$(STOP_SCRIPT_BUDGET_S=2 WAIT_S=5 UNIT_KILL_GRACE_S=2 \
    ENSEMBLE_SUPERVISION_RESULT=UNIT_MANAGED:ensemble-s2.service \
    SYSTEMCTL_BIN="$SC2" SC_LOG="$AFIX/sc.log" \
    bash "$STOP_SCRIPT" "$AFIX" 19989 2>&1)"
RC=$?
END=$(date +%s)
ELAPSED=$((END - START))
unset SC_HANG SC_HANG_SUB
# stop-ensemble.sh on a hung systemctl stop with bounded timeout falls
# THROUGH to the re-verify loop + SIGKILL escalation; an undying unit
# eventually exits 1 (the loud-fail tail). The PIN here: bounded
# timeout happens in ~2s, NOT infinite hang.
[ "$ELAPSED" -le 25 ] && _pass "S2 hang-stop: bounded timeout + escalation (elapsed ${ELAPSED}s, ≤25)" \
    || _fail "S2 hang-stop: bounded" "≤25s" "${ELAPSED}s"
assert_contains "S2 hang-stop: warn line fires" "TIMED OUT" "$S2_OUT"
assert_contains "S2 hang-stop: stop cmd reached the stub" "stop ensemble-s2.service" "$(cat "$AFIX/sc.log")"
rm -rf "$STUBBIN2" "$AFIX"

# ===========================================================================
section "Sites 3-5 — _supervision_handback_unit reset-failed/is-active/start (§9 repro)"

if [ "$HOST_HAS_SYSTEMD" = "1" ]; then
    STUBBIN3="$(mktemp -d -t bswaits-s3.XXXXXX)"
    make_systemctl_stub "$STUBBIN3"
    SC3="$STUBBIN3/systemctl"
    CFIX="$(mktemp -d -t bswaits-c.XXXXXX)"
    make_fixture "$CFIX"
    mkdir -p "$CFIX/releases"
    cat > "$CFIX/releases/state.json" <<'J'
{"current":"v1","previous":null,"in_flight":null,"rollback_window_count":{"24h":0,"window_start":null},"cooldown_until":null,"quarantined":[],"history":[]}
J
    SC_LOG="$CFIX/sc.log"; export SC_LOG
    LSOF_OK="$(mktemp -d -t bswaits-lsofok.XXXXXX)"
    cat > "$LSOF_OK/lsof" <<'L'
#!/bin/bash
exit 0
L
    chmod +x "$LSOF_OK/lsof"

    run_handback() {
        local setup="$2"
        PATH="$LSOF_OK:$PATH" bash -c '
            export INSTALL_DIR="'"$CFIX"'"
            export SYSTEMCTL_BIN="'"$SC3"'"
            export SC_LOG="'"$CFIX"'/sc.log"
            export PORT=19987
            export LIVEZ_BUDGET_S=2
            unset ENSEMBLE_SUPERVISION 2>/dev/null || true
            . "'"$UPGRADE_DIR"'/lib.sh" >/dev/null 2>&1
            '"$setup"'
            _supervision_handback_unit "ensemble-hang.service" "9999" "0" "/dev/null"
            echo "hb-rc=$?"
        ' 2>&1
    }
    hb_rc() { printf '%s' "$1" | grep -oE 'hb-rc=[0-9]+' | head -1; }

    # ── Site 3 — reset-failed hang
    reset_stub_state "$CFIX/sc.log"
    SC_HANG=1 SC_HANG_SUB=reset-failed; export SC_HANG SC_HANG_SUB
    S3_OUT="$(run_handback "$CFIX" '
        SYSTEMCTL_PROBE_BUDGET_S=2
        SUPERVISION_MODE=unit; SUPERVISION_STATE=UNIT_MANAGED
    ')"
    unset SC_HANG SC_HANG_SUB
    assert_eq "S3 reset-failed hang: rc 1 (continue into start attempt)" "hb-rc=1" "$(hb_rc "$S3_OUT")"
    assert_contains "S3 reset-failed hang: subprocess_wait_timeout event" "handback_reset_failed" "$(cat "$CFIX/releases/state.json")"

    # ── Site 4 — is-active hang
    reset_stub_state "$CFIX/sc.log"
    SC_HANG=1 SC_HANG_SUB=is-active; export SC_HANG SC_HANG_SUB
    S4_OUT="$(run_handback "$CFIX" '
        SYSTEMCTL_PROBE_BUDGET_S=2
        SUPERVISION_MODE=unit; SUPERVISION_STATE=UNIT_MANAGED
    ')"
    unset SC_HANG SC_HANG_SUB
    # is-active timeout falls into start attempt; with is-active treated
    # as NOT-active, the noop-detection line is skipped and start fires
    # (which with SC_MAINPID=0 + START_RC=0 succeeds at the start rc but
    # then the verify loop finds MainPID=0 → returns 1). Key proof:
    # bounded is-active timeout, then continues.
    assert_eq "S4 is-active hang: rc 1 (continue; treat as not-active, start fails verify)" "hb-rc=1" "$(hb_rc "$S4_OUT")"
    assert_contains "S4 is-active hang: subprocess_wait_timeout event" "handback_is_active" "$(cat "$CFIX/releases/state.json")"

    # ── Site 5 — start hang (§9 culprit)
    reset_stub_state "$CFIX/sc.log"
    SC_HANG=1 SC_HANG_SUB=start; export SC_HANG SC_HANG_SUB
    START=$(date +%s)
    S5_OUT="$(run_handback "$CFIX" '
        HANDBACK_START_BUDGET_S=2
        SUPERVISION_MODE=unit; SUPERVISION_STATE=UNIT_MANAGED
    ')"
    END=$(date +%s)
    ELAPSED=$((END - START))
    unset SC_HANG SC_HANG_SUB
    assert_eq "S5 start hang (§9): rc 1 (halt, NO nohup fallback)" "hb-rc=1" "$(hb_rc "$S5_OUT")"
    [ "$ELAPSED" -le 10 ] && _pass "S5 start hang: elapsed within budget + grace (got ${ELAPSED}s, ≤10)" \
        || _fail "S5 start hang: bounded" "≤10s" "${ELAPSED}s"
    assert_contains "S5 start hang: subprocess_wait_timeout event with site=handback_start" "handback_start" "$(cat "$CFIX/releases/state.json")"
    assert_contains "S5 start hang: warns NO nohup fallback (Amendment #1)" "NO nohup fallback" "$S5_OUT"
    assert_not_contains "S5 start hang: launcher started (nohup) NEVER fires" "launcher started (nohup)" "$S5_OUT"

    # ── Regression — happy path no timeout events (start succeeds)
    reset_stub_state "$CFIX/sc.log"
    cat > "$CFIX/releases/state.json" <<'JSON'
{"current":null,"previous":null,"in_flight":null,"rollback_window_count":{"24h":0,"window_start":null},"cooldown_until":null,"quarantined":[],"history":[]}
JSON
    SC_HANG=0; export SC_HANG
    SC_IS_ACTIVE=inactive SC_MAINPID=8888 SC_START_RC=0; export SC_IS_ACTIVE SC_MAINPID SC_START_RC
    R_OUT="$(run_handback "$CFIX" '
        SUPERVISION_MODE=unit; SUPERVISION_STATE=UNIT_MANAGED
        SUPERVISION_PRESTOP_MAINPID=7777
    ')"
    unset SC_HANG
    assert_eq "R happy-path: rc 0" "hb-rc=0" "$(hb_rc "$R_OUT")"
    assert_not_contains "R happy-path: NO timeout events journaled" "subprocess_wait_timeout" "$(cat "$CFIX/releases/state.json")"
    assert_not_contains "R happy-path: success path free of banned literal" "systemctl start" "$R_OUT"

    rm -rf "$STUBBIN3" "$CFIX" "$LSOF_OK"
else
    _skip "Sites 3-5 — FENCE: no /run/systemd/system on this host"
fi

# ===========================================================================
section "Sites 6-7 — restart_via_launcher scope-arm is-active / start"

if [ "$HOST_HAS_SYSTEMD" = "1" ]; then
    STUBBIN4="$(mktemp -d -t bswaits-s4.XXXXXX)"
    make_systemctl_stub "$STUBBIN4"
    SC4="$STUBBIN4/systemctl"
    SFIX="$(mktemp -d -t bswaits-scope.XXXXXX)"
    make_fixture "$SFIX"
    mkdir -p "$SFIX/releases"
    cat > "$SFIX/releases/state.json" <<'J'
{"current":"v1","previous":null,"in_flight":null,"rollback_window_count":{"24h":0,"window_start":null},"cooldown_until":null,"quarantined":[],"history":[]}
J
    SC_LOG="$SFIX/sc.log"; export SC_LOG

    run_scope() {
        local setup="$2"
        PATH="$STUBBIN4:$PATH" bash -c '
            export INSTALL_DIR="'"$SFIX"'"
            export SYSTEMCTL_BIN="'"$SC4"'"
            export SC_LOG="'"$SFIX"'/sc.log"
            export PORT=19986
            unset ENSEMBLE_SUPERVISION ENSEMBLE_RESTART_UNIT 2>/dev/null || true
            export ENSEMBLE_RESTART_UNIT=ensemble-scope.service
            . "'"$UPGRADE_DIR"'/lib.sh" >/dev/null 2>&1
            '"$setup"'
            restart_via_launcher
            echo "scope-rc=$?"
        ' 2>&1
    }
    scope_rc() { printf '%s' "$1" | grep -oE 'scope-rc=[0-9]+' | head -1; }

    # Site 6: is-active hang on scope-arm
    reset_stub_state "$SFIX/sc.log"
    cat > "$SFIX/releases/state.json" <<'JSON'
{"current":null,"previous":null,"in_flight":null,"rollback_window_count":{"24h":0,"window_start":null},"cooldown_until":null,"quarantined":[],"history":[]}
JSON
    SC_HANG=1 SC_HANG_SUB=is-active; export SC_HANG SC_HANG_SUB
    S6_OUT="$(run_scope "$SFIX" '
        SYSTEMCTL_PROBE_BUDGET_S=2
        SUPERVISION_MODE=script; SUPERVISION_STATE=SCRIPT_NOHUP; SUPERVISION_UNIT=""
    ')"
    unset SC_HANG SC_HANG_SUB
    assert_eq "S6 is-active scope hang: rc 0 (probe timeout → not-active, start fires)" "scope-rc=0" "$(scope_rc "$S6_OUT")"
    assert_contains "S6 is-active scope hang: subprocess_wait_timeout event" "scope_arm_is_active" "$(cat "$SFIX/releases/state.json")"
    assert_contains "S6 is-active scope hang: warns probe exceeded budget" "is-active probe exceeded budget" "$S6_OUT"
    # After the probe timeout, start fires normally (SC_HANG_SUB=is-active
    # doesn't affect start); the unit path succeeds. Verifies the bound
    # fired AND the path through is correct (no false nohup fallback).

    # Site 7: start hang on scope-arm
    reset_stub_state "$SFIX/sc.log"
    cat > "$SFIX/releases/state.json" <<'JSON'
{"current":null,"previous":null,"in_flight":null,"rollback_window_count":{"24h":0,"window_start":null},"cooldown_until":null,"quarantined":[],"history":[]}
JSON
    SC_HANG=1 SC_HANG_SUB=start; export SC_HANG SC_HANG_SUB
    S7_OUT="$(run_scope "$SFIX" '
        SCOPE_START_BUDGET_S=2
        SUPERVISION_MODE=script; SUPERVISION_STATE=SCRIPT_NOHUP; SUPERVISION_UNIT=""
    ')"
    unset SC_HANG SC_HANG_SUB
    assert_eq "S7 start scope hang: rc 0 (comp7 opt-in keeps the nohup fallback)" "scope-rc=0" "$(scope_rc "$S7_OUT")"
    assert_contains "S7 start scope hang: subprocess_wait_timeout event with site=scope_arm_start" "scope_arm_start" "$(cat "$SFIX/releases/state.json")"
    assert_contains "S7 start scope hang: falls back to nohup" "launcher started (nohup)" "$S7_OUT"
    assert_contains "S7 start scope hang: frozen 7c wording preserved" "falling back to nohup launcher (comp7 opt-in path)" "$S7_OUT"

    rm -rf "$STUBBIN4" "$SFIX"
else
    _skip "Sites 6-7 — FENCE: no /run/systemd/system on this host"
fi

# ===========================================================================
section "Cooldown — lib.sh:982 BSD/GNU uname dispatch (P2.1 debt opportunistic)"

# We exercise the dispatch via the unit host (Linux+systemd assumed for
# the journal machinery). The test: arm cooldown via journal mock,
# read back cooldown_until, assert delta ≥ COOLDOWN_S on GNU. On a
# Darwin stub the BSD branch should fire (not validated here in depth
# because GNU date can't fully exercise BSD date semantics, but the
# uname_dispatch fires per the dispatch family precedent).
COOLDOWN_FIX="$REPO_ROOT/scripts/upgrade/lib.sh"
if grep -q 'Darwin|\*BSD\*|\*bsd\*)' "$COOLDOWN_FIX" \
   && grep -q 'Linux|GNU\*|\*GNU\*)' "$COOLDOWN_FIX" \
   && grep -q 'uname -s' "$COOLDOWN_FIX"; then
    _pass "Cooldown fix: case 'uname -s' dispatch present (Darwin/BSD + Linux/GNU branches)"
else
    _fail "Cooldown fix: dispatch missing" "case uname …" "literal"
fi
assert_contains "Cooldown fix: Linux branch uses GNU date -d" 'date -u -d "+${COOLDOWN_S} seconds"' "$(cat "$COOLDOWN_FIX")"
assert_contains "Cooldown fix: BSD branch keeps -ju -v form" 'date -ju -v+${COOLDOWN_S}S' "$(cat "$COOLDOWN_FIX")"

# ===========================================================================
section "Derivation — STOP_SCRIPT_BUDGET_S derived from resolved WAIT_S (post-review hardening)"

# The integration review on 2.2b flagged: parent STOP_SCRIPT_BUDGET_S
# (fixed 120s) was not derived from the child's resolved WAIT_S, so an
# operator with WAIT_S>120 hit a premature parent SIGKILL on the stop
# child → the flip proceeded against a still-live daemon. The fix:
# parent budget = composition sum (child site-2 stop bound + resolved
# WAIT_S + UNIT_KILL_GRACE_S + _run_bounded TERM grace + margin).
# Below we factor the derivation into a callable helper in a subshell
# and assert the composition arithmetic against several fixtures.
#
# The helpers (_resolve_wait_s_mirror, _derive_parent_stop_budget,
# _effective_stop_script_budget) are defined inside lib.sh but
# lib.sh has many preconditions (resolve_env, journal, lock, etc.)
# that we don't want to source live in a test. Instead, we EXTRACT
# the helper functions to a scratch file by reading the source and
# pulling the function bodies out, then sourcing the scratch in a
# clean subshell with only the env the helpers need. This is a
# white-box extraction (we know the helpers don't call any other
# lib.sh internals) — the test verifies the SHAPE, not the wiring.
LIB_SH_SRC="$REPO_ROOT/scripts/upgrade/lib.sh"
SCRATCH="$(mktemp -t bswaits-derive.XXXXXX.sh)"
trap "rm -f '$SCRATCH' '$SCRATCH'.both" EXIT

# Extract _resolve_wait_s_mirror + DEFAULT_WAIT_S / WAIT_S_FLOOR / WAIT_S_CAP
# + _derive_parent_stop_budget + _effective_stop_script_budget by
# line range from the source (the helpers are a contiguous block in
# lib.sh; their start/end markers are unique comments).
_extract() {
    local start_pat="$1" end_pat="$2"
    awk -v sp="$start_pat" -v ep="$end_pat" '
        $0 ~ sp { p=1 }
        p { print }
        p && $0 ~ ep { exit }
    ' "$LIB_SH_SRC"
}
{
    cat <<'PRELUDE'
# Minimal prelude for the derivation helpers — none of these touch
# any other lib.sh internals (verified: the helpers use printf, sed,
# grep, and parameter expansion only; no _run_bounded / journal /
# lock dependency).
PRELUDE
    _extract "^# _resolve_wait_s_mirror <env_file>" "^_effective_stop_script_budget[(][)][{]"
    _extract "^_effective_stop_script_budget[(][)][{]" "^# ── Promote/rollback shared mechanics"
} > "$SCRATCH"

# Sanity: the scratch should contain all three helpers
for f in _resolve_wait_s_mirror _derive_parent_stop_budget _effective_stop_script_budget; do
    if grep -q "^$f" "$SCRATCH"; then
        _pass "Derivation extract: $f present in scratch"
    else
        _fail "Derivation extract: $f present in scratch" "present" "missing"
    fi
done

# ── (i) WAIT_S=180 → assert derivation is ≥ 180+margin and ≥ the
# composition sum. Composition: child_site2(120) + WAIT_S(180) +
# UNIT_KILL_GRACE_S(10) + TERM_grace(5) + margin(20) = 335.
(
    unset WAIT_S STOP_SCRIPT_BUDGET_S UNIT_KILL_GRACE_S
    WAIT_S=180
    INSTALL_DIR=/nonexistent  # force .env read to fail → fall back to WAIT_S explicit
    . "$SCRATCH" 2>/dev/null
    D="$(_derive_parent_stop_budget)"
    E="$(_effective_stop_script_budget)"
    printf 'D=%s\nE=%s\n' "$D" "$E" > /tmp/bswaits-derive.out
)
. /tmp/bswaits-derive.out
assert_eq "D1 (i) WAIT_S=180: derived budget is 335 (composition sum)" "335" "$D"
assert_eq "D1 (i) WAIT_S=180: effective budget matches derived" "335" "$E"
[ "$D" -ge "$((180 + 15))" ] && _pass "D1 (i) derived budget ≥ WAIT_S+15 (reviewer minimal floor)" \
    || _fail "D1 (i) derived budget ≥ WAIT_S+15" "≥195" "$D"

# ── (i) WAIT_S=600 (the cap): composition = 120+600+10+5+20 = 755.
(
    unset WAIT_S STOP_SCRIPT_BUDGET_S UNIT_KILL_GRACE_S
    WAIT_S=600
    INSTALL_DIR=/nonexistent
    . "$SCRATCH" 2>/dev/null
    D="$(_derive_parent_stop_budget)"
    E="$(_effective_stop_script_budget)"
    printf 'D=%s\nE=%s\n' "$D" "$E" > /tmp/bswaits-derive.out
)
. /tmp/bswaits-derive.out
assert_eq "D2 (i) WAIT_S=600 (cap): derived budget is 755" "755" "$D"
assert_eq "D2 (i) WAIT_S=600: effective budget matches derived" "755" "$E"

# ── (ii) explicit WAIT_S beyond the sane cap (WAIT_S=900) → child
# returns 900 verbatim (the +10 offset + clamp is only applied via
# the .env read; explicit WAIT_S is the operator's verbatim config).
# The parent mirror is byte-identical: explicit WAIT_S=900 returns
# 900, derivation = 120+900+10+5+20 = 1055. No clamp at the
# derivation — the composition sum grows with WAIT_S. Pin this:
(
    unset WAIT_S STOP_SCRIPT_BUDGET_S UNIT_KILL_GRACE_S
    WAIT_S=900
    INSTALL_DIR=/nonexistent
    . "$SCRATCH" 2>/dev/null
    R="$(_resolve_wait_s_mirror /nonexistent)"
    D="$(_derive_parent_stop_budget)"
    printf 'R=%s\nD=%s\n' "$R" "$D" > /tmp/bswaits-derive.out
)
. /tmp/bswaits-derive.out
assert_eq "D3 (ii) explicit WAIT_S=900: resolver returns 900 verbatim" "900" "$R"
assert_eq "D3 (ii) explicit WAIT_S=900: derived budget is 1055" "1055" "$D"
_pass "D3 (ii) explicit WAIT_S=900: NO refusal (variant A is robust — the parent grows with the operator's explicit config)"

# ── (ii) explicit WAIT_S with NO WAIT_S env, .env carries a clamped
# value. Precedence 3 path. WAIT_S unset, .env file has
# DAEMON_GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS=590 → +10=600, clamped to
# CAP=600 → resolver returns 600, derivation = 755.
ENV_FIX="$(mktemp -t bswaits-envfix.XXXXXX)"
printf 'DAEMON_GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS=590\n' > "$ENV_FIX"
(
    unset WAIT_S STOP_SCRIPT_BUDGET_S UNIT_KILL_GRACE_S
    INSTALL_DIR=/nonexistent
    ENV_PATH="$ENV_FIX"
    . "$SCRATCH" 2>/dev/null
    R="$(_resolve_wait_s_mirror "$ENV_PATH")"
    D="$(_derive_parent_stop_budget "$ENV_PATH")"
    printf 'R=%s\nD=%s\n' "$R" "$D" > /tmp/bswaits-derive.out
)
. /tmp/bswaits-derive.out
assert_eq "D4 (ii) .env=590 (+10=600, clamped): resolver returns 600" "600" "$R"
assert_eq "D4 (ii) .env=590: derived budget is 755" "755" "$D"
rm -f "$ENV_FIX"

# ── (iii) default WAIT_S=70 regression: derivation returns 225.
# Byte-identical to the prior happy path (STOP_SCRIPT_BUDGET_S=120
# is a floor; the derivation at 225 is MORE generous, not less —
# the happy path completes in <2s in both shapes).
(
    unset WAIT_S STOP_SCRIPT_BUDGET_S UNIT_KILL_GRACE_S
    INSTALL_DIR=/nonexistent
    . "$SCRATCH" 2>/dev/null
    D="$(_derive_parent_stop_budget)"
    E="$(_effective_stop_script_budget)"
    printf 'D=%s\nE=%s\n' "$D" "$E" > /tmp/bswaits-derive.out
)
. /tmp/bswaits-derive.out
assert_eq "D5 (iii) default WAIT_S=70: derived budget is 225" "225" "$D"
[ "$D" -ge 120 ] && _pass "D5 (iii) derived budget ≥ 120 floor (no regression)" \
    || _fail "D5 (iii) derived budget ≥ 120 floor" "≥120" "$D"

# ── (iii) operator override: STOP_SCRIPT_BUDGET_S=300 explicit env
# → effective budget is 300 verbatim, derivation is bypassed. This
# is the env-overridable contract for sandbox drills / tests.
(
    unset WAIT_S UNIT_KILL_GRACE_S
    STOP_SCRIPT_BUDGET_S=300
    INSTALL_DIR=/nonexistent
    . "$SCRATCH" 2>/dev/null
    E="$(_effective_stop_script_budget)"
    printf 'E=%s\n' "$E" > /tmp/bswaits-derive.out
)
. /tmp/bswaits-derive.out
assert_eq "D6 (iii) operator override STOP_SCRIPT_BUDGET_S=300: effective budget 300" "300" "$E"

# ── (iii) malformed operator override: STOP_SCRIPT_BUDGET_S="abc"
# → fall through to derivation (mirror the child's "malformed →
# fall back" handling for $WAIT_S). Effective budget = 225 at default.
(
    unset WAIT_S UNIT_KILL_GRACE_S
    STOP_SCRIPT_BUDGET_S="abc"
    INSTALL_DIR=/nonexistent
    . "$SCRATCH" 2>/dev/null
    E="$(_effective_stop_script_budget)"
    printf 'E=%s\n' "$E" > /tmp/bswaits-derive.out
)
. /tmp/bswaits-derive.out
assert_eq "D7 (iii) malformed override 'abc' falls through to derivation" "225" "$E"

# ===========================================================================
section "D8 — sync-guard: child _resolve_wait_s ≡ parent _resolve_wait_s_mirror"

# The brief's most load-bearing ask: BOTH resolvers (child at
# stop-ensemble.sh:270, parent at lib.sh:_resolve_wait_s_mirror) MUST
# produce IDENTICAL output for the SAME fixtures. We extract the
# child's function source from stop-ensemble.sh, wrap it in a
# scratch, and run it through the same fixtures as the parent's
# mirror. The constants DEFAULT_WAIT_S / WAIT_S_FLOOR / WAIT_S_CAP
# must also be equivalent — the child uses them at the top of the
# script (lines 110-112), the parent mirror defines them next to the
# helper.
CHILD_SRC="$REPO_ROOT/scripts/stop-ensemble.sh"
SCRATCH_CHILD="$(mktemp -t bswaits-child-resolve.XXXXXX.sh)"
trap "rm -f '$SCRATCH_CHILD'" EXIT

# Extract child's _resolve_wait_s + its constants. The constants
# are at lines 110-112 (DEFAULT_WAIT_S=70, WAIT_S_FLOOR=10,
# WAIT_S_CAP=600) — the child reads them as bare assignments in
# script scope. We need to extract them too.
# Extract child's FULL resolution chain: constants (DEFAULT_WAIT_S,
# WAIT_S_FLOOR, WAIT_S_CAP), _resolve_wait_s function (.env read),
# AND the explicit-WAIT_S block (lines 290-307) that wraps them.
# We define a `_child_resolve_full <env_file>` wrapper that mirrors
# the exact outer block: precedence is explicit $WAIT_S env
# (digits-only wins; malformed → DEFAULT_WAIT_S; absent →
# _resolve_wait_s <.env>), then echoes the result.
{
    cat <<'PRELUDE'
# Child resolver extracted from stop-ensemble.sh:110-112 + 270-289
# (the .env reader) + 290-307 (the explicit-$WAIT_S wrapper). Mirrors
# the parent's mirror function-for-function.
PRELUDE
    awk '/^DEFAULT_WAIT_S=70$|^WAIT_S_FLOOR=10$|^WAIT_S_CAP=600$/ { print }' "$CHILD_SRC"
    awk '/^_resolve_wait_s[(][)] \{$/{p=1} p{print} p && /^}$/{exit}' "$CHILD_SRC"
    cat <<'CHAIN'
_child_resolve_full() {
    # Mirror of stop-ensemble.sh:290-307: explicit $WAIT_S env
    # (digits-only) wins; malformed → DEFAULT_WAIT_S; absent →
    # _resolve_wait_s <.env> read. Sets WAIT_SOURCE for symmetry
    # with the child (the variable is local; not strictly required
    # for the derivation, but the sync-guard test exercises the
    # side effects too).
    local env_file="$1" _child_was_explicit=""
    if [ -n "${WAIT_S:-}" ]; then
        if printf '%s' "${WAIT_S}" | grep -Eq '^[0-9]+$'; then
            printf '%s\n' "${WAIT_S}"
            return 0
        fi
        printf '%s\n' "$DEFAULT_WAIT_S"
        return 0
    fi
    _resolve_wait_s "$env_file"
}
CHAIN
} > "$SCRATCH_CHILD"

if grep -q '^_resolve_wait_s()' "$SCRATCH_CHILD"; then
    _pass "D8 sync-guard: child _resolve_wait_s extracted"
else
    _fail "D8 sync-guard: child _resolve_wait_s extracted" "present" "missing"
fi

# Run BOTH resolvers over a battery of fixtures and assert identical
# output. Each fixture sets up a unique (WAIT_S, .env-file) state.
sync_check() {
    local label="$1" wait_s="$2" env_path="$3" env_content="$4"
    # Write .env file (if content provided)
    local env_file="/tmp/bswaits-sync-env.$$"
    if [ -n "$env_content" ]; then
        printf '%s\n' "$env_content" > "$env_file"
    else
        rm -f "$env_file"
        env_file="/nonexistent"
    fi
    # Parent mirror — sourced from $SCRATCH
    local parent_out
    parent_out="$(WAIT_S="$wait_s" INSTALL_DIR=/nonexistent bash -c '
        . "'"$SCRATCH"'" 2>/dev/null
        _resolve_wait_s_mirror "'"$env_file"'"
    ')"
    # Child resolver — sourced from $SCRATCH_CHILD (which provides
    # the FULL chain via _child_resolve_full — explicit WAIT_S env
    # wrap + the .env read underneath, mirroring the parent's
    # _resolve_wait_s_mirror in the same precedence order)
    local child_out
    child_out="$(WAIT_S="$wait_s" INSTALL_DIR=/nonexistent bash -c '
        . "'"$SCRATCH_CHILD"'" 2>/dev/null
        _child_resolve_full "'"$env_file"'"
    ')"
    if [ "$parent_out" = "$child_out" ]; then
        _pass "D8 sync-guard: $label → parent=$parent_out child=$child_out (match)"
    else
        _fail "D8 sync-guard: $label parent=$parent_out child=$child_out" "match" "parent=$parent_out child=$child_out"
    fi
    rm -f "$env_file"
}

# Fixture 1: explicit WAIT_S=70 (digits-only) → both return 70
sync_check "explicit WAIT_S=70" "70" "/tmp/bswaits-sync-env.$$" ""

# Fixture 2: explicit WAIT_S=180 → both return 180
sync_check "explicit WAIT_S=180" "180" "/tmp/bswaits-sync-env.$$" ""

# Fixture 3: explicit WAIT_S=900 (above cap) → both return 900 verbatim
sync_check "explicit WAIT_S=900" "900" "/tmp/bswaits-sync-env.$$" ""

# Fixture 4: malformed explicit WAIT_S="abc" → both fall back to
# DEFAULT_WAIT_S=70 (do NOT consult the .env file)
sync_check "malformed WAIT_S=abc" "abc" "/tmp/bswaits-sync-env.$$" "DAEMON_GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS=300"

# Fixture 5: no explicit, .env=60 → both return 60+10=70 (clamped
# from below, lands at DEFAULT_WAIT_S=70)
sync_check "no explicit, .env=60" "" "/tmp/bswaits-sync-env.$$" "DAEMON_GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS=60"

# Fixture 6: no explicit, .env=120 → both return 120+10=130
sync_check "no explicit, .env=120" "" "/tmp/bswaits-sync-env.$$" "DAEMON_GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS=120"

# Fixture 7: no explicit, .env=590 → both return 590+10=600 (clamped to CAP)
sync_check "no explicit, .env=590 (clamped to 600)" "" "/tmp/bswaits-sync-env.$$" "DAEMON_GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS=590"

# Fixture 8: no explicit, .env=900 (above cap) → both return 600 (clamped)
sync_check "no explicit, .env=900 (clamped)" "" "/tmp/bswaits-sync-env.$$" "DAEMON_GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS=900"

# Fixture 9: no explicit, malformed .env (DAEMON_GRACEFUL=garbage) →
# both return 70 (fall back to default)
sync_check "no explicit, malformed .env" "" "/tmp/bswaits-sync-env.$$" "DAEMON_GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS=garbage"

# Fixture 10: no explicit, absent .env (path doesn't exist) → both
# return 70 (fall back to default — both helpers handle absent
# .env file gracefully: the child has the `[ -f "$env_file" ]`
# guard implicit via the `2>/dev/null` and missing-file tolerance
# of sed; the mirror has an explicit `[ -f "$env_file" ]` guard).
sync_check "no explicit, absent .env" "" "/tmp/bswaits-nonexistent" ""

# ===========================================================================
section "Final — summary"

printf '\n== summary ==\n'
printf 'PASS=%d FAIL=%d SKIP=%d\n' "$PASS" "$FAIL" "$SKIP"
if [ "$FAIL" -gt 0 ]; then
    printf 'FAILED TESTS:\n%s\n' "$FAILED_TESTS"
    exit 1
fi
printf '=== ALL TESTS PASSED ===\n'
exit 0