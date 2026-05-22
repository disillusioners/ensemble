"""Tests for project history injection in format_project_context()."""

import json
import pytest
from unittest.mock import MagicMock

from daemon.manager import format_project_context


class TestProjectHistoryInjection:
    """Tests for format_project_context() project history injection."""

    @pytest.fixture
    def base_project_dict(self):
        """Base project dict without critical_experience."""
        return {
            "project_id": "test-123",
            "name": "Test Project",
            "description": "A test project",
        }

    @pytest.fixture
    def mock_project(self, base_project_dict):
        """Create a mock project with to_dict method."""
        project = MagicMock()
        project.project_id = "test-123"
        project.to_dict.return_value = {**base_project_dict, "critical_experience": []}
        return project

    @pytest.fixture
    def mock_store(self):
        """Create a mock store for history access."""
        store = MagicMock()
        store.get_recent_history.return_value = []
        return store

    # --- History section rendering ---

    def test_empty_history_no_section(self, mock_project, mock_store):
        """Store with empty history should not produce the section."""
        mock_store.get_recent_history.return_value = []
        result = format_project_context(mock_project, store=mock_store)
        assert "### 📜 Recent History" not in result

    def test_history_entries_produce_section(self, mock_project, mock_store):
        """Store with entries should produce the Recent History section."""
        mock_store.get_recent_history.return_value = [
            {
                "entry_type": "milestone",
                "summary": "Released v1.0",
                "created_at": "2025-01-15T10:00:00"
            }
        ]
        result = format_project_context(mock_project, store=mock_store)
        assert "### 📜 Recent History" in result

    def test_history_entries_with_summaries(self, mock_project, mock_store):
        """Formatted section should contain all entry summaries."""
        mock_store.get_recent_history.return_value = [
            {"entry_type": "milestone", "summary": "First milestone", "created_at": "2025-01-15T10:00:00"},
            {"entry_type": "commit", "summary": "Second commit", "created_at": "2025-01-14T10:00:00"},
        ]
        result = format_project_context(mock_project, store=mock_store)
        assert "First milestone" in result
        assert "Second commit" in result

    def test_empty_history_list_does_not_produce_section(self, mock_project, mock_store):
        """Store returning empty list should not produce section."""
        mock_store.get_recent_history.return_value = []
        result = format_project_context(mock_project, store=mock_store)
        assert "### 📜 Recent History" not in result

    def test_none_store_no_error(self, mock_project):
        """Store=None should not cause errors."""
        result = format_project_context(mock_project, store=None)
        assert "## Related Project" in result
        assert "### 📜 Recent History" not in result

    # --- Emoji icon mapping for each HistoryEntryType ---

    def test_emoji_milestone(self, mock_project, mock_store):
        """Entry type 'milestone' should use 🏆."""
        mock_store.get_recent_history.return_value = [
            {"entry_type": "milestone", "summary": "Released v1.0", "created_at": "2025-01-15T10:00:00"}
        ]
        result = format_project_context(mock_project, store=mock_store)
        assert "🏆" in result
        assert "**[milestone]**" in result

    def test_emoji_commit(self, mock_project, mock_store):
        """Entry type 'commit' should use 📦."""
        mock_store.get_recent_history.return_value = [
            {"entry_type": "commit", "summary": "New feature", "created_at": "2025-01-15T10:00:00"}
        ]
        result = format_project_context(mock_project, store=mock_store)
        assert "📦" in result
        assert "**[commit]**" in result

    def test_emoji_phase(self, mock_project, mock_store):
        """Entry type 'phase' should use 🔀."""
        mock_store.get_recent_history.return_value = [
            {"entry_type": "phase", "summary": "Started development", "created_at": "2025-01-15T10:00:00"}
        ]
        result = format_project_context(mock_project, store=mock_store)
        assert "🔀" in result
        assert "**[phase]**" in result

    def test_emoji_bugfix(self, mock_project, mock_store):
        """Entry type 'bugfix' should use 🐛."""
        mock_store.get_recent_history.return_value = [
            {"entry_type": "bugfix", "summary": "Fixed login bug", "created_at": "2025-01-15T10:00:00"}
        ]
        result = format_project_context(mock_project, store=mock_store)
        assert "🐛" in result
        assert "**[bugfix]**" in result

    def test_emoji_deployment(self, mock_project, mock_store):
        """Entry type 'deployment' should use 🚀."""
        mock_store.get_recent_history.return_value = [
            {"entry_type": "deployment", "summary": "Deployed to prod", "created_at": "2025-01-15T10:00:00"}
        ]
        result = format_project_context(mock_project, store=mock_store)
        assert "🚀" in result
        assert "**[deployment]**" in result

    def test_emoji_note(self, mock_project, mock_store):
        """Entry type 'note' should use 📝."""
        mock_store.get_recent_history.return_value = [
            {"entry_type": "note", "summary": "Remember to refactor", "created_at": "2025-01-15T10:00:00"}
        ]
        result = format_project_context(mock_project, store=mock_store)
        assert "📝" in result
        assert "**[note]**" in result

    def test_emoji_config_change(self, mock_project, mock_store):
        """Entry type 'config_change' should use ⚙️."""
        mock_store.get_recent_history.return_value = [
            {"entry_type": "config_change", "summary": "Updated config", "created_at": "2025-01-15T10:00:00"}
        ]
        result = format_project_context(mock_project, store=mock_store)
        assert "⚙️" in result
        assert "**[config_change]**" in result

    def test_emoji_other(self, mock_project, mock_store):
        """Entry type 'other' should use ❓."""
        mock_store.get_recent_history.return_value = [
            {"entry_type": "other", "summary": "Misc update", "created_at": "2025-01-15T10:00:00"}
        ]
        result = format_project_context(mock_project, store=mock_store)
        assert "❓" in result
        assert "**[other]**" in result

    def test_emoji_unknown_type_defaults_to_other(self, mock_project, mock_store):
        """Unknown entry type should use ❓ as fallback."""
        mock_store.get_recent_history.return_value = [
            {"entry_type": "custom_type", "summary": "Custom entry", "created_at": "2025-01-15T10:00:00"}
        ]
        result = format_project_context(mock_project, store=mock_store)
        assert "❓" in result

    # --- Format structure ---

    def test_section_header_format(self, mock_project, mock_store):
        """Section header should match expected format."""
        mock_store.get_recent_history.return_value = [
            {"entry_type": "milestone", "summary": "Test", "created_at": "2025-01-15T10:00:00"}
        ]
        result = format_project_context(mock_project, store=mock_store)
        assert "### 📜 Recent History" in result

    def test_entry_format_structure(self, mock_project, mock_store):
        """Entry should follow: '- {emoji} **[{type}]** {summary} — _{relative_time}_'"""
        mock_store.get_recent_history.return_value = [
            {"entry_type": "milestone", "summary": "Released v1.0", "created_at": "2025-01-15T10:00:00"}
        ]
        result = format_project_context(mock_project, store=mock_store)
        # Check format: emoji, type tag, summary, and relative time
        assert "🏆" in result
        assert "**[milestone]**" in result
        assert "Released v1.0" in result
        assert "_" in result  # Italics for relative time

    # --- Entry ordering and limits ---

    def test_entries_ordered_by_most_recent(self, mock_project, mock_store):
        """Entries should appear in order returned by get_recent_history."""
        mock_store.get_recent_history.return_value = [
            {"entry_type": "milestone", "summary": "First (oldest)", "created_at": "2025-01-10T10:00:00"},
            {"entry_type": "commit", "summary": "Second (middle)", "created_at": "2025-01-12T10:00:00"},
            {"entry_type": "note", "summary": "Third (newest)", "created_at": "2025-01-15T10:00:00"},
        ]
        result = format_project_context(mock_project, store=mock_store)

        first_pos = result.index("First (oldest)")
        second_pos = result.index("Second (middle)")
        third_pos = result.index("Third (newest)")
        assert first_pos < second_pos < third_pos

    def test_limit_10_in_spec(self, mock_project, mock_store):
        """Store should be called with limit=10 as per spec."""
        mock_store.get_recent_history.return_value = []
        format_project_context(mock_project, store=mock_store)
        mock_store.get_recent_history.assert_called_once_with("test-123", limit=10)

    # --- Error handling ---

    def test_store_exception_handled_gracefully(self, mock_project, mock_store):
        """Store exception should be caught and logged, not crash."""
        mock_store.get_recent_history.side_effect = Exception("DB error")
        # Should not raise
        result = format_project_context(mock_project, store=mock_store)
        assert "## Related Project" in result
        # History section should not be present due to exception
        assert "### 📜 Recent History" not in result

    def test_missing_entry_type_handled(self, mock_project, mock_store):
        """Entry without entry_type should use default emoji."""
        mock_store.get_recent_history.return_value = [
            {"summary": "Entry without type", "created_at": "2025-01-15T10:00:00"}
        ]
        result = format_project_context(mock_project, store=mock_store)
        assert "❓" in result

    def test_missing_summary_handled(self, mock_project, mock_store):
        """Entry without summary should not crash."""
        mock_store.get_recent_history.return_value = [
            {"entry_type": "milestone", "created_at": "2025-01-15T10:00:00"}
        ]
        # Should not raise
        result = format_project_context(mock_project, store=mock_store)
        assert "### 📜 Recent History" in result

    def test_missing_created_at_handled(self, mock_project, mock_store):
        """Entry without created_at should not crash."""
        mock_store.get_recent_history.return_value = [
            {"entry_type": "milestone", "summary": "Test entry"}
        ]
        # Should not raise
        result = format_project_context(mock_project, store=mock_store)
        assert "### 📜 Recent History" in result

    # --- Integration with both sections ---

    def test_both_ce_and_history_sections_present(self, mock_project, mock_store, base_project_dict):
        """When both CE and history exist, both sections should be present."""
        mock_project.to_dict.return_value = {
            **base_project_dict,
            "critical_experience": [
                {"category": "convention", "priority": "high", "summary": "Use snake_case"}
            ]
        }
        mock_store.get_recent_history.return_value = [
            {"entry_type": "milestone", "summary": "Released v1.0", "created_at": "2025-01-15T10:00:00"}
        ]
        result = format_project_context(mock_project, store=mock_store)

        assert "### ⚡ Critical Experience" in result
        assert "### 📜 Recent History" in result
        assert "Use snake_case" in result
        assert "Released v1.0" in result


class TestProjectHistoryFieldSerialization:
    """Tests for recent_history field in ProjectResponse schema."""

    def test_recent_history_field_exists(self):
        """ProjectResponse should have recent_history field."""
        from daemon.routers.schemas import ProjectResponse

        assert hasattr(ProjectResponse, "model_fields")
        assert "recent_history" in ProjectResponse.model_fields

    def test_recent_history_field_type(self):
        """recent_history field should be list[dict] | None."""
        from daemon.routers.schemas import ProjectResponse

        field = ProjectResponse.model_fields["recent_history"]
        # Check that it's optional (None is allowed)
        assert field.is_required() is False or field.default is None

    def test_recent_history_serialization(self):
        """recent_history entries should serialize properly."""
        from daemon.routers.schemas import ProjectResponse
        from datetime import datetime

        response = ProjectResponse(
            project_id="test-123",
            name="Test Project",
            project_type="software",
            status="active",
            created_at="2025-01-01T00:00:00",
            updated_at="2025-01-01T00:00:00",
            recent_history=[
                {"entry_type": "milestone", "summary": "Test milestone"}
            ]
        )

        data = response.model_dump()
        assert "recent_history" in data
        assert len(data["recent_history"]) == 1
        assert data["recent_history"][0]["entry_type"] == "milestone"

    def test_recent_history_none_allowed(self):
        """recent_history should be None when not set."""
        from daemon.routers.schemas import ProjectResponse

        response = ProjectResponse(
            project_id="test-123",
            name="Test Project",
            project_type="software",
            status="active",
            created_at="2025-01-01T00:00:00",
            updated_at="2025-01-01T00:00:00",
        )

        data = response.model_dump()
        assert data.get("recent_history") is None

    def test_recent_history_empty_list_allowed(self):
        """recent_history should accept empty list."""
        from daemon.routers.schemas import ProjectResponse

        response = ProjectResponse(
            project_id="test-123",
            name="Test Project",
            project_type="software",
            status="active",
            created_at="2025-01-01T00:00:00",
            updated_at="2025-01-01T00:00:00",
            recent_history=[]
        )

        data = response.model_dump()
        assert data["recent_history"] == []
