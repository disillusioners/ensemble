"""Tests for critical notes injection in format_project_context()."""

import json
import pytest
from unittest.mock import MagicMock

from daemon.manager import format_project_context


class TestCriticalNotesInjection:
    """Tests for format_project_context() critical notes injection."""

    @pytest.fixture
    def base_project_dict(self):
        """Base project dict without critical_notes."""
        return {
            "project_id": "test-123",
            "name": "Test Project",
            "description": "A test project",
        }

    @pytest.fixture
    def mock_project(self, base_project_dict):
        """Create a mock project with to_dict method."""
        project = MagicMock()
        project.to_dict.return_value = {**base_project_dict, "critical_notes": []}
        return project

    def test_empty_critical_notes_no_section(self, mock_project):
        """Project with empty critical_notes=[] should not contain the section."""
        result = format_project_context(mock_project)
        assert "### ⚡ Critical Notes" not in result

    def test_entries_produce_section(self, mock_project, base_project_dict):
        """Project with entries should produce the Critical Notes section."""
        mock_project.to_dict.return_value = {
            **base_project_dict,
            "critical_notes": [
                {"category": "convention", "priority": "high", "summary": "Use snake_case"}
            ]
        }
        result = format_project_context(mock_project)
        assert "### ⚡ Critical Notes" in result

    def test_json_dump_no_critical_notes(self, mock_project, base_project_dict):
        """JSON block should NOT contain 'critical_notes' key (deduplication)."""
        mock_project.to_dict.return_value = {
            **base_project_dict,
            "critical_notes": [
                {"category": "convention", "priority": "high", "summary": "Use snake_case"}
            ]
        }
        result = format_project_context(mock_project)

        # Extract JSON block
        json_start = result.index("```json\n") + len("```json\n")
        json_end = result.index("\n```", json_start)
        json_block = json.loads(result[json_start:json_end])

        assert "critical_notes" not in json_block

    def test_section_contains_entries(self, mock_project, base_project_dict):
        """Formatted section should contain all entry summaries."""
        entries = [
            {"category": "convention", "priority": "high", "summary": "Use snake_case"},
            {"category": "pattern", "priority": "critical", "summary": "Use repository pattern"},
        ]
        mock_project.to_dict.return_value = {
            **base_project_dict,
            "critical_notes": entries
        }
        result = format_project_context(mock_project)

        assert "Use snake_case" in result
        assert "Use repository pattern" in result

    def test_non_dict_entry_skipped(self, mock_project, base_project_dict):
        """Entry that's not a dict should be skipped gracefully (no crash)."""
        mock_project.to_dict.return_value = {
            **base_project_dict,
            "critical_notes": [
                "invalid string entry",
                42,
                None,
                {"category": "convention", "priority": "high", "summary": "Valid entry"}
            ]
        }
        # Should not raise
        result = format_project_context(mock_project)
        assert "Valid entry" in result
        # Should not contain the invalid entries
        assert "invalid string entry" not in result
        assert "42" not in result

    def test_entry_with_reference(self, mock_project, base_project_dict):
        """Entry with reference should contain the ref string."""
        mock_project.to_dict.return_value = {
            **base_project_dict,
            "critical_notes": [
                {
                    "category": "pattern",
                    "priority": "high",
                    "summary": "Use caching",
                    "reference": "https://example.com/caching"
                }
            ]
        }
        result = format_project_context(mock_project)
        assert "*(ref: https://example.com/caching)*" in result

    def test_entry_without_reference(self, mock_project, base_project_dict):
        """Entry without reference should not contain ref string."""
        mock_project.to_dict.return_value = {
            **base_project_dict,
            "critical_notes": [
                {
                    "category": "convention",
                    "priority": "high",
                    "summary": "Use type hints"
                }
            ]
        }
        result = format_project_context(mock_project)
        assert "*(ref:" not in result

    def test_priority_icons(self, mock_project, base_project_dict):
        """Priority icons should map correctly: critical=🔴, high=🟡, medium=🟢."""
        # Test critical
        mock_project.to_dict.return_value = {
            **base_project_dict,
            "critical_notes": [
                {"category": "risk", "priority": "critical", "summary": "Critical issue"}
            ]
        }
        result = format_project_context(mock_project)
        assert "🔴" in result

        # Test high
        mock_project.to_dict.return_value = {
            **base_project_dict,
            "critical_notes": [
                {"category": "convention", "priority": "high", "summary": "High priority"}
            ]
        }
        result = format_project_context(mock_project)
        assert "🟡" in result

        # Test medium
        mock_project.to_dict.return_value = {
            **base_project_dict,
            "critical_notes": [
                {"category": "pattern", "priority": "medium", "summary": "Medium priority"}
            ]
        }
        result = format_project_context(mock_project)
        assert "🟢" in result

    def test_unknown_priority_icon(self, mock_project, base_project_dict):
        """Entry with unknown priority should use ⚪."""
        mock_project.to_dict.return_value = {
            **base_project_dict,
            "critical_notes": [
                {"category": "constraint", "priority": "urgent", "summary": "Unknown priority"}
            ]
        }
        result = format_project_context(mock_project)
        assert "⚪" in result

    def test_category_formatting(self, mock_project, base_project_dict):
        """Category should be shown as **[category]**."""
        mock_project.to_dict.return_value = {
            **base_project_dict,
            "critical_notes": [
                {"category": "convention", "priority": "high", "summary": "Test"}
            ]
        }
        result = format_project_context(mock_project)
        assert "**[convention]**" in result

    def test_summary_formatting(self, mock_project, base_project_dict):
        """Summary should be shown after category."""
        mock_project.to_dict.return_value = {
            **base_project_dict,
            "critical_notes": [
                {"category": "decision", "priority": "high", "summary": "Use async/await"}
            ]
        }
        result = format_project_context(mock_project)
        assert "Use async/await" in result

    def test_multiple_entries(self, mock_project, base_project_dict):
        """All entries should be formatted in order."""
        entries = [
            {"category": "convention", "priority": "critical", "summary": "First entry"},
            {"category": "pattern", "priority": "high", "summary": "Second entry"},
            {"category": "risk", "priority": "medium", "summary": "Third entry"},
        ]
        mock_project.to_dict.return_value = {
            **base_project_dict,
            "critical_notes": entries
        }
        result = format_project_context(mock_project)

        # All entries present
        assert "First entry" in result
        assert "Second entry" in result
        assert "Third entry" in result

        # Order preserved
        first_pos = result.index("First entry")
        second_pos = result.index("Second entry")
        third_pos = result.index("Third entry")
        assert first_pos < second_pos < third_pos

    def test_project_without_to_dict(self, base_project_dict):
        """Project without to_dict method should use vars(project) fallback."""
        # Create a simple object without to_dict
        class SimpleProject:
            def __init__(self, data):
                self.project_id = data["project_id"]
                self.name = data["name"]
                self.critical_notes = data["critical_notes"]

        project = SimpleProject({**base_project_dict, "critical_notes": []})
        result = format_project_context(project)
        assert "## Related Project" in result

    def test_missing_critical_notes_key(self, base_project_dict):
        """Project dict without critical_notes key should be treated as empty."""
        class SimpleProject:
            def __init__(self, data):
                self.project_id = data["project_id"]
                self.name = data["name"]

            def to_dict(self):
                return {"project_id": self.project_id, "name": self.name}

        project = SimpleProject(base_project_dict)
        result = format_project_context(project)
        assert "### ⚡ Critical Notes" not in result
