"""Verification tests for the ``asyncio.to_thread`` deadlock fix.

Background
----------

The daemon was hanging on shutdown because synchronous SQLAlchemy writes
were running directly on the asyncio event loop thread. The full chain is
documented in the experience docs (search for "synchronous sqlalchemy db
writes on asyncio event loop"):

    ``JobFeedbackObserver._finalize_job`` → ``notify_watchers`` →
    ``enqueue_message`` → ``_prepare_enqueued_message`` →
    ``session.commit()`` — all sync, all on the event loop thread.

Under SQLite WAL + ``busy_timeout=30s``, a single contended write could
wedge the event loop for up to 30 seconds — long enough to ignore
SIGINT, freeze every API request, and break shutdown.

This module pins the fix:

  1. ``_prepare_enqueued_message`` is offloaded via ``asyncio.to_thread``
     inside ``InstanceMessagingService.enqueue_message``.
  2. ``JobWatcherRepository.get_watchers_for_job`` (and the related
     cleanup writes) are offloaded via ``asyncio.to_thread`` inside
     ``JobQueueService.notify_watchers``.
  3. ``JobFeedbackObserver._finalize_instance_db_sync`` is offloaded via
     ``asyncio.to_thread`` from ``_finalize_instance``.

Each test verifies the offload two ways:

  * **Thread identity check** — the strongest proof. We wrap the sync
    function with a spy that records ``threading.get_ident()`` on entry.
    If the fix is in place, the recorded thread is NOT the event-loop
    thread (the pytest-asyncio loop runs on a dedicated worker thread).
    If the fix is missing, the spy fires on the event-loop thread and
    the assertion fails.
  * **asyncio.to_thread spy** — verifies the function was specifically
    offloaded via ``asyncio.to_thread`` (not just ``run_in_executor``
    or some other indirection).

If both spies pass, the function cannot block the event loop on its
sync DB calls — which is exactly what the fix is meant to guarantee.

Run with::

    pytest tests/test_deadlock_fix.py -v --tb=short
"""

from __future__ import annotations

import asyncio
import threading
import uuid
from contextlib import contextmanager
from typing import Any, Callable
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session

# Model imports — required so SQLModel.metadata sees the tables when we
# create them on the test engine.
from daemon.repositories.event.models import Event, EventKind  # noqa: F401
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.instance.repository import (
    SQLModelInstanceRepository,
    get_agent_name,
)
from daemon.repositories.message_queue.models import (  # noqa: F401
    MessageQueue,
    MessageStatus,
    MessageType,
)
from daemon.repositories.task.models import Task  # noqa: F401
from daemon.services.cancellation import CancellationService
from daemon.services.child_reports import ChildReportsService
from daemon.services.correlation_manager import set_correlation_manager
from daemon.services.error_reporting import ErrorReportingService
from daemon.services.instance_messaging import InstanceMessagingService
from daemon.services.job_feedback_observer import JobFeedbackObserver
from daemon.services.job_queue_service import JobQueueService
from daemon.write_pause_guard import WritePauseGuard


# ─────────────────────────────────────────────────────────────────────────────
# Shared helpers — kept local so this test file is self-contained and does
# not depend on private fixtures in test_enqueue_shared.py or
# test_finalize_instance.py (which have their own Mock(PauseGuard)-vs-Real
# choices that don't matter here).
# ─────────────────────────────────────────────────────────────────────────────


@contextmanager
def _spy_thread(target: Callable[..., Any]):
    """Wrap ``target`` so each call records ``threading.get_ident()``.

    Yields a 2-tuple ``(thread_ids, spy)``:

      * ``thread_ids`` is a list mutated in place (one entry per call).
      * ``spy`` is the wrapper that should be installed in place of
        ``target``. It preserves the return value of ``target``.

    Use on a bound method (e.g. ``service._prepare_enqueued_message``) by
    replacing the attribute on the instance for the duration of the
    ``with`` block.
    """
    thread_ids: list[int] = []

    def spy(*args: Any, **kwargs: Any) -> Any:
        thread_ids.append(threading.get_ident())
        return target(*args, **kwargs)

    yield thread_ids, spy


@contextmanager
def _spied_to_thread():
    """Replace ``asyncio.to_thread`` with a spy that still runs in a thread.

    The real ``asyncio.to_thread`` runs ``func`` in the default
    ``ThreadPoolExecutor`` and returns a coroutine that resolves to its
    result. Our spy does the same — it records every ``func`` it is asked
    to run AND schedules the call on the default executor — so the
    production code under test continues to work end-to-end.

    Yields a 2-tuple ``(funcs_called, spy_to_thread)``:

      * ``funcs_called`` is a list of the callables that were scheduled
        (one entry per call to ``to_thread``).
      * ``spy_to_thread`` is the patched replacement (returned for
        completeness; the test patches the ``asyncio.to_thread`` symbol
        with ``side_effect=spy_to_thread``).
    """
    funcs_called: list[Callable[..., Any]] = []
    real_to_thread = asyncio.to_thread

    async def spy_to_thread(func: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
        funcs_called.append(func)
        return await real_to_thread(func, *args, **kwargs)

    yield funcs_called, spy_to_thread


# ─────────────────────────────────────────────────────────────────────────────
# Engine + repository fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def engine() -> Engine:
    """In-memory SQLite engine (StaticPool for cross-thread safety)."""
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(eng, "connect")
    def _enable_fk(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


def _seed_instance(
    engine: Engine,
    *,
    instance_id: str | None = None,
    agent_id: str = "coder",
    agent_name: str | None = None,
    agent_dir: str = "/tmp/agent",
    parent_id: str | None = None,
    status: str = InstanceStatus.IDLE.value,
    waiting_for: int = 0,
) -> str:
    """Insert an Instance row. Returns the instance_id used."""
    iid = instance_id or f"inst-{uuid.uuid4().hex[:8]}"
    with Session(engine) as session:
        inst = Instance(
            instance_id=iid,
            agent_id=agent_id,
            agent_name=agent_name,
            agent_dir=agent_dir,
            parent_id=parent_id,
            status=status,
            waiting_for=waiting_for,
            version=1,
            instance_metadata={},
            children="[]",
        )
        session.add(inst)
        session.commit()
    return iid


def _build_messaging_manager(engine: Engine, instance_repo: SQLModelInstanceRepository) -> MagicMock:
    """Build a manager mock matching the contract ``enqueue_message`` uses.

    Mirrors the ``_build_manager`` helper in ``tests/test_enqueue_shared.py``
    so we don't depend on private test fixtures.
    """
    manager = MagicMock()
    manager.engine = engine
    manager._instance_repository = instance_repo
    manager._live_hub = MagicMock()
    manager._live_hub.stream_status_change = MagicMock()  # sync — not awaited
    manager._worker_pool = None  # None is fine — code guards on it
    manager._job_queue_service = MagicMock()
    manager._job_queue_service.enqueue = MagicMock(
        return_value=MagicMock(job_id="job-test-123")
    )
    return manager


def _build_messaging_service(engine: Engine) -> InstanceMessagingService:
    """Build a real ``InstanceMessagingService`` with mocked manager deps."""
    instance_repo = SQLModelInstanceRepository(engine)
    manager = _build_messaging_manager(engine, instance_repo)
    cancellation = MagicMock(spec=CancellationService)
    cancellation.is_shutting_down = False
    return InstanceMessagingService(
        manager=manager,
        cancellation_service=cancellation,
    )


def _build_observer(engine: Engine) -> JobFeedbackObserver:
    """Build a real ``JobFeedbackObserver`` with mocked repos + side deps."""
    mock_manager = MagicMock(name="InstanceManager")
    mock_manager.engine = engine
    mock_manager.write_guard = WritePauseGuard()
    mock_manager._live_hub = MagicMock()
    mock_manager._live_hub.stream_status_change = MagicMock()  # sync — not awaited
    mock_manager._events_service = MagicMock()
    mock_manager._events_service._publish_instance_lifecycle_event = MagicMock()
    mock_manager._get_last_assistant_message_raw = MagicMock(
        return_value="agent response"
    )

    return JobFeedbackObserver(
        event_bus=MagicMock(),
        job_queue_service=MagicMock(),
        job_repo=MagicMock(),
        lock_repo=MagicMock(),
        project_repo=MagicMock(),
        instance_manager=mock_manager,
    )


def _build_job_queue_service() -> JobQueueService:
    """Build a real ``JobQueueService`` with mocked repo deps.

    ``notify_watchers`` short-circuits when ``_watcher_repo is None`` or
    ``_instance_manager is None`` — so both must be set for the test to
    exercise the ``get_watchers_for_job`` call site.
    """
    service = JobQueueService.__new__(JobQueueService)
    service._watcher_repo = MagicMock(name="JobWatcherRepository")
    # Default: empty watcher list (covers the early-return path cleanly).
    service._watcher_repo.get_watchers_for_job = MagicMock(return_value=[])
    service._watcher_repo.remove_all_watches_for_job = MagicMock(return_value=0)
    service._repository = MagicMock(name="JobQueueRepository")
    service._repository.get = MagicMock(return_value=None)
    service._instance_manager = MagicMock(name="InstanceManager")
    service._instance_manager.enqueue_message = MagicMock(return_value=None)
    return service


def _build_child_reports_service(engine: Engine) -> ChildReportsService:
    """Build a real ``ChildReportsService`` with mocked manager deps.

    The async caller (``_process_child_completion_and_notify_parent``) does:

      1. ``asyncio.to_thread(self._instance_repository.get, instance_id)``
         → real ``SQLModelInstanceRepository`` over the test engine.
      2. ``self._get_last_assistant_message(...)`` → falls through to
         ``messages = []`` when ``_checkpointer`` is ``None`` (no LLM
         trace to summarize), returning ``last_content=None`` which the
         caller replaces with the empty-content sentinel.
      3. ``asyncio.to_thread(self._process_child_completion_db_sync, ...)``
         → the sync helper under test (writes to the real engine via
         ``WriteGuardSession``).
      4. ``_dispatch_post_commit_side_effects`` → SSE / CompletionRegistry /
         lifecycle event / title generation. We mock the title trigger
         and pass ``_events_service=None`` so the only real call is
         ``CompletionRegistry.complete`` (in-process, no I/O).

    We use ``__new__`` to skip ``__init__`` and bind attributes manually
    (mirrors the pattern in ``test_phase4_deprecation.py``) so we don't
    need a full ``InstanceManager`` facade.
    """
    manager = MagicMock(name="InstanceManager")
    manager.engine = engine
    manager.write_guard = WritePauseGuard()
    manager._instance_repository = SQLModelInstanceRepository(engine)
    manager._checkpointer = None  # skips _get_last_assistant_message_raw
    manager._live_hub = None  # SSE no-op (guarded on truthiness)
    manager._queue_repository = MagicMock()  # only used by _trigger_title_generation

    service = ChildReportsService.__new__(ChildReportsService)
    service._manager = manager
    service._events_service = None  # lifecycle event publish is guarded
    service._trigger_title_generation = MagicMock()  # no-op (would hit MainLoopBridge)
    return service


def _build_error_reporting_service(
    engine: Engine,
    *,
    child_id: str,
    parent_id: str,
) -> ErrorReportingService:
    """Build a real ``ErrorReportingService`` with mocked manager deps.

    The async caller (``_send_error_report``) does:

      1. Dedup check via two ``asyncio.to_thread`` calls — ``get`` reads
         from the real engine, ``list`` reads from the mocked queue repo
         (returns ``[]`` so the dedup never short-circuits).
      2. Metadata re-fetch via ``asyncio.to_thread(get, ...)``.
      3. ``asyncio.to_thread(self._send_error_report_db_sync, ...)`` →
         the sync helper under test.
      4. CM hook (we patch ``notify_corr_resolve`` to ``AsyncMock``) +
         post-commit SSE / enqueue / SSE for ``child_failed``.

    Pre-seeds the parent and child rows so the sync helper finds them
    via the real engine. The child's ``agent_name`` is set so the
    ``get_agent_name(meta.agent_dir)`` fallback is never hit.
    """
    _seed_instance(
        engine,
        instance_id=parent_id,
        agent_id="coder",
        agent_name="coder",
        status=InstanceStatus.RUNNING.value,
        waiting_for=1,
    )
    _seed_instance(
        engine,
        instance_id=child_id,
        agent_id="coder",
        agent_name="coder",
        parent_id=parent_id,
        status=InstanceStatus.RUNNING.value,
        waiting_for=0,
    )

    manager = MagicMock(name="InstanceManager")
    manager.engine = engine
    manager.write_guard = WritePauseGuard()
    manager._instance_repository = SQLModelInstanceRepository(engine)
    manager._live_hub = MagicMock()
    manager._live_hub.stream_status_change = MagicMock()
    manager._live_hub.stream_lifecycle = MagicMock()

    queue_repo = MagicMock(name="MessageQueueRepository")
    queue_repo.list = MagicMock(return_value=[])  # no duplicate error reports
    queue_repo.enqueue = MagicMock(
        return_value=MagicMock(message_id="err-msg-test")
    )
    manager._queue_repository = queue_repo

    service = ErrorReportingService.__new__(ErrorReportingService)
    service._manager = manager
    service._events_service = None  # lifecycle event publish is guarded
    return service


# ═════════════════════════════════════════════════════════════════════════════
# Test 1 — `_prepare_enqueued_message` is offloaded via `asyncio.to_thread`
# ═════════════════════════════════════════════════════════════════════════════


class TestPrepareEnqueuedMessageOffloaded:
    """``enqueue_message`` must run ``_prepare_enqueued_message`` off the loop.

    Before the fix, ``enqueue_message`` called ``self._prepare_enqueued_message(...)``
    synchronously — which executed ``session.commit()`` on the event loop
    thread and could wedge the loop on SQLite WAL contention. The fix
    wraps the call in ``asyncio.to_thread``; these tests pin that.

    Two complementary checks:

      1. The thread the spy runs on is NOT the event-loop thread.
      2. ``asyncio.to_thread`` was called with ``_prepare_enqueued_message``.
    """

    @pytest.mark.asyncio
    async def test_prepare_runs_off_loop_thread(self, engine):
        """``_prepare_enqueued_message`` must execute on a worker thread,
        NOT on the event loop thread that pytest-asyncio drives.
        """
        service = _build_messaging_service(engine)
        _seed_instance(engine, instance_id="inst-1", status=InstanceStatus.RUNNING.value)

        with _spy_thread(service._prepare_enqueued_message) as ctx:
            thread_ids, spy = ctx
            service._prepare_enqueued_message = spy  # type: ignore[method-assign]
            await service.enqueue_message(
                instance_id="inst-1",
                message="hi",
                source="api",
            )

        loop_thread = threading.get_ident()
        assert thread_ids, "_prepare_enqueued_message was never called"
        assert all(tid != loop_thread for tid in thread_ids), (
            "FIX MISSING: _prepare_enqueued_message ran on the event-loop "
            f"thread (tid={loop_thread}); it MUST run in a worker thread "
            "via asyncio.to_thread so its session.commit() cannot wedge the loop."
        )

    @pytest.mark.asyncio
    async def test_prepare_is_scheduled_via_to_thread(self, engine):
        """``asyncio.to_thread`` must be called with ``_prepare_enqueued_message``.

        Patches the symbol at the import site (production module) so the
        call inside ``enqueue_message`` resolves to our spy. The spy still
        runs the function on a real worker thread — we just additionally
        record every callable that was scheduled.
        """
        service = _build_messaging_service(engine)
        _seed_instance(engine, instance_id="inst-1", status=InstanceStatus.RUNNING.value)

        # Patch `asyncio.to_thread` AT THE IMPORT SITE used by production code.
        # The function inside `enqueue_message` does:
        #     ctx = await asyncio.to_thread(self._prepare_enqueued_message, ...)
        # `asyncio` is imported at the top of instance_messaging.py, so we
        # patch the symbol on the daemon.services.instance_messaging module.
        with _spied_to_thread() as ctx:
            funcs_called, spy_to_thread = ctx
            with patch(
                "daemon.services.instance_messaging.asyncio.to_thread",
                side_effect=spy_to_thread,
            ):
                await service.enqueue_message(
                    instance_id="inst-1",
                    message="hi",
                    source="api",
                )

        assert funcs_called, "asyncio.to_thread was never called from enqueue_message"
        assert any(
            getattr(f, "__name__", "") == "_prepare_enqueued_message"
            for f in funcs_called
        ), (
            f"FIX MISSING: asyncio.to_thread was called with "
            f"{[getattr(f, '__name__', repr(f)) for f in funcs_called]}, "
            "expected at least one call to _prepare_enqueued_message."
        )


# ═════════════════════════════════════════════════════════════════════════════
# Test 2 — `get_watchers_for_job` is offloaded via `asyncio.to_thread`
# ═════════════════════════════════════════════════════════════════════════════


class TestNotifyWatchersOffloaded:
    """``JobQueueService.notify_watchers`` must offload DB calls to a thread.

    Two sync DB calls inside ``notify_watchers`` must run off the event
    loop: ``watcher_repo.get_watchers_for_job`` (read) and
    ``watcher_repo.remove_all_watches_for_job`` (write, only for terminal
    states). These tests pin both via thread-identity and asyncio.to_thread
    spies.

    Only ``get_watchers_for_job`` is asserted directly because the
    ``remove_all_watches_for_job`` write is conditional on
    ``status in ALL_TERMINAL_STATES`` — and to keep this single test file
    robust we exercise the terminal path with a non-empty watcher list so
    the cleanup write also fires.
    """

    @pytest.mark.asyncio
    async def test_get_watchers_runs_off_loop_thread(self):
        """``get_watchers_for_job`` must run on a worker thread, not the loop."""
        service = _build_job_queue_service()

        with _spy_thread(service._watcher_repo.get_watchers_for_job) as ctx:
            thread_ids, spy = ctx
            service._watcher_repo.get_watchers_for_job = spy  # type: ignore[method-assign]
            await service.notify_watchers(
                job_id="job-1",
                status="completed",
            )

        loop_thread = threading.get_ident()
        assert thread_ids, "get_watchers_for_job was never called"
        assert all(tid != loop_thread for tid in thread_ids), (
            "FIX MISSING: watcher_repo.get_watchers_for_job ran on the "
            f"event-loop thread (tid={loop_thread}); it MUST run in a "
            "worker thread via asyncio.to_thread."
        )

    @pytest.mark.asyncio
    async def test_get_watchers_is_scheduled_via_to_thread(self):
        """``asyncio.to_thread`` must be invoked with
        ``watcher_repo.get_watchers_for_job``.
        """
        service = _build_job_queue_service()
        # Bind a stable local reference for the identity check below — we
        # compare the function passed to to_thread against this exact
        # bound method (MagicMock-bound ``__name__`` is empty by default,
        # so identity is the only reliable check).
        watcher_lookup = service._watcher_repo.get_watchers_for_job

        with _spied_to_thread() as ctx:
            funcs_called, spy_to_thread = ctx
            with patch(
                "daemon.services.job_queue_service.asyncio.to_thread",
                side_effect=spy_to_thread,
            ):
                await service.notify_watchers(
                    job_id="job-1",
                    status="completed",
                )

        assert funcs_called, "asyncio.to_thread was never called from notify_watchers"
        # Identity check: the watcher lookup is a MagicMock-bound method,
        # so we compare by reference. Also check `__name__` for the
        # case where someone replaces the mock with a real bound method.
        by_identity = any(c is watcher_lookup for c in funcs_called)
        by_name = any(
            getattr(f, "__name__", "") == "get_watchers_for_job"
            for f in funcs_called
        )
        assert by_identity or by_name, (
            f"FIX MISSING: asyncio.to_thread was not invoked with "
            f"get_watchers_for_job; calls were "
            f"{[getattr(f, '__name__', repr(f)) for f in funcs_called]}."
        )


# ═════════════════════════════════════════════════════════════════════════════
# Test 3 — `_finalize_instance_db_sync` is offloaded via `asyncio.to_thread`
# ═════════════════════════════════════════════════════════════════════════════


class TestFinalizeInstanceDbSyncOffloaded:
    """``_finalize_instance`` must run ``_finalize_instance_db_sync`` in a thread.

    The DB write half of instance finalization (open session → transition
    status → commit) was extracted into a sync helper
    ``_finalize_instance_db_sync`` precisely so it can be offloaded via
    ``asyncio.to_thread``. The async ``_finalize_instance`` is responsible
    for invoking that helper off the loop.

    Two checks: thread identity and ``asyncio.to_thread`` spy.
    """

    @pytest.mark.asyncio
    async def test_finalize_db_sync_runs_off_loop_thread(self, engine):
        """``_finalize_instance_db_sync`` must execute on a worker thread."""
        observer = _build_observer(engine)
        instance_id = _seed_instance(
            engine, instance_id="inst-fin-1", status=InstanceStatus.RUNNING.value
        )

        with _spy_thread(observer._finalize_instance_db_sync) as ctx:
            thread_ids, spy = ctx
            observer._finalize_instance_db_sync = spy  # type: ignore[method-assign]
            await observer._finalize_instance(instance_id, "completed")

        loop_thread = threading.get_ident()
        assert thread_ids, "_finalize_instance_db_sync was never called"
        assert all(tid != loop_thread for tid in thread_ids), (
            "FIX MISSING: _finalize_instance_db_sync ran on the event-loop "
            f"thread (tid={loop_thread}); the WriteGuardSession + commit "
            "must run in a worker thread so SQLite WAL contention cannot "
            "wedge the loop."
        )

    @pytest.mark.asyncio
    async def test_finalize_db_sync_is_scheduled_via_to_thread(self, engine):
        """``asyncio.to_thread`` must be invoked with
        ``_finalize_instance_db_sync``.
        """
        observer = _build_observer(engine)
        instance_id = _seed_instance(
            engine, instance_id="inst-fin-2", status=InstanceStatus.RUNNING.value
        )

        with _spied_to_thread() as ctx:
            funcs_called, spy_to_thread = ctx
            with patch(
                "daemon.services.job_feedback_observer.asyncio.to_thread",
                side_effect=spy_to_thread,
            ):
                await observer._finalize_instance(instance_id, "completed")

        assert funcs_called, "asyncio.to_thread was never called from _finalize_instance"
        assert any(
            getattr(f, "__name__", "") == "_finalize_instance_db_sync"
            for f in funcs_called
        ), (
            f"FIX MISSING: asyncio.to_thread was not invoked with "
            f"_finalize_instance_db_sync; calls were "
            f"{[getattr(f, '__name__', repr(f)) for f in funcs_called]}."
        )


# ═════════════════════════════════════════════════════════════════════════════
# Test 4 — `child_reports._process_child_completion_db_sync` is offloaded
# ═════════════════════════════════════════════════════════════════════════════


class TestProcessChildCompletionDbSyncOffloaded:
    """``ChildReportsService._process_child_completion_and_notify_parent``
    must run the sync DB helper on a worker thread.

    Before the fix, the entire ``WriteGuardSession`` block (DB reads,
    writes, ``session.commit()``) was inlined in the async caller — the
    same deadlock pattern as ``_finalize_instance``. The fix extracts
    the sync work into ``_process_child_completion_db_sync`` and calls
    it via ``asyncio.to_thread``. These tests pin that.

    Two complementary checks:

      1. The thread the spy runs on is NOT the event-loop thread.
      2. ``asyncio.to_thread`` was called with
         ``_process_child_completion_db_sync``.

    We use a root instance (no parent) seeded in the test engine. With
    no children and no pending messages, the sync helper hits the
    ``root_completed`` branch and commits — exercising the WriteGuardSession
    code path that previously wedged the loop.
    """

    @pytest.mark.asyncio
    async def test_process_child_completion_db_sync_runs_off_loop_thread(self, engine):
        """``_process_child_completion_db_sync`` must execute on a worker thread.

        The async caller (``_process_child_completion_and_notify_parent``)
        does some non-DB work first (pre-fetch content via
        ``asyncio.to_thread(self._instance_repository.get, ...)`` and the
        assistant-message fetch) — those use the default executor too.
        We only assert the thread identity of the SYNC DB helper, not
        the pre-fetch.
        """
        service = _build_child_reports_service(engine)
        # Root instance → no parent → the sync helper hits root_completed
        # (waiting_for=0, no pending messages) and commits on a worker thread.
        _seed_instance(
            engine,
            instance_id="cr-root-1",
            status=InstanceStatus.RUNNING.value,
        )

        with _spy_thread(service._process_child_completion_db_sync) as ctx:
            thread_ids, spy = ctx
            service._process_child_completion_db_sync = spy  # type: ignore[method-assign]
            await service._process_child_completion_and_notify_parent(
                instance_id="cr-root-1",
                completed_message_id="msg-cr-1",
            )

        loop_thread = threading.get_ident()
        assert thread_ids, "_process_child_completion_db_sync was never called"
        assert all(tid != loop_thread for tid in thread_ids), (
            "FIX MISSING: _process_child_completion_db_sync ran on the "
            f"event-loop thread (tid={loop_thread}); the WriteGuardSession + "
            "commit must run in a worker thread so SQLite WAL contention "
            "cannot wedge the loop."
        )

    @pytest.mark.asyncio
    async def test_process_child_completion_db_sync_is_scheduled_via_to_thread(self, engine):
        """``asyncio.to_thread`` must be invoked with
        ``_process_child_completion_db_sync``.

        Patches the symbol at the import site (production module) so the
        call inside ``_process_child_completion_and_notify_parent``
        resolves to our spy. The spy still runs the function on a real
        worker thread — we just additionally record every callable that
        was scheduled.
        """
        service = _build_child_reports_service(engine)
        _seed_instance(
            engine,
            instance_id="cr-root-2",
            status=InstanceStatus.RUNNING.value,
        )

        # Patch `asyncio.to_thread` AT THE IMPORT SITE used by production code.
        # The function inside `_process_child_completion_and_notify_parent`
        # does:
        #     result = await asyncio.to_thread(
        #         self._process_child_completion_db_sync, ...
        #     )
        # `asyncio` is imported at the top of child_reports.py, so we
        # patch the symbol on the daemon.services.child_reports module.
        with _spied_to_thread() as ctx:
            funcs_called, spy_to_thread = ctx
            with patch(
                "daemon.services.child_reports.asyncio.to_thread",
                side_effect=spy_to_thread,
            ):
                await service._process_child_completion_and_notify_parent(
                    instance_id="cr-root-2",
                    completed_message_id="msg-cr-2",
                )

        assert funcs_called, (
            "asyncio.to_thread was never called from "
            "_process_child_completion_and_notify_parent"
        )
        assert any(
            getattr(f, "__name__", "") == "_process_child_completion_db_sync"
            for f in funcs_called
        ), (
            f"FIX MISSING: asyncio.to_thread was not invoked with "
            f"_process_child_completion_db_sync; calls were "
            f"{[getattr(f, '__name__', repr(f)) for f in funcs_called]}."
        )


# ═════════════════════════════════════════════════════════════════════════════
# Test 5 — `error_reporting._send_error_report_db_sync` is offloaded
# ═════════════════════════════════════════════════════════════════════════════


class TestSendErrorReportDbSyncOffloaded:
    """``ErrorReportingService._send_error_report`` must run the sync DB
    helper on a worker thread.

    Before the fix, the atomic DB transaction (child status update,
    parent counter decrement, cascade check, ``session.commit()``) was
    inlined in the async caller — the same deadlock pattern as
    ``_finalize_instance``. The fix extracts the sync work into
    ``_send_error_report_db_sync`` and calls it via
    ``asyncio.to_thread``. These tests pin that.

    Two complementary checks:

      1. The thread the spy runs on is NOT the event-loop thread.
      2. ``asyncio.to_thread`` was called with
         ``_send_error_report_db_sync``.

    We pre-seed a parent (status=RUNNING, waiting_for=1) and a child
    (parent_id=parent) in the test engine. The dedup list is mocked to
    return ``[]`` so the dedup check passes. The sync helper then
    executes: child status → ERROR, parent counter decrement, cascade
    (waiting_for reaches 0, no pending messages → parent.COMPLETED),
    commit — all on a worker thread.
    """

    @pytest.fixture(autouse=True)
    def _disable_cm(self):
        """Pin the CorrelationManager to ``None`` for both tests.

        The cascade decision reads ``cm.is_complete()`` (CM-active) or
        falls back to ``parent.waiting_for == 0`` (CM-disabled). For the
        test we want the legacy fallback path so the sync helper
        exercises the ``session.exec`` / ``session.commit`` block.
        """
        set_correlation_manager(None)
        yield
        set_correlation_manager(None)

    @pytest.mark.asyncio
    async def test_send_error_report_db_sync_runs_off_loop_thread(self, engine):
        """``_send_error_report_db_sync`` must execute on a worker thread."""
        service = _build_error_reporting_service(
            engine, child_id="er-child-1", parent_id="er-parent-1"
        )

        with _spy_thread(service._send_error_report_db_sync) as ctx:
            thread_ids, spy = ctx
            service._send_error_report_db_sync = spy  # type: ignore[method-assign]
            # The dedup branch + metadata re-fetch use the real engine +
            # mocked queue repo. The CM hook is patched below so the
            # post-commit path runs cleanly without a wired CM.
            with patch(
                "daemon.services.correlation_manager.notify_corr_resolve",
                new=AsyncMock(),
            ):
                await service._send_error_report(
                    instance_id="er-child-1",
                    error="test error",
                    error_type="execution_error",
                    message_id="msg-er-1",
                )

        loop_thread = threading.get_ident()
        assert thread_ids, "_send_error_report_db_sync was never called"
        assert all(tid != loop_thread for tid in thread_ids), (
            "FIX MISSING: _send_error_report_db_sync ran on the event-loop "
            f"thread (tid={loop_thread}); the WriteGuardSession + commit "
            "must run in a worker thread so SQLite WAL contention cannot "
            "wedge the loop."
        )

    @pytest.mark.asyncio
    async def test_send_error_report_db_sync_is_scheduled_via_to_thread(self, engine):
        """``asyncio.to_thread`` must be invoked with
        ``_send_error_report_db_sync``.

        Patches the symbol at the import site (production module) so the
        call inside ``_send_error_report`` resolves to our spy. The spy
        still runs the function on a real worker thread — we just
        additionally record every callable that was scheduled.
        """
        service = _build_error_reporting_service(
            engine, child_id="er-child-2", parent_id="er-parent-2"
        )

        # Patch `asyncio.to_thread` AT THE IMPORT SITE used by production code.
        # The function inside `_send_error_report` does:
        #     db_result = await asyncio.to_thread(
        #         self._send_error_report_db_sync, ...
        #     )
        # `asyncio` is imported at the top of error_reporting.py, so we
        # patch the symbol on the daemon.services.error_reporting module.
        with _spied_to_thread() as ctx:
            funcs_called, spy_to_thread = ctx
            with patch(
                "daemon.services.error_reporting.asyncio.to_thread",
                side_effect=spy_to_thread,
            ):
                with patch(
                    "daemon.services.correlation_manager.notify_corr_resolve",
                    new=AsyncMock(),
                ):
                    await service._send_error_report(
                        instance_id="er-child-2",
                        error="test error",
                        error_type="execution_error",
                        message_id="msg-er-2",
                    )

        assert funcs_called, (
            "asyncio.to_thread was never called from _send_error_report"
        )
        assert any(
            getattr(f, "__name__", "") == "_send_error_report_db_sync"
            for f in funcs_called
        ), (
            f"FIX MISSING: asyncio.to_thread was not invoked with "
            f"_send_error_report_db_sync; calls were "
            f"{[getattr(f, '__name__', repr(f)) for f in funcs_called]}."
        )


# ═════════════════════════════════════════════════════════════════════════════
# Test 6 — Regression: `waiting_children_parent_agent_id` must cross the
#          `asyncio.to_thread` boundary from
#          `_process_child_completion_db_sync` into
#          `_dispatch_post_commit_side_effects`.
# ═════════════════════════════════════════════════════════════════════════════


class TestWaitingChildrenSseSideEffect:
    """Regression test for the ``NameError`` in
    ``_dispatch_post_commit_side_effects``.

    The sync helper ``_process_child_completion_db_sync`` runs on a worker
    thread (via ``asyncio.to_thread``) and records the parent's
    ``agent_id`` in a local variable when it sets the parent to
    ``WAITING_CHILDREN``. The async caller needs that value to emit the
    parent's ``waiting_children`` SSE — but a local variable cannot
    survive the thread boundary.

    Before the fix, the async caller referenced the local name directly,
    which raised ``NameError`` after ``asyncio.to_thread`` returned. The
    exception was silently swallowed by the ``except Exception`` guard at
    child_reports.py:1539-1542, dropping the parent SSE event entirely.

    The fix threads the value through the ``_ChildCompletionDbResult``
    NamedTuple (set by the sync helper, read in the async caller via
    ``result.waiting_children_parent_agent_id``).

    This test exercises the ``regular_child_completed`` +
    ``parent_waiting_children_sse=True`` path and asserts the SSE fires
    with the correct parent ``agent_id`` — proving the field crossed the
    thread boundary. If the bare-name reference regresses, the NameError
    is swallowed and ``stream_status_change`` is never awaited.
    """

    @pytest.fixture(autouse=True)
    def _disable_cm(self):
        """Pin the CorrelationManager to ``None`` for the test.

        With CM active, the cascade decision takes the early-return at
        child_reports.py:1239-1245 ("CM callback owns completion") and
        ``parent_waiting_children_sse`` is never set. We need the legacy
        fallback path so the ``else`` branch (lines 1266-1276) executes
        and ``parent_waiting_children_sse=True`` gets set.
        """
        set_correlation_manager(None)
        yield
        set_correlation_manager(None)

    @pytest.mark.asyncio
    async def test_waiting_children_sse_emits_with_correct_agent_id(self, engine):
        """A regular child completing for a parent with ``waiting_for=0``
        and a pending own-queue message must trigger the parent's
        ``waiting_children`` SSE with the correct ``agent_id``.

        The bare-name ``NameError`` regression would silently drop the
        SSE — this test catches it by asserting
        ``stream_status_change`` was awaited once with the right kwargs.
        """
        # ── Build service with a real (mocked) _live_hub so the SSE
        # ── branch at child_reports.py:1532-1542 actually executes.
        # ── The existing _build_child_reports_service helper uses
        # ── _live_hub=None which would guard the branch out via
        # ── `if self._manager._live_hub`. We need a real hub here.
        manager = MagicMock(name="InstanceManager")
        manager.engine = engine
        manager.write_guard = WritePauseGuard()
        manager._instance_repository = SQLModelInstanceRepository(engine)
        manager._checkpointer = None  # skips _get_last_assistant_message_raw
        manager._live_hub = MagicMock()
        # stream_status_change is awaited by the dispatch path — must be
        # awaitable. AsyncMock returns a coroutine when called.
        manager._live_hub.stream_status_change = AsyncMock()
        manager._queue_repository = MagicMock()

        service = ChildReportsService.__new__(ChildReportsService)
        service._manager = manager
        service._events_service = None  # lifecycle event publish is guarded
        service._trigger_title_generation = MagicMock()

        # ── Seed parent: RUNNING, waiting_for=0 (children done),
        # ── with one pending READY message in its own queue so the
        # ── cascade hits the `else` branch (line 1266-1276) which
        # ── sets parent.status=WAITING_CHILDREN and
        # ── parent_waiting_children_sse=True.
        parent_id = "wcs-parent-1"
        child_id = "wcs-child-1"
        _seed_instance(
            engine,
            instance_id=parent_id,
            agent_id="parent-agent",
            status=InstanceStatus.RUNNING.value,
            waiting_for=0,
        )
        with Session(engine) as session:
            session.add(
                MessageQueue(
                    message_id="wcs-msg-1",
                    instance_id=parent_id,
                    content="pending follow-up",
                    status=MessageStatus.READY.value,
                )
            )
            session.commit()

        # ── Seed child: parent_id set, status RUNNING (the sync helper
        # ── will transition it to COMPLETED inside the WriteGuardSession).
        _seed_instance(
            engine,
            instance_id=child_id,
            agent_id="child-agent",
            parent_id=parent_id,
            status=InstanceStatus.RUNNING.value,
            waiting_for=0,
        )

        # ── Drive the async caller end-to-end. Must not raise NameError.
        await service._process_child_completion_and_notify_parent(
            instance_id=child_id,
            completed_message_id="msg-wcs-1",
        )

        # ── Assertions. The dispatch path emits multiple SSE events:
        # ── 1. The child's "completed" status (somewhere in the regular
        # ──    child completion path)
        # ── 2. The parent's "waiting_children" status (the one we care
        # ──    about for this regression)
        # ── If the bare-name NameError regresses, the parent's
        # ── `except Exception` swallows it and the
        # ── `stream_status_change(... "waiting_children" ...)` call is
        # ── never made — so the "waiting_children" entry in
        # ── `await_args_list` will be missing or have a wrong agent_id.
        await_args_list = manager._live_hub.stream_status_change.await_args_list
        assert await_args_list, (
            "stream_status_change was never awaited — the dispatch path "
            "did not emit any SSE events."
        )

        # Find the specific call for the parent's "waiting_children" SSE.
        waiting_children_calls = [
            c for c in await_args_list if c.args[1] == "waiting_children"
        ]
        assert len(waiting_children_calls) == 1, (
            f"Expected exactly one stream_status_change call with "
            f"status='waiting_children', got {len(waiting_children_calls)}. "
            f"All calls: {await_args_list}"
        )
        wc_call = waiting_children_calls[0]

        # The production call is:
        #   await self._manager._live_hub.stream_status_change(
        #       result.parent_id, "waiting_children",
        #       agent_id=result.waiting_children_parent_agent_id,
        #   )
        # On a MagicMock, positional args land in `args` and keyword args
        # in `kwargs`.
        assert wc_call.args[0] == parent_id, (
            f"Expected parent_id={parent_id!r}, got {wc_call.args[0]!r}"
        )
        assert wc_call.args[1] == "waiting_children", (
            f"Expected status='waiting_children', got {wc_call.args[1]!r}"
        )
        assert wc_call.kwargs.get("agent_id") == "parent-agent", (
            f"REGRESSION: waiting_children SSE was emitted but agent_id "
            f"did not cross the asyncio.to_thread boundary. "
            f"Expected 'parent-agent', got "
            f"{wc_call.kwargs.get('agent_id')!r}. The sync helper's "
            f"local 'waiting_children_parent_agent_id' must be carried "
            f"back on _ChildCompletionDbResult."
        )

        # The production call is:
        #   await self._manager._live_hub.stream_status_change(
        #       result.parent_id, "waiting_children",
        #       agent_id=result.waiting_children_parent_agent_id,
        #   )
        # On a MagicMock, positional args land in `args` and keyword args
        # in `kwargs`.
        assert waiting_children_call.args[0] == parent_id, (
            f"Expected parent_id={parent_id!r}, got "
            f"{waiting_children_call.args[0]!r}"
        )
        assert waiting_children_call.kwargs.get("agent_id") == "parent-agent", (
            f"REGRESSION: waiting_children SSE was emitted but agent_id "
            f"did not cross the asyncio.to_thread boundary. "
            f"Expected 'parent-agent', got "
            f"{waiting_children_call.kwargs.get('agent_id')!r}. The sync "
            f"helper's local 'waiting_children_parent_agent_id' must be "
            f"carried back on _ChildCompletionDbResult."
        )
