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
# SF-2 (tester micro-fix cycle): mktemp failure left FAKE_HOME empty and
# degenerated the guard's "$FAKE_HOME"/* pattern to "/*" — accepting ANY
# absolute path. Two hardenings: (a) fail-closed validation BEFORE any
# guard evaluation; (b) the guard's own first branch requires the
# LITERAL expected mktemp shape — an empty/garbage FAKE_HOME matches NO
# allow branch, so degenerate acceptance is structurally impossible.
if [ -z "$FAKE_HOME" ] || [ ! -d "$FAKE_HOME" ]; then
    printf 'FATAL: FAKE_HOME invalid (%q) — mktemp failure must refuse, never degrade the fence\n' "$FAKE_HOME" >&2
    exit 99
fi
case "$FAKE_HOME" in
    /tmp/ens-restart-sim.??????) : ;;   # the literal mktemp -d shape
    *) printf 'FATAL: FAKE_HOME %q does not match the expected mktemp shape — refusing (guard non-degeneracy)\n' "$FAKE_HOME" >&2; exit 99 ;;
esac
trap 'KEEP=$?; if [ "$KEEP" = "0" ]; then rm -rf "$FAKE_HOME"; else echo "FAKE_HOME kept: $FAKE_HOME" >&2; fi' EXIT
export HOME="$FAKE_HOME"

SIM_PORT="${SIM_PORT:-39713}"
# SF-3: 8088 (ensemble self-system lane) joins the refusal set.
case "$SIM_PORT" in
    9797|7979|8079|8088) printf 'FATAL: SIM_PORT=%s collides with a real daemon lane (live/demo/dev/self-system) — refuse\n' "$SIM_PORT" >&2; exit 99 ;;
esac

_escape_guard() {
    # REAL guard: every fixture path must resolve under FAKE_HOME (or a
    # bare /tmp scratch). The mktemp+HOME scoping makes escapes
    # structurally impossible; this guard makes them LOUD if a future
    # edit introduces one. (Review fix-cycle-1 (b)/(c): the old guard
    # only ever saw FAKE_HOME itself and the HOME re-check was
    # structurally inert.) SF-2: the allow branches use the LITERAL
    # validated shape — an empty FAKE_HOME cannot degenerate the pattern
    # (it is rejected before the guard ever runs, and would match no
    # branch regardless).
    case "$1" in
        /tmp/ens-restart-sim.??????/*) return 0 ;;
        /tmp/ens-restart-sim.??????) return 0 ;;
        /tmp/*) return 0 ;;
        *) printf 'FATAL: live-path escape blocked: %s\n' "$1" >&2; exit 99 ;;
    esac
}

# SF-2 NEGATIVE FIXTURE: prove degeneracy refuses, not accepts. Run the
# guard body with a poisoned-empty FAKE_HOME shape in a subshell and
# assert the loud exit (99), never a silent accept.
_sf2_neg_rc=0
( FAKE_HOME=""; _escape_guard "/etc/passwd" ) >/dev/null 2>&1 || _sf2_neg_rc=$?
if [ "$_sf2_neg_rc" = "99" ]; then
    _pass "SF-2 negative fixture: empty-FAKE_HOME guard refuses /etc/passwd (exit 99)"
else
    _fail "SF-2 negative fixture: expected refusal rc=99, got $_sf2_neg_rc"
fi
_sf2_neg2_rc=0
( FAKE_HOME=""; _escape_guard "/etc/systemd/system/ensemble-main.service" ) >/dev/null 2>&1 || _sf2_neg2_rc=$?
[ "$_sf2_neg2_rc" = "99" ] && _pass "SF-2 negative fixture: unit-file path refused" || _fail "SF-2 negative fixture 2 rc=$_sf2_neg2_rc"

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
    # SF-5 (tester micro-fix cycle): a bare MagicMock short-circuits
    # _capture_pending_overrides to [] (auto-attr get_overrides() returns a
    # MagicMock, not a dict). Wire a REAL-shaped settings stub: one pending
    # DB override (worker_pool_size 7 vs live 5) flows through the REAL
    # resolver (source=db wins; divergent) into the receipt + audit row.
    from types import SimpleNamespace
    mgr._settings_service = SimpleNamespace(
        get_overrides=lambda: {"limits.worker_pool_size": 7}
    )
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
    # SF-5: surface the receipt for shell-side assertions (the first arm's
    # overrides_pending render is a captured-flow assertion target).
    print("RECEIPT-BEGIN")
    print(out)
    print("RECEIPT-END")
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

# SF-5 (tester micro-fix cycle): the REAL resolver flow (settings stub →
# source=db wins → divergent vs live 5) must surface in BOTH the receipt
# and the journaled audit row.
assert_contains "SF-5: receipt renders the captured pending override" "overrides_pending: limits.worker_pool_size: 5→7" "$ARM_OUT"
J1="$(cat "$INSTALL/releases/state.json" 2>/dev/null || echo '{}')"
if printf '%s' "$J1" | "$PY" -c '
import json, sys
d = json.load(sys.stdin)
rows = [
    r for r in (d.get("restart_audits") or {}).values()
    if isinstance(r, dict)
    and any(
        isinstance(o, dict)
        and o.get("key") == "limits.worker_pool_size"
        and o.get("db_value") == 7
        and o.get("live_before") == 5
        and o.get("status") == "pending"
        for o in (r.get("overrides") or [])
    )
]' 2>/dev/null; then
    _pass "SF-5: audit row carries the pending override {key, live_before:5, db_value:7, status:pending}"
else
    _fail "SF-5: audit row override capture missing"
fi

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
# stub SINGLE-TERM stop — records the TERM and exits 0.
# SF-1 (tester micro-fix cycle): the OLD body referenced $INSTALL from the
# environment, which never reaches this shell (the caller passes the
# install dir as argv, and the env var name differs) — the append
# degenerated to /stub-stop.log, a WRITE AT THE FILESYSTEM ROOT (EACCES
# as nea; would SUCCEED as root). The stub now derives its root from its
# OWN script location — argv/env-free, contained by construction — and
# records the resolved ABSOLUTE path it writes into a manifest the sim
# audits (no write path may escape the temp root).
STUB_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STUB_LOG="$STUB_ROOT/stub-stop.log"
printf '%s\n' "stub-stop: SIGTERM $*" >> "$STUB_LOG"
printf '%s\n' "stub-stop wrote: $STUB_LOG" >> "$STUB_ROOT/stub-write-paths.txt"
# TERM the serving launcher when its pid file exists (PASS-direction leg)
if [ -f "$STUB_ROOT/launcher.pid" ]; then
    kill "$(cat "$STUB_ROOT/launcher.pid")" 2>/dev/null || true
fi
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
# SF-7 (tester micro-fix cycle): the old presence-only `readyz=` check
# could not distinguish a REAL probe-green from the boot-grace-SUPPRESSED
# one-shot pass (lib.sh _probe_once returns 0 for boot-grace-suppressed —
# nothing is listening in this leg; that "pass" is suppression, not a
# probe). The honest pin: the suppressed form is asserted EXPLICITLY by
# name, and a REAL green (`readyz=pass latency=`) must be ABSENT on this
# dead-stub leg. The FAIL direction stays truthful; the PASS direction is
# exercised for real in section 4b below.
assert_contains "readyz one-shot recorded (boot-grace-SUPPRESSED — named, not green-washed)" "readyz=pass(one-shot" "$J"
assert_absent "no REAL readyz green claimed on the dead-stub leg" "readyz=pass latency=" "$J"

section "4b. SF-1 write-path containment audit"
# The stop stub records every path it wrote into a manifest; each must be
# under the temp root, and the root must NOT carry the old degenerate
# artifact. Belt: scan the manifest + the FAKE_HOME tree.
WP="$INSTALL/stub-write-paths.txt"
if [ -f "$WP" ]; then
    # each manifest line is `stub-stop wrote: <abs-path>` — check the PATH
    # FIELD, not the line prefix.
    ESCAPES="$(awk '{print $3}' "$WP" | grep -v "^$FAKE_HOME/" || true)"
    if [ -z "$ESCAPES" ] && [ -s "$WP" ]; then
        _pass "SF-1: all stub write paths contained under FAKE_HOME ($(wc -l < "$WP") path(s))"
    else
        _fail "SF-1: stub write escaped temp: $ESCAPES"
    fi
else
    _fail "SF-1: stub write-path manifest absent (stop stub never ran?)"
fi
if [ -f "/stub-stop.log" ]; then
    _fail "SF-1: ROOT-LEVEL /stub-stop.log exists — out-of-temp write SUCCEEDED"
else
    _pass "SF-1: no root-level /stub-stop.log (the old degenerate target stayed untouched)"
fi
if [ -f "$INSTALL/stub-stop.log" ]; then
    _pass "SF-1: TERM record landed INSIDE the fixture (stub-stop.log present)"
else
    _fail "SF-1: stub-stop.log missing under INSTALL (TERM-record leg degraded)"
fi

section "4b2. G-4.2 pin — budget survives a process (daemon-restart) boundary"
# A FRESH interpreter = fresh module state = the in-memory daemon is
# "restarted"; the sliding window must survive via the journal FILE.
# ONE live arm has run so far (section 1; the second arm is the PASS leg
# below) — the fresh interpreter must read that persisted count from the
# journal FILE (the restart-boundary proof).
BUDGET_FRESH="$(INSTALL_DIR="$INSTALL" HOME="$FAKE_HOME" "$PY" - <<'BUDGETPY'
import os
from pathlib import Path
import daemon.tools.upgrade_journal as uj
print(uj.restart_budget_count(Path(os.environ["INSTALL_DIR"])))
BUDGETPY
)" || BUDGET_FRESH="ERR"
case "$BUDGET_FRESH" in
    1) _pass "G-4.2: budget count=1 read by a FRESH interpreter (survives the restart boundary)" ;;
    ERR|*) _fail "G-4.2: fresh-interpreter budget read gave '$BUDGET_FRESH' (want 1)" ;;
esac

section "4c. SF-7 PASS direction — real green via serving launcher (new arm + restart.sh)"
# Second arm against the SAME fixture (the first op is closed by the halt
# finalize); a SERVING launcher stub answers /livez + /readyz with the
# manifest version — the gate then greens FOR REAL (probe-green, not
# boot-grace suppression), and the terminal is `restart` with restart_gate
# BEFORE it (N-1 pinned in BOTH directions).
cat > "$INSTALL/launcher.sh" <<'LSTUB'
#!/bin/bash
# serving stub: answers /livez + /readyz 200 with the manifest version,
# runs ~30s (longer than the gate budgets), contained under the fixture.
ROOT="$(cd "$(dirname "$0")" && pwd)"
printf '%s\n' "$$" > "$ROOT/launcher.pid"
exec "$SIM_PY" - "${PORT:?PORT must be exported to the serving stub}" <<'PSRV'
import http.server, json, socketserver, sys
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"status": "ok", "version": "0.25.4-sim"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *a):
        pass
socketserver.TCPServer.allow_reuse_address = True
with socketserver.TCPServer(("127.0.0.1", int(sys.argv[1])), H) as srv:
    srv.timeout = 30
    for _ in range(8):
        srv.handle_request()
PSRV
LSTUB
chmod +x "$INSTALL/launcher.sh"

# Fresh journal for the PASS leg: the negative leg's live-restart-refused
# prose contains the literal token `pending_op`, and lib.sh's textual
# _json_sub extracts the FIRST occurrence — pre-existing extractor
# fragility, avoided harness-side (all FAIL-direction assertions have
# already run; section 5's wake assertion reads the pre-reset $J string).
INSTALL_DIR="$INSTALL" HOME="$FAKE_HOME" "$PY" - <<'RESETPY'
import os
from pathlib import Path
import daemon.tools.upgrade_journal as uj
inst = Path(os.environ["INSTALL_DIR"])
uj.journal_write(inst, dict(uj.JOURNAL_EMPTY))
uj.ensure_extensions(inst)
RESETPY
ARM2_OUT="$(run_py)" || { printf '%s\n' "$ARM2_OUT"; _fail "PASS-direction arm leg"; }
RUN_ID2="$(printf '%s\n' "$ARM2_OUT" | tail -n 1 | "$PY" -c 'import json,sys; print(json.load(sys.stdin)["run_id"])' 2>/dev/null || true)"
[ -n "$RUN_ID2" ] && _pass "PASS-direction arm run_id: $RUN_ID2" || _fail "PASS-direction run_id"
LOCK_DIR2="$INSTALL/releases/rollback.lock.d"
if [ -d "$LOCK_DIR2" ]; then printf '%s\n' "$$" > "$LOCK_DIR2/owner"; fi
RC2=0
ENSEMBLE_RESTART_LIVE=1 INSTALL_DIR="$INSTALL" PORT="$SIM_PORT" \
LIVEZ_BUDGET_S=8 READYZ_BUDGET_S=8 SIM_PY="$PY" \
    bash "$INSTALL/scripts/upgrade/restart.sh" live --run-id "$RUN_ID2" --grace-s 1 > "$INSTALL/restart-run2.log" 2>&1 || RC2=$?
LOG2="$(cat "$INSTALL/restart-run2.log" 2>/dev/null)"
J2="$(cat "$INSTALL/releases/state.json" 2>/dev/null || echo '{}')"
if printf '%s' "$J2" | "$PY" -c '
import json, sys
d = json.load(sys.stdin)
ev = [e["event"] for e in d["history"]]
ok = "restart" in ev and "restart_gate" in ev
gi = ev.index("restart_gate") if "restart_gate" in ev else -1
# the LATEST restart_gate + restart pair belong to run 2 — check the last occurrences
ri = len(ev) - 1 - ev[::-1].index("restart") if "restart" in ev else -1
gi_last = len(ev) - 1 - ev[::-1].index("restart_gate") if "restart_gate" in ev else -1
sys.exit(0 if (ok and gi_last < ri) else 1)
' 2>/dev/null; then
    _pass "PASS direction: gate PASSED — restart terminal, restart_gate BEFORE it (N-1 both directions)"
else
    _fail "PASS direction: gate/terminal order (rc=$RC2; see restart-run2.log)"
fi
assert_contains "SF-7: REAL readyz green rendered (latency form, not suppression)" "readyz=pass latency=" "$J2"
assert_contains "SF-7: REAL livez green rendered" "livez=pass latency=" "$J2"
assert_contains "PASS-direction gate detail: version identity ok" "version=identity-ok" "$J2"
# serving launcher cleanup (it self-exits after 4 requests; belt):
[ -f "$INSTALL/launcher.pid" ] && kill "$(cat "$INSTALL/launcher.pid")" 2>/dev/null || true

section "5. wake + audit settle shape"
assert_contains "pending wake armed for the run" '"kind":"restart"' "$J"

printf '\n────\n'
if [ "$FAIL" -eq 0 ]; then
    printf 'ALL GREEN (%d assertions)\n' "$PASS"
    exit 0
fi
printf 'FAILURES:%s\n' "$FAILED_TESTS"
exit 1
