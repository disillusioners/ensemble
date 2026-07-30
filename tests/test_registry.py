"""Unit tests for the agent registry module."""

import json
from pathlib import Path

import pytest

from daemon.registry import AgentMetadata, AgentRegistry, _parse_agent_dir_name, get_registry


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

    def test_discover_non_dict_caller_model_overrides_loaded_as_empty(
        self, temp_agents_dir: Path, caplog
    ) -> None:
        """A non-dict caller_model_overrides (e.g. admin typo: a string
        instead of a map) must gracefully resolve to an empty dict instead
        of failing Pydantic validation and making the agent disappear."""
        import logging

        # Admin typo: caller_model_overrides set to a plain string instead
        # of the expected dict[str, str | None] map.
        create_agent_meta(
            temp_agents_dir, "typo_agent", caller_model_overrides="coder"
        )

        registry = AgentRegistry(temp_agents_dir)
        with caplog.at_level(logging.WARNING, logger="daemon.registry"):
            registry.discover()

        # Agent must still load cleanly — it must NOT disappear from the
        # registry due to a broad-except that swallowed the ValidationError.
        agent = registry.get("typo_agent")
        assert agent is not None
        assert agent.caller_model_overrides == {}

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


class TestAgentVersioning:
    """Regression tests for Phase 1 agent versioning.

    Directory-name ``[tag]`` suffix support: tagged variants are stored in
    a separate ``_versioned_agents`` dict keyed by composite
    ``"{base}[{tag}]"`` strings. Base agents live in ``_agents`` with
    plain keys. The D16 lookup family ignores tagged-only entries so
    legacy callers cannot accidentally resolve a composite key.
    """

    # -------------------- _parse_agent_dir_name --------------------

    @pytest.mark.parametrize(
        "dir_name, expected_base, expected_tag",
        [
            ("developer", "developer", None),
            ("developer[v2]", "developer", "v2"),
            ("developer[test_version]", "developer", "test_version"),
            ("developer[v-1]", "developer", "v-1"),
            ("developer[V_2]", "developer", "V_2"),
            ("a[1]", "a", "1"),
        ],
    )
    def test_parse_agent_dir_name_valid_tags(
        self, dir_name: str, expected_base: str, expected_tag: str | None
    ) -> None:
        assert _parse_agent_dir_name(dir_name) == (expected_base, expected_tag)

    @pytest.mark.parametrize(
        "dir_name",
        [
            "dev[[v2]]",            # nested brackets → no match
            "developer[v2][v3]",    # chained brackets → no match (regression)
            "dev[../etc]",          # path chars (slash) → no match
            "dev[v2]/etc",          # path separator → no match
            "dev[.v2]",             # dot char → no match
            "dev[v2 ",              # missing closing bracket → no match
            "dev v2]",              # missing opening bracket → no match
            "dev[]",                # empty tag → no match
        ],
    )
    def test_parse_agent_dir_name_rejects_path_and_nested_brackets(
        self, dir_name: str
    ) -> None:
        base, tag = _parse_agent_dir_name(dir_name)
        assert tag is None
        assert base == dir_name

    # -------------------- AgentMetadata default --------------------

    def test_agent_metadata_version_tag_defaults_to_none(
        self, temp_agents_dir: Path
    ) -> None:
        meta = AgentMetadata(
            id="test",
            name="Test",
            path=temp_agents_dir / "test",
        )
        assert meta.version_tag is None

    # -------------------- Storage layout --------------------

    @staticmethod
    def _create_agent_dir(agents_dir: Path, dir_name: str, meta: dict | None = None) -> Path:
        """Create an agent directory with a (possibly tagged) name and meta.json."""
        agent_dir = agents_dir / dir_name
        agent_dir.mkdir()
        payload = meta if meta is not None else {"id": dir_name, "name": dir_name}
        with open(agent_dir / "meta.json", "w") as f:
            json.dump(payload, f)
        return agent_dir

    def test_base_and_tagged_stored_in_separate_dicts(
        self, temp_agents_dir: Path
    ) -> None:
        self._create_agent_dir(temp_agents_dir, "developer", {"id": "developer"})
        self._create_agent_dir(temp_agents_dir, "developer[v2]", {"id": "developer"})
        self._create_agent_dir(temp_agents_dir, "reviewer", {"id": "reviewer"})
        self._create_agent_dir(temp_agents_dir, "reviewer[v1]", {"id": "reviewer"})

        reg = AgentRegistry(temp_agents_dir)
        reg.discover()

        # _agents: plain keys only, never composite.
        assert set(reg._agents.keys()) == {"developer", "reviewer"}
        # _versioned_agents: composite keys only.
        assert set(reg._versioned_agents.keys()) == {"developer[v2]", "reviewer[v1]"}
        # The two dicts never overlap.
        assert set(reg._agents.keys()) & set(reg._versioned_agents.keys()) == set()

    def test_versions_dict_records_base_and_tags_deduplicated(
        self, temp_agents_dir: Path
    ) -> None:
        self._create_agent_dir(temp_agents_dir, "developer", {"id": "developer"})
        self._create_agent_dir(temp_agents_dir, "developer[v2]", {"id": "developer"})
        self._create_agent_dir(temp_agents_dir, "developer[v1]", {"id": "developer"})

        reg = AgentRegistry(temp_agents_dir)
        reg.discover()

        versions = reg._versions["developer"]
        # All entries recorded once (no duplicates).
        assert len(versions) == len(set(versions))
        assert set(versions) == {None, "v1", "v2"}

    # -------------------- get_version --------------------

    def test_get_version_exact_tagged_lookup(self, temp_agents_dir: Path) -> None:
        self._create_agent_dir(temp_agents_dir, "developer", {"id": "developer"})
        self._create_agent_dir(temp_agents_dir, "developer[v2]", {"id": "developer"})

        reg = AgentRegistry(temp_agents_dir)
        reg.discover()

        v2 = reg.get_version("developer", "v2")
        assert v2 is not None
        assert v2.version_tag == "v2"

    def test_get_version_missing_tag_returns_none_no_fallback(
        self, temp_agents_dir: Path
    ) -> None:
        """Explicit tag lookup must NOT fall back to base or other tags."""
        self._create_agent_dir(temp_agents_dir, "developer", {"id": "developer"})
        self._create_agent_dir(temp_agents_dir, "developer[v2]", {"id": "developer"})

        reg = AgentRegistry(temp_agents_dir)
        reg.discover()

        # None when the specific tag is unknown — no lexicographic fallback.
        assert reg.get_version("developer", "v9") is None

    def test_get_version_prefers_base_when_no_tag_given(
        self, temp_agents_dir: Path
    ) -> None:
        self._create_agent_dir(temp_agents_dir, "developer", {"id": "developer"})
        self._create_agent_dir(temp_agents_dir, "developer[v2]", {"id": "developer"})

        reg = AgentRegistry(temp_agents_dir)
        reg.discover()

        meta = reg.get_version("developer")
        assert meta is not None
        assert meta.version_tag is None

    def test_get_version_tagged_only_fallback_is_lex_first(
        self, temp_agents_dir: Path
    ) -> None:
        """When only tagged versions exist, lex-first tagged version is returned."""
        # No base "developer" directory — only tagged variants.
        self._create_agent_dir(temp_agents_dir, "developer[v9]", {"id": "developer"})
        self._create_agent_dir(temp_agents_dir, "developer[v2]", {"id": "developer"})
        self._create_agent_dir(temp_agents_dir, "developer[v10]", {"id": "developer"})

        reg = AgentRegistry(temp_agents_dir)
        reg.discover()

        meta = reg.get_version("developer")
        assert meta is not None
        # Lexicographically smallest tag is "v10" (v1 < v2 < v9).
        assert meta.version_tag == "v10"

    def test_get_version_unknown_base_returns_none(
        self, temp_agents_dir: Path
    ) -> None:
        reg = AgentRegistry(temp_agents_dir)
        reg.discover()
        assert reg.get_version("ghost") is None
        assert reg.get_version("ghost", "v1") is None

    # -------------------- list_versions --------------------

    def test_list_versions_returns_base_and_tags(self, temp_agents_dir: Path) -> None:
        self._create_agent_dir(temp_agents_dir, "developer", {"id": "developer"})
        self._create_agent_dir(temp_agents_dir, "developer[v2]", {"id": "developer"})
        self._create_agent_dir(temp_agents_dir, "developer[v1]", {"id": "developer"})

        reg = AgentRegistry(temp_agents_dir)
        reg.discover()

        versions = reg.list_versions("developer")
        # All versions present (None for base), no duplicates.
        assert set(versions) == {None, "v1", "v2"}
        assert len(versions) == 3

    def test_list_versions_unknown_base_empty(self, temp_agents_dir: Path) -> None:
        reg = AgentRegistry(temp_agents_dir)
        reg.discover()
        assert reg.list_versions("ghost") == []

    # -------------------- list_all_grouped --------------------

    def test_list_all_grouped_groups_by_base_id(self, temp_agents_dir: Path) -> None:
        self._create_agent_dir(temp_agents_dir, "developer", {"id": "developer"})
        self._create_agent_dir(temp_agents_dir, "developer[v2]", {"id": "developer"})
        self._create_agent_dir(temp_agents_dir, "developer[v1]", {"id": "developer"})
        self._create_agent_dir(temp_agents_dir, "reviewer", {"id": "reviewer"})

        reg = AgentRegistry(temp_agents_dir)
        reg.discover()

        grouped = reg.list_all_grouped()
        assert set(grouped.keys()) == {"developer", "reviewer"}
        # All three developer variants are grouped under "developer".
        dev_paths = {m.path.name for m in grouped["developer"]}
        assert dev_paths == {"developer", "developer[v2]", "developer[v1]"}
        assert len(grouped["reviewer"]) == 1
        assert grouped["reviewer"][0].path.name == "reviewer"

    # -------------------- D16 methods ignore tagged-only entries --------------------

    def test_get_ignores_composite_key(self, temp_agents_dir: Path) -> None:
        self._create_agent_dir(temp_agents_dir, "developer[v2]", {"id": "developer"})
        reg = AgentRegistry(temp_agents_dir)
        reg.discover()
        assert reg.get("developer[v2]") is None

    def test_get_resolved_composite_key_returns_none(
        self, temp_agents_dir: Path
    ) -> None:
        """W1 regression: get_resolved must NOT resolve composite keys.

        D16 keystone invariant: ``get_resolved`` is the canonical-id lookup
        used by legacy spawn/restore paths. It must ignore composite keys
        like ``"developer[v2]"`` and return ``None`` — otherwise callers
        would accidentally load a tagged prompt while believing they had
        the base agent.
        """
        # Build a registry that contains BOTH base and tagged variants so
        # the test would fail if get_resolved fell through to a more
        # permissive lookup path.
        self._create_agent_dir(temp_agents_dir, "developer", {"id": "developer"})
        self._create_agent_dir(temp_agents_dir, "developer[v2]", {"id": "developer"})

        reg = AgentRegistry(temp_agents_dir)
        reg.discover()

        # Composite key → None (no fallback to base, no fallback to tagged).
        assert reg.get_resolved("developer[v2]") is None

        # Sanity: base path still resolves correctly — proves the test
        # didn't accidentally break resolution for valid ids.
        base = reg.get_resolved("developer")
        assert base is not None
        assert base.id == "developer"
        assert base.version_tag is None

    def test_get_resolved_composite_key_returns_none_tagged_only(
        self, temp_agents_dir: Path
    ) -> None:
        """Even with no base agent present, a composite key must return None
        from get_resolved — never auto-resolve to a tagged variant."""
        # Only tagged variant, no base "developer".
        self._create_agent_dir(temp_agents_dir, "developer[v2]", {"id": "developer"})

        reg = AgentRegistry(temp_agents_dir)
        reg.discover()

        # Composite key still None — get_resolved never accepts [tag] suffixes.
        assert reg.get_resolved("developer[v2]") is None

    def test_resolve_pure_id_ignores_composite_key(self, temp_agents_dir: Path) -> None:
        self._create_agent_dir(temp_agents_dir, "developer[v2]", {"id": "developer"})
        reg = AgentRegistry(temp_agents_dir)
        reg.discover()
        assert reg.resolve_pure_id("developer[v2]") is None

    def test_resolve_to_id_ignores_composite_key(self, temp_agents_dir: Path) -> None:
        self._create_agent_dir(temp_agents_dir, "developer[v2]", {"id": "developer"})
        reg = AgentRegistry(temp_agents_dir)
        reg.discover()
        assert reg.resolve_to_id("developer[v2]") is None

    def test_resolve_path_to_id_ignores_tagged_dir_path(
        self, temp_agents_dir: Path
    ) -> None:
        self._create_agent_dir(temp_agents_dir, "developer[v2]", {"id": "developer"})
        reg = AgentRegistry(temp_agents_dir)
        reg.discover()
        # Tagged dir should not be discoverable via D16 path lookups.
        assert reg.resolve_path_to_id("./agents/developer[v2]") is None
        assert reg.resolve_path_to_id("agents/developer[v2]") is None
        assert reg.resolve_path_to_id(str(temp_agents_dir / "developer[v2]")) is None

    def test_list_all_excludes_tagged_versions(self, temp_agents_dir: Path) -> None:
        self._create_agent_dir(temp_agents_dir, "developer", {"id": "developer"})
        self._create_agent_dir(temp_agents_dir, "developer[v2]", {"id": "developer"})
        self._create_agent_dir(temp_agents_dir, "reviewer", {"id": "reviewer"})

        reg = AgentRegistry(temp_agents_dir)
        reg.discover()

        ids = {a.id for a in reg.list_all()}
        assert ids == {"developer", "reviewer"}

    def test_exists_ignores_composite_key(self, temp_agents_dir: Path) -> None:
        self._create_agent_dir(temp_agents_dir, "developer[v2]", {"id": "developer"})
        reg = AgentRegistry(temp_agents_dir)
        reg.discover()
        assert reg.exists("developer[v2]") is False

    def test_d16_methods_return_for_base_when_tagged_present(
        self, temp_agents_dir: Path
    ) -> None:
        """When base and tagged both exist, plain id still resolves to base."""
        self._create_agent_dir(temp_agents_dir, "developer", {"id": "developer"})
        self._create_agent_dir(temp_agents_dir, "developer[v2]", {"id": "developer"})
        reg = AgentRegistry(temp_agents_dir)
        reg.discover()
        assert reg.exists("developer") is True
        assert reg.resolve_pure_id("developer") == "developer"
        meta = reg.get("developer")
        assert meta is not None
        assert meta.version_tag is None

    # -------------------- find_skill --------------------

    def test_find_skill_returns_base_ids_for_tagged_versions(
        self, temp_agents_dir: Path
    ) -> None:
        """find_skill must report base ids (never composite) and dedup."""
        # Base developer with a skill.
        self._create_agent_dir(temp_agents_dir, "developer", {"id": "developer"})
        (temp_agents_dir / "developer" / "skills" / "coding").mkdir(parents=True)
        (temp_agents_dir / "developer" / "skills" / "coding" / "skill.md").write_text("# coding")

        # Tagged developer[v2] also has the same skill — should NOT duplicate.
        self._create_agent_dir(temp_agents_dir, "developer[v2]", {"id": "developer"})
        (temp_agents_dir / "developer[v2]" / "skills" / "coding").mkdir(parents=True)
        (temp_agents_dir / "developer[v2]" / "skills" / "coding" / "skill.md").write_text("# coding")

        # Tagged-only reviewer[v1] with the skill — must report base id "reviewer".
        self._create_agent_dir(temp_agents_dir, "reviewer[v1]", {"id": "reviewer"})
        (temp_agents_dir / "reviewer[v1]" / "skills" / "coding").mkdir(parents=True)
        (temp_agents_dir / "reviewer[v1]" / "skills" / "coding" / "skill.md").write_text("# coding")

        reg = AgentRegistry(temp_agents_dir)
        reg.discover()

        result = reg.find_skill("coding")
        # Both base ids present, no duplicates, no composite keys.
        assert result == ["developer", "reviewer"]
        assert "developer[v2]" not in result
        assert "reviewer[v1]" not in result

    def test_find_skill_tagged_only_no_base_skill(self, temp_agents_dir: Path) -> None:
        """Tagged-only agent with skill is surfaced via its base id."""
        self._create_agent_dir(temp_agents_dir, "reviewer[v1]", {"id": "reviewer"})
        (temp_agents_dir / "reviewer[v1]" / "skills" / "coding").mkdir(parents=True)
        (temp_agents_dir / "reviewer[v1]" / "skills" / "coding" / "skill.md").write_text("# coding")

        reg = AgentRegistry(temp_agents_dir)
        reg.discover()

        assert reg.find_skill("coding") == ["reviewer"]

    # -------------------- validate_tool_configs with tagged version --------------------

    @staticmethod
    def _setup_mock_tools(monkeypatch) -> None:
        """Seed a minimal tool registry for validate_tool_configs."""
        from daemon.tools import _tool_registry

        _tool_registry._tool_metadata.clear()
        _tool_registry._tool_metadata.update({
            "bash": {"category": "bash", "short_doc": "Run bash"},
            "read_file": {"category": "filesystem", "short_doc": "Read file"},
            "write_file": {"category": "filesystem", "short_doc": "Write file"},
        })
        _tool_registry._full_docs.clear()

    def test_validate_tool_configs_tagged_version_with_distinct_invalid_meta_id(
        self, temp_agents_dir: Path, monkeypatch
    ) -> None:
        """A tagged version with a distinct meta.id and invalid config emits
        a warning that displays the meta.id (not a composite key)."""
        self._setup_mock_tools(monkeypatch)

        # Base "developer" with a valid config — no warning expected.
        self._create_agent_dir(
            temp_agents_dir, "developer",
            {"id": "developer", "name": "Dev",
             "tools": {"allow": ["bash"], "deny": []}},
        )
        # Tagged version "developer[v2]" with a distinct meta.id and an
        # invalid config (references a non-existent tool).
        self._create_agent_dir(
            temp_agents_dir, "developer[v2]",
            {"id": "developer_v2", "name": "Dev v2",
             "tools": {"allow": ["nonexistent_tool"], "deny": []}},
        )

        reg = AgentRegistry(temp_agents_dir)
        reg.discover()

        warnings = reg.validate_tool_configs()
        # Exactly one warning, for the tagged variant, displayed by meta.id.
        assert len(warnings) == 1
        msg = warnings[0]
        assert "developer_v2" in msg
        assert "nonexistent_tool" in msg
        # The warning must NOT display a composite key or base name.
        assert "developer[v2]" not in msg
        assert "'developer'" not in msg

    def test_validate_tool_configs_does_not_mask_tagged_with_shared_meta_id(
        self, temp_agents_dir: Path, monkeypatch
    ) -> None:
        """A base agent with no tools config must NOT mask a tagged version
        that shares the same meta.id but has an invalid allow entry.

        Regression guard: a base ``tools is None`` entry must not silently
        suppress validation of a tagged variant's tools config under the
        same meta.id.
        """
        self._setup_mock_tools(monkeypatch)

        # Base "developer" with no tools config (tools is None).
        self._create_agent_dir(
            temp_agents_dir, "developer",
            {"id": "developer", "name": "Dev"},
        )
        # Tagged version sharing the SAME meta.id with an invalid allow entry.
        self._create_agent_dir(
            temp_agents_dir, "developer[v2]",
            {"id": "developer", "name": "Dev v2",
             "tools": {"allow": ["nonexistent_tool"], "deny": []}},
        )

        reg = AgentRegistry(temp_agents_dir)
        reg.discover()

        warnings = reg.validate_tool_configs()
        # The tagged invalid entry must surface as a warning, even though
        # the base shared the same meta.id with no tools config.
        assert any(
            "nonexistent_tool" in w and "Agent 'developer'" in w for w in warnings
        ), warnings


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


class TestCoderAgentResolution:
    """Tests for coder as a standalone agent (no alias to developer).

    Coder is a first-class agent in its own right; there is no
    alias indirection mapping ``coder`` to ``developer``. The registry
    must resolve ``coder`` to itself.
    """

    def test_resolve_pure_id_coder(self) -> None:
        """resolve_pure_id('coder') returns 'coder' (standalone agent, no alias)."""
        registry = get_registry()
        result = registry.resolve_pure_id("coder")
        assert result == "coder"

    def test_resolve_path_to_id_coder(self) -> None:
        """resolve_path_to_id('./agents/coder') returns 'coder' (standalone agent, no alias)."""
        registry = get_registry()
        result = registry.resolve_path_to_id("./agents/coder")
        assert result == "coder"

    def test_exists_coder(self) -> None:
        """exists('coder') returns True (coder is a standalone agent)."""
        registry = get_registry()
        assert registry.exists("coder") is True

    def test_get_resolved_coder(self) -> None:
        """get_resolved('coder') returns the coder metadata directly."""
        registry = get_registry()
        resolved = registry.get_resolved("coder")
        assert resolved is not None
        assert resolved.id == "coder"

    def test_get_resolved_canonical(self) -> None:
        """get_resolved('developer') returns the same metadata as get('developer') (canonical id, no alias indirection)."""
        registry = get_registry()
        assert registry.get_resolved("developer") == registry.get("developer")

    def test_get_resolved_unknown_returns_none(self) -> None:
        """get_resolved for an unknown id returns None (alias-aware)."""
        registry = get_registry()
        assert registry.get_resolved("definitely-not-an-agent") is None
        # Aliases to a non-existent canonical also resolve to None.
        assert registry.get_resolved("ghost-alias") is None

    def test_instance_create_preserves_coder(self) -> None:
        """InstanceCreate(agent_id='coder') preserves 'coder' as-is (agent_id is preserved as-is, no normalization)."""
        from daemon.models.instance import InstanceCreate

        instance = InstanceCreate(agent_id="coder")
        assert instance.agent_id == "coder"


class TestRegistryValidatePath:
    """Tests for opt-in path existence validation on registry lookup methods."""

    def test_get_resolved_validate_path_true_returns_none_for_missing_dir(
        self, temp_agents_dir: Path, caplog
    ) -> None:
        """get_resolved(validate_path=True) returns None when the cached dir is deleted."""
        import logging

        create_agent_meta(temp_agents_dir, "ghost")
        registry = AgentRegistry(temp_agents_dir)
        registry.discover()

        # Sanity: cached meta is reachable by default.
        assert registry.get_resolved("ghost") is not None

        # Delete the directory on disk; cached meta still resolves by default.
        ghost_dir = temp_agents_dir / "ghost"
        (ghost_dir / "meta.json").unlink()
        ghost_dir.rmdir()

        assert registry.get_resolved("ghost") is not None
        assert registry.get_resolved("ghost", validate_path=False) is not None

        caplog.set_level(logging.WARNING, logger="daemon.registry")
        result = registry.get_resolved("ghost", validate_path=True)
        assert result is None
        assert any("ghost" in rec.message for rec in caplog.records)

    def test_get_version_validate_path_true_returns_none_for_missing_dir(
        self, temp_agents_dir: Path, caplog
    ) -> None:
        """get_version(validate_path=True) returns None when the cached dir is deleted."""
        import logging

        create_agent_meta(temp_agents_dir, "ghost")
        registry = AgentRegistry(temp_agents_dir)
        registry.discover()

        # Add a tagged version sibling so we can exercise the tagged lookup branch too.
        tagged_dir = temp_agents_dir / "ghost[v2]"
        tagged_dir.mkdir()
        meta = {
            "id": "ghost",
            "name": "Ghost",
            "description": "Test agent ghost (v2)",
            "icon": "g",
            "color": "accent-blue",
        }
        with open(tagged_dir / "meta.json", "w") as f:
            json.dump(meta, f)
        registry.discover()

        assert registry.get_version("ghost", "v2") is not None
        # Default + explicit False keep stale-meta behavior.
        assert registry.get_version("ghost", "v2") is not None
        assert registry.get_version("ghost", "v2", validate_path=False) is not None

        # Remove only the tagged directory; cached meta must still be returned by default.
        (tagged_dir / "meta.json").unlink()
        tagged_dir.rmdir()
        assert registry.get_version("ghost", "v2") is not None

        caplog.set_level(logging.WARNING, logger="daemon.registry")
        result = registry.get_version("ghost", "v2", validate_path=True)
        assert result is None
        assert any("ghost" in rec.message for rec in caplog.records)

    def test_get_version_validate_path_does_not_affect_unknown_agent(self) -> None:
        """Unknown agent still resolves to None regardless of validate_path."""
        registry = get_registry()
        assert registry.get_version("definitely-not-an-agent", validate_path=True) is None
        assert registry.get_version("definitely-not-an-agent", validate_path=False) is None
        assert registry.get_version("definitely-not-an-agent", "v2", validate_path=True) is None
