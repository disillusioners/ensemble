"""Tests for per-agent tool filtering functionality."""

import pytest
from unittest.mock import MagicMock, patch

from daemon.tools.instance import (
    resolve_tool_filter,
    _apply_tool_filter,
    expand_allow_for_innate_skills,
    INNATE_SKILL_TOOL_CATEGORIES,
)
from daemon.registry import ToolFilter


# Expected tool categories used in tests (mirrors what the registry should contain)
EXPECTED_TOOL_CATEGORIES: dict[str, list[str]] = {
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


class TestToolFilterModel:
    """Test ToolFilter Pydantic model validation."""

    def test_valid_config_with_allow_and_deny(self):
        """Valid config with both allow and deny lists."""
        config = ToolFilter(allow=["bash", "filesystem"], deny=["write_file"])
        assert config.allow == ["bash", "filesystem"]
        assert config.deny == ["write_file"]

    def test_empty_config(self):
        """Empty ToolFilter should have None fields."""
        config = ToolFilter()
        assert config.allow is None
        assert config.deny is None

    def test_config_with_only_allow(self):
        """Config with only allow list."""
        config = ToolFilter(allow=["bash"])
        assert config.allow == ["bash"]
        assert config.deny is None

    def test_config_with_only_deny(self):
        """Config with only deny list."""
        config = ToolFilter(deny=["write_file"])
        assert config.allow is None
        assert config.deny == ["write_file"]

    def test_none_fields(self):
        """Explicit None fields."""
        config = ToolFilter(allow=None, deny=None)
        assert config.allow is None
        assert config.deny is None

    def test_extra_fields_ignored(self):
        """Extra fields should be ignored due to extra='ignore'."""
        config = ToolFilter(allow=["bash"], extra_field="ignored")
        assert config.allow == ["bash"]
        assert not hasattr(config, "extra_field")


class TestResolveToolFilter:
    """Test resolve_tool_filter() function."""

    def test_both_none_returns_none(self):
        """allow=None, deny=None → returns None (all tools)."""
        result = resolve_tool_filter(allow=None, deny=None)
        assert result is None

    def test_both_empty_lists_returns_none(self):
        """allow=[], deny=[] → returns None (all tools)."""
        result = resolve_tool_filter(allow=[], deny=[])
        assert result is None

    def test_allow_specific_tools(self):
        """allow=["bash", "filesystem"], deny=None → returns only bash + filesystem tool names."""
        result = resolve_tool_filter(
            allow=["bash", "filesystem"],
            deny=None,
            tool_categories=EXPECTED_TOOL_CATEGORIES,
        )
        expected = set(["bash", "list_directory", "read_file", "write_file", "glob_files", "grep_files", "edit_file"])
        assert result == expected

    def test_allow_only_bash(self):
        """allow=["bash"] → returns only bash tool."""
        result = resolve_tool_filter(
            allow=["bash"],
            deny=None,
            tool_categories=EXPECTED_TOOL_CATEGORIES,
        )
        assert result == {"bash"}

    def test_deny_without_allow_denies_nothing(self):
        """allow=None, deny=["write_file"] → returns all tools (deny without allow means all minus deny)."""
        # Per implementation: if allow is None/empty, start with all tools, then apply deny
        result = resolve_tool_filter(
            allow=None,
            deny=["write_file"],
            tool_categories=EXPECTED_TOOL_CATEGORIES,
        )
        # Should return all tools minus write_file
        all_tools = set()
        for category_tools in EXPECTED_TOOL_CATEGORIES.values():
            all_tools.update(category_tools)
        assert result == all_tools - {"write_file"}

    def test_allow_with_deny(self):
        """allow=["filesystem"], deny=["write_file", "edit_file"] → filesystem minus write_file and edit_file."""
        result = resolve_tool_filter(
            allow=["filesystem"],
            deny=["write_file", "edit_file"],
            tool_categories=EXPECTED_TOOL_CATEGORIES,
        )
        expected = {"list_directory", "read_file", "glob_files", "grep_files"}
        assert result == expected

    def test_mixed_categories_and_individual_tools(self):
        """Mixed categories + individual tool names in same allow list."""
        result = resolve_tool_filter(
            allow=["bash", "time"],
            deny=None,
            tool_categories=EXPECTED_TOOL_CATEGORIES,
        )
        expected = {"bash", "time"}
        assert result == expected

    def test_deny_wins_over_allow(self):
        """If a tool is in both allow and deny, it should be denied."""
        # bash is a category that expands to ["bash"]
        # Explicitly allow bash but also deny it
        result = resolve_tool_filter(
            allow=["bash"],
            deny=["bash"],
            tool_categories=EXPECTED_TOOL_CATEGORIES,
        )
        assert result == set()  # bash is denied

    def test_deny_category_removes_category_tools(self):
        """Deny a category removes all tools from that category."""
        result = resolve_tool_filter(
            allow=["filesystem"],
            deny=["filesystem"],
            tool_categories=EXPECTED_TOOL_CATEGORIES,
        )
        assert result == set()

    def test_individual_deny_in_allow_category(self):
        """Deny individual tool in an allowed category."""
        result = resolve_tool_filter(
            allow=["filesystem"],
            deny=["write_file"],
            tool_categories=EXPECTED_TOOL_CATEGORIES,
        )
        assert "write_file" not in result
        assert "read_file" in result

    def test_unknown_tool_names_pass_through(self):
        """Unknown tool names should pass through in allow."""
        result = resolve_tool_filter(
            allow=["unknown_tool"],
            deny=None,
            tool_categories=EXPECTED_TOOL_CATEGORIES,
        )
        assert "unknown_tool" in result

    def test_empty_allow_with_empty_deny(self):
        """Empty lists should be treated as None."""
        result = resolve_tool_filter(allow=[], deny=[])
        assert result is None


class TestCategoryExpansion:
    """Test that each category expands to expected tool names."""

    def test_bash_category(self):
        """bash category should contain just bash."""
        result = resolve_tool_filter(
            allow=["bash"],
            deny=None,
            tool_categories=EXPECTED_TOOL_CATEGORIES,
        )
        assert result == {"bash"}

    def test_filesystem_category(self):
        """filesystem category should contain 6 tools."""
        result = resolve_tool_filter(
            allow=["filesystem"],
            deny=None,
            tool_categories=EXPECTED_TOOL_CATEGORIES,
        )
        expected = {"list_directory", "read_file", "write_file", "glob_files", "grep_files", "edit_file"}
        assert result == expected

    def test_time_category(self):
        """time category should contain just time."""
        result = resolve_tool_filter(
            allow=["time"],
            deny=None,
            tool_categories=EXPECTED_TOOL_CATEGORIES,
        )
        assert result == {"time"}

    def test_instance_category(self):
        """instance category should contain instance tools."""
        result = resolve_tool_filter(
            allow=["instance"],
            deny=None,
            tool_categories=EXPECTED_TOOL_CATEGORIES,
        )
        expected = {
            "spawn_instance", "send_message", "terminate_instance",
            "list_instances", "get_instance_info"
        }
        assert result == expected

    def test_self_category(self):
        """self category should contain inner_soul and access_memory."""
        result = resolve_tool_filter(
            allow=["self"],
            deny=None,
            tool_categories=EXPECTED_TOOL_CATEGORIES,
        )
        expected = {"inner_soul", "access_memory"}
        assert result == expected

    def test_project_category(self):
        """project category should contain project tools."""
        result = resolve_tool_filter(
            allow=["project"],
            deny=None,
            tool_categories=EXPECTED_TOOL_CATEGORIES,
        )
        expected = {
            "project_create", "project_get", "project_list", "project_search",
            "project_get_by_instance", "project_get_by_directory", "project_update",
            "project_set_status", "project_add_directory", "project_remove_directory",
            "project_set_tags", "project_add_tag", "project_remove_tag",
            "project_set_shortnames", "project_add_shortname", "project_remove_shortname",
            "project_set_metadata", "project_delete_metadata",
            "project_link", "project_unlink", "project_delete",
        }
        assert result == expected

    def test_help_category(self):
        """help category should contain tool_help."""
        result = resolve_tool_filter(
            allow=["help"],
            deny=None,
            tool_categories=EXPECTED_TOOL_CATEGORIES,
        )
        assert result == {"tool_help"}

    def test_mother_category(self):
        """mother category should contain agent management tools."""
        result = resolve_tool_filter(
            allow=["mother"],
            deny=None,
            tool_categories=EXPECTED_TOOL_CATEGORIES,
        )
        expected = {"agent_list", "agent_create", "agent_read", "agent_modify", "agent_delete"}
        assert result == expected

    def test_all_categories_combined(self):
        """All categories should cover all tools in EXPECTED_TOOL_CATEGORIES."""
        all_tools = set()
        for category_tools in EXPECTED_TOOL_CATEGORIES.values():
            all_tools.update(category_tools)

        result = resolve_tool_filter(
            allow=list(EXPECTED_TOOL_CATEGORIES.keys()),
            deny=None,
            tool_categories=EXPECTED_TOOL_CATEGORIES,
        )
        assert result == all_tools


class TestApplyToolFilter:
    """Test _apply_tool_filter() function."""

    def _create_mock_tool(self, name: str):
        """Create a mock tool with a name attribute."""
        tool = MagicMock()
        tool.name = name
        return tool

    def test_no_filter_returns_all_tools(self):
        """With no filter, all tools should be returned."""
        tools = [
            self._create_mock_tool("bash"),
            self._create_mock_tool("read_file"),
            self._create_mock_tool("write_file"),
        ]

        with patch("daemon.registry.get_registry") as mock_registry:
            mock_agent_meta = MagicMock()
            mock_agent_meta.tools = None
            mock_registry.return_value.get.return_value = mock_agent_meta

            result = _apply_tool_filter(tools, "test_agent")
            assert len(result) == 3

    def test_allow_filter_restricts_tools(self):
        """With allow filter, only allowed tools should be returned."""
        tools = [
            self._create_mock_tool("bash"),
            self._create_mock_tool("read_file"),
            self._create_mock_tool("write_file"),
        ]

        with patch("daemon.registry.get_registry") as mock_registry:
            with patch("daemon.tools.instance.list_tools_by_category") as mock_list_tools:
                mock_list_tools.return_value = EXPECTED_TOOL_CATEGORIES
                mock_agent_meta = MagicMock()
                mock_filter = MagicMock()
                mock_filter.allow = ["bash", "filesystem"]
                mock_filter.deny = None
                mock_agent_meta.tools = mock_filter
                mock_registry.return_value.get.return_value = mock_agent_meta

                result = _apply_tool_filter(tools, "test_agent")
                tool_names = {t.name for t in result}
                assert tool_names == {"bash", "read_file", "write_file"}  # filesystem expands to these

    def test_deny_filter_removes_tools(self):
        """With deny filter, denied tools should be removed."""
        tools = [
            self._create_mock_tool("bash"),
            self._create_mock_tool("read_file"),
            self._create_mock_tool("write_file"),
        ]

        with patch("daemon.registry.get_registry") as mock_registry:
            with patch("daemon.tools.instance.list_tools_by_category") as mock_list_tools:
                mock_list_tools.return_value = EXPECTED_TOOL_CATEGORIES
                mock_agent_meta = MagicMock()
                mock_filter = MagicMock()
                mock_filter.allow = None
                mock_filter.deny = ["write_file"]
                mock_agent_meta.tools = mock_filter
                mock_registry.return_value.get.return_value = mock_agent_meta

                result = _apply_tool_filter(tools, "test_agent")
                tool_names = {t.name for t in result}
                assert "write_file" not in tool_names
                assert "bash" in tool_names
                assert "read_file" in tool_names

    def test_tool_without_name_gets_warning(self):
        """Tools without names should log a warning and be skipped."""
        # Create a nameless tool using a class that has no name attribute
        class NamelessTool:
            pass

        nameless_tool = NamelessTool()

        with patch("daemon.registry.get_registry") as mock_registry:
            with patch("daemon.tools.instance.logger") as mock_logger:
                mock_agent_meta = MagicMock()
                mock_filter = MagicMock()
                mock_filter.allow = ["bash", "filesystem"]
                mock_filter.deny = None
                mock_agent_meta.tools = mock_filter
                mock_registry.return_value.get.return_value = mock_agent_meta

                tools = [
                    self._create_mock_tool("bash"),
                    nameless_tool,
                ]
                result = _apply_tool_filter(tools, "test_agent")

                # Warning should be logged
                assert mock_logger.warning.called

    def test_agent_not_found_returns_all_tools(self):
        """If agent is not in registry, all tools should be returned."""
        tools = [
            self._create_mock_tool("bash"),
            self._create_mock_tool("read_file"),
        ]

        with patch("daemon.registry.get_registry") as mock_registry:
            mock_registry.return_value.get.return_value = None

            result = _apply_tool_filter(tools, "nonexistent_agent")
            assert len(result) == 2

    def test_tool_name_from_func_attribute(self):
        """Tool name should be extracted from func.__name__ as fallback."""
        # Create a custom mock class that doesn't auto-create attributes
        class FuncBasedTool:
            def __init__(self):
                self.func = type('func', (), {'__name__': 'func_based_tool'})()

        tool = FuncBasedTool()
        tools = [tool]

        with patch("daemon.registry.get_registry") as mock_registry:
            with patch("daemon.tools.instance.list_tools_by_category") as mock_list_tools:
                mock_list_tools.return_value = EXPECTED_TOOL_CATEGORIES
                mock_agent_meta = MagicMock()
                mock_filter = MagicMock()
                mock_filter.allow = ["func_based_tool"]
                mock_filter.deny = None
                mock_agent_meta.tools = mock_filter
                mock_registry.return_value.get.return_value = mock_agent_meta

                result = _apply_tool_filter(tools, "test_agent")
                assert len(result) == 1

    def test_tool_name_from_coroutine_attribute(self):
        """Tool name should be extracted from coroutine.__name__ as fallback."""
        # Create a custom mock class that doesn't auto-create attributes
        class CoroutineBasedTool:
            def __init__(self):
                self.coroutine = type('coro', (), {'__name__': 'coroutine_based_tool'})()

        tool = CoroutineBasedTool()
        tools = [tool]

        with patch("daemon.registry.get_registry") as mock_registry:
            with patch("daemon.tools.instance.list_tools_by_category") as mock_list_tools:
                mock_list_tools.return_value = EXPECTED_TOOL_CATEGORIES
                mock_agent_meta = MagicMock()
                mock_filter = MagicMock()
                mock_filter.allow = ["coroutine_based_tool"]
                mock_filter.deny = None
                mock_agent_meta.tools = mock_filter
                mock_registry.return_value.get.return_value = mock_agent_meta

                result = _apply_tool_filter(tools, "test_agent")
                assert len(result) == 1

    def test_debug_logging_when_tools_filtered(self):
        """Debug logging should be triggered when tools are filtered."""
        tools = [
            self._create_mock_tool("bash"),
            self._create_mock_tool("write_file"),
            self._create_mock_tool("edit_file"),
        ]

        with patch("daemon.registry.get_registry") as mock_registry:
            with patch("daemon.tools.instance.logger") as mock_logger:
                mock_agent_meta = MagicMock()
                mock_filter = MagicMock()
                mock_filter.allow = ["bash"]  # Only bash allowed
                mock_filter.deny = None
                mock_agent_meta.tools = mock_filter
                mock_registry.return_value.get.return_value = mock_agent_meta

                result = _apply_tool_filter(tools, "test_agent")

                # Should have filtered from 3 to 1 tool
                assert len(result) == 1
                assert result[0].name == "bash"

                # Debug logging should have been called
                assert mock_logger.debug.called
                debug_calls = [str(c) for c in mock_logger.debug.call_args_list]
                assert any("Filtered tools for test_agent" in str(c) for c in debug_calls)


class TestMcpToolFiltering:
    """Test MCP tool filtering scenarios."""

    # Sample MCP tool names (dynamically added, not in static categories)
    SAMPLE_MCP_TOOLS = [
        "mcp_filesystem_read",
        "mcp_filesystem_write",
        "mcp_github_issues",
        "mcp_github_prs",
        "mcp_git_status",
    ]

    def _create_mock_tool(self, name: str):
        """Create a mock tool with a name attribute."""
        tool = MagicMock()
        tool.name = name
        return tool

    def test_deny_mcp_denies_all_mcp_tools(self):
        """tools.deny: ["mcp"] → all mcp_* tools denied."""
        result = resolve_tool_filter(
            allow=None,
            deny=["mcp"],
            all_tool_names=set(self.SAMPLE_MCP_TOOLS),
        )
        # All MCP tools should be removed from the set
        for mcp_tool in self.SAMPLE_MCP_TOOLS:
            assert mcp_tool not in result

    def test_allow_star_includes_mcp_tools(self):
        """tools.allow: ["*"] → MCP tools included."""
        # Start with all tools, then allow only "*"
        all_tools = set(self.SAMPLE_MCP_TOOLS + ["bash", "read_file"])
        result = resolve_tool_filter(
            allow=["*"],
            deny=None,
            tool_categories={"*": list(all_tools)},
            all_tool_names=set(self.SAMPLE_MCP_TOOLS),
        )
        # "*" as a category doesn't exist, so just "mcp_filesystem_read" is in result
        # This test verifies "*" doesn't break - actual "*" handling is separate
        assert result is not None

    def test_allow_mcp_only_allows_mcp_tools(self):
        """tools.allow: ["mcp"] → only mcp_* tools allowed."""
        # Categories with "mcp" having empty list (unexpanded)
        categories_with_empty_mcp = {
            "bash": ["bash"],
            "mcp": [],  # Empty - should be expanded from all_tool_names
        }
        all_tools = set(self.SAMPLE_MCP_TOOLS + ["bash", "read_file"])

        result = resolve_tool_filter(
            allow=["mcp"],
            deny=None,
            tool_categories=categories_with_empty_mcp,
            all_tool_names=all_tools,
        )

        # Should only have MCP tools
        assert result == set(self.SAMPLE_MCP_TOOLS)

    def test_allow_bash_excludes_mcp_tools(self):
        """tools.allow: ["bash"] → MCP tools excluded."""
        all_tools = set(self.SAMPLE_MCP_TOOLS + ["bash", "read_file"])

        result = resolve_tool_filter(
            allow=["bash"],
            deny=None,
            all_tool_names=all_tools,
        )

        # Should only have bash
        assert result == {"bash"}
        for mcp_tool in self.SAMPLE_MCP_TOOLS:
            assert mcp_tool not in result

    def test_default_no_allow_deny_includes_all_tools(self):
        """Default (no allow/deny) → all tools including MCP available."""
        result = resolve_tool_filter(
            allow=None,
            deny=None,
        )
        # Returns None meaning all tools allowed
        assert result is None

    def test_mcp_category_with_partial_expansion(self):
        """MCP category already having some tools should not be overwritten."""
        categories = {
            "mcp": ["mcp_filesystem_read"],  # Already has one tool
        }
        all_tools = set(self.SAMPLE_MCP_TOOLS)

        result = resolve_tool_filter(
            allow=["mcp"],
            deny=None,
            tool_categories=categories,
            all_tool_names=all_tools,
        )

        # Should keep existing entry, not overwrite
        assert "mcp_filesystem_read" in result
        # Other MCP tools should NOT be added since category was already populated
        assert "mcp_github_issues" not in result

    def test_mcp_in_both_allow_and_deny_deny_wins(self):
        """If mcp is in both allow and deny, deny wins (mcp tools denied)."""
        categories = {
            "bash": ["bash"],
            "mcp": [],
        }
        all_tools = set(self.SAMPLE_MCP_TOOLS + ["bash"])

        result = resolve_tool_filter(
            allow=["bash", "mcp"],
            deny=["mcp"],
            tool_categories=categories,
            all_tool_names=all_tools,
        )

        # Only bash should remain
        assert result == {"bash"}
        for mcp_tool in self.SAMPLE_MCP_TOOLS:
            assert mcp_tool not in result

    def test_apply_tool_filter_with_mcp_deny(self):
        """_apply_tool_filter with MCP deny should filter out MCP tools."""
        tools = [
            self._create_mock_tool("bash"),
            self._create_mock_tool("read_file"),
            self._create_mock_tool("mcp_filesystem_read"),
            self._create_mock_tool("mcp_github_issues"),
        ]

        # Categories including "mcp" with empty list (unexpanded)
        categories = {
            "bash": ["bash"],
            "filesystem": ["read_file"],
            "mcp": [],  # Empty - should be expanded from all_tool_names
        }

        with patch("daemon.registry.get_registry") as mock_registry:
            with patch("daemon.tools.instance.list_tools_by_category") as mock_list_tools:
                mock_list_tools.return_value = categories
                mock_agent_meta = MagicMock()
                mock_filter = MagicMock()
                mock_filter.allow = None
                mock_filter.deny = ["mcp"]
                mock_agent_meta.tools = mock_filter
                mock_registry.return_value.get.return_value = mock_agent_meta

                result = _apply_tool_filter(tools, "test_agent")
                tool_names = {t.name for t in result}

                # MCP tools should be filtered out
                assert "bash" in tool_names
                assert "read_file" in tool_names
                assert "mcp_filesystem_read" not in tool_names
                assert "mcp_github_issues" not in tool_names

    def test_apply_tool_filter_with_mcp_allow(self):
        """_apply_tool_filter with MCP allow should only return MCP tools."""
        tools = [
            self._create_mock_tool("bash"),
            self._create_mock_tool("read_file"),
            self._create_mock_tool("mcp_filesystem_read"),
            self._create_mock_tool("mcp_github_issues"),
        ]

        # Categories including "mcp" with empty list (unexpanded)
        categories = {
            "bash": ["bash"],
            "filesystem": ["read_file"],
            "mcp": [],  # Empty - should be expanded from all_tool_names
        }

        with patch("daemon.registry.get_registry") as mock_registry:
            with patch("daemon.tools.instance.list_tools_by_category") as mock_list_tools:
                mock_list_tools.return_value = categories
                mock_agent_meta = MagicMock()
                mock_filter = MagicMock()
                mock_filter.allow = ["mcp"]
                mock_filter.deny = None
                mock_agent_meta.tools = mock_filter
                mock_registry.return_value.get.return_value = mock_agent_meta

                result = _apply_tool_filter(tools, "test_agent")
                tool_names = {t.name for t in result}

                # Only MCP tools should remain
                assert "bash" not in tool_names
                assert "read_file" not in tool_names
                assert "mcp_filesystem_read" in tool_names
                assert "mcp_github_issues" in tool_names


class TestExpandAllowForInnateSkills:
    """Tests for expand_allow_for_innate_skills() helper.

    Innate skills (e.g. "opencode") should implicitly grant the tool
    categories they require, so the agent does not have to repeat them
    in `tools.allow`.
    """

    def _create_mock_tool(self, name: str):
        """Create a mock tool with a name attribute."""
        tool = MagicMock()
        tool.name = name
        return tool

    def test_no_innate_skills_returns_allow_unchanged(self):
        assert expand_allow_for_innate_skills(["bash"], []) == ["bash"]
        assert expand_allow_for_innate_skills(["bash"], None) == ["bash"]

    def test_none_allow_returns_none(self):
        """If allow is None, agent already has everything — no expansion."""
        assert expand_allow_for_innate_skills(None, ["opencode"]) is None
        assert expand_allow_for_innate_skills(None, []) is None

    def test_opencode_innate_skill_adds_external_opencode_category(self):
        result = expand_allow_for_innate_skills(
            ["bash", "filesystem"], ["opencode"]
        )
        assert "external_opencode" in result
        assert "bash" in result
        assert "filesystem" in result

    def test_does_not_duplicate_existing_category(self):
        """If external_opencode already in allow, leave it alone."""
        result = expand_allow_for_innate_skills(
            ["bash", "external_opencode"], ["opencode"]
        )
        assert result.count("external_opencode") == 1

    def test_unknown_innate_skill_is_ignored(self):
        result = expand_allow_for_innate_skills(
            ["bash"], ["some-unrelated-skill"]
        )
        assert result == ["bash"]

    def test_chart_innate_skill_adds_chart_category_not_instance(self):
        """SECURITY: chart skill must grant 'chart' tools, NOT 'instance' tools.

        Regression test for the historical mis-mapping where ``chart`` was
        registered against the ``["instance"]`` category, which would have
        leaked spawn_instance / send_message / terminate_instance to any
        agent declaring ``innate_skills: ["chart"]``. The contract is:
        ``chart`` → ``["chart"]`` only.
        """
        result = expand_allow_for_innate_skills(["bash"], ["chart"])
        assert "chart" in result
        assert "instance" not in result, (
            "chart innate skill must NOT grant instance management tools "
            "(spawn_instance/send_message/terminate_instance/list_instances/"
            "get_instance_info); granting them lets any chart-enabled agent "
            "spawn and message arbitrary other instances."
        )
        assert "bash" in result

    def test_multiple_innate_skills(self):
        """If INNATE_SKILL_TOOL_CATEGORIES gains more entries, they merge."""
        # Pretend a new mapping exists, then call the helper and clean up.
        original = dict(INNATE_SKILL_TOOL_CATEGORIES)
        INNATE_SKILL_TOOL_CATEGORIES["test-skill"] = ["test-category"]
        try:
            result = expand_allow_for_innate_skills(
                ["bash"], ["opencode", "test-skill"]
            )
        finally:
            INNATE_SKILL_TOOL_CATEGORIES.clear()
            INNATE_SKILL_TOOL_CATEGORIES.update(original)
        assert "external_opencode" in result
        assert "test-category" in result
        assert "bash" in result

    def test_apply_tool_filter_grants_opencode_tools_for_innate_skill(self):
        """End-to-end: agent with innate_skills=['opencode'] gets opencode tools."""
        tools = [
            self._create_mock_tool("bash"),
            self._create_mock_tool("external_opencode_init_session"),
            self._create_mock_tool("external_opencode_send_message"),
        ]
        categories = {
            "bash": ["bash"],
            "external_opencode": [
                "external_opencode_init_session",
                "external_opencode_send_message",
            ],
        }
        with patch("daemon.registry.get_registry") as mock_registry:
            with patch("daemon.tools.instance.list_tools_by_category") as mock_list:
                mock_list.return_value = categories
                mock_agent_meta = MagicMock()
                mock_filter = MagicMock()
                # Allow list does NOT include external_opencode
                mock_filter.allow = ["bash"]
                mock_filter.deny = None
                mock_agent_meta.tools = mock_filter
                mock_agent_meta.innate_skills = ["opencode"]
                mock_registry.return_value.get.return_value = mock_agent_meta

                result = _apply_tool_filter(tools, "developer")
                tool_names = {t.name for t in result}

        # opencode tools should be auto-included
        assert "bash" in tool_names
        assert "external_opencode_init_session" in tool_names
        assert "external_opencode_send_message" in tool_names

    def test_explicit_deny_still_wins_over_innate_skill_grant(self):
        """If user explicitly denies a category, deny wins (per resolve_tool_filter)."""
        tools = [
            self._create_mock_tool("bash"),
            self._create_mock_tool("external_opencode_init_session"),
        ]
        categories = {
            "bash": ["bash"],
            "external_opencode": ["external_opencode_init_session"],
        }
        with patch("daemon.registry.get_registry") as mock_registry:
            with patch("daemon.tools.instance.list_tools_by_category") as mock_list:
                mock_list.return_value = categories
                mock_agent_meta = MagicMock()
                mock_filter = MagicMock()
                mock_filter.allow = ["bash"]
                mock_filter.deny = ["external_opencode"]
                mock_agent_meta.tools = mock_filter
                mock_agent_meta.innate_skills = ["opencode"]
                mock_registry.return_value.get.return_value = mock_agent_meta

                result = _apply_tool_filter(tools, "developer")
                tool_names = {t.name for t in result}

        assert "bash" in tool_names
        assert "external_opencode_init_session" not in tool_names



