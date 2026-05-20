"""Tests for CriticalExperience model, Project integration, and migration."""

import uuid
import pytest
from pathlib import Path
from pydantic import ValidationError

from daemon.repositories.project.models import (
    CriticalExperience,
    CriticalExperienceCategory,
    CriticalExperiencePriority,
    Project,
)


class TestCriticalExperienceModel:
    """Tests for CriticalExperience Pydantic model."""

    def test_create_valid_entry(self):
        """Create with valid fields should succeed."""
        entry = CriticalExperience(
            category="convention",
            priority="high",
            summary="Use snake_case for variables"
        )
        assert entry.category == "convention"
        assert entry.priority == "high"
        assert entry.summary == "Use snake_case for variables"

    def test_default_id_is_uuid(self):
        """ID should be a valid UUID string by default."""
        entry = CriticalExperience(
            category="pattern",
            priority="critical",
            summary="Use repository pattern"
        )
        # Should not raise
        uuid.UUID(entry.id)

    def test_default_timestamps(self):
        """created_at and updated_at should be ISO timestamps."""
        entry = CriticalExperience(
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
            CriticalExperience(
                category="unknown",
                priority="high",
                summary="Test summary"
            )
        assert "Invalid category 'unknown'" in str(exc_info.value)

    def test_invalid_priority_raises(self):
        """Priority='urgent' should raise ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            CriticalExperience(
                category="convention",
                priority="urgent",
                summary="Test summary"
            )
        assert "Invalid priority 'urgent'" in str(exc_info.value)

    def test_summary_too_long_raises(self):
        """Summary with 201 chars should raise ValidationError with ≤200 chars message."""
        long_summary = "x" * 201
        with pytest.raises(ValidationError) as exc_info:
            CriticalExperience(
                category="convention",
                priority="high",
                summary=long_summary
            )
        assert "≤200 chars" in str(exc_info.value)

    def test_all_categories_valid(self):
        """Each category value should create successfully."""
        for cat in CriticalExperienceCategory:
            entry = CriticalExperience(
                category=cat.value,
                priority="high",
                summary="Test summary"
            )
            assert entry.category == cat.value

    def test_all_priorities_valid(self):
        """Each priority value should create successfully."""
        for pri in CriticalExperiencePriority:
            entry = CriticalExperience(
                category="convention",
                priority=pri.value,
                summary="Test summary"
            )
            assert entry.priority == pri.value

    def test_to_dict_returns_all_fields(self):
        """to_dict() should return all 8 fields."""
        entry = CriticalExperience(
            category="pattern",
            priority="critical",
            summary="Use adapter pattern",
            reference="https://example.com",
            source_agent="coder"
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
        entry = CriticalExperience(
            category="convention",
            priority="high",
            summary="Test"
        )
        assert entry.reference is None

    def test_source_agent_default_empty(self):
        """source_agent should default to empty string."""
        entry = CriticalExperience(
            category="convention",
            priority="high",
            summary="Test"
        )
        assert entry.source_agent == ""

    def test_empty_summary_allowed(self):
        """Empty string summary should be allowed at model level."""
        entry = CriticalExperience(
            category="convention",
            priority="high",
            summary=""
        )
        assert entry.summary == ""


class TestProjectCriticalExperience:
    """Tests for Project model critical_experience field."""

    def test_project_critical_experience_default(self):
        """New Project should have critical_experience=[]."""
        project = Project(name="Test Project")
        assert project.critical_experience == []

    def test_project_to_dict_includes_critical_experience(self):
        """to_dict() should include 'critical_experience' key."""
        project = Project(name="Test Project")
        d = project.to_dict()
        assert "critical_experience" in d

    def test_project_to_dict_empty_list(self):
        """Empty list should be returned as empty list."""
        project = Project(name="Test Project")
        d = project.to_dict()
        assert d["critical_experience"] == []

    def test_project_to_dict_with_entries(self):
        """to_dict() should return critical_experience entries."""
        project = Project(
            name="Test Project",
            critical_experience=[
                {"category": "convention", "priority": "high", "summary": "Test"}
            ]
        )
        d = project.to_dict()
        assert len(d["critical_experience"]) == 1
        assert d["critical_experience"][0]["summary"] == "Test"


class TestMigrationFile:
    """Tests for critical_experience migration file."""

    MIGRATION_PATH = Path(
        "daemon/migrations/versions/20260520_000001_add_critical_experience_to_projects.sql"
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

    def test_migration_up_adds_column(self):
        """UP section should add critical_experience column."""
        content = self.MIGRATION_PATH.read_text()
        up_section = content.split("-- DOWN")[0]
        assert "ALTER TABLE projects ADD COLUMN critical_experience" in up_section

    def test_migration_down_drops_column(self):
        """DOWN section should drop critical_experience column."""
        content = self.MIGRATION_PATH.read_text()
        down_section = content.split("-- DOWN")[1]
        assert "ALTER TABLE projects DROP COLUMN critical_experience" in down_section

    def test_migration_default_empty_array(self):
        """Default value should be empty array '[]'."""
        content = self.MIGRATION_PATH.read_text()
        assert "DEFAULT '[]'" in content
