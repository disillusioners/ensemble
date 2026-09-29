#!/bin/bash
# ============================================================================
# tests/test_supervision_stop_handback.sh — pin tests for the P2 unit-aware
# stop path + the P3 supervision-aware hand-back (supervision-detection P5)
# ============================================================================
# Items covered (mission matrix 5, 6, 7, 15):
#
#   5. Stop unit-path pins — scripted is-active/MainPID/port sequences via
#      the SYSTEMCTL_BIN stub, incl. the b″ respawn simulation (old pids
#      dead, unit still active: old code false-stopped, new code WAITS on
#      unit state), kill-escalation (UNIT_KILL_GRACE_S), fail-loud tail on
#      an undying unit. SCRIPT/SCOPE/BSD arms byte-identical (zero
#      systemctl calls).
#   6. Hand-back matrix — unit happy (new MainPID≠prestop + port verify);
#      failure→halt-NO-fallback (start rc≠0 / stale MainPID / never-appears
#      timeout); scope→unit self-heal (+ supervision_handback journal
#      event, outcome=degraded); scope-no-unit (nohup byte-identical +
#      WARN, zero systemctl calls); comp7 a′ shapes (already-active+serving
#      verified success, already-active+not-serving no-false-success,
#      error propagation).
#   7. DUAL_FIGHT → 78 PRE-TXN (halt event BEFORE any mutation; also at
#      the P2 stop-site refuse-before-stop).
#   15. restart.sh:202 dedup — the single-announcement behavior (the
#      outer 'stop: ownership-scoped SINGLE-TERM' line was removed in P3;
#      stop_via_stop_script announces the actual path exactly once).
#
# 7b SUBSTRING TRAP discipline: the banned 'systemctl start' literal must
# NEVER appear on a success path — every success-path pin carries an
# assert_not_contains for it (the frozen 7c wording lives only on the
# failure path, pinned with assert_contains like comp7 7c).
#
# Stub seams (P2/P3-designed): SYSTEMCTL_BIN (PATH-independent absolute
# override), lsof via PATH injection, uname via PATH injection (Darwin
# arm), _supervision_owned_pids function stubs where pid discovery is
# irrelevant. Fixture INSTALL_DIRs are throwaway mktemp dirs.
#
# Systemd-host arms are NAMED-FENCED (no /run/systemd/system → counted
# SKIP, never FAIL).
#
# Run:
#   bash tests/test_supervision_stop_handback.sh
# ============================================================================

set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UPGRADE_DIR="$REPO_ROOT/scripts/upgrade"
STOP_SCRIPT="$REPO_ROOT/scripts/stop-ensemble.sh"

PASS=0
FAIL=0
SKIP=0
SKIPPED=""
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
    SKIPPED="$SKIPPED
  ⏭ $1"
    printf 'SKIP: %s\n' "$1" >&2
}
assert_eq() {
    local name="$1" expected="$2" actual="$3"
    if [ "$expected" = "$actual" ]; then _pass "$name"; else _fail "$name" "$expected" "$actual"; fi
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

REAL_UNAME="$(command -v uname)"

HOST_HAS_SYSTEMD=0
[ "$(uname -s)" = "Linux" ] && [ -d /run/systemd/system ] && HOST_HAS_SYSTEMD=1

# ── scripted systemctl stub ──────────────────────────────────────────────────
# Behavior driven by env SC_* knobs read at call time:
#   SC_STOP_RC        rc for `stop`                        (default 0)
#   SC_STOP_ERR       stderr line for a failing stop       (default "")
#   SC_KILL_TO_STOP   when 1, `kill` flips the unit to stopped (escalation sim)
#   SC_POST_KILL_ACTIVE when 1, unit STAYS active after kill (undying)
# Cross-call state via marker files next to SC_LOG (env cannot propagate
# from a stub child back to the caller): `.killed` flips is-active→inactive
# and MainPID→0 AFTER a successful escalation kill.
make_systemctl_stub() {
    local dir="$1"
    mkdir -p "$dir"
    cat > "$dir/systemctl" <<'STUB'
#!/bin/bash
echo "$*" >> "${SC_LOG:-/dev/null}"
sub="$1"; shift
case "$sub" in
    is-active)
        if [ -f "${SC_LOG}.killed" ]; then echo inactive; exit 0; fi
        n="$(cat "${SC_LOG}.isactive.n" 2>/dev/null || echo 0)"
        n=$((n + 1)); printf '%s' "$n" > "${SC_LOG}.isactive.n"
        if [ -n "${SC_IS_ACTIVE_N:-}" ] && [ "$n" -gt "${SC_IS_ACTIVE_N}" ] \
           && [ -n "${SC_IS_ACTIVE2:-}" ]; then
            echo "${SC_IS_ACTIVE2}"; exit 0
        fi
        echo "${SC_IS_ACTIVE:-inactive}"; exit 0 ;;
    show)
        prop=""; unit=""
        while [ $# -gt 0 ]; do
            case "$1" in
                -p) prop="$2"; shift 2 ;;
                --value) shift ;;
                *) unit="$1"; shift ;;
            esac
        done
        case "$prop" in
            MainPID)
                if [ -f "${SC_LOG}.killed" ]; then echo 0; exit 0; fi
                n="$(cat "${SC_LOG}.mp.n" 2>/dev/null || echo 0)"
                n=$((n + 1)); printf '%s' "$n" > "${SC_LOG}.mp.n"
                if [ -n "${SC_MAINPID_N:-}" ] && [ "$n" -gt "${SC_MAINPID_N}" ] \
                   && [ -n "${SC_MAINPID2:-}" ]; then
                    echo "${SC_MAINPID2}"; exit 0
                fi
                echo "${SC_MAINPID:-0}" ; exit 0 ;;
            ControlGroup) echo "${SC_CONTROLGROUP:-}" ; exit 0 ;;
            Restart)      echo "${SC_RESTART:-no}"     ; exit 0 ;;
        esac
        exit 0 ;;
    start)
        if [ -n "${SC_START_ERR}" ]; then echo "${SC_START_ERR}" >&2; fi
        exit "${SC_START_RC:-0}" ;;
    stop)
        if [ -n "${SC_STOP_ERR:-}" ]; then echo "${SC_STOP_ERR}" >&2; fi
        exit "${SC_STOP_RC:-0}" ;;
    kill)
        # escalation sim: default = the kill WORKS (marker flips the unit
        # to stopped for subsequent is-active/MainPID calls)
        if [ "${SC_POST_KILL_ACTIVE:-0}" != "1" ]; then
            : > "${SC_LOG}.killed"
        fi
        exit 0 ;;
    reset-failed) exit 0 ;;
    daemon-reload) exit 0 ;;
    enable) exit 0 ;;
    *) exit 0 ;;
esac
STUB
    chmod +x "$dir/systemctl"
}

# reset the stub's cross-call state (log + counters + markers).
reset_stub_state() {
    local log="$1"
    : > "$log"
    rm -f "$log".* 2>/dev/null || true
}

# ── lsof stub (port-serving conjunct) ───────────────────────────────────────
make_lsof_stub() {
    local dir="$1" rc="$2"
    mkdir -p "$dir"
    cat > "$dir/lsof" <<STUB
#!/bin/bash
echo "lsof \$*" >> "\${LSOF_LOG:-/dev/null}"
exit $rc
STUB
    chmod +x "$dir/lsof"
}

# Fixture install with a stub launcher (comp7 precedent).
make_fixture() {
    local d="$1"
    rm -rf "$d"; mkdir -p "$d/releases" "$d/data"
    printf '#!/bin/bash\nexit 0\n' > "$d/launcher.sh"
    chmod +x "$d/launcher.sh"
}

# ===========================================================================
section "A — stop-ensemble.sh unit-path pins (SYSTEMCTL_BIN stub)"

if [ "$HOST_HAS_SYSTEMD" = "1" ]; then
    STUBBIN="$(mktemp -d -t supstop-stub.XXXXXX)"
    make_systemctl_stub "$STUBBIN"
    SC="$STUBBIN/systemctl"
    AFIX="$(mktemp -d -t supstop-a.XXXXXX)"

    # A1 happy unit stop: stop rc0 → poll inactive+MainPID=0 → exit 0.
    SC_LOG="$AFIX/sc.log"; export SC_LOG
    reset_stub_state "$SC_LOG"
    SC_IS_ACTIVE=inactive SC_MAINPID=0 SC_STOP_RC=0
    export SC_IS_ACTIVE SC_MAINPID SC_STOP_RC
    A1_OUT="$(WAIT_S=10 ENSEMBLE_SUPERVISION_RESULT=UNIT_MANAGED:ensemble-a1.service \
        SYSTEMCTL_BIN="$SC" bash "$STOP_SCRIPT" "$AFIX" 19999 2>&1)"
    A1_RC=$?
    assert_eq "A1 happy unit stop: exit 0" "0" "$A1_RC"
    assert_contains "A1 cites unit-owned stop (b″)" "unit-owned stop: systemctl stop ensemble-a1.service" "$A1_OUT"
    assert_contains "A1 verifies UNIT STATE not pids" "verifying UNIT STATE, not pids" "$A1_OUT"
    assert_contains "A1 stopped confirmation" "ensemble-a1.service stopped (unit not running + MainPID=0" "$A1_OUT"
    assert_contains "A1 done line (unit path)" "done — $AFIX is stopped (unit path)" "$A1_OUT"
    assert_not_contains "A1 success path free of the banned literal" "systemctl start" "$A1_OUT"
    assert_contains "A1 stop dispatched via stub" "stop ensemble-a1.service" "$(cat "$SC_LOG")"

    # A2 b″ respawn simulation: unit respawns (is-active stays active,
    # old MainPID dead) for two polls, THEN settles inactive. Old code
    # false-stopped on the dead-pid poll; the unit-state poll must WAIT.
    reset_stub_state "$SC_LOG"
    SC_IS_ACTIVE=active SC_IS_ACTIVE2=inactive SC_IS_ACTIVE_N=2
    SC_MAINPID=4242 SC_MAINPID2=0 SC_MAINPID_N=2
    export SC_IS_ACTIVE SC_IS_ACTIVE2 SC_IS_ACTIVE_N SC_MAINPID SC_MAINPID2 SC_MAINPID_N
    A2_OUT="$(WAIT_S=10 ENSEMBLE_SUPERVISION_RESULT=UNIT_MANAGED:ensemble-a2.service \
        SYSTEMCTL_BIN="$SC" bash "$STOP_SCRIPT" "$AFIX" 19999 2>&1)"
    A2_RC=$?
    assert_eq "A2 b″ respawn sim: exit 0 (waited through the respawn)" "0" "$A2_RC"
    assert_contains "A2 b″ still confirms via unit state" "ensemble-a2.service stopped" "$A2_OUT"
    assert_not_contains "A2 b″ no false 'not confirmed' while waiting" "not confirmed stopped" "$A2_OUT"
    IA_N="$(cat "$SC_LOG.isactive.n" 2>/dev/null || echo 0)"
    [ "$IA_N" -ge 3 ] && _pass "A2 b″ polled unit state ≥3 times (waited, not false-stopped)" \
        || _fail "A2 b″ poll count" "≥3" "$IA_N"

    # A3 systemctl stop FAILS → exit 1, fail-loud, NO pid-TERM fallback.
    reset_stub_state "$SC_LOG"
    SC_STOP_RC=1 SC_STOP_ERR="Unit ensemble-a3.service not loaded"
    export SC_STOP_RC SC_STOP_ERR
    unset SC_IS_ACTIVE2 SC_IS_ACTIVE_N SC_MAINPID2 SC_MAINPID_N 2>/dev/null || true
    SC_IS_ACTIVE=active SC_MAINPID=4242; export SC_IS_ACTIVE SC_MAINPID
    A3_OUT="$(WAIT_S=10 ENSEMBLE_SUPERVISION_RESULT=UNIT_MANAGED:ensemble-a3.service \
        SYSTEMCTL_BIN="$SC" bash "$STOP_SCRIPT" "$AFIX" 19999 2>&1)"
    A3_RC=$?
    assert_eq "A3 stop cmd failure: exit 1" "1" "$A3_RC"
    assert_contains "A3 fail-loud (no pid-TERM fallback under a live unit)" "NOT falling back to pid TERMs under a live unit" "$A3_OUT"
    assert_contains "A3 surfaces first stderr line" "Unit ensemble-a3.service not loaded" "$A3_OUT"

    # A4 escalation: unit never settles within WAIT_S → systemctl kill →
    # (kill works) → stopped after SIGKILL escalation → exit 0.
    reset_stub_state "$SC_LOG"
    SC_STOP_RC=0; unset SC_STOP_ERR; export SC_STOP_RC; unset SC_STOP_ERR 2>/dev/null || true
    SC_IS_ACTIVE=active SC_MAINPID=4242
    export SC_IS_ACTIVE SC_MAINPID
    # keep MainPID nonzero through the kill (cgroup teardown in flight)
    A4_OUT="$(WAIT_S=10 UNIT_KILL_GRACE_S=2 ENSEMBLE_SUPERVISION_RESULT=UNIT_MANAGED:ensemble-a4.service \
        SYSTEMCTL_BIN="$SC" bash "$STOP_SCRIPT" "$AFIX" 19999 2>&1)"
    A4_RC=$?
    assert_eq "A4 kill escalation recovers: exit 0" "0" "$A4_RC"
    assert_contains "A4 announces the escalation" "escalating: systemctl kill" "$A4_OUT"
    assert_contains "A4 post-escalation confirmation" "stopped after SIGKILL escalation" "$A4_OUT"
    assert_contains "A4 kill actually dispatched" "kill ensemble-a4.service" "$(cat "$SC_LOG")"

    # A5 undying unit: kill issued, unit STILL active after grace →
    # exit 1 + the fail-loud tail.
    reset_stub_state "$SC_LOG"
    SC_POST_KILL_ACTIVE=1; export SC_POST_KILL_ACTIVE
    A5_OUT="$(WAIT_S=10 UNIT_KILL_GRACE_S=2 ENSEMBLE_SUPERVISION_RESULT=UNIT_MANAGED:ensemble-a5.service \
        SYSTEMCTL_BIN="$SC" bash "$STOP_SCRIPT" "$AFIX" 19999 2>&1)"
    A5_RC=$?
    assert_eq "A5 undying unit: exit 1" "1" "$A5_RC"
    assert_contains "A5 fail-loud tail" "STILL not confirmed stopped after systemctl kill" "$A5_OUT"
    assert_contains "A5 names caller policy" "caller aborts; its txn policy owns recovery" "$A5_OUT"
    unset SC_POST_KILL_ACTIVE

    # A6 DRY_RUN on the unit arm: plan only, zero stub stop calls.
    reset_stub_state "$SC_LOG"
    SC_IS_ACTIVE=inactive SC_MAINPID=0; export SC_IS_ACTIVE SC_MAINPID
    A6_OUT="$(WAIT_S=10 DRY_RUN=1 ENSEMBLE_SUPERVISION_RESULT=UNIT_MANAGED:ensemble-a6.service \
        SYSTEMCTL_BIN="$SC" bash "$STOP_SCRIPT" "$AFIX" 19999 2>&1)"
    A6_RC=$?
    assert_eq "A6 DRY_RUN exit 0" "0" "$A6_RC"
    assert_contains "A6 DRY_RUN plan names the unit" "would systemctl stop ensemble-a6.service" "$A6_OUT"
    assert_not_contains "A6 DRY_RUN dispatched nothing" "stop ensemble-a6.service" "$(cat "$SC_LOG")"

    rm -rf "$STUBBIN" "$AFIX"
else
    _skip "A stop unit-path pins — FENCE: no /run/systemd/system on this host"
fi

# ===========================================================================
section "A′ — SCRIPT / SCOPE / BSD / malformed arms byte-identical"

APFIX="$(mktemp -d -t supstop-b.XXXXXX)"
STUBBIN2="$(mktemp -d -t supstop-stub2.XXXXXX)"
make_systemctl_stub "$STUBBIN2"
SC2="$STUBBIN2/systemctl"
SC_LOG="$APFIX/sc2.log"; export SC_LOG

# A′1 no classification → pid-scoped path, ZERO systemctl calls.
reset_stub_state "$SC_LOG"
B1_OUT="$(WAIT_S=10 SYSTEMCTL_BIN="$SC2" bash "$STOP_SCRIPT" "$APFIX" 19999 2>&1)"
B1_RC=$?
assert_eq "A′1 no-classification arm: exit 0 (nothing owned)" "0" "$B1_RC"
assert_eq "A′1 zero systemctl calls" "" "$(cat "$SC_LOG")"
assert_not_contains "A′1 no unit-owned stop line" "unit-owned stop" "$B1_OUT"

# A′2 SCOPE_SURVIVOR classification → pid-scoped path (NOT the unit arm).
reset_stub_state "$SC_LOG"
B2_OUT="$(WAIT_S=10 ENSEMBLE_SUPERVISION_RESULT=SCOPE_SURVIVOR SYSTEMCTL_BIN="$SC2" \
    bash "$STOP_SCRIPT" "$APFIX" 19999 2>&1)"
assert_eq "A′2 scope arm: exit 0" "0" "$?"
assert_eq "A′2 zero systemctl calls (byte-identical pid path)" "" "$(cat "$SC_LOG")"

# A′3 malformed classification value → pid-scoped path.
reset_stub_state "$SC_LOG"
B3_OUT="$(WAIT_S=10 ENSEMBLE_SUPERVISION_RESULT=garbage SYSTEMCTL_BIN="$SC2" \
    bash "$STOP_SCRIPT" "$APFIX" 19999 2>&1)"
assert_eq "A′3 malformed arm: exit 0" "0" "$?"
assert_eq "A′3 zero systemctl calls" "" "$(cat "$SC_LOG")"

# A′4 BSD arm (Darwin uname stub) + UNIT_MANAGED classification →
# degrade LOUD to the pid path; zero systemctl calls (guard short-circuits
# on uname BEFORE resolving systemctl).
STUBDIR_D="$(mktemp -d -t supstop-darwin.XXXXXX)"
cat > "$STUBDIR_D/uname" <<STUB
#!/bin/bash
case "\$1" in
    -s) echo "Darwin" ;;
    *) "$REAL_UNAME" "\$@" ;;
esac
STUB
chmod +x "$STUBDIR_D/uname"
reset_stub_state "$SC_LOG"
B4_OUT="$(WAIT_S=10 PATH="$STUBDIR_D:$PATH" ENSEMBLE_SUPERVISION_RESULT=UNIT_MANAGED:ensemble-b4.service \
    SYSTEMCTL_BIN="$SC2" bash "$STOP_SCRIPT" "$APFIX" 19999 2>&1)"
assert_eq "A′4 BSD arm: exit 0 (nothing owned)" "0" "$?"
assert_contains "A′4 BSD degrade-loud line" "unit classification UNIT_MANAGED:ensemble-b4.service present but unit path unavailable on this host" "$B4_OUT"
assert_contains "A′4 BSD names the fallback" "falling back to pid-scoped stop (degraded)" "$B4_OUT"
assert_eq "A′4 zero systemctl calls on the BSD arm" "" "$(cat "$SC_LOG")"
rm -rf "$STUBDIR_D"

# A′5 no systemctl resolvable (Linux, stub pointed at a missing binary) →
# same degrade-loud pid path.
reset_stub_state "$SC_LOG"
B5_OUT="$(WAIT_S=10 ENSEMBLE_SUPERVISION_RESULT=UNIT_MANAGED:ensemble-b5.service \
    SYSTEMCTL_BIN="$APFIX/no-such-systemctl" bash "$STOP_SCRIPT" "$APFIX" 19999 2>&1)"
assert_eq "A′5 unresolvable systemctl: exit 0" "0" "$?"
assert_contains "A′5 degrade-loud line" "falling back to pid-scoped stop (degraded)" "$B5_OUT"

rm -rf "$APFIX" "$STUBBIN2"

# ===========================================================================
section "B — lib.sh stop_via_stop_script (dispatch + prestop capture + stop-site DUAL_FIGHT)"

if [ "$HOST_HAS_SYSTEMD" = "1" ]; then
    BFIX="$(mktemp -d -t supstop-c.XXXXXX)"
    make_fixture "$BFIX"
    mkdir -p "$BFIX/releases"
    cat > "$BFIX/releases/state.json" <<'JOURNAL'
{"current":"v1","previous":null,"in_flight":null,"rollback_window_count":{"24h":0,"window_start":null},"cooldown_until":null,"quarantined":[],"history":[]}
JOURNAL
    STUBBIN3="$(mktemp -d -t supstop-stub3.XXXXXX)"
    make_systemctl_stub "$STUBBIN3"
    SC3="$STUBBIN3/systemctl"
    export SC_LOG="$BFIX/sc3.log"

    # B1 UNIT dispatch: classification stub sets UNIT globals; the stop
    # routes through the stubbed systemctl; PRESTOP captured from the
    # stub's MainPID (777).
    reset_stub_state "$SC_LOG"; rm -f "$SC_LOG".*
    SC_MAINPID=777 SC_IS_ACTIVE=active; export SC_MAINPID SC_IS_ACTIVE
    B1_OUT="$(
        (
            export INSTALL_DIR="$BFIX"
            export SYSTEMCTL_BIN="$SC3"
            unset ENSEMBLE_SUPERVISION_RESULT ENSEMBLE_RESTART_UNIT ENSEMBLE_SUPERVISION 2>/dev/null || true
            . "$UPGRADE_DIR/lib.sh" >/dev/null 2>&1
            supervision_classify() {
                SUPERVISION_MODE="unit"; SUPERVISION_STATE="UNIT_MANAGED"
                SUPERVISION_UNIT="ensemble-b1.service"
                printf 'ENSEMBLE_SUPERVISION_RESULT=%s:%s\n' "$SUPERVISION_STATE" "$SUPERVISION_UNIT"
            }
            stop_via_stop_script
            echo "svs-rc=$?"
            echo "PRESTOP=${SUPERVISION_PRESTOP_MAINPID:-}"
        ) 2>&1
    )"
    assert_contains "B1 lib.sh announces the UNIT path (b″)" "stop: UNIT path — systemctl stop ensemble-b1.service + unit-state poll" "$B1_OUT"
    assert_contains "B1 prestop MainPID captured" "PRESTOP=777" "$B1_OUT"
    assert_contains "B1 stop dispatched through the stub" "stop ensemble-b1.service" "$(cat "$SC_LOG")"

    # B2 script-arm byte-identical: classification says SCRIPT → the SAME
    # pid-scoped invocation, no env handoff, zero systemctl calls.
    reset_stub_state "$SC_LOG"; rm -f "$SC_LOG".*
    SC_IS_ACTIVE=inactive SC_MAINPID=0; export SC_IS_ACTIVE SC_MAINPID
    B2_OUT="$(
        (
            export INSTALL_DIR="$BFIX"
            export SYSTEMCTL_BIN="$SC3"
            . "$UPGRADE_DIR/lib.sh" >/dev/null 2>&1
            supervision_classify() {
                SUPERVISION_MODE="script"; SUPERVISION_STATE="SCRIPT_NOHUP"; SUPERVISION_UNIT=""
                printf 'ENSEMBLE_SUPERVISION_RESULT=%s\n' "$SUPERVISION_STATE"
            }
            stop_via_stop_script
            echo "svs-rc=$?"
        ) 2>&1
    )"
    assert_contains "B2 script arm: SINGLE-TERM announcement (the ONE site)" "stop: ownership-scoped SINGLE-TERM via" "$B2_OUT"
    assert_eq "B2 zero systemctl calls on the script arm" "" "$(cat "$SC_LOG")"

    # B3 stop-site DUAL_FIGHT refuses BEFORE any stop action: armed unit
    # (is-active=active, Restart=always) + owned pid OUTSIDE the unit
    # cgroup (own pid — real /proc read, host cgroup ≠ unit cgroup).
    reset_stub_state "$SC_LOG"; rm -f "$SC_LOG".*
    SC_IS_ACTIVE=active SC_RESTART=always SC_MAINPID=111 SC_CONTROLGROUP=/machine.slice/ensemble-b3.service
    export SC_IS_ACTIVE SC_RESTART SC_MAINPID SC_CONTROLGROUP
    B3_OUT="$(
        (
            export INSTALL_DIR="$BFIX"
            export SYSTEMCTL_BIN="$SC3"
            . "$UPGRADE_DIR/lib.sh" >/dev/null 2>&1
            supervision_classify() {
                SUPERVISION_MODE="unit"; SUPERVISION_STATE="UNIT_MANAGED"
                SUPERVISION_UNIT="ensemble-b3.service"
                printf 'ENSEMBLE_SUPERVISION_RESULT=%s:%s\n' "$SUPERVISION_STATE" "$SUPERVISION_UNIT"
            }
            _supervision_owned_pids() { printf '%s\n' "$$"; }
            ( stop_via_stop_script )
            echo "svs-rc=$?"
        ) 2>&1
    )"
    # (Check A's per-pid warn is deliberately swallowed by the fault
    # pipeline's 2>/dev/null — the journal halt event is the surface.)
    case "$B3_OUT" in
        *svs-rc=78*) _pass "B3 stop-site DUAL_FIGHT exit 78 (refuse-before-stop)" ;;
        *) _fail "B3 stop-site DUAL_FIGHT exit 78" "svs-rc=78" "$(printf '%s' "$B3_OUT" | grep -o 'svs-rc=[0-9]*' || echo none)" ;;
    esac
    assert_not_contains "B3 NO stop dispatched (pre-stop refusal)" "stop ensemble-b3.service" "$(cat "$SC_LOG")"
    assert_not_contains "B3 no SINGLE-TERM announcement either (refused earlier)" "ownership-scoped SINGLE-TERM" "$B3_OUT"
    B3_J="$(cat "$BFIX/releases/state.json")"
    assert_contains "B3 halt journal event PRE-mutation" '"event":"halt"' "$B3_J"
    assert_contains "B3 halt cites DUAL_FIGHT" "supervision DUAL_FIGHT" "$B3_J"
    assert_contains "B3 halt cites the owned-pids fault arm" "owned-pids-outside-unit-cgroup" "$B3_J"

    # B4 DUAL_FIGHT via the port-holder arm: unit armed, no owned pids,
    # but the PORT holder sits outside the unit lineage (lsof stub).
    reset_stub_state "$SC_LOG"; rm -f "$SC_LOG".*
    LSOFDIR="$(mktemp -d -t supstop-lsof.XXXXXX)"
    make_lsof_stub "$LSOFDIR" 0
    export LSOF_LOG="$BFIX/lsof.log"
    SC_IS_ACTIVE=active SC_RESTART=on-failure SC_MAINPID=111 SC_CONTROLGROUP=/ensemble-b4cg
    export SC_IS_ACTIVE SC_RESTART SC_MAINPID SC_CONTROLGROUP
    B4_OUT="$(
        PATH="$LSOFDIR:$PATH" bash -c '
            export INSTALL_DIR="'"$BFIX"'"
            export SYSTEMCTL_BIN="'"$SC3"'"
            export PORT=19998
            . "'"$UPGRADE_DIR"'/lib.sh" >/dev/null 2>&1
            _supervision_owned_pids() { return 0; }
            # globals set DIRECTLY (consumed contract — classify is not
            # re-derived at the check site)
            SUPERVISION_MODE=unit; SUPERVISION_STATE=UNIT_MANAGED
            SUPERVISION_UNIT=ensemble-b4.service
            # make lsof report OUR pid as the port holder (outside the unit)
            lsof() { echo "lsof-stub $*" >> "'"$BFIX"'/lsof.log"; echo $$; return 0; }
            export -f lsof 2>/dev/null || true
            ( supervision_dualfight_check )
            echo "df-rc=$?"
        ' 2>&1
    )"
    assert_contains "B4 port-holder DUAL_FIGHT warn" "port 19998 holder" "$B4_OUT"
    case "$B4_OUT" in
        *df-rc=78*) _pass "B4 port-holder arm exit 78" ;;
        *) _fail "B4 port-holder arm exit 78" "df-rc=78" "$(printf '%s' "$B4_OUT" | grep -o 'df-rc=[0-9]*' || echo none)" ;;
    esac
    rm -rf "$LSOFDIR"

    # B5 DUAL_FIGHT negative: dormant + unarmed unit → rc 0, no fault.
    reset_stub_state "$SC_LOG"; rm -f "$SC_LOG".*
    SC_IS_ACTIVE=inactive SC_RESTART=no; export SC_IS_ACTIVE SC_RESTART
    B5_OUT="$(
        (
            export INSTALL_DIR="$BFIX"
            export SYSTEMCTL_BIN="$SC3"
            . "$UPGRADE_DIR/lib.sh" >/dev/null 2>&1
            SUPERVISION_MODE=unit; SUPERVISION_UNIT="ensemble-b5.service"
            supervision_dualfight_check
            echo "df-rc=$?"
        ) 2>&1
    )"
    assert_contains "B5 dormant+unarmed unit: no fault (rc 0)" "df-rc=0" "$B5_OUT"

    # B6 DUAL_FIGHT negative: unit active but ControlGroup unobservable
    # → diagnostic WARN + skip (never fault on a missing observation).
    reset_stub_state "$SC_LOG"; rm -f "$SC_LOG".*
    SC_IS_ACTIVE=active SC_CONTROLGROUP=""; export SC_IS_ACTIVE SC_CONTROLGROUP
    B6_OUT="$(
        (
            export INSTALL_DIR="$BFIX"
            export SYSTEMCTL_BIN="$SC3"
            . "$UPGRADE_DIR/lib.sh" >/dev/null 2>&1
            SUPERVISION_MODE=unit; SUPERVISION_UNIT="ensemble-b6.service"
            supervision_dualfight_check
            echo "df-rc=$?"
        ) 2>&1
    )"
    assert_contains "B6 unobservable ControlGroup → diagnostic skip WARN" "ControlGroup unobservable — skipping DUAL_FIGHT pid checks" "$B6_OUT"
    assert_contains "B6 diagnostic-only skip: rc 0" "df-rc=0" "$B6_OUT"

    rm -rf "$BFIX" "$STUBBIN3"
else
    _skip "B lib.sh stop-site pins — FENCE: no /run/systemd/system on this host"
fi

# ===========================================================================
section "C — restart_via_launcher hand-back matrix"

if [ "$HOST_HAS_SYSTEMD" = "1" ]; then
    CFIX="$(mktemp -d -t suphb.XXXXXX)"
    make_fixture "$CFIX"
    mkdir -p "$CFIX/releases"
    cat > "$CFIX/releases/state.json" <<'JOURNAL'
{"current":"v1","previous":null,"in_flight":null,"rollback_window_count":{"24h":0,"window_start":null},"cooldown_until":null,"quarantined":[],"history":[]}
JOURNAL
    STUBBIN4="$(mktemp -d -t suphb-stub.XXXXXX)"
    make_systemctl_stub "$STUBBIN4"
    SC4="$STUBBIN4/systemctl"
    LSOF_OK="$(mktemp -d -t suphb-lsofok.XXXXXX)";  make_lsof_stub "$LSOF_OK" 0
    LSOF_NO="$(mktemp -d -t suphb-lsofno.XXXXXX)";  make_lsof_stub "$LSOF_NO" 1

    # run_rvl <path-extra> <globals-setup> — sources lib.sh, applies the
    # supervision globals (consumed contract), runs restart_via_launcher.
    run_rvl() {
        local path_extra="$1" setup="$2"
        PATH="$path_extra:$PATH" bash -c '
            export INSTALL_DIR="'"$CFIX"'"
            export SYSTEMCTL_BIN="'"$SC4"'"
            export SC_LOG="'"$CFIX"'/sc4.log"
            export PORT=19997
            export LIVEZ_BUDGET_S=2
            unset ENSEMBLE_SUPERVISION ENSEMBLE_RESTART_UNIT 2>/dev/null || true
            . "'"$UPGRADE_DIR"'/lib.sh" >/dev/null 2>&1
            '"$setup"'
            restart_via_launcher
            echo "rvl-rc=$?"
        ' 2>&1
    }
    rvl_rc() { printf '%s' "$1" | grep -o 'rvl-rc=[0-9]*' | head -1; }

    # C1 unit hand-back happy: reset-failed → is-active inactive → start
    # rc0 → MainPID 9999 (≠ prestop 777) → port serving → rc 0.
    reset_stub_state "$CFIX/sc4.log"
    SC_IS_ACTIVE=inactive SC_MAINPID=9999 SC_START_RC=0
    export SC_IS_ACTIVE SC_MAINPID SC_START_RC
    C1_OUT="$(run_rvl "$LSOF_OK" '
        SUPERVISION_MODE=unit; SUPERVISION_STATE=UNIT_MANAGED
        SUPERVISION_UNIT=ensemble-c1.service; SUPERVISION_PRESTOP_MAINPID=777
    ')"
    assert_eq "C1 unit happy: rc 0" "rvl-rc=0" "$(rvl_rc "$C1_OUT")"
    assert_contains "C1 announces the unit return" "hand-back: UNIT_MANAGED — the flipped release returns to unit ensemble-c1.service" "$C1_OUT"
    assert_contains "C1 hand-back COMPLETE with new MainPID + prestop" "unit hand-back COMPLETE: ensemble-c1.service serves the flipped release (new MainPID 9999, pre-stop was 777" "$C1_OUT"
    assert_contains "C1 outcome surfaced" "outcome=conforming" "$C1_OUT"
    assert_not_contains "C1 7b trap: no banned literal on the success path" "systemctl start" "$C1_OUT"
    assert_not_contains "C1 NO nohup on the unit path" "launcher started (nohup)" "$C1_OUT"
    assert_contains "C1 reset-failed issued (stale failed state cleared)" "reset-failed ensemble-c1.service" "$(cat "$CFIX/sc4.log")"

    # C2 start rc≠0 → halt arm: rc 1, NO nohup fallback, first stderr line.
    reset_stub_state "$CFIX/sc4.log"
    SC_START_RC=1 SC_START_ERR="Policy denies unit management"
    export SC_START_RC SC_START_ERR
    C2_OUT="$(run_rvl "$LSOF_OK" '
        SUPERVISION_MODE=unit; SUPERVISION_STATE=UNIT_MANAGED
        SUPERVISION_UNIT=ensemble-c2.service; SUPERVISION_PRESTOP_MAINPID=777
    ')"
    assert_eq "C2 start failure: rc 1 (halt + B4 is the caller's)" "rvl-rc=1" "$(rvl_rc "$C2_OUT")"
    assert_contains "C2 NO nohup fallback (Amendment #1)" "NO nohup fallback (Amendment #1" "$C2_OUT"
    assert_contains "C2 surfaces first stderr line" "Policy denies unit management" "$C2_OUT"
    assert_not_contains "C2 never nohup'd" "launcher started (nohup)" "$C2_OUT"
    assert_not_contains "C2 no COMPLETE line on failure" "hand-back COMPLETE" "$C2_OUT"

    # C3 stale MainPID (new == prestop): fail FAST, no retry loop.
    reset_stub_state "$CFIX/sc4.log"
    unset SC_START_RC SC_START_ERR; export SC_START_RC=0 2>/dev/null || true
    SC_START_RC=0; unset SC_START_ERR; export SC_START_RC
    SC_MAINPID=777; export SC_MAINPID
    C3_OUT="$(run_rvl "$LSOF_OK" '
        SUPERVISION_MODE=unit; SUPERVISION_STATE=UNIT_MANAGED
        SUPERVISION_UNIT=ensemble-c3.service; SUPERVISION_PRESTOP_MAINPID=777
    ')"
    assert_eq "C3 stale MainPID: rc 1" "rvl-rc=1" "$(rvl_rc "$C3_OUT")"
    assert_contains "C3 fail-fast rationale (start no-op'd / stop false-succeeded)" "EQUALS the pre-stop pid — the old daemon never left" "$C3_OUT"
    assert_contains "C3 NO nohup fallback" "NO nohup fallback (Amendment #1" "$C3_OUT"
    MPN="$(cat "$CFIX/sc4.log.mp.n" 2>/dev/null || echo 0)"
    assert_eq "C3 fail-fast: exactly ONE MainPID poll (no retry loop)" "1" "$MPN"

    # C4 never-appears timeout: MainPID stays 0 → LIVEZ_BUDGET_S-bounded
    # timeout → rc 1.
    reset_stub_state "$CFIX/sc4.log"
    SC_MAINPID=0; export SC_MAINPID
    C4_OUT="$(run_rvl "$LSOF_OK" '
        SUPERVISION_MODE=unit; SUPERVISION_STATE=UNIT_MANAGED
        SUPERVISION_UNIT=ensemble-c4.service; SUPERVISION_PRESTOP_MAINPID=777
    ')"
    assert_eq "C4 never-appears: rc 1 (timeout)" "rvl-rc=1" "$(rvl_rc "$C4_OUT")"
    assert_contains "C4 timeout warn names unit + budget" "hand-back verify: unit ensemble-c4.service not confirmed" "$C4_OUT"
    assert_contains "C4 timeout cites the budget" "within 2s" "$C4_OUT"
    assert_contains "C4 NO nohup fallback" "NO nohup fallback (Amendment #1" "$C4_OUT"

    # C5 scope→unit self-heal: SCOPE_SURVIVOR + unit configured → SAME
    # hand-back + supervision_handback journal event (scope→unit,
    # outcome=degraded) → rc 0.
    reset_stub_state "$CFIX/sc4.log"
    SC_MAINPID=5555; export SC_MAINPID
    C5_OUT="$(run_rvl "$LSOF_OK" '
        SUPERVISION_MODE=script; SUPERVISION_STATE=SCOPE_SURVIVOR; SUPERVISION_UNIT=""
        export ENSEMBLE_RESTART_UNIT=ensemble-c5.service
        SUPERVISION_PRESTOP_MAINPID=777
    ')"
    assert_eq "C5 scope→unit self-heal: rc 0" "rvl-rc=0" "$(rvl_rc "$C5_OUT")"
    assert_contains "C5 announces the self-heal" "hand-back: SCOPE_SURVIVOR + unit ensemble-c5.service configured — SAME unit hand-back" "$C5_OUT"
    assert_contains "C5 hand-back COMPLETE" "unit hand-back COMPLETE: ensemble-c5.service" "$C5_OUT"
    C5_J="$(cat "$CFIX/releases/state.json")"
    assert_contains "C5 supervision_handback journal event" '"event":"supervision_handback"' "$C5_J"
    assert_contains "C5 event detail: scope→unit lineage heal" "scope→unit: scope-survivor lineage handed back to unit ensemble-c5.service" "$C5_J"
    assert_contains "C5 event carries declared×verified + outcome=degraded" "declared=script verified=SCOPE_SURVIVOR outcome=degraded" "$C5_J"
    assert_not_contains "C5 7b trap on the success path" "systemctl start" "$C5_OUT"

    # C6 scope-no-unit: nohup path byte-identical + WARN, ZERO systemctl.
    reset_stub_state "$CFIX/sc4.log"
    C6_OUT="$(run_rvl "$LSOF_OK" '
        SUPERVISION_MODE=script; SUPERVISION_STATE=SCOPE_SURVIVOR; SUPERVISION_UNIT=""
    ')"
    assert_eq "C6 scope-no-unit: rc 0" "rvl-rc=0" "$(rvl_rc "$C6_OUT")"
    assert_contains "C6 WARN names the survivor continuation" "SCOPE_SURVIVOR with NO unit configured" "$C6_OUT"
    assert_contains "C6 byte-identical nohup path" "launcher started (nohup)" "$C6_OUT"
    assert_eq "C6 ZERO systemctl calls" "" "$(cat "$CFIX/sc4.log")"

    # C7 a′ already-active + serving: verified success, NO start issued.
    reset_stub_state "$CFIX/sc4.log"
    SC_IS_ACTIVE=active; export SC_IS_ACTIVE
    C7_OUT="$(run_rvl "$LSOF_OK" '
        SUPERVISION_MODE=script; SUPERVISION_STATE=SCRIPT_NOHUP; SUPERVISION_UNIT=""
        export ENSEMBLE_RESTART_UNIT=ensemble-c7.service
    ')"
    assert_eq "C7 a′ already-active+serving: rc 0" "rvl-rc=0" "$(rvl_rc "$C7_OUT")"
    assert_contains "C7 verified-success line (port serving)" "already active and serving :19997" "$C7_OUT"
    assert_contains "C7 no-op rc is NOT success" "a no-op" "$C7_OUT"
    assert_not_contains "C7 7b trap: success path clean" "systemctl start" "$C7_OUT"
    assert_not_contains "C7 no nohup needed" "launcher started (nohup)" "$C7_OUT"
    case "$CFIX/sc4.log" in
        *" start "*) _fail "C7 NO start issued on the already-active arm" "no start" "$(cat "$CFIX/sc4.log")" ;;
        *) _pass "C7 NO start issued on the already-active arm" ;;
    esac

    # C8 a′ already-active + NOT serving: skip the no-op start, nohup
    # fallback (no false success).
    reset_stub_state "$CFIX/sc4.log"
    C8_OUT="$(run_rvl "$LSOF_NO" '
        SUPERVISION_MODE=script; SUPERVISION_STATE=SCRIPT_NOHUP; SUPERVISION_UNIT=""
        export ENSEMBLE_RESTART_UNIT=ensemble-c8.service
    ')"
    assert_eq "C8 a′ active+not-serving: rc 0 (fallback taken)" "rvl-rc=0" "$(rvl_rc "$C8_OUT")"
    assert_contains "C8 no-false-success warn" "already active but :19997 NOT serving" "$C8_OUT"
    assert_contains "C8 falls back to nohup" "launcher started (nohup)" "$C8_OUT"
    case "$CFIX/sc4.log" in
        *" start "*) _fail "C8 no no-op start on the not-serving arm" "no start" "$(cat "$CFIX/sc4.log")" ;;
        *) _pass "C8 no no-op start on the not-serving arm" ;;
    esac

    # C9 a′ start error: real error propagation (first stderr line) +
    # nohup fallback (comp7 opt-in KEEPS the fallback — 7c contract).
    reset_stub_state "$CFIX/sc4.log"
    SC_IS_ACTIVE=inactive SC_START_RC=1 SC_START_ERR="Startup timed out"
    export SC_IS_ACTIVE SC_START_RC SC_START_ERR
    C9_OUT="$(run_rvl "$LSOF_OK" '
        SUPERVISION_MODE=script; SUPERVISION_STATE=SCRIPT_NOHUP; SUPERVISION_UNIT=""
        export ENSEMBLE_RESTART_UNIT=ensemble-c9.service
    ')"
    assert_eq "C9 a′ start error: rc 0 (comp7 fallback taken)" "rvl-rc=0" "$(rvl_rc "$C9_OUT")"
    assert_contains "C9 frozen 7c wording (failure path)" "falling back to nohup launcher (comp7 opt-in path)" "$C9_OUT"
    assert_contains "C9 first stderr line propagated" "Startup timed out" "$C9_OUT"
    assert_contains "C9 nohup launched" "launcher started (nohup)" "$C9_OUT"

    # C10 unit classification on a non-Linux host → degrade LOUD, nohup.
    DARWIN_BIN="$(mktemp -d -t suphb-darwin.XXXXXX)"
    cat > "$DARWIN_BIN/uname" <<STUB
#!/bin/bash
case "\$1" in
    -s) echo "Darwin" ;;
    *) "$REAL_UNAME" "\$@" ;;
esac
STUB
    chmod +x "$DARWIN_BIN/uname"
    reset_stub_state "$CFIX/sc4.log"
    C10_OUT="$(run_rvl "$LSOF_OK:$DARWIN_BIN" '
        SUPERVISION_MODE=unit; SUPERVISION_STATE=UNIT_MANAGED
        SUPERVISION_UNIT=ensemble-c10.service; SUPERVISION_PRESTOP_MAINPID=777
    ')"
    assert_eq "C10 BSD arm: rc 0 (legacy nohup shape)" "rvl-rc=0" "$(rvl_rc "$C10_OUT")"
    assert_contains "C10 degrade LOUD" "degrading LOUD to the nohup path" "$C10_OUT"
    assert_contains "C10 byte-identical nohup line" "launcher started (nohup)" "$C10_OUT"
    assert_eq "C10 zero systemctl calls (host guard first)" "" "$(cat "$CFIX/sc4.log")"
    rm -rf "$DARWIN_BIN"

    # C11 consumed-never-rederived contract: a FAULT-INJECTING classify
    # stub is never invoked by restart_via_launcher (state at hand-back
    # time would collapse to SCRIPT_NOHUP — P3's core fix).
    C11_OUT="$(
        (
            export INSTALL_DIR="$CFIX"
            export SYSTEMCTL_BIN="$SC4"
            export SC_LOG="$CFIX/sc4.log"
            : > "$CFIX/classify.calls"
            . "$UPGRADE_DIR/lib.sh" >/dev/null 2>&1
            supervision_classify() { echo CALLED >> "$CFIX/classify.calls"; return 0; }
            SUPERVISION_MODE=unit; SUPERVISION_STATE=UNIT_MANAGED
            SUPERVISION_UNIT=ensemble-c11.service; SUPERVISION_PRESTOP_MAINPID=777
            SC_IS_ACTIVE=inactive SC_MAINPID=31337
            restart_via_launcher >/dev/null 2>&1
            echo "classify-calls=$(cat "$CFIX/classify.calls" 2>/dev/null | wc -l | tr -d ' ')"
        ) 2>&1
    )"
    assert_contains "C11 classification CONSUMED, never re-derived at hand-back" "classify-calls=0" "$C11_OUT"

    rm -rf "$CFIX" "$STUBBIN4" "$LSOF_OK" "$LSOF_NO"
else
    _skip "C hand-back matrix — FENCE: no /run/systemd/system on this host"
fi

# ===========================================================================
section "D — item 15: restart.sh:202 dedup (single-announcement behavior)"

# D1 behavioral: stop_via_stop_script announces the script-arm stop EXACTLY
# ONCE (the outer restart.sh announcement was removed by P3 — the dedup).
DFIX="$(mktemp -d -t supdedup.XXXXXX)"
make_fixture "$DFIX"
D1_OUT="$(
    (
        export INSTALL_DIR="$DFIX"
        . "$UPGRADE_DIR/lib.sh" >/dev/null 2>&1
        supervision_classify() {
            SUPERVISION_MODE="script"; SUPERVISION_STATE="SCRIPT_NOHUP"; SUPERVISION_UNIT=""
            printf 'ENSEMBLE_SUPERVISION_RESULT=%s\n' "$SUPERVISION_STATE"
        }
        stop_via_stop_script
    ) 2>&1
)"
D1_N="$(printf '%s' "$D1_OUT" | grep -c "stop: ownership-scoped SINGLE-TERM via" || true)"
assert_eq "D1 script-arm announcement appears exactly ONCE" "1" "$D1_N"
rm -rf "$DFIX"

# D2 structural: restart.sh carries NO duplicate outer announcement (the
# P3 dedup); lib.sh owns the single site. Zero occurrences in every
# action script.
R_N="$(grep -c 'stop: ownership-scoped SINGLE-TERM' "$UPGRADE_DIR/restart.sh" || true)"
assert_eq "D2 restart.sh outer announcement count = 0 (dedup landed)" "0" "$R_N"
P_N="$(grep -c 'stop: ownership-scoped SINGLE-TERM' "$UPGRADE_DIR/promote.sh" || true)"
assert_eq "D2 promote.sh has no outer announcement" "0" "$P_N"
RB_N="$(grep -c 'stop: ownership-scoped SINGLE-TERM' "$UPGRADE_DIR/rollback.sh" || true)"
assert_eq "D2 rollback.sh has no outer announcement" "0" "$RB_N"
L_N="$(grep -c 'stop: ownership-scoped SINGLE-TERM via' "$UPGRADE_DIR/lib.sh" || true)"
assert_eq "D2 lib.sh owns exactly ONE announcement site" "1" "$L_N"

# ===========================================================================
section "summary"
printf 'PASS=%s FAIL=%s SKIP=%s\n' "$PASS" "$FAIL" "$SKIP"
if [ "$SKIP" -gt 0 ]; then
    printf 'SKIPPED (named fences):%s\n' "$SKIPPED"
fi
if [ "$FAIL" -gt 0 ]; then
    printf 'FAILED TESTS:%s\n' "$FAILED_TESTS"
    exit 1
fi
printf '\n=== ALL TESTS PASSED ===\n'
