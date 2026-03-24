"""Unit tests for the agent registry module."""

import json
from pathlib import Path

import pytest

from daemon.registry import AgentMetadata, AgentRegistry


@pytest.fixture
def temp_agents_dir(tmp_path: Path) -> Path:
    """Create a temporary agents directory for testing."""
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    return agents_dir


@pytest.fixture
def registry(temp_agents_dir: Path) -> AgentRegistry:
    """Create a registry instance with a temporary directory."""
    reg = AgentRegistry(temp_agents_dir)
    reg.discover()
    return reg


def create_agent_meta(agents_dir: Path, agent_id: str, **meta_overrides) -> None:
    """Helper to create an agent directory with meta.json."""
    agent_dir = agents_dir / agent_id
    agent_dir.mkdir()

    meta = {
        "id": agent_id,
        "name": agent_id.title(),
        "description": f"Test agent {agent_id}",
        "icon": "🤖",
        "color": "accent-blue",
        **meta_overrides,
    }

    with open(agent_dir / "meta.json", "w") as f:
        json.dump(meta, f)


class TestDiscoverAgents:
    """Tests for agent discovery."""

    def test_discover_agents(self, temp_agents_dir: Path) -> None:
        """Test basic agent discovery."""
        # Create test agents
        create_agent_meta(temp_agents_dir, "coder")
        create_agent_meta(temp_agents_dir, "reviewer")

        registry = AgentRegistry(temp_agents_dir)
        registry.discover()

        agents = registry.list_all()
        assert len(agents) == 2
        agent_ids = {a.id for a in agents}
        assert agent_ids == {"coder", "reviewer"}

    def test_discover_sorted_alphabetically(self, temp_agents_dir: Path) -> None:
        """Test that agents are discovered in alphabetical order."""
        create_agent_meta(temp_agents_dir, "zebra")
        create_agent_meta(temp_agents_dir, "alpha")
        create_agent_meta(temp_agents_dir, "middle")

        registry = AgentRegistry(temp_agents_dir)
        registry.discover()

        agents = registry.list_all()
        assert [a.id for a in agents] == ["alpha", "middle", "zebra"]

    def test_skip_hidden_dirs(self, temp_agents_dir: Path) -> None:
        """Test that hidden directories are skipped during discovery."""
        create_agent_meta(temp_agents_dir, "visible")
        (temp_agents_dir / ".hidden_agent").mkdir()
        (temp_agents_dir / ".hidden_agent" / "meta.json").write_text(
            json.dumps({"id": "hidden", "name": "Hidden"})
        )

        registry = AgentRegistry(temp_agents_dir)
        registry.discover()

        assert len(registry.list_all()) == 1
        assert registry.exists("visible")
        assert not registry.exists("hidden")

    def test_skip_special_dirs(self, temp_agents_dir: Path) -> None:
        """Test that _trash and _baby_template are skipped."""
        create_agent_meta(temp_agents_dir, "real_agent")

        # Create special directories
        for special in ["_trash", "_baby_template"]:
            dir_path = temp_agents_dir / special
            dir_path.mkdir()
            (dir_path / "meta.json").write_text(
                json.dumps({"id": special, "name": special})
            )

        registry = AgentRegistry(temp_agents_dir)
        registry.discover()

        assert len(registry.list_all()) == 1
        assert registry.exists("real_agent")
        assert not registry.exists("_trash")
        assert not registry.exists("_baby_template")

    def test_skip_directories_without_meta_json(self, temp_agents_dir: Path) -> None:
        """Test that directories without meta.json are skipped."""
        create_agent_meta(temp_agents_dir, "valid_agent")

        # Create directory without meta.json
        (temp_agents_dir / "no_meta").mkdir()

        registry = AgentRegistry(temp_agents_dir)
        registry.discover()

        assert len(registry.list_all()) == 1
        assert registry.exists("valid_agent")
        assert not registry.exists("no_meta")

    def test_missing_meta_json_logs_warning(self, temp_agents_dir: Path, caplog) -> None:
        """Test that missing meta.json logs a warning."""
        (temp_agents_dir / "no_meta").mkdir()

        registry = AgentRegistry(temp_agents_dir)

        with caplog.at_level("WARNING"):
            registry.discover()

        assert any("No meta.json found" in record.message for record in caplog.records)

    def test_malformed_json_logs_warning(self, temp_agents_dir: Path, caplog) -> None:
        """Test that malformed JSON logs a warning."""
        agent_dir = temp_agents_dir / "bad_json"
        agent_dir.mkdir()
        (agent_dir / "meta.json").write_text("{ invalid json }")

        registry = AgentRegistry(temp_agents_dir)

        with caplog.at_level("WARNING"):
            registry.discover()

        assert any("Failed to parse meta.json" in record.message for record in caplog.records)

    def test_missing_agents_dir(self, tmp_path: Path) -> None:
        """Test that missing agents directory is handled gracefully."""
        nonexistent = tmp_path / "does_not_exist"
        registry = AgentRegistry(nonexistent)
        registry.discover()

        assert len(registry.list_all()) == 0


class TestGetAgent:
    """Tests for getting agent by ID."""

    def test_get_by_id(self, registry: AgentRegistry, temp_agents_dir: Path) -> None:
        """Test getting an existing agent by ID."""
        create_agent_meta(
            temp_agents_dir, "coder", name="Coder", description="Writes code"
        )
        registry.discover()

        agent = registry.get("coder")
        assert agent is not None
        assert agent.id == "coder"
        assert agent.name == "Coder"
        assert agent.description == "Writes code"
        assert isinstance(agent.path, Path)

    def test_get_nonexistent(self, registry: AgentRegistry) -> None:
        """Test getting a non-existent agent returns None."""
        agent = registry.get("nonexistent")
        assert agent is None


class TestExists:
    """Tests for checking agent existence."""

    def test_exists_true(self, registry: AgentRegistry, temp_agents_dir: Path) -> None:
        """Test exists returns True for existing agent."""
        create_agent_meta(temp_agents_dir, "coder")
        registry.discover()

        assert registry.exists("coder") is True

    def test_exists_false(self, registry: AgentRegistry) -> None:
        """Test exists returns False for non-existent agent."""
        assert registry.exists("nonexistent") is False


class TestListAll:
    """Tests for listing all agents."""

    def test_list_all(self, registry: AgentRegistry, temp_agents_dir: Path) -> None:
        """Test listing all agents."""
        create_agent_meta(temp_agents_dir, "coder")
        create_agent_meta(temp_agents_dir, "reviewer")
        create_agent_meta(temp_agents_dir, "leader")
        registry.discover()

        agents = registry.list_all()
        assert len(agents) == 3
        agent_ids = {a.id for a in agents}
        assert agent_ids == {"coder", "reviewer", "leader"}

    def test_list_all_empty(self, registry: AgentRegistry) -> None:
        """Test listing when no agents exist."""
        agents = registry.list_all()
        assert agents == []


class TestResolveToId:
    """Tests for resolving paths to agent IDs."""

    def test_resolve_to_id_with_id(self, registry: AgentRegistry, temp_agents_dir: Path) -> None:
        """Test resolving a pure agent ID."""
        create_agent_meta(temp_agents_dir, "coder")
        registry.discover()

        assert registry.resolve_to_id("coder") == "coder"

    def test_resolve_to_id_with_relative_path(self, registry: AgentRegistry, temp_agents_dir: Path) -> None:
        """Test resolving agents/coder path format."""
        create_agent_meta(temp_agents_dir, "coder")
        registry.discover()

        assert registry.resolve_to_id("agents/coder") == "coder"

    def test_resolve_to_id_with_leading_dot_slash(self, registry: AgentRegistry, temp_agents_dir: Path) -> None:
        """Test resolving ./agents/coder path format."""
        create_agent_meta(temp_agents_dir, "coder")
        registry.discover()

        assert registry.resolve_to_id("./agents/coder") == "coder"

    def test_resolve_to_id_nonexistent(self, registry: AgentRegistry) -> None:
        """Test resolving a non-existent agent returns None."""
        assert registry.resolve_to_id("nonexistent") is None

    def test_resolve_to_id_with_absolute_path(self, registry: AgentRegistry, temp_agents_dir: Path) -> None:
        """Test resolving an absolute path to agent directory."""
        create_agent_meta(temp_agents_dir, "coder")
        registry.discover()

        abs_path = str(temp_agents_dir / "coder")
        assert registry.resolve_to_id(abs_path) == "coder"


class TestAgentMetadata:
    """Tests for AgentMetadata model."""

    def test_agent_metadata_defaults(self, temp_agents_dir: Path) -> None:
        """Test AgentMetadata default values."""
        meta = AgentMetadata(
            id="test",
            name="Test",
            path=temp_agents_dir / "test",
        )

        assert meta.description == ""
        assert meta.icon == "🤖"
        assert meta.color == "accent-blue"
        assert meta.version is None
        assert meta.system is False
        assert meta.capabilities == []
        assert meta.tags == []

    def test_agent_metadata_full(self, temp_agents_dir: Path) -> None:
        """Test AgentMetadata with all fields."""
        meta = AgentMetadata(
            id="coder",
            name="Coder",
            description="Writes code",
            icon="💻",
            color="accent-cyan",
            version="2.0.0",
            path=temp_agents_dir / "coder",
            system=True,
            capabilities=["code_generation", "refactoring"],
            tags=["development", "coding"],
        )

        assert meta.id == "coder"
        assert meta.name == "Coder"
        assert meta.description == "Writes code"
        assert meta.icon == "💻"
        assert meta.color == "accent-cyan"
        assert meta.version == "2.0.0"
        assert meta.path == temp_agents_dir / "coder"
        assert meta.system is True
        assert meta.capabilities == ["code_generation", "refactoring"]
        assert meta.tags == ["development", "coding"]

    def test_agent_metadata_path_conversion(self) -> None:
        """Test that path is converted to Path object."""
        meta = AgentMetadata(
            id="test",
            name="Test",
            path="/some/string/path",  # type: ignore - validator handles string conversion
        )

        assert isinstance(meta.path, Path)
        assert meta.path == Path("/some/string/path")


class TestAgentMetadataPath:
    """Tests for AgentMetadata path handling."""

    def test_path_resolved_from_string(self) -> None:
        """Test that string paths are converted to Path objects."""
        meta = AgentMetadata(
            id="test",
            name="Test",
            path="/tmp/agents/test",  # type: ignore - validator handles string conversion
        )
        assert meta.path == Path("/tmp/agents/test")

    def test_path_preserved_if_path_object(self) -> None:
        """Test that Path objects are preserved."""
        path = Path("/tmp/agents/test")
        meta = AgentMetadata(
            id="test",
            name="Test",
            path=path,
        )
        assert meta.path is path


class TestRegistryIntegration:
    """Integration tests for the registry."""

    def test_real_agent_format(self, temp_agents_dir: Path) -> None:
        """Test that registry handles real meta.json format."""
        # Create meta.json similar to actual agents in the project
        agent_dir = temp_agents_dir / "leader"
        agent_dir.mkdir()

        meta = {
            "id": "leader",
            "name": "Leader",
            "description": "Coordinates tasks and manages workflow delegation",
            "icon": "👑",
            "color": "accent-amber",
            "version": "1.0.0",
        }

        with open(agent_dir / "meta.json", "w") as f:
            json.dump(meta, f)

        registry = AgentRegistry(temp_agents_dir)
        registry.discover()

        agent = registry.get("leader")
        assert agent is not None
        assert agent.id == "leader"
        assert agent.name == "Leader"
        assert agent.description == "Coordinates tasks and manages workflow delegation"
        assert agent.icon == "👑"
        assert agent.color == "accent-amber"
        assert agent.version == "1.0.0"
        assert agent.system is False
