"""Tests for the empty-job-completed-event fix.

Covers both defects:

DEFECT 1 — JobFeedbackObserver terminal transitions now carry result_summary
(extracted best-effort from the root's last assistant message), so watcher
notifications render a non-empty "Result:" body and job_get stops returning
result_summary:null.

DEFECT 2 — child_reports root/cascade completion is gated on
waiting_for==0 AND pending_count==0 AND a fresh assistant message after the
last child_completed event. Suppressed instances are held in WAITING_CHILDREN
and re-evaluated on the existing message-completed signal (event-driven, no
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

from sqlmodel import Session, select

from daemon.repositories.event.models import Event, EventKind
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.job_queue import JobRepository, JobStatus
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
    assert started is not None and started.status == JobStatus.PROCESSING.value
    return repository, job


@pytest.fixture
def observer_env(engine, processing_task_job, job_queue_service):
    """Observer wired to the real JobQueueService + repo; manager mock exposes
    _checkpointer and captures watcher notifications."""
    job_repo, job = processing_task_job
    manager = MagicMock()
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
    the patched canonical extractor (`history` is the returned message list)."""
    manager = MagicMock()
    manager._engine = job_engine
    manager._live_hub = MagicMock()
    manager._live_hub.stream_status_change = AsyncMock()
    manager._checkpointer = MagicMock()  # unused; history comes from the patch
    manager._instance_repository = MagicMock()
    manager._instance_repository.get = MagicMock(
        return_value=MagicMock(agent_id="leader", instance_metadata={})
    )
    manager._queue_repository = MagicMock()
    events_service = MagicMock()
    events_service._publish_instance_lifecycle_event = AsyncMock()
    service = ChildReportsService(manager=manager, events_service=events_service)
    patcher = patch(
        "daemon.services.completion_content.get_instance_messages",
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
            waiting_for=row.waiting_for,
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
        ):
            await observer._process_event({
                "event_type": "instance_lifecycle",
                "data": {"instance_id": "root-instance-1", "status": "completed", "error": None},
            })

        row = job_repo.get(job.job_id)
        assert row.status == JobStatus.COMPLETED.value
        assert row.result_summary == "FINAL REPORT BODY"
        # job_get parity: the tool returns job_item.to_dict()
        assert row.to_dict()["result_summary"] == "FINAL REPORT BODY"

    @pytest.mark.asyncio
    async def test_failed_event_carries_error_and_best_effort_result(self, observer_env):
        """DEFECT 1 (error branch): error_message kept AND best-effort result_summary."""
        observer, manager, job_repo, job = observer_env

        # See note in ``test_completed_event_carries_result_summary`` —
        # the production seam is ``_get_last_assistant_message_raw``.
        with patch.object(
            manager, "_get_last_assistant_message_raw",
            new_callable=AsyncMock,
            return_value="partial progress notes",
        ):
            await observer._process_event({
                "event_type": "instance_lifecycle",
                "data": {"instance_id": "root-instance-1", "status": "error",
                         "error": "child exploded"},
            })

        row = job_repo.get(job.job_id)
        assert row.status == JobStatus.FAILED.value
        assert row.error_message == "child exploded"
        assert row.result_summary == "partial progress notes"
        d = row.to_dict()
        assert d["error_message"] == "child exploded"
        assert d["result_summary"] == "partial progress notes"

    @pytest.mark.asyncio
    async def test_deadlock_guard_terminal_without_result_still_terminates(
        self, engine, processing_task_job, job_queue_service
    ):
        """DEADLOCK GUARD: root terminal without extractable result content.

        The observer is predicate-blind — even when no assistant message can be
        extracted (stale/absent history, no checkpointer), the terminal lifecycle
        event still terminates the job. No eternal PROCESSING.
        """
        job_repo, job = processing_task_job
        manager = MagicMock()
        manager._checkpointer = None  # nothing extractable
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
        assert row.status == JobStatus.COMPLETED.value
        assert row.result_summary is None  # best-effort/empty result, job terminated

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
        ):
            await observer._process_event({
                "event_type": "instance_lifecycle",
                "data": {"instance_id": "root-instance-1", "status": "completed", "error": None},
            })

        manager.enqueue_message.assert_awaited_once()
        notification = manager.enqueue_message.call_args.kwargs["message"]
        assert "Result: FINAL REPORT BODY" in notification
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
                status=InstanceStatus.RUNNING.value, waiting_for=0,
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
                status=InstanceStatus.RUNNING.value, waiting_for=0,
            ))
            session.commit()

        add_queued_report(job_engine, "root-instance-1", "report-msg-1")
        add_child_completed_event(job_engine, "root-instance-1", T_CHILD_COMPLETED)

        service, events_service, manager, patcher = make_child_reports(
            job_engine, None
        )

        # Turn 1: report still queued, response stale -> held
        patcher_1 = patch(
            "daemon.services.completion_content.get_instance_messages",
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
            "daemon.services.completion_content.get_instance_messages",
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
                status=InstanceStatus.RUNNING.value, waiting_for=0,
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
                status=InstanceStatus.RUNNING.value, waiting_for=0,
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
        """Children still running (waiting_for>0) -> no completed event."""
        with Session(job_engine) as session:
            session.add(Instance(
                instance_id="root-instance-1", agent_id="leader",
                agent_dir="./agents/leader", parent_id=None,
                status=InstanceStatus.RUNNING.value, waiting_for=2,
            ))
            session.commit()

        service, events_service, manager, patcher = make_child_reports(
            job_engine, make_history("any response", T_FRESH_ASSISTANT)
        )
        with patcher, patch("daemon.services.child_reports.MainLoopBridge.run_async_no_wait"):
            await service._process_child_completion_and_notify_parent(
                "root-instance-1", "root-turn-1"
            )

        events_service._publish_instance_lifecycle_event.assert_not_awaited()
        assert get_instance_row(job_engine, "root-instance-1").status == \
            InstanceStatus.WAITING_CHILDREN.value


class TestCascadeCompletionGate:
    """Cascade site: parent completion gated on the full predicate."""

    def _make_parent_child(self, job_engine, waiting_for: int):
        with Session(job_engine) as session:
            session.add(Instance(
                instance_id="parent-1", agent_id="leader",
                agent_dir="./agents/leader", parent_id=None,
                status=InstanceStatus.WAITING_CHILDREN.value, waiting_for=waiting_for,
            ))
            session.add(Instance(
                instance_id="child-1", agent_id="coder",
                agent_dir="./agents/coder", parent_id="parent-1",
                status=InstanceStatus.COMPLETED.value, waiting_for=0,
            ))
            session.commit()

    @pytest.mark.asyncio
    async def test_cascade_holds_parent_when_assistant_message_stale(self, job_engine):
        """All children done + nothing queued, but parent never responded after
        the last child_completed -> held in WAITING_CHILDREN, not completed."""
        self._make_parent_child(job_engine, waiting_for=1)
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

        assert transitioned is True
        assert completed_parent_id is None
        assert get_instance_row(job_engine, "parent-1").status == \
            InstanceStatus.WAITING_CHILDREN.value

    @pytest.mark.asyncio
    async def test_cascade_completes_parent_when_fresh(self, job_engine):
        """All children done, nothing queued, parent responded after last
        child_completed -> parent completes (normal path preserved)."""
        self._make_parent_child(job_engine, waiting_for=1)
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
        assert completed_parent_id == "parent-1"
        assert completed_parent_parent_id is None
        assert get_instance_row(job_engine, "parent-1").status == \
            InstanceStatus.COMPLETED.value

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
                status=InstanceStatus.WAITING_CHILDREN.value, waiting_for=1,
            ))
            session.add(Instance(
                instance_id="child-1", agent_id="coder",
                agent_dir="./agents/coder", parent_id="parent-1",
                status=InstanceStatus.COMPLETED.value, waiting_for=0,
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
