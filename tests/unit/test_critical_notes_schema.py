"""Tests for CriticalNotes model, Project integration, and migration."""

import uuid
import pytest
from pathlib import Path
from pydantic import ValidationError

from daemon.repositories.project.models import (
    CriticalNotes,
    CriticalNotesCategory,
    CriticalNotesPriority,
    Project,
)


class TestCriticalNotesModel:
    """Tests for CriticalNotes Pydantic model."""

    def test_create_valid_entry(self):
        """Create with valid fields should succeed."""
        entry = CriticalNotes(
            category="convention",
            priority="high",
            summary="Use snake_case for variables"
        )
        assert entry.category == "convention"
        assert entry.priority == "high"
        assert entry.summary == "Use snake_case for variables"

    def test_default_id_is_uuid(self):
        """ID should be a valid UUID string by default."""
        entry = CriticalNotes(
            category="pattern",
            priority="critical",
            summary="Use repository pattern"
        )
        # Should not raise
        uuid.UUID(entry.id)

    def test_default_timestamps(self):
        """created_at and updated_at should be ISO timestamps."""
        entry = CriticalNotes(
            category="decision",
            priority="medium",
            summary="Use async/await"
        )
        # Should be valid ISO format (basic check)
        assert "T" in entry.created_at
        assert "T" in entry.updated_at
        # Should be parseable (basic sanity check)
        from datetime import datetime
        datetime.fromisoformat(entry.created_at.replace("Z", "+00:00"))
        datetime.fromisoformat(entry.updated_at.replace("Z", "+00:00"))

    def test_invalid_category_raises(self):
        """Category='unknown' should raise ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            CriticalNotes(
                category="unknown",
                priority="high",
                summary="Test summary"
            )
        assert "Invalid category 'unknown'" in str(exc_info.value)

    def test_invalid_priority_raises(self):
        """Priority='urgent' should raise ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            CriticalNotes(
                category="convention",
                priority="urgent",
                summary="Test summary"
            )
        assert "Invalid priority 'urgent'" in str(exc_info.value)

    def test_summary_too_long_raises(self):
        """Summary with 201 chars should raise ValidationError with ≤200 chars message."""
        long_summary = "x" * 201
        with pytest.raises(ValidationError) as exc_info:
            CriticalNotes(
                category="convention",
                priority="high",
                summary=long_summary
            )
        assert "≤200 chars" in str(exc_info.value)

    def test_all_categories_valid(self):
        """Each category value should create successfully."""
        for cat in CriticalNotesCategory:
            entry = CriticalNotes(
                category=cat.value,
                priority="high",
                summary="Test summary"
            )
            assert entry.category == cat.value

    def test_all_priorities_valid(self):
        """Each priority value should create successfully."""
        for pri in CriticalNotesPriority:
            entry = CriticalNotes(
                category="convention",
                priority=pri.value,
                summary="Test summary"
            )
            assert entry.priority == pri.value

    def test_to_dict_returns_all_fields(self):
        """to_dict() should return all 8 fields."""
        entry = CriticalNotes(
            category="pattern",
            priority="critical",
            summary="Use adapter pattern",
            reference="https://example.com",
            source_agent="developer"
        )
        d = entry.to_dict()

        expected_fields = [
            "id", "created_at", "updated_at", "source_agent",
            "category", "priority", "summary", "reference"
        ]
        for field in expected_fields:
            assert field in d, f"Missing field: {field}"

    def test_reference_default_none(self):
        """Reference should default to None."""
        entry = CriticalNotes(
            category="convention",
            priority="high",
            summary="Test"
        )
        assert entry.reference is None

    def test_source_agent_default_empty(self):
        """source_agent should default to empty string."""
        entry = CriticalNotes(
            category="convention",
            priority="high",
            summary="Test"
        )
        assert entry.source_agent == ""

    def test_empty_summary_allowed(self):
        """Empty string summary should be allowed at model level."""
        entry = CriticalNotes(
            category="convention",
            priority="high",
            summary=""
        )
        assert entry.summary == ""


class TestProjectCriticalNotes:
    """Tests for Project model critical_notes integration (now via repository)."""

    def test_project_to_dict_does_not_include_critical_notes(self):
        """to_dict() should NOT include critical_notes key (now in dedicated table)."""
        project = Project(name="Test Project")
        d = project.to_dict()
        # critical_notes is no longer on Project model - it's in a separate table
        assert "critical_notes" not in d

    def test_project_to_dict_excludes_critical_notes_field(self):
        """to_dict() should not have critical_notes key (migrated to separate table)."""
        project = Project(name="Test Project")
        d = project.to_dict()
        # The comment in to_dict says "critical_notes removed - now in dedicated table"
        # So critical_notes should NOT appear in to_dict output
        assert "critical_notes" not in d


class TestMigrationFile:
    """Tests for critical_notes migration file."""

    MIGRATION_PATH = Path(
        "daemon/migrations/versions/20260524_000001_create_critical_notes_table.sql"
    )

    def test_migration_file_exists(self):
        """Migration file should exist at expected path."""
        assert self.MIGRATION_PATH.exists(), f"Migration file not found: {self.MIGRATION_PATH}"

    def test_migration_has_up_section(self):
        """Migration should contain '-- UP' section."""
        content = self.MIGRATION_PATH.read_text()
        assert "-- UP" in content

    def test_migration_has_down_section(self):
        """Migration should contain '-- DOWN' section."""
        content = self.MIGRATION_PATH.read_text()
        assert "-- DOWN" in content

    def test_migration_up_creates_critical_notes_table(self):
        """UP section should create the critical_notes table."""
        content = self.MIGRATION_PATH.read_text()
        up_section = content.split("-- DOWN")[0]
        assert "CREATE TABLE IF NOT EXISTS critical_notes" in up_section

    def test_migration_down_drops_table(self):
        """DOWN section should drop the critical_notes table."""
        content = self.MIGRATION_PATH.read_text()
        down_section = content.split("-- DOWN")[1]
        assert "DROP TABLE IF EXISTS critical_notes" in down_section

    def test_migration_has_project_id_foreign_key(self):
        """Migration should include project_id foreign key to projects table."""
        content = self.MIGRATION_PATH.read_text()
        up_section = content.split("-- DOWN")[0]
        assert "project_id TEXT NOT NULL REFERENCES projects" in up_section

    def test_migration_creates_project_id_index(self):
        """Migration should create an index on project_id."""
        content = self.MIGRATION_PATH.read_text()
        up_section = content.split("-- DOWN")[0]
        assert "CREATE INDEX IF NOT EXISTS ix_critical_notes_project_id" in up_section


class TestCriticalNoteModel:
    """Tests for CriticalNoteModel SQLModel table."""

    def test_critical_note_model_has_required_fields(self):
        """CriticalNoteModel should have all required fields."""
        from daemon.repositories.project.models import CriticalNoteModel
        # Check the model has the expected fields
        assert hasattr(CriticalNoteModel, 'id')
        assert hasattr(CriticalNoteModel, 'project_id')
        assert hasattr(CriticalNoteModel, 'created_at')
        assert hasattr(CriticalNoteModel, 'updated_at')
        assert hasattr(CriticalNoteModel, 'source_agent')
        assert hasattr(CriticalNoteModel, 'category')
        assert hasattr(CriticalNoteModel, 'priority')
        assert hasattr(CriticalNoteModel, 'summary')
        assert hasattr(CriticalNoteModel, 'reference')

    def test_critical_note_model_to_dict(self):
        """CriticalNoteModel.to_dict() should return all fields."""
        from daemon.repositories.project.models import CriticalNoteModel
        note = CriticalNoteModel(
            project_id="test-project",
            source_agent="test-agent",
            category="convention",
            priority="high",
            summary="Test summary",
            reference="https://example.com",
        )
        d = note.to_dict()
        assert d["project_id"] == "test-project"
        assert d["source_agent"] == "test-agent"
        assert d["category"] == "convention"
        assert d["priority"] == "high"
        assert d["summary"] == "Test summary"
        assert d["reference"] == "https://example.com"
        assert "id" in d
        assert "created_at" in d
        assert "updated_at" in d
