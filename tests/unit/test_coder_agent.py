"""Comprehensive tests for the Coder agent.

Tests Coder agent discovery, loading, tool filtering, and prompt composition.
Coder is a direct-coding agent that works with files and bash WITHOUT delegating
to OpenCode. It has only soul.md (no rule.md, no workflow.md, no tools_note.md).

All tests run in the unit test environment with langgraph mocks from conftest.py.
"""

import json
from pathlib import Path

import pytest


# Path constants
CODER_AGENT_DIR = Path(__file__).parent.parent.parent / "agents" / "coder"

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
}


# =============================================================================
# 1. Agent Auto-Discovery
# =============================================================================


class TestCoderAutoDiscovery:
    """Tests for Coder agent auto-discovery via AgentRegistry."""

    def test_coder_directory_exists(self) -> None:
        assert CODER_AGENT_DIR.exists()
        assert CODER_AGENT_DIR.is_dir()

    def test_coder_not_in_skip_dirs(self) -> None:
        from daemon.registry import SKIP_DIRS
        assert "coder" not in SKIP_DIRS

    def test_coder_discovered_in_registry(self) -> None:
        from daemon.registry import AgentRegistry
        registry = AgentRegistry(CODER_AGENT_DIR.parent)
        registry.discover()
        assert registry.exists("coder")

    def test_coder_in_agent_list(self) -> None:
        from daemon.registry import AgentRegistry
        registry = AgentRegistry(CODER_AGENT_DIR.parent)
        registry.discover()
        agent_ids = {a.id for a in registry.list_all()}
        assert "coder" in agent_ids

    def test_coder_metadata_loaded_correctly(self) -> None:
        from daemon.registry import AgentRegistry
        registry = AgentRegistry(CODER_AGENT_DIR.parent)
        registry.discover()
        coder = registry.get("coder")
        assert coder is not None
        assert coder.id == "coder"
        assert coder.name == "Coder"


# =============================================================================
# 2. meta.json Validity
# =============================================================================


class TestCoderMetaJsonValidation:
    """Tests for Coder meta.json structure and content."""

    def test_meta_json_exists(self) -> None:
        meta_path = CODER_AGENT_DIR / "meta.json"
        assert meta_path.exists()

    def test_meta_json_is_valid_json(self) -> None:
        meta_path = CODER_AGENT_DIR / "meta.json"
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        assert isinstance(meta, dict)

    def test_required_fields_exist(self) -> None:
        meta_path = CODER_AGENT_DIR / "meta.json"
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        required_fields = ["id", "name", "description", "icon", "color", "version"]
        for field in required_fields:
            assert field in meta

    def test_agent_id_and_name(self) -> None:
        meta_path = CODER_AGENT_DIR / "meta.json"
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        assert meta.get("id") == "coder"
        assert meta.get("name") == "Coder"

    def test_field_types_are_correct(self) -> None:
        meta_path = CODER_AGENT_DIR / "meta.json"
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
        meta_path = CODER_AGENT_DIR / "meta.json"
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        innate_skills = meta.get("innate_skills", [])
        assert "todo" in innate_skills
        assert "chart" in innate_skills

    def test_innate_skills_does_not_contain_opencode(self) -> None:
        meta_path = CODER_AGENT_DIR / "meta.json"
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        assert "opencode" not in meta.get("innate_skills", [])

    def test_tools_config_structure(self) -> None:
        meta_path = CODER_AGENT_DIR / "meta.json"
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        tools_config = meta.get("tools")
        assert tools_config is not None
        assert "allow" in tools_config
        assert isinstance(tools_config["allow"], list)

    def test_tools_allow_list(self) -> None:
        meta_path = CODER_AGENT_DIR / "meta.json"
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        allowed = meta.get("tools", {}).get("allow", [])
        expected = ["bash", "filesystem", "time", "self", "help", "knowledge", "context"]
        for tool in expected:
            assert tool in allowed

    def test_tools_allow_does_not_contain_opencode(self) -> None:
        meta_path = CODER_AGENT_DIR / "meta.json"
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        allowed = meta.get("tools", {}).get("allow", [])
        assert "external_opencode" not in allowed

    def test_tools_allow_does_not_contain_db(self) -> None:
        meta_path = CODER_AGENT_DIR / "meta.json"
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        allowed = meta.get("tools", {}).get("allow", [])
        assert "db" not in allowed

    def test_tools_allow_does_not_contain_mcp(self) -> None:
        meta_path = CODER_AGENT_DIR / "meta.json"
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        allowed = meta.get("tools", {}).get("allow", [])
        assert "mcp" not in allowed

    def test_tools_config_parsed_by_registry(self) -> None:
        from daemon.registry import AgentRegistry, ToolFilter
        registry = AgentRegistry(CODER_AGENT_DIR.parent)
        registry.discover()
        coder = registry.get("coder")
        assert coder is not None
        assert coder.tools is not None
        assert isinstance(coder.tools, ToolFilter)
        assert coder.tools.deny is None

    def test_registry_tools_match_meta(self) -> None:
        from daemon.registry import AgentRegistry
        registry = AgentRegistry(CODER_AGENT_DIR.parent)
        registry.discover()
        meta_path = CODER_AGENT_DIR / "meta.json"
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        coder = registry.get("coder")
        assert coder is not None
        assert coder.tools is not None
        assert coder.tools.allow == meta["tools"]["allow"]


# =============================================================================
# 3. Prompt Composition
# =============================================================================


class TestCoderPromptComposition:
    """Tests for Coder agent prompt loading and composition."""

    def test_load_coder_prompts(self) -> None:
        from daemon.loader import load_agent_prompts
        prompts = load_agent_prompts(CODER_AGENT_DIR)
        assert "soul" in prompts
        # Coder has only soul.md, no rule.md or workflow.md
        assert "rule" not in prompts
        assert "workflow" not in prompts

    def test_soul_content_included(self) -> None:
        from daemon.loader import load_agent_prompts
        prompts = load_agent_prompts(CODER_AGENT_DIR)
        assert "Coder" in prompts["soul"]
        assert "direct" in prompts["soul"].lower()

    def test_compose_system_prompt_includes_soul(self) -> None:
        from daemon.loader import compose_system_prompt, load_agent_prompts
        prompts = load_agent_prompts(CODER_AGENT_DIR)
        system_prompt = compose_system_prompt(prompts)
        assert "Coder" in system_prompt

    def test_system_prompt_contains_coder_identity(self) -> None:
        from daemon.loader import compose_system_prompt, load_agent_prompts
        prompts = load_agent_prompts(CODER_AGENT_DIR)
        system_prompt = compose_system_prompt(prompts)
        # Identity markers for a coder agent
        identity_markers = ["code", "implementer", "direct"]
        assert any(marker in system_prompt.lower() for marker in identity_markers)

    def test_no_opencode_skill_content_in_system_prompt(self) -> None:
        from daemon.loader import (
            compose_system_prompt,
            load_agent_prompts,
            load_agent_skills,
        )
        meta_path = CODER_AGENT_DIR / "meta.json"
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        skills = load_agent_skills(CODER_AGENT_DIR, meta)
        # Coder does NOT declare "opencode" as innate skill
        assert "opencode" not in skills, f"opencode skill should not be loaded: {list(skills.keys())}"
        prompts = load_agent_prompts(CODER_AGENT_DIR)
        system_prompt = compose_system_prompt(prompts, skills)
        # No opencode injection tools should appear in the composed prompt
        assert "external_opencode_init_session" not in system_prompt
        assert "OpenCode_Skill" not in system_prompt
        # Other innate skills (todo, chart) MAY load - check they did if system has them
        assert isinstance(skills, dict)

    def test_load_and_cache_prompt_works(self) -> None:
        from daemon.loader import PromptCache, load_and_cache_prompt
        cache = PromptCache()
        prompt, tokens = load_and_cache_prompt("coder", CODER_AGENT_DIR, cache)
        assert isinstance(prompt, str)
        assert len(prompt) > 0
        assert tokens > 0
        assert "Coder" in prompt

    def test_no_errors_during_loading(self) -> None:
        from daemon.loader import (
            PromptCache,
            compose_system_prompt,
            load_agent_prompts,
            load_and_cache_prompt,
        )
        prompts = load_agent_prompts(CODER_AGENT_DIR)
        system_prompt = compose_system_prompt(prompts)
        cache = PromptCache()
        prompt, tokens = load_and_cache_prompt("coder", CODER_AGENT_DIR, cache)
        assert len(prompt) > 0
        assert tokens > 0


# =============================================================================
# 4. Tool Configuration
# =============================================================================


class TestCoderToolConfiguration:
    """Tests for Coder tool configuration and filtering."""

    def test_coder_tool_filter_in_registry(self) -> None:
        from daemon.registry import AgentRegistry
        registry = AgentRegistry(CODER_AGENT_DIR.parent)
        registry.discover()
        coder = registry.get("coder")
        assert coder is not None
        assert coder.tools is not None

    def test_coder_has_bash_tool(self) -> None:
        from daemon.tools.instance import resolve_tool_filter
        allowed_tools = resolve_tool_filter(
            allow=["bash", "filesystem", "time", "self", "help", "knowledge", "context"],
            deny=None,
            tool_categories=TOOL_CATEGORIES,
        )
        assert allowed_tools is not None
        assert "bash" in allowed_tools

    def test_coder_has_filesystem_tools(self) -> None:
        from daemon.tools.instance import resolve_tool_filter
        allowed_tools = resolve_tool_filter(
            allow=["bash", "filesystem", "time", "self", "help", "knowledge", "context"],
            deny=None,
            tool_categories=TOOL_CATEGORIES,
        )
        assert allowed_tools is not None
        filesystem_tools = {"list_directory", "read_file", "write_file", "glob_files", "grep_files", "edit_file"}
        for tool in filesystem_tools:
            assert tool in allowed_tools

    def test_coder_has_time_tool(self) -> None:
        from daemon.tools.instance import resolve_tool_filter
        allowed_tools = resolve_tool_filter(
            allow=["bash", "filesystem", "time", "self", "help", "knowledge", "context"],
            deny=None,
            tool_categories=TOOL_CATEGORIES,
        )
        assert allowed_tools is not None
        assert "time" in allowed_tools

    def test_coder_has_self_tools(self) -> None:
        from daemon.tools.instance import resolve_tool_filter
        allowed_tools = resolve_tool_filter(
            allow=["bash", "filesystem", "time", "self", "help", "knowledge", "context"],
            deny=None,
            tool_categories=TOOL_CATEGORIES,
        )
        assert allowed_tools is not None
        self_tools = {"inner_soul", "access_memory"}
        for tool in self_tools:
            assert tool in allowed_tools

    def test_coder_does_not_have_instance_tools(self) -> None:
        from daemon.tools.instance import resolve_tool_filter
        allowed_tools = resolve_tool_filter(
            allow=["bash", "filesystem", "time", "self", "help", "knowledge", "context"],
            deny=None,
            tool_categories=TOOL_CATEGORIES,
        )
        assert allowed_tools is not None
        instance_tools = {"spawn_instance", "send_message", "terminate_instance", "list_instances", "get_instance_info"}
        for tool in instance_tools:
            assert tool not in allowed_tools

    def test_coder_does_not_have_opencode_tools(self) -> None:
        from daemon.tools.instance import resolve_tool_filter
        allowed_tools = resolve_tool_filter(
            allow=["bash", "filesystem", "time", "self", "help", "knowledge", "context"],
            deny=None,
            tool_categories=TOOL_CATEGORIES,
        )
        assert allowed_tools is not None
        opencode_tools = {
            "external_opencode_init_session",
            "external_opencode_send_message",
            "external_opencode_get_status",
            "external_opencode_wait_for_result",
            "external_opencode_abort_session",
        }
        for tool in opencode_tools:
            assert tool not in allowed_tools

    def test_coder_does_not_have_db_tools(self) -> None:
        from daemon.tools.instance import resolve_tool_filter
        allowed_tools = resolve_tool_filter(
            allow=["bash", "filesystem", "time", "self", "help", "knowledge", "context"],
            deny=None,
            tool_categories=TOOL_CATEGORIES,
        )
        assert allowed_tools is not None
        # db category is not in allow, so db tools should not be present
        db_tools = {"db_query", "db_execute", "db_schema"}
        for tool in db_tools:
            assert tool not in allowed_tools

    def test_coder_tools_doc_loads(self, monkeypatch) -> None:
        from daemon.loader import load_tools_doc_for_agent
        from daemon.registry import AgentRegistry
        from daemon.tools._tool_registry import _full_docs, _tool_metadata, clear_registry
        import daemon.registry

        # Initialize and discover an AgentRegistry using the correct agents directory,
        # following the existing project test pattern (see test_coder_discovered_in_registry).
        # load_tools_doc_for_agent() resolves agent metadata through the global
        # registry returned by get_registry(); a freshly discovered registry wired
        # into that global slot guarantees the coder's tool filter is visible.
        agents_dir = CODER_AGENT_DIR.parent
        registry = AgentRegistry(agents_dir)
        registry.discover()

        # monkeypatch handles auto-restore of the module-level registry slot
        # on teardown, so neighbouring tests do not see this leaked registry.
        monkeypatch.setattr(daemon.registry, "_registry", registry)

        # Snapshot the current tool-registration state so we can restore it
        # after the test. clear_registry() mutates these dicts in-place, so
        # monkeypatch (which only handles attribute reassignment) cannot undo
        # the mutation — we restore explicitly in finally below.
        saved_metadata = dict(_tool_metadata)
        saved_full_docs = dict(_full_docs)

        try:
            # Reset _tool_metadata so load_tools_doc_for_agent's
            # _ensure_tool_metadata_populated() runs the full scan. The daemon.tools
            # package transitively registers ``language_skip_check`` at import time,
            # which would otherwise trigger the ensure function's early-return branch
            # and leave bash/filesystem/etc. tools unregistered.
            clear_registry()
            docs = load_tools_doc_for_agent("coder")
            assert isinstance(docs, str)
            assert len(docs) > 0
            # Should contain tool categories that coder has
            assert "Bash" in docs or "bash" in docs.lower()
            assert "File Operations" in docs or "Filesystem" in docs or "filesystem" in docs.lower()
        finally:
            # Restore the tool-registration state to avoid leaking it into
            # other tests that rely on the pre-populated tool metadata.
            _tool_metadata.clear()
            _tool_metadata.update(saved_metadata)
            _full_docs.clear()
            _full_docs.update(saved_full_docs)

    def test_innate_skills_todo_chart_means_chart_expansion(self) -> None:
        from daemon.tools.instance import expand_allow_for_innate_skills
        allow = ["bash", "filesystem", "time", "self", "help", "knowledge", "context"]
        result = expand_allow_for_innate_skills(allow, ["todo", "chart"])
        assert result is not None
        # chart and todo categories should be added
        assert "chart" in result
        assert "todo" in result
        # original allow items still present
        for tool in allow:
            assert tool in result

    def test_apply_tool_filter_restricts_to_coder_tools(self) -> None:
        from unittest.mock import MagicMock, patch
        from daemon.tools.instance import _apply_tool_filter

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

        mock_agent_meta = MagicMock()
        mock_filter = MagicMock()
        mock_filter.allow = ["bash", "filesystem", "time", "self", "help", "knowledge", "context"]
        mock_filter.deny = None
        mock_agent_meta.tools = mock_filter
        mock_agent_meta.innate_skills = ["todo", "chart"]

        with patch("daemon.tools.instance.list_tools_by_category") as mock_list_tools:
            with patch("daemon.registry.get_registry") as mock_registry:
                mock_list_tools.return_value = TOOL_CATEGORIES
                # _apply_tool_filter calls registry.get_resolved (not .get)
                mock_registry.return_value.get_resolved.return_value = mock_agent_meta
                mock_registry.return_value.get_version.return_value = mock_agent_meta

                result = _apply_tool_filter(tools, "coder")
                tool_names = {t.name for t in result}

                # Allowed tools should be present
                assert "bash" in tool_names
                assert "read_file" in tool_names
                assert "write_file" in tool_names
                # spawn_instance should be filtered out
                assert "spawn_instance" not in tool_names
                # opencode tools should be filtered out
                assert "external_opencode_init_session" not in tool_names


# =============================================================================
# 5. No OpenCode Contamination
# =============================================================================


class TestCoderNoOpencodeContamination:
    """Tests ensuring Coder has no opencode contamination."""

    def test_coder_soul_does_not_mention_opencode_delegation(self) -> None:
        soul_path = CODER_AGENT_DIR / "soul.md"
        content = soul_path.read_text(encoding="utf-8")
        lower = content.lower()
        # The soul MAY describe coder in contrast to opencode (e.g. "opposite of
        # developer who orchestrates opencode sessions"). What is NOT allowed
        # is a statement that coder itself uses opencode.
        bad_phrases = [
            "i use opencode",
            "use opencode to",
            "delegating to opencode",
            "via opencode",
            "spawning opencode",
        ]
        for phrase in bad_phrases:
            assert phrase not in lower, (
                f"soul.md should not describe coder as using opencode (found: '{phrase}')"
            )

    def test_coder_has_no_opencode_in_team_members(self) -> None:
        meta_path = CODER_AGENT_DIR / "meta.json"
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        # meta.json should NOT have team_members with opencode
        team_members = meta.get("team_members", [])
        if team_members:
            assert "opencode" not in team_members, (
                f"team_members should not contain 'opencode': {team_members}"
            )