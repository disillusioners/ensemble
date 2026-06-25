"""Tests for JobQueue SQLModel models.

This module tests the JobQueue and related models including:
- QueueType enum values
- JobQueue creation with various fields
- Default values
- to_dict() serialization
- JobItem with queue_id relationship
- Table name and constraints
"""

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from daemon.repositories.job_queue.models import (
    JobQueue,
    JobItem,
    QueueType,
)


@pytest.fixture
def engine():
    """Create in-memory SQLite engine for testing."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


class TestQueueTypeEnum:
    """Tests for QueueType enum."""

    def test_queue_type_enum_values(self):
        """Test QueueType enum has correct values for FIFO, PARALLEL, and DEFER."""
        assert QueueType.FIFO.value == "fifo"
        assert QueueType.PARALLEL.value == "parallel"
        assert QueueType.DEFER.value == "defer"

    def test_queue_type_enum_is_string_enum(self):
        """Test QueueType is a string enum (can compare directly to strings)."""
        assert QueueType.FIFO == "fifo"
        assert QueueType.PARALLEL == "parallel"
        assert QueueType.DEFER == "defer"

    def test_queue_type_enum_count(self):
        """Test QueueType has exactly three values."""
        assert len(QueueType) == 3


class TestJobQueueCreation:
    """Tests for JobQueue model creation."""

    def test_job_queue_creation_with_valid_data(self, engine):
        """Test creating JobQueue with all fields specified."""
        queue = JobQueue(
            queue_id="test-queue-123",
            project_id="project-abc",
            queue_name="my-queue",
            queue_name_lower="my-queue",
            queue_type=QueueType.FIFO.value,
            concurrency_limit=5,
            is_system=False,
            is_paused=False,
            description="A test queue",
            created_at="2026-04-09T10:00:00",
            updated_at="2026-04-09T10:00:00",
        )
        
        # Verify all fields are set correctly
        assert queue.queue_id == "test-queue-123"
        assert queue.project_id == "project-abc"
        assert queue.queue_name == "my-queue"
        assert queue.queue_name_lower == "my-queue"
        assert queue.queue_type == QueueType.FIFO.value
        assert queue.concurrency_limit == 5
        assert queue.is_system is False
        assert queue.is_paused is False
        assert queue.description == "A test queue"
        assert queue.created_at == "2026-04-09T10:00:00"
        assert queue.updated_at == "2026-04-09T10:00:00"

    def test_job_queue_default_values(self, engine):
        """Test JobQueue default values when created with minimal fields."""
        queue = JobQueue(
            project_id="project-abc",
        )
        
        # Check defaults
        assert queue.queue_id is not None  # Generated UUID
        assert len(queue.queue_id) == 36  # UUID format
        assert queue.queue_name == "default"
        assert queue.queue_name_lower == "default"
        assert queue.queue_type == QueueType.FIFO.value  # "fifo"
        assert queue.concurrency_limit == 1
        assert queue.is_system is False
        assert queue.is_paused is False
        assert queue.description is None
        assert queue.created_at is not None
        assert queue.updated_at is not None

    def test_job_queue_with_parallel_type(self, engine):
        """Test creating JobQueue with PARALLEL type and higher concurrency."""
        queue = JobQueue(
            project_id="project-abc",
            queue_name="parallel-queue",
            queue_name_lower="parallel-queue",
            queue_type=QueueType.PARALLEL.value,
            concurrency_limit=10,
        )
        
        assert queue.queue_type == "parallel"
        assert queue.concurrency_limit == 10

    def test_job_queue_with_defer_type(self, engine):
        """Test creating JobQueue with DEFER type (always concurrency=1)."""
        queue = JobQueue(
            project_id="project-abc",
            queue_name="defer-queue",
            queue_name_lower="defer-queue",
            queue_type=QueueType.DEFER.value,
            concurrency_limit=1,
        )
        
        assert queue.queue_type == "defer"
        assert queue.concurrency_limit == 1

    def test_job_queue_uuid_generation(self, engine):
        """Test that queue_id is auto-generated as UUID when not provided."""
        queue1 = JobQueue(project_id="project-1")
        queue2 = JobQueue(project_id="project-2")
        
        # Both should have valid UUIDs
        assert len(queue1.queue_id) == 36
        assert len(queue2.queue_id) == 36
        assert queue1.queue_id != queue2.queue_id  # Unique


class TestJobQueueToDict:
    """Tests for JobQueue.to_dict() method."""

    def test_job_queue_to_dict(self, engine):
        """Test JobQueue.to_dict() returns correct dictionary."""
        queue = JobQueue(
            queue_id="test-queue-123",
            project_id="project-abc",
            queue_name="my-queue",
            queue_name_lower="my-queue",
            queue_type=QueueType.PARALLEL.value,
            concurrency_limit=5,
            is_system=True,
            is_paused=True,
            description="Test description",
            created_at="2026-04-09T10:00:00",
            updated_at="2026-04-09T12:00:00",
        )
        
        result = queue.to_dict()
        
        assert isinstance(result, dict)
        assert result["queue_id"] == "test-queue-123"
        assert result["project_id"] == "project-abc"
        assert result["queue_name"] == "my-queue"
        assert result["queue_name_lower"] == "my-queue"
        assert result["queue_type"] == "parallel"
        assert result["concurrency_limit"] == 5
        assert result["is_system"] is True
        assert result["is_paused"] is True
        assert result["description"] == "Test description"
        assert result["created_at"] == "2026-04-09T10:00:00"
        assert result["updated_at"] == "2026-04-09T12:00:00"

    def test_job_queue_to_dict_with_defaults(self, engine):
        """Test to_dict() with default values includes all fields."""
        queue = JobQueue(project_id="project-abc")
        
        result = queue.to_dict()
        
        # Should include all expected keys
        expected_keys = {
            "queue_id", "project_id", "queue_name", "queue_name_lower",
            "queue_type", "concurrency_limit", "is_system", "is_paused",
            "description", "created_at", "updated_at"
        }
        assert set(result.keys()) == expected_keys


class TestJobItemWithQueueId:
    """Tests for JobItem with queue_id field."""

    def test_job_item_with_queue_id(self, engine):
        """Test creating JobItem with queue_id set."""
        # First create a queue
        queue = JobQueue(
            queue_id="test-queue-123",
            project_id="project-abc",
        )
        
        # Create job item with queue_id
        job = JobItem(
            job_id="test-job-456",
            agent_id="developer",
            agent_dir="/agents/developer",
            message="Test message",
            source="api",
            project_id="project-abc",
            queue_id="test-queue-123",
        )
        
        assert job.queue_id == "test-queue-123"
        assert job.project_id == "project-abc"

    def test_job_item_without_queue_id_backward_compat(self, engine):
        """Test creating JobItem without queue_id (should be None/default)."""
        job = JobItem(
            job_id="test-job-456",
            agent_id="developer",
            agent_dir="/agents/developer",
            message="Test message",
            source="api",
            project_id="project-abc",
        )
        
        # queue_id should be None by default
        assert job.queue_id is None

    def test_job_item_to_dict_includes_queue_id(self, engine):
        """Test JobItem.to_dict() includes queue_id field."""
        job = JobItem(
            job_id="test-job-456",
            agent_id="developer",
            agent_dir="/agents/developer",
            message="Test message",
            source="api",
            queue_id="test-queue-123",
        )
        
        result = job.to_dict()
        
        assert "queue_id" in result
        assert result["queue_id"] == "test-queue-123"


class TestJobQueueTableName:
    """Tests for JobQueue table configuration."""

    def test_job_queue_table_name(self):
        """Test JobQueue maps to 'job_queues' table."""
        assert JobQueue.__tablename__ == "job_queues"

    def test_job_queue_table_args_exist(self):
        """Test JobQueue has table_args defined."""
        assert hasattr(JobQueue, "__table_args__")
        assert JobQueue.__table_args__ is not None


class TestJobQueueUniqueConstraint:
    """Tests for JobQueue unique constraint."""

    def test_job_queue_unique_constraint_definition(self):
        """Test unique constraint is defined on (project_id, queue_name_lower)."""
        table_args = JobQueue.__table_args__
        
        # Find UniqueConstraint in table_args
        from sqlalchemy import UniqueConstraint
        unique_constraints = [arg for arg in table_args if isinstance(arg, UniqueConstraint)]
        
        assert len(unique_constraints) == 1
        uc = unique_constraints[0]
        # Check the column names
        assert set(uc.columns.keys()) == {"project_id", "queue_name_lower"}

    def test_job_queue_unique_constraint_enforced_at_db_level(self, engine):
        """Test that unique constraint is enforced at database level."""
        from sqlalchemy.exc import IntegrityError
        from sqlmodel import Session
        
        # Insert first queue
        queue1 = JobQueue(
            project_id="project-abc",
            queue_name="unique-queue",
            queue_name_lower="unique-queue",
        )
        with Session(engine) as session:
            session.add(queue1)
            session.commit()
        
        # Try to insert second queue with same project_id and queue_name_lower
        queue2 = JobQueue(
            project_id="project-abc",
            queue_name="unique-queue",  # Same name
            queue_name_lower="unique-queue",  # Same lowercase name
        )
        
        with pytest.raises(IntegrityError):
            with Session(engine) as session:
                session.add(queue2)
                session.commit()

    def test_job_queue_same_name_different_projects_allowed(self, engine):
        """Test that same queue name is allowed for different projects."""
        from sqlmodel import Session
        
        queue1 = JobQueue(
            project_id="project-abc",
            queue_name="same-name",
            queue_name_lower="same-name",
        )
        queue2 = JobQueue(
            project_id="project-xyz",
            queue_name="same-name",
            queue_name_lower="same-name",
        )
        
        # Both should be insertable
        with Session(engine) as session:
            session.add(queue1)
            session.add(queue2)
            session.commit()
        
        # Verify both were inserted
        with Session(engine) as session:
            from sqlmodel import select
            result = session.exec(select(JobQueue)).all()
            assert len(result) == 2


class TestJobQueueIndex:
    """Tests for JobQueue indexes."""

    def test_job_queue_project_index_defined(self):
        """Test index on project_id is defined."""
        table_args = JobQueue.__table_args__
        
        from sqlalchemy import Index
        indexes = [arg for arg in table_args if isinstance(arg, Index)]
        
        # Should have idx_job_queues_project index
        index_names = [idx.name for idx in indexes]
        assert "idx_job_queues_project" in index_names


class TestJobQueueDeferQueueConcurrencyLimit:
    """Tests for defer queue concurrency_limit enforcement.

    Defer queues are special queues that only process jobs when the entire
    project is idle (no active jobs in any other queue). They must always
    have concurrency_limit=1 to ensure serialized processing.
    """

    def test_defer_queue_allows_concurrency_limit_1(self, engine):
        """Test that defer queue can be created with concurrency_limit=1."""
        queue = JobQueue(
            project_id="project-abc",
            queue_name="defer-queue",
            queue_name_lower="defer-queue",
            queue_type=QueueType.DEFER.value,
            concurrency_limit=1,
        )
        assert queue.queue_type == "defer"
        assert queue.concurrency_limit == 1

    def test_defer_queue_raises_on_concurrency_limit_2_or_more(self, engine):
        """Test that defer queue raises ValueError if concurrency_limit >= 2.

        Note: concurrency_limit=0 is caught by the SQLModel field validator (ge=1),
        so this test covers the defer-specific validator for values >= 2.

        SQLModel requires using model_validate() to trigger model validators
        in some configurations, so we use that instead of direct instantiation.
        """
        with pytest.raises(ValueError, match="concurrency_limit=1"):
            JobQueue.model_validate({
                "project_id": "project-abc",
                "queue_name": "defer-queue",
                "queue_name_lower": "defer-queue",
                "queue_type": QueueType.DEFER.value,
                "concurrency_limit": 5,  # Invalid - must be 1
            })

    def test_defer_queue_allows_default_concurrency_limit(self, engine):
        """Test that defer queue uses default concurrency_limit=1."""
        queue = JobQueue(
            project_id="project-abc",
            queue_name="defer-queue",
            queue_name_lower="defer-queue",
            queue_type=QueueType.DEFER.value,
            # concurrency_limit not specified - should use default 1
        )
        assert queue.concurrency_limit == 1

    def test_fifo_queue_allows_higher_concurrency(self, engine):
        """Test that FIFO queue allows higher concurrency limits."""
        queue = JobQueue(
            project_id="project-abc",
            queue_name="fifo-queue",
            queue_name_lower="fifo-queue",
            queue_type=QueueType.FIFO.value,
            concurrency_limit=10,  # Valid for FIFO
        )
        assert queue.queue_type == "fifo"
        assert queue.concurrency_limit == 10

    def test_parallel_queue_allows_higher_concurrency(self, engine):
        """Test that PARALLEL queue allows higher concurrency limits."""
        queue = JobQueue(
            project_id="project-abc",
            queue_name="parallel-queue",
            queue_name_lower="parallel-queue",
            queue_type=QueueType.PARALLEL.value,
            concurrency_limit=20,  # Max allowed
        )
        assert queue.queue_type == "parallel"
        assert queue.concurrency_limit == 20
