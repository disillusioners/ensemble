"""Tests for daemon/routers/projects.py - Project History API endpoints."""

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
    repo.ensure_system_default_project()

    # Set the repository on the router
    set_project_repository(repo)

    # Mock the queue management service
    mock_queue_mgmt = MagicMock()
    mock_queue_mgmt.auto_provision_system_queues = MagicMock(return_value=None)
    set_job_queue_mgmt_service(mock_queue_mgmt)

    # Create app state for any middleware that needs it
    app.state.manager = None
    app.state.start_time = 1000.0

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ac:
        yield ac, repo


@pytest_asyncio.fixture
async def project_with_history(client):
    """Create a project with multiple history entries for testing."""
    client_fixture, repo = client

    # Create a test project
    project = repo.create(name="test-history-project")
    project_id = project.project_id

    # Add some history entries
    entries = []
    for i in range(5):
        entry = repo.add_history_entry(
            project_id=project_id,
            entry_type="milestone",
            summary=f"Milestone {i}",
            details=f"Details for milestone {i}",
            entry_metadata={"index": i},
        )
        entries.append(entry)

    # Add a deployment entry
    repo.add_history_entry(
        project_id=project_id,
        entry_type="deployment",
        summary="Deployed to production",
        details="Version 1.0.0 deployed successfully",
    )

    return client_fixture, repo, project_id, entries


# ============== GET /projects/{project_id}/history (List) tests ==============

@pytest.mark.asyncio
async def test_list_history_empty_for_new_project(client):
    """Test GET /history returns empty list for new project."""
    client_fixture, repo = client

    # Create a fresh project
    project = repo.create(name="empty-history-project")
    project_id = project.project_id

    response = await client_fixture.get(f"/api/projects/{project_id}/history")

    assert response.status_code == 200
    data = response.json()
    assert data["entries"] == []
    assert data["total"] == 0
    assert data["limit"] == 20
    assert data["offset"] == 0


@pytest.mark.asyncio
async def test_list_history_returns_entries(client, project_with_history):
    """Test GET /history returns entries after adding them."""
    client_fixture, repo, project_id, entries = project_with_history

    response = await client_fixture.get(f"/api/projects/{project_id}/history")

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 6  # 5 milestones + 1 deployment
    assert len(data["entries"]) == 6


@pytest.mark.asyncio
async def test_list_history_with_entry_type_filter(client, project_with_history):
    """Test GET /history supports entry_type filter."""
    client_fixture, repo, project_id, entries = project_with_history

    # Filter for milestones only
    response = await client_fixture.get(
        f"/api/projects/{project_id}/history?entry_type=milestone"
    )

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 5
    assert len(data["entries"]) == 5
    for entry in data["entries"]:
        assert entry["entry_type"] == "milestone"


@pytest.mark.asyncio
async def test_list_history_with_deployment_filter(client, project_with_history):
    """Test GET /history with deployment filter."""
    client_fixture, repo, project_id, entries = project_with_history

    response = await client_fixture.get(
        f"/api/projects/{project_id}/history?entry_type=deployment"
    )

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert len(data["entries"]) == 1
    assert data["entries"][0]["entry_type"] == "deployment"


@pytest.mark.asyncio
async def test_list_history_with_limit(client, project_with_history):
    """Test GET /history supports limit parameter."""
    client_fixture, repo, project_id, entries = project_with_history

    response = await client_fixture.get(
        f"/api/projects/{project_id}/history?limit=2"
    )

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 6  # Total still shows all entries
    assert len(data["entries"]) == 2
    assert data["limit"] == 2


@pytest.mark.asyncio
async def test_list_history_with_offset(client, project_with_history):
    """Test GET /history supports offset parameter."""
    client_fixture, repo, project_id, entries = project_with_history

    response = await client_fixture.get(
        f"/api/projects/{project_id}/history?limit=3&offset=2"
    )

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 6
    assert len(data["entries"]) == 3
    assert data["offset"] == 2
    assert data["limit"] == 3


@pytest.mark.asyncio
async def test_list_history_nonexistent_project_returns_404(client):
    """Test GET /history returns 404 for non-existent project."""
    client_fixture, repo = client

    response = await client_fixture.get("/api/projects/nonexistent-id-12345/history")

    assert response.status_code == 404
    data = response.json()
    assert "error" in data["detail"] or "detail" in data


# ============== POST /projects/{project_id}/history (Add) tests ==============

@pytest.mark.asyncio
async def test_add_history_entry_with_all_fields(client):
    """Test POST /history creates entry with all fields."""
    client_fixture, repo = client

    # Create a project
    project = repo.create(name="add-entry-project")
    project_id = project.project_id

    response = await client_fixture.post(
        f"/api/projects/{project_id}/history",
        json={
            "entry_type": "milestone",
            "summary": "Phase 1 completed",
            "details": "All requirements implemented and tested",
            "entry_metadata": {"phase": 1, "completed_by": "coder"},
        },
    )

    assert response.status_code == 201
    data = response.json()
    assert data["id"] is not None
    assert data["project_id"] == project_id
    assert data["entry_type"] == "milestone"
    assert data["summary"] == "Phase 1 completed"
    assert data["details"] == "All requirements implemented and tested"
    assert data["entry_metadata"] == {"phase": 1, "completed_by": "coder"}
    assert data["source_agent"] is None
    assert data["source_instance_id"] is None
    assert data["created_at"] is not None


@pytest.mark.asyncio
async def test_add_history_entry_required_fields_only(client):
    """Test POST /history creates entry with only required fields."""
    client_fixture, repo = client

    project = repo.create(name="minimal-entry-project")
    project_id = project.project_id

    response = await client_fixture.post(
        f"/api/projects/{project_id}/history",
        json={
            "entry_type": "note",
            "summary": "Quick note",
        },
    )

    assert response.status_code == 201
    data = response.json()
    assert data["id"] is not None
    assert data["entry_type"] == "note"
    assert data["summary"] == "Quick note"
    assert data["details"] is None
    assert data["entry_metadata"] is None


@pytest.mark.asyncio
async def test_add_history_entry_invalid_entry_type(client):
    """Test POST /history returns 400 for invalid entry_type."""
    client_fixture, repo = client

    project = repo.create(name="invalid-type-project")
    project_id = project.project_id

    response = await client_fixture.post(
        f"/api/projects/{project_id}/history",
        json={
            "entry_type": "invalid_type_xyz",
            "summary": "This should fail",
        },
    )

    assert response.status_code == 400
    data = response.json()
    assert "error" in data["detail"] or "detail" in data


@pytest.mark.asyncio
async def test_add_history_entry_missing_required_fields(client):
    """Test POST /history returns 422 for missing required fields."""
    client_fixture, repo = client

    project = repo.create(name="missing-fields-project")
    project_id = project.project_id

    # Missing entry_type
    response = await client_fixture.post(
        f"/api/projects/{project_id}/history",
        json={
            "summary": "Missing entry_type",
        },
    )

    assert response.status_code == 422

    # Missing summary
    response = await client_fixture.post(
        f"/api/projects/{project_id}/history",
        json={
            "entry_type": "note",
        },
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_add_history_entry_nonexistent_project_returns_404(client):
    """Test POST /history returns 404 for non-existent project."""
    client_fixture, repo = client

    response = await client_fixture.post(
        "/api/projects/nonexistent-id-12345/history",
        json={
            "entry_type": "note",
            "summary": "This should fail",
        },
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_add_history_entry_all_valid_types(client):
    """Test POST /history accepts all valid entry types."""
    client_fixture, repo = client

    project = repo.create(name="all-types-project")
    project_id = project.project_id

    valid_types = [
        "milestone", "commit", "phase", "bugfix",
        "deployment", "note", "config_change", "other",
    ]

    for entry_type in valid_types:
        response = await client_fixture.post(
            f"/api/projects/{project_id}/history",
            json={
                "entry_type": entry_type,
                "summary": f"Testing {entry_type}",
            },
        )

        assert response.status_code == 201, f"Failed for type: {entry_type}"
        data = response.json()
        assert data["entry_type"] == entry_type


# ============== GET /projects/{project_id}/history/search tests ==============

@pytest.mark.asyncio
async def test_search_history_returns_matching_entries(client, project_with_history):
    """Test GET /history/search returns matching entries."""
    client_fixture, repo, project_id, entries = project_with_history

    response = await client_fixture.get(
        f"/api/projects/{project_id}/history/search?q=Milestone 2"
    )

    assert response.status_code == 200
    data = response.json()
    assert data["total"] >= 1
    assert len(data["entries"]) >= 1
    assert data["query"] == "Milestone 2"
    # At least one entry should contain "Milestone 2"
    summaries = [e["summary"] for e in data["entries"]]
    assert any("Milestone 2" in s for s in summaries)


@pytest.mark.asyncio
async def test_search_history_by_details(client):
    """Test GET /history/search searches in details field too."""
    client_fixture, repo = client

    project = repo.create(name="search-details-project")
    project_id = project.project_id

    # Add entry with searchable details
    repo.add_history_entry(
        project_id=project_id,
        entry_type="note",
        summary="Updated component",
        details="Fixed memory leak issue",
    )

    response = await client_fixture.get(
        f"/api/projects/{project_id}/history/search?q=memory leak"
    )

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert "memory leak" in data["entries"][0]["details"]


@pytest.mark.asyncio
async def test_search_history_no_match_returns_empty(client, project_with_history):
    """Test GET /history/search returns empty for non-matching query."""
    client_fixture, repo, project_id, entries = project_with_history

    response = await client_fixture.get(
        f"/api/projects/{project_id}/history/search?q=nonexistent-search-term-xyz"
    )

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 0
    assert data["entries"] == []
    assert data["query"] == "nonexistent-search-term-xyz"


@pytest.mark.asyncio
async def test_search_history_with_limit(client, project_with_history):
    """Test GET /history/search supports limit parameter."""
    client_fixture, repo, project_id, entries = project_with_history

    response = await client_fixture.get(
        f"/api/projects/{project_id}/history/search?q=Milestone&limit=2"
    )

    assert response.status_code == 200
    data = response.json()
    assert data["limit"] == 2
    assert len(data["entries"]) <= 2


@pytest.mark.asyncio
async def test_search_history_with_offset(client, project_with_history):
    """Test GET /history/search supports offset parameter."""
    client_fixture, repo, project_id, entries = project_with_history

    # First get total count
    response_all = await client_fixture.get(
        f"/api/projects/{project_id}/history/search?q=Milestone"
    )
    total = response_all.json()["total"]

    if total >= 2:
        response = await client_fixture.get(
            f"/api/projects/{project_id}/history/search?q=Milestone&limit=2&offset=1"
        )

        assert response.status_code == 200
        data = response.json()
        assert data["offset"] == 1
        assert len(data["entries"]) <= 2


@pytest.mark.asyncio
async def test_search_history_nonexistent_project_returns_404(client):
    """Test GET /history/search returns 404 for non-existent project."""
    client_fixture, repo = client

    response = await client_fixture.get(
        "/api/projects/nonexistent-id-12345/history/search?q=test"
    )

    assert response.status_code == 404


# ============== DELETE /projects/{project_id}/history/{entry_id} tests ==============

@pytest.mark.asyncio
async def test_delete_history_entry_success(client):
    """Test DELETE /history/{entry_id} successfully deletes own entry."""
    client_fixture, repo = client

    project = repo.create(name="delete-test-project")
    project_id = project.project_id

    # Add an entry
    entry = repo.add_history_entry(
        project_id=project_id,
        entry_type="note",
        summary="Entry to delete",
    )
    entry_id = entry["id"]

    # Delete it
    response = await client_fixture.delete(
        f"/api/projects/{project_id}/history/{entry_id}"
    )

    assert response.status_code == 200
    data = response.json()
    assert data["message"] == "History entry deleted"
    assert data["entry_id"] == entry_id

    # Verify it's gone
    assert repo.get_history_entry(entry_id) is None


@pytest.mark.asyncio
async def test_delete_history_entry_not_found(client):
    """Test DELETE /history/{entry_id} returns 404 for non-existent entry."""
    client_fixture, repo = client

    project = repo.create(name="not-found-delete-project")
    project_id = project.project_id

    response = await client_fixture.delete(
        f"/api/projects/{project_id}/history/nonexistent-entry-id-12345"
    )

    assert response.status_code == 404
    data = response.json()
    assert "error" in data["detail"] or "detail" in data


@pytest.mark.asyncio
async def test_delete_history_entry_wrong_project(client):
    """Test DELETE /history/{entry_id} returns 404 for entry belonging to different project."""
    client_fixture, repo = client

    # Create two projects
    project_a = repo.create(name="project-a-delete")
    project_b = repo.create(name="project-b-delete")

    # Add entry to project A
    entry = repo.add_history_entry(
        project_id=project_a.project_id,
        entry_type="note",
        summary="Entry in project A",
    )
    entry_id = entry["id"]

    # Try to delete from project B
    response = await client_fixture.delete(
        f"/api/projects/{project_b.project_id}/history/{entry_id}"
    )

    assert response.status_code == 404

    # Verify entry still exists in project A
    assert repo.get_history_entry(entry_id) is not None


@pytest.mark.asyncio
async def test_delete_history_project_not_found(client):
    """Test DELETE /history/{entry_id} returns 404 for non-existent project."""
    client_fixture, repo = client

    # First create an entry in a valid project so we have a valid entry_id
    project = repo.create(name="temp-project")
    entry = repo.add_history_entry(
        project_id=project.project_id,
        entry_type="note",
        summary="Temporary entry",
    )
    entry_id = entry["id"]

    # Try to delete from non-existent project
    response = await client_fixture.delete(
        f"/api/projects/nonexistent-id-12345/history/{entry_id}"
    )

    assert response.status_code == 404


# ============== Integration flow tests ==============

@pytest.mark.asyncio
async def test_integration_flow_add_list_search_delete(client):
    """Test complete flow: add -> list -> search -> delete -> verify deleted."""
    client_fixture, repo = client

    # Create project
    project = repo.create(name="integration-test-project")
    project_id = project.project_id

    # 1. List should be empty initially
    response = await client_fixture.get(f"/api/projects/{project_id}/history")
    assert response.status_code == 200
    assert response.json()["total"] == 0

    # 2. Add multiple entries
    entry1 = await client_fixture.post(
        f"/api/projects/{project_id}/history",
        json={
            "entry_type": "milestone",
            "summary": "Phase 1: API implementation",
            "details": "Built REST endpoints",
        },
    )
    assert entry1.status_code == 201
    entry1_id = entry1.json()["id"]

    entry2 = await client_fixture.post(
        f"/api/projects/{project_id}/history",
        json={
            "entry_type": "deployment",
            "summary": "Deploy to staging",
            "details": "Version 0.1.0",
        },
    )
    assert entry2.status_code == 201
    entry2_id = entry2.json()["id"]

    entry3 = await client_fixture.post(
        f"/api/projects/{project_id}/history",
        json={
            "entry_type": "bugfix",
            "summary": "Fixed login bug",
        },
    )
    assert entry3.status_code == 201
    entry3_id = entry3.json()["id"]

    # 3. List should show 3 entries
    response = await client_fixture.get(f"/api/projects/{project_id}/history")
    assert response.status_code == 200
    assert response.json()["total"] == 3

    # 4. Search for "Phase"
    response = await client_fixture.get(
        f"/api/projects/{project_id}/history/search?q=Phase"
    )
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["entries"][0]["summary"] == "Phase 1: API implementation"

    # 5. Search for "deploy" (case insensitive)
    response = await client_fixture.get(
        f"/api/projects/{project_id}/history/search?q=deploy"
    )
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert "Deploy" in response.json()["entries"][0]["summary"]

    # 6. Filter by entry_type
    response = await client_fixture.get(
        f"/api/projects/{project_id}/history?entry_type=deployment"
    )
    assert response.status_code == 200
    assert response.json()["total"] == 1

    # 7. Delete entry2 (deployment)
    response = await client_fixture.delete(
        f"/api/projects/{project_id}/history/{entry2_id}"
    )
    assert response.status_code == 200

    # 8. List should show 2 entries now
    response = await client_fixture.get(f"/api/projects/{project_id}/history")
    assert response.status_code == 200
    assert response.json()["total"] == 2

    # 9. Verify deleted entry is gone from search
    response = await client_fixture.get(
        f"/api/projects/{project_id}/history/search?q=staging"
    )
    assert response.status_code == 200
    assert response.json()["total"] == 0

    # 10. Verify deleting already deleted entry returns 404
    response = await client_fixture.delete(
        f"/api/projects/{project_id}/history/{entry2_id}"
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_cross_project_isolation_in_history(client):
    """Test that history entries are isolated between projects."""
    client_fixture, repo = client

    # Create two projects
    project_a = repo.create(name="project-alpha")
    project_b = repo.create(name="project-beta")
    project_a_id = project_a.project_id
    project_b_id = project_b.project_id

    # Add entries to project A
    repo.add_history_entry(
        project_id=project_a_id,
        entry_type="milestone",
        summary="Alpha milestone",
    )

    # Add entries to project B
    repo.add_history_entry(
        project_id=project_b_id,
        entry_type="deployment",
        summary="Beta deployment",
    )

    # List history for project A - should only show Alpha entries
    response_a = await client_fixture.get(f"/api/projects/{project_a_id}/history")
    assert response_a.status_code == 200
    assert response_a.json()["total"] == 1
    assert response_a.json()["entries"][0]["summary"] == "Alpha milestone"

    # List history for project B - should only show Beta entries
    response_b = await client_fixture.get(f"/api/projects/{project_b_id}/history")
    assert response_b.status_code == 200
    assert response_b.json()["total"] == 1
    assert response_b.json()["entries"][0]["summary"] == "Beta deployment"

    # Search in project A - should not find Beta
    response = await client_fixture.get(
        f"/api/projects/{project_a_id}/history/search?q=Beta"
    )
    assert response.json()["total"] == 0

    # Try to delete project B's entry from project A - should fail
    entry_b_id = response_b.json()["entries"][0]["id"]
    response = await client_fixture.delete(
        f"/api/projects/{project_a_id}/history/{entry_b_id}"
    )
    assert response.status_code == 404

    # Verify entry still exists in project B
    assert repo.get_history_entry(entry_b_id) is not None


@pytest.mark.asyncio
async def test_pagination_across_all_endpoints(client):
    """Test pagination works consistently across list and search endpoints."""
    client_fixture, repo = client

    project = repo.create(name="pagination-test-project")
    project_id = project.project_id

    # Add 15 entries
    for i in range(15):
        repo.add_history_entry(
            project_id=project_id,
            entry_type="note",
            summary=f"Note number {i}",
        )

    # Test list pagination
    page1 = await client_fixture.get(
        f"/api/projects/{project_id}/history?limit=5&offset=0"
    )
    assert page1.status_code == 200
    assert page1.json()["total"] == 15
    assert len(page1.json()["entries"]) == 5
    assert page1.json()["limit"] == 5
    assert page1.json()["offset"] == 0

    page2 = await client_fixture.get(
        f"/api/projects/{project_id}/history?limit=5&offset=5"
    )
    assert page2.status_code == 200
    assert len(page2.json()["entries"]) == 5
    assert page2.json()["offset"] == 5

    page3 = await client_fixture.get(
        f"/api/projects/{project_id}/history?limit=5&offset=10"
    )
    assert page3.status_code == 200
    assert len(page3.json()["entries"]) == 5
    assert page3.json()["offset"] == 10

    # Test search pagination
    search1 = await client_fixture.get(
        f"/api/projects/{project_id}/history/search?q=Note&limit=5&offset=0"
    )
    assert search1.status_code == 200
    assert search1.json()["total"] == 15
    assert len(search1.json()["entries"]) == 5
