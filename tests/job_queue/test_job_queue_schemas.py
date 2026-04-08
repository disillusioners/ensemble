"""Tests for JobQueue Pydantic schemas.

This module tests the request/response schemas for job queue management:
- JobQueueCreateRequest validation
- JobQueueUpdateRequest validation
- JobQueueResponse serialization

The tests verify validators, field constraints, and serialization.
"""

import pytest
from pydantic import ValidationError

from daemon.routers.schemas import (
    JobQueueCreateRequest,
    JobQueueUpdateRequest,
    JobQueueResponse,
)
from daemon.repositories.job_queue.models import (
    JobQueue,
    QueueType,
)


class TestJobQueueCreateRequest:
    """Tests for JobQueueCreateRequest schema."""

    def test_create_request_valid_fifo(self):
        """Test valid FIFO queue creation request."""
        request = JobQueueCreateRequest(
            queue_name="my-fifo-queue",
            queue_type="fifo",
            concurrency_limit=1,
            description="A FIFO queue",
        )
        
        assert request.queue_name == "my-fifo-queue"
        assert request.queue_type == "fifo"
        assert request.concurrency_limit == 1
        assert request.description == "A FIFO queue"

    def test_create_request_valid_parallel(self):
        """Test valid parallel queue creation request."""
        request = JobQueueCreateRequest(
            queue_name="my-parallel-queue",
            queue_type="parallel",
            concurrency_limit=5,
            description="A parallel queue with 5 concurrent jobs",
        )
        
        assert request.queue_name == "my-parallel-queue"
        assert request.queue_type == "parallel"
        assert request.concurrency_limit == 5
        assert request.description == "A parallel queue with 5 concurrent jobs"

    def test_create_request_minimal_fields(self):
        """Test creating queue with minimal required fields."""
        request = JobQueueCreateRequest(
            queue_name="minimal-queue",
        )
        
        assert request.queue_name == "minimal-queue"
        assert request.queue_type == "fifo"  # Default
        assert request.concurrency_limit == 1  # Default
        assert request.description is None  # Default

    def test_create_request_reserved_name_fifo_rejected(self):
        """Test that 'system_fifo_queue' as queue_name is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            JobQueueCreateRequest(queue_name="system_fifo_queue")
        
        errors = exc_info.value.errors()
        assert len(errors) == 1
        assert "reserved" in errors[0]["msg"].lower()

    def test_create_request_reserved_name_parallel_rejected(self):
        """Test that 'system_parallel_queue' as queue_name is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            JobQueueCreateRequest(queue_name="system_parallel_queue")
        
        errors = exc_info.value.errors()
        assert len(errors) == 1
        assert "reserved" in errors[0]["msg"].lower()

    def test_create_request_reserved_name_case_insensitive(self):
        """Test that reserved name check is case-insensitive."""
        with pytest.raises(ValidationError):
            JobQueueCreateRequest(queue_name="SYSTEM_FIFO_QUEUE")
        
        with pytest.raises(ValidationError):
            JobQueueCreateRequest(queue_name="System_Parallel_Queue")

    def test_create_request_fifo_concurrency_forced_to_1(self):
        """Test FIFO queue concurrency_limit is automatically set to 1."""
        request = JobQueueCreateRequest(
            queue_name="my-queue",
            queue_type="fifo",
            concurrency_limit=10,  # Invalid for FIFO, should be forced to 1
        )
        
        # The model_validator forces concurrency to 1 for FIFO
        assert request.queue_type == "fifo"
        assert request.concurrency_limit == 1

    def test_create_request_parallel_allows_higher_concurrency(self):
        """Test parallel queue allows higher concurrency values."""
        request = JobQueueCreateRequest(
            queue_name="my-queue",
            queue_type="parallel",
            concurrency_limit=10,
        )
        
        assert request.queue_type == "parallel"
        assert request.concurrency_limit == 10

    def test_create_request_name_normalization(self):
        """Test queue_name is normalized (stripped of whitespace by Pydantic)."""
        request = JobQueueCreateRequest(
            queue_name="  my-queue  ",
        )
        
        # Pydantic strips leading/trailing whitespace by default
        assert request.queue_name == "my-queue"

    def test_create_request_invalid_queue_type_rejected(self):
        """Test invalid queue_type is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            JobQueueCreateRequest(
                queue_name="my-queue",
                queue_type="priority",  # Invalid
            )
        
        errors = exc_info.value.errors()
        assert len(errors) == 1
        assert "queue_type" in str(errors[0])

    def test_create_request_empty_queue_name_rejected(self):
        """Test empty queue_name is rejected (min_length=1)."""
        with pytest.raises(ValidationError) as exc_info:
            JobQueueCreateRequest(queue_name="")
        
        errors = exc_info.value.errors()
        assert len(errors) == 1
        assert "queue_name" in str(errors[0])

    def test_create_request_queue_name_too_long_rejected(self):
        """Test queue_name exceeding max_length is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            JobQueueCreateRequest(queue_name="a" * 101)  # max_length=100
        
        errors = exc_info.value.errors()
        assert len(errors) == 1

    def test_create_request_concurrency_limit_minimum(self):
        """Test concurrency_limit has minimum of 1."""
        request = JobQueueCreateRequest(
            queue_name="my-queue",
            concurrency_limit=1,
        )
        
        assert request.concurrency_limit == 1

    def test_create_request_concurrency_limit_exceeds_maximum_rejected(self):
        """Test concurrency_limit exceeding max (20) is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            JobQueueCreateRequest(
                queue_name="my-queue",
                concurrency_limit=21,  # max is 20
            )
        
        errors = exc_info.value.errors()
        assert len(errors) == 1

    def test_create_request_description_too_long_rejected(self):
        """Test description exceeding max_length is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            JobQueueCreateRequest(
                queue_name="my-queue",
                description="a" * 501,  # max_length=500
            )
        
        errors = exc_info.value.errors()
        assert len(errors) == 1


class TestJobQueueUpdateRequest:
    """Tests for JobQueueUpdateRequest schema."""

    def test_update_request_partial_update_name_only(self):
        """Test updating just the queue name."""
        request = JobQueueUpdateRequest(queue_name="new-name")
        
        assert request.queue_name == "new-name"
        assert request.concurrency_limit is None
        assert request.is_paused is None
        assert request.description is None

    def test_update_request_partial_update_concurrency_only(self):
        """Test updating just the concurrency limit."""
        request = JobQueueUpdateRequest(concurrency_limit=5)
        
        assert request.queue_name is None
        assert request.concurrency_limit == 5

    def test_update_request_partial_update_paused_only(self):
        """Test updating just the paused status."""
        request = JobQueueUpdateRequest(is_paused=True)
        
        assert request.is_paused is True

    def test_update_request_partial_update_description_only(self):
        """Test updating just the description."""
        request = JobQueueUpdateRequest(description="New description")
        
        assert request.description == "New description"

    def test_update_request_reserved_name_protection_fifo(self):
        """Test cannot rename to system_fifo_queue."""
        with pytest.raises(ValidationError) as exc_info:
            JobQueueUpdateRequest(queue_name="system_fifo_queue")
        
        errors = exc_info.value.errors()
        assert len(errors) == 1
        assert "reserved" in errors[0]["msg"].lower()

    def test_update_request_reserved_name_protection_parallel(self):
        """Test cannot rename to system_parallel_queue."""
        with pytest.raises(ValidationError) as exc_info:
            JobQueueUpdateRequest(queue_name="system_parallel_queue")
        
        errors = exc_info.value.errors()
        assert len(errors) == 1
        assert "reserved" in errors[0]["msg"].lower()

    def test_update_request_reserved_name_case_insensitive(self):
        """Test reserved name protection is case-insensitive."""
        with pytest.raises(ValidationError):
            JobQueueUpdateRequest(queue_name="SYSTEM_FIFO_QUEUE")
        
        with pytest.raises(ValidationError):
            JobQueueUpdateRequest(queue_name="System_Parallel_Queue")

    def test_update_request_empty_name_rejected(self):
        """Test empty queue_name is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            JobQueueUpdateRequest(queue_name="")
        
        errors = exc_info.value.errors()
        assert len(errors) == 1

    def test_update_request_name_too_long_rejected(self):
        """Test queue_name exceeding max_length is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            JobQueueUpdateRequest(queue_name="a" * 101)
        
        errors = exc_info.value.errors()
        assert len(errors) == 1

    def test_update_request_concurrency_validation_min(self):
        """Test concurrency_limit has minimum of 1."""
        request = JobQueueUpdateRequest(concurrency_limit=1)
        
        assert request.concurrency_limit == 1

    def test_update_request_concurrency_validation_max(self):
        """Test concurrency_limit has maximum of 20."""
        request = JobQueueUpdateRequest(concurrency_limit=20)
        
        assert request.concurrency_limit == 20

    def test_update_request_concurrency_exceeds_max_rejected(self):
        """Test concurrency_limit exceeding max is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            JobQueueUpdateRequest(concurrency_limit=25)
        
        errors = exc_info.value.errors()
        assert len(errors) == 1

    def test_update_request_multiple_fields(self):
        """Test updating multiple fields at once."""
        request = JobQueueUpdateRequest(
            queue_name="new-name",
            concurrency_limit=10,
            is_paused=True,
            description="Updated description",
        )
        
        assert request.queue_name == "new-name"
        assert request.concurrency_limit == 10
        assert request.is_paused is True
        assert request.description == "Updated description"


class TestJobQueueResponse:
    """Tests for JobQueueResponse schema."""

    def test_response_serialization_from_job_queue(self):
        """Test JobQueueResponse serializes from JobQueue model correctly."""
        # Create a JobQueue model instance
        queue = JobQueue(
            queue_id="queue-123",
            project_id="project-abc",
            queue_name="my-queue",
            queue_name_lower="my-queue",
            queue_type="parallel",
            concurrency_limit=5,
            is_system=False,
            is_paused=False,
            description="Test queue",
            created_at="2026-04-09T10:00:00",
            updated_at="2026-04-09T12:00:00",
        )
        
        # Create response from model (manual mapping)
        response = JobQueueResponse(
            queue_id=queue.queue_id,
            project_id=queue.project_id,
            queue_name=queue.queue_name,
            queue_type=queue.queue_type,
            concurrency_limit=queue.concurrency_limit,
            is_system=queue.is_system,
            is_paused=queue.is_paused,
            description=queue.description,
            created_at=queue.created_at,
            updated_at=queue.updated_at,
            active_jobs=2,
            pending_jobs=10,
        )
        
        assert response.queue_id == "queue-123"
        assert response.project_id == "project-abc"
        assert response.queue_name == "my-queue"
        assert response.queue_type == "parallel"
        assert response.concurrency_limit == 5
        assert response.is_system is False
        assert response.is_paused is False
        assert response.description == "Test queue"
        assert response.created_at == "2026-04-09T10:00:00"
        assert response.updated_at == "2026-04-09T12:00:00"
        assert response.active_jobs == 2
        assert response.pending_jobs == 10

    def test_response_defaults_active_jobs(self):
        """Test active_jobs defaults to 0."""
        response = JobQueueResponse(
            queue_id="queue-123",
            project_id="project-abc",
            queue_name="my-queue",
            queue_type="fifo",
            concurrency_limit=1,
            is_system=False,
            is_paused=False,
            created_at="2026-04-09T10:00:00",
            updated_at="2026-04-09T12:00:00",
        )
        
        assert response.active_jobs == 0
        assert response.pending_jobs == 0

    def test_response_serialization(self):
        """Test JobQueueResponse serializes to dict correctly."""
        response = JobQueueResponse(
            queue_id="queue-123",
            project_id="project-abc",
            queue_name="my-queue",
            queue_type="fifo",
            concurrency_limit=1,
            is_system=True,
            is_paused=True,
            description="System queue",
            created_at="2026-04-09T10:00:00",
            updated_at="2026-04-09T12:00:00",
            active_jobs=1,
            pending_jobs=5,
        )
        
        # Convert to dict
        data = response.model_dump()
        
        assert isinstance(data, dict)
        assert data["queue_id"] == "queue-123"
        assert data["project_id"] == "project-abc"
        assert data["queue_name"] == "my-queue"
        assert data["queue_type"] == "fifo"
        assert data["concurrency_limit"] == 1
        assert data["is_system"] is True
        assert data["is_paused"] is True
        assert data["description"] == "System queue"
        assert data["active_jobs"] == 1
        assert data["pending_jobs"] == 5

    def test_response_all_fields_optional_except_required(self):
        """Test required vs optional fields in response."""
        response = JobQueueResponse(
            queue_id="queue-123",
            project_id="project-abc",
            queue_name="my-queue",
            queue_type="parallel",
            concurrency_limit=3,
            is_system=False,
            is_paused=False,
            created_at="2026-04-09T10:00:00",
            updated_at="2026-04-09T12:00:00",
        )
        
        # All fields should be accessible
        assert response.queue_id is not None
        assert response.project_id is not None
        assert response.description is None  # Optional


class TestSchemaIntegration:
    """Integration tests for schema workflows."""

    def test_create_then_update_workflow(self):
        """Test the typical create then update workflow."""
        # Create request
        create_request = JobQueueCreateRequest(
            queue_name="new-queue",
            queue_type="parallel",
            concurrency_limit=5,
            description="Initial description",
        )
        
        assert create_request.queue_name == "new-queue"
        assert create_request.concurrency_limit == 5
        
        # Update request
        update_request = JobQueueUpdateRequest(
            description="Updated description",
            is_paused=True,
        )
        
        assert update_request.description == "Updated description"
        assert update_request.is_paused is True

    def test_system_queue_not_creatable(self):
        """Test that system queues cannot be created via API."""
        # Both system names should be rejected
        with pytest.raises(ValidationError):
            JobQueueCreateRequest(queue_name="system_fifo_queue")
        
        with pytest.raises(ValidationError):
            JobQueueCreateRequest(queue_name="system_parallel_queue")

    def test_system_queue_not_renamable(self):
        """Test that existing queues cannot be renamed to system names."""
        with pytest.raises(ValidationError):
            JobQueueUpdateRequest(queue_name="system_fifo_queue")
        
        with pytest.raises(ValidationError):
            JobQueueUpdateRequest(queue_name="system_parallel_queue")
