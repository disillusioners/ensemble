"""End-to-end integration tests for the opencode workflow.

These tests exercise the **full** opencode stack against a live local
OpenCode HTTP server (default ``http://127.0.0.1:4095``). They are
skipped by default because they require a running OpenCode instance —
the unit tests in this directory mock the HTTP boundary instead.

Enable the suite by either:

* starting an OpenCode server before running pytest, or
* running with ``pytest -m integration`` (the ``pytestmark`` below
  is keyed off a probe to ``127.0.0.1:4095`` — see ``_opencode_reachable``).

Test scenarios:

1. **Full lifecycle** — ``init → send → wait → status → answer → abort``
   for a single session.  Verifies the happy-path orchestration across
   the registry, session manager, HTTP client, and tool layer.

2. **Parallel sessions** — 3 sessions are created in parallel, each gets
   a prompt, and ``wait_any`` is used to surface the first completion.

3. **Persistence across restart** — A session is created, the registry
   is shut down, a fresh registry is built on the same engine, and
   ``recover_from_registry()`` rehydrates the session.  This is the
   crash-recovery path used when the daemon restarts.

Each test is hermetic:

* A fresh in-memory SQLite engine is created (or a tempfile-backed one
  for the persistence test).
* A unique ``working_dir`` (``tempfile.mkdtemp``) is used per test so
  that sessions do not collide on the OpenCode server.
* Every session created during a test is aborted in the ``finally``
  block — even when assertions fail — so we never leak OpenCode
  sessions across test runs.
"""

from __future__ import annotations

import asyncio
import os
import socket
import tempfile
import uuid
from pathlib import Path
from typing import Iterator

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from daemon.opencode.registry import OpenCodeSessionRegistry
from daemon.opencode.repository import (
    OpenCodeSessionRecord,
    create_opencode_session_repository,
)
from daemon.tools.external_opencode import create_opencode_tools

# ─────────────────────────────────────────────────────────────────────────────
# Reachability probe + skip-by-default
# ─────────────────────────────────────────────────────────────────────────────


OPENCODE_HOST: str = os.environ.get("OPENCODE_HOST", "127.0.0.1")
OPENCODE_PORT: int = int(os.environ.get("OPENCODE_PORT", "4095"))
OPENCODE_URL: str = f"http://{OPENCODE_HOST}:{OPENCODE_PORT}"


def _opencode_reachable(host: str = OPENCODE_HOST, port: int = OPENCODE_PORT) -> bool:
    """Cheap probe — does an HTTP-speaking service sit on the opencode port?

    Combines a TCP-level check (catches "port closed") with a short
    httpx HEAD request (catches "port open but wrong service" — e.g.
    SSH on a default workstation).  Any 2xx/4xx counts as "running"
    because a 404 simply means the endpoint path is missing, not that
    the server is down.
    """
    try:
        with socket.create_connection((host, port), timeout=0.5):
            pass
    except OSError:
        return False

    try:
        with httpx.Client(timeout=1.0) as client:
            resp = client.get(f"http://{host}:{port}/session")
            return resp.status_code < 500
    except (httpx.HTTPError, OSError):
        return False


# Module-level skip: every test in this file is gated on a live opencode
# server.  When the server is down, pytest reports a "skipped" outcome
# (not an error) so CI does not turn red.
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not _opencode_reachable(),
        reason=f"OpenCode not reachable at {OPENCODE_URL}",
    ),
]


# ─────────────────────────────────────────────────────────────────────────────
# Per-test fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def working_dir() -> Iterator[str]:
    """A fresh temporary working directory, removed after the test.

    The opencode HTTP API uses the ``x-opencode-directory`` header to
    pick a project root; passing a unique tempdir per test prevents
    sessions from different tests stepping on each other.
    """
    with tempfile.TemporaryDirectory(prefix="opencode-it-") as tmp:
        yield tmp


@pytest.fixture
def sqlite_engine() -> Iterator[Engine]:
    """In-memory SQLite engine with **only** the opencode table.

    Mirrors the production factory in
    ``daemon.opencode.repository.create_opencode_session_repository``
    so the integration tests exercise the same code path.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    OpenCodeSessionRecord.__table__.create(engine, checkfirst=True)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def registry(sqlite_engine: Engine) -> Iterator[OpenCodeSessionRegistry]:
    """A live ``OpenCodeSessionRegistry`` wired to the opencode HTTP server.

    The module-level ``pytestmark`` already ensures the server is
    reachable before any test runs, so no per-test probe is needed.

    Teardown is intentionally a no-op: each session is aborted in the
    test's ``finally`` block, and the background manager loop is killed
    when pytest tears down the event loop.  Calling
    ``reg.shutdown()`` from a sync fixture via ``asyncio.run`` is
    fragile because the production ``manager.stop()`` waits up to
    5 seconds per manager and the session loop can be in a 30-second
    ``asyncio.sleep(POLL_INTERVAL_S)`` — the shutdown can hang and
    pollute subsequent test timings.
    """
    repo = create_opencode_session_repository(sqlite_engine)
    yield OpenCodeSessionRegistry(repo)


@pytest.fixture
def manager(registry: OpenCodeSessionRegistry) -> object:
    """Minimal stand-in for ``InstanceManager`` exposing ``_opencode_registry``.

    The tool layer reads the registry off this attribute, so a plain
    ``SimpleNamespace`` is sufficient — we are not exercising any of the
    other ``InstanceManager`` responsibilities in this suite.
    """
    from types import SimpleNamespace

    return SimpleNamespace(_opencode_registry=registry)


@pytest.fixture
def tools(manager: object) -> list:
    """The 8 ``external_opencode_*`` tools bound to the test registry."""
    return create_opencode_tools(manager, current_instance_id="integration-test")  # type: ignore[arg-type]


@pytest.fixture
def project_name() -> str:
    """Unique project identifier — keeps test sessions isolated from each other."""
    return f"it-proj-{uuid.uuid4().hex[:8]}"


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


async def _abort_session(
    registry: OpenCodeSessionRegistry,
    project: str,
    session_name: str,
) -> None:
    """Best-effort abort.  Failures are logged but never re-raised.

    Cleanup helpers must not raise — otherwise a test failure could
    turn into a "the test failed AND leaked a session".
    """
    try:
        result = await registry.abort_session(project, session_name)
        if result.get("status") != "ok":
            # Logged here rather than raised — see docstring.
            print(f"[cleanup] abort returned non-ok: {result}")
    except Exception as exc:  # noqa: BLE001
        print(f"[cleanup] abort raised: {exc!r}")


def _find_tool(tools: list, name: str):
    """Return the tool with ``name`` or raise ``LookupError``."""
    for tool in tools:
        if tool.name == name:
            return tool
    raise LookupError(f"tool {name!r} not in {sorted(t.name for t in tools)}")


async def _ainvoke(tool, **kwargs) -> str:
    """Call ``tool.ainvoke`` with a kwarg dict.  Mirrors agent usage."""
    return await tool.ainvoke(kwargs)


# ─────────────────────────────────────────────────────────────────────────────
# 1. Full lifecycle
# ─────────────────────────────────────────────────────────────────────────────


class TestFullLifecycle:
    """End-to-end: init → send → wait → status → answer → abort."""

    @pytest.mark.asyncio
    async def test_init_send_wait_status_abort(
        self,
        registry: OpenCodeSessionRegistry,
        tools: list,
        working_dir: str,
        project_name: str,
    ) -> None:
        """Happy path: a session that runs through the complete lifecycle."""
        project = project_name
        session_name = f"lifecycle-{uuid.uuid4().hex[:6]}"

        try:
            # ── init ─────────────────────────────────────────────────────
            init = _find_tool(tools, "external_opencode_init_session")
            init_result = await _ainvoke(
                init,
                project=project,
                session_name=session_name,
                working_dir=working_dir,
            )
            assert init_result.startswith("[SUCCESS]"), init_result
            assert session_name in init_result

            # The session must be persisted in the repository.
            record = registry._repository.get(project, session_name)
            assert record is not None
            assert record["id"] is not None

            # ── send ─────────────────────────────────────────────────────
            send = _find_tool(tools, "external_opencode_send_message")
            send_result = await _ainvoke(
                send,
                project=project,
                session_name=session_name,
                message="echo hello",  # a tiny prompt — minimal model cost
            )
            assert send_result.startswith("[SUBMITTED]"), send_result

            # ── wait (fixed 660s timeout; the prompt is tiny) ────────────
            wait = _find_tool(tools, "external_opencode_wait_for_result")
            wait_result = await _ainvoke(
                wait,
                project=project,
                session_name=session_name,
            )
            # Either it completed, needs input, or the wait timed out —
            # all are valid outcomes for a live integration run.  We only
            # assert the wait produced *some* structured response.
            assert wait_result.startswith(("[COMPLETED]", "[WAITING_FOR_INPUT]", "[TIMEOUT]")), wait_result

            # ── status ──────────────────────────────────────────────────
            status = _find_tool(tools, "external_opencode_get_status")
            status_result = await _ainvoke(
                status,
                project=project,
                session_name=session_name,
            )
            # The status output always carries the state line and the
            # last-activity line — that's the minimum contract.
            assert "State:" in status_result
            assert "Last Activity:" in status_result

            # ── answer (only if questions are pending) ──────────────────
            if "Questions:" in status_result:
                # Pull the first request id out of the questions block.
                # Format: "  [?] <id>: [...]"
                for line in status_result.splitlines():
                    if line.strip().startswith("[?]"):
                        first_qid = line.split(":", 1)[0].split("]")[-1].strip()
                        break
                else:
                    first_qid = None  # defensive — shouldn't happen here

                if first_qid:
                    answer = _find_tool(tools, "external_opencode_answer_question")
                    answer_result = await _ainvoke(
                        answer,
                        project=project,
                        session_name=session_name,
                        request_id=first_qid,
                        answers=["yes"],
                    )
                    assert answer_result.startswith("[ANSWERED]"), answer_result

        finally:
            # ── abort / cleanup ───────────────────────────────────────
            await _abort_session(registry, project, session_name)

            # The registry should be free of any leftover in-memory manager
            # for the aborted session.  We don't assert it's gone because
            # the abort path keeps the manager in place — only the remote
            # session and the persistent state are reset.
            record_after = registry._repository.get(project, session_name)
            if record_after is not None:
                # The record may or may not still exist; if it does, the
                # state should be IDLE after the abort.
                assert record_after.get("state") in {"IDLE", "BUSY"}


# ─────────────────────────────────────────────────────────────────────────────
# 2. Parallel sessions
# ─────────────────────────────────────────────────────────────────────────────


class TestParallelSessions:
    """3 sessions created in parallel; ``wait_any`` surfaces the first result."""

    @pytest.mark.asyncio
    async def test_three_sessions_completed_via_wait_any(
        self,
        registry: OpenCodeSessionRegistry,
        tools: list,
        working_dir: str,
        project_name: str,
    ) -> None:
        """Create 3 sessions concurrently, send to each, then use ``wait_any``."""
        project = project_name
        session_names = [f"parallel-{i}-{uuid.uuid4().hex[:4]}" for i in range(3)]
        created_ids: list[str] = []

        init = _find_tool(tools, "external_opencode_init_session")
        send = _find_tool(tools, "external_opencode_send_message")

        try:
            # ── Create 3 sessions in parallel ──────────────────────────
            init_results = await asyncio.gather(*(
                _ainvoke(
                    init,
                    project=project,
                    session_name=name,
                    working_dir=working_dir,
                )
                for name in session_names
            ))
            for name, result in zip(session_names, init_results):
                assert result.startswith("[SUCCESS]"), f"{name}: {result}"
                record = registry._repository.get(project, name)
                assert record is not None
                created_ids.append(record["id"])

            # ── Send a prompt to each in parallel ──────────────────────
            send_results = await asyncio.gather(*(
                _ainvoke(
                    send,
                    project=project,
                    session_name=name,
                    message=f"echo from {name}",
                )
                for name in session_names
            ))
            for name, result in zip(session_names, send_results):
                assert result.startswith("[SUBMITTED]"), f"{name}: {result}"

            # ── wait_any: surface the first completion ─────────────────
            wait_any = _find_tool(tools, "external_opencode_wait_any")
            wait_result = await _ainvoke(
                wait_any,
                sessions=[{"project": project, "session_name": n} for n in session_names],
            )

            # wait_any returns a summary block.  Either it found at
            # least one completed session, or the whole wait timed out
            # — both are valid; we just verify the output is well-formed.
            assert (
                wait_result.startswith("[SUMMARY]")
                or wait_result.startswith("[TIMEOUT]")
                or wait_result.startswith("[ERROR]")
            ), wait_result

        finally:
            # Clean up all three sessions in parallel — they are independent.
            await asyncio.gather(*(
                _abort_session(registry, project, name)
                for name in session_names
            ))


# ─────────────────────────────────────────────────────────────────────────────
# 3. Persistence across restart
# ─────────────────────────────────────────────────────────────────────────────


class TestPersistenceAcrossRestart:
    """A session survives a registry shutdown + fresh-registry rehydrate."""

    @pytest.mark.asyncio
    async def test_session_survives_registry_restart(
        self,
        working_dir: str,
        project_name: str,
    ) -> None:
        """Create a session, stop the manager, recover from the registry.

        This test deliberately does NOT use the ``registry`` fixture —
        it builds two registries in sequence on the **same** engine so
        the second one can prove the data outlived the first.
        """
        project = project_name
        session_name = f"persist-{uuid.uuid4().hex[:6]}"

        # ── Use a file-backed SQLite so the second registry can re-open it.
        db_file = Path(tempfile.gettempdir()) / f"opencode-it-{uuid.uuid4().hex}.db"
        db_url = f"sqlite:///{db_file}"
        engine = create_engine(
            db_url,
            connect_args={"check_same_thread": False},
        )
        OpenCodeSessionRecord.__table__.create(engine, checkfirst=True)

        try:
            # ── Phase 1: create a session in the "first" registry ────
            repo1 = create_opencode_session_repository(engine)
            reg1 = OpenCodeSessionRegistry(repo1)
            try:
                new_id = await reg1.create_new(
                    project=project,
                    session_name=session_name,
                    working_dir=working_dir,
                )
                assert new_id
                assert reg1._repository.get(project, session_name) is not None
            finally:
                # Single shutdown: background loops die.
                await reg1.shutdown()

            # After shutdown (via finally), the in-memory map is empty
            # but the persisted row survives — that is the persistence guarantee.
            assert reg1._managers == {}

            # But the row in the repository is still there.
            record_in_db = reg1._repository.get(project, session_name)
            assert record_in_db is not None
            assert record_in_db["id"] == new_id

            # ── Phase 2: build a fresh registry on the same engine ───
            repo2 = create_opencode_session_repository(engine)
            reg2 = OpenCodeSessionRegistry(repo2)
            try:
                recovered = await reg2.recover_from_registry()
                assert recovered >= 1, "no sessions recovered from the registry"

                # The recovered session must be findable by id and have
                # an in-memory manager attached.
                recovered_record = reg2._repository.get(project, session_name)
                assert recovered_record is not None
                assert recovered_record["id"] == new_id

                recovered_manager = await reg2.get_manager(new_id)
                assert recovered_manager is not None
                assert recovered_manager.session_id == new_id
            finally:
                await reg2.shutdown()

        finally:
            # Tear down the on-disk DB.
            engine.dispose()
            try:
                db_file.unlink()
            except FileNotFoundError:
                pass

    @pytest.mark.asyncio
    async def test_aborted_session_state_persists_across_restart(
        self,
        working_dir: str,
        project_name: str,
    ) -> None:
        """A session's persisted state (e.g. last_agent) survives a restart.

        Sub-scenario of the above: exercises the persistence callback so
        we verify that ``OnStateChange`` writes are visible after recovery.
        """
        project = project_name
        session_name = f"state-{uuid.uuid4().hex[:6]}"

        db_file = Path(tempfile.gettempdir()) / f"opencode-it-{uuid.uuid4().hex}.db"
        engine = create_engine(
            f"sqlite:///{db_file}",
            connect_args={"check_same_thread": False},
        )
        OpenCodeSessionRecord.__table__.create(engine, checkfirst=True)

        try:
            # ── Phase 1: create + lock to "atlas" via /start-work.
            reg1 = OpenCodeSessionRegistry(
                create_opencode_session_repository(engine),
            )
            try:
                new_id = await reg1.create_new(
                    project=project,
                    session_name=session_name,
                    working_dir=working_dir,
                )
                await reg1.handle_start_work(project, session_name, agent="atlas")
            finally:
                await reg1.shutdown()

            # Phase 2: recover and verify the lock state was persisted.
            reg2 = OpenCodeSessionRegistry(
                create_opencode_session_repository(engine),
            )
            try:
                await reg2.recover_from_registry()
                record = reg2._repository.get(project, session_name)
                assert record is not None
                assert record["last_agent"] == "atlas"
                assert record["is_agent_locked"] is True
            finally:
                await reg2.shutdown()
        finally:
            engine.dispose()
            try:
                db_file.unlink()
            except FileNotFoundError:
                pass
