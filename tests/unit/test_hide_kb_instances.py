"""Tests for hide KB instances feature - repository filtering, API routing, SSE agent_id propagation."""

import pytest
from datetime import datetime, timezone
from sqlmodel import SQLModel, create_engine

from daemon.repositories.instance.repository import SQLModelInstanceRepository, KB_AGENT_IDS
from daemon.repositories.instance.models import Instance


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def repo():
    """In-memory SQLite repository with real schema."""
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    return SQLModelInstanceRepository(engine)


def _make_instance(
    repo: SQLModelInstanceRepository,
    agent_id: str,
    status: str = "running",
    project_id: str = "proj-1",
) -> Instance:
    """Helper to create an instance in the repository."""
    return repo.create(
        instance_id=f"inst-{agent_id}-{datetime.now(timezone.utc).timestamp()}",
        agent_id=agent_id,
        agent_dir=f"./agents/{agent_id}",
        status=status,
        project_id=project_id,
    )


# =============================================================================
# 2A: REPOSITORY-LEVEL TESTS
# =============================================================================

class TestKBAgentIdsConstant:
    """Tests for KB_AGENT_IDS constant."""

    def test_kb_agent_ids_constant(self):
        """Verify KB_AGENT_IDS is a frozenset containing experiencer and kb-importer."""
        assert KB_AGENT_IDS == frozenset(["experiencer", "kb-importer"])
        assert isinstance(KB_AGENT_IDS, frozenset)
        assert "experiencer" in KB_AGENT_IDS
        assert "kb-importer" in KB_AGENT_IDS
        assert len(KB_AGENT_IDS) == 2


class TestListExcludesKB:
    """Tests for repository list() with exclude_kb parameter."""

    def test_list_excludes_kb_by_default(self, repo):
        """Insert 3 instances (1 regular, 1 experiencer, 1 kb-importer).
        Call repo.list(). Assert only regular returned, total=1.
        """
        # Create instances
        regular = _make_instance(repo, "developer", project_id="proj-1")
        experiencer = _make_instance(repo, "experiencer", project_id="proj-1")
        kb_importer = _make_instance(repo, "kb-importer", project_id="proj-1")

        # List with default exclude_kb=True
        instances, total = repo.list()

        assert total == 1
        assert len(instances) == 1
        assert instances[0].agent_id == "developer"
        assert regular.instance_id in [i.instance_id for i in instances]
        assert experiencer.instance_id not in [i.instance_id for i in instances]
        assert kb_importer.instance_id not in [i.instance_id for i in instances]

    def test_list_includes_kb_when_excluded_false(self, repo):
        """Same 3 instances. Call repo.list(exclude_kb=False). Assert all 3 returned."""
        # Create instances
        _make_instance(repo, "developer", project_id="proj-1")
        _make_instance(repo, "experiencer", project_id="proj-1")
        _make_instance(repo, "kb-importer", project_id="proj-1")

        # List with exclude_kb=False
        instances, total = repo.list(exclude_kb=False)

        assert total == 3
        assert len(instances) == 3
        agent_ids = {i.agent_id for i in instances}
        assert agent_ids == {"developer", "experiencer", "kb-importer"}

    def test_list_kb_filter_with_project_id(self, repo):
        """Insert instances across 2 projects. Verify exclude_kb works with project_id filter."""
        # Project 1: developer and experiencer
        _make_instance(repo, "developer", project_id="proj-1")
        _make_instance(repo, "experiencer", project_id="proj-1")

        # Project 2: developer, kb-importer, and another regular agent
        _make_instance(repo, "developer", project_id="proj-2")
        _make_instance(repo, "kb-importer", project_id="proj-2")
        _make_instance(repo, "designer", project_id="proj-2")

        # Filter by project_id=proj-1, exclude_kb=True -> should return only developer
        instances, total = repo.list(project_id="proj-1", exclude_kb=True)
        assert total == 1
        assert len(instances) == 1
        assert instances[0].agent_id == "developer"
        assert instances[0].project_id == "proj-1"

        # Filter by project_id=proj-2, exclude_kb=True -> should return developer and designer
        instances, total = repo.list(project_id="proj-2", exclude_kb=True)
        assert total == 2
        assert len(instances) == 2
        agent_ids = {i.agent_id for i in instances}
        assert agent_ids == {"developer", "designer"}

        # Filter by project_id=proj-2, exclude_kb=False -> should return all 3
        instances, total = repo.list(project_id="proj-2", exclude_kb=False)
        assert total == 3
        assert len(instances) == 3

    def test_list_kb_filter_pagination(self, repo):
        """Insert 5 regular + 2 KB instances. Verify limit/offset work with exclude_kb=True."""
        # Create 5 regular instances
        for i in range(5):
            _make_instance(repo, "developer", project_id="proj-1")

        # Create 2 KB instances
        _make_instance(repo, "experiencer", project_id="proj-1")
        _make_instance(repo, "kb-importer", project_id="proj-1")

        # With exclude_kb=True, total should be 5
        instances, total = repo.list(exclude_kb=True)
        assert total == 5
        assert len(instances) == 5

        # Test limit=2, offset=0
        instances, total = repo.list(limit=2, offset=0, exclude_kb=True)
        assert total == 5
        assert len(instances) == 2

        # Test limit=2, offset=2
        instances, total = repo.list(limit=2, offset=2, exclude_kb=True)
        assert total == 5
        assert len(instances) == 2

        # Test limit=2, offset=4 (last page)
        instances, total = repo.list(limit=2, offset=4, exclude_kb=True)
        assert total == 5
        assert len(instances) == 1

        # Test with exclude_kb=False, total should be 7
        instances, total = repo.list(exclude_kb=False)
        assert total == 7

    def test_list_kb_filter_status_combined(self, repo):
        """Verify exclude_kb works correctly with status filter combined."""
        # Create running and completed instances of each type
        _make_instance(repo, "developer", status="running")
        _make_instance(repo, "developer", status="completed")
        _make_instance(repo, "experiencer", status="running")
        _make_instance(repo, "experiencer", status="completed")
        _make_instance(repo, "kb-importer", status="running")
        _make_instance(repo, "kb-importer", status="completed")

        # With exclude_kb=True and status=running -> 1 developer running
        instances, total = repo.list(status="running", exclude_kb=True)
        assert total == 1
        assert len(instances) == 1
        assert instances[0].agent_id == "developer"
        assert instances[0].status == "running"

        # With exclude_kb=False and status=running -> 3 running instances
        instances, total = repo.list(status="running", exclude_kb=False)
        assert total == 3
        assert len(instances) == 3


# =============================================================================
# 2B: API ROUTER TESTS (mock-based, following existing pattern in test_api.py)
# =============================================================================

import pytest_asyncio
from unittest.mock import Mock, AsyncMock
import httpx


@pytest_asyncio.fixture
async def mock_manager_with_kb():
    """Create a mock InstanceManager with KB instances."""
    manager = Mock()
    manager.list_instances = Mock(return_value=([], 0))
    manager.get_instance_info = Mock(return_value={
        "instance_id": "test-instance-id",
        "agent_id": "developer",
        "agent_dir": "/path/to/agent",
        "status": "running",
        "parent_id": None,
        "children": [],
        "project_id": None,
        "created_at": "2024-01-01T00:00:00",
        "updated_at": "2024-01-01T00:00:00"
    })
    return manager


@pytest.fixture
def app_with_mock_manager(mock_manager_with_kb):
    """Create FastAPI app with mocked manager."""
    from daemon.api import app
    from unittest.mock import Mock
    app.state.manager = mock_manager_with_kb
    app.state.start_time = 1000.0
    app.state.credential_manager = Mock()
    return mock_manager_with_kb


@pytest_asyncio.fixture
async def client(app_with_mock_manager):
    """Create async test client."""
    from daemon.api import app

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test/api") as ac:
        yield ac


class TestListInstancesExcludeKB:
    """Tests for exclude_kb parameter in API router."""

    @pytest.mark.asyncio
    async def test_list_instances_exclude_kb_default(self, client, mock_manager_with_kb):
        """Call GET /api/instances, verify mock called with exclude_kb=True."""
        mock_manager_with_kb.list_instances.return_value = ([], 0)

        response = await client.get("/instances")

        assert response.status_code == 200
        mock_manager_with_kb.list_instances.assert_called_once_with(
            limit=10, offset=0, project_id=None, exclude_kb=True, include_descendants=True
        )

    @pytest.mark.asyncio
    async def test_list_instances_exclude_kb_false(self, client, mock_manager_with_kb):
        """Call GET /api/instances?exclude_kb=false, verify mock called with exclude_kb=False."""
        mock_manager_with_kb.list_instances.return_value = ([], 0)

        response = await client.get("/instances?exclude_kb=false")

        assert response.status_code == 200
        mock_manager_with_kb.list_instances.assert_called_once_with(
            limit=10, offset=0, project_id=None, exclude_kb=False, include_descendants=True
        )

    @pytest.mark.asyncio
    async def test_list_instances_exclude_kb_true_explicit(self, client, mock_manager_with_kb):
        """Call GET /api/instances?exclude_kb=true, verify mock called with exclude_kb=True."""
        mock_manager_with_kb.list_instances.return_value = ([], 0)

        response = await client.get("/instances?exclude_kb=true")

        assert response.status_code == 200
        mock_manager_with_kb.list_instances.assert_called_once_with(
            limit=10, offset=0, project_id=None, exclude_kb=True, include_descendants=True
        )

    @pytest.mark.asyncio
    async def test_list_instances_exclude_kb_with_project_id(self, client, mock_manager_with_kb):
        """Verify exclude_kb works with project_id filter combined."""
        mock_manager_with_kb.list_instances.return_value = ([], 0)

        response = await client.get("/instances?project_id=proj-1&exclude_kb=true")

        assert response.status_code == 200
        mock_manager_with_kb.list_instances.assert_called_once_with(
            limit=10, offset=0, project_id="proj-1", exclude_kb=True, include_descendants=True
        )

    @pytest.mark.asyncio
    async def test_list_instances_exclude_kb_false_with_project_id(self, client, mock_manager_with_kb):
        """Verify exclude_kb=false works with project_id filter combined."""
        mock_manager_with_kb.list_instances.return_value = ([], 0)

        response = await client.get("/instances?project_id=proj-1&exclude_kb=false")

        assert response.status_code == 200
        mock_manager_with_kb.list_instances.assert_called_once_with(
            limit=10, offset=0, project_id="proj-1", exclude_kb=False, include_descendants=True
        )


# =============================================================================
# 2C: SSE agent_id TESTS
# =============================================================================

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch


class TestStreamStatusChangeAgentId:
    """Tests for stream_status_change with agent_id parameter."""

    @pytest.mark.asyncio
    async def test_stream_status_change_includes_agent_id(self):
        """Call stream_status_change with agent_id, verify event contains agent_id."""
        from daemon.services.live_event_hub import LiveEventHub

        hub = LiveEventHub()

        # Capture the event dict that gets streamed
        captured_event = None

        async def mock_stream(instance_id, event):
            nonlocal captured_event
            captured_event = event

        hub._stream_to_connections = mock_stream

        await hub.stream_status_change("inst-1", "completed", agent_id="developer")

        assert captured_event is not None
        assert captured_event["instance_id"] == "inst-1"
        assert captured_event["event_type"] == "status_change"
        assert captured_event["status"] == "completed"
        assert "agent_id" in captured_event
        assert captured_event["agent_id"] == "developer"

    @pytest.mark.asyncio
    async def test_stream_status_change_without_agent_id(self):
        """Call stream_status_change without agent_id, verify event does NOT contain agent_id."""
        from daemon.services.live_event_hub import LiveEventHub

        hub = LiveEventHub()

        captured_event = None

        async def mock_stream(instance_id, event):
            nonlocal captured_event
            captured_event = event

        hub._stream_to_connections = mock_stream

        await hub.stream_status_change("inst-1", "completed")

        assert captured_event is not None
        assert captured_event["instance_id"] == "inst-1"
        assert captured_event["event_type"] == "status_change"
        assert captured_event["status"] == "completed"
        assert "agent_id" not in captured_event

    @pytest.mark.asyncio
    async def test_stream_status_change_with_none_agent_id(self):
        """Call stream_status_change with agent_id=None, verify event does NOT contain agent_id."""
        from daemon.services.live_event_hub import LiveEventHub

        hub = LiveEventHub()

        captured_event = None

        async def mock_stream(instance_id, event):
            nonlocal captured_event
            captured_event = event

        hub._stream_to_connections = mock_stream

        await hub.stream_status_change("inst-1", "completed", agent_id=None)

        assert captured_event is not None
        assert captured_event["instance_id"] == "inst-1"
        assert captured_event["event_type"] == "status_change"
        assert captured_event["status"] == "completed"
        assert "agent_id" not in captured_event

    @pytest.mark.asyncio
    async def test_stream_status_change_different_statuses(self):
        """Verify agent_id is included for different status values."""
        from daemon.services.live_event_hub import LiveEventHub

        hub = LiveEventHub()

        for status in ["running", "completed", "error", "terminated", "paused"]:
            captured_event = None

            async def mock_stream(instance_id, event):
                nonlocal captured_event
                captured_event = event

            hub._stream_to_connections = mock_stream

            await hub.stream_status_change("inst-1", status, agent_id="test-agent")

            assert captured_event is not None
            assert captured_event["status"] == status
            assert captured_event["agent_id"] == "test-agent"
