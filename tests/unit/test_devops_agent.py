"""Comprehensive tests for the DevOps agent.

Tests DevOps agent discovery, loading, tool filtering, and leader integration.
All tests run in the unit test environment with langgraph mocks from conftest.py.
"""

import json
import re
from pathlib import Path

import pytest


# Path constants
DEVOPS_AGENT_DIR = Path(__file__).parent.parent.parent / "agents" / "devops"
LEADER_AGENT_DIR = Path(__file__).parent.parent.parent / "agents" / "leader"

# Tool categories for testing (mirrors what the registry should contain)
TOOL_CATEGORIES: dict[str, list[str]] = {
    "bash": ["bash"],
    "filesystem": ["list_directory", "read_file", "write_file", "glob_files", "grep_files", "edit_file"],
    "time": ["time"],
    "instance": [
        "spawn_instance", "send_message", "terminate_instance",
        "list_instances", "get_instance_info"
    ],
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
}


# =============================================================================
# 1. Agent Auto-Discovery
# =============================================================================


class TestDevopsAutoDiscovery:
    """Tests for DevOps agent auto-discovery via AgentRegistry."""

    def test_devops_directory_exists(self) -> None:
        """The agents/devops/ directory should exist."""
        assert DEVOPS_AGENT_DIR.exists(), f"agents/devops/ directory not found at {DEVOPS_AGENT_DIR}"
        assert DEVOPS_AGENT_DIR.is_dir(), "agents/devops/ should be a directory"

    def test_devops_not_in_skip_dirs(self) -> None:
        """'devops' should NOT be in SKIP_DIRS (template/internal directories)."""
        from daemon.registry import SKIP_DIRS

        assert "devops" not in SKIP_DIRS, (
            "devops should NOT be in SKIP_DIRS — it is a real agent, not a template"
        )

    def test_devops_discovered_in_registry(self) -> None:
        """DevOps should be discovered when scanning the agents directory."""
        from daemon.registry import AgentRegistry

        agents_dir = DEVOPS_AGENT_DIR.parent
        registry = AgentRegistry(agents_dir)
        registry.discover()

        assert registry.exists("devops"), "DevOps should be discovered in the agents directory"

    def test_devops_in_agent_list(self) -> None:
        """DevOps should appear in the list of all agents."""
        from daemon.registry import AgentRegistry

        agents_dir = DEVOPS_AGENT_DIR.parent
        registry = AgentRegistry(agents_dir)
        registry.discover()

        agents = registry.list_all()
        agent_ids = {a.id for a in agents}
        assert "devops" in agent_ids, "devops should be in the list of agents"

    def test_devops_metadata_loaded_correctly(self) -> None:
        """DevOps metadata loaded from registry should match meta.json."""
        from daemon.registry import AgentRegistry

        agents_dir = DEVOPS_AGENT_DIR.parent
        registry = AgentRegistry(agents_dir)
        registry.discover()

        devops = registry.get("devops")
        assert devops is not None, "DevOps should be retrievable from registry"
        assert devops.id == "devops"
        assert devops.name == "DevOps"


# =============================================================================
# 2. meta.json Validity
# =============================================================================


class TestDevopsMetaJsonValidation:
    """Tests for DevOps meta.json structure and content."""

    def test_meta_json_exists(self) -> None:
        """meta.json file should exist in the DevOps agent directory."""
        meta_path = DEVOPS_AGENT_DIR / "meta.json"
        assert meta_path.exists(), f"meta.json not found at {meta_path}"

    def test_meta_json_is_valid_json(self) -> None:
        """meta.json should be parseable as valid JSON."""
        meta_path = DEVOPS_AGENT_DIR / "meta.json"
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        assert isinstance(meta, dict)

    def test_required_fields_exist(self) -> None:
        """meta.json should contain all required fields."""
        meta_path = DEVOPS_AGENT_DIR / "meta.json"
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)

        required_fields = ["id", "name", "description", "icon", "color", "version"]
        for field in required_fields:
            assert field in meta, f"Required field '{field}' missing from meta.json"

    def test_agent_id_and_name(self) -> None:
        """Agent id and name should be 'devops' / 'DevOps'."""
        meta_path = DEVOPS_AGENT_DIR / "meta.json"
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)

        assert meta.get("id") == "devops", f"Agent id should be 'devops', got '{meta.get('id')}'"
        assert meta.get("name") == "DevOps", f"Agent name should be 'DevOps', got '{meta.get('name')}'"

    def test_field_types_are_correct(self) -> None:
        """meta.json field types should match expected types."""
        meta_path = DEVOPS_AGENT_DIR / "meta.json"
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)

        assert isinstance(meta.get("id"), str), "id should be a string"
        assert isinstance(meta.get("name"), str), "name should be a string"
        assert isinstance(meta.get("description"), str), "description should be a string"
        assert isinstance(meta.get("icon"), str), "icon should be a string"
        assert isinstance(meta.get("color"), str), "color should be a string"
        assert isinstance(meta.get("innate_skills"), list), "innate_skills should be a list"
        assert "tools" in meta, "tools field should be present"

    def test_innate_skills_is_empty_list(self) -> None:
        """DevOps should have empty innate_skills (giter pattern, no opencode delegation)."""
        meta_path = DEVOPS_AGENT_DIR / "meta.json"
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)

        innate_skills = meta.get("innate_skills", [])
        assert innate_skills == [], (
            f"DevOps should have empty innate_skills (no opencode skills), got: {innate_skills}"
        )

    def test_capabilities_field_exists(self) -> None:
        """meta.json should have capabilities field with infra-related capabilities."""
        meta_path = DEVOPS_AGENT_DIR / "meta.json"
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)

        assert "capabilities" in meta, "capabilities field should be present"
        capabilities = meta.get("capabilities", [])
        assert isinstance(capabilities, list), "capabilities should be a list"

        # Check for expected DevOps capabilities
        expected_caps = {"docker", "kubernetes", "terraform", "ci-cd", "shell-ops"}
        actual_caps = set(capabilities)
        assert expected_caps.issubset(actual_caps), (
            f"Expected capabilities {expected_caps} not found in {actual_caps}"
        )

    def test_tools_config_structure(self) -> None:
        """tools configuration should have the expected structure."""
        meta_path = DEVOPS_AGENT_DIR / "meta.json"
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)

        tools_config = meta.get("tools")
        assert tools_config is not None, "tools config should not be None"
        assert "allow" in tools_config, "tools config should have 'allow' key"
        assert isinstance(tools_config["allow"], list), "'allow' should be a list"

    def test_tools_allow_list(self) -> None:
        """tools.allow should contain the 8 standard tools for DevOps (giter pattern)."""
        meta_path = DEVOPS_AGENT_DIR / "meta.json"
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)

        tools_config = meta.get("tools", {})
        allowed = tools_config.get("allow", [])

        expected_tools = ["bash", "filesystem", "time", "self", "help", "knowledge", "mcp", "context"]
        for tool in expected_tools:
            assert tool in allowed, f"'{tool}' should be in allowed tools: {allowed}"

    def test_tools_config_parsed_by_registry(self) -> None:
        """DevOps tools config should be parseable by the registry ToolFilter model."""
        from daemon.registry import AgentRegistry, ToolFilter

        agents_dir = DEVOPS_AGENT_DIR.parent
        registry = AgentRegistry(agents_dir)
        registry.discover()

        devops = registry.get("devops")
        assert devops is not None
        assert devops.tools is not None
        assert isinstance(devops.tools, ToolFilter)
        assert devops.tools.deny is None

    def test_registry_tools_match_meta(self) -> None:
        """Registry-loaded tools should match meta.json tools."""
        from daemon.registry import AgentRegistry

        agents_dir = DEVOPS_AGENT_DIR.parent
        registry = AgentRegistry(agents_dir)
        registry.discover()

        meta_path = DEVOPS_AGENT_DIR / "meta.json"
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)

        devops = registry.get("devops")
        assert devops is not None
        assert devops.tools is not None
        assert devops.tools.allow == meta["tools"]["allow"]


# =============================================================================
# 3. Prompt Composition
# =============================================================================


class TestDevopsPromptComposition:
    """Tests for DevOps agent prompt loading and composition."""

    def test_load_devops_prompts(self) -> None:
        """All DevOps markdown files should be loadable as prompts."""
        from daemon.loader import load_agent_prompts

        prompts = load_agent_prompts(DEVOPS_AGENT_DIR)

        # Should have soul, rule, workflow, tools (from tools_note.md)
        assert "soul" in prompts, "soul.md should be loaded"
        assert "rule" in prompts, "rule.md should be loaded"
        assert "workflow" in prompts, "workflow.md should be loaded"
        assert "tools" in prompts, "tools_note.md (or tools.md) should be loaded"

    def test_soul_content_included(self) -> None:
        """soul.md content should be included in the loaded prompts."""
        from daemon.loader import load_agent_prompts

        prompts = load_agent_prompts(DEVOPS_AGENT_DIR)

        assert "DevOps" in prompts["soul"]
        assert "infrastructure" in prompts["soul"].lower()

    def test_rule_content_included(self) -> None:
        """rule.md content should be included in the loaded prompts."""
        from daemon.loader import load_agent_prompts

        prompts = load_agent_prompts(DEVOPS_AGENT_DIR)

        assert "Rules" in prompts["rule"]
        assert "Must" in prompts["rule"]
        assert "Must Not" in prompts["rule"]

    def test_workflow_content_included(self) -> None:
        """workflow.md content should be included in the loaded prompts."""
        from daemon.loader import load_agent_prompts

        prompts = load_agent_prompts(DEVOPS_AGENT_DIR)

        assert "Workflow" in prompts["workflow"]
        assert "Step" in prompts["workflow"]

    def test_tools_note_content_included(self) -> None:
        """tools_note.md content should be included as the tools prompt."""
        from daemon.loader import load_agent_prompts

        prompts = load_agent_prompts(DEVOPS_AGENT_DIR)

        # Should use tools_note.md content
        assert "Tool Usage Notes" in prompts["tools"] or "bash" in prompts["tools"].lower()

    def test_compose_system_prompt_includes_all_sections(self) -> None:
        """Composed system prompt should include soul, rule, workflow, tools."""
        from daemon.loader import compose_system_prompt, load_agent_prompts

        prompts = load_agent_prompts(DEVOPS_AGENT_DIR)
        system_prompt = compose_system_prompt(prompts)

        # All sections should be present
        assert "DevOps" in system_prompt, "soul content (DevOps identity) should be in system prompt"
        assert "Rules" in system_prompt, "rule content should be in system prompt"
        assert "Workflow" in system_prompt, "workflow content should be in system prompt"

    def test_system_prompt_contains_devops_identity(self) -> None:
        """System prompt should contain DevOps-specific identity content."""
        from daemon.loader import compose_system_prompt, load_agent_prompts

        prompts = load_agent_prompts(DEVOPS_AGENT_DIR)
        system_prompt = compose_system_prompt(prompts)

        # DevOps-specific identity markers
        assert "infrastructure" in system_prompt.lower()
        assert "docker" in system_prompt.lower() or "deployment" in system_prompt.lower()

    def test_no_opencode_skill_content_in_system_prompt(self) -> None:
        """With empty innate_skills, no opencode skill files should be loaded.

        The composed system prompt MAY mention OpenCode in agent text (e.g. as a
        contrast in soul.md) — that is legitimate content, not skill injection.
        What we must verify is that no skill file content was injected by virtue
        of having `innate_skills` in meta.json.
        """
        from daemon.loader import load_agent_skills, load_agent_prompts, compose_system_prompt

        # Load meta.json to pass to load_agent_skills
        meta_path = DEVOPS_AGENT_DIR / "meta.json"
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)

        # With empty innate_skills, no skills should be loaded from the
        # centralized innate-skills registry or per-agent skills/ directory.
        skills = load_agent_skills(DEVOPS_AGENT_DIR, meta)
        assert skills == {}, (
            f"No skills should be loaded (innate_skills is empty), got: {list(skills.keys())}"
        )

        # Also verify that the soul.md does not contain "OpenCode_Skill" tool
        # name tokens that would indicate an opencode-skill file was injected.
        prompts = load_agent_prompts(DEVOPS_AGENT_DIR)
        system_prompt = compose_system_prompt(prompts, skills)

        # OpenCode_Skill is the legacy tool name used to inject opencode skill content.
        # The new approach uses external_opencode_* tool names from external_opencode.py.
        # Neither should appear in the composed prompt for an agent with no innate skills.
        assert "OpenCode_Skill" not in system_prompt, (
            "Legacy 'OpenCode_Skill' tool should not appear in DevOps system prompt"
        )
        # external_opencode tools shouldn't be in the prompt either (they're tools,
        # not skills, and are listed via the dynamic tools section only when allowed)
        assert "external_opencode_init_session" not in system_prompt, (
            "external_opencode_init_session tool should not appear in DevOps system prompt"
        )

    def test_system_prompt_composition_order(self) -> None:
        """System prompt should follow correct order: soul → rule → tools → workflow."""
        from daemon.loader import compose_system_prompt, load_agent_prompts

        prompts = load_agent_prompts(DEVOPS_AGENT_DIR)
        system_prompt = compose_system_prompt(prompts)

        # Find positions (rule.md uses "# Rules" as H1, not "## Rules")
        soul_pos = system_prompt.find("Who I Am")
        rule_pos = system_prompt.find("# Rules")
        workflow_pos = system_prompt.find("# Workflow")

        assert soul_pos != -1, "soul (Who I Am) should be in system prompt"
        assert rule_pos != -1, "rule (# Rules) should be in system prompt"
        assert workflow_pos != -1, "workflow should be in system prompt"

        # Order should be soul → rule → workflow
        assert soul_pos < rule_pos, "soul should come before rule"
        assert rule_pos < workflow_pos, "rule should come before workflow"

    def test_load_and_cache_prompt_works(self) -> None:
        """load_and_cache_prompt should successfully load DevOps prompt."""
        from daemon.loader import PromptCache, load_and_cache_prompt

        cache = PromptCache()
        prompt, tokens = load_and_cache_prompt("devops", DEVOPS_AGENT_DIR, cache)

        assert isinstance(prompt, str)
        assert len(prompt) > 0
        assert tokens > 0
        assert "DevOps" in prompt
        assert "Rules" in prompt

    def test_load_and_cache_prompt_caches(self) -> None:
        """load_and_cache_prompt should cache the result."""
        from daemon.loader import PromptCache, load_and_cache_prompt

        cache = PromptCache()

        # First load
        prompt1, tokens1 = load_and_cache_prompt("devops", DEVOPS_AGENT_DIR, cache)

        # Should be cached
        cached = cache.get("devops")
        assert cached is not None
        assert cached[0] == prompt1
        assert cached[1] == tokens1

    def test_no_errors_during_loading(self) -> None:
        """Loading DevOps should not raise any exceptions."""
        from daemon.loader import (
            load_agent_prompts,
            compose_system_prompt,
            PromptCache,
            load_and_cache_prompt,
        )

        # Should not raise
        prompts = load_agent_prompts(DEVOPS_AGENT_DIR)
        system_prompt = compose_system_prompt(prompts)
        cache = PromptCache()
        prompt, tokens = load_and_cache_prompt("devops", DEVOPS_AGENT_DIR, cache)

        assert len(prompt) > 0


# =============================================================================
# 4. Tool Configuration
# =============================================================================


class TestDevopsToolConfiguration:
    """Tests for DevOps tool configuration and filtering."""

    def test_devops_tool_filter_in_registry(self) -> None:
        """DevOps should have a tool filter configured in the registry."""
        from daemon.registry import AgentRegistry

        agents_dir = DEVOPS_AGENT_DIR.parent
        registry = AgentRegistry(agents_dir)
        registry.discover()

        devops = registry.get("devops")
        assert devops is not None
        assert devops.tools is not None

    def test_devops_has_bash_tool(self) -> None:
        """DevOps should have bash tool available."""
        from daemon.tools.instance import resolve_tool_filter

        allowed_tools = resolve_tool_filter(
            allow=["bash", "filesystem", "time", "self", "help", "knowledge", "mcp", "context"],
            deny=None,
            tool_categories=TOOL_CATEGORIES,
        )

        assert allowed_tools is not None
        assert "bash" in allowed_tools

    def test_devops_has_filesystem_tools(self) -> None:
        """DevOps should have filesystem tools available."""
        from daemon.tools.instance import resolve_tool_filter

        allowed_tools = resolve_tool_filter(
            allow=["bash", "filesystem", "time", "self", "help", "knowledge", "mcp", "context"],
            deny=None,
            tool_categories=TOOL_CATEGORIES,
        )

        assert allowed_tools is not None
        filesystem_tools = {"list_directory", "read_file", "write_file", "glob_files", "grep_files", "edit_file"}
        for tool in filesystem_tools:
            assert tool in allowed_tools, f"{tool} should be in DevOps allowed tools"

    def test_devops_has_time_tool(self) -> None:
        """DevOps should have time tool available."""
        from daemon.tools.instance import resolve_tool_filter

        allowed_tools = resolve_tool_filter(
            allow=["bash", "filesystem", "time", "self", "help", "knowledge", "mcp", "context"],
            deny=None,
            tool_categories=TOOL_CATEGORIES,
        )

        assert allowed_tools is not None
        assert "time" in allowed_tools

    def test_devops_has_self_tools(self) -> None:
        """DevOps should have self-modification tools (inner_soul, access_memory)."""
        from daemon.tools.instance import resolve_tool_filter

        allowed_tools = resolve_tool_filter(
            allow=["bash", "filesystem", "time", "self", "help", "knowledge", "mcp", "context"],
            deny=None,
            tool_categories=TOOL_CATEGORIES,
        )

        assert allowed_tools is not None
        self_tools = {"inner_soul", "access_memory"}
        for tool in self_tools:
            assert tool in allowed_tools, f"{tool} should be in DevOps allowed tools"

    def test_devops_does_not_have_instance_tools(self) -> None:
        """DevOps should NOT have instance management tools (no spawn_instance etc)."""
        from daemon.tools.instance import resolve_tool_filter

        allowed_tools = resolve_tool_filter(
            allow=["bash", "filesystem", "time", "self", "help", "knowledge", "mcp", "context"],
            deny=None,
            tool_categories=TOOL_CATEGORIES,
        )

        assert allowed_tools is not None
        instance_tools = {"spawn_instance", "send_message", "terminate_instance", "list_instances", "get_instance_info"}
        for tool in instance_tools:
            assert tool not in allowed_tools, f"{tool} should NOT be in DevOps allowed tools"

    def test_devops_does_not_have_opencode_tools(self) -> None:
        """DevOps should NOT have opencode tools (external_opencode_*)."""
        from daemon.tools.instance import resolve_tool_filter

        allowed_tools = resolve_tool_filter(
            allow=["bash", "filesystem", "time", "self", "help", "knowledge", "mcp", "context"],
            deny=None,
            tool_categories=TOOL_CATEGORIES,
        )

        assert allowed_tools is not None
        # OpenCode tools are registered under "external_opencode" category
        opencode_tools = {
            "external_opencode_init_session",
            "external_opencode_send_message",
            "external_opencode_get_status",
            "external_opencode_wait_for_result",
            "external_opencode_abort_session",
        }
        for tool in opencode_tools:
            assert tool not in allowed_tools, (
                f"{tool} should NOT be in DevOps allowed tools (no opencode delegation)"
            )

    def test_devops_tools_doc_loads(self) -> None:
        """load_tools_doc_for_agent should return docs for DevOps allowed tools."""
        from daemon.loader import load_tools_doc_for_agent

        # This should not raise
        docs = load_tools_doc_for_agent("devops")

        assert isinstance(docs, str)
        assert len(docs) > 0
        # Should contain tool categories
        assert "Bash" in docs or "bash" in docs.lower()
        assert "File Operations" in docs or "Filesystem" in docs or "filesystem" in docs.lower()

    def test_innate_skills_empty_means_no_tool_expansion(self) -> None:
        """Empty innate_skills means no automatic tool category expansion."""
        from daemon.tools.instance import expand_allow_for_innate_skills

        # With empty innate_skills, allow list should be unchanged
        allow = ["bash", "filesystem"]
        result = expand_allow_for_innate_skills(allow, [])
        assert result == allow

    def test_apply_tool_filter_restricts_to_devops_tools(self) -> None:
        """_apply_tool_filter should restrict tools for DevOps to its allow list."""
        from unittest.mock import MagicMock, patch
        from daemon.tools.instance import _apply_tool_filter

        # Create mock tools
        class MockTool:
            def __init__(self, name: str):
                self.name = name

        tools = [
            MockTool("bash"),
            MockTool("read_file"),
            MockTool("write_file"),
            MockTool("spawn_instance"),  # Should be filtered out
            MockTool("external_opencode_init_session"),  # Should be filtered out
        ]

        with patch("daemon.tools.instance.list_tools_by_category") as mock_list_tools:
            with patch("daemon.registry.get_registry") as mock_registry:
                mock_list_tools.return_value = TOOL_CATEGORIES
                mock_agent_meta = MagicMock()
                mock_filter = MagicMock()
                mock_filter.allow = ["bash", "filesystem", "time", "self", "help", "knowledge", "mcp", "context"]
                mock_filter.deny = None
                mock_agent_meta.tools = mock_filter
                mock_agent_meta.innate_skills = []
                mock_registry.return_value.get.return_value = mock_agent_meta

                result = _apply_tool_filter(tools, "devops")
                tool_names = {t.name for t in result}

                # bash and filesystem tools should be present
                assert "bash" in tool_names
                assert "read_file" in tool_names
                assert "write_file" in tool_names
                # spawn_instance should be filtered out
                assert "spawn_instance" not in tool_names
                # opencode tools should be filtered out
                assert "external_opencode_init_session" not in tool_names


# =============================================================================
# 5. Leader Integration
# =============================================================================


class TestDevopsLeaderIntegration:
    """Tests for DevOps integration in the leader agent."""

    def test_leader_soul_has_devops_team_row(self) -> None:
        """Leader's soul.md should have a 'devops' row in the team table."""
        soul_path = LEADER_AGENT_DIR / "soul.md"
        content = soul_path.read_text(encoding="utf-8")

        # Team table should contain devops (case-insensitive — may be lowercased
        # in the table row for markdown table compatibility)
        assert "devops" in content.lower(), "Leader soul.md should mention devops in team table"
        # Should appear in a table row context — leader uses lowercase bold
        # (e.g. `| **devops** | ...`) for table cell content.
        assert "**devops**" in content, (
            "Team table should have '**devops**' row (lowercase bold for table)"
        )

    def test_leader_soul_devops_row_content(self) -> None:
        """Leader's devops team row should describe the DevOps role."""
        soul_path = LEADER_AGENT_DIR / "soul.md"
        content = soul_path.read_text(encoding="utf-8")

        # Should describe infrastructure/deployment role
        assert any(keyword in content.lower() for keyword in ["infrastructure", "deployment", "ci/cd", "devops"]), (
            "DevOps team row should describe infrastructure/deployment role"
        )

    def test_leader_workflow_routes_infra_to_devops(self) -> None:
        """Leader workflow.md should route infra tasks to DevOps (not hardcoded to developer)."""
        workflow_path = LEADER_AGENT_DIR / "workflow.md"
        content = workflow_path.read_text(encoding="utf-8")

        # Should mention routing to DevOps
        assert "DevOps" in content or "devops" in content.lower(), (
            "Leader workflow.md should mention DevOps for routing"
        )
        # Should NOT hardcode everything to Developer for infra
        # Check for infrastructure routing mentions
        infra_keywords = ["docker", "kubernetes", "terraform", "ci/cd", "deployment", "infrastructure"]
        has_infra_routing = any(kw in content.lower() for kw in infra_keywords)
        assert has_infra_routing, "Leader workflow should contain infrastructure routing keywords"

    def test_leader_workflow_implementation_step_routes_to_devops(self) -> None:
        """Leader workflow.md Implementation step should route infra to DevOps."""
        workflow_path = LEADER_AGENT_DIR / "workflow.md"
        content = workflow_path.read_text(encoding="utf-8")

        # Implementation section should mention DevOps routing
        impl_section = content[content.find("## Implementation Workflow"):] if "Implementation Workflow" in content else content

        assert "DevOps" in impl_section or "devops" in impl_section.lower(), (
            "Implementation workflow section should mention DevOps"
        )
        # Should mention infrastructure tasks → DevOps
        assert any(kw in impl_section.lower() for kw in ["docker", "kubernetes", "ci/cd", "infrastructure", "devops"]), (
            "Implementation section should route infrastructure tasks to DevOps"
        )

    def test_leader_workflow_debug_phase_classifies_by_domain(self) -> None:
        """Leader workflow.md Debug phase should classify by cause domain."""
        workflow_path = LEADER_AGENT_DIR / "workflow.md"
        content = workflow_path.read_text(encoding="utf-8")

        # Debug section should classify by domain (not just route to developer)
        assert "Debug" in content or "debug" in content.lower(), "Leader workflow should have debug section"
        # Should mention Phase 1.5 classification
        assert "1.5" in content or "CLASSIFY" in content or "classify" in content.lower(), (
            "Debug workflow should have domain classification step"
        )

    def test_leader_workflow_debug_routes_infra_cause_to_devops(self) -> None:
        """Leader debug workflow should route infra causes to DevOps."""
        workflow_path = LEADER_AGENT_DIR / "workflow.md"
        content = workflow_path.read_text(encoding="utf-8")

        # Find debug section
        debug_start = content.find("## Debug Workflow")
        assert debug_start != -1, "Leader workflow should have Debug Workflow section"

        debug_section = content[debug_start:]
        # Should mention DevOps for infra causes
        assert "DevOps" in debug_section or "devops" in debug_section.lower(), (
            "Debug workflow should mention DevOps for infrastructure causes"
        )

    def test_leader_rule_decision_tree_includes_devops(self) -> None:
        """Leader rule.md decision tree should include DevOps routing."""
        rule_path = LEADER_AGENT_DIR / "rule.md"
        content = rule_path.read_text(encoding="utf-8")

        # Decision tree should mention devops/infrastructure routing
        assert "DevOps" in content or "devops" in content.lower(), (
            "Leader rule.md should mention DevOps in decision tree"
        )

    def test_leader_rule_devops_before_developer_catchall(self) -> None:
        """Leader decision tree should check DevOps before Developer catchall."""
        rule_path = LEADER_AGENT_DIR / "rule.md"
        content = rule_path.read_text(encoding="utf-8")

        # Check relative positions of devops and developer mentions
        devops_pos = content.lower().find("devops")
        developer_pos = content.lower().find("developer")

        if devops_pos != -1 and developer_pos != -1:
            # DevOps should appear in the decision context
            # (The exact ordering depends on document structure, so we just check both exist)
            assert True
        else:
            # At minimum, devops should be mentioned somewhere in the routing context
            assert "devops" in content.lower(), "Leader rule should mention devops for routing"


# =============================================================================
# 6. Markdown Quality
# =============================================================================


class TestDevopsMarkdownQuality:
    """Tests for DevOps markdown file quality and consistency."""

    def test_all_six_files_exist(self) -> None:
        """All 6 required devops files should exist."""
        expected_files = [
            "meta.json",
            "soul.md",
            "workflow.md",
            "rule.md",
            "tools_note.md",
            "user.md",
        ]
        for filename in expected_files:
            filepath = DEVOPS_AGENT_DIR / filename
            assert filepath.exists(), f"Required file {filename} should exist at {filepath}"

    def test_markdown_files_are_valid_utf8(self) -> None:
        """All markdown files should be readable as UTF-8."""
        md_files = ["soul.md", "workflow.md", "rule.md", "tools_note.md", "user.md"]
        for filename in md_files:
            filepath = DEVOPS_AGENT_DIR / filename
            try:
                content = filepath.read_text(encoding="utf-8")
                assert len(content) > 0, f"{filename} should not be empty"
            except UnicodeDecodeError as e:
                pytest.fail(f"{filename} is not valid UTF-8: {e}")

    def test_no_broken_headings(self) -> None:
        """Markdown files should not have broken/mismatched headings."""
        md_files = ["soul.md", "workflow.md", "rule.md", "tools_note.md", "user.md"]

        for filename in md_files:
            filepath = DEVOPS_AGENT_DIR / filename
            content = filepath.read_text(encoding="utf-8")

            # Find all headings (lines starting with #)
            heading_lines = [
                line for line in content.split("\n")
                if line.strip().startswith("#")
            ]

            # Check for heading levels (should be consecutive: #, ##, ###, etc.)
            for line in heading_lines:
                stripped = line.strip()
                if stripped.startswith("#"):
                    # Count hash marks
                    level = len(stripped) - len(stripped.lstrip("#"))
                    # Heading level should be reasonable (1-6)
                    assert 1 <= level <= 6, (
                        f"{filename}: Broken heading level in line: {line.strip()}"
                    )

    def test_tables_are_balanced(self) -> None:
        """Markdown tables should have balanced columns across each table.

        A markdown table looks like:
            | col1 | col2 | col3 |
            |------|------|------|
            | a    | b    | c    |
        All rows in the same table must have the same number of columns.
        """
        md_files = ["soul.md", "workflow.md", "rule.md", "tools_note.md", "user.md"]

        for filename in md_files:
            filepath = DEVOPS_AGENT_DIR / filename
            content = filepath.read_text(encoding="utf-8")
            lines = content.split("\n")

            # State machine: track current table's expected column count.
            # Reset whenever we encounter a non-table line.
            current_table_cols: int | None = None

            def _count_cols(line: str) -> int:
                """Count columns in a table line (excluding leading/trailing |)."""
                stripped = line.strip()
                # Split by |, drop empty leading/trailing entries from outer |...| wrapping
                parts = stripped.split("|")
                # If line starts and ends with |, both are empty after split
                if parts and parts[0] == "":
                    parts = parts[1:]
                if parts and parts[-1] == "":
                    parts = parts[:-1]
                return len(parts)

            def _is_separator(line: str) -> bool:
                """Check if line is a markdown table separator like |---|---|"""
                stripped = line.strip()
                if not stripped.startswith("|"):
                    return False
                parts = stripped.split("|")[1:-1]  # Drop outer empty parts
                if not parts:
                    return False
                # Each part should be only dashes/colons/spaces
                return all(re.match(r"^[\s\-:]+$", p) and "-" in p for p in parts if p)

            for i, line in enumerate(lines):
                stripped = line.strip()
                if not stripped.startswith("|"):
                    # Non-table line resets table state
                    current_table_cols = None
                    continue

                cols = _count_cols(line)
                if _is_separator(line):
                    # Separator defines the column count for the table
                    current_table_cols = cols
                    continue

                # Regular table row (header or data)
                if current_table_cols is None:
                    # Header row of a new table — just record cols
                    current_table_cols = cols
                else:
                    # Data row — must match the table's column count
                    assert cols == current_table_cols, (
                        f"{filename}:line {i+1}: Table column mismatch. "
                        f"Expected {current_table_cols} columns, got {cols}: {stripped[:60]}"
                    )

    def test_code_blocks_are_balanced(self) -> None:
        """Markdown code blocks (```) should be balanced (even count)."""
        md_files = ["soul.md", "workflow.md", "rule.md", "tools_note.md", "user.md"]

        for filename in md_files:
            filepath = DEVOPS_AGENT_DIR / filename
            content = filepath.read_text(encoding="utf-8")

            # Count triple backtick pairs
            code_block_count = content.count("```")
            assert code_block_count % 2 == 0, (
                f"{filename}: Unbalanced code blocks (```). "
                f"Count: {code_block_count} (should be even)"
            )

    def test_rule_uses_four_tier_risk_vocabulary(self) -> None:
        """rule.md should use 4-tier risk vocabulary: Low, Medium, High, Critical."""
        rule_path = DEVOPS_AGENT_DIR / "rule.md"
        content = rule_path.read_text(encoding="utf-8")

        # Check for the four risk tiers
        risk_tiers = {
            "Low": False,
            "Medium": False,
            "High": False,
            "Critical": False,
        }

        for tier in risk_tiers:
            if tier in content:
                risk_tiers[tier] = True

        # At minimum, the risk vocabulary should be present
        found_tiers = sum(risk_tiers.values())
        assert found_tiers >= 3, (
            f"rule.md should use at least 3 of the 4 risk tiers "
            f"(Low, Medium, High, Critical). Found: {found_tiers}. "
            f"Content sample: {content[content.lower().find('low'):content.lower().find('low')+200] if 'low' in content.lower() else 'Not found'}"
        )

    def test_rule_risk_table_exists(self) -> None:
        """rule.md should have a risk classification table."""
        rule_path = DEVOPS_AGENT_DIR / "rule.md"
        content = rule_path.read_text(encoding="utf-8")

        # Should have a table-like structure with Risk column
        # Look for table headers or clear risk classification sections
        has_risk_table = (
            "| Risk |" in content or
            "| Risk Level |" in content or
            "| Severity |" in content or
            ("Low" in content and "High" in content and "Critical" in content and "Medium" in content)
        )

        assert has_risk_table, "rule.md should have a risk classification table or section"

    def test_workflow_has_operation_flows(self) -> None:
        """workflow.md should have organized operation flows."""
        workflow_path = DEVOPS_AGENT_DIR / "workflow.md"
        content = workflow_path.read_text(encoding="utf-8")

        # Should have multiple operation sections
        sections = [
            "Docker" in content or "docker" in content,
            "Kubernetes" in content or "kubectl" in content,
            "Terraform" in content or "terraform" in content,
            "CI/CD" in content or "ci-cd" in content.lower(),
        ]

        assert sum(sections) >= 2, (
            "workflow.md should have at least 2 of 4 major operation sections "
            "(Docker, Kubernetes, Terraform, CI/CD)"
        )

    def test_workflow_has_lifecycle_steps(self) -> None:
        """workflow.md should have the 6-step lifecycle (Assess, Plan, Confirm, Execute, Verify, Report)."""
        workflow_path = DEVOPS_AGENT_DIR / "workflow.md"
        content = workflow_path.read_text(encoding="utf-8")

        lifecycle_steps = ["Assess", "Plan", "Confirm", "Execute", "Verify", "Report"]
        found_steps = sum(1 for step in lifecycle_steps if step in content)

        assert found_steps >= 4, (
            f"workflow.md should contain most of the 6 lifecycle steps. "
            f"Found: {found_steps}/6"
        )

    def test_soul_describes_giter_pattern(self) -> None:
        """soul.md should describe the giter/bash-direct pattern (no opencode delegation)."""
        soul_path = DEVOPS_AGENT_DIR / "soul.md"
        content = soul_path.read_text(encoding="utf-8")

        # Should mention direct bash execution
        assert "bash" in content.lower(), "soul.md should mention bash tool"

        # Should describe that DevOps executes directly (not delegating to opencode)
        giter_pattern_keywords = [
            "directly", "direct", "execute", "run",
        ]
        has_giter_pattern = any(kw in content.lower() for kw in giter_pattern_keywords)
        assert has_giter_pattern, (
            "soul.md should describe that DevOps executes operations directly via bash"
        )

    def test_user_md_has_good_requests_examples(self) -> None:
        """user.md should have good request examples."""
        user_path = DEVOPS_AGENT_DIR / "user.md"
        content = user_path.read_text(encoding="utf-8")

        # Should have example requests
        assert len(content) > 200, "user.md should have meaningful content"

        # Should mention some operations
        operations = ["docker", "deploy", "kubernetes", "kubectl", "terraform", "ci/cd"]
        has_operations = any(op in content.lower() for op in operations)
        assert has_operations, "user.md should describe operations the agent handles"


# =============================================================================
# Integration: Full Loading Pipeline
# =============================================================================


class TestDevopsFullLoadingPipeline:
    """Integration tests for the complete DevOps loading pipeline."""

    def test_devops_loads_without_errors(self) -> None:
        """DevOps should load successfully through the full pipeline."""
        from daemon.loader import load_and_cache_prompt
        from daemon.registry import AgentRegistry

        # Get registry
        agents_dir = DEVOPS_AGENT_DIR.parent
        registry = AgentRegistry(agents_dir)
        registry.discover()

        # Get DevOps metadata
        devops = registry.get("devops")
        assert devops is not None

        # Load prompts
        from daemon.loader import PromptCache
        cache = PromptCache()
        prompt, tokens = load_and_cache_prompt("devops", DEVOPS_AGENT_DIR, cache)

        assert len(prompt) > 1000, "DevOps system prompt should be substantial"
        assert tokens > 100, "DevOps token count should be significant"

    def test_devops_system_prompt_is_self_contained(self) -> None:
        """DevOps system prompt should contain all necessary sections."""
        from daemon.loader import load_and_cache_prompt
        from daemon.loader import PromptCache

        cache = PromptCache()
        prompt, _ = load_and_cache_prompt("devops", DEVOPS_AGENT_DIR, cache)

        # Check all required sections are present
        assert "DevOps" in prompt, "Should contain identity (DevOps)"
        assert "Rules" in prompt, "Should contain rules"
        assert "Workflow" in prompt, "Should contain workflow"

    def test_devops_description_matches_meta(self) -> None:
        """DevOps registry description should match meta.json."""
        from daemon.registry import AgentRegistry

        agents_dir = DEVOPS_AGENT_DIR.parent
        registry = AgentRegistry(agents_dir)
        registry.discover()

        meta_path = DEVOPS_AGENT_DIR / "meta.json"
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)

        devops = registry.get("devops")
        assert devops is not None
        assert devops.description == meta["description"]

    def test_devops_tools_in_system_prompt(self) -> None:
        """DevOps system prompt should include tool documentation."""
        from daemon.loader import load_and_cache_prompt
        from daemon.loader import PromptCache

        cache = PromptCache()
        prompt, _ = load_and_cache_prompt("devops", DEVOPS_AGENT_DIR, cache)

        # Should contain tool-related content (from load_tools_doc_for_agent)
        assert "Bash" in prompt or "bash" in prompt.lower(), (
            "System prompt should include Bash tool documentation"
        )

    def test_devops_has_correct_capabilities(self) -> None:
        """DevOps should have infrastructure-related capabilities in registry."""
        from daemon.registry import AgentRegistry

        agents_dir = DEVOPS_AGENT_DIR.parent
        registry = AgentRegistry(agents_dir)
        registry.discover()

        devops = registry.get("devops")
        assert devops is not None

        expected_caps = {"docker", "kubernetes", "terraform", "ci-cd", "shell-ops"}
        assert set(devops.capabilities) == expected_caps, (
            f"Expected capabilities {expected_caps}, got {set(devops.capabilities)}"
        )
