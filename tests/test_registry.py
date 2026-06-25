"""Unit tests for the agent registry module."""

import json
from pathlib import Path

import pytest

from daemon.registry import AgentMetadata, AgentRegistry, get_registry


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
        create_agent_meta(temp_agents_dir, "developer")
        create_agent_meta(temp_agents_dir, "reviewer")

        registry = AgentRegistry(temp_agents_dir)
        registry.discover()

        agents = registry.list_all()
        assert len(agents) == 2
        agent_ids = {a.id for a in agents}
        assert agent_ids == {"developer", "reviewer"}

    def test_discover_sorted_alphabetically(self, temp_agents_dir: Path) -> None:
        """Test that agents are discovered in alphabetical order."""
        create_agent_meta(temp_agents_dir, "zebra")
        create_agent_meta(temp_agents_dir, "alpha")
        create_agent_meta(temp_agents_dir, "middle")

        registry = AgentRegistry(temp_agents_dir)
        registry.discover()

        agents = registry.list_all()
        assert [a.id for a in agents] == ["alpha", "middle", "zebra"]

    def test_discover_with_innate_skills(self, temp_agents_dir: Path) -> None:
        """Test that innate_skills are properly loaded from meta.json."""
        # Create agent with innate_skills using helper
        create_agent_meta(
            temp_agents_dir, "developer",
            name="Developer",
            description="Test developer",
            innate_skills=["coding", "reviewing"],
        )

        registry = AgentRegistry(temp_agents_dir)
        registry.discover()

        agent = registry.get("developer")
        assert agent is not None
        assert agent.innate_skills == ["coding", "reviewing"]

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

    def test_symlink_directories_skipped(self, temp_agents_dir: Path) -> None:
        """Symlink directories should be skipped for security."""
        # Create real agent
        create_agent_meta(temp_agents_dir, "real_agent")

        # Create symlink to agent
        symlink_agent = temp_agents_dir / "symlink_agent"
        symlink_agent.symlink_to(temp_agents_dir / "real_agent")

        registry = AgentRegistry(temp_agents_dir)
        registry.discover()

        # Should only find real agent, not symlink
        assert registry.exists("real_agent")
        assert not registry.exists("symlink_agent")

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
            temp_agents_dir, "developer", name="Developer", description="Writes code"
        )
        registry.discover()

        agent = registry.get("developer")
        assert agent is not None
        assert agent.id == "developer"
        assert agent.name == "Developer"
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
        create_agent_meta(temp_agents_dir, "developer")
        registry.discover()

        assert registry.exists("developer") is True

    def test_exists_false(self, registry: AgentRegistry) -> None:
        """Test exists returns False for non-existent agent."""
        assert registry.exists("nonexistent") is False


class TestListAll:
    """Tests for listing all agents."""

    def test_list_all(self, registry: AgentRegistry, temp_agents_dir: Path) -> None:
        """Test listing all agents."""
        create_agent_meta(temp_agents_dir, "developer")
        create_agent_meta(temp_agents_dir, "reviewer")
        create_agent_meta(temp_agents_dir, "leader")
        registry.discover()

        agents = registry.list_all()
        assert len(agents) == 3
        agent_ids = {a.id for a in agents}
        assert agent_ids == {"developer", "reviewer", "leader"}

    def test_list_all_empty(self, registry: AgentRegistry) -> None:
        """Test listing when no agents exist."""
        agents = registry.list_all()
        assert agents == []


class TestResolveToId:
    """Tests for resolving paths to agent IDs."""

    def test_resolve_to_id_with_id(self, registry: AgentRegistry, temp_agents_dir: Path) -> None:
        """Test resolving a pure agent ID."""
        create_agent_meta(temp_agents_dir, "developer")
        registry.discover()

        assert registry.resolve_to_id("developer") == "developer"

    def test_resolve_to_id_with_relative_path(self, registry: AgentRegistry, temp_agents_dir: Path) -> None:
        """Test resolving agents/developer path format."""
        create_agent_meta(temp_agents_dir, "developer")
        registry.discover()

        assert registry.resolve_to_id("agents/developer") == "developer"

    def test_resolve_to_id_with_leading_dot_slash(self, registry: AgentRegistry, temp_agents_dir: Path) -> None:
        """Test resolving ./agents/developer path format."""
        create_agent_meta(temp_agents_dir, "developer")
        registry.discover()

        assert registry.resolve_to_id("./agents/developer") == "developer"

    def test_resolve_to_id_nonexistent(self, registry: AgentRegistry) -> None:
        """Test resolving a non-existent agent returns None."""
        assert registry.resolve_to_id("nonexistent") is None

    def test_resolve_to_id_with_absolute_path(self, registry: AgentRegistry, temp_agents_dir: Path) -> None:
        """Test resolving an absolute path to agent directory."""
        create_agent_meta(temp_agents_dir, "developer")
        registry.discover()

        abs_path = str(temp_agents_dir / "developer")
        assert registry.resolve_to_id(abs_path) == "developer"

    def test_resolve_to_id_empty_string(self) -> None:
        """Empty string should return None."""
        registry = AgentRegistry(Path("/tmp/test_agents"))
        registry.discover()
        assert registry.resolve_to_id("") is None

    def test_path_traversal_blocked(self) -> None:
        """Path traversal attempts should return None."""
        registry = AgentRegistry(Path("/tmp/test_agents"))
        registry.discover()
        # Try to escape agents directory
        assert registry.resolve_to_id("../../../etc/passwd") is None
        assert registry.resolve_to_id("../../daemon/config.py") is None
        assert registry.resolve_to_id("../_trash/evil") is None

    def test_resolve_to_id_with_absolute_path_outside_agents(self, tmp_path: Path) -> None:
        """Absolute path outside agents dir should return None."""
        registry = AgentRegistry(tmp_path / "agents")
        registry.discover()
        # Create a file outside agents dir
        outside_dir = tmp_path / "outside"
        outside_dir.mkdir()
        (outside_dir / "meta.json").write_text('{"name": "Evil"}')
        assert registry.resolve_to_id(str(outside_dir)) is None


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
        assert meta.innate_skills == []

    def test_agent_metadata_full(self, temp_agents_dir: Path) -> None:
        """Test AgentMetadata with all fields."""
        meta = AgentMetadata(
            id="developer",
            name="Developer",
            description="Writes code",
            icon="💻",
            color="accent-cyan",
            version="2.0.0",
            path=temp_agents_dir / "developer",
            system=True,
            capabilities=["code_generation", "refactoring"],
            tags=["development", "coding"],
            innate_skills=["coding", "reviewing"],
        )

        assert meta.id == "developer"
        assert meta.name == "Developer"
        assert meta.description == "Writes code"
        assert meta.icon == "💻"
        assert meta.color == "accent-cyan"
        assert meta.version == "2.0.0"
        assert meta.path == temp_agents_dir / "developer"
        assert meta.system is True
        assert meta.capabilities == ["code_generation", "refactoring"]
        assert meta.tags == ["development", "coding"]
        assert meta.innate_skills == ["coding", "reviewing"]

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


class TestValidateToolConfigs:
    """Tests for tool config validation."""

    def _setup_mock_tools(self, monkeypatch) -> None:
        """Set up mock tool registry with known categories and tools."""
        from daemon.tools import _tool_registry
        
        # Clear and set up mock data
        _tool_registry._tool_metadata.clear()
        _tool_registry._tool_metadata.update({
            "bash": {"category": "bash", "short_doc": "Run bash"},
            "read_file": {"category": "filesystem", "short_doc": "Read file"},
            "write_file": {"category": "filesystem", "short_doc": "Write file"},
            "spawn_instance": {"category": "instance", "short_doc": "Spawn"},
        })
        _tool_registry._full_docs.clear()

    def _create_agent_with_tools(self, agents_dir: Path, agent_id: str, tools_config: dict | None) -> None:
        """Helper to create an agent with tools config."""
        agent_dir = agents_dir / agent_id
        agent_dir.mkdir()
        
        meta = {
            "id": agent_id,
            "name": agent_id.title(),
            "description": f"Test agent {agent_id}",
            **({"tools": tools_config} if tools_config is not None else {}),
        }
        
        with open(agent_dir / "meta.json", "w") as f:
            json.dump(meta, f)

    def test_valid_config_no_warnings(self, temp_agents_dir: Path, monkeypatch) -> None:
        """Test that valid configs produce no warnings."""
        self._setup_mock_tools(monkeypatch)
        
        # Create agent with valid tool config (known category and tool)
        self._create_agent_with_tools(
            temp_agents_dir, "test_agent",
            {"allow": ["bash", "filesystem"], "deny": []}
        )
        
        registry = AgentRegistry(temp_agents_dir)
        registry.discover()
        
        warnings = registry.validate_tool_configs()
        assert warnings == []

    def test_none_tools_config_no_warnings(self, temp_agents_dir: Path, monkeypatch) -> None:
        """Test that None tools config (no restrictions) produces no warnings."""
        self._setup_mock_tools(monkeypatch)
        
        # Create agent without tools config
        self._create_agent_with_tools(temp_agents_dir, "test_agent", None)
        
        registry = AgentRegistry(temp_agents_dir)
        registry.discover()
        
        warnings = registry.validate_tool_configs()
        assert warnings == []

    def test_unknown_category_in_allow_warning(self, temp_agents_dir: Path, monkeypatch) -> None:
        """Test that unknown category in allow list produces a warning."""
        self._setup_mock_tools(monkeypatch)
        
        self._create_agent_with_tools(
            temp_agents_dir, "test_agent",
            {"allow": ["unknown_category", "bash"], "deny": []}
        )
        
        registry = AgentRegistry(temp_agents_dir)
        registry.discover()
        
        warnings = registry.validate_tool_configs()
        assert len(warnings) == 1
        assert "unknown_category" in warnings[0]
        assert "allow" in warnings[0]
        assert "test_agent" in warnings[0]

    def test_unknown_tool_in_deny_warning(self, temp_agents_dir: Path, monkeypatch) -> None:
        """Test that unknown tool name in deny list produces a warning."""
        self._setup_mock_tools(monkeypatch)
        
        self._create_agent_with_tools(
            temp_agents_dir, "test_agent",
            {"allow": ["bash"], "deny": ["nonexistent_tool"]}
        )
        
        registry = AgentRegistry(temp_agents_dir)
        registry.discover()
        
        warnings = registry.validate_tool_configs()
        assert len(warnings) == 1
        assert "nonexistent_tool" in warnings[0]
        assert "deny" in warnings[0]
        assert "test_agent" in warnings[0]

    def test_zero_tools_result_warning(self, temp_agents_dir: Path, monkeypatch) -> None:
        """Test that a config resulting in zero tools produces a warning."""
        self._setup_mock_tools(monkeypatch)
        
        # deny all known tools - should result in zero
        self._create_agent_with_tools(
            temp_agents_dir, "test_agent",
            {"allow": ["bash"], "deny": ["bash"]}
        )
        
        registry = AgentRegistry(temp_agents_dir)
        registry.discover()
        
        warnings = registry.validate_tool_configs()
        assert len(warnings) == 1
        assert "ZERO available tools" in warnings[0]
        assert "test_agent" in warnings[0]

    def test_multiple_warnings_for_multiple_agents(self, temp_agents_dir: Path, monkeypatch) -> None:
        """Test that multiple warnings are collected for multiple agents."""
        self._setup_mock_tools(monkeypatch)
        
        # Agent 1: unknown allow entry
        self._create_agent_with_tools(
            temp_agents_dir, "agent1",
            {"allow": ["bad_category"], "deny": []}
        )
        # Agent 2: unknown deny entry
        self._create_agent_with_tools(
            temp_agents_dir, "agent2",
            {"allow": ["bash"], "deny": ["bad_tool"]}
        )
        # Agent 3: valid config
        self._create_agent_with_tools(
            temp_agents_dir, "agent3",
            {"allow": ["bash", "filesystem"], "deny": []}
        )
        
        registry = AgentRegistry(temp_agents_dir)
        registry.discover()
        
        warnings = registry.validate_tool_configs()
        assert len(warnings) == 2
        warning_text = " ".join(warnings)
        assert "agent1" in warning_text
        assert "agent2" in warning_text
        assert "agent3" not in warning_text

    def test_valid_tool_name_in_allow_no_warning(self, temp_agents_dir: Path, monkeypatch) -> None:
        """Test that valid individual tool names in allow don't produce warnings."""
        self._setup_mock_tools(monkeypatch)
        
        self._create_agent_with_tools(
            temp_agents_dir, "test_agent",
            {"allow": ["read_file", "write_file"], "deny": []}
        )
        
        registry = AgentRegistry(temp_agents_dir)
        registry.discover()
        
        warnings = registry.validate_tool_configs()
        assert warnings == []

    def test_empty_allow_deny_no_warning(self, temp_agents_dir: Path, monkeypatch) -> None:
        """Test that empty allow/deny lists don't produce warnings."""
        self._setup_mock_tools(monkeypatch)
        
        self._create_agent_with_tools(
            temp_agents_dir, "test_agent",
            {"allow": [], "deny": []}
        )
        
        registry = AgentRegistry(temp_agents_dir)
        registry.discover()
        
        warnings = registry.validate_tool_configs()
        assert warnings == []


class TestLLMModelParsing:
    """Tests for per-agent LLM model override parsing."""

    def test_llm_model_defaults_to_none(self, temp_agents_dir: Path) -> None:
        """Test that llm_model defaults to None when not specified in meta.json."""
        create_agent_meta(temp_agents_dir, "test_agent")
        
        registry = AgentRegistry(temp_agents_dir)
        registry.discover()
        
        agent = registry.get("test_agent")
        assert agent is not None
        assert agent.llm_model is None

    def test_llm_model_parsed_from_meta_json(self, temp_agents_dir: Path) -> None:
        """Test that llm_model is correctly parsed from meta.json."""
        create_agent_meta(temp_agents_dir, "custom_agent", llm_model="gpt-4o-mini")
        
        registry = AgentRegistry(temp_agents_dir)
        registry.discover()
        
        agent = registry.get("custom_agent")
        assert agent is not None
        assert agent.llm_model == "gpt-4o-mini"

    def test_llm_model_whitespace_only_loaded_as_is(self, temp_agents_dir: Path) -> None:
        """Test that whitespace-only llm_model is loaded as-is.

        Validation (empty after strip) is handled downstream.
        """
        create_agent_meta(temp_agents_dir, "whitespace_agent", llm_model="  ")

        registry = AgentRegistry(temp_agents_dir)
        registry.discover()

        agent = registry.get("whitespace_agent")
        assert agent is not None
        assert agent.llm_model == "  "


class TestAgentIdAliasBackwardCompatibility:
    """Tests for the AGENT_ID_ALIASES backward-compatibility layer.

    These tests guard against regressions when an agent_id is renamed
    (e.g. ``coder`` → ``developer``). Old database rows, persisted
    agent_ids, and external API consumers may still reference the old
    id; the registry must transparently resolve the alias to the new
    canonical id.
    """

    def test_resolve_pure_id_alias(self) -> None:
        """resolve_pure_id('coder') returns 'developer' via alias."""
        registry = get_registry()
        result = registry.resolve_pure_id("coder")
        assert result == "developer"

    def test_resolve_path_to_id_alias(self) -> None:
        """resolve_path_to_id('./agents/coder') returns 'developer' via alias."""
        registry = get_registry()
        result = registry.resolve_path_to_id("./agents/coder")
        assert result == "developer"

    def test_exists_alias(self) -> None:
        """exists('coder') returns True via alias."""
        registry = get_registry()
        assert registry.exists("coder") is True

    def test_get_resolved_alias(self) -> None:
        """get_resolved('coder') returns the canonical developer metadata via alias."""
        registry = get_registry()
        resolved = registry.get_resolved("coder")
        assert resolved is not None
        assert resolved.id == "developer"

    def test_get_resolved_canonical(self) -> None:
        """get_resolved('developer') returns the same metadata as get('developer')."""
        registry = get_registry()
        assert registry.get_resolved("developer") == registry.get("developer")

    def test_get_resolved_unknown_returns_none(self) -> None:
        """get_resolved for an unknown id returns None (alias-aware)."""
        registry = get_registry()
        assert registry.get_resolved("definitely-not-an-agent") is None
        # Aliases to a non-existent canonical also resolve to None.
        assert registry.get_resolved("ghost-alias") is None

    def test_instance_create_normalizes_alias(self) -> None:
        """InstanceCreate(agent_id='coder') normalizes to 'developer'."""
        from daemon.models.instance import InstanceCreate

        instance = InstanceCreate(agent_id="coder")
        assert instance.agent_id == "developer"
