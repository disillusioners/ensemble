"""Comprehensive mock tests for Job Queue Backend API.

This test module covers all API endpoints for the job queue system,
including edge cases and error handling scenarios.
"""

import asyncio
import json
import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient

from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from daemon.repositories.job_queue import JobRepository
from daemon.repositories.job_queue.models import JobStatus, JobItem
from daemon.services.job_lock_manager import JobLockManager
from daemon.services.job_queue_service import JobQueueService
from daemon.routers.jobs import set_job_queue_service


# ==================== Fixtures ====================


@pytest.fixture
def engine():
    """Create in-memory SQLite engine for testing with thread-safe configuration."""
    engine = create_engine(
        "sqlite:///:memory:",
        echo=False,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def repository(engine):
    """Create JobRepository instance with fresh database."""
    return JobRepository(engine)


@pytest.fixture
def lock_manager():
    """Create fresh JobLockManager instance."""
    manager = JobLockManager()
    yield manager
    manager.clear()


@pytest.fixture
def job_queue_service(repository, lock_manager):
    """Create JobQueueService with repository and lock manager."""
    return JobQueueService(repository, lock_manager)


@pytest.fixture
def test_app(job_queue_service):
    """Create FastAPI test app with job queue router."""
    from fastapi import FastAPI
    from daemon.routers.jobs import router as jobs_router
    
    app = FastAPI()
    app.include_router(jobs_router, prefix="/api")
    
    # Set up dependency injection
    set_job_queue_service(job_queue_service)
    
    return app


@pytest.fixture
def client(test_app):
    """Create TestClient for API testing."""
    # Mock validate_agent_id at the location where it's imported from
    with patch("daemon.api.validate_agent_id") as mock_validate:
        mock_validate.return_value = ("developer", Path("/test/agents/developer"))
        with TestClient(test_app) as client:
            yield client


@pytest.fixture
def valid_job_data():
    """Valid job creation request data."""
    return {
        "agent_id": "developer",
        "message": "Test job message",
        "project_id": "test-project-123",
        "priority": 5,
        "source": "api",
        "metadata": {"test": True}
    }


@pytest.fixture
def valid_job_data_no_project():
    """Valid job creation request data without project_id."""
    return {
        "agent_id": "developer",
        "message": "Test job message",
        "priority": 5,
        "source": "api"
    }


# ==================== Helper Functions ====================


def create_mock_job(
    job_id="test-job-123",
    status=JobStatus.PENDING.value,
    priority=5,
    agent_dir="/agents/developer",
    project_id="test-project",
    instance_id=None,
    created_at=None,
    started_at=None,
    completed_at=None,
    result_summary=None,
    error_message=None,
    job_metadata=None,
):
    """Create a mock JobItem for testing."""
    job = MagicMock(spec=JobItem)
    job.job_id = job_id
    job.status = status
    job.priority = priority
    job.agent_dir = agent_dir
    job.project_id = project_id
    job.instance_id = instance_id
    job.created_at = created_at or datetime.utcnow().isoformat()
    job.started_at = started_at
    job.completed_at = completed_at
    job.result_summary = result_summary
    job.error_message = error_message
    job.job_metadata = job_metadata or {}
    return job


# ==================== A. Job Submission Tests ====================


class TestJobSubmission:
    """Tests for POST /api/jobs endpoint."""
    
    def test_submit_job_immediate_start_no_project(self, client, job_queue_service):
        """Test job submission without project_id starts immediately."""
        response = client.post("/api/jobs", json={
            "agent_id": "developer",
            "message": "Test job",
            "priority": 5
        })
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "processing"
        assert data["instance_id"] is not None
        assert data["message"] == "Job started immediately"
    
    def test_submit_job_queued_with_project(self, client, job_queue_service):
        """Test job submission with project_id gets queued when lock held."""
        # First, submit a job that holds the lock
        response1 = client.post("/api/jobs", json={
            "agent_id": "developer",
            "message": "First job",
            "project_id": "test-project",
            "priority": 5
        })
        assert response1.status_code == 200  # First job starts immediately
        
        # Second job should be queued
        response2 = client.post("/api/jobs", json={
            "agent_id": "developer",
            "message": "Second job",
            "project_id": "test-project",
            "priority": 5
        })
        
        assert response2.status_code == 202
        data = response2.json()
        assert data["status"] == "pending"
        assert data["position"] is not None
    
    def test_submit_job_all_priorities(self, client, job_queue_service):
        """Test job submission with all valid priority values (1-10)."""
        for priority in range(1, 11):
            response = client.post("/api/jobs", json={
                "agent_id": "developer",
                "message": f"Priority {priority} job",
                "priority": priority
            })
            
            assert response.status_code == 200, f"Failed for priority {priority}"
            assert response.json()["priority"] == priority
    
    def test_submit_job_missing_agent_id(self, client, job_queue_service):
        """Test job submission with missing agent_id returns 422."""
        response = client.post("/api/jobs", json={
            "message": "Test job",
            "priority": 5
        })
        
        assert response.status_code == 422
        data = response.json()
        assert "detail" in data
    
    def test_submit_job_missing_message(self, client, job_queue_service):
        """Test job submission with missing message returns 422."""
        response = client.post("/api/jobs", json={
            "agent_id": "developer",
            "priority": 5
        })
        
        assert response.status_code == 422
        data = response.json()
        assert "detail" in data
    
    def test_submit_job_empty_payload(self, client, job_queue_service):
        """Test job submission with empty payload returns 422."""
        response = client.post("/api/jobs", json={})
        
        assert response.status_code == 422
    
    def test_submit_job_invalid_priority_zero(self, client, job_queue_service):
        """Test job submission with priority 0 returns 422."""
        response = client.post("/api/jobs", json={
            "agent_id": "developer",
            "message": "Test job",
            "priority": 0
        })
        
        assert response.status_code == 422
    
    def test_submit_job_invalid_priority_eleven(self, client, job_queue_service):
        """Test job submission with priority 11 returns 422."""
        response = client.post("/api/jobs", json={
            "agent_id": "developer",
            "message": "Test job",
            "priority": 11
        })
        
        assert response.status_code == 422
    
    def test_submit_job_invalid_priority_negative(self, client, job_queue_service):
        """Test job submission with negative priority returns 422."""
        response = client.post("/api/jobs", json={
            "agent_id": "developer",
            "message": "Test job",
            "priority": -1
        })
        
        assert response.status_code == 422
    
    def test_submit_job_with_metadata(self, client, job_queue_service):
        """Test job submission with custom metadata."""
        response = client.post("/api/jobs", json={
            "agent_id": "developer",
            "message": "Test job with metadata",
            "priority": 5,
            "metadata": {
                "user_id": "user-123",
                "tags": ["important", "urgent"],
                "config": {"timeout": 300}
            }
        })
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "processing"
    
    def test_submit_job_with_unicode_message(self, client, job_queue_service):
        """Test job submission with unicode characters in message."""
        response = client.post("/api/jobs", json={
            "agent_id": "developer",
            "message": "Fix the bug in 文件.py 🐛 你好世界",
            "priority": 5
        })
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "processing"
    
    def test_submit_job_with_large_payload(self, client, job_queue_service):
        """Test job submission with large message (>1KB)."""
        large_message = "A" * 2000  # 2KB message
        
        response = client.post("/api/jobs", json={
            "agent_id": "developer",
            "message": large_message,
            "priority": 5
        })
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "processing"


# ==================== B. Job Retrieval Tests ====================


class TestJobRetrieval:
    """Tests for GET /api/jobs/{job_id} endpoint."""
    
    def test_get_existing_job(self, client, job_queue_service):
        """Test retrieving an existing job."""
        # First create a job
        create_response = client.post("/api/jobs", json={
            "agent_id": "developer",
            "message": "Test job",
            "priority": 5
        })
        job_id = create_response.json()["job_id"]
        
        # Get the job
        response = client.get(f"/api/jobs/{job_id}")
        
        assert response.status_code == 200
        data = response.json()
        assert data["job_id"] == job_id
        assert data["status"] == "processing"
    
    def test_get_nonexistent_job(self, client, job_queue_service):
        """Test retrieving a non-existent job returns 404."""
        response = client.get("/api/jobs/nonexistent-job-id")
        
        assert response.status_code == 404
        data = response.json()
        assert "detail" in data
        assert data["detail"]["error"] == "Job not found"
    
    def test_get_job_with_project_and_position(self, client, job_queue_service):
        """Test getting a pending job shows queue position."""
        # Create first job (holds lock)
        response1 = client.post("/api/jobs", json={
            "agent_id": "developer",
            "message": "First job",
            "project_id": "test-project",
            "priority": 5
        })
        
        # Create second job (queued)
        response2 = client.post("/api/jobs", json={
            "agent_id": "developer",
            "message": "Second job",
            "project_id": "test-project",
            "priority": 5
        })
        job_id = response2.json()["job_id"]
        
        # Get the queued job
        response = client.get(f"/api/jobs/{job_id}")
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "pending"
        assert data["position"] is not None


# ==================== C. Job Listing Tests ====================


class TestJobListing:
    """Tests for GET /api/jobs endpoint."""
    
    def test_list_all_jobs(self, client, job_queue_service):
        """Test listing all jobs."""
        # Create a few jobs
        for i in range(3):
            client.post("/api/jobs", json={
                "agent_id": "developer",
                "message": f"Job {i}",
                "priority": 5
            })
        
        response = client.get("/api/jobs")
        
        assert response.status_code == 200
        data = response.json()
        assert "jobs" in data
        assert "total" in data
        assert data["total"] >= 3
    
    def test_list_jobs_filter_by_status_pending(self, client, job_queue_service):
        """Test listing jobs filtered by status=pending."""
        # Create jobs that will be pending (with same project to queue)
        client.post("/api/jobs", json={
            "agent_id": "developer",
            "message": "First job",
            "project_id": "test-project",
            "priority": 5
        })
        client.post("/api/jobs", json={
            "agent_id": "developer",
            "message": "Second job",
            "project_id": "test-project",
            "priority": 5
        })
        
        response = client.get("/api/jobs?status=pending")
        
        assert response.status_code == 200
        data = response.json()
        for job in data["jobs"]:
            assert job["status"] == "pending"
    
    def test_list_jobs_filter_by_status_processing(self, client, job_queue_service):
        """Test listing jobs filtered by status=processing."""
        # Create job without project (starts immediately)
        client.post("/api/jobs", json={
            "agent_id": "developer",
            "message": "Processing job",
            "priority": 5
        })
        
        response = client.get("/api/jobs?status=processing")
        
        assert response.status_code == 200
        data = response.json()
        for job in data["jobs"]:
            assert job["status"] == "processing"
    
    def test_list_jobs_filter_by_project(self, client, job_queue_service):
        """Test listing jobs filtered by project_id."""
        project_id = "specific-project-123"
        
        # Create job for specific project
        client.post("/api/jobs", json={
            "agent_id": "developer",
            "message": "Project job",
            "project_id": project_id,
            "priority": 5
        })
        
        response = client.get(f"/api/jobs?project_id={project_id}")
        
        assert response.status_code == 200
        data = response.json()
        for job in data["jobs"]:
            assert job["project_id"] == project_id
    
    def test_list_jobs_with_limit(self, client, job_queue_service):
        """Test listing jobs with limit parameter."""
        # Create multiple jobs
        for i in range(5):
            client.post("/api/jobs", json={
                "agent_id": "developer",
                "message": f"Job {i}",
                "priority": 5
            })
        
        response = client.get("/api/jobs?limit=2")
        
        assert response.status_code == 200
        data = response.json()
        assert len(data["jobs"]) <= 2
    
    def test_list_jobs_empty_result(self, client, job_queue_service):
        """Test listing jobs with non-matching filters returns empty list."""
        response = client.get("/api/jobs?project_id=nonexistent-project")
        
        assert response.status_code == 200
        data = response.json()
        assert data["jobs"] == []
        assert data["total"] == 0
    
    def test_list_jobs_invalid_status(self, client, job_queue_service):
        """Test listing jobs with invalid status returns 400."""
        response = client.get("/api/jobs?status=invalid_status")
        
        assert response.status_code == 400
        data = response.json()
        assert "detail" in data
        assert "Invalid status" in data["detail"]["error"]


# ==================== D. Job Cancellation Tests ====================


class TestJobCancellation:
    """Tests for DELETE /api/jobs/{job_id} endpoint."""
    
    def test_cancel_pending_job(self, client, job_queue_service):
        """Test cancelling a pending job."""
        # Create first job to hold lock
        client.post("/api/jobs", json={
            "agent_id": "developer",
            "message": "First job",
            "project_id": "test-project",
            "priority": 5
        })
        
        # Create second job (will be pending)
        create_response = client.post("/api/jobs", json={
            "agent_id": "developer",
            "message": "Second job",
            "project_id": "test-project",
            "priority": 5
        })
        job_id = create_response.json()["job_id"]
        
        # Cancel the pending job
        response = client.delete(f"/api/jobs/{job_id}")
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "cancelled"
    
    def test_cancel_processing_job(self, client, job_queue_service):
        """Test cancelling a processing job."""
        # Create job without project (starts immediately)
        create_response = client.post("/api/jobs", json={
            "agent_id": "developer",
            "message": "Processing job",
            "priority": 5
        })
        job_id = create_response.json()["job_id"]
        
        # Cancel the processing job
        response = client.delete(f"/api/jobs/{job_id}")
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "cancelled"
    
    def test_cancel_completed_job_fails(self, client, job_queue_service, repository):
        """Test cancelling a completed job returns 400."""
        # Create and start a job
        create_response = client.post("/api/jobs", json={
            "agent_id": "developer",
            "message": "Job to complete",
            "priority": 5
        })
        job_id = create_response.json()["job_id"]
        
        # Manually mark as completed
        repository.complete_job(job_id, "Test completion")
        
        # Try to cancel completed job
        response = client.delete(f"/api/jobs/{job_id}")
        
        assert response.status_code == 400
        data = response.json()
        assert "cannot be cancelled" in data["detail"]["error"]
    
    def test_cancel_nonexistent_job(self, client, job_queue_service):
        """Test cancelling a non-existent job returns 404."""
        response = client.delete("/api/jobs/nonexistent-job-id")
        
        assert response.status_code == 404
        data = response.json()
        assert data["detail"]["error"] == "Job not found"
    
    def test_cancel_already_cancelled_job(self, client, job_queue_service, repository):
        """Test cancelling an already cancelled job returns 400."""
        # Create a job
        create_response = client.post("/api/jobs", json={
            "agent_id": "developer",
            "message": "Job to cancel",
            "priority": 5
        })
        job_id = create_response.json()["job_id"]
        
        # Cancel it
        client.delete(f"/api/jobs/{job_id}")
        
        # Try to cancel again
        response = client.delete(f"/api/jobs/{job_id}")
        
        assert response.status_code == 400


# ==================== E. Job Retry Tests ====================


class TestJobRetry:
    """Tests for POST /api/jobs/{job_id}/retry endpoint."""
    
    def test_retry_failed_job(self, client, job_queue_service, repository):
        """Test retrying a failed job."""
        # Create and fail a job
        create_response = client.post("/api/jobs", json={
            "agent_id": "developer",
            "message": "Job to fail",
            "priority": 5
        })
        job_id = create_response.json()["job_id"]
        
        # Manually mark as failed
        repository.fail_job(job_id, "Test failure")
        
        # Retry the job
        response = client.post(f"/api/jobs/{job_id}/retry")
        
        assert response.status_code == 200
        data = response.json()
        # Should create a new job with new ID
        assert data["job_id"] != job_id
        assert data["status"] in ["pending", "processing"]
    
    def test_retry_completed_job_fails(self, client, job_queue_service, repository):
        """Test retrying a completed job returns 400."""
        # Create and complete a job
        create_response = client.post("/api/jobs", json={
            "agent_id": "developer",
            "message": "Job to complete",
            "priority": 5
        })
        job_id = create_response.json()["job_id"]
        
        # Manually mark as completed
        repository.complete_job(job_id, "Test completion")
        
        # Try to retry
        response = client.post(f"/api/jobs/{job_id}/retry")
        
        assert response.status_code == 400
        data = response.json()
        assert "cannot be retried" in data["detail"]["error"]
    
    def test_retry_pending_job_fails(self, client, job_queue_service):
        """Test retrying a pending job returns 400."""
        # Create first job to hold lock
        client.post("/api/jobs", json={
            "agent_id": "developer",
            "message": "First job",
            "project_id": "test-project",
            "priority": 5
        })
        
        # Create second job (will be pending)
        create_response = client.post("/api/jobs", json={
            "agent_id": "developer",
            "message": "Second job",
            "project_id": "test-project",
            "priority": 5
        })
        job_id = create_response.json()["job_id"]
        
        # Try to retry pending job
        response = client.post(f"/api/jobs/{job_id}/retry")
        
        assert response.status_code == 400
    
    def test_retry_nonexistent_job(self, client, job_queue_service):
        """Test retrying a non-existent job returns 404."""
        response = client.post("/api/jobs/nonexistent-job-id/retry")
        
        assert response.status_code == 404
        data = response.json()
        assert data["detail"]["error"] == "Job not found"


# ==================== F. Job Events Tests (SSE) ====================


class TestJobEvents:
    """Tests for GET /api/jobs/{job_id}/events endpoint."""
    
    def test_subscribe_to_job_events(self, client, job_queue_service, repository):
        """Test subscribing to job events via SSE for completed job."""
        # Create and complete a job so SSE immediately sends events
        create_response = client.post("/api/jobs", json={
            "agent_id": "developer",
            "message": "Test job",
            "priority": 5
        })
        job_id = create_response.json()["job_id"]
        repository.complete_job(job_id, "Done")
        
        # Subscribe to events - should immediately get connected + completed
        with client.stream("GET", f"/api/jobs/{job_id}/events") as response:
            assert response.status_code == 200
            assert "text/event-stream" in response.headers["content-type"]
            
            # Read all events from completed job
            content = ""
            lines_read = 0
            for line in response.iter_lines():
                lines_read += 1
                content += line + "\n"
                # Exit after reading enough lines for both events (event + data for each)
                if lines_read >= 10:
                    break
                # If we've seen both connected and completed events, we can exit
                if "connected" in content and "completed" in content:
                    break
            
            assert "connected" in content
            # For completed jobs, both events should be sent
            assert "completed" in content or "processing" in content
    
    def test_subscribe_to_nonexistent_job(self, client, job_queue_service):
        """Test subscribing to events for non-existent job returns 404."""
        response = client.get("/api/jobs/nonexistent-job-id/events")
        
        assert response.status_code == 404
        data = response.json()
        assert data["detail"]["error"] == "Job not found"
    
    def test_sse_endpoint_returns_sse_content_type(self, client, job_queue_service, repository):
        """Test SSE endpoint returns correct content type."""
        # Create and complete a job
        create_response = client.post("/api/jobs", json={
            "agent_id": "developer",
            "message": "Completed job",
            "priority": 5
        })
        job_id = create_response.json()["job_id"]
        repository.complete_job(job_id, "Done")
        
        # Verify SSE endpoint exists and returns correct content type
        with client.stream("GET", f"/api/jobs/{job_id}/events") as response:
            assert response.status_code == 200
            assert "text/event-stream" in response.headers.get("content-type", "")


# ==================== G. Edge Cases ====================


class TestEdgeCases:
    """Tests for edge cases and special scenarios."""
    
    def test_concurrent_enqueues_same_project(self, client, job_queue_service):
        """Test 20 concurrent enqueues to same project."""
        import concurrent.futures
        
        project_id = "concurrent-test-project"
        results = []
        
        def submit_job(i):
            response = client.post("/api/jobs", json={
                "agent_id": "developer",
                "message": f"Concurrent job {i}",
                "project_id": project_id,
                "priority": 5
            })
            return response.status_code, response.json()
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
            futures = [executor.submit(submit_job, i) for i in range(20)]
            for future in concurrent.futures.as_completed(futures):
                results.append(future.result())
        
        # All requests should succeed
        assert len(results) == 20
        
        # Count processing vs pending
        processing = sum(1 for status, _ in results if status == 200)
        pending = sum(1 for status, _ in results if status == 202)
        
        # At least one should be processing (the one that got the lock)
        # The rest should be pending (queued)
        assert processing >= 1
        assert pending >= 1
        assert processing + pending == 20
    
    def test_priority_ordering(self, client, job_queue_service):
        """Test that higher priority jobs are processed first."""
        # Create first job to hold lock
        client.post("/api/jobs", json={
            "agent_id": "developer",
            "message": "Lock holder",
            "project_id": "priority-test-project",
            "priority": 5
        })
        
        # Queue jobs with different priorities (lower priority first)
        low_priority_id = client.post("/api/jobs", json={
            "agent_id": "developer",
            "message": "Low priority",
            "project_id": "priority-test-project",
            "priority": 1
        }).json()["job_id"]
        
        high_priority_id = client.post("/api/jobs", json={
            "agent_id": "developer",
            "message": "High priority",
            "project_id": "priority-test-project",
            "priority": 10
        }).json()["job_id"]
        
        medium_priority_id = client.post("/api/jobs", json={
            "agent_id": "developer",
            "message": "Medium priority",
            "project_id": "priority-test-project",
            "priority": 5
        }).json()["job_id"]
        
        # List pending jobs and verify order
        response = client.get("/api/jobs?status=pending&project_id=priority-test-project")
        jobs = response.json()["jobs"]
        
        # Jobs should be ordered by priority (desc), then created_at (asc)
        if len(jobs) >= 2:
            # High priority should be first
            assert jobs[0]["job_id"] == high_priority_id
    
    def test_special_characters_in_message(self, client, job_queue_service):
        """Test job with special characters in message."""
        special_message = "Test with special chars: \n\t\r\"'\\<>&{}[]"
        
        response = client.post("/api/jobs", json={
            "agent_id": "developer",
            "message": special_message,
            "priority": 5
        })
        
        assert response.status_code == 200
    
    def test_unicode_in_all_fields(self, client, job_queue_service):
        """Test job with unicode in all text fields."""
        response = client.post("/api/jobs", json={
            "agent_id": "developer",
            "message": "你好世界 مرحبا Hello 世界 🌍",
            "priority": 5,
            "metadata": {"key": "值 🎉"}
        })
        
        assert response.status_code == 200
    
    def test_null_bytes_in_message(self, client, job_queue_service):
        """Test job with null bytes in message (should be handled gracefully)."""
        # Note: JSON doesn't allow raw null bytes, but \u0000 escape should work
        response = client.post("/api/jobs", json={
            "agent_id": "developer",
            "message": "Test\u0000with\u0000nulls",
            "priority": 5
        })
        
        # Should either accept or reject gracefully
        assert response.status_code in [200, 400]
    
    def test_very_long_agent_id(self, client, job_queue_service):
        """Test job with very long agent_id path."""
        long_id = "a" * 500
        
        response = client.post("/api/jobs", json={
            "agent_id": long_id,
            "message": "Test",
            "priority": 5
        })
        
        # Should handle gracefully (may fail validation)
        assert response.status_code in [200, 400, 404]
    
    def test_empty_message_string(self, client, job_queue_service):
        """Test job with empty message string."""
        response = client.post("/api/jobs", json={
            "agent_id": "developer",
            "message": "",
            "priority": 5
        })
        
        # Empty string is technically valid, but Pydantic may reject it
        assert response.status_code in [200, 422]
    
    def test_whitespace_only_message(self, client, job_queue_service):
        """Test job with whitespace-only message."""
        response = client.post("/api/jobs", json={
            "agent_id": "developer",
            "message": "   \t\n   ",
            "priority": 5
        })
        
        # Should be accepted (whitespace is valid content)
        assert response.status_code == 200
    
    def test_duplicate_job_id_prevention(self, client, job_queue_service, repository):
        """Test that duplicate job IDs cannot be created."""
        # Create a job
        response1 = client.post("/api/jobs", json={
            "agent_id": "developer",
            "message": "First job",
            "priority": 5
        })
        job_id_1 = response1.json()["job_id"]
        
        # Create another job - should get different ID
        response2 = client.post("/api/jobs", json={
            "agent_id": "developer",
            "message": "Second job",
            "priority": 5
        })
        job_id_2 = response2.json()["job_id"]
        
        assert job_id_1 != job_id_2
    
    def test_different_projects_parallel(self, client, job_queue_service):
        """Test that jobs for different projects can run in parallel."""
        # Create jobs for different projects
        response1 = client.post("/api/jobs", json={
            "agent_id": "developer",
            "message": "Project A job",
            "project_id": "project-a",
            "priority": 5
        })
        
        response2 = client.post("/api/jobs", json={
            "agent_id": "developer",
            "message": "Project B job",
            "project_id": "project-b",
            "priority": 5
        })
        
        # Both should start immediately (different projects)
        assert response1.status_code == 200
        assert response2.status_code == 200
        assert response1.json()["status"] == "processing"
        assert response2.json()["status"] == "processing"
    
    def test_job_metadata_preserved_on_retry(self, client, job_queue_service, repository):
        """Test that metadata is preserved when retrying a failed job."""
        original_metadata = {
            "user_id": "user-123",
            "request_id": "req-456",
            "custom": {"nested": "data"}
        }
        
        # Create job with metadata
        create_response = client.post("/api/jobs", json={
            "agent_id": "developer",
            "message": "Job to retry",
            "priority": 5,
            "metadata": original_metadata
        })
        job_id = create_response.json()["job_id"]
        
        # Mark as failed
        repository.fail_job(job_id, "Test failure")
        
        # Retry the job
        retry_response = client.post(f"/api/jobs/{job_id}/retry")
        
        assert retry_response.status_code == 200
        new_job_id = retry_response.json()["job_id"]
        
        # Verify metadata was preserved
        new_job = repository.get(new_job_id)
        assert new_job is not None
        assert new_job.job_metadata == original_metadata
    
    def test_limit_boundary_values(self, client, job_queue_service):
        """Test limit parameter with boundary values."""
        # Create some jobs
        for i in range(5):
            client.post("/api/jobs", json={
                "agent_id": "developer",
                "message": f"Job {i}",
                "priority": 5
            })
        
        # Test limit=1
        response = client.get("/api/jobs?limit=1")
        assert response.status_code == 200
        assert len(response.json()["jobs"]) <= 1
        
        # Test limit=100 (max allowed)
        response = client.get("/api/jobs?limit=100")
        assert response.status_code == 200
        
        # Test limit=0 (should be clamped to 1)
        response = client.get("/api/jobs?limit=0")
        assert response.status_code == 200
        
        # Test limit=101 (should be clamped to 100)
        response = client.get("/api/jobs?limit=101")
        assert response.status_code == 200


# ==================== Performance Tests ====================


class TestPerformance:
    """Tests for performance-related scenarios."""
    
    def test_large_queue_listing(self, client, job_queue_service):
        """Test listing with many jobs in queue."""
        # Create many jobs (with same project to queue them)
        client.post("/api/jobs", json={
            "agent_id": "developer",
            "message": "Lock holder",
            "project_id": "perf-test-project",
            "priority": 5
        })
        
        for i in range(50):
            client.post("/api/jobs", json={
                "agent_id": "developer",
                "message": f"Queued job {i}",
                "project_id": "perf-test-project",
                "priority": 5
            })
        
        # List jobs
        response = client.get("/api/jobs?limit=50")
        
        assert response.status_code == 200
        data = response.json()
        assert len(data["jobs"]) <= 50
    
    def test_rapid_sequential_submissions(self, client, job_queue_service):
        """Test rapid sequential job submissions."""
        job_ids = []
        
        for i in range(20):
            response = client.post("/api/jobs", json={
                "agent_id": "developer",
                "message": f"Rapid job {i}",
                "priority": 5
            })
            assert response.status_code == 200
            job_ids.append(response.json()["job_id"])
        
        # All job IDs should be unique
        assert len(set(job_ids)) == 20


# ==================== Run Tests ====================


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
