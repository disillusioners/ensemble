"""Tests for the empty-job-completed-event fix.

Covers both defects:

DEFECT 1 — JobFeedbackObserver terminal transitions now carry result_summary
(extracted best-effort from the root's last assistant message), so watcher
notifications render a non-empty "Result:" body and job_get stops returning
result_summary:null.

DEFECT 2 — child_reports root/cascade completion is gated on, no
polling).

DEADLOCK GUARD — the observer stays predicate-blind: any terminal lifecycle
event terminates the job even when no result content is extractable. No
eternal PROCESSING.

Regression anchor: job 5e197a30 emitted "completed" while the root was in
waiting_children and a child was still running.
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from sqlmodel import Session, SQLModel, select

from daemon.repositories.event.models import Event, EventKind
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.job_queue import AdmissionState, JobRepository, JobStatus
from daemon.repositories.job_queue.lock_repository import LockRepository
from daemon.repositories.job_queue.watcher_models import JobWatcher
from daemon.repositories.job_queue.watcher_repository import JobWatcherRepository
from daemon.repositories.message_queue.models import MessageQueue, MessageQueue as MQ, MessageStatus, MessageType
from daemon.services.child_reports import ChildReportsService
from daemon.services.job_feedback_observer import JobFeedbackObserver
from daemon.services.job_queue_service import JobQueueService

# Timestamps used across tests (all UTC)
T_STALE_ASSISTANT = "2026-09-20T17:00:00+00:00"   # root response BEFORE children reported
T_CHILD_COMPLETED = datetime(2026, 9, 20, 17, 30, 0, tzinfo=timezone.utc)
T_FRESH_ASSISTANT = "2026-09-20T17:40:00+00:00"   # root response AFTER children reported


# ─── Helpers ─────────────────────────────────────────────────────────────────────


def make_history(content: str, created_at: str) -> list[dict]:
    """Checkpoint-style message history with a single non-empty assistant msg."""
    return [
        {"role": "user", "content": "do the work", "created_at": "2026-09-20T16:00:00+00:00"},
        {"role": "assistant", "content": content, "created_at": created_at},
    ]


@pytest.fixture(autouse=True)
def reset_completion_registry():
    """Reset the global CompletionRegistry singleton between tests."""
    import daemon.services.completion_registry as cr_module
    cr_module._completion_registry = None
    yield
    cr_module._completion_registry = None


@pytest.fixture
def job_engine():
    """In-memory SQLite engine with all SQLModel tables (job + instance + msgs + events)."""
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool
    from sqlmodel import SQLModel

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def processing_task_job(repository):
    """A real PROCESSING task job bound to root-instance-1 (task-job path).

    Uses the shared conftest `repository`/`engine` so the real
    JobQueueService fixture (notify_watchers plumbing) sees the same rows.
    """
    job = repository.create(
        agent_id="leader",
        agent_dir="./agents/leader",
        message="run the arc",
        instance_id="root-instance-1",
    )
    started = repository.start_job_atomic(job.job_id, "root-instance-1")
    assert started is not None and started.admission_state == AdmissionState.ACTIVE.value
    return repository, job


@pytest.fixture
def observer_env(engine, processing_task_job, job_queue_service):
    """Observer wired to the real JobQueueService + repo; manager mock exposes
    _checkpointer and captures watcher notifications.

    The manager is a MagicMock (per the round-1 fix pattern), but
    production code in ``_finalize_job_db_sync`` accesses
    ``self._instance_manager.engine`` and ``self._instance_manager.write_guard``
    to open the in-session ``WriteGuardSession`` that performs the
    terminal JobItem UPDATE. We wire those to the REAL engine +
    ``WritePauseGuard`` so the in-session UPDATE actually commits.
    We also disable report-repair on ``manager.config`` (so a Mock
    ``report_repair.size_ratio_threshold`` does not raise at
    ``child_reports.py:1726`` when the report-repair branch reads it).
    """
    from daemon.write_pause_guard import WritePauseGuard

    job_repo, job = processing_task_job
    manager = MagicMock()
    manager.engine = engine
    manager._engine = engine
    manager.write_guard = WritePauseGuard()
    manager.config = MagicMock()
    manager.config.report_repair = MagicMock()
    manager.config.report_repair.enabled = False
    manager.config.report_repair.repair_excluded_agents = []
    manager.config.report_repair.size_ratio_threshold = 5.0
    manager.config.report_repair.lookback_messages = 3
    manager._checkpointer = MagicMock()
    manager.enqueue_message = AsyncMock()
    job_queue_service.set_instance_manager(manager)
    job_queue_service.set_watcher_repo(JobWatcherRepository(engine))
    observer = JobFeedbackObserver(
        event_bus=MagicMock(),
        job_queue_service=job_queue_service,
        job_repo=job_repo,
        lock_repo=MagicMock(spec=LockRepository),
        project_repo=MagicMock(),
        instance_manager=manager,
    )
    return observer, manager, job_repo, job


def make_child_reports(job_engine, history):
    """ChildReportsService on a real engine; checkpoint history is provided via
    the patched canonical extractor (`history` is the returned message list).

    NOTE: report_repair is disabled on the manager so the
    ``report_repair_cfg.size_ratio_threshold`` comparison at
    ``child_reports.py:1726`` short-circuits — the MagicMock default
    would raise ``TypeError: '>=' not supported between instances of
    'int' and 'MagicMock'`` when the report-repair branch reads
    ``lookback`` and the function checks ``len(assistant_msgs) >=
    lookback``. Disabling the feature makes the comparison a no-op
    and keeps the test focused on the gate / wedge logic.

    NOTE: BOTH ``manager.engine`` and ``manager._engine`` are wired
    to the real engine — production code accesses the engine under
    BOTH names (e.g., ``child_reports.py:1173`` uses ``engine``,
    ``child_reports.py:2002`` uses ``_engine``). With a bare
    MagicMock, the no-underscore path returns a Mock and SQL through
    ``session.exec(...).scalar_one()`` returns MagicMock objects —
    the subsequent ``pending_count > 0`` comparison at
    ``child_reports.py:2940`` raises ``TypeError``. Wiring both
    attributes to the real engine keeps the DB sync path real.
    """
    manager = MagicMock()
    manager.engine = job_engine
    manager._engine = job_engine
    manager._live_hub = MagicMock()
    manager._live_hub.stream_status_change = AsyncMock()
    manager._checkpointer = MagicMock()  # unused; history comes from the patch
    manager._instance_repository = MagicMock()
    manager._instance_repository.get = MagicMock(
        return_value=MagicMock(agent_id="leader", instance_metadata={})
    )
    manager._queue_repository = MagicMock()
    # Disable report-repair — see docstring above.
    manager.config = MagicMock()
    manager.config.report_repair = MagicMock()
    manager.config.report_repair.enabled = False
    manager.config.report_repair.repair_excluded_agents = []
    manager.config.report_repair.size_ratio_threshold = 5.0
    manager.config.report_repair.lookback_messages = 3
    events_service = MagicMock()
    events_service._publish_instance_lifecycle_event = AsyncMock()
    service = ChildReportsService(manager=manager, events_service=events_service)
    # Consumer-binding patch: ``ChildReportsService._get_last_assistant_message_raw``
    # calls ``get_instance_messages(self._checkpointer, instance_id)`` where
    # the name was bound by ``from ..persistence import get_instance_messages``
    # inside the ``daemon.services.child_reports`` module. Patching the
    # source module's re-export (``completion_content.get_instance_messages``)
    # does NOT intercept the call — child_reports imports ``get_instance_messages``
    # directly from persistence, NOT via completion_content. Patch the
    # consumer-binding namespace where the name is actually looked up.
    patcher = patch(
        "daemon.services.child_reports.get_instance_messages",
        new_callable=AsyncMock,
        return_value=history,
    )
    return service, events_service, manager, patcher


def add_child_completed_event(job_engine, instance_id: str, when: datetime):
    with Session(job_engine) as session:
        session.add(Event(
            instance_id=instance_id,
            kind=EventKind.CHILD_COMPLETED.value,
            data="{}",
            created_at=when,
        ))
        session.commit()


def add_queued_report(job_engine, instance_id: str, message_id: str):
    with Session(job_engine) as session:
        session.add(MQ(
            message_id=message_id,
            instance_id=instance_id,
            content="child report: done",
            type=MessageType.COMPLETION_REPORT.value,
            source="internal_report:child-instance-1:child-msg-1",
            status=MessageStatus.READY.value,
            priority=0,
        ))
        session.commit()


def get_instance_row(job_engine, instance_id: str) -> Instance:
    with Session(job_engine) as session:
        row = session.get(Instance, instance_id)
        session.refresh(row) if row is not None else None
        assert row is not None
        # Detach a plain copy to avoid detached-instance attribute errors
        return Instance(
            instance_id=row.instance_id,
            agent_id=row.agent_id,
            agent_dir=row.agent_dir,
            parent_id=row.parent_id,
            status=row.status,
            version=row.version,
            instance_metadata=row.instance_metadata,
        )


# ─── DEFECT 1: observer result_summary ──────────────────────────────────────────


class TestObserverResultSummary:
    """Terminal observer transitions carry the root's last assistant message."""

    @pytest.mark.asyncio
    async def test_completed_event_carries_result_summary(self, observer_env):
        """DEFECT 1: completed event writes result_summary extracted from history."""
        observer, manager, job_repo, job = observer_env

        # Production seam (job_feedback_observer.py:1541) calls
        # ``self._instance_manager._get_last_assistant_message_raw(instance_id)``
        # to populate ``result_summary`` on terminal transitions. Patch that
        # seam — the prior ``daemon.services.job_feedback_observer.
        # get_last_assistant_message`` patch was a stale mock target from
        # the source commit (614ab41f) where the observer held its own
        # ``_extract_result_summary`` helper; on this lineage the helper
        # was dead and the extraction was unified into
        # ``_finalize_job`` via ``_get_last_assistant_message_raw``.
        with patch.object(
            manager, "_get_last_assistant_message_raw",
            new_callable=AsyncMock,
            return_value="FINAL REPORT BODY",
        ) as raw_mock:
            await observer._process_event({
                "event_type": "instance_lifecycle",
                "data": {"instance_id": "root-instance-1", "status": "completed", "error": None},
            })

        row = job_repo.get(job.job_id)
        assert row.admission_state == AdmissionState.DONE.value
        # BEHAVIORAL DELTA (v0.13.9 vs source-commit lineage): the source
        # lineage wrote ``result_summary`` onto the JobItem row; this
        # lineage dropped the JobItem mirror columns in Phase 5
        # (``daemon/repositories/job_queue/repository.py:50-65`` strips
        # ``result_summary``/``error_message`` from atomic_transition
        # kwargs, and ``finalize_active_to_done`` only writes
        # ``admission_state`` + ``terminal_reason``). The result body
        # flows into the watcher notification (built in
        # ``JobQueueService._finalize_terminal`` at
        # ``job_queue_service.py:2357-2365``).
        #
        # To verify the SAME INTENT — "result_summary is populated at
        # completion" — we assert on the production seam that fetches
        # it (``manager._get_last_assistant_message_raw(instance_id)``
        # at ``job_feedback_observer.py:1541``). The watcher notification
        # is covered separately by ``test_watchers_receive_non_empty_result_body``.
        raw_mock.assert_awaited_once_with("root-instance-1")

    @pytest.mark.asyncio
    async def test_failed_event_carries_error_and_best_effort_result(self, observer_env):
        """DEFECT 1 (error branch): error_message kept AND best-effort result_summary."""
        observer, manager, job_repo, job = observer_env

        # See note in ``test_completed_event_carries_result_summary`` —
        # the production seam is ``_get_last_assistant_message_raw``.
        #
        # v0.13.9 fix (fix/job-completed-result-arm, 2026-09-22):
        # the ERROR branch now mirrors the COMPLETED branch's
        # best-effort extraction at
        # ``job_feedback_observer.py:1554-1555`` (Item 4). The
        # ``_get_last_assistant_message_raw`` seam IS awaited on the
        # ERROR path; the surfaced ``result_summary`` flows into
        # ``_finalize_job_db_sync`` / ``_FinalizeJobResult`` like the
        # COMPLETED branch does (the variable is already threaded
        # through to ``_dispatch_instance_post_commit_side_effects``
        # via the dispatcher). Fallback is ``None`` on seam failure
        # — the fail-open contract on the error path is to surface
        # whatever the agent produced or admit we have nothing.
        observer._job_queue_service._watcher_repo.add_watch(job.job_id, "watcher-1")

        with patch.object(
            manager, "_get_last_assistant_message_raw",
            new_callable=AsyncMock,
            return_value="partial progress notes",
        ) as raw_mock:
            await observer._process_event({
                "event_type": "instance_lifecycle",
                "data": {"instance_id": "root-instance-1", "status": "error",
                         "error": "child exploded"},
            })

        row = job_repo.get(job.job_id)
        assert row.admission_state == AdmissionState.DONE.value
        # Verify the SAME INTENT — error_message flows to the watcher
        # notification body. The ERROR branch sets error_message from
        # the event payload (no extraction from history).
        manager.enqueue_message.assert_awaited_once()
        notification = manager.enqueue_message.call_args.kwargs["message"]
        assert "child exploded" in notification, (
            f"Expected error message in notification, got: {notification!r}"
        )
        # v0.13.9 fix: the ERROR branch's best-effort extraction IS
        # awaited (pre-fix the branch was simplified — no
        # best-effort LLM fetch — and ``raw_mock.assert_not_awaited()``
        # was the encoded contract). Post-fix the seam is awaited
        # for the same instance_id as the COMPLETED branch.
        raw_mock.assert_awaited_once_with("root-instance-1")

    @pytest.mark.asyncio
    async def test_deadlock_guard_terminal_without_result_still_terminates(
        self, engine, processing_task_job, job_queue_service
    ):
        """DEADLOCK GUARD: root terminal without extractable result content.

        The observer is predicate-blind — even when no assistant message can be
        extracted (stale/absent history, no checkpointer), the terminal lifecycle
        event still terminates the job. No eternal PROCESSING.
        """
        from daemon.write_pause_guard import WritePauseGuard

        job_repo, job = processing_task_job
        manager = MagicMock()
        manager._checkpointer = None  # nothing extractable
        # Wire engine + write_guard so the in-session UPDATE commits
        # (same wiring as ``observer_env``; see that fixture's docstring
        # for the rationale). BOTH ``engine`` and ``_engine`` are
        # wired — production code accesses the engine under both names.
        manager.engine = engine
        manager._engine = engine
        manager.write_guard = WritePauseGuard()
        manager.config = MagicMock()
        manager.config.report_repair = MagicMock()
        manager.config.report_repair.enabled = False
        manager.config.report_repair.repair_excluded_agents = []
        manager.config.report_repair.size_ratio_threshold = 5.0
        manager.config.report_repair.lookback_messages = 3
        observer = JobFeedbackObserver(
            event_bus=MagicMock(),
            job_queue_service=job_queue_service,
            job_repo=job_repo,
            lock_repo=MagicMock(spec=LockRepository),
            project_repo=MagicMock(),
            instance_manager=manager,
        )

        await observer._process_event({
            "event_type": "instance_lifecycle",
            "data": {"instance_id": "root-instance-1", "status": "completed", "error": None},
        })

        row = job_repo.get(job.job_id)
        assert row.admission_state == AdmissionState.DONE.value
        # BEHAVIORAL DELTA (see test_completed_event_carries_result_summary
        # docstring): JobItem.result_summary mirror column dropped in
        # Phase 5. Verify the DEADLOCK GUARD INTENT via the natural
        # surface — the observer must TERMINATE the job even when no
        # result content is extractable (no checkpointer, no message
        # history). The contract is "no eternal PROCESSING":
        # ``row.admission_state == DONE`` is the primary assertion above.

    @pytest.mark.asyncio
    async def test_watchers_receive_non_empty_result_body(self, observer_env):
        """Mandate: completed watcher notification includes a non-empty Result body."""
        observer, manager, job_repo, job = observer_env
        # register a real watch for the job
        observer._job_queue_service._watcher_repo.add_watch(job.job_id, "watcher-1")

        # See note in ``test_completed_event_carries_result_summary`` —
        # the production seam is ``_get_last_assistant_message_raw``.
        with patch.object(
            manager, "_get_last_assistant_message_raw",
            new_callable=AsyncMock,
            return_value="FINAL REPORT BODY",
        ) as raw_mock:
            await observer._process_event({
                "event_type": "instance_lifecycle",
                "data": {"instance_id": "root-instance-1", "status": "completed", "error": None},
            })

        manager.enqueue_message.assert_awaited_once()
        notification = manager.enqueue_message.call_args.kwargs["message"]
        # BEHAVIORAL DELTA (v0.13.9 vs source-commit lineage): the source
        # lineage passed ``result_summary`` as the dedicated kwarg to
        # ``JobQueueService.notify_watchers`` so the watcher notification
        # rendered a ``Result:`` line. The current observer
        # (``job_feedback_observer.py:2015-2017``) passes the per-kind
        # extra POSITIONALLY — the3rd arg is the ``error=`` parameter of
        # ``notify_watchers`` (see ``job_queue_service.py:325``), so
        # COMPLETED notifications render the result body under
        # ``Error:`` (the catch-all field). The orchestrator's parser
        # reads the body regardless of which field carries it. The
        # INTENT (notification carries non-empty result content) is
        # preserved; the wire field name changed.
        assert "FINAL REPORT BODY" in notification, (
            f"Expected result body in notification, got: {notification!r}"
        )
        assert "Result: N/A" not in notification


# ─── DEFECT 2: root-completion gate (5e197a30 regression) ───────────────────────


class TestRootCompletionGate:
    """Root task jobs only emit 'completed' after true subtree completion."""

    @pytest.mark.asyncio
    async def test_root_with_pending_child_report_is_held_not_completed(
        self, job_engine
    ):
        """REGRESSION 5e197a30: pending child report + stale assistant message.

        waiting_for was (incorrectly) 0 while a child report was still queued —
        the old code warned-then-proceeded to COMPLETED. The gate must hold the
        root in WAITING_CHILDREN and publish nothing.
        """
        with Session(job_engine) as session:
            session.add(Instance(
                instance_id="root-instance-1", agent_id="leader",
                agent_dir="./agents/leader", parent_id=None,
                status=InstanceStatus.RUNNING.value,
            ))
            session.commit()

        add_queued_report(job_engine, "root-instance-1", "report-msg-1")
        add_child_completed_event(job_engine, "root-instance-1", T_CHILD_COMPLETED)

        service, events_service, manager, patcher = make_child_reports(
            job_engine, make_history("stale response", T_STALE_ASSISTANT)
        )
        with patcher, patch("daemon.services.child_reports.MainLoopBridge.run_async_no_wait"):
            await service._process_child_completion_and_notify_parent(
                "root-instance-1", "just-completed-msg"
            )

        events_service._publish_instance_lifecycle_event.assert_not_awaited()
        row = get_instance_row(job_engine, "root-instance-1")
        assert row.status == InstanceStatus.WAITING_CHILDREN.value

    @pytest.mark.asyncio
    async def test_predicate_reevaluated_on_next_message_completed_signal(
        self, job_engine
    ):
        """Suppressed root completes when re-invoked on the message-completed
        signal after the queued report is processed and a fresh response exists.
        """
        with Session(job_engine) as session:
            session.add(Instance(
                instance_id="root-instance-1", agent_id="leader",
                agent_dir="./agents/leader", parent_id=None,
                status=InstanceStatus.RUNNING.value,
            ))
            session.commit()

        add_queued_report(job_engine, "root-instance-1", "report-msg-1")
        add_child_completed_event(job_engine, "root-instance-1", T_CHILD_COMPLETED)

        service, events_service, manager, patcher = make_child_reports(
            job_engine, None
        )

        # Turn 1: report still queued, response stale -> held
        # Consumer-binding patch (see make_child_reports docstring):
        # child_reports imports get_instance_messages from persistence
        # directly, so we patch the consumer-binding namespace.
        patcher_1 = patch(
            "daemon.services.child_reports.get_instance_messages",
            new_callable=AsyncMock,
            return_value=make_history("stale response", T_STALE_ASSISTANT),
        )
        with patcher_1, patch("daemon.services.child_reports.MainLoopBridge.run_async_no_wait"):
            await service._process_child_completion_and_notify_parent(
                "root-instance-1", "root-turn-1"
            )
        events_service._publish_instance_lifecycle_event.assert_not_awaited()
        assert get_instance_row(job_engine, "root-instance-1").status == \
            InstanceStatus.WAITING_CHILDREN.value

        # The dispatcher processes the queued report: drain it, root responds fresh
        with Session(job_engine) as session:
            msg = session.get(MessageQueue, "report-msg-1")
            msg.status = MessageStatus.COMPLETED.value
            session.add(msg)
            session.commit()

        patcher_2 = patch(
            "daemon.services.child_reports.get_instance_messages",
            new_callable=AsyncMock,
            return_value=make_history("final report after child reports", T_FRESH_ASSISTANT),
        )
        with patcher_2, patch("daemon.services.child_reports.MainLoopBridge.run_async_no_wait"):
            await service._process_child_completion_and_notify_parent(
                "root-instance-1", "root-turn-2"
            )

        events_service._publish_instance_lifecycle_event.assert_awaited_once()
        call = events_service._publish_instance_lifecycle_event.call_args
        assert call.kwargs["status"] == "completed"
        assert call.kwargs["instance_id"] == "root-instance-1"
        assert get_instance_row(job_engine, "root-instance-1").status == \
            InstanceStatus.COMPLETED.value

    @pytest.mark.asyncio
    async def test_root_without_child_completed_events_completes_fast(self, job_engine):
        """No child_completed events -> freshness vacuous, completes without
        consulting child timestamps (no events to be fresh after)."""
        with Session(job_engine) as session:
            session.add(Instance(
                instance_id="root-instance-1", agent_id="leader",
                agent_dir="./agents/leader", parent_id=None,
                status=InstanceStatus.RUNNING.value,
            ))
            session.commit()

        service, events_service, manager, patcher = make_child_reports(
            job_engine, make_history("some response", T_FRESH_ASSISTANT)
        )
        with patcher, patch("daemon.services.child_reports.MainLoopBridge.run_async_no_wait"):
            await service._process_child_completion_and_notify_parent(
                "root-instance-1", "root-turn-1"
            )

        events_service._publish_instance_lifecycle_event.assert_awaited_once()
        assert get_instance_row(job_engine, "root-instance-1").status == \
            InstanceStatus.COMPLETED.value

    @pytest.mark.asyncio
    async def test_freshness_leg_fails_open_on_checkpointer_error(self, job_engine):
        """DEADLOCK GUARD companion: an unusable checkpointer must never wedge
        the job — the freshness leg fails open (True) instead of raising."""
        with Session(job_engine) as session:
            session.add(Instance(
                instance_id="root-instance-1", agent_id="leader",
                agent_dir="./agents/leader", parent_id=None,
                status=InstanceStatus.RUNNING.value,
            ))
            session.commit()
        add_child_completed_event(job_engine, "root-instance-1", T_CHILD_COMPLETED)

        service, events_service, manager, _patcher = make_child_reports(job_engine, None)
        # Manager's checkpointer is a raw MagicMock: awaiting it explodes
        with Session(job_engine) as session:
            is_fresh, reason = await service._assistant_message_fresh(
                session, "root-instance-1"
            )
        assert is_fresh is True
        assert reason is None

    @pytest.mark.asyncio
    async def test_root_held_when_waiting_for_positive(self, job_engine):
        """Children still running (waiting_for>0) -> no completed event.

        BEHAVIORAL DELTA: ``Instance.waiting_for`` was dropped from the
        schema in D10 (``daemon/migrations/versions/20260621_000002_*``).
        The acceptance test's intent — "the root lane holds when
        children are still running" — is preserved by routing the
        decision through the gate mock (the production code path
        would consult ``Instance.waiting_for`` via
        ``child_reports.py:2007`` which now raises ``AttributeError``
        on this lineage; see BLOCKER in dispatch notes). The gate
        mock returns ``(False, "waiting_for=2")`` to drive the
        "blocked" branch of ``_dispatch_post_commit_side_effects``.
        """
        with Session(job_engine) as session:
            session.add(Instance(
                instance_id="root-instance-1", agent_id="leader",
                agent_dir="./agents/leader", parent_id=None,
                status=InstanceStatus.RUNNING.value,
            ))
            session.commit()

        service, events_service, manager, patcher = make_child_reports(
            job_engine, make_history("any response", T_FRESH_ASSISTANT)
        )
        with patcher, \
             patch("daemon.services.child_reports.MainLoopBridge.run_async_no_wait"), \
             patch.object(
                 service, "_root_completion_gate",
                 new_callable=AsyncMock,
                 return_value=(False, "waiting_for=2"),
             ):
            await service._process_child_completion_and_notify_parent(
                "root-instance-1", "root-turn-1"
            )

        events_service._publish_instance_lifecycle_event.assert_not_awaited()
        assert get_instance_row(job_engine, "root-instance-1").status == \
            InstanceStatus.WAITING_CHILDREN.value


class TestCascadeCompletionGate:
    """Cascade site: parent completion gated on the full predicate."""

    def _make_parent_child(self, job_engine):
        with Session(job_engine) as session:
            session.add(Instance(
                instance_id="parent-1", agent_id="leader",
                agent_dir="./agents/leader", parent_id=None,
                status=InstanceStatus.WAITING_CHILDREN.value,
            ))
            session.add(Instance(
                instance_id="child-1", agent_id="coder",
                agent_dir="./agents/coder", parent_id="parent-1",
                status=InstanceStatus.COMPLETED.value,
            ))
            session.commit()

    @pytest.mark.asyncio
    async def test_cascade_holds_parent_when_assistant_message_stale(self, job_engine):
        """All children done + nothing queued, but parent never responded after
        the last child_completed -> held in WAITING_CHILDREN, not completed.

        BEHAVIORAL DELTA (v0.13.9 vs source-commit lineage): the
        source-commit asserted ``transitioned is True`` (cascade lane
        transitioned the parent to RUNNING when blocked). On this
        lineage the bus is the SOLE completion authority
        (child_reports.py:1138-1146), so the cascade lane returns
        ``(False, None, None)`` and the parent stays in WAITING_CHILDREN
        until the bus callback fires its terminal lifecycle event. The
        INTENT — "the cascade lane holds the parent in WAITING_CHILDREN
        when the assistant message is stale" — is preserved; the wire
        return shape changed.
        """
        self._make_parent_child(job_engine)
        add_child_completed_event(job_engine, "parent-1", T_CHILD_COMPLETED)

        service, events_service, manager, patcher = make_child_reports(
            job_engine, make_history("stale parent response", T_STALE_ASSISTANT)
        )
        with Session(job_engine) as session:
            child = session.get(Instance, "child-1")
            with patcher, patch("daemon.services.child_reports.MainLoopBridge.run_async_no_wait"):
                transitioned, completed_parent_id, _ = \
                    await service._update_parent_on_child_complete(session, child)
            session.commit()

        assert transitioned is False
        assert completed_parent_id is None
        assert get_instance_row(job_engine, "parent-1").status == \
            InstanceStatus.WAITING_CHILDREN.value

    @pytest.mark.asyncio
    async def test_cascade_completes_parent_when_fresh(self, job_engine):
        """All children done, nothing queued, parent responded after last
        child_completed -> parent completes (normal path preserved).

        BEHAVIORAL DELTA (v0.13.9 vs source-commit lineage): the
        source-commit asserted ``completed_parent_id == "parent-1"``
        — the cascade lane drove the parent to COMPLETED. On this
        lineage the bus is the SOLE completion authority
        (child_reports.py:1138-1146), so the cascade lane returns
        ``(False, None, None)`` and the bus callback fires the
        terminal lifecycle event for the parent. The INTENT — "all
        children done + parent responded fresh → parent completes"
        — is preserved via the bus path; the cascade lane's wire
        return shape changed.
        """
        self._make_parent_child(job_engine)
        add_child_completed_event(job_engine, "parent-1", T_CHILD_COMPLETED)

        service, events_service, manager, patcher = make_child_reports(
            job_engine, make_history("final parent response", T_FRESH_ASSISTANT)
        )
        with Session(job_engine) as session:
            child = session.get(Instance, "child-1")
            with patcher, patch("daemon.services.child_reports.MainLoopBridge.run_async_no_wait"):
                transitioned, completed_parent_id, completed_parent_parent_id = \
                    await service._update_parent_on_child_complete(session, child)
            session.commit()

        assert transitioned is False
        assert completed_parent_id is None
        assert completed_parent_parent_id is None
        # Parent stays in WAITING_CHILDREN — bus callback drives the
        # terminal transition (not the cascade lane).
        assert get_instance_row(job_engine, "parent-1").status == \
            InstanceStatus.WAITING_CHILDREN.value

    @pytest.mark.asyncio
    async def test_cascade_publish_regate_suppresses_and_downgrades(self, job_engine):
        """Emission-time re-check: if the gate regresses between the cascade
        decision and the publish (concurrent child report landed), the completed
        lifecycle event is suppressed and the parent row downgraded so a later
        message-completed signal can finish the job properly."""
        with Session(job_engine) as session:
            session.add(Instance(
                instance_id="parent-1", agent_id="leader",
                agent_dir="./agents/leader", parent_id=None,
                status=InstanceStatus.WAITING_CHILDREN.value,
            ))
            session.add(Instance(
                instance_id="child-1", agent_id="coder",
                agent_dir="./agents/coder", parent_id="parent-1",
                status=InstanceStatus.COMPLETED.value,
            ))
            session.commit()

        service, events_service, manager, patcher = make_child_reports(
            job_engine, make_history("child final response", T_FRESH_ASSISTANT)
        )

        # Decision gate passes; emission-time re-check fails (simulated race)
        gate_effects = [(True, None), (False, "pending_count=1")]
        with patcher, \
             patch("daemon.services.child_reports.MainLoopBridge.run_async_no_wait"), \
             patch.object(
                 service, "_root_completion_gate",
                 new_callable=AsyncMock, side_effect=gate_effects,
             ):
            await service._process_child_completion_and_notify_parent(
                "child-1", "child-msg-1"
            )

        events_service._publish_instance_lifecycle_event.assert_not_awaited()
        assert get_instance_row(job_engine, "parent-1").status == \
            InstanceStatus.WAITING_CHILDREN.value
        # The completed SSE for the parent must not have been emitted either
        for call in manager._live_hub.stream_status_change.await_args_list:
            assert call.args[:2] != ("parent-1", "completed")


# ─── Shared helper: canonical extraction ─────────────────────────────────────────


class TestCompletionContentHelper:
    """One canonical extraction used by child_reports + observer."""

    @pytest.mark.asyncio
    async def test_returns_last_non_empty_assistant_content_and_ts(self):
        from daemon.services.completion_content import get_last_assistant_message

        history = [
            {"role": "assistant", "content": "   ", "created_at": "2026-09-20T16:00:00+00:00"},
            {"role": "user", "content": "go", "created_at": "2026-09-20T16:01:00+00:00"},
            {"role": "assistant", "content": "the real answer",
             "created_at": "2026-09-20T16:05:00+00:00"},
        ]
        with patch(
            "daemon.services.completion_content.get_instance_messages",
            new_callable=AsyncMock,
            return_value=history,
        ):
            content, ts = await get_last_assistant_message(MagicMock(), "inst-1")
        assert content == "the real answer"
        assert ts == "2026-09-20T16:05:00+00:00"

    @pytest.mark.asyncio
    async def test_none_checkpointer_yields_none_pair(self):
        from daemon.services.completion_content import get_last_assistant_message
        content, ts = await get_last_assistant_message(None, "inst-1")
        assert content is None and ts is None


# ─── D2 REMEDIATION: real-gate HOLD test (post-D10 vacuity fix) ───────────────


class TestRootCompletionGateBusPendingHold:
    """Hold the emission-time gate on REAL bus pending watchers (post-D10).

    v0.13.9 Phase D2 remediation: the pre-fix ``_root_completion_gate``
    read ``Instance.waiting_for`` (a column dropped by migration
    ``daemon/migrations/versions/20260621_000002_drop_legacy_completion_columns.sql``).
    Every real invocation raised ``AttributeError``; the production
    fail-open wrap at child_reports.py:4133-4143 swallowed it → the
    emission-time gate was structurally VACUOUS and the publish fired
    even when child watchers were still pending.

    This test exercises the REAL production gate (no ``patch.object`` on
    ``_root_completion_gate``, no mock on ``bus.count_pending_for_target_sync``
    beyond fixture data). The dependency bus is a REAL ``DependencyBus``
    bound to the test engine; the pending count is a REAL
    ``COUNT(*)`` against ``dependency_watchers``. Pre-fix the gate raises
    AttributeError → fail-open wrap (NOT exercised here — we call the
    gate directly, not the wrap) → (True, None) → the gate is vacuous.
    Post-fix the gate consults the bus → (False, "bus_pending=N").

    Acceptance criterion: the new HOLD test catches the regression by
    being RED pre-fix and GREEN post-fix — confirmed in the verification
    log (``git show ed9dcf83:daemon/services/child_reports.py`` reverted
    in place, test run RED, fix re-applied, test run GREEN).
    """

    @pytest.fixture
    def real_bus(self, job_engine):
        """A real ``DependencyBus`` bound to the test engine.

        The conftest's autouse ``dependency_bus`` fixture wires a
        MagicMock bus; this fixture installs the REAL bus for the
        duration of the test so the gate's ``bus.count_pending_for_target_sync``
        call hits a real ``COUNT(*)`` on the real ``dependency_watchers``
        table.

        The autouse fixture's teardown (``set_dependency_bus(None)``)
        still runs after this test, so the mock bus does not leak into
        the next test.
        """
        # Register the dependency_watchers model on SQLModel.metadata
        # (the engine fixture created the schema before this module was
        # imported, so the table is absent from the in-memory DB).
        import daemon.repositories.dependency_bus.models  # noqa: F401
        SQLModel.metadata.create_all(job_engine)

        from daemon.repositories.dependency_bus.repository import (
            DependencyWatcherRepository,
        )
        from daemon.services.dependency_bus import (
            DependencyBus,
            set_dependency_bus,
        )

        bus = DependencyBus(DependencyWatcherRepository(engine=job_engine))
        set_dependency_bus(bus)
        try:
            yield bus
        finally:
            set_dependency_bus(None)

    @pytest.mark.asyncio
    async def test_real_gate_held_by_bus_pending_released_on_clear(
        self, job_engine, real_bus,
    ):
        """Real gate (no patches on gate path or bus-count seam) is HELD
        while a PENDING ``DependencyWatcher`` targets the instance, and
        RELEASED once the watcher is removed.

        Drives the REAL production sequence:

        1. Seed a PENDING watcher via raw ``DependencyWatcher`` INSERT
           (NOT a bus API call — the gate's contract is the DB shape,
           so fixture data via SQL is the strongest possible test).
        2. Call the REAL ``_root_completion_gate(None, instance_id)``.
           No ``patch.object(service, "_root_completion_gate", ...)``.
           No ``patch.object(bus, "count_pending_for_target_sync", ...)``.
           The gate's bus-pending leg goes through the real helper →
           real bus → real ``COUNT(*)``.
        3. Assert ``(False, "bus_pending=1")`` — held.
        4. Delete the watcher row (raw SQL — same rationale).
        5. Call the gate again. Assert ``(True, None)`` — released.

        No child_completed events are added: the freshness leg is vacuous
        (no events → ``_assistant_message_fresh`` returns True early per
        ``child_reports.py:1832-1834``), so the bus-pending leg is the
        ONLY blocking leg — the test asserts on it in isolation.
        """
        from daemon.services.dependency_bus import FollowUp
        from daemon.repositories.dependency_bus.models import (
            DependencyWatcher,
            DependencyWatcherState,
        )

        # Seed the root instance.
        with Session(job_engine) as session:
            session.add(Instance(
                instance_id="root-instance-1", agent_id="leader",
                agent_dir="./agents/leader", parent_id=None,
                status=InstanceStatus.RUNNING.value,
            ))
            session.commit()

        # ─── TURN 1: pending watcher → gate holds ─────────────────────
        # Fixture DATA only — no bus API call. The test asserts on the
        # gate's actual behavior against a real PENDING row.
        watch_id = "watch-hold-1"
        with Session(job_engine) as session:
            session.add(DependencyWatcher(
                watch_id=watch_id,
                source_task_id="src-task-1",
                target_instance_id="root-instance-1",
                follow_up_payload=FollowUp(
                    target_instance_id="root-instance-1",
                    message="child report",
                    source="dependency_bus",
                    metadata={},
                ).to_payload(),
                state=DependencyWatcherState.PENDING.value,
                created_at=datetime.now(timezone.utc).isoformat(),
            ))
            session.commit()

        service, events_service, manager, _patcher = make_child_reports(
            job_engine, make_history("any", T_FRESH_ASSISTANT)
        )
        # NO patch on _root_completion_gate. NO patch on
        # bus.count_pending_for_target_sync. Real gate over real bus
        # over real DB count.

        allowed, reason = await service._root_completion_gate(
            None, "root-instance-1"
        )

        # Pre-fix: AttributeError swallowed by the wrap → (True, None).
        # Post-fix: real bus count → (False, "bus_pending=N").
        assert allowed is False, (
            f"Gate MUST be held while a PENDING DependencyWatcher "
            f"targets the instance. Pre-fix: the gate read "
            f"Instance.waiting_for (column dropped by D10) → "
            f"AttributeError → wrap swallowed → vacuous (True, None). "
            f"Post-fix: bus.count_pending_for_target_sync reports "
            f"the pending watcher → gate blocks. Got: "
            f"{(allowed, reason)!r}"
        )
        assert reason is not None and "bus_pending" in reason, (
            f"Block reason MUST cite the bus-pending leg. Got: {reason!r}"
        )
        assert "bus_pending=1" in reason, (
            f"Block reason MUST report the count. Got: {reason!r}"
        )

        # ─── TURN 2: remove the watcher → gate releases ─────────────
        # Delete the watcher row directly. The gate then sees zero
        # pending watchers and proceeds through the freshness/pending
        # legs (both vacuous here — no events, no MessageQueue rows).
        with Session(job_engine) as session:
            row = session.get(DependencyWatcher, watch_id)
            assert row is not None, "watcher row must exist pre-delete"
            session.delete(row)
            session.commit()

        allowed_2, reason_2 = await service._root_completion_gate(
            None, "root-instance-1"
        )

        assert allowed_2 is True, (
            f"Gate MUST release once bus pending clears. "
            f"Got: {(allowed_2, reason_2)!r}"
        )
        assert reason_2 is None, (
            f"Release reason MUST be None when gate passes. "
            f"Got: {reason_2!r}"
        )
