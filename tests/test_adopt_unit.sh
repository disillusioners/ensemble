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
#       line (ExecStart=launcher.sh absolute, substituted User/label,
#       every template directive, the %h-under-home rule, torn-token
#       guard) + the ZERO-ENV CONTRACT (NO .env-derived Environment=
#       lines — zero-env pins, mutation-verified below) + ZERO mutations
#       (no unit file, .env untouched, zero systemctl calls).
#
# PIN DISCLOSURE (honest-skip discipline; §19 NAMED-SKIP precedent
# tests/test_release_journal.sh:2474-2517):
#   - The REAL-systemd mutation path (a live unit actually carrying
#     minted Environment= lines) is a NAMED SKIP on this fence — it needs
#     the systemd substrate. Mutation coverage lives at the generator/
#     DRY_RUN level: the template-copy injection leg below (plant
#     Environment=POSTGRES_PASSWORD=<clearly-marked DUMMY> in a
#     THROWAWAY template copy under $WORK → zero-env pins RED → restore
#     → GREEN). The repo template is NEVER mutated.
#   - The 4-rung env-precedence contract (.env > unit Environment=
#     [EMPTY BY DESIGN — zero-env contract] > systemctl set-environment
#     > code defaults) is DOC-PINNED
#     (docs/runbooks/systemd-adoption.md §"Env sourcing & precedence") —
#     not executable-pinned here.
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

# ---------------------------------------------------------------------------
# Layer-2 internal watchdog (test-pack skill: dual-layer timeout). The suite
# normally finishes in ~30s; 240s self-interrupt guards a hung arm (e.g. the
# tier-1a real-launcher wait). Outer `timeout 300` remains layer 1.
_adopt_test_on_timeout() {
    printf 'RESULT: TIMEOUT (internal %ss watchdog)\n' "$_ADOPT_TEST_MAX_S" >&2
    exit 124
}
_ADOPT_TEST_MAX_S=240
trap _adopt_test_on_timeout TERM
( sleep "$_ADOPT_TEST_MAX_S" && kill -TERM "$$" ) 2>/dev/null &
_ADOPT_WATCHDOG=$!
trap 'kill "$_ADOPT_WATCHDOG" 2>/dev/null; wait "$_ADOPT_WATCHDOG" 2>/dev/null' EXIT
# ---------------------------------------------------------------------------

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

# Strip pure-comment lines from a blob (lines whose first non-whitespace
# character is '#'). Preserves byte content of every other line. Used by
# the MALLOC_ARENA_MAX pins because the template header rationale comment
# (scripts/systemd/ensemble-daemon.service:59) names the directive by the
# same literal string — without stripping, a substring match on the
# directive would also match the comment and the pin would pass even when
# the real [Service] directive (line 106) had been deleted.
_strip_comments() {
    printf '%s\n' "$1" | sed '/^[[:space:]]*#/d'
}

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

# r3 mirror pin (supervisor war r3, 2026-09-30): scripts/upgrade/lib.sh
# _supervision_derive_unit_name — the nohup-survivor heal's last-resort
# name rung — is a PINNED MIRROR of the adopt-unit.sh naming rule above.
# Same table, cross-pinned: drift in EITHER function fails its own table,
# so the two can never silently diverge (the mirror is not sourced from
# here — adopt-unit.sh is an entry script; same pin discipline as
# lib.sh's _supervision_owned_pids tier mirror).
libsh_derive() { # <basename-of-fake-install-dir> → lib.sh derived unit name
    INSTALL_DIR="$WORK/names/$1" bash -c '
        . "'"$REPO_ROOT"'/scripts/upgrade/lib.sh" >/dev/null 2>&1
        _supervision_derive_unit_name
    '
}
assert_eq "r3 mirror: agents-ensemble → ensemble-main.service" "ensemble-main.service" "$(libsh_derive agents-ensemble)"
assert_eq "r3 mirror: agents-ensemble-demo → ensemble-demo.service" "ensemble-demo.service" "$(libsh_derive agents-ensemble-demo)"
assert_eq "r3 mirror: ensemble-prod-eu → ensemble-prod-eu.service" "ensemble-prod-eu.service" "$(libsh_derive ensemble-prod-eu)"
assert_eq "r3 mirror: bare 'ensemble' → ensemble-main.service" "ensemble-main.service" "$(libsh_derive ensemble)"
assert_eq "r3 mirror: space sanitized to dash" "ensemble-ens-foo.service" "$(libsh_derive 'ens foo')"

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

# static allocator cap (resource-cost-control O1): the TEMPLATE carries it
# (a content pin on the repo file itself — survives even if adoption of a
# given install predates this directive), and adoption rides it through to
# every generated unit via the directive loop below.
#
# DIRECTIVE-SPECIFIC pin: the template's header rationale comment at
# scripts/systemd/ensemble-daemon.service:59 also names the literal
# ``Environment=MALLOC_ARENA_MAX=2`` (it documents the directive). A raw
# substring match would therefore pass via the comment alone, letting a
# deleted real [Service] directive slip past. The matched haystack is
# stripped of pure-comment lines first so the needle can only land on a
# real directive line.
assert_contains "template carries static MALLOC_ARENA_MAX cap as a real directive" \
    "Environment=MALLOC_ARENA_MAX=2" \
    "$(_strip_comments "$(cat "$REPO_ROOT/scripts/systemd/ensemble-daemon.service")")"

# substituted values
assert_contains "ExecStart is the ABSOLUTE fixture launcher" "ExecStart=$FIX/launcher.sh" "$H_OUT"
assert_contains "User substituted" "User=ens-adm" "$H_OUT"
assert_contains "WorkingDirectory substituted (absolute, non-HOME fixture)" "WorkingDirectory=$FIX" "$H_OUT"

# ZERO-ENV CONTRACT pins (unit-secret-exposure): the generated unit carries
# NO .env-derived Environment= lines. The fixture .env carries PORT + all
# five POSTGRES_* keys (maximal input), so these negatives run against the
# worst case. `Environment=POSTGRES_` covers the HOST/DB/USER/PASSWORD
# variants in one needle; `__ENV_LINES__` proves the token did not survive.
assert_not_contains "zero-env: no minted PORT env line" "Environment=PORT=" "$H_OUT"
assert_not_contains "zero-env: no minted POSTGRES_* env lines" "Environment=POSTGRES_" "$H_OUT"
assert_not_contains "zero-env: no __ENV_LINES__ token survived" "__ENV_LINES__" "$H_OUT"

# every template directive rides through. MALLOC_ARENA_MAX is checked
# separately BELOW with a comment-stripped haystack — see that pin's note
# for the rationale (the header also names it by string, which would let
# the directive loop's substring match pass against the comment alone).
for directive in "Type=simple" "Restart=on-failure" "RestartSec=10" \
                 "RestartPreventExitStatus=78" "SuccessExitStatus=143" \
                 "KillMode=mixed" "StandardOutput=journal" "StandardError=journal" \
                 "TimeoutStopSec=90" "WantedBy=multi-user.target"; do
    assert_contains "directive $directive present" "$directive" "$H_OUT"
done
# MALLOC_ARENA_MAX — directive-specific pin (see _strip_comments above).
# The real [Service] directive rode through from the template; matching
# the comment-stripped haystack ensures the needle can only land on a
# real directive line, not on the header rationale.
assert_contains "directive Environment=MALLOC_ARENA_MAX=2 present (directive-specific)" \
    "Environment=MALLOC_ARENA_MAX=2" \
    "$(_strip_comments "$H_OUT")"
assert_not_contains "NO WatchdogSec (deferred by design)" "WatchdogSec=" "$H_OUT"
assert_not_contains "torn-token guard: no __TOKEN__ survived" "__" "$H_OUT"

# zero-env DRY_RUN preview (unit-secret-exposure): nothing secret remains
# to mask — the preview prints the unit raw under the neutral banner
assert_not_contains "no masked-section banner (masking removed with the minting)" "secret env value masked" "$H_OUT"
assert_not_contains "real password NEVER echoed" "sekret-adopt-pw" "$H_OUT"
assert_contains "preview announces the zero-env section" "generated unit (zero-env contract)" "$H_OUT"

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
section "MUTATION LEG — zero-env pins are mutation-meaningful (house rule)"
# Vehicle: THROWAWAY copies of the generator + template under $WORK
# (never the repo files — the repo template is byte-immutable here).
# _resolve_template resolves SELF_DIR-relative, so BOTH files must be
# copied into the fence for the copy to self-contain.
MUT="$WORK/mut"
mkdir -p "$MUT/scripts/upgrade" "$MUT/scripts/systemd"
cp "$REPO_ROOT/scripts/upgrade/adopt-unit.sh" "$REPO_ROOT/scripts/upgrade/lib.sh" "$MUT/scripts/upgrade/"
cp "$REPO_ROOT/scripts/systemd/ensemble-daemon.service" "$MUT/scripts/systemd/"

mut_run() { # DRY_RUN against the FENCED copy of the generator
    UNIT_DIR="$UNITDIR" POLKIT_RULES_DIR="$POLKIT" ADOPT_SYSTEMD_RUN_DIR="$RUND" \
        SYSTEMCTL_BIN="$SCBIN/systemctl" DRY_RUN=1 \
        bash "$MUT/scripts/upgrade/adopt-unit.sh" "$FIX" 2>&1
}
# the RED detector = the exact needle the zero-env pins assert on
mut_goes_red() { printf '%s' "$1" | grep -qF 'Environment=POSTGRES_'; }

# leg 1 (GREEN baseline): the fenced copy behaves like the repo generator
make_install "$FIX"
MUT_G_OUT="$(mut_run)"
MUT_G_RC=$?
assert_eq "mutation baseline (GREEN): fenced copy exit 0" "0" "$MUT_G_RC"
if mut_goes_red "$MUT_G_OUT"; then
    _fail "mutation baseline (GREEN)" "no minted env on the unmutated copy" "pin went RED"
else
    _pass "mutation baseline (GREEN): zero-env pin holds on the unmutated copy"
fi

# leg 2 (RED): plant a minted env line in the TEMPLATE COPY → the pin MUST
# go RED. (Not a __TOKEN__, so the torn-token guard correctly does not
# fire — this proves the pins detect minted CONTENT, not merely tokens.)
printf 'Environment=POSTGRES_PASSWORD=DUMMY_MUTATION_PW_NOT_A_SECRET\n' \
    >> "$MUT/scripts/systemd/ensemble-daemon.service"
MUT_R_OUT="$(mut_run)"
MUT_R_RC=$?
assert_eq "mutation (RED): fenced copy still completes generation" "0" "$MUT_R_RC"
if mut_goes_red "$MUT_R_OUT"; then
    _pass "mutation (RED): planted minted line DETECTED by the zero-env pin"
else
    _fail "mutation (RED)" "pin RED on planted minted line" "pin stayed GREEN (mutation-dead)"
fi

# leg 3 (restore → GREEN): restore the pristine template copy
cp "$REPO_ROOT/scripts/systemd/ensemble-daemon.service" "$MUT/scripts/systemd/"
MUT_G2_OUT="$(mut_run)"
if mut_goes_red "$MUT_G2_OUT"; then
    _fail "mutation restore (GREEN)" "pin recovered after restore" "pin still RED"
else
    _pass "mutation restore (GREEN): zero-env pin recovered after restore"
fi
rm -rf "$MUT"

# ===========================================================================
section "MODE PINS — every .env/.env.prod.demo write site leaves mode 0600 (P5)"
# Static byte-adjacency pins: a `chmod 600` must appear within 3 lines
# AFTER each of the 5 write sites (deploy.sh ×2, stage.sh ×3). The
# stage.sh chmod is LOAD-BEARING (the marker rewrite's `mv -f` re-creates
# the inode at the default umask and would silently reset any prior 600).
# These pins run against the REPO files (read-only) — mutation coverage
# comes from the W5 leg below on THROWAWAY copies.

_pin_deploy="$REPO_ROOT/scripts/deploy.sh"
_pin_stage="$REPO_ROOT/scripts/upgrade/stage.sh"

adjacent_chmod() { # <file> <fixed-string anchor on the write line> → rc 0 iff chmod 600 within +1..+3
    local f="$1" anchor="$2" n
    n="$(grep -nF -- "$anchor" "$f" | head -1 | cut -d: -f1)"
    [ -n "$n" ] || return 1
    sed -n "$((n + 1)),$((n + 3))p" "$f" | grep -q 'chmod 600'
}
assert_adjacent_chmod() { # <name> <file> <anchor>
    if adjacent_chmod "$2" "$3"; then
        _pass "$1"
    else
        _fail "$1" "chmod 600 within 3 lines after the write" "not found"
    fi
}

assert_adjacent_chmod "mode pin: deploy.sh installed-copy cp → chmod 600" \
    "$_pin_deploy" 'cp "$ENV_SOURCE" "$INSTALL_DIR/.env"'
assert_adjacent_chmod "mode pin: deploy.sh demo-guard generate-mv → chmod 600" \
    "$_pin_deploy" 'mv -f "$ENV_TMP" "$ENV_SOURCE"'
assert_adjacent_chmod "mode pin: stage.sh marker rewrite-mv → chmod 600" \
    "$_pin_stage" 'failed to update $ENV_FILE marker'
assert_adjacent_chmod "mode pin: stage.sh marker append-mv → chmod 600" \
    "$_pin_stage" 'failed to append marker to $ENV_FILE'
assert_adjacent_chmod "mode pin: stage.sh create path → chmod 600" \
    "$_pin_stage" 'failed to create $ENV_FILE'

# W5 mutation leg (house rule — pins MUST be mutation-verified): remove the
# create-path chmod in a THROWAWAY stage.sh copy → the create-site pin goes
# RED → restore → GREEN. Repo files never mutated; the 4-space indent
# matches ONLY the create-path chmod (the two mv-path chmods indent 8).
MUT_PIN="$WORK/mut-pin"
mkdir -p "$MUT_PIN"
cp "$_pin_stage" "$MUT_PIN/stage.sh"

assert_adjacent_chmod "mode-pin mutation baseline (GREEN): stage copy create-site pinned" \
    "$MUT_PIN/stage.sh" 'failed to create $ENV_FILE'

sed -i '/^    chmod 600 "$ENV_FILE" || { _warn "failed to chmod 600 \$ENV_FILE"; exit 1; }/d' \
    "$MUT_PIN/stage.sh"
if adjacent_chmod "$MUT_PIN/stage.sh" 'failed to create $ENV_FILE'; then
    _fail "mode-pin mutation (RED)" "create-site pin RED after chmod removal" "pin stayed GREEN (mutation-dead)"
else
    _pass "mode-pin mutation (RED): create-site chmod removed → pin RED"
fi

cp "$_pin_stage" "$MUT_PIN/stage.sh"
assert_adjacent_chmod "mode-pin mutation restore (GREEN): create-site pin recovered" \
    "$MUT_PIN/stage.sh" 'failed to create $ENV_FILE'
rm -rf "$MUT_PIN"

# BEHAVIORAL-PIN DISCLOSURE (D5b) — accurate wording: the existing
# FAKE_REPO harness in tests/test_stage_freshness_guard.sh (real stage.sh
# under a FAKE_REPO fence, one reused sandbox INSTALL_DIR across its
# scenarios) REACHES the marker-rewrite path, and the chmod is exercised
# there FAIL-LOUD: a chmod failure makes stage.sh exit 1, which reddens
# that harness's exit-code assertions.
# WHAT THAT HARNESS DOES NOT DO: it has NO mode assertion on .env (its
# only .env touch is creating its own fixture at :89) — so mode-600
# itself is NOT covered there. Mode-600 coverage lives in THIS pack: the
# static byte-adjacency pins (above) + the W5 mutation leg (RED/GREEN).

rm -rf "$WORK"

# ===========================================================================
section "summary"
printf 'PASS=%s FAIL=%s\n' "$PASS" "$FAIL"
if [ "$FAIL" -gt 0 ]; then
    printf 'FAILED TESTS:%s\n' "$FAILED_TESTS"
    exit 1
fi
printf '\n=== ALL TESTS PASSED ===\n'
