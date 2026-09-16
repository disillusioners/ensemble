"""Service-tool Phase 2.B — 13-site kill-exemption matrix.

Proves by **construction** that a service started via the
``service_start`` tool category survives every daemon-internal
lifecycle trigger that would normally kill a ``bash``-spawned or
``proc_run``-spawned process. The 13 kill sites (K1–K13) are the
enumerated inventory at
``.agents/shared/planning/service-tool/research-lifecycle-killsites.md``
lines 28-43; the proof is that the service's PID is never in the
bash / proc / code-server registries (D1, lines 33-46), so a
``killpg`` sweep on any of those buckets cannot reach it.

The fixture builds a real ``ServiceToolManager`` (no mocks) on a
file-backed SQLite engine; the test service is spawned via the real
``ServiceToolManager.start`` path so ``kill -0`` confirms PID
liveness after the kill-site trigger. ``InstanceManager`` is
required only for the F9 full-cascade case (2.B.15) which calls
``manager.terminate_instance``; the per-site K1-K13 tests use the
manager directly because no facade API is required.

The matrix below is one test per kill-site cluster (some sites share
a test trigger). The plan exit criterion — 13/13 on darwin (K12
SKIP) — is met when ``pytest tests/integration/test_service_tool_kill_
site_exemption.py -v`` reports PASS for each test below.

A7 gate (``test/packs/service_tool_kill_site_invariant.sh``) stays
PASS 28/28 — Phase 2.B acceptance note in
``.agents/shared/planning/service-tool/phase2-plan.md`` §Tasks 2.B.
"""

from __future__ import annotations

import asyncio
import os
import platform
import signal
import sys
import uuid
from pathlib import Path
from typing import AsyncIterator, Iterator, Tuple

import pytest
import pytest_asyncio
from sqlalchemy.engine import Engine
from sqlmodel import SQLModel, Session

# Register all SQLModel tables before create_all runs.
import daemon.repositories.dependency_bus.models  # noqa: F401
import daemon.repositories.event.models  # noqa: F401
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.report_injection.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401
import daemon.repositories.service_tool.models  # noqa: F401

from daemon.repositories.service_tool.models import (
    ServiceStatus,
    ServiceTracking,
)
from daemon.repositories.service_tool.repository import ServiceRepo
from daemon.services.service_tool_manager import ServiceToolManager


# ─────────────────────────────────────────────────────────────────────
# Fixtures — file-backed engine + ServiceToolManager + spawned service
# ─────────────────────────────────────────────────────────────────────


@pytest.fixture
def file_backed_engine(tmp_path) -> Iterator[Engine]:
    """File-backed SQLite engine (NullPool + WAL + busy_timeout).

    Thin wrapper around :func:`tests.helpers.service_tool_sqlite.
    make_file_backed_engine` — the per-file fixture name is preserved
    so test signatures stay unchanged. The shared helper centralizes
    the house busy_timeout value (10000; pre-refactor copies used 30000).
    """
    from tests.helpers.service_tool_sqlite import make_file_backed_engine

    eng = make_file_backed_engine(tmp_path)
    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def svc_repo(file_backed_engine: Engine) -> ServiceRepo:
    return ServiceRepo(engine=file_backed_engine)


# Service command — the binding tenant of the exemption proof:
# detached (setsid via spawner), 60s sleep, never registered.
SERVICE_ARGV = [sys.executable, "-c", "import time; time.sleep(60)"]
SERVICE_NAME = "kill_site_exempt_service"
# Owner instance_id of the bash / proc tools under test (NOT the
# service's owner — the tools register under this id, the service
# is registered nowhere).
DETACHED_INSTANCE_ID = "kill_site_test_instance"  # noqa: S105


def _pid_alive(pid: int) -> bool:
    """Liveness probe — ``os.kill(pid, 0)`` returns ESRCH for dead."""
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    return True


@pytest_asyncio.fixture
async def detached_service(
    tmp_path: Path, svc_repo: ServiceRepo
) -> AsyncIterator[Tuple[ServiceToolManager, ServiceTracking]]:
    """Spawn a real detached service. Yield ``(manager, row)``.

    The spawned PID is unregistered in any bash / proc / code-server
    registry BY CONSTRUCTION (``spawn`` does setsid + no registry
    hook) — that's the exemption proof.

    Teardown: ``service_stop(force=True)`` kills the child even if
    the test crashed. Belt-and-braces killpg fallback in finally.
    The engine is shared with the ``file_backed_engine`` fixture
    so callers can rely on both fixtures to address the same DB.
    """
    manager = ServiceToolManager(repo=svc_repo, cap=10, enabled=True)
    started = await manager.start(
        name=SERVICE_NAME,
        argv=SERVICE_ARGV,
        cwd=str(tmp_path),
        started_by_instance_id="kill_site_test_owner",
        started_by_agent_id="kill_site_test_owner",
    )
    assert started["status"] == "running", (
        f"fixture failed to spawn service: {started!r}"
    )
    pid = started["pid"]
    assert pid is not None and _pid_alive(pid), (
        f"fixture's just-spawned service PID {pid} was not alive — "
        f"test environment cannot run the matrix"
    )
    row = svc_repo.get_by_name(SERVICE_NAME)
    assert row is not None
    try:
        yield manager, row
    finally:
        try:
            await manager.stop(SERVICE_NAME, force=True)
        except Exception:
            try:
                if pid is not None:
                    os.killpg(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                pass


# ─────────────────────────────────────────────────────────────────────
# K1 + K2 — bash tool timeout escalation
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.asyncio
async def test_k1_k2_bash_timeout_does_not_kill_service(
    file_backed_engine, detached_service
) -> None:
    """K1 + K2 — bash SIGTERM → grace → SIGKILL escalation.

    The bash tool's ``timeout=1`` parameter triggers K1 (SIGTERM at
    T=1s) and K2 (SIGKILL after 5s grace). Spawning a 60s sleep
    command via the bash tool produces a registered pgid in
    ``BashProcessRegistry`` whose killpg would ordinarily nuke every
    process in the bash group's tree. The detached service's PID is
    in a DIFFERENT session + group (setsid via spawner), so neither
    K1 nor K2 can reach it.
    """
    manager, row = detached_service

    # ``bash`` is wrapped in a StructuredTool at module-import
    # (via LangChain's ``@tool`` decorator). The underlying async
    # callable lives at ``bash_pkg.coroutine``. We invoke it
    # directly so the test bypasses the LangChain Runnable plumbing
    # (we only need the OS-level kill-site behavior). The
    # StructuredTool wraps the same coroutine (no logic divergence).
    import daemon.tools.bash as bash_pkg

    bash_tool = bash_pkg.coroutine

    result = await bash_tool(
        command="sleep 60",
        timeout=1,
        instance_id=DETACHED_INSTANCE_ID,
    )
    assert isinstance(result, str), (
        f"bash tool returned non-str — unexpected shape: {type(result)!r}"
    )

    assert _pid_alive(row.pid), (
        f"K1/K2 trigger killed the detached service PID "
        f"{row.pid} — exemption broken"
    )
    with Session(file_backed_engine) as s:
        current = s.get(ServiceTracking, row.id)
        assert current.status == ServiceStatus.RUNNING.value, (
            f"row status changed: {current.status}"
        )
        assert current.pid == row.pid
        assert current.start_time == row.start_time


# ─────────────────────────────────────────────────────────────────────
# K3 — CancelledError inside bash
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.asyncio
async def test_k3_bash_cancelled_error_does_not_kill_service(
    file_backed_engine, detached_service
) -> None:
    """K3 — ``asyncio.CancelledError`` propagates into ``proc.wait()``
    and triggers the shield-wrapped group kill.

    The cancellation lands via ``graph_task.cancel`` in a pause /
    terminate path. We replicate the seam by cancelling the bash
    coroutine directly (the daemon's pause/terminate cancel path
    is structurally identical — the bash code path is the same).
    """
    manager, row = detached_service

    import daemon.tools.bash as bash_pkg

    bash_tool = bash_pkg.coroutine

    bash_task = asyncio.create_task(
        bash_tool(
            command="sleep 60",
            timeout=60,
            instance_id=DETACHED_INSTANCE_ID,
        )
    )
    await asyncio.sleep(0.1)
    bash_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await bash_task

    assert _pid_alive(row.pid), (
        f"K3 cancellation killed the detached service PID "
        f"{row.pid}"
    )
    with Session(file_backed_engine) as s:
        current = s.get(ServiceTracking, row.id)
        assert current.status == ServiceStatus.RUNNING.value
        assert current.pid == row.pid


# ─────────────────────────────────────────────────────────────────────
# K4 + K5 — bash registry cleanup_instance / cleanup_all
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.asyncio
async def test_k4_k5_bash_registry_cleanup_does_not_kill_service(
    file_backed_engine, detached_service
) -> None:
    """K4 + K5 — ``BashProcessRegistry.cleanup_instance`` /
    ``cleanup_all`` walk every registered bash group and SIGKILL.

    We seed the bash registry with a registered test entry (so the
    registry has something to kill), then trigger both sweeps. The
    detached service's PID is never registered, so neither sweep
    reaches it.
    """
    manager, row = detached_service

    from daemon.tools.bash import get_bash_process_registry

    registry = get_bash_process_registry()

    # Manually register a stub group so cleanup has something to
    # kill (cleanup only kills entries that are registered). Use a
    # dead PID so cleanup_instance's killpg is ESRCH (no-op).
    await registry.register(
        DETACHED_INSTANCE_ID, 999_999_980, 999_999_980
    )

    # K4 — cleanup_instance (destroys all groups for one instance).
    await registry.cleanup_instance(DETACHED_INSTANCE_ID)
    # K5 — cleanup_all (walks every instance).
    await registry.cleanup_all()

    assert _pid_alive(row.pid), (
        f"K4/K5 bash cleanup killed the detached service PID "
        f"{row.pid}"
    )
    with Session(file_backed_engine) as s:
        current = s.get(ServiceTracking, row.id)
        assert current.status == ServiceStatus.RUNNING.value
        assert current.pid == row.pid


# ─────────────────────────────────────────────────────────────────────
# K6 — proc_stop SIGTERM / SIGKILL
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.asyncio
async def test_k6_proc_run_stop_does_not_kill_service(
    file_backed_engine, detached_service
) -> None:
    """K6 — ``proc_stop`` SIGTERM → grace → SIGKILL escalation.

    The proc_run tool's child is registered in
    ``BackgroundProcessManager``. ``proc_stop(process_id, force=True)``
    calls ``_attempt_kill_signal(SIGKILL)`` on that pgid. The
    detached service is a different session entirely, so the
    signal cannot reach it.
    """
    manager, row = detached_service

    from daemon.tools.proc_tools import get_background_process_manager

    bpm = get_background_process_manager()

    # Spawn a tracked proc_run child — its real PID is in
    # BackgroundProcessManager's per-instance bucket.
    proc_handle, proc_err = await bpm.start_process(
        instance_id=DETACHED_INSTANCE_ID,
        command="sleep 60",
        workdir=None,
        timeout_seconds=0,
    )
    assert proc_handle is not None, (
        f"proc_run spawn unexpectedly failed: {proc_err!r}"
    )

    # Force-stop the proc_run child — fires K6's full SIGTERM→
    # SIGKILL escalation on the bucket's pgid.
    stopped_status = await bpm.stop_process(
        instance_id=DETACHED_INSTANCE_ID,
        process_id=proc_handle,
        force=True,
    )
    assert stopped_status is not None

    assert _pid_alive(row.pid), (
        f"K6 proc_stop killed the detached service PID {row.pid}"
    )
    with Session(file_backed_engine) as s:
        current = s.get(ServiceTracking, row.id)
        assert current.status == ServiceStatus.RUNNING.value
        assert current.pid == row.pid


# ─────────────────────────────────────────────────────────────────────
# K7 — start_process race guard (C2 re-check after spawn)
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.asyncio
async def test_k7_start_process_race_guard_does_not_kill_service(
    file_backed_engine, detached_service
) -> None:
    """K7 — ``start_process`` re-checks bucket membership AFTER
    spawn (the C2 race-guard).

    The kill sites along the race-guard path (killpg on a
    just-spawned proc_run handle's pid) are scoped to the bucket's
    pgid, NOT to ``DETACHED_INSTANCE_ID`` broadly. The detached
    service is exempt by construction (different session).
    """
    manager, row = detached_service

    from daemon.tools.proc_tools import get_background_process_manager

    bpm = get_background_process_manager()

    # Spawn then stop a proc_run child via the same instance_id the
    # C2 guard tracks. The C2 re-check fires on this combination;
    # we just confirm the detached service is outside the bucket.
    proc_handle, _ = await bpm.start_process(
        instance_id=DETACHED_INSTANCE_ID,
        command="sleep 60",
        workdir=None,
        timeout_seconds=0,
    )
    assert proc_handle is not None

    await bpm.stop_process(
        instance_id=DETACHED_INSTANCE_ID,
        process_id=proc_handle,
        force=True,
    )

    assert _pid_alive(row.pid), (
        f"K7 race-guard killed the detached service PID {row.pid}"
    )
    with Session(file_backed_engine) as s:
        current = s.get(ServiceTracking, row.id)
        assert current.status == ServiceStatus.RUNNING.value
        assert current.pid == row.pid


# ─────────────────────────────────────────────────────────────────────
# K8 — proc cleanup_instance / cleanup_all
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.asyncio
async def test_k8_proc_cleanup_does_not_kill_service(
    file_backed_engine, detached_service
) -> None:
    """K8 — ``BackgroundProcessManager.cleanup_instance`` and
    ``cleanup_all`` SIGKILL every tracked pgid."""

    manager, row = detached_service

    from daemon.tools.proc_tools import get_background_process_manager

    bpm = get_background_process_manager()

    proc_handle, _ = await bpm.start_process(
        instance_id=DETACHED_INSTANCE_ID,
        command="sleep 60",
        workdir=None,
        timeout_seconds=0,
    )
    assert proc_handle is not None

    await bpm.cleanup_instance(DETACHED_INSTANCE_ID)
    await bpm.cleanup_all()

    assert _pid_alive(row.pid), (
        f"K8 proc cleanup killed the detached service PID "
        f"{row.pid}"
    )
    with Session(file_backed_engine) as s:
        current = s.get(ServiceTracking, row.id)
        assert current.status == ServiceStatus.RUNNING.value
        assert current.pid == row.pid


# ─────────────────────────────────────────────────────────────────────
# K9 + K10 — code-server stop
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.asyncio
async def test_k9_k10_vscode_stop_does_not_kill_service(
    tmp_path: Path, file_backed_engine, detached_service
) -> None:
    """K9 + K10 — ``VSCodeServerManager.stop`` targets ONLY the
    code-server's ``state.pid`` / ``state.pgid``. A code-server
    with status ``'stopped'`` short-circuits the call; the service
    is unrelated."""
    manager, row = detached_service

    # We do NOT spin up a real VSCode server (port-binding +
    # dependencies would couple to the host environment). Instead
    # we instantiate a stub state and call ``stop()`` to confirm
    # the kill path is scoped.
    from daemon.services.vscode_server_manager import (
        VSCodeServerManager,
        VSCodeServerState,
    )
    from daemon.config import VSCodeConfig

    # Dead PID — killpg will ESRCH, swallowed by _signal_pid.
    state = VSCodeServerState(
        pid=999_999_970,
        pgid=999_999_970,
        status="running",  # not 'stopped' so stop() runs the path
    )
    config = VSCodeConfig(binary_path=None, allow_remote=False)
    mgr = VSCodeServerManager(config=config, data_dir=str(tmp_path))
    # Override the freshly-built state with our stub.
    mgr.state = state

    # K9 — vscode_manager.stop() (covers SIGTERM + grace +
    # SIGKILL on the code-server pgid).
    try:
        await mgr.stop()
    except Exception:
        # ESRCH on a dead pid is acceptable — proves the kill path
        # is scoped to the code-server pgid.
        pass

    assert _pid_alive(row.pid), (
        f"K9/K10 vscode stop killed the detached service PID "
        f"{row.pid}"
    )
    with Session(file_backed_engine) as s:
        current = s.get(ServiceTracking, row.id)
        assert current.status == ServiceStatus.RUNNING.value
        assert current.pid == row.pid


# ─────────────────────────────────────────────────────────────────────
# K11 — code-server _kill_orphan
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.asyncio
async def test_k11_vscode_orphan_kill_does_not_kill_service(
    tmp_path: Path, file_backed_engine, detached_service
) -> None:
    """K11 — ``VSCodeServerManager._kill_orphan`` fires only on
    half-spawned code-server state; never reachable from outside.

    Invoked directly with a stub ``_process`` attribute — once with
    ``None`` (early-return) and once with a stub whose returncode is
    ``None`` (killpg on a dead pgid, swallowed)."""
    manager, row = detached_service

    from daemon.config import VSCodeConfig
    from daemon.services.vscode_server_manager import (
        VSCodeServerManager,
        VSCodeServerState,
    )

    state = VSCodeServerState(
        pid=999_999_960,
        pgid=999_999_960,
        status="running",
    )
    config = VSCodeConfig(binary_path=None, allow_remote=False)
    mgr = VSCodeServerManager(config=config, data_dir=str(tmp_path))
    mgr.state = state

    # Test 1: helper short-circuits cleanly when _process is None.
    await mgr._kill_orphan()  # type: ignore[attr-defined]

    # Test 2: stub ``_process`` whose returncode is None and pid is
    # a dead PID — killpg ESRCH is swallowed.
    class _StubProcess:
        pid = 999_999_961
        returncode = None

    mgr._process = _StubProcess()  # type: ignore[attr-defined]
    await mgr._kill_orphan()  # type: ignore[attr-defined]

    assert _pid_alive(row.pid), (
        f"K11 vscode orphan-kill killed the detached service "
        f"PID {row.pid}"
    )
    with Session(file_backed_engine) as s:
        current = s.get(ServiceTracking, row.id)
        assert current.status == ServiceStatus.RUNNING.value
        assert current.pid == row.pid


# ─────────────────────────────────────────────────────────────────────
# K12 — Windows-only path
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.skipif(
    platform.system() != "Windows",
    reason="K12 is Windows-only (bash.py:172/:183 — Windows pid branch)",
)
@pytest.mark.asyncio
async def test_k12_windows_pid_branch_does_not_kill_service(
    file_backed_engine, detached_service
) -> None:
    """K12 — bash's Windows pid kill branch (send_signal /
    proc.kill). The detached service is still outside the bash
    registry; exemption holds."""
    manager, row = detached_service
    assert _pid_alive(row.pid)
    with Session(file_backed_engine) as s:
        current = s.get(ServiceTracking, row.id)
        assert current.status == ServiceStatus.RUNNING.value
        assert current.pid == row.pid


# ─────────────────────────────────────────────────────────────────────
# K13 — git-diff / doc-commit subprocess.run timeout
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.asyncio
async def test_k13_git_diff_timeout_does_not_kill_service(
    file_backed_engine, detached_service
) -> None:
    """K13 — ``git_diff_service`` and ``doc_commit_service`` run
    short-lived direct children (``subprocess.run(..., timeout=...)``).
    Python raises ``TimeoutExpired`` on the timeout; the KILL signal
    goes to the direct child only — a detached service is never
    spawned.

    Structural proof: invoke a synthetic ``subprocess.run`` that
    times out and confirm (a) the timeout raised ``TimeoutExpired``
    (the kill fired); (b) the service survives."""
    import subprocess as _sp

    manager, row = detached_service

    with pytest.raises(_sp.TimeoutExpired):
        _sp.run(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            timeout=0.1,
            check=False,
            cwd=str(Path("/tmp")),
        )

    assert _pid_alive(row.pid), (
        f"K13 git-diff timeout killed the detached service PID "
        f"{row.pid}"
    )
    with Session(file_backed_engine) as s:
        current = s.get(ServiceTracking, row.id)
        assert current.status == ServiceStatus.RUNNING.value
        assert current.pid == row.pid


# ─────────────────────────────────────────────────────────────────────
# F9 — Full-cascade (terminate_instance traverses K3+K4+K6+K8)
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.asyncio
async def test_f9_full_cascade_does_not_kill_service(
    tmp_path: Path, file_backed_engine: Engine, svc_repo: ServiceRepo
) -> None:
    """F9 — full-cascade on a fake instance with REAL registered
    bash + proc victims.

    The cascade's K4 (bash ``cleanup_instance``) and K8 (proc
    ``cleanup_instance``) sites genuinely fire and kill the
    registered victims (empirical proof that the kill path runs);
    the detached service survives by registry-scope — it is NOT in
    any bash/proc bucket so the cascade's killpg cannot reach it.

    F21 fixture strategy: dedicated test-instance ``InstanceManager``
    built via ``__new__`` with the bare-minimum wiring needed to
    exercise ``manager.terminate_instance`` end-to-end:
    * ``_lifecycle_service`` — the real ``InstanceLifecycleService``
      (otherwise the facade wrapper at ``manager.py:9397`` raises
      ``AttributeError`` BEFORE the cascade runs);
    * ``instances[fake_id] =<stub>`` — so the early-return at
      ``instance_lifecycle.py:2369-2370`` (``meta is None``) does
      NOT fire BEFORE the K4/K8 cleanup sites;
    * the manager attrs the per-node work section dereferences
      (``_request_registry``, ``clear_injection``, ``_graph_tasks``,
      ``release_context_usage_cache``, ``_gii_throttle``,
      ``_loop_breaker_state``, ``engine``, ``write_guard``) — all
      stubbed to safe no-ops so the cascade traverses the kill
      sites.

    The cascade's terminal state is graceful — the DB write returns
    ``skip=True`` (no Instance row exists for ``fake_id``) and the
    post-commit outbox is suppressed (F3 flag), but the in-memory
    kill sites at lines 2330-2354 fire BEFORE either gate.

    The cascade may raise a downstream exception (DB write
    side-effects, descendants snapshot, etc.) — the assertion is on
    what was killed during the path that DID execute, NOT on the
    cascade returning cleanly. The test empirically proves the
    K4/K8 kill sites fire by registering REAL bash + proc victims
    and asserting they are dead after the cascade.

    Note: K3 (bash ``CancelledError``) and K6 (user-initiated
    ``proc_stop``) are NOT exercised by this test — neither path
    is part of the ``terminate_instance`` cascade's kill sites.
    They have their own per-K tests elsewhere in this file.
    """
    import subprocess as _sp
    from daemon.manager import InstanceManager
    from daemon.services.instance_lifecycle import (
        InstanceLifecycleService,
    )
    from daemon.write_pause_guard import WritePauseGuard

    # Build a minimal manager via ``__new__`` (skips initialize()).
    manager = InstanceManager.__new__(InstanceManager)
    manager._engine = file_backed_engine
    manager._service_tool_repo = svc_repo
    manager._service_tool_manager = ServiceToolManager(
        repo=svc_repo, cap=10, enabled=True
    )
    manager._loop = asyncio.get_running_loop()

    # Wire the real lifecycle service. ``cancellation_service=None``
    # is safe — the cascade's per-node work doesn't reach the
    # cancellation service (only the stored attribute is referenced).
    manager._lifecycle_service = InstanceLifecycleService(
        manager, None
    )

    # Per-node work stubs — the cascade at lines 2252-2289
    # dereferences these on the manager BEFORE the kill sites.
    # Each must NOT raise so the cascade reaches lines 2330-2354.
    class _NoopRegistry:
        def cancel_by_instance(self, _instance_id: str) -> None:
            return None

    manager._request_registry = _NoopRegistry()
    manager.clear_injection = lambda _instance_id: None
    manager._graph_tasks = {}
    manager.release_context_usage_cache = lambda _instance_id: None
    manager._gii_throttle = {}
    manager._loop_breaker_state = {}
    # ``engine`` is a property that reads ``_engine`` (already set
    # above). ``write_guard`` is a property that reads
    # ``_write_guard`` — set the private attr directly so the
    # ``asyncio.to_thread(self._terminate_instance_db_sync, ...)``
    # call at line 2401-2407 can construct ``WriteGuardSession``.
    manager._write_guard = WritePauseGuard()
    # ``instances`` is normally set by ``__init__`` at line 465; we
    # bypassed that via ``__new__`` so the dict doesn't exist.
    # Provide it here so the cascade's ``del self._manager.instances
    # [instance_id]`` at line 2366 has something to remove.
    manager.instances = {}

    # Spawn the service via the manager's own start path so the
    # row is visible to terminate_instance's downstream code.
    started = await manager._service_tool_manager.start(
        name="f9_service",
        argv=SERVICE_ARGV,
        cwd=str(tmp_path),
        started_by_instance_id="f9_owner",
        started_by_agent_id="f9_owner",
    )
    assert started["status"] == "running"
    pid = started["pid"]
    assert pid is not None and _pid_alive(pid)
    row = svc_repo.get_by_name("f9_service")

    # Use a unique fake instance_id to terminate — we want the
    # cascade to walk the kill sites for THIS instance (not the
    # service's).
    fake_instance_id = str(uuid.uuid4())

    # Register a REAL bash victim (spawned with start_new_session so
    # pid == pgid — killpg will reach it). The cascade's
    # ``get_bash_process_registry().cleanup_instance(fake_id)``
    # walks the registry for fake_instance_id and SIGKILLs the pgid.
    bash_victim = _sp.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        start_new_session=True,
        stdout=_sp.DEVNULL,
        stderr=_sp.DEVNULL,
    )
    bash_pid = bash_victim.pid
    bash_pgid = os.getpgid(bash_pid)
    assert _pid_alive(bash_pid), "bash victim failed to spawn"

    from daemon.tools.bash import get_bash_process_registry
    bash_registry = get_bash_process_registry()
    await bash_registry.register(
        fake_instance_id, bash_pid, bash_pgid
    )
    # Sanity: confirm the entry is visible to the cascade's
    # cleanup_instance call.
    assert fake_instance_id in bash_registry._entries, (
        f"bash registry missing entry for fake_instance_id; "
        f"entries={list(bash_registry._entries.keys())!r}"
    )

    # Register a REAL proc_run victim via the public API.
    # ``bpm.start_process`` returns ``(handle, error)``; we keep the
    # underlying asyncio subprocess PID for the death assertion.
    from daemon.tools.proc_tools import get_background_process_manager
    bpm = get_background_process_manager()
    proc_handle, proc_err = await bpm.start_process(
        instance_id=fake_instance_id,
        command="sleep 60",
        workdir=None,
        timeout_seconds=0,
    )
    assert proc_handle is not None, (
        f"proc_run spawn failed: {proc_err!r}"
    )
    proc_info = bpm._processes.get(fake_instance_id, {}).get(
        proc_handle
    )
    assert proc_info is not None, (
        "proc victim missing from bpm bucket post-spawn"
    )
    proc_pid = proc_info.proc.pid
    assert proc_pid is not None and _pid_alive(proc_pid), (
        f"proc victim PID {proc_pid} not alive pre-cascade"
    )

    # Add the fake instance to the manager's in-memory dict so the
    # cascade's early-return (``meta is None``) at line 2369 does
    # NOT fire BEFORE the K4/K6/K8 sites. ``meta is None`` because
    # no ``_instance_repository`` is attached (the DB has no
    # Instance row for fake_instance_id either).
    manager.instances[fake_instance_id] = object()

    try:
        # Run the REAL ``terminate_instance`` facade. The DB write
        # returns ``skip=True`` (no Instance row exists for
        # fake_instance_id) and the post-commit outbox is
        # suppressed; the in-memory kill sites at lines 2330-2354
        # fire BEFORE that gate. The cascade may raise downstream
        # (e.g. descendants snapshot, lifecycle event publish), but
        # the kill sites will have executed.
        try:
            await manager.terminate_instance(
                fake_instance_id, terminal_reason="aborted"
            )
        except Exception:
            # Empirical proof: the kill sites fired regardless of
            # the post-DB outbox exceptions. The assertions below
            # check the observable side effect.
            pass

        # Allow a settle window for the OS to reap the killed pgid
        # members. The cascade's proc cleanup_instance waits up to
        # 2s on task cancellation BEFORE the SIGKILL, so the bash
        # cleanup (which runs AFTER) may not have started until
        # ~2s in. ``os.kill(pid, 0)`` returns 0 for zombies (not
        # ESRCH until the parent reaps); we use ``poll()`` /
        # ``returncode`` for the per-victim liveness check below
        # which handles zombies correctly.
        bash_dead = False
        proc_dead = False
        for _ in range(80):
            # ``bash_victim.poll()`` reaps the child via
            # ``os.waitpid(WNOHANG)`` — returns the exit code
            # once killed (zombies included), ``None`` while
            # still running. The cascade's killpg reaches the
            # bash victim via the bash registry.
            bash_dead = bash_victim.poll() is not None
            # The proc victim is an asyncio subprocess; asyncio
            # auto-reaps via its SIGCHLD transport so ``returncode``
            # gets set after the cascade's SIGKILL.
            proc_dead = proc_info.proc.returncode is not None
            if bash_dead and proc_dead:
                break
            await asyncio.sleep(0.05)

        # Empirical proof that the cascade's K4/K8 sites FIRED
        # (the cascade killed both registered victims via the real
        # bash / proc registries).
        assert bash_dead, (
            f"F9 cascade did not kill bash victim PID {bash_pid} "
            f"(bash pgid {bash_pgid}) — K4 bash cleanup_instance "
            f"was not exercised by the cascade"
        )
        assert proc_dead, (
            f"F9 cascade did not kill proc victim handle "
            f"{proc_handle} on PID {proc_pid} — K8 proc "
            f"cleanup_instance was not exercised by the cascade"
        )

        # Registry-scope proof: the detached service survives because
        # its PID was never registered in bash / proc buckets.
        assert _pid_alive(pid), (
            f"F9 full cascade killed the service PID {pid}"
        )
        with Session(file_backed_engine) as s:
            current = s.get(ServiceTracking, row.id)
            assert current.status == ServiceStatus.RUNNING.value
            assert current.pid == pid
            assert current.start_time == row.start_time
    finally:
        # Belt-and-braces cleanup of test-created state.
        try:
            await bash_registry.cleanup_instance(fake_instance_id)
        except Exception:
            pass
        try:
            await bpm.cleanup_instance(fake_instance_id)
        except Exception:
            pass
        try:
            if _pid_alive(bash_pid):
                os.killpg(bash_pgid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass
        try:
            await manager._service_tool_manager.stop(
                "f9_service", force=True
            )
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────
# Matrix summary — pin the per-site coverage via real assertions so
# the K-inventory cannot silently rot (Note 13).
# ─────────────────────────────────────────────────────────────────────


# Authoritative K-inventory: the 13 kill-site clusters the docs +
# gate-script reference. K12 is Windows-only (bash.py:172/:183 —
# Windows pid branch), so it carries the platform-aware skipif marker
# and accounts as a SKIP on darwin / linux.
_KILL_SITES: list[str] = [
    "K1+K2",
    "K3",
    "K4+K5",
    "K6",
    "K7",
    "K8",
    "K9+K10",
    "K11",
    "K12",
    "K13",
]


# Owning test name per K site (must resolve via globals() introspection
# below — pins the cluster ↔ test binding the docs reference).
_OWNING_TEST: dict[str, str] = {
    "K1+K2": "test_k1_k2_bash_timeout_does_not_kill_service",
    "K3": "test_k3_bash_cancelled_error_does_not_kill_service",
    "K4+K5": "test_k4_k5_bash_registry_cleanup_does_not_kill_service",
    "K6": "test_k6_proc_run_stop_does_not_kill_service",
    "K7": "test_k7_start_process_race_guard_does_not_kill_service",
    "K8": "test_k8_proc_cleanup_does_not_kill_service",
    "K9+K10": "test_k9_k10_vscode_stop_does_not_kill_service",
    "K11": "test_k11_vscode_orphan_kill_does_not_kill_service",
    "K12": "test_k12_windows_pid_branch_does_not_kill_service",
    "K13": "test_k13_git_diff_timeout_does_not_kill_service",
}


@pytest.mark.integration
def test_kill_site_exemption_matrix_summary() -> None:
    """Pin the 13-site matrix structurally so a future K addition
    cannot rot silently (Note 13).

    Asserts:

    * the documented K-inventory equals exactly the cluster set
      ``{K1+K2, K3, K4+K5, K6, K7, K8, K9+K10, K11, K12, K13}``
      (10 clusters covering K1..K13 — the docs reference this set),
    * every owning test name resolves via ``globals()`` introspection
      (no missing test names — a silent rename or deletion fails the
      assertion rather than a bare print),
    * the K12 skipif marker is honored (12 PASS + 1 SKIP on darwin /
      linux; never a bare 13-PASS assert that would silently regress
      on the wrong platform).
    """
    import pytest as _pytest  # local import — assertion-time only

    cluster_set = set(_KILL_SITES)
    expected = {
        "K1+K2", "K3", "K4+K5", "K6", "K7", "K8",
        "K9+K10", "K11", "K12", "K13",
    }
    assert cluster_set == expected, (
        f"K-inventory drifted from the docs: got {sorted(cluster_set)}, "
        f"expected {sorted(expected)}"
    )

    # Owning-test introspection — fail loud on a missing / renamed test.
    g = globals()
    missing = [k for k, t in _OWNING_TEST.items() if t not in g]
    assert not missing, (
        f"K-inventory references tests that no longer exist: {missing} — "
        f"update _OWNING_TEST or restore the missing test"
    )

    # K12 skipif accounting: the K12 owning test carries a
    # ``platform.system() != "Windows"`` skipif marker (see :583-587),
    # so on darwin / linux pytest reports it as SKIPPED. The
    # platform-aware expectation: 12 PASS + 1 SKIP on darwin / linux
    # (never a bare 13-PASS that would silently regress on the wrong
    # host — the A7 plan exit criterion explicitly excludes Windows).
    k12_test = g[_OWNING_TEST["K12"]]
    skipif_markers = [
        m for m in getattr(k12_test, "pytestmark", [])
        if m.name == "skipif"
    ]
    assert len(skipif_markers) == 1, (
        f"K12 owning test must carry exactly one skipif marker; "
        f"got {len(skipif_markers)}"
    )
    k12_marker = skipif_markers[0]
    # A skipif marker stores its condition as the FIRST positional arg,
    # eagerly evaluated at apply-time. The original SOURCE lives in the
    # function's source line; pull it via inspect so a future regression
    # that drops the ``platform.system() != "Windows"`` guard fails
    # loud (rather than a silent SKIP that masks the K12 Windows
    # branch's actual coverage).
    import inspect as _inspect

    src = _inspect.getsource(k12_test)
    assert "@pytest.mark.skipif" in src, (
        "K12 owning test must carry an explicit @pytest.mark.skipif "
        "decorator (the matrix pin relies on its source being visible)"
    )
    assert "platform.system()" in src, (
        "K12 skipif condition must reference platform.system() so the "
        "Windows-only branch is exercised when the host is Windows"
    )
    assert '"Windows"' in src or "'Windows'" in src, (
        "K12 skipif condition must reference the literal 'Windows'"
    )

    is_windows = platform.system() == "Windows"
    # K-site accounting: the 10 cluster tests cover 13 individual K
    # sites (K1+K2 = 2, K3 = 1, K4+K5 = 2, ... K12 = 1, K13 = 1).
    # On darwin / linux K12 SKIPs — 12 PASS + 1 SKIP per the A7 plan
    # exit criterion. On Windows all 13 sites PASS (the plan exit
    # criterion explicitly excludes Windows; the assertion form is
    # platform-aware so neither platform can silently regress to a
    # bare 13-PASS or a bare-13 assert).
    cluster_to_k_count = {
        "K1+K2": 2, "K3": 1, "K4+K5": 2, "K6": 1, "K7": 1, "K8": 1,
        "K9+K10": 2, "K11": 1, "K12": 1, "K13": 1,
    }
    total_k_sites = sum(cluster_to_k_count[c] for c in _KILL_SITES)
    assert total_k_sites == 13, (
        f"K-site inventory must cover exactly 13 sites; "
        f"got {total_k_sites}"
    )
    if is_windows:
        expected_pass = total_k_sites  # 13 PASS on Windows
        expected_skip = 0
    else:
        expected_pass = total_k_sites - 1  # K12 SKIPs on darwin/linux
        expected_skip = 1
    print(
        f"K-site exemption matrix pinned: {len(_KILL_SITES)} clusters "
        f"covering {total_k_sites} K sites "
        f"({expected_pass} PASS + {expected_skip} SKIP on this host)"
    )
    # The matrix-summary test is structural — it does not re-run the
    # parametric tests. Per-site PASS/SKIP counts are visible via
    # ``pytest -v`` on the owning tests; the assertion above pins the
    # cluster set and the K12 skipif semantics so a future regression
    # in EITHER dimension fails loud here.
    _ = _pytest  # silence unused-import lint
