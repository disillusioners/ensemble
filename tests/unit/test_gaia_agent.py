"""Comprehensive tests for the Gaia agent.

Tests Gaia agent discovery, loading, tool filtering, and script accessibility.
All tests run in the unit test environment with langgraph mocks from conftest.py.
"""

import json
from pathlib import Path

import pytest


# Path constants
GAIA_AGENT_DIR = Path(__file__).parent.parent.parent / "agents" / "gaia"

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
# 1. Meta.json Validation
# =============================================================================


class TestGaiaMetaJsonValidation:
    """Tests for Gaia's meta.json structure and content."""

    def test_meta_json_exists(self) -> None:
        """meta.json file should exist in the Gaia agent directory."""
        meta_path = GAIA_AGENT_DIR / "meta.json"
        assert meta_path.exists(), f"meta.json not found at {meta_path}"

    def test_meta_json_is_valid_json(self) -> None:
        """meta.json should be parseable as valid JSON."""
        meta_path = GAIA_AGENT_DIR / "meta.json"
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        assert isinstance(meta, dict)

    def test_required_fields_exist(self) -> None:
        """meta.json should contain all required fields."""
        meta_path = GAIA_AGENT_DIR / "meta.json"
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)

        required_fields = ["id", "name", "description"]
        for field in required_fields:
            assert field in meta, f"Required field '{field}' missing from meta.json"

    def test_agent_name_is_gaia(self) -> None:
        """Agent id and name should be 'gaia' / 'Gaia'."""
        meta_path = GAIA_AGENT_DIR / "meta.json"
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)

        assert meta.get("id") == "gaia", f"Agent id should be 'gaia', got '{meta.get('id')}'"
        assert meta.get("name") == "Gaia", f"Agent name should be 'Gaia', got '{meta.get('name')}'"

    def test_field_types_are_correct(self) -> None:
        """meta.json field types should match expected types."""
        meta_path = GAIA_AGENT_DIR / "meta.json"
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)

        assert isinstance(meta.get("id"), str), "id should be a string"
        assert isinstance(meta.get("name"), str), "name should be a string"
        assert isinstance(meta.get("description"), str), "description should be a string"
        assert isinstance(meta.get("icon"), str), "icon should be a string"
        assert isinstance(meta.get("color"), str), "color should be a string"
        assert isinstance(meta.get("system"), bool), "system should be a boolean"
        assert isinstance(meta.get("capabilities"), list), "capabilities should be a list"
        assert isinstance(meta.get("tags"), list), "tags should be a list"
        assert isinstance(meta.get("innate_skills"), list), "innate_skills should be a list"
        assert "tools" in meta, "tools field should be present"

    def test_tools_config_structure(self) -> None:
        """tools configuration should have the expected structure."""
        meta_path = GAIA_AGENT_DIR / "meta.json"
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)

        tools_config = meta.get("tools")
        assert tools_config is not None, "tools config should not be None"
        assert "allow" in tools_config, "tools config should have 'allow' key"
        assert isinstance(tools_config["allow"], list), "'allow' should be a list"

    def test_tools_allowed_list(self) -> None:
        """tools.allow should contain bash, filesystem, and help."""
        meta_path = GAIA_AGENT_DIR / "meta.json"
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)

        tools_config = meta.get("tools", {})
        allowed = tools_config.get("allow", [])

        assert "bash" in allowed, "bash should be in allowed tools"
        assert "filesystem" in allowed, "filesystem should be in allowed tools"
        assert "help" in allowed, "help should be in allowed tools"

    def test_llm_model_can_be_null(self) -> None:
        """llm_model field should exist and be nullable."""
        meta_path = GAIA_AGENT_DIR / "meta.json"
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)

        # llm_model can be null (no override)
        assert "llm_model" in meta, "llm_model field should be present"
        assert meta["llm_model"] is None, "llm_model should be null for Gaia"


# =============================================================================
# 2. Agent Registry Discovery
# =============================================================================


class TestGaiaRegistryDiscovery:
    """Tests for Gaia agent discovery via AgentRegistry."""

    def test_gaia_discovered_in_real_registry(self) -> None:
        """Gaia should be discovered when scanning the real agents directory."""
        from daemon.registry import AgentRegistry

        agents_dir = GAIA_AGENT_DIR.parent
        registry = AgentRegistry(agents_dir)
        registry.discover()

        assert registry.exists("gaia"), "Gaia should be discovered in the agents directory"

    def test_gaia_in_agent_list(self) -> None:
        """Gaia should appear in the list of all agents."""
        from daemon.registry import AgentRegistry

        agents_dir = GAIA_AGENT_DIR.parent
        registry = AgentRegistry(agents_dir)
        registry.discover()

        agents = registry.list_all()
        agent_ids = {a.id for a in agents}
        assert "gaia" in agent_ids, "gaia should be in the list of agents"

    def test_gaia_metadata_loaded_correctly(self) -> None:
        """Gaia metadata loaded from registry should match meta.json."""
        from daemon.registry import AgentRegistry

        agents_dir = GAIA_AGENT_DIR.parent
        registry = AgentRegistry(agents_dir)
        registry.discover()

        gaia = registry.get("gaia")
        assert gaia is not None, "Gaia should be retrievable from registry"
        assert gaia.id == "gaia"
        assert gaia.name == "Gaia"
        assert gaia.description.startswith("Environment setup")
        assert gaia.icon == "🌍"
        assert gaia.color == "accent-green"
        assert gaia.version == "1.0.0"
        assert gaia.system is False
        assert gaia.path == GAIA_AGENT_DIR

    def test_gaia_tools_config_in_registry(self) -> None:
        """Gaia's tool filter should be correctly loaded into registry."""
        from daemon.registry import AgentRegistry, ToolFilter

        agents_dir = GAIA_AGENT_DIR.parent
        registry = AgentRegistry(agents_dir)
        registry.discover()

        gaia = registry.get("gaia")
        assert gaia is not None
        assert gaia.tools is not None
        assert isinstance(gaia.tools, ToolFilter)
        assert gaia.tools.allow == ["bash", "filesystem", "help", "mcp", "system"]
        assert gaia.tools.deny is None

    def test_gaia_tags_and_capabilities(self) -> None:
        """Gaia should have correct tags and capabilities."""
        from daemon.registry import AgentRegistry

        agents_dir = GAIA_AGENT_DIR.parent
        registry = AgentRegistry(agents_dir)
        registry.discover()

        gaia = registry.get("gaia")
        assert gaia is not None
        assert "environment" in gaia.tags
        assert "setup" in gaia.tags
        assert "dependencies" in gaia.tags
        assert gaia.capabilities == []


# =============================================================================
# 3. Agent Loading (System Prompt Composition)
# =============================================================================


class TestGaiaAgentLoading:
    """Tests for Gaia agent loading and system prompt composition."""

    def test_load_gaia_prompts(self) -> None:
        """All Gaia markdown files should be loadable as prompts."""
        from daemon.loader import load_agent_prompts

        prompts = load_agent_prompts(GAIA_AGENT_DIR)

        # Should have soul, rule, workflow, tools (from tools_note.md)
        assert "soul" in prompts, "soul.md should be loaded"
        assert "rule" in prompts, "rule.md should be loaded"
        assert "workflow" in prompts, "workflow.md should be loaded"
        assert "tools" in prompts, "tools_note.md (or tools.md) should be loaded"

    def test_soul_content_included(self) -> None:
        """soul.md content should be included in the loaded prompts."""
        from daemon.loader import load_agent_prompts

        prompts = load_agent_prompts(GAIA_AGENT_DIR)

        assert "Gaia" in prompts["soul"]
        assert "environment" in prompts["soul"].lower()

    def test_rule_content_included(self) -> None:
        """rule.md content should be included in the loaded prompts."""
        from daemon.loader import load_agent_prompts

        prompts = load_agent_prompts(GAIA_AGENT_DIR)

        assert "Rules" in prompts["rule"]
        assert "Must" in prompts["rule"]
        assert "Must Not" in prompts["rule"]

    def test_workflow_content_included(self) -> None:
        """workflow.md content should be included in the loaded prompts."""
        from daemon.loader import load_agent_prompts

        prompts = load_agent_prompts(GAIA_AGENT_DIR)

        assert "Workflow" in prompts["workflow"]
        assert "Step" in prompts["workflow"]

    def test_tools_note_content_included(self) -> None:
        """tools_note.md content should be included as the tools prompt."""
        from daemon.loader import load_agent_prompts

        prompts = load_agent_prompts(GAIA_AGENT_DIR)

        # Should use tools_note.md content
        assert "Tools Reference" in prompts["tools"]
        assert "list_directory" in prompts["tools"] or "bash" in prompts["tools"]

    def test_compose_system_prompt_includes_all_sections(self) -> None:
        """Composed system prompt should include soul, rule, workflow, tools."""
        from daemon.loader import compose_system_prompt, load_agent_prompts

        prompts = load_agent_prompts(GAIA_AGENT_DIR)
        system_prompt = compose_system_prompt(prompts)

        # All sections should be present
        assert "Gaia" in system_prompt, "soul content should be in system prompt"
        assert "Rules" in system_prompt, "rule content should be in system prompt"
        assert "Workflow" in system_prompt, "workflow content should be in system prompt"
        assert "Tools" in system_prompt or "list_directory" in system_prompt, "tools content should be in system prompt"

    def test_system_prompt_composition_order(self) -> None:
        """System prompt should follow correct order: soul → rule → tools → workflow."""
        from daemon.loader import compose_system_prompt, load_agent_prompts

        prompts = load_agent_prompts(GAIA_AGENT_DIR)
        system_prompt = compose_system_prompt(prompts)

        # Find positions
        soul_pos = system_prompt.find("# Gaia")
        rule_pos = system_prompt.find("# Rules")
        tools_pos = system_prompt.find("Tools Reference")
        workflow_pos = system_prompt.find("# Workflow")

        assert soul_pos != -1, "soul (Gaia) should be in system prompt"
        assert rule_pos != -1, "rule should be in system prompt"
        assert tools_pos != -1, "tools should be in system prompt"
        assert workflow_pos != -1, "workflow should be in system prompt"

        # Order should be soul → rule → tools → workflow
        assert soul_pos < rule_pos, "soul should come before rule"
        assert rule_pos < tools_pos, "rule should come before tools"
        assert tools_pos < workflow_pos, "tools should come before workflow"

    def test_load_and_cache_prompt_works(self) -> None:
        """load_and_cache_prompt should successfully load Gaia's prompt."""
        from daemon.loader import PromptCache, load_and_cache_prompt

        cache = PromptCache()
        prompt, tokens = load_and_cache_prompt("gaia", GAIA_AGENT_DIR, cache)

        assert isinstance(prompt, str)
        assert len(prompt) > 0
        assert tokens > 0
        assert "Gaia" in prompt
        assert "Rules" in prompt

    def test_load_and_cache_prompt_caches(self) -> None:
        """load_and_cache_prompt should cache the result."""
        from daemon.loader import PromptCache, load_and_cache_prompt

        cache = PromptCache()

        # First load
        prompt1, tokens1 = load_and_cache_prompt("gaia", GAIA_AGENT_DIR, cache)

        # Should be cached
        cached = cache.get("gaia")
        assert cached is not None
        assert cached[0] == prompt1
        assert cached[1] == tokens1

    def test_no_errors_during_loading(self) -> None:
        """Loading Gaia should not raise any exceptions."""
        from daemon.loader import load_agent_prompts, compose_system_prompt, PromptCache, load_and_cache_prompt

        # Should not raise
        prompts = load_agent_prompts(GAIA_AGENT_DIR)
        system_prompt = compose_system_prompt(prompts)
        cache = PromptCache()
        prompt, tokens = load_and_cache_prompt("gaia", GAIA_AGENT_DIR, cache)

        assert len(prompt) > 0


# =============================================================================
# 4. Tool Filtering
# =============================================================================


class TestGaiaToolFiltering:
    """Tests for Gaia's tool filtering configuration."""

    def test_gaia_tool_filter_from_registry(self) -> None:
        """Gaia should have a tool filter configured in the registry."""
        from daemon.registry import AgentRegistry

        agents_dir = GAIA_AGENT_DIR.parent
        registry = AgentRegistry(agents_dir)
        registry.discover()

        gaia = registry.get("gaia")
        assert gaia is not None
        assert gaia.tools is not None
        assert gaia.tools.allow == ["bash", "filesystem", "help", "mcp", "system"]

    def test_gaia_tools_doc_loads(self) -> None:
        """load_tools_doc_for_agent should return docs for Gaia's allowed tools."""
        from daemon.loader import load_tools_doc_for_agent

        # This should not raise
        docs = load_tools_doc_for_agent("gaia")

        assert isinstance(docs, str)
        assert len(docs) > 0
        # Should contain tool categories
        assert "Bash" in docs or "bash" in docs.lower()
        assert "File Operations" in docs or "Filesystem" in docs or "filesystem" in docs.lower()
        assert "Help" in docs or "help" in docs.lower()

    def test_gaia_has_bash_tool(self) -> None:
        """Gaia should have bash tool available."""
        from daemon.tools.instance import resolve_tool_filter

        allowed_tools = resolve_tool_filter(
            allow=["bash", "filesystem", "help"],
            deny=None,
            tool_categories=TOOL_CATEGORIES,
        )

        assert allowed_tools is not None
        assert "bash" in allowed_tools

    def test_gaia_has_filesystem_tools(self) -> None:
        """Gaia should have filesystem tools available."""
        from daemon.tools.instance import resolve_tool_filter

        allowed_tools = resolve_tool_filter(
            allow=["bash", "filesystem", "help"],
            deny=None,
            tool_categories=TOOL_CATEGORIES,
        )

        assert allowed_tools is not None
        filesystem_tools = {"list_directory", "read_file", "write_file", "glob_files", "grep_files", "edit_file"}
        for tool in filesystem_tools:
            assert tool in allowed_tools, f"{tool} should be in Gaia's allowed tools"

    def test_gaia_has_help_tool(self) -> None:
        """Gaia should have help tool available."""
        from daemon.tools.instance import resolve_tool_filter

        allowed_tools = resolve_tool_filter(
            allow=["bash", "filesystem", "help"],
            deny=None,
            tool_categories=TOOL_CATEGORIES,
        )

        assert allowed_tools is not None
        assert "tool_help" in allowed_tools

    def test_gaia_does_not_have_instance_tools(self) -> None:
        """Gaia should NOT have instance management tools."""
        from daemon.tools.instance import resolve_tool_filter

        allowed_tools = resolve_tool_filter(
            allow=["bash", "filesystem", "help"],
            deny=None,
            tool_categories=TOOL_CATEGORIES,
        )

        assert allowed_tools is not None
        instance_tools = {"spawn_instance", "send_message", "terminate_instance", "list_instances", "get_instance_info"}
        for tool in instance_tools:
            assert tool not in allowed_tools, f"{tool} should NOT be in Gaia's allowed tools"

    def test_gaia_does_not_have_project_tools(self) -> None:
        """Gaia should NOT have project management tools."""
        from daemon.tools.instance import resolve_tool_filter

        allowed_tools = resolve_tool_filter(
            allow=["bash", "filesystem", "help"],
            deny=None,
            tool_categories=TOOL_CATEGORIES,
        )

        assert allowed_tools is not None
        project_tools = {"project_create", "project_get", "project_list", "project_search"}
        for tool in project_tools:
            assert tool not in allowed_tools, f"{tool} should NOT be in Gaia's allowed tools"

    def test_gaia_does_not_have_self_tools(self) -> None:
        """Gaia should NOT have self-modification tools."""
        from daemon.tools.instance import resolve_tool_filter

        allowed_tools = resolve_tool_filter(
            allow=["bash", "filesystem", "help"],
            deny=None,
            tool_categories=TOOL_CATEGORIES,
        )

        assert allowed_tools is not None
        self_tools = {"inner_soul", "access_memory"}
        for tool in self_tools:
            assert tool not in allowed_tools, f"{tool} should NOT be in Gaia's allowed tools"

    def test_gaia_tool_filter_config_parsed_correctly(self) -> None:
        """Gaia's tool filter should be parseable from meta.json."""
        from daemon.registry import ToolFilter

        meta_path = GAIA_AGENT_DIR / "meta.json"
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)

        tools_config = meta.get("tools")
        tool_filter = ToolFilter.model_validate(tools_config)

        assert tool_filter.allow == ["bash", "filesystem", "help", "mcp", "system"]
        assert tool_filter.deny is None


# =============================================================================
# 5. Script Accessibility
# =============================================================================


class TestGaiaScriptAccessibility:
    """Tests for Gaia's setup scripts accessibility."""

    def test_scripts_directory_exists(self) -> None:
        """scripts/ directory should exist in Gaia agent."""
        scripts_dir = GAIA_AGENT_DIR / "scripts"
        assert scripts_dir.exists(), f"scripts directory not found at {scripts_dir}"
        assert scripts_dir.is_dir(), "scripts should be a directory"

    def test_npx_script_exists(self) -> None:
        """npx.md script should exist in the scripts directory."""
        npx_script = GAIA_AGENT_DIR / "scripts" / "npx.md"
        assert npx_script.exists(), f"npx.md script not found at {npx_script}"

    def test_npx_script_is_readable(self) -> None:
        """npx.md script should be readable."""
        npx_script = GAIA_AGENT_DIR / "scripts" / "npx.md"
        content = npx_script.read_text(encoding="utf-8")
        assert isinstance(content, str)
        assert len(content) > 0

    def test_npx_script_has_content(self) -> None:
        """npx.md script should have meaningful content."""
        npx_script = GAIA_AGENT_DIR / "scripts" / "npx.md"
        content = npx_script.read_text(encoding="utf-8")

        # Should contain npx/Node.js related content
        assert len(content) > 50, "npx.md should have meaningful content"
        # Check for key terms (case insensitive)
        content_lower = content.lower()
        assert any(term in content_lower for term in ["npx", "node", "npm", "javascript"]), \
            "npx.md should contain npx/Node.js related content"

    def test_npx_script_referenced_in_workflow(self) -> None:
        """npx.md should be referenced in the workflow documentation."""
        workflow_path = GAIA_AGENT_DIR / "workflow.md"
        content = workflow_path.read_text(encoding="utf-8")

        # Workflow should mention the scripts directory
        assert "scripts" in content.lower(), "workflow.md should reference scripts directory"
        # And ideally mention npx
        assert "npx" in content.lower(), "workflow.md should reference npx"

    def test_npx_script_referenced_in_rule(self) -> None:
        """npx.md should be referenced in the rule documentation."""
        rule_path = GAIA_AGENT_DIR / "rule.md"
        content = rule_path.read_text(encoding="utf-8")

        # Rules should mention reading scripts
        assert "script" in content.lower() or "read" in content.lower(), \
            "rule.md should mention reading scripts"

    def test_scripts_directory_listable(self) -> None:
        """scripts/ directory should be listable."""
        scripts_dir = GAIA_AGENT_DIR / "scripts"
        entries = list(scripts_dir.iterdir())

        assert len(entries) >= 1, "scripts directory should have at least one script"
        script_names = [e.name for e in entries]
        assert "npx.md" in script_names, "npx.md should be in scripts directory"

    def test_no_symlinks_in_scripts(self) -> None:
        """scripts/ should not contain symlinks (security)."""
        scripts_dir = GAIA_AGENT_DIR / "scripts"
        for entry in scripts_dir.iterdir():
            if entry.is_symlink():
                pytest.fail(f"Script {entry.name} is a symlink, which should be avoided for security")

    def test_scripts_are_markdown_files(self) -> None:
        """All scripts should be markdown files (.md extension)."""
        scripts_dir = GAIA_AGENT_DIR / "scripts"
        for entry in scripts_dir.iterdir():
            if entry.is_file():
                assert entry.suffix == ".md", f"Script {entry.name} should be a markdown file (.md)"


# =============================================================================
# Integration: Full Loading Pipeline
# =============================================================================


class TestGaiaFullLoadingPipeline:
    """Integration tests for the complete Gaia loading pipeline."""

    def test_gaia_loads_without_errors(self) -> None:
        """Gaia should load successfully through the full pipeline."""
        from daemon.loader import load_and_cache_prompt
        from daemon.registry import AgentRegistry

        # Get registry
        agents_dir = GAIA_AGENT_DIR.parent
        registry = AgentRegistry(agents_dir)
        registry.discover()

        # Get Gaia metadata
        gaia = registry.get("gaia")
        assert gaia is not None

        # Load prompts
        from daemon.loader import PromptCache
        cache = PromptCache()
        prompt, tokens = load_and_cache_prompt("gaia", GAIA_AGENT_DIR, cache)

        assert len(prompt) > 1000, "Gaia's system prompt should be substantial"
        assert tokens > 100, "Gaia's token count should be significant"

    def test_gaia_system_prompt_is_self_contained(self) -> None:
        """Gaia's system prompt should contain all necessary sections."""
        from daemon.loader import load_and_cache_prompt
        from daemon.loader import PromptCache

        cache = PromptCache()
        prompt, _ = load_and_cache_prompt("gaia", GAIA_AGENT_DIR, cache)

        # Check all required sections are present
        assert "Gaia" in prompt, "Should contain identity (Gaia)"
        assert "Rules" in prompt, "Should contain rules"
        assert "Workflow" in prompt, "Should contain workflow"
        assert ("Tools" in prompt or "tools" in prompt), "Should contain tools reference"

    def test_gaia_description_matches_meta(self) -> None:
        """Gaia's registry description should match meta.json."""
        from daemon.registry import AgentRegistry

        agents_dir = GAIA_AGENT_DIR.parent
        registry = AgentRegistry(agents_dir)
        registry.discover()

        meta_path = GAIA_AGENT_DIR / "meta.json"
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)

        gaia = registry.get("gaia")
        assert gaia.description == meta["description"]
