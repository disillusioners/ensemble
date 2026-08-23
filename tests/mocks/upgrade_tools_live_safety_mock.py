#!/usr/bin/env python3
"""Real-behavior mock verification: P2.2 Upgrade-Tools Live-Safety DYNAMIC sandbox.

Drives the REAL P2.2 tool surface (daemon/tools/upgrade_tools.py, branch
feature/self-restart-p2p2-ari-tools @ ca5a404e) end-to-end, IN-PROCESS,
against FAKE deploy state under /tmp/p22-dynamic-sandbox — proving
(a) the 4 tools genuinely work through the real factory / stamping / gate /
spawn code and (b) they cannot touch the real live install.

REAL seams used (no re-implementations):
  * create_upgrade_tools() factory (the same call instance.py makes)
  * InstanceManager.stamp_user_origin_window / set_pending_system_execution
    (REAL method bodies, bound to a minimal manager facade whose only mocks
    are the outermost DB edges: _task_repo, _queue_repository, config port)
  * daemon.tools.upgrade_journal nonce store + journal + lock protocol
  * scripts/upgrade/lib.sh + status.sh (real shell writers/readers) build
    and parity-check the fixtures
  * daemon.tools.upgrade_journal.spawn_executor (REAL spawn, S5)

FAKE state isolation (S7 guards prove zero live contact):
  * HOME is redirected to <sandbox>/fakehome so the REAL
    _resolve_install_dir("live"/"demo") resolves FAKE install trees
    (~/agents-ensemble, ~/agents-ensemble-demo) under the sandbox.
  * Shell drivers get an isolated parity-home + TARGET=sandbox so lib.sh's
    canon guard passes and no real install path is ever addressed.
  * Fake self-env marker via ENSEMBLE_SELF_ENV per scenario (mirrors the
    staged .env marker exported by launcher.sh).

Dual-layer timeout: signal.alarm(240) inner + `timeout 300` outer guard.
Never writes into the repo worktree; never touches the real live install
(read-only stat + lsof guards only); never exports ENSEMBLE_DEPLOY_LIVE.

Output: per-scenario evidence lines + RESULT: PASS|FAIL (exit 0/1/124).
Pattern: tests/mocks/reasoning_echo_denylist_mock.py.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import traceback
from pathlib import Path
from types import MethodType, SimpleNamespace
from typing import Any

# ── Repo root (script lives in /tmp; repo workdir is fixed by dispatch) ─────
REPO_ROOT = Path(
    os.environ.get(
        "P22_REPO_ROOT",
        "/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble",
    )
)
if (REPO_ROOT / "daemon" / "tools" / "upgrade_tools.py").is_file():
    pass
elif (Path.cwd() / "daemon" / "tools" / "upgrade_tools.py").is_file():
    REPO_ROOT = Path.cwd()
else:
    print("FATAL: cannot locate the agents-ensemble repo root", flush=True)
    sys.exit(2)
sys.path.insert(0, str(REPO_ROOT))

SANDBOX = Path("/tmp/p22-dynamic-sandbox")
FAKE_HOME = SANDBOX / "fakehome"
PARITY_HOME = SANDBOX / "parity-home"  # isolated HOME for shell drivers
FAKE_LIVE = FAKE_HOME / "agents-ensemble"
FAKE_DEMO = FAKE_HOME / "agents-ensemble-demo"
SELF_PORT = 10797  # the ONLY port allowed for a daemon; nothing listens here
INSTANCE_ID = "inst-p22-dynamic-1"

LIB_SH = REPO_ROOT / "scripts" / "upgrade" / "lib.sh"
STATUS_SH = REPO_ROOT / "scripts" / "upgrade" / "status.sh"

# env vars we save/restore around the whole run
_SAVED_ENV_KEYS = (
    "HOME", "ENSEMBLE_SELF_ENV", "ENSEMBLE_UPGRADE_LIVE", "ENSEMBLE_DEPLOY_LIVE",
    "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "POSTGRES_PASSWORD", "POSTGRES_URL",
    "SECRET_POISON_CANARY", "AWS_SECRET_ACCESS_KEY", "XDG_CONFIG_HOME", "PGPASSWORD",
)
POISON_VARS = {
    "ENSEMBLE_UPGRADE_LIVE": "1",          # THE critical poison (F2 ledger)
    "ENSEMBLE_DEPLOY_LIVE": "1",           # deploy-pipeline live key
    "ENSEMBLE_SELF_ENV": "demo",           # parent marker (child re-derives)
    "OPENAI_API_KEY": "sk-POISON-SENTINEL",
    "ANTHROPIC_API_KEY": "sk-ant-POISON-SENTINEL",
    "POSTGRES_PASSWORD": "POISON-SENTINEL",
    "POSTGRES_URL": "postgresql://POISON@nowhere/db",
    "SECRET_POISON_CANARY": "1",
    "AWS_SECRET_ACCESS_KEY": "POISON-SENTINEL",
    "XDG_CONFIG_HOME": "/tmp/p22-dynamic-sandbox/poison-canary-xdg",
    "PGPASSWORD": "PG-PREFIX-PASSTHROUGH-SENTINEL",  # PG* prefix: BY DESIGN kept
}

# ── Inner self-timeout (dual layer, layer 2 = outer `timeout 300`) ──────────
HARD_TIMEOUT_SECONDS = 240


def _timeout_handler(_signum: int, _frame: Any) -> None:
    print("RESULT: TIMEOUT (script exceeded 240s hard cap)", flush=True)
    sys.exit(124)


signal.signal(signal.SIGALRM, _timeout_handler)
signal.alarm(HARD_TIMEOUT_SECONDS)

# ── Imports AFTER repo path setup ───────────────────────────────────────────
from daemon.manager import InstanceManager  # noqa: E402
from daemon.tools import upgrade_journal as uj  # noqa: E402
from daemon.tools import upgrade_tools as ut  # noqa: E402
from daemon.tools.upgrade_tools import create_upgrade_tools  # noqa: E402

RESULTS: list[tuple[str, str, str]] = []  # (id, status, evidence)
S5_TABLE: list[tuple[str, str, str, str]] = []  # (var, parent, child, verdict)
S7_BEFORE: dict[str, str] = {}
S7_AFTER: dict[str, str] = []


def record(sid: str, status: str, evidence: str) -> None:
    RESULTS.append((sid, status, evidence))
    print(f"[{sid}] {status} — {evidence}", flush=True)


def reason_of(result: str) -> str | None:
    m = re.search(r"REFUSED — reason=([a-z0-9-]+)", result)
    return m.group(1) if m else None


# ── Fake manager facade: REAL methods + mocked outermost DB edges ───────────
class _FakeTaskRepo:
    async_ok = True

    def has_instance_busy(self, instance_id: str) -> bool:
        return False


class _FakeRow:
    def __init__(self, content: str | None):
        self.content = content


class _FakeQueueRepo:
    def __init__(self):
        self.rows: dict[str, _FakeRow] = {}

    def get(self, message_id: str | None):
        if not message_id:
            return None
        return self.rows.get(message_id)


class _ManagerFacade:
    """Minimal manager carrying the REAL P2.2 method bodies (bound via
    MethodType — the exact code the production funnel calls), plus faked
    outermost DB edges only."""

    def __init__(self):
        self.config = SimpleNamespace(daemon=SimpleNamespace(port=SELF_PORT))
        self._task_repo = _FakeTaskRepo()
        self._queue_repository = _FakeQueueRepo()
        self._user_origin_windows: dict[str, dict] = {}
        self._pending_system_executions: dict[str, dict] = {}
        # REAL method bodies (not reimplementations):
        self.stamp_user_origin_window = MethodType(
            InstanceManager.stamp_user_origin_window, self
        )
        self.set_pending_system_execution = MethodType(
            InstanceManager.set_pending_system_execution, self
        )


# ── Shell-driver helper (real lib.sh / status.sh, isolated env) ─────────────
def run_shell(script_body: str, *, timeout: int = 30, extra_env: dict | None = None,
              argv: list[str] | None = None) -> subprocess.CompletedProcess:
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(PARITY_HOME),  # never the real home; canon guard passes
        "TARGET": "sandbox",
        "INSTALL_DIR": "",  # set per call via extra_env
        "PORT": str(SELF_PORT),
        "POSTGRES_DB": "ensemble_sandbox",
    }
    if extra_env:
        env.update(extra_env)
    cmd = argv if argv is not None else ["bash", "-c", script_body]
    return subprocess.run(
        cmd, env=env, cwd=str(SANDBOX), capture_output=True, text=True,
        timeout=timeout,
    )


def driver(install_dir: Path, body: str, timeout: int = 30) -> subprocess.CompletedProcess:
    """Source the REAL lib.sh in sandbox mode against a fixture tree."""
    script = f'. "{LIB_SH}"\nresolve_env sandbox\n{body}'
    return run_shell(script, timeout=timeout,
                     extra_env={"INSTALL_DIR": str(install_dir)})


def status_sh(install_dir: Path) -> str:
    rc = run_shell(None, argv=["bash", str(STATUS_SH), "sandbox"],
                   extra_env={"INSTALL_DIR": str(install_dir)})
    if rc.returncode != 0:
        raise RuntimeError(f"status.sh rc={rc.returncode}: {rc.stderr[-800:]}")
    return rc.stdout


# ── Fixtures ─────────────────────────────────────────────────────────────────
def _write_manifest(install: Path, version: str, *, rollback_safe: bool = True,
                    binary_version: str | None = None) -> None:
    """Fixture manifest.json — identity-field schema of stage.sh's writer
    (same field names/types stage.sh emits; checksums omitted — the tools'
    manifest reads are identity-only). Same precedent as the committed
    unit-test fixture builder (tests/unit/tools/test_upgrade_tools.py)."""
    rel = install / "releases" / version
    rel.mkdir(parents=True, exist_ok=True)
    (rel / "manifest.json").write_text(
        json.dumps(
            {
                "version": version,
                "binary_version": binary_version or version,
                "staged_at": "2026-08-23T09:00:00Z",
                "known_schema_gen": 14,
                "contains_contract_phase": False,
                "rollback_safe": rollback_safe,
            }
        ),
        encoding="utf-8",
    )


def build_fixtures() -> None:
    # idempotent: wipe the fixture trees first (a previous FAIL run keeps
    # its sandbox for inspection; journal_init alone would not reset state)
    for inst in (FAKE_DEMO, FAKE_LIVE):
        shutil.rmtree(inst, ignore_errors=True)
    PARITY_HOME.mkdir(parents=True, exist_ok=True)
    for inst in (FAKE_DEMO, FAKE_LIVE):
        (inst / "releases").mkdir(parents=True, exist_ok=True)
        (inst / "data").mkdir(parents=True, exist_ok=True)

    # ── FAKE DEMO tree (rich P2.1-state parity fixture) — written by lib.sh ──
    for ver in ("v0.7.0", "v0.8.0", "v0.9.0", "v0.10.0"):
        _write_manifest(FAKE_DEMO, ver)
    _write_manifest(FAKE_DEMO, "v0.8.0-bad", rollback_safe=False)
    (FAKE_DEMO / "releases" / ".staging.v0.11.0.tmp").mkdir(exist_ok=True)
    rc = driver(
        FAKE_DEMO,
        "journal_init\n"
        'journal_set_current "v0.9.0"\n'
        'journal_set_previous "v0.8.0"\n'
        'journal_quarantine "v0.7.0"\n'
        "journal_history_append commit 'promote run-fixture-0001 complete: v0.8.0 -> v0.9.0 (gate green)'\n"
        'atomic_flip "v0.9.0"\n',
    )
    assert rc.returncode == 0, f"demo fixture driver failed: {rc.stderr[-800:]}"
    (FAKE_DEMO / ".launcher-state").write_text(
        "last_exit=0\ncrash_count=0\nwindow_start=1\nlast_backoff=0\n"
        "notified_75=0\nlast_uptime=600\n",
        encoding="utf-8",
    )
    (FAKE_DEMO / ".env").write_text(
        f"ENSEMBLE_SELF_ENV=demo\nPORT={SELF_PORT}\nPOSTGRES_DB=ensemble_demo\n",
        encoding="utf-8",
    )

    # ── FAKE LIVE tree (nonce gate fixture) — journal by lib.sh ──────────────
    for ver in ("v1.0.0", "v1.1.0"):
        _write_manifest(FAKE_LIVE, ver)
    rc = driver(
        FAKE_LIVE,
        "journal_init\n" 'journal_set_current "v1.0.0"\n' 'atomic_flip "v1.0.0"\n',
    )
    assert rc.returncode == 0, f"live fixture driver failed: {rc.stderr[-800:]}"
    # staged marker per D-FA2.3 (launcher.sh exports this; fake PORT — NOT 9797)
    (FAKE_LIVE / ".env").write_text(
        "ENSEMBLE_SELF_ENV=live\nPORT=19797\nPOSTGRES_DB=ensemble_prod\n",
        encoding="utf-8",
    )

    # ── fixture executor payload (S5) ────────────────────────────────────────
    payload = SANDBOX / "fixture_executor_payload.sh"
    payload.write_text(
        "#!/bin/bash\n"
        "# fixture executor payload — dumps received env for allowlist proof\n"
        'out="$1"\n'
        "{\n"
        '  echo "ARGV: $*"\n'
        '  echo "CWD: $(pwd)"\n'
        '  echo "PPID: $PPID"\n'
        '  echo "----ENV-BEGIN----"\n'
        "env | sort\n"
        '  echo "----ENV-END----"\n'
        '} > "$out"\n'
        'echo "FIXTURE-PAYLOAD-STDOUT-LINE (routes to data/upgrade.log)"\n',
        encoding="utf-8",
    )
    payload.chmod(0o755)


# ── S7 guard helpers (read-only) ────────────────────────────────────────────
def lsof_9797() -> str:
    rc = subprocess.run(["lsof", "-nP", "-iTCP:9797", "-sTCP:LISTEN"],
                        capture_output=True, text=True, timeout=15)
    return rc.stdout.strip()


def stat_tree_top(path: Path) -> str:
    """Read-only signature of an install dir's top level (mtime/size/name)."""
    if not path.is_dir():
        return "<absent>"
    lines = []
    st = path.stat()
    lines.append(f"DIR {st.st_mtime_ns} {st.st_size}")
    try:
        for child in sorted(path.iterdir()):
            cst = child.lstat()
            lines.append(f"{child.name} {cst.st_mtime_ns} {cst.st_size}")
    except OSError as exc:
        lines.append(f"<read-error {exc}>")
    return "\n".join(lines)


def git_porcelain() -> str:
    rc = subprocess.run(["git", "-C", str(REPO_ROOT), "status", "--porcelain"],
                        capture_output=True, text=True, timeout=30)
    return rc.stdout


def lsof_port(port: int) -> str:
    rc = subprocess.run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"],
                        capture_output=True, text=True, timeout=15)
    return rc.stdout.strip()


def pgrep_pattern(pattern: str) -> list[str]:
    """INLINE pgrep call (macOS: shell-function collectors silently fail)."""
    rc = subprocess.run(["pgrep", "-f", pattern],
                        capture_output=True, text=True, timeout=15)
    return [ln for ln in rc.stdout.split() if ln]


def pid_dead(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return False
    except ProcessLookupError:
        return True
    except PermissionError:
        return False


# ── Tool invocation helpers ─────────────────────────────────────────────────
def make_tools(mgr: _ManagerFacade) -> dict:
    tools = create_upgrade_tools(mgr, INSTANCE_ID, agent_id="ari", agent_tag="v2")
    return {t.name: t for t in tools}


async def call(tools: dict, name: str, **args: Any) -> str:
    return await tools[name].ainvoke(dict(args))


def set_self_env(value: str | None) -> None:
    if value is None:
        os.environ.pop("ENSEMBLE_SELF_ENV", None)
    else:
        os.environ["ENSEMBLE_SELF_ENV"] = value


def journal_bytes(install: Path) -> bytes:
    return uj.journal_path(install).read_bytes()


def parse_status_sh(out: str) -> dict[str, Any]:
    """Field-parse status.sh output (strip the upgrade-status[sbx]: tag)."""
    fields: dict[str, Any] = {"releases": []}
    lines = out.splitlines()
    for i, raw in enumerate(lines):
        line = re.sub(r"^upgrade-status\[[a-z]+\]: ", "", raw)
        m = re.match(r"resolved env: target=(\S+) dir=(\S+) port=(\S+) db=(\S+)", line)
        if m:
            fields["env"] = m.groups()
        if line.startswith("journal (") and i + 1 < len(lines):
            try:
                fields["journal"] = json.loads(lines[i + 1])
            except json.JSONDecodeError:
                pass
        m = re.match(r"current -> (.+)$", line)
        if m:
            fields["current"] = m.group(1)
        if line.startswith("pipeline lock:"):
            fields["lock"] = line
        m = re.match(r"(\S+)\s+rollback_safe=(\S+)(.*)$", line.strip())
        if m and "[protocol artifact" not in line:
            fields["releases"].append(
                (m.group(1), m.group(2), "QUARANTINED" in m.group(3))
            )
        if "[protocol artifact" in line:
            fields.setdefault("artifacts", []).append(line.strip())
        if "/livez" in line:
            fields["livez_line"] = line.strip()
    return fields


# ═══════════════════════════ SCENARIOS ══════════════════════════════════════
async def s1_parity(tools: dict) -> None:
    set_self_env("demo")
    st = parse_status_sh(status_sh(FAKE_DEMO))
    ri = await call(tools, "release_info", target_env="demo", section="all")
    us = await call(tools, "upgrade_status", target_env="demo")

    checks: list[tuple[str, bool, str]] = []
    # dir parity (target/db differ by construction: status runs in sandbox
    # mode, the tool reports its self-env)
    checks.append((
        "dir parity", st["env"][1] == str(FAKE_DEMO) and f"dir={FAKE_DEMO}" in ri,
        f"status dir={st['env'][1]} tool-dir-in-output={f'dir={FAKE_DEMO}' in ri}",
    ))
    j = st["journal"]
    checks.append((
        "journal current/previous", (
            f"current={j['current']}" in ri and f"previous={j['previous']}" in ri
        ),
        f"journal current={j['current']} previous={j['previous']}",
    ))
    checks.append((
        "in-flight none parity", "in-flight=none" in ri and j["in_flight"] is None,
        "both report no in-flight txn",
    ))
    checks.append((
        "rollback window parity",
        "rollbacks_24h=0/3 cooldown=none" in ri,
        f"status window={j['rollback_window_count']} tool line has 0/3+none",
    ))
    q_list = ",".join(f"'{v}'" for v in j["quarantined"])
    checks.append((
        "quarantine parity",
        f"quarantine=[{q_list}]" in ri,
        f"quarantined={j['quarantined']}",
    ))
    cur_name = Path(st["current"]).name
    checks.append((
        "current symlink parity",
        f"current={cur_name} (via releases/current" in ri,
        f"status current -> {st['current']}",
    ))
    checks.append((
        "lock free parity",
        st["lock"] == "pipeline lock: free" and "pipeline lock: free" in ri,
        "both report lock free",
    ))
    for name, rb, quar in st["releases"]:
        line = next(
            (ln for ln in ri.splitlines() if ln.strip().startswith(name + " ")), ""
        )
        ok = bool(line) and f"rollback_safe={rb.lower()}" in line
        if quar:
            ok = ok and "[QUARANTINED]" in line
        checks.append((f"release {name}", ok,
                       f"status rollback_safe={rb} quar={quar} tool-line={'✓' if line else 'MISSING'}"))
    staging_ok = any(
        ".staging.v0.11.0.tmp" in ln and "[protocol artifact" in ln
        for ln in ri.splitlines()
    ) and any(".staging.v0.11.0.tmp" in a for a in st.get("artifacts", []))
    checks.append((
        "protocol artifact parity (.staging)", staging_ok,
        "both label .staging.v0.11.0.tmp as protocol artifact, not a release",
    ))
    hist = j["history"][-1]
    hist_detail = f"{hist['event']} — {hist['detail']}"
    checks.append((
        "history event parity", hist_detail in ri,
        f"commit detail match: {hist['detail'][:60]}",
    ))
    checks.append((
        "upgrade_status TERMINAL", "TERMINAL" in us and "outcome=committed" in us,
        "upgrade_status derives TERMINAL outcome=committed from same history",
    ))
    checks.append((
        "livez not-answering parity (dead :10797)",
        "daemon :10797 /livez: not answering" in st.get("livez_line", "")
        and "/livez: not answering" in ri,
        f"status: {st.get('livez_line', '')[:70]}",
    ))
    failed = [c for c in checks if not c[1]]
    if failed:
        record("S1", "FAIL", "; ".join(f"{n}: {e}" for n, _, e in failed))
    else:
        record("S1", "PASS",
               f"release_info/upgrade_status vs status.sh on the SAME lib.sh-written "
               f"fixture: {len(checks)}/{len(checks)} field checks green "
               f"(current/previous/in-flight/rollback-window/quarantine/5 releases/"
               "artifacts/current symlink/lock/history/livez-degrade)")


async def s2_live_restart_refusal(tools: dict, mgr: _ManagerFacade) -> None:
    set_self_env("live")
    before = journal_bytes(FAKE_LIVE)
    # all-factors-satisfied case: HUMAN origin stamped + user_confirmed + valid
    # nonce + dry_run=false — still refused outright, before any gate logic.
    mgr.stamp_user_origin_window(INSTANCE_ID, "api", "msg-s2")
    out = await call(tools, "system_restart", target_env="live",
                     reason="p2.2 dynamic sandbox", user_confirmed=True,
                     mode="graceful-now", nonce="CONFIRM-AAAAAAAA", dry_run=False)
    r1 = reason_of(out)
    out_dry = await call(tools, "system_restart", target_env="live",
                         reason="p2.2 dynamic sandbox", dry_run=True)
    r2 = reason_of(out_dry)
    unchanged = journal_bytes(FAKE_LIVE) == before
    no_lock = not uj.lock_dir(FAKE_LIVE).exists()
    no_marker = not mgr._pending_system_executions
    if (r1, r2) == ("live-restart-refused", "live-restart-refused") \
            and unchanged and no_lock and no_marker:
        record("S2", "PASS",
               "target_env=live refused outright with reason=live-restart-refused "
               "even with user_confirmed=true + stamped HUMAN origin + nonce + "
               f"dry_run=false (and in dry_run too); fake-live journal bytes "
               f"unchanged={unchanged}, no lock, no execution marker")
    else:
        record("S2", "FAIL",
               f"armed-reason={r1} dry-reason={r2} journal-unchanged={unchanged} "
               f"no-lock={no_lock} no-marker={no_marker}")


async def s3_taxonomy(tools: dict, mgr: _ManagerFacade) -> None:
    fails: list[str] = []
    ev: list[str] = []

    # (1) invalid target env — both actor tools
    set_self_env("live")
    r = reason_of(await call(tools, "system_restart", target_env="prod",
                             reason="x", dry_run=False))
    if r != "invalid-target-env":
        fails.append(f"invalid-target-env(restart)={r}")
    r = reason_of(await call(tools, "system_upgrade", target_env="Production",
                             dry_run=False))
    if r != "invalid-target-env":
        fails.append(f"invalid-target-env(upgrade)={r}")
    ev.append("invalid-target-env ×2 (prod/Production on restart+upgrade)")

    # (2) env-marker-absent (bonus dynamic: fail-closed actor gate)
    set_self_env(None)
    r = reason_of(await call(tools, "system_restart", target_env="demo",
                             reason="x", dry_run=False))
    if r != "env-marker-absent":
        fails.append(f"env-marker-absent={r}")
    ev.append("env-marker-absent (marker unset → actor refuses fail-closed)")

    # (3) env-self-match: a demo daemon can NEVER address live
    set_self_env("demo")
    r = reason_of(await call(tools, "system_upgrade", target_env="live",
                             version="v1.1.0", dry_run=False))
    if r != "env-self-match":
        fails.append(f"env-self-match={r}")
    ev.append("env-self-match (self_env=demo, target=live → structural refusal)")

    # live-tree cases below
    set_self_env("live")
    mgr._queue_repository.rows.clear()
    mgr.stamp_user_origin_window(INSTANCE_ID, "api", "msg-s3")

    # mint a REAL nonce via the live dry_run preflight (real nonce store)
    dry = await call(tools, "system_upgrade", target_env="live",
                     version="v1.1.0", dry_run=True)
    m = re.search(r"nonce (CONFIRM-[A-Z2-7]{4}-[A-Z2-7]{4})", dry)
    if not m:
        fails.append(f"live dry_run mint failed: {dry[:200]}")
        nonce = "CONFIRM-00000000"
    else:
        nonce = m.group(1)

    # (4) missing user_confirmed (fresh unconsumed nonce + HUMAN window)
    r = reason_of(await call(tools, "system_upgrade", target_env="live",
                             version="v1.1.0", user_confirmed=False,
                             nonce=nonce, dry_run=False))
    if r != "user-confirmation-missing":
        fails.append(f"user-confirmation-missing={r}")
    ev.append("user-confirmation-missing (param false, nonce+window green)")

    # (5) spoofed/api-origin through the REAL origin-stamping path:
    # stamp api, then a scheduler turn — the REAL stamp method POPS the
    # window (per-turn semantics); the gate then sees no HUMAN marker.
    mgr.stamp_user_origin_window(INSTANCE_ID, "api", "msg-s3b")
    had = INSTANCE_ID in mgr._user_origin_windows
    mgr.stamp_user_origin_window(INSTANCE_ID, "scheduler", "msg-s3c")
    popped = INSTANCE_ID not in mgr._user_origin_windows
    out = await call(tools, "system_upgrade", target_env="live",
                     version="v1.1.0", user_confirmed=True,
                     nonce=nonce, dry_run=False)
    r = reason_of(out)
    if r != "user-confirmation-missing" or "whitelisted user-origin" not in out:
        fails.append(f"spoofed-origin={r}")
    ev.append(f"spoofed-origin (real stamp: api→scheduler POPS window "
              f"[stamped={had}, popped={popped}] → gate refuses: no HUMAN marker)")

    # (6) nonce mismatch (fresh HUMAN window so the ORIGIN factor is green
    # and the refusal provably comes from the nonce factor)
    mgr.stamp_user_origin_window(INSTANCE_ID, "api", "msg-s3e")
    r = reason_of(await call(tools, "system_upgrade", target_env="live",
                             version="v1.1.0", user_confirmed=True,
                             nonce="CONFIRM-AAAAAAAA", dry_run=False))
    if r != "nonce-mismatch":
        fails.append(f"nonce-mismatch={r}")
    ev.append("nonce-mismatch (well-formed unknown nonce)")

    # (7) expired nonce — real store first (a past-TTL record is GC'd by
    # store_pending_action itself — opportunistic GC drops provably-dead
    # entries on write), then the ttl is aged out via the REAL atomic
    # journal writer (read-modify-write, the twin of time passing).
    stale = uj.PendingAction(
        run_id="run-expired-probe", nonce=uj.mint_nonce(), kind="upgrade",
        env="live", target="v1.1.0", issued_to_instance=INSTANCE_ID,
    )
    uj.store_pending_action(FAKE_LIVE, stale)
    data = uj.journal_read(FAKE_LIVE)
    data["pending_actions"][stale.run_id]["ttl_expires_at"] = uj.iso_plus(
        uj.now_iso(), -3600)
    uj.journal_write(FAKE_LIVE, data)
    mgr.stamp_user_origin_window(INSTANCE_ID, "api", "msg-s3d")
    r = reason_of(await call(tools, "system_upgrade", target_env="live",
                             version="v1.1.0", user_confirmed=True,
                             nonce=stale.nonce, dry_run=False))
    if r != "nonce-expired":
        fails.append(f"nonce-expired={r}")
    ev.append("nonce-expired (real-stored record aged out via the real atomic "
              "journal writer, TTL 1h in the past)")

    if fails:
        record("S3", "FAIL", "; ".join(fails))
    else:
        record("S3", "PASS",
               f"{len(ev)} dynamic refusal cases, all with the distinct reason "
               f"token: " + "; ".join(ev))


async def s4_three_factor(tools: dict, mgr: _ManagerFacade) -> None:
    set_self_env("live")
    mgr._queue_repository.rows.clear()
    mgr.stamp_user_origin_window(INSTANCE_ID, "api", "msg-live-confirm")

    # Factor setup: nonce minted via the REAL live dry_run preflight
    dry = await call(tools, "system_upgrade", target_env="live",
                     version="v1.1.0", dry_run=True)
    m = re.search(r"nonce (CONFIRM-[A-Z2-7]{4}-[A-Z2-7]{4})", dry)
    mr = re.search(r"pending-action \(run_id=(\S+)\)", dry)
    if not (m and mr):
        record("S4", "FAIL", f"could not parse mint from dry_run: {dry[:300]}")
        return
    nonce, mint_run = m.group(1), mr.group(1)

    # nonce record bound to (kind, env, target, instance) via the real store
    rec = None
    data = uj.journal_read(FAKE_LIVE)
    for entry in data.get("pending_actions", {}).values():
        if entry.get("nonce", "").replace("-", "") == nonce.replace("-", ""):
            rec = entry
    bound = bool(rec) and rec["kind"] == "upgrade" and rec["env"] == "live" \
        and rec["target"] == "v1.1.0" and rec["issued_to_instance"] == INSTANCE_ID \
        and rec.get("consumed_at") is None

    # Factor 3: the triggering HUMAN message CONTENT carries the nonce
    mgr._queue_repository.rows["msg-live-confirm"] = _FakeRow(
        f"yes, please upgrade to v1.1.1. my nonce: {nonce}"
    )
    out = await call(tools, "system_upgrade", target_env="live",
                     version="v1.1.0", user_confirmed=True, nonce=nonce,
                     dry_run=False)
    scheduled = "UPGRADE SCHEDULED" in out
    same_run = f"run_id={mint_run} env=live" in out
    op = uj.read_pending_op(FAKE_LIVE)
    lock_run = uj.lock_run_id(FAKE_LIVE)
    data = uj.journal_read(FAKE_LIVE)
    consumed = data.get("pending_actions", {}).get(mint_run, {}).get("consumed_at")
    consumed_by = data.get("pending_actions", {}).get(mint_run, {}).get(
        "consumed_by_message_id")
    op_ok = op is not None and op.run_id == mint_run and op.nonce.replace(
        "-", "") == nonce.replace("-", "") and op.confirmed_by_human \
        and op.confirmed_source == "api"
    lock_ok = lock_run == mint_run

    # identical replay → refused (nonce single-use)
    replay = await call(tools, "system_upgrade", target_env="live",
                        version="v1.1.0", user_confirmed=True, nonce=nonce,
                        dry_run=False)
    replay_reason = reason_of(replay)

    ok = all([scheduled, same_run, bound, op_ok, lock_ok, bool(consumed),
              consumed_by == "msg-live-confirm",
              replay_reason == "nonce-already-used"])
    if ok:
        record("S4", "PASS",
               f"3-factor PASS on FAKE live marker: nonce minted by real dry_run "
               f"(bound kind/env/target/instance={bound}), armed call SCHEDULED "
               f"with SAME run_id={mint_run}; pending_op human-confirmed "
               f"(source=api), lock run_id match, nonce consumed "
               f"(by msg-live-confirm); identical replay → nonce-already-used")
    else:
        record("S4", "FAIL",
               f"scheduled={scheduled} same_run={same_run} bound={bound} "
               f"op_ok={op_ok} lock_ok={lock_ok} consumed={bool(consumed)} "
               f"consumed_by={consumed_by} replay={replay_reason}")
    # cleanup: release the arm-time lock (test-process owned) — sandbox-only
    uj.lock_release(FAKE_LIVE)


async def s5_spawn_proof(tools: dict, mgr: _ManagerFacade) -> tuple[str, int] | None:
    set_self_env("demo")
    # Simulate the post-turn drain: S4's promote marker was consumed at ITS
    # turn end in production (the daemonized promote.sh fired); the marker
    # store is per-turn, so clear it before arming the restart turn.
    mgr._pending_system_executions.clear()
    before = journal_bytes(FAKE_DEMO)

    # dry_run default TRUE → zero mutation
    preview = await call(tools, "system_restart", target_env="demo",
                         reason="p2.2 s5 preview")
    zero_mut = (
        "RESTART PREVIEW (dry-run)" in preview
        and journal_bytes(FAKE_DEMO) == before
        and not uj.lock_dir(FAKE_DEMO).exists()
        and not mgr._pending_system_executions
    )

    # ARM for real (demo is free — journaled + lock-protected)
    armed = await call(tools, "system_restart", target_env="demo",
                       reason="p2.2 dynamic sandbox S5", dry_run=False)
    m = re.search(r"RESTART SCHEDULED — run_id=(\S+)", armed)
    if not m:
        record("S5", "FAIL", f"demo arm refused/failed: {armed[:300]}")
        return None
    run_id = m.group(1)
    data = uj.journal_read(FAKE_DEMO)
    op = uj.read_pending_op(FAKE_DEMO)
    arm_ok = (
        data.get("in_flight", {}).get("kind") == "restart"
        and data.get("in_flight", {}).get("run_id") == run_id
        and op is not None and op.run_id == run_id
        and data.get("pending_restart") == run_id
        and uj.lock_run_id(FAKE_DEMO) == run_id
        and mgr._pending_system_executions.get(INSTANCE_ID, {}).get("run_id") == run_id
    )

    # REAL spawn_executor with a FIXTURE payload; poison sentinels in the
    # PARENT env first — the allowlist must strip them in the spawned child.
    for k, v in POISON_VARS.items():
        os.environ[k] = v
    dump = SANDBOX / "s5-child-env.txt"
    if dump.exists():
        dump.unlink()
    argv = ["/bin/bash", str(SANDBOX / "fixture_executor_payload.sh"), str(dump)]
    try:
        child_pid = uj.spawn_executor(
            argv, FAKE_DEMO,
            {"INSTALL_DIR": str(FAKE_DEMO), "PORT": str(SELF_PORT)},
        )
    finally:
        for k in POISON_VARS:
            os.environ.pop(k, None)
        set_self_env("demo")  # POISON_VARS included ENSEMBLE_SELF_ENV

    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and not dump.exists():
        await asyncio.sleep(0.2)
    if not dump.exists():
        record("S5", "FAIL", f"env dump never appeared (child pid={child_pid})")
        return run_id
    await asyncio.sleep(0.5)  # let the payload finish writing + exiting
    text = dump.read_text(encoding="utf-8", errors="replace")
    env_block = text.split("----ENV-BEGIN----", 1)[-1].split("----ENV-END----", 1)[0]
    child_env: dict[str, str] = {}
    for ln in env_block.splitlines():
        if "=" in ln:
            k, _, v = ln.partition("=")
            child_env[k] = v
    cwd_line = next((l for l in text.splitlines() if l.startswith("CWD:")), "")
    argv_line = next((l for l in text.splitlines() if l.startswith("ARGV:")), "")

    for var, val in POISON_VARS.items():
        parent = "yes" if os.environ.get(var) is not None else "yes(set-at-spawn)"
        in_child = var in child_env
        if var.startswith("PG"):
            verdict, ok = "PRESENT (by design: PG* prefix allowlist)", True
        else:
            verdict, ok = ("PRESENT — LEAK!" if in_child else "stripped", not in_child)
        S5_TABLE.append((var, parent, "yes" if in_child else "no", verdict))
        if not ok:
            record("S5", "FAIL", f"env allowlist LEAK: {var} present in child")
            return run_id

    extras_ok = (
        child_env.get("INSTALL_DIR") == str(FAKE_DEMO)
        and child_env.get("PORT") == str(SELF_PORT)
        and "PATH" in child_env
        and Path(cwd_line.partition(": ")[2] or "?").resolve() == FAKE_DEMO.resolve()
        and argv_line == f"ARGV: {dump}"
    )
    log_path = FAKE_DEMO / "data" / "upgrade.log"
    log_ok = log_path.is_file() and "FIXTURE-PAYLOAD-STDOUT-LINE" in log_path.read_text(
        encoding="utf-8", errors="replace")
    child_gone = False
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            # we are the parent: reap the (fast-exiting) child; WNOHANG
            # returns (0,0) while it still runs, ChildProcessError once gone
            pid_w, _st = os.waitpid(child_pid, os.WNOHANG)
            if pid_w == child_pid:
                child_gone = True
                break
        except ChildProcessError:
            child_gone = True
            break
        await asyncio.sleep(0.2)

    ok = zero_mut and arm_ok and extras_ok and log_ok and child_gone
    if ok:
        record("S5", "PASS",
               f"demo arm (run_id={run_id}) + REAL spawn_executor: dry_run default "
               f"left ZERO mutation={zero_mut}; armed journal/lock/marker green="
               f"{arm_ok}; spawned child (pid={child_pid}) env dump proves all "
               f"{len(POISON_VARS)} poison vars stripped (PG* passthrough by "
               "design); INSTALL_DIR/PORT extras delivered, argv intact, "
               f"cwd=install dir (resolved), stdio→data/upgrade.log, child "
               "exited cleanly")
    else:
        record("S5", "FAIL",
               f"zero_mut={zero_mut} arm_ok={arm_ok} extras_ok={extras_ok} "
               f"log_ok={log_ok} child_exited={child_gone}")
    return run_id


async def s6_journal_poll(tools: dict, run_id: str | None) -> None:
    if not run_id:
        record("S6", "FAIL", "no armed run_id from S5")
        return
    set_self_env("demo")
    # Fake executor completion — mirrors restart.sh §5 finalize EXACTLY, via
    # the REAL lib.sh writers (lock adoption → close txn → clear markers →
    # 'restart' history event → release).
    body = (
        f'LOCK="$(lock_dir_path)"\n'
        f'[ -d "$LOCK" ] || {{ echo "LOCK MISSING"; exit 1; }}\n'
        f'LOCK_RUN="$(cat "$LOCK/run_id" 2>/dev/null)"\n'
        f'[ "$LOCK_RUN" = "{run_id}" ] || {{ echo "LOCK RUN MISMATCH: $LOCK_RUN"; exit 1; }}\n'
        f'printf \'%s\\n\' "$$" > "$LOCK/owner"\n'
        f"lock_heartbeat\n"
        f"journal_close_txn\n"
        f'journal_update "pending_op" "null"\n'
        f'journal_update "pending_restart" "null"\n'
        f'journal_history_append restart \'intentional restart run_id={run_id} complete (reason: p2.2 dynamic sandbox; SINGLE-TERM + launcher re-exec + /livez gate green)\'\n'
        f"lock_release\n"
    )
    rc = driver(FAKE_DEMO, body)
    if rc.returncode != 0:
        record("S6", "FAIL", f"completion driver rc={rc.returncode}: {rc.stderr[-400:]}")
        return

    out = await call(tools, "upgrade_status", target_env="demo", run_id=run_id)
    same_run = run_id in out
    terminal = "TERMINAL" in out and "restarted (intentional)" in out
    not_active = "not active — terminal events in tail below" in out
    lock_free = "pipeline lock: free" in out
    if same_run and terminal and not_active and lock_free:
        record("S6", "PASS",
               f"after fake-executor completion (real lib.sh finalize), "
               f"upgrade_status(run_id={run_id}) → TERMINAL outcome=restarted "
               f"(intentional) with the SAME run_id round-trip; lock free; "
               "softened 'not active' note instead of unknown-run error")
    else:
        record("S6", "FAIL",
               f"same_run={same_run} terminal={terminal} not_active={not_active} "
               f"lock_free={lock_free}; output head: {out[:300]}")


def s7_guards(porcelain_before: str) -> None:
    problems: list[str] = []
    a = S7_AFTER
    if a[0] != S7_BEFORE["lsof9797"]:
        problems.append(f"lsof 9797 changed: {S7_BEFORE['lsof9797']!r} → {a[0]!r}")
    if a[1] != S7_BEFORE["live_stat"]:
        problems.append("real ~/agents-ensemble top-level stat CHANGED")
    if a[2] != S7_BEFORE["demo_stat"]:
        problems.append("real ~/agents-ensemble-demo top-level stat CHANGED")
    # (c) fixture-path containment: every artifact the scenarios touched
    # (macOS: /tmp is a symlink to /private/tmp — compare RESOLVED paths)
    sandbox_r = str(SANDBOX.resolve())
    paths = [
        FAKE_LIVE, FAKE_DEMO,
        uj.journal_path(FAKE_LIVE), uj.journal_path(FAKE_DEMO),
        FAKE_DEMO / "data" / "upgrade.log", SANDBOX / "s5-child-env.txt",
        PARITY_HOME, SANDBOX / "fixture_executor_payload.sh",
    ]
    escaped = [str(p) for p in paths
               if not str(p.resolve()).startswith(sandbox_r)]
    if escaped:
        problems.append(f"paths escaped sandbox: {escaped}")
    if not str(FAKE_LIVE.resolve()).startswith(sandbox_r):
        problems.append("fake live tree not under sandbox")
    # (d) leaks: ports + processes
    if lsof_port(SELF_PORT):
        problems.append(f"port {SELF_PORT} still bound")
    leaks = pgrep_pattern("fixture_executor_payload")
    if leaks:
        problems.append(f"leaked payload processes: {leaks}")
    # repo worktree: shared with parallel workers — diff is a WARNING, not FAIL
    porcelain_after = git_porcelain()
    repo_note = "unchanged"
    if porcelain_after != porcelain_before:
        repo_note = (f"WARNING: git status changed during run (shared worktree — "
                     f"parallel verification workers); first-line-before="
                     f"{porcelain_before.splitlines()[:1]}, after="
                     f"{porcelain_after.splitlines()[:1]}")
    if problems:
        record("S7", "FAIL", "; ".join(problems) + f" [{repo_note}]")
    else:
        record("S7", "PASS",
               f"(a) lsof 9797 identical before/after; (b) real "
               f"~/agents-ensemble + ~/agents-ensemble-demo stat unchanged; "
               f"(c) all {len(paths)} fixture/journal/log paths resolve under "
               f"{SANDBOX}; (d) no leaked processes/ports; repo porcelain "
               f"{repo_note}")


# ═══════════════════════════ MAIN ═══════════════════════════════════════════
async def main() -> int:
    print("=== Test Pack: upgrade_tools_live_safety_mock (P2.2 dynamic sandbox) ===",
          flush=True)
    print(f"date: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}", flush=True)
    print(f"repo: {REPO_ROOT}  sandbox: {SANDBOX}", flush=True)

    saved_env = {k: os.environ.get(k) for k in _SAVED_ENV_KEYS}
    porcelain_before = git_porcelain()
    S7_BEFORE["lsof9797"] = lsof_9797()
    S7_BEFORE["live_stat"] = stat_tree_top(Path.home() / "agents-ensemble")
    S7_BEFORE["demo_stat"] = stat_tree_top(Path.home() / "agents-ensemble-demo")
    print(f"S7 before: lsof9797={S7_BEFORE['lsof9797']!r}", flush=True)
    print(f"S7 before: live-stat-lines={len(S7_BEFORE['live_stat'].splitlines())} "
          f"demo-stat-lines={len(S7_BEFORE['demo_stat'].splitlines())}", flush=True)

    exit_code = 1
    try:
        # HOME redirect FIRST: every Path.home() in the real resolver lands
        # inside the sandbox (this is what keeps _resolve_install_dir("live")
        # on the FAKE tree — the REAL resolution code, a fake target).
        os.environ["HOME"] = str(FAKE_HOME)
        FAKE_HOME.mkdir(parents=True, exist_ok=True)
        build_fixtures()

        mgr = _ManagerFacade()
        tools = make_tools(mgr)
        names = sorted(tools)
        assert set(names) >= {"release_info", "upgrade_status",
                              "system_restart", "system_upgrade"}, names

        await s1_parity(tools)
        await s2_live_restart_refusal(tools, mgr)
        await s3_taxonomy(tools, mgr)
        await s4_three_factor(tools, mgr)
        run_id = await s5_spawn_proof(tools, mgr)
        await s6_journal_poll(tools, run_id)
        # S7(b) stats the REAL home (saved before the HOME redirect), not the
        # redirected sandbox fake-home.
        real_home = Path(saved_env["HOME"]) if saved_env["HOME"] else None
        S7_AFTER.append(lsof_9797())
        S7_AFTER.append(
            stat_tree_top(real_home / "agents-ensemble") if real_home else "<absent>")
        S7_AFTER.append(
            stat_tree_top(real_home / "agents-ensemble-demo") if real_home
            else "<absent>")
        s7_guards(porcelain_before)

        blocked = [r for r in RESULTS if r[1] == "BLOCKED"]
        failed = [r for r in RESULTS if r[1] == "FAIL"]
        if blocked or failed:
            print(f"RESULT: FAIL ({len(RESULTS) - len(failed) - len(blocked)}/"
                  f"{len(RESULTS)} scenarios passed)", flush=True)
        else:
            print(f"RESULT: PASS ({len(RESULTS)}/{len(RESULTS)} scenarios)", flush=True)
            exit_code = 0
    finally:
        # restore env + release any test-owned locks (sandbox-contained anyway)
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        try:
            uj.lock_release(FAKE_LIVE)
            uj.lock_release(FAKE_DEMO)
        except Exception:
            pass
        # fixture cleanup: remove sandbox trees (keep the script itself)
        ok = all(r[1] == "PASS" for r in RESULTS)
        if ok:
            for p in (FAKE_HOME, PARITY_HOME, SANDBOX / "s5-child-env.txt"):
                shutil.rmtree(p, ignore_errors=True) if p.is_dir() else (
                    p.unlink(missing_ok=True) if p.exists() else None)
            print("cleanup: fixture sandbox removed (script kept in /tmp/p22-dynamic-sandbox/)",
                  flush=True)
        else:
            print("cleanup: FAIL run — fixture sandbox KEPT for inspection "
                  f"({SANDBOX})", flush=True)

    print("\n--- S5 env-allowlist proof table (parent → spawned child) ---",
          flush=True)
    for var, parent, child, verdict in S5_TABLE:
        print(f"  {var:<26} parent={parent:<20} child={child:<4} {verdict}", flush=True)
    print("\n--- S7 before/after ---", flush=True)
    print(f"  lsof 9797 before: {S7_BEFORE['lsof9797']!r}", flush=True)
    print(f"  lsof 9797 after : {S7_AFTER[0] if S7_AFTER else '<not reached>'!r}", flush=True)
    print(f"  real ~/agents-ensemble stat identical: "
          f"{S7_BEFORE['live_stat'] == (S7_AFTER[1] if len(S7_AFTER) > 1 else None)}",
          flush=True)
    print(f"  real ~/agents-ensemble-demo stat identical: "
          f"{S7_BEFORE['demo_stat'] == (S7_AFTER[2] if len(S7_AFTER) > 2 else None)}",
          flush=True)
    return exit_code


if __name__ == "__main__":
    code = asyncio.run(main())
    sys.exit(code)
