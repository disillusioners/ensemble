"""C4 — a degraded MCP server degrades ALONE and is reportable (integration).

WHAT THIS PIN PROVES
--------------------
A built-in MCP server whose init keeps failing must NOT take the
daemon down, must NOT flip a readiness component, and MUST be visible
in the ``/readyz`` advisory map (``detail.mcp``) with the reason, the
timestamp and the ``recoverable`` answer. After the breaker's recovery
window the server can come back WITHOUT a daemon restart.

THE LANE
--------
A CHILD PROCESS boots the REAL daemon (``create_app()`` + the REAL
``lifespan``) over a DISPOSABLE PostgreSQL database and probes
``/livez`` + ``/readyz`` through the in-process ASGI transport.

Why a child process: the pytest root conftest injects MOCK ``mcp`` /
``langchain_mcp_adapters`` modules, so a real subprocess and a real
MCP handshake are unreachable from inside pytest. A plain python child
has no conftest, hence no mocks.

Why a probe server and not the ``context7`` built-in: the built-in's
command is ``npx -y @upstash/context7-mcp`` — network-dependent, so it
cannot be a deterministic pin. The probe server exercises the SAME
init path (the pool's spawn + handshake + the per-boot breaker) and is
keyed under its own name, so it can never collide with a built-in.

FENCES (binding)
-----------------
* the database is a DISPOSABLE scratch database (a throwaway LOGIN
  role + an owned database), created and DROPPED by this file;
* the ambient ``POSTGRES_*`` values are NEVER trusted — the five
  variables are pinned PER-VARIABLE to the disposable database (the
  ``env -u POSTGRES_*`` glob idiom is a silent no-op — house critical
  note);
* the child REFUSES to boot unless the resolved database name is
  scratch-shaped;
* ``HOME`` is a fresh mktemp directory — no live-shaped path is ever
  resolved;
* the child exits non-zero on a failed assertion: a crashed probe is a
  FAILED pin, never a silent pass.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tests"))

from helpers.nestops_pg_fixtures import (  # noqa: E402
    assert_scratch_db,
    disposable_db,
    pin_scratch_postgres_env,
    probe_admin_engine,
)

#: The probe server's name. It is deliberately NOT a built-in name.
PROBE_SERVER = "brokenprobe"

#: The probe server's command scripts (written into the child's HOME).
FAILING_SERVER_SCRIPT = (
    "import sys\n"
    "sys.stderr.write('probe server: failing on purpose\\n')\n"
    "sys.stderr.flush()\n"
    "raise SystemExit(3)\n"
)

HEALTHY_SERVER_SCRIPT = (
    "from mcp.server.fastmcp import FastMCP\n"
    "app = FastMCP('probe')\n"
    "@app.tool()\n"
    "def ping() -> str:\n"
    "    return 'pong'\n"
    "app.run()\n"
)

#: The child probe body. It boots the REAL daemon and returns a JSON
#: payload with every observable the pin asserts on.
PROBE_SCRIPT = f'''
import asyncio, json, os, sys, time

sys.path.insert(0, {str(REPO_ROOT)!r})

# ── the scratch-shape refusal (the child's own fence) ────────────────
_db = os.environ.get("POSTGRES_DB", "")
if "test" not in _db.lower() and "scratch" not in _db.lower():
    print(json.dumps({{"error": "refusing to boot against a non-scratch database: " + _db}}))
    raise SystemExit(3)

import httpx
from daemon.api import create_app, lifespan
from daemon.mcp import init_guard
from daemon.mcp.config import McpStdioConfig
from daemon.mcp.warmup_pool import get_mcp_warmup_pool

# The recovery window is SHORT so the recovery leg is bounded.
init_guard.INIT_RECOVERY_TIMEOUT_S = 1.0

FAILING = {FAILING_SERVER_SCRIPT!r}
HEALTHY = {HEALTHY_SERVER_SCRIPT!r}
PROBE = {PROBE_SERVER!r}

_home = os.environ["HOME"]
_fail_path = os.path.join(_home, "failing_server.py")
_ok_path = os.path.join(_home, "healthy_server.py")
with open(_fail_path, "w") as fh:
    fh.write(FAILING)
with open(_ok_path, "w") as fh:
    fh.write(HEALTHY)


async def _drive_failures(pool, count):
    """Drive REAL failing spawns through the pool's init path."""
    errors = []
    for _ in range(count):
        try:
            await pool._create_pooled_connection(PROBE)
        except Exception as exc:
            errors.append(type(exc).__name__)
    return errors


async def main():
    app = create_app()
    result = {{}}
    async with lifespan(app):
        pool = get_mcp_warmup_pool()
        pool.register_server(
            PROBE,
            McpStdioConfig(
                transport="stdio",
                command=sys.executable,
                args=[_fail_path],
            ),
            pool_size=1,
        )

        # ── the permanently broken server ─────────────────────────
        errors = await _drive_failures(pool, 3)
        result["spawn_errors"] = errors
        result["quarantined"] = init_guard.is_quarantined(PROBE)
        result["can_attempt_after_trip"] = await init_guard.can_attempt(PROBE)

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://daemon"
        ) as client:
            livez = readyz = None
            body = {{}}
            # The readiness composite is CACHED and refreshed on the
            # refresher's cadence — poll until the advisory map carries
            # the quarantine (a single early read would see the
            # pre-quarantine snapshot and the pin would be vacuous).
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                await asyncio.sleep(0.5)
                try:
                    livez = (await client.get("/livez")).status_code
                except Exception as exc:
                    livez = repr(exc)
                try:
                    resp = await client.get("/readyz")
                    readyz = resp.status_code
                    body = resp.json()
                except Exception as exc:
                    readyz = repr(exc)
                published = ((body.get("detail") or {{}}).get("mcp") or {{}})
                if published.get(PROBE, {{}}).get("state") == "quarantined":
                    break
            result["livez"] = livez
            result["readyz"] = readyz
            result["status"] = body.get("status")
            result["components"] = body.get("components")
            result["reasons"] = (body.get("detail") or {{}}).get("reasons")
            result["mcp"] = (body.get("detail") or {{}}).get("mcp")

        # ── the recovery window ───────────────────────────────────
        await asyncio.sleep(1.6)
        result["can_attempt_after_window"] = await init_guard.can_attempt(PROBE)

        # The server "recovers": the command now speaks MCP.
        pool._configs[PROBE] = McpStdioConfig(
            transport="stdio",
            command=sys.executable,
            args=[_ok_path],
        )
        # C-1 fix: the recovery goes through the PRODUCTION gate
        # (``_replenish`` — the only path that reaches the re-probe
        # caller), NOT through a direct ``_create_pooled_connection``
        # call (which would prove the state machine but not the
        # wiring). The loop tolerates the health check winning the
        # single-probe race: whichever caller gets the probe performs
        # it, and the registry must end up at "ok".
        recovered = False
        recover_error = None
        for _ in range(12):
            await pool._replenish(PROBE)
            state = init_guard.state_snapshot().get(PROBE, {{}}).get("state")
            if state == "ok" and not init_guard.is_quarantined(PROBE):
                recovered = True
                break
            await asyncio.sleep(1.2)
        result["recovered"] = recovered
        result["recover_error"] = recover_error
        result["state_after_recovery"] = (
            init_guard.state_snapshot().get(PROBE, {{}}).get("state")
        )
        result["quarantined_after_recovery"] = init_guard.is_quarantined(PROBE)

    print(json.dumps(result, default=str))


asyncio.run(main())
'''


def _child_env(db) -> dict[str, str]:
    """A scrubbed child environment with the scratch DB pinned."""
    env = dict(os.environ)
    # The ambient POSTGRES_* values are removed ONE BY ONE (the
    # ``env -u POSTGRES_*`` glob idiom is a SILENT no-op — house
    # critical note) and then re-pinned to the disposable database.
    for key in (
        "POSTGRES_DB",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "POSTGRES_HOST",
        "POSTGRES_PORT",
        "POSTGRES_URL",
        "DAEMON_PORT",
    ):
        env.pop(key, None)
    env["POSTGRES_HOST"] = os.environ["PG_TEST_HOST"]
    env["POSTGRES_PORT"] = str(os.environ["PG_TEST_PORT"])
    env["POSTGRES_DB"] = db.db_name
    env["POSTGRES_USER"] = db.role
    env["POSTGRES_PASSWORD"] = db.password
    env["HOME"] = tempfile.mkdtemp(prefix="mcp-degrade-alone-home-")
    env["ENSEMBLE_SELF_ENV"] = "dev"
    env["PYTHONPATH"] = str(REPO_ROOT)
    return env


@pytest.fixture
def scratch_pg(monkeypatch):
    """A disposable scratch PostgreSQL database (created + dropped)."""
    admin = probe_admin_engine()
    if admin is None:
        pytest.skip(
            "no PostgreSQL admin engine on "
            f"{os.environ.get('PG_TEST_HOST', 'localhost')}:"
            f"{os.environ.get('PG_TEST_PORT', '5432')} — skipping loudly"
        )
    monkeypatch.setenv("PG_TEST_HOST", os.environ.get("PG_TEST_HOST", "localhost"))
    monkeypatch.setenv("PG_TEST_PORT", os.environ.get("PG_TEST_PORT", "5432"))
    with disposable_db(admin) as db:
        assert_scratch_db(db.db_name, where="integration scratch db")
        pin_scratch_postgres_env(monkeypatch, db)
        yield db


@pytest.mark.timeout(300)
def test_a_broken_mcp_server_degrades_alone_and_is_reportable(scratch_pg):
    """The full C4 acceptance criterion, on a REAL daemon boot."""
    completed = subprocess.run(
        [sys.executable, "-c", PROBE_SCRIPT],
        capture_output=True,
        text=True,
        env=_child_env(scratch_pg),
        cwd=str(REPO_ROOT),
        timeout=280,
        check=False,
    )
    payload = None
    for line in completed.stdout.splitlines():
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
    assert payload is not None, (
        "the probe child printed no JSON payload:\n%s\n--- stderr ---\n%s"
        % (completed.stdout[-4000:], completed.stderr[-4000:])
    )
    assert "error" not in payload, payload

    # (a) the daemon stays UP.
    assert payload["livez"] == 200, payload
    assert payload["readyz"] == 200, payload

    # (b) the readiness components are UNAFFECTED.
    assert payload["components"] == {
        "database": True,
        "queue_freshness": True,
        "services": True,
        "checkpoint_saver": True,
    }, payload

    # (c) the advisory map reports the server, the reason, the timestamp
    #     and the recoverable answer.
    mcp = payload.get("mcp") or {}
    assert PROBE_SERVER in mcp, (
        f"the degraded server must be visible in detail.mcp: {mcp}"
    )
    entry = mcp[PROBE_SERVER]
    assert entry["state"] == "quarantined", entry
    assert entry["reason"], entry
    assert entry["since"], entry
    assert entry["init_attempts"] >= 3, entry
    assert entry["recoverable"] is True, entry
    assert entry["breaker_state"] == "open", entry

    # Degrade ALONE — the other built-ins are untouched.
    for other in ("context7", "webfetch", "plane"):
        if other in mcp:
            assert mcp[other]["state"] in ("ok", "degraded"), mcp

    # (d) after the recovery window the server can return, and the
    #     registry records it.
    assert payload["quarantined"] is True, payload
    assert payload["can_attempt_after_trip"] is False, payload
    assert payload["can_attempt_after_window"] is True, payload
    # (d) after the recovery window the server comes back — through the
    #     PRODUCTION gate (``_replenish``), without a daemon restart.
    assert payload["recovered"] is True, payload
    assert payload["state_after_recovery"] == "ok", payload
    assert payload["quarantined_after_recovery"] is False, payload


def test_the_integration_lane_is_not_vacuous():
    """The house rule: the integration lane must collect a real count.

    ``pyproject.toml``'s ``addopts`` deselects the ``integration``
    marker by default, so a bare run reports "0 failed" with ZERO
    tests executed. This gate asserts the file really collects.
    """
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--override-ini=addopts=",
            "-m",
            "integration",
            "--collect-only",
            "-q",
            str(Path(__file__).resolve()),
        ],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        timeout=300,
        check=False,
    )
    output = completed.stdout + completed.stderr
    collected = 0
    for line in output.splitlines():
        stripped = line.strip()
        if "::" in stripped and stripped.startswith("tests/"):
            collected += 1
    assert collected > 0, (
        "the integration lane collected ZERO tests — the gate would be "
        f"vacuous:\n{output[-2000:]}"
    )
