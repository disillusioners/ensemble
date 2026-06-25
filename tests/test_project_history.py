"""Tests for daemon/repositories/project - SQLModelProjectRepository history methods."""

import pytest

from sqlmodel import Session, SQLModel, create_engine

from daemon.repositories import SQLModelProjectRepository as ProjectStore


@pytest.fixture
def engine():
    """Create in-memory SQLite engine for testing."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def store(engine):
    """Create ProjectStore instance with SQLModel Engine."""
    return ProjectStore(engine)


@pytest.fixture
def project(store):
    """Create a test project for history tests."""
    return store.create(name="Test Project")


class TestAddHistoryEntry:
    """Tests for add_history_entry() method."""

    def test_add_history_entry(self, store, project):
        """Test adding a basic history entry."""
        entry = store.add_history_entry(
            project_id=project.project_id,
            entry_type="deployment",
            summary="Deployed API v2 to production",
        )

        assert entry is not None
        assert entry["id"] is not None
        assert entry["project_id"] == project.project_id
        assert entry["entry_type"] == "deployment"
        assert entry["summary"] == "Deployed API v2 to production"
        assert entry["details"] is None
        assert entry["source_agent"] is None
        assert entry["source_instance_id"] is None
        assert entry["entry_metadata"] is None
        assert "created_at" in entry

    def test_add_history_entry_with_all_fields(self, store, project):
        """Test adding a history entry with all optional fields."""
        entry = store.add_history_entry(
            project_id=project.project_id,
            entry_type="task",
            summary="Fixed critical bug",
            details="Fixed memory leak in worker process",
            source_agent="developer",
            source_instance_id="instance-123",
            entry_metadata={"severity": "critical", "bug_id": "BUG-456"},
        )

        assert entry["entry_type"] == "task"
        assert entry["summary"] == "Fixed critical bug"
        assert entry["details"] == "Fixed memory leak in worker process"
        assert entry["source_agent"] == "developer"
        assert entry["source_instance_id"] == "instance-123"
        assert entry["entry_metadata"] == {"severity": "critical", "bug_id": "BUG-456"}

    def test_add_history_entry_truncates_summary(self, store, project):
        """Test that summary is truncated to 300 characters."""
        long_summary = "x" * 400
        entry = store.add_history_entry(
            project_id=project.project_id,
            entry_type="test",
            summary=long_summary,
        )

        assert len(entry["summary"]) == 300

    def test_add_history_entry_truncates_details(self, store, project):
        """Test that details are truncated to 5000 characters."""
        long_details = "x" * 6000
        entry = store.add_history_entry(
            project_id=project.project_id,
            entry_type="test",
            summary="Test",
            details=long_details,
        )

        assert len(entry["details"]) == 5000


class TestGetHistoryEntry:
    """Tests for get_history_entry() method."""

    def test_get_history_entry(self, store, project):
        """Test getting a history entry by ID."""
        added = store.add_history_entry(
            project_id=project.project_id,
            entry_type="deployment",
            summary="Test entry",
        )

        entry = store.get_history_entry(added["id"])

        assert entry is not None
        assert entry["id"] == added["id"]
        assert entry["project_id"] == project.project_id
        assert entry["summary"] == "Test entry"

    def test_get_history_entry_not_found(self, store, project):
        """Test getting non-existent history entry returns None."""
        result = store.get_history_entry("non-existent-entry-id")

        assert result is None


class TestDeleteHistoryEntry:
    """Tests for delete_history_entry() method."""

    def test_delete_history_entry(self, store, project):
        """Test deleting a history entry."""
        entry = store.add_history_entry(
            project_id=project.project_id,
            entry_type="deployment",
            summary="To be deleted",
        )

        result = store.delete_history_entry(entry["id"])

        assert result is True

        # Verify it's gone
        assert store.get_history_entry(entry["id"]) is None

    def test_delete_history_entry_with_project_id_validation(self, store, project):
        """Test that deleting with wrong project_id returns False."""
        entry = store.add_history_entry(
            project_id=project.project_id,
            entry_type="deployment",
            summary="Test entry",
        )

        # Try to delete with different project_id
        result = store.delete_history_entry(entry["id"], project_id="different-project-id")

        assert result is False

        # Verify entry still exists
        assert store.get_history_entry(entry["id"]) is not None

    def test_delete_history_entry_not_found(self, store, project):
        """Test deleting non-existent entry returns False."""
        result = store.delete_history_entry("non-existent-entry-id")

        assert result is False


class TestListHistoryEntries:
    """Tests for list_history_entries() method."""

    def test_list_history_entries(self, store, project):
        """Test listing all history entries for a project."""
        # Add 5 entries
        for i in range(5):
            store.add_history_entry(
                project_id=project.project_id,
                entry_type="task",
                summary=f"Entry {i}",
            )

        result = store.list_history_entries(project.project_id)

        assert result["total"] == 5
        assert len(result["entries"]) == 5
        assert result["limit"] == 20
        assert result["offset"] == 0

    def test_list_history_entries_with_type_filter(self, store, project):
        """Test filtering history entries by type."""
        # Add mixed types
        store.add_history_entry(
            project_id=project.project_id, entry_type="deployment", summary="Deploy 1"
        )
        store.add_history_entry(
            project_id=project.project_id, entry_type="task", summary="Task 1"
        )
        store.add_history_entry(
            project_id=project.project_id, entry_type="deployment", summary="Deploy 2"
        )
        store.add_history_entry(
            project_id=project.project_id, entry_type="task", summary="Task 2"
        )

        result = store.list_history_entries(project.project_id, entry_type="deployment")

        assert result["total"] == 2
        assert len(result["entries"]) == 2
        for entry in result["entries"]:
            assert entry["entry_type"] == "deployment"

    def test_list_history_entries_paging(self, store, project):
        """Test pagination of history entries."""
        # Add 25 entries
        for i in range(25):
            store.add_history_entry(
                project_id=project.project_id,
                entry_type="task",
                summary=f"Entry {i}",
            )

        result = store.list_history_entries(project.project_id, limit=10, offset=0)

        assert result["total"] == 25
        assert len(result["entries"]) == 10
        assert result["limit"] == 10
        assert result["offset"] == 0

    def test_list_history_entries_empty(self, store, project):
        """Test listing for project with no entries returns empty."""
        result = store.list_history_entries(project.project_id)

        assert result["total"] == 0
        assert result["entries"] == []

    def test_list_history_entries_offset(self, store, project):
        """Test offset pagination."""
        # Add 15 entries
        for i in range(15):
            store.add_history_entry(
                project_id=project.project_id,
                entry_type="task",
                summary=f"Entry {i}",
            )

        # Get second page
        result = store.list_history_entries(project.project_id, limit=5, offset=5)

        assert result["total"] == 15
        assert len(result["entries"]) == 5


class TestSearchHistoryEntries:
    """Tests for search_history_entries() method."""

    def test_search_history_entries_by_summary(self, store, project):
        """Test searching history entries by summary."""
        store.add_history_entry(
            project_id=project.project_id,
            entry_type="deployment",
            summary="Deployed API v2 to production",
        )
        store.add_history_entry(
            project_id=project.project_id,
            entry_type="task",
            summary="Updated documentation",
        )

        result = store.search_history_entries(project.project_id, "API v2")

        assert result["total"] == 1
        assert len(result["entries"]) == 1
        assert "API v2" in result["entries"][0]["summary"]
        assert result["query"] == "API v2"

    def test_search_history_entries_by_details(self, store, project):
        """Test searching history entries by details."""
        store.add_history_entry(
            project_id=project.project_id,
            entry_type="task",
            summary="Fixed bug",
            details="Fixed memory leak in worker process",
        )
        store.add_history_entry(
            project_id=project.project_id,
            entry_type="task",
            summary="Another task",
            details="Updated config file",
        )

        result = store.search_history_entries(project.project_id, "memory leak")

        assert result["total"] == 1
        assert len(result["entries"]) == 1
        assert "memory leak" in result["entries"][0]["details"]

    def test_search_history_entries_null_details(self, store, project):
        """Test search works when details are NULL."""
        store.add_history_entry(
            project_id=project.project_id,
            entry_type="task",
            summary="Searchable summary entry",
            details=None,
        )
        store.add_history_entry(
            project_id=project.project_id,
            entry_type="task",
            summary="Another entry",
            details="Some details",
        )

        # Search for something that only matches summary
        result = store.search_history_entries(project.project_id, "Searchable")

        assert result["total"] == 1
        assert len(result["entries"]) == 1
        assert result["entries"][0]["summary"] == "Searchable summary entry"
        # Verify NULL details didn't cause issues
        assert result["entries"][0]["details"] is None

    def test_search_history_entries_no_match(self, store, project):
        """Test searching for non-existent term returns empty."""
        store.add_history_entry(
            project_id=project.project_id,
            entry_type="task",
            summary="Real entry",
        )

        result = store.search_history_entries(project.project_id, "nonexistent-term")

        assert result["total"] == 0
        assert result["entries"] == []

    def test_search_history_entries_returns_query(self, store, project):
        """Test that search returns the original query."""
        store.add_history_entry(
            project_id=project.project_id,
            entry_type="task",
            summary="Test entry",
        )

        result = store.search_history_entries(project.project_id, "my search query")

        assert result["query"] == "my search query"


class TestGetRecentHistory:
    """Tests for get_recent_history() method."""

    def test_get_recent_history(self, store, project):
        """Test getting recent history entries in DESC order."""
        # Add 15 entries with different summaries
        for i in range(15):
            store.add_history_entry(
                project_id=project.project_id,
                entry_type="task",
                summary=f"Entry {i}",
            )

        result = store.get_recent_history(project.project_id, limit=5)

        assert len(result) == 5
        # Verify descending order by created_at (most recent first)
        for i in range(len(result) - 1):
            assert result[i]["created_at"] >= result[i + 1]["created_at"]

    def test_get_recent_history_default_limit(self, store, project):
        """Test that default limit is 10."""
        # Add 20 entries
        for i in range(20):
            store.add_history_entry(
                project_id=project.project_id,
                entry_type="task",
                summary=f"Entry {i}",
            )

        # Call without explicit limit
        result = store.get_recent_history(project.project_id)

        assert len(result) == 10

    def test_get_recent_history_empty(self, store, project):
        """Test getting recent history for project with no entries."""
        result = store.get_recent_history(project.project_id)

        assert result == []

    def test_get_recent_history_less_than_limit(self, store, project):
        """Test when there are fewer entries than the limit."""
        # Add only 3 entries
        for i in range(3):
            store.add_history_entry(
                project_id=project.project_id,
                entry_type="task",
                summary=f"Entry {i}",
            )

        result = store.get_recent_history(project.project_id, limit=10)

        assert len(result) == 3


class TestCrossProjectIsolation:
    """Tests for cross-project data isolation."""

    def test_list_history_entries_isolated_by_project(self, store):
        """Test that list_history_entries only returns entries for the specified project."""
        project_a = store.create(name="Project A")
        project_b = store.create(name="Project B")

        # Add 3 entries to Project A
        for i in range(3):
            store.add_history_entry(
                project_id=project_a.project_id,
                entry_type="task",
                summary=f"Project A entry {i}",
            )

        # Add 2 entries to Project B
        for i in range(2):
            store.add_history_entry(
                project_id=project_b.project_id,
                entry_type="deployment",
                summary=f"Project B entry {i}",
            )

        result = store.list_history_entries(project_a.project_id)

        assert result["total"] == 3
        assert len(result["entries"]) == 3
        for entry in result["entries"]:
            assert entry["project_id"] == project_a.project_id

    def test_search_history_entries_isolated_by_project(self, store):
        """Test that search_history_entries only returns entries for the specified project."""
        project_a = store.create(name="Project A")
        project_b = store.create(name="Project B")

        # Add entries with unique search terms
        store.add_history_entry(
            project_id=project_a.project_id,
            entry_type="task",
            summary="Alpha unique term",
        )
        store.add_history_entry(
            project_id=project_b.project_id,
            entry_type="task",
            summary="Beta unique term",
        )

        result = store.search_history_entries(project_a.project_id, "Alpha")

        assert result["total"] == 1
        assert len(result["entries"]) == 1
        assert result["entries"][0]["project_id"] == project_a.project_id
        assert "Alpha" in result["entries"][0]["summary"]

    def test_get_recent_history_isolated_by_project(self, store):
        """Test that get_recent_history only returns entries for the specified project."""
        project_a = store.create(name="Project A")
        project_b = store.create(name="Project B")

        # Add 3 entries to Project A
        for i in range(3):
            store.add_history_entry(
                project_id=project_a.project_id,
                entry_type="task",
                summary=f"Project A recent {i}",
            )

        # Add 2 entries to Project B
        for i in range(2):
            store.add_history_entry(
                project_id=project_b.project_id,
                entry_type="task",
                summary=f"Project B recent {i}",
            )

        result = store.get_recent_history(project_a.project_id)

        assert len(result) == 3
        for entry in result:
            assert entry["project_id"] == project_a.project_id


class TestEntryMetadataRoundTrip:
    """Tests for entry_metadata round-trip preservation."""

    def test_entry_metadata_complex_structure(self, store, project):
        """Test that complex metadata is preserved exactly on round-trip."""
        complex_metadata = {
            "key": "value",
            "nested": {"a": 1},
            "list": [1, 2, 3],
        }

        entry = store.add_history_entry(
            project_id=project.project_id,
            entry_type="task",
            summary="Test metadata",
            entry_metadata=complex_metadata,
        )

        retrieved = store.get_history_entry(entry["id"])

        assert retrieved is not None
        assert retrieved["entry_metadata"] == complex_metadata
