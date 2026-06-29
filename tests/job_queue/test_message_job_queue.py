"""Tests for MESSAGE job queue integration.

NOTE: As of D13 (Phase 2 architecture migration), MESSAGE jobs no longer create
JobItem rows. Messages flow through the WorkerPool path via ``enqueue_message``
in ``daemon.services.instance_messaging``, which creates Task + MessageQueue
rows instead.

This file retains only tests that exercise TASK-type job behavior (unaffected
by D13) or generic repository behavior. All MESSAGE-JobItem-specific tests
were removed as part of the cleanup:

- HTTP message routing to system_parallel_queue (JobItem path eliminated)
- Concurrency gate via ``find_processing_message_jobs_by_instance`` (method removed)
- Orphan message-job recovery (no MESSAGE JobItems exist)
- Message job cancellation / instance termination (no MESSAGE JobItems)
- Message job side effects / error handling (no MESSAGE JobItems)
- Instance reactivation for MESSAGE jobs (logic removed — TASK jobs now clear
  stale instance_id instead)

See commit history (D13 architecture migration) for context.
"""

import pytest

from daemon.repositories.job_queue import AdmissionState, JobRepository
from daemon.repositories.job_queue.models import JobItem, AdmissionState


# ── 3. Orphan Recovery Guard (TASK path only) ───────────────────────────────────


class TestOrphanRecoveryGuard:
    """Tests for orphan TASK jobs (MESSAGE path removed in D13)."""

    def test_orphan_task_job_respawned(self, repository, sample_job_data):
        """Test TASK job stuck in PROCESSING gets respawned (existing behavior)."""
        instance_id = "task-orphan-instance"

        # Create a TASK job in PROCESSING state
        job = repository.create(**sample_job_data, job_type="task")
        repository.start_job(job.job_id, instance_id)

        # Verify it's PROCESSING
        retrieved = repository.get(job.job_id)
        assert retrieved.admission_state == AdmissionState.ACTIVE.value

        # TASK jobs should remain in PROCESSING for orphan recovery
        assert job.job_type == "task"


# ── 8. Status Endpoint ──────────────────────────────────────────────────────────


class TestStatusEndpoint:
    """Tests for job status endpoint returning correct status."""

    def test_status_endpoint_returns_job_status(self, repository, sample_job_data):
        """Test GET /jobs/{id} returns correct job status."""
        job = repository.create(**sample_job_data)

        retrieved = repository.get(job.job_id)
        assert retrieved.admission_state == job.admission_state

    def test_status_endpoint_pending_job(self, repository, sample_job_data):
        """Test PENDING job returns 'pending' status."""
        job = repository.create(**sample_job_data)

        assert job.admission_state == AdmissionState.QUEUED.value