#!/bin/bash
# ============================================================================
# tests/test_adopt_unit.sh — pin tests for scripts/upgrade/adopt-unit.sh
# (supervision-detection P4 A3, tested in P5 — mission matrix item 14)
# ============================================================================
# Arms covered:
#   (a) REFUSED when any owned pid is alive (DUAL_FIGHT-mint prevention)
#   (b) REFUSED on non-Linux (Darwin uname stub) / no systemd run dir /
#       unresolvable systemctl
#   (c) REFUSED (🔴) without a pattern-restricted polkit manage-units rule
#       (dir absent / no rules file / rule not ensemble-restricted)
#   (d) REFUSED when the destination unit already exists (one-time
#       migration never clobbers); launcher missing; PORT unresolvable
#   naming derivation table (header rule) + explicit-name override +
#       invalid-name refusal
#   DRY_RUN=1 happy path — the generated unit CONTENT asserted line-by-
#       line (ExecStart=launcher.sh absolute, substituted User/port/PG,
#       every template directive, the %h-under-home rule, secret masking,
#       torn-token guard) + ZERO mutations (no unit file, .env untouched,
#       zero systemctl calls).
#
# Stub seams (the P4-designed operator/test surface): SYSTEMCTL_BIN,
# UNIT_DIR, POLKIT_RULES_DIR, ADOPT_SYSTEMD_RUN_DIR, ADOPT_USER, DRY_RUN,
# ENSEMBLE_UNIT_NAME; uname via PATH injection. The pid-alive arm uses a
# REAL anchored launcher process (tier 1a) — no /proc stubs needed.
#
# Lifted from the P4 /tmp smoke pattern (docs/runbooks/upgrade-drills.md
# DR-4; the P4 commit's manual harness) into a committed suite.
#
# Run:
#   bash tests/test_adopt_unit.sh
# ============================================================================

set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ADOPT="$REPO_ROOT/scripts/upgrade/adopt-unit.sh"
REAL_UNAME="$(command -v uname)"

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

# one fixture install per case: launcher + .env (+ optional extras)
# $1 = dir
make_install() {
    rm -rf "$1"; mkdir -p "$1"
    printf '#!/bin/bash\nsleep 300\n' > "$1/launcher.sh"
    chmod +x "$1/launcher.sh"
    printf 'PORT=18095\nPOSTGRES_HOST=10.44.0.2\nPOSTGRES_PORT=5432\nPOSTGRES_DB=ensemble_adopt\nPOSTGRES_USER=ens\nPOSTGRES_PASSWORD=sekret-adopt-pw\n' \
        > "$1/.env"
}

# a valid polkit rules dir (the §4 pattern-restricted rule)
make_polkit_ok() {
    local d="$1"; rm -rf "$d"; mkdir -p "$d"
    cat > "$d/50-ensemble-adopt.rules" <<'RULES'
polkit.addRule(function(action, subject) {
    if (action.id == "org.freedesktop.systemd1.manage-units" &&
        subject.isInGroup("ensemble")) {
        var unit = action.lookup("unit");
        if (unit && unit.match(/^ensemble-[0-9A-Za-z@._-]+\.service$/)) {
            return polkit.Result.YES;
        }
    }
    return polkit.Result.NOT_AUTHORIZED;
});
RULES
}

# systemctl stub that RECORDS calls (dry-run must produce ZERO)
make_sc_stub() {
    local d="$1"; mkdir -p "$d"
    cat > "$d/systemctl" <<'STUB'
#!/bin/bash
echo "$*" >> "${ADOPT_SC_LOG:-/dev/null}"
exit 0
STUB
    chmod +x "$d/systemctl"
}

WORK="$(mktemp -d -t adopt.XXXXXX)"
FIX="$WORK/install"
POLKIT="$WORK/polkit"
UNITDIR="$WORK/units"
RUND="$WORK/run-systemd"      # stands in for /run/systemd/system
SCBIN="$WORK/scbin"
mkdir -p "$UNITDIR" "$RUND"
make_polkit_ok "$POLKIT"
make_sc_stub "$SCBIN"
: > "$WORK/sc.log"

# run_adopt <fixture> <extra env assignments...> — prints stdout+stderr
run_adopt() {
    local fixt="$1"; shift
    local envs="$*"
    ( eval "export ${envs:-ADOPT_NOOP=1}" 2>/dev/null || true
      bash "$ADOPT" "$fixt" ${ADOPT_ARGS:-} 2>&1 )
    return 0
}

# ===========================================================================
section "(a) refusal — owned pid alive (DUAL_FIGHT-mint prevention)"

make_install "$FIX"
# stdout/stderr to /dev/null: an orphaned child must NEVER inherit the
# suite's output pipe (it would hold EOF hostage past suite exit)
bash "$FIX/launcher.sh" > /dev/null 2>&1 &
LIVE_PID=$!
# wait for the anchored pid to be VISIBLE (tier 1a must find it — a fixed
# sleep races slow machines)
for _ in $(seq 1 50); do
    pgrep -f "$FIX/launcher\.sh( |$)" > /dev/null 2>&1 && break
    sleep 0.1
done
A_OUT="$(ADOPT_LIVEZ_BUDGET_S=3 UNIT_DIR="$UNITDIR" POLKIT_RULES_DIR="$POLKIT" \
    ADOPT_SYSTEMD_RUN_DIR="$RUND" SYSTEMCTL_BIN="$SCBIN/systemctl" \
    bash "$ADOPT" "$FIX" 2>&1)"
A_RC=$?
kill -TERM "$LIVE_PID" 2>/dev/null || true
pkill -TERM -P "$LIVE_PID" 2>/dev/null || true   # the sleeper child too
wait "$LIVE_PID" 2>/dev/null || true
assert_eq "(a) pid alive: exit 78" "78" "$A_RC"
assert_contains "(a) cites DUAL_FIGHT minting" "mints DUAL_FIGHT" "$A_OUT"
assert_contains "(a) tells the operator to stop first" "Stop the install first" "$A_OUT"
assert_not_contains "(a) no unit generated" "generated unit" "$A_OUT"

# ===========================================================================
section "(b) refusals — host guards"

# b1: non-Linux (Darwin uname stub)
make_install "$FIX"
STUB_D="$(mktemp -d -t adopt-darwin.XXXXXX)"
cat > "$STUB_D/uname" <<STUB
#!/bin/bash
case "\$1" in
    -s) echo "Darwin" ;;
    *) "$REAL_UNAME" "\$@" ;;
esac
STUB
chmod +x "$STUB_D/uname"
B1_OUT="$(PATH="$STUB_D:$PATH" UNIT_DIR="$UNITDIR" POLKIT_RULES_DIR="$POLKIT" \
    ADOPT_SYSTEMD_RUN_DIR="$RUND" SYSTEMCTL_BIN="$SCBIN/systemctl" \
    bash "$ADOPT" "$FIX" 2>&1)"
B1_RC=$?
assert_eq "(b1) non-Linux: exit 78" "78" "$B1_RC"
assert_contains "(b1) cites launchd future scope" "launchd (documented FUTURE scope" "$B1_OUT"
rm -rf "$STUB_D"

# b2: ADOPT_SYSTEMD_RUN_DIR absent
UNIT_DIR="$UNITDIR" POLKIT_RULES_DIR="$POLKIT" \
    ADOPT_SYSTEMD_RUN_DIR="$WORK/no-such-run" SYSTEMCTL_BIN="$SCBIN/systemctl" \
    bash "$ADOPT" "$FIX" > /dev/null 2>&1
assert_eq "(b2) no live systemd marker: exit 78" "78" "$?"

# b3: systemctl unresolvable
UNIT_DIR="$UNITDIR" POLKIT_RULES_DIR="$POLKIT" \
    ADOPT_SYSTEMD_RUN_DIR="$RUND" SYSTEMCTL_BIN="$WORK/no-such-systemctl" \
    bash "$ADOPT" "$FIX" > /dev/null 2>&1
assert_eq "(b3) unresolvable systemctl: exit 78" "78" "$?"

# ===========================================================================
section "(c) refusals — polkit prerequisite (🔴 adoption blocker)"

# c1: rules dir does not exist
C1_OUT="$(UNIT_DIR="$UNITDIR" POLKIT_RULES_DIR="$WORK/no-polkit" \
    ADOPT_SYSTEMD_RUN_DIR="$RUND" SYSTEMCTL_BIN="$SCBIN/systemctl" \
    bash "$ADOPT" "$FIX" 2>&1)"
assert_eq "(c1) polkit dir absent: exit 78" "78" "$?"
assert_contains "(c1) 🔴 POLKIT BLOCKER" "POLKIT BLOCKER" "$C1_OUT"

# c2: rules dir exists but no ensemble-restricted manage-units rule
EMPTY_POLKIT="$WORK/polkit-empty"; mkdir -p "$EMPTY_POLKIT"
printf 'polkit.addRule(function(a,s){ return polkit.Result.NO; });\n' \
    > "$EMPTY_POLKIT/60-other.rules"
C2_OUT="$(UNIT_DIR="$UNITDIR" POLKIT_RULES_DIR="$EMPTY_POLKIT" \
    ADOPT_SYSTEMD_RUN_DIR="$RUND" SYSTEMCTL_BIN="$SCBIN/systemctl" \
    bash "$ADOPT" "$FIX" 2>&1)"
assert_eq "(c2) no ensemble-scoped rule: exit 78" "78" "$?"
assert_contains "(c2) names the pattern requirement" "ensemble-*.service" "$C2_OUT"

# ===========================================================================
section "(d) refusals — destination / launcher / port"

make_install "$FIX"
# d1: destination unit already exists (derived name ensemble-install.service
#     from basename "install")
printf '[Unit]\nDescription=stale\n' > "$UNITDIR/ensemble-install.service"
D1_OUT="$(UNIT_DIR="$UNITDIR" POLKIT_RULES_DIR="$POLKIT" \
    ADOPT_SYSTEMD_RUN_DIR="$RUND" SYSTEMCTL_BIN="$SCBIN/systemctl" \
    DRY_RUN=1 bash "$ADOPT" "$FIX" 2>&1)"
assert_eq "(d1) dest exists: exit 78 (even in DRY_RUN — one-time never clobbers)" "78" "$?"
assert_contains "(d1) names the existing destination" "already exists" "$D1_OUT"
rm -f "$UNITDIR/ensemble-install.service"

# d2: launcher missing
make_install "$FIX"; rm -f "$FIX/launcher.sh"
D2_OUT="$(UNIT_DIR="$UNITDIR" POLKIT_RULES_DIR="$POLKIT" \
    ADOPT_SYSTEMD_RUN_DIR="$RUND" SYSTEMCTL_BIN="$SCBIN/systemctl" \
    DRY_RUN=1 bash "$ADOPT" "$FIX" 2>&1)"
assert_eq "(d2) launcher missing: exit 78" "78" "$?"
assert_contains "(d2) cites the unit-inert design" "launcher as ExecStart" "$D2_OUT"

# d3: PORT unresolvable (ADR-014)
make_install "$FIX"; printf 'POSTGRES_HOST=x\n' > "$FIX/.env"
D3_OUT="$(UNIT_DIR="$UNITDIR" POLKIT_RULES_DIR="$POLKIT" \
    ADOPT_SYSTEMD_RUN_DIR="$RUND" SYSTEMCTL_BIN="$SCBIN/systemctl" \
    DRY_RUN=1 bash "$ADOPT" "$FIX" 2>&1)"
assert_eq "(d3) no PORT: exit 78" "78" "$?"
assert_contains "(d3) cites ADR-014" "ADR-014" "$D3_OUT"

# ===========================================================================
section "naming derivation table (header rule)"

derive_name() { # <basename-of-fake-install-dir> → the derived unit name
    local base="$1"
    local d="$WORK/names/$base"
    mkdir -p "$(dirname "$d")"
    make_install "$d"
    UNIT_DIR="$UNITDIR" POLKIT_RULES_DIR="$POLKIT" ADOPT_SYSTEMD_RUN_DIR="$RUND" \
        SYSTEMCTL_BIN="$SCBIN/systemctl" DRY_RUN=1 \
        bash "$ADOPT" "$d" 2>&1 | grep -oE 'ensemble-[0-9A-Za-z@._-]+\.service' | head -1
}
assert_eq "agents-ensemble → ensemble-main.service" "ensemble-main.service" "$(derive_name agents-ensemble)"
assert_eq "agents-ensemble-demo → ensemble-demo.service" "ensemble-demo.service" "$(derive_name agents-ensemble-demo)"
assert_eq "ensemble-prod-eu → ensemble-prod-eu.service" "ensemble-prod-eu.service" "$(derive_name ensemble-prod-eu)"
assert_eq "bare 'ensemble' → ensemble-main.service" "ensemble-main.service" "$(derive_name ensemble)"
assert_eq "space sanitized to dash" "ensemble-ens-foo.service" "$(derive_name 'ens foo')"

# explicit name override (2nd argv) + validation
make_install "$FIX"
E1_OUT="$(UNIT_DIR="$UNITDIR" POLKIT_RULES_DIR="$POLKIT" ADOPT_SYSTEMD_RUN_DIR="$RUND" \
    SYSTEMCTL_BIN="$SCBIN/systemctl" DRY_RUN=1 bash "$ADOPT" "$FIX" ensemble-explicit.service 2>&1)"
assert_eq "explicit name override: exit 0" "0" "$?"
assert_contains "explicit name used in the preview" "ensemble-explicit.service" "$E1_OUT"

E2_OUT="$(UNIT_DIR="$UNITDIR" POLKIT_RULES_DIR="$POLKIT" ADOPT_SYSTEMD_RUN_DIR="$RUND" \
    SYSTEMCTL_BIN="$SCBIN/systemctl" DRY_RUN=1 bash "$ADOPT" "$FIX" other-unit.service 2>&1)"
assert_eq "invalid explicit name (pattern): exit 78" "78" "$?"
assert_contains "invalid name cites the polkit pattern" "does not match the polkit pattern" "$E2_OUT"

E3_OUT="$(UNIT_DIR="$UNITDIR" POLKIT_RULES_DIR="$POLKIT" ADOPT_SYSTEMD_RUN_DIR="$RUND" \
    SYSTEMCTL_BIN="$SCBIN/systemctl" DRY_RUN=1 ENSEMBLE_UNIT_NAME=ensemble-envname.service \
    bash "$ADOPT" "$FIX" 2>&1)"
assert_contains "ENSEMBLE_UNIT_NAME env override used" "ensemble-envname.service" "$E3_OUT"

# ===========================================================================
section "DRY_RUN happy path — generated unit content + zero mutations"

make_install "$FIX"
: > "$WORK/sc.log"
H_OUT="$(ADOPT_SC_LOG="$WORK/sc.log" UNIT_DIR="$UNITDIR" POLKIT_RULES_DIR="$POLKIT" \
    ADOPT_SYSTEMD_RUN_DIR="$RUND" SYSTEMCTL_BIN="$SCBIN/systemctl" \
    ADOPT_USER=ens-adm DRY_RUN=1 bash "$ADOPT" "$FIX" 2>&1)"
H_RC=$?
assert_eq "DRY_RUN happy: exit 0" "0" "$H_RC"

# substituted values
assert_contains "ExecStart is the ABSOLUTE fixture launcher" "ExecStart=$FIX/launcher.sh" "$H_OUT"
assert_contains "User substituted" "User=ens-adm" "$H_OUT"
assert_contains "WorkingDirectory substituted (absolute, non-HOME fixture)" "WorkingDirectory=$FIX" "$H_OUT"
assert_contains "Environment=PORT substituted" "Environment=PORT=18095" "$H_OUT"
assert_contains "Environment=POSTGRES_HOST substituted" "Environment=POSTGRES_HOST=10.44.0.2" "$H_OUT"
assert_contains "Environment=POSTGRES_DB substituted" "Environment=POSTGRES_DB=ensemble_adopt" "$H_OUT"

# every template directive rides through
for directive in "Type=simple" "Restart=on-failure" "RestartSec=10" \
                 "RestartPreventExitStatus=78" "SuccessExitStatus=143" \
                 "KillMode=mixed" "StandardOutput=journal" "StandardError=journal" \
                 "TimeoutStopSec=90" "WantedBy=multi-user.target"; do
    assert_contains "directive $directive present" "$directive" "$H_OUT"
done
assert_not_contains "NO WatchdogSec (deferred by design)" "WatchdogSec=" "$H_OUT"
assert_not_contains "torn-token guard: no __TOKEN__ survived" "__" "$H_OUT"

# secret masking in the DRY_RUN preview (value never echoed)
assert_contains "POSTGRES_PASSWORD masked in the preview" "Environment=POSTGRES_PASSWORD=***masked***" "$H_OUT"
assert_not_contains "real password NEVER echoed" "sekret-adopt-pw" "$H_OUT"
assert_contains "preview announces the masked section" "generated unit (secret env value masked)" "$H_OUT"

# zero mutations
assert_eq "no unit file installed under DRY_RUN" "0" "$(find "$UNITDIR" -type f | wc -l | tr -d ' ')"
assert_not_contains ".env untouched by DRY_RUN (no staging)" "ENSEMBLE_RESTART_UNIT" "$(cat "$FIX/.env")"
assert_eq "ZERO systemctl calls under DRY_RUN" "" "$(cat "$WORK/sc.log")"

# ===========================================================================
section "%h-under-home rule (WorkingDirectory relativizes under \$HOME)"

HOMEFIX="$WORK/home-root/install-under-home"
mkdir -p "$(dirname "$HOMEFIX")"
make_install "$HOMEFIX"
HH_OUT="$(HOME="$WORK/home-root" UNIT_DIR="$UNITDIR" POLKIT_RULES_DIR="$POLKIT" \
    ADOPT_SYSTEMD_RUN_DIR="$RUND" SYSTEMCTL_BIN="$SCBIN/systemctl" \
    ADOPT_USER=ens-adm DRY_RUN=1 bash "$ADOPT" "$HOMEFIX" 2>&1)"
assert_contains "WorkingDirectory relativized to %h under HOME" "WorkingDirectory=%h/install-under-home" "$HH_OUT"
assert_contains "ExecStart stays ABSOLUTE even under HOME" "ExecStart=$HOMEFIX/launcher.sh" "$HH_OUT"

# ===========================================================================
section "PG quoting arm (values outside the bare charset get systemd-quoted)"

make_install "$FIX"
printf 'PORT=18095\nPOSTGRES_HOST=host with space\nPOSTGRES_PASSWORD=p@ss word!\n' > "$FIX/.env"
Q_OUT="$(UNIT_DIR="$UNITDIR" POLKIT_RULES_DIR="$POLKIT" ADOPT_SYSTEMD_RUN_DIR="$RUND" \
    SYSTEMCTL_BIN="$SCBIN/systemctl" DRY_RUN=1 bash "$ADOPT" "$FIX" 2>&1)"
assert_contains "bare-charset value stays unquoted" "Environment=PORT=18095" "$Q_OUT"
assert_contains "space-containing value single-quoted" "Environment=POSTGRES_HOST='host with space'" "$Q_OUT"
assert_contains "masking still applies to the quoted secret" "Environment=POSTGRES_PASSWORD=***masked***" "$Q_OUT"

rm -rf "$WORK"

# ===========================================================================
section "summary"
printf 'PASS=%s FAIL=%s\n' "$PASS" "$FAIL"
if [ "$FAIL" -gt 0 ]; then
    printf 'FAILED TESTS:%s\n' "$FAILED_TESTS"
    exit 1
fi
printf '\n=== ALL TESTS PASSED ===\n'
