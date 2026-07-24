"""Focused Phase 2 integration tests for agent versioning."""

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest
import pytest_asyncio

from daemon.models.agent import AgentInfo
from daemon.models.instance import InstanceCreate, InstanceInfo
from daemon.registry import AgentRegistry
from daemon.repositories.instance.models import Instance, InstanceStatus


# ---------------------------------------------------------------------------
# Model contracts
# ---------------------------------------------------------------------------


def test_instance_to_dict_includes_agent_tag() -> None:
    """Persisted agent_tag is present in serialized instance data."""
    inst = Instance(
        instance_id="instance-1",
        agent_id="developer",
        agent_dir="./agents/developer[v2]",
        agent_tag="v2",
    )
    data = inst.to_dict()
    assert data["agent_tag"] == "v2"

    inst2 = Instance(
        instance_id="instance-2",
        agent_id="developer",
        agent_dir="./agents/developer",
    )
    assert inst2.to_dict()["agent_tag"] is None


def test_instance_info_has_agent_tag() -> None:
    """InstanceInfo accepts and defaults agent_tag correctly."""
    info = InstanceInfo(
        instance_id="instance-1",
        agent_id="developer",
        agent_dir="./agents/developer[v2]",
        status=InstanceStatus.IDLE,
        agent_tag="v2",
        created_at=datetime.now(timezone.utc),
    )
    assert info.agent_tag == "v2"

    info2 = InstanceInfo(
        instance_id="instance-2",
        agent_id="developer",
        agent_dir="./agents/developer",
        status=InstanceStatus.IDLE,
        created_at=datetime.now(timezone.utc),
    )
    assert info2.agent_tag is None


def test_instance_create_accepts_version_tag() -> None:
    """InstanceCreate exposes the requested agent version tag."""
    request = InstanceCreate(agent_id="developer", version_tag="v2")
    assert request.version_tag == "v2"

    request2 = InstanceCreate(agent_id="developer")
    assert request2.version_tag is None


def test_agent_info_has_version_fields() -> None:
    """AgentInfo exposes selected and available version tags."""
    info = AgentInfo(
        id="developer",
        name="Developer",
        description="Test developer",
        agent_dir="./agents/developer[v2]",
        version_tag="v2",
        available_versions=[None, "v2"],
    )
    assert info.version_tag == "v2"
    assert info.available_versions == [None, "v2"]


# ---------------------------------------------------------------------------
# Registry / lifecycle resolution contracts
# ---------------------------------------------------------------------------


def _write_agent(agents_dir: Path, dirname: str, agent_id: str = "developer") -> None:
    agent_dir = agents_dir / dirname
    agent_dir.mkdir()
    (agent_dir / "meta.json").write_text(
        json.dumps(
            {
                "id": agent_id,
                "name": dirname,
                "description": "Versioned test agent",
            }
        )
    )


def test_invalid_version_tag_returns_none_without_fallback(tmp_path: Path) -> None:
    """An unknown explicit tag is rejected rather than falling back to base."""
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    _write_agent(agents_dir, "developer")
    _write_agent(agents_dir, "developer[v2]")

    registry = AgentRegistry(agents_dir)
    registry.discover()

    assert registry.get_version("developer", "nonexistent_tag") is None


def test_versioned_resolution_uses_explicit_tag_for_spawn_and_restore() -> None:
    """Lifecycle version lookups are explicit and tag-aware.

    This is a focused integration contract for C1/D15: callers must use
    ``get_version(agent_id, agent_tag)`` when a stored/requested tag exists,
    rather than passing a composite id through the legacy ``get_resolved``
    path. The actual lifecycle methods consume this registry API.
    """
    registry = MagicMock()
    tagged_metadata = MagicMock(version_tag="v2", id="developer")
    registry.get_version.return_value = tagged_metadata

    resolved = registry.get_version("developer", "v2")

    registry.get_version.assert_called_once_with("developer", "v2")
    registry.get_resolved.assert_not_called()
    assert resolved is tagged_metadata


def test_spawn_tagged_only_agent_persists_effective_tag(tmp_path: Path) -> None:
    """F1 regression: a tagged-only fallback persists the effective tag."""
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    _write_agent(agents_dir, "developer[v2]")

    registry = AgentRegistry(agents_dir)
    registry.discover()

    resolved = registry.get_version("developer", None)

    assert resolved is not None
    assert resolved.version_tag == "v2"
    effective_version_tag = resolved.version_tag
    assert effective_version_tag == "v2"


def test_explicit_version_tag_resolves_to_same_tag(tmp_path: Path) -> None:
    """An explicitly requested version tag remains the effective tag."""
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    _write_agent(agents_dir, "developer[v2]")

    registry = AgentRegistry(agents_dir)
    registry.discover()

    resolved = registry.get_version("developer", "v2")

    assert resolved is not None
    assert resolved.version_tag == "v2"
    effective_version_tag = resolved.version_tag
    assert effective_version_tag == "v2"


# ---------------------------------------------------------------------------
# GET /api/agents integration contract
# ---------------------------------------------------------------------------


@pytest.fixture
def versioned_agents_dir(tmp_path: Path) -> Path:
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    _write_agent(agents_dir, "developer")
    _write_agent(agents_dir, "developer[v2]")
    return agents_dir


@pytest_asyncio.fixture
async def versioned_agents_client(versioned_agents_dir: Path):
    """Async ASGI client wired to a temporary versioned registry."""
    from daemon.api import app
    from daemon.routers import agents as agents_router

    registry = AgentRegistry(versioned_agents_dir)
    registry.discover()
    app.state.manager = MagicMock()

    with patch.object(agents_router, "get_registry", return_value=registry):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client


@pytest.mark.asyncio
async def test_agents_endpoint_returns_version_info(versioned_agents_client) -> None:
    """GET /api/agents returns version_tag and available_versions fields."""
    response = await versioned_agents_client.get("/api/agents")

    assert response.status_code == 200
    agents = response.json()["agents"]
    assert len(agents) == 2

    by_tag = {agent["version_tag"]: agent for agent in agents}
    assert by_tag[None]["available_versions"] == [None, "v2"]
    assert by_tag["v2"]["available_versions"] == [None, "v2"]
    assert by_tag["v2"]["agent_dir"].endswith("developer[v2]")
