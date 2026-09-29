#!/bin/bash
# ============================================================================
# tests/test_supervision_e2e.sh — REAL-SEAT E2E legs (supervision-detection
# P5; the DR-4 leg (e) procedure from docs/runbooks/upgrade-drills.md)
# ============================================================================
# Legs (fenced, counted-SKIP never FAIL; fences NAMED):
#
#   E1 BOOT UNDER A REAL SERVICE — a systemd SERVICE unit (unit FILE under
#      ~/.config/systemd/user/, the user manager — NOT a scope) runs the
#      REAL launcher.sh from a FIXTURE install; assert INVOCATION_ID
#      present (unit-level + in the daemon's environ), livez serving, and
#      the boot journal `supervision_boot` advisory = UNIT_MANAGED:<unit>
#      outcome=conforming (the REAL daemon-side python twin classifying
#      its own cgroup).
#   E2 REAL PROMOTE UNDER THE UNIT — a full promote.sh run on the fixture
#      release ladder with SYSTEMCTL_BIN pointed at a wrapper that execs
#      `systemctl --user` (the sanctioned P2/P3 stub seam carrying REAL
#      systemd semantics): preflight classifies UNIT_MANAGED:<unit> from
#      the REAL cgroup (no stubs in the classifier), stop goes through
#      the P2 unit branch (real systemctl stop + unit-state poll), the
#      hand-back STARTS the unit for real and verifies a NEW MainPID ≠
#      pre-stop + port serving, gates pass, journal commits — and the
#      post-promote daemon is still inside the unit's cgroup.
#   E3 SURVIVOR-HEAL — the daemon runs as a scope SURVIVOR
#      (systemd-run --user --scope --unit=ensemble-upgrade-e2e-<ts>.scope,
#      today's live shape) with the unit configured: the promote's
#      hand-back SELF-HEALS scope→unit (+ `supervision_handback` journal
#      event, outcome=degraded) and the daemon lands inside the unit.
#   E4 DUAL_FIGHT REFUSAL DRILL — unit ACTIVE + a foreign owned pid
#      outside the unit's cgroup → the preflight halts PRE-TXN (exit 78,
#      halt journal event, no txn opened, no mutation).
#
# SAFETY (absolute): 9797 = live ensemble_prod, 7979 = demo — NEVER
# touched/probed/booted. Every leg uses a fixture INSTALL_DIR under /tmp,
# ephemeral 18079-family ports, a FAKE_HOME (sandbox target), and a
# scrubbed env (standalone-wrapper discipline: POSTGRES_*/PG*/
# DATABASE_URL/ENSEMBLE_UPGRADE_LIVE unset + echo-verified). Transient
# artifacts are named ensemble-e2e-<ts> ONLY. MANDATORY cleanup per leg
# (unit stop + unit-file removal + fixture removal) — the suite leaves
# the host clean and VERIFIES it.
#
# Run:
#   bash tests/test_supervision_e2e.sh
# ============================================================================

set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UPGRADE_DIR="$REPO_ROOT/scripts/upgrade"
STUB_DAEMON="$REPO_ROOT/tests/fixtures/supervision_stub_daemon.py"

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

# ── env scrub (standalone-wrapper discipline; this SUITE is the wrapper) ───
unset POSTGRES_URL POSTGRES_HOST POSTGRES_PORT POSTGRES_DB POSTGRES_USER \
      POSTGRES_PASSWORD POSTGRES_SSLMODE POSTGRES_SSL CERT_PATH_POSTGRES \
      DATABASE_URL ENSEMBLE_UPGRADE_LIVE ENSEMBLE_INSTALL_DIR 2>/dev/null || true
SCRUB_SURVIVORS="$(env | grep -iE '^(POSTGRES_|PG|DATABASE_URL=)' || true)"
if [ -n "$SCRUB_SURVIVORS" ]; then
    printf 'E2E ENV-SCRUB FAIL: survivors=%s\n' "$SCRUB_SURVIVORS" >&2
    exit 99
fi
_pass "env scrub: zero POSTGRES_*/DATABASE_URL survivors (echo-verified)"

# ── fences (NAMED; counted-SKIP never FAIL) ────────────────────────────────
XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
export XDG_RUNTIME_DIR

FENCE_REASON=""
if [ "$(uname -s)" != "Linux" ] || [ ! -d /run/systemd/system ]; then
    FENCE_REASON="FENCE: no /run/systemd/system (non-systemd host)"
else
    # reachability, not health: 'degraded' (rc 1) still proves the user
    # manager answers — only 'offline'/a dead socket fences the legs
    E2E_ISRUN="$(systemctl --user is-system-running 2>/dev/null || true)"
    case "$E2E_ISRUN" in
        running|degraded|starting|stopping) : ;;   # manager reachable
        *) FENCE_REASON="FENCE: user manager unreachable (is-system-running: '${E2E_ISRUN:-<no answer>}')" ;;
    esac
fi
if [ -z "$FENCE_REASON" ] \
   && ! systemd-run --user --scope --unit="ensemble-e2e-fenceprobe-$$.scope" true > /dev/null 2>&1; then
    FENCE_REASON="FENCE: systemd-run denied (user-bus scope probe refused)"
fi

if [ -n "$FENCE_REASON" ]; then
    _skip "ALL E2E legs — $FENCE_REASON"
    section "summary"
    printf 'PASS=%s FAIL=%s SKIP=%s\n' "$PASS" "$FAIL" "$SKIP"
    printf '\n=== E2E SUITE: %s passes, %s skips (fenced), 0 fails ===\n' "$PASS" "$SKIP"
    exit 0
fi
_pass "fences open: /run/systemd/system present + user manager + systemd-run probe OK"

# ── shared machinery ────────────────────────────────────────────────────────
WORK="$(mktemp -d -t sup-e2e.XXXXXX)"
E2E_UNITS=""          # unit FILES installed under ~/.config/systemd/user
E2E_FIXTURES=""       # fixture install dirs
TS="$(date -u +%Y%m%d-%H%M%S)"
PORT_BASE=18089       # 18079-family ephemeral

# systemctl wrapper: the sanctioned SYSTEMCTL_BIN seam carrying REAL
# user-manager systemd semantics
SC_WRAP="$WORK/systemctl-user"
printf '#!/bin/bash\nexec /usr/bin/systemctl --user "$@"\n' > "$SC_WRAP"
chmod +x "$SC_WRAP"

cleanup_e2e() {
    # MANDATORY per-leg cleanup: stop + remove every ensemble-e2e unit,
    # reset failed state, remove fixtures. NEVER touches anything else.
    for u in $E2E_UNITS; do
        "$SC_WRAP" stop "$u" > /dev/null 2>&1 || true
        "$SC_WRAP" reset-failed "$u" > /dev/null 2>&1 || true
        rm -f "$HOME/.config/systemd/user/$u"
    done
    # scope survivors from E3 (transient; stop if still around)
    for s in $("$SC_WRAP" list-units 'ensemble-upgrade-e2e-*.scope' --no-legend 2>/dev/null | awk '{print $1}'); do
        "$SC_WRAP" stop "$s" > /dev/null 2>&1 || true
    done
    "$SC_WRAP" daemon-reload > /dev/null 2>&1 || true
    # kill any fixture launcher/stub-daemon still running (fixture-anchored
    # patterns ONLY — never a port sweep, never a foreign process)
    pkill -TERM -f "supervision_stub_daemon.py --port $PORT_BASE" > /dev/null 2>&1 || true
    pkill -TERM -f "supervision_stub_daemon.py --port $((PORT_BASE + 2))" > /dev/null 2>&1 || true
    pkill -TERM -f "$WORK/" > /dev/null 2>&1 || true
    sleep 1
    pkill -KILL -f "supervision_stub_daemon.py --port $PORT_BASE" > /dev/null 2>&1 || true
    if [ -n "${SUP_E2E_KEEP:-}" ]; then
        printf 'SUP_E2E_KEEP set — WORK left at %s\n' "$WORK" >&2
        return 0
    fi
    for f in $E2E_FIXTURES; do rm -rf "$f"; done
    rm -rf "$WORK"
}
trap cleanup_e2e EXIT

install_unit_file() { # <unit-name> <fixture> <port>
    local name="$1" fixt="$2" port="$3"
    mkdir -p "$HOME/.config/systemd/user"
    cat > "$HOME/.config/systemd/user/$name" <<UNIT
[Unit]
Description=Ensemble E2E fixture — $name (supervision P5)
After=network-online.target

[Service]
Type=simple
WorkingDirectory=$fixt
Environment=PORT=$port
ExecStart=$fixt/launcher.sh
Restart=on-failure
RestartSec=2
RestartPreventExitStatus=78
SuccessExitStatus=143
KillMode=mixed
StandardOutput=journal
StandardError=journal
TimeoutStopSec=30

[Install]
WantedBy=default.target
UNIT
    E2E_UNITS="$E2E_UNITS $name"
    "$SC_WRAP" daemon-reload
}

# stub daemon binary: EXEC python directly (a launcher TERM must reach the
# server process — an intermediate bash layer would orphan it) and carry
# the anchored path via --anchor so the ownership tiers / the classifier's
# owned-pid discovery can see it (tier 1a: <FIX>/current/ensemble-prod)
make_stub_bin() { # <path> <version> <port> <fixture>
    {
        printf '#!/bin/bash\n'
        printf 'exec python3 "%s" --port "${PORT:-%s}" --version "%s" --install-dir "%s" --anchor "%s/current/ensemble-prod" --emit-advisory\n' \
            "$STUB_DAEMON" "$3" "$2" "$4" "$4"
    } > "$1"
    chmod +x "$1"
}

wait_port_free() { # <port> [budget] — poll until nothing holds the port
    local port="$1" budget="${2:-15}" i
    for i in $(seq 1 "$budget"); do
        lsof -ti:"$port" > /dev/null 2>&1 || return 0
        sleep 1
    done
    return 1
}

wait_livez() { # <port> [budget] — poll until /livez answers
    local port="$1" budget="${2:-20}" i
    for i in $(seq 1 "$budget"); do
        curl -fsS -m 2 "http://127.0.0.1:$port/livez" > /dev/null 2>&1 && return 0
        sleep 1
    done
    return 1
}

# fixture install + repo (staged ladder) — the J-suite precedent
make_fixture() { # <dir> [port] — sets up FIX + REPO + HOME + stages vA/vB
    local d="$1"
    local fport="${2:-$PORT_BASE}"
    local repo="$d/repo" home="$d/home" fix="$d/install" sbx="$d/sbx"
    mkdir -p "$repo/scripts/upgrade" "$repo/agents/leader" \
             "$repo/daemon/migrations/versions" \
             "$repo/frontend/dist/frontend/browser" \
             "$home/agents-ensemble" "$sbx" \
             "$fix/releases"
    cp "$UPGRADE_DIR/lib.sh"     "$repo/scripts/upgrade/lib.sh"
    cp "$UPGRADE_DIR/stage.sh"   "$repo/scripts/upgrade/stage.sh"
    cp "$UPGRADE_DIR/promote.sh" "$repo/scripts/upgrade/promote.sh"
    cp "$REPO_ROOT/scripts/stop-ensemble.sh" "$repo/scripts/stop-ensemble.sh"
    chmod +x "$repo/scripts/upgrade/"*.sh
    cp "$REPO_ROOT/launcher.sh" "$repo/launcher.sh"
    chmod +x "$repo/launcher.sh"
    printf 'stub-agent-definition\n' > "$repo/agents/leader/soul.md"
    printf 'port: ${PORT:-8088}\n' > "$repo/config.yaml"
    printf 'stub-index\n' > "$repo/frontend/dist/frontend/browser/index.html"
    printf 'stub-app\n' > "$repo/frontend/dist/frontend/browser/main.js"
    printf 'CREATE TABLE x (id int);\n' > "$repo/daemon/migrations/versions/20260101_000001_init.sql"
    git -C "$repo" init -q
    git -C "$repo" add -A 2>/dev/null
    git -C "$repo" -c user.email=t@t -c user.name=t commit -qm fixture > /dev/null 2>&1

    VA="v9.9.9-e2ea"; VB="v9.9.9-e2eb"
    local v b
    for v in "$VA" "$VB"; do
        case "$v" in
            *e2ea) b="9.9.9-e2ea" ;;
            *)     b="9.9.9-e2eb" ;;
        esac
        printf 'fixture-%s\n' "$v" > "$repo/FIX_$v"
        git -C "$repo" add -A 2>/dev/null
        git -C "$repo" -c user.email=t@t -c user.name=t commit -qm "f-$v" > /dev/null 2>&1
        git -C "$repo" tag "$v" 2>/dev/null
        # install-dir baked as the FIXTURE from the start — the staged
        # copy and the fixture copy stay byte-identical (integrity guard)
        make_stub_bin "$d/stub-$v" "$b" "$PORT_BASE" "$fix"
        (
            HOME="$home" VERSION="$v" TARGET=sandbox INSTALL_DIR="$sbx" \
            PORT="$PORT_BASE" ENSEMBLE_BINARY_VERSION="$b" \
            bash "$repo/scripts/upgrade/stage.sh" sandbox --skip-build "$d/stub-$v" \
                > "$d/stage-$v.log" 2>&1
        ) || { printf "E2E FATAL: stage %s failed\n" "$v" >&2; tail -3 "$d/stage-$v.log" >&2; return 1; }
    done
    # install-shaped: launcher + .env + current→vA + journal seeded
    cp "$REPO_ROOT/launcher.sh" "$fix/launcher.sh"
    chmod +x "$fix/launcher.sh"
    printf 'PORT=%s\n' "$fport" > "$fix/.env"
    mkdir -p "$fix/releases"
    cp -r "$sbx/releases/$VA" "$fix/releases/$VA"
    cp -r "$sbx/releases/$VB" "$fix/releases/$VB"
    printf '{"current":"%s","previous":null,"in_flight":null,"rollback_window_count":{"24h":0,"window_start":null},"cooldown_until":null,"quarantined":[],"history":[]}\n' "$VA" \
        > "$fix/releases/state.json"
    ln -sfn "releases/$VA" "$fix/current"
    E2E_FIXTURES="$E2E_FIXTURES $d"
    return 0
}

# ===========================================================================
section "E1 — boot under a REAL service unit (INVOCATION_ID + livez + advisory)"

E1D="$WORK/e1"
make_fixture "$E1D" || _fail "E1 fixture build"
E1FIX="$E1D/install"
U1="ensemble-e2e-$TS-e1.service"
install_unit_file "$U1" "$E1FIX" "$PORT_BASE"
"$SC_WRAP" start "$U1"
if wait_livez "$PORT_BASE" 20; then
    _pass "E1 livez serving under the unit"
else
    _fail "E1 livez serving under the unit" "200" "no answer within 20s"
fi
U1_STATE="$("$SC_WRAP" is-active "$U1" 2>/dev/null || true)"
assert_eq "E1 unit ACTIVE" "active" "$U1_STATE"
U1_MP="$("$SC_WRAP" show "$U1" -p MainPID --value 2>/dev/null || true)"
case "$U1_MP" in ''|0) _fail "E1 MainPID nonzero" ">0" "$U1_MP" ;; *) _pass "E1 MainPID nonzero ($U1_MP)" ;; esac
U1_INV="$("$SC_WRAP" show "$U1" -p InvocationID --value 2>/dev/null || true)"
case "$U1_INV" in ''|0) _fail "E1 unit InvocationID present" "non-empty" "$U1_INV" ;; *) _pass "E1 unit InvocationID present (${U1_INV:0:12}…)" ;; esac
# the DAEMON process environ carries INVOCATION_ID too (service env)
E1_DAEMON_PID="$(pgrep -f "supervision_stub_daemon.py --port $PORT_BASE" | head -1)"
if [ -n "$E1_DAEMON_PID" ] && [ -r "/proc/$E1_DAEMON_PID/environ" ]; then
    case "$(tr '\0' '\n' < "/proc/$E1_DAEMON_PID/environ" | grep -c '^INVOCATION_ID=' || true)" in
        1) _pass "E1 daemon environ carries INVOCATION_ID" ;;
        *) _fail "E1 daemon environ carries INVOCATION_ID" "1" "0" ;;
    esac
    E1_CG="$(tail -n1 "/proc/$E1_DAEMON_PID/cgroup" 2>/dev/null || true)"
    case "$E1_CG" in
        *"$U1"*) _pass "E1 daemon cgroup is INSIDE the unit ($E1_CG)" ;;
        *) _fail "E1 daemon cgroup inside unit" "*$U1*" "$E1_CG" ;;
    esac
else
    _fail "E1 daemon pid found + environ readable" "pid" "none"
fi
# the REAL python twin's boot advisory
sleep 1
E1J="$E1FIX/releases/state.json"
if [ -f "$E1J" ]; then
    E1_ADV="$(python3 -c "
import json
d = json.load(open('$E1J'))
evs = [h for h in d.get('history', []) if h.get('event') == 'supervision_boot']
print(evs[-1]['detail'] if evs else 'NONE')" 2>/dev/null || echo NONE)"
    assert_contains "E1 boot advisory: UNIT_MANAGED + unit name" "state=UNIT_MANAGED mode=unit unit=$U1" "$E1_ADV"
    assert_contains "E1 boot advisory: outcome conforming" "outcome=conforming" "$E1_ADV"
else
    _fail "E1 fixture journal exists" "file" "missing"
fi
"$SC_WRAP" stop "$U1"
wait_port_free "$PORT_BASE" 15 || _fail "E1→E2 port handoff: port $PORT_BASE freed" "free" "still held"

# ===========================================================================
section "E2 — real promote under the unit (classify→unit-stop→hand-back→commit)"

# re-seed the fixture journal to a clean promote start (E1's advisory
# history is fine to keep; in_flight must be null and current=vA)
E2FIX="$E1FIX"
VA="v9.9.9-e2ea"; VB="v9.9.9-e2eb"
python3 - "$E2FIX/releases/state.json" <<'PY'
import json, sys
p = sys.argv[1]
d = json.load(open(p))
d["in_flight"] = None
d["current"] = "v9.9.9-e2ea"
json.dump(d, open(p, "w"))
PY
ln -sfn "releases/$VA" "$E2FIX/current"

# daemon live under the unit
"$SC_WRAP" start "$U1"
wait_livez "$PORT_BASE" 20 || _fail "E2 daemon up pre-promote" "livez" "no answer"
PRESTOP_MP="$("$SC_WRAP" show "$U1" -p MainPID --value 2>/dev/null || true)"

E2_LOG="$WORK/e2-promote.log"
(
    HOME="$E1D/home" VERSION="$VB" TARGET=sandbox \
    INSTALL_DIR="$E2FIX" PORT="$PORT_BASE" \
    ENSEMBLE_RESTART_UNIT="$U1" \
    SYSTEMCTL_BIN="$SC_WRAP" \
    LIVEZ_BUDGET_S=20 READYZ_BUDGET_S=20 ENSEMBLE_PROMOTE_SOAK_S=0 \
    XDG_RUNTIME_DIR="$XDG_RUNTIME_DIR" \
    bash "$E1D/repo/scripts/upgrade/promote.sh" sandbox
) > "$E2_LOG" 2>&1
E2_RC=$?
E2_OUT="$(cat "$E2_LOG")"

assert_eq "E2 promote exits 0 (committed)" "0" "$E2_RC"
assert_contains "E2 preflight machine line: UNIT_MANAGED from the REAL cgroup" "ENSEMBLE_SUPERVISION_RESULT=UNIT_MANAGED:$U1" "$E2_OUT"
assert_contains "E2 outcome line conforming" "ENSEMBLE_SUPERVISION_OUTCOME=conforming" "$E2_OUT"
assert_contains "E2 stop routed through the UNIT branch (b″)" "stop: UNIT path — systemctl stop $U1 + unit-state poll" "$E2_OUT"
assert_contains "E2 hand-back: the flipped release returns to the unit" "hand-back: UNIT_MANAGED — the flipped release returns to unit $U1" "$E2_OUT"
assert_contains "E2 hand-back COMPLETE (new MainPID + prestop)" "unit hand-back COMPLETE: $U1 serves the flipped release" "$E2_OUT"
assert_not_contains "E2 7b trap: success path clean of the literal" "systemctl start" "$(printf '%s' "$E2_OUT" | grep -v 'falling back\|failed\|FAILED\|did not come up' || true)"
assert_not_contains "E2 NO nohup on the unit path" "launcher started (nohup)" "$E2_OUT"

# post-promote: still ACTIVE, NEW MainPID lineage, daemon inside the unit
U2_STATE="$("$SC_WRAP" is-active "$U1" 2>/dev/null || true)"
assert_eq "E2 post-promote unit still ACTIVE" "active" "$U2_STATE"
POST_MP="$("$SC_WRAP" show "$U1" -p MainPID --value 2>/dev/null || true)"
case "$POST_MP" in
    "$PRESTOP_MP"|''|0) _fail "E2 post-promote MainPID is NEW (≠ pre-stop $PRESTOP_MP)" "≠$PRESTOP_MP" "$POST_MP" ;;
    *) _pass "E2 post-promote MainPID is NEW ($PRESTOP_MP → $POST_MP)" ;;
esac
wait_livez "$PORT_BASE" 20 && _pass "E2 post-promote livez serving" \
    || _fail "E2 post-promote livez serving" "200" "no answer"
E2_DAEMON_PID="$(pgrep -f "supervision_stub_daemon.py --port $PORT_BASE" | head -1)"
E2_CG="$(tail -n1 "/proc/$E2_DAEMON_PID/cgroup" 2>/dev/null || true)"
case "$E2_CG" in
    *"$U1"*) _pass "E2 post-promote daemon still under the unit (MainPID lineage)" ;;
    *) _fail "E2 post-promote daemon under unit" "*$U1*" "$E2_CG" ;;
esac
# committed journal + supervision stamp rode the txn (stop-time snapshot:
# the stamp landed at txn-open — assert via the commit + machine evidence)
E2J="$(cat "$E2FIX/releases/state.json")"
assert_contains "E2 journal commit event" '"event":"commit"' "$E2J"
assert_contains "E2 boot advisory under the unit present" '"event":"supervision_boot"' "$E2J"
"$SC_WRAP" stop "$U1"
wait_port_free "$PORT_BASE" 15 || true

# ===========================================================================
section "E3 — survivor-heal (scope survivor + unit configured → self-heal)"

E3D="$WORK/e3"
E3_PORT=$((PORT_BASE + 1))
make_fixture "$E3D" "$E3_PORT" || _fail "E3 fixture build"
E3FIX="$E3D/install"
U3="ensemble-e2e-$TS-e3.service"
install_unit_file "$U3" "$E3FIX" "$E3_PORT"

# the SURVIVOR shape: the daemon runs under an ensemble-upgrade-*.scope
# (today's live topology — systemd-run --user --scope; --scope runs the
# command SYNCHRONOUSLY, so the launcher goes to the background)
SCOPE_NAME="ensemble-upgrade-e2e-$TS.scope"
systemd-run --user --scope --unit="$SCOPE_NAME" \
    env PORT="$E3_PORT" "$E3FIX/launcher.sh" > /dev/null 2>&1 &
if wait_livez "$E3_PORT" 20; then
    _pass "E3 survivor daemon serving (scope lineage)"
else
    _fail "E3 survivor daemon serving" "livez" "no answer"
fi
# the scope shape is REAL: the daemon's cgroup leaf is the scope
E3_DAEMON_PID="$(pgrep -f "supervision_stub_daemon.py --port $E3_PORT" | head -1)"
E3_PRE_CG="$(tail -n1 "/proc/$E3_DAEMON_PID/cgroup" 2>/dev/null || true)"
case "$E3_PRE_CG" in
    *ensemble-upgrade-e2e-*.scope*) _pass "E3 pre-promote cgroup leaf IS the upgrade scope ($E3_PRE_CG)" ;;
    *) _fail "E3 pre-promote cgroup is the scope" "*ensemble-upgrade-e2e-*.scope*" "$E3_PRE_CG" ;;
esac

E3_LOG="$WORK/e3-promote.log"
(
    HOME="$E3D/home" VERSION="$VB" TARGET=sandbox \
    INSTALL_DIR="$E3FIX" PORT="$E3_PORT" \
    ENSEMBLE_RESTART_UNIT="$U3" \
    SYSTEMCTL_BIN="$SC_WRAP" \
    LIVEZ_BUDGET_S=20 READYZ_BUDGET_S=20 ENSEMBLE_PROMOTE_SOAK_S=0 \
    XDG_RUNTIME_DIR="$XDG_RUNTIME_DIR" \
    bash "$E3D/repo/scripts/upgrade/promote.sh" sandbox
) > "$E3_LOG" 2>&1
E3_RC=$?
E3_OUT="$(cat "$E3_LOG")"

assert_eq "E3 promote exits 0 (committed via the self-heal)" "0" "$E3_RC"
assert_contains "E3 preflight classifies SCOPE_SURVIVOR (REAL §0 ladder)" "ENSEMBLE_SUPERVISION_RESULT=SCOPE_SURVIVOR" "$E3_OUT"
assert_contains "E3 outcome line degraded" "ENSEMBLE_SUPERVISION_OUTCOME=degraded" "$E3_OUT"
assert_contains "E3 hand-back self-heal announced" "hand-back: SCOPE_SURVIVOR + unit $U3 configured — SAME unit hand-back" "$E3_OUT"
assert_contains "E3 hand-back COMPLETE" "unit hand-back COMPLETE: $U3 serves the flipped release" "$E3_OUT"
assert_not_contains "E3 7b trap: success path clean" "systemctl start" "$(printf '%s' "$E3_OUT" | grep -v 'falling back\|failed\|FAILED\|did not come up' || true)"
E3J="$(cat "$E3FIX/releases/state.json")"
assert_contains "E3 supervision_handback journal event" '"event":"supervision_handback"' "$E3J"
assert_contains "E3 event: scope→unit lineage" "scope→unit: scope-survivor lineage handed back to unit $U3" "$E3J"
assert_contains "E3 event: outcome=degraded" "outcome=degraded" "$E3J"
assert_contains "E3 commit event" '"event":"commit"' "$E3J"
# the daemon now lives under the UNIT
E3_STATE="$("$SC_WRAP" is-active "$U3" 2>/dev/null || true)"
assert_eq "E3 unit ACTIVE after the heal" "active" "$E3_STATE"
wait_livez "$E3_PORT" 20 && _pass "E3 post-heal livez serving" \
    || _fail "E3 post-heal livez serving" "200" "no answer"
E3_DAEMON2="$(pgrep -f "supervision_stub_daemon.py --port $E3_PORT" | head -1)"
E3_POST_CG="$(tail -n1 "/proc/$E3_DAEMON2/cgroup" 2>/dev/null || true)"
case "$E3_POST_CG" in
    *"$U3"*) _pass "E3 daemon handed back INTO the unit ($E3_POST_CG)" ;;
    *) _fail "E3 daemon in unit after heal" "*$U3*" "$E3_POST_CG" ;;
esac
"$SC_WRAP" stop "$U3"
wait_port_free "$E3_PORT" 15 || true

# ===========================================================================
section "E4 — DUAL_FIGHT refusal drill (armed unit + foreign pid → 78 PRE-TXN)"

E4D="$WORK/e4"
E4_PORT=$((PORT_BASE + 2))
make_fixture "$E4D" "$E4_PORT" || _fail "E4 fixture build"
E4FIX="$E4D/install"
U4="ensemble-e2e-$TS-e4.service"
install_unit_file "$U4" "$E4FIX" "$E4_PORT"
"$SC_WRAP" start "$U4"
wait_livez "$E4_PORT" 20 || _fail "E4 unit daemon up" "livez" "no answer"

# the FOREIGN owned pid: a STRAY SECOND DAEMON of the SAME install
# (tier-1a anchored at INSTALL_DIR — owned by $E4FIX) but started from
# THIS shell, i.e. living OUTSIDE the unit's cgroup: two masters.
PORT="$((PORT_BASE + 3))" "$E4FIX/current/ensemble-prod" > /dev/null 2>&1 &
E4_FOREIGN_PID=$!
sleep 2
if ! kill -0 "$E4_FOREIGN_PID" 2>/dev/null; then
    _fail "E4 foreign daemon alive" "running" "dead"
else
    _pass "E4 foreign daemon alive (owned-by-install, outside-unit)"
fi

E4_LOG="$WORK/e4-promote.log"
(
    ENSEMBLE_SUPERVISION=unit ENSEMBLE_RESTART_UNIT="$U4" \
    HOME="$E4D/home" VERSION="$VB" TARGET=sandbox \
    INSTALL_DIR="$E4FIX" PORT="$E4_PORT" \
    SYSTEMCTL_BIN="$SC_WRAP" \
    XDG_RUNTIME_DIR="$XDG_RUNTIME_DIR" \
    bash "$E4D/repo/scripts/upgrade/promote.sh" sandbox
) > /dev/null 2> "$E4_LOG"
E4_RC=$?

assert_eq "E4 DUAL_FIGHT: exit 78 PRE-TXN" "78" "$E4_RC"
E4_POST_J="$(cat "$E4FIX/releases/state.json")"
assert_contains "E4 halt detail: two masters must never meet a flip" "two masters must never meet a flip" "$E4_POST_J"
assert_contains "E4 halt journal event" '"event":"halt"' "$E4_POST_J"
assert_contains "E4 halt cites DUAL_FIGHT" "supervision DUAL_FIGHT" "$E4_POST_J"
assert_contains "E4 halt cites PRE-TXN refusal" "refusing PRE-TXN" "$E4_POST_J"
# nothing opened, nothing mutated
assert_not_contains "E4 NO txn opened" '"in_flight":{"kind"' "$E4_POST_J"
assert_eq "E4 current untouched at LKG" "$VA" "$(python3 -c "import json; print(json.load(open('$E4FIX/releases/state.json'))['current'])")"
assert_eq "E4 symlink untouched" "releases/$VA" "$(readlink "$E4FIX/current")"

# cleanup the foreign pid + stop the unit
kill -TERM "$E4_FOREIGN_PID" 2>/dev/null || true
pkill -TERM -f "supervision_stub_daemon.py --port $((PORT_BASE + 3))" 2>/dev/null || true
"$SC_WRAP" stop "$U4" > /dev/null 2>&1 || true
sleep 1

# ===========================================================================
section "host-clean verification (mandatory post-suite)"

# run the FULL cleanup explicitly FIRST (the EXIT trap is the backstop)
cleanup_e2e
unset E2E_UNITS E2E_FIXTURES 2>/dev/null || true
E2E_UNITS=""; E2E_FIXTURES=""

# M4 (review cycle 1): the port-free assert used to be a bare
# `lsof -ti:<port>` — on a host WITHOUT lsof the command fails, the
# value collapses to "" and the assert passes VACUOUSLY. Guard with
# command -v and fall back to the pgrep -f stub-daemon pattern (the same
# pattern LEFT_DAEMONS uses) so the assert has teeth on lsof-less hosts.
port_holders() { # <port> — pids holding the port (lsof) or its stub daemon
    local port="$1"
    if command -v lsof >/dev/null 2>&1; then
        lsof -ti:"$port" 2>/dev/null || true
    else
        pgrep -f "supervision_stub_daemon.py --port $port" 2>/dev/null || true
    fi
}

LEFT_UNITS="$("$SC_WRAP" list-units 'ensemble-e2e-*' --all --no-legend 2>/dev/null | awk '{print $1}' | grep -v '^$' || true)"
LEFT_FILES="$(ls "$HOME/.config/systemd/user/"ensemble-e2e-* 2>/dev/null || true)"
LEFT_DAEMONS="$(pgrep -f "supervision_stub_daemon.py --port $PORT_BASE" || true)$(pgrep -f "supervision_stub_daemon.py --port $((PORT_BASE + 1))" || true)$(pgrep -f "supervision_stub_daemon.py --port $((PORT_BASE + 2))" || true)$(pgrep -f "supervision_stub_daemon.py --port $((PORT_BASE + 3))" || true)"
LEFT_PORT="$(port_holders "$PORT_BASE")$(port_holders "$((PORT_BASE + 1))")$(port_holders "$((PORT_BASE + 2))")$(port_holders "$((PORT_BASE + 3))")"
assert_eq "no ensemble-e2e-* units remain" "" "$LEFT_UNITS"
assert_eq "no ensemble-e2e-* unit files remain" "" "$LEFT_FILES"
assert_eq "no fixture stub daemons remain" "" "$LEFT_DAEMONS"
assert_eq "fixture port free (lsof-guarded, pgrep fallback — never vacuous)" "" "$LEFT_PORT"

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
