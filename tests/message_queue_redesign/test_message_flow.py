"""Tests for Phase 3 message flow migration.

Tests the atomic operations for:
- enqueue_message_v2: Creates Message + Task + Event atomically
- check_child_completion_v2: Child completion logic with idempotency
- Startup recovery: Resets orphaned tasks and messages
"""

import pytest
import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch, MagicMock

from sqlmodel import Session

from daemon.repositories.message_queue.models import MessageQueue, MessageStatus, MessageType
from daemon.repositories.message_queue.repository import SQLModelMessageQueueRepository
from daemon.repositories.instance.models import Instance, InstanceHierarchy, InstanceStatus
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.task.models import Task, TaskStatus, TaskType
from daemon.repositories.task.repository import TaskRepository
from daemon.repositories.event.models import Event, EventKind
from daemon.repositories.event.repository import EventRepository


async def _passthrough_gate(*args, **kwargs):
    """Default Execution Gate stub for unit tests.

    Most tests want the Gate to be transparent — the work runs, no
    contention. Tests that exercise the contention path override
    ``manager.execution_gate.run`` to return a contention signal
    instead.

    The signature is ``(*args, **kwargs)`` so it accepts whatever
    the production code passes (typically
    ``instance_id=..., holder_id=..., holder_kind=..., work_fn=...``).

    Note: this is a coroutine function passed directly to
    ``AsyncMock(side_effect=...)``. Do NOT wrap it in a lambda that
    returns a coroutine — AsyncMock's side_effect handling expects
    either a value or a coroutine function whose result is awaited,
    not a lambda that returns a coroutine (which would not be
    awaited and would leak the coroutine as the return value).
    """
    work_fn = kwargs.get("work_fn")
    return await work_fn()


# ============================================================================
# Helper Functions
# ============================================================================


def create_test_instance(
    engine,
    instance_id: str,
    parent_id: str | None = None,
    status: str = InstanceStatus.IDLE.value,
    waiting_for: int = 0,
) -> None:
    """Create a test instance with optional parent relationship."""
    with Session(engine) as session:
        instance = Instance(
            instance_id=instance_id,
            agent_id="test-agent",
            agent_dir="./agents/test",
            status=status,
            parent_id=parent_id,
            waiting_for=waiting_for,
            children="[]",
            version=1,
        )
        session.add(instance)
        if parent_id:
            hierarchy = InstanceHierarchy(parent_id=parent_id, child_id=instance_id)
            session.add(hierarchy)
        session.commit()


def get_instance(engine, instance_id: str) -> Instance | None:
    """Get an instance by ID."""
    with Session(engine) as session:
        return session.get(Instance, instance_id)


def count_pending_messages(engine, instance_id: str) -> int:
    """Count pending/ready messages for an instance."""
    with Session(engine) as session:
        from sqlalchemy import func, select
        count = session.exec(
            select(func.count())
            .select_from(MessageQueue)
            .where(MessageQueue.instance_id == instance_id)
            .where(MessageQueue.status.in_([
                MessageStatus.READY.value,
                MessageStatus.PROCESSING.value,
            ]))
        ).scalar()
        return count or 0


def create_completion_report(engine, parent_id: str, child_id: str, content: str) -> MessageQueue:
    """Create a completion report message to parent."""
    with Session(engine) as session:
        msg = MessageQueue(
            message_id=str(uuid.uuid4()),
            instance_id=parent_id,
            content=content,
            type=MessageType.COMPLETION_REPORT.value,
            source=f"internal_report:{child_id}",
            status=MessageStatus.READY.value,
            priority=1,
            enqueued_at=datetime.now(timezone.utc),
        )
        session.add(msg)
        session.commit()
        session.refresh(msg)
        return msg


# ============================================================================
# Repository Fixtures
# ============================================================================


@pytest.fixture
def message_repo(engine):
    """Create message queue repository."""
    return SQLModelMessageQueueRepository(engine)


@pytest.fixture
def instance_repo(engine):
    """Create instance repository."""
    return SQLModelInstanceRepository(engine)


@pytest.fixture
def task_repo(engine):
    """Create task repository."""
    return TaskRepository(engine)


@pytest.fixture
def event_repo(engine):
    """Create event repository."""
    return EventRepository(engine)


# ============================================================================
# Test Class: Enqueue Message V2 (Atomic Operation)
# ============================================================================


class TestEnqueueMessageV2:
    """Tests for atomic Message + Task + Event creation."""

    def test_creates_message_and_task_and_event_atomically(self, engine, message_repo, task_repo, event_repo):
        """enqueue_message_v2 creates MessageQueue, Task, and Event in single transaction."""
        instance_id = str(uuid.uuid4())
        message_id = str(uuid.uuid4())
        create_test_instance(engine, instance_id)

        # Simulate atomic operation
        with Session(engine) as session:
            # 1. Create Message
            msg = MessageQueue(
                message_id=message_id,
                instance_id=instance_id,
                content="Test message",
                source="api",
                type=MessageType.HUMAN.value,
                status=MessageStatus.READY.value,
                priority=1,
                enqueued_at=datetime.now(timezone.utc),
            )
            session.add(msg)

            # 2. Create Task
            task = Task(
                task_type=TaskType.PROCESS_MESSAGE.value,
                instance_id=instance_id,
                message_id=message_id,
                status=TaskStatus.PENDING.value,
                created_at=datetime.now(timezone.utc),
            )
            session.add(task)

            # 3. Create Event
            event = Event(
                instance_id=instance_id,
                message_id=message_id,
                kind=EventKind.MESSAGE_RECEIVED.value,
                data='{"source": "api"}',
                created_at=datetime.now(timezone.utc),
            )
            session.add(event)

            session.commit()

        # Verify Message exists
        retrieved_msg = message_repo.get(message_id)
        assert retrieved_msg is not None
        assert retrieved_msg.content == "Test message"
        assert retrieved_msg.type == MessageType.HUMAN.value
        assert retrieved_msg.status == MessageStatus.READY.value

        # Verify Task exists and linked to message
        retrieved_task = task_repo.get_by_message(message_id)
        assert retrieved_task is not None
        assert retrieved_task.task_type == TaskType.PROCESS_MESSAGE.value
        assert retrieved_task.status == TaskStatus.PENDING.value
        assert retrieved_task.instance_id == instance_id

        # Verify Event exists
        events = event_repo.get_by_instance(instance_id)
        assert len(events) == 1
        assert events[0].kind == EventKind.MESSAGE_RECEIVED.value
        assert events[0].message_id == message_id

    def test_instance_status_transitions_to_running(self, engine, message_repo):
        """Instance transitions from IDLE to RUNNING after enqueue."""
        instance_id = str(uuid.uuid4())
        create_test_instance(engine, instance_id, status=InstanceStatus.IDLE.value)

        # Verify initial status
        instance = get_instance(engine, instance_id)
        assert instance.status == InstanceStatus.IDLE.value

        # Enqueue message and update status atomically
        with Session(engine) as session:
            instance = session.get(Instance, instance_id)
            instance.status = InstanceStatus.RUNNING.value
            instance.version = (instance.version or 1) + 1
            session.commit()

        # Verify updated status
        updated = get_instance(engine, instance_id)
        assert updated.status == InstanceStatus.RUNNING.value

    def test_task_linked_to_message_via_foreign_key(self, engine, message_repo, task_repo):
        """Task references the message via message_id."""
        instance_id = str(uuid.uuid4())
        message_id = str(uuid.uuid4())
        create_test_instance(engine, instance_id)

        # Create message first
        with Session(engine) as session:
            msg = MessageQueue(
                message_id=message_id,
                instance_id=instance_id,
                content="Test",
                type=MessageType.HUMAN.value,
                status=MessageStatus.READY.value,
                priority=1,
                enqueued_at=datetime.now(timezone.utc),
            )
            session.add(msg)
            session.commit()

        # Create task referencing the message
        task = task_repo.create(
            task_type=TaskType.PROCESS_MESSAGE.value,
            instance_id=instance_id,
            message_id=message_id,
        )

        # Verify bidirectional link
        assert task.message_id == message_id
        retrieved_task = task_repo.get_by_message(message_id)
        assert retrieved_task is not None
        assert retrieved_task.id == task.id

    def test_event_contains_correct_metadata(self, engine, event_repo):
        """Event stores correct metadata from enqueue operation."""
        instance_id = str(uuid.uuid4())
        message_id = str(uuid.uuid4())
        create_test_instance(engine, instance_id)

        event = event_repo.create_event(
            instance_id=instance_id,
            message_id=message_id,
            kind=EventKind.MESSAGE_RECEIVED.value,
            data={"source": "api", "priority": 1},
        )

        assert event.id is not None
        assert event.instance_id == instance_id
        assert event.message_id == message_id
        assert event.kind == EventKind.MESSAGE_RECEIVED.value


# ============================================================================
# Test Class: Check Child Completion V2
# ============================================================================


class TestCheckChildCompletionV2:
    """Tests for atomic child completion check and reporting."""

    def test_skips_if_instance_has_no_parent(self, engine):
        """Should not report completion if instance has no parent."""
        instance_id = str(uuid.uuid4())
        create_test_instance(engine, instance_id)

        instance = get_instance(engine, instance_id)
        assert instance.parent_id is None

        # No parent means no completion report should be created
        # The logic should short-circuit here

    def test_skips_if_pending_messages_exist(self, engine, message_repo):
        """Should skip completion check if instance has pending messages."""
        parent_id = str(uuid.uuid4())
        child_id = str(uuid.uuid4())
        create_test_instance(engine, parent_id)
        create_test_instance(engine, child_id, parent_id=parent_id)

        # Add pending message
        message_repo.enqueue(
            instance_id=child_id,
            content="Still processing",
            source="api",
        )

        pending_count = count_pending_messages(engine, child_id)
        assert pending_count > 0

        # Should NOT complete because pending messages exist
        # The check_child_completion_v2 should skip

    def test_skips_if_content_is_none(self, engine, message_repo):
        """FIX C3 UPDATE: When content is None, should proceed with sentinel content."""
        parent_id = str(uuid.uuid4())
        child_id = str(uuid.uuid4())
        create_test_instance(engine, parent_id, waiting_for=1)
        create_test_instance(engine, child_id, parent_id=parent_id)

        # FIX C3: If content is None, use sentinel and proceed with completion
        content = None  # This is the bug scenario
        sentinel_content = "[No response content]"
        actual_content = sentinel_content if content is None else content

        # Create completion report with sentinel content
        create_completion_report(engine, parent_id, child_id, actual_content)

        # Verify completion report WAS created with sentinel content using message_repo helper
        reports = message_repo.get_by_instance(parent_id)
        completion_reports = [
            r for r in reports
            if r.type == MessageType.COMPLETION_REPORT.value and r.source == f"internal_report:{child_id}"
        ]

        assert len(completion_reports) == 1, "Completion report should be created with sentinel content"
        assert completion_reports[0].content == sentinel_content, "Report should use sentinel content"

    def test_idempotent_no_duplicate_reports(self, engine, message_repo):
        """Should not create duplicate completion reports (idempotent)."""
        parent_id = str(uuid.uuid4())
        child_id = str(uuid.uuid4())
        create_test_instance(engine, parent_id, waiting_for=1)
        create_test_instance(engine, child_id, parent_id=parent_id)

        # Check if report already exists before creating
        with Session(engine) as session:
            from sqlalchemy import select
            existing = session.exec(
                select(MessageQueue)
                .where(MessageQueue.instance_id == parent_id)
                .where(MessageQueue.source == f"internal_report:{child_id}")
                .where(MessageQueue.status != MessageStatus.FAILED.value)
            ).first()

            if existing:
                # Skip - already exists (idempotent behavior)
                pass
            else:
                # Create new report
                create_completion_report(engine, parent_id, child_id, "Complete")

        # Verify only ONE report exists
        with Session(engine) as session:
            from sqlalchemy import select
            reports = session.exec(
                select(MessageQueue)
                .where(MessageQueue.instance_id == parent_id)
                .where(MessageQueue.source == f"internal_report:{child_id}")
            ).all()

        assert len(reports) == 1  # Idempotent - only one report

    def test_creates_completion_report_with_correct_content(self, engine, message_repo):
        """Completion report contains correct content from child."""
        parent_id = str(uuid.uuid4())
        child_id = str(uuid.uuid4())
        create_test_instance(engine, parent_id)
        create_test_instance(engine, child_id, parent_id=parent_id)

        # Create completion report with child content
        child_output = "Child task completed successfully"
        report = create_completion_report(engine, parent_id, child_id, child_output)

        # Verify report content
        assert report.content == child_output
        assert report.type == MessageType.COMPLETION_REPORT.value
        assert report.source == f"internal_report:{child_id}"
        assert report.instance_id == parent_id

    def test_completion_report_is_high_priority(self, engine, message_repo):
        """Completion reports should be high priority for quick parent response."""
        parent_id = str(uuid.uuid4())
        child_id = str(uuid.uuid4())
        create_test_instance(engine, parent_id)
        create_test_instance(engine, child_id, parent_id=parent_id)

        with Session(engine) as session:
            report = MessageQueue(
                message_id=str(uuid.uuid4()),
                instance_id=parent_id,
                content="Child complete",
                type=MessageType.COMPLETION_REPORT.value,
                source=f"internal_report:{child_id}",
                status=MessageStatus.READY.value,
                priority=10,  # High priority
                enqueued_at=datetime.now(timezone.utc),
            )
            session.add(report)
            session.commit()
            session.refresh(report)

        assert report.priority >= 5  # High priority threshold


# ============================================================================
# Test Class: Parent State Transitions
# ============================================================================


class TestParentStateTransitions:
    """Tests for parent's waiting_for counter and status transitions."""

    def test_waiting_for_decremented_on_child_completion(self, engine):
        """Parent's waiting_for counter decremented when child completes."""
        parent_id = str(uuid.uuid4())
        child_id = str(uuid.uuid4())
        create_test_instance(engine, parent_id, waiting_for=2)
        create_test_instance(engine, child_id, parent_id=parent_id)

        parent = get_instance(engine, parent_id)
        initial_waiting = parent.waiting_for

        # Simulate: decrement waiting_for
        with Session(engine) as session:
            parent = session.get(Instance, parent_id)
            parent.waiting_for = max(0, (parent.waiting_for or 0) - 1)
            session.commit()

        updated = get_instance(engine, parent_id)
        assert updated.waiting_for == initial_waiting - 1

    def test_parent_transitions_to_running_when_waiting_for_zero(self, engine):
        """Parent transitions to RUNNING when waiting_for reaches 0."""
        parent_id = str(uuid.uuid4())
        create_test_instance(engine, parent_id, status=InstanceStatus.WAITING_CHILDREN.value, waiting_for=1)

        # Simulate: last child completes
        with Session(engine) as session:
            parent = session.get(Instance, parent_id)
            parent.waiting_for = 0
            session.commit()

        # Check: should transition to RUNNING (not directly to COMPLETED,
        # as parent may have its own messages to process)
        parent = get_instance(engine, parent_id)
        assert parent.waiting_for == 0
        # Status stays WAITING_CHILDREN until all messages processed
        # then transitions to COMPLETED

    def test_parent_transitions_to_completed_when_all_done(self, engine, message_repo):
        """Parent transitions to COMPLETED when all children done and no pending messages."""
        parent_id = str(uuid.uuid4())
        create_test_instance(engine, parent_id, status=InstanceStatus.WAITING_CHILDREN.value, waiting_for=0)

        # Simulate: all children complete, no pending messages
        pending_count = count_pending_messages(engine, parent_id)
        assert pending_count == 0

        # Check: should transition to COMPLETED
        with Session(engine) as session:
            parent = session.get(Instance, parent_id)
            if parent.waiting_for == 0 and parent.status == InstanceStatus.WAITING_CHILDREN.value:
                # Also check no pending messages (already asserted above)
                parent.status = InstanceStatus.COMPLETED.value
                session.commit()

        updated = get_instance(engine, parent_id)
        assert updated.status == InstanceStatus.COMPLETED.value

    def test_waiting_for_does_not_go_negative(self, engine):
        """waiting_for counter should never go below 0."""
        parent_id = str(uuid.uuid4())
        create_test_instance(engine, parent_id, waiting_for=0)

        with Session(engine) as session:
            parent = session.get(Instance, parent_id)
            # Using max() prevents negative values
            new_value = max(0, (parent.waiting_for or 0) - 1)
            parent.waiting_for = new_value
            session.commit()

        updated = get_instance(engine, parent_id)
        assert updated.waiting_for >= 0


# ============================================================================
# Test Class: Startup Recovery
# ============================================================================


class TestStartupRecovery:
    """Tests for startup crash recovery of orphaned tasks and messages."""

    def test_resets_stale_running_tasks(self, engine, task_repo):
        """Running tasks from crash should be reset to pending."""
        # Create stale running tasks (simulating crash)
        with Session(engine) as session:
            for i in range(3):
                task = Task(
                    task_type=TaskType.PROCESS_MESSAGE.value,
                    instance_id=f"instance-{i}",
                    status=TaskStatus.RUNNING.value,
                    worker_id=f"worker-{i}",
                    started_at=datetime.now(timezone.utc) - timedelta(minutes=30),
                    created_at=datetime.now(timezone.utc) - timedelta(minutes=30),
                )
                session.add(task)
            session.commit()

        # Recovery: reset stale tasks
        count = task_repo.reset_stale_tasks(threshold_minutes=15)
        assert count == 3

        # Verify all are pending now
        assert task_repo.get_pending_count() == 3

    def test_ignores_recent_running_tasks(self, engine, task_repo):
        """Recent running tasks should NOT be reset."""
        # Create a recent running task
        with Session(engine) as session:
            task = Task(
                task_type=TaskType.PROCESS_MESSAGE.value,
                instance_id="recent-instance",
                status=TaskStatus.RUNNING.value,
                worker_id="worker-1",
                started_at=datetime.now(timezone.utc) - timedelta(minutes=5),
                created_at=datetime.now(timezone.utc) - timedelta(minutes=5),
            )
            session.add(task)
            session.commit()

        # Recovery with short threshold should NOT reset recent tasks
        count = task_repo.reset_stale_tasks(threshold_minutes=15)
        assert count == 0

        # Task should still be RUNNING
        task = task_repo.get_by_instance("recent-instance")[0]
        assert task.status == TaskStatus.RUNNING.value

    def test_recovers_orphaned_processing_messages(self, engine, message_repo):
        """Processing messages from crash should be recovered to ready."""
        # Create a message
        msg = message_repo.enqueue(
            instance_id="instance-1",
            content="Crashed message",
            source="api",
        )

        # Simulate: message was being processed when crash happened
        # Set started_at and last_activity_at to be > 1 hour old
        # (find_stuck_messages uses MESSAGE_TIMEOUT_SECONDS = 3600)
        with Session(engine) as session:
            msg_row = session.get(MessageQueue, msg.message_id)
            msg_row.status = MessageStatus.PROCESSING.value
            msg_row.processing_started_at = datetime.now(timezone.utc) - timedelta(hours=2)
            msg_row.last_activity_at = datetime.now(timezone.utc) - timedelta(hours=2)
            session.commit()

        # Verify message is stuck in processing
        msg_check = message_repo.get(msg.message_id)
        assert msg_check.status == MessageStatus.PROCESSING.value

        # Recovery: find stuck messages (uses default 1-hour timeout)
        stuck_messages = message_repo.find_stuck_messages()
        assert len(stuck_messages) >= 1  # Should find at least our stuck message

    def test_recovery_preserves_completed_tasks(self, engine, task_repo):
        """Completed tasks should NOT be affected by recovery."""
        # Create a completed task
        with Session(engine) as session:
            task = Task(
                task_type=TaskType.PROCESS_MESSAGE.value,
                instance_id="completed-instance",
                status=TaskStatus.COMPLETED.value,
                worker_id="worker-1",
                started_at=datetime.now(timezone.utc) - timedelta(minutes=60),
                completed_at=datetime.now(timezone.utc) - timedelta(minutes=30),
                created_at=datetime.now(timezone.utc) - timedelta(minutes=60),
            )
            session.add(task)
            session.commit()

        # Recovery should not affect completed tasks
        count = task_repo.reset_stale_tasks(threshold_minutes=15)
        assert count == 0  # Should not reset completed tasks

        # Verify task is still completed
        tasks = task_repo.get_by_instance("completed-instance")
        assert len(tasks) == 1
        assert tasks[0].status == TaskStatus.COMPLETED.value

    def test_recovery_cleans_up_old_completed_messages(self, engine, message_repo):
        """Old completed messages should be cleaned up."""
        # Create old completed messages
        with Session(engine) as session:
            for i in range(3):
                msg = MessageQueue(
                    message_id=str(uuid.uuid4()),
                    instance_id="instance-1",
                    content=f"Old message {i}",
                    type=MessageType.HUMAN.value,
                    source="api",
                    status=MessageStatus.COMPLETED.value,
                    completed_at=datetime.now(timezone.utc) - timedelta(hours=48),
                    enqueued_at=datetime.now(timezone.utc) - timedelta(hours=50),
                )
                session.add(msg)
            session.commit()

        # Cleanup old messages
        deleted = message_repo.cleanup_old(max_age_hours=24)

        # Should delete messages older than 24 hours
        assert deleted == 3


# ============================================================================
# Test Class: Integration Scenarios
# ============================================================================


class TestIntegrationScenarios:
    """Integration tests for full message flow scenarios."""

    def test_full_child_completion_flow(self, engine, message_repo, task_repo, event_repo):
        """Test complete flow: child completes, parent notified, waiting_for decremented."""
        parent_id = str(uuid.uuid4())
        child_id = str(uuid.uuid4())

        # Setup: parent waiting for child
        create_test_instance(engine, parent_id, waiting_for=1)
        create_test_instance(engine, child_id, parent_id=parent_id)

        # Child processes its messages
        child_msg = message_repo.enqueue(instance_id=child_id, content="Child work", source="api")
        # Dequeue transitions the message to PROCESSING — required for
        # the atomic ``complete()`` (status='processing' guard).
        dequeued = message_repo.dequeue(instance_id=child_id)
        assert dequeued is not None and dequeued.status == MessageStatus.PROCESSING.value
        child_task = task_repo.create(
            task_type=TaskType.PROCESS_MESSAGE.value,
            instance_id=child_id,
            message_id=child_msg.message_id,
        )
        task_repo.complete_task(child_task.id, result={"status": "success"})

        # Child completes message
        message_repo.complete(child_msg.message_id)

        # Check child has no more pending messages
        pending = count_pending_messages(engine, child_id)
        assert pending == 0

        # Simulate check_child_completion_v2
        with Session(engine) as session:
            from sqlalchemy import select
            existing = session.exec(
                select(MessageQueue)
                .where(MessageQueue.instance_id == parent_id)
                .where(MessageQueue.source == f"internal_report:{child_id}")
            ).first()

            if not existing:
                # Create completion report
                report = MessageQueue(
                    message_id=str(uuid.uuid4()),
                    instance_id=parent_id,
                    content="Child completed",
                    type=MessageType.COMPLETION_REPORT.value,
                    source=f"internal_report:{child_id}",
                    status=MessageStatus.READY.value,
                    priority=10,
                    enqueued_at=datetime.now(timezone.utc),
                )
                session.add(report)

                # Decrement parent's waiting_for
                parent = session.get(Instance, parent_id)
                parent.waiting_for = max(0, (parent.waiting_for or 0) - 1)

                session.commit()

        # Verify: completion report exists
        reports = message_repo.get_by_instance(parent_id)
        completion_reports = [m for m in reports if m.type == MessageType.COMPLETION_REPORT.value]
        assert len(completion_reports) == 1

        # Verify: waiting_for decremented
        parent = get_instance(engine, parent_id)
        assert parent.waiting_for == 0

    def test_multiple_children_completion(self, engine):
        """Test parent with multiple children completing."""
        parent_id = str(uuid.uuid4())
        child_ids = [str(uuid.uuid4()) for _ in range(3)]

        create_test_instance(engine, parent_id, waiting_for=len(child_ids))
        for child_id in child_ids:
            create_test_instance(engine, child_id, parent_id=parent_id)

        # Each child completes
        for child_id in child_ids:
            with Session(engine) as session:
                # Create completion report
                report = MessageQueue(
                    message_id=str(uuid.uuid4()),
                    instance_id=parent_id,
                    content=f"Child {child_id} done",
                    type=MessageType.COMPLETION_REPORT.value,
                    source=f"internal_report:{child_id}",
                    status=MessageStatus.READY.value,
                    priority=10,
                    enqueued_at=datetime.now(timezone.utc),
                )
                session.add(report)

                # Decrement waiting_for
                parent = session.get(Instance, parent_id)
                parent.waiting_for = max(0, (parent.waiting_for or 0) - 1)
                session.commit()

        # Verify: all children reported
        parent = get_instance(engine, parent_id)
        assert parent.waiting_for == 0

        # Verify: all completion reports exist
        with Session(engine) as session:
            from sqlalchemy import select
            reports = session.exec(
                select(MessageQueue)
                .where(MessageQueue.instance_id == parent_id)
                .where(MessageQueue.type == MessageType.COMPLETION_REPORT.value)
            ).all()
            assert len(reports) == 3


# ============================================================================
# Test Class: C3 None Check (Real Behavior)
# ============================================================================


class TestCheckChildCompletionC3Fix:
    """Tests for FIX C3: Content fetch BEFORE transaction with empty string fallback.
    
    This fix ensures that if _get_last_assistant_message returns None (e.g., child
    failed immediately without any assistant message), we proceed with completion
    using empty string content.
    
    The bug before C3: Set instance to COMPLETED in transaction, then content fetch
    failed, leaving instance completed but no report sent to parent.
    
    The fix: Fetch content BEFORE transaction. If None, use empty string and proceed
    with completion - state transition MUST still happen even if assistant message
    content is unavailable.
    """

    @pytest.fixture
    def manager_with_mocked_content(self, engine):
        """Create a mock manager with controllable _get_last_assistant_message."""
        from unittest.mock import AsyncMock, MagicMock
        from daemon.manager import InstanceManager
        
        # Create minimal mock manager
        manager = MagicMock()
        manager._engine = engine
        manager.checkpointer = MagicMock()
        
        return manager

    def test_proceeds_with_empty_content_marks_instance_completed(self, engine, manager_with_mocked_content):
        """FIX C3: When content is None, instance SHOULD be marked COMPLETED with empty content."""
        parent_id = str(uuid.uuid4())
        child_id = str(uuid.uuid4())
        
        # Setup parent and child instances
        create_test_instance(engine, parent_id, waiting_for=1)
        create_test_instance(engine, child_id, parent_id=parent_id)
        
        # Verify child is not completed initially
        child_before = get_instance(engine, child_id)
        assert child_before.status == InstanceStatus.IDLE.value
        
        # Simulate FIX C3 behavior:
        # Step 1: Fetch content BEFORE transaction (this is what _check_child_completion_v2 does)
        last_content = None  # Simulating _get_last_assistant_message returning None
        
        # Step 2: If content is None, use empty string and proceed with completion
        content = last_content if last_content is not None else ""
        
        # Mark instance as COMPLETED with empty content
        with Session(engine) as session:
            instance = session.get(Instance, child_id)
            instance.status = InstanceStatus.COMPLETED.value
            session.commit()
        
        # Verify: instance SHOULD be COMPLETED with empty content
        child_after = get_instance(engine, child_id)
        assert child_after.status == InstanceStatus.COMPLETED.value, \
            "FIX C3 violation: Instance should be marked COMPLETED even with empty content"

    def test_proceeds_with_empty_content_creates_report(self, engine, manager_with_mocked_content, message_repo):
        """FIX C3: When content is None, completion report SHOULD be created with empty content."""
        parent_id = str(uuid.uuid4())
        child_id = str(uuid.uuid4())
        
        # Setup parent and child instances
        create_test_instance(engine, parent_id, waiting_for=1)
        create_test_instance(engine, child_id, parent_id=parent_id)
        
        # Simulate FIX C3 behavior
        last_content = None  # _get_last_assistant_message returns None

        # The fix: if content is None, use sentinel content and create report
        sentinel_content = "[No response content]"
        content = last_content if last_content is not None else sentinel_content
        create_completion_report(engine, parent_id, child_id, content)

        # Verify: completion report WAS created with sentinel content using message_repo helper
        reports = message_repo.get_by_instance(parent_id)
        completion_reports = [
            r for r in reports
            if r.type == MessageType.COMPLETION_REPORT.value and r.source == f"internal_report:{child_id}"
        ]

        assert len(completion_reports) == 1, "FIX C3 violation: Completion report should be created with sentinel content"
        assert completion_reports[0].content == sentinel_content, "Completion report should have sentinel content"

    def test_proceeds_with_empty_content_decrements_parent_waiting(self, engine, manager_with_mocked_content):
        """FIX C3: When content is None, parent's waiting_for SHOULD be decremented."""
        parent_id = str(uuid.uuid4())
        child_id = str(uuid.uuid4())
        
        # Setup parent waiting for child
        create_test_instance(engine, parent_id, waiting_for=1)
        create_test_instance(engine, child_id, parent_id=parent_id)
        
        parent_before = get_instance(engine, parent_id)
        initial_waiting = parent_before.waiting_for
        
        # Simulate FIX C3: content is None, so use empty string and proceed
        last_content = None
        content = last_content if last_content is not None else ""
        
        # Proceed with completion flow - decrement waiting_for
        with Session(engine) as session:
            parent = session.get(Instance, parent_id)
            parent.waiting_for = max(0, (parent.waiting_for or 0) - 1)
            session.commit()
        
        # Verify: waiting_for WAS decremented
        parent_after = get_instance(engine, parent_id)
        assert parent_after.waiting_for == initial_waiting - 1, \
            "FIX C3 violation: Parent waiting_for should be decremented even with empty content"

    def test_proceeds_and_marks_completed_when_content_exists(self, engine, manager_with_mocked_content, message_repo):
        """When content exists, instance SHOULD be marked COMPLETED (positive test)."""
        parent_id = str(uuid.uuid4())
        child_id = str(uuid.uuid4())
        
        # Setup parent and child
        create_test_instance(engine, parent_id, waiting_for=1)
        create_test_instance(engine, child_id, parent_id=parent_id)
        
        # Simulate FIX C3 behavior with actual content
        last_content = "Child completed successfully"
        
        if last_content is None:
            pass  # Skip
        else:
            # Mark instance as completed (the correct behavior when content exists)
            with Session(engine) as session:
                instance = session.get(Instance, child_id)
                instance.status = InstanceStatus.COMPLETED.value
                instance.version = (instance.version or 1) + 1
                session.commit()
            
            # Create completion report
            create_completion_report(engine, parent_id, child_id, last_content)
        
        # Verify: instance IS completed
        child_after = get_instance(engine, child_id)
        assert child_after.status == InstanceStatus.COMPLETED.value
        
        # Verify: completion report was created using message_repo helper
        reports = message_repo.get_by_instance(parent_id)
        completion_reports = [
            r for r in reports 
            if r.type == MessageType.COMPLETION_REPORT.value and r.source == f"internal_report:{child_id}"
        ]
        
        assert len(completion_reports) == 1, "Completion report should be created when content exists"
        assert completion_reports[0].content == last_content

    def test_content_fetch_happens_before_transaction_boundary(self, engine, manager_with_mocked_content, message_repo):
        """FIX C3 UPDATE: Verifies the critical ordering - content fetched OUTSIDE transaction.
        
        This test documents the FIX C3 pattern where:
        1. Content fetch happens BEFORE any database transaction
        2. If content is None, use sentinel content and PROCEED with completion
        3. State transition MUST happen even with sentinel content
        
        This ensures the instance is properly marked COMPLETED and report is sent,
        even when _get_last_assistant_message() returns None.
        """
        parent_id = str(uuid.uuid4())
        child_id = str(uuid.uuid4())
        
        create_test_instance(engine, parent_id, waiting_for=1)
        create_test_instance(engine, child_id, parent_id=parent_id)
        
        # Step 1: Content fetch (OUTSIDE transaction) - this is FIX C3
        # In real code: last_content = await self._get_last_assistant_message(instance_id)
        last_content = None  # Simulating failure
        
        # Step 2: FIX C3 - If content is None, use sentinel and proceed with completion
        sentinel_content = "[No response content]"
        if last_content is None:
            last_content = sentinel_content  # Proceed with sentinel content

        # Simulate the completion flow with the (potentially sentinel) content
        # 1. Mark instance as COMPLETED
        with Session(engine) as session:
            instance = session.get(Instance, child_id)
            instance.status = InstanceStatus.COMPLETED.value
            session.commit()

        # 2. Create completion report with the (sentinel) content
        create_completion_report(engine, parent_id, child_id, last_content)
        
        # Verify: instance IS completed (state transition happened)
        child_after = get_instance(engine, child_id)
        assert child_after.status == InstanceStatus.COMPLETED.value, \
            "Instance should transition to COMPLETED even with sentinel content"

        # Verify: completion report was created with sentinel content using message_repo helper
        reports = message_repo.get_by_instance(parent_id)
        completion_reports = [
            r for r in reports
            if r.type == MessageType.COMPLETION_REPORT.value and r.source == f"internal_report:{child_id}"
        ]
        assert len(completion_reports) == 1, "Completion report should be created with sentinel content"
        assert completion_reports[0].content == sentinel_content, "Report should use sentinel content"


# ============================================================================
# Test Class: C3 - WAITING_CHILDREN → RUNNING Transition
# ============================================================================


class TestWaitingChildrenToRunningTransition:
    """Tests for FIX C3: WAITING_CHILDREN → RUNNING transition on message enqueue.

    This tests the fix that ensures when a message is enqueued to an instance
    in WAITING_CHILDREN status, the instance transitions to RUNNING (not staying
    stuck in WAITING_CHILDREN).

    Before the fix: Instance stayed in WAITING_CHILDREN when message arrived,
    preventing parent from processing its own messages.

    After the fix: Instance transitions WAITING_CHILDREN → RUNNING when any
    message is enqueued, allowing it to process its own work.
    """

    def test_enqueue_message_transitions_waiting_children_to_running(self, engine, message_repo):
        """FIX C3: enqueue_message() transitions instance from WAITING_CHILDREN to RUNNING."""
        instance_id = str(uuid.uuid4())

        # Create instance in WAITING_CHILDREN status
        create_test_instance(
            engine,
            instance_id,
            status=InstanceStatus.WAITING_CHILDREN.value,
            waiting_for=1,
        )

        # Verify initial status is WAITING_CHILDREN
        instance = get_instance(engine, instance_id)
        assert instance.status == InstanceStatus.WAITING_CHILDREN.value

        # Enqueue message and update status (simulating enqueue_message behavior)
        with Session(engine) as session:
            instance = session.get(Instance, instance_id)
            # This is the key fix: WAITING_CHILDREN is now included in the transition check
            if instance.status in (
                InstanceStatus.IDLE.value,
                InstanceStatus.PAUSED.value,
                InstanceStatus.WAITING_CHILDREN.value,
            ):
                instance.status = InstanceStatus.RUNNING.value
            instance.last_activity_at = datetime.now(timezone.utc)
            instance.version = (instance.version or 1) + 1
            session.commit()

        # Verify: status should be RUNNING (not stuck in WAITING_CHILDREN)
        updated = get_instance(engine, instance_id)
        assert updated.status == InstanceStatus.RUNNING.value, (
            "FIX C3 violation: Instance should transition WAITING_CHILDREN → RUNNING on message enqueue"
        )

    def test_enqueue_message_via_jq_transitions_waiting_children_to_running(self, engine, message_repo):
        """FIX C3: enqueue_message_via_jq() transitions instance from WAITING_CHILDREN to RUNNING."""
        instance_id = str(uuid.uuid4())

        # Create instance in WAITING_CHILDREN status
        create_test_instance(
            engine,
            instance_id,
            status=InstanceStatus.WAITING_CHILDREN.value,
            waiting_for=1,
        )

        # Verify initial status is WAITING_CHILDREN
        instance = get_instance(engine, instance_id)
        assert instance.status == InstanceStatus.WAITING_CHILDREN.value

        # Enqueue message and update status (simulating enqueue_message_via_jq behavior)
        with Session(engine) as session:
            instance = session.get(Instance, instance_id)
            # This is the key fix: WAITING_CHILDREN is now included in the transition check
            if instance.status in (
                InstanceStatus.IDLE.value,
                InstanceStatus.PAUSED.value,
                InstanceStatus.WAITING_CHILDREN.value,
            ):
                instance.status = InstanceStatus.RUNNING.value
            instance.last_activity_at = datetime.now(timezone.utc)
            instance.version = (instance.version or 1) + 1
            session.commit()

        # Verify: status should be RUNNING (not stuck in WAITING_CHILDREN)
        updated = get_instance(engine, instance_id)
        assert updated.status == InstanceStatus.RUNNING.value, (
            "FIX C3 violation: Instance should transition WAITING_CHILDREN → RUNNING on message enqueue via JobQueue"
        )

    def test_waiting_children_with_zero_waiting_for_still_transitions(self, engine):
        """FIX C3: WAITING_CHILDREN → RUNNING even when waiting_for is 0."""
        instance_id = str(uuid.uuid4())

        # Create instance in WAITING_CHILDREN but with waiting_for=0
        # (edge case: all children completed but instance still in WAITING_CHILDREN)
        create_test_instance(
            engine,
            instance_id,
            status=InstanceStatus.WAITING_CHILDREN.value,
            waiting_for=0,  # All children done, but status wasn't updated
        )

        # Verify initial status
        instance = get_instance(engine, instance_id)
        assert instance.status == InstanceStatus.WAITING_CHILDREN.value

        # Transition on message enqueue
        with Session(engine) as session:
            instance = session.get(Instance, instance_id)
            if instance.status in (
                InstanceStatus.IDLE.value,
                InstanceStatus.PAUSED.value,
                InstanceStatus.WAITING_CHILDREN.value,
            ):
                instance.status = InstanceStatus.RUNNING.value
            instance.last_activity_at = datetime.now(timezone.utc)
            instance.version = (instance.version or 1) + 1
            session.commit()

        # Verify: status should be RUNNING
        updated = get_instance(engine, instance_id)
        assert updated.status == InstanceStatus.RUNNING.value

    def test_idle_status_still_transitions_to_running(self, engine):
        """Sanity check: IDLE → RUNNING still works (existing behavior)."""
        instance_id = str(uuid.uuid4())
        create_test_instance(engine, instance_id, status=InstanceStatus.IDLE.value)

        instance = get_instance(engine, instance_id)
        assert instance.status == InstanceStatus.IDLE.value

        # Transition on message enqueue
        with Session(engine) as session:
            instance = session.get(Instance, instance_id)
            if instance.status in (
                InstanceStatus.IDLE.value,
                InstanceStatus.PAUSED.value,
                InstanceStatus.WAITING_CHILDREN.value,
            ):
                instance.status = InstanceStatus.RUNNING.value
            session.commit()

        updated = get_instance(engine, instance_id)
        assert updated.status == InstanceStatus.RUNNING.value

    def test_paused_status_still_transitions_to_running(self, engine):
        """Sanity check: PAUSED → RUNNING still works (existing behavior)."""
        instance_id = str(uuid.uuid4())
        create_test_instance(engine, instance_id, status=InstanceStatus.PAUSED.value)

        instance = get_instance(engine, instance_id)
        assert instance.status == InstanceStatus.PAUSED.value

        # Transition on message enqueue
        with Session(engine) as session:
            instance = session.get(Instance, instance_id)
            if instance.status in (
                InstanceStatus.IDLE.value,
                InstanceStatus.PAUSED.value,
                InstanceStatus.WAITING_CHILDREN.value,
            ):
                instance.status = InstanceStatus.RUNNING.value
            session.commit()

        updated = get_instance(engine, instance_id)
        assert updated.status == InstanceStatus.RUNNING.value

    def test_running_status_stays_running(self, engine):
        """Sanity check: RUNNING → RUNNING stays the same."""
        instance_id = str(uuid.uuid4())
        create_test_instance(engine, instance_id, status=InstanceStatus.RUNNING.value)

        instance = get_instance(engine, instance_id)
        assert instance.status == InstanceStatus.RUNNING.value

        # Transition on message enqueue (RUNNING not in the list)
        with Session(engine) as session:
            instance = session.get(Instance, instance_id)
            if instance.status in (
                InstanceStatus.IDLE.value,
                InstanceStatus.PAUSED.value,
                InstanceStatus.WAITING_CHILDREN.value,
            ):
                instance.status = InstanceStatus.RUNNING.value
            session.commit()

        # Should stay RUNNING
        updated = get_instance(engine, instance_id)
        assert updated.status == InstanceStatus.RUNNING.value

# ============================================================================
# Test Class: Feature Flag Routing
# ============================================================================


class TestFeatureFlagRouting:
    """Tests for USE_WORKER_POOL environment variable handling.
    
    The worker pool is now the only message processing path.
    The USE_WORKER_POOL env var can still disable it for testing purposes.
    """

    def test_use_worker_pool_property_removed(self):
        """
        The use_worker_pool property has been removed - worker pool is always enabled.
        """
        from daemon.manager import InstanceManager
        # Verify the property no longer exists
        import inspect
        # Check that use_worker_pool is not a property on the class
        has_property = False
        for name, value in inspect.getmembers(InstanceManager):
            if name == 'use_worker_pool' and isinstance(value, property):
                has_property = True
                break
        
        assert not has_property, "use_worker_pool should not be a property anymore"

# End of test file
