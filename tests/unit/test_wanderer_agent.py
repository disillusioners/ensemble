"""Comprehensive tests for the Wanderer agent.

Tests Wanderer agent discovery, loading, tool filtering, and prompt composition.
Wanderer is a read-only investigation agent that explores codebases, answers
questions, and does library research WITHOUT modifying files. Wanderer can
delegate bounded investigation sub-tasks to coder instances for complex,
multi-file investigations — wanderer itself never writes.

All tests run in the unit test environment with langgraph mocks from conftest.py.
"""

import json
from pathlib import Path

import pytest


# Path constants
WANDERER_AGENT_DIR = Path(__file__).parent.parent.parent / "agents" / "wanderer"

# Tool categories for testing (mirrors what the registry should contain).
TOOL_CATEGORIES: dict[str, list[str]] = {
    "bash": ["bash"],
    "filesystem": ["list_directory", "read_file", "write_file", "glob_files", "grep_files", "edit_file"],
    "time": ["time"],
    "instance": ["spawn_instance", "send_message", "terminate_instance", "list_instances", "get_instance_info"],
    "self": ["inner_soul", "access_memory"],
    "project": [
        "project_create", "project_get", "project_list", "project_search",
        "project_get_by_instance", "project_get_by_directory", "project_update",
        "project_set_status", "project_add_directory", "project_remove_directory",
        "project_set_tags", "project_add_tag", "project_remove_tag",
        "project_set_shortnames", "project_add_shortname", "project_remove_shortname",
        "project_set_metadata", "project_delete_metadata",
        "project_link", "project_unlink", "project_delete",
    ],
    "help": ["tool_help"],
    "mother": ["agent_list", "agent_create", "agent_read", "agent_modify", "agent_delete"],
    "todo": ["create_todo", "update_todo", "list_todos", "complete_todo"],
    "chart": ["render_chart", "list_chart_types"],
    "external_opencode": [
        "external_opencode_init_session", "external_opencode_send_message",
        "external_opencode_get_status", "external_opencode_wait_for_result",
        "external_opencode_abort_session",
    ],
    "db": ["db_query", "db_execute", "db_schema"],
    "mcp": ["mcp_list_servers", "mcp_invoke"],
    "knowledge": ["explore", "experience"],
    "context": ["context"],
    "rag": [
        "rag_insert_text", "rag_insert_texts", "rag_query", "rag_query_data",
        "rag_search_labels", "rag_get_graph", "rag_create_entity", "rag_get_entity",
    ],
}

# Expected tool categories in meta.json allow list (13 declared categories).
# Updated for worker migration: 'knowledge' category was replaced by the
# top-level 'explore' tool + 'dynamic-skill' innate skill. Added 'proc' and
# 'blueprint' per migration.
EXPECTED_ALLOW_CATEGORIES = [
    "bash", "proc", "filesystem", "time", "self", "help",
    "explore", "mcp", "context", "shared_context", "rag", "instance", "blueprint",
]


# =============================================================================
# 1. Agent Auto-Discovery
# =============================================================================


class TestWandererAutoDiscovery:
    """Tests for Wanderer agent auto-discovery via AgentRegistry."""

    def test_wanderer_directory_exists(self) -> None:
        assert WANDERER_AGENT_DIR.exists()
        assert WANDERER_AGENT_DIR.is_dir()

    def test_wanderer_not_in_skip_dirs(self) -> None:
        from daemon.registry import SKIP_DIRS
        assert "wanderer" not in SKIP_DIRS

    def test_wanderer_discovered_in_registry(self) -> None:
        from daemon.registry import AgentRegistry
        registry = AgentRegistry(WANDERER_AGENT_DIR.parent)
        registry.discover()
        assert registry.exists("wanderer")

    def test_wanderer_in_agent_list(self) -> None:
        from daemon.registry import AgentRegistry
        registry = AgentRegistry(WANDERER_AGENT_DIR.parent)
        registry.discover()
        agent_ids = {a.id for a in registry.list_all()}
        assert "wanderer" in agent_ids

    def test_wanderer_metadata_loaded_correctly(self) -> None:
        from daemon.registry import AgentRegistry
        registry = AgentRegistry(WANDERER_AGENT_DIR.parent)
        registry.discover()
        wanderer = registry.get("wanderer")
        assert wanderer is not None
        assert wanderer.id == "wanderer"
        assert wanderer.name == "Wanderer"


# =============================================================================
# 2. meta.json Validity
# =============================================================================


class TestWandererMetaJsonValidation:
    """Tests for Wanderer meta.json structure and content."""

    def test_meta_json_exists(self) -> None:
        meta_path = WANDERER_AGENT_DIR / "meta.json"
        assert meta_path.exists()

    def test_meta_json_is_valid_json(self) -> None:
        meta_path = WANDERER_AGENT_DIR / "meta.json"
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        assert isinstance(meta, dict)

    def test_required_fields_exist(self) -> None:
        meta_path = WANDERER_AGENT_DIR / "meta.json"
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        required_fields = ["id", "name", "description", "icon", "color", "version"]
        for field in required_fields:
            assert field in meta

    def test_agent_id_and_name(self) -> None:
        meta_path = WANDERER_AGENT_DIR / "meta.json"
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        assert meta.get("id") == "wanderer"
        assert meta.get("name") == "Wanderer"

    def test_field_types_are_correct(self) -> None:
        meta_path = WANDERER_AGENT_DIR / "meta.json"
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        assert isinstance(meta.get("id"), str)
        assert isinstance(meta.get("name"), str)
        assert isinstance(meta.get("description"), str)
        assert isinstance(meta.get("icon"), str)
        assert isinstance(meta.get("color"), str)
        assert isinstance(meta.get("innate_skills"), list)
        assert "tools" in meta

    def test_innate_skills_value(self) -> None:
        meta_path = WANDERER_AGENT_DIR / "meta.json"
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        innate_skills = meta.get("innate_skills", [])
        assert "todo" in innate_skills
        assert "chart" in innate_skills

    def test_innate_skills_does_not_contain_opencode(self) -> None:
        meta_path = WANDERER_AGENT_DIR / "meta.json"
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        assert "opencode" not in meta.get("innate_skills", [])

    def test_tools_config_structure(self) -> None:
        meta_path = WANDERER_AGENT_DIR / "meta.json"
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        tools_config = meta.get("tools")
        assert tools_config is not None
        assert "allow" in tools_config
        assert isinstance(tools_config["allow"], list)

    def test_tools_allow_has_all_declared_categories(self) -> None:
        """Verify all 9 categories listed in meta.json are in the allow list."""
        meta_path = WANDERER_AGENT_DIR / "meta.json"
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        allowed = meta.get("tools", {}).get("allow", [])
        for category in EXPECTED_ALLOW_CATEGORIES:
            assert category in allowed, (
                f"tools.allow should include '{category}'. Got: {allowed}"
            )
        assert len(allowed) == len(EXPECTED_ALLOW_CATEGORIES), (
            f"tools.allow should have exactly {len(EXPECTED_ALLOW_CATEGORIES)} "
            f"entries, got {len(allowed)}: {allowed}"
        )

    def test_tools_allow_does_not_contain_db(self) -> None:
        meta_path = WANDERER_AGENT_DIR / "meta.json"
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        allowed = meta.get("tools", {}).get("allow", [])
        assert "db" not in allowed

    def test_tools_allow_contains_instance(self) -> None:
        """Wanderer delegates complex investigations to coder instances."""
        meta_path = WANDERER_AGENT_DIR / "meta.json"
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        allowed = meta.get("tools", {}).get("allow", [])
        assert "instance" in allowed, (
            f"tools.allow should include 'instance' so wanderer can spawn "
            f"coder for complex investigations. Got: {allowed}"
        )

    def test_team_members_field_is_present(self) -> None:
        """Wanderer has a team_members field; wanderer delegates to coder."""
        meta_path = WANDERER_AGENT_DIR / "meta.json"
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        assert "team_members" in meta, (
            "meta.json must define 'team_members' — wanderer delegates "
            "complex investigations to coder"
        )

    def test_team_members_contains_worker(self) -> None:
        """Wanderer's team members are explorer + worker (post coder→worker migration)."""
        meta_path = WANDERER_AGENT_DIR / "meta.json"
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        team_members = meta.get("team_members", [])
        assert isinstance(team_members, list)
        assert team_members == ["explorer", "worker"], (
            f"team_members must be exactly ['explorer', 'worker'] after "
            f"coder→worker migration. Got: {team_members}"
        )

    def test_team_members_does_not_contain_opencode(self) -> None:
        """team_members must not contain opencode — that's the coder agent's lane."""
        meta_path = WANDERER_AGENT_DIR / "meta.json"
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        team_members = meta.get("team_members", [])
        assert "opencode" not in team_members, (
            f"team_members should not contain 'opencode': {team_members}"
        )

    def test_tools_config_parsed_by_registry(self) -> None:
        from daemon.registry import AgentRegistry, ToolFilter
        registry = AgentRegistry(WANDERER_AGENT_DIR.parent)
        registry.discover()
        wanderer = registry.get("wanderer")
        assert wanderer is not None
        assert wanderer.tools is not None
        assert isinstance(wanderer.tools, ToolFilter)
        assert wanderer.tools.deny is None

    def test_registry_tools_match_meta(self) -> None:
        from daemon.registry import AgentRegistry
        registry = AgentRegistry(WANDERER_AGENT_DIR.parent)
        registry.discover()
        meta_path = WANDERER_AGENT_DIR / "meta.json"
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        wanderer = registry.get("wanderer")
        assert wanderer is not None
        assert wanderer.tools is not None
        assert wanderer.tools.allow == meta["tools"]["allow"]


# =============================================================================
# 3. Tool Filter
# =============================================================================


class TestWandererToolFilter:
    """Tests for Wanderer tool configuration and filtering."""

    def test_wanderer_tool_filter_in_registry(self) -> None:
        from daemon.registry import AgentRegistry
        registry = AgentRegistry(WANDERER_AGENT_DIR.parent)
        registry.discover()
        wanderer = registry.get("wanderer")
        assert wanderer is not None
        assert wanderer.tools is not None

    def test_wanderer_tools_deny_is_none(self) -> None:
        """Wanderer should not have a deny list - allow list is the source of truth."""
        meta_path = WANDERER_AGENT_DIR / "meta.json"
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        tools_config = meta.get("tools", {})
        assert "deny" not in tools_config or tools_config.get("deny") is None

    def test_wanderer_resolve_tool_filter_returns_all_categories(self) -> None:
        from daemon.tools.instance import resolve_tool_filter
        allowed_tools = resolve_tool_filter(
            allow=EXPECTED_ALLOW_CATEGORIES,
            deny=None,
            tool_categories=TOOL_CATEGORIES,
        )
        assert allowed_tools is not None
        # bash should resolve
        assert "bash" in allowed_tools
        # filesystem tools should resolve
        for t in ["read_file", "glob_files", "grep_files", "list_directory"]:
            assert t in allowed_tools
        # mcp tools should resolve
        assert "mcp_list_servers" in allowed_tools
        assert "mcp_invoke" in allowed_tools
        # knowledge tools should resolve (note: 'experience' tool was removed in
        # commit a813454e — replaced by 'explore' tool + 'dynamic-skill' innate skill)
        assert "explore" in allowed_tools
        # rag tools should resolve
        assert "rag_query" in allowed_tools
        # self tools should resolve
        assert "inner_soul" in allowed_tools
        # instance tools MUST resolve (wanderer delegates complex investigations)
        assert "spawn_instance" in allowed_tools
        assert "send_message" in allowed_tools
        assert "terminate_instance" in allowed_tools

    def test_wanderer_resolve_excludes_db_and_opencode(self) -> None:
        from daemon.tools.instance import resolve_tool_filter
        allowed_tools = resolve_tool_filter(
            allow=EXPECTED_ALLOW_CATEGORIES,
            deny=None,
            tool_categories=TOOL_CATEGORIES,
        )
        assert allowed_tools is not None
        # db not in allow -> not resolved
        for t in ["db_query", "db_execute", "db_schema"]:
            assert t not in allowed_tools
        # opencode not in allow -> not resolved
        for t in [
            "external_opencode_init_session",
            "external_opencode_send_message",
            "external_opencode_get_status",
        ]:
            assert t not in allowed_tools

    def test_wanderer_has_no_write_tools(self) -> None:
        """Wanderer is read-only: filesystem write_file/edit_file must NOT be available."""
        from daemon.tools.instance import resolve_tool_filter
        allowed_tools = resolve_tool_filter(
            allow=EXPECTED_ALLOW_CATEGORIES,
            deny=None,
            tool_categories=TOOL_CATEGORIES,
        )
        assert allowed_tools is not None
        # filesystem category is in allow, so write_file/edit_file ARE technically
        # available. Verify the soul.md policy explicitly forbids their use.
        soul_path = WANDERER_AGENT_DIR / "soul.md"
        soul_text = soul_path.read_text(encoding="utf-8").lower()
        assert "never" in soul_text and "write_file" in soul_text, (
            "soul.md must explicitly forbid write_file usage"
        )
        assert "never" in soul_text and "edit_file" in soul_text, (
            "soul.md must explicitly forbid edit_file usage"
        )

    def test_wanderer_innate_skills_todo_chart_expansion(self) -> None:
        from daemon.tools.instance import expand_allow_for_innate_skills
        result = expand_allow_for_innate_skills(EXPECTED_ALLOW_CATEGORIES, ["todo", "chart"])
        assert result is not None
        assert "chart" in result
        assert "todo" in result
        for category in EXPECTED_ALLOW_CATEGORIES:
            assert category in result

    def test_apply_tool_filter_restricts_to_wanderer_tools(self) -> None:
        from unittest.mock import MagicMock, patch
        from daemon.tools.instance import _apply_tool_filter

        class MockTool:
            def __init__(self, name: str):
                self.name = name

        tools = [
            MockTool("read_file"),
            MockTool("glob_files"),
            MockTool("rag_query"),
            MockTool("db_query"),  # Should be filtered out (db not in allow)
            MockTool("write_file"),  # Available but forbidden by soul policy
            MockTool("external_opencode_init_session"),  # Should be filtered out
            MockTool("spawn_instance"),  # Available — wanderer delegates to coder
        ]

        mock_agent_meta = MagicMock()
        mock_filter = MagicMock()
        mock_filter.allow = EXPECTED_ALLOW_CATEGORIES
        mock_filter.deny = None
        mock_agent_meta.tools = mock_filter
        mock_agent_meta.innate_skills = ["todo", "chart"]

        with patch("daemon.tools.instance.list_tools_by_category") as mock_list_tools:
            with patch("daemon.registry.get_registry") as mock_registry:
                mock_list_tools.return_value = TOOL_CATEGORIES
                mock_registry.return_value.get_resolved.return_value = mock_agent_meta
                mock_registry.return_value.get_version.return_value = mock_agent_meta

                result = _apply_tool_filter(tools, "wanderer")
                tool_names = {t.name for t in result}

                # Allowed tools should be present
                assert "read_file" in tool_names
                assert "glob_files" in tool_names
                assert "rag_query" in tool_names
                # write_file is technically available (filesystem in allow)
                # but soul policy forbids it - that's a policy, not a tool-filter test
                # db tools should be filtered out
                assert "db_query" not in tool_names
                # opencode tools should be filtered out
                assert "external_opencode_init_session" not in tool_names
                # instance tools SHOULD be present (wanderer delegates to coder)
                assert "spawn_instance" in tool_names


# =============================================================================
# 4. Soul Content
# =============================================================================


class TestWandererSoulContent:
    """Tests for Wanderer soul.md content and identity."""

    def test_soul_file_exists(self) -> None:
        soul_path = WANDERER_AGENT_DIR / "soul.md"
        assert soul_path.exists()

    def test_soul_contains_wanderer_identity(self) -> None:
        soul_path = WANDERER_AGENT_DIR / "soul.md"
        content = soul_path.read_text(encoding="utf-8")
        assert "Wanderer" in content
        assert "investigator" in content.lower() or "investigation" in content.lower()

    def test_soul_declares_readonly_discipline_with_worker_hands(self) -> None:
        """Soul must declare read-only for wanderer and identify worker as hands."""
        soul_path = WANDERER_AGENT_DIR / "soul.md"
        content = soul_path.read_text(encoding="utf-8").lower()
        # Wanderer is read-only
        assert "read-only" in content or "read only" in content, (
            "soul.md must explicitly declare read-only discipline"
        )
        # But wanderer delegates bounded investigation work to worker instances
        assert "worker" in content, (
            "soul.md must mention worker as the hands for complex investigations"
        )

    def test_soul_forbids_modifying_files(self) -> None:
        soul_path = WANDERER_AGENT_DIR / "soul.md"
        content = soul_path.read_text(encoding="utf-8").lower()
        # Must explicitly forbid write_file and edit_file
        assert "write_file" in content
        assert "edit_file" in content
        # Must contain "never" near these references
        assert "never" in content

    def test_soul_mentions_worker_delegation(self) -> None:
        """Soul documents worker delegation for complex, multi-file investigations."""
        soul_path = WANDERER_AGENT_DIR / "soul.md"
        content = soul_path.read_text(encoding="utf-8").lower()
        # Must mention delegation / spawn to worker
        assert "worker" in content
        assert "delegat" in content or "spawn" in content, (
            "soul.md must describe delegating to worker instances"
        )
        # Must NOT mention the obsolete coder→developer alias note
        assert "coder→developer" not in content, (
            "soul.md must not reference the obsolete coder→developer alias"
        )
        assert "coder->developer" not in content, (
            "soul.md must not reference the obsolete coder->developer alias"
        )

    def test_soul_mentions_mcp_for_research(self) -> None:
        soul_path = WANDERER_AGENT_DIR / "soul.md"
        content = soul_path.read_text(encoding="utf-8").lower()
        assert "mcp" in content
        # Should mention research/web/github
        assert any(term in content for term in ["web", "github", "research"])

    def test_soul_mentions_explore_and_dynamic_skill(self) -> None:
        """Soul documents explore (knowledge search) + dynamic-skill tools.

        Post-migration: 'experience' tool was removed (see commit a813454e)
        and replaced by the dynamic-skill innate skill (skill_search/view/feedback).
        """
        soul_path = WANDERER_AGENT_DIR / "soul.md"
        content = soul_path.read_text(encoding="utf-8")
        assert "explore" in content
        assert "skill_search" in content or "skill_view" in content

    def test_soul_has_required_sections(self) -> None:
        """Match the structural pattern of coder/soul.md."""
        soul_path = WANDERER_AGENT_DIR / "soul.md"
        content = soul_path.read_text(encoding="utf-8")
        required_sections = [
            "Who I Am",
            "Core Identity",
            "Core Beliefs",
            "Role",
            "Tool Inventory",
        ]
        for section in required_sections:
            assert section in content, (
                f"soul.md missing required section: '{section}'"
            )

    def test_soul_prompt_loads_via_loader(self) -> None:
        from daemon.loader import load_agent_prompts
        prompts = load_agent_prompts(WANDERER_AGENT_DIR)
        assert "soul" in prompts
        assert "Wanderer" in prompts["soul"]
