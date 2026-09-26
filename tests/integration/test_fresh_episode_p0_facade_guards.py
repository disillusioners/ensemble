"""P0 review-2 regression GUARDS (findings 8-9) for
``is_fresh_episode_user_message``.

Background
----------
The P0 hotfix (commits 118bd45c + be0a3794 on top of 416ae70d)
threads the ledger-derived fresh-episode flag across the
enqueue→process boundary via ``ProcessingContext``. The flag is
computed at the canonical construction site
(``daemon/services/task_processor.py``:551-554) from the persisted
``MessageQueue`` row's ``priority`` + ``type`` columns using the
EXACT same logic the ledger uses at
``daemon/services/instance_messaging.py``:2009-2012:

    is_fresh_episode_user_message = (
        message.priority == 1
        and message.type == MessageType.HUMAN.value
    )

Independent review flagged TWO test holes after cycle-2 / cycle-3:

  * Finding 8 (FACADE-FORWARDING) — the existing 13-test integration
    file (``test_fresh_episode_attestation_reset_p0.py``) drives the
    SERVICE directly via ``_process_message_with_tracking`` and would
    stay GREEN if someone dropped the facade-forwarding kwarg at
    ``manager.py``:7389 OR the pipeline-forwarding kwarg at
    ``message_processing_pipeline.py``:449. The bug class is
    documented in BLUEPRINT Core Architecture §Facade-Forwarding
    Discipline: "InstanceManager = manual-forwarding facade; new
    kwargs need facade-forwarding check + real-dispatch integration
    test — AsyncMock + inspect.getsource substring assertions stay
    green at the seam."

  * Finding 9 (REAL-COMPUTATION-SITE) — the existing
    ``test_persisted_row_priority_5_stamps_false`` RE-DERIVES the
    computation locally and passes the local value to the consumer
    seam. A copy-paste typo in the production expression (e.g.
    ``== 5`` instead of ``== 1``) would be INVISIBLE to that test
    — both the local re-derivation AND the production site would
    compute the same (wrong) value.

This file pins BOTH forwarding hops and the REAL computation site
with mangle-matrix self-proof (mangle → RED → restore → GREEN,
``git diff`` clean of the production code).

Reference pattern
-----------------
``tests/unit/test_manager_enqueue_message_work_id_required.py``
guards the same facade seam for a DIFFERENT kwarg. It deliberately
uses ``AsyncMock`` spy + ``assert_awaited_once_with`` on the service
seam (NOT ``inspect.getsource`` substring pins, which stay green at
the facade seam by design). The two-hop guard at the pipeline seam
uses the same discipline: a real ``ExecutionGateService`` so the
work_fn is actually awaited, and a spied manager facade so the
forwarded kwarg is the assertion target.

The real-computation-site guard drives the REAL
``ProcessMessageProcessor.process()`` method with a stubbed
``pipeline.execute`` that captures the ``ProcessingContext`` it
receives. The canonical computation at ``task_processor.py``:551-554
runs unchanged; we read the flag off the captured context — if the
production expression is mangled, the flag value changes and the
test goes RED.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel

# Register every model so ``SQLModel.metadata.create_all`` builds the
# full schema (matches the existing integration recipe; without these
# imports the Instance / Project / MessageQueue / Task tables are
# absent and the repositories raise
# ``sqlalchemy.exc.OperationalError``).
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.project.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401

from daemon.manager import InstanceManager, MessageResult
from daemon.services.execution_gate import ExecutionGateService
from daemon.services.message_processing_pipeline import (
    MessageProcessingPipeline,
    PipelineCallbacks,
    ProcessingContext,
    ProcessingResult,
)
from daemon.services.task_processor import ProcessMessageProcessor
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.message_queue.models import (
    MessageQueue,
    MessageStatus,
    MessageType,
)
from daemon.repositories.project.models import Project, ProjectStatus
from daemon.repositories.task.models import Task, TaskStatus, TaskType


INSTANCE_ID = "iid-p0-ctx-rev2-guard-7d4a3bd9"
PROJECT_ID = "proj-p0-ctx-rev2-guard"


# ─── DB recipe (file-backed SQLite per BLUEPRINT §3) ─────────────────────────


@pytest.fixture
def engine(tmp_path) -> Engine:
    """File-backed SQLite at ``tmp_path`` (NullPool + FK on + WAL).

    BLUEPRINT §3 recipe — ``NullPool`` + file-backed SQLite at
    ``tmp_path`` + ``PRAGMA journal_mode=WAL`` +
    ``PRAGMA busy_timeout=10000`` + foreign-keys ON.
    """
    db_path = tmp_path / "p0_ctx_rev2_guard.db"
    eng = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )

    @event.listens_for(eng, "connect")
    def _enable(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=10000")
        cursor.close()

    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


# ─── Seed helpers ─────────────────────────────────────────────────────────────


def _seed_project(engine: Engine) -> None:
    now_iso = "2026-09-26T00:00:00+00:00"
    with Session(engine) as session:
        session.add(
            Project(
                project_id=PROJECT_ID,
                name="p0-ctx-rev2-guard",
                project_type="software",
                status=ProjectStatus.ACTIVE.value,
                description=(
                    "P0 regression — review-2 facade + real-site guards"
                ),
                project_metadata={},
                relationships={},
                created_at=now_iso,
                updated_at=now_iso,
            )
        )
        session.commit()


def _seed_instance(engine: Engine) -> None:
    """Insert the Instance row that the message's ``instance_id`` FK
    requires (MessageQueue.instance_id → Instance.instance_id).
    """
    now_iso = "2026-09-26T00:00:00+00:00"
    now_naive = datetime(2026, 9, 26, 0, 0, 0)
    with Session(engine) as session:
        session.add(
            Instance(
                instance_id=INSTANCE_ID,
                agent_id="worker",
                agent_dir="./agents/worker",
                status=InstanceStatus.IDLE.value,
                parent_id=None,
                project_id=PROJECT_ID,
                project_injected=False,
                instance_metadata={"project_id": PROJECT_ID},
                created_at=now_iso,
                updated_at=now_iso,
                last_activity_at=now_naive,
            )
        )
        session.commit()


def _seed_message(
    engine: Engine,
    message_id: str,
    message_type: str,
    priority: int,
) -> None:
    """Insert the MessageQueue row the canonical claim path reads
    to compute ``is_fresh_episode_user_message``. Status is
    ``PROCESSING`` (not ``COMPLETED``) so the processor's
    idempotency-guard skip at ``task_processor.py``:488 doesn't
    short-circuit before the canonical computation runs.
    """
    with Session(engine) as session:
        session.add(
            MessageQueue(
                message_id=message_id,
                instance_id=INSTANCE_ID,
                content="hello",
                type=message_type,
                source=None,
                root_source=None,
                status=MessageStatus.PROCESSING.value,
                priority=priority,
                enqueued_at=datetime.now().replace(tzinfo=None),
            )
        )
        session.commit()


# ─── Task 1 — Facade-forwarding guard (finding 8) ─────────────────────────────
#
# Hop (a): InstanceManager._process_message_with_tracking → InstanceMessagingService._process_message_with_tracking
#          — guard at manager.py:7389 (the forwarding kwarg the facade
#            method passes into ``self._messaging_service._process_message_with_tracking``)
# Hop (b): MessageProcessingPipeline._do_process → InstanceManager._process_message_with_tracking
#          — guard at message_processing_pipeline.py:449 (the kwarg the
#            pipeline forwards from ProcessingContext into the manager
#            facade call)


def _facade_with_service_spy() -> tuple[InstanceManager, AsyncMock]:
    """Build a bare facade whose service seam is spied.

    Mirrors ``tests/unit/test_manager_enqueue_message_work_id_required.py``
    exactly — the established pattern in this repo for facade-seam
    guards. ``InstanceManager.__new__(InstanceManager)`` skips
    ``__init__`` (the pattern from
    ``tests/unit/test_phase4_manager_decomposition.py``). The spy
    replaces the whole ``_messaging_service`` OBJECT, with an
    ``AsyncMock`` standing in for ``_process_message_with_tracking``.
    The facade calls
    ``self._messaging_service._process_message_with_tracking(...)``
    so that method-level mock is what records (and forwards) the
    kwargs.

    Why this pattern (not ``inspect.getsource`` substring pins):
    the bug class documented in BLUEPRINT Core Architecture §Facade-
    Forwarding Discipline is that substring assertions stay green at
    this seam — a dropped forwarding line still has the keyword
    present somewhere in the source. The AsyncMock + ``await_args``
    technique records the ACTUAL call kwargs at the receiving
    function, so a missing kwarg surfaces immediately as a key
    absence / wrong value.
    """
    manager = InstanceManager.__new__(InstanceManager)
    service = MagicMock()
    process_spy = AsyncMock(
        return_value=MessageResult(content="ok")
    )
    service._process_message_with_tracking = process_spy
    manager._messaging_service = service
    return manager, process_spy


class TestManagerFacadeForwardsKwarg:
    """Hop (a): ``InstanceManager._process_message_with_tracking`` MUST
    forward the ``is_fresh_episode_user_message`` kwarg to the
    service-layer
    ``InstanceMessagingService._process_message_with_tracking``
    (``daemon/manager.py``:7389).

    The bug class is documented in BLUEPRINT §Facade-Forwarding
    Discipline: drop the forwarding line at the facade and every
    call site dies before the contract is reachable. This test
    bites that class.
    """

    async def test_forwards_true_to_service(self):
        """A real call with ``is_fresh_episode_user_message=True`` must
        reach the service spy with the SAME value (no TypeError, no
        default-False substitution). Mangle target: comment out the
        ``is_fresh_episode_user_message=is_fresh_episode_user_message``
        line at ``manager.py``:7389 — this assertion goes RED.
        """
        manager, spy = _facade_with_service_spy()

        await manager._process_message_with_tracking(
            instance_id=INSTANCE_ID,
            message="hello",
            message_id="msg-1",
            is_fresh_episode_user_message=True,
        )

        spy.assert_awaited_once()
        forwarded = spy.await_args.kwargs
        assert (
            forwarded.get("is_fresh_episode_user_message") is True
        ), (
            f"manager.py:7389 dropped or wrongly forwarded the "
            f"``is_fresh_episode_user_message`` kwarg; service "
            f"received {forwarded.get('is_fresh_episode_user_message')!r}"
        )

    async def test_forwards_false_default(self):
        """Omitting the kwarg forwards ``False`` — the cascade-resume
        direct-dispatch site at ``manager.py``:10767-10786 explicitly
        passes False (a PAUSED→RUNNING resume is NOT a fresh episode)
        and ad-hoc test paths rely on the default. Mangle target:
        same line at ``manager.py``:7389 — this assertion goes RED.
        """
        manager, spy = _facade_with_service_spy()

        await manager._process_message_with_tracking(
            instance_id=INSTANCE_ID,
            message="hello",
            message_id="msg-1",
        )

        spy.assert_awaited_once()
        forwarded = spy.await_args.kwargs
        assert (
            forwarded.get("is_fresh_episode_user_message") is False
        ), (
            f"manager.py:7389 default-forward must be False (the "
            f"kwarg is keyword-only on the facade signature); got "
            f"{forwarded.get('is_fresh_episode_user_message')!r}"
        )


class TestPipelineForwardsKwargToManagerFacade:
    """Hop (b): ``MessageProcessingPipeline._do_process`` MUST thread
    ``is_fresh_episode_user_message`` from ``ProcessingContext`` into
    the manager facade call
    (``daemon/services/message_processing_pipeline.py``:449).

    The pipeline builds ``ProcessingContext`` from the canonical
    claim path (task_processor) and forwards each field by name into
    ``manager._process_message_with_tracking(...)``. A drop here
    silently reduces the carrier kwarg to its default False for
    every claim-path message, breaking the ledger parity the hotfix
    is supposed to restore.
    """

    async def test_pipeline_forwards_context_kwarg_to_manager(self):
        """The pipeline's ``_do_process`` closure must pull
        ``context.is_fresh_episode_user_message`` (the P0 carrier
        kwarg) into the ``manager._process_message_with_tracking``
        call.

        Mangle target: replace
        ``is_fresh_episode_user_message=context.is_fresh_episode_user_message``
        at ``message_processing_pipeline.py``:449 with a literal
        ``False`` — this assertion goes RED.
        """
        # Real ExecutionGateService (uses per-instance asyncio.Lock)
        # so ``work_fn`` is actually awaited via the asyncio.Lock
        # gate. The only thing we spy is the manager facade that
        # ``_do_process`` calls.
        gate = ExecutionGateService()

        # Manager facade stub: only ``_process_message_with_tracking``
        # is spied (the assertion target). ``_instance_repository``
        # is ``None`` so the defensive ``_is_instance_paused`` short-
        # circuit at ``message_processing_pipeline.py``:1029 skips.
        # Other ``getattr`` reads return Mock auto-specs that don't
        # surface real side-effects.
        stub_manager = MagicMock()
        manager_spy = AsyncMock(
            return_value=MessageResult(content="ok")
        )
        stub_manager._process_message_with_tracking = manager_spy
        stub_manager._instance_repository = None
        stub_manager._process_child_completion_and_notify_parent = (
            None
        )
        stub_manager._live_hub = None
        stub_manager._job_feedback_observer = None

        pipeline = MessageProcessingPipeline(
            execution_gate=gate,
            manager=stub_manager,
            source_dispatcher=None,  # _dispatch_completed skip
            queue_repository=None,   # _claim + _mark_completed skip
        )

        ctx = ProcessingContext(
            instance_id=INSTANCE_ID,
            message_id="msg-pipeline-1",
            message="hello",
            message_source="api",
            # The flag we want to see forwarded verbatim into the
            # manager facade.
            is_fresh_episode_user_message=True,
        )
        callbacks = PipelineCallbacks(
            on_success=AsyncMock(),
            on_error=AsyncMock(),
            on_contention=None,
            on_cancel=None,
        )

        await pipeline.execute(
            context=ctx,
            holder_id="task:test-1",
            holder_kind="task",
            callbacks=callbacks,
            error_handler_id={"task_id": "1"},
        )

        manager_spy.assert_awaited_once()
        forwarded = manager_spy.await_args.kwargs
        assert (
            forwarded.get("is_fresh_episode_user_message") is True
        ), (
            f"message_processing_pipeline.py:449 dropped or wrongly "
            f"forwarded the ``is_fresh_episode_user_message`` kwarg; "
            f"manager facade received "
            f"{forwarded.get('is_fresh_episode_user_message')!r}"
        )


# ─── Task 2 — Real-computation-site guard (finding 9) ───────────────────────
#
# The canonical computation lives at ``task_processor.py``:551-554:
#
#     is_fresh_episode_user_message = False
#     if message is not None:
#         from daemon.repositories.message_queue.models import (
#             MessageType,
#         )
#         is_fresh_episode_user_message = (
#             message.priority == 1
#             and message.type == MessageType.HUMAN.value
#         )
#
# The existing ``test_persisted_row_priority_5_stamps_false``
# RE-DERIVES this expression locally and would NOT catch a
# copy-paste typo in the production code. This test exercises the
# REAL production path via ``ProcessMessageProcessor.process()``
# with a stubbed ``pipeline.execute`` that captures the
# ``ProcessingContext`` it receives.


def _build_real_site_processor(
    engine: Engine,
) -> tuple[ProcessMessageProcessor, dict]:
    """Build a ``ProcessMessageProcessor`` whose ``pipeline.execute``
    is a stub that captures the ``ProcessingContext`` it receives.

    The processor's ``process()`` method runs the REAL canonical
    computation at ``task_processor.py``:551-554 unchanged and
    passes the result via
    ``ProcessingContext.is_fresh_episode_user_message``. We
    intercept the context BEFORE the pipeline runs to verify the
    flag is what the production code computed.

    The DB lookup (``self._message_repo.get``) is wired to the
    file-backed SQLite ``engine`` so the ``message.priority`` and
    ``message.type`` the production code reads come from a real
    persisted row, not a hand-built Mock.
    """
    captured: dict = {}

    async def _stub_execute(*args, **kwargs):
        captured["context"] = kwargs.get("context")
        return ProcessingResult(success=True, result_content="ok")

    stub_pipeline = MagicMock()
    stub_pipeline.execute = AsyncMock(side_effect=_stub_execute)

    # The processor reaches ``self._message_repo.get`` at
    # ``task_processor.py``:410 — wrapped in ``asyncio.to_thread``
    # (a SYNC function is required, not a coroutine). We stub it to
    # return the real MessageQueue row from the file-backed SQLite
    # ``engine`` so the canonical computation reads real
    # ``priority`` + ``type`` values, not a Mock's auto-spec.
    SessionLocal = sessionmaker(bind=engine)

    def _get_msg_sync(message_id):
        with SessionLocal() as s:
            return s.get(MessageQueue, message_id)

    msg_repo = MagicMock()
    msg_repo.get = _get_msg_sync

    processor = ProcessMessageProcessor(
        instance_manager=MagicMock(),  # _report_injection_repo read at :440 is skipped (PROCESS_MESSAGE)
        task_repo=MagicMock(),
        event_repo=MagicMock(),
        message_repository=msg_repo,
        source_dispatcher=MagicMock(),
        pipeline=stub_pipeline,
        work_resolver=MagicMock(),
        watcher_repo=MagicMock(),
    )
    return processor, captured


class TestRealCanonicalComputation:
    """Finding 9: the REAL canonical computation at
    ``daemon/services/task_processor.py``:551-554 —

        is_fresh_episode_user_message = (
            message.priority == 1
            and message.type == MessageType.HUMAN.value
        )

    — must produce the right flag for a real persisted MessageQueue
    row.

    The existing ``test_persisted_row_priority_5_stamps_false``
    RE-DERIVES this expression locally and would NOT catch a
    copy-paste typo in the production code (e.g. ``== 5`` instead
    of ``== 1``). This test exercises the REAL production path
    via ``ProcessMessageProcessor.process()`` with a stubbed
    ``pipeline.execute`` that captures the ``ProcessingContext``
    it receives — the canonical computation runs UNCHANGED, we
    read the flag off the captured context.
    """

    async def test_priority_1_human_yields_true(self, engine: Engine):
        """A user-API row with ``priority=1`` + ``type=HUMAN`` MUST
        compute ``is_fresh_episode_user_message=True`` — the
        canonical fresh-episode path.

        Mangle target: flip ``message.priority == 1`` to
        ``message.priority == 5`` at ``task_processor.py``:552 —
        this assertion goes RED.
        """
        _seed_project(engine)
        _seed_instance(engine)
        message_id = "msg-real-p1-human"
        _seed_message(
            engine, message_id, MessageType.HUMAN.value, priority=1
        )

        processor, captured = _build_real_site_processor(engine)
        task = Task(
            id=101,
            task_type=TaskType.PROCESS_MESSAGE.value,
            instance_id=INSTANCE_ID,
            message_id=message_id,
            status=TaskStatus.PENDING.value,
            retry_count=0,
        )

        await processor.process(task)

        ctx = captured.get("context")
        assert ctx is not None, (
            "process() never reached pipeline.execute; the canonical "
            "computation at task_processor.py:551-554 did not run."
        )
        assert ctx.is_fresh_episode_user_message is True, (
            f"REAL canonical computation returned "
            f"{ctx.is_fresh_episode_user_message!r} for "
            f"(priority=1, type=HUMAN); expected True. A copy-paste "
            f"typo at task_processor.py:552 (e.g. ``== 5``) would "
            f"land here as False — RED."
        )

    async def test_priority_5_human_yields_false(self, engine: Engine):
        """A scheduler row with ``priority=5`` + ``type=HUMAN`` MUST
        compute ``is_fresh_episode_user_message=False`` — the
        priority gate keeps the sentinel OFF even though the type
        is HUMAN.

        This is the exact case the cycle-2 ad-hoc re-derivation
        MISSED (priority was not in scope at the consumer seam).
        The REAL canonical computation now sees priority and stamps
        False here; a copy-paste typo (``== 5`` instead of ``== 1``)
        would wrongly stamp True — this test would RED.

        This is the STRONGEST guard against the typo class: it
        exercises BOTH the priority arm AND the conjunction.
        """
        _seed_project(engine)
        _seed_instance(engine)
        message_id = "msg-real-p5-human"
        _seed_message(
            engine, message_id, MessageType.HUMAN.value, priority=5
        )

        processor, captured = _build_real_site_processor(engine)
        task = Task(
            id=102,
            task_type=TaskType.PROCESS_MESSAGE.value,
            instance_id=INSTANCE_ID,
            message_id=message_id,
            status=TaskStatus.PENDING.value,
            retry_count=0,
        )

        await processor.process(task)

        ctx = captured.get("context")
        assert ctx is not None, (
            "process() never reached pipeline.execute; the canonical "
            "computation at task_processor.py:551-554 did not run."
        )
        assert ctx.is_fresh_episode_user_message is False, (
            f"REAL canonical computation returned "
            f"{ctx.is_fresh_episode_user_message!r} for "
            f"(priority=5, type=HUMAN); expected False. A copy-paste "
            f"typo at task_processor.py:552 (e.g. ``== 5``) would "
            f"land here as True — RED."
        )

    async def test_priority_1_agent_yields_false(self, engine: Engine):
        """An internal_agent row with ``priority=1`` + ``type=AGENT``
        MUST compute ``is_fresh_episode_user_message=False`` — the
        ``type==HUMAN`` half of the conjunction fails.

        Mangle target: drop the ``type==HUMAN.value`` check
        (``message.priority == 1`` alone) at ``task_processor.py``:
        552-553 — this assertion goes RED.
        """
        _seed_project(engine)
        _seed_instance(engine)
        message_id = "msg-real-p1-agent"
        _seed_message(
            engine, message_id, MessageType.AGENT.value, priority=1
        )

        processor, captured = _build_real_site_processor(engine)
        task = Task(
            id=103,
            task_type=TaskType.PROCESS_MESSAGE.value,
            instance_id=INSTANCE_ID,
            message_id=message_id,
            status=TaskStatus.PENDING.value,
            retry_count=0,
        )

        await processor.process(task)

        ctx = captured.get("context")
        assert ctx is not None, (
            "process() never reached pipeline.execute; the canonical "
            "computation at task_processor.py:551-554 did not run."
        )
        assert ctx.is_fresh_episode_user_message is False, (
            f"REAL canonical computation returned "
            f"{ctx.is_fresh_episode_user_message!r} for "
            f"(priority=1, type=AGENT); expected False. Dropping "
            f"the ``type==HUMAN`` half of the conjunction at "
            f"task_processor.py:553 would land here as True — RED."
        )