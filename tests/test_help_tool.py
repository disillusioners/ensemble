"""Tests for the help tool system."""

import pytest
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
        help_tool = create_help_tool(sample_tools)
        result = help_tool.invoke({})
        
        assert "Available Tools" in result
        assert "test_create" in result
        assert "test_get" in result
        assert "test_list" in result
    
    def test_help_get_specific_tool(self, sample_tools):
        """Test getting help for a specific tool."""
        help_tool = create_help_tool(sample_tools)
        result = help_tool.invoke({"tool_name": "test_create"})
        
        assert "test_create" in result
        assert "Create a new item" in result
        assert "Args:" in result
        assert "Example:" in result
    
    def test_help_tool_not_found(self, sample_tools):
        """Test help for non-existent tool."""
        help_tool = create_help_tool(sample_tools)
        result = help_tool.invoke({"tool_name": "nonexistent"})
        
        assert "not found" in result
    
    def test_help_tool_suggests_similar(self, sample_tools):
        """Test that help suggests similar tools."""
        help_tool = create_help_tool(sample_tools)
        result = help_tool.invoke({"tool_name": "test_creat"})
        
        assert "Similar tools" in result or "test_create" in result
    
    def test_help_by_category(self, sample_tools):
        """Test listing tools by category."""
        help_tool = create_help_tool(sample_tools)
        result = help_tool.invoke({"category": "test"})
        
        assert "test" in result.lower()
        assert "test_create" in result
    
    def test_help_invalid_category(self, sample_tools):
        """Test invalid category shows available categories."""
        help_tool = create_help_tool(sample_tools)
        result = help_tool.invoke({"category": "nonexistent"})
        
        assert "No tools" in result or "not found" in result.lower()
    
    def test_help_short_doc_fallback(self):
        """Test that short doc is shown when full doc not available."""
        @tool
        def no_full_doc():
            """This tool has no full doc."""
            return {}
        
        help_tool = create_help_tool([no_full_doc])
        result = help_tool.invoke({"tool_name": "no_full_doc"})
        
        assert "no_full_doc" in result
        assert "no full doc" in result.lower() or "This tool has no full doc" in result


class TestHelpToolWithProjectTools:
    """Tests for help tool with actual project tools."""
    
    def test_help_with_project_tools(self):
        """Test help tool works with project tools."""
        from sqlmodel import Session, SQLModel, create_engine
        from daemon.tools.project import create_project_tools
        from daemon.project_store import ProjectStore
        
        engine = create_engine("sqlite:///:memory:", echo=False)
        SQLModel.metadata.create_all(engine)
        
        with Session(engine) as session:
            store = ProjectStore(session)
            project_tools = create_project_tools(store)
            help_tool = create_help_tool(project_tools)
            
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
