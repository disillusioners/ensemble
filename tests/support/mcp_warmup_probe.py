"""MCP warmup probe scripts (Part B / B1 · B2 · B3).

These are the *child-process* bodies used by the integration pins in
``tests/integration/test_mcp_warmup_loop_lag.py``,
``tests/integration/test_mcp_warmup_starters.py`` and
``tests/integration/test_mcp_child_liveness.py``.

WHY A CHILD PROCESS
-------------------
The repository's pytest root conftest injects MOCK ``mcp`` / 
``langchain_mcp_adapters`` modules into ``sys.modules`` (so unit tests can
import ``daemon.manager`` without the real SDKs). Those mocks make the whole
MCP client stack fake: ``ClientSession`` is a ``MagicMock``, so a warmup
never spawns a real child and never hangs. The pins below need the REAL
pinned SDK (``mcp==1.27.1``) — a real subprocess, a real handshake that
never completes, a real ``/proc`` census.

A plain ``python -c`` child has NO pytest conftest, hence NO mocks: the
real SDK is what runs. The child prints ONE JSON object on stdout; the
pytest side asserts on it. The child never touches the live daemon, the
live install dir or ``ensemble_prod`` — it only spawns its own children.

FENCES
------
* the child gets a scrubbed environment (no ambient ``POSTGRES_*``) and a
  ``mktemp`` FAKE_HOME;
* the child only ever spawns ``sleep``/``python`` children of its own;
* the child exits non-zero on a failed assertion, so a crashed probe is a
  failed pin, never a silent pass.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

#: A server that never speaks the MCP protocol: it just sleeps.
HANGING_SERVER_SCRIPT = (
    "import time\n"
    "time.sleep(300)\n"
)

#: A REAL MCP server (stdio) exposing one tool — used to prove the
#: discovery/schema cache path still works while another server hangs.
HEALTHY_SERVER_SCRIPT = (
    "from mcp.server.fastmcp import FastMCP\n"
    "app = FastMCP('probe')\n"
    "@app.tool()\n"
    "def ping() -> str:\n"
    "    return 'pong'\n"
    "app.run()\n"
)


def child_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """A scrubbed environment for a probe child.

    The ambient ``POSTGRES_*`` values are removed ONE BY ONE (never with
    the ``env -u POSTGRES_*`` glob idiom, which is a silent no-op), and
    ``HOME`` points at a fresh mktemp directory so no live-shaped path is
    ever resolved.
    """
    env = dict(os.environ)
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
    env["HOME"] = tempfile.mkdtemp(prefix="mcp-warmup-probe-home-")
    env["ENSEMBLE_SELF_ENV"] = "dev"
    env["PYTHONPATH"] = str(repo_root())
    if extra:
        env.update(extra)
    return env


def repo_root() -> Path:
    """The repository root (the parent of ``tests/``)."""
    return Path(__file__).resolve().parents[2]


def run_probe(script: str, *, timeout: float = 180.0) -> dict:
    """Run ``script`` in a child interpreter; return its JSON payload.

    Raises ``AssertionError`` when the child fails or prints no JSON — a
    crashed probe is a FAILED pin, never a silent pass.
    """
    completed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        env=child_env(),
        cwd=str(repo_root()),
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(
            "probe child failed (rc=%d)\n--- stdout ---\n%s\n--- stderr ---\n%s"
            % (completed.returncode, completed.stdout[-4000:], completed.stderr[-4000:])
        )
    payload = None
    for line in completed.stdout.splitlines():
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
    if payload is None:
        raise AssertionError(
            "probe child printed no JSON payload:\n%s" % completed.stdout[-4000:]
        )
    return payload


# ── the three probe bodies ───────────────────────────────────────────────


def loop_lag_script() -> str:
    """Measure event-loop scheduling lag while a HANGING server warms.

    The pool warms a server whose command never speaks the MCP protocol.
    A heartbeat task samples the scheduling lag of the event loop for the
    whole warmup window; the p95 / max are reported (the acceptance
    thresholds live in ``requirements.md`` §4.6, P6).
    """
    return (
        "import asyncio, json, os, sys, time\n"
        "sys.path.insert(0, %r)\n" % str(repo_root())
        + "from daemon.mcp.warmup_pool import McpWarmupPool\n"
        "from daemon.mcp.config import McpStdioConfig\n"
        "import daemon.mcp.warmup_pool as wp\n"
        "# Keep the probe short: the total budget is the mechanism under\n"
        "# test, but 90s is too long for a test run.\n"
        "wp.WARMUP_TOTAL_BUDGET_S = 6.0\n"
        "wp.WARMUP_CREATE_BUDGET_S = 6.0\n"
        "\n"
        "SERVER = %r\n" % HANGING_SERVER_SCRIPT
        + "\n"
        "async def main():\n"
        "    path = os.path.join(os.environ['HOME'], 'hanging_server.py')\n"
        "    with open(path, 'w') as fh:\n"
        "        fh.write(SERVER)\n"
        "    pool = McpWarmupPool()\n"
        "    cfg = McpStdioConfig(command=sys.executable, args=[path])\n"
        "    pool.register_server('hang', cfg, pool_size=1)\n"
        "    lags = []\n"
        "    async def heartbeat():\n"
        "        last = time.monotonic()\n"
        "        while True:\n"
        "            await asyncio.sleep(0.05)\n"
        "            now = time.monotonic()\n"
        "            lag = (now - last - 0.05) * 1000.0\n"
        "            lags.append(lag if lag > 0 else 0.0)\n"
        "            last = now\n"
        "    hb = asyncio.create_task(heartbeat())\n"
        "    t0 = time.monotonic()\n"
        "    try:\n"
        "        await pool.warmup()\n"
        "    finally:\n"
        "        hb.cancel()\n"
        "        try:\n"
        "            await hb\n"
        "        except BaseException:\n"
        "            pass\n"
        "    elapsed = time.monotonic() - t0\n"
        "    ordered = sorted(lags)\n"
        "    p95 = ordered[int(0.95 * (len(ordered) - 1))] if ordered else 0.0\n"
        "    print(json.dumps({\n"
        "        'p95_ms': p95,\n"
        "        'max_ms': max(lags) if lags else 0.0,\n"
        "        'samples': len(lags),\n"
        "        'warmup_elapsed_s': elapsed,\n"
        "    }))\n"
        "\n"
        "asyncio.run(main())\n"
    )


def daemon_boot_loop_lag_script() -> str:
    """LANE B — full daemon boot with an MCP warmup in flight.

    Boots the REAL ``create_app()`` + REAL ``lifespan`` (no ASGI server,
    no pytest conftest — a plain python process), starts a warmup against
    a server that NEVER speaks the MCP protocol, and measures the
    event-loop scheduling lag while the warmup is in flight.

    Also probes ``/livez`` and ``/readyz`` through the in-process ASGI
    transport and records whether they answered inside their budgets.

    The daemon's own MCP warmup runs against the built-in servers, so
    the hang is injected by registering an extra server that hangs
    BEFORE the boot's warmup task starts. The lag numbers are the
    acceptance observable (requirements.md §4.6, P6).
    """
    return (
        "import asyncio, json, os, sys, time\n"
        "sys.path.insert(0, %r)\n" % str(repo_root())
        + "import httpx\n"
        "from daemon.api import create_app, lifespan\n"
        "from daemon.mcp.config import McpStdioConfig\n"
        "from daemon.mcp.warmup_pool import get_mcp_warmup_pool\n"
        "\n"
        "SERVER = %r\n" % HANGING_SERVER_SCRIPT
        + "\n"
        "async def main():\n"
        "    home = os.environ.get('HOME', '/tmp')\n"
        "    path = os.path.join(home, 'hang.py')\n"
        "    with open(path, 'w') as fh:\n"
        "        fh.write(SERVER)\n"
        "    app = create_app()\n"
        "    async with lifespan(app):\n"
        "        pool = get_mcp_warmup_pool()\n"
        "        pool.register_server(\n"
        "            'hang', McpStdioConfig(command=sys.executable, args=[path]),\n"
        "            pool_size=1,\n"
        "        )\n"
        "        lags = []\n"
        "        async def heartbeat():\n"
        "            last = time.monotonic()\n"
        "            while True:\n"
        "                await asyncio.sleep(0.05)\n"
        "                now = time.monotonic()\n"
        "                lag = (now - last - 0.05) * 1000.0\n"
        "                lags.append(lag if lag > 0 else 0.0)\n"
        "                last = now\n"
        "        hb = asyncio.create_task(heartbeat())\n"
        "        warm = asyncio.create_task(pool.warmup())\n"
        "        transport = httpx.ASGITransport(app=app)\n"
        "        async with httpx.AsyncClient(\n"
        "            transport=transport, base_url='http://daemon'\n"
        "        ) as client:\n"
        "            livez = readyz = None\n"
        "            for _ in range(12):\n"
        "                await asyncio.sleep(0.5)\n"
        "                try:\n"
        "                    livez = (await client.get('/livez')).status_code\n"
        "                except Exception:\n"
        "                    livez = -1\n"
        "                try:\n"
        "                    readyz = (await client.get('/readyz')).status_code\n"
        "                except Exception:\n"
        "                    readyz = -1\n"
        "        warm.cancel()\n"
        "        try:\n"
        "            await warm\n"
        "        except BaseException:\n"
        "            pass\n"
        "        hb.cancel()\n"
        "        try:\n"
        "            await hb\n"
        "        except BaseException:\n"
        "            pass\n"
        "        ordered = sorted(lags)\n"
        "        p95 = ordered[int(0.95 * (len(ordered) - 1))] if ordered else 0.0\n"
        "        print(json.dumps({\n"
        "            'p95_ms': p95,\n"
        "            'max_ms': max(lags) if lags else 0.0,\n"
        "            'samples': len(lags),\n"
        "            'livez': livez,\n"
        "            'readyz': readyz,\n"
        "        }))\n"
        "\n"
        "asyncio.run(main())\n"
    )


def starters_script() -> str:
    """Prove the hang-wedge: the starters run even though a server hangs.

    The pool warms TWO servers: one that hangs (never speaks the protocol)
    and one REAL MCP server. ``pool.warmup()`` must RETURN (the total
    budget is the belt-and-braces) and afterwards:

    * the health-check task is alive (``start_health_check``), and
    * the tool-discovery / schema cache of the HEALTHY server is populated
      (the schema-warm starter's observable effect).

    Both observations are what the manager's ``_warmup_and_report`` needs
    in order to reach ``eager_warm_schemas()``.
    """
    return (
        "import asyncio, json, os, sys, time\n"
        "sys.path.insert(0, %r)\n" % str(repo_root())
        + "from daemon.mcp.warmup_pool import McpWarmupPool\n"
        "from daemon.mcp.config import McpStdioConfig\n"
        "import daemon.mcp.warmup_pool as wp\n"
        "wp.WARMUP_TOTAL_BUDGET_S = 6.0\n"
        "wp.WARMUP_CREATE_BUDGET_S = 45.0\n"
        "\n"
        "HANG = %r\n" % HANGING_SERVER_SCRIPT
        + "OK = %r\n" % HEALTHY_SERVER_SCRIPT
        + "\n"
        "async def main():\n"
        "    home = os.environ['HOME']\n"
        "    hang_path = os.path.join(home, 'hang.py')\n"
        "    ok_path = os.path.join(home, 'ok.py')\n"
        "    for path, body in ((hang_path, HANG), (ok_path, OK)):\n"
        "        with open(path, 'w') as fh:\n"
        "            fh.write(body)\n"
        "    pool = McpWarmupPool()\n"
        "    pool.register_server('hang', McpStdioConfig(command=sys.executable, args=[hang_path]), pool_size=1)\n"
        "    pool.register_server('ok', McpStdioConfig(command=sys.executable, args=[ok_path]), pool_size=1)\n"
        "    t0 = time.monotonic()\n"
        "    await pool.warmup()\n"
        "    elapsed = time.monotonic() - t0\n"
        "    pool.start_health_check(60)\n"
        "    task = pool._health_task\n"
        "    health_alive = bool(task is not None and not task.done())\n"
        "    schemas = pool.get_cached_tool_schemas('ok')\n"
        "    await asyncio.sleep(3.0)\n"
        "    survivors = []\n"
        "    for d in os.listdir('/proc'):\n"
        "        if not d.isdigit():\n"
        "            continue\n"
        "        pid = int(d)\n"
        "        if pid == os.getpid():\n"
        "            continue\n"
        "        try:\n"
        "            with open('/proc/%d/cmdline' % pid, 'rb') as fh:\n"
        "                cmd = fh.read()\n"
        "        except OSError:\n"
        "            continue\n"
        "        if b'hang.py' in cmd:\n"
        "            survivors.append(pid)\n"
        "    print(json.dumps({\n"
        "        'warmup_returned': True,\n"
        "        'warmup_elapsed_s': elapsed,\n"
        "        'health_task_alive': health_alive,\n"
        "        'schema_cache_populated': bool(schemas),\n"
        "        'schema_count': len(schemas) if schemas else 0,\n"
        "        'survivors': survivors,\n"
        "    }))\n"
        "\n"
        "asyncio.run(main())\n"
    )


def child_liveness_script() -> str:
    """REAL-SDK census: a closed transport leaves NO live child.

    Uses the REAL pinned MCP SDK (``mcp.client.stdio.stdio_client``), so a
    REAL child process is spawned and the SDK's own process-group shutdown
    (stdin close -> wait -> SIGTERM -> SIGKILL) is what tears it down.
    After the transport is closed the probe censuses ``/proc`` and asserts
    the child is GONE (starttime-checked, PID-recycle safe).

    A control phase proves the census CAN see a live child, so "0
    survivors" is a measurement and not a broken matcher.
    """
    return (
        "import asyncio, json, os, sys, time\n"
        "sys.path.insert(0, %r)\n" % str(repo_root())
        + "from mcp import StdioServerParameters\n"
        "from mcp.client.stdio import stdio_client\n"
        "from daemon.mcp.stdio_wrapper import TaskScopedStdioClient, scan_mcp_child_pids\n"
        "\n"
        "SERVER = %r\n" % HANGING_SERVER_SCRIPT
        + "\n"
        "def stat_of(pid):\n"
        "    try:\n"
        "        with open('/proc/%d/stat' % pid) as fh:\n"
        "            return fh.read()\n"
        "    except (FileNotFoundError, ProcessLookupError, OSError):\n"
        "        return None\n"
        "\n"
        "def live_children(path):\n"
        "    out = []\n"
        "    for pid, starttime in scan_mcp_child_pids(command=sys.executable, args=[path]):\n"
        "        stat = stat_of(pid)\n"
        "        if stat is None:\n"
        "            continue\n"
        "        try:\n"
        "            if int(stat.split()[21]) == starttime:\n"
        "                out.append(pid)\n"
        "        except (IndexError, ValueError):\n"
        "            pass\n"
        "    return out\n"
        "\n"
        "async def main():\n"
        "    home = os.environ['HOME']\n"
        "    path = os.path.join(home, 'hang.py')\n"
        "    with open(path, 'w') as fh:\n"
        "        fh.write(SERVER)\n"
        "    params = StdioServerParameters(command=sys.executable, args=[path])\n"
        "    cm = TaskScopedStdioClient(params, instance_id='probe', server_name='probe')\n"
        "    await cm.__aenter__()\n"
        "    registered = cm.registered_child_pids\n"
        "    control = live_children(path)\n"
        "    await cm.__aexit__(None, None, None)\n"
        "    await asyncio.sleep(1.0)\n"
        "    survivors = live_children(path)\n"
        "    print(json.dumps({\n"
        "        'registered': [p for p, _ in registered],\n"
        "        'control_live': control,\n"
        "        'survivors': survivors,\n"
        "    }))\n"
        "\n"
        "asyncio.run(main())\n"
    )


def child_cancellation_script() -> str:
    """REAL-SDK G1 census: a cancelled handshake leaves NO live child.

    The REAL pinned SDK spawns the child and owns the teardown (the
    killpg lives in its async generator's ``finally`` AFTER the
    ``yield``). This probe makes the cancel window DETERMINISTIC without
    faking the process lifecycle: the real SDK's context manager is
    entered for real (so the child really exists and is really owned by
    the SDK), and only the RETURN of ``__aenter__`` is held on a gate
    the probe controls.

    The probe then cancels the caller of the wrapper's ``__aenter__``
    while the inner transport is still entering — the G1 route — and
    censuses ``/proc`` for the child afterwards.
    """
    return (
        "import asyncio, json, os, sys, time\n"
        "sys.path.insert(0, %r)\n" % str(repo_root())
        + "from mcp import StdioServerParameters\n"
        "from mcp.client.stdio import stdio_client\n"
        "import daemon.mcp.stdio_wrapper as sw\n"
        "from daemon.mcp.stdio_wrapper import TaskScopedStdioClient, scan_mcp_child_pids\n"
        "\n"
        "SERVER = %r\n" % HANGING_SERVER_SCRIPT
        + "\n"
        "GATE = asyncio.Event()\n"
        "\n"
        "class _GatedCM:\n"
        "    def __init__(self, inner):\n"
        "        self._inner = inner\n"
        "    async def __aenter__(self):\n"
        "        streams = await self._inner.__aenter__()\n"
        "        await GATE.wait()\n"
        "        return streams\n"
        "    async def __aexit__(self, *exc):\n"
        "        return await self._inner.__aexit__(*exc)\n"
        "\n"
        "_REAL = stdio_client\n"
        "def _gated(server_params, *a, **kw):\n"
        "    return _GatedCM(_REAL(server_params, *a, **kw))\n"
        "sw.mcp.stdio_client = _gated\n"
        "\n"
        "def live_children(path):\n"
        "    out = []\n"
        "    for pid, starttime in scan_mcp_child_pids(command=sys.executable, args=[path]):\n"
        "        try:\n"
        "            with open('/proc/%d/stat' % pid) as fh:\n"
        "                if int(fh.read().split()[21]) == starttime:\n"
        "                    out.append(pid)\n"
        "        except (OSError, IndexError, ValueError):\n"
        "            pass\n"
        "    return out\n"
        "\n"
        "async def main():\n"
        "    home = os.environ['HOME']\n"
        "    path = os.path.join(home, 'hang.py')\n"
        "    with open(path, 'w') as fh:\n"
        "        fh.write(SERVER)\n"
        "    params = StdioServerParameters(command=sys.executable, args=[path])\n"
        "    cm = TaskScopedStdioClient(params, instance_id='probe', server_name='probe')\n"
        "    caller = asyncio.ensure_future(cm.__aenter__())\n"
        "    await asyncio.sleep(1.0)\n"
        "    control = live_children(path)\n"
        "    caller.cancel()\n"
        "    try:\n"
        "        await caller\n"
        "    except BaseException:\n"
        "        pass\n"
        "    GATE.set()\n"
        "    await asyncio.sleep(3.0)\n"
        "    survivors = live_children(path)\n"
        "    print(json.dumps({\n"
        "        'control_live': control,\n"
        "        'survivors': survivors,\n"
        "    }))\n"
        "\n"
        "asyncio.run(main())\n"
    )


def warmup_cancel_script() -> str:
    """Cancelling a task awaiting ``pool.warmup()`` must RE-RAISE.

    The exact observable the reviewer asked for: a manager-shaped
    ``await self._warmup_task`` must see ``CancelledError`` (and
    ``task.cancelled()`` must be True) when the warmup task is
    cancelled — NOT a successful completion. If the total-budget
    ``except`` swallowed the cancellation, the task would complete
    successfully, ``_warmup_and_report`` would continue past the
    cancel and would call ``start_health_check`` /
    ``eager_warm_schemas`` DURING SHUTDOWN (spawning fresh MCP
    subprocesses while the daemon goes down).

    The probe registers a server that never speaks the MCP protocol
    (so the warmup is genuinely in flight) and cancels the awaiting
    task.
    """
    return (
        "import asyncio, json, os, sys, time\n"
        "sys.path.insert(0, %r)\n" % str(repo_root())
        + "from daemon.mcp.warmup_pool import McpWarmupPool\n"
        "from daemon.mcp.config import McpStdioConfig\n"
        "import daemon.mcp.warmup_pool as wp\n"
        "wp.WARMUP_TOTAL_BUDGET_S = 6.0\n"
        "wp.WARMUP_CREATE_BUDGET_S = 45.0\n"
        "\n"
        "SERVER = %r\n" % HANGING_SERVER_SCRIPT
        + "\n"
        "async def main():\n"
        "    home = os.environ['HOME']\n"
        "    path = os.path.join(home, 'hang.py')\n"
        "    with open(path, 'w') as fh:\n"
        "        fh.write(SERVER)\n"
        "    pool = McpWarmupPool()\n"
        "    pool.register_server('hang', McpStdioConfig(command=sys.executable, args=[path]), pool_size=1)\n"
        "    task = asyncio.ensure_future(pool.warmup())\n"
        "    await asyncio.sleep(1.0)\n"
        "    task.cancel()\n"
        "    raised = None\n"
        "    try:\n"
        "        await task\n"
        "    except BaseException as exc:\n"
        "        raised = type(exc).__name__\n"
        "    print(json.dumps({\n"
        "        'raised': raised,\n"
        "        'cancelled': task.cancelled(),\n"
        "    }))\n"
        "\n"
        "asyncio.run(main())\n"
    )


def sibling_reap_script() -> str:
    """A failing session's reap must NOT kill a live sibling's child.

    The sibling uses the SAME command basename with DIFFERENT args (the
    common case — two npx-based MCP servers). The baseline snapshot and
    the reap candidate set MUST use the SAME matcher, otherwise the
    sibling is absent from the baseline, present in the reap set, and
    (until its own stamp claims it) vulnerable to a cross-session
    killpg.

    The probe: spawns the sibling child (same command, different args),
    then drives a FAILING session's ``_reap_orphans()`` and checks
    that the sibling is still alive.
    """
    return (
        "import asyncio, json, os, sys, time\n"
        "sys.path.insert(0, %r)\n" % str(repo_root())
        + "import daemon.mcp.stdio_wrapper as sw\n"
        "from daemon.mcp.stdio_wrapper import TaskScopedStdioClient\n"
        "\n"
        "SERVER = %r\n" % HANGING_SERVER_SCRIPT
        + "\n"
        "def alive(pid):\n"
        "    try:\n"
        "        with open('/proc/%d/stat' % pid) as fh:\n"
        "            return fh.read()\n"
        "    except OSError:\n"
        "        return None\n"
        "\n"
        "async def main():\n"
        "    home = os.environ['HOME']\n"
        "    sibling_path = os.path.join(home, 'sibling.py')\n"
        "    with open(sibling_path, 'w') as fh:\n"
        "        fh.write(SERVER)\n"
        "    # A LIVE sibling: same command basename, DIFFERENT args.\n"
        "    import subprocess\n"
        "    # start_new_session mirrors the SDK's anyio.open_process\n"
        "    # (start_new_session=True) — the child leads its OWN process\n"
        "    # group, so a killpg on it cannot reach this probe.\n"
        "    sibling = subprocess.Popen(\n"
        "        [sys.executable, sibling_path], start_new_session=True,\n"
        "    )\n"
        "    await asyncio.sleep(0.5)\n"
        "    # The failing session: same command basename, different args.\n"
        "    import types\n"
        "    failing_args = [os.path.join(home, 'failing.py')]\n"
        "    with open(failing_args[0], 'w') as fh:\n"
        "        fh.write(SERVER)\n"
        "    params = types.SimpleNamespace(command=sys.executable, args=failing_args)\n"
        "    session = TaskScopedStdioClient(params, instance_id='failing', server_name='failing')\n"
        "    session._snapshot_preexisting()\n"
        "    session._reap_orphans()\n"
        "    await asyncio.sleep(0.5)\n"
        "    sibling_alive = sibling.poll() is None\n"
        "    print(json.dumps({\n"
        "        'sibling_alive': sibling_alive,\n"
        "        'sibling_pid': sibling.pid,\n"
        "    }))\n"
        "    if sibling_alive:\n"
        "        sibling.kill()\n"
        "        sibling.wait()\n"
        "\n"
        "asyncio.run(main())\n"
    )
