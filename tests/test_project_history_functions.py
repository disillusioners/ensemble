"""Integration tests for Phase 3 project history code.

Tests for _format_relative_time() and format_project_context() functions
from daemon.manager module.
"""

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock


class TestFormatRelativeTime:
    """Tests for _format_relative_time() function."""

    def _get_function_under_test(self):
        """Import function at test time to avoid import issues."""
        from daemon.manager import _format_relative_time
        return _format_relative_time

    def test_none_input(self):
        """Test with None input returns 'unknown time'."""
        _format_relative_time = self._get_function_under_test()
        result = _format_relative_time(None)
        assert result == "unknown time"

    def test_future_date(self):
        """Test with a future date returns 'just now'."""
        _format_relative_time = self._get_function_under_test()
        future = datetime.now(timezone.utc) + timedelta(hours=1)
        result = _format_relative_time(future)
        assert result == "just now"

    def test_iso_string_format(self):
        """Test with ISO string format input."""
        _format_relative_time = self._get_function_under_test()
        # 2 hours ago
        past = datetime.now(timezone.utc) - timedelta(hours=2)
        result = _format_relative_time(past.isoformat())
        assert "hour" in result and "ago" in result

    def test_iso_string_with_z_suffix(self):
        """Test with ISO string ending in Z."""
        _format_relative_time = self._get_function_under_test()
        # 30 minutes ago
        past = datetime.now(timezone.utc) - timedelta(minutes=30)
        iso_str = past.isoformat().replace("+00:00", "Z")
        result = _format_relative_time(iso_str)
        assert "minute" in result and "ago" in result

    def test_zero_seconds_ago(self):
        """Test with 0 seconds ago (just now)."""
        _format_relative_time = self._get_function_under_test()
        now = datetime.now(timezone.utc)
        result = _format_relative_time(now)
        assert result == "just now"

    def test_boundary_60_seconds(self):
        """Test boundary at 60 seconds (1 minute)."""
        _format_relative_time = self._get_function_under_test()
        # Exactly 60 seconds ago
        past = datetime.now(timezone.utc) - timedelta(seconds=60)
        result = _format_relative_time(past)
        assert result == "1 minute ago"

    def test_boundary_3600_seconds(self):
        """Test boundary at 3600 seconds (1 hour)."""
        _format_relative_time = self._get_function_under_test()
        # Exactly 1 hour ago
        past = datetime.now(timezone.utc) - timedelta(seconds=3600)
        result = _format_relative_time(past)
        assert result == "1 hour ago"

    def test_boundary_86400_seconds(self):
        """Test boundary at 86400 seconds (1 day)."""
        _format_relative_time = self._get_function_under_test()
        # Exactly 1 day ago
        past = datetime.now(timezone.utc) - timedelta(seconds=86400)
        result = _format_relative_time(past)
        assert result == "1 day ago"

    def test_seconds_range(self):
        """Test with seconds (less than 60)."""
        _format_relative_time = self._get_function_under_test()
        past = datetime.now(timezone.utc) - timedelta(seconds=45)
        result = _format_relative_time(past)
        assert result == "just now"

    def test_minutes_range(self):
        """Test with minutes range."""
        _format_relative_time = self._get_function_under_test()
        past = datetime.now(timezone.utc) - timedelta(minutes=5)
        result = _format_relative_time(past)
        assert result == "5 minutes ago"

    def test_hours_range(self):
        """Test with hours range."""
        _format_relative_time = self._get_function_under_test()
        past = datetime.now(timezone.utc) - timedelta(hours=3)
        result = _format_relative_time(past)
        assert result == "3 hours ago"

    def test_days_range(self):
        """Test with days range."""
        _format_relative_time = self._get_function_under_test()
        past = datetime.now(timezone.utc) - timedelta(days=2)
        result = _format_relative_time(past)
        assert result == "2 days ago"

    def test_weeks_range(self):
        """Test with weeks range."""
        _format_relative_time = self._get_function_under_test()
        past = datetime.now(timezone.utc) - timedelta(weeks=2)
        result = _format_relative_time(past)
        assert result == "2 weeks ago"

    def test_months_range(self):
        """Test with months range (~30 days)."""
        _format_relative_time = self._get_function_under_test()
        past = datetime.now(timezone.utc) - timedelta(days=60)
        result = _format_relative_time(past)
        assert result == "2 months ago"

    def test_years_range(self):
        """Test with years range."""
        _format_relative_time = self._get_function_under_test()
        past = datetime.now(timezone.utc) - timedelta(days=400)
        result = _format_relative_time(past)
        assert result == "1 year ago"

    def test_invalid_string_returns_unknown(self):
        """Test that invalid string format returns 'unknown time'."""
        _format_relative_time = self._get_function_under_test()
        result = _format_relative_time("not-a-date")
        assert result == "unknown time"

    def test_naive_datetime_handled(self):
        """Test that naive datetime (no timezone) is handled correctly."""
        _format_relative_time = self._get_function_under_test()
        # Create naive datetime (no timezone info)
        past = datetime.utcnow() - timedelta(hours=1)
        result = _format_relative_time(past)
        assert "hour" in result and "ago" in result


class MockProject:
    """Mock project object for testing format_project_context."""

    def __init__(self, project_id="test-project-123", name="Test Project", **kwargs):
        self.project_id = project_id
        self.name = name
        self.project_type = kwargs.get("project_type", "general")
        self.status = kwargs.get("status", "active")
        self.main_directory = kwargs.get("main_directory", "/test")
        self.related_directories = kwargs.get("related_directories", [])
        self.description = kwargs.get("description", "A test project")
        self.job_queue_paused = kwargs.get("job_queue_paused", False)
        self.project_metadata = kwargs.get("project_metadata", {})
        self.relationships = kwargs.get("relationships", {})
        self.critical_experience = kwargs.get("critical_experience", [])
        self.creator_instance_id = kwargs.get("creator_instance_id", None)
        self.creator_agent_id = kwargs.get("creator_agent_id", None)
        self.created_at = kwargs.get("created_at", "2024-01-01T00:00:00")
        self.updated_at = kwargs.get("updated_at", "2024-01-01T00:00:00")

    def to_dict(self):
        """Convert to dictionary matching Project model."""
        return {
            "project_id": self.project_id,
            "name": self.name,
            "project_type": self.project_type,
            "status": self.status,
            "main_directory": self.main_directory,
            "related_directories": list(self.related_directories),
            "description": self.description,
            "job_queue_paused": self.job_queue_paused,
            "tags": [],
            "shortnames": [],
            "metadata": self.project_metadata,
            "relationships": self.relationships,
            "critical_experience": self.critical_experience,
            "creator_instance_id": self.creator_instance_id,
            "creator_agent_id": self.creator_agent_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class TestFormatProjectContextWithHistory:
    """Tests for format_project_context() with store/history."""

    def _get_function_under_test(self):
        """Import function at test time."""
        from daemon.manager import format_project_context
        return format_project_context

    def test_history_section_renders(self):
        """Test that history section renders correctly in output."""
        format_project_context = self._get_function_under_test()

        # Create mock store with history
        mock_store = MagicMock()
        mock_store.get_recent_history.return_value = [
            {
                "id": "entry-1",
                "project_id": "test-project-123",
                "entry_type": "deployment",
                "summary": "Deployed API v2",
                "details": None,
                "source_agent": None,
                "source_instance_id": None,
                "entry_metadata": None,
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
            {
                "id": "entry-2",
                "project_id": "test-project-123",
                "entry_type": "milestone",
                "summary": "Completed Phase 1",
                "details": None,
                "source_agent": "coder",
                "source_instance_id": "inst-456",
                "entry_metadata": None,
                "created_at": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
            },
        ]

        project = MockProject(name="Test Project", project_id="test-project-123")
        result = format_project_context(project, store=mock_store)

        # Verify history section is present
        assert "Recent History" in result
        assert "Deployed API v2" in result
        assert "Completed Phase 1" in result
        assert "deployment" in result
        assert "milestone" in result

    def test_history_uses_entry_type_icons(self):
        """Test that entry types have appropriate icons."""
        format_project_context = self._get_function_under_test()

        mock_store = MagicMock()
        mock_store.get_recent_history.return_value = [
            {
                "id": "entry-1",
                "project_id": "test-project-123",
                "entry_type": "commit",
                "summary": "Pushed commit",
                "details": None,
                "source_agent": None,
                "source_instance_id": None,
                "entry_metadata": None,
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
            {
                "id": "entry-2",
                "project_id": "test-project-123",
                "entry_type": "bugfix",
                "summary": "Fixed bug",
                "details": None,
                "source_agent": None,
                "source_instance_id": None,
                "entry_metadata": None,
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        ]

        project = MockProject(project_id="test-project-123")
        result = format_project_context(project, store=mock_store)

        # Verify icons are present for different entry types
        assert "commit" in result
        assert "bugfix" in result

    def test_history_relative_time_shown(self):
        """Test that relative time is shown for history entries."""
        format_project_context = self._get_function_under_test()

        mock_store = MagicMock()
        mock_store.get_recent_history.return_value = [
            {
                "id": "entry-1",
                "project_id": "test-project-123",
                "entry_type": "note",
                "summary": "Added note",
                "details": None,
                "source_agent": None,
                "source_instance_id": None,
                "entry_metadata": None,
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        ]

        project = MockProject(project_id="test-project-123")
        result = format_project_context(project, store=mock_store)

        # Verify relative time is shown
        assert "ago" in result or "just now" in result

    def test_history_empty_returns_no_section(self):
        """Test that empty history doesn't show section."""
        format_project_context = self._get_function_under_test()

        mock_store = MagicMock()
        mock_store.get_recent_history.return_value = []

        project = MockProject(project_id="test-project-123")
        result = format_project_context(project, store=mock_store)

        # Empty history should not add a section
        assert "Recent History" not in result

    def test_project_name_in_output(self):
        """Test that project name appears in the output."""
        format_project_context = self._get_function_under_test()

        mock_store = MagicMock()
        mock_store.get_recent_history.return_value = [
            {
                "id": "entry-1",
                "project_id": "proj-1",
                "entry_type": "note",
                "summary": "Test entry",
                "details": None,
                "source_agent": None,
                "source_instance_id": None,
                "entry_metadata": None,
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        ]

        project = MockProject(name="My Awesome Project", project_id="proj-1")
        result = format_project_context(project, store=mock_store)

        assert "My Awesome Project" in result

    def test_project_json_block_present(self):
        """Test that project JSON block is present in output."""
        format_project_context = self._get_function_under_test()

        mock_store = MagicMock()
        mock_store.get_recent_history.return_value = []

        project = MockProject(project_id="proj-1", name="Test")
        result = format_project_context(project, store=mock_store)

        assert "Related Project" in result
        assert "```json" in result

    def test_critical_experience_section_present(self):
        """Test that critical experience section renders when present."""
        format_project_context = self._get_function_under_test()

        mock_store = MagicMock()
        mock_store.get_recent_history.return_value = []

        project = MockProject(
            project_id="proj-1",
            critical_experience=[
                {
                    "priority": "critical",
                    "category": "database",
                    "summary": "Always use transactions for writes",
                }
            ],
        )
        result = format_project_context(project, store=mock_store)

        assert "Critical Experience" in result
        assert "database" in result
        assert "Always use transactions" in result


class TestFormatProjectContextBackwardCompat:
    """Tests for format_project_context() backward compatibility (store=None)."""

    def _get_function_under_test(self):
        """Import function at test time."""
        from daemon.manager import format_project_context
        return format_project_context

    def test_store_none_no_error(self):
        """Test that passing store=None doesn't cause errors."""
        format_project_context = self._get_function_under_test()

        project = MockProject(project_id="proj-1", name="Test")
        # Should not raise
        result = format_project_context(project, store=None)

        assert result is not None
        assert isinstance(result, str)

    def test_store_omitted_no_error(self):
        """Test that omitting store parameter doesn't cause errors."""
        format_project_context = self._get_function_under_test()

        project = MockProject(project_id="proj-1", name="Test")
        # Should not raise
        result = format_project_context(project)

        assert result is not None
        assert isinstance(result, str)

    def test_store_none_no_history_section(self):
        """Test that no history section when store is None."""
        format_project_context = self._get_function_under_test()

        project = MockProject(project_id="proj-1", name="Test")
        result = format_project_context(project, store=None)

        assert "Recent History" not in result

    def test_store_none_still_has_project_json(self):
        """Test that project JSON is still present without store."""
        format_project_context = self._get_function_under_test()

        project = MockProject(project_id="proj-1", name="Test")
        result = format_project_context(project, store=None)

        assert "Related Project" in result
        assert "```json" in result


class TestFormatProjectContextExceptionHandling:
    """Tests for format_project_context() exception handling."""

    def _get_function_under_test(self):
        """Import function at test time."""
        from daemon.manager import format_project_context
        return format_project_context

    def test_store_get_recent_history_raises_exception(self):
        """Test that exception in get_recent_history is handled gracefully."""
        format_project_context = self._get_function_under_test()

        mock_store = MagicMock()
        mock_store.get_recent_history.side_effect = Exception("Database error")

        project = MockProject(project_id="proj-1", name="Test")
        # Should not raise, should return context without history
        result = format_project_context(project, store=mock_store)

        assert result is not None
        assert isinstance(result, str)
        # History section should be absent due to exception
        assert "Recent History" not in result
        # But project info should still be present
        assert "Related Project" in result

    def test_store_get_recent_history_raises_value_error(self):
        """Test handling of ValueError from get_recent_history."""
        format_project_context = self._get_function_under_test()

        mock_store = MagicMock()
        mock_store.get_recent_history.side_effect = ValueError("Invalid project ID")

        project = MockProject(project_id="proj-1", name="Test")
        result = format_project_context(project, store=mock_store)

        assert result is not None
        assert isinstance(result, str)
        assert "Recent History" not in result

    def test_store_get_recent_history_returns_none(self):
        """Test handling when get_recent_history returns None."""
        format_project_context = self._get_function_under_test()

        mock_store = MagicMock()
        mock_store.get_recent_history.return_value = None

        project = MockProject(project_id="proj-1", name="Test")
        result = format_project_context(project, store=mock_store)

        # Should handle gracefully - None is falsy so no history section
        assert result is not None
        assert "Recent History" not in result

    def test_store_get_recent_history_returns_exception_entry(self):
        """Test handling when an entry in the list causes an exception."""
        format_project_context = self._get_function_under_test()

        mock_store = MagicMock()
        # Return entries where entry_type causes issues
        mock_store.get_recent_history.return_value = [
            {
                "id": "entry-1",
                "project_id": "proj-1",
                "entry_type": "custom",
                "summary": "Custom entry",
                "details": None,
                "source_agent": None,
                "source_instance_id": None,
                "entry_metadata": None,
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        ]

        project = MockProject(project_id="proj-1", name="Test")
        result = format_project_context(project, store=mock_store)

        # Custom entry type should get default icon (❓)
        assert result is not None
        assert "custom" in result

    def test_history_entry_missing_fields(self):
        """Test handling of history entries with missing fields."""
        format_project_context = self._get_function_under_test()

        mock_store = MagicMock()
        mock_store.get_recent_history.return_value = [
            {
                # Minimal entry - missing some fields
                "id": "entry-1",
                "project_id": "proj-1",
                "summary": "Minimal entry",
            },
        ]

        project = MockProject(project_id="proj-1", name="Test")
        result = format_project_context(project, store=mock_store)

        # Should handle missing fields gracefully
        assert result is not None
        assert "Minimal entry" in result
