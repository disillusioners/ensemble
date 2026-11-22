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

# ── hbk-err inode-churn containment (2026-11-22) ──────────────────────────
# scripts/upgrade/lib.sh mints its hand-back side-channels into
# ${TMPDIR:-/tmp}. This suite sources lib.sh 49 times; give the run a
# scratch TMPDIR so the channels (and every other fixture that honours
# TMPDIR) stay OUT of the shared /tmp root.
HBK_SCRATCH_DIR="$(mktemp -d "${TMPDIR:-/tmp}/hbk-scratch.XXXXXX")"
export TMPDIR="$HBK_SCRATCH_DIR"
# W1 (review 2026-11-22): this suite does not source upgrade_test_lib.sh, so
# it owns the removal of its own scratch dir.
trap 'rm -rf "$HBK_SCRATCH_DIR"' EXIT

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
        prop=""; props=""; unit=""
        while [ $# -gt 0 ]; do
            case "$1" in
                -p) prop="$2"; props="${props} $2"; shift 2 ;;
                --value) shift ;;
                *) unit="$1"; shift ;;
            esac
        done
        # D6 (self-service-promote Wave 2): the hand-back verify's bounded
        # 3-field state probe `show -p ActiveState -p SubState -p MainPID`
        # (assignment form). SC_ACTIVESTATE/SC_SUBSTATE injectable; the
        # state probe rides the MainPID counter (sc.log.mp.n).
        case "$props" in
            *" ActiveState "*)
                if [ -f "${SC_LOG}.killed" ]; then
                    echo "ActiveState=inactive"; echo "SubState=dead"; echo "MainPID=0"
                    exit 0
                fi
                n="$(cat "${SC_LOG}.mp.n" 2>/dev/null || echo 0)"
                n=$((n + 1)); printf '%s' "$n" > "${SC_LOG}.mp.n"
                if [ -n "${SC_MAINPID_N:-}" ] && [ "$n" -gt "${SC_MAINPID_N}" ] \
                   && [ -n "${SC_MAINPID2:-}" ]; then
                    echo "MainPID=${SC_MAINPID2}"
                else
                    echo "MainPID=${SC_MAINPID:-0}"
                fi
                if [ -n "${SC_ACTIVESTATE_N:-}" ] && [ "$n" -gt "${SC_ACTIVESTATE_N}" ] \
                   && [ -n "${SC_ACTIVESTATE2:-}" ]; then
                    echo "ActiveState=${SC_ACTIVESTATE2}"
                else
                    echo "ActiveState=${SC_ACTIVESTATE:-active}"
                fi
                echo "SubState=${SC_SUBSTATE:-running}"
                exit 0 ;;
        esac
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
# make_lsof_stub <dir> <rc> [delay-s] — the optional delay models the
# incident's shape: the daemon BINDS THE PORT only when it finishes
# booting (the base verifier polls lsof under the borrowed LIVEZ_BUDGET_S
# ⇒ a slow bind is what tripped the abort on 2026-11-01).
make_lsof_stub() {
    local dir="$1" rc="$2"
    mkdir -p "$dir"
    cat > "$dir/lsof" <<STUB
#!/bin/bash
echo "lsof \$*" >> "\${LSOF_LOG:-/dev/null}"
if [ -n "\${LSOF_DELAY_S:-}" ] && [ -n "\${LSOF_START_EPOCH:-}" ]; then
    now="\$(date +%s)"
    if [ "\$(( now - LSOF_START_EPOCH ))" -lt "\${LSOF_DELAY_S}" ]; then
        exit 1
    fi
fi
exit $rc
STUB
    chmod +x "$dir/lsof"
}

# ── curl stub (the Phase-1 serving conjunct — promote-handback-halt) ────────
# lib.sh's hand-back verifier calls bare curl through
# _probe_once_classified ("curl -sS -o <tmp> -w '%{http_code}' --max-time 5
# <url>") so a PATH stub controls it exactly like the real binary. Modes via
# CURL_MODE (default ok); CURL_VERSION sets the body version; CURL_DELAY_S +
# CURL_START_EPOCH serve ok-mode only after the delay (the slow-boot seam).
make_curl_stub() {
    local dir="$1"
    mkdir -p "$dir"
    cat > "$dir/curl" <<'STUB'
#!/bin/bash
out=""; url=""
while [ $# -gt 0 ]; do
    case "$1" in
        -o) out="$2"; shift 2 ;;
        -w) shift 2 ;;
        --max-time) shift 2 ;;
        -sS|-s|-S) shift ;;
        *) url="$1"; shift ;;
    esac
done
mode="${CURL_MODE:-ok}"
if [ -n "${CURL_DELAY_S:-}" ] && [ -n "${CURL_START_EPOCH:-}" ]; then
    now="$(date +%s)"
    if [ $(( now - CURL_START_EPOCH )) -lt "${CURL_DELAY_S}" ]; then
        mode="unreachable"
    fi
fi
case "$mode" in
    ok)
        [ -n "$out" ] && printf '{"status":"alive","version":"%s"}\n' "${CURL_VERSION:-0.28.5}" > "$out"
        printf '200'
        exit 0 ;;
    wrongver)
        [ -n "$out" ] && printf '{"status":"alive","version":"0.0.0"}\n' > "$out"
        printf '200'
        exit 0 ;;
    noversion)
        [ -n "$out" ] && printf '{"status":"alive"}\n' > "$out"
        printf '200'
        exit 0 ;;
    non2xx)
        [ -n "$out" ] && : > "$out"
        printf '404'
        exit 0 ;;
    busy503)
        # Phase 3 (P3 consolidated pin, AM-P1-2 behavioural): HTTP 503 with
        # curl rc 0 — the house classifier's TRANSIENT class (the boot-storm
        # window). Distinct from `unreachable` (rc 7, connection refused).
        [ -n "$out" ] && : > "$out"
        printf '503'
        exit 0 ;;
    unreachable)
        printf '000'
        exit 7 ;;
    *)
        printf '000'
        exit 7 ;;
esac
STUB
    chmod +x "$dir/curl"
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
            export PORT=19999   # report hint for the stop child (set -u)
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

    # B1h nohup-survivor heal (supervisor war r3, 2026-09-30): the REAL
    # classifier (NOT stubbed) + a heal fixture — session-*.scope leaf +
    # INSTALL_DIR/.env pin + the unit FILE in SUPERVISION_UNIT_DIR — must
    # heal inside stop_via_stop_script and route the stop through the
    # UNIT path (the r3 downstream-consistency requirement: healed
    # UNIT_MANAGED behaves exactly like a native one at the stop seam;
    # dualfight still runs first — dormant+unarmed unit → no fault).
    reset_stub_state "$SC_LOG"; rm -f "$SC_LOG".*
    SC_IS_ACTIVE=inactive SC_MAINPID=0 SC_RESTART=no; export SC_IS_ACTIVE SC_MAINPID SC_RESTART
    B1H_UNITS="$(mktemp -d -t supstop-heal.XXXXXX)"
    touch "$B1H_UNITS/ensemble-heal-stop.service"
    printf 'ENSEMBLE_RESTART_UNIT=ensemble-heal-stop.service\n' > "$BFIX/.env"
    B1H_OUT="$(
        (
            export INSTALL_DIR="$BFIX"
            export SYSTEMCTL_BIN="$SC3"
            export SUPERVISION_UNIT_DIR="$B1H_UNITS"
            export PORT=19996
            unset ENSEMBLE_SUPERVISION ENSEMBLE_RESTART_UNIT 2>/dev/null || true
            . "$UPGRADE_DIR/lib.sh" >/dev/null 2>&1
            # NO supervision_classify stub — the REAL classifier runs,
            # both at the classify seam and inside stop_via_stop_script.
            _supervision_owned_pids() { printf '%s\n' 424242; }
            _supervision_pid_cgroup_leaf() { printf '%s\n' 'session-7.scope'; }
            stop_via_stop_script
            echo "svs-rc=$?"
        ) 2>&1
    )"
    assert_contains "B1h heal log fires at the stop site" "adopting UNIT_MANAGED (healing from nohup-survivor state" "$B1H_OUT"
    assert_contains "B1h healed classification routes the UNIT path" "stop: UNIT path — systemctl stop ensemble-heal-stop.service + unit-state poll" "$B1H_OUT"
    assert_contains "B1h stop dispatched through the stub" "stop ensemble-heal-stop.service" "$(cat "$SC_LOG")"
    case "$B1H_OUT" in
        *svs-rc=0*) _pass "B1h healed unit-path stop succeeds (rc 0)" ;;
        *) _fail "B1h healed unit-path stop succeeds (rc 0)" "svs-rc=0" "$(printf '%s' "$B1H_OUT" | grep -o 'svs-rc=[0-9]*' || echo none)" ;;
    esac
    rm -f "$BFIX/.env" && rm -rf "$B1H_UNITS"

    # B1h1 heal × DUAL_FIGHT composition (finding 2, r3 hardening): the
    # healed classifier + an ARMED+ACTIVE unit + the owned pid genuinely
    # outside the unit's cgroup (a REAL pid — its /proc cgroup can never
    # contain the stubbed ControlGroup) → supervision_dualfight_check
    # must HALT the pipeline (exit 78) BEFORE any stop action. Pinned by
    # EXECUTION: the heal never bypasses the two-masters guard (the r3
    # war shape — nohup survivor + crash-looping unit — halts for a
    # human, never flips).
    reset_stub_state "$SC_LOG"; rm -f "$SC_LOG".*
    SC_IS_ACTIVE=active SC_RESTART=on-failure SC_MAINPID=555 SC_CONTROLGROUP=/ensemble-r3h1-cg
    export SC_IS_ACTIVE SC_RESTART SC_MAINPID SC_CONTROLGROUP
    B1H1_UNITS="$(mktemp -d -t supstop-heal1.XXXXXX)"
    touch "$B1H1_UNITS/ensemble-heal-guard.service"
    printf 'ENSEMBLE_RESTART_UNIT=ensemble-heal-guard.service\n' > "$BFIX/.env"
    B1H1_OUT="$(
        (
            export INSTALL_DIR="$BFIX"
            export SYSTEMCTL_BIN="$SC3"
            export SUPERVISION_UNIT_DIR="$B1H1_UNITS"
            export PORT=19995
            unset ENSEMBLE_SUPERVISION ENSEMBLE_RESTART_UNIT 2>/dev/null || true
            . "$UPGRADE_DIR/lib.sh" >/dev/null 2>&1
            _supervision_owned_pids() { printf '%s\n' $$; }
            _supervision_pid_cgroup_leaf() { printf '%s\n' 'session-7.scope'; }
            stop_via_stop_script
            echo "unreachable-svs-rc=$?"
        ) 2>&1
    )"; B1H1_RC=$?
    assert_contains "B1h1 heal fires before the guard halts" "adopting UNIT_MANAGED (healing from nohup-survivor state" "$B1H1_OUT"
    assert_contains "B1h1 halt journaled" '"event":"halt"' "$(cat "$BFIX/releases/state.json")"
    assert_contains "B1h1 halt cites DUAL_FIGHT" "supervision DUAL_FIGHT" "$(cat "$BFIX/releases/state.json")"
    assert_contains "B1h1 DUAL_FIGHT cites the outside-cgroup fault" "owned-pids-outside-unit-cgroup" "$(cat "$BFIX/releases/state.json")"
    case "$B1H1_RC" in
        78) _pass "B1h1 heal×dualfight halts the pipeline (rc 78)" ;;
        *) _fail "B1h1 heal×dualfight halts the pipeline (rc 78)" "78" "$B1H1_RC" ;;
    esac
    assert_not_contains "B1h1 NO stop dispatched (halt pre-mutation)" "stop ensemble-heal-guard.service" "$(cat "$SC_LOG")"
    case "$B1H1_OUT" in
        *"unreachable-svs-rc"*) _fail "B1h1 the halt must PREVENT the stop (line past dualfight printed)" "absent 'unreachable-svs-rc'" "$B1H1_OUT" ;;
        *) _pass "B1h1 execution never passed the guard" ;;
    esac
    rm -f "$BFIX/.env" && rm -rf "$B1H1_UNITS"

    # B1h2 heal × port-held composition (finding 2, r3 hardening): healed
    # classifier + DORMANT/UNARMED unit (dualfight passes) + a stubbed
    # lsof always reporting a foreign port holder → the unit-path stop
    # must FAIL LOUD: poll exhausts, systemctl kill escalation fires,
    # exit 1 — never a silent nohup fallback, never a false success over
    # a foreign port holder.
    reset_stub_state "$SC_LOG"; rm -f "$SC_LOG".*
    SC_IS_ACTIVE=inactive SC_RESTART=no SC_MAINPID=0; export SC_IS_ACTIVE SC_RESTART SC_MAINPID
    B1H2_UNITS="$(mktemp -d -t supstop-heal2.XXXXXX)"
    touch "$B1H2_UNITS/ensemble-heal-held.service"
    printf 'ENSEMBLE_RESTART_UNIT=ensemble-heal-held.service\n' > "$BFIX/.env"
    B1H2_LSOF="$(mktemp -d -t supstop-lsof2.XXXXXX)"
    printf '#!/bin/bash\necho 654321\n' > "$B1H2_LSOF/lsof"
    chmod +x "$B1H2_LSOF/lsof"
    B1H2_OUT="$(
        (
            export INSTALL_DIR="$BFIX"
            export SYSTEMCTL_BIN="$SC3"
            export SUPERVISION_UNIT_DIR="$B1H2_UNITS"
            export PORT=19994
            export WAIT_S=2 UNIT_KILL_GRACE_S=2
            unset ENSEMBLE_SUPERVISION ENSEMBLE_RESTART_UNIT 2>/dev/null || true
            export PATH="$B1H2_LSOF:$PATH"
            . "$UPGRADE_DIR/lib.sh" >/dev/null 2>&1
            _supervision_owned_pids() { printf '%s\n' $$; }
            _supervision_pid_cgroup_leaf() { printf '%s\n' 'session-7.scope'; }
            stop_via_stop_script
            echo "svs-rc=$?"
        ) 2>&1
    )"
    assert_contains "B1h2 heal routes the UNIT path" "stop: UNIT path — systemctl stop ensemble-heal-held.service" "$B1H2_OUT"
    assert_contains "B1h2 failing loud: STILL not confirmed stopped" "STILL not confirmed stopped" "$B1H2_OUT"
    assert_contains "B1h2 cites the failing-loud policy" "failing loud" "$B1H2_OUT"
    case "$B1H2_OUT" in
        *svs-rc=1*) _pass "B1h2 port-held unit-path stop exits 1 (loud, no fallback)" ;;
        *) _fail "B1h2 port-held unit-path stop exits 1 (loud, no fallback)" "svs-rc=1" "$(printf '%s' "$B1H2_OUT" | grep -o 'svs-rc=[0-9]*' || echo none)" ;;
    esac
    assert_contains "B1h2 stop dispatched via stub" "stop ensemble-heal-held.service" "$(cat "$SC_LOG")"
    assert_contains "B1h2 kill escalation dispatched via stub" "kill ensemble-heal-held.service" "$(cat "$SC_LOG")"
    rm -f "$BFIX/.env" && rm -rf "$B1H2_UNITS" "$B1H2_LSOF"

    # B2 script-arm byte-identical: classification says SCRIPT → the SAME
    # pid-scoped invocation, no env handoff, zero systemctl calls.
    reset_stub_state "$SC_LOG"; rm -f "$SC_LOG".*
    SC_IS_ACTIVE=inactive SC_MAINPID=0; export SC_IS_ACTIVE SC_MAINPID
    B2_OUT="$(
        (
            export INSTALL_DIR="$BFIX"
            export SYSTEMCTL_BIN="$SC3"
            export PORT=19999
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

    # ── B7 — B4 census-scope + the three §1.3 signatures ────────────────────
    # (self-service-promote Wave 2, 2026-11-19: AC-D1-1, AC-D1-5, AC-D1-6,
    # P-4 — `port-holder-outside-unit-lineage` had ZERO coverage; the
    # census-scope defect was unreachable by the suite).
    #
    # B7-static (AC-D1-1 / P-4): Check B contains NO bare `lsof -ti:$PORT` —
    # only the LISTEN-scoped shared reader; the THREE boolean stop-path
    # sites are the only bare users in the whole file.
    B7_DFBODY="$(awk '/^supervision_dualfight_check\(\)/,/^}$/' "$UPGRADE_DIR/lib.sh")"
    assert_eq "B7s/AC-D1-1: the DUAL_FIGHT body contains NO bare lsof -ti: EXECUTION site" "0" \
        "$(printf '%s' "$B7_DFBODY" | grep 'lsof -ti:' | grep -cvE '^[[:space:]]*#')"
    assert_contains "B7s/AC-D1-1: the census rides the canonical shared reader" "_supervision_port_holders" "$B7_DFBODY"
    assert_contains "B7s: the census scoping is LISTEN (the §2.2 rule, on the reader call)" "-sTCP:LISTEN" \
        "$(awk '/^_supervision_port_holders\(\)/,/^}$/' "$UPGRADE_DIR/lib.sh")"
    # file-wide: exactly THREE bare execution sites remain (the boolean
    # stop-path trio) — comment mentions and the LISTEN-scoped reader line
    # are excluded from the count.
    assert_eq "B7s/AC-D1-1: exactly THREE bare lsof -ti: sites file-wide (the boolean stop-path trio)" "3" \
        "$(grep 'lsof -ti:' "$UPGRADE_DIR/lib.sh" | grep -v -- '-sTCP:LISTEN' | grep -cvE '^[[:space:]]*#')"

    # B7a — §1.3 signature (a), the fd-leak holder: unit active+armed, the
    # cg IS the test process's OWN real cgroup, and the LISTEN census
    # reports a pid inside it (a leaked-but-cgroup-contained child) ⇒
    # IMMUNE — no fault (the live 04:38 proof: 12 fd-leak holders, DUAL_FIGHT
    # still `conforming`).
    B7A_CG="$(tail -n 1 /proc/self/cgroup 2>/dev/null)"; B7A_CG="${B7A_CG##*:}"
    reset_stub_state "$SC_LOG"; rm -f "$SC_LOG".*
    SC_IS_ACTIVE=active SC_RESTART=on-failure SC_MAINPID=111 SC_CONTROLGROUP="$B7A_CG"
    export SC_IS_ACTIVE SC_RESTART SC_MAINPID SC_CONTROLGROUP
    B7A_OUT="$(
        (
            export INSTALL_DIR="$BFIX"
            export SYSTEMCTL_BIN="$SC3"
            export PORT=19993
            . "$UPGRADE_DIR/lib.sh" >/dev/null 2>&1
            _supervision_owned_pids() { printf '%s\n' $$; }
            SUPERVISION_MODE=unit; SUPERVISION_STATE=UNIT_MANAGED
            SUPERVISION_UNIT=ensemble-b7a.service
            lsof() { echo $$; return 0; }
            export -f lsof 2>/dev/null || true
            ( supervision_dualfight_check )
            echo "df-rc=$?"
        ) 2>&1
    )"
    assert_contains "B7a fd-leak holder (cgroup-contained): no fault" "df-rc=0" "$B7A_OUT"

    # B7b — §1.3 signature (b), the transient ESTABLISHED client: a client
    # holding only an ESTABLISHED connection is INVISIBLE to the LISTEN
    # census (the 05:42:58Z false-refusal class). The stub reports the
    # foreign pid ONLY to a BROAD query; the LISTEN census must (i) ask
    # with -sTCP:LISTEN and (ii) therefore see nothing ⇒ no fault. A
    # broad-census regression turns this leg red (the holder WOULD fault).
    reset_stub_state "$SC_LOG"; rm -f "$SC_LOG".*
    SC_IS_ACTIVE=active SC_RESTART=on-failure SC_MAINPID=111 SC_CONTROLGROUP=/ensemble-b7b-cg
    export SC_IS_ACTIVE SC_RESTART SC_MAINPID SC_CONTROLGROUP
    B7B_OUT="$(
        PATH="$BFIX:$PATH" bash -c '
            export INSTALL_DIR="'"$BFIX"'"
            export SYSTEMCTL_BIN="'"$SC3"'"
            export PORT=19992
            . "'"$UPGRADE_DIR"'/lib.sh" >/dev/null 2>&1
            _supervision_owned_pids() { return 0; }
            SUPERVISION_MODE=unit; SUPERVISION_STATE=UNIT_MANAGED
            SUPERVISION_UNIT=ensemble-b7b.service
            # discriminating census: LISTEN-scoped → SILENT (rc 0, no
            # listener); broad → the foreign ESTABLISHED holder.
            lsof() {
                echo "lsof-stub $*" >> "'"$BFIX"'/lsof-b7b.log"
                case " $* " in
                    *"-sTCP:LISTEN"*) return 0 ;;
                esac
                echo $$; return 0
            }
            export -f lsof 2>/dev/null || true
            ( supervision_dualfight_check )
            echo "df-rc=$?"
        ' 2>&1
    )"
    assert_contains "B7b transient ESTABLISHED client: no fault (LISTEN-invisible)" "df-rc=0" "$B7B_OUT"
    assert_contains "B7b the census asked with the LISTEN scope" "-sTCP:LISTEN" "$(cat "$BFIX/lsof-b7b.log" 2>/dev/null || true)"
    rm -f "$BFIX/lsof-b7b.log"

    # B7c — §1.3 signature (c), the true foreign LISTEN holder: OUTSIDE the
    # unit lineage ⇒ fault `port-holder-outside-unit-lineage` + exit 78 +
    # the halt journal row (AC-D1-5 path 1 — DUAL_FIGHT; P-4's token, which
    # had ZERO coverage before this wave).
    reset_stub_state "$SC_LOG"; rm -f "$SC_LOG".*
    SC_IS_ACTIVE=active SC_RESTART=on-failure SC_MAINPID=111 SC_CONTROLGROUP=/ensemble-b7c-cg
    export SC_IS_ACTIVE SC_RESTART SC_MAINPID SC_CONTROLGROUP
    B7C_OUT="$(
        (
            export INSTALL_DIR="$BFIX"
            export SYSTEMCTL_BIN="$SC3"
            export PORT=19991
            . "$UPGRADE_DIR/lib.sh" >/dev/null 2>&1
            _supervision_owned_pids() { return 0; }
            SUPERVISION_MODE=unit; SUPERVISION_STATE=UNIT_MANAGED
            SUPERVISION_UNIT=ensemble-b7c.service
            lsof() { echo $$; return 0; }
            export -f lsof 2>/dev/null || true
            ( supervision_dualfight_check )
            echo "df-rc=$?"
        ) 2>&1
    )"
    case "$B7C_OUT" in
        *df-rc=78*) _pass "B7c the foreign LISTEN holder halts the guard (exit 78)" ;;
        *) _fail "B7c the foreign LISTEN holder halts the guard (exit 78)" "df-rc=78" "$(printf '%s' "$B7C_OUT" | grep -o 'df-rc=[0-9]*' || echo none)" ;;
    esac
    assert_contains "B7c halt journaled" '"event":"halt"' "$(cat "$BFIX/releases/state.json")"
    assert_contains "B7c/AC-D1-5+P-4: the port-holder-outside-unit-lineage token (was ZERO-coverage)" "port-holder-outside-unit-lineage" "$(cat "$BFIX/releases/state.json")"
    assert_contains "B7c halt cites DUAL_FIGHT" "supervision DUAL_FIGHT" "$(cat "$BFIX/releases/state.json")"

    # B7d — AC-D1-5 path 2 (the serving-attribution gate): a foreign-unit
    # LISTEN holder is refused by the GATE too — Layer 1 passes (the holder
    # IS owned) but Layer 2 (unit lineage) refuses with the OUTSIDE-lineage
    # detail (the lib.sh refusal literal, byte-unchanged).
    reset_stub_state "$SC_LOG"; rm -f "$SC_LOG".*
    SC_MAINPID=111 SC_CONTROLGROUP=/ensemble-b7d-cg
    export SC_MAINPID SC_CONTROLGROUP
    B7D_OUT="$(
        (
            export INSTALL_DIR="$BFIX"
            export SYSTEMCTL_BIN="$SC3"
            export PORT=19990
            . "$UPGRADE_DIR/lib.sh" >/dev/null 2>&1
            _supervision_owned_pids() { printf '%s\n' $$; }
            SUPERVISION_MODE=unit; SUPERVISION_UNIT=ensemble-b7d.service
            lsof() { echo $$; return 0; }
            export -f lsof 2>/dev/null || true
            if _gate_serving_attribution "b7d-leg"; then
                echo "gate-rc=0"
            else
                echo "gate-rc=1"
            fi
            echo "detail=[${GATE_ATTRIBUTION_DETAIL:-}]"
        ) 2>&1
    )"
    assert_contains "B7d/AC-D1-5: the gate REFUSES the foreign-unit LISTEN holder" "gate-rc=1" "$B7D_OUT"
    assert_contains "B7d the Layer-2 refusal names the OUTSIDE-unit lineage" "OUTSIDE unit 'ensemble-b7d.service' lineage" "$B7D_OUT"
    assert_contains "B7d the refusal names the holder" "a second supervisor is serving" "$B7D_OUT"

    # B7e — §1.3(b) vanished-holder semantics: a census holder that EXITED
    # between census and cgroup read (/proc/<pid> GONE) is NOT evidence —
    # skipped with the vanished warn, no fault (BASE: unreadable cgroup ⇒
    # hard fault — the 05:42:58Z ROLLBACK_SH_EXIT=78 shape).
    reset_stub_state "$SC_LOG"; rm -f "$SC_LOG".*
    SC_IS_ACTIVE=active SC_RESTART=on-failure SC_MAINPID=111 SC_CONTROLGROUP=/ensemble-b7e-cg
    export SC_IS_ACTIVE SC_RESTART SC_MAINPID SC_CONTROLGROUP
    B7E_GONE="$(mktemp -t supstop-gone.XXXXXX)"
    B7E_OUT="$(
        (
            export INSTALL_DIR="$BFIX"
            export SYSTEMCTL_BIN="$SC3"
            export PORT=19989
            . "$UPGRADE_DIR/lib.sh" >/dev/null 2>&1
            _supervision_owned_pids() { return 0; }
            SUPERVISION_MODE=unit; SUPERVISION_STATE=UNIT_MANAGED
            SUPERVISION_UNIT=ensemble-b7e.service
            # spawn a REAL short-lived holder, capture its pid, let it die,
            # then report THAT pid from the census — /proc/<pid> is gone at
            # cgroup-read time (the §1.3(b) race).
            sleep 0.2 &
            gone_pid=$!
            wait "$gone_pid" 2>/dev/null
            lsof() { echo "$gone_pid"; return 0; }
            export -f lsof 2>/dev/null || true
            export gone_pid
            ( supervision_dualfight_check )
            echo "df-rc=$?"
        ) 2>&1
    )"
    assert_contains "B7e vanished holder: no fault (not evidence)" "df-rc=0" "$B7E_OUT"
    assert_contains "B7e the vanished-holder skip is NAMED (diagnosable)" "vanished between census and cgroup read" "$B7E_OUT"
    rm -f "$B7E_GONE"

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
    # Phase 1 (promote-handback-halt): the hand-back verifier's serving
    # conjunct (/livez 2xx via _probe_once_classified) is satisfied by a
    # PATH-injected curl stub in ok mode for every pre-existing leg —
    # PROMOTE_EXPECTED_VERSION is unset in these contexts, so the version
    # comparison is skipped (the 2xx alone confirms serving), exactly the
    # restart.sh/rollback.sh degraded contract.
    CURL_DIR="$(mktemp -d -t suphb-curl.XXXXXX)"
    make_curl_stub "$CURL_DIR"

    # run_rvl <path-extra> <globals-setup> — sources lib.sh, applies the
    # supervision globals (consumed contract), runs restart_via_launcher.
    run_rvl() {
        local path_extra="$1" setup="$2"
        PATH="$path_extra:$CURL_DIR:$PATH" bash -c '
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
    # [Phase 1 / promote-handback-halt: this leg IS leg L5 of
    # phase1-plan.md §9 — the equal-to-prestop fast-fail is byte-unchanged
    # under the Phase-1 verifier rewrite (knob quartet + serving conjunct)
    # and re-pinned here under the new budget; exactly ONE MainPID poll.]
    reset_stub_state "$CFIX/sc4.log"
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

    # C4 never-appears timeout: MainPID stays 0 → budget-bounded timeout
    # → rc 1. [Phase 1 / promote-handback-halt: the verifier no longer
    # borrows LIVEZ_BUDGET_S — the leg now pins the timeout against the
    # NEW knob (HANDBACK_VERIFY_BUDGET_S=2 keeps the wall time bounded);
    # the warn still says "within 2s" but as the EFFECTIVE hand-back
    # budget, not the livez gate's borrow.]
    reset_stub_state "$CFIX/sc4.log"
    SC_MAINPID=0; export SC_MAINPID
    export HANDBACK_VERIFY_BUDGET_S=2
    C4_OUT="$(run_rvl "$LSOF_OK" '
        SUPERVISION_MODE=unit; SUPERVISION_STATE=UNIT_MANAGED
        SUPERVISION_UNIT=ensemble-c4.service; SUPERVISION_PRESTOP_MAINPID=777
    ')"
    unset HANDBACK_VERIFY_BUDGET_S
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

    # C10 EXPLICIT-unit classification on a unit-INCAPABLE host — M3
    # (review cycle 1, leader-ruled FAIL-CLOSED): a ladder-top EXPLICIT
    # unit declaration (SUPERVISION_EXPLICIT_UNIT=1, set by
    # supervision_classify's 'unit' arm) must REFUSE exit 78 at the
    # restart_via_launcher elif — NEVER loud-degrade + nohup at rc 0
    # (the r-f82e surprise class). The Darwin uname PATH-stub simulates
    # the incapable host; the nested subshell keeps _refuse's exit 78
    # from killing the rc-capture. C10b pins the complementary contract:
    # the NON-explicit (auto-resolved) UNIT_MANAGED shape KEEPS the
    # legacy loud-degrade — M3 is confined to the explicit arm.
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
    C10_OUT="$(PATH="$LSOF_OK:$DARWIN_BIN:$PATH" bash -c '
        export INSTALL_DIR="'"$CFIX"'"
        export SYSTEMCTL_BIN="'"$SC4"'"
        export SC_LOG="'"$CFIX"'/sc4.log"
        export PORT=19997
        unset ENSEMBLE_SUPERVISION ENSEMBLE_RESTART_UNIT 2>/dev/null || true
        . "'"$UPGRADE_DIR"'/lib.sh" >/dev/null 2>&1
        SUPERVISION_MODE=unit; SUPERVISION_STATE=UNIT_MANAGED
        SUPERVISION_UNIT=ensemble-c10.service; SUPERVISION_PRESTOP_MAINPID=777
        SUPERVISION_EXPLICIT_UNIT=1
        ( restart_via_launcher )
        echo "rvl-rc=$?"
    ' 2>&1)"
    assert_eq "C10 explicit unit + incapable host: rc 78 (fail-closed refusal)" "rvl-rc=78" "$(rvl_rc "$C10_OUT")"
    assert_contains "C10 refusal names never silent-degrade" "never silent-degrade" "$C10_OUT"
    assert_contains "C10 refusal cites the r-f82e surprise class" "r-f82e surprise class" "$C10_OUT"
    assert_contains "C10 refusal names the unit + host guards" "unit hand-back is unavailable here (non-Linux / no systemd / no resolvable systemctl)" "$C10_OUT"
    assert_not_contains "C10 NEVER nohup'd the explicit-unit lineage" "launcher started (nohup)" "$C10_OUT"
    assert_eq "C10 zero systemctl calls (host guard first)" "" "$(cat "$CFIX/sc4.log")"
    C10_J="$(cat "$CFIX/releases/state.json")"
    assert_contains "C10 refusal journaled" '"event":"refusal"' "$C10_J"
    assert_contains "C10 refusal carries the supervision-unit-incapable-host token" "reason=supervision-unit-incapable-host" "$C10_J"

    # C10b D2a CHANGE: NON-explicit (auto-resolved) UNIT_MANAGED on Darwin.
    # Darwin is now FAIL-CLOSED — restart_via_launcher refuses (exit 78) rather
    # than degrade to the nohup lineage (a promote-started launcher against a
    # launchd job IS the 2026-11-11 dual-supervisor incident). The legacy
    # loud-degrade survives NON-Darwin only.
    reset_stub_state "$CFIX/sc4.log"
    C10B_OUT="$(PATH="$LSOF_OK:$DARWIN_BIN:$PATH" bash -c '
        export INSTALL_DIR="'"$CFIX"'"
        export SYSTEMCTL_BIN="'"$SC4"'"
        export SC_LOG="'"$CFIX"'/sc4.log"
        export PORT=19997
        unset ENSEMBLE_SUPERVISION ENSEMBLE_RESTART_UNIT 2>/dev/null || true
        . "'"$UPGRADE_DIR"'/lib.sh" >/dev/null 2>&1
        SUPERVISION_MODE=unit; SUPERVISION_STATE=UNIT_MANAGED
        SUPERVISION_UNIT=ensemble-c10b.service; SUPERVISION_PRESTOP_MAINPID=777
        ( restart_via_launcher )
        echo "rvl-rc=$?"
    ' 2>&1)"
    assert_eq "C10b auto-resolved unit + Darwin: rc 78 (fail-closed, no nohup degrade)" "rvl-rc=78" "$(rvl_rc "$C10B_OUT")"
    assert_contains "C10b Darwin refusal names the nohup fallback hazard" "reached the nohup fallback with NO verified launchd job" "$C10B_OUT"
    assert_not_contains "C10b NEVER nohup'd on Darwin" "launcher started (nohup)" "$C10B_OUT"
    assert_eq "C10b zero systemctl calls (host guard first)" "" "$(cat "$CFIX/sc4.log")"
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

    # C12 hand-back pin-rung (v0.16.9 lane-convergence fix): when a
    # valid pin is present (env var OR .env), the hand-back declares
    # the PINNED unit (not the cgroup leaf), enabling the
    # ensemble-live.service → ensemble-main.service convergence.
    # C12a env-var rung: ENSEMBLE_RESTART_UNIT=ensemble-c12a.service
    # (with the unit file in SUPERVISION_UNIT_DIR) overrides the
    # cgroup-derived SUPERVISION_UNIT (ensemble-c12a-other.service).
    # C12b .env rung: same shape, but pin sourced from INSTALL_DIR/.env
    # (the load-bearing path per the brief — the executor's env is
    # stripped by EXECUTOR_ENV_ALLOWLIST, .env pin is the only
    # resolution path that fires from the promote executor).
    C12_UNITS="$(mktemp -d -t supstop-c12.XXXXXX)"
    touch "$C12_UNITS/ensemble-c12a.service"
    touch "$C12_UNITS/ensemble-c12b.service"
    touch "$C12_UNITS/ensemble-c12b2.service"

    # C12a: env-var rung — pin via ENSEMBLE_RESTART_UNIT; the cgroup
    # leaf is intentionally a DIFFERENT name (the pre-convergence
    # shape: cgroup says ensemble-live.service, pin says
    # ensemble-main.service). Hand-back must declare the pin.
    reset_stub_state "$CFIX/sc4.log"; rm -f "$CFIX/sc4.log".*
    SC_MAINPID=9999 SC_IS_ACTIVE=inactive SC_START_RC=0
    export SC_MAINPID SC_IS_ACTIVE SC_START_RC
    C12A_OUT="$(PATH="$LSOF_OK:$CURL_DIR:$PATH" SUPERVISION_UNIT_DIR="$C12_UNITS" bash -c '
            export INSTALL_DIR="'"$CFIX"'"
            export SYSTEMCTL_BIN="'"$SC4"'"
            export SC_LOG="'"$CFIX"'/sc4.log"
            export PORT=19997
            export LIVEZ_BUDGET_S=2
            export ENSEMBLE_RESTART_UNIT=ensemble-c12a.service
            unset ENSEMBLE_SUPERVISION 2>/dev/null || true
            . "'"$UPGRADE_DIR"'/lib.sh" >/dev/null 2>&1
            SUPERVISION_MODE=unit; SUPERVISION_STATE=UNIT_MANAGED
            SUPERVISION_UNIT=ensemble-c12a-other.service; SUPERVISION_PRESTOP_MAINPID=777
            restart_via_launcher
            echo "rvl-rc=$?"
        ' 2>&1)"
    assert_eq "C12a env-var pin: rc 0" "rvl-rc=0" "$(rvl_rc "$C12A_OUT")"
    assert_contains "C12a env-var pin declares the PINNED unit (not cgroup leaf)" "returns to unit ensemble-c12a.service" "$C12A_OUT"
    assert_not_contains "C12a cgroup-leaf is NEVER a unit hand-back target" "returns to unit ensemble-c12a-other.service" "$C12A_OUT"
    assert_contains "C12a hand-back COMPLETE on the pin" "unit hand-back COMPLETE: ensemble-c12a.service" "$C12A_OUT"

    # C12b: .env rung — pin sourced from INSTALL_DIR/.env (env-var
    # unset). Same convergence contract: hand-back uses the .env pin.
    reset_stub_state "$CFIX/sc4.log"; rm -f "$CFIX/sc4.log".*
    C12B_OUT="$(PATH="$LSOF_OK:$CURL_DIR:$PATH" SUPERVISION_UNIT_DIR="$C12_UNITS" bash -c '
            export INSTALL_DIR="'"$CFIX"'"
            export SYSTEMCTL_BIN="'"$SC4"'"
            export SC_LOG="'"$CFIX"'/sc4.log"
            export PORT=19997
            export LIVEZ_BUDGET_S=2
            unset ENSEMBLE_RESTART_UNIT ENSEMBLE_SUPERVISION 2>/dev/null || true
            . "'"$UPGRADE_DIR"'/lib.sh" >/dev/null 2>&1
            printf "ENSEMBLE_RESTART_UNIT=ensemble-c12b.service\n" > "'"$CFIX"'/.env"
            SUPERVISION_MODE=unit; SUPERVISION_STATE=UNIT_MANAGED
            SUPERVISION_UNIT=ensemble-c12b-other.service; SUPERVISION_PRESTOP_MAINPID=777
            restart_via_launcher
            echo "rvl-rc=$?"
        ' 2>&1)"
    assert_eq "C12b .env pin: rc 0" "rvl-rc=0" "$(rvl_rc "$C12B_OUT")"
    assert_contains "C12b .env pin declares the PINNED unit (not cgroup leaf)" "returns to unit ensemble-c12b.service" "$C12B_OUT"
    assert_not_contains "C12b cgroup-leaf is NEVER a unit hand-back target" "returns to unit ensemble-c12b-other.service" "$C12B_OUT"
    rm -f "$CFIX/.env"

    # C12b2: .env rung with env-var cleared EXTERNALLY (simulating the
    # promote executor's EXECUTOR_ENV_ALLOWLIST strip — the load-
    # bearing case from the brief). The .env pin is the only rung
    # that can fire from the executor; the env-var rung is unreachable.
    # Hand-back must still declare the pin.
    reset_stub_state "$CFIX/sc4.log"; rm -f "$CFIX/sc4.log".*
    C12B2_OUT="$(PATH="$LSOF_OK:$CURL_DIR:$PATH" SUPERVISION_UNIT_DIR="$C12_UNITS" bash -c '
            export INSTALL_DIR="'"$CFIX"'"
            export SYSTEMCTL_BIN="'"$SC4"'"
            export SC_LOG="'"$CFIX"'/sc4.log"
            export PORT=19997
            export LIVEZ_BUDGET_S=2
            unset ENSEMBLE_RESTART_UNIT ENSEMBLE_SUPERVISION 2>/dev/null || true
            . "'"$UPGRADE_DIR"'/lib.sh" >/dev/null 2>&1
            printf "ENSEMBLE_RESTART_UNIT=ensemble-c12b2.service\n" > "'"$CFIX"'/.env"
            SUPERVISION_MODE=unit; SUPERVISION_STATE=UNIT_MANAGED
            SUPERVISION_UNIT=ensemble-c12b2-other.service; SUPERVISION_PRESTOP_MAINPID=777
            restart_via_launcher
            echo "rvl-rc=$?"
        ' 2>&1)"
    assert_eq "C12b2 .env pin only (env stripped): rc 0" "rvl-rc=0" "$(rvl_rc "$C12B2_OUT")"
    assert_contains "C12b2 .env pin declares the PINNED unit when env stripped" "returns to unit ensemble-c12b2.service" "$C12B2_OUT"
    rm -f "$CFIX/.env"

    # C12b-pin hand-back validation (unit ran, ruling, verify fast)
    rm -rf "$C12_UNITS"

    # C12b (FALLBACK — note: legacy name reused for the inverse contract;
    # test id "C12b" in the brief = "pin absent OR invalid → fallback to
    # cgroup leaf; STOP path unchanged". The first C12b block above tests
    # the .env-rung path; this C12b block tests the FALLBACK contract.
    # Numbering preserved per brief for traceability.)
    C12B_UNITS="$(mktemp -d -t supstop-c12b.XXXXXX)"
    touch "$C12B_UNITS/ensemble-c12bfb.service"   # the cgroup-leaf unit, present
    # No ensemble-c12bfb-pin.service — the pin unit is INTENTIONALLY absent

    # C12b absent pin: NO ENSEMBLE_RESTART_UNIT, no .env pin → hand-back
    # declares the cgroup leaf (fallback contract).
    reset_stub_state "$CFIX/sc4.log"; rm -f "$CFIX/sc4.log".*
    C12B_ABSENT_OUT="$(PATH="$LSOF_OK:$CURL_DIR:$PATH" SUPERVISION_UNIT_DIR="$C12B_UNITS" bash -c '
            export INSTALL_DIR="'"$CFIX"'"
            export SYSTEMCTL_BIN="'"$SC4"'"
            export SC_LOG="'"$CFIX"'/sc4.log"
            export PORT=19997
            export LIVEZ_BUDGET_S=2
            unset ENSEMBLE_RESTART_UNIT ENSEMBLE_SUPERVISION 2>/dev/null || true
            . "'"$UPGRADE_DIR"'/lib.sh" >/dev/null 2>&1
            SUPERVISION_MODE=unit; SUPERVISION_STATE=UNIT_MANAGED
            SUPERVISION_UNIT=ensemble-c12bfb.service; SUPERVISION_PRESTOP_MAINPID=777
            restart_via_launcher
            echo "rvl-rc=$?"
        ' 2>&1)"
    assert_eq "C12b absent pin: rc 0" "rvl-rc=0" "$(rvl_rc "$C12B_ABSENT_OUT")"
    assert_contains "C12b absent pin: hand-back falls back to cgroup leaf" "returns to unit ensemble-c12bfb.service" "$C12B_ABSENT_OUT"

    # C12b invalid pin (non-conforming name): ENSEMBLE_RESTART_UNIT
    # set but not ensemble-*.service → cgroup leaf (with WARN).
    reset_stub_state "$CFIX/sc4.log"; rm -f "$CFIX/sc4.log".*
    C12B_INVALID_OUT="$(PATH="$LSOF_OK:$CURL_DIR:$PATH" SUPERVISION_UNIT_DIR="$C12B_UNITS" bash -c '
            export INSTALL_DIR="'"$CFIX"'"
            export SYSTEMCTL_BIN="'"$SC4"'"
            export SC_LOG="'"$CFIX"'/sc4.log"
            export PORT=19997
            export LIVEZ_BUDGET_S=2
            export ENSEMBLE_RESTART_UNIT=foo.service
            unset ENSEMBLE_SUPERVISION 2>/dev/null || true
            . "'"$UPGRADE_DIR"'/lib.sh" >/dev/null 2>&1
            SUPERVISION_MODE=unit; SUPERVISION_STATE=UNIT_MANAGED
            SUPERVISION_UNIT=ensemble-c12bfb.service; SUPERVISION_PRESTOP_MAINPID=777
            restart_via_launcher
            echo "rvl-rc=$?"
        ' 2>&1)"
    assert_eq "C12b invalid pin (non-conforming): rc 0" "rvl-rc=0" "$(rvl_rc "$C12B_INVALID_OUT")"
    assert_contains "C12b invalid pin: hand-back falls back to cgroup leaf" "returns to unit ensemble-c12bfb.service" "$C12B_INVALID_OUT"
    assert_contains "C12b invalid pin: WARN logs non-conforming pin (WARN: prefix)" "WARN: hand-back: pin 'foo.service' not ensemble-*.service" "$C12B_INVALID_OUT"

    # C12b invalid pin (unit file missing): ENSEMBLE_RESTART_UNIT
    # set + conforming name + NO unit file → cgroup leaf (with WARN).
    reset_stub_state "$CFIX/sc4.log"; rm -f "$CFIX/sc4.log".*
    C12B_NOF_OUT="$(PATH="$LSOF_OK:$CURL_DIR:$PATH" SUPERVISION_UNIT_DIR="$C12B_UNITS" bash -c '
            export INSTALL_DIR="'"$CFIX"'"
            export SYSTEMCTL_BIN="'"$SC4"'"
            export SC_LOG="'"$CFIX"'/sc4.log"
            export PORT=19997
            export LIVEZ_BUDGET_S=2
            export ENSEMBLE_RESTART_UNIT=ensemble-c12bfb-missing.service
            unset ENSEMBLE_SUPERVISION 2>/dev/null || true
            . "'"$UPGRADE_DIR"'/lib.sh" >/dev/null 2>&1
            SUPERVISION_MODE=unit; SUPERVISION_STATE=UNIT_MANAGED
            SUPERVISION_UNIT=ensemble-c12bfb.service; SUPERVISION_PRESTOP_MAINPID=777
            restart_via_launcher
            echo "rvl-rc=$?"
        ' 2>&1)"
    assert_eq "C12b missing-unit-file: rc 0" "rvl-rc=0" "$(rvl_rc "$C12B_NOF_OUT")"
    assert_contains "C12b missing-unit-file: hand-back falls back to cgroup leaf" "returns to unit ensemble-c12bfb.service" "$C12B_NOF_OUT"
    assert_contains "C12b missing-unit-file: WARN logs absent file (WARN: prefix)" "WARN: hand-back: pin 'ensemble-c12bfb-missing.service' set but unit file absent at" "$C12B_NOF_OUT"

    # C12b STOP PATH UNCHANGED (the hard constraint from the brief):
    # the fix is HAND-BACK SCOPED ONLY — stop_via_stop_script must
    # still adopt the cgroup leaf regardless of the pin. Pin the
    # invariant by invoking the real stop-ensemble.sh with the cgroup-
    # leaf unit handed over via ENSEMBLE_SUPERVISION_RESULT (P3 seam,
    # stop-ensemble.sh:511-513) AND a SYSTEMCTL_BIN stub. Assert:
    #   - stop-ensemble.sh's own stdout announces the cgroup-leaf unit
    #     (NOT the pin) as the unit-owned stop target — the B1 pattern
    #     (test stops via the real stop-ensemble.sh; the announced unit
    #     is observable in the script's own output, no stub log needed)
    #   - the SYSTEMCTL_BIN stub ALSO records the cgroup-leaf stop
    #     (defense-in-depth pin; the assertion is independent of stub
    #     log existence)
    C12B_STOP_STUB_LOG="$(mktemp -t supstop-c12b-stop.XXXXXX)"
    C12B_STOP_STUB_BIN="$(mktemp -d -t supstop-c12b-stub.XXXXXX)"
    cat > "$C12B_STOP_STUB_BIN/systemctl" <<STUB
#!/bin/bash
echo "\$*" >> "\${SC_LOG:-/dev/null}"
exit 0
STUB
    chmod +x "$C12B_STOP_STUB_BIN/systemctl"
    rm -f "$C12B_STOP_STUB_LOG"
    C12B_STOP_OUT="$(
        (
            export WAIT_S=2
            export SYSTEMCTL_BIN="$C12B_STOP_STUB_BIN/systemctl"
            export SC_LOG="$C12B_STOP_STUB_LOG"
            export ENSEMBLE_SUPERVISION_RESULT="UNIT_MANAGED:ensemble-c12bfb.service"
            export ENSEMBLE_RESTART_UNIT=ensemble-c12bfb-missing.service
            unset ENSEMBLE_SUPERVISION 2>/dev/null || true
            # STOP_FORCE=1 bypasses pipeline_settled / current-symlink
            # refusal — the test exercises unit-selection, not the
            # preflight gate (D-FA5.3); operator-emergency exit is fine.
            export STOP_FORCE=1
            # stop-ensemble.sh also needs a `current` symlink at the
            # fixture root for the unit-path leg to render past the
            # preflight; the C fixture has releases/state.json without
            # a symlink by default (D-FA5.3 contract).
            mkdir -p "$CFIX/releases"
            ln -sfn "releases/v1" "$CFIX/current"
            bash "$STOP_SCRIPT" "$CFIX" 19997
        ) 2>&1 || true
    )"
    # Primary: stop-ensemble.sh's stdout announces the cgroup-leaf unit.
    # Asserts the script leg's behavior (independent of stub log).
    if printf '%s' "$C12B_STOP_OUT" | grep -q "systemctl stop ensemble-c12bfb.service"; then
        _pass "C12b STOP path unchanged: stop-ensemble.sh announces cgroup-leaf unit"
    else
        _fail "C12b STOP path unchanged: stop-ensemble.sh announces cgroup-leaf unit" "systemctl stop ensemble-c12bfb.service" "$(printf '%s' "$C12B_STOP_OUT" | head -5)"
    fi
    if printf '%s' "$C12B_STOP_OUT" | grep -q "ensemble-c12bfb-missing"; then
        _fail "C12b STOP path: NEVER retargeted to the pin (would pre-flip-abort)" "absent pin name" "present"
    else
        _pass "C12b STOP path: NEVER retargeted to the pin (would pre-flip-abort)"
    fi
    rm -f "$C12B_STOP_STUB_LOG"
    rm -rf "$C12B_STOP_STUB_BIN"
    rm -rf "$C12B_UNITS"

    # ── P5 (self-service-promote Wave 4 — AC-D2-5): the version fast-fail
    # is ARMED on the rollback/restart paths ───────────────────────────────
    # The blind pin (design §2.3 "Blind pins found (D2)"): rollback.sh and
    # restart.sh never set PROMOTE_EXPECTED_VERSION ⇒ the hand-back's
    # version fast-fail was INACTIVE there — a WRONG-VERSION daemon was
    # accepted as SUCCESS (the P-5 pin at the old :730 read "PROMOTE_
    # EXPECTED_VERSION is unset in these contexts ... exactly the
    # restart.sh/rollback.sh degraded contract" — that degraded contract is
    # GONE as of this wave). The leg pins BOTH halves:
    #   (a) the SOURCE half — both scripts set the expected version;
    #   (b) the BEHAVIOUR half — with the expected version known, a
    #       wrong-version answer is NOT a success.
    P5_ROLLBACK_SRC="$(cat "$UPGRADE_DIR/rollback.sh")"
    P5_RESTART_SRC="$(cat "$UPGRADE_DIR/restart.sh")"
    case "$P5_ROLLBACK_SRC" in
        *'PROMOTE_EXPECTED_VERSION='*) _pass "P5 rollback.sh sets PROMOTE_EXPECTED_VERSION (AC-D2-5)" ;;
        *) _fail "P5 rollback.sh sets PROMOTE_EXPECTED_VERSION (AC-D2-5)" "an assignment" "none found" ;;
    esac
    case "$P5_RESTART_SRC" in
        *'PROMOTE_EXPECTED_VERSION='*) _pass "P5 restart.sh sets PROMOTE_EXPECTED_VERSION (AC-D2-5)" ;;
        *) _fail "P5 restart.sh sets PROMOTE_EXPECTED_VERSION (AC-D2-5)" "an assignment" "none found" ;;
    esac
    # (b) the behavioural half — the fast-fail fires when the expected
    # version is known (the wrong version is NOT a success).
    # The lsof stub MUST report a listener pid AND the owned-pid set must
    # contain it: the attribution conjunct (Fix #2) refuses an
    # unattributable answer BEFORE the version conjunct is reached (the
    # pack's C-section lsof stub prints no pid — that is the known C-section
    # debt, not this leg's subject).
    P5_LSOF="$(mktemp -d -t suphb-lsofp.XXXXXX)"
    cat > "$P5_LSOF/lsof" <<'STUB'
#!/bin/bash
echo "lsof $*" >> "${LSOF_LOG:-/dev/null}"
printf '%s\n' 424242
exit 0
STUB
    chmod +x "$P5_LSOF/lsof"
    reset_stub_state "$CFIX/sc4.log"
    SC_IS_ACTIVE=inactive SC_MAINPID=9999 SC_START_RC=0
    export SC_IS_ACTIVE SC_MAINPID SC_START_RC
    CURL_MODE=wrongver; export CURL_MODE
    P5_OUT="$(run_rvl "$P5_LSOF" '
        SUPERVISION_MODE=unit; SUPERVISION_STATE=UNIT_MANAGED
        SUPERVISION_UNIT=ensemble-p5.service; SUPERVISION_PRESTOP_MAINPID=777
        _supervision_owned_pids() { printf "%s\n" 424242; }
        export SC_CONTROLGROUP=""
        export PROMOTE_EXPECTED_VERSION=0.28.5
    ')"
    unset CURL_MODE
    assert_eq "P5 with the expected version set, a wrong-version daemon is NOT a success" "rvl-rc=1" "$(rvl_rc "$P5_OUT")"
    assert_contains "P5 the observed wrong version is named on the wire" "version=0.0.0" "$P5_OUT"
    # and the degraded (unset) contract still degrades: with NO expected
    # version the 2xx alone confirms serving (the documented posture).
    reset_stub_state "$CFIX/sc4.log"
    CURL_MODE=wrongver; export CURL_MODE
    P5B_OUT="$(run_rvl "$P5_LSOF" '
        SUPERVISION_MODE=unit; SUPERVISION_STATE=UNIT_MANAGED
        SUPERVISION_UNIT=ensemble-p5b.service; SUPERVISION_PRESTOP_MAINPID=777
        _supervision_owned_pids() { printf "%s\n" 424242; }
        export SC_CONTROLGROUP=""
    ')"
    unset CURL_MODE
    assert_eq "P5 with NO expected version the serving check degrades to 2xx (the documented posture)" "rvl-rc=0" "$(rvl_rc "$P5B_OUT")"
    rm -rf "$P5_LSOF"

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
        export PORT=19999
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
section "D′ — Phase 3 consolidated pins: P3-PIN-09 slow-boot budget + AM-P1-2 probe classification"

# P3-PIN-09 (the gap this file had — NO slow-boot timing pin): a slow
# unit-active→serving boot INSIDE the dedicated hand-back verify budget
# must NOT trip the abort predicate. The incident (r-20261101-212542-8279)
# was exactly this shape: the unit became active, the daemon took ~3 min to
# answer /livez, and the verifier gave up on a fixed budget.
#
# The legs are RED at BASE (the base verifier borrows LIVEZ_BUDGET_S=2 ⇒ it
# gives up long before the daemon serves) and GREEN at HEAD (the dedicated
# HANDBACK_VERIFY_BUDGET_S is honoured) — the discrimination the plan
# demands ("shrink the budget back to the fixed 180 s class ⇒ RED").
#
# AM-P1-2 (the Phase-1 residual routed to Phase 3): the probe CLASSIFICATION
# table must be behavioural, not just structural — /livez 404 ⇒ RED
# (immediate, fail-fast) vs /livez 503 ⇒ TRANSIENT (keep polling). The
# house classifier (`_probe_once_classified`) is reused verbatim (never a
# second taxonomy).
if [ "$HOST_HAS_SYSTEMD" = "1" ]; then
    # The lib.sh under test (the base leg points HB_BASE_LIBSH at the BASE
    # copy; the default is the tree's own).
    HB_LIBSH="${HB_LIBSH:-$UPGRADE_DIR/lib.sh}"
    D3FIX="$(mktemp -d -t supslow.XXXXXX)"
    make_fixture "$D3FIX"
    mkdir -p "$D3FIX/releases"
    cat > "$D3FIX/releases/state.json" <<'JOURNAL'
{"current":"v1","previous":null,"in_flight":null,"rollback_window_count":{"24h":0,"window_start":null},"cooldown_until":null,"quarantined":[],"history":[]}
JOURNAL
    D3STUB="$(mktemp -d -t supslow-stub.XXXXXX)"
    make_systemctl_stub "$D3STUB"
    D3SC="$D3STUB/systemctl"
    D3LSOF="$(mktemp -d -t supslow-lsof.XXXXXX)"; make_lsof_stub "$D3LSOF" 0
    # The DELAYED lsof: the daemon binds the port only when it finishes
    # booting (the incident's shape — the base verifier polls lsof under
    # the borrowed LIVEZ_BUDGET_S, so a slow bind is what tripped the
    # abort on 2026-11-01).
    D3LSOFD="$(mktemp -d -t supslow-lsofd.XXXXXX)"; make_lsof_stub "$D3LSOFD" 0 4
    D3CURL="$(mktemp -d -t supslow-curl.XXXXXX)"; make_curl_stub "$D3CURL"

    # run_slow <libsh> <verify-budget> <curl-mode> <delay-s> — one hand-back
    # attempt with the given lib.sh. The unit is active with a NEW MainPID
    # from the first poll; the PORT binds and /livez answers only after
    # <delay-s> (the slow boot).
    run_slow() {
        local libsh="$1" budget="$2" mode="$3" delay="$4"
        PATH="$D3LSOFD:$D3CURL:$PATH" bash -c '
            export INSTALL_DIR="'"$D3FIX"'"
            export SYSTEMCTL_BIN="'"$D3SC"'"
            export SC_LOG="'"$D3FIX"'/sc3.log"
            export PORT=19997
            export LIVEZ_BUDGET_S=2
            export HANDBACK_VERIFY_BUDGET_S="'"$budget"'"
            export HANDBACK_VERIFY_GRACE_S=0
            export CURL_MODE="'"$mode"'"
            export CURL_DELAY_S="'"$delay"'"
            export CURL_START_EPOCH="$(date +%s)"
            export LSOF_DELAY_S="'"$delay"'"
            export LSOF_START_EPOCH="$CURL_START_EPOCH"
            unset ENSEMBLE_SUPERVISION ENSEMBLE_RESTART_UNIT 2>/dev/null || true
            . "'"$libsh"'" >/dev/null 2>&1
            SUPERVISION_MODE=unit; SUPERVISION_STATE=UNIT_MANAGED
            SUPERVISION_UNIT=ensemble-d3.service; SUPERVISION_PRESTOP_MAINPID=777
            restart_via_launcher
            echo "rvl-rc=$?"
        ' 2>&1
    }
    slow_rc() { printf '%s' "$1" | grep -o 'rvl-rc=[0-9]*' | head -1; }

    # run_probe <libsh> <budget> <curl-mode> — the verifier-level legs (the
    # port is bound from the start: only the /livez classification differs).
    run_probe() {
        local libsh="$1" budget="$2" mode="$3"
        PATH="$D3LSOF:$D3CURL:$PATH" bash -c '
            export INSTALL_DIR="'"$D3FIX"'"
            export SYSTEMCTL_BIN="'"$D3SC"'"
            export SC_LOG="'"$D3FIX"'/sc3.log"
            export PORT=19997
            export LIVEZ_BUDGET_S=2
            export HANDBACK_VERIFY_BUDGET_S="'"$budget"'"
            export HANDBACK_VERIFY_GRACE_S=0
            export CURL_MODE="'"$mode"'"
            unset ENSEMBLE_SUPERVISION ENSEMBLE_RESTART_UNIT 2>/dev/null || true
            . "'"$libsh"'" >/dev/null 2>&1
            SUPERVISION_MODE=unit; SUPERVISION_STATE=UNIT_MANAGED
            SUPERVISION_UNIT=ensemble-d3.service; SUPERVISION_PRESTOP_MAINPID=777
            restart_via_launcher
            echo "rvl-rc=$?"
        ' 2>&1
    }

    # P3-PIN-09a — the slow boot inside the dedicated budget ⇒ SUCCESS.
    reset_stub_state "$D3FIX/sc3.log"
    SC_MAINPID=9999 SC_START_RC=0
    export SC_MAINPID SC_START_RC
    D3_T0="$(date +%s)"
    D3A_OUT="$(run_slow "$HB_LIBSH" 20 ok 4)"
    D3_T1="$(date +%s)"
    D3A_ELAPSED=$(( D3_T1 - D3_T0 ))
    assert_eq "P3-PIN-09a the slow-boot hand-back COMPLETES inside the dedicated budget" "rvl-rc=0" "$(slow_rc "$D3A_OUT")"
    assert_contains "P3-PIN-09a the hand-back announces the unit return" "hand-back COMPLETE" "$D3A_OUT"
    if [ "$D3A_ELAPSED" -ge 3 ]; then
        _pass
    else
        _fail "P3-PIN-09a the verifier REALLY WAITED for the slow boot (>= 3s)" ">= 3s" "${D3A_ELAPSED}s"
    fi

    # P3-PIN-09b — the fail direction (budget expiry): the SAME slow fixture
    # with a 2s dedicated budget ⇒ a BOUNDED failure, never a hang.
    reset_stub_state "$D3FIX/sc3.log"
    D3B_T0="$(date +%s)"
    D3B_OUT="$(run_slow "$HB_LIBSH" 2 ok 4)"
    D3B_T1="$(date +%s)"
    D3B_ELAPSED=$(( D3B_T1 - D3B_T0 ))
    assert_eq "P3-PIN-09b the budget expiry is a BOUNDED failure" "rvl-rc=1" "$(slow_rc "$D3B_OUT")"
    if [ "$D3B_ELAPSED" -le 10 ]; then
        _pass
    else
        _fail "P3-PIN-09b the failure is bounded by the budget (<= 10s)" "<= 10s" "${D3B_ELAPSED}s"
    fi

    # P3-PIN-09-base — the BASE leg: the pre-Phase-1 verifier borrows
    # LIVEZ_BUDGET_S (2 in this harness) ⇒ the SAME slow fixture fails
    # there and succeeds at HEAD. This is the red-at-base proof.
    if [ -n "${HB_BASE_LIBSH:-}" ] && [ -f "${HB_BASE_LIBSH}" ]; then
        reset_stub_state "$D3FIX/sc3.log"
        D3C_OUT="$(run_slow "$HB_BASE_LIBSH" 20 ok 4)"
        assert_eq "P3-PIN-09-base the BASE verifier gives up on the slow boot (the leg is RED at base)" "rvl-rc=1" "$(slow_rc "$D3C_OUT")"
    else
        _skip "P3-PIN-09-base the base leg (HB_BASE_LIBSH not supplied — the base lib.sh copy)"
    fi

    # ── AM-P1-2: the probe CLASSIFICATION table, behaviourally ──────────────
    # 404 ⇒ RED (immediate fail-fast); 503 ⇒ TRANSIENT (keep polling).
    probe_class() {
        local mode="$1"
        PATH="$D3CURL:$PATH" CURL_MODE="$mode" bash -c '
            . "'"$HB_LIBSH"'" >/dev/null 2>&1
            _probe_once_classified "/livez" 19997 >/dev/null 2>&1
            printf "rc=%s status=%s\n" "$?" "$_PROBE_STATUS"
        ' 2>/dev/null
    }
    PC404="$(probe_class non2xx)"
    assert_contains "AM-P1-2a /livez 404 classifies RED (fail-fast, not transient)" "rc=1" "$PC404"
    assert_contains "AM-P1-2a /livez 404 carries the red status" "status=red" "$PC404"
    PC503="$(probe_class busy503)"
    assert_contains "AM-P1-2b /livez 503 classifies TRANSIENT (keep polling)" "rc=2" "$PC503"
    assert_contains "AM-P1-2b /livez 503 carries the transient status" "status=transient" "$PC503"
    PC200="$(probe_class ok)"
    assert_contains "AM-P1-2c /livez 200 classifies GREEN" "rc=0" "$PC200"
    assert_contains "AM-P1-2c /livez 200 carries the green status" "status=green" "$PC200"

    # The VERIFIER-level consequence: 404 fails FAST (the abort predicate
    # fires immediately — no polling to the budget); 503 keeps polling until
    # the budget expires.
    reset_stub_state "$D3FIX/sc3.log"
    D3R404="$(run_probe "$HB_LIBSH" 20 non2xx)"
    assert_contains "AM-P1-2d /livez 404 makes the verifier fail FAST (the red class)" "hard fail (red" "$D3R404"
    reset_stub_state "$D3FIX/sc3.log"
    D3R503="$(run_probe "$HB_LIBSH" 4 busy503)"
    assert_not_contains "AM-P1-2d /livez 503 does NOT fail fast (the transient class polls on)" "hard fail (red" "$D3R503"

    rm -rf "$D3FIX" "$D3STUB" "$D3LSOF" "$D3LSOFD" "$D3CURL"
else
    _skip "D′ P3-PIN-09 slow-boot + AM-P1-2 classification — FENCE: no /run/systemd/system on this host"
fi

# ===========================================================================
section "L — PC-1 launchd hand-back plist resolution (2026-11-14 halt)"
# THE halt (docs/incidents/2026-11-14-macos-launchd-handback-halt.md):
# the stop phase bootouts the job BEFORE the hand-back → `launchctl print`
# answers "Could not find service" → the old single-rung resolver returned
# empty → WARN → halt-for-human with the daemon down (v0.30.5 @ 11:54,
# v0.30.9 recurrence). The fix is THREE-runged: live `launchctl print`
# (FIRST — backward compat) → the pre-stop classify stash
# (SUPERVISION_LAUNCHD_PLIST_PATH) → the label-verbatim
# ~/Library/LaunchAgents/<label>.plist fallback (incident §7b ratified
# filename == job label). Every leg drives the REAL lib.sh code through a
# hermetic launchctl stub on PATH (the LAUNCHCTL_BIN seam is
# PATH-resolved by design — the same seam test_release_journal.sh uses);
# Darwin is simulated with the uname stub (the C10 pattern) and HOME is a
# fixture dir so the fallback rung is hermetic (never the operator's real
# ~/Library/LaunchAgents). The stub's print path (registered.plist) is
# deliberately DIFFERENT from the fallback file so each leg's resolved
# path ATTRIBUTES ITS RUNG.
L_FIX="$(mktemp -d -t suphb-launchd.XXXXXX)"
# The C-section's rvl_rc lives inside the systemd fence (skipped on this
# Darwin host) — the L legs define their own rc extractor.
l_rvl_rc() { printf '%s' "$1" | grep -o 'rvl-rc=[0-9]*' | head -1; }
L_LABEL="com.ensemble.hb.fixture"
L_INSTALL="$L_FIX/install"
L_REGISTERED_PLIST="$L_FIX/registered.plist"          # rung 1 (live print)
mkdir -p "$L_INSTALL" "$L_FIX/home/Library/LaunchAgents"
printf '<?xml version="1.0" encoding="UTF-8"?>\n<plist version="1.0"><dict><key>Label</key><string>%s</string></dict></plist>\n' "$L_LABEL" > "$L_REGISTERED_PLIST"
printf '<?xml version="1.0" encoding="UTF-8"?>\n<plist version="1.0"><dict><key>Label</key><string>%s</string></dict></plist>\n' "$L_LABEL" > "$L_FIX/home/Library/LaunchAgents/${L_LABEL}.plist"
cat > "$L_FIX/launchctl" <<STUB
#!/bin/bash
set -u
STATE="${L_FIX}/launchd-registered"
LOG="${L_FIX}/launchctl.calls"
LABEL="${L_LABEL}"
PLIST="${L_REGISTERED_PLIST}"
cmd="\${1:-}"
[ \$# -gt 0 ] && shift
echo "launchctl \$cmd \$*" >> "\$LOG"
case "\$cmd" in
    list)
        [ -f "\$STATE" ] && printf '%s\t%s\t%s\n' "4321" "0" "\$LABEL"
        exit 0 ;;
    print)
        target="\${1:-}"
        if [ -f "\$STATE" ]; then
            printf 'program = /bin/bash\n'
            printf 'program arguments = {\n    /bin/bash\n    %s/launcher.sh\n}\n' "${L_INSTALL}"
            printf 'path = %s\n' "\$PLIST"
            printf 'pid = 4321\n'
            exit 0
        fi
        echo "Could not find service \"\${target}\" in GUI domain for uid \$(id -u)" >&2
        exit 113 ;;
    bootout)
        rm -f "\$STATE"
        exit 0 ;;
    bootstrap)
        exit "\${LAUNCHD_BOOTSTRAP_RC:-0}" ;;
    *) exit 0 ;;
esac
STUB
chmod +x "$L_FIX/launchctl"
cat > "$L_FIX/uname" <<STUB
#!/bin/bash
case "\${1:-}" in
    -s) echo "Darwin" ;;
    *) "$(command -v uname)" "\$@" ;;
esac
STUB
chmod +x "$L_FIX/uname"

# L1 rung 1 — registered job: the live `launchctl print` `path =` answer.
touch "$L_FIX/launchd-registered"
L1_OUT="$(PATH="$L_FIX:$PATH" HOME="$L_FIX/home" bash -c '
    export INSTALL_DIR="'"$L_INSTALL"'"
    . "'"$UPGRADE_DIR"'/lib.sh" >/dev/null 2>&1
    P="$(_supervision_launchd_plist_path "'"$L_LABEL"'")"
    printf "%s rc=%s\n" "$P" "$?"
' 2>&1)"
assert_contains "L1 rung 1 (registered): resolves the launchd-attested path" "$L_REGISTERED_PLIST" "$L1_OUT"
assert_contains "L1 rung 1: rc 0" "rc=0" "$L1_OUT"

# L2 THE PC-1 NODE — classify (registered: stash captured) → bootout (the
# M1 barrier; the stub flips to "Could not find service") → hand-back
# resolves via the STASH → bootstrap issued → rc 0. This is the incident
# sequence end-to-end, and the stash wiring proof.
rm -f "$L_FIX/launchctl.calls"
L2_OUT="$(PATH="$L_FIX:$PATH" HOME="$L_FIX/home" bash -c '
    export INSTALL_DIR="'"$L_INSTALL"'"
    unset ENSEMBLE_SUPERVISION 2>/dev/null || true
    . "'"$UPGRADE_DIR"'/lib.sh" >/dev/null 2>&1
    touch "'"$L_FIX"'/launchd-registered"
    SUPERVISION_LAUNCHD_PLIST_PATH=""
    supervision_classify || true
    echo "classify-state=$SUPERVISION_STATE unit=$SUPERVISION_UNIT stash=$SUPERVISION_LAUNCHD_PLIST_PATH"
    _supervision_launchd_bootout "$SUPERVISION_UNIT" >/dev/null 2>&1   # the stop barrier — job now GONE
    SUPERVISION_STATE=LAUNCHD_MANAGED                                   # consume the PRE-STOP classification
    SUPERVISION_PRESTOP_MAINPID=4321
    restart_via_launcher
    echo "rvl-rc=$?"
' 2>&1)"
assert_contains "L2 classify (registered): LAUNCHD_MANAGED + label resolved" "classify-state=LAUNCHD_MANAGED unit=$L_LABEL" "$L2_OUT"
assert_contains "L2 PC-1 stash: captured at classify while the job was registered" "stash=$L_REGISTERED_PLIST" "$L2_OUT"
assert_eq "L2 PC-1 hand-back post-bootout: rc 0 (the halt is dead)" "rvl-rc=0" "$(l_rvl_rc "$L2_OUT")"
assert_contains "L2 hand-back announces the launchd bootstrap" "hand-back: launchd — bootstrapped job" "$L2_OUT"
assert_contains "L2 bootstrap was issued with the STASHED path (rung 2, not fallback)" "bootstrap gui/$(id -u) $L_REGISTERED_PLIST" "$(cat "$L_FIX/launchctl.calls" 2>/dev/null)"
assert_not_contains "L2 no halt warn on the stash path" "ALL unresolvable" "$L2_OUT"
assert_not_contains "L2 Amendment #1: never a nohup fallback" "launcher started (nohup)" "$L2_OUT"

# L3 rung 3 — NO stash (belt absent), print failing, but the host file
# exists under the RATIFIED label-verbatim name → the fallback resolves.
rm -f "$L_FIX/launchctl.calls" "$L_FIX/launchd-registered"
L3_OUT="$(PATH="$L_FIX:$PATH" HOME="$L_FIX/home" bash -c '
    export INSTALL_DIR="'"$L_INSTALL"'"
    . "'"$UPGRADE_DIR"'/lib.sh" >/dev/null 2>&1
    SUPERVISION_STATE=LAUNCHD_MANAGED; SUPERVISION_MODE=launchd
    SUPERVISION_UNIT="'"$L_LABEL"'"; SUPERVISION_PRESTOP_MAINPID=4321
    SUPERVISION_LAUNCHD_PLIST_PATH=""
    restart_via_launcher
    echo "rvl-rc=$?"
' 2>&1)"
assert_eq "L3 fallback rung (no stash, print dead): rc 0" "rvl-rc=0" "$(l_rvl_rc "$L3_OUT")"
assert_contains "L3 bootstrap issued with the LABEL-VERBATIM fallback path (rung 3)" "bootstrap gui/$(id -u) $L_FIX/home/Library/LaunchAgents/$L_LABEL.plist" "$(cat "$L_FIX/launchctl.calls" 2>/dev/null)"

# L4 fail-closed — print dead, no stash, NO host file: empty resolution →
# rc 1 + the updated WARN + NO bootstrap issued (Amendment #1 intact).
rm -f "$L_FIX/launchctl.calls"
mv "$L_FIX/home/Library/LaunchAgents/${L_LABEL}.plist" "$L_FIX/home/Library/LaunchAgents/${L_LABEL}.plist.hidden"
L4_OUT="$(PATH="$L_FIX:$PATH" HOME="$L_FIX/home" bash -c '
    export INSTALL_DIR="'"$L_INSTALL"'"
    . "'"$UPGRADE_DIR"'/lib.sh" >/dev/null 2>&1
    SUPERVISION_STATE=LAUNCHD_MANAGED; SUPERVISION_MODE=launchd
    SUPERVISION_UNIT="'"$L_LABEL"'"; SUPERVISION_PRESTOP_MAINPID=4321
    SUPERVISION_LAUNCHD_PLIST_PATH=""
    restart_via_launcher
    echo "rvl-rc=$?"
' 2>&1)"
assert_eq "L4 all rungs unresolvable: rc 1 (halt fail-safe, no fabricated path)" "rvl-rc=1" "$(l_rvl_rc "$L4_OUT")"
assert_contains "L4 the WARN names the three rungs" "live probe, pre-stop stash, and the label-verbatim" "$L4_OUT"
assert_contains "L4 the WARN cites the ratified convention (incident §7b)" "filename must equal the job label" "$L4_OUT"
case "$(cat "$L_FIX/launchctl.calls" 2>/dev/null)" in
    *" bootstrap "*) _fail "L4 NO bootstrap on an unresolvable path (fail-closed)" "no bootstrap" "$(cat "$L_FIX/launchctl.calls")" ;;
    *) _pass "L4 NO bootstrap on an unresolvable path (fail-closed)" ;;
esac
assert_not_contains "L4 Amendment #1: never a nohup fallback" "launcher started (nohup)" "$L4_OUT"
mv "$L_FIX/home/Library/LaunchAgents/${L_LABEL}.plist.hidden" "$L_FIX/home/Library/LaunchAgents/${L_LABEL}.plist"

# L5 stale-stash hygiene — a re-classify must not inherit the previous
# stash when the job can no longer be verified (the reset at classify top).
L5_OUT="$(PATH="$L_FIX:$PATH" HOME="$L_FIX/home" bash -c '
    export INSTALL_DIR="'"$L_INSTALL"'"
    unset ENSEMBLE_SUPERVISION 2>/dev/null || true
    . "'"$UPGRADE_DIR"'/lib.sh" >/dev/null 2>&1
    SUPERVISION_LAUNCHD_PLIST_PATH="/stale/from/previous/run.plist"
    rm -f "'"$L_FIX"'/launchd-registered"
    supervision_classify >/dev/null 2>&1
    echo "stash-after-refused-classify=$SUPERVISION_LAUNCHD_PLIST_PATH"
' 2>&1)"
assert_contains "L5 re-classify resets the stash (no stale inheritance)" "stash-after-refused-classify=" "$L5_OUT"
assert_not_contains "L5 stale path is GONE after a refused classify" "/stale/from/previous/run.plist" "$L5_OUT"

rm -rf "$L_FIX"

# ===========================================================================
section "M — Fix #2 serving-process attribution (2026-11-14 §3.1/§5.1)"
# THE v0.30.3 commit defect: gate+soak validated /livez answers from a
# NOHUP daemon while the launchd-adopted tree crash-looped on EADDRINUSE
# for 9h — and the ensemble-vm shape (:9797 held by kubectl port-forward +
# uptime monitor + code-server child while the daemon serves :9798). Every
# gate now refuses unless the PORT-HOLDER is this install's supervised
# daemon: layer 1 = holder ∈ _supervision_owned_pids; layer 2 = holder ∈
# the resolved supervisor's lineage (launchd job pid / systemd MainPID +
# cgroup). All legs drive the REAL lib.sh gates through a hermetic PATH
# stub set (lsof/curl/launchctl/uname) + a function-stubbed owned-pid set
# (the pack's fixture daemon IS the owned daemon by construction — pid
# discovery itself is pinned elsewhere).
M_FIX="$(mktemp -d -t suphb-attr.XXXXXX)"
M_INSTALL="$M_FIX/install"
mkdir -p "$M_INSTALL"
cat > "$M_FIX/lsof" <<STUB
#!/bin/bash
case "\${1:-}" in
    -ti:*) printf '%s\n' \${ATTR_LSOF_PIDS:-} ;;
    *) exit 1 ;;
esac
STUB
cat > "$M_FIX/curl" <<STUB
#!/bin/bash
# hermetic curl emulating the lib.sh probe shapes: -o <file> (body target),
# -w <fmt> (%{http_code} → stdout), plain fetch → body on stdout.
__out=""; __fmt=""; __url=""
while [ \$# -gt 0 ]; do
    case "\$1" in
        -o) __out="\$2"; shift 2 ;;
        -w) __fmt="\$2"; shift 2 ;;
        -sS|-s|-S|--silent|--show-error) shift ;;
        --max-time) shift 2 ;;
        *) __url="\$__url \$1"; shift ;;
    esac
done
case "\$__url" in
    *"/livez"*)
        __code=200
        __body='{"status":"ok","version":"'"\${ATTR_VERSION:-vTEST}"'"}'
        ;;
    *"/readyz"*)
        __code=200
        __body='{"status":"ok","detail":{"inflight_turns":0}}'
        ;;
    *) exit 7 ;;
esac
if [ -n "\$__out" ] && [ "\$__out" != "/dev/null" ]; then
    printf '%s' "\$__body" > "\$__out"
fi
case "\$__fmt" in
    *http_code*) printf '%s' "\$__code" ;;
    *) [ -n "\$__out" ] || printf '%s' "\$__body" ;;
esac
exit 0
STUB
cat > "$M_FIX/launchctl" <<STUB
#!/bin/bash
case "\${1:-}" in
    print) printf 'pid = %s\n' "\${ATTR_JOB_PID-4321}"; exit 0 ;;
    *) exit 0 ;;
esac
STUB
cat > "$M_FIX/uname" <<STUB
#!/bin/bash
case "\${1:-}" in
    -s) echo "Darwin" ;;
    *) "$(command -v uname)" "\$@" ;;
esac
STUB
chmod +x "$M_FIX"/lsof "$M_FIX"/curl "$M_FIX"/launchctl "$M_FIX"/uname

m_attr() {  # m_attr "VAR=value"... — export the legs' knobs, probe once
    local kv
    for kv in "$@"; do export "$kv"; done
    PATH="$M_FIX:$PATH" INSTALL_DIR="$M_INSTALL" PORT=18777 \
    LIVEZ_BUDGET_S=2 READYZ_BUDGET_S=2 bash -c '
        . "'"$UPGRADE_DIR"'/lib.sh" >/dev/null 2>&1
        _supervision_owned_pids() { printf "%s\n" ${ATTR_OWNED_PIDS-424242}; }
        _gate_serving_attribution "M"
        echo "attr-rc=$?"
        echo "attr-detail=$GATE_ATTRIBUTION_DETAIL"
    ' 2>&1
    local rc=$?   # the child's rc is not needed here; output carries it
    true
}

# M1 — foreign holder (owned set says 424242, the port is held by 999999):
# the gate REFUSES and the detail names the holder + the owned set.
# [AC-D1-4 — T-POLISH-A marker, 2026-11-20: the refusal detail still
#  contains "NOT in the supervision-owned pid set" byte-for-byte]
M1_OUT="$(m_attr "ATTR_LSOF_PIDS=999999")"
assert_contains "M1 foreign holder: attribution refuses" "attr-rc=1" "$M1_OUT"
assert_contains "M1 detail names the foreign holder" "999999 NOT in the supervision-owned pid set" "$M1_OUT"

# M2 — owned holder (holder == the owned pid): attribution PASSES.
M2_OUT="$(m_attr "ATTR_LSOF_PIDS=424242")"
assert_contains "M2 owned holder: attribution passes" "attr-rc=0" "$M2_OUT"

# M3 — EMPTY owned set against a live holder: same mismatch (owned:<none>).
M3_OUT="$(m_attr "ATTR_LSOF_PIDS=999999" "ATTR_OWNED_PIDS=")"
assert_contains "M3 empty owned set + live holder: refuses" "attr-rc=1" "$M3_OUT"
assert_contains "M3 detail carries owned:<none>" "owned:<none>" "$M3_OUT"

# M4 — green answer with NO listener behind it: unattributable → refuse.
M4_OUT="$(m_attr "ATTR_LSOF_PIDS=")"
assert_contains "M4 green answer + no listener: refuses" "attr-rc=1" "$M4_OUT"
assert_contains "M4 detail says unattributable" "finds NO listener" "$M4_OUT"

# M5 — launchd lineage, member holder: pass (layer 2 engages only under the
# resolved launchd classification — CONSUMED, never re-derived).
M5_OUT="$(m_attr "ATTR_LSOF_PIDS=4321" "ATTR_OWNED_PIDS=4321" "SUPERVISION_MODE=launchd" "SUPERVISION_UNIT=com.ensemble.prod")"
assert_contains "M5 launchd holder in lineage: passes" "attr-rc=0" "$M5_OUT"

# M6 — launchd lineage, holder owned-shaped but OUTSIDE the job lineage:
# the v0.30.3 nohup-serves-while-launchd-crash-loops shape → REFUSE.
M6_OUT="$(m_attr "ATTR_LSOF_PIDS=9999" "ATTR_OWNED_PIDS=9999" "SUPERVISION_MODE=launchd" "SUPERVISION_UNIT=com.ensemble.prod")"
assert_contains "M6 holder outside launchd lineage: refuses" "attr-rc=1" "$M6_OUT"
assert_contains "M6 detail names the second supervisor" "OUTSIDE launchd job 'com.ensemble.prod' lineage" "$M6_OUT"

# M7 — launchd job registered but PIDLESS (crash-looping) while the port is
# served by an owned-shaped pid: the v0.30.3 dual-supervisor shape → REFUSE.
M7_OUT="$(m_attr "ATTR_LSOF_PIDS=424242" "ATTR_JOB_PID=4294967295" "SUPERVISION_MODE=launchd" "SUPERVISION_UNIT=com.ensemble.prod")"
assert_contains "M7 pidless launchd job + served port: refuses" "attr-rc=1" "$M7_OUT"
assert_contains "M7 detail names the crash-loop shape" "NO live pid while" "$M7_OUT"

# M8 — script mode (no supervisor resolved): layer 2 absent, layer 1 governs.
M8_OUT="$(m_attr "ATTR_LSOF_PIDS=424242" "SUPERVISION_MODE=script")"
assert_contains "M8 script mode: ownership layer alone governs (pass)" "attr-rc=0" "$M8_OUT"

# M9 — GATE WIRING: gate_livez and gate_version REFUSE when the answer is
# unattributable (empty census) or FOREIGN — the conjunct rides INSIDE the
# gates, so every caller (promote/rollback/restart/status re-gates)
# inherits it without a per-site edit.
M9_OUT="$(PATH="$M_FIX:$PATH" INSTALL_DIR="$M_INSTALL" PORT=18777 LIVEZ_BUDGET_S=2 bash -c '
    . "'"$UPGRADE_DIR"'/lib.sh" >/dev/null 2>&1
    _supervision_owned_pids() { printf "%s\n" 424242; }
    gate_livez >/dev/null 2>&1
    echo "livez-rc=$?"
    gate_version "vTEST" >/dev/null 2>&1
    echo "version-rc=$?"
' 2>&1)"
# the lsof stub reports NO pids here (ATTR_LSOF_PIDS unset → empty) → the
# no-listener refusal fires for BOTH gates.
assert_contains "M9 gate_livez refuses the unattributable answer" "livez-rc=1" "$M9_OUT"
assert_contains "M9 gate_version refuses the unattributable answer" "version-rc=1" "$M9_OUT"
M9B_OUT="$(PATH="$M_FIX:$PATH" INSTALL_DIR="$M_INSTALL" PORT=18777 LIVEZ_BUDGET_S=2 ATTR_LSOF_PIDS="999999" bash -c '
    . "'"$UPGRADE_DIR"'/lib.sh" >/dev/null 2>&1
    _supervision_owned_pids() { printf "%s\n" 424242; }
    gate_livez >/dev/null 2>&1
    echo "livez-rc=$?"
    gate_version "vTEST" >/dev/null 2>&1
    echo "version-rc=$?"
' 2>&1)"
assert_contains "M9b gate_livez refuses the FOREIGN holder" "livez-rc=1" "$M9B_OUT"
assert_contains "M9b gate_version refuses the FOREIGN holder" "version-rc=1" "$M9B_OUT"

# M10 — the WIRING inverse: with an OWNED holder both gates go green.
M10_OUT="$(PATH="$M_FIX:$PATH" INSTALL_DIR="$M_INSTALL" PORT=18777 LIVEZ_BUDGET_S=2 ATTR_LSOF_PIDS="424242" bash -c '
    . "'"$UPGRADE_DIR"'/lib.sh" >/dev/null 2>&1
    _supervision_owned_pids() { printf "%s\n" 424242; }
    gate_livez >/dev/null 2>&1
    echo "livez-rc=$?"
    gate_version "vTEST" >/dev/null 2>&1
    echo "version-rc=$?"
' 2>&1)"
assert_contains "M10 owned holder: gate_livez greens" "livez-rc=0" "$M10_OUT"
assert_contains "M10 owned holder: gate_version greens" "version-rc=0" "$M10_OUT"

# M11 — unprobeable (a PATH whose stub dir has curl but NO lsof): the gate
# PASSES with the ONE-TIME loud skip warn (no-missing-tool-fault
# precedent; never silent, never spammy across the probe loops).
M11_DIR="$M_FIX/nolsof"
mkdir -p "$M11_DIR"
cp "$M_FIX/curl" "$M11_DIR/curl"
M11_OUT="$(PATH="$M11_DIR:/bin" INSTALL_DIR="$M_INSTALL" PORT=18777 LIVEZ_BUDGET_S=2 bash -c '
    . "'"$UPGRADE_DIR"'/lib.sh" >/dev/null 2>&1
    command -v lsof >/dev/null 2>&1 && { echo "fence-broken: lsof reachable"; exit 0; }
    _supervision_owned_pids() { printf "%s\n" 424242; }
    gate_livez >/dev/null 2>&1
    echo "livez-rc=$?"
' 2>&1)"
assert_not_contains "M11 fence: the stub PATH truly lacks lsof" "fence-broken" "$M11_OUT"
assert_contains "M11 no lsof: gate skips attribution (rc 0, degraded-loud)" "livez-rc=0" "$M11_OUT"
M11B_OUT="$(PATH="$M11_DIR:/bin" INSTALL_DIR="$M_INSTALL" PORT=18777 LIVEZ_BUDGET_S=2 READYZ_BUDGET_S=2 bash -c '
    . "'"$UPGRADE_DIR"'/lib.sh" >/dev/null 2>&1
    _supervision_owned_pids() { printf "%s\n" 424242; }
    gate_livez >/dev/null || true   # stderr STAYS visible: the skip warn lives there
    gate_readyz >/dev/null || true
    echo done
' 2>&1)"
M11B_COUNT="$(printf '%s' "$M11B_OUT" | grep -c ']: skipped')"
assert_eq "M11b exactly ONE skip warn across two gates (one-time)" "1" "$M11B_COUNT"

# M12 — dualfight launchd arm: holder OUTSIDE the job lineage → halt 78
# (the arm that was MISSING on Darwin — the old guard returned 0 there).
# exit 78 kills the child before any echo; the rc comes from the child.
M12_OUT="$(PATH="$M_FIX:$PATH" INSTALL_DIR="$M_INSTALL" PORT=18777 ATTR_LSOF_PIDS="9999" bash -c '
    . "'"$UPGRADE_DIR"'/lib.sh" >/dev/null 2>&1
    _supervision_owned_pids() { printf "%s\n" 9999; }
    SUPERVISION_MODE=launchd SUPERVISION_UNIT=com.ensemble.prod
    supervision_dualfight_check
' 2>&1)"
M12_RC=$?
assert_eq "M12 dualfight launchd arm faults on a foreign holder (exit 78)" "78" "$M12_RC"
assert_contains "M12 the halt names the launchd lineage fault" "OUTSIDE launchd job com.ensemble.prod lineage" "$M12_OUT"

# M13 — dualfight launchd arm, holder IN the job lineage: green rc 0.
M13_OUT="$(PATH="$M_FIX:$PATH" INSTALL_DIR="$M_INSTALL" PORT=18777 ATTR_LSOF_PIDS="4321" bash -c '
    . "'"$UPGRADE_DIR"'/lib.sh" >/dev/null 2>&1
    _supervision_owned_pids() { printf "%s\n" 4321; }
    SUPERVISION_MODE=launchd SUPERVISION_UNIT=com.ensemble.prod
    supervision_dualfight_check
    echo "df-rc=$?"
' 2>&1)"
assert_contains "M13 dualfight launchd arm passes an in-lineage holder" "df-rc=0" "$M13_OUT"

# M14 — non-launchd shapes unchanged: script mode still returns 0 (the
# systemd path's own Linux fencing is untouched by the launchd arm).
M14_OUT="$(PATH="$M_FIX:$PATH" INSTALL_DIR="$M_INSTALL" PORT=18777 bash -c '
    . "'"$UPGRADE_DIR"'/lib.sh" >/dev/null 2>&1
    SUPERVISION_MODE=script
    supervision_dualfight_check
    echo "df-rc=$?"
' 2>&1)"
assert_contains "M14 script mode still returns 0 (no launchd/systemd fight)" "df-rc=0" "$M14_OUT"

# M15 — gate_readyz: the third touched gate refuses a FOREIGN holder too
# (the brief's "every touched gate refuses" — livez/version pinned in M9).
M15_OUT="$(PATH="$M_FIX:$PATH" INSTALL_DIR="$M_INSTALL" PORT=18777 READYZ_BUDGET_S=2 ATTR_LSOF_PIDS="999999" bash -c '
    . "'"$UPGRADE_DIR"'/lib.sh" >/dev/null 2>&1
    _supervision_owned_pids() { printf "%s\n" 424242; }
    gate_readyz >/dev/null 2>&1
    echo "readyz-rc=$?"
' 2>&1)"
assert_contains "M15 gate_readyz refuses the FOREIGN holder" "readyz-rc=1" "$M15_OUT"

# M16 — _handback_serving_probe: the hand-back verify rides the SAME
# conjunct (Fix #2 site per the incident §3.1 — the commit validated the
# wrong daemon's answers). Foreign holder → probe-level fail-fast (rc 1,
# the probe's own vocabulary); owned holder → green (no expected-version
# knob in this leg ⇒ the version conjunct is skipped by design).
M16_OUT="$(PATH="$M_FIX:$PATH" INSTALL_DIR="$M_INSTALL" PORT=18777 ATTR_LSOF_PIDS="999999" bash -c '
    . "'"$UPGRADE_DIR"'/lib.sh" >/dev/null 2>&1
    _supervision_owned_pids() { printf "%s\n" 424242; }
    _handback_serving_probe
    echo "hsp-rc=$?"
' 2>&1)"
assert_contains "M16 hand-back probe refuses the FOREIGN holder (fail-fast)" "hsp-rc=1" "$M16_OUT"
assert_contains "M16 the refusal names the attribution conjunct" "serving-process attribution REFUSED" "$M16_OUT"
M16B_OUT="$(PATH="$M_FIX:$PATH" INSTALL_DIR="$M_INSTALL" PORT=18777 ATTR_LSOF_PIDS="424242" bash -c '
    . "'"$UPGRADE_DIR"'/lib.sh" >/dev/null 2>&1
    _supervision_owned_pids() { printf "%s\n" 424242; }
    _handback_serving_probe
    echo "hsp-rc=$?"
' 2>&1)"
assert_contains "M16b hand-back probe greens the OWNED holder" "hsp-rc=0" "$M16B_OUT"

# M17 — gate_soak inherits the conjunct through its per-tick gate_version
# (the soak loop probes livez/readyz directly but versions THROUGH the
# gate): a foreign holder fails the soak on the FIRST green tick — no
# wall-clock is wasted validating the wrong daemon (the v0.30.3 9h shape).
# The adaptive knobs are shrunk to their floor so the leg runs in ~2-4s.
M17_OUT="$(PATH="$M_FIX:$PATH" INSTALL_DIR="$M_INSTALL" PORT=18777 ATTR_LSOF_PIDS="999999" \
    ADAPTIVE_SOAK_FLOOR_S=1 ADAPTIVE_SOAK_CAP_S=2 ADAPTIVE_SOAK_CONSECUTIVE=1 ADAPTIVE_SOAK_CADENCE_S=1 \
    bash -c '
    . "'"$UPGRADE_DIR"'/lib.sh" >/dev/null 2>&1
    _supervision_owned_pids() { printf "%s\n" 424242; }
    gate_soak 2 "vTEST"
    echo "soak-rc=$?"
' 2>&1)"
assert_contains "M17 gate_soak refuses via the per-tick version conjunct (foreign holder)" "soak-rc=1" "$M17_OUT"
assert_contains "M17 the soak failure carries the attribution detail" "attribution [version verify]: REFUSED" "$M17_OUT"
M17B_OUT="$(PATH="$M_FIX:$PATH" INSTALL_DIR="$M_INSTALL" PORT=18777 ATTR_LSOF_PIDS="424242" \
    ADAPTIVE_SOAK_FLOOR_S=1 ADAPTIVE_SOAK_CAP_S=2 ADAPTIVE_SOAK_CONSECUTIVE=1 ADAPTIVE_SOAK_CADENCE_S=1 \
    bash -c '
    . "'"$UPGRADE_DIR"'/lib.sh" >/dev/null 2>&1
    _supervision_owned_pids() { printf "%s\n" 424242; }
    gate_soak 2 "vTEST"
    echo "soak-rc=$?"
' 2>&1)"
assert_contains "M17b gate_soak greens the OWNED holder (early-exit floor)" "soak-rc=0" "$M17B_OUT"

# M18 — MULTI-PID OWNED SET, both directions (F1 council fix pin). The
# supervised production shape has >=2 owned pids (the launcher run_loop
# holds the daemon as a live child; the launchd job argv anchors the
# launcher) and _supervision_owned_pids emits them NEWLINE-joined. The
# pre-fix membership check matched SPACE-delimited tokens against that
# raw newline list and false-refused exactly this shape (holder = the
# daemon pid, owned = launcher NL daemon => REFUSED) — reproduced on real
# lib.sh before the fix, hidden until now because every seam armed a
# SINGLE-pid set (a seam blind-spot). The seam here prints TWO pids
# newline-joined — the real emission shape.
# M18a positive: holder = the daemon pid (222), owned = {launcher 111,
# daemon 222} => attribution PASSES.
M18_OUT="$(m_attr "ATTR_LSOF_PIDS=222" "ATTR_OWNED_PIDS=111 222")"
assert_contains "M18a two-pid owned set: the daemon-pid holder PASSES" "attr-rc=0" "$M18_OUT"
# M18b negative: holder = foreign 333 against the same two-pid set => REFUSES.
M18B_OUT="$(m_attr "ATTR_LSOF_PIDS=333" "ATTR_OWNED_PIDS=111 222")"
assert_contains "M18b two-pid owned set: a foreign holder still REFUSES" "attr-rc=1" "$M18B_OUT"
assert_contains "M18b the refusal names 333 and carries both owned pids" "333 NOT in the supervision-owned pid set" "$M18B_OUT"
assert_contains "M18b the detail's owned list is SPACE-flattened (journal single-line)" "owned:111 222" "$M18B_OUT"

# M19 — status.sh --verify, DRIVEN (F2 council): the verify-hardening arm
# needs a driven leg like every other gate class (the N4b pin is grep-only).
# The fixture stages a REAL status.sh + lib.sh copy with the owned-pid seam
# appended to the STAGED copy (the soak-pack fixture convention — the seam
# must survive status.sh's own `. lib.sh` re-source), a hand-built VALID
# release (manifest sha256s + tree hashes computed by the REAL lib.sh
# helpers) so the ONLY rc-flipping mechanism between the two legs is the
# attribution arm — the positive control proves the refusal leg's rc=1 came
# from the attribution arm, not from an integrity failure (non-vacuity).
S_FIX="$(mktemp -d -t suphb-status.XXXXXX)"
S_INSTALL="$S_FIX/inst"
S_STAGE="$S_FIX/scripts-upgrade"
mkdir -p "$S_INSTALL/releases/vT/agents/leader" "$S_INSTALL/releases/vT/frontend/dist" "$S_STAGE"
cp "$UPGRADE_DIR/status.sh" "$S_STAGE/status.sh"
cp "$UPGRADE_DIR/lib.sh" "$S_STAGE/lib.sh"
chmod +x "$S_STAGE/status.sh" "$S_STAGE/lib.sh"
cat >> "$S_STAGE/lib.sh" <<'SSEAM'

# [pack fixture seam — ENSEMBLE-32 Family-2 status legs] fixture-is-daemon.
_supervision_owned_pids() { printf "%s\n" ${ATTR_OWNED_PIDS:-424242}; }
SSEAM
printf '#!/bin/bash\nexit 0\n' > "$S_INSTALL/releases/vT/ensemble-prod"
printf '# stub launcher (fixture)\n' > "$S_INSTALL/releases/vT/launcher.sh"
printf 'port: 1\n' > "$S_INSTALL/releases/vT/config.yaml"
printf 'stub-agent\n' > "$S_INSTALL/releases/vT/agents/leader/soul.md"
printf 'stub-fe\n' > "$S_INSTALL/releases/vT/frontend/dist/index.html"
chmod +x "$S_INSTALL/releases/vT/ensemble-prod" "$S_INSTALL/releases/vT/launcher.sh"
S_RAW="$(INSTALL_DIR="$S_INSTALL" bash -c '
    . "'"$UPGRADE_DIR"'/lib.sh" >/dev/null 2>&1
    # 7 plain lines: 5 hashes + the 2 raw tree walks (sha  relpath). ALL
    # manifest assembly happens OUTSIDE the nested bash -c (python3 below) —
    # the multi-line printf quote-state inside a nested bash -c body was the
    # source of an unparseable-quoting defect in this block first draft.
    # printf format is DOUBLE-quoted (the pack nested-body idiom): an
    # unquoted \n loses its backslash in the assembled body.
    printf "%s\n" \
        "$(_sha256 "$INSTALL_DIR/releases/vT/ensemble-prod")" \
        "$(_sha256 "$INSTALL_DIR/releases/vT/launcher.sh")" \
        "$(_sha256 "$INSTALL_DIR/releases/vT/config.yaml")" \
        "$(_tree_hash_of_lines "$(_tree_manifest "$INSTALL_DIR/releases/vT/agents" "")")" \
        "$(_tree_hash_of_lines "$(_tree_manifest "$INSTALL_DIR/releases/vT/frontend" "")")" \
        "$(_tree_manifest "$INSTALL_DIR/releases/vT/agents" "")" \
        "$(_tree_manifest "$INSTALL_DIR/releases/vT/frontend" "")"
')"
# Assemble in stage.sh's EXACT line shape (per-tree maps inline-compacted on
# their own line — _manifest_map_lines parses line-scoped; the per-file maps
# are REQUIRED, else integrity pinpoints every file "on disk, not in
# manifest").
S_MANIFEST="$(printf '%s' "$S_RAW" | python3 -c '
import json, sys
lines = sys.stdin.read().splitlines()
bin_sha, lau_sha, cfg_sha, ag_tree, fe_tree, ag_walk, fe_walk = lines[:7]

def m(walk):
    items = [l.split(None, 1) for l in walk.splitlines() if l.strip()]
    return json.dumps({p: s for s, p in items}, separators=(",", ": "))

out = "{"
out += "\n  \"version\": \"vT\","
out += "\n  \"binary_version\": \"vT\","
out += "\n  \"binary_sha256\": \"%s\"," % bin_sha
out += "\n  \"launcher_sha256\": \"%s\"," % lau_sha
out += "\n  \"config_sha256\": \"%s\"," % cfg_sha
out += "\n  \"agents_tree_sha256\": \"%s\"," % ag_tree
out += "\n  \"frontend_tree_sha256\": \"%s\"," % fe_tree
out += "\n  \"agents_manifest\": %s," % m(ag_walk)
out += "\n  \"frontend_manifest\": %s" % m(fe_walk)
out += "\n}"
print(out)
')"
printf '%s' "$S_MANIFEST" > "$S_INSTALL/releases/vT/manifest.json"
ln -s releases/vT "$S_INSTALL/current"
# the SAME hermetic curl stub shape as the M harness (body + http_code).
# Quoted heredoc: NO backslash escapes — the body lands verbatim and the
# ATTR_* expansions happen at stub RUNTIME.
cat > "$S_FIX/curl" <<'SSTUB'
#!/bin/bash
__out=""; __fmt=""; __url=""
while [ $# -gt 0 ]; do
    case "$1" in
        -o) __out="$2"; shift 2 ;;
        -w) __fmt="$2"; shift 2 ;;
        -sS|-s|-S|--silent|--show-error) shift ;;
        --max-time) shift 2 ;;
        *) __url="$__url $1"; shift ;;
    esac
done
case "$__url" in
    *"/livez"*)
        __code=200
        __body='{"status":"ok","version":"'"${ATTR_VERSION:-vT}"'"}'
        ;;
    *"/readyz"*)
        __code=200
        __body='{"status":"ok","detail":{"inflight_turns":0}}'
        ;;
    *) exit 7 ;;
esac
if [ -n "$__out" ] && [ "$__out" != "/dev/null" ]; then
    printf '%s' "$__body" > "$__out"
fi
case "$__fmt" in
    *http_code*) printf '%s' "$__code" ;;
    *) [ -n "$__out" ] || printf '%s' "$__body" ;;
esac
exit 0
SSTUB
chmod +x "$S_FIX/curl"
# the lsof stub (ATTR-driven holder census) — WITHOUT it both legs refuse via
# "no listener behind a green answer", which would make M19a green for the
# WRONG mechanism (the foreign-pid refusal never engaging = mechanism-vacuity).
cat > "$S_FIX/lsof" <<'SLSTUB'
#!/bin/bash
case "${1:-}" in
    -ti:*) printf '%s\n' ${ATTR_LSOF_PIDS:-} ;;
    *) exit 1 ;;
esac
SLSTUB
chmod +x "$S_FIX/lsof"
s_status() {  # s_status <ATTR_LSOF_PIDS> — drive the STAGED status.sh --verify
    PATH="$S_FIX:$PATH" INSTALL_DIR="$S_INSTALL" PORT=18778 ATTR_LSOF_PIDS="$1" \
    bash -c '
        . "'"$S_STAGE"'/status.sh" sandbox --verify
    ' 2>&1
}
# M19a negative: the answerer (999999) is NOT the owned daemon (424242) —
# the mismatch must HARDEN into a verify failure (rc 1) and name itself.
M19A_OUT="$(s_status 999999)"; M19A_RC=$?
assert_eq "M19a status --verify REFUSES a foreign answerer (rc 1)" "1" "$M19A_RC"
assert_contains "M19a the verify failure NAMES the attribution arm" "attribution MISMATCH is a verify failure" "$M19A_OUT"
assert_contains "M19a the plain-mode warning fired too (wrong-process alert)" "serving-process attribution MISMATCH" "$M19A_OUT"
# M19b positive control: the SAME valid release with an OWNED answerer —
# verify PASSes (rc 0), proving M19a's rc=1 was the attribution arm alone.
M19B_OUT="$(s_status 424242)"; M19B_RC=$?
assert_eq "M19b status --verify PASSes the owned answerer (rc 0)" "0" "$M19B_RC"
assert_contains "M19b integrity held (the release is valid)" "integrity OK" "$M19B_OUT"
assert_contains "M19b no mismatch warn on the owned leg" "PASS" "$M19B_OUT"

rm -rf "$S_FIX"

rm -rf "$M_FIX"

# ===========================================================================
section "N — Fix #4 hand-back error capture (2026-11-14 §3.2/§5.2)"
# The v0.30.5/v0.30.9 halts recorded NO launchctl error anywhere: the
# bootstrap output was discarded, the WARNs printed rc only, and the
# resolver print stderr was destroyed. Now: _supervision_launchd_bootstrap
# stashes its captured output into HANDBACK_LAST_ERROR, the resolver
# stashes its print stderr, the WARN lines surface it, and the three halt
# templates (promote/restart/rollback) append it to the journal halt
# detail. The stub launchctl's bootstrap stderr text is the assertion
# needle — the halt journal must be able to carry exactly this.
N_FIX="$(mktemp -d -t suphb-hbe.XXXXXX)"
N_INSTALL="$N_FIX/install"
N_LABEL="com.ensemble.hbe.fixture"
mkdir -p "$N_INSTALL" "$N_FIX/home/Library/LaunchAgents"
printf '<?xml version="1.0" encoding="UTF-8"?>\n<plist version="1.0"><dict><key>Label</key><string>%s</string></dict></plist>\n' "$N_LABEL" > "$N_FIX/home/Library/LaunchAgents/${N_LABEL}.plist"
cat > "$N_FIX/launchctl" <<STUB
#!/bin/bash
set -u
STATE="${N_FIX}/launchd-registered"
LOG="${N_FIX}/launchctl.calls"
LABEL="${N_LABEL}"
cmd="\${1:-}"
[ \$# -gt 0 ] && shift
echo "launchctl \$cmd \$*" >> "\$LOG"
case "\$cmd" in
    list) exit 0 ;;
    print) echo "Could not find service \"gui/501/\${1:-}\" in GUI domain for uid \$(id -u)" >&2; exit 113 ;;
    bootout) rm -f "\$STATE"; exit 0 ;;
    bootstrap)
        if [ "\${N_BOOTSTRAP_RC:-0}" = "0" ]; then exit 0; fi
        printf '%s\n' "\${N_BOOTSTRAP_ERR:-5454: Bootstrap failed: 57: Operation not permitted}" >&2
        exit "\${N_BOOTSTRAP_RC:-5}" ;;
    *) exit 0 ;;
esac
STUB
chmod +x "$N_FIX/launchctl"

# N1 — the bootstrap failure is CAPTURED: restart_via_launcher halts (rc 1,
# Amendment #1) and HANDBACK_LAST_ERROR carries the stub's launchctl stderr;
# the WARN lines surface it ("captured:").
N1_OUT="$(PATH="$N_FIX:$PATH" HOME="$N_FIX/home" INSTALL_DIR="$N_INSTALL" N_BOOTSTRAP_RC=5 bash -c '
    . "'"$UPGRADE_DIR"'/lib.sh" >/dev/null 2>&1
    SUPERVISION_STATE=LAUNCHD_MANAGED; SUPERVISION_MODE=launchd
    SUPERVISION_UNIT="'"$N_LABEL"'"; SUPERVISION_PRESTOP_MAINPID=4321
    SUPERVISION_LAUNCHD_PLIST_PATH=""
    restart_via_launcher
    echo "rvl-rc=$?"
    echo "HBE=[${HANDBACK_LAST_ERROR:-<empty>}]"
' 2>&1)"
assert_contains "N1 hand-back halts fail-safe on the bootstrap failure" "rvl-rc=1" "$N1_OUT"
assert_contains "N1 the stub launchctl stderr is CAPTURED" "Bootstrap failed: 57: Operation not permitted" "$N1_OUT"
assert_contains "N1 the WARN surfaces the capture" "captured: launchctl bootstrap" "$N1_OUT"

# N2 — MAJOR #1/#2 (review round 2): the resolver is consumed via `$()` at
# the PRODUCTION hand-back site (restart_via_launcher — lib.sh restart_via_
# launcher's launchd arm), and a VARIABLE stash dies inside that command
# substitution: the pre-review N2 invoked the resolver PLAINLY, so the
# 93P/0F green was invocation-shape vacuous for this seam (the plain call
# let the variable survive; production loses it). Rewritten against the
# production shape: restart_via_launcher itself, all three resolver rungs
# failing (stub `launchctl print` → "Could not find service"; host plist
# hidden; no pre-stop stash). Asserts (a) the fail-safe halt (rc 1) AND
# (b) the stub stderr TEXT propagating to the halt-destined WARN through
# the OUT-OF-BAND channel ($$-keyed HANDBACK_ERROR_FILE — subshell-proof;
# _handback_last_error_text is the reader).
mv "$N_FIX/home/Library/LaunchAgents/${N_LABEL}.plist" "$N_FIX/home/Library/LaunchAgents/${N_LABEL}.plist.hidden"
N2_OUT="$(PATH="$N_FIX:$PATH" HOME="$N_FIX/home" INSTALL_DIR="$N_INSTALL" bash -c '
    . "'"$UPGRADE_DIR"'/lib.sh" >/dev/null 2>&1
    SUPERVISION_STATE=LAUNCHD_MANAGED; SUPERVISION_MODE=launchd
    SUPERVISION_UNIT="'"$N_LABEL"'"; SUPERVISION_PRESTOP_MAINPID=4321
    SUPERVISION_LAUNCHD_PLIST_PATH=""
    restart_via_launcher
    echo "rvl-rc=$?"
' 2>&1)"
assert_contains "N2 hand-back fails fail-safe via the PRODUCTION \$() consumption site" "rvl-rc=1" "$N2_OUT"
assert_contains "N2 the halt WARN carries the stub stderr (out-of-band, subshell-proof)" "Could not find service" "$N2_OUT"
assert_contains "N2 the capture rides the WARN captured: field" "captured: plist resolution failed" "$N2_OUT"
mv "$N_FIX/home/Library/LaunchAgents/${N_LABEL}.plist.hidden" "$N_FIX/home/Library/LaunchAgents/${N_LABEL}.plist"
# N2b — success hygiene: now that the resolver SUCCEEDS again (plist
# restored), the out-of-band channel must be CLEAR — N2's failure seeded
# it, and a successful resolution must clear it (a stale failure diagnosis
# must never ride a later halt in the same run).
N2B_OUT="$(PATH="$N_FIX:$PATH" HOME="$N_FIX/home" INSTALL_DIR="$N_INSTALL" bash -c '
    . "'"$UPGRADE_DIR"'/lib.sh" >/dev/null 2>&1
    SUPERVISION_LAUNCHD_PLIST_PATH=""
    _p="$(_supervision_launchd_plist_path "'"$N_LABEL"'")"
    echo "resolved=[${_p:+yes}]"
    echo "ACCESSOR=[$(_handback_last_error_text)]"
' 2>&1)"
assert_contains "N2b the resolver resolves the restored plist" "resolved=[yes]" "$N2B_OUT"
assert_contains "N2b the out-of-band channel is CLEAR after a successful resolution" "ACCESSOR=[]" "$N2B_OUT"

# N3 — the three halt templates append the capture to the journal halt
# detail (promote/restart/rollback; the LIVE halt leg through restart.sh
# is the restart-sim pack's §N leg — this pins the template wiring).
for N_SCRIPT in promote.sh restart.sh rollback.sh; do
    # MAJOR #1 (review round 2): the templates read through the
    # _handback_last_error_text ACCESSOR (HB_ERR) — the bare
    # ${HANDBACK_LAST_ERROR:-…} read was the subshell-loss bug shape.
    if grep -q 'hand-back error: ${HB_ERR:-none captured}' "$UPGRADE_DIR/$N_SCRIPT" \
       && grep -q 'HB_ERR="$(_handback_last_error_text)"' "$UPGRADE_DIR/$N_SCRIPT"; then
        _pass "N3 $N_SCRIPT halt template reads the accessor (HB_ERR — subshell-proof)"
    else
        _fail "N3 $N_SCRIPT halt template reads the accessor (HB_ERR — subshell-proof)" "hand-back error: \${HB_ERR:-none captured} + the accessor read" "absent"
    fi
done

# N4 — Fix #2-ii structural pins: the promote FLIP-VERIFY must require the
# port-holder census (a crash-looping launcher — a NEW owned pid with
# NOTHING serving — must NOT satisfy it; incident §3.1) and the status
# --verify must harden an attribution mismatch into a verify failure.
# Grep-pins from the definition sites (N3 pattern): driving a REAL promote
# to the flip-verify needs a launchd-classified sandbox; the refusal arms
# themselves are one straight-line block each.
# D-1 (E2E round 1): the census moved into lib.sh _flip_verify_port_
# attribution (bounded listen-grace) — the needles follow the verdict
# messages to their new home; promote.sh must still CONSUME the function.
if grep -q 'NOTHING listened on :${PORT} within' "$UPGRADE_DIR/lib.sh" \
   && grep -q 'NOT in the owned-pid set' "$UPGRADE_DIR/lib.sh" \
   && grep -q '_flip_verify_port_attribution "$_post_pid"' "$UPGRADE_DIR/promote.sh"; then
    _pass "N4 promote flip-verify requires the port-holder census (owned-pid set; D-1 grace function)"
else
    _fail "N4 promote flip-verify requires the port-holder census (owned-pid set; D-1 grace function)" "census needles" "absent"
fi
if grep -q 'STATUS_ATTR_MISMATCH' "$UPGRADE_DIR/status.sh" \
   && grep -q 'attribution MISMATCH is a verify failure' "$UPGRADE_DIR/status.sh"; then
    _pass "N4b status --verify hardens an attribution mismatch into a failure"
else
    _fail "N4b status --verify hardens an attribution mismatch into a failure" "mismatch needles" "absent"
fi

# N5 — F3 council fix: the BENIGN bootstrap arm ("already bootstrapped"/rc 2
# after the caller's retry) must CLEAR HANDBACK_LAST_ERROR, mirroring the
# success arm. Stale-capture shape: attempt 1 fails hard (stash set with the
# stub's stderr), attempt 2 comes back benign — a later warn in the same run
# must not surface attempt 1's diagnosis as if it described the benign arm.
N5B_ERR="5454: Bootstrap failed: 57: Operation not permitted"
N5_OUT="$(PATH="$N_FIX:$PATH" HOME="$N_FIX/home" INSTALL_DIR="$N_INSTALL" \
    N_BOOTSTRAP_RC=2 N_BOOTSTRAP_ERR="already bootstrapped" bash -c '
    . "'"$UPGRADE_DIR"'/lib.sh" >/dev/null 2>&1
    HANDBACK_LAST_ERROR="stale capture from a previous failed attempt"
    _supervision_launchd_bootstrap "'"$N_FIX/home/Library/LaunchAgents/$N_LABEL.plist"'"
    echo "benign-rc=$?"
    echo "HBE-after-benign=[${HANDBACK_LAST_ERROR:-<empty>}]"
    # the hard-fail arm STILL stashes (the clear must not neuter Fix #4):
    export N_BOOTSTRAP_RC=5
    export N_BOOTSTRAP_ERR="'"$N5B_ERR"'"
    _supervision_launchd_bootstrap "'"$N_FIX/home/Library/LaunchAgents/$N_LABEL.plist"'"
    echo "hard-rc=$?"
    echo "HBE-after-hard=[${HANDBACK_LAST_ERROR:-<empty>}]"
' 2>&1)"
assert_contains "N5 the benign arm returns rc 2 (caller revalidates)" "benign-rc=2" "$N5_OUT"
assert_contains "N5 the benign arm CLEARED the stale capture (F3)" "HBE-after-benign=[<empty>]" "$N5_OUT"
assert_contains "N5 the hard-fail arm still captures (Fix #4 intact)" "HBE-after-hard=[launchctl bootstrap" "$N5_OUT"
assert_contains "N5 the hard-fail stash carries the launchctl stderr" "$N5B_ERR" "$N5_OUT"

rm -rf "$N_FIX"

# ===========================================================================
# N6 — D-1 (E2E round 1): the FLIP-VERIFY census lives in the lib.sh grace
# function and promote.sh consumes it — structural pins (the live drive of
# the function is the handback pack's G-D1 section; the harness here owns
# the restart_via_launcher seam, so this is the wiring pin).
if grep -q '_flip_verify_port_attribution "$_post_pid"' "$UPGRADE_DIR/promote.sh" \
   && grep -q 'FLIP_VERIFY_LISTEN_GRACE_S=' "$UPGRADE_DIR/lib.sh" \
   && grep -q '_flip_verify_port_attribution()' "$UPGRADE_DIR/lib.sh"; then
    _pass "N6 promote flip-verify consumes the lib.sh listen-grace function (D-1)"
else
    _fail "N6 promote flip-verify consumes the lib.sh listen-grace function (D-1)" "grace wiring" "absent"
fi

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
