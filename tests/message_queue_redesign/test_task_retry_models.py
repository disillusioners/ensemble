"""Tests for Task retry/cancel data model."""

import pytest
from datetime import datetime, timezone
from sqlalchemy import text

from daemon.repositories.task.models import Task, TaskStatus, TaskType
from daemon.repositories.task.repository import TaskRepository


class TestTaskStatusEnum:
    """Tests for TaskStatus enum including CANCELLED."""

    def test_task_status_cancelled_exists(self):
        """Verify CANCELLED status exists in enum."""
        assert hasattr(TaskStatus, "CANCELLED")
        assert TaskStatus.CANCELLED.value == "cancelled"

    def test_task_status_all_original_statuses_exist(self):
        """Verify all original statuses still exist."""
        assert TaskStatus.PENDING.value == "pending"
        assert TaskStatus.RUNNING.value == "running"
        assert TaskStatus.COMPLETED.value == "completed"
        assert TaskStatus.FAILED.value == "failed"
        assert TaskStatus.CANCELLED.value == "cancelled"

    def test_task_status_paused_exists(self):
        """Verify PAUSED status exists in enum.

        Phase 1 (2026-06-25) of the pause/resume redesign added
        ``PAUSED`` as a non-terminal task state so that pausing an
        instance can transition its in-flight task out of ``RUNNING``
        instead of relying on the prior workaround of keeping the
        row running while the instance is paused.
        """
        assert hasattr(TaskStatus, "PAUSED")
        assert TaskStatus.PAUSED.value == "paused"

    def test_task_status_count(self):
        """Verify correct number of statuses.

        History:
          * 5 — PENDING / RUNNING / COMPLETED / FAILED / CANCELLED.
          * 6 — Phase 1 of pause/resume redesign (2026-06-25) added PAUSED.
        """
        assert len(TaskStatus) == 6


class TestTaskModelDefaults:
    """Tests for Task model new field defaults."""

    def test_task_default_retry_count_is_zero(self):
        """Task should have retry_count default of 0."""
        task = Task(
            task_type=TaskType.PROCESS_MESSAGE.value,
            instance_id="test-instance",
        )
        assert task.retry_count == 0

    def test_task_default_next_retry_at_is_none(self):
        """Task should have next_retry_at default of None."""
        task = Task(
            task_type=TaskType.PROCESS_MESSAGE.value,
            instance_id="test-instance",
        )
        assert task.next_retry_at is None

    def test_task_default_cancel_requested_is_false(self):
        """Task should have cancel_requested default of False."""
        task = Task(
            task_type=TaskType.PROCESS_MESSAGE.value,
            instance_id="test-instance",
        )
        assert task.cancel_requested is False

    def test_task_default_cancel_requested_at_is_none(self):
        """Task should have cancel_requested_at default of None."""
        task = Task(
            task_type=TaskType.PROCESS_MESSAGE.value,
            instance_id="test-instance",
        )
        assert task.cancel_requested_at is None

    def test_task_default_retry_scheduled_is_false(self):
        """Task should have retry_scheduled default of False."""
        task = Task(
            task_type=TaskType.PROCESS_MESSAGE.value,
            instance_id="test-instance",
        )
        assert task.retry_scheduled is False

    def test_task_all_new_fields_defaults(self):
        """Test all new fields at once with no arguments."""
        task = Task(
            task_type=TaskType.PROCESS_MESSAGE.value,
            instance_id="test-instance",
        )
        assert task.retry_count == 0
        assert task.next_retry_at is None
        assert task.cancel_requested is False
        assert task.cancel_requested_at is None
        assert task.retry_scheduled is False


class TestTaskModelCustomValues:
    """Tests for Task model accepting custom values for new fields."""

    def test_task_accepts_custom_retry_count(self):
        """Task should accept custom retry_count value."""
        task = Task(
            task_type=TaskType.PROCESS_MESSAGE.value,
            instance_id="test-instance",
            retry_count=3,
        )
        assert task.retry_count == 3

    def test_task_accepts_custom_next_retry_at(self):
        """Task should accept custom next_retry_at value."""
        next_retry = "2026-04-15T12:00:00Z"
        task = Task(
            task_type=TaskType.PROCESS_MESSAGE.value,
            instance_id="test-instance",
            next_retry_at=next_retry,
        )
        assert task.next_retry_at == next_retry

    def test_task_accepts_custom_cancel_requested(self):
        """Task should accept custom cancel_requested value."""
        task = Task(
            task_type=TaskType.PROCESS_MESSAGE.value,
            instance_id="test-instance",
            cancel_requested=True,
        )
        assert task.cancel_requested is True

    def test_task_accepts_custom_cancel_requested_at(self):
        """Task should accept custom cancel_requested_at value."""
        cancel_time = "2026-04-15T12:00:00Z"
        task = Task(
            task_type=TaskType.PROCESS_MESSAGE.value,
            instance_id="test-instance",
            cancel_requested_at=cancel_time,
        )
        assert task.cancel_requested_at == cancel_time

    def test_task_accepts_custom_retry_scheduled(self):
        """Task should accept custom retry_scheduled value."""
        task = Task(
            task_type=TaskType.PROCESS_MESSAGE.value,
            instance_id="test-instance",
            retry_scheduled=True,
        )
        assert task.retry_scheduled is True

    def test_task_all_custom_values(self):
        """Test all new fields accept custom values."""
        next_retry = "2026-04-15T12:00:00Z"
        cancel_time = "2026-04-15T12:30:00Z"

        task = Task(
            task_type=TaskType.PROCESS_MESSAGE.value,
            instance_id="test-instance",
            retry_count=5,
            next_retry_at=next_retry,
            cancel_requested=True,
            cancel_requested_at=cancel_time,
            retry_scheduled=True,
        )

        assert task.retry_count == 5
        assert task.next_retry_at == next_retry
        assert task.cancel_requested is True
        assert task.cancel_requested_at == cancel_time
        assert task.retry_scheduled is True


class TestMigrationIdempotent:
    """Tests for migration applying cleanly and idempotently.

    These tests create a fresh database with the OLD task table schema
    (without new columns) to test the migration SQL.
    """

    @pytest.fixture
    def old_schema_engine(self):
        """Create engine with OLD task table schema (before migration)."""
        from sqlalchemy import create_engine
        from sqlalchemy.pool import StaticPool

        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )

        # Create OLD task table without new columns
        with engine.begin() as conn:
            conn.execute(text("""
                CREATE TABLE task (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_type TEXT NOT NULL,
                    instance_id TEXT NOT NULL,
                    message_id TEXT,
                    status TEXT NOT NULL DEFAULT 'pending',
                    worker_id TEXT,
                    result TEXT,
                    error TEXT,
                    created_at TIMESTAMP NOT NULL,
                    started_at TIMESTAMP,
                    completed_at TIMESTAMP
                )
            """))

        yield engine
        engine.dispose()

    def test_migration_applies_without_error(self, old_schema_engine):
        """Migration should apply without error."""
        migration_sql = """
        ALTER TABLE task ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE task ADD COLUMN next_retry_at TEXT;
        ALTER TABLE task ADD COLUMN cancel_requested INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE task ADD COLUMN cancel_requested_at TEXT;
        ALTER TABLE task ADD COLUMN retry_scheduled INTEGER NOT NULL DEFAULT 0;
        CREATE INDEX IF NOT EXISTS idx_task_status_next_retry ON task(status, next_retry_at);
        CREATE INDEX IF NOT EXISTS idx_task_cancel_status ON task(cancel_requested, status);
        """

        with old_schema_engine.begin() as conn:
            for statement in migration_sql.strip().split(";"):
                statement = statement.strip()
                if statement:
                    conn.execute(text(statement))

        # Verify columns exist
        with old_schema_engine.begin() as conn:
            result = conn.execute(text("PRAGMA table_info(task)"))
            columns = {row[1] for row in result}

        assert "retry_count" in columns
        assert "next_retry_at" in columns
        assert "cancel_requested" in columns
        assert "cancel_requested_at" in columns
        assert "retry_scheduled" in columns

    def test_migration_is_idempotent(self, old_schema_engine):
        """Migration should be idempotent - apply twice without error.

        Note: ALTER TABLE is NOT idempotent in SQLite, but CREATE INDEX with
        IF NOT EXISTS IS idempotent. This test verifies index creation is idempotent.
        """
        # First apply ALTER TABLE to add columns
        with old_schema_engine.begin() as conn:
            conn.execute(text("ALTER TABLE task ADD COLUMN next_retry_at TEXT"))
            conn.execute(text("ALTER TABLE task ADD COLUMN cancel_requested INTEGER NOT NULL DEFAULT 0"))

        # Now test that CREATE INDEX IF NOT EXISTS is idempotent
        create_index_sql = """
        CREATE INDEX IF NOT EXISTS idx_task_status_next_retry ON task(status, next_retry_at);
        CREATE INDEX IF NOT EXISTS idx_task_cancel_status ON task(cancel_requested, status);
        """

        # Apply indexes first time
        with old_schema_engine.begin() as conn:
            for statement in create_index_sql.strip().split(";"):
                statement = statement.strip()
                if statement:
                    conn.execute(text(statement))

        # Apply again - should not raise error (idempotent due to IF NOT EXISTS)
        with old_schema_engine.begin() as conn:
            for statement in create_index_sql.strip().split(";"):
                statement = statement.strip()
                if statement:
                    conn.execute(text(statement))  # Should not raise

    def test_indexes_exist_after_migration(self, old_schema_engine):
        """Verify indexes are created by migration."""
        migration_sql = """
        ALTER TABLE task ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE task ADD COLUMN next_retry_at TEXT;
        ALTER TABLE task ADD COLUMN cancel_requested INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE task ADD COLUMN cancel_requested_at TEXT;
        ALTER TABLE task ADD COLUMN retry_scheduled INTEGER NOT NULL DEFAULT 0;
        CREATE INDEX IF NOT EXISTS idx_task_status_next_retry ON task(status, next_retry_at);
        CREATE INDEX IF NOT EXISTS idx_task_cancel_status ON task(cancel_requested, status);
        """

        with old_schema_engine.begin() as conn:
            for statement in migration_sql.strip().split(";"):
                statement = statement.strip()
                if statement:
                    conn.execute(text(statement))

        with old_schema_engine.begin() as conn:
            result = conn.execute(text("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='task'"))
            indexes = {row[0] for row in result}

        assert "idx_task_status_next_retry" in indexes
        assert "idx_task_cancel_status" in indexes


class TestRowToTaskMapping:
    """Tests for _row_to_task() mapping new fields correctly."""

    def test_row_to_task_maps_new_fields(self, repository):
        """_row_to_task should map all new fields from row."""
        # Create a task using repository (ensures all columns exist)
        task = repository.create(
            task_type=TaskType.PROCESS_MESSAGE.value,
            instance_id="test-instance",
            message_id="msg-123",
        )

        # Manually update the task in DB with new field values
        # Use past datetime so task is claimable
        from datetime import datetime, timezone, timedelta
        past_time = datetime.now(timezone.utc) - timedelta(hours=1)
        past_time_str = past_time.strftime("%Y-%m-%dT%H:%M:%S.%f") + past_time.strftime("%z")

        from sqlmodel import SQLModel, Session as SQLModelSession
        with SQLModelSession(self.engine if hasattr(self, 'engine') else repository.engine) as db_session:
            db_task = db_session.get(Task, task.id)
            db_task.retry_count = 3
            db_task.next_retry_at = past_time_str
            db_task.cancel_requested = True
            db_task.cancel_requested_at = past_time_str
            db_task.retry_scheduled = True
            db_session.commit()

        # Now claim to trigger _row_to_task
        claimed = repository.claim_pending_task(worker_id="worker-1")

        assert claimed is not None
        assert claimed.retry_count == 3
        assert claimed.next_retry_at == past_time_str
        assert claimed.cancel_requested == 1  # SQLite stores bool as int
        assert claimed.cancel_requested_at == past_time_str
        assert claimed.retry_scheduled == 1  # SQLite stores bool as int

    def test_row_to_task_maps_original_fields(self, repository):
        """_row_to_task should still map all original fields."""
        task = repository.create(
            task_type=TaskType.PROCESS_MESSAGE.value,
            instance_id="test-instance",
            message_id="msg-123",
        )

        claimed = repository.claim_pending_task(worker_id="worker-1")

        assert claimed is not None
        assert claimed.task_type == "process_message"
        assert claimed.instance_id == "test-instance"
        assert claimed.message_id == "msg-123"
        assert claimed.status == "running"


class TestRowToTaskBackwardCompat:
    """Tests for _row_to_task() backward compatibility with old rows."""

    def test_row_to_task_defaults_for_missing_fields(self, engine):
        """_row_to_task should provide defaults when new fields are missing."""

        class MockRowOld:
            """Mock row without new fields (old schema)."""
            def __init__(self):
                self.id = 1
                self.task_type = "process_message"
                self.instance_id = "test-instance"
                self.message_id = "msg-123"
                self.status = "pending"
                self.worker_id = None
                self.result = None
                self.error = None
                self.created_at = datetime.now(timezone.utc)
                self.started_at = None
                self.completed_at = None

        mock_row = MockRowOld()
        repo = TaskRepository(engine)
        task = repo._row_to_task(mock_row)

        # All new fields should get default values
        assert task.retry_count == 0
        assert task.next_retry_at is None
        assert task.cancel_requested is False
        assert task.cancel_requested_at is None
        assert task.retry_scheduled is False

    def test_row_to_task_partial_new_fields(self, engine):
        """_row_to_task should handle partial new fields (hasattr returns True for 0/False)."""

        class MockRowPartial:
            """Mock row with some new fields."""
            def __init__(self):
                self.id = 1
                self.task_type = "process_message"
                self.instance_id = "test-instance"
                self.message_id = "msg-123"
                self.status = "pending"
                self.worker_id = None
                self.result = None
                self.error = None
                self.created_at = datetime.now(timezone.utc)
                self.started_at = None
                self.completed_at = None
                # Only some new fields
                self.retry_count = 2
                self.next_retry_at = "2026-04-15T12:00:00Z"
                # Missing: cancel_requested, cancel_requested_at, retry_scheduled

        mock_row = MockRowPartial()
        repo = TaskRepository(engine)
        task = repo._row_to_task(mock_row)

        assert task.retry_count == 2
        assert task.next_retry_at == "2026-04-15T12:00:00Z"
        assert task.cancel_requested is False  # Default
        assert task.cancel_requested_at is None  # Default
        assert task.retry_scheduled is False  # Default


class TestBackwardCompatibility:
    """Tests for existing task operations still working."""

    def test_create_task_has_new_fields_with_defaults(self, repository):
        """Creating a task should have new fields with defaults."""
        task = repository.create(
            task_type=TaskType.PROCESS_MESSAGE.value,
            instance_id="test-instance",
            message_id="test-message",
        )

        assert task.retry_count == 0
        assert task.next_retry_at is None
        assert task.cancel_requested is False
        assert task.cancel_requested_at is None
        assert task.retry_scheduled is False

    def test_get_task_includes_new_fields(self, repository):
        """Getting a task should return new fields."""
        created = repository.create(
            task_type=TaskType.PROCESS_MESSAGE.value,
            instance_id="test-instance",
        )

        retrieved = repository.get(created.id)

        assert retrieved is not None
        assert retrieved.retry_count == 0
        assert retrieved.next_retry_at is None
        assert retrieved.cancel_requested is False
        assert retrieved.cancel_requested_at is None
        assert retrieved.retry_scheduled is False

    def test_claim_task_includes_new_fields(self, repository):
        """Claiming a task should return new fields."""
        task = repository.create(
            task_type=TaskType.PROCESS_MESSAGE.value,
            instance_id="test-instance",
        )

        claimed = repository.claim_pending_task(worker_id="worker-1")

        assert claimed is not None
        assert claimed.retry_count == 0
        assert claimed.next_retry_at is None
        # SQLite stores bool as integer (0/1)
        assert claimed.cancel_requested == 0
        assert claimed.cancel_requested_at is None
        assert claimed.retry_scheduled == 0

    def test_complete_task_preserves_new_fields(self, repository):
        """Completing a task should preserve new fields."""
        task = repository.create(
            task_type=TaskType.PROCESS_MESSAGE.value,
            instance_id="test-instance",
        )
        repository.claim_pending_task(worker_id="worker-1")

        completed = repository.complete_task(task.id, {"success": True})

        assert completed is not None
        assert completed.retry_count == 0
        assert completed.next_retry_at is None
        assert completed.cancel_requested is False
        assert completed.cancel_requested_at is None
        assert completed.retry_scheduled is False

    def test_fail_task_preserves_new_fields(self, repository):
        """Failing a task should preserve new fields."""
        task = repository.create(
            task_type=TaskType.PROCESS_MESSAGE.value,
            instance_id="test-instance",
        )
        repository.claim_pending_task(worker_id="worker-1")

        failed = repository.fail_task(task.id, "Test error")

        assert failed is not None
        assert failed.retry_count == 0
        assert failed.next_retry_at is None
        assert failed.cancel_requested is False
        assert failed.cancel_requested_at is None
        assert failed.retry_scheduled is False

    def test_count_by_status_includes_cancelled(self, repository):
        """count_by_status should include CANCELLED status."""
        counts = repository.count_by_status()
        assert "cancelled" in counts

    def test_task_to_dict_includes_new_fields(self, repository):
        """to_dict should include new fields."""
        task = repository.create(
            task_type=TaskType.PROCESS_MESSAGE.value,
            instance_id="test-instance",
        )

        task_dict = task.to_dict()

        assert "retry_count" in task_dict
        assert "next_retry_at" in task_dict
        assert "cancel_requested" in task_dict
        assert "cancel_requested_at" in task_dict
        assert "retry_scheduled" in task_dict
        assert task_dict["retry_count"] == 0
        assert task_dict["next_retry_at"] is None
        assert task_dict["cancel_requested"] is False
        assert task_dict["cancel_requested_at"] is None
        assert task_dict["retry_scheduled"] is False
