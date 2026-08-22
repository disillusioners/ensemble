"""Unit tests for the FM-1 type-aware guard (pause-report-recovery
Phase 2, task 2.3).

Phase 2 task 2.3 inserts a type-aware exemption predicate inside
the ``_schedule_explicit_handle_resume`` PENDING-task branch. The
guard exempts PROCESS_REPORT tasks whose message is a
``completion_report`` row AND a non-terminal ``report_injections``
row exists — preventing the unmodified FM-1 loop from killing the
PROCESS_REPORT tasks that the Phase 2 recovery sweep just created.

The exemption predicate is the canonical ``has_non_terminal_injection_for``
helper on the manager (one of three conditions in the corrected
predicate):

* ``task.task_type == PROCESS_REPORT``
* AND message type is COMPLETION_REPORT
* AND a non-terminal ``ReportInjection`` row exists for the
  ``report_message_id`` (PENDING or DEFERRED state).

POST-REVIEW AMENDMENT (2026-08-20, deep-review verdict REJECT): the
v1 predicate also required ``msg.status == READY`` — that term was
DEAD CODE because the enclosing loop already filters messages to
``PENDING|PROCESSING|RETRYING`` (READY messages never enter the
inner branch). The corrected predicate drops the READY term. The
natural lifecycle keeps a freshly-swept PROCESS_REPORT task tied
to a PROCESSING message — FM-1 must exempt exactly there.

PROCESS_MESSAGE tasks keep the existing cancel+complete behavior
(they cannot be a completion_report delivery).

ANTIPHANTOM-RACE-FIX regression must remain intact — the
RUNNING-task skip and the no-task skip above are untouched. This
guard ONLY EXEMPTS — it does NOT re-open the cancel path. The
RUNNING-task skip branch is a refactor-guarded branch verified by
the manager.py inline comments (the canonical contract), not by a
dedicated unit test (T-H1, 2026-08-20 — the vacuous ``assert True``
pin was deleted; real coverage lives in ``TestFM1GuardRealLoop``).

These tests run against a real in-memory SQLite database so the
write-side and read-side semantics are observable.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, select as sm_select

# Register every table the helper touches before create_all().
import daemon.repositories.dependency_bus.models  # noqa: F401
import daemon.repositories.event.models  # noqa: F401
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.report_injection.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401

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
from daemon.repositories.task.models import Task, TaskStatus, TaskType


# =============================================================================
# Fixtures + helpers
# =============================================================================


@pytest.fixture
def engine() -> Engine:
    """Real in-memory SQLite engine with all tables created."""
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
    parent_id: str | None = None,
    status: str = InstanceStatus.RUNNING.value,
) -> str:
    """Insert an Instance row."""
    instance_id = instance_id or f"inst-{uuid.uuid4().hex[:8]}"
    with Session(engine) as session:
        session.add(
            Instance(
                instance_id=instance_id,
                agent_id="test",
                agent_name="test",
                agent_dir="/tmp",
                parent_id=parent_id,
                status=status,
                version=1,
                instance_metadata={},
            )
        )
        session.commit()
    return instance_id


def _seed_message_and_task(
    engine: Engine,
    *,
    instance_id: str,
    task_type: str = TaskType.PROCESS_REPORT.value,
    msg_status: str = MessageStatus.READY.value,
    msg_type: str = MessageType.COMPLETION_REPORT.value,
) -> tuple[str, int]:
    """Insert a MessageQueue + Task pair. Returns ``(msg_id, task_id)``.

    Task is in PENDING state (the FM-1 guard fires on the PENDING
    branch).
    """
    msg_id = str(uuid.uuid4())
    with Session(engine) as session:
        session.add(
            MessageQueue(
                message_id=msg_id,
                instance_id=instance_id,
                content="report",
                source=f"internal_report:child:child-msg",
                type=msg_type,
                status=msg_status,
                priority=0,
                enqueued_at=datetime.now(timezone.utc),
            )
        )
        session.commit()
        task = Task(
            task_type=task_type,
            instance_id=instance_id,
            message_id=msg_id,
            status=TaskStatus.PENDING.value,
            created_at=datetime.now(timezone.utc),
        )
        session.add(task)
        session.commit()
        session.refresh(task)
        task_id = task.id
    return msg_id, task_id


def _seed_injection_row(
    engine: Engine,
    *,
    parent_instance_id: str,
    report_message_id: str,
    state: str = ReportInjectionState.PENDING.value,
) -> str:
    """Insert a ``ReportInjection`` row. Returns the injection_id."""
    injection_id = str(uuid.uuid4())
    with Session(engine) as session:
        session.add(
            ReportInjection(
                injection_id=injection_id,
                parent_instance_id=parent_instance_id,
                child_instance_id="child-1",
                child_message_id="child-msg",
                report_message_id=report_message_id,
                content="report",
                state=state,
                recovery_attempted_at=None,
                created_at=datetime.now(timezone.utc).isoformat(),
            )
        )
        session.commit()
    return injection_id


def _build_minimal_manager(engine: Engine) -> MagicMock:
    """Build a minimal MagicMock that quacks like InstanceManager.

    The FM-1 guard tests exercise ``_has_non_terminal_injection_for``
    on the manager and the actual predicate inside the PENDING-task
    branch. We mock ``_task_repo.get_by_message`` to return the
    seeded Task; the rest of the FM-1 logic is observed through the
    ``should_cancel`` decision.
    """
    from daemon.repositories.report_injection.repository import (
        ReportInjectionRepository,
    )
    from daemon.manager import InstanceManager

    manager = MagicMock(name="InstanceManager")
    manager.engine = engine
    real_ri_repo = ReportInjectionRepository(engine=engine)
    manager._report_injection_repo = real_ri_repo
    # Wire the REAL helper (not a MagicMock) — the helper is a
    # plain method that reads ``self._report_injection_repo``.
    manager._has_non_terminal_injection_for = (
        InstanceManager._has_non_terminal_injection_for.__get__(
            manager
        )
    )
    return manager


# =============================================================================
# FM-1 guard — PROCESS_REPORT exemption (PENDING + DEFERRED variants)
# =============================================================================


class TestFM1GuardProcessReportExempt:
    """PROCESS_REPORT PENDING + READY message + non-terminal
    injection → NOT cancelled (the guard exempts)."""

    def test_process_report_with_pending_injection_exempt(
        self, engine: Engine
    ) -> None:
        """PROCESS_REPORT + READY completion_report + PENDING
        injection row → exempt (NOT cancelled)."""
        parent = _seed_instance(engine)
        msg_id, task_id = _seed_message_and_task(
            engine,
            instance_id=parent,
            task_type=TaskType.PROCESS_REPORT.value,
        )
        _seed_injection_row(
            engine,
            parent_instance_id=parent,
            report_message_id=msg_id,
            state=ReportInjectionState.PENDING.value,
        )

        manager = _build_minimal_manager(engine)

        # The guard predicate mirrors the inline FM-1 guard logic.
        # Simulate the predicate by calling the helper.
        result = manager._has_non_terminal_injection_for(msg_id)
        assert result is True, (
            "PROCESS_REPORT + READY completion_report + PENDING "
            "injection row must be EXEMPT from cancel+complete"
        )

    def test_process_report_with_deferred_injection_exempt(
        self, engine: Engine
    ) -> None:
        """PROCESS_REPORT + READY completion_report + DEFERRED
        injection row → exempt (NOT cancelled).

        The DEFERRED variant: a fresh DEFERRED marker just
        transitioned → the row is now PENDING with
        ``recovery_attempted_at`` stamped. The exemption
        predicate covers BOTH shapes via the
        ``state IN ('PENDING', 'DEFERRED')`` check.
        """
        parent = _seed_instance(engine)
        msg_id, task_id = _seed_message_and_task(
            engine,
            instance_id=parent,
            task_type=TaskType.PROCESS_REPORT.value,
        )
        # Insert a DEFERRED row directly (the predicate covers
        # DEFERRED too).
        _seed_injection_row(
            engine,
            parent_instance_id=parent,
            report_message_id=msg_id,
            state=ReportInjectionState.DEFERRED.value,
        )

        manager = _build_minimal_manager(engine)
        result = manager._has_non_terminal_injection_for(msg_id)
        assert result is True, (
            "PROCESS_REPORT + READY completion_report + DEFERRED "
            "injection row must be EXEMPT (predicate covers both "
            "shapes)"
        )

    def test_process_report_with_terminal_injection_not_exempt(
        self, engine: Engine
    ) -> None:
        """PROCESS_REPORT + READY completion_report + TERMINAL
        injection row (INJECTED / TASK_DELIVERED) → NOT exempt.

        Delivery has happened; the FM-1 loop's cancel+complete is
        safe to fire.
        """
        parent = _seed_instance(engine)
        msg_id, task_id = _seed_message_and_task(
            engine,
            instance_id=parent,
            task_type=TaskType.PROCESS_REPORT.value,
        )
        _seed_injection_row(
            engine,
            parent_instance_id=parent,
            report_message_id=msg_id,
            state=ReportInjectionState.TASK_DELIVERED.value,
        )

        manager = _build_minimal_manager(engine)
        result = manager._has_non_terminal_injection_for(msg_id)
        assert result is False, (
            "PROCESS_REPORT + terminal injection row must NOT be "
            "exempt (delivery has happened)"
        )

    def test_process_report_no_injection_row_not_exempt(
        self, engine: Engine
    ) -> None:
        """PROCESS_REPORT + READY completion_report + NO injection
        row → NOT exempt.

        The legacy / upgrade / future-code-path shape — the
        FM-1 loop's cancel+complete is safe to fire.
        """
        parent = _seed_instance(engine)
        msg_id, task_id = _seed_message_and_task(
            engine,
            instance_id=parent,
            task_type=TaskType.PROCESS_REPORT.value,
        )
        # No injection row.

        manager = _build_minimal_manager(engine)
        result = manager._has_non_terminal_injection_for(msg_id)
        assert result is False


class TestFM1GuardProcessMessageUnchanged:
    """PROCESS_MESSAGE keeps the existing cancel+complete behavior."""

    def test_process_message_unaffected(
        self, engine: Engine
    ) -> None:
        """PROCESS_MESSAGE tasks keep the FM-1 cancel+complete —
        the guard ONLY EXEMPTS PROCESS_REPORT.
        """
        parent = _seed_instance(engine)
        msg_id, task_id = _seed_message_and_task(
            engine,
            instance_id=parent,
            task_type=TaskType.PROCESS_MESSAGE.value,
            msg_type=MessageType.HUMAN.value,
        )
        # Even if a non-terminal injection row exists for the
        # PROCESS_MESSAGE task's message (very unusual), the
        # guard must NOT exempt — PROCESS_MESSAGE keeps the
        # original cancel+complete.
        _seed_injection_row(
            engine,
            parent_instance_id=parent,
            report_message_id=msg_id,
            state=ReportInjectionState.PENDING.value,
        )

        manager = _build_minimal_manager(engine)
        # The guard's predicate keys on
        # ``task.task_type == PROCESS_REPORT`` BEFORE checking
        # the injection row, so PROCESS_MESSAGE tasks skip the
        # check and keep the original behavior. We assert that
        # the helper returns True (the row exists), but the
        # inline guard checks the task_type FIRST.
        assert manager._has_non_terminal_injection_for(msg_id) is True
        # The decision is made at the call site:
        # ``is_deliverable_process_report`` requires
        # ``stale_task.task_type == PROCESS_REPORT`` AND the
        # helper's True result. PROCESS_MESSAGE fails the first
        # condition → not exempt.

    def test_process_report_message_processing_exempt(
        self, engine: Engine
    ) -> None:
        """A PROCESS_REPORT whose message is PROCESSING (the
        realistic freeze scenario) → EXEMPT. POST-REVIEW
        AMENDMENT (2026-08-20): the corrected predicate drops
        the ``msg.status == READY`` term (it was DEAD CODE because
        the enclosing loop already filters out READY messages).
        The natural lifecycle keeps a freshly-swept PROCESS_REPORT
        task tied to a PROCESSING message — the cascade
        transitioned the Task PAUSED→PENDING (Phase 4b/4c) while
        the message stayed PROCESSING. FM-1 must exempt this case.
        """
        parent = _seed_instance(engine)
        msg_id, task_id = _seed_message_and_task(
            engine,
            instance_id=parent,
            task_type=TaskType.PROCESS_REPORT.value,
            msg_status=MessageStatus.PROCESSING.value,
        )
        _seed_injection_row(
            engine,
            parent_instance_id=parent,
            report_message_id=msg_id,
            state=ReportInjectionState.PENDING.value,
        )

        manager = _build_minimal_manager(engine)
        # The corrected inline predicate no longer checks msg.status.
        # PROCESS_REPORT + PROCESSING message + non-terminal injection
        # → exempt (the predicate fires).
        assert manager._has_non_terminal_injection_for(msg_id) is True


class TestFM1GuardNullKeyed:
    """C4 NULL-keyed ``report_message_id`` handling."""

    def test_null_report_message_id_returns_false(
        self, engine: Engine
    ) -> None:
        """A NULL ``report_message_id`` (the Site-1 marker shape)
        returns ``False`` from the helper — the FM-1 guard MUST
        NOT exempt NULL-keyed rows. The C4 grep-audit covers
        this: the helper handles NULL by short-circuiting.

        The recovery sweep / router handle the NULL-keyed shape
        via the 2.2 reconciliation path BEFORE the FM-1 loop
        sees it (a NULL-keyed row is a pre-artifact marker — by
        the time the FM-1 loop checks, the reconciliation has
        backfilled ``report_message_id``).
        """
        manager = _build_minimal_manager(engine)
        result = manager._has_non_terminal_injection_for(None)
        assert result is False, (
            "C4: NULL-keyed report_message_id MUST return False "
            "(pre-artifact Site-1 marker shape; not a deliverable "
            "PROCESS_REPORT task)"
        )

    def test_missing_injection_row_returns_false(
        self, engine: Engine
    ) -> None:
        """A non-NULL but missing ``report_message_id`` returns
        ``False`` (no row exists for the message_id).
        """
        manager = _build_minimal_manager(engine)
        result = manager._has_non_terminal_injection_for(
            "no-such-message-id"
        )
        assert result is False


# =============================================================================
# FM-1 guard — REAL-LOOP end-to-end (deep-review addition, 2026-08-20)
# =============================================================================


class TestFM1GuardRealLoop:
    """Drive the real ``_schedule_explicit_handle_resume`` path against
    an in-memory engine.

    The deep-review verdict REJECTed the v1 fix because the unit
    tests above only exercise the
    ``_has_non_terminal_injection_for`` helper directly — the inline
    guard inside the FM-1 loop is not driven by a real flow. This
    class exercises the REAL FM-1 inner loop end-to-end with:

    * a real ``MessageQueueRepository`` against an in-memory engine,
    * a real ``TaskRepository`` against the same engine,
    * a real ``ReportInjectionRepository`` for the helper,
    * mock ``_request_registry`` + mock ``_resume_processing_background``
      so we observe the FM-1 cleanup without actually starting the
      background turn,
    * mock ``_graph_tasks`` dict.

    The corrected predicate (drop ``msg.status == READY``) is
    asserted: a PROCESS_REPORT task tied to a non-terminal injection
    row SURVIVES the cleanup loop while a PROCESS_MESSAGE task is
    still cancelled+completed.

    The test seeds the realistic freeze scenario:
    ``msg.status=PROCESSING`` + ``task.status=PENDING`` — this is the
    shape the cascade produces when a worker claimed the message
    mid-flight when pause fired and the cascade later transitioned
    the Task PAUSED→PENDING (Phase 4b/4c).
    """

    @pytest.mark.asyncio
    async def test_real_loop_process_report_survives(
        self, engine: Engine
    ) -> None:
        """PROCESS_REPORT + completion_report message + non-terminal
        injection → SURVIVES the cleanup loop (task still PENDING,
        message still PROCESSING). PROCESS_MESSAGE + HUMAN → cancelled
        and completed. POST-REVIEW real-loop regression.
        """
        from daemon.cancellation import CancellationTokenSource
        from daemon.manager import InstanceManager
        from daemon.repositories.message_queue.repository import (
            SQLModelMessageQueueRepository,
        )
        from daemon.repositories.task.repository import TaskRepository

        parent = _seed_instance(engine)

        # Seed PROCESS_REPORT: message PROCESSING, task PENDING,
        # injection PENDING. This is the realistic freeze scenario.
        report_msg_id, report_task_id = _seed_message_and_task(
            engine,
            instance_id=parent,
            task_type=TaskType.PROCESS_REPORT.value,
            msg_status=MessageStatus.PROCESSING.value,
            msg_type=MessageType.COMPLETION_REPORT.value,
        )
        _seed_injection_row(
            engine,
            parent_instance_id=parent,
            report_message_id=report_msg_id,
            state=ReportInjectionState.PENDING.value,
        )

        # Seed PROCESS_MESSAGE: message PROCESSING, task PENDING,
        # no injection row.
        msg_id, task_id = _seed_message_and_task(
            engine,
            instance_id=parent,
            task_type=TaskType.PROCESS_MESSAGE.value,
            msg_status=MessageStatus.PROCESSING.value,
            msg_type=MessageType.HUMAN.value,
        )

        # Build the manager with REAL repositories against the engine
        # — the FM-1 cleanup loop reads/writes through these.
        manager = _build_minimal_manager(engine)
        manager._queue_repository = SQLModelMessageQueueRepository(
            engine=engine
        )
        manager._task_repo = TaskRepository(
            engine=engine,
            on_pending_task=lambda: None,
        )
        # Bind the REAL async method (not a MagicMock) — the
        # existing helper-only tests already use this pattern via
        # ``__get__``; the real-loop test needs the real async
        # method bound so the actual FM-1 cleanup loop runs.
        manager._schedule_explicit_handle_resume = (
            InstanceManager._schedule_explicit_handle_resume.__get__(
                manager
            )
        )

        # Mock the request registry — the resume path registers a
        # CancellationTokenSource but never reads it for the FM-1
        # cleanup. We mock to avoid wiring a real registry.
        token_source = CancellationTokenSource()
        manager._request_registry = MagicMock()
        manager._request_registry.register = MagicMock(
            return_value=token_source
        )

        # Mock the background processing task — we want to observe
        # the FM-1 cleanup outcome without actually starting the
        # graph turn. The cleanup runs to completion BEFORE the
        # background task is created, so this mock only needs to
        # exist (asyncio.create_task will schedule and immediately
        # cancel it via the test's event loop teardown).
        async def _noop_bg(*args, **kwargs):
            return None

        manager._resume_processing_background = _noop_bg
        manager._graph_tasks = {}

        # Drive the REAL FM-1 cleanup loop.
        result = await manager._schedule_explicit_handle_resume(
            instance_id=parent,
            message="resume trigger",
            silent=True,
            images=None,
            target_work_id="work-test",
            selected_suspension_reason="test",
            handle_work_id="work-handle",
            route_outcome="test_route",
        )
        # The cleanup returns the "resuming" envelope — we don't
        # assert on the envelope shape; the assertions below are
        # the real proof.
        assert result["status"] == "resuming"

        # ASSERTIONS: PROCESS_REPORT survives, PROCESS_MESSAGE is
        # cancelled + completed.
        with Session(engine) as session:
            # PROCESS_REPORT task still PENDING (the FM-1 guard
            # exempted it because injection row is non-terminal).
            sm_task = sm_select(Task).where(Task.id == report_task_id)
            survived_task = session.exec(sm_task).first()
            assert survived_task.status == TaskStatus.PENDING.value, (
                "PROCESS_REPORT + non-terminal injection must SURVIVE "
                "FM-1 cleanup (post-review predicate); got "
                f"task.status={survived_task.status}"
            )

            # PROCESS_REPORT message still PROCESSING (the FM-1
            # guard exempted the row from the complete() call too).
            sm_msg = sm_select(MessageQueue).where(
                MessageQueue.message_id == report_msg_id
            )
            survived_msg = session.exec(sm_msg).first()
            assert survived_msg.status == MessageStatus.PROCESSING.value, (
                "PROCESS_REPORT message must remain PROCESSING "
                "(FM-1 guard exempts the cancel+complete pair); got "
                f"msg.status={survived_msg.status}"
            )

            # PROCESS_MESSAGE task CANCELLED (cancel+complete path).
            sm_task2 = sm_select(Task).where(Task.id == task_id)
            cancelled_task = session.exec(sm_task2).first()
            assert cancelled_task.status == TaskStatus.CANCELLED.value, (
                "PROCESS_MESSAGE must be CANCELLED by FM-1 cleanup "
                "(no exemption — guard keys on PROCESS_REPORT only); "
                f"got task.status={cancelled_task.status}"
            )

            # PROCESS_MESSAGE message COMPLETED (the message was
            # PROCESSING so ``complete()`` succeeded; for RETRYING
            # messages the complete() call no-ops per the guarded
            # UPDATE — see W3 follow-up).
            sm_msg2 = sm_select(MessageQueue).where(
                MessageQueue.message_id == msg_id
            )
            completed_msg = session.exec(sm_msg2).first()
            assert completed_msg.status == MessageStatus.COMPLETED.value, (
                "PROCESS_MESSAGE message must be COMPLETED by FM-1 "
                "cleanup; got "
                f"msg.status={completed_msg.status}"
            )

    @pytest.mark.asyncio
    async def test_real_loop_lookup_error_preserves_task(
        self, engine: Engine
    ) -> None:
        """D1 (2026-08-20): when the injection-row LOOKUP RAISES
        (transient DB error), the FM-1 guard MUST exempt the
        PROCESS_REPORT task — it is PRESERVED (still PENDING, its
        message still PROCESSING), NOT cancelled+completed.

        Pre-D1 the lookup-error default was ``False`` (not exempt)
        → FM-1 killed the freshly-swept PENDING report task on a
        transient error, recreating the incident variant (c)
        freeze. D1 flips the default to True: passive+recoverable
        beats destructive+recoverable — a false exemption just
        leaves the task to the worker pool / claim lane.
        """
        from daemon.cancellation import CancellationTokenSource
        from daemon.manager import InstanceManager
        from daemon.repositories.message_queue.repository import (
            SQLModelMessageQueueRepository,
        )
        from daemon.repositories.task.repository import TaskRepository

        parent = _seed_instance(engine)

        # The realistic freeze scenario: PROCESS_REPORT task PENDING
        # on a PROCESSING completion_report message, plus a
        # non-terminal injection row (seeded so the row EXISTS —
        # the lookup-error path is forced below by raising).
        report_msg_id, report_task_id = _seed_message_and_task(
            engine,
            instance_id=parent,
            task_type=TaskType.PROCESS_REPORT.value,
            msg_status=MessageStatus.PROCESSING.value,
            msg_type=MessageType.COMPLETION_REPORT.value,
        )
        _seed_injection_row(
            engine,
            parent_instance_id=parent,
            report_message_id=report_msg_id,
            state=ReportInjectionState.PENDING.value,
        )

        manager = _build_minimal_manager(engine)
        manager._queue_repository = SQLModelMessageQueueRepository(
            engine=engine
        )
        manager._task_repo = TaskRepository(
            engine=engine,
            on_pending_task=lambda: None,
        )
        manager._schedule_explicit_handle_resume = (
            InstanceManager._schedule_explicit_handle_resume.__get__(
                manager
            )
        )

        token_source = CancellationTokenSource()
        manager._request_registry = MagicMock()
        manager._request_registry.register = MagicMock(
            return_value=token_source
        )

        async def _noop_bg(*args, **kwargs):
            return None

        manager._resume_processing_background = _noop_bg
        manager._graph_tasks = {}

        # FORCE the lookup error: the repo's find_row_by_report_
        # message_id raises for every call — the helper's except
        # branch (D1) fires.
        def _raise_lookup(*_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError("simulated transient DB error")

        manager._report_injection_repo.find_row_by_report_message_id = (
            MagicMock(side_effect=_raise_lookup)
        )

        # Drive the REAL FM-1 cleanup loop.
        result = await manager._schedule_explicit_handle_resume(
            instance_id=parent,
            message="resume trigger",
            silent=True,
            images=None,
            target_work_id="work-test",
            selected_suspension_reason="test",
            handle_work_id="work-handle",
            route_outcome="test_route",
        )
        assert result["status"] == "resuming"

        # D1 assertion: the PROCESS_REPORT task is PRESERVED.
        with Session(engine) as session:
            sm_task = sm_select(Task).where(Task.id == report_task_id)
            survived_task = session.exec(sm_task).first()
            assert survived_task.status == TaskStatus.PENDING.value, (
                "D1: lookup error MUST exempt (preserve) the PENDING "
                "PROCESS_REPORT task; got "
                f"task.status={survived_task.status}"
            )

            sm_msg = sm_select(MessageQueue).where(
                MessageQueue.message_id == report_msg_id
            )
            survived_msg = session.exec(sm_msg).first()
            assert survived_msg.status == MessageStatus.PROCESSING.value, (
                "D1: lookup error MUST exempt the message from the "
                "complete() call too; got "
                f"msg.status={survived_msg.status}"
            )
