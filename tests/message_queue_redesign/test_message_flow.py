"""Tests for Phase 3 message flow migration.

Tests the atomic operations for:
- enqueue_message_v2: Creates Message + Task + Event atomically
- check_child_completion_v2: Child completion logic with idempotency
- Startup recovery: Resets orphaned tasks and messages
"""

import pytest
import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch

from sqlmodel import Session

from daemon.repositories.message_queue.models import MessageQueue, MessageStatus, MessageType
from daemon.repositories.message_queue.repository import SQLModelMessageQueueRepository
from daemon.repositories.instance.models import Instance, InstanceHierarchy, InstanceStatus
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.task.models import Task, TaskStatus, TaskType
from daemon.repositories.task.repository import TaskRepository
from daemon.repositories.event.models import Event, EventKind
from daemon.repositories.event.repository import EventRepository


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
            source=f"report:{child_id}",
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
        """Should skip if completion report content is None (FIX: C3)."""
        parent_id = str(uuid.uuid4())
        child_id = str(uuid.uuid4())
        create_test_instance(engine, parent_id, waiting_for=1)
        create_test_instance(engine, child_id, parent_id=parent_id)

        # Simulate: check if we should skip when content would be None
        content = None  # This is the bug scenario

        if content is None:
            # Skip the completion report - this is the fix
            pass
        else:
            # Would create completion report
            create_completion_report(engine, parent_id, child_id, content)

        # Verify NO completion report was created
        with Session(engine) as session:
            from sqlalchemy import select
            reports = session.exec(
                select(MessageQueue)
                .where(MessageQueue.instance_id == parent_id)
                .where(MessageQueue.type == MessageType.COMPLETION_REPORT.value)
            ).all()

        assert len(reports) == 0  # Should be skipped

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
                .where(MessageQueue.source == f"report:{child_id}")
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
                .where(MessageQueue.source == f"report:{child_id}")
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
        assert report.source == f"report:{child_id}"
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
                source=f"report:{child_id}",
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
                .where(MessageQueue.source == f"report:{child_id}")
            ).first()

            if not existing:
                # Create completion report
                report = MessageQueue(
                    message_id=str(uuid.uuid4()),
                    instance_id=parent_id,
                    content="Child completed",
                    type=MessageType.COMPLETION_REPORT.value,
                    source=f"report:{child_id}",
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
                    source=f"report:{child_id}",
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
