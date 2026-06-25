"""Tests for daemon/api.py agent endpoints (GET/POST/DELETE /agents)."""

import pytest
import pytest_asyncio
import json
import tempfile
import shutil
from unittest.mock import Mock, patch
from pathlib import Path

import httpx

# Import the app and manager directly
from daemon import api as api_module


@pytest.fixture
def temp_agents_dir():
    """Create a temporary agents directory with test data."""
    with tempfile.TemporaryDirectory() as tmpdir:
        agents_path = Path(tmpdir) / "agents"
        agents_path.mkdir()
        
        # Create template directory
        template_dir = agents_path / "_baby_template"
        template_dir.mkdir()
        (template_dir / "soul.md").write_text("# Soul\nYou are a helpful assistant.")
        (template_dir / "rule.md").write_text("# Rules\nFollow these rules.")
        (template_dir / "workflow.md").write_text("# Workflow\nSteps here.")
        
        # Create existing agent
        developer_dir = agents_path / "developer"
        developer_dir.mkdir()
        (developer_dir / "meta.json").write_text(json.dumps({
            "id": "developer",
            "name": "Developer",
            "description": "Code generation agent",
            "icon": "💻",
            "color": "accent-cyan",
            "version": "1.0.0"
        }))
        
        # Create internal agent (should be hidden)
        inner_dir = agents_path / "_inner_soul"
        inner_dir.mkdir()
        (inner_dir / "meta.json").write_text(json.dumps({
            "id": "_inner_soul",
            "name": "Inner Soul",
            "description": "Internal agent",
            "icon": "🔮",
            "color": "accent-violet"
        }))
        
        yield agents_path


@pytest_asyncio.fixture
async def client_with_temp_agents(temp_agents_dir):
    """Create async test client with temporary agents directory."""
    # Import app and agents module
    from daemon.api import app
    from daemon.routers import agents as agents_module
    
    # Set mock manager on app.state (Phase 3: routers use request.app.state.manager)
    app.state.manager = Mock()
    app.state.start_time = 1000.0
    
    # Patch agents module BASE_DIR for test agents directory
    with patch.object(agents_module, 'BASE_DIR', temp_agents_dir.parent):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ac:
            yield ac, temp_agents_dir


# ============== GET /agents tests ==============

@pytest.mark.asyncio
async def test_list_agents_success(client_with_temp_agents):
    """Test GET /agents returns list of available agents."""
    client, _ = client_with_temp_agents
    
    response = await client.get("/api/agents")
    
    assert response.status_code == 200
    data = response.json()
    assert "agents" in data
    
    # Should only include non-internal agents
    agents = data["agents"]
    assert len(agents) == 1
    assert agents[0]["id"] == "developer"
    assert agents[0]["name"] == "Developer"
    assert agents[0]["icon"] == "💻"
    assert agents[0]["color"] == "accent-cyan"
    assert agents[0]["agent_dir"] == "./agents/developer"


@pytest.mark.asyncio
async def test_list_agents_excludes_internal(client_with_temp_agents):
    """Test GET /agents excludes internal agents (starting with _)."""
    client, _ = client_with_temp_agents
    
    response = await client.get("/api/agents")
    
    assert response.status_code == 200
    agents = response.json()["agents"]
    
    # _inner_soul should not be in the list
    agent_ids = [a["id"] for a in agents]
    assert "_inner_soul" not in agent_ids
    assert "_baby_template" not in agent_ids


@pytest.mark.asyncio
async def test_list_agents_empty_directory(client_with_temp_agents):
    """Test GET /agents with no agents returns empty list."""
    client, temp_agents_dir = client_with_temp_agents
    
    # Remove all non-internal agents
    developer_dir = temp_agents_dir / "developer"
    shutil.rmtree(developer_dir)
    
    response = await client.get("/api/agents")
    
    assert response.status_code == 200
    data = response.json()
    assert data["agents"] == []


@pytest.mark.asyncio
async def test_list_agents_missing_meta_json(client_with_temp_agents):
    """Test GET /agents handles agent without meta.json gracefully."""
    client, temp_agents_dir = client_with_temp_agents
    
    # Create agent without meta.json
    no_meta_dir = temp_agents_dir / "no-meta-agent"
    no_meta_dir.mkdir()
    
    response = await client.get("/api/agents")
    
    assert response.status_code == 200
    agents = response.json()["agents"]
    
    # Should only include agents with valid meta.json
    agent_ids = [a["id"] for a in agents]
    assert "no-meta-agent" not in agent_ids


# ============== POST /agents tests ==============

@pytest.mark.asyncio
async def test_create_agent_success(client_with_temp_agents):
    """Test POST /agents creates a new agent."""
    client, temp_agents_dir = client_with_temp_agents
    
    response = await client.post(
        "/api/agents",
        json={
            "id": "test-agent",
            "name": "Test Agent",
            "description": "A test agent",
            "icon": "🚀",
            "color": "accent-emerald"
        }
    )
    
    assert response.status_code == 201
    data = response.json()
    assert data["id"] == "test-agent"
    assert data["name"] == "Test Agent"
    assert data["description"] == "A test agent"
    assert data["icon"] == "🚀"
    assert data["color"] == "accent-emerald"
    assert data["version"] == "1.0.0"
    assert data["agent_dir"] == "./agents/test-agent"
    
    # Verify directory was created
    agent_dir = temp_agents_dir / "test-agent"
    assert agent_dir.exists()
    assert (agent_dir / "meta.json").exists()
    assert (agent_dir / "soul.md").exists()
    assert (agent_dir / "history").is_dir()
    assert (agent_dir / "memories").is_dir()


@pytest.mark.asyncio
async def test_create_agent_already_exists(client_with_temp_agents):
    """Test POST /agents with existing ID returns 409."""
    client, _ = client_with_temp_agents
    
    response = await client.post(
        "/api/agents",
        json={
            "id": "developer",  # Already exists
            "name": "Another Coder",
            "description": "Duplicate"
        }
    )
    
    assert response.status_code == 409
    data = response.json()
    assert data["detail"]["code"] == "INVALID_REQUEST"
    assert "already exists" in data["detail"]["message"]


@pytest.mark.asyncio
async def test_create_agent_invalid_id(client_with_temp_agents):
    """Test POST /agents with invalid ID returns 400."""
    client, _ = client_with_temp_agents
    
    response = await client.post(
        "/api/agents",
        json={
            "id": "Invalid ID!",  # Contains spaces and special chars
            "name": "Invalid",
            "description": "Test"
        }
    )
    
    assert response.status_code == 400
    data = response.json()
    assert data["detail"]["code"] == "INVALID_REQUEST"
    assert "alphanumeric" in data["detail"]["message"].lower()


@pytest.mark.asyncio
async def test_create_agent_with_hyphens_and_underscores(client_with_temp_agents):
    """Test POST /agents allows hyphens and underscores in ID."""
    client, _ = client_with_temp_agents
    
    response = await client.post(
        "/api/agents",
        json={
            "id": "my_test-agent",
            "name": "Valid ID",
            "description": "Test"
        }
    )
    
    assert response.status_code == 201
    assert response.json()["id"] == "my_test-agent"


@pytest.mark.asyncio
async def test_create_agent_default_values(client_with_temp_agents):
    """Test POST /agents uses default values for optional fields."""
    client, _ = client_with_temp_agents
    
    response = await client.post(
        "/api/agents",
        json={
            "id": "minimal-agent",
            "name": "Minimal Agent"
        }
    )
    
    assert response.status_code == 201
    data = response.json()
    assert data["description"] == ""
    assert data["icon"] == "🤖"
    assert data["color"] == "accent-blue"


# ============== DELETE /agents tests ==============

@pytest.mark.asyncio
async def test_delete_agent_success(client_with_temp_agents):
    """Test DELETE /agents/{id} moves agent to trash."""
    client, temp_agents_dir = client_with_temp_agents
    
    # First create an agent to delete
    await client.post(
        "/api/agents",
        json={
            "id": "to-delete",
            "name": "To Delete",
            "description": "Will be deleted"
        }
    )
    
    # Verify it exists
    assert (temp_agents_dir / "to-delete").exists()
    
    # Delete it
    response = await client.delete("/api/agents/to-delete")
    
    assert response.status_code == 200
    data = response.json()
    assert data["deleted"] is True
    assert data["agent_id"] == "to-delete"
    assert "trashed_as" in data
    
    # Verify it was moved to trash
    assert not (temp_agents_dir / "to-delete").exists()
    trash_dir = temp_agents_dir / "_trash"
    assert trash_dir.exists()
    
    # Find the trashed agent
    trashed_items = list(trash_dir.iterdir())
    assert len(trashed_items) == 1
    assert trashed_items[0].name.startswith("to-delete_")


@pytest.mark.asyncio
async def test_delete_agent_not_found(client_with_temp_agents):
    """Test DELETE /agents/{id} with non-existent agent returns 404."""
    client, _ = client_with_temp_agents
    
    response = await client.delete("/api/agents/non-existent")
    
    assert response.status_code == 404
    data = response.json()
    assert data["detail"]["code"] == "INVALID_REQUEST"
    assert "not found" in data["detail"]["message"].lower()


@pytest.mark.asyncio
async def test_delete_agent_internal_forbidden(client_with_temp_agents):
    """Test DELETE /agents/{id} cannot delete internal agents."""
    client, _ = client_with_temp_agents
    
    # Try to delete _inner_soul
    response = await client.delete("/api/agents/_inner_soul")
    
    assert response.status_code == 400
    data = response.json()
    assert data["detail"]["code"] == "INVALID_REQUEST"
    assert "internal" in data["detail"]["message"].lower()


@pytest.mark.asyncio
async def test_delete_agent_preserves_in_trash(client_with_temp_agents):
    """Test DELETE /agents/{id} preserves agent files in trash."""
    client, temp_agents_dir = client_with_temp_agents
    
    # Create and delete an agent
    await client.post(
        "/api/agents",
        json={
            "id": "preserve-test",
            "name": "Preserve Test",
            "description": "Files should be preserved"
        }
    )
    
    response = await client.delete("/api/agents/preserve-test")
    trashed_name = response.json()["trashed_as"]
    
    # Verify files are preserved in trash
    trashed_path = temp_agents_dir / "_trash" / trashed_name
    assert trashed_path.exists()
    assert (trashed_path / "meta.json").exists()
    assert (trashed_path / "soul.md").exists()
    
    # Verify meta.json content is preserved
    with open(trashed_path / "meta.json") as f:
        meta = json.load(f)
    assert meta["name"] == "Preserve Test"


@pytest.mark.asyncio
async def test_delete_agent_multiple_same_id(client_with_temp_agents):
    """Test DELETE /agents/{id} handles multiple deletions with unique names."""
    client, temp_agents_dir = client_with_temp_agents
    
    # Create, delete, create again, delete again
    for i in range(2):
        await client.post(
            "/api/agents",
            json={
                "id": "multi-delete",
                "name": f"Multi Delete {i}",
                "description": "Test"
            }
        )
        response = await client.delete("/api/agents/multi-delete")
        assert response.status_code == 200
    
    # Verify both are in trash with unique names
    trash_dir = temp_agents_dir / "_trash"
    trashed_items = [item.name for item in trash_dir.iterdir() if item.name.startswith("multi-delete")]
    assert len(trashed_items) == 2
    # Names should be different
    assert trashed_items[0] != trashed_items[1]
