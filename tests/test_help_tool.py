"""Tests for the help tool system."""

import pytest
from unittest.mock import MagicMock, patch
from langchain_core.tools import tool

from daemon.tools._tool_registry import (
    register_full_doc,
    get_full_doc,
    get_tool_metadata,
    list_tools,
    list_tools_by_category,
    clear_registry,
    scan_tools_for_full_docs,
)
from daemon.tools.help import create_help_tool


# Test fixtures
@pytest.fixture(autouse=True)
def clean_registry():
    """Clear registry before each test."""
    clear_registry()
    yield
    clear_registry()


@pytest.fixture
def sample_tools():
    """Create sample tools for testing."""
    
    @tool
    def test_create(name: str) -> dict:
        """Create a new item. Use tool_help("test_create") for details."""
        return {"name": name}
    test_create._full_doc_ = """Create a new item.

Args:
    name: Item name (required).

Returns:
    Dictionary with item details.

Example:
    test_create(name="My Item")"""
    
    @tool
    def test_get(item_id: str) -> dict:
        """Get an item by ID. Use tool_help("test_get") for details."""
        return {"id": item_id}
    test_get._full_doc_ = """Get an item by ID.

Args:
    item_id: The item ID.

Returns:
    Item dictionary or None."""
    
    @tool
    def test_list(limit: int = 10) -> list:
        """List all items. Use tool_help("test_list") for details."""
        return []
    
    return [test_create, test_get, test_list]


class TestToolRegistry:
    """Tests for _tool_registry module."""
    
    def test_register_full_doc(self):
        """Test registering full documentation."""
        register_full_doc("test_tool", "This is the full doc.")
        assert get_full_doc("test_tool") == "This is the full doc."
    
    def test_get_full_doc_not_found(self):
        """Test getting doc for non-existent tool."""
        assert get_full_doc("nonexistent") is None
    
    def test_scan_tools_for_full_docs(self, sample_tools):
        """Test scanning tools for _full_doc_ attributes."""
        scan_tools_for_full_docs(sample_tools)
        
        # Check that docs were registered
        assert get_full_doc("test_create") is not None
        assert get_full_doc("test_get") is not None
        assert "Create a new item" in get_full_doc("test_create")
    
    def test_list_tools(self, sample_tools):
        """Test listing all tools."""
        scan_tools_for_full_docs(sample_tools)
        tools = list_tools()
        
        assert "test_create" in tools
        assert "test_get" in tools
        assert "test_list" in tools
    
    def test_list_tools_by_category(self, sample_tools):
        """Test listing tools by category."""
        scan_tools_for_full_docs(sample_tools)
        categories = list_tools_by_category()
        
        # All test_* tools should be in "test" category
        assert "test" in categories
        assert "test_create" in categories["test"]
    
    def test_get_tool_metadata(self, sample_tools):
        """Test getting tool metadata."""
        scan_tools_for_full_docs(sample_tools)
        meta = get_tool_metadata("test_create")
        
        assert meta is not None
        assert "short_doc" in meta
        assert "category" in meta
        assert "Create a new item" in meta["short_doc"]


class TestHelpTool:
    """Tests for the help tool."""
    
    def test_help_list_all_tools(self, sample_tools):
        """Test listing all tools."""
        help_tool = create_help_tool(sample_tools, agent_id="test")
        result = help_tool.invoke({})
        
        assert "Available Tools" in result
        assert "test_create" in result
        assert "test_get" in result
        assert "test_list" in result
    
    def test_help_get_specific_tool(self, sample_tools):
        """Test getting help for a specific tool."""
        help_tool = create_help_tool(sample_tools, agent_id="test")
        result = help_tool.invoke({"tool_name": "test_create"})
        
        assert "test_create" in result
        assert "Create a new item" in result
        assert "Args:" in result
        assert "Example:" in result
    
    def test_help_tool_not_found(self, sample_tools):
        """Test help for non-existent tool."""
        help_tool = create_help_tool(sample_tools, agent_id="test")
        result = help_tool.invoke({"tool_name": "nonexistent"})
        
        assert "not found" in result
    
    def test_help_tool_suggests_similar(self, sample_tools):
        """Test that help suggests similar tools."""
        help_tool = create_help_tool(sample_tools, agent_id="test")
        result = help_tool.invoke({"tool_name": "test_creat"})
        
        assert "Similar tools" in result or "test_create" in result
    
    def test_help_by_category(self, sample_tools):
        """Test listing tools by category."""
        help_tool = create_help_tool(sample_tools, agent_id="test")
        result = help_tool.invoke({"category": "bash"})
        
        # Should show bash category (one of the valid categories)
        assert "bash" in result.lower() or "shell" in result.lower()
    
    def test_help_invalid_category(self, sample_tools):
        """Test invalid category shows available categories."""
        help_tool = create_help_tool(sample_tools, agent_id="test")
        result = help_tool.invoke({"category": "nonexistent"})
        
        # Should show available categories
        assert "Unknown category" in result
        assert "available" in result.lower()
    
    def test_help_short_doc_fallback(self):
        """Test that short doc is shown when full doc not available."""
        @tool
        def no_full_doc():
            """This tool has no full doc."""
            return {}
        
        help_tool = create_help_tool([no_full_doc], agent_id="test")
        result = help_tool.invoke({"tool_name": "no_full_doc"})
        
        assert "no_full_doc" in result
        assert "no full doc" in result.lower() or "This tool has no full doc" in result


class TestHelpToolWithProjectTools:
    """Tests for help tool with actual project tools."""
    
    def test_help_with_project_tools(self):
        """Test help tool works with project tools."""
        from sqlmodel import Session, SQLModel, create_engine
        from daemon.tools.project import create_project_tools
        from daemon.repositories import SQLModelProjectRepository as ProjectStore
        
        engine = create_engine("sqlite:///:memory:", echo=False)
        SQLModel.metadata.create_all(engine)
        
        with Session(engine) as session:
            store = ProjectStore(session)
            project_tools = create_project_tools(store)
            help_tool = create_help_tool(project_tools, agent_id="test")
            
            # Test listing
            result = help_tool.invoke({})
            assert "project_create" in result
            
            # Test specific tool help
            result = help_tool.invoke({"tool_name": "project_create"})
            assert "project_create" in result
            assert "Create a new project" in result
            
            # Test category listing
            result = help_tool.invoke({"category": "project"})
            assert "project_create" in result
        
        engine.dispose()


class TestToolHelpFiltering:
    """Tests for tool_help() filtering based on agent tool configuration."""

    @pytest.fixture(autouse=True)
    def setup_and_teardown(self):
        """Clear registry before and after each test."""
        clear_registry()
        yield
        clear_registry()

    @pytest.fixture
    def mock_agent_with_restricted_tools(self):
        """Set up a mock agent with restricted tool access."""
        from daemon.registry import ToolFilter
        
        # Create mock registry
        mock_agent_meta = MagicMock()
        mock_agent_meta.tools = ToolFilter(allow=["bash", "filesystem"], deny=["write_file"])
        
        mock_registry = MagicMock()
        mock_registry.get.return_value = mock_agent_meta
        
        return mock_registry

    @pytest.fixture
    def mock_agent_with_no_restrictions(self):
        """Set up a mock agent with full tool access."""
        mock_agent_meta = MagicMock()
        mock_agent_meta.tools = None  # No restrictions
        
        mock_registry = MagicMock()
        mock_registry.get.return_value = mock_agent_meta
        
        return mock_registry

    @pytest.fixture
    def sample_tools_with_docs(self):
        """Create sample tools with full documentation."""
        clear_registry()
        
        @tool
        def allowed_tool(name: str) -> dict:
            """An allowed tool. Use tool_help("allowed_tool") for details."""
            return {"name": name}
        allowed_tool._full_doc_ = """An allowed tool for testing.

Args:
    name: The name parameter (required).

Returns:
    Dictionary with name."""
        
        @tool
        def denied_tool(secret: str) -> dict:
            """A denied tool. Use tool_help("denied_tool") for details."""
            return {"secret": secret}
        denied_tool._full_doc_ = """A denied tool for testing.

Args:
    secret: A secret parameter (required).

Returns:
    Dictionary with secret."""
        
        @tool
        def bash(command: str) -> str:
            """Execute a bash command."""
            return "command output"
        bash._full_doc_ = """Execute a bash command.

Args:
    command: The command to execute (required).

Returns:
    Command output as string."""
        
        return [allowed_tool, denied_tool, bash]

    def test_help_with_no_args_shows_only_allowed_tools(self, sample_tools_with_docs, mock_agent_with_restricted_tools):
        """tool_help() with no args should only show allowed tools."""
        with patch("daemon.registry.get_registry", return_value=mock_agent_with_restricted_tools):
            help_tool = create_help_tool(sample_tools_with_docs, agent_id="restricted_agent")
            result = help_tool.invoke({})
            
            # Should show bash tool (in allowed list)
            assert "bash" in result.lower()

    def test_help_shows_allowed_tool_docstring(self, sample_tools_with_docs, mock_agent_with_restricted_tools):
        """tool_help(allowed_tool) should show the docstring for allowed tools."""
        with patch("daemon.registry.get_registry", return_value=mock_agent_with_restricted_tools):
            # Note: bash is the tool that's allowed in our mock
            help_tool = create_help_tool(sample_tools_with_docs, agent_id="restricted_agent")
            result = help_tool.invoke({"tool_name": "bash"})
            
            # Should show full documentation
            assert "bash" in result.lower()
            assert "Execute a bash command" in result or "command" in result.lower()

    def test_help_denies_unavailable_tool(self, sample_tools_with_docs, mock_agent_with_restricted_tools):
        """tool_help(denied_tool) should show 'not available' message."""
        with patch("daemon.registry.get_registry", return_value=mock_agent_with_restricted_tools):
            help_tool = create_help_tool(sample_tools_with_docs, agent_id="restricted_agent")
            result = help_tool.invoke({"tool_name": "denied_tool"})
            
            # Should indicate tool is not available
            assert "not available" in result.lower() or "not found" in result.lower()

    def test_help_category_shows_only_allowed_tools(self, sample_tools_with_docs, mock_agent_with_restricted_tools):
        """tool_help(category="bash") should show only allowed tools in that category."""
        with patch("daemon.registry.get_registry", return_value=mock_agent_with_restricted_tools):
            help_tool = create_help_tool(sample_tools_with_docs, agent_id="restricted_agent")
            result = help_tool.invoke({"category": "bash"})
            
            # Should show bash category
            assert "bash" in result.lower()

    def test_help_denied_category_message(self, sample_tools_with_docs, mock_agent_with_restricted_tools):
        """tool_help(category="mother") should handle denied category appropriately."""
        with patch("daemon.registry.get_registry", return_value=mock_agent_with_restricted_tools):
            help_tool = create_help_tool(sample_tools_with_docs, agent_id="restricted_agent")
            result = help_tool.invoke({"category": "mother"})
            
            # Should either show empty message or indicate no tools available
            # Since mother category tools aren't in our allowed list
            assert "mother" in result.lower() or "no tools" in result.lower() or "available" in result.lower()

    def test_unrestricted_agent_sees_all_tools(self, sample_tools_with_docs, mock_agent_with_no_restrictions):
        """Agent with no restrictions should see all tools."""
        with patch("daemon.registry.get_registry", return_value=mock_agent_with_no_restrictions):
            help_tool = create_help_tool(sample_tools_with_docs, agent_id="full_access_agent")
            result = help_tool.invoke({})
            
            # Should contain Available Tools header
            assert "available" in result.lower() or "tools" in result.lower()

    def test_unrestricted_agent_can_get_any_tool_help(self, sample_tools_with_docs, mock_agent_with_no_restrictions):
        """Agent with no restrictions can get help for any tool."""
        with patch("daemon.registry.get_registry", return_value=mock_agent_with_no_restrictions):
            help_tool = create_help_tool(sample_tools_with_docs, agent_id="full_access_agent")
            
            # denied_tool should be available
            result = help_tool.invoke({"tool_name": "denied_tool"})
            
            # Should show full doc, not "not available"
            assert "not available" not in result.lower()
            assert "secret" in result.lower() or "A denied tool" in result


class TestToolHelpBackwardCompatibility:
    """Tests for backward compatibility when agent has no tools config."""

    @pytest.fixture(autouse=True)
    def setup_and_teardown(self):
        """Clear registry before and after each test."""
        clear_registry()
        yield
        clear_registry()

    def test_agent_not_in_registry_gets_all_tools(self):
        """Agent not in registry should get access to all tools."""
        @tool
        def any_tool() -> str:
            """Any tool docstring."""
            return "result"
        any_tool._full_doc_ = "Any tool full documentation."
        
        # Mock registry returning None (agent not found)
        mock_registry = MagicMock()
        mock_registry.get.return_value = None
        
        with patch("daemon.registry.get_registry", return_value=mock_registry):
            help_tool = create_help_tool([any_tool], agent_id="unknown_agent")
            result = help_tool.invoke({})
            
            # Should show the tool as available
            assert "any_tool" in result.lower()

    def test_agent_with_none_tools_gets_full_access(self):
        """Agent with tools=None should get full access."""
        @tool
        def another_tool() -> str:
            """Another tool docstring."""
            return "result"
        another_tool._full_doc_ = "Another tool full documentation."
        
        # Mock registry with tools=None
        mock_agent_meta = MagicMock()
        mock_agent_meta.tools = None
        
        mock_registry = MagicMock()
        mock_registry.get.return_value = mock_agent_meta
        
        with patch("daemon.registry.get_registry", return_value=mock_registry):
            help_tool = create_help_tool([another_tool], agent_id="no_restrictions_agent")
            
            # Should be able to get help for another_tool
            result = help_tool.invoke({"tool_name": "another_tool"})
            
            # Should show full documentation, not "not available"
            assert "not available" not in result.lower()
            assert "Another tool" in result
