"""Tests for JobQueueMgmtService."""

import pytest
from unittest.mock import AsyncMock, MagicMock, PropertyMock, call
from datetime import datetime, timezone

from daemon.services.job_queue_mgmt_service import (
    JobQueueMgmtService,
    RESERVED_QUEUE_NAMES,
)
from daemon.repositories.job_queue.models import JobQueue, JobStatus


# ---------------------------------------------------------------------------
# Helper: build a mock JobQueue
# ---------------------------------------------------------------------------

def make_queue(
    queue_id: str = "q-001",
    project_id: str = "proj-1",
    queue_name: str = "test-queue",
    queue_type: str = "fifo",
    concurrency_limit: int = 1,
    is_system: bool = False,
    is_paused: bool = False,
) -> JobQueue:
    """Factory to build a JobQueue with sensible defaults."""
    queue = MagicMock(spec=JobQueue)
    queue.queue_id = queue_id
    queue.project_id = project_id
    queue.queue_name = queue_name
    queue.queue_name_lower = queue_name.lower()
    queue.queue_type = queue_type
    queue.concurrency_limit = concurrency_limit
    queue.is_system = is_system
    queue.is_paused = is_paused
    queue.description = None
    queue.created_at = datetime.now(timezone.utc).isoformat()
    queue.updated_at = datetime.now(timezone.utc).isoformat()
    queue.to_dict.return_value = {
        "queue_id": queue_id,
        "project_id": project_id,
        "queue_name": queue_name,
        "queue_name_lower": queue_name.lower(),
        "queue_type": queue_type,
        "concurrency_limit": concurrency_limit,
        "is_system": is_system,
        "is_paused": is_paused,
        "description": None,
        "created_at": queue.created_at,
        "updated_at": queue.updated_at,
    }
    return queue


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_queue_repo():
    """Mock JobQueueRepository."""
    return MagicMock()


@pytest.fixture
def mock_job_repo():
    """Mock JobRepository."""
    return MagicMock()


@pytest.fixture
def service(mock_queue_repo, mock_job_repo):
    """Build a service with mocked dependencies."""
    return JobQueueMgmtService(mock_queue_repo, mock_job_repo)


# ---------------------------------------------------------------------------
# TestAutoProvisionSystemQueues
# ---------------------------------------------------------------------------

class TestAutoProvisionSystemQueues:
    """Tests for auto_provision_system_queues()."""

    @pytest.mark.asyncio
    async def test_auto_provision_creates_all_system_queues(self, service, mock_queue_repo):
        """Creates FIFO, parallel, and KB FIFO system queues for project."""
        # No queue exists yet
        mock_queue_repo.get_by_name.return_value = None
        mock_queue_repo.create.side_effect = [
            make_queue(queue_id="sys-fifo", queue_name="system_fifo_queue", is_system=True),
            make_queue(queue_id="sys-para", queue_name="system_parallel_queue", is_system=True),
            make_queue(queue_id="sys-kb-fifo", queue_name="system_kb_fifo_queue", is_system=True),
            make_queue(queue_id="sys-defer", queue_name="system_defer_queue", is_system=True),
        ]

        result = await service.auto_provision_system_queues("proj-1")

        assert len(result) == 4
        mock_queue_repo.create.assert_has_calls([
            call(
                project_id="proj-1",
                queue_name="system_fifo_queue",
                queue_type="fifo",
                concurrency_limit=1,
                is_system=True,
            ),
            call(
                project_id="proj-1",
                queue_name="system_parallel_queue",
                queue_type="parallel",
                concurrency_limit=5,
                is_system=True,
            ),
            call(
                project_id="proj-1",
                queue_name="system_kb_fifo_queue",
                queue_type="fifo",
                concurrency_limit=1,
                is_system=True,
                description="System FIFO queue for Knowledge Base import jobs",
            ),
            call(
                project_id="proj-1",
                queue_name="system_defer_queue",
                queue_type="defer",
                concurrency_limit=1,
                is_system=True,
                description="System defer queue - only processes when project is idle",
            ),
        ])

    @pytest.mark.asyncio
    async def test_auto_provision_sets_correct_ids(self, service, mock_queue_repo):
        """System queues have expected queue names."""
        mock_queue_repo.get_by_name.return_value = None
        created = [
            make_queue(queue_name="system_fifo_queue", is_system=True),
            make_queue(queue_name="system_parallel_queue", is_system=True),
            make_queue(queue_name="system_kb_fifo_queue", is_system=True),
            make_queue(queue_name="system_defer_queue", is_system=True),
        ]
        mock_queue_repo.create.side_effect = created

        result = await service.auto_provision_system_queues("proj-1")

        assert result[0].queue_name == "system_fifo_queue"
        assert result[1].queue_name == "system_parallel_queue"
        assert result[2].queue_name == "system_kb_fifo_queue"
        assert result[3].queue_name == "system_defer_queue"

    @pytest.mark.asyncio
    async def test_auto_provision_sets_queue_type(self, service, mock_queue_repo):
        """Queues have expected types: FIFO, parallel, and KB FIFO."""
        mock_queue_repo.get_by_name.return_value = None
        created = [
            make_queue(queue_name="system_fifo_queue", queue_type="fifo", is_system=True),
            make_queue(queue_name="system_parallel_queue", queue_type="parallel", is_system=True),
            make_queue(queue_name="system_kb_fifo_queue", queue_type="fifo", is_system=True),
            make_queue(queue_name="system_defer_queue", queue_type="defer", is_system=True),
        ]
        mock_queue_repo.create.side_effect = created

        result = await service.auto_provision_system_queues("proj-1")

        assert result[0].queue_type == "fifo"
        assert result[1].queue_type == "parallel"
        assert result[2].queue_type == "fifo"
        assert result[3].queue_type == "defer"

    @pytest.mark.asyncio
    async def test_auto_provision_sets_system_flag(self, service, mock_queue_repo):
        """All created queues are marked as system."""
        mock_queue_repo.get_by_name.return_value = None
        created = [
            make_queue(queue_name="system_fifo_queue", is_system=True),
            make_queue(queue_name="system_parallel_queue", is_system=True),
            make_queue(queue_name="system_kb_fifo_queue", is_system=True),
            make_queue(queue_name="system_defer_queue", is_system=True),
        ]
        mock_queue_repo.create.side_effect = created

        result = await service.auto_provision_system_queues("proj-1")

        assert result[0].is_system is True
        assert result[1].is_system is True
        assert result[2].is_system is True
        assert result[3].is_system is True

    @pytest.mark.asyncio
    async def test_auto_provision_idempotent(self, service, mock_queue_repo):
        """Calling twice does not create duplicates."""
        # All queues already exist
        existing_fifo = make_queue(queue_name="system_fifo_queue", is_system=True)
        existing_para = make_queue(queue_name="system_parallel_queue", is_system=True)
        existing_kb_fifo = make_queue(queue_name="system_kb_fifo_queue", is_system=True)
        existing_defer = make_queue(queue_name="system_defer_queue", is_system=True)
        mock_queue_repo.get_by_name.side_effect = [existing_fifo, existing_para, existing_kb_fifo, existing_defer]

        result = await service.auto_provision_system_queues("proj-1")

        assert len(result) == 4
        mock_queue_repo.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_auto_provision_sets_default_concurrency(self, service, mock_queue_repo):
        """System queues have expected concurrency limits."""
        mock_queue_repo.get_by_name.return_value = None
        created = [
            make_queue(queue_name="system_fifo_queue", concurrency_limit=1, is_system=True),
            make_queue(queue_name="system_parallel_queue", concurrency_limit=5, is_system=True),
            make_queue(queue_name="system_kb_fifo_queue", concurrency_limit=1, is_system=True),
            make_queue(queue_name="system_defer_queue", concurrency_limit=1, is_system=True),
        ]
        mock_queue_repo.create.side_effect = created

        result = await service.auto_provision_system_queues("proj-1")

        assert result[0].concurrency_limit == 1
        assert result[1].concurrency_limit == 5
        assert result[2].concurrency_limit == 1
        assert result[3].concurrency_limit == 1


# ---------------------------------------------------------------------------
# TestCreateCustomQueue
# ---------------------------------------------------------------------------

class TestCreateCustomQueue:
    """Tests for create_queue()."""

    @pytest.mark.asyncio
    async def test_create_custom_queue_success(self, service, mock_queue_repo):
        """Creates custom queue with valid data."""
        mock_queue_repo.get_by_name.return_value = None
        created = make_queue(queue_id="q-new", queue_name="my-queue", is_system=False)
        mock_queue_repo.create.return_value = created

        result = await service.create_queue(
            project_id="proj-1",
            queue_name="my-queue",
            queue_type="parallel",
            concurrency_limit=3,
        )

        assert result.queue_name == "my-queue"
        mock_queue_repo.create.assert_called_once_with(
            project_id="proj-1",
            queue_name="my-queue",
            queue_type="parallel",
            concurrency_limit=3,
            is_system=False,
            description=None,
        )

    @pytest.mark.asyncio
    async def test_create_custom_queue_sets_type(self, service, mock_queue_repo):
        """Created queue has the requested type."""
        mock_queue_repo.get_by_name.return_value = None
        created = make_queue(queue_name="fifo-queue", queue_type="fifo", is_system=False)
        mock_queue_repo.create.return_value = created

        result = await service.create_queue(project_id="proj-1", queue_name="fifo-queue", queue_type="fifo")

        assert result.queue_type == "fifo"

    @pytest.mark.asyncio
    async def test_create_custom_queue_rejects_reserved_name(self, service, mock_queue_repo):
        """Rejects queue names that conflict with system queue names."""
        with pytest.raises(ValueError, match="reserved"):
            await service.create_queue(
                project_id="proj-1",
                queue_name="system_fifo_queue",
            )

    @pytest.mark.asyncio
    async def test_create_custom_queue_rejects_reserved_name_case_insensitive(self, service, mock_queue_repo):
        """Reserved name check is case-insensitive."""
        with pytest.raises(ValueError, match="reserved"):
            await service.create_queue(
                project_id="proj-1",
                queue_name="SYSTEM_FIFO_QUEUE",
            )

    @pytest.mark.asyncio
    async def test_reserved_name_system_kb_fifo_queue(self, service, mock_queue_repo):
        """Rejects queue name system_kb_fifo_queue as reserved."""
        with pytest.raises(ValueError, match="reserved"):
            await service.create_queue(
                project_id="proj-1",
                queue_name="system_kb_fifo_queue",
            )

    @pytest.mark.asyncio
    async def test_reserved_name_case_insensitive_system_kb_fifo_queue(self, service, mock_queue_repo):
        """Reserved name check is case-insensitive for system_kb_fifo_queue."""
        with pytest.raises(ValueError, match="reserved"):
            await service.create_queue(
                project_id="proj-1",
                queue_name="SYSTEM_KB_FIFO_QUEUE",
            )

    @pytest.mark.asyncio
    async def test_create_custom_queue_rejects_duplicate_name(self, service, mock_queue_repo):
        """Cannot create two queues with same name in same project."""
        mock_queue_repo.get_by_name.return_value = make_queue(queue_name="existing")

        with pytest.raises(ValueError, match="already exists"):
            await service.create_queue(project_id="proj-1", queue_name="existing")

    @pytest.mark.asyncio
    async def test_create_custom_queue_sets_defaults(self, service, mock_queue_repo):
        """Default values are applied when not specified."""
        mock_queue_repo.get_by_name.return_value = None
        created = make_queue(queue_name="defaults-queue", is_system=False)
        mock_queue_repo.create.return_value = created

        await service.create_queue(project_id="proj-1", queue_name="defaults-queue")

        call_kwargs = mock_queue_repo.create.call_args.kwargs
        assert call_kwargs["is_system"] is False

    @pytest.mark.asyncio
    async def test_create_custom_queue_different_projects_same_name(self, service, mock_queue_repo):
        """Same name is allowed in different projects."""
        mock_queue_repo.get_by_name.return_value = None
        created = make_queue(queue_id="q-2", queue_name="shared-name", is_system=False)
        mock_queue_repo.create.return_value = created

        # First call: get_by_name returns None (no conflict), create succeeds
        result = await service.create_queue(project_id="proj-2", queue_name="shared-name")

        assert result.queue_name == "shared-name"

    @pytest.mark.asyncio
    async def test_create_custom_queue_rejects_fifo_with_concurrency_greater_than_one(self, service, mock_queue_repo):
        """FIFO queues must have concurrency_limit=1."""
        with pytest.raises(ValueError, match="concurrency_limit=1"):
            await service.create_queue(
                project_id="proj-1",
                queue_name="my-fifo",
                queue_type="fifo",
                concurrency_limit=3,
            )


# ---------------------------------------------------------------------------
# TestGetQueue
# ---------------------------------------------------------------------------

class TestGetQueue:
    """Tests for get_queue()."""

    @pytest.mark.asyncio
    async def test_get_existing_queue(self, service, mock_queue_repo):
        """Returns queue when found and owned by project."""
        queue = make_queue(queue_id="q-001", project_id="proj-1")
        mock_queue_repo.get.return_value = queue

        result = await service.get_queue("proj-1", "q-001")

        assert result is queue

    @pytest.mark.asyncio
    async def test_get_nonexistent_queue(self, service, mock_queue_repo):
        """Returns None when queue does not exist."""
        mock_queue_repo.get.return_value = None

        result = await service.get_queue("proj-1", "q-nonexistent")

        assert result is None

    @pytest.mark.asyncio
    async def test_get_queue_idor_wrong_project(self, service, mock_queue_repo):
        """Accessing queue from wrong project returns None (IDOR protection)."""
        queue = make_queue(queue_id="q-001", project_id="proj-other")
        mock_queue_repo.get.return_value = queue

        result = await service.get_queue("proj-1", "q-001")

        assert result is None


# ---------------------------------------------------------------------------
# TestListQueues
# ---------------------------------------------------------------------------

class TestListQueues:
    """Tests for list_queues()."""

    @pytest.mark.asyncio
    async def test_list_queues_includes_custom_and_system(self, service, mock_queue_repo):
        """Returns all queues for project (custom + system)."""
        system_queue = make_queue(queue_name="system_fifo_queue", is_system=True)
        custom_queue = make_queue(queue_id="q-custom", queue_name="my-queue", is_system=False)
        mock_queue_repo.list_by_project.return_value = [system_queue, custom_queue]
        mock_queue_repo.count_jobs_by_status.return_value = {"pending": 0, "processing": 0}

        result = await service.list_queues("proj-1")

        assert len(result) == 2
        names = {r["queue_name"] for r in result}
        assert "system_fifo_queue" in names
        assert "my-queue" in names

    @pytest.mark.asyncio
    async def test_list_queues_excludes_other_projects(self, service, mock_queue_repo):
        """list_by_project only returns queues for the given project."""
        mock_queue_repo.list_by_project.return_value = []

        await service.list_queues("proj-1")

        mock_queue_repo.list_by_project.assert_called_once_with("proj-1")

    @pytest.mark.asyncio
    async def test_list_queues_includes_job_counts(self, service, mock_queue_repo):
        """Response includes active and pending job counts."""
        queue = make_queue(queue_name="my-queue")
        mock_queue_repo.list_by_project.return_value = [queue]
        mock_queue_repo.count_jobs_by_status.return_value = {"pending": 5, "processing": 2}

        result = await service.list_queues("proj-1")

        assert result[0]["pending_jobs"] == 5
        assert result[0]["active_jobs"] == 2

    @pytest.mark.asyncio
    async def test_list_queues_empty_project(self, service, mock_queue_repo):
        """Returns empty list for project with no queues."""
        mock_queue_repo.list_by_project.return_value = []

        result = await service.list_queues("proj-new")

        assert result == []


# ---------------------------------------------------------------------------
# TestUpdateQueue
# ---------------------------------------------------------------------------

class TestUpdateQueue:
    """Tests for update_queue()."""

    @pytest.mark.asyncio
    async def test_update_queue_name(self, service, mock_queue_repo):
        """Updates queue name successfully."""
        queue = make_queue(queue_id="q-001", project_id="proj-1", queue_name="old-name")
        mock_queue_repo.get.return_value = queue
        mock_queue_repo.get_by_name.return_value = None
        updated = make_queue(queue_id="q-001", project_id="proj-1", queue_name="new-name")
        mock_queue_repo.update.return_value = updated

        result = await service.update_queue("proj-1", "q-001", queue_name="new-name")

        assert result.queue_name == "new-name"

    @pytest.mark.asyncio
    async def test_update_queue_concurrency(self, service, mock_queue_repo):
        """Updates concurrency_limit successfully."""
        queue = make_queue(queue_id="q-001", project_id="proj-1", queue_type="parallel", concurrency_limit=1)
        mock_queue_repo.get.return_value = queue
        updated = make_queue(queue_id="q-001", project_id="proj-1", queue_type="parallel", concurrency_limit=5)
        mock_queue_repo.update.return_value = updated

        result = await service.update_queue("proj-1", "q-001", concurrency_limit=5)

        assert result.concurrency_limit == 5

    @pytest.mark.asyncio
    async def test_update_queue_nonexistent(self, service, mock_queue_repo):
        """Returns None for non-existent queue."""
        mock_queue_repo.get.return_value = None

        result = await service.update_queue("proj-1", "q-nonexistent", concurrency_limit=5)

        assert result is None

    @pytest.mark.asyncio
    async def test_update_queue_idor(self, service, mock_queue_repo):
        """Updating queue from wrong project returns None."""
        queue = make_queue(queue_id="q-001", project_id="proj-other")
        mock_queue_repo.get.return_value = queue

        result = await service.update_queue("proj-1", "q-001", concurrency_limit=5)

        assert result is None

    @pytest.mark.asyncio
    async def test_update_queue_rejects_reserved_name(self, service, mock_queue_repo):
        """Cannot rename to a reserved system queue name."""
        queue = make_queue(queue_id="q-001", project_id="proj-1", queue_name="custom")
        mock_queue_repo.get.return_value = queue

        with pytest.raises(ValueError, match="reserved"):
            await service.update_queue("proj-1", "q-001", queue_name="system_fifo_queue")

    @pytest.mark.asyncio
    async def test_rename_to_reserved_system_kb_fifo_queue(self, service, mock_queue_repo):
        """Cannot rename queue to reserved system_kb_fifo_queue."""
        queue = make_queue(queue_id="q-001", project_id="proj-1", queue_name="custom")
        mock_queue_repo.get.return_value = queue

        with pytest.raises(ValueError, match="reserved"):
            await service.update_queue("proj-1", "q-001", queue_name="system_kb_fifo_queue")

    @pytest.mark.asyncio
    async def test_update_queue_rejects_fifo_concurrency_change(self, service, mock_queue_repo):
        """Cannot change FIFO queue concurrency to > 1."""
        queue = make_queue(queue_id="q-001", project_id="proj-1", queue_type="fifo", concurrency_limit=1)
        mock_queue_repo.get.return_value = queue

        with pytest.raises(ValueError, match="concurrency_limit=1"):
            await service.update_queue("proj-1", "q-001", concurrency_limit=5)


# ---------------------------------------------------------------------------
# TestDeleteCustomQueue
# ---------------------------------------------------------------------------

class TestDeleteCustomQueue:
    """Tests for delete_queue()."""

    @pytest.mark.asyncio
    async def test_delete_custom_queue_success(self, service, mock_queue_repo):
        """Deletes custom queue successfully."""
        queue = make_queue(queue_id="q-001", project_id="proj-1", is_system=False)
        system_fifo = make_queue(queue_name="system_fifo_queue", is_system=True)
        mock_queue_repo.get.return_value = queue
        mock_queue_repo.count_jobs_by_status.return_value = {"pending": 0, "processing": 0}
        mock_queue_repo.get_by_name.return_value = system_fifo
        mock_queue_repo.reassign_pending_jobs_atomic.return_value = 0

        result = await service.delete_queue("proj-1", "q-001")

        assert result["deleted"] is True
        assert result["queue_id"] == "q-001"
        mock_queue_repo.delete.assert_called_once_with("q-001")

    @pytest.mark.asyncio
    async def test_delete_custom_queue_reassigns_pending(self, service, mock_queue_repo):
        """PENDING jobs are reassigned to system FIFO queue before deletion."""
        queue = make_queue(queue_id="q-001", project_id="proj-1", is_system=False)
        system_fifo = MagicMock()
        system_fifo.queue_id = "sys-fifo"
        system_fifo.queue_name = "system_fifo_queue"
        system_fifo.is_system = True
        mock_queue_repo.get.side_effect = [queue, system_fifo]
        mock_queue_repo.get_by_name.return_value = system_fifo
        mock_queue_repo.count_jobs_by_status.return_value = {"pending": 3, "processing": 0}
        mock_queue_repo.reassign_pending_jobs_atomic.return_value = 3

        result = await service.delete_queue("proj-1", "q-001")

        assert result["reassigned_jobs"] == 3
        mock_queue_repo.reassign_pending_jobs_atomic.assert_called_once_with(
            "q-001", "sys-fifo", [JobStatus.PENDING.value]
        )

    @pytest.mark.asyncio
    async def test_delete_rejects_system_queue(self, service, mock_queue_repo):
        """Returns 403 error when trying to delete system queue."""
        queue = make_queue(queue_id="sys-fifo", project_id="proj-1", is_system=True)
        mock_queue_repo.get.return_value = queue

        with pytest.raises(ValueError, match="system queue"):
            await service.delete_queue("proj-1", "sys-fifo")

    @pytest.mark.asyncio
    async def test_delete_rejects_with_processing_jobs(self, service, mock_queue_repo):
        """Returns 409 error when queue has PROCESSING jobs."""
        queue = make_queue(queue_id="q-001", project_id="proj-1", is_system=False)
        mock_queue_repo.get.return_value = queue
        mock_queue_repo.count_jobs_by_status.return_value = {"pending": 0, "processing": 1}

        with pytest.raises(ValueError, match="processing jobs"):
            await service.delete_queue("proj-1", "q-001")

    @pytest.mark.asyncio
    async def test_delete_queue_idor(self, service, mock_queue_repo):
        """Deleting queue from wrong project raises not-found error."""
        queue = make_queue(queue_id="q-001", project_id="proj-other")
        mock_queue_repo.get.return_value = queue

        with pytest.raises(ValueError, match="not found"):
            await service.delete_queue("proj-1", "q-001")


# ---------------------------------------------------------------------------
# TestStartQueue
# ---------------------------------------------------------------------------

class TestStartQueue:
    """Tests for start_queue()."""

    @pytest.mark.asyncio
    async def test_start_queue_unpauses(self, service, mock_queue_repo):
        """Sets is_paused=False."""
        queue = make_queue(queue_id="q-001", project_id="proj-1", is_paused=True)
        mock_queue_repo.get.return_value = queue
        updated = make_queue(queue_id="q-001", project_id="proj-1", is_paused=False)
        mock_queue_repo.update.return_value = updated

        result = await service.start_queue("proj-1", "q-001")

        assert result.is_paused is False

    @pytest.mark.asyncio
    async def test_start_queue_already_running(self, service, mock_queue_repo):
        """No-op if already running (is_paused already False)."""
        queue = make_queue(queue_id="q-001", project_id="proj-1", is_paused=False)
        mock_queue_repo.get.return_value = queue
        # update_queue will be called but is_paused is already False
        updated = make_queue(queue_id="q-001", project_id="proj-1", is_paused=False)
        mock_queue_repo.update.return_value = updated

        result = await service.start_queue("proj-1", "q-001")

        assert result is not None

    @pytest.mark.asyncio
    async def test_start_queue_nonexistent(self, service, mock_queue_repo):
        """Returns None for non-existent queue."""
        mock_queue_repo.get.return_value = None

        result = await service.start_queue("proj-1", "q-nonexistent")

        assert result is None


# ---------------------------------------------------------------------------
# TestStopQueue
# ---------------------------------------------------------------------------

class TestStopQueue:
    """Tests for stop_queue()."""

    @pytest.mark.asyncio
    async def test_stop_queue_pauses(self, service, mock_queue_repo):
        """Sets is_paused=True."""
        queue = make_queue(queue_id="q-001", project_id="proj-1", is_paused=False)
        mock_queue_repo.get.return_value = queue
        updated = make_queue(queue_id="q-001", project_id="proj-1", is_paused=True)
        mock_queue_repo.update.return_value = updated

        result = await service.stop_queue("proj-1", "q-001")

        assert result.is_paused is True

    @pytest.mark.asyncio
    async def test_stop_queue_already_paused(self, service, mock_queue_repo):
        """No-op if already paused."""
        queue = make_queue(queue_id="q-001", project_id="proj-1", is_paused=True)
        mock_queue_repo.get.return_value = queue
        updated = make_queue(queue_id="q-001", project_id="proj-1", is_paused=True)
        mock_queue_repo.update.return_value = updated

        result = await service.stop_queue("proj-1", "q-001")

        assert result is not None

    @pytest.mark.asyncio
    async def test_stop_queue_nonexistent(self, service, mock_queue_repo):
        """Returns None for non-existent queue."""
        mock_queue_repo.get.return_value = None

        result = await service.stop_queue("proj-1", "q-nonexistent")

        assert result is None
