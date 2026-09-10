"""Reproducing tests for the 3-defect answer-gate resume chain
(2026-09-10, instance ca14e233-b270-4d3d-9fbb-31ebe857bbfc).

Production failure chain (verified from daemon logs; the original
defect chain):

    Defect 1 — GATE STEAL (15:11:06): an unrelated plain user message
              hit ``POST /resume`` and was routed
              ``answer_gate_existing_turn`` — consuming the
              ``awaiting_answer`` handle as if it were the answer.
              Suspected site: ``daemon/routers/instances.py``
              ``resume_instance``.

    Defect 2 — HANDLE ORPHANING (15:11:07.128): the resumed task
              (task 32779) was completed by the PROCESS_REPORT
              skip-path BEFORE the replacement graph turn ran; the
              new ask_questions pause had no RUNNING task to
              suspend → no fresh handle. Two suspect sites:

                (a) ``daemon/manager.py`` ``_schedule_explicit_handle_resume``
                    cleanup cancelling the PAUSED awaiting_answer
                    handle task.
                (b) ``daemon/services/task_processor.py``
                    ``_skip_task_as_completed`` clearing a fresh
                    ``awaiting_answer`` handle re-set by the cascade
                    pause.

    Defect 3 — SILENT ANSWER DROP (15:12:10): the real answer hit
              ``POST /api/instances/{id}/answer`` →
              ``resume_processing_job`` returned ``None`` →
              router logged WARNING and returned HTTP 200 with no
              payload delivered and no event emitted. Suspected site:
              ``daemon/routers/instances.py`` ``answer_questions``
              None-fallback.

The fixes in this branch:

  * Fix 1: ``resume_instance`` detects a pending question pack and
    treats the plain message as a gate-supersession (emit SSE
    ``status="superseded"``, clear pack, drop pause flag, discard
    deferred marker, cascade-resume, enqueue as fresh message).
    A plain message hitting ``/resume`` while an answer gate is
    pending NEVER consumes the gate.

  * Fix 2: Two layered guards.
      (a) ``_schedule_explicit_handle_resume`` cleanup skips the
          ``cancel_task`` for PAUSED tasks carrying an
          ``awaiting_answer`` handle — the cascade-resume's
          ``ResumeTurn`` is the sole consumer.
      (b) ``_skip_task_as_completed`` re-reads the live task and,
          if the cascade pause has set a fresh awaiting_answer
          handle, preserves the handle (marks message COMPLETED,
          fires watcher notification) WITHOUT completing the task.

  * Fix 3: ``answer_questions`` None-fallback enqueues the answer
    as a fresh user message via ``enqueue_message``; no DB-only
    paused→running flip; HTTP 500 if even the fallback enqueue
    fails. Distinct response status ``answer_fallback_enqueued``
    surfaces the degraded path so the FE can distinguish.

Tests in this file:

  Section A — Fix 1 (gate-supersession in resume endpoint)
    A1. plain message via /resume while gated → gate NOT consumed
    A2. plain message via /resume with no pack → existing resume path
    A3. plain message via /resume emits superseded SSE + clears pack

  Section B — Fix 2 (handle preservation)
    B1. cleanup at _schedule_explicit_handle_resume skips cancel for
        awaiting_answer handle task (regression guard for the cleanup
        carve-out)
    B2. cleanup at _schedule_explicit_handle_resume still cancels
        PAUSED tasks WITHOUT awaiting_answer (regression — non-handle
        PAUSED tasks are not resume handles and should still be
        cleaned up)

  Section C — Fix 2b (skip path handle preservation)
    C1. _skip_task_as_completed preserves handle when cascade pause
        has set a fresh awaiting_answer handle
    C2. _skip_task_as_completed completes task normally when no
        fresh handle (regression — existing skip behaviour intact)

  Section D — Fix 3 (answer endpoint fallback)
    D1. answer endpoint with no handle → enqueues as fresh message,
        returns status="answer_fallback_enqueued", NO 200-mask
    D2. answer endpoint with no handle + fallback enqueue fails →
        HTTP 500 with actionable message (NEVER 200-mask a delivery
        failure)
    D3. answer endpoint with valid handle → status="answered"
        (regression — existing happy path intact)
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel


# Register every model so ``SQLModel.metadata.create_all()`` builds the
# full schema (mirrors the §11.4.1 fixture recipe used by the e2e
# answer/dismiss flow tests).
import daemon.repositories.dependency_bus.models  # noqa: F401
import daemon.repositories.event.models  # noqa: F401
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.job_queue.watcher_models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.report_injection.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401

from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.message_queue.models import (
    MessageQueue,
    MessageStatus,
)
from daemon.repositories.task.models import (
    SuspensionReason,
    Task,
    TaskStatus,
    TaskType,
)


# ============================================================================
# Fixtures + helpers
# ============================================================================


@pytest.fixture
def engine() -> Engine:
    """Real in-memory SQLite engine with FK enforcement."""
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(eng, "connect")
    def _enable_fk(dbapi_conn, _connection_record):  # noqa: ANN001
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
    instance_id: str,
    status: str = InstanceStatus.PAUSED.value,
) -> None:
    """Insert a minimal Instance row for FK satisfaction."""
    with Session(engine) as session:
        session.add(
            Instance(
                instance_id=instance_id,
                agent_id="test",
                agent_name="test",
                agent_dir="/tmp/test",
                status=status,
            )
        )
        session.commit()


def _seed_task(
    engine: Engine,
    *,
    work_id: str,
    instance_id: str,
    task_id: int | None = None,
    message_id: str | None = None,
    task_type: str = TaskType.PROCESS_MESSAGE.value,
    status: str = TaskStatus.PAUSED.value,
    suspension_reason: str | None = None,
    resume_target_turn_id: str | None = None,
) -> int:
    """Insert a Task row. Returns the int task PK.

    Default state matches the production handle (PAUSED with
    awaiting_answer handle + a resume_target_turn_id pointing at
    itself). Test-specific overrides pass kwargs.
    """
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    work_id = work_id or str(uuid.uuid4())
    message_id = message_id or str(uuid.uuid4())
    with Session(engine) as session:
        row = Task(
            id=task_id,
            work_id=work_id,
            instance_id=instance_id,
            message_id=message_id,
            task_type=task_type,
            status=status,
            suspension_reason=suspension_reason,
            resume_target_turn_id=resume_target_turn_id,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return row.id


def _seed_message(
    engine: Engine,
    *,
    message_id: str,
    instance_id: str,
    status: str = MessageStatus.PROCESSING.value,
    content: str = "seeded for cleanup test",
) -> None:
    """Insert a MessageQueue row in the given status."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    with Session(engine) as session:
        session.add(
            MessageQueue(
                message_id=message_id,
                instance_id=instance_id,
                content=content,
                type="agent",
                status=status,
                priority=1,
                enqueued_at=now,
            )
        )
        session.commit()


def _read_task_status(engine: Engine, task_id: int) -> str | None:
    with Session(engine) as session:
        row = session.get(Task, task_id)
        return row.status if row else None


def _read_task_handle(engine: Engine, task_id: int) -> tuple[str | None, str | None]:
    """Return (suspension_reason, resume_target_turn_id) for a Task."""
    with Session(engine) as session:
        row = session.get(Task, task_id)
        if row is None:
            return (None, None)
        return (row.suspension_reason, row.resume_target_turn_id)


def _read_message_status(engine: Engine, message_id: str) -> str | None:
    with Session(engine) as session:
        stmt = (
            __import__("sqlmodel").select(MessageQueue)
            .where(MessageQueue.message_id == message_id)
        )
        row = session.exec(stmt).first()
        return row.status if row else None


# ============================================================================
# Section A — Fix 1: gate-supersession in resume endpoint
# ============================================================================


def _make_gate_manager(
    *,
    instance_id: str,
    pending_pack: Any | None,
    resume_processing_return: dict | None = None,
    resume_cascade_return: dict | None = None,
    enqueue_return: Any | None = None,
    enqueue_side_effect: BaseException | None = None,
) -> MagicMock:
    """Build a mock InstanceManager shaped like the resume endpoint's surface.

    Captures calls so tests can assert gate-supersession routed
    through enqueue_message and skipped resume_processing_job.
    """
    manager = MagicMock()
    manager.is_write_paused = False

    # Instance-existence check.
    async def _get_instance(iid: str):
        return MagicMock(instance_id=iid)
    manager.get_instance = _get_instance

    # Question manager surface — gate detection.
    manager._question_manager = MagicMock()
    manager._question_manager.get_question_pack = MagicMock(
        return_value=pending_pack,
    )
    manager._question_manager.clear_question_pack = MagicMock(
        return_value=None,
    )

    # Pause-flag + deferred marker surface (must be discarded on
    # supersession, mirroring dismiss_question).
    manager.clear_question_pause_requested = MagicMock(return_value=None)
    manager._deferred_question_pause = set()

    # Resume processing — the gate-steal trigger. With gate present
    # the resume endpoint MUST NOT call this; the test asserts it.
    manager.resume_processing_job = AsyncMock(
        return_value=resume_processing_return
        if resume_processing_return is not None
        else {"status": "resumed", "instance_id": instance_id},
    )

    # Cascade resume — runs in both branches.
    if resume_cascade_return is None:
        resume_cascade_return = {
            "target_id": instance_id,
            "resumed_ids": [instance_id],
            "skipped_ids": [],
        }
    manager.resume_instance_cascade = AsyncMock(
        return_value=resume_cascade_return,
    )

    # enqueue_message — the supersession delivery path.
    if enqueue_side_effect is not None:
        manager.enqueue_message = AsyncMock(side_effect=enqueue_side_effect)
    else:
        if enqueue_return is None:
            enqueue_return = MagicMock(
                message_id="msg-supersede-fresh",
                job_id="job-supersede-fresh",
            )
        manager.enqueue_message = AsyncMock(return_value=enqueue_return)

    return manager


def _make_live_hub() -> MagicMock:
    """Mock LiveEventHub — resume endpoint emits question_pack SSE
    on supersession (status="superseded")."""
    hub = MagicMock()
    hub.stream_question_pack = AsyncMock(return_value=None)
    return hub


@pytest.fixture
def client_and_state():
    """Yield (TestClient, state_dict) for the resume endpoint.

    Mirrors the lightweight pattern in tests/test_question_dismiss.py —
    mount only the instances router on a bare FastAPI app, then
    middleware-inject manager + live_hub into ``app.state`` per request.
    """
    from daemon.routers.instances import router

    app = FastAPI()
    app.include_router(router)
    state: dict = {"manager": None, "live_hub": None}

    @app.middleware("http")
    async def _inject_state(request, call_next):
        request.app.state.manager = state["manager"]
        request.app.state.live_hub = state["live_hub"]
        return await call_next(request)

    client = TestClient(app)
    yield client, state


class TestResumeEndpointGateSupersession:
    """Fix 1: plain user message via /resume while gated → gate NOT
    consumed, message enqueued as fresh, SSE superseded emitted."""

    def test_plain_message_via_resume_while_gated_does_not_consume_handle(
        self, client_and_state,
    ):
        """Gate-steal regression guard (Defect 1).

        Production: plain message hit /resume, was routed
        answer_gate_existing_turn, handle was consumed as if it were
        the answer. With Fix 1, the resume endpoint detects the
        pending pack and routes via gate-supersession — the
        ``resume_processing_job`` (which would call
        ``find_suspended_turn_for_answer``) is NEVER invoked for
        the gated target.
        """
        from daemon.services.question_manager import QuestionPack

        pack = QuestionPack(
            instance_id="inst-gate-steal",
            questions=[],
        )
        client, state = client_and_state
        manager = _make_gate_manager(
            instance_id="inst-gate-steal",
            pending_pack=pack,
        )
        state["manager"] = manager
        state["live_hub"] = _make_live_hub()

        # The supersession path delivers the message via the normal
        # ``enqueue_message`` path (NOT via the answer-gate route).
        # The enqueue must complete before the DB-only cascade begins;
        # pin that ordering while the endpoint is executing rather than
        # merely asserting both calls happened afterward.
        async def _enqueue_only_after_cascade(*args, **kwargs):
            manager.resume_instance_cascade.assert_not_called()
            return MagicMock(
                message_id="msg-supersede-fresh",
                job_id="job-supersede-fresh",
            )
        manager.enqueue_message = AsyncMock(side_effect=_enqueue_only_after_cascade)
        resp = client.post(
            "/instances/inst-gate-steal/resume",
            json={"message": "actually I just want to ask something else"},
        )

        assert resp.status_code == 200, resp.text
        # Gate-steal guard: ``resume_processing_job`` MUST NOT have
        # been called for the gated target — that selector is the
        # only entry that consumes the awaiting_answer handle.
        manager.resume_processing_job.assert_not_called()

        # The supersession path delivers the message via the normal
        # ``enqueue_message`` path (NOT via the answer-gate route).
        manager.enqueue_message.assert_awaited_once()
        call_kwargs = manager.enqueue_message.call_args.kwargs
        assert call_kwargs["instance_id"] == "inst-gate-steal"
        assert call_kwargs["source"] == "api_gate_supersede"
        assert (
            call_kwargs["message"]
            == "actually I just want to ask something else"
        )

        # Cascade-resume still ran (consumes the awaiting_answer
        # handle via ResumeTurn → PAUSED→PENDING; this is the SOLE
        # legitimate handle consumer).
        manager.resume_instance_cascade.assert_awaited_once()

        # Response shape surfaces gate-supersession so the FE can
        # distinguish it from a plain resume.
        body = resp.json()
        assert body["gate_superseded"] is True
        target_result = body["resume_results"]["inst-gate-steal"]
        assert target_result["status"] == "enqueued_as_fresh_message"
        assert target_result["route"] == "api_gate_supersede"
        assert target_result["message_id"] == "msg-supersede-fresh"

    def test_gate_supersession_enqueue_failure_keeps_instance_paused(
        self, client_and_state,
    ):
        """A failed supersession enqueue must surface 500 and never
        invoke the DB-only cascade, which would leave the instance
        RUNNING without a Task."""
        from daemon.services.question_manager import QuestionPack

        pack = QuestionPack(
            instance_id="inst-supersede-failure",
            questions=[],
        )
        client, state = client_and_state
        manager = _make_gate_manager(
            instance_id="inst-supersede-failure",
            pending_pack=pack,
            enqueue_side_effect=RuntimeError("enqueue boom"),
        )
        state["manager"] = manager
        state["live_hub"] = _make_live_hub()

        resp = client.post(
            "/instances/inst-supersede-failure/resume",
            json={"message": "try superseding"},
        )

        assert resp.status_code == 500, resp.text
        assert (
            "superseded question gate but failed to enqueue message: "
            "enqueue boom"
        ) in resp.json()["detail"]
        # The DB-only state transition is the dangerous part: it was
        # never reached, so the instance remains paused.
        manager.enqueue_message.assert_awaited_once()
        manager.resume_instance_cascade.assert_not_awaited()
        manager.resume_processing_job.assert_not_awaited()

    def test_plain_message_via_resume_clears_pack_and_emits_sse(
        self, client_and_state,
    ):
        """Question-pack lifecycle stays coherent on supersession.

        The pack MUST be cleared (answered → cleared OR superseded
        → cleared; stale-pack-forever is unacceptable), the pause
        flag MUST be dropped, the deferred-pause marker MUST be
        discarded, and the SSE event MUST carry status="superseded".
        """
        from daemon.services.question_manager import QuestionPack

        pack = QuestionPack(
            instance_id="inst-pack-clear",
            questions=[],
        )
        client, state = client_and_state
        manager = _make_gate_manager(
            instance_id="inst-pack-clear",
            pending_pack=pack,
        )
        # Stage a deferred-pause marker to confirm it gets discarded.
        manager._deferred_question_pause.add("inst-pack-clear")
        state["manager"] = manager
        state["live_hub"] = _make_live_hub()

        resp = client.post(
            "/instances/inst-pack-clear/resume",
            json={"message": "go"},
        )
        assert resp.status_code == 200, resp.text

        # Pack was read, then cleared.
        manager._question_manager.get_question_pack.assert_called_once_with(
            "inst-pack-clear",
        )
        manager._question_manager.clear_question_pack.assert_called_once_with(
            "inst-pack-clear",
        )
        # Pause flag was dropped.
        manager.clear_question_pause_requested.assert_called_once_with(
            "inst-pack-clear",
        )
        # Deferred-pause marker was discarded.
        assert "inst-pack-clear" not in manager._deferred_question_pause

        # SSE emitted with status="superseded".
        live_hub = state["live_hub"]
        live_hub.stream_question_pack.assert_awaited_once()
        # The call uses keyword args ``stream_question_pack(instance_id, payload)``.
        call = live_hub.stream_question_pack.call_args
        # Position 0 is instance_id, position 1 is the payload dict.
        sse_args = call.args
        sse_payload = sse_args[1] if len(sse_args) >= 2 else call.kwargs.get(
            next(iter(call.kwargs.keys()), None)
        )
        # Fallback: pull the dict from kwargs if positional wasn't found.
        if not isinstance(sse_payload, dict):
            for v in call.kwargs.values():
                if isinstance(v, dict):
                    sse_payload = v
                    break
        assert sse_payload["status"] == "superseded"

    def test_plain_message_via_resume_with_no_pack_uses_existing_path(
        self, client_and_state,
    ):
        """Regression: without a pending pack, the existing
        resume_processing_job path runs unchanged (Defect 1 fix is
        strictly conditional on a pending pack)."""
        client, state = client_and_state
        manager = _make_gate_manager(
            instance_id="inst-no-pack",
            pending_pack=None,
            resume_processing_return={
                "status": "resumed",
                "instance_id": "inst-no-pack",
            },
        )
        state["manager"] = manager
        state["live_hub"] = _make_live_hub()

        resp = client.post(
            "/instances/inst-no-pack/resume",
            json={"message": "go"},
        )

        assert resp.status_code == 200, resp.text
        # Existing path: ``resume_processing_job`` was called for the
        # target; ``enqueue_message`` (supersession path) was NOT.
        manager.resume_processing_job.assert_awaited_once()
        manager.enqueue_message.assert_not_called()

        # No ``gate_superseded`` flag on the response (this is a
        # plain resume, not a supersession).
        body = resp.json()
        assert "gate_superseded" not in body

        # Pack surface was read for the gate check but not cleared
        # (no pack present).
        manager._question_manager.get_question_pack.assert_called_once_with(
            "inst-no-pack",
        )
        manager._question_manager.clear_question_pack.assert_not_called()


# ============================================================================
# Section B — Fix 2a: cleanup preserves awaiting_answer handle
# ============================================================================


class TestScheduleExplicitHandleResumeRealSeamPreservesAwaitingAnswerHandle:
    """Drive Fix 2a's carve-out through the real manager seam.

    The cleanup in ``_schedule_explicit_handle_resume`` must preserve a
    PAUSED ``awaiting_answer`` handle while still cancelling a normal
    PAUSED external task.  This test does not duplicate the predicate:
    it invokes the real method with real task/message repositories.
    """

    @pytest.mark.asyncio
    async def test_real_seam_preserves_handle_and_cancels_paused_external(
        self, engine,
    ):
        """The real cleanup preserves the handle and cancels the control."""
        from daemon.cancellation import CancellationTokenSource
        from daemon.manager import InstanceManager
        from daemon.repositories.message_queue.repository import (
            SQLModelMessageQueueRepository,
        )
        from daemon.repositories.task.repository import TaskRepository

        instance_id = "inst-real-seam-cleanup"
        _seed_instance(engine, instance_id=instance_id)

        handle_work_id = str(uuid.uuid4())
        handle_message_id = str(uuid.uuid4())
        handle_task_id = _seed_task(
            engine,
            work_id=handle_work_id,
            instance_id=instance_id,
            task_type=TaskType.PROCESS_REPORT.value,
            status=TaskStatus.PAUSED.value,
            suspension_reason=SuspensionReason.AWAITING_ANSWER.value,
            resume_target_turn_id=handle_work_id,
            message_id=handle_message_id,
        )
        _seed_message(
            engine,
            message_id=handle_message_id,
            instance_id=instance_id,
            status=MessageStatus.PROCESSING.value,
        )

        control_work_id = str(uuid.uuid4())
        control_message_id = str(uuid.uuid4())
        control_task_id = _seed_task(
            engine,
            work_id=control_work_id,
            instance_id=instance_id,
            task_type=TaskType.PROCESS_MESSAGE.value,
            status=TaskStatus.PAUSED.value,
            suspension_reason=SuspensionReason.PAUSED_EXTERNAL.value,
            resume_target_turn_id=None,
            message_id=control_message_id,
        )
        _seed_message(
            engine,
            message_id=control_message_id,
            instance_id=instance_id,
            status=MessageStatus.PROCESSING.value,
        )

        manager = InstanceManager.__new__(InstanceManager)
        manager._graph_tasks = {}
        manager._deferred_question_pause = set()
        manager._task_repo = TaskRepository(engine=engine)
        manager._queue_repository = SQLModelMessageQueueRepository(
            engine=engine,
        )
        manager._request_registry = MagicMock()
        token_source = CancellationTokenSource()
        manager._request_registry.register.return_value = token_source

        async def _noop_background(*args, **kwargs):  # noqa: ANN001
            return None

        manager._resume_processing_background = _noop_background
        manager._schedule_explicit_handle_resume = (
            InstanceManager._schedule_explicit_handle_resume.__get__(
                manager
            )
        )

        await manager._schedule_explicit_handle_resume(
            instance_id=instance_id,
            message="resume trigger",
            silent=True,
            images=None,
            target_work_id=handle_work_id,
            selected_suspension_reason=(
                SuspensionReason.AWAITING_ANSWER.value
            ),
            handle_work_id=handle_work_id,
            route_outcome="answer_gate_existing_turn",
        )

        with Session(engine) as session:
            handle_task = session.get(Task, handle_task_id)
            control_task = session.get(Task, control_task_id)
            assert handle_task is not None
            assert control_task is not None
            assert handle_task.status == TaskStatus.PAUSED.value
            assert control_task.status == TaskStatus.CANCELLED.value

        # The real method scheduled only the no-op replacement task. Drain
        # it so this focused test leaves no background task behind.
        scheduled_task = manager._graph_tasks.pop(instance_id)
        await scheduled_task


# ============================================================================
# Section C — Fix 2b: skip path preserves fresh awaiting_answer handle
# ============================================================================


class TestSkipPathPreservesAwaitingAnswerHandle:
    """Fix 2b: ``_skip_task_as_completed`` re-reads the live task and,
    if the cascade pause has set a fresh awaiting_answer handle,
    preserves the handle (marks message COMPLETED + fires watcher
    notification) WITHOUT completing the task. CompleteTurn would
    otherwise clear the freshly-set handle."""

    def _build_processor(self, engine, live_task_factory):
        """Construct a ProcessMessageProcessor whose task_repo reads
        from ``engine`` and whose ``get_by_work_id`` returns a
        caller-supplied live task (simulating the cascade-pause
        having just re-set the awaiting_answer handle).

        NOTE: bare MagicMock is truthy — per the task brief, we use
        a real TaskRepository + a thin shim around ``get_by_work_id``
        so the carve-out sees the live row's actual fields.
        """
        from daemon.services.task_processor import ProcessMessageProcessor
        from daemon.repositories.task.repository import TaskRepository

        manager = MagicMock()
        manager._task_repo = TaskRepository(engine=engine)
        # Override get_by_work_id with a factory the test controls.
        manager._task_repo.get_by_work_id = MagicMock(
            side_effect=live_task_factory,
        )
        manager._work_resolver = None
        manager._watcher_repo = None
        manager._queue_repository = MagicMock()
        manager._queue_repository.complete = MagicMock(return_value=None)

        proc = ProcessMessageProcessor.__new__(ProcessMessageProcessor)
        proc._manager = manager
        proc._task_repo = manager._task_repo
        proc._queue_repository = manager._queue_repository
        proc._work_resolver = None
        proc._watcher_repo = None
        proc._pipeline = MagicMock()
        return proc

    def _run(self, coro):
        """Drive the coroutine to completion synchronously.

        Uses ``asyncio.new_event_loop`` + ``asyncio.set_event_loop`` to
        avoid the Python 3.12+ deprecation that ``get_event_loop`` no
        longer auto-creates the loop on the main thread. The
        ``run_until_complete`` shape matches the legacy
        ``asyncio.get_event_loop().run_until_complete(...)`` recipe
        while remaining compatible across test orderings (the prior
        recipe occasionally saw ``RuntimeError: There is no current
        event loop`` when another test in the suite had already
        closed the default loop).
        """
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            return loop.run_until_complete(coro)
        finally:
            loop.close()
            asyncio.set_event_loop(None)

    def test_skip_preserves_handle_when_cascade_pause_set_fresh_awaiting_answer(
        self, engine,
    ):
        """C1: when the cascade pause re-suspends the task back to
        PAUSED with a fresh awaiting_answer handle BEFORE the skip
        path runs, the skip path MUST NOT call ``complete_task``
        (which would ``CompleteTurn``-clear the handle). It marks
        the message COMPLETED, returns success, and surfaces
        ``handle_preserved=True`` in the result dict.
        """
        instance_id = "inst-skip-preserve"
        _seed_instance(engine, instance_id=instance_id)

        work_id = str(uuid.uuid4())
        message_id = str(uuid.uuid4())
        task_pk = _seed_task(
            engine,
            work_id=work_id,
            instance_id=instance_id,
            task_type=TaskType.PROCESS_REPORT.value,
            # Claim-time state (RUNNING) — the WorkerPool's stale
            # view.
            status=TaskStatus.RUNNING.value,
            suspension_reason=None,
            resume_target_turn_id=None,
            message_id=message_id,
        )
        _seed_message(
            engine,
            message_id=message_id,
            instance_id=instance_id,
            status=MessageStatus.COMPLETED.value,
        )

        # Cascade-pause simulation: live re-read returns the task
        # back in PAUSED with a fresh awaiting_answer handle
        # (mirror of the ``finally`` block in
        # ``_process_message_with_tracking``).
        from daemon.repositories.task.repository import TaskRepository
        live_repo = TaskRepository(engine=engine)

        def live_task_factory(wid):
            row = live_repo.get_by_work_id(wid)
            return row

        proc = self._build_processor(engine, live_task_factory)

        # Build a stale task mirror (status=RUNNING, no handle) the
        # way the WorkerPool would hand it to ``process``.
        from sqlmodel import Session
        with Session(engine) as session:
            stale = session.get(Task, task_pk)

        # Now flip the live row to PAUSED with awaiting_answer
        # handle (the cascade pause).
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        with Session(engine) as session:
            row = session.get(Task, task_pk)
            row.status = TaskStatus.PAUSED.value
            row.suspension_reason = (
                SuspensionReason.AWAITING_ANSWER.value
            )
            row.resume_target_turn_id = work_id  # self-pointing
            session.add(row)
            session.commit()

        # Run the skip path.
        result = self._run(proc._skip_task_as_completed(stale))

        # Carve-out contract: handle preserved, message marked
        # COMPLETED, success returned.
        assert result["success"] is True
        assert result["handle_preserved"] is True
        assert result["message_id"] == message_id

        # The task row is STILL PAUSED with the awaiting_answer
        # handle intact — CompleteTurn was NOT called.
        post_status = _read_task_status(engine, task_pk)
        post_handle = _read_task_handle(engine, task_pk)
        assert post_status == TaskStatus.PAUSED.value
        assert post_handle[0] == SuspensionReason.AWAITING_ANSWER.value
        assert post_handle[1] == work_id

    def test_skip_completes_task_normally_without_fresh_handle(
        self, engine,
    ):
        """C2 regression: when the cascade pause has NOT re-suspended
        the task (no fresh awaiting_answer handle), the existing
        skip path completes the task normally. The carve-out MUST
        NOT widen to every skip invocation — only to the
        handle-preservation window.
        """
        instance_id = "inst-skip-normal"
        _seed_instance(engine, instance_id=instance_id)

        work_id = str(uuid.uuid4())
        message_id = str(uuid.uuid4())
        task_pk = _seed_task(
            engine,
            work_id=work_id,
            instance_id=instance_id,
            task_type=TaskType.PROCESS_REPORT.value,
            status=TaskStatus.RUNNING.value,
            suspension_reason=None,
            resume_target_turn_id=None,
            message_id=message_id,
        )
        _seed_message(
            engine,
            message_id=message_id,
            instance_id=instance_id,
            status=MessageStatus.COMPLETED.value,
        )

        # Cascade-pause did NOT fire — live re-read still shows
        # RUNNING with no handle.
        from daemon.repositories.task.repository import TaskRepository
        live_repo = TaskRepository(engine=engine)

        def live_task_factory(wid):
            return live_repo.get_by_work_id(wid)

        proc = self._build_processor(engine, live_task_factory)
        from sqlmodel import Session
        with Session(engine) as session:
            stale = session.get(Task, task_pk)

        result = self._run(proc._skip_task_as_completed(stale))

        # Existing skip shape: success, no handle_preserved flag,
        # task transitions RUNNING → COMPLETED.
        assert result["success"] is True
        assert "handle_preserved" not in result
        post_status = _read_task_status(engine, task_pk)
        assert post_status == TaskStatus.COMPLETED.value


# ============================================================================
# Section D — Fix 3: answer endpoint fallback (no 200-mask)
# ============================================================================


def _make_answer_manager(
    *,
    instance_id: str,
    pending_pack: Any | None,
    resume_processing_return: dict | None,
    resume_cascade_return: dict | None = None,
    enqueue_return: Any | None = None,
    enqueue_side_effect: BaseException | None = None,
) -> MagicMock:
    """Build a mock InstanceManager shaped like the answer endpoint's surface."""
    manager = MagicMock()
    manager.is_write_paused = False

    async def _get_instance(iid: str):
        return MagicMock(instance_id=iid)
    manager.get_instance = _get_instance

    manager._question_manager = MagicMock()
    manager._question_manager.set_answers = MagicMock(return_value=pending_pack)

    manager.resume_processing_job = AsyncMock(
        return_value=resume_processing_return,
    )

    if resume_cascade_return is None:
        resume_cascade_return = {
            "target_id": instance_id,
            "resumed_ids": [instance_id],
            "skipped_ids": [],
        }
    manager.resume_instance_cascade = AsyncMock(
        return_value=resume_cascade_return,
    )

    if enqueue_side_effect is not None:
        manager.enqueue_message = AsyncMock(side_effect=enqueue_side_effect)
    else:
        if enqueue_return is None:
            enqueue_return = MagicMock(
                message_id="msg-answer-fallback",
                job_id="job-answer-fallback",
            )
        manager.enqueue_message = AsyncMock(return_value=enqueue_return)

    return manager


@pytest.fixture
def answer_client_and_state():
    """Yield (TestClient, state_dict) for the answer endpoint.

    Same lightweight pattern as Section A — mount only the instances
    router, middleware-inject manager per request.
    """
    from daemon.routers.instances import router

    app = FastAPI()
    app.include_router(router)
    state: dict = {"manager": None, "live_hub": None}

    @app.middleware("http")
    async def _inject_state(request, call_next):
        request.app.state.manager = state["manager"]
        request.app.state.live_hub = state["live_hub"]
        return await call_next(request)

    client = TestClient(app)
    yield client, state


class TestAnswerEndpointNoHandleFallback:
    """Fix 3: the answer endpoint MUST NOT silently drop the answer
    when no awaiting_answer handle is resolvable. Fallback =
    enqueue as fresh user message; surface distinct status."""

    def test_answer_with_no_handle_enqueues_as_fresh_message(
        self, answer_client_and_state,
    ):
        """D1: ``resume_processing_job`` returns None (no handle)
        → fallback ``enqueue_message`` delivers the answer content
        → response surfaces ``status="answer_fallback_enqueued"``
        so the FE can distinguish the degraded path from a normal
        answer-resume. NEVER 200-mask a lost answer."""
        from daemon.services.question_manager import QuestionPack

        # A pack so set_answers succeeds; the resume_processing_job
        # is what returns None.
        pack = QuestionPack(
            instance_id="inst-answer-fallback",
            questions=[],
        )
        client, state = answer_client_and_state
        manager = _make_answer_manager(
            instance_id="inst-answer-fallback",
            pending_pack=pack,
            resume_processing_return=None,  # NO HANDLE — Defect 3 trigger
            enqueue_return=MagicMock(
                message_id="msg-answer-fresh",
                job_id="job-answer-fresh",
            ),
        )
        state["manager"] = manager
        state["live_hub"] = MagicMock(stream_question_pack=AsyncMock())

        resp = client.post(
            "/instances/inst-answer-fallback/answer",
            json={"answers": {"q1": "answer1"}},
        )

        assert resp.status_code == 200, resp.text
        # Fallback enqueue was called with the answer content
        # (the Q↔A formatted HumanMessage text).
        manager.enqueue_message.assert_awaited_once()
        call_kwargs = manager.enqueue_message.call_args.kwargs
        assert call_kwargs["instance_id"] == "inst-answer-fallback"
        assert call_kwargs["source"] == "api_answer_fallback"
        # Message contains the Q↔A text (at minimum the header).
        assert "answer" in call_kwargs["message"].lower()

        # Cascade still ran (the fallback enqueued a fresh Task row
        # the WorkerPool will drive).
        manager.resume_instance_cascade.assert_awaited_once()

        # Response shape surfaces the distinct fallback status.
        body = resp.json()
        assert body["status"] == "answer_fallback_enqueued"
        target_result = body["resume_info"]["resume_results"][
            "inst-answer-fallback"
        ]
        assert target_result["status"] == "enqueued_as_fresh_message"
        assert target_result["route"] == "api_answer_fallback"
        assert target_result["message_id"] == "msg-answer-fresh"

    def test_answer_with_no_handle_and_fallback_enqueue_fails_returns_500(
        self, answer_client_and_state,
    ):
        """D2: when even the fallback enqueue fails, the answer
        endpoint MUST surface HTTP 500 (NOT 200-mask). The user
        typed an answer; a delivery failure MUST be loud."""
        from daemon.services.question_manager import QuestionPack

        pack = QuestionPack(
            instance_id="inst-answer-fallback-fail",
            questions=[],
        )
        client, state = answer_client_and_state
        manager = _make_answer_manager(
            instance_id="inst-answer-fallback-fail",
            pending_pack=pack,
            resume_processing_return=None,
            enqueue_side_effect=RuntimeError("queue is down"),
        )
        state["manager"] = manager
        state["live_hub"] = MagicMock(stream_question_pack=AsyncMock())

        resp = client.post(
            "/instances/inst-answer-fallback-fail/answer",
            json={"answers": {"q1": "answer1"}},
        )

        # 500 — NEVER 200-mask a delivery failure. The cascade must
        # NOT have flipped the instance to RUNNING-idle; the
        # request fails fast.
        assert resp.status_code == 500, resp.text
        detail = resp.json()["detail"]
        assert "queue is down" in detail["message"]
        assert "no awaiting_answer" in detail["message"].lower()
        # Cascade did NOT run (the failure surfaced before the
        # cascade call).
        manager.resume_instance_cascade.assert_not_called()

    def test_answer_with_valid_handle_returns_answered_status(
        self, answer_client_and_state,
    ):
        """D3 regression: when ``resume_processing_job`` returns a
        valid handle (the normal answer-resume path), the response
        is ``status="answered"`` and the fallback enqueue is NOT
        called. The Defect 3 fix is strictly conditional on the
        ``None`` return path."""
        from daemon.services.question_manager import QuestionPack

        pack = QuestionPack(
            instance_id="inst-answer-happy",
            questions=[],
        )
        client, state = answer_client_and_state
        manager = _make_answer_manager(
            instance_id="inst-answer-happy",
            pending_pack=pack,
            resume_processing_return={
                "status": "resumed",
                "instance_id": "inst-answer-happy",
                "job_id": "job-happy",
                "message_id": "msg-happy",
            },
        )
        state["manager"] = manager
        state["live_hub"] = MagicMock(stream_question_pack=AsyncMock())

        resp = client.post(
            "/instances/inst-answer-happy/answer",
            json={"answers": {"q1": "answer1"}},
        )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "answered"
        # Fallback enqueue MUST NOT fire on the happy path.
        manager.enqueue_message.assert_not_called()
