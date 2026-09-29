#!/bin/bash
# ============================================================================
# tests/test_supervision_classify.sh — pin tests for the P1 supervision
# classifier twins' SHELL side (supervision_classify in scripts/upgrade/lib.sh)
# ============================================================================
# Supervision-detection commission P5 (final): the stub-driven classifier
# matrix. Items covered (mission matrix 1-4):
#
#   1. Ladder × fixtures — classification across fixture cgroup leaves
#      (`.service`, `ensemble-upgrade-*.scope`, `session-*.scope`,
#      user/init/machine slices, unknown leaf) × env matrix (unit / script /
#      auto / unset / 0 / false / no / off / garbage) → assert (state, unit)
#      + WARN-once semantics. Non-Linux arm (Darwin uname stub) asserts ZERO
#      /proc reads attempted.
#   2. Allowlist-strip regression pin (§0 mandate): INVOCATION_ID absent +
#      upgrade-scope cgroup → SCOPE_SURVIVOR not SCRIPT (today's live
#      shape), and the §0 corroboration WARN cells both ways.
#   3. Unit-name precedence: env explicit > cgroup-derived (UNIT_MANAGED
#      only) > INSTALL_DIR/.env; all fail → script.
#   4. Explicit-unit-no-name → exit 78 at preflight (never silent-degrade).
#
# Stub strategy (comp7 style): lib.sh is SOURCED into a subshell, then the
# two /proc-reading helpers are overridden with fault-injecting stubs that
# (a) record every call into a log file (proving zero-reads arms) and
# (b) return fixture-determined values. The fixture pid 424242 is
# deliberately nonexistent — /proc/424242/environ is unreadable, which is
# exactly the "INVOCATION_ID absent" (executor allowlist-strip) shape.
# uname is stubbed via PATH injection for the Darwin arm (comp4c
# precedent). The raw /proc cgroup LINE shapes (v2 `0::` vs v1
# `name=systemd:`) are pinned against the real parse in the twins suite
# (tests/test_supervision_twins.sh §C) — here the leaf VALUE is
# post-parse.
#
# Systemd-host arms are NAMED-FENCED: a host without /run/systemd/system
# cannot reach the auto-derive chain's leaf classification (the inline
# guard short-circuits), so those cells count as SKIP with the fence name
# cited — counted-SKIP never FAIL.
#
# Run:
#   bash tests/test_supervision_classify.sh
# ============================================================================

set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UPGRADE_DIR="$REPO_ROOT/scripts/upgrade"

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

# Fixture INSTALL_DIR (throwaway — the classifier only needs the path for
# WARN text + the .env rung; a real .env is planted per-case).
FIXT="$(mktemp -d -t supclass.XXXXXX)"

# The fault-injecting call log — cleared per run_classify invocation.
CALLLOG="$(mktemp -t supclass-calls.XXXXXX)"
export CALLLOG

# run_classify <linux|darwin> <leaf|SPECIAL> <env-assignments ;-separated>
#   SPECIAL leaf values:
#     UNREADABLE — leaf helper returns nothing (cgroup unreadable)
#     NONE       — owned-pid helper returns nothing (no owned pid)
# Prints classify stdout+stderr, then rc=/MODE=/STATE=/UNIT= lines, then a
# final CALLS: line (comma-joined fault-injector hits; empty = zero reads).
run_classify() {
    local mode="$1" leaf="$2" envs="${3:-}"
    : > "$CALLLOG"
    local script
    script="$(mktemp -t supclass-run.XXXXXX)"
    {
        printf 'export INSTALL_DIR="%s"\n' "$FIXT"
        printf 'unset ENSEMBLE_SUPERVISION ENSEMBLE_RESTART_UNIT\n'
        printf 'export SUP_FIXT_LEAF="%s"\n' "$leaf"
        local a
        if [ -n "$envs" ]; then
            # shellcheck disable=SC2086
            for a in ${envs//;/ }; do
                case "$a" in *=*) printf 'export %s\n' "$a" ;; esac
            done
        fi
        printf '. "%s" >/dev/null 2>&1\n' "$UPGRADE_DIR/lib.sh"
        cat <<'STUBS'
_supervision_owned_pids() {
    printf 'owned_pids\n' >> "$CALLLOG"
    case "$SUP_FIXT_LEAF" in
        NONE) return 0 ;;
        *)    printf '%s\n' 424242 ;;
    esac
}
_supervision_pid_cgroup_leaf() {
    printf 'cgroup_leaf\n' >> "$CALLLOG"
    case "$SUP_FIXT_LEAF" in
        UNREADABLE|NONE) return 1 ;;
        *) printf '%s\n' "$SUP_FIXT_LEAF" ;;
    esac
}
STUBS
        printf 'supervision_classify\n'
        printf 'printf "rc=%%s\\n" "$?"\n'
        printf 'printf "MODE=%%s\\nSTATE=%%s\\nUNIT=%%s\\n" "${SUPERVISION_MODE:-}" "${SUPERVISION_STATE:-}" "${SUPERVISION_UNIT:-}"\n'
    } > "$script"
    local out
    if [ "$mode" = "darwin" ]; then
        out="$(PATH="$SUP_DARWIN_PATH:$PATH" bash "$script" 2>&1)"
    else
        out="$(bash "$script" 2>&1)"
    fi
    rm -f "$script"
    printf '%s\n' "$out"
    printf 'CALLS:%s\n' "$(cat "$CALLLOG" 2>/dev/null | tr '\n' ',' | sed 's/,$//')"
}

globals_state() { printf '%s' "$1" | sed -n 's/^STATE=//p'; }
globals_unit()  { printf '%s' "$1" | sed -n 's/^UNIT=//p' | head -1; }
globals_mode()  { printf '%s' "$1" | sed -n 's/^MODE=//p'; }
machine_line()  { printf '%s' "$1" | grep '^ENSEMBLE_SUPERVISION_RESULT=' | head -1; }
rc_line()       { printf '%s' "$1" | sed -n 's/^rc=//p'; }
calls_of()      { printf '%s' "$1" | sed -n 's/^CALLS://p'; }

# Host fence: the auto-derive chain past the /run guard needs the live
# systemd marker dir (checked inline in lib.sh — deliberately NOT
# stubbable without overriding the function under test, which would void
# the pin).
HOST_HAS_SYSTEMD=0
[ "$(uname -s)" = "Linux" ] && [ -d /run/systemd/system ] && HOST_HAS_SYSTEMD=1

# ===========================================================================
section "1a — env ladder top (explicit / opt-out / garbage): zero /proc reads"

# Every ladder-top cell must classify deterministically (guard discipline
# pinned at the function seam). Column 5 = expected fault-log hits:
#   '-'  → zero /proc reads (short-circuits before the derive chain)
#   'R'  → the cgroup rung runs (explicit-unit with no env name resolves
#          through owned-pid discovery on a systemd host — the §3 ladder)
TOP_MATRIX='
unit_env|ENSEMBLE_SUPERVISION=unit ENSEMBLE_RESTART_UNIT=ensemble-x.service|UNIT_MANAGED|ensemble-x.service|unit|-
unit_env_no_name|ENSEMBLE_SUPERVISION=unit|UNIT_MANAGED||unit|R
script_env|ENSEMBLE_SUPERVISION=script|SCRIPT_NOHUP||script|-
false0|ENSEMBLE_SUPERVISION=0|SCRIPT_NOHUP||script|-
falsefalse|ENSEMBLE_SUPERVISION=false|SCRIPT_NOHUP||script|-
falseno|ENSEMBLE_SUPERVISION=no|SCRIPT_NOHUP||script|-
falseoff|ENSEMBLE_SUPERVISION=off|SCRIPT_NOHUP||script|-
garbage|ENSEMBLE_SUPERVISION=banana|SCRIPT_NOHUP||script|-
garbage_near|ENSEMBLE_SUPERVISION=unitx|SCRIPT_NOHUP||script|-
'
while IFS='|' read -r cell envs want_state want_unit want_mode want_reads; do
    [ -n "${cell:-}" ] || continue
    out="$(run_classify linux session-9.scope "$envs")"
    assert_eq "$cell: state" "$want_state" "$(globals_state "$out")"
    assert_eq "$cell: unit"  "$want_unit"  "$(globals_unit "$out")"
    assert_eq "$cell: mode"  "$want_mode"  "$(globals_mode "$out")"
    assert_eq "$cell: machine line" "ENSEMBLE_SUPERVISION_RESULT=$want_state${want_unit:+:$want_unit}" "$(machine_line "$out")"
    if [ "$want_reads" = "-" ]; then
        assert_eq "$cell: zero /proc reads" "" "$(calls_of "$out")"
    else
        assert_eq "$cell: cgroup rung ran" "owned_pids,cgroup_leaf" "$(calls_of "$out")"
    fi
done <<EOF
$TOP_MATRIX
EOF

# rc expectations split: only the unresolved-explicit-unit cell returns 1.
XU="$(run_classify linux session-9.scope 'ENSEMBLE_SUPERVISION=unit')"
assert_eq "explicit-unit unresolved (no env/no cgroup/no .env): rc 1" "1" "$(rc_line "$XU")"
assert_contains "explicit-unit unresolved: WARN names the refusal" "preflight must refuse (78)" "$XU"
XG="$(run_classify linux session-9.scope 'ENSEMBLE_SUPERVISION=garbageval')"
assert_eq "garbage: rc 0 (fail-toward-script)" "0" "$(rc_line "$XG")"

# ── M3 (review cycle 1 fixback): explicit unit on a unit-INCAPABLE host ────
# The Darwin uname PATH-stub simulates a host that cannot honor unit
# supervision (non-Linux ⇒ no systemd, no systemctl — zero /proc reads by
# the guard discipline). An EXPLICIT ENSEMBLE_SUPERVISION=unit assertion
# there must FAIL CLOSED at classify (rc 1, refuse reason carried for the
# preflight's exit-78) — NEVER loud-degrade + nohup at rc 0 (the r-f82e
# surprise class). Pinned for BOTH name shapes: name resolved from env
# (the defect arm — used to return rc 0 'conforming') and no name at all.
# The globals keep the DECLARED shape (UNIT_MANAGED + the name) so the
# refusal is diagnosable and the machine-line grammar stays frozen.
SUP_DARWIN_PATH="$(mktemp -d -t supclass-m3darwin.XXXXXX)"
cat > "$SUP_DARWIN_PATH/uname" <<STUB
#!/bin/bash
case "\$1" in
    -s) echo "Darwin" ;;
    *) "$REAL_UNAME" "\$@" ;;
esac
STUB
chmod +x "$SUP_DARWIN_PATH/uname"

M3A="$(run_classify darwin session-9.scope 'ENSEMBLE_SUPERVISION=unit ENSEMBLE_RESTART_UNIT=ensemble-m3.service')"
assert_eq "M3 incapable host + env name: rc 1 (fail-closed, never silent-degrade)" "1" "$(rc_line "$M3A")"
assert_eq "M3 incapable host: state keeps the declared shape" "UNIT_MANAGED" "$(globals_state "$M3A")"
assert_eq "M3 incapable host: unit carried for the diagnosis" "ensemble-m3.service" "$(globals_unit "$M3A")"
assert_eq "M3 incapable host: mode unit" "unit" "$(globals_mode "$M3A")"
assert_eq "M3 incapable host: machine line grammar frozen (name carried)" "ENSEMBLE_SUPERVISION_RESULT=UNIT_MANAGED:ensemble-m3.service" "$(machine_line "$M3A")"
assert_contains "M3 incapable host: WARN names the host incapability" "cannot honor unit supervision" "$M3A"
assert_contains "M3 incapable host: WARN names the refusal" "preflight must refuse (78)" "$M3A"
assert_contains "M3 incapable host: WARN cites the env/.env name as unusable" "ensemble-m3.service' from env/INSTALL_DIR/.env is unusable here" "$M3A"
assert_eq "M3 incapable host: zero /proc reads (guard short-circuits)" "" "$(calls_of "$M3A")"

M3B="$(run_classify darwin session-9.scope 'ENSEMBLE_SUPERVISION=unit')"
assert_eq "M3 incapable host, no name: rc 1" "1" "$(rc_line "$M3B")"
assert_eq "M3 incapable host, no name: bare machine line" "ENSEMBLE_SUPERVISION_RESULT=UNIT_MANAGED" "$(machine_line "$M3B")"
assert_contains "M3 incapable host, no name: WARN names the refusal" "preflight must refuse (78)" "$M3B"
assert_eq "M3 incapable host, no name: zero /proc reads" "" "$(calls_of "$M3B")"
rm -rf "$SUP_DARWIN_PATH"

# WARN-once semantics: a garbage declaration warns EXACTLY once per
# process even across three classify calls.
WO="$(mktemp -t supclass-wo.XXXXXX)"
(
    export INSTALL_DIR="$FIXT"
    export ENSEMBLE_SUPERVISION=pineapple
    . "$UPGRADE_DIR/lib.sh" >/dev/null 2>&1
    supervision_classify >/dev/null
    supervision_classify >/dev/null
    supervision_classify >/dev/null
) 2>&1 | tee "$WO" >/dev/null
WO_N="$(grep -c "garbage ENSEMBLE_SUPERVISION='pineapple'" "$WO" || true)"
assert_eq "garbage WARN-once across 3 calls" "1" "$WO_N"
rm -f "$WO"

# Explicit script NEVER verifies the cgroup (A1 'unreachable' cell) —
# even with a .service leaf fixture, script stays script, no reads.
ES_OUT="$(run_classify linux my-daemon.service 'ENSEMBLE_SUPERVISION=script')"
assert_eq "explicit script ignores cgroup (state)" "SCRIPT_NOHUP" "$(globals_state "$ES_OUT")"
assert_eq "explicit script ignores cgroup (zero reads)" "" "$(calls_of "$ES_OUT")"

# ===========================================================================
section "1b — auto/unset derive chain × fixture leaves (systemd host)"

if [ "$HOST_HAS_SYSTEMD" = "1" ]; then
    AUTO_MATRIX='
v2_service|my-ensemble.service|UNIT_MANAGED|my-ensemble.service|unit|auto
v2_scope|ensemble-upgrade-r-20260929-023557-7dd2.scope|SCOPE_SURVIVOR||script|auto
v2_scope_ts|ensemble-upgrade-r-20260928-005506-f82e.scope|SCOPE_SURVIVOR||script|unset
v2_session|session-1839.scope|SCRIPT_NOHUP||script|auto
v2_init|init.scope|SCRIPT_NOHUP||script|auto
v1_service|ensemble-live.service|UNIT_MANAGED|ensemble-live.service|unit|auto
v1_session|session-42.scope|SCRIPT_NOHUP||script|auto
slice_user|user-1000.slice|SCRIPT_NOHUP||script|auto
slice_user_root|user.slice|SCRIPT_NOHUP||script|auto
slice_machine|machine.slice|SCRIPT_NOHUP||script|auto
slice_machine_nested|foo-machine.slice|SCRIPT_NOHUP||script|auto
unknown_leaf|systemd-tmpfiles-setup|SCRIPT_NOHUP||script|auto
'
    while IFS='|' read -r cell leaf want_state want_unit want_mode decl; do
        [ -n "${cell:-}" ] || continue
        local_env=""
        [ "$decl" = "auto" ] && local_env="ENSEMBLE_SUPERVISION=auto"
        out="$(run_classify linux "$leaf" "$local_env")"
        assert_eq "$cell: state" "$want_state" "$(globals_state "$out")"
        assert_eq "$cell: unit"  "$want_unit"  "$(globals_unit "$out")"
        assert_eq "$cell: mode"  "$want_mode"  "$(globals_mode "$out")"
        assert_eq "$cell: rc"    "0"           "$(rc_line "$out")"
        assert_eq "$cell: discovery ran (owned_pids+cgroup_leaf)" "owned_pids,cgroup_leaf" "$(calls_of "$out")"
        assert_eq "$cell: machine line" "ENSEMBLE_SUPERVISION_RESULT=$want_state${want_unit:+:$want_unit}" "$(machine_line "$out")"
    done <<EOF
$AUTO_MATRIX
EOF

    # Unreadable cgroup → script + WARN-once.
    UNR="$(run_classify linux UNREADABLE 'ENSEMBLE_SUPERVISION=auto')"
    assert_eq "unreadable cgroup: state" "SCRIPT_NOHUP" "$(globals_state "$UNR")"
    assert_contains "unreadable cgroup: WARN-once" "cgroup unreadable for owned pid 424242 — classifying script" "$UNR"

    # No owned pid → script + WARN-once.
    NOP="$(run_classify linux NONE 'ENSEMBLE_SUPERVISION=auto')"
    assert_eq "no owned pid: state" "SCRIPT_NOHUP" "$(globals_state "$NOP")"
    assert_contains "no owned pid: WARN-once" "no owned pid discovered" "$NOP"
    assert_contains "no owned pid: WARN cites INSTALL_DIR" "INSTALL_DIR=$FIXT" "$NOP"

    # A .service leaf WITHOUT INVOCATION_ID in the owning environ
    # (allowlist-strip shape) still classifies UNIT_MANAGED (§0 cgroup
    # primary) + the corroboration-missing WARN.
    NIN="$(run_classify linux ensemble-main.service 'ENSEMBLE_SUPERVISION=auto')"
    assert_eq "service leaf, INVOCATION_ID absent: state" "UNIT_MANAGED" "$(globals_state "$NIN")"
    assert_eq "service leaf, INVOCATION_ID absent: unit" "ensemble-main.service" "$(globals_unit "$NIN")"
    assert_contains "§0: no-INVOCATION_ID corroboration WARN" "trusting cgroup (§0 corroboration only)" "$NIN"
else
    _skip "1b auto-derive leaf matrix — FENCE: no /run/systemd/system on this host (the inline guard short-circuits before leaf classification)"
fi

# ===========================================================================
section "2 — §0 allowlist-strip regression pin (TODAY'S LIVE SHAPE)"

if [ "$HOST_HAS_SYSTEMD" = "1" ]; then
    # The promote executor env allowlist STRIPS INVOCATION_ID, and the
    # daemon-under-a-scope carries an ensemble-upgrade-*.scope leaf with
    # NO INVOCATION_ID surviving in its environ. An INVOCATION_ID-gated
    # classifier would call that SCRIPT; §0's cgroup-primary rule must
    # call it SCOPE_SURVIVOR. Fixture pid 424242 (nonexistent) has an
    # unreadable environ == INVOCATION_ID absent.
    LIVE_OUT="$(run_classify linux ensemble-upgrade-r-20260929-023557-7dd2.scope 'ENSEMBLE_SUPERVISION=auto')"
    assert_eq "§0 allowlist-strip: SCOPE_SURVIVOR not SCRIPT" "SCOPE_SURVIVOR" "$(globals_state "$LIVE_OUT")"
    assert_eq "§0 allowlist-strip: rc" "0" "$(rc_line "$LIVE_OUT")"
    assert_eq "§0 allowlist-strip: machine line" "ENSEMBLE_SUPERVISION_RESULT=SCOPE_SURVIVOR" "$(machine_line "$LIVE_OUT")"
    assert_not_contains "§0 allowlist-strip: no unit invented" ":ensemble" "$(machine_line "$LIVE_OUT")"
    assert_not_contains "§0 allowlist-strip: NEVER classifies SCRIPT" "STATE=SCRIPT_NOHUP" "$LIVE_OUT"

    # Complementary cell: INVOCATION_ID PRESENT + scope leaf → STILL scope
    # (§0: transient scopes mint INVOCATION_ID too) + WARN trusting cgroup.
    # /proc/<pid>/environ shows the EXEC-TIME environment, so the probe
    # child is EXEC'D with INVOCATION_ID already set (an export inside a
    # running shell would be invisible to the classifier's environ read —
    # the very allowlist-strip shape this cell guards).
    PRESENT_OUT="$(INVOCATION_ID=feedface0000111122223333444455556 \
        INSTALL_DIR="$FIXT" ENSEMBLE_SUPERVISION=auto bash -c '
            # capture ONE pid for both stubs — $BASHPID inside a command
            # substitution is the SUBSHELL pid, so a per-stub BASHPID
            # comparison would never match
            TARGET_PID="$BASHPID"
            . "$1" >/dev/null 2>&1
            _supervision_owned_pids() { printf "%s\n" "$TARGET_PID"; }
            _supervision_pid_cgroup_leaf() {
                if [ "$1" = "$TARGET_PID" ]; then
                    printf "%s\n" "ensemble-upgrade-r-live.scope"
                    return 0
                fi
                return 1
            }
            supervision_classify
            printf "STATE=%s\n" "${SUPERVISION_STATE:-}"
        ' _ "$UPGRADE_DIR/lib.sh" 2>&1)"
    assert_contains "§0 INVOCATION_ID present + scope → still SCOPE_SURVIVOR" "STATE=SCOPE_SURVIVOR" "$PRESENT_OUT"
    assert_contains "§0 INVOCATION_ID present + scope → WARN trusting cgroup" "trusting cgroup (§0: transient scopes mint INVOCATION_ID too)" "$PRESENT_OUT"
    assert_not_contains "§0 INVOCATION_ID present must NOT mint UNIT_MANAGED" "STATE=UNIT_MANAGED" "$PRESENT_OUT"
    rm -f "$PRESENT_OUT"
else
    _skip "2 §0 allowlist-strip pins — FENCE: no /run/systemd/system on this host"
fi

# ===========================================================================
section "3 — unit-name precedence (env > cgraph > .env; all-fail shapes)"

if [ "$HOST_HAS_SYSTEMD" = "1" ]; then
    # 3a. env explicit OVERRIDES cgroup-derived (.service leaf present).
    P1_OUT="$(run_classify linux cgraph-derived.service 'ENSEMBLE_SUPERVISION=auto ENSEMBLE_RESTART_UNIT=env-wins.service')"
    assert_eq "3a env beats cgroup (unit)" "env-wins.service" "$(globals_unit "$P1_OUT")"
    assert_eq "3a env beats cgroup (state)" "UNIT_MANAGED" "$(globals_state "$P1_OUT")"

    # 3b. no env → cgroup-derived name used.
    P2_OUT="$(run_classify linux cgraph-derived.service 'ENSEMBLE_SUPERVISION=auto')"
    assert_eq "3b cgraph-derived name" "cgraph-derived.service" "$(globals_unit "$P2_OUT")"

    # 3c. explicit-unit + no env + non-service leaf → INSTALL_DIR/.env rung.
    printf 'PORT=1\nENSEMBLE_RESTART_UNIT=from-dotenv.service\n' > "$FIXT/.env"
    P3_OUT="$(run_classify linux session-5.scope 'ENSEMBLE_SUPERVISION=unit')"
    assert_eq "3c explicit unit falls to .env (state)" "UNIT_MANAGED" "$(globals_state "$P3_OUT")"
    assert_eq "3c explicit unit falls to .env (unit)" "from-dotenv.service" "$(globals_unit "$P3_OUT")"
    assert_eq "3c explicit unit falls to .env (rc)" "0" "$(rc_line "$P3_OUT")"

    # 3d. explicit-unit + no env + .service leaf → cgraph rung BEFORE .env.
    P4_OUT="$(run_classify linux leafsvc.service 'ENSEMBLE_SUPERVISION=unit')"
    assert_eq "3d explicit unit + .service leaf → leaf name wins over .env" "leafsvc.service" "$(globals_unit "$P4_OUT")"

    # 3e. auto + .service leaf → env-override rung applies to the
    #     cgraph-derived UNIT_MANAGED name too.
    P5_OUT="$(run_classify linux leafsvc.service 'ENSEMBLE_SUPERVISION=auto ENSEMBLE_RESTART_UNIT=envwins2.service')"
    assert_eq "3e auto: env override applies to UNIT_MANAGED name" "envwins2.service" "$(globals_unit "$P5_OUT")"

    # 3f. all rungs fail under auto → script, no unit, no invented name.
    rm -f "$FIXT/.env"
    P6_OUT="$(run_classify linux session-5.scope 'ENSEMBLE_SUPERVISION=auto')"
    assert_eq "3f all-rungs-fail under auto → script" "SCRIPT_NOHUP" "$(globals_state "$P6_OUT")"
    assert_eq "3f all-rungs-fail under auto → no unit" "" "$(globals_unit "$P6_OUT")"

    # 3g. quoted .env values are honored (quote-strip grammar).
    printf "ENSEMBLE_RESTART_UNIT='quoted-dotenv.service'\n" > "$FIXT/.env"
    P7_OUT="$(run_classify linux session-5.scope 'ENSEMBLE_SUPERVISION=unit')"
    assert_eq "3g quoted .env unit honored" "quoted-dotenv.service" "$(globals_unit "$P7_OUT")"

    # 3h. .env rung does NOT fire for a non-unit fixture under auto
    #     (scope leaf + .env configured → still SCOPE_SURVIVOR at
    #     classify time; the .env name is only CONSUMED at hand-back).
    P8_OUT="$(run_classify linux ensemble-upgrade-z.scope 'ENSEMBLE_SUPERVISION=auto')"
    assert_eq "3h scope leaf ignores .env name at classify" "SCOPE_SURVIVOR" "$(globals_state "$P8_OUT")"
    assert_eq "3h scope leaf carries no unit name" "" "$(globals_unit "$P8_OUT")"
    rm -f "$FIXT/.env"
else
    _skip "3 precedence pins — FENCE: no /run/systemd/system on this host"
fi

# ===========================================================================
section "4 — explicit-unit-no-name → exit 78 at preflight (never silent-degrade)"

# 4a was pinned at 1a (rc 1 + WARN). 4b: supervision_preflight converts
# the rc into _refuse (exit 78) with the supervision-unit-unresolved
# token; the journal carries the refusal record.
PF_FIXT="$(mktemp -d -t supclass-pf.XXXXXX)"
mkdir -p "$PF_FIXT/releases"
cat > "$PF_FIXT/releases/state.json" <<'JOURNAL'
{"current":null,"previous":null,"in_flight":null,"rollback_window_count":{"24h":0,"window_start":null},"cooldown_until":null,"quarantined":[],"history":[]}
JOURNAL
PF_OUT="$(
    (
        export INSTALL_DIR="$PF_FIXT"
        export ENSEMBLE_SUPERVISION=unit
        unset ENSEMBLE_RESTART_UNIT
        . "$UPGRADE_DIR/lib.sh" >/dev/null 2>&1
        _supervision_owned_pids() { printf '%s\n' 424242; }
        _supervision_pid_cgroup_leaf() { printf '%s\n' 'session-9.scope'; }
        # nested subshell so _refuse's exit 78 does not kill the harness
        ( supervision_preflight )
        echo "pf-rc=$?"
    ) 2>&1
)"
assert_contains "4b preflight refusal message (never silent-degrade)" "preflight refused: ENSEMBLE_SUPERVISION=unit is explicit" "$PF_OUT"
case "$PF_OUT" in
    *pf-rc=78*) _pass "4b preflight exit 78" ;;
    *) _fail "4b preflight exit 78" "pf-rc=78" "$(printf '%s' "$PF_OUT" | grep -o 'pf-rc=[0-9]*' || echo none)" ;;
esac
assert_contains "4b machine line emitted pre-refusal" "ENSEMBLE_SUPERVISION_RESULT=UNIT_MANAGED" "$PF_OUT"
assert_not_contains "4b no stale outcome line on refusal" "ENSEMBLE_SUPERVISION_OUTCOME=" "$PF_OUT"
PF_J="$(cat "$PF_FIXT/releases/state.json")"
assert_contains "4b refusal journaled" '"event":"refusal"' "$PF_J"
assert_contains "4b refusal carries the supervision-unit-unresolved token" "reason=supervision-unit-unresolved" "$PF_J"
assert_contains "4b refusal cites never silent-degrade" "never silent-degrade" "$PF_J"
rm -rf "$PF_FIXT"

# 4c. preflight HAPPY path: resolvable unit → machine line + outcome line,
#     no refusal, DUAL_FIGHT check runs (unit dormant under stub → pass).
PF2_FIXT="$(mktemp -d -t supclass-pf2.XXXXXX)"
mkdir -p "$PF2_FIXT/releases"
cat > "$PF2_FIXT/releases/state.json" <<'JOURNAL'
{"current":null,"previous":null,"in_flight":null,"rollback_window_count":{"24h":0,"window_start":null},"cooldown_until":null,"quarantined":[],"history":[]}
JOURNAL
SC_STUB="$(mktemp -d -t supclass-sc.XXXXXX)"
cat > "$SC_STUB/systemctl" <<'STUB'
#!/bin/bash
# dormant-unit stub: is-active → inactive, show → empty
case "$1" in
    is-active) echo "inactive" ;;
    show) echo "" ;;
    *) exit 0 ;;
esac
STUB
chmod +x "$SC_STUB/systemctl"
PF2_OUT="$(
    (
        export INSTALL_DIR="$PF2_FIXT"
        export ENSEMBLE_SUPERVISION=unit
        export ENSEMBLE_RESTART_UNIT=ensemble-happy.service
        export SYSTEMCTL_BIN="$SC_STUB/systemctl"
        . "$UPGRADE_DIR/lib.sh" >/dev/null 2>&1
        _supervision_owned_pids() { return 0; }
        supervision_preflight
        echo "pf-rc=$?"
    ) 2>&1
)"
assert_contains "4c happy: machine line with unit" "ENSEMBLE_SUPERVISION_RESULT=UNIT_MANAGED:ensemble-happy.service" "$PF2_OUT"
assert_contains "4c happy: outcome line conforming" "ENSEMBLE_SUPERVISION_OUTCOME=conforming" "$PF2_OUT"
assert_not_contains "4c happy: no refusal" "supervision-unit-unresolved" "$PF2_OUT"
case "$PF2_OUT" in
    *pf-rc=0*) _pass "4c happy: rc 0" ;;
    *) _fail "4c happy: rc 0" "pf-rc=0" "$(printf '%s' "$PF2_OUT" | grep -o 'pf-rc=[0-9]*' || echo none)" ;;
esac
rm -rf "$PF2_FIXT" "$SC_STUB"

# 4d. M3 (review cycle 1): EXPLICIT unit on a unit-INCAPABLE host (Darwin
#     uname PATH-stub) with the name RESOLVED from env — the preflight
#     converts the classify rc into _refuse supervision-unit-incapable-host
#     + exit 78 (its own greppable token, distinct from
#     supervision-unit-unresolved); the refusal is journaled.
PF3_FIXT="$(mktemp -d -t supclass-pf3.XXXXXX)"
mkdir -p "$PF3_FIXT/releases"
cat > "$PF3_FIXT/releases/state.json" <<'JOURNAL'
{"current":null,"previous":null,"in_flight":null,"rollback_window_count":{"24h":0,"window_start":null},"cooldown_until":null,"quarantined":[],"history":[]}
JOURNAL
PF3_DARWIN="$(mktemp -d -t supclass-pf3darwin.XXXXXX)"
cat > "$PF3_DARWIN/uname" <<STUB
#!/bin/bash
case "\$1" in
    -s) echo "Darwin" ;;
    *) "$REAL_UNAME" "\$@" ;;
esac
STUB
chmod +x "$PF3_DARWIN/uname"
PF3_OUT="$(
    (
        export INSTALL_DIR="$PF3_FIXT"
        export ENSEMBLE_SUPERVISION=unit
        export ENSEMBLE_RESTART_UNIT=ensemble-m3pf.service
        unset SYSTEMCTL_BIN 2>/dev/null || true
        # nested subshell so _refuse's exit 78 does not kill the harness;
        # lib.sh sourced INSIDE the PATH-stubbed shell so every uname call
        # the classifier makes (host guard + preflight) sees Darwin
        PATH="$PF3_DARWIN:$PATH" bash -c '
            . "'"$UPGRADE_DIR"'/lib.sh" >/dev/null 2>&1
            ( supervision_preflight )
            echo "pf-rc=$?"
        '
    ) 2>&1
)"
assert_contains "4d M3 refusal message names the host incapability" "this host cannot honor unit supervision" "$PF3_OUT"
assert_contains "4d M3 refusal cites never silent-degrade" "never silent-degrade" "$PF3_OUT"
case "$PF3_OUT" in
    *pf-rc=78*) _pass "4d M3 preflight exit 78" ;;
    *) _fail "4d M3 preflight exit 78" "pf-rc=78" "$(printf '%s' "$PF3_OUT" | grep -o 'pf-rc=[0-9]*' || echo none)" ;;
esac
assert_contains "4d machine line emitted pre-refusal (name carried)" "ENSEMBLE_SUPERVISION_RESULT=UNIT_MANAGED:ensemble-m3pf.service" "$PF3_OUT"
assert_not_contains "4d no stale outcome line on refusal" "ENSEMBLE_SUPERVISION_OUTCOME=" "$PF3_OUT"
PF3_J="$(cat "$PF3_FIXT/releases/state.json")"
assert_contains "4d refusal journaled" '"event":"refusal"' "$PF3_J"
assert_contains "4d refusal carries the supervision-unit-incapable-host token" "reason=supervision-unit-incapable-host" "$PF3_J"
assert_not_contains "4d NOT the unresolved token (distinct class)" "reason=supervision-unit-unresolved" "$PF3_J"
rm -rf "$PF3_FIXT" "$PF3_DARWIN"

# ===========================================================================
section "5 — non-Linux arm (Darwin uname stub): zero /proc reads attempted"

STUBDIR="$(mktemp -d -t supclass-darwin.XXXXXX)"
cat > "$STUBDIR/uname" <<STUB
#!/bin/bash
case "\$1" in
    -s) echo "Darwin" ;;
    *) "$REAL_UNAME" "\$@" ;;
esac
STUB
chmod +x "$STUBDIR/uname"
DAR_SCRIPT="$(mktemp -t supclass-darwin.XXXXXX)"
cat > "$DAR_SCRIPT" <<DARWIN
export INSTALL_DIR="$FIXT"
unset ENSEMBLE_SUPERVISION ENSEMBLE_RESTART_UNIT
. "$UPGRADE_DIR/lib.sh" >/dev/null 2>&1
_supervision_owned_pids()      { echo 'owned_pids' >> "$CALLLOG"; printf '%s\n' 424242; }
_supervision_pid_cgroup_leaf() { echo 'cgroup_leaf' >> "$CALLLOG"; printf '%s\n' 'ensemble-upgrade-x.scope'; }
: > "$CALLLOG"
supervision_classify
echo "rc-auto=\$?"
echo "STATE-auto=\$SUPERVISION_STATE"
ENSEMBLE_SUPERVISION=unit supervision_classify >/dev/null
echo "rc-unit=\$?"
echo "STATE-unit=\$SUPERVISION_STATE"
echo "CALLS-body:\$(cat "$CALLLOG")"
DARWIN
chmod +x "$DAR_SCRIPT"
: > "$CALLLOG"
DAR_OUT="$(PATH="$STUBDIR:$PATH" bash "$DAR_SCRIPT" 2>&1)"
assert_contains "5 Darwin auto: SCRIPT_NOHUP" "STATE-auto=SCRIPT_NOHUP" "$DAR_OUT"
assert_contains "5 Darwin auto rc 0" "rc-auto=0" "$DAR_OUT"
assert_contains "5 Darwin explicit-unit: no cgroup reads → unresolved rc 1" "rc-unit=1" "$DAR_OUT"
assert_contains "5 Darwin: ZERO /proc reads attempted (auto + unit arms)" "CALLS-body:" "$DAR_OUT"
assert_not_contains "5 Darwin: no owned_pids call" "owned_pids" "$DAR_OUT"
assert_not_contains "5 Darwin: no cgroup_leaf call" "cgroup_leaf" "$DAR_OUT"
rm -rf "$STUBDIR" "$DAR_SCRIPT"

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
