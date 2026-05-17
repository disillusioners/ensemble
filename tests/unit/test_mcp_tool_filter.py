"""Unit tests for MCP tool filtering edge cases.

These tests complement tests/test_tool_filter.py by focusing on:
- is_mcp_tool() function behavior
- Edge cases with MCP category expansion
- Tools that look like MCP but aren't
- Empty/no MCP tool scenarios
"""

import pytest
from unittest.mock import MagicMock, patch

from daemon.tools.instance import resolve_tool_filter
from daemon.mcp.tool_adapter import is_mcp_tool


class TestIsMcpTool:
    """Test the is_mcp_tool() function for correct identification."""

    def test_mcp_tool_with_multiple_underscores(self):
        """MCP tool with multiple underscores should be identified correctly."""
        assert is_mcp_tool("mcp_github_create_issue") is True
        assert is_mcp_tool("mcp_filesystem_read_file") is True
        assert is_mcp_tool("mcp_a_b_c") is True

    def test_mcp_tool_requires_second_underscore_after_prefix(self):
        """MCP tool MUST have underscore in the tool name part (after mcp_).

        The naming convention is: mcp_{server}_{tool} where server and tool
        names are slugified (may have underscores themselves).

        So mcp_github_issues is valid (github_issues has underscore)
        But mcp_github is NOT valid (github has no underscore)
        """
        # Valid: server+tool name has underscore
        assert is_mcp_tool("mcp_github_issues") is True
        assert is_mcp_tool("mcp_filesystem_read") is True
        assert is_mcp_tool("mcp_a_b") is True
        assert is_mcp_tool("mcp_server_tool") is True

        # Invalid: only mcp_ prefix, no underscore in remainder
        assert is_mcp_tool("mcp_github") is False
        assert is_mcp_tool("mcp_git") is False
        assert is_mcp_tool("mcp_server") is False
        assert is_mcp_tool("mcp_test") is False

    def test_tool_not_starts_with_mcp_underscore(self):
        """Tools that don't start with 'mcp_' are NOT MCP tools."""
        assert is_mcp_tool("camcp_tool") is False
        assert is_mcp_tool("amcp_server") is False
        assert is_mcp_tool("mmcp_github") is False
        assert is_mcp_tool("bash") is False
        assert is_mcp_tool("read_file") is False
        assert is_mcp_tool("mcpfile") is False

    def test_tool_starts_with_mcp_but_no_underscore_after(self):
        """Tools starting with 'mcp' but no underscore after 'mcp_' are NOT MCP."""
        assert is_mcp_tool("mcpgithub") is False
        assert is_mcp_tool("mcpfilesystem") is False
        assert is_mcp_tool("mcp") is False

    def test_empty_string(self):
        """Empty string is not an MCP tool."""
        assert is_mcp_tool("") is False

    def test_exactly_mcp_underscore(self):
        """Exactly 'mcp_' is not an MCP tool (no tool name after prefix)."""
        assert is_mcp_tool("mcp_") is False


class TestMcpToolFilterWithEmptyOrNoMcpTools:
    """Test MCP filtering when MCP tools are absent or empty.

    IMPORTANT: MCP expansion only happens when "mcp" is already in tool_categories
    with an empty list. Without passing tool_categories, "mcp" passes through as literal.

    Also note: MCP tools must start with "mcp_" AND have underscore after prefix.
    So "mcp_github" is NOT an MCP tool (no underscore in 'github').
    """

    def test_resolve_filter_with_no_mcp_tools_present(self):
        """Should return empty set when no MCP tools exist in all_tool_names."""
        tool_categories = {
            "bash": ["bash"],
            "mcp": [],  # MCP category exists but empty
        }
        # None of these are actual MCP tools (mcp_github has no underscore after prefix)
        all_tool_names = {"bash", "read_file", "write_file", "grep_files", "mcp_github"}

        result = resolve_tool_filter(
            allow=["mcp"],
            deny=None,
            tool_categories=tool_categories,
            all_tool_names=all_tool_names,
        )

        # MCP expansion finds no actual MCP tools (mcp_* with underscore after)
        assert result == set()

    def test_resolve_filter_with_empty_all_tool_names(self):
        """Should return empty set with empty all_tool_names."""
        tool_categories = {
            "bash": ["bash"],
            "mcp": [],  # MCP category exists but empty
        }

        result = resolve_tool_filter(
            allow=["mcp"],
            deny=None,
            tool_categories=tool_categories,
            all_tool_names=set(),
        )

        assert result == set()

    def test_resolve_filter_deny_mcp_with_no_mcp_tools(self):
        """Deny mcp should return all tools from categories when no actual MCP tools exist.

        Note: mcp_github is NOT an MCP tool (no underscore after prefix),
        so it's never added to the MCP category during expansion.
        """
        tool_categories = {
            "bash": ["bash"],
            "filesystem": ["read_file", "write_file"],
            "mcp": [],  # MCP category exists but will expand to empty
        }
        # mcp_github is NOT in all_tool_names as a valid MCP tool
        all_tool_names = {"bash", "read_file", "write_file", "mcp_github"}

        result = resolve_tool_filter(
            allow=None,
            deny=["mcp"],
            tool_categories=tool_categories,
            all_tool_names=all_tool_names,
        )

        # MCP expansion finds no tools (mcp_github doesn't qualify)
        # Start with all tools from categories, then deny MCP (empty set)
        expected = {"bash", "read_file", "write_file"}
        assert result == expected

    def test_resolve_filter_no_mcp_category_at_all(self):
        """Should handle gracefully when 'mcp' is not in tool_categories.

        When 'mcp' is not in tool_categories at all, it passes through as
        a literal tool name rather than being expanded.
        """
        tool_categories = {
            "bash": ["bash"],
            "filesystem": ["read_file", "write_file"],
        }
        all_tool_names = {"bash", "read_file", "write_file", "mcp_github_issues"}

        result = resolve_tool_filter(
            allow=["mcp"],
            deny=None,
            tool_categories=tool_categories,
            all_tool_names=all_tool_names,
        )

        # Since "mcp" is not in tool_categories, it's treated as literal tool name
        assert result == {"mcp"}

    def test_resolve_filter_mcp_category_not_empty_no_expansion(self):
        """MCP category already populated should NOT be overwritten."""
        tool_categories = {
            "mcp": ["mcp_specific_tool"],
        }
        all_tool_names = {
            "mcp_specific_tool",
            "mcp_github_issues",
            "mcp_filesystem_read",
        }

        result = resolve_tool_filter(
            allow=["mcp"],
            deny=None,
            tool_categories=tool_categories,
            all_tool_names=all_tool_names,
        )

        # Should keep pre-existing tool, NOT expand to all MCP tools
        assert result == {"mcp_specific_tool"}
        assert "mcp_github_issues" not in result
        assert "mcp_filesystem_read" not in result


class TestMcpToolFilterDenyWithAllowStar:
    """Test deny: ["mcp"] with allow: ["*"] scenario."""

    def test_allow_star_deny_mcp(self):
        """allow=["*"] with deny=["mcp"] should exclude MCP, include others.

        Note: '*' is not a known category, so it's passed through as a literal.
        This means '*' doesn't mean "all tools" - it means the literal '*' tool.
        """
        tool_categories = {
            "bash": ["bash"],
            "filesystem": ["read_file", "write_file"],
            "mcp": [],  # Empty - will be expanded from all_tool_names
        }
        all_tool_names = {
            "bash",
            "read_file",
            "write_file",
            "mcp_github_issues",
            "mcp_filesystem_read",
        }

        result = resolve_tool_filter(
            allow=["*"],
            deny=["mcp"],
            tool_categories=tool_categories,
            all_tool_names=all_tool_names,
        )

        # '*' passes through as literal (not expanded to all tools)
        assert "*" in result
        # MCP tools should be denied
        assert "mcp_github_issues" not in result
        assert "mcp_filesystem_read" not in result


class TestMcpToolFilterEdgeCases:
    """Additional edge cases for MCP tool filtering."""

    def test_tool_named_mcp_underscore_is_mcp(self):
        """Tool named 'mcp_something_other' is correctly identified as MCP.

        MCP tools must have underscore in the tool name part:
        - mcp_custom → False (no underscore in 'custom')
        - mcp_a_b → True (underscore in 'a_b')
        """
        # These ARE MCP tools (underscore in remainder)
        assert is_mcp_tool("mcp_a_b") is True
        assert is_mcp_tool("mcp_server_tool") is True

        # These are NOT MCP tools (no underscore in remainder)
        assert is_mcp_tool("mcp_custom") is False
        assert is_mcp_tool("mcp_github") is False

    def test_tool_named_mcp_no_underscore(self):
        """Tool literally named 'mcpsomething' is NOT MCP (no underscore after prefix)."""
        assert is_mcp_tool("mcpgeneric") is False
        assert is_mcp_tool("mcp") is False
        assert is_mcp_tool("mcpgithub") is False

    def test_tool_named_camcp_excluded(self):
        """Tool named 'camcp_*' should NOT be identified as MCP (doesn't start with 'mcp_')."""
        assert is_mcp_tool("camcp_tool") is False
        assert is_mcp_tool("camcp_github_issues") is False
        assert is_mcp_tool("amcp_github") is False
        assert is_mcp_tool("xmcp_server") is False

    def test_deny_mcp_allows_other_tools(self):
        """Deny mcp should allow all non-MCP tools."""
        tool_categories = {
            "bash": ["bash"],
            "filesystem": ["read_file", "write_file"],
            "mcp": [],  # MCP category for expansion
        }
        # Note: mcp_github is NOT an MCP tool (no underscore in 'github')
        # But mcp_github_issues IS (has underscore in 'github_issues')
        all_tool_names = {
            "bash",
            "read_file",
            "write_file",
            "mcp_github",  # NOT an MCP tool (no underscore after prefix)
            "mcp_github_issues",  # IS an MCP tool
            "mcp_filesystem",
            "instance_spawn",
        }

        result = resolve_tool_filter(
            allow=None,
            deny=["mcp"],
            tool_categories=tool_categories,
            all_tool_names=all_tool_names,
        )

        # Non-MCP tools should be allowed
        assert "bash" in result
        assert "read_file" in result
        assert "write_file" in result
        # mcp_github is NOT an MCP tool, so it's never added to MCP category
        # The MCP category only gets tools that match the pattern from all_tool_names

        # Actual MCP tools should be denied
        assert "mcp_github_issues" not in result
        assert "mcp_filesystem" not in result

    def test_allow_only_non_mcp_with_mcp_tools_present(self):
        """Allow non-MCP tools when MCP tools exist in all_tool_names."""
        tool_categories = {
            "bash": ["bash"],
            "mcp": [],  # For MCP expansion
        }
        all_tool_names = {
            "bash",
            "mcp_github_issues",  # IS MCP
            "mcp_filesystem",  # NOT MCP (no underscore after mcp_)
        }

        result = resolve_tool_filter(
            allow=["bash"],
            deny=None,
            tool_categories=tool_categories,
            all_tool_names=all_tool_names,
        )

        assert result == {"bash"}
        assert "mcp_github_issues" not in result
        assert "mcp_filesystem" not in result

    def test_allow_mcp_and_other_with_deny_mcp_wins(self):
        """Allow mcp + other with deny mcp = only other (deny wins)."""
        tool_categories = {
            "bash": ["bash"],
            "mcp": [],  # Will be expanded
        }
        all_tool_names = {
            "bash",
            "mcp_github_issues",
            "mcp_filesystem_read",
        }

        result = resolve_tool_filter(
            allow=["bash", "mcp"],
            deny=["mcp"],
            tool_categories=tool_categories,
            all_tool_names=all_tool_names,
        )

        # Deny wins - MCP tools excluded, only bash remains
        assert result == {"bash"}
        assert "mcp_github_issues" not in result
        assert "mcp_filesystem_read" not in result

    def test_resolve_filter_allow_mcp_expands_correctly(self):
        """Verify MCP expansion finds tools with underscore pattern only."""
        tool_categories = {
            "bash": ["bash"],
            "mcp": [],  # Empty - will be expanded
        }
        all_tool_names = {
            "bash",
            "mcp_github",  # NOT MCP - no underscore after mcp_
            "mcp_github_issues",  # IS MCP
            "mcp_filesystem_read",  # IS MCP
            "mcp_a",  # NOT MCP - no underscore after mcp_
            "mcp_a_b",  # IS MCP
            "camcp_tool",  # NOT MCP - doesn't start with mcp_
        }

        result = resolve_tool_filter(
            allow=["mcp"],
            deny=None,
            tool_categories=tool_categories,
            all_tool_names=all_tool_names,
        )

        # Only tools that start with mcp_ AND have underscore after
        expected_mcp_tools = {"mcp_github_issues", "mcp_filesystem_read", "mcp_a_b"}
        assert result == expected_mcp_tools

        # Verify excluded
        assert "mcp_github" not in result  # No underscore after prefix
        assert "mcp_a" not in result  # No underscore after prefix
        assert "camcp_tool" not in result  # Doesn't start with mcp_
        assert "bash" not in result  # Not MCP at all


class TestMcpToolNamingPattern:
    """Test the MCP tool naming pattern understanding."""

    def test_valid_mcp_tool_names(self):
        """Verify which tool names are considered valid MCP tools."""
        # Must have underscore in the tool name part (after mcp_)
        valid_mcp = [
            "mcp_github_issues",  # github_issues has underscore
            "mcp_filesystem_read",  # filesystem_read has underscore
            "mcp_git_status",  # git_status has underscore
            "mcp_a_b",  # a_b has underscore
            "mcp_server_tool",  # server_tool has underscore
        ]

        for name in valid_mcp:
            assert is_mcp_tool(name) is True, f"Expected {name} to be MCP tool"

    def test_invalid_mcp_tool_names(self):
        """Verify which tool names are NOT considered MCP tools."""
        # MCP tools MUST have underscore after the prefix
        invalid_mcp = [
            "bash",
            "read_file",
            "mcp_github",  # No underscore in 'github'
            "mcp_git",  # No underscore in 'git'
            "mcp_server",  # No underscore in 'server'
            "mcp_test",  # No underscore in 'test'
            "mcpgeneric",  # No underscore at all
            "mcp",  # No tool name after prefix
            "mcp_",  # No tool name after underscore
            "camcp_tool",  # Doesn't start with mcp_
            "amcp_github",  # Doesn't start with mcp_
        ]

        for name in invalid_mcp:
            assert is_mcp_tool(name) is False, f"Expected {name} to NOT be MCP tool"

    def test_underscore_detection_explanation(self):
        """Explain the underscore detection logic.

        The check is: "_" in name[4:]

        For "mcp_github_issues":
        - name[4:] = "github_issues"
        - "_" in "github_issues" = True ✓

        For "mcp_github":
        - name[4:] = "github"
        - "_" in "github" = False ✗

        For "mcp_test":
        - name[4:] = "test"
        - "_" in "test" = False ✗
        """
        assert is_mcp_tool("mcp_github_issues") is True
        assert is_mcp_tool("mcp_github") is False
        assert is_mcp_tool("mcp_test") is False
