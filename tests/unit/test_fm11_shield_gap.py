"""FM-11 shield-gap tests: 3.9(a) cancel-at-await + 3.9(c) W12
crash-mid-shield fixture (pause-report-recovery Phase 3, task 3.9).

Plan anchor: ``.agents/shared/planning/pause-report-recovery/phase3-plan.md``
line 39 (task 3.9). This file is the plan-named home for the FM-11
shield-gap detection tests that decide the **Option B** question
(leader digest item 4) and pin the **W12 crash-mid-shield** contract.

Coverage audit context (2026-08-20, branch feature/pause-report-recovery
@ 73bfe0ed) — what ALREADY existed vs what this file adds:

* 3.9(a) PRE-TRY HOIST WINDOW — already covered by
  ``TestCancelDuringHoistedPauseCheckAwait::
  test_cancel_during_hoisted_pause_check_await_no_marker_but_clean_cancel``
  (tests/unit/test_report_deferred_marker_pipeline.py:428, commit
  4167d6b1). That test pins the contract for a cancel landing DURING
  the hoisted ``was_paused = await _is_instance_paused(...)`` await
  (before try-entry): CancelledError escapes, NO marker, Phase 2
  no-row backstop is the net. OVERLAP NOTE (cited per plan): this
  file's ``test_real_cancel_at_pause_check_await_escape_lane_no_marker``
  re-covers the SAME window with a REAL ``task.cancel()`` delivered
  at a controllable-future suspension point (the existing test
  raises CancelledError from inside the stub — a simulated delivery,
  not a real one). The two are complementary: same window, stronger
  cancellation semantics here.

* 3.9(a) POST-TRY-ENTRY SHAPE (the Option A proof) — NOT covered
  before this file. The existing suite never cancels the pipeline
  AFTER ``was_paused=True`` is cached (inside the try body).
  ``test_cancel_after_pause_cached_marker_scheduled_before_escape``
  + ``test_cancel_at_on_cancel_await_marker_survives`` cover both
  cancel points with the deterministic protocol.

* 3.9(b) LOST-WRITE BACKSTOP — already covered in
  tests/unit/test_report_delivery_recovery_service.py:

  - ``TestNoRowBackstopFalsePositiveMatrix`` (C3 matrix:
    ``test_excludes_when_existing_completion_report_message``,
    ``test_excludes_when_existing_injection_row``,
    ``test_excludes_when_parent_terminal``,
    ``test_diagnostic_includes_terminal_parents``)
  - ``TestNoRowBackstopFalsePositiveMatrix::
    test_no_row_lane_end_state_not_deferred`` (D2 end-state)
  - ``TestPendingAgeLanes::test_pending_row_recovered`` (retry lane)

  Only the ONE-CYCLE pin (``test_lost_write_shape_backstop_recovers_
  within_one_cycle`` below) is added here — a cross-reference, not a
  duplicate of the matrix.

* 3.9(c) W12 CRASH-MID-SHIELD — NOT covered anywhere before this
  file (grep for ConnectionError / post-commit across the phase-3
  unit files returns nothing). The W12 fixture wraps the repository
  so ``ensure_deferred``'s COMMIT SUCCEEDS (marker durable) and the
  step AFTER the commit raises ``ConnectionError`` — the "connection
  dropped right after COMMIT was acked" crash shape.

Test-infrastructure notes (deterministic, NO wall-clock sleeps):

* Every suspension point under test is a controllable
  ``loop.create_future()``; synchronization is ``asyncio.Event`` set
  by the coroutine under test immediately BEFORE its suspension.
* Cancellation is a REAL ``task.cancel()`` delivered while the
  pipeline coroutine is parked at the synchronized suspension point.
* Swallowing a CancelledError consumes the cancellation (asyncio
  semantics) — the escape-lane test therefore lets the error
  propagate (production has no try/except around the hoisted pause
  check), and the post-try-entry tests deliver the cancel at the
  Stage-4 suspension instead.
* Python except/finally ordering (verified against the pipeline's
  control flow): when an exception is raised in the try body, the
  MATCHING EXCEPT ARM runs to completion FIRST (including
  ``_handle_cancel``'s ``await on_cancel(exc)`` — the second cancel
  point), and the ``finally`` block runs before the exception ESCAPES
  ``execute()`` to the caller. The FM-11 contract this file pins is
  "marker dispatch SCHEDULED in finally BEFORE the CancelledError
  escapes to the caller" — pinned via an order-spy flag asserted
  immediately after ``await task`` raises.
* SHARED-CONNECTION DISCIPLINE: the in-memory SQLite engine uses
  ``StaticPool`` (ONE connection shared by every Session — required
  so the detached ``asyncio.to_thread`` write and the test's reads
  see the same DB). A Session opened by the test WHILE the detached
  thread's INSERT transaction is open would, on close, roll back the
  shared connection's transaction and silently destroy the write.
  The tests therefore NEVER poll the DB while the detached write may
  be in flight: each repo wrapper sets a ``threading.Event`` AFTER
  the real write method returns (commit done), the test blocks on
  that event via ``asyncio.to_thread`` (no DB access), drives a few
  ``sleep(0)`` settle turns for the coroutine tail, and only then
  performs ONE read.
"""

from __future__ import annotations

import asyncio
import threading
import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, select as sm_select

# Register every table the write paths touch before create_all().
import daemon.repositories.dependency_bus.models  # noqa: F401
import daemon.repositories.event.models  # noqa: F401
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.report_injection.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401

from daemon.constants import (
    DEFERRED_REASON_PAUSE_TOCTOU,
    DEFERRED_REASON_RESUME_ROUTER,
)
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.message_queue.models import (
    MessageQueue,
    MessageStatus,
    MessageType,
)
from daemon.repositories.report_injection.models import (
    ReportInjection,
    ReportInjectionState,
)
from daemon.services.message_processing_pipeline import (
    MessageProcessingPipeline,
    ProcessingContext,
    PipelineCallbacks,
)
from daemon.services.report_delivery_recovery import (
    ReportDeliveryRecoveryService,
    SweepResult,
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

    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


class _SimpleInstRepo:
    """Minimal instance repository reading from the shared engine."""

    def __init__(self, eng: Engine) -> None:
        self.engine = eng

    def get(self, instance_id: str) -> Instance | None:
        with Session(self.engine) as session:
            return session.get(Instance, instance_id)


class _SignallingRepo:
    """Repository wrapper that signals when the marker write is DONE.

    Sets a ``threading.Event`` AFTER the real ``ensure_deferred``
    returns (i.e. after its session committed and closed) so the test
    can deterministically wait for write-thread completion WITHOUT
    opening a Session on the shared StaticPool connection while the
    write transaction may be open (see module docstring — a
    concurrent Session close would roll back the shared
    connection's transaction).
    """

    def __init__(self, real_repo: Any, real_inst_repo: Any) -> None:
        self._real = real_repo
        self._real_inst = real_inst_repo
        self.write_done = threading.Event()
        self.ensure_deferred_calls: list[dict] = []

    def get(self, instance_id: str) -> Instance | None:
        return self._real_inst.get(instance_id)

    def ensure_deferred(self, **kwargs: Any) -> ReportInjection | None:
        self.ensure_deferred_calls.append(kwargs)
        row = self._real.ensure_deferred(**kwargs)
        self.write_done.set()  # commit is durable at this point
        return row


class _CrashAfterCommitRepo(_SignallingRepo):
    """W12 fixture: COMMIT SUCCEEDS, the post-commit step raises
    ``ConnectionError``.

    The W12 crash shape: the dispatched coroutine's session ``commit()``
    SUCCEEDS (the marker row is durable in the DB), and THEN the
    post-commit step raises ``ConnectionError`` — the "connection
    dropped right after COMMIT was acked" form of crash-mid-shield
    (the crashing coroutine never observes its own write, but every
    other observer does).
    """

    def ensure_deferred(self, **kwargs: Any) -> ReportInjection | None:
        self.ensure_deferred_calls.append(kwargs)
        # The COMMIT succeeds — the marker row is durable.
        row = self._real.ensure_deferred(**kwargs)
        self.write_done.set()  # commit is durable at this point
        # ... and the post-commit step hits a dropped connection.
        raise ConnectionError(
            "simulated connection drop AFTER successful COMMIT "
            "(W12 crash-mid-shield)"
        )


def _seed_child_instance(
    engine: Engine,
    *,
    instance_id: str | None = None,
    parent_id: str | None = "parent-1",
    status: str = InstanceStatus.PAUSED.value,
) -> str:
    """Insert a child instance row with the given parent_id."""
    instance_id = instance_id or f"fm11-{uuid.uuid4().hex[:8]}"
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


def _seed_parent_instance(
    engine: Engine,
    *,
    instance_id: str = "parent-1",
    status: str = InstanceStatus.RUNNING.value,
) -> str:
    """Insert a parent (root) instance row."""
    with Session(engine) as session:
        session.add(
            Instance(
                instance_id=instance_id,
                agent_id="parent-agent",
                agent_name="parent",
                agent_dir="/tmp/parent",
                parent_id=None,
                status=status,
                version=1,
                instance_metadata={},
            )
        )
        session.commit()
    return instance_id


def _seed_child_message(
    engine: Engine,
    *,
    instance_id: str,
    message_id: str,
) -> None:
    """Insert the child's COMPLETED message (Stage-6 artifact)."""
    with Session(engine) as session:
        session.add(
            MessageQueue(
                message_id=message_id,
                instance_id=instance_id,
                content="child report content",
                source="external:test",
                type=MessageType.HUMAN.value,
                status=MessageStatus.COMPLETED.value,
                priority=0,
                enqueued_at=datetime.now(timezone.utc),
            )
        )
        session.commit()


def _build_pipeline_with_repos(
    engine: Engine,
    *,
    crash_after_commit: bool = False,
) -> tuple[MessageProcessingPipeline, MagicMock, _SignallingRepo]:
    """Build a MessageProcessingPipeline with real repo wiring.

    Mirrors the fixture in test_report_deferred_marker_pipeline.py,
    with two additions:

    * the execution gate is an ``AsyncMock`` returning a
      ``MessageResult``-shaped object (Stage 2's ``await gate.run()``
      must be awaitable — it runs BEFORE the pause check);
    * the report-injection repo is wrapped in a signalling repo
      (``_SignallingRepo`` or, with ``crash_after_commit=True``, the
      W12 ``_CrashAfterCommitRepo`` fixture) so the tests can wait
      deterministically for the detached write to complete.

    Returns ``(pipeline, manager, signalling_repo)``.
    """
    from daemon.repositories.report_injection.repository import (
        ReportInjectionRepository,
    )

    pipeline = MessageProcessingPipeline.__new__(MessageProcessingPipeline)
    manager = MagicMock(spec=[])
    manager.engine = engine
    real_repo = ReportInjectionRepository(engine)
    inst_repo = _SimpleInstRepo(engine)
    repo_cls = _CrashAfterCommitRepo if crash_after_commit else _SignallingRepo
    sig_repo = repo_cls(real_repo=real_repo, real_inst_repo=inst_repo)
    manager._report_injection_repo = sig_repo
    manager._instance_repository = inst_repo

    pipeline._manager = manager
    pipeline._execution_gate = MagicMock()
    pipeline._execution_gate.run = AsyncMock(
        return_value=MagicMock(content="done")
    )
    pipeline._source_dispatcher = None
    pipeline._queue_repository = None
    return pipeline, manager, sig_repo


def _context_for(instance_id: str, message_id: str = "msg-1") -> ProcessingContext:
    """Build a minimal ProcessingContext."""
    return ProcessingContext(
        instance_id=instance_id,
        message_id=message_id,
        message="hello",
    )


def _callbacks(
    on_cancel: Any = None,
) -> PipelineCallbacks:
    """Build PipelineCallbacks with an optional on_cancel."""
    return PipelineCallbacks(
        on_success=None,
        on_error=None,
        on_contention=None,
        on_cancel=on_cancel,
    )


def _fetch_markers(engine: Engine, instance_id: str) -> list[ReportInjection]:
    """Fetch all ReportInjection rows for the child instance."""
    with Session(engine) as session:
        return list(
            session.exec(
                sm_select(ReportInjection).where(
                    ReportInjection.child_instance_id == instance_id
                )
            ).all()
        )


async def _await_marker_write(sig_repo: _SignallingRepo) -> None:
    """Deterministically wait for the detached marker write to be
    committed: block on the repo wrapper's ``threading.Event`` (via
    ``asyncio.to_thread`` — no DB access), then drive a few
    ``sleep(0)`` settle turns so the detached coroutine's tail (the
    ``to_thread`` future resolution + self-contained except/logging)
    finishes BEFORE any test-side Session reads.

    NO DB polling: see module docstring (shared-connection
    discipline). NO wall-clock sleeps: the event + ``sleep(0)``
    turns are fully deterministic.
    """
    await asyncio.to_thread(sig_repo.write_done.wait)
    for _ in range(5):
        await asyncio.sleep(0)


# =============================================================================
# 3.9(a) — cancel lands AT the pause-check await (pre-try escape lane)
# =============================================================================


class TestRealCancelAtPauseCheckAwaitEscapeLane:
    """3.9(a) — REAL ``task.cancel()`` delivered while the pipeline is
    parked on the hoisted ``await self._is_instance_paused(...)``.

    OVERLAP (cited): the same window is pinned by
    ``test_cancel_during_hoisted_pause_check_await_no_marker_but_clean_cancel``
    (tests/unit/test_report_deferred_marker_pipeline.py:428, commit
    4167d6b1) using a stub that RAISES CancelledError from inside the
    pause check. This class delivers a REAL cancel signal at a
    controllable-future suspension — exercising the task-level
    cancellation machinery (``Task.cancel`` → delivery at the await →
    propagation) rather than simulating the exception.

    Expected contract (pinned by 4167d6b1 as the plan-assigned escape
    lane): the CancelledError propagates RAW out of ``execute()``
    (the await sits BEFORE try-entry at pipeline.py:447, so neither
    the except arm nor the finally marker block runs), no marker is
    scheduled, and the Phase 2 no-row backstop is the designed net.

    This is the Option B decision input for the hoist window: the
    Option A shield does NOT (by design) cover a cancel that lands
    before try-entry.
    """

    @pytest.mark.asyncio
    async def test_real_cancel_at_pause_check_await_escape_lane_no_marker(
        self, engine: Engine
    ) -> None:
        pipeline, manager, sig_repo = _build_pipeline_with_repos(engine)
        instance_id = _seed_child_instance(engine, parent_id="parent-1")

        pause_entered = asyncio.Event()
        pause_gate = asyncio.get_running_loop().create_future()

        async def _parked_pause_check(instance_id_arg: str) -> bool:
            pause_entered.set()
            await pause_gate  # deterministic suspension point
            return True

        pipeline._is_instance_paused = _parked_pause_check  # type: ignore[method-assign]

        schedule_calls: list[dict] = []

        def _spy_schedule(**kwargs: Any) -> None:
            schedule_calls.append(kwargs)
            raise AssertionError(
                "_schedule_deferred_pause_marker must not run when the "
                "cancel lands during the hoisted pause-check await "
                "(pre-try entry — no finally)"
            )

        pipeline._schedule_deferred_pause_marker = _spy_schedule  # type: ignore[method-assign]

        on_cancel_entered = asyncio.Event()

        async def _on_cancel(exc: BaseException) -> None:
            on_cancel_entered.set()
            return None

        pipeline_task = asyncio.create_task(
            pipeline.execute(
                context=_context_for(instance_id),
                holder_id="test:fm11-escape-lane",
                holder_kind="task",
                callbacks=_callbacks(on_cancel=_on_cancel),
            ),
            name="fm11-escape-lane",
        )

        # Wait until the pipeline is parked INSIDE the pause check.
        await pause_entered.wait()
        # REAL cancel — the pause cascade's graph_task.cancel().
        pipeline_task.cancel()

        escaped: BaseException | None = None
        try:
            await pipeline_task
        except asyncio.CancelledError:
            escaped = asyncio.CancelledError()
        except BaseException as e:  # noqa: BLE001 — capture, then assert
            escaped = e

        # (1) Clean cancel: CancelledError propagates out of execute().
        assert isinstance(escaped, asyncio.CancelledError), (
            f"expected asyncio.CancelledError out of pipeline.execute(), "
            f"got {escaped!r}"
        )
        # NOT routed through _handle_cancel — the await is pre-try.
        assert not on_cancel_entered.is_set(), (
            "the hoisted pause-check await sits BEFORE try-entry; the "
            "CancelledError must propagate raw, not through "
            "_handle_cancel"
        )
        # (2) No marker scheduled (spy would have raised) and no row.
        assert schedule_calls == []
        # Settle the loop (a few yield turns — no DB polling).
        for _ in range(5):
            await asyncio.sleep(0)
        rows = _fetch_markers(engine, instance_id)
        assert rows == [], (
            "escape lane: no marker row may exist when the cancel lands "
            "pre-try (Phase 2 no-row backstop is the designed net)"
        )


# =============================================================================
# 3.9(a) — cancel lands AFTER was_paused cached (post-try-entry, Option A)
# =============================================================================


class TestCancelAfterPauseCachedMarkerFirst:
    """3.9(a) — the FM-11 Option A proof: the pause check RESOLVED
    ``was_paused=True`` (cached before try-entry — the hoist-window
    contract from commit 4167d6b1), and the pause cascade's cancel
    lands at the NEXT suspension point — Stage 4 inside the try body.

    Deterministic protocol: the pause check parks on a controllable
    future which the test RESOLVES (so the pipeline caches True and
    enters the try), then Stage 4 (``_mark_message_completed``) parks
    on a second controllable future; the test delivers a REAL
    ``task.cancel()`` at that synchronized point.

    Expected shape (Option A pattern; f21c59c9 pinned the Python 3.14
    shield form):

    1. CancelledError raised at the Stage-4 await;
    2. ``except asyncio.CancelledError`` arm routes through
       ``_handle_cancel`` (on_cancel entered — proof of the routing);
    3. the ``finally`` block runs BEFORE the exception escapes
       ``execute()`` and schedules the detached-shield marker
       dispatch (spy flag observable immediately after the task
       await raises);
    4. the detached task's DB write lands while the loop is alive.

    If (3) or (4) fails, the Option A shield is insufficient for this
    window — the pre-approved Option B fallback trigger.
    """

    @pytest.mark.asyncio
    async def test_cancel_after_pause_cached_marker_scheduled_before_escape(
        self, engine: Engine
    ) -> None:
        pipeline, manager, sig_repo = _build_pipeline_with_repos(engine)
        instance_id = _seed_child_instance(engine, parent_id="parent-1")

        loop = asyncio.get_running_loop()
        pause_entered = asyncio.Event()
        pause_gate = loop.create_future()
        stage4_entered = asyncio.Event()
        stage4_gate = loop.create_future()

        async def _pausable_pause_check(instance_id_arg: str) -> bool:
            pause_entered.set()
            await pause_gate  # suspension 1: the Stage-6 pause check
            return True  # PAUSED committed — was_paused cached True

        pipeline._is_instance_paused = _pausable_pause_check  # type: ignore[method-assign]

        async def _pausable_stage4(message_id: str | None) -> None:
            stage4_entered.set()
            await stage4_gate  # suspension 2: INSIDE the try body

        pipeline._mark_message_completed = _pausable_stage4  # type: ignore[method-assign]

        schedule_order: list[str] = []
        real_schedule = pipeline._schedule_deferred_pause_marker

        def _sched_spy(*, instance_id: str, child_message_id: str | None) -> None:
            schedule_order.append("schedule")
            real_schedule(
                instance_id=instance_id, child_message_id=child_message_id
            )

        pipeline._schedule_deferred_pause_marker = _sched_spy  # type: ignore[method-assign]

        on_cancel_order: list[str] = []

        async def _on_cancel(exc: BaseException) -> None:
            on_cancel_order.append("on_cancel")
            return None  # None → _handle_cancel re-raises the original

        pipeline_task = asyncio.create_task(
            pipeline.execute(
                context=_context_for(instance_id),
                holder_id="test:fm11-marker-first",
                holder_kind="task",
                callbacks=_callbacks(on_cancel=_on_cancel),
            ),
            name="fm11-marker-first",
        )

        # Step 1: pipeline parks inside the pause check; resolve it so
        # was_paused=True is cached and the try body is entered.
        await pause_entered.wait()
        pause_gate.set_result(True)
        # Step 2: pipeline parks INSIDE the try body (Stage 4).
        await stage4_entered.wait()
        # Step 3: REAL cancel at the synchronized suspension point.
        pipeline_task.cancel()

        escaped: BaseException | None = None
        try:
            await pipeline_task
        except asyncio.CancelledError:
            escaped = asyncio.CancelledError()
        except BaseException as e:  # noqa: BLE001 — capture, then assert
            escaped = e

        # (1) CancelledError exits the pipeline properly.
        assert isinstance(escaped, asyncio.CancelledError), (
            f"expected asyncio.CancelledError out of pipeline.execute(), "
            f"got {escaped!r}"
        )
        # (1b) It was routed through _handle_cancel (the except arm),
        # not a raw propagate.
        assert on_cancel_order == ["on_cancel"], (
            "the post-try-entry cancel MUST route through _handle_cancel"
        )
        # (2) The marker dispatch was scheduled in the finally BEFORE
        # the exception escaped to the caller: the spy flag is
        # observable IMMEDIATELY after ``await pipeline_task`` raised
        # — before any further loop turns are driven.
        assert schedule_order == ["schedule"], (
            "FM-11 Option A contract violated: the finally-block "
            "detached-shield marker dispatch was not scheduled before "
            "the CancelledError escaped execute() (Option B trigger); "
            f"observed order: cancels={on_cancel_order}, "
            f"schedules={schedule_order}"
        )
        # Python except/finally ordering pin: the except arm
        # (including _handle_cancel's await) completes BEFORE the
        # finally block runs; the FM-11 contract is schedule-before-
        # ESCAPE, which the assertion above proves.
        assert on_cancel_order + schedule_order == [
            "on_cancel",
            "schedule",
        ]

        # (3) The detached write lands while the loop is alive.
        await _await_marker_write(sig_repo)
        rows = _fetch_markers(engine, instance_id)
        assert len(rows) == 1, (
            "3.9(a) FM-11 contract violated: the detached-shield "
            "DEFERRED marker was NOT persisted after the post-try-entry "
            "cancel — the parent's delivery obligation was lost "
            "(Option B trigger)"
        )
        row = rows[0]
        assert row.state == ReportInjectionState.DEFERRED.value
        assert row.parent_instance_id == "parent-1"
        assert row.child_instance_id == instance_id
        assert row.child_message_id == "msg-1"
        assert row.deferred_reason == DEFERRED_REASON_PAUSE_TOCTOU
        assert row.report_message_id is None
        assert row.content is None


# =============================================================================
# 3.9(a) — second cancel point: _handle_cancel's on_cancel await
# =============================================================================


class TestCancelAtHandleCancelSecondAwait:
    """3.9(a) — the SECOND cancel point: ``_handle_cancel``'s
    ``await callbacks.on_cancel(exc)`` (pipeline.py:1153).

    The first cancel lands at the Stage-4 suspension; the except arm
    routes into ``_handle_cancel`` and suspends on ``on_cancel``; the
    test cancels AGAIN at that synchronized point. The detached
    marker dispatch scheduled by the finally must survive BOTH cancel
    points and the pipeline must still exit with CancelledError.
    """

    @pytest.mark.asyncio
    async def test_cancel_at_on_cancel_await_marker_survives(
        self, engine: Engine
    ) -> None:
        pipeline, manager, sig_repo = _build_pipeline_with_repos(engine)
        instance_id = _seed_child_instance(engine, parent_id="parent-1")

        async def _paused(instance_id_arg: str) -> bool:
            return True

        pipeline._is_instance_paused = _paused  # type: ignore[method-assign]

        stage4_entered = asyncio.Event()
        stage4_gate = asyncio.get_running_loop().create_future()

        async def _pausable_stage4(message_id: str | None) -> None:
            stage4_entered.set()
            await stage4_gate

        pipeline._mark_message_completed = _pausable_stage4  # type: ignore[method-assign]

        on_cancel_entered = asyncio.Event()

        async def _on_cancel(exc: BaseException) -> None:
            on_cancel_entered.set()
            await asyncio.get_running_loop().create_future()  # park

        pipeline_task = asyncio.create_task(
            pipeline.execute(
                context=_context_for(instance_id),
                holder_id="test:fm11-second-cancel",
                holder_kind="task",
                callbacks=_callbacks(on_cancel=_on_cancel),
            ),
            name="fm11-second-cancel",
        )

        # First cancel point: Stage 4 (inside try).
        await stage4_entered.wait()
        pipeline_task.cancel()
        # Wait until _handle_cancel is parked on on_cancel.
        await on_cancel_entered.wait()
        # SECOND cancel point: the documented re-delivery window.
        pipeline_task.cancel()

        escaped: BaseException | None = None
        try:
            await pipeline_task
        except asyncio.CancelledError:
            escaped = asyncio.CancelledError()
        except BaseException as e:  # noqa: BLE001
            escaped = e

        assert isinstance(escaped, asyncio.CancelledError), (
            f"second cancel point must still exit via CancelledError; "
            f"got {escaped!r}"
        )

        await _await_marker_write(sig_repo)
        rows = _fetch_markers(engine, instance_id)
        assert len(rows) == 1, (
            "the detached-shield marker must survive BOTH cancel points "
            "(Stage-4 await + _handle_cancel's on_cancel await)"
        )
        assert rows[0].state == ReportInjectionState.DEFERRED.value
        assert rows[0].deferred_reason == DEFERRED_REASON_PAUSE_TOCTOU


# =============================================================================
# 3.9(c) — W12 CRASH-MID-SHIELD FIXTURE
# =============================================================================


class TestW12CrashMidShield:
    """3.9(c): the detached coroutine's session commits the marker,
    then a ``ConnectionError`` is raised AFTER the commit.

    W12 (leader digest item 4) asserts:

    (i)   the detached task's error path does not corrupt state —
          the marker row IS present and intact in the DB;
    (ii)  no partial/orphan artifacts — no PENDING task row, no
          stray message rows beyond the seeded child message;
    (iii) the pipeline state is unaffected — the pipeline returns
          its normal happy-path result (the crash happened inside
          the DETACHED coroutine whose errors are self-contained);
    (iv)  the backstop lane does not double-recover — one sweep
          cycle recovers the durable marker exactly once via the
          DEFERRED lane, and the no-row lane stays at zero (Option
          A+C defense-in-depth: the marker net + the sweep net).
    """

    @pytest.mark.asyncio
    async def test_connection_error_after_commit_marker_intact(
        self, engine: Engine
    ) -> None:
        """The W12 fixture: commit() succeeds, post-commit raises
        ConnectionError. Marker present + intact; no orphan
        artifacts; pipeline unaffected; sweep recovers within one
        cycle via the DEFERRED lane (never double-delivers)."""
        pipeline, manager, crash_repo = _build_pipeline_with_repos(
            engine, crash_after_commit=True
        )
        instance_id = _seed_child_instance(engine, parent_id="parent-1")
        _seed_child_message(engine, instance_id=instance_id, message_id="msg-1")

        # Run the full pipeline happy path (no cancel): the finally
        # block schedules the detached marker dispatch; the dispatch
        # commits the marker and THEN crashes with ConnectionError.
        result = await pipeline.execute(
            context=_context_for(instance_id),
            holder_id="test:w12-crash",
            holder_kind="task",
            callbacks=_callbacks(),
        )

        # Deterministically wait for the detached coroutine: commit
        # done + post-commit ConnectionError raised + self-contained
        # warn-log. (No DB polling — see module docstring.)
        await _await_marker_write(crash_repo)
        # A few extra settle turns for the coroutine's except arm.
        for _ in range(5):
            await asyncio.sleep(0)

        # The dispatched coroutine WAS invoked (the crash fixture
        # actually ran — otherwise this test proves nothing).
        assert len(crash_repo.ensure_deferred_calls) == 1, (
            "the W12 fixture must exercise the real ensure_deferred "
            "commit path exactly once"
        )

        # (iii) Pipeline state unaffected: the happy-path result is
        # returned; the crash inside the detached coroutine never
        # leaked into the pipeline's own control flow.
        assert result.success is True, (
            f"pipeline must complete normally despite the W12 crash "
            f"inside the detached marker coroutine; got {result!r}"
        )

        # (i) The marker row is present and intact in the DB — the
        # COMMIT was durable and the post-commit ConnectionError did
        # NOT corrupt it (committed rows survive a subsequent
        # connection error).
        rows = _fetch_markers(engine, instance_id)
        assert len(rows) == 1, (
            "W12(i): the marker row MUST be present after the "
            "post-commit ConnectionError — a missing row means the "
            "crash corrupted the write (rollback-after-commit shape)"
        )
        row = rows[0]
        assert row.state == ReportInjectionState.DEFERRED.value
        assert row.parent_instance_id == "parent-1"
        assert row.child_instance_id == instance_id
        assert row.child_message_id == "msg-1"
        assert row.deferred_reason == DEFERRED_REASON_PAUSE_TOCTOU
        assert row.report_message_id is None
        assert row.content is None

        # (ii) No partial/orphan artifacts: exactly one message row
        # (the seeded child message) and NO PENDING task rows.
        from daemon.repositories.task.models import Task

        with Session(engine) as session:
            msgs = list(session.exec(sm_select(MessageQueue)).all())
            tasks = list(session.exec(sm_select(Task)).all())
        assert len(msgs) == 1, (
            f"W12(ii): no stray message rows expected; found "
            f"{[(m.message_id, m.status) for m in msgs]}"
        )
        assert msgs[0].message_id == "msg-1"
        pending_tasks = [t for t in tasks if t.status == "pending"]
        assert pending_tasks == [], (
            "W12(ii): no orphan PENDING task rows may be created by "
            "the crash shape"
        )

        # (iv) Backstop + DEFERRED lane defense-in-depth, one cycle:
        # seed the parent (non-terminal) so the DEFERRED lane can
        # claim the crashed-but-durable marker, then run ONE
        # recover_now() sweep. The marker MUST be recovered exactly
        # once — via the DEFERRED lane — and the no-row backstop
        # lane MUST NOT create a duplicate obligation (the marker's
        # existence excludes the triple from the no-row query).
        _seed_parent_instance(engine, instance_id="parent-1")

        from daemon.repositories.report_injection.repository import (
            ReportInjectionRepository,
        )

        ri_repo = ReportInjectionRepository(engine=engine)
        task_repo = MagicMock()
        task_repo.has_instance_busy = MagicMock(return_value=False)
        sweep_manager = MagicMock()
        sweep_manager._handle_recover_deferred_report = MagicMock()
        service = ReportDeliveryRecoveryService(
            task_repo=task_repo,
            report_injection_repo=ri_repo,
            queue_repo=MagicMock(),
            instance_repo=MagicMock(),
            manager_ref=sweep_manager,
            interval_seconds=300,
            age_bound_minutes=10,
            batch_cap=100,
            recovery_retry_minutes=1,
            enabled=True,
            lane_orphan=False,
        )
        sweep_result = service.recover_now()
        assert isinstance(sweep_result, SweepResult)
        deferred_lane = sweep_result.lanes["deferred"]
        assert deferred_lane.recovered == 1, (
            "W12(iv): the durable marker must be recovered by the "
            f"DEFERRED lane within one cycle; lanes={sweep_result.lanes}"
        )
        sweep_manager._handle_recover_deferred_report.assert_called_once()
        # Defense-in-depth: the no-row backstop lane did NOT see the
        # crashed triple (the marker's existence excludes it).
        no_row = sweep_result.lanes["no_row_backstop"]
        assert no_row.recovered == 0, (
            "W12(iv): the no-row backstop lane must not duplicate-"
            "recover a triple that already has a durable marker "
            f"(defense-in-depth violated); lanes={sweep_result.lanes}"
        )
        # And still exactly ONE marker row for the child — no
        # duplicate obligation was written by any lane.
        assert len(_fetch_markers(engine, instance_id)) == 1, (
            "W12(iv): exactly one obligation row must exist after the "
            "sweep (no duplicate markers)"
        )

    @pytest.mark.asyncio
    async def test_lost_write_shape_backstop_recovers_within_one_cycle(
        self, engine: Engine
    ) -> None:
        """3.9(b)/(c) — the OTHER crash half: the marker did NOT land
        (lost write — crash BEFORE commit). A completed child with no
        marker, no queued report, and no FIRED watcher is recovered by
        the no-row backstop lane within ONE ``recover_now()`` cycle:
        marker written (RESUME_ROUTER), transitioned to PENDING (D2
        end-state), hand-off invoked.

        Cross-reference (overlap, deliberately not duplicated here):
        the full C3 false-positive matrix and the D2 end-state
        assertion live in
        ``TestNoRowBackstopFalsePositiveMatrix`` and
        ``test_no_row_lane_end_state_not_deferred``
        (tests/unit/test_report_delivery_recovery_service.py). This
        test adds the one-cycle pin the plan assigns to 3.9(b).
        """
        parent = _seed_parent_instance(engine, instance_id="parent-lost-1")
        child_id = _seed_child_instance(
            engine,
            parent_id=parent,
            status=InstanceStatus.COMPLETED.value,
        )
        _seed_child_message(
            engine, instance_id=child_id, message_id="msg-lost-1"
        )

        from daemon.repositories.report_injection.repository import (
            ReportInjectionRepository,
        )

        ri_repo = ReportInjectionRepository(engine=engine)
        task_repo = MagicMock()
        task_repo.has_instance_busy = MagicMock(return_value=False)
        sweep_manager = MagicMock()
        sweep_manager._handle_recover_deferred_report = MagicMock()
        service = ReportDeliveryRecoveryService(
            task_repo=task_repo,
            report_injection_repo=ri_repo,
            queue_repo=MagicMock(),
            instance_repo=MagicMock(),
            manager_ref=sweep_manager,
            interval_seconds=300,
            age_bound_minutes=10,
            batch_cap=100,
            recovery_retry_minutes=1,
            enabled=True,
            lane_orphan=False,
        )
        sweep_result = service.recover_now()

        no_row = sweep_result.lanes["no_row_backstop"]
        assert no_row.recovered == 1, (
            "3.9(b): the lost-write shape (no marker) MUST be "
            "recovered by the no-row backstop lane within one cycle; "
            f"lanes={sweep_result.lanes}"
        )
        sweep_manager._handle_recover_deferred_report.assert_called_once()
        rows = _fetch_markers(engine, child_id)
        assert len(rows) == 1, (
            "3.9(b): the backstop must write the obligation marker"
        )
        assert rows[0].deferred_reason == DEFERRED_REASON_RESUME_ROUTER
        assert rows[0].state == ReportInjectionState.PENDING.value, (
            "D2 end-state: the fresh marker ends the cycle PENDING, "
            "never half-recovered DEFERRED"
        )
        assert rows[0].recovery_attempted_at is not None
