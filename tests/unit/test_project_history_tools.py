"""Unit tests for project history tools: add, list, search, delete.

Tests tool logic (validation, truncation, error handling) with mocked store.
"""

import pytest
from unittest.mock import MagicMock

from daemon.tools.project_history import create_project_history_tools, _MAX_SUMMARY_LEN, _MAX_DETAILS_LEN
from daemon.repositories.project.models import HistoryEntryType


# =============================================================================
# Helper Functions
# =============================================================================


def make_mock_store(initial_entries: list = None):
    """Create a properly isolated mock store.

    Args:
        initial_entries: Optional list of entries to pre-populate.

    Returns:
        MagicMock store with project.get and history methods.
    """
    store = MagicMock()

    # Mock project returned by store.get()
    project = MagicMock()
    project.project_id = "test-project-id"
    project.name = "Test Project"
    store.get.return_value = project

    # Mutable entries list for tracking state
    entries = initial_entries if initial_entries is not None else []
    entry_id_counter = [100]  # Mutable counter for generating unique IDs

    def add_history_entry_handler(
        project_id,
        entry_type,
        summary,
        details=None,
        source_agent=None,
        source_instance_id=None,
        entry_metadata=None,
    ):
        entry_id_counter[0] += 1
        entry = {
            "id": f"entry-{entry_id_counter[0]}",
            "project_id": project_id,
            "entry_type": entry_type,
            "summary": summary,
            "details": details,
            "source_agent": source_agent,
            "source_instance_id": source_instance_id,
            "entry_metadata": entry_metadata,
            "created_at": "2024-01-01T00:00:00",
        }
        entries.append(entry)
        return entry

    def delete_history_entry_handler(entry_id, project_id=None):
        for i, e in enumerate(entries):
            if e["id"] == entry_id:
                if project_id is not None and e["project_id"] != project_id:
                    return False
                entries.pop(i)
                return True
        return False

    def list_history_entries_handler(project_id, entry_type=None, limit=20, offset=0):
        filtered = [e for e in entries if e["project_id"] == project_id]
        if entry_type:
            filtered = [e for e in filtered if e["entry_type"] == entry_type]
        total = len(filtered)
        filtered = sorted(filtered, key=lambda x: x["created_at"], reverse=True)
        paginated = filtered[offset : offset + limit]
        return {
            "entries": paginated,
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    def search_history_entries_handler(project_id, query, limit=20, offset=0):
        filtered = [
            e
            for e in entries
            if e["project_id"] == project_id
            and (query.lower() in e["summary"].lower() or (e["details"] and query.lower() in e["details"].lower()))
        ]
        total = len(filtered)
        filtered = sorted(filtered, key=lambda x: x["created_at"], reverse=True)
        paginated = filtered[offset : offset + limit]
        return {
            "entries": paginated,
            "total": total,
            "limit": limit,
            "offset": offset,
            "query": query,
        }

    store.add_history_entry.side_effect = add_history_entry_handler
    store.delete_history_entry.side_effect = delete_history_entry_handler
    store.list_history_entries.side_effect = list_history_entries_handler
    store.search_history_entries.side_effect = search_history_entries_handler

    return store


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_store():
    """Mock store with empty history entries."""
    return make_mock_store()


@pytest.fixture
def history_tools(mock_store):
    """Create project history tools with mock store."""
    return create_project_history_tools(
        mock_store, current_instance_id="test-instance", agent_id="test-agent"
    )


@pytest.fixture
def add_tool(history_tools):
    """Get the project_history_add tool."""
    for tool in history_tools:
        if tool.name == "project_history_add":
            return tool
    raise ValueError("project_history_add tool not found")


@pytest.fixture
def list_tool(history_tools):
    """Get the project_history_list tool."""
    for tool in history_tools:
        if tool.name == "project_history_list":
            return tool
    raise ValueError("project_history_list tool not found")


@pytest.fixture
def search_tool(history_tools):
    """Get the project_history_search tool."""
    for tool in history_tools:
        if tool.name == "project_history_search":
            return tool
    raise ValueError("project_history_search tool not found")


@pytest.fixture
def delete_tool(history_tools):
    """Get the project_history_delete tool."""
    for tool in history_tools:
        if tool.name == "project_history_delete":
            return tool
    raise ValueError("project_history_delete tool not found")


# =============================================================================
# Test Class: TestProjectHistoryAdd
# =============================================================================


class TestProjectHistoryAdd:
    """Tests for the project_history_add tool."""

    def test_add_valid_entry_with_all_fields(self, add_tool, mock_store):
        """Add entry with all fields -> returns dict with all fields."""
        result = add_tool.invoke(
            {
                "project_id": "test-project-id",
                "entry_type": "deployment",
                "summary": "Deployed v2.0.0 to production",
                "details": "Blue-green deployment with zero downtime",
                "entry_metadata": {"version": "2.0.0", "environment": "production"},
            }
        )

        assert "error" not in result
        assert result["project_id"] == "test-project-id"
        assert result["entry_type"] == "deployment"
        assert result["summary"] == "Deployed v2.0.0 to production"
        assert result["details"] == "Blue-green deployment with zero downtime"
        assert result["source_agent"] == "test-agent"
        assert result["source_instance_id"] == "test-instance"
        assert result["entry_metadata"] == {"version": "2.0.0", "environment": "production"}

    def test_add_valid_entry_minimal_fields(self, add_tool, mock_store):
        """Add entry with only required fields -> succeeds."""
        result = add_tool.invoke(
            {
                "project_id": "test-project-id",
                "entry_type": "note",
                "summary": "Simple note",
            }
        )

        assert "error" not in result
        assert result["summary"] == "Simple note"
        assert result["entry_type"] == "note"
        assert result["details"] is None
        assert result["entry_metadata"] is None

    def test_add_rejects_empty_summary(self, add_tool, mock_store):
        """Empty summary -> error dict."""
        result = add_tool.invoke(
            {
                "project_id": "test-project-id",
                "entry_type": "milestone",
                "summary": "",
            }
        )

        assert "error" in result
        assert "Summary cannot be empty" in result["error"]

    def test_add_rejects_whitespace_only_summary(self, add_tool, mock_store):
        """Whitespace-only summary -> error dict."""
        result = add_tool.invoke(
            {
                "project_id": "test-project-id",
                "entry_type": "milestone",
                "summary": "   \n\t  ",
            }
        )

        assert "error" in result
        assert "Summary cannot be empty" in result["error"]

    def test_add_rejects_invalid_entry_type(self, add_tool, mock_store):
        """Invalid entry_type -> error dict with valid types."""
        result = add_tool.invoke(
            {
                "project_id": "test-project-id",
                "entry_type": "invalid_type",
                "summary": "Test summary",
            }
        )

        assert "error" in result
        assert "Invalid entry_type 'invalid_type'" in result["error"]
        # Should include valid types
        valid_types = [e.value for e in HistoryEntryType]
        for vt in valid_types:
            assert vt in result["error"]

    def test_add_valid_entry_types(self, mock_store):
        """Each valid entry type should be accepted."""
        valid_types = [e.value for e in HistoryEntryType]

        for entry_type in valid_types:
            store = make_mock_store()
            tools = create_project_history_tools(store, agent_id="test-agent")
            add_tool = next(t for t in tools if t.name == "project_history_add")

            result = add_tool.invoke(
                {
                    "project_id": "test-project-id",
                    "entry_type": entry_type,
                    "summary": f"Testing {entry_type}",
                }
            )

            assert "error" not in result, f"Failed for entry_type: {entry_type}"
            assert result["entry_type"] == entry_type

    def test_add_truncates_summary_300_chars(self, add_tool, mock_store):
        """Summary > 300 chars -> truncated to 300."""
        long_summary = "A" * 400
        result = add_tool.invoke(
            {
                "project_id": "test-project-id",
                "entry_type": "note",
                "summary": long_summary,
            }
        )

        assert "error" not in result
        assert len(result["summary"]) == _MAX_SUMMARY_LEN
        assert result["summary"] == "A" * 300

    def test_add_truncates_summary_exactly_300(self, add_tool, mock_store):
        """Summary exactly 300 chars -> not truncated."""
        summary_300 = "B" * 300
        result = add_tool.invoke(
            {
                "project_id": "test-project-id",
                "entry_type": "note",
                "summary": summary_300,
            }
        )

        assert "error" not in result
        assert len(result["summary"]) == 300

    def test_add_truncates_details_5000_chars(self, add_tool, mock_store):
        """Details > 5000 chars -> truncated to 5000."""
        long_details = "X" * 6000
        result = add_tool.invoke(
            {
                "project_id": "test-project-id",
                "entry_type": "note",
                "summary": "Short summary",
                "details": long_details,
            }
        )

        assert "error" not in result
        assert len(result["details"]) == _MAX_DETAILS_LEN
        assert result["details"] == "X" * 5000

    def test_add_details_none_unchanged(self, add_tool, mock_store):
        """Details None -> remains None (no truncation)."""
        result = add_tool.invoke(
            {
                "project_id": "test-project-id",
                "entry_type": "milestone",
                "summary": "Test summary",
            }
        )

        assert result["details"] is None

    def test_add_special_characters_percent(self, add_tool, mock_store):
        """Summary with % character -> preserved."""
        result = add_tool.invoke(
            {
                "project_id": "test-project-id",
                "entry_type": "note",
                "summary": "Success rate: 100%",
                "details": "100% uptime achieved",
            }
        )

        assert "error" not in result
        assert "%" in result["summary"]
        assert "%" in result["details"]

    def test_add_special_characters_underscore(self, add_tool, mock_store):
        """Summary with _ character -> preserved (LIKE wildcard)."""
        result = add_tool.invoke(
            {
                "project_id": "test-project-id",
                "entry_type": "note",
                "summary": "variable_name = value",
                "details": "Uses snake_case_naming",
            }
        )

        assert "error" not in result
        assert "_" in result["summary"]
        assert "_" in result["details"]

    def test_add_special_characters_quotes(self, add_tool, mock_store):
        """Summary with quotes -> preserved."""
        result = add_tool.invoke(
            {
                "project_id": "test-project-id",
                "entry_type": "note",
                "summary": "He said 'hello' and \"goodbye\"",
            }
        )

        assert "error" not in result
        assert "'" in result["summary"]
        assert '"' in result["summary"]

    def test_add_unicode_characters(self, add_tool, mock_store):
        """Summary with unicode -> preserved."""
        result = add_tool.invoke(
            {
                "project_id": "test-project-id",
                "entry_type": "note",
                "summary": "Hello 你好 🌍 日本語",
                "details": "Supports emoji: 🚀 ⭐ 💯",
            }
        )

        assert "error" not in result
        assert "你好" in result["summary"]
        assert "🌍" in result["summary"]
        assert "🚀" in result["details"]

    def test_add_project_not_found(self, add_tool, mock_store):
        """Store.get returns None -> error dict."""
        mock_store.get.return_value = None

        result = add_tool.invoke(
            {
                "project_id": "nonexistent",
                "entry_type": "milestone",
                "summary": "Test summary",
            }
        )

        assert "error" in result
        assert "Project 'nonexistent' not found" in result["error"]


# =============================================================================
# Test Class: TestProjectHistoryList
# =============================================================================


class TestProjectHistoryList:
    """Tests for the project_history_list tool."""

    def test_list_returns_entries_for_correct_project(self, list_tool, mock_store):
        """List returns entries for the correct project_id."""
        result = list_tool.invoke({"project_id": "test-project-id"})

        assert "entries" in result
        assert "total" in result
        assert "limit" in result
        assert "offset" in result

    def test_list_empty_for_new_project(self, list_tool, mock_store):
        """List on project with no entries -> empty list."""
        result = list_tool.invoke({"project_id": "test-project-id"})

        assert result["entries"] == []
        assert result["total"] == 0

    def test_list_clamps_negative_limit(self, list_tool, mock_store):
        """Negative limit -> clamped to 1."""
        result = list_tool.invoke(
            {"project_id": "test-project-id", "limit": -5, "offset": 0}
        )

        assert result["limit"] == 1

    def test_list_clamps_negative_offset(self, list_tool, mock_store):
        """Negative offset -> clamped to 0."""
        result = list_tool.invoke(
            {"project_id": "test-project-id", "limit": 20, "offset": -10}
        )

        assert result["offset"] == 0

    def test_list_caps_limit_at_100(self, list_tool, mock_store):
        """Limit > 100 -> capped to 100."""
        result = list_tool.invoke(
            {"project_id": "test-project-id", "limit": 500, "offset": 0}
        )

        assert result["limit"] == 100

    def test_list_with_entry_type_filter(self, mock_store):
        """List with entry_type filter -> returns only matching entries."""
        # Add entries with different types
        store = make_mock_store()
        tools = create_project_history_tools(store, agent_id="test-agent")
        add_tool = next(t for t in tools if t.name == "project_history_add")
        list_tool = next(t for t in tools if t.name == "project_history_list")

        add_tool.invoke(
            {"project_id": "test-project-id", "entry_type": "milestone", "summary": "M1"}
        )
        add_tool.invoke(
            {"project_id": "test-project-id", "entry_type": "deployment", "summary": "D1"}
        )
        add_tool.invoke(
            {"project_id": "test-project-id", "entry_type": "milestone", "summary": "M2"}
        )

        result = list_tool.invoke(
            {"project_id": "test-project-id", "entry_type": "milestone"}
        )

        assert result["total"] == 2
        for entry in result["entries"]:
            assert entry["entry_type"] == "milestone"

    def test_list_invalid_entry_type_error(self, list_tool, mock_store):
        """Invalid entry_type filter -> error dict."""
        result = list_tool.invoke(
            {"project_id": "test-project-id", "entry_type": "invalid"}
        )

        assert "error" in result
        assert "Invalid entry_type 'invalid'" in result["error"]

    def test_list_pagination_offset_beyond_total(self, mock_store):
        """Offset beyond total entries -> empty list."""
        store = make_mock_store()
        tools = create_project_history_tools(store, agent_id="test-agent")
        add_tool = next(t for t in tools if t.name == "project_history_add")
        list_tool = next(t for t in tools if t.name == "project_history_list")

        # Add 3 entries
        for i in range(3):
            add_tool.invoke(
                {"project_id": "test-project-id", "entry_type": "note", "summary": f"Note {i}"}
            )

        # Request offset beyond total
        result = list_tool.invoke(
            {"project_id": "test-project-id", "limit": 20, "offset": 100}
        )

        assert result["entries"] == []
        assert result["total"] == 3

    def test_list_project_not_found(self, list_tool, mock_store):
        """Project not found -> error dict."""
        mock_store.get.return_value = None

        result = list_tool.invoke({"project_id": "nonexistent"})

        assert "error" in result
        assert "Project 'nonexistent' not found" in result["error"]


# =============================================================================
# Test Class: TestProjectHistorySearch
# =============================================================================


class TestProjectHistorySearch:
    """Tests for the project_history_search tool."""

    def test_search_returns_proper_format(self, search_tool, mock_store):
        """Search returns dict with entries, total, limit, offset, query."""
        result = search_tool.invoke(
            {"project_id": "test-project-id", "query": "test"}
        )

        assert "entries" in result
        assert "total" in result
        assert "limit" in result
        assert "offset" in result
        assert "query" in result
        assert result["query"] == "test"

    def test_search_by_query_string(self, mock_store):
        """Search finds entries matching query."""
        store = make_mock_store()
        tools = create_project_history_tools(store, agent_id="test-agent")
        add_tool = next(t for t in tools if t.name == "project_history_add")
        search_tool = next(t for t in tools if t.name == "project_history_search")

        add_tool.invoke(
            {"project_id": "test-project-id", "entry_type": "deployment", "summary": "Deployed API v2"}
        )
        add_tool.invoke(
            {"project_id": "test-project-id", "entry_type": "note", "summary": "Fixed a bug"}
        )
        add_tool.invoke(
            {"project_id": "test-project-id", "entry_type": "milestone", "summary": "API milestone reached"}
        )

        result = search_tool.invoke(
            {"project_id": "test-project-id", "query": "API"}
        )

        assert result["total"] == 2
        for entry in result["entries"]:
            assert "API" in entry["summary"]

    def test_search_handles_special_character_percent(self, mock_store):
        """Search with % in query -> handled (LIKE wildcard escape)."""
        store = make_mock_store()
        tools = create_project_history_tools(store, agent_id="test-agent")
        add_tool = next(t for t in tools if t.name == "project_history_add")
        search_tool = next(t for t in tools if t.name == "project_history_search")

        add_tool.invoke(
            {
                "project_id": "test-project-id",
                "entry_type": "note",
                "summary": "100% uptime achieved",
            }
        )

        result = search_tool.invoke(
            {"project_id": "test-project-id", "query": "100%"}
        )

        assert result["total"] == 1
        assert "100%" in result["entries"][0]["summary"]

    def test_search_handles_special_character_underscore(self, mock_store):
        """Search with _ in query -> handled (LIKE wildcard escape)."""
        store = make_mock_store()
        tools = create_project_history_tools(store, agent_id="test-agent")
        add_tool = next(t for t in tools if t.name == "project_history_add")
        search_tool = next(t for t in tools if t.name == "project_history_search")

        add_tool.invoke(
            {
                "project_id": "test-project-id",
                "entry_type": "note",
                "summary": "variable_name = value",
            }
        )

        result = search_tool.invoke(
            {"project_id": "test-project-id", "query": "variable_name"}
        )

        assert result["total"] == 1
        assert "variable_name" in result["entries"][0]["summary"]

    def test_search_returns_empty_when_no_matches(self, search_tool, mock_store):
        """Search with no matching entries -> empty list."""
        result = search_tool.invoke(
            {"project_id": "test-project-id", "query": "nonexistent_query_string"}
        )

        assert result["entries"] == []
        assert result["total"] == 0

    def test_search_clamps_negative_limit(self, search_tool, mock_store):
        """Negative limit -> clamped to 1."""
        result = search_tool.invoke(
            {"project_id": "test-project-id", "query": "test", "limit": -5, "offset": 0}
        )

        assert result["limit"] == 1

    def test_search_clamps_negative_offset(self, search_tool, mock_store):
        """Negative offset -> clamped to 0."""
        result = search_tool.invoke(
            {"project_id": "test-project-id", "query": "test", "limit": 20, "offset": -10}
        )

        assert result["offset"] == 0

    def test_search_project_not_found(self, search_tool, mock_store):
        """Project not found -> error dict."""
        mock_store.get.return_value = None

        result = search_tool.invoke(
            {"project_id": "nonexistent", "query": "test"}
        )

        assert "error" in result
        assert "Project 'nonexistent' not found" in result["error"]


# =============================================================================
# Test Class: TestProjectHistoryDelete
# =============================================================================


class TestProjectHistoryDelete:
    """Tests for the project_history_delete tool."""

    def test_delete_existing_entry(self, mock_store):
        """Delete existing entry -> returns success dict."""
        store = make_mock_store()
        tools = create_project_history_tools(store, agent_id="test-agent")
        add_tool = next(t for t in tools if t.name == "project_history_add")
        delete_tool = next(t for t in tools if t.name == "project_history_delete")

        # Add an entry
        add_result = add_tool.invoke(
            {
                "project_id": "test-project-id",
                "entry_type": "milestone",
                "summary": "Entry to delete",
            }
        )
        entry_id = add_result["id"]

        # Delete it
        result = delete_tool.invoke(
            {"project_id": "test-project-id", "entry_id": entry_id}
        )

        assert "success" in result
        assert result["success"] is True
        assert result["deleted_entry_id"] == entry_id

    def test_delete_nonexistent_entry(self, delete_tool, mock_store):
        """Delete nonexistent entry -> error dict."""
        result = delete_tool.invoke(
            {"project_id": "test-project-id", "entry_id": "nonexistent-entry-id"}
        )

        assert "error" in result
        assert "Entry 'nonexistent-entry-id' not found" in result["error"]

    def test_delete_validates_ownership(self, mock_store):
        """Delete entry from different project -> returns error (ownership validation)."""
        store = make_mock_store()
        tools = create_project_history_tools(store, agent_id="test-agent")
        add_tool = next(t for t in tools if t.name == "project_history_add")
        delete_tool = next(t for t in tools if t.name == "project_history_delete")

        # Add an entry to project A
        add_result = add_tool.invoke(
            {
                "project_id": "project-a",
                "entry_type": "note",
                "summary": "Entry in project A",
            }
        )
        entry_id = add_result["id"]

        # Try to delete from project B
        result = delete_tool.invoke(
            {"project_id": "project-b", "entry_id": entry_id}
        )

        assert "error" in result
        assert "not found" in result["error"]

    def test_delete_project_not_found(self, delete_tool, mock_store):
        """Delete with nonexistent project -> error dict."""
        mock_store.get.return_value = None

        result = delete_tool.invoke(
            {"project_id": "nonexistent", "entry_id": "some-entry-id"}
        )

        assert "error" in result
        assert "Project 'nonexistent' not found" in result["error"]


# =============================================================================
# Test Class: TestConstants
# =============================================================================


class TestConstants:
    """Tests for module constants."""

    def test_max_summary_len_is_300(self):
        """Verify _MAX_SUMMARY_LEN is 300."""
        assert _MAX_SUMMARY_LEN == 300

    def test_max_details_len_is_5000(self):
        """Verify _MAX_DETAILS_LEN is 5000."""
        assert _MAX_DETAILS_LEN == 5000
