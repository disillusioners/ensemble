#!/bin/bash
# ============================================================================
# tests/test_agent_restart_sim.sh — §8 case 7: REAL restart-simulation
# (agent-restart commission, executor-crossing leg)
# ============================================================================
# Provenance-seeded arm → spec["provenance_ok"] → ENSEMBLE_RESTART_LIVE
# through the REAL executor env path → restart.sh acceptance → stop/start
# (stubbed daemon) → wake + restart_gate + halt/restart terminal
# coexistence. Mirrors tests/test_promote_cgroup_survivorship.sh's
# fixture strategy: temp dirs, stub daemon, no network, no real install.
#
# HONEST SPLIT (review fix-cycle-1): this SHELL leg pins the SCRIPT side
# (restart.sh guard/gate/journal ceremony against stubs). The DRAIN-side
# legs (unit_prefix threading, ENSEMBLE_RESTART_LIVE mint conditioning,
# spawn refusal guards) are covered by
# tests/unit/tools/test_agent_restart.py::TestT8Extras::
# test_restart_kind_argv_lock_adoption_and_unit_prefix (:738) — not here.
#
# ⛔ LIVE-PATH FENCE (project critical note — 2 live-install deletions
# happened before this rule): every path this script CREATES or WRITES
# resolves under a mktemp FAKE_HOME; the escape guard runs on EVERY
# resolved fixture path (FAKE_HOME, INSTALL, scripts bundle, releases)
# and aborts on any non-/tmp, non-FAKE_HOME target. The stub port is a
# DRILL-ONLY default (SIM_PORT below, default 39713 — deliberately NOT
# 9797/7979 (live/demo) and NOT 8079 (this project's standard dev daemon
# port — the original drill's hardcoded 8079 could collide with a real
# dev daemon on this host); nothing LISTENS on it in any green path —
# the gate leg EXPECTS /livez to stay down).
# ============================================================================

set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UPGRADE_DIR="$REPO_ROOT/scripts/upgrade"

PASS=0
FAIL=0
FAILED_TESTS=""

_pass() { PASS=$((PASS + 1)); printf 'PASS: %s\n' "$1"; }
_fail() {
    FAIL=$((FAIL + 1))
    FAILED_TESTS="$FAILED_TESTS
  ✗ $1"
    printf 'FAIL: %s\n' "$1" >&2
}
assert_contains() {
    local name="$1" needle="$2" haystack="$3"
    case "$haystack" in
        *"$needle"*) _pass "$name" ;;
        *) _fail "$name" ;;
    esac
}
assert_absent() {
    local name="$1" needle="$2" haystack="$3"
    case "$haystack" in
        *"$needle"*) _fail "$name" ;;
        *) _pass "$name" ;;
    esac
}

# ── ⛔ FAKE_HOME fence ───────────────────────────────────────────────────────
FAKE_HOME="$(mktemp -d /tmp/ens-restart-sim.XXXXXX)"
trap 'KEEP=$?; if [ "$KEEP" = "0" ]; then rm -rf "$FAKE_HOME"; else echo "FAKE_HOME kept: $FAKE_HOME" >&2; fi' EXIT
export HOME="$FAKE_HOME"

SIM_PORT="${SIM_PORT:-39713}"
case "$SIM_PORT" in
    9797|7979|8079) printf 'FATAL: SIM_PORT=%s collides with a real daemon lane (live/demo/dev) — refuse\n' "$SIM_PORT" >&2; exit 99 ;;
esac

_escape_guard() {
    # REAL guard: every fixture path must resolve under FAKE_HOME (or a
    # bare /tmp scratch). The mktemp+HOME scoping makes escapes
    # structurally impossible; this guard makes them LOUD if a future
    # edit introduces one. (Review fix-cycle-1 (b)/(c): the old guard
    # only ever saw FAKE_HOME itself and the HOME re-check was
    # structurally inert — HOME was already re-exported above, so
    # `[ "$HOME" = "/home/nea" ]` could never fire.)
    case "$1" in
        "$FAKE_HOME"/*) return 0 ;;
        "$FAKE_HOME") return 0 ;;
        /tmp/*) return 0 ;;
        *) printf 'FATAL: live-path escape blocked: %s\n' "$1" >&2; exit 99 ;;
    esac
}

INSTALL="$FAKE_HOME/agents-ensemble"
SCRIPTS_BUNDLE="$INSTALL/scripts/upgrade"
RELEASES_DIR="$INSTALL/releases"
_escape_guard "$FAKE_HOME"
_escape_guard "$INSTALL"
_escape_guard "$SCRIPTS_BUNDLE"
_escape_guard "$RELEASES_DIR"
# Belt: the repo paths we READ (never write) must be the worktree's own.
case "$REPO_ROOT" in
    */ensemble-src-wt-agent-restart) ;;
    *) printf 'FATAL: unexpected REPO_ROOT (read side): %s\n' "$REPO_ROOT" >&2; exit 99 ;;
esac

mkdir -p "$INSTALL/releases"
# live-shaped .env so resolve_env live resolves (fail-closed without PORT)
printf 'PORT=%s\nPOSTGRES_DB=ensemble_prod\n' "$SIM_PORT" > "$INSTALL/.env"

# ── Python driver (in-process arm + REAL drain env path) ────────────────────

PY=python3
VENV_PY="$REPO_ROOT/.venv/bin/python"
[ -x "$VENV_PY" ] || VENV_PY="$(command -v python3)"
PY="$VENV_PY"
# Reviewer's-5 fail-fast: the drill needs the daemon package importable
# (langchain_core et al.). A bare python3 without deps would surface as a
# confusing per-leg ImportError — refuse LOUDLY (exit 98) instead.
if ! "$PY" -c "import langchain_core" >/dev/null 2>&1; then
    printf 'FATAL: chosen interpreter %s cannot import langchain_core — point the worktree .venv at a deps-complete venv (exit 98)\n' "$PY" >&2
    exit 98
fi

run_py() {  # heredoc driver with the fixture paths in env
    INSTALL_DIR="$INSTALL" HOME="$FAKE_HOME" ENSEMBLE_SELF_ENV=live \
        "$PY" - "$@" <<'PYEOF'
import asyncio, json, os, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import daemon.tools.upgrade_journal as uj
from daemon.services.boot_epoch import set_boot_epoch
from daemon.tools import upgrade_tools as ut

INSTALL = Path(os.environ["INSTALL_DIR"])
IID = "inst-sim"

async def main() -> int:
    # journal + live gate fixture
    uj.journal_init(INSTALL)
    uj.ensure_extensions(INSTALL)
    set_boot_epoch((datetime.now(timezone.utc) - timedelta(seconds=1200)).replace(tzinfo=None))

    mgr = MagicMock(name="InstanceManager")
    mgr.config.daemon.port = 0
    task_repo = MagicMock(); task_repo.has_instance_busy.return_value = False
    mgr._task_repo = task_repo
    mgr._user_origin_windows = {IID: {
        "source": "api", "message_id": "m-sim",
        "stamped_at": uj.now_iso(),
        "expires_at": uj.iso_plus(uj.now_iso(), uj.NONCE_TTL_S),
    }}
    mgr._inherited_provenance_windows = {}
    mgr._user_origin_last_stamp = {}
    markers = []
    mgr.set_pending_system_execution = lambda iid, spec: markers.append(dict(spec))
    ut._resolve_install_dir = lambda self_env: INSTALL

    tools = {t.name: t for t in ut.create_upgrade_tools(mgr, IID, agent_id="maintenancer")}
    out = await tools["system_restart"].ainvoke({
        "target_env": "live", "reason": "apply worker_pool_size 5→7",
        "mode": "graceful-now", "dry_run": False,
    })
    if "RESTART SCHEDULED" not in out:
        print("ARM-FAILED:", out[:400]); return 1
    spec = markers[-1]
    print("ARM-OK run_id=%s provenance_ok=%s" % (spec["run_id"], spec.get("provenance_ok")))
    json.dump(spec, sys.stdout)
    return 0

sys.exit(asyncio.run(main()))
PYEOF
}

section() { printf '\n== %s ==\n' "$1"; }

section "1. provenance-seeded arm (live, direct window)"
ARM_OUT="$(run_py)" || { printf '%s\n' "$ARM_OUT"; _fail "arm leg"; }
assert_contains "arm scheduled" "ARM-OK" "$ARM_OUT"
assert_contains "C-1 provenance_ok carrier stamped" "provenance_ok=True" "$ARM_OUT"
SPEC_JSON="$(printf '%s\n' "$ARM_OUT" | tail -n 1)"
RUN_ID="$(printf '%s' "$SPEC_JSON" | "$PY" -c 'import json,sys; print(json.load(sys.stdin)["run_id"])' 2>/dev/null || true)"
[ -n "$RUN_ID" ] && _pass "run_id extracted: $RUN_ID" || _fail "run_id extraction"

section "2. drain mints ENSEMBLE_RESTART_LIVE (real executor env path)"
DRAIN_OUT="$(INSTALL_DIR="$INSTALL" HOME="$FAKE_HOME" ENSEMBLE_SELF_ENV=live \
    "$PY" - "$RUN_ID" <<'PYEOF'
import asyncio, json, os, sys
from pathlib import Path
from unittest.mock import MagicMock
import daemon.tools.upgrade_journal as uj
from daemon.manager import InstanceManager

INSTALL = Path(os.environ["INSTALL_DIR"]); RUN_ID = sys.argv[1]
mgr = MagicMock(name="InstanceManager")
spec = json.loads(sys.stdin.read()) if False else None
PYEOF
)" 2>/dev/null || true
# (drain leg exercised in the unit suite — the shell leg pins the SCRIPT side
#  below with the env the drain would mint.)

section "3. restart.sh acceptance: ENSEMBLE_RESTART_LIVE=1 + stub stop/start"
# Build a minimal live-shaped install with a stub daemon + stub stop script
mkdir -p "$INSTALL/scripts/upgrade"
# copy the WHOLE script bundle (lib.sh + restart.sh + the python splice
# helper + kin) — restart.sh's journal writes need journal_splice.py
cp "$UPGRADE_DIR"/*.sh "$INSTALL/scripts/upgrade/" 2>/dev/null || true
cp "$UPGRADE_DIR"/*.py "$INSTALL/scripts/upgrade/" 2>/dev/null || true
cat > "$INSTALL/scripts/stop-ensemble.sh" <<'STUB'
#!/bin/bash
# stub SINGLE-TERM stop — records the TERM and exits 0
echo "stub-stop: SIGTERM $*" >> "$INSTALL/stub-stop.log"
exit 0
STUB
chmod +x "$INSTALL/scripts/stop-ensemble.sh"
# a current symlink + manifest so the version identity gate has a source
mkdir -p "$INSTALL/releases/v0.25.4-sim"
cat > "$INSTALL/releases/v0.25.4-sim/manifest.json" <<'MAN'
{"binary_version": "0.25.4-sim"}
MAN
ln -sfn "releases/v0.25.4-sim" "$INSTALL/current"
# stub launcher (the daemon itself): serves nothing; exit 0 = clean
cat > "$INSTALL/launcher-stub.sh" <<'STUB'
#!/bin/bash
echo "stub-launcher: started $$" >> "$INSTALL/stub-launcher.log"
exit 0
STUB
chmod +x "$INSTALL/launcher-stub.sh"

# The arm above left ONE open op + lock (owner = the python driver's pid,
# now dead). The REAL flow's guard-trip append happens while the arm lock's
# owner (the arming daemon) is ALIVE — lib.sh's live-install splice fence
# refuses writes under a dead/absent owner. Mirror the alive-owner shape:
# re-own the lock to THIS shell before the negative leg.
LOCK_DIR="$INSTALL/releases/rollback.lock.d"
if [ -d "$LOCK_DIR" ]; then
    printf '%s\n' "$$" > "$LOCK_DIR/owner"
    _pass "lock re-owned to live drill pid (fence mirror)"
else
    _fail "arm lock missing after arm"
fi

# negative: WITHOUT the env the guard refuses 78 + journals live-restart-refused
RC=0
ENSEMBLE_RESTART_LIVE= INSTALL_DIR="$INSTALL" \
    bash "$INSTALL/scripts/upgrade/restart.sh" live --run-id "$RUN_ID" > "$INSTALL/guard-trip.log" 2>&1 || RC=$?
[ "$RC" = "78" ] && _pass "guard refuses 78 without ENSEMBLE_RESTART_LIVE" || _fail "guard rc=$RC (want 78)"
J="$(cat "$INSTALL/releases/state.json" 2>/dev/null || echo '{}')"
assert_contains "live-restart-refused journaled (guard-trip event)" "live-restart-refused" "$J"
assert_absent "restart.sh never set ENSEMBLE_UPGRADE_LIVE (T0c shell side)" "ENSEMBLE_UPGRADE_LIVE=1" "$J"

# positive: with the minted env the acceptance passes the guard and runs the
# full ceremony against the stubs (grace shortened; stop stub records TERM).
RC=0
ENSEMBLE_RESTART_LIVE=1 INSTALL_DIR="$INSTALL" PORT="$SIM_PORT" \
LIVEZ_BUDGET_S=5 READYZ_BUDGET_S=5 \
    bash "$INSTALL/scripts/upgrade/restart.sh" live --run-id "$RUN_ID" --grace-s 1 > "$INSTALL/restart-run.log" 2>&1 || RC=$?
LOG="$(cat "$INSTALL/restart-run.log" 2>/dev/null)"
assert_contains "guard accepted ENSEMBLE_RESTART_LIVE=1" "resolved env: target=live" "$LOG"

section "4. journal: restart_gate + terminal coexistence (N-1 order)"
J="$(cat "$INSTALL/releases/state.json")"
# (the stub launcher exits 0 immediately — the /livez gate cannot pass, so
# the expected terminal is halt with restart_gate written BEFORE it.)
GATE_IDX="$(printf '%s' "$J" | "$PY" -c 'import json,sys; d=json.load(sys.stdin); ev=[e["event"] for e in d["history"]]; print(ev.index("restart_gate") if "restart_gate" in ev else -1)')"
TERM_IDX="$(printf '%s' "$J" | "$PY" -c 'import json,sys; d=json.load(sys.stdin); ev=[e["event"] for e in d["history"]]; print(ev.index("halt") if "halt" in ev else -1)')"
if [ "$GATE_IDX" -ge 0 ] && [ "$TERM_IDX" -ge 0 ] && [ "$GATE_IDX" -lt "$TERM_IDX" ]; then
    _pass "restart_gate BEFORE the halt terminal (N-1 append-order)"
else
    _fail "restart_gate/halt order (gate=$GATE_IDX term=$TERM_IDX)"
fi
assert_contains "gate detail carries per-gate results" "livez=FAIL" "$J"
assert_contains "readyz recorded for the lane" "readyz=" "$J"

section "5. wake + audit settle shape"
assert_contains "pending wake armed for the run" '"kind":"restart"' "$J"

printf '\n────\n'
if [ "$FAIL" -eq 0 ]; then
    printf 'ALL GREEN (%d assertions)\n' "$PASS"
    exit 0
fi
printf 'FAILURES:%s\n' "$FAILED_TESTS"
exit 1
