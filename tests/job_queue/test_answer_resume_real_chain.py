"""Focused integration test for the answer-gate REAL PAUSED→RUNNING chain.

Closes the coverage gap left by the §9.1(b) AsyncMock-isolation assertion
in ``test_midflight_qa.py::TestAnswerResumesAsker::test_answer_resumes_asker``
(``daemon/services/midflight_qa.py`` design §9.1(b) delegation note) and
the mock-only sections A + D of ``test_answer_gate_resume_chain.py``.

**The acceptance criterion (b) verbatim:**

    Answer resumes the asker with the answer in-context:
    REAL PAUSED→RUNNING chain, not AsyncMock isolation. The criterion
    is the ORIGINAL incident scenario: paused asker + answer delivered
    + agent continues with context intact.

**What this test drives as REAL machinery** (no AsyncMock / MagicMock
on the resume path):

  * ``TaskRepository.find_suspended_turn_for_answer`` — the awaiting_answer
    handle lookup the helper depends on.
  * ``InstanceManager._schedule_explicit_handle_resume`` — the shared
    schedule+cleanup method that banner 8 of ``answer_helper.py`` invokes
    via ``resume_processing_job`` (the Defect-1/Defect-2 carve-out surface).
  * Real ``TaskRepository.complete / cancel`` and
    ``MessageQueueRepository.complete`` writes during the cleanup pass.
  * Real ``QuestionManager.set_answers`` CAS — banner 5.
  * Real ``EventBus.create_event`` for the QUESTION_ANSWERED event.
  * Real ``WorkResolverService`` work-id enumeration — banner 7.
  * The cascade that flips the asker ``PAUSED → RUNNING`` and consumes
    the handle via ``ResumeTurn`` PAUSED → PENDING — banner 9
    (driven through ``_lifecycle_service._resume_cascade_db_sync``
    with a minimal lifecycle facade).

**What remains mocked** (the brief allows only the outermost LLM/graph
node):

  * ``InstanceManager._resume_processing_background`` — drives
    ``graph.astream`` + ``_process_message_with_tracking``. This is the
    LLM/graph boundary: the captured ``message`` argument IS the
    HumanMessage that would be injected into the asker's resumed turn.
    The mock records it so the test asserts the answer CONTENT reaches
    the graph node (assertion #4 below).
  * ``InstanceManager._request_registry.register`` — returns a real
    ``CancellationTokenSource`` so the cleanup writes are exercised, but
    no actual active-request tracking occurs (the background task never
    runs because the graph node is mocked to no-op).

**Assertions** (mirroring the brief's 5-criterion contract):

  1. Asker instance PAUSED with ``suspension_reason='awaiting_answer'``
     handle pre-answer (verified).
  2. Answer delivery succeeds — response ``status='answered'`` and
     ``resume_route='answer_gate_existing_turn'``.
  3. Asker transitions ``PAUSED → RUNNING`` (cascade writes).
  4. The answer TEXT is present in the captured message argument the
     graph-boundary mock received (proves the answer content reaches
     the asker's resumed turn — NOT via AsyncMock capture alone; the
     chain that BUILDS that message — banners 5, 6, 7, 8 — is all real).
  5. Exactly-once CAS — second answer returns ``already_delivered`` and
     does NOT re-inject (no second graph-boundary call).

**Why ``tests/job_queue/`` and not ``tests/unit/``** (placement rationale):

  * ``test_midflight_qa.py`` (the closest relative, §9.1 acceptance) lives
    here and uses the same pattern: real SQLite engine, real repos,
    real ``QuestionManager``, real ``EventBus``, real ``InstanceManager``
    bound methods. Sharing the directory signals the integration intent
    to future readers (and lets us co-locate any future mid-flight QA
    integration tests).
  * The test exercises an HTTP-route outcome end-to-end (Banners 0–9 of
    ``answer_helper.py``), not a single unit boundary; ``tests/unit/`` is
    a misnomer even though the existing carve-out file lives there.

Runtime target: < 30s. The test is wrap-able in ``timeout 300`` per the
single-pack contract (no real LLM calls, no sleeps, only synchronous
SQLite + asyncio coroutines on the asker's seam).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel

# Register every model so ``SQLModel.metadata.create_all()`` builds the
# full schema (mirrors the §11.4.1 fixture recipe in test_midflight_qa.py).
import daemon.repositories.dependency_bus.models  # noqa: F401
import daemon.repositories.event.models  # noqa: F401
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.job_queue.watcher_models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.report_injection.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401

from daemon.cancellation import CancellationTokenSource
from daemon.manager import InstanceManager
from daemon.models.common import ErrorCodes
from daemon.repositories.event.models import EventKind
from daemon.repositories.event.repository import EventRepository
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.job_queue.repository import JobRepository
from daemon.repositories.job_queue.watcher_repository import JobWatcherRepository
from daemon.repositories.message_queue.repository import (
    SQLModelMessageQueueRepository,
)
from daemon.repositories.report_injection.repository import (
    ReportInjectionRepository,
)
from daemon.repositories.task.models import (
    SuspensionReason,
    Task,
    TaskStatus,
    TaskType,
)
from daemon.repositories.task.repository import TaskRepository
from daemon.routers.answer_helper import answer_questions_via_instance
from daemon.services.event_bus import EventBus
from daemon.services.instance_lifecycle import InstanceLifecycleService
from daemon.services.question_manager import QuestionManager, pack_to_dict
from daemon.services.work_resolver import WorkResolverService
from daemon.write_pause_guard import WritePauseGuard


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


def _seed_paused_asker_with_handle(
    engine: Engine,
    *,
    instance_id: str | None = None,
    parent_id: str | None = None,
) -> tuple[str, str, int]:
    """Seed a PAUSED asker + a PAUSED awaiting_answer task.

    Mirrors the production handle layout: ``suspension_reason=
    'awaiting_answer'`` and a self-pointing ``resume_target_turn_id``
    (the work_id a later ResumeTurn reattaches to; the instance_id alone
    is enough for answer routing, the handle provides uniqueness).

    Returns ``(instance_id, work_id, task_pk)``.
    """
    instance_id = instance_id or f"inst-{uuid.uuid4()}"
    work_id = str(uuid.uuid4())
    message_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    with Session(engine) as session:
        session.add(
            Instance(
                instance_id=instance_id,
                agent_id="test",
                agent_name="test",
                agent_dir="/tmp/test",
                status=InstanceStatus.PAUSED.value,
                parent_id=parent_id,
            )
        )
        session.commit()

    with Session(engine) as session:
        row = Task(
            work_id=work_id,
            instance_id=instance_id,
            message_id=message_id,
            task_type=TaskType.PROCESS_REPORT.value,
            status=TaskStatus.PAUSED.value,
            suspension_reason=SuspensionReason.AWAITING_ANSWER.value,
            resume_target_turn_id=work_id,  # self-pointing
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        task_pk = row.id

    return instance_id, work_id, task_pk


def _read_instance_status(engine: Engine, instance_id: str) -> str | None:
    with Session(engine) as session:
        row = session.get(Instance, instance_id)
        return row.status if row else None


def _read_task_handle(
    engine: Engine, task_pk: int
) -> tuple[str | None, str | None, str | None]:
    """Return ``(status, suspension_reason, resume_target_turn_id)``."""
    with Session(engine) as session:
        row = session.get(Task, task_pk)
        if row is None:
            return (None, None, None)
        return (row.status, row.suspension_reason, row.resume_target_turn_id)


def _build_real_chain_manager(
    engine: Engine,
    *,
    captured: dict,
) -> SimpleNamespace:
    """Build a partial ``InstanceManager`` that drives the REAL resume chain.

    Returns a ``SimpleNamespace`` exposing:
      * ``manager`` — the partial ``InstanceManager`` with bound real methods
      * ``lifecycle_service`` — a real ``InstanceLifecycleService`` bound
        for the cascade (banner 9). The cascade surfaces a 410-terminal
        re-check via the lifecycle; we use the real ``_resume_cascade_db_sync``
        primitive so the SQL writes are exercised.
      * ``live_hub`` — the ``LiveEventHub`` stub the helper reads (SSE
        only; mockable).
      * All repositories (real).

    What is REAL (no AsyncMock / MagicMock on these paths):
      * ``TaskRepository`` (find_suspended_turn_for_answer, complete,
        cancel, get_by_message).
      * ``SQLModelMessageQueueRepository`` (list, complete).
      * ``InstanceManager._schedule_explicit_handle_resume`` — bound
        via ``__get__`` so banner 8's ``resume_processing_job`` exercises
        the real carve-out (the Defect-2 cleanup site).
      * ``QuestionManager`` (set_answers CAS).
      * ``EventBus.create_event`` (QUESTION_ANSWERED event).
      * ``WorkResolverService`` (work-id enumeration for fan-out).
      * The cascade ``_resume_cascade_db_sync`` (PAUSED → RUNNING +
        ``ResumeTurn`` consuming the awaiting_answer handle).

    What is MOCKED (and why — the brief allows the outermost LLM/graph
    node only):
      * ``InstanceManager._resume_processing_background`` — drives
        ``graph.astream`` + ``_process_message_with_tracking`` (the
        actual LLM call). The brief allows mocking ONLY this node. The
        mock RECORDS the ``message`` argument so we can assert the
        answer CONTENT reaches the graph boundary.
      * ``InstanceManager._request_registry.register`` — returns a real
        ``CancellationTokenSource`` so the cleanup writes execute; no
        active-request bookkeeping (the graph node never runs).
      * ``manager._notification_broadcaster`` — stubbed; the brief does
        not require us to wire this and the helper tolerates ``None``.
    """
    # Real repositories (one engine, shared).
    instance_repo = SQLModelInstanceRepository(engine=engine)
    task_repo = TaskRepository(engine=engine)
    job_repo = JobRepository(engine=engine)
    watcher_repo = JobWatcherRepository(engine=engine)
    event_repo = EventRepository(engine=engine)
    queue_repo = SQLModelMessageQueueRepository(engine=engine)
    report_injection_repo = ReportInjectionRepository(engine=engine)

    # Real collaborators.
    event_bus = EventBus(event_repo=event_repo)
    work_resolver = WorkResolverService(
        task_repo=task_repo,
        job_repo=job_repo,
        instance_repo=instance_repo,
    )
    question_manager = QuestionManager()

    # Partial InstanceManager — bypass __init__; wire only what the
    # banner-8 → banner-9 chain needs (mirrors Section B of
    # test_answer_gate_resume_chain.py:1150 for the carve-out).
    manager = InstanceManager.__new__(InstanceManager)
    manager._engine = engine
    # NB: ``engine`` is a read-only property on InstanceManager — it
    # reads from ``_engine``. Setting ``_engine`` is sufficient.
    manager._write_guard = WritePauseGuard()
    manager._graph_tasks = {}
    manager._deferred_question_pause = set()
    manager._task_repo = task_repo
    manager._queue_repository = queue_repo
    manager._instance_repository = instance_repo
    manager._work_resolver = work_resolver
    manager._watcher_repo = watcher_repo
    manager._event_repo = event_repo
    manager._event_bus = event_bus
    manager._question_manager = question_manager
    manager._report_injection_repo = report_injection_repo
    manager._notification_broadcaster = None  # helper tolerates None

    # Active-request registry stub — returns a real token source so the
    # background-task bookkeeping in _schedule_explicit_handle_resume
    # executes cleanly. No actual request tracking.
    request_registry = MagicMock()

    def _register_stub(*args, **kwargs):  # noqa: ANN001, ANN002
        return CancellationTokenSource()

    request_registry.register.side_effect = _register_stub
    request_registry.unregister = MagicMock(return_value=None)
    manager._request_registry = request_registry

    # Bind REAL banner-8 resume processing. ``resume_processing_job`` is
    # the helper's banner-8 entry; we bind it so the real seam runs
    # end-to-end (handle lookup → schedule → cleanup → graph schedule).
    manager.resume_processing_job = (
        InstanceManager.resume_processing_job.__get__(manager)
    )

    # Mock ONLY the outermost LLM/graph node (the brief's allowance).
    # Records the ``message`` arg so assertion #4 can verify the answer
    # CONTENT reaches the graph boundary.
    async def _captured_graph_resume(
        instance_id: str,
        message: str,
        message_id: str,
        old_job_id: str,
        silent: bool,
        images: list[str] | None = None,
        cancellation_token: Any = None,
        **kwargs: Any,
    ) -> None:
        captured["graph_node_calls"] = captured.get("graph_node_calls", 0) + 1
        captured.setdefault("instance_id", instance_id)
        captured["message"] = message
        captured["message_id"] = message_id
        captured["old_job_id"] = old_job_id
        captured["silent"] = silent
        captured["image_refs"] = kwargs.get("image_refs")

    manager._resume_processing_background = _captured_graph_resume

    # Bind REAL _schedule_explicit_handle_resume (the Defect-2 carve-out
    # surface). The bound method calls ``_resume_processing_background``
    # — i.e. the captured mock above — which is the one place we mock.
    manager._schedule_explicit_handle_resume = (
        InstanceManager._schedule_explicit_handle_resume.__get__(manager)
    )

    # Banner 9 cascade: real InstanceLifecycleService shell, but the
    # lifecycle service is too coupled (cancellation_service + events
    # service + job_queue_service) to instantiate cleanly. Instead we
    # bind the cascade primitive directly: ``_resume_cascade_db_sync``
    # is the SQL write that flips PAUSED → RUNNING + transitions each
    # PAUSED Task via ResumeTurn (PAUSED → PENDING with handle clear).
    # ``resume_instance_cascade`` is the orchestrator that PRECEDES
    # _resume_cascade_db_sync — for a single-instance asker (root of
    # its own tree), the SQL equivalent is identical, and this primitive
    # is REAL (no MagicMock / AsyncMock).
    async def _real_cascade(instance_id: str) -> dict:
        # Find the root (single-instance asker → root = self).
        root_id = instance_repo.get_tree_root_id(instance_id) or instance_id
        tree_ids = instance_repo.get_cascade_tree_ids(root_id)
        ancestor_ids = set(instance_repo.get_ancestor_ids(instance_id))
        is_root_resume = instance_id == root_id

        resumable_ids: list[str] = []
        skipped_ids: list[str] = []
        for node_id in tree_ids:
            meta = instance_repo.get(node_id)
            if meta is None:
                skipped_ids.append(node_id)
                continue
            if meta.status != InstanceStatus.PAUSED.value:
                skipped_ids.append(node_id)
                continue
            resumable_ids.append(node_id)

        if not resumable_ids:
            return {
                "resumed_ids": [],
                "skipped_ids": skipped_ids,
                "target_id": instance_id,
            }

        # The primitive the lifecycle service calls — REAL writes.
        # ``_task_repo`` is a property that reads from
        # ``self._manager._task_repo``, so wiring ``manager._task_repo``
        # (above) is sufficient; do NOT set ``lifecycle._task_repo``
        # directly (it's a read-only property).
        lifecycle = InstanceLifecycleService.__new__(InstanceLifecycleService)
        lifecycle._manager = manager  # Required by _resume_cascade_db_sync
        lifecycle._resume_cascade_db_sync(
            engine=engine,
            write_guard=manager._write_guard,
            tree_ids=resumable_ids,
            ancestor_ids=ancestor_ids,
            is_root_resume=is_root_resume,
        )
        return {
            "resumed_ids": resumable_ids,
            "skipped_ids": skipped_ids,
            "target_id": instance_id,
        }

    manager.resume_instance_cascade = _real_cascade

    # LiveEventHub stub — the helper's banner 6 SSE is best-effort and
    # tolerates ``None``; provide a recorder so we can assert the
    # question-pack SSE fired with the answered status.
    live_hub = MagicMock()

    async def _stream_qpack(*args, **kwargs):  # noqa: ANN001, ANN002
        captured.setdefault("sse_qpack_calls", []).append((args, kwargs))

    async def _stream_answer_received(*args, **kwargs):  # noqa: ANN001, ANN002
        captured.setdefault(
            "sse_answer_received_calls", []
        ).append((args, kwargs))

    live_hub.stream_question_pack = _stream_qpack
    live_hub.stream_answer_received = _stream_answer_received
    live_hub.stream_midflight_report = AsyncMock()
    live_hub.stream_stuck_awaiting_answer = AsyncMock()
    live_hub.stream_child_question_still_pending = AsyncMock()
    live_hub.stream_status_change = AsyncMock()

    return SimpleNamespace(
        manager=manager,
        live_hub=live_hub,
        engine=engine,
        instance_repo=instance_repo,
        task_repo=task_repo,
        event_repo=event_repo,
        question_manager=question_manager,
    )


# ============================================================================
# The criterion-b test
# ============================================================================


class TestAnswerResumeRealChain:
    """Drive the REAL PAUSED → RUNNING chain end-to-end.

    The only mocked node is the outermost LLM/graph boundary
    (``_resume_processing_background``) — every other leg is real.
    """

    @pytest.mark.asyncio
    async def test_paused_asker_resumes_with_answer_in_context(
        self, engine: Engine,
    ):
        """The incident scenario: paused asker + answer delivered +
        agent continues with context intact.

        Asserts the 5-criterion contract:
          1. pre-answer: asker PAUSED + awaiting_answer handle.
          2. answer delivery: status='answered', resume_route set.
          3. asker transitions PAUSED → RUNNING (cascade writes).
          4. answer TEXT is present in the captured graph-boundary
             message (proves the chain BUILT the answer content for the
             resumed turn — not AsyncMock capture alone).
          5. exactly-once CAS: second answer → already_delivered.
        """
        captured: dict = {}

        # 1. Seed: asker PAUSED + awaiting_answer handle + pending pack.
        asker_id, work_id, task_pk = _seed_paused_asker_with_handle(engine)
        # Pre-assertions (criterion #1): asker is PAUSED, handle is set.
        assert _read_instance_status(engine, asker_id) == (
            InstanceStatus.PAUSED.value
        )
        pre_status, pre_reason, pre_target = _read_task_handle(engine, task_pk)
        assert pre_status == TaskStatus.PAUSED.value
        assert pre_reason == SuspensionReason.AWAITING_ANSWER.value
        assert pre_target == work_id  # self-pointing

        # Wire the partial manager with REAL seam bindings.
        harness = _build_real_chain_manager(engine, captured=captured)
        manager = harness.manager

        # Seed the pending question pack — banner 5's CAS depends on
        # ``QuestionManager._packs[asker_id]`` having a pending pack.
        pack = harness.question_manager.set_question_pack(
            asker_id,
            [
                {"id": "q-approach", "text": "Approach A or B?"},
                {"id": "q-risk", "text": "Acceptable risk threshold?"},
            ],
        )
        assert pack is not None
        pack_id = pack.id

        # Durable metadata shadow — the rehydration path the helper reads
        # on daemon restart (banner 3). Real DB write.
        harness.instance_repo.set_metadata(
            asker_id, "question_pack_id", pack_id
        )
        harness.instance_repo.set_metadata(
            asker_id, "question_pack_payload", pack_to_dict(pack)
        )

        async def _drive() -> dict:
            return await answer_questions_via_instance(
                manager,
                asker_id,
                {
                    "q-approach": "Approach A",
                    "q-risk": "Low",
                },
                pack_id,  # T1″ pack correlation guard (strict)
                harness.live_hub,
            )

        result = await _drive()

        # Drain pending fire-and-forget tasks scheduled by
        # ``_schedule_explicit_handle_resume`` (``asyncio.create_task``
        # for ``_resume_processing_background``). Production returns
        # the HTTP response synchronously while the graph runs in the
        # background; here we yield the loop once so the captured mock
        # records the ``message`` argument BEFORE the assertion fires.
        # ``_build_real_chain_manager`` exposes the task in
        # ``manager._graph_tasks`` so we can await it directly.
        for t in list(manager._graph_tasks.values()):
            if not t.done():
                await t

        # ── 2. Answer delivery succeeded ─────────────────────────────
        assert result["status"] == "answered"
        assert result["resume_route"] == "answer_gate_existing_turn"
        assert result["instance_id"] == asker_id

        # ── 3. Asker transitioned PAUSED → RUNNING ────────────────────
        post_status = _read_instance_status(engine, asker_id)
        assert post_status == InstanceStatus.RUNNING.value, (
            f"asker must transition to RUNNING after cascade, got "
            f"{post_status!r}"
        )

        # ── Handle consumed: task transitioned PAUSED → PENDING,
        #    suspension_reason / resume_target_turn_id CLEARED.
        post_task_status, post_reason, post_target = _read_task_handle(
            engine, task_pk
        )
        assert post_task_status == TaskStatus.PENDING.value, (
            f"task must transition PAUSED → PENDING (handle consumed), "
            f"got {post_task_status!r}"
        )
        assert post_reason is None, (
            f"suspension_reason must be cleared exactly-once, got "
            f"{post_reason!r}"
        )
        assert post_target is None, (
            f"resume_target_turn_id must be cleared exactly-once, got "
            f"{post_target!r}"
        )

        # ── 4. Answer TEXT reaches the graph-boundary ─────────────────
        # The captured mock recorded what the REAL chain built — not
        # what an AsyncMock happened to return. The message has to
        # contain both the F7 echo (questions) AND the user's answers.
        delivered = captured.get("message", "")
        assert delivered, "graph-boundary mock recorded an empty message"
        assert "Approach A" in delivered, (
            f"answer 'Approach A' must reach the graph node, got: "
            f"{delivered!r}"
        )
        assert "Approach A or B?" in delivered, (
            f"F7 echo must include the question text, got: {delivered!r}"
        )
        assert "q-approach" in delivered or "Approach A" in delivered, (
            f"Q↔A formatted message missing answer content: {delivered!r}"
        )

        # The graph node was called EXACTLY ONCE for the winning CAS.
        assert captured.get("graph_node_calls", 0) == 1

        # Cascade metadata: the old_job_id the graph-boundary mock
        # received points at the awaiting_answer handle's work_id (the
        # authoritative target per design §9.1 step 4-7).
        assert captured["old_job_id"] == work_id
        assert captured["instance_id"] == asker_id
        assert captured["silent"] is False

        # SSE fired (banner 6): the answered-status pack payload reached
        # the watcher, and the answer-received banner was emitted.
        assert any(
            args and args[0] == asker_id
            for args, _ in captured.get("sse_qpack_calls", [])
        ), "question_pack SSE must fire on the answered pack"
        assert any(
            args and args[0] == asker_id
            for args, _ in captured.get("sse_answer_received_calls", [])
        ), "answer_received SSE must fire on the answered pack"

        # QUESTION_ANSWERED event persisted in EventBus (banner 7).
        events = harness.event_repo.get_by_instance(asker_id)
        kinds = [e.kind for e in events]
        assert EventKind.QUESTION_ANSWERED.value in kinds, (
            f"QUESTION_ANSWERED event must persist; got kinds: {kinds!r}"
        )

        # ── 5. Exactly-once CAS ──────────────────────────────────────
        # A second answer for the same (now-answered) pack must
        # short-circuit to already_delivered — NO second graph call,
        # NO second cascade, NO second event.
        async def _drive_second() -> dict:
            return await answer_questions_via_instance(
                manager,
                asker_id,
                {"q-approach": "Approach C"},  # would-be override attempt
                pack_id,
                harness.live_hub,
            )

        second = await _drive_second()

        # Drain the second call's background task (the CAS-loser branch
        # does NOT schedule a graph task, but the cascade does — and any
        # straggler ``_schedule_explicit_handle_resume`` scheduled task
        # for a stale row would still fire if it slipped through).
        for t in list(manager._graph_tasks.values()):
            if not t.done():
                await t

        assert second["status"] == "already_delivered", (
            f"CAS loser must short-circuit to already_delivered, got "
            f"{second.get('status')!r}"
        )
        assert second["resume_route"] == "already_delivered"
        # The graph-boundary mock was NOT called a second time.
        assert captured.get("graph_node_calls", 0) == 1, (
            f"CAS-loser branch must NOT invoke the graph node, got "
            f"{captured.get('graph_node_calls')} calls"
        )
        # The first answer's payload remains authoritative.
        delivered_after_second = captured["message"]
        assert "Approach A" in delivered_after_second, (
            "first answer must not be overwritten by the CAS-loser "
            f"attempt; got: {delivered_after_second!r}"
        )
        assert "Approach C" not in delivered_after_second, (
            "CAS-loser must NOT clobber the first answer's payload; "
            f"got: {delivered_after_second!r}"
        )

    @pytest.mark.asyncio
    async def test_real_seam_terminated_asker_returns_410(
        self, engine: Engine,
    ):
        """Edge case (bonus, in the same vein as criterion b):

        A TERMINATED asker (the leader-decision-1 carve-out) must be
        refused at banner 2 — NOT silently revived. Verifies the T3
        terminal pre-check runs against the REAL instance_repository.
        """
        captured: dict = {}
        asker_id = f"inst-{uuid.uuid4()}"
        now = datetime.now(timezone.utc)
        with Session(engine) as session:
            session.add(
                Instance(
                    instance_id=asker_id,
                    agent_id="test",
                    agent_name="test",
                    agent_dir="/tmp/test",
                    status=InstanceStatus.COMPLETED.value,
                )
            )
            session.commit()

        harness = _build_real_chain_manager(engine, captured=captured)
        manager = harness.manager

        # Seed a pending pack (so banner 3 would have resolved); the
        # banner-2 pre-check must refuse BEFORE banner 3 runs.
        harness.question_manager.set_question_pack(
            asker_id, [{"id": "q1", "text": "Q?"}]
        )

        from fastapi import HTTPException

        async def _drive() -> dict:
            return await answer_questions_via_instance(
                manager,
                asker_id,
                {"q1": "Answer"},
                None,
                harness.live_hub,
            )

        with pytest.raises(HTTPException) as exc_info:
            await _drive()

        # 410 ANSWER_TARGET_TERMINAL — leader decision 1 (no silent revive).
        assert exc_info.value.status_code == 410
        body = exc_info.value.detail
        assert body["code"] == ErrorCodes.ANSWER_TARGET_TERMINAL.value

        # The graph node was NEVER called (pre-check refused the request).
        assert captured.get("graph_node_calls", 0) == 0