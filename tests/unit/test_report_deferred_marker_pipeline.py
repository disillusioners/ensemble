"""Unit tests for the Site 1 DEFERRED marker dispatch in
``MessageProcessingPipeline`` (pause-report-recovery Phase 1, Task 1.4).

The Site 1 fix hardens the natural child-completion skip against the
pause cascade's cancel race (FM-11):

* the pause-check result is cached BEFORE the try/except so the
  finally block can schedule the marker write;
* the marker write is dispatched via
  ``asyncio.create_task(asyncio.shield(asyncio.to_thread(...)))`` —
  schedule and DETACH, do not hold the Task ref, do not await from
  the cancelled finally;
* the detached task survives the pause-check cancel point AND the
  ``_handle_cancel`` second cancel point by construction;
* parent_id is fetched inside the dispatched coroutine
  (``ProcessingContext`` has no ``parent_id`` member — W5);
* root instances (parent_id is None) do not crash — no marker is
  written.

These tests cover:

* Site 1 normal skip — pause detected, marker written, no crash;
* cancel-at-await — pause-check returns mid-Stage-6, marker survives
  (both cancel points: the pause-check await and the
  ``_handle_cancel.on_cancel`` second await);
* pre-try hoist window — the pause cascade's cancel lands DURING the
  hoisted ``_is_instance_paused`` await (before try-entry): the
  CancelledError escapes ``execute()`` cleanly with NO marker — the
  plan-assigned escape lane (Phase 2 no-row backstop);
* root-instance no-marker no-crash (W5);
* DB-error best-effort — marker write failure is logged, not raised.
"""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool, StaticPool
from sqlmodel import Session, SQLModel, select as sm_select

# Import all tables used by the pipeline marker dispatch.
from daemon.constants import DEFERRED_REASON_PAUSE_TOCTOU
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.report_injection.models import (
    ReportInjection,
    ReportInjectionState,
)
from daemon.services.message_processing_pipeline import (
    MessageProcessingPipeline,
    ProcessingContext,
    ProcessingResult,
    PipelineCallbacks,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def engine() -> Engine:
    """In-memory SQLite engine with all required tables created."""
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

    # Register every table that touches the report_injection write path.
    import daemon.repositories.dependency_bus.models  # noqa: F401
    import daemon.repositories.event.models  # noqa: F401
    import daemon.repositories.instance.models  # noqa: F401
    import daemon.repositories.job_queue.models  # noqa: F401
    import daemon.repositories.message_queue.models  # noqa: F401
    import daemon.repositories.report_injection.models  # noqa: F401
    import daemon.repositories.task.models  # noqa: F401

    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


def _seed_child_instance(
    engine: Engine,
    *,
    parent_id: str | None = "parent-1",
    status: str = InstanceStatus.PAUSED.value,
) -> str:
    """Insert a child instance row with the given parent_id."""
    instance_id = f"site1-{uuid.uuid4().hex[:8]}"
    with Session(engine) as session:
        session.add(
            Instance(
                instance_id=instance_id,
                agent_id="child-agent",
                agent_name="child",
                agent_dir="/tmp/child",
                parent_id=parent_id,
                status=status,
                version=1,
                instance_metadata={},
            )
        )
        session.commit()
    return instance_id


def _build_pipeline_with_repos(
    engine: Engine,
) -> tuple[MessageProcessingPipeline, MagicMock, MagicMock]:
    """Build a MessageProcessingPipeline with real repo wiring.

    The ``manager`` is a MagicMock but exposes ``engine`` (real),
    ``_report_injection_repo`` (real ReportInjectionRepository),
    ``_instance_repository`` (real, simple stub), and
    ``_process_child_completion_and_notify_parent`` (no-op). The
    pipeline's ``_is_instance_paused`` reads from a real DB; the
    marker dispatch writes to a real DB.
    """
    from daemon.repositories.report_injection.repository import (
        ReportInjectionRepository,
    )

    pipeline = MessageProcessingPipeline.__new__(MessageProcessingPipeline)
    manager = MagicMock(spec=[])
    manager.engine = engine
    manager._report_injection_repo = ReportInjectionRepository(engine)

    # Minimal instance repository: a ``get`` that reads from the DB.
    class _InstRepo:
        def __init__(self, eng: Engine) -> None:
            self.engine = eng

        def get(self, instance_id: str) -> Instance | None:
            with Session(self.engine) as session:
                return session.get(Instance, instance_id)

    inst_repo = _InstRepo(engine)
    manager._instance_repository = inst_repo

    pipeline._manager = manager
    pipeline._execution_gate = MagicMock()
    pipeline._source_dispatcher = None
    pipeline._queue_repository = None
    return pipeline, manager, inst_repo


def _context_for(instance_id: str, message_id: str = "msg-1") -> ProcessingContext:
    """Build a minimal ProcessingContext."""
    return ProcessingContext(
        instance_id=instance_id,
        message_id=message_id,
        message="hello",
    )


def _no_callbacks() -> PipelineCallbacks:
    """Build a no-op PipelineCallbacks."""
    return PipelineCallbacks(
        on_success=None,
        on_error=None,
        on_contention=None,
        on_cancel=None,
    )


# =============================================================================
# TestMarkerDispatchedOnPause
# =============================================================================


class TestSite1MarkerDispatchedOnPause:
    """The Site 1 detached-shield marker write survives pause + cancel."""

    @pytest.mark.asyncio
    async def test_normal_skip_writes_deferred_marker(
        self, engine: Engine
    ) -> None:
        """Stage 6: instance is PAUSED → natural child-completion path
        is skipped → DEFERRED marker is persisted on
        ``report_injections`` (Phase 1) so the parent's delivery
        obligation survives the pause.

        This is the happy-path test: no cancellation, just the pause
        re-check returning True.
        """
        pipeline, manager, _ = _build_pipeline_with_repos(engine)
        instance_id = _seed_child_instance(
            engine, parent_id="parent-1"
        )

        # Call the helper that the pipeline's finally block invokes.
        pipeline._schedule_deferred_pause_marker(
            instance_id=instance_id,
            child_message_id="msg-1",
        )

        # Let the detached task complete.
        await asyncio.sleep(0.05)

        # Verify the marker landed.
        with Session(engine) as session:
            rows = list(
                session.exec(
                    sm_select(ReportInjection).where(
                        ReportInjection.child_instance_id == instance_id
                    )
                ).all()
            )
        assert len(rows) == 1
        row = rows[0]
        assert row.state == ReportInjectionState.DEFERRED.value
        assert row.deferred_reason == DEFERRED_REASON_PAUSE_TOCTOU
        assert row.parent_instance_id == "parent-1"
        assert row.child_message_id == "msg-1"
        assert row.report_message_id is None
        assert row.content is None
        assert row.recovery_attempted_at is None

    @pytest.mark.asyncio
    async def test_root_instance_no_marker_no_crash(
        self, engine: Engine
    ) -> None:
        """W5: a root instance (parent_id is None) must not crash the
        dispatcher — and must NOT write a marker (root instances have
        no parent and therefore no delivery obligation)."""
        pipeline, manager, _ = _build_pipeline_with_repos(engine)
        instance_id = _seed_child_instance(engine, parent_id=None)

        # Must not raise.
        pipeline._schedule_deferred_pause_marker(
            instance_id=instance_id,
            child_message_id="msg-1",
        )
        # Let any scheduled task complete.
        await asyncio.sleep(0.05)

        # No marker written (root has no parent).
        with Session(engine) as session:
            rows = list(
                session.exec(
                    sm_select(ReportInjection).where(
                        ReportInjection.child_instance_id == instance_id
                    )
                ).all()
            )
        assert rows == []

    @pytest.mark.asyncio
    async def test_disappeared_instance_no_marker_no_crash(
        self, engine: Engine
    ) -> None:
        """W5: an instance that disappears between schedule and
        execution (e.g. race with cascade termination) must not crash
        the dispatcher — no marker is written."""
        pipeline, manager, _ = _build_pipeline_with_repos(engine)
        # No instance seeded — the lookup returns None.

        pipeline._schedule_deferred_pause_marker(
            instance_id="never-existed",
            child_message_id="msg-1",
        )
        await asyncio.sleep(0.05)
        # No crash, no rows.
        with Session(engine) as session:
            rows = list(
                session.exec(sm_select(ReportInjection)).all()
            )
        assert rows == []

    @pytest.mark.asyncio
    async def test_db_error_is_best_effort(self, engine: Engine) -> None:
        """DB errors during the marker write are best-effort: logged
        at WARNING, NOT raised. The detached task swallows the error
        and does not crash the pipeline."""
        pipeline, manager, _ = _build_pipeline_with_repos(engine)
        instance_id = _seed_child_instance(
            engine, parent_id="parent-1"
        )

        # Force the repository's ``ensure_deferred`` to raise.
        class _BoomRepo:
            def ensure_deferred(self, **kwargs):  # noqa: ANN003
                raise RuntimeError("simulated DB error")

        manager._report_injection_repo = _BoomRepo()

        # Must not raise.
        pipeline._schedule_deferred_pause_marker(
            instance_id=instance_id,
            child_message_id="msg-1",
        )
        await asyncio.sleep(0.05)
        # No marker (the simulated error prevented the write).
        with Session(engine) as session:
            rows = list(
                session.exec(
                    sm_select(ReportInjection).where(
                        ReportInjection.child_instance_id == instance_id
                    )
                ).all()
            )
        assert rows == []

    @pytest.mark.asyncio
    async def test_concurrent_duplicate_marker_absorbed(
        self, engine: Engine
    ) -> None:
        """W6: a concurrent duplicate ``ensure_deferred`` for the
        same triple must be absorbed (no crash, single row).

        D-1 de-flake (2026-08-20): the original test waited a fixed
        ``asyncio.sleep(0.05)`` for two detached ``shield(to_thread(...))``
        hops — empirically too tight under load (observed ~37% flake
        standalone). Worse, the shared ``StaticPool`` SQLite engine
        caused SQLAlchemy ``InvalidRequestError: Could not refresh
        instance`` when the two threadpool writers reused the same
        underlying connection's identity map (each ``ensure_deferred``
        does ``session.refresh(row)``; the second writer's refresh
        raced with the first writer's commit/refresh on the same
        connection). The dispatcher's broad ``except Exception``
        swallowed both errors and zero rows landed — which is the
        *production* mitigation path (Phase 2 no-row backstop), but
        not the contract this test pins.

        Test-only fix (two changes, no production code touched):

        * **Deterministic rendezvous (option a)**: wrap
          ``asyncio.create_task`` with a tracker that retains every
          dispatched Task, then ``await asyncio.gather(*tasks)`` before
          asserting. The rendezvous is exact: every detached write is
          finished (either committed or absorbed) before the test
          reads the DB.
        * **No shared connection (option b secondary)**: build a
          per-test ``NullPool`` engine for the two concurrent writers
          so each threadpool worker gets its OWN SQLite connection
          and identity map. The shared ``StaticPool`` fixture stays
          in place — every other test in this file uses a SINGLE
          writer and never observed the refresh race.
        """
        import daemon.services.message_processing_pipeline as _mpp_mod

        # Per-test engine: file-backed SQLite with NullPool so each
        # threadpool writer gets its OWN connection (no StaticPool
        # identity-map contention) AND the schema persists across
        # connections (a NullPool ``:memory:`` SQLite opens a fresh
        # empty DB per connection — useless). The tmp file is cleaned
        # up in the ``finally`` below.
        import tempfile

        _tmp_db = tempfile.NamedTemporaryFile(
            prefix="site1-concurrent-", suffix=".sqlite", delete=False
        )
        _tmp_db.close()
        _isolated_db_path = _tmp_db.name

        isolated_engine = create_engine(
            f"sqlite:///{_isolated_db_path}",
            connect_args={"check_same_thread": False},
            poolclass=NullPool,
        )

        @event.listens_for(isolated_engine, "connect")
        def _enable_fk(dbapi_conn, _connection_record):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        SQLModel.metadata.create_all(isolated_engine)
        try:
            pipeline, manager, _ = _build_pipeline_with_repos(
                isolated_engine
            )
            instance_id = _seed_child_instance(
                isolated_engine, parent_id="parent-1"
            )

            # Capture dispatched Tasks deterministically. We wrap
            # ``asyncio.create_task`` so the test can ``await`` both
            # detached writes to completion before reading the DB.
            # Production code is untouched — the wrapper lives only
            # inside this test.
            captured_tasks: list[asyncio.Task] = []
            original_create_task = _mpp_mod.asyncio.create_task

            def _tracking_create_task(coro, *, name=None):  # noqa: ANN001
                t = original_create_task(coro, name=name)
                captured_tasks.append(t)
                return t

            _mpp_mod.asyncio.create_task = _tracking_create_task
            try:
                # Fire the dispatcher twice for the same triple
                # (simulates a router/sweep/Site-1 race).
                pipeline._schedule_deferred_pause_marker(
                    instance_id=instance_id,
                    child_message_id="msg-1",
                )
                pipeline._schedule_deferred_pause_marker(
                    instance_id=instance_id,
                    child_message_id="msg-1",
                )
                assert len(captured_tasks) == 2, (
                    f"expected both schedule calls to dispatch a Task, "
                    f"got {len(captured_tasks)}"
                )
                # Deterministic rendezvous: wait for both detached
                # writes to settle (each completes with the dispatcher
                # swallowing its own errors; gather never re-raises
                # because the dispatched coroutine catches them).
                await asyncio.gather(
                    *captured_tasks, return_exceptions=True
                )
            finally:
                _mpp_mod.asyncio.create_task = original_create_task

            # Exactly ONE row — the partial unique index rejects the
            # duplicate and ``ensure_deferred`` absorbs it.
            with Session(isolated_engine) as session:
                rows = list(
                    session.exec(
                        sm_select(ReportInjection).where(
                            ReportInjection.child_instance_id
                            == instance_id
                        )
                    ).all()
                )
            assert len(rows) == 1
        finally:
            isolated_engine.dispose()
            import os
            try:
                os.unlink(_isolated_db_path)
            except FileNotFoundError:
                pass


# =============================================================================
# Detached-task cancellation safety
# =============================================================================


class TestSite1MarkerSurvivesCancellation:
    """The detached-shield pattern must survive both cancel points:
    the ``_is_instance_paused`` await and the ``_handle_cancel.on_cancel``
    second await.
    """

    @pytest.mark.asyncio
    async def test_detached_task_not_awaited_from_caller(
        self, engine: Engine
    ) -> None:
        """The dispatched coroutine runs independently of the
        pipeline coroutine — a cancellation of the pipeline does not
        cancel the dispatched marker write.

        We simulate this by scheduling the marker dispatch, then
        cancelling the current task. The marker should still land.
        """
        pipeline, manager, _ = _build_pipeline_with_repos(engine)
        instance_id = _seed_child_instance(
            engine, parent_id="parent-1"
        )

        # The detached schedule returns a Task; we deliberately drop
        # the ref (mirroring the production pipeline's pattern).
        pipeline._schedule_deferred_pause_marker(
            instance_id=instance_id,
            child_message_id="msg-1",
        )

        # Give the event loop a chance to run the dispatched task.
        # The pipeline coroutine is NOT awaited — the dispatched task
        # owns its lifecycle.
        await asyncio.sleep(0.05)

        with Session(engine) as session:
            rows = list(
                session.exec(
                    sm_select(ReportInjection).where(
                        ReportInjection.child_instance_id == instance_id
                    )
                ).all()
            )
        assert len(rows) == 1
        assert rows[0].state == ReportInjectionState.DEFERRED.value


# =============================================================================
# Pre-try hoist window (FM-11 plan-assigned escape lane)
# =============================================================================


class TestCancelDuringHoistedPauseCheckAwait:
    """Cancel lands DURING the hoisted ``_is_instance_paused`` await.

    The pause check ``was_paused = await self._is_instance_paused(...)``
    is hoisted BEFORE the try/finally (plan 1.4(a)). If the pause
    cascade's ``graph_task.cancel()`` fires while that await is in
    flight, ``CancelledError`` propagates out of ``execute()`` without
    the try body ever being entered — so the ``finally`` marker block
    never runs and no marker is written.

    This is the plan-assigned escape lane — cancel before try-entry is
    NOT marker-covered in Phase 1; the Phase 2 no-row backstop
    (task 2.4) is the designed net for this shape (FM-11). Test pins
    the CURRENT contract: clean cancel, no marker, no crash.
    """

    @pytest.mark.asyncio
    async def test_cancel_during_hoisted_pause_check_await_no_marker_but_clean_cancel(
        self, engine: Engine
    ) -> None:
        """Simulate the pause cascade's cancel landing mid-await, BEFORE
        try-entry: (a) ``CancelledError`` propagates out of
        ``execute()`` (clean cancel, no swallow); (b) no marker write
        is attempted — ``was_paused`` was never cached so the ``finally``
        block never runs; (c) nothing other than ``CancelledError``
        escapes."""
        pipeline, manager, _ = _build_pipeline_with_repos(engine)
        instance_id = _seed_child_instance(
            engine, parent_id="parent-1"
        )

        # The pause-check await raises CancelledError — the cascade's
        # graph_task.cancel() landing mid-``_is_instance_paused``.
        async def _pause_then_cancel(instance_id_arg: str) -> bool:
            raise asyncio.CancelledError()

        pipeline._is_instance_paused = _pause_then_cancel

        # Marker dispatch must NEVER be invoked on this path: spy on the
        # scheduler AND make any actual write loudly fail the test.
        marker_calls: list[dict] = []

        def _spy_schedule(**kwargs):  # noqa: ANN003
            marker_calls.append(kwargs)
            raise AssertionError(
                "_schedule_deferred_pause_marker must not run when the "
                "cancel lands during the hoisted pause-check await "
                "(pre-try entry, no finally)"
            )

        pipeline._schedule_deferred_pause_marker = _spy_schedule

        # The gate stages before the pause check must still run; the
        # AsyncMock makes ``await gate.run(...)`` awaitable and returns
        # a MessageResult-shaped object.
        pipeline._execution_gate = MagicMock()
        pipeline._execution_gate.run = AsyncMock(
            return_value=MagicMock(content="done")
        )

        escaped: BaseException | None = None
        try:
            await pipeline.execute(
                context=_context_for(instance_id),
                holder_id="test:hoist-window",
                holder_kind="task",
                callbacks=_no_callbacks(),
            )
        except BaseException as e:  # noqa: BLE001 — capture, then assert
            escaped = e

        # (a) Clean cancel: CancelledError propagates out of execute().
        assert isinstance(escaped, asyncio.CancelledError), (
            f"expected asyncio.CancelledError to propagate out of "
            f"pipeline.execute(), got {escaped!r}"
        )
        # (c) Nothing other than CancelledError escapes: the spy would
        # have raised AssertionError through the finally had it run, and
        # any other exception type is captured in ``escaped`` above.
        assert type(escaped) is asyncio.CancelledError

        # (b) No marker: the spy was never invoked AND no row landed.
        assert marker_calls == []
        with Session(engine) as session:
            rows = list(
                session.exec(sm_select(ReportInjection)).all()
            )
        assert rows == []


# =============================================================================
# TestStage6FinallyMarkerHook
# =============================================================================


class TestStage6FinallyMarkerHook:
    """The pipeline's ``finally`` block must schedule the marker write
    when the cached ``was_paused`` is True. We test the helper directly
    (the full pipeline execute() path is covered by integration tests
    elsewhere).
    """

    @pytest.mark.asyncio
    async def test_schedule_helper_invokes_repo(
        self, engine: Engine
    ) -> None:
        pipeline, manager, _ = _build_pipeline_with_repos(engine)
        instance_id = _seed_child_instance(
            engine, parent_id="parent-x"
        )

        pipeline._schedule_deferred_pause_marker(
            instance_id=instance_id,
            child_message_id="m-1",
        )

        # Give the event loop time to run the dispatched task.
        await asyncio.sleep(0.05)

        with Session(engine) as session:
            row = session.exec(
                sm_select(ReportInjection).where(
                    ReportInjection.child_instance_id == instance_id
                )
            ).first()
        assert row is not None
        assert row.state == ReportInjectionState.DEFERRED.value
        assert row.parent_instance_id == "parent-x"
