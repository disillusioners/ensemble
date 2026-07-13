"""Tests for ensure_system_queues endpoint and service method.

Tests cover:
- Service layer: ensure_system_queues() method
- API layer: POST /projects/{project_id}/queues/ensure-system endpoint
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from daemon.services.job_queue_mgmt_service import (
    JobQueueMgmtService,
    RESERVED_QUEUE_NAMES,
)
from daemon.repositories.job_queue.queue_repository import JobQueueRepository
from daemon.repositories.job_queue.repository import JobRepository
from daemon.repositories.job_queue.models import JobQueue
from daemon.routers.queues import (
    router,
    set_job_queue_mgmt_service,
    set_project_repository,
)


# =============================================================================
# Helper: build a mock JobQueue
# =============================================================================

def make_queue(
    queue_id: str = "q-001",
    project_id: str = "proj-1",
    queue_name: str = "test-queue",
    queue_type: str = "fifo",
    concurrency_limit: int = 1,
    is_system: bool = False,
    is_paused: bool = False,
    description: str | None = None,
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
    queue.description = description
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
        "description": description,
        "created_at": queue.created_at,
        "updated_at": queue.updated_at,
    }
    return queue


# =============================================================================
# Fixtures - Service Layer Tests
# =============================================================================

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


# =============================================================================
# Fixtures - API Layer Tests
# =============================================================================

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
def queue_repository(engine):
    """Create JobQueueRepository (empty - no system queues pre-provisioned)."""
    return JobQueueRepository(engine)


@pytest.fixture
def job_repository(engine):
    """Create JobRepository."""
    return JobRepository(engine)


@pytest.fixture
def mock_project_repo():
    """Mock project repository for testing project existence check."""
    return MagicMock()


@pytest.fixture
def job_queue_service(queue_repository, job_repository):
    """Create JobQueueMgmtService with real repositories."""
    return JobQueueMgmtService(
        queue_repo=queue_repository,
        job_repo=job_repository,
    )


@pytest.fixture
def test_app(job_queue_service, mock_project_repo):
    """Create FastAPI test app with queue router."""
    app = FastAPI()
    app.include_router(router)
    set_job_queue_mgmt_service(job_queue_service)
    set_project_repository(mock_project_repo)
    yield app


@pytest.fixture
def client(test_app):
    """Create TestClient for API testing."""
    with TestClient(test_app) as client:
        yield client


# =============================================================================
# TestEnsureSystemQueuesService - Happy Path
# =============================================================================

class TestEnsureSystemQueuesService:
    """Tests for ensure_system_queues() service method."""

    @pytest.mark.asyncio
    async def test_ensure_system_queues_partial_existing(self, service, mock_queue_repo):
        """Some queues exist, some don't → correct tracking of existing vs created.

        Scenario: project has system_fifo_queue and system_parallel_queue,
        but is missing system_kb_fifo_queue, system_defer_queue, and
        system_background_queue.
        """
        # Setup: two queues already exist
        existing_fifo = make_queue(
            queue_id="sys-fifo", queue_name="system_fifo_queue", is_system=True
        )
        existing_parallel = make_queue(
            queue_id="sys-para", queue_name="system_parallel_queue", is_system=True
        )

        # auto_provision creates the missing queues
        created_kb_fifo = make_queue(
            queue_id="sys-kb", queue_name="system_kb_fifo_queue", is_system=True
        )
        created_defer = make_queue(
            queue_id="sys-defer", queue_name="system_defer_queue", is_system=True
        )
        created_background = make_queue(
            queue_id="sys-bg", queue_name="system_background_queue", is_system=True
        )

        # Sequence of calls expected:
        # 1. list_by_project (before) -> [existing_fifo, existing_parallel]
        # 2. get_by_name("system_fifo_queue") -> existing_fifo
        # 3. get_by_name("system_parallel_queue") -> existing_parallel
        # 4. get_by_name("system_kb_fifo_queue") -> None (needs creation)
        # 5. create(system_kb_fifo_queue) -> created_kb_fifo
        # 6. get_by_name("system_defer_queue") -> None (needs creation)
        # 7. create(system_defer_queue) -> created_defer
        # 8. get_by_name("system_background_queue") -> None (needs creation)
        # 9. create(system_background_queue) -> created_background
        # 10. list_by_project (after) -> all 5 queues

        call_count = [0]

        def list_by_project_side_effect(project_id):
            call_count[0] += 1
            if call_count[0] == 1:
                # First call: before provisioning
                return [existing_fifo, existing_parallel]
            else:
                # After provisioning: all 5 exist
                return [
                    existing_fifo,
                    existing_parallel,
                    created_kb_fifo,
                    created_defer,
                    created_background,
                ]

        def get_by_name_side_effect(project_id, queue_name):
            if queue_name == "system_fifo_queue":
                return existing_fifo
            if queue_name == "system_parallel_queue":
                return existing_parallel
            return None  # Missing queues

        mock_queue_repo.list_by_project.side_effect = list_by_project_side_effect
        mock_queue_repo.get_by_name.side_effect = get_by_name_side_effect
        mock_queue_repo.create.side_effect = [
            created_kb_fifo,
            created_defer,
            created_background,
        ]

        result = await service.ensure_system_queues("proj-1")

        assert "existing_queues" in result
        assert "created_queues" in result
        assert "total_system_queues" in result
        assert set(result["existing_queues"]) == {"system_fifo_queue", "system_parallel_queue"}
        assert set(result["created_queues"]) == {
            "system_kb_fifo_queue",
            "system_defer_queue",
            "system_background_queue",
        }
        assert result["total_system_queues"] == 5

    @pytest.mark.asyncio
    async def test_ensure_system_queues_all_exist(self, service, mock_queue_repo):
        """All 5 queues already exist → all in existing_queues, created empty."""
        # Setup: all 5 system queues already exist
        existing_fifo = make_queue(
            queue_name="system_fifo_queue", is_system=True
        )
        existing_parallel = make_queue(
            queue_name="system_parallel_queue", is_system=True
        )
        existing_kb_fifo = make_queue(
            queue_name="system_kb_fifo_queue", is_system=True
        )
        existing_defer = make_queue(
            queue_name="system_defer_queue", is_system=True
        )
        existing_background = make_queue(
            queue_name="system_background_queue", is_system=True
        )

        all_queues = [
            existing_fifo,
            existing_parallel,
            existing_kb_fifo,
            existing_defer,
            existing_background,
        ]
        # Before provisioning: all 5 exist
        mock_queue_repo.list_by_project.return_value = all_queues

        # get_by_name returns existing for all queues (no creation needed)
        def get_by_name_side_effect(project_id, queue_name):
            for q in all_queues:
                if q.queue_name == queue_name:
                    return q
            return None

        mock_queue_repo.get_by_name.side_effect = get_by_name_side_effect

        # After provisioning: still all 5 exist
        mock_queue_repo.list_by_project.return_value = all_queues

        result = await service.ensure_system_queues("proj-1")

        assert set(result["existing_queues"]) == RESERVED_QUEUE_NAMES
        assert result["created_queues"] == []
        assert result["total_system_queues"] == 5

    @pytest.mark.asyncio
    async def test_ensure_system_queues_none_exist(self, service, mock_queue_repo):
        """No queues exist → all 5 created, existing empty."""
        # Setup: all 5 queues to be created
        created_fifo = make_queue(
            queue_id="new-1", queue_name="system_fifo_queue", is_system=True
        )
        created_parallel = make_queue(
            queue_id="new-2", queue_name="system_parallel_queue", is_system=True
        )
        created_kb_fifo = make_queue(
            queue_id="new-3", queue_name="system_kb_fifo_queue", is_system=True
        )
        created_defer = make_queue(
            queue_id="new-4", queue_name="system_defer_queue", is_system=True
        )
        created_background = make_queue(
            queue_id="new-5", queue_name="system_background_queue", is_system=True
        )

        # Sequence of calls expected:
        # 1. list_by_project (before) -> [] (no queues exist)
        # 2. get_by_name for each queue -> None (needs creation)
        # 3. create for each queue -> created queue
        # 4. list_by_project (after) -> all 5 queues

        call_count = [0]

        def list_by_project_side_effect(project_id):
            call_count[0] += 1
            if call_count[0] == 1:
                # First call: before provisioning - no queues
                return []
            else:
                # After provisioning: all 5 created
                return [
                    created_fifo,
                    created_parallel,
                    created_kb_fifo,
                    created_defer,
                    created_background,
                ]

        mock_queue_repo.list_by_project.side_effect = list_by_project_side_effect
        mock_queue_repo.get_by_name.return_value = None  # All need to be created
        mock_queue_repo.create.side_effect = [
            created_fifo,
            created_parallel,
            created_kb_fifo,
            created_defer,
            created_background,
        ]

        result = await service.ensure_system_queues("proj-1")

        assert result["existing_queues"] == []
        assert set(result["created_queues"]) == RESERVED_QUEUE_NAMES
        assert result["total_system_queues"] == 5

    @pytest.mark.asyncio
    async def test_ensure_system_queues_idempotent(self, service, mock_queue_repo):
        """Calling twice, second time all should be in existing_queues."""
        # Setup: all 5 queues
        created_fifo = make_queue(
            queue_id="new-1", queue_name="system_fifo_queue", is_system=True
        )
        created_parallel = make_queue(
            queue_id="new-2", queue_name="system_parallel_queue", is_system=True
        )
        created_kb_fifo = make_queue(
            queue_id="new-3", queue_name="system_kb_fifo_queue", is_system=True
        )
        created_defer = make_queue(
            queue_id="new-4", queue_name="system_defer_queue", is_system=True
        )
        created_background = make_queue(
            queue_id="new-5", queue_name="system_background_queue", is_system=True
        )
        all_queues = [
            created_fifo,
            created_parallel,
            created_kb_fifo,
            created_defer,
            created_background,
        ]

        def get_by_name_side_effect(project_id, queue_name):
            for q in all_queues:
                if q.queue_name == queue_name:
                    return q
            return None

        mock_queue_repo.get_by_name.side_effect = get_by_name_side_effect
        mock_queue_repo.create.side_effect = list(all_queues)

        # Track calls across both invocations
        # Call 1: before=[], after=all_queues (queues created)
        # Call 2: before=all_queues, after=all_queues (no new queues)
        list_call_count = [0]

        def list_by_project_side_effect(project_id):
            list_call_count[0] += 1
            if list_call_count[0] <= 2:
                # First ensure_system_queues call: before=[], after=all_queues
                return [] if list_call_count[0] == 1 else all_queues
            else:
                # Second ensure_system_queues call: both before and after return all_queues
                return all_queues

        mock_queue_repo.list_by_project.side_effect = list_by_project_side_effect

        result1 = await service.ensure_system_queues("proj-1")
        assert result1["existing_queues"] == []
        assert set(result1["created_queues"]) == RESERVED_QUEUE_NAMES

        # Second call: queues already exist
        result2 = await service.ensure_system_queues("proj-1")

        assert set(result2["existing_queues"]) == RESERVED_QUEUE_NAMES
        assert result2["created_queues"] == []
        assert result2["total_system_queues"] == 5


# =============================================================================
# TestEnsureSystemQueuesAPI - Endpoint Tests
# =============================================================================

class TestEnsureSystemQueuesAPI:
    """Tests for POST /projects/{project_id}/queues/ensure-system endpoint."""

    def test_ensure_system_queues_200_ok(self, client, mock_project_repo):
        """Valid project returns 200 with correct response structure."""
        # Setup: project exists
        mock_project = MagicMock()
        mock_project.project_id = "test-project"
        mock_project_repo.get.return_value = mock_project

        response = client.post("/projects/test-project/queues/ensure-system")

        assert response.status_code == 200
        data = response.json()
        assert "project_id" in data
        assert "existing_queues" in data
        assert "created_queues" in data
        assert "total_system_queues" in data
        assert data["project_id"] == "test-project"
        assert isinstance(data["existing_queues"], list)
        assert isinstance(data["created_queues"], list)
        assert data["total_system_queues"] == 5  # All 5 system queues created

    def test_ensure_system_queues_404_non_existent_project(self, client, mock_project_repo):
        """Non-existent project returns 404."""
        # Setup: project does not exist
        mock_project_repo.get.return_value = None

        response = client.post("/projects/nonexistent-project/queues/ensure-system")

        assert response.status_code == 404
        data = response.json()
        assert "detail" in data
        assert "not found" in data["detail"]["error"].lower()

    def test_ensure_system_queues_correct_queue_properties(
        self, client, mock_project_repo, queue_repository
    ):
        """After ensure, each queue has correct name, type, and concurrency."""
        # Setup: project exists
        mock_project = MagicMock()
        mock_project.project_id = "test-project"
        mock_project_repo.get.return_value = mock_project

        # Call ensure-system
        response = client.post("/projects/test-project/queues/ensure-system")
        assert response.status_code == 200

        # Verify queue properties directly in repository
        fifo = queue_repository.get_by_name("test-project", "system_fifo_queue")
        parallel = queue_repository.get_by_name("test-project", "system_parallel_queue")
        kb_fifo = queue_repository.get_by_name("test-project", "system_kb_fifo_queue")
        defer = queue_repository.get_by_name("test-project", "system_defer_queue")
        background = queue_repository.get_by_name("test-project", "system_background_queue")

        # system_fifo_queue
        assert fifo is not None
        assert fifo.queue_name == "system_fifo_queue"
        assert fifo.queue_type == "fifo"
        assert fifo.concurrency_limit == 1
        assert fifo.is_system is True

        # system_parallel_queue
        assert parallel is not None
        assert parallel.queue_name == "system_parallel_queue"
        assert parallel.queue_type == "parallel"
        assert parallel.concurrency_limit == 5
        assert parallel.is_system is True

        # system_kb_fifo_queue
        assert kb_fifo is not None
        assert kb_fifo.queue_name == "system_kb_fifo_queue"
        assert kb_fifo.queue_type == "fifo"
        assert kb_fifo.concurrency_limit == 1
        assert kb_fifo.is_system is True

        # system_defer_queue
        assert defer is not None
        assert defer.queue_name == "system_defer_queue"
        assert defer.queue_type == "defer"
        assert defer.concurrency_limit == 1
        assert defer.is_system is True

        # system_background_queue
        assert background is not None
        assert background.queue_name == "system_background_queue"
        assert background.queue_type == "background"
        assert background.concurrency_limit == 1
        assert background.is_system is True

    def test_ensure_system_queues_idempotent_api(self, client, mock_project_repo):
        """Calling endpoint twice returns existing queues on second call."""
        # Setup: project exists
        mock_project = MagicMock()
        mock_project.project_id = "test-project"
        mock_project_repo.get.return_value = mock_project

        # First call
        response1 = client.post("/projects/test-project/queues/ensure-system")
        assert response1.status_code == 200
        data1 = response1.json()
        assert set(data1["created_queues"]) == {
            "system_fifo_queue",
            "system_parallel_queue",
            "system_kb_fifo_queue",
            "system_defer_queue",
            "system_background_queue",
        }
        assert data1["existing_queues"] == []
        assert data1["total_system_queues"] == 5

        # Second call
        response2 = client.post("/projects/test-project/queues/ensure-system")
        assert response2.status_code == 200
        data2 = response2.json()
        assert set(data2["existing_queues"]) == {
            "system_fifo_queue",
            "system_parallel_queue",
            "system_kb_fifo_queue",
            "system_defer_queue",
            "system_background_queue",
        }
        assert data2["created_queues"] == []
        assert data2["total_system_queues"] == 5

    def test_ensure_system_queues_partial_existing_api(self, client, mock_project_repo, queue_repository):
        """Pre-create some queues, then call ensure - correctly tracks existing vs created."""
        # Setup: project exists and some queues already exist
        mock_project = MagicMock()
        mock_project.project_id = "test-project"
        mock_project_repo.get.return_value = mock_project

        # Pre-create only 2 queues
        queue_repository.create(
            project_id="test-project",
            queue_name="system_fifo_queue",
            queue_type="fifo",
            concurrency_limit=1,
            is_system=True,
        )
        queue_repository.create(
            project_id="test-project",
            queue_name="system_parallel_queue",
            queue_type="parallel",
            concurrency_limit=5,
            is_system=True,
        )

        # Call ensure-system
        response = client.post("/projects/test-project/queues/ensure-system")
        assert response.status_code == 200
        data = response.json()

        # Verify correct tracking
        assert set(data["existing_queues"]) == {"system_fifo_queue", "system_parallel_queue"}
        assert set(data["created_queues"]) == {
            "system_kb_fifo_queue",
            "system_defer_queue",
            "system_background_queue",
        }
        assert data["total_system_queues"] == 5
