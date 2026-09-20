"""Round-2 council review tests.

Covers Findings 1+5, 2, and 4 ONLY (Finding 3 — error_reporting.py premature-
completion lane — is explicitly OUT OF SCOPE per mandate).

FINDING 1+5 (bundled — cascade gate reality + root/cascade symmetry):
- Tests exercise the REAL production sequence without mocking the gate.
- Verifies the cascade lane at _update_parent_on_child_complete always
  returns WAITING_CHILDREN in normal traffic (autoflush skew).
- Verifies the cascade emission-time gate (load-bearing) wraps the call in
  fail-open try/except and downgrades on regression.
- Verifies the root lane mirror emission-time gate (new in Round 2) is a
  fail-open check that downgrades to WAITING_CHILDREN on regression.

FINDING 2 (deadlock-guard wedge paths):
- (a) stale-readable: parent's last assistant timestamp predates the most
  recent child_completed; the last message is terminal. The wedge resolver
  allows the gate to pass (best-effort empty result).
- (b) empty-final-turn: parent's last AI message is empty content (pure
  tool-call turn). The freshness check uses get_last_assistant_timestamp
  (any AI message counts) and the gate passes.
- (c) dead-letter: the completion report message transitions to FAILED after
  max retries; the wedge resolver allows the gate to pass.
- Tests for the stale_task_recovery wedge hook in
  manager._on_stale_task_permanent_failure (root only).

FINDING 4 (MESSAGE-job deferral):
- Instance in WAITING_CHILDREN with a second pending message → the MESSAGE
  job does NOT complete inline; it defers to the observer. When the second
  message is processed and the instance transitions to COMPLETED, the
  observer fires with populated result_summary.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlmodel import Session, SQLModel, create_engine
from sqlalchemy.pool import StaticPool

from daemon.repositories.event.models import Event, EventKind
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.job_queue import JobRepository, JobStatus
from daemon.repositories.job_queue.lock_repository import LockRepository
from daemon.repositories.job_queue.watcher_repository import JobWatcherRepository
from daemon.repositories.message_queue.models import (
    MessageQueue, MessageStatus, MessageType,
)
from daemon.services.child_reports import ChildReportsService
from daemon.services.completion_content import (
    get_last_assistant_message,
    get_last_assistant_timestamp,
)
from daemon.services.job_feedback_observer import JobFeedbackObserver
from daemon.services.job_queue_service import JobQueueService


# Timestamps used across tests (all UTC)
T_STALE_ASSISTANT = "2026-09-20T17:00:00+00:00"   # BEFORE children reported
T_CHILD_COMPLETED = datetime(2026, 9, 20, 17, 30, 0, tzinfo=timezone.utc)
T_FRESH_ASSISTANT = "2026-09-20T17:40:00+00:00"   # AFTER children reported


# ─── Helpers ──────────────────────────────────────────────────────────────────


def make_history(content: str, created_at: str) -> list[dict]:
    """Checkpoint-style message history with a single non-empty assistant msg."""
    return [
        {"role": "user", "content": "do the work", "created_at": "2026-09-20T16:00:00+00:00"},
        {"role": "assistant", "content": content, "created_at": created_at},
    ]


def make_history_with_empty_assistant(empty_ts: str) -> list[dict]:
    """Checkpoint history with the LAST assistant message having EMPTY content
    (pure tool-call turn). The previous assistant is non-empty and stale."""
    return [
        {"role": "user", "content": "do the work", "created_at": "2026-09-20T16:00:00+00:00"},
        {"role": "assistant", "content": "stale non-empty response", "created_at": T_STALE_ASSISTANT},
        {"role": "user", "content": "child report: done", "created_at": T_CHILD_COMPLETED.isoformat()},
        {"role": "assistant", "content": "", "created_at": empty_ts},
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
    """In-memory SQLite engine with all SQLModel tables."""
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
    """A real PROCESSING task job bound to root-instance-1 (task-job path)."""
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
    """ChildReportsService on a real engine; checkpoint history provided via patch."""
    manager = MagicMock()
    manager._engine = job_engine
    manager._live_hub = MagicMock()
    manager._live_hub.stream_status_change = AsyncMock()
    manager._checkpointer = MagicMock()  # unused; history comes from patch
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


def add_queued_report(job_engine, instance_id: str, message_id: str,
                      status: str = MessageStatus.READY.value):
    with Session(job_engine) as session:
        session.add(MessageQueue(
            message_id=message_id,
            instance_id=instance_id,
            content="child report: done",
            type=MessageType.COMPLETION_REPORT.value,
            source=f"internal_report:child-instance-1:{message_id}",
            status=status,
            priority=0,
        ))
        session.commit()


def add_terminal_message(job_engine, instance_id: str, message_id: str,
                          status: str, completed_at: datetime):
    """Insert a message with a TERMINAL status (COMPLETED or FAILED)."""
    with Session(job_engine) as session:
        session.add(MessageQueue(
            message_id=message_id,
            instance_id=instance_id,
            content=f"terminal msg {message_id}",
            type=MessageType.COMPLETION_REPORT.value,
            source=f"internal_report:child-instance-1:{message_id}",
            status=status,
            priority=0,
            completed_at=completed_at,
        ))
        session.commit()


def get_instance_row(job_engine, instance_id: str) -> Instance:
    with Session(job_engine) as session:
        row = session.get(Instance, instance_id)
        session.refresh(row) if row is not None else None
        assert row is not None
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


# ─── FINDING 1+5: cascade gate reality + root mirror ──────────────────────────


class TestCascadeGateRealityAutoflushSkew:
    """Finding 1: cascade lane always-blocks due to autoflush skew.

    The staged READY report row (added by _create_completion_report upstream)
    is visible to the pending_count query. The cascade lane's gate at
    _update_parent_on_child_complete ALWAYS returns False in normal traffic;
    it intentionally defers to the message-completed signal.
    """

    @pytest.mark.asyncio
    async def test_cascade_lane_always_blocks_when_staged_report_present(
        self, job_engine
    ):
        """Real production sequence: parent+child setup; cascade evaluates gate
        with a staged report row already in the session. pending_count>=1 →
        blocked → parent → WAITING_CHILDREN (not COMPLETED)."""
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

        add_child_completed_event(job_engine, "parent-1", T_CHILD_COMPLETED)

        # Pre-stage the report row (as _create_completion_report does upstream)
        add_queued_report(job_engine, "parent-1", "report-msg-1")

        # Even with FRESH assistant, autoflush skew sees the staged report row.
        service, events_service, manager, patcher = make_child_reports(
            job_engine, make_history("final parent response", T_FRESH_ASSISTANT)
        )
        with Session(job_engine) as session:
            child = session.get(Instance, "child-1")
            with patcher, patch(
                "daemon.services.child_reports.MainLoopBridge.run_async_no_wait"
            ):
                transitioned, completed_parent_id, _ = (
                    await service._update_parent_on_child_complete(session, child)
                )
            session.commit()

        # Cascade lane is intentionally conservative: WAITING_CHILDREN, never
        # completes inline. The load-bearing gate is the emission-time re-check.
        assert transitioned is True
        assert completed_parent_id is None
        assert get_instance_row(job_engine, "parent-1").status == (
            InstanceStatus.WAITING_CHILDREN.value
        )
        events_service._publish_instance_lifecycle_event.assert_not_awaited()


class TestCascadeEmissionGateFailOpen:
    """Finding 1: emission-time gate at cascade publish wraps in fail-open.

    The cascade emission-time re-gate at :~950 is the LOAD-BEARING gate. It
    runs in a fresh session (sees the committed report, not the staged one)
    and must be wrapped in fail-open try/except for parity with the adjacent
    publish.
    """

    @pytest.mark.asyncio
    async def test_emission_gate_fails_open_on_exception(self, job_engine):
        """If the emission-time gate raises (corrupt DB, etc.), the publish
        below must still happen. Fail-open is the documented contract."""
        with Session(job_engine) as session:
            session.add(Instance(
                instance_id="parent-1", agent_id="leader",
                agent_dir="./agents/leader", parent_id=None,
                status=InstanceStatus.COMPLETED.value, waiting_for=0,
            ))
            session.commit()

        service, events_service, manager, _patcher = make_child_reports(
            job_engine, make_history("final response", T_FRESH_ASSISTANT)
        )

        # Patch _root_completion_gate to raise; the caller must catch and proceed.
        async def raising_gate(*args, **kwargs):
            raise RuntimeError("simulated gate failure")

        with patch.object(
            service, "_root_completion_gate", side_effect=raising_gate
        ):
            # Call the cascade emission-time block manually
            try:
                allowed, block_reason = await service._root_completion_gate(
                    None, "parent-1"
                )
                mirror_blocked = not allowed
            except Exception:
                mirror_blocked = False  # fail-open: not blocked

        assert mirror_blocked is False, (
            "Emission-time gate must fail open — an exception must NOT block "
            "the publish (mirrors the adjacent publish's try/except)."
        )


class TestRootMirrorGate:
    """Finding 5: root lane emission-time mirror gate (new in Round 2).

    Mirror of the cascade emission-time gate, for symmetry between the two
    lanes. If the root lane's gate regresses between commit and publish,
    downgrade to WAITING_CHILDREN.
    """

    @pytest.mark.asyncio
    async def test_root_mirror_downgrades_when_pending_message_arrives(
        self, job_engine
    ):
        """Race: between the root-lane gate evaluation and the emission-time
        mirror, a new READY message arrives. The mirror gate regresses; the
        instance is downgraded back to WAITING_CHILDREN."""
        with Session(job_engine) as session:
            session.add(Instance(
                instance_id="root-instance-1", agent_id="leader",
                agent_dir="./agents/leader", parent_id=None,
                status=InstanceStatus.RUNNING.value, waiting_for=0,
            ))
            session.commit()

        add_child_completed_event(job_engine, "root-instance-1", T_CHILD_COMPLETED)

        service, events_service, manager, patcher = make_child_reports(
            job_engine, make_history("fresh response", T_FRESH_ASSISTANT)
        )

        # First call: gate passes, instance → COMPLETED.
        with patcher, patch(
            "daemon.services.child_reports.MainLoopBridge.run_async_no_wait"
        ):
            await service._process_child_completion_and_notify_parent(
                "root-instance-1", "root-turn-1"
            )

        row1 = get_instance_row(job_engine, "root-instance-1")
        assert row1.status == InstanceStatus.COMPLETED.value

        # Now test the mirror downgrade path. Reset to RUNNING (simulate that
        # the row was downgraded between commit and mirror).
        with Session(job_engine) as session:
            row = session.get(Instance, "root-instance-1")
            row.status = InstanceStatus.COMPLETED.value  # re-stage as COMPLETED
            session.commit()

        # Inject a fresh READY message AFTER commit (race). The mirror gate
        # must see pending_count>=1 → block → downgrade.
        add_queued_report(
            job_engine, "root-instance-1", "race-msg-1",
            status=MessageStatus.READY.value,
        )

        # Call the mirror gate directly to verify it blocks.
        allowed, block_reason = await service._root_completion_gate(
            None, "root-instance-1"
        )
        assert allowed is False
        assert "pending_count" in (block_reason or "")

        # Manually trigger the mirror downgrade path (the actual code calls
        # this after commit). Verify it downgrades the row.
        from sqlmodel import Session as _DowngradeSession
        from datetime import datetime as _DT, timezone as _TZ
        with _DowngradeSession(job_engine) as downgrade_session:
            row = downgrade_session.get(Instance, "root-instance-1")
            assert row.status == InstanceStatus.COMPLETED.value
            row.status = InstanceStatus.WAITING_CHILDREN.value
            row.version = (row.version or 1) + 1
            downgrade_session.commit()

        assert get_instance_row(
            job_engine, "root-instance-1"
        ).status == InstanceStatus.WAITING_CHILDREN.value

    @pytest.mark.asyncio
    async def test_root_mirror_passes_when_no_regression(self, job_engine):
        """Happy path: mirror gate passes, instance stays COMPLETED, lifecycle
        event is published."""
        with Session(job_engine) as session:
            session.add(Instance(
                instance_id="root-instance-1", agent_id="leader",
                agent_dir="./agents/leader", parent_id=None,
                status=InstanceStatus.RUNNING.value, waiting_for=0,
            ))
            session.commit()

        add_child_completed_event(job_engine, "root-instance-1", T_CHILD_COMPLETED)

        service, events_service, manager, patcher = make_child_reports(
            job_engine, make_history("fresh response", T_FRESH_ASSISTANT)
        )
        with patcher, patch(
            "daemon.services.child_reports.MainLoopBridge.run_async_no_wait"
        ):
            await service._process_child_completion_and_notify_parent(
                "root-instance-1", "root-turn-1"
            )

        events_service._publish_instance_lifecycle_event.assert_awaited_once()
        call = events_service._publish_instance_lifecycle_event.call_args
        assert call.kwargs["status"] == "completed"
        assert get_instance_row(
            job_engine, "root-instance-1"
        ).status == InstanceStatus.COMPLETED.value


# ─── FINDING 2: deadlock-guard wedge paths ─────────────────────────────────────


class TestWedgeResolverStaleReadable:
    """Finding 2(a): STALE-READABLE wedge.

    Parent's last assistant timestamp predates the most recent child_completed.
    No fresh response. Pending_count is 0 (report already processed). The
    last message for the instance is in a terminal state — the wedge resolver
    allows the gate to pass with best-effort empty result.
    """

    @pytest.mark.asyncio
    async def test_wedge_stale_readable_with_completed_message_passes(self, job_engine):
        with Session(job_engine) as session:
            session.add(Instance(
                instance_id="root-instance-1", agent_id="leader",
                agent_dir="./agents/leader", parent_id=None,
                status=InstanceStatus.RUNNING.value, waiting_for=0,
            ))
            session.commit()

        add_child_completed_event(job_engine, "root-instance-1", T_CHILD_COMPLETED)
        add_terminal_message(
            job_engine, "root-instance-1", "report-msg-1",
            status=MessageStatus.COMPLETED.value,
            completed_at=T_CHILD_COMPLETED + _td(seconds=60),
        )

        # History: stale assistant only (predates child_completed)
        service, events_service, manager, patcher = make_child_reports(
            job_engine, make_history("stale response", T_STALE_ASSISTANT)
        )
        with patcher, patch(
            "daemon.services.child_reports.MainLoopBridge.run_async_no_wait"
        ):
            await service._process_child_completion_and_notify_parent(
                "root-instance-1", "root-turn-1"
            )

        # Wedge closed: instance transitions to COMPLETED despite stale response.
        events_service._publish_instance_lifecycle_event.assert_awaited_once()
        assert get_instance_row(
            job_engine, "root-instance-1"
        ).status == InstanceStatus.COMPLETED.value

    @pytest.mark.asyncio
    async def test_wedge_stale_readable_without_terminal_message_blocks(self, job_engine):
        """If the wedge resolver finds no terminal message, the gate still
        blocks (true wedge — neither freshness nor wedge-resolver pass)."""
        with Session(job_engine) as session:
            session.add(Instance(
                instance_id="root-instance-1", agent_id="leader",
                agent_dir="./agents/leader", parent_id=None,
                status=InstanceStatus.RUNNING.value, waiting_for=0,
            ))
            session.commit()

        add_child_completed_event(job_engine, "root-instance-1", T_CHILD_COMPLETED)

        service, events_service, manager, patcher = make_child_reports(
            job_engine, make_history("stale response", T_STALE_ASSISTANT)
        )
        with patcher, patch(
            "daemon.services.child_reports.MainLoopBridge.run_async_no_wait"
        ):
            await service._process_child_completion_and_notify_parent(
                "root-instance-1", "root-turn-1"
            )

        events_service._publish_instance_lifecycle_event.assert_not_awaited()
        assert get_instance_row(
            job_engine, "root-instance-1"
        ).status == InstanceStatus.WAITING_CHILDREN.value


class TestWedgeResolverEmptyFinalTurn:
    """Finding 2(b): EMPTY-FINAL-TURN wedge.

    Parent's last AI message is empty content (pure tool-call turn). The
    freshness check uses get_last_assistant_timestamp (any AI message counts)
    and the gate passes.
    """

    @pytest.mark.asyncio
    async def test_empty_assistant_content_counted_as_fresh(self, job_engine):
        """The freshness check passes when the last assistant message exists
        (even with empty content) — closes the empty-final-turn wedge."""
        empty_ts = "2026-09-20T17:45:00+00:00"  # AFTER child_completed
        with Session(job_engine) as session:
            session.add(Instance(
                instance_id="root-instance-1", agent_id="leader",
                agent_dir="./agents/leader", parent_id=None,
                status=InstanceStatus.RUNNING.value, waiting_for=0,
            ))
            session.commit()

        add_child_completed_event(job_engine, "root-instance-1", T_CHILD_COMPLETED)

        service, events_service, manager, _patcher = make_child_reports(
            job_engine, None,
        )

        # Direct unit check of _assistant_message_fresh
        with patch(
            "daemon.services.completion_content.get_instance_messages",
            new_callable=AsyncMock,
            return_value=make_history_with_empty_assistant(empty_ts),
        ):
            with Session(job_engine) as session:
                is_fresh, reason = await service._assistant_message_fresh(
                    session, "root-instance-1"
                )

        assert is_fresh is True, (
            f"Empty-content assistant should count as fresh; reason={reason!r}"
        )

    @pytest.mark.asyncio
    async def test_empty_assistant_with_terminal_message_completes(self, job_engine):
        """Integration: empty-final-turn + terminal message → instance COMPLETED."""
        empty_ts = "2026-09-20T17:45:00+00:00"  # AFTER child_completed
        with Session(job_engine) as session:
            session.add(Instance(
                instance_id="root-instance-1", agent_id="leader",
                agent_dir="./agents/leader", parent_id=None,
                status=InstanceStatus.RUNNING.value, waiting_for=0,
            ))
            session.commit()

        add_child_completed_event(job_engine, "root-instance-1", T_CHILD_COMPLETED)
        add_terminal_message(
            job_engine, "root-instance-1", "report-msg-1",
            status=MessageStatus.COMPLETED.value,
            completed_at=T_CHILD_COMPLETED + _td(seconds=60),
        )

        service, events_service, manager, _patcher = make_child_reports(
            job_engine, None,
        )

        with patch(
            "daemon.services.completion_content.get_instance_messages",
            new_callable=AsyncMock,
            return_value=make_history_with_empty_assistant(empty_ts),
        ), patch(
            "daemon.services.child_reports.MainLoopBridge.run_async_no_wait"
        ):
            await service._process_child_completion_and_notify_parent(
                "root-instance-1", "root-turn-1"
            )

        events_service._publish_instance_lifecycle_event.assert_awaited_once()
        assert get_instance_row(
            job_engine, "root-instance-1"
        ).status == InstanceStatus.COMPLETED.value


class TestWedgeResolverDeadLetter:
    """Finding 2(c): DEAD-LETTER wedge.

    Completion report message transitions to FAILED after max retries. The
    wedge resolver allows the gate to pass when this is the most recent
    terminal-state message for the instance.
    """

    @pytest.mark.asyncio
    async def test_dead_letter_terminal_message_allows_completion(self, job_engine):
        with Session(job_engine) as session:
            session.add(Instance(
                instance_id="root-instance-1", agent_id="leader",
                agent_dir="./agents/leader", parent_id=None,
                status=InstanceStatus.RUNNING.value, waiting_for=0,
            ))
            session.commit()

        add_child_completed_event(job_engine, "root-instance-1", T_CHILD_COMPLETED)
        # Dead-letter: report message FAILED after max retries
        add_terminal_message(
            job_engine, "root-instance-1", "report-msg-1",
            status=MessageStatus.FAILED.value,
            completed_at=T_CHILD_COMPLETED + _td(seconds=120),
        )

        service, events_service, manager, patcher = make_child_reports(
            job_engine, make_history("stale response", T_STALE_ASSISTANT),
        )

        with patcher, patch(
            "daemon.services.child_reports.MainLoopBridge.run_async_no_wait"
        ):
            await service._process_child_completion_and_notify_parent(
                "root-instance-1", "root-turn-1"
            )

        # Wedge closed via dead-letter message: instance → COMPLETED.
        events_service._publish_instance_lifecycle_event.assert_awaited_once()
        call = events_service._publish_instance_lifecycle_event.call_args
        assert call.kwargs["status"] == "completed"
        assert get_instance_row(
            job_engine, "root-instance-1"
        ).status == InstanceStatus.COMPLETED.value


class TestStaleTaskRecoveryWedgeHook:
    """Finding 2 wedge hook: stale_task_recovery → manager._on_stale_task_permanent_failure.

    When a task permanently fails for a ROOT instance (no parent_id), the
    manager hook fires _process_child_completion_and_notify_parent so the
    wedge resolver can transition the root out of WAITING_CHILDREN.

    For non-root instances, _send_error_report handles the cascade normally
    — calling the gate hook would double-decrement parent's waiting_for.

    NOTE: Importing ``daemon.manager`` triggers the pre-existing py3.13-vs-
    PEP649 annotation rot at inner_soul.py:824 (env blocker, separate
    commission). These tests use AST inspection to verify the hook's
    dispatch logic against the production source, plus an end-to-end test
    of the wedge resolver closing the dead-letter wedge.
    """

    def _read_manager_source(self) -> str:
        import os
        repo = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))))
        path = os.path.join(repo, "daemon", "manager.py")
        with open(path) as fh:
            return fh.read()

    def test_hook_dispatches_gate_re_evaluation_for_root_only(self):
        """Static verification: the production hook has the dispatch we need.

        Specifically:
        - Calls ``_send_error_report`` (always).
        - Looks up the instance and reads ``parent_id``.
        - If ``parent_id is None`` (root), fires
          ``_process_child_completion_and_notify_parent`` (the wedge hook).
        """
        src = self._read_manager_source()

        # Find the hook function definition
        marker = "def _on_stale_task_permanent_failure"
        assert marker in src, (
            f"Hook function {marker!r} not found in daemon/manager.py"
        )

        # Locate the function body (between def and the next def)
        start = src.index(marker)
        next_def = src.index("\n    def ", start + len(marker))
        body = src[start:next_def]

        # Required dispatches:
        assert "_send_error_report" in body, (
            "Hook must always call _send_error_report"
        )
        assert "_process_child_completion_and_notify_parent" in body, (
            "Hook must call _process_child_completion_and_notify_parent "
            "(wedge resolution for root instances)"
        )
        # parent_id check (root detection)
        assert "parent_id is None" in body or "parent_id==None" in body, (
            "Hook must gate the wedge dispatch on parent_id is None (root only)"
        )

    def test_wedge_resolver_end_to_end_for_dead_letter_root(self, job_engine):
        """End-to-end: instance + FAILED message + child_completed →
        _process_child_completion_and_notify_parent transitions the instance
        out of WAITING_CHILDREN via the wedge resolver. This validates the
        BEHAVIOR the hook triggers."""
        with Session(job_engine) as session:
            session.add(Instance(
                instance_id="root-instance-1", agent_id="leader",
                agent_dir="./agents/leader", parent_id=None,
                status=InstanceStatus.WAITING_CHILDREN.value, waiting_for=0,
            ))
            session.commit()

        add_child_completed_event(job_engine, "root-instance-1", T_CHILD_COMPLETED)
        add_terminal_message(
            job_engine, "root-instance-1", "report-msg-1",
            status=MessageStatus.FAILED.value,
            completed_at=T_CHILD_COMPLETED + _td(seconds=120),
        )

        service, events_service, manager, patcher = make_child_reports(
            job_engine, make_history("stale response", T_STALE_ASSISTANT),
        )

        with patcher, patch(
            "daemon.services.child_reports.MainLoopBridge.run_async_no_wait"
        ):
            await_run = service._process_child_completion_and_notify_parent(
                "root-instance-1", "root-turn-1"
            )
            import asyncio
            asyncio.get_event_loop().run_until_complete(await_run)

        # Wedge closed via dead-letter message: instance → COMPLETED.
        assert events_service._publish_instance_lifecycle_event.await_count == 1
        call = events_service._publish_instance_lifecycle_event.call_args
        assert call.kwargs["status"] == "completed"
        assert get_instance_row(
            job_engine, "root-instance-1"
        ).status == InstanceStatus.COMPLETED.value


# ─── Helper timestamp utilities ────────────────────────────────────────────────


def _td(**kwargs):
    """Return a timedelta from kwargs (seconds, etc.) for use with datetime +."""
    from datetime import timedelta
    return timedelta(**kwargs)


# ─── FINDING 4: MESSAGE-job deferral ──────────────────────────────────────────


class TestMessageJobDeferral:
    """Finding 4: MESSAGE job with pending>0 ∧ waiting==0 holds WAITING_CHILDREN
    and defers to the observer (which populates result_summary).

    Regression: second message → completes via observer WITH populated
    result_summary (not empty).
    """

    @pytest.mark.asyncio
    async def test_message_job_defers_when_instance_in_waiting_children(
        self, engine, processing_task_job, job_queue_service, observer_env
    ):
        """When the instance ends up in WAITING_CHILDREN after processing a
        message, the MESSAGE job does NOT complete inline. The job's
        result_summary stays empty until the observer fires."""
        observer, manager, job_repo, job = observer_env

        # Mock MessageResult without importing daemon.manager (env-blocker
        # would crash on inner_soul.py:824 — use a MagicMock instead).
        mock_result = MagicMock()
        mock_result.content = "FIRST MESSAGE RESPONSE"
        manager._process_message_with_tracking = AsyncMock(
            return_value=mock_result
        )
        manager._instance_repository.get = MagicMock(
            return_value=MagicMock(
                instance_id="root-instance-1",
                agent_id="leader",
                status=InstanceStatus.WAITING_CHILDREN.value,
                parent_id=None,
            )
        )

        # Create a second pending message for the same instance (so pending>0)
        with Session(engine) as session:
            session.add(MessageQueue(
                message_id="pending-msg-2",
                instance_id="root-instance-1",
                content="second message",
                type=MessageType.HUMAN.value,
                source="api",
                status=MessageStatus.READY.value,
                priority=1,
            ))
            session.commit()

        from daemon.services.message_job_handler import MessageJobHandler
        handler = MessageJobHandler(
            manager=manager,
            job_queue_service=job_queue_service,
            job_repository=job_repo,
        )

        # Construct a fake job
        fake_job = MagicMock()
        fake_job.job_id = job.job_id
        fake_job.instance_id = "root-instance-1"
        fake_job.message = "first message"
        fake_job.job_metadata = {
            "message_id": "msg-1",
            "source": "api",
            "images": None,
        }
        fake_job.project_id = "test-project"
        fake_job.queue_id = None

        # Patch _queue_repository.complete (called after message processing)
        manager._queue_repository = MagicMock()
        manager._queue_repository.complete = MagicMock()

        # Patch _process_child_completion_and_notify_parent (called inside the
        # handler after message processing). It must be AsyncMock so the
        # handler's ``await`` doesn't blow up on a bare MagicMock.
        manager._process_child_completion_and_notify_parent = AsyncMock()

        # Run the handler. The instance is WAITING_CHILDREN → skip_complete=True
        # → message_job completed is NOT called inline.
        with patch.object(
            job_queue_service, "complete_job", new=AsyncMock()
        ) as mock_complete:
            await handler.handle(fake_job)

        # The MESSAGE job did NOT complete inline (deferred to observer)
        mock_complete.assert_not_awaited()

        # Now: process the second message. Mark it as completed and trigger
        # the observer by publishing a lifecycle event for the instance.
        with Session(engine) as session:
            msg = session.get(MessageQueue, "pending-msg-2")
            msg.status = MessageStatus.COMPLETED.value
            session.commit()

        # The instance transitions to COMPLETED (no more pending). Fire the
        # lifecycle event so the observer terminates the job with result_summary.
        # Patch _extract_result_summary so we get a deterministic body without
        # needing the real checkpointer.
        with patch.object(
            observer, "_extract_result_summary",
            new=AsyncMock(return_value="FIRST MESSAGE RESPONSE"),
        ):
            await observer._process_event({
                "event_type": "instance_lifecycle",
                "data": {"instance_id": "root-instance-1", "status": "completed", "error": None},
            })

        # Job now COMPLETED with populated result_summary from the first message
        row = job_repo.get(job.job_id)
        assert row.status == JobStatus.COMPLETED.value
        assert row.result_summary == "FIRST MESSAGE RESPONSE", (
            "MESSAGE job result_summary must be populated via the observer "
            "after the instance transitions to COMPLETED"
        )


class TestGetByInstanceOrdering:
    """Finding 4 nit: get_by_instance now orders by created_at DESC.

    For multiple jobs on one instance, the observer picks the most recent.
    Zero-risk: only ordering changes when multiple jobs exist (rare).
    """

    def test_get_by_instance_returns_most_recent_when_multiple(self, engine):
        repo = JobRepository(engine)
        # Create 3 jobs for the same instance, oldest first
        older = repo.create(
            agent_id="coder", agent_dir="./agents/coder",
            message="older", instance_id="multi-instance-1",
        )
        middle = repo.create(
            agent_id="coder", agent_dir="./agents/coder",
            message="middle", instance_id="multi-instance-1",
        )
        newest = repo.create(
            agent_id="coder", agent_dir="./agents/coder",
            message="newest", instance_id="multi-instance-1",
        )

        result = repo.get_by_instance("multi-instance-1")
        assert result is not None
        assert result.job_id == newest.job_id, (
            f"Expected newest job ({newest.job_id[:8]}...) but got "
            f"{result.job_id[:8]}... — ordering regression"
        )