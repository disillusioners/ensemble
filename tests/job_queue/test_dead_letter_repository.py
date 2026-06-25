"""Tests for DeadLetterRepository.

This module tests the DeadLetterRepository with in-memory SQLite database.
"""

import pytest
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from daemon.repositories.job_queue.dead_letter_repository import DeadLetterRepository
from daemon.repositories.job_queue.models import DeadLetterItem


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


@pytest.fixture
def repository(engine):
    """Create DeadLetterRepository with test engine."""
    return DeadLetterRepository(engine)


class TestDeadLetterRepositoryEnqueue:
    """Tests for DeadLetterRepository.enqueue() method."""

    def test_enqueue_creates_dlq_item(self, repository):
        """Test that enqueue() creates a new DLQ item."""
        item = DeadLetterItem(
            job_id="job-123",
            agent_id="developer",
            agent_dir="/agents/developer",
            message="Test message",
            source="api",
            project_id="project-abc",
            queue_id="queue-xyz",
            priority=5,
            error_message="Connection timeout",
            retry_count=0,
            failed_at=datetime.utcnow().isoformat(),
            reason="MAX_RETRIES",
        )
        
        result = repository.enqueue(item)
        
        assert result.dlq_id is not None
        assert result.job_id == "job-123"
        assert result.project_id == "project-abc"
        assert result.reason == "MAX_RETRIES"


class TestDeadLetterRepositoryGet:
    """Tests for DeadLetterRepository.get() method."""

    def test_get_existing_item(self, repository):
        """Test retrieving an existing DLQ item by dlq_id."""
        item = DeadLetterItem(
            job_id="job-123",
            agent_id="developer",
            agent_dir="/agents/developer",
            message="Test message",
            source="api",
            project_id="project-abc",
            queue_id="queue-xyz",
            priority=5,
            error_message="Connection timeout",
            retry_count=0,
            failed_at=datetime.utcnow().isoformat(),
            reason="MAX_RETRIES",
        )
        created = repository.enqueue(item)
        
        result = repository.get(created.dlq_id)
        
        assert result is not None
        assert result.dlq_id == created.dlq_id
        assert result.job_id == "job-123"

    def test_get_nonexistent_item(self, repository):
        """Test retrieving a non-existent DLQ item returns None."""
        result = repository.get("non-existent-id")
        
        assert result is None


class TestDeadLetterRepositoryGetByJobId:
    """Tests for DeadLetterRepository.get_by_job_id() method."""

    def test_get_by_job_id_existing(self, repository):
        """Test retrieving DLQ item by job_id."""
        item = DeadLetterItem(
            job_id="job-456",
            agent_id="developer",
            agent_dir="/agents/developer",
            message="Test message",
            source="api",
            project_id="project-abc",
            queue_id="queue-xyz",
            priority=5,
            error_message="Connection timeout",
            retry_count=0,
            failed_at=datetime.utcnow().isoformat(),
            reason="MAX_RETRIES",
        )
        repository.enqueue(item)
        
        result = repository.get_by_job_id("job-456")
        
        assert result is not None
        assert result.job_id == "job-456"

    def test_get_by_job_id_nonexistent(self, repository):
        """Test retrieving by non-existent job_id returns None."""
        result = repository.get_by_job_id("non-existent-job")
        
        assert result is None


class TestDeadLetterRepositoryList:
    """Tests for DeadLetterRepository.list() method."""

    def test_list_with_project_id_filter(self, repository):
        """Test listing DLQ items filtered by project_id."""
        # Create items for different projects
        item1 = DeadLetterItem(
            job_id="job-1",
            agent_id="developer",
            agent_dir="/agents/developer",
            message="Message 1",
            source="api",
            project_id="project-a",
            queue_id="queue-1",
            priority=5,
            error_message="Error 1",
            retry_count=0,
            failed_at=datetime.utcnow().isoformat(),
            reason="MAX_RETRIES",
        )
        item2 = DeadLetterItem(
            job_id="job-2",
            agent_id="developer",
            agent_dir="/agents/developer",
            message="Message 2",
            source="api",
            project_id="project-b",
            queue_id="queue-2",
            priority=5,
            error_message="Error 2",
            retry_count=0,
            failed_at=datetime.utcnow().isoformat(),
            reason="MAX_RETRIES",
        )
        repository.enqueue(item1)
        repository.enqueue(item2)
        
        items, total = repository.list(project_id="project-a")
        
        assert total == 1
        assert len(items) == 1
        assert items[0].project_id == "project-a"

    def test_list_with_queue_id_filter(self, repository):
        """Test listing DLQ items filtered by queue_id."""
        item1 = DeadLetterItem(
            job_id="job-1",
            agent_id="developer",
            agent_dir="/agents/developer",
            message="Message 1",
            source="api",
            project_id="project-a",
            queue_id="queue-1",
            priority=5,
            error_message="Error 1",
            retry_count=0,
            failed_at=datetime.utcnow().isoformat(),
            reason="MAX_RETRIES",
        )
        item2 = DeadLetterItem(
            job_id="job-2",
            agent_id="developer",
            agent_dir="/agents/developer",
            message="Message 2",
            source="api",
            project_id="project-a",
            queue_id="queue-2",
            priority=5,
            error_message="Error 2",
            retry_count=0,
            failed_at=datetime.utcnow().isoformat(),
            reason="MAX_RETRIES",
        )
        repository.enqueue(item1)
        repository.enqueue(item2)
        
        items, total = repository.list(queue_id="queue-1")
        
        assert total == 1
        assert items[0].queue_id == "queue-1"

    def test_list_with_reason_filter(self, repository):
        """Test listing DLQ items filtered by reason."""
        item1 = DeadLetterItem(
            job_id="job-1",
            agent_id="developer",
            agent_dir="/agents/developer",
            message="Message 1",
            source="api",
            project_id="project-a",
            queue_id="queue-1",
            priority=5,
            error_message="Error 1",
            retry_count=0,
            failed_at=datetime.utcnow().isoformat(),
            reason="MAX_RETRIES",
        )
        item2 = DeadLetterItem(
            job_id="job-2",
            agent_id="developer",
            agent_dir="/agents/developer",
            message="Message 2",
            source="api",
            project_id="project-a",
            queue_id="queue-1",
            priority=5,
            error_message="Error 2",
            retry_count=0,
            failed_at=datetime.utcnow().isoformat(),
            reason="MANUAL",
        )
        repository.enqueue(item1)
        repository.enqueue(item2)
        
        items, total = repository.list(reason="MAX_RETRIES")
        
        assert total == 1
        assert items[0].reason == "MAX_RETRIES"

    def test_list_with_multiple_filters(self, repository):
        """Test listing DLQ items with multiple filters combined."""
        item1 = DeadLetterItem(
            job_id="job-1",
            agent_id="developer",
            agent_dir="/agents/developer",
            message="Message 1",
            source="api",
            project_id="project-a",
            queue_id="queue-1",
            priority=5,
            error_message="Error 1",
            retry_count=0,
            failed_at=datetime.utcnow().isoformat(),
            reason="MAX_RETRIES",
        )
        item2 = DeadLetterItem(
            job_id="job-2",
            agent_id="developer",
            agent_dir="/agents/developer",
            message="Message 2",
            source="api",
            project_id="project-a",
            queue_id="queue-1",
            priority=5,
            error_message="Error 2",
            retry_count=0,
            failed_at=datetime.utcnow().isoformat(),
            reason="MANUAL",
        )
        item3 = DeadLetterItem(
            job_id="job-3",
            agent_id="developer",
            agent_dir="/agents/developer",
            message="Message 3",
            source="api",
            project_id="project-b",
            queue_id="queue-1",
            priority=5,
            error_message="Error 3",
            retry_count=0,
            failed_at=datetime.utcnow().isoformat(),
            reason="MAX_RETRIES",
        )
        repository.enqueue(item1)
        repository.enqueue(item2)
        repository.enqueue(item3)
        
        items, total = repository.list(
            project_id="project-a",
            queue_id="queue-1",
            reason="MAX_RETRIES"
        )
        
        assert total == 1
        assert items[0].job_id == "job-1"

    def test_list_with_pagination_offset(self, repository):
        """Test listing DLQ items with offset pagination."""
        # Create 5 items
        for i in range(5):
            item = DeadLetterItem(
                job_id=f"job-{i}",
                agent_id="developer",
                agent_dir="/agents/developer",
                message=f"Message {i}",
                source="api",
                project_id="project-a",
                queue_id="queue-1",
                priority=5,
                error_message=f"Error {i}",
                retry_count=0,
                failed_at=datetime.utcnow().isoformat(),
                reason="MAX_RETRIES",
            )
            repository.enqueue(item)
        
        items, total = repository.list(offset=2)
        
        assert total == 5
        assert len(items) == 3  # 5 - 2 offset

    def test_list_with_pagination_limit(self, repository):
        """Test listing DLQ items with limit pagination."""
        # Create 5 items
        for i in range(5):
            item = DeadLetterItem(
                job_id=f"job-{i}",
                agent_id="developer",
                agent_dir="/agents/developer",
                message=f"Message {i}",
                source="api",
                project_id="project-a",
                queue_id="queue-1",
                priority=5,
                error_message=f"Error {i}",
                retry_count=0,
                failed_at=datetime.utcnow().isoformat(),
                reason="MAX_RETRIES",
            )
            repository.enqueue(item)
        
        items, total = repository.list(limit=2)
        
        assert total == 5
        assert len(items) == 2

    def test_list_with_pagination_offset_and_limit(self, repository):
        """Test listing DLQ items with both offset and limit."""
        # Create 10 items
        for i in range(10):
            item = DeadLetterItem(
                job_id=f"job-{i}",
                agent_id="developer",
                agent_dir="/agents/developer",
                message=f"Message {i}",
                source="api",
                project_id="project-a",
                queue_id="queue-1",
                priority=5,
                error_message=f"Error {i}",
                retry_count=0,
                failed_at=datetime.utcnow().isoformat(),
                reason="MAX_RETRIES",
            )
            repository.enqueue(item)
        
        items, total = repository.list(offset=3, limit=2)
        
        assert total == 10
        assert len(items) == 2

    def test_list_empty_repository(self, repository):
        """Test listing from empty repository returns empty list."""
        items, total = repository.list()
        
        assert total == 0
        assert items == []


class TestDeadLetterRepositoryDelete:
    """Tests for DeadLetterRepository.delete() method."""

    def test_delete_existing_item(self, repository):
        """Test deleting an existing DLQ item returns True."""
        item = DeadLetterItem(
            job_id="job-123",
            agent_id="developer",
            agent_dir="/agents/developer",
            message="Test message",
            source="api",
            project_id="project-abc",
            queue_id="queue-xyz",
            priority=5,
            error_message="Connection timeout",
            retry_count=0,
            failed_at=datetime.utcnow().isoformat(),
            reason="MAX_RETRIES",
        )
        created = repository.enqueue(item)
        
        result = repository.delete(created.dlq_id)
        
        assert result is True
        # Verify item is gone
        assert repository.get(created.dlq_id) is None

    def test_delete_nonexistent_item(self, repository):
        """Test deleting non-existent item returns False."""
        result = repository.delete("non-existent-id")
        
        assert result is False


class TestDeadLetterRepositoryDeleteByJobId:
    """Tests for DeadLetterRepository.delete_by_job_id() method."""

    def test_delete_by_job_id_existing(self, repository):
        """Test deleting DLQ item by job_id."""
        item = DeadLetterItem(
            job_id="job-456",
            agent_id="developer",
            agent_dir="/agents/developer",
            message="Test message",
            source="api",
            project_id="project-abc",
            queue_id="queue-xyz",
            priority=5,
            error_message="Connection timeout",
            retry_count=0,
            failed_at=datetime.utcnow().isoformat(),
            reason="MAX_RETRIES",
        )
        repository.enqueue(item)
        
        result = repository.delete_by_job_id("job-456")
        
        assert result is True
        assert repository.get_by_job_id("job-456") is None

    def test_delete_by_job_id_nonexistent(self, repository):
        """Test deleting by non-existent job_id returns False."""
        result = repository.delete_by_job_id("non-existent-job")
        
        assert result is False


class TestDeadLetterRepositoryCleanupByAge:
    """Tests for DeadLetterRepository.cleanup_by_age() method."""

    def test_cleanup_by_age_deletes_old_items(self, repository):
        """Test cleanup deletes items older than max_age_hours."""
        # Create an old item (25 hours ago)
        old_time = datetime.utcnow() - timedelta(hours=25)
        old_item = DeadLetterItem(
            job_id="job-old",
            agent_id="developer",
            agent_dir="/agents/developer",
            message="Old message",
            source="api",
            project_id="project-a",
            queue_id="queue-1",
            priority=5,
            error_message="Old error",
            retry_count=0,
            failed_at=old_time.isoformat(),
            moved_to_dlq_at=old_time.isoformat(),
            reason="MAX_RETRIES",
        )
        repository.enqueue(old_item)
        
        # Create a recent item (1 hour ago)
        recent_time = datetime.utcnow() - timedelta(hours=1)
        recent_item = DeadLetterItem(
            job_id="job-recent",
            agent_id="developer",
            agent_dir="/agents/developer",
            message="Recent message",
            source="api",
            project_id="project-a",
            queue_id="queue-1",
            priority=5,
            error_message="Recent error",
            retry_count=0,
            failed_at=recent_time.isoformat(),
            moved_to_dlq_at=recent_time.isoformat(),
            reason="MAX_RETRIES",
        )
        repository.enqueue(recent_item)
        
        # Cleanup items older than 24 hours
        deleted_count = repository.cleanup_by_age(max_age_hours=24)
        
        assert deleted_count == 1
        # Old item should be gone
        assert repository.get(old_item.dlq_id) is None
        # Recent item should still exist
        assert repository.get(recent_item.dlq_id) is not None

    def test_cleanup_by_age_with_reason_filter(self, repository):
        """Test cleanup_by_age with reason filter."""
        # Create old MAX_RETRIES item
        old_time = datetime.utcnow() - timedelta(hours=25)
        old_item1 = DeadLetterItem(
            job_id="job-old-1",
            agent_id="developer",
            agent_dir="/agents/developer",
            message="Old message 1",
            source="api",
            project_id="project-a",
            queue_id="queue-1",
            priority=5,
            error_message="Old error 1",
            retry_count=0,
            failed_at=old_time.isoformat(),
            moved_to_dlq_at=old_time.isoformat(),
            reason="MAX_RETRIES",
        )
        repository.enqueue(old_item1)
        
        # Create old MANUAL item (should not be deleted with MAX_RETRIES filter)
        old_item2 = DeadLetterItem(
            job_id="job-old-2",
            agent_id="developer",
            agent_dir="/agents/developer",
            message="Old message 2",
            source="api",
            project_id="project-a",
            queue_id="queue-1",
            priority=5,
            error_message="Old error 2",
            retry_count=0,
            failed_at=old_time.isoformat(),
            moved_to_dlq_at=old_time.isoformat(),
            reason="MANUAL",
        )
        repository.enqueue(old_item2)
        
        # Cleanup old MAX_RETRIES items only
        deleted_count = repository.cleanup_by_age(max_age_hours=24, reason="MAX_RETRIES")
        
        assert deleted_count == 1
        # MAX_RETRIES item should be gone
        assert repository.get(old_item1.dlq_id) is None
        # MANUAL item should still exist
        assert repository.get(old_item2.dlq_id) is not None

    def test_cleanup_by_age_no_matching_items(self, repository):
        """Test cleanup when no items match the age criteria."""
        # Create a recent item only
        recent_time = datetime.utcnow() - timedelta(hours=1)
        recent_item = DeadLetterItem(
            job_id="job-recent",
            agent_id="developer",
            agent_dir="/agents/developer",
            message="Recent message",
            source="api",
            project_id="project-a",
            queue_id="queue-1",
            priority=5,
            error_message="Recent error",
            retry_count=0,
            failed_at=recent_time.isoformat(),
            moved_to_dlq_at=recent_time.isoformat(),
            reason="MAX_RETRIES",
        )
        repository.enqueue(recent_item)
        
        # Try to cleanup items older than 24 hours (none should match)
        deleted_count = repository.cleanup_by_age(max_age_hours=24)
        
        assert deleted_count == 0
        # Recent item should still exist
        assert repository.get(recent_item.dlq_id) is not None


class TestDeadLetterRepositoryCount:
    """Tests for DeadLetterRepository.count() method."""

    def test_count_no_filters(self, repository):
        """Test counting all items with no filters."""
        for i in range(5):
            item = DeadLetterItem(
                job_id=f"job-{i}",
                agent_id="developer",
                agent_dir="/agents/developer",
                message=f"Message {i}",
                source="api",
                project_id="project-a",
                queue_id="queue-1",
                priority=5,
                error_message=f"Error {i}",
                retry_count=0,
                failed_at=datetime.utcnow().isoformat(),
                reason="MAX_RETRIES",
            )
            repository.enqueue(item)
        
        count = repository.count()
        
        assert count == 5

    def test_count_with_project_filter(self, repository):
        """Test counting items filtered by project_id."""
        item1 = DeadLetterItem(
            job_id="job-1",
            agent_id="developer",
            agent_dir="/agents/developer",
            message="Message 1",
            source="api",
            project_id="project-a",
            queue_id="queue-1",
            priority=5,
            error_message="Error 1",
            retry_count=0,
            failed_at=datetime.utcnow().isoformat(),
            reason="MAX_RETRIES",
        )
        item2 = DeadLetterItem(
            job_id="job-2",
            agent_id="developer",
            agent_dir="/agents/developer",
            message="Message 2",
            source="api",
            project_id="project-b",
            queue_id="queue-1",
            priority=5,
            error_message="Error 2",
            retry_count=0,
            failed_at=datetime.utcnow().isoformat(),
            reason="MAX_RETRIES",
        )
        repository.enqueue(item1)
        repository.enqueue(item2)
        
        count = repository.count(project_id="project-a")
        
        assert count == 1
