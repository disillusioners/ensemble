"""Tests for daemon/api.py project endpoints (visibility controls for system project)."""

import os
import tempfile
from unittest.mock import MagicMock

import pytest
import pytest_asyncio

import httpx
from sqlmodel import SQLModel, create_engine

from daemon.api import app
from daemon.routers.projects import set_project_repository, set_job_queue_mgmt_service
from daemon.repositories import SQLModelProjectRepository
from daemon.repositories.project.models import Project, ProjectTagLink, ProjectShortnameLink  # noqa: F401 - needed for table creation
from daemon.constants import SYSTEM_DEFAULT_PROJECT_NAME, SYSTEM_DEFAULT_PROJECT_ID


@pytest.fixture
def db_path():
    """Create a temporary database file for testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    yield db_path
    # Cleanup
    try:
        os.unlink(db_path)
    except OSError:
        pass


@pytest.fixture
def engine(db_path):
    """Create SQLite engine for testing with file-based database."""
    engine = create_engine(f"sqlite:///{db_path}", echo=False)
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest_asyncio.fixture
async def client(engine):
    """Create async test client with project repository."""
    # Create repository and bootstrap system project
    repo = SQLModelProjectRepository(engine)
    system_project_id = repo.ensure_system_default_project()

    # Set the repository on the router
    set_project_repository(repo)

    # Mock the queue management service (auto_provision_system_queues is called in background)
    mock_queue_mgmt = MagicMock()
    mock_queue_mgmt.auto_provision_system_queues = MagicMock(return_value=None)
    set_job_queue_mgmt_service(mock_queue_mgmt)

    # Create app state for any middleware that needs it
    app.state.manager = None
    app.state.start_time = 1000.0

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ac:
        yield ac, repo, system_project_id


# ============== GET /projects tests ==============

@pytest.mark.asyncio
async def test_list_projects_includes_system_by_default(client):
    """Test GET /projects (no params) includes the system project."""
    client, repo, system_project_id = client

    response = await client.get("/api/projects")

    assert response.status_code == 200
    data = response.json()
    assert "projects" in data
    assert "total" in data

    # System project should be in the list
    project_names = [p["name"] for p in data["projects"]]
    assert SYSTEM_DEFAULT_PROJECT_NAME in project_names


@pytest.mark.asyncio
async def test_list_projects_exclude_system_true(client):
    """Test GET /projects?exclude_system=true excludes the system project."""
    client, repo, system_project_id = client

    response = await client.get("/api/projects?exclude_system=true")

    assert response.status_code == 200
    data = response.json()

    # System project should NOT be in the list
    project_names = [p["name"] for p in data["projects"]]
    assert SYSTEM_DEFAULT_PROJECT_NAME not in project_names


@pytest.mark.asyncio
async def test_list_projects_exclude_system_false(client):
    """Test GET /projects?exclude_system=false includes the system project."""
    client, repo, system_project_id = client

    response = await client.get("/api/projects?exclude_system=false")

    assert response.status_code == 200
    data = response.json()

    # System project should be in the list
    project_names = [p["name"] for p in data["projects"]]
    assert SYSTEM_DEFAULT_PROJECT_NAME in project_names


@pytest.mark.asyncio
async def test_get_single_project_returns_system_project(client):
    """Test GET /projects/{id} returns the system project normally."""
    client, repo, system_project_id = client

    response = await client.get(f"/api/projects/{system_project_id}")

    assert response.status_code == 200
    data = response.json()
    assert data["project_id"] == system_project_id
    assert data["name"] == SYSTEM_DEFAULT_PROJECT_NAME


@pytest.mark.asyncio
async def test_is_system_true_for_system_project(client):
    """Test that the system project's response has is_system: true."""
    client, repo, system_project_id = client

    response = await client.get("/api/projects")

    assert response.status_code == 200
    data = response.json()

    # Find the system project in the list
    system_projects = [p for p in data["projects"] if p["name"] == SYSTEM_DEFAULT_PROJECT_NAME]
    assert len(system_projects) == 1
    assert system_projects[0]["is_system"] is True


@pytest.mark.asyncio
async def test_is_system_false_for_regular_project(client):
    """Test that a regular project's response has is_system: false."""
    client, repo, system_project_id = client

    # Create a regular project via API
    create_response = await client.post(
        "/api/projects",
        json={
            "name": "test-regular-project",
            "project_type": "general",
            "description": "A test project"
        }
    )
    assert create_response.status_code == 201
    regular_project = create_response.json()
    regular_project_id = regular_project["project_id"]

    # Verify is_system is False
    assert regular_project["is_system"] is False

    # Also verify via GET /projects listing
    list_response = await client.get("/api/projects")
    assert list_response.status_code == 200
    projects = list_response.json()["projects"]

    # Find our regular project
    found = [p for p in projects if p["project_id"] == regular_project_id]
    assert len(found) == 1
    assert found[0]["is_system"] is False


# ============== Additional edge case tests ==============

@pytest.mark.asyncio
async def test_list_projects_trailing_slash(client):
    """Test GET /projects/ (trailing slash) works the same as GET /projects."""
    client, repo, system_project_id = client

    response = await client.get("/api/projects/")

    assert response.status_code == 200
    data = response.json()
    project_names = [p["name"] for p in data["projects"]]
    assert SYSTEM_DEFAULT_PROJECT_NAME in project_names


@pytest.mark.asyncio
async def test_get_nonexistent_project_returns_404(client):
    """Test GET /projects/{id} with non-existent ID returns 404."""
    client, repo, system_project_id = client

    response = await client.get("/api/projects/nonexistent-id-12345")

    assert response.status_code == 404
    data = response.json()
    assert "error" in data or "detail" in data


@pytest.mark.asyncio
async def test_total_count_reflects_exclude_system(client):
    """Test that total count excludes system project when exclude_system=true."""
    client, repo, system_project_id = client

    # Create a regular project
    await client.post(
        "/api/projects",
        json={"name": "count-test-project", "project_type": "general"}
    )

    # Get counts with and without system project
    with_system = await client.get("/api/projects")
    without_system = await client.get("/api/projects?exclude_system=true")

    assert with_system.status_code == 200
    assert without_system.status_code == 200

    # Total should be different (by exactly 1)
    assert with_system.json()["total"] == without_system.json()["total"] + 1
