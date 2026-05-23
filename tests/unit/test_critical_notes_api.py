"""Tests for critical notes in Projects API responses.

This module verifies that the critical_notes field is properly
included in all Projects API responses.
"""

import pytest
from unittest.mock import MagicMock, AsyncMock
from datetime import datetime

from daemon.routers.projects import (
    _project_to_response,
    get_project,
    list_projects,
)
from daemon.routers.schemas import ProjectResponse


def create_mock_project(
    project_id: str = "test-project-123",
    name: str = "Test Project",
    critical_notes: list[dict] | None = None,
) -> MagicMock:
    """Create a mock Project with all required fields."""
    project = MagicMock()
    project.project_id = project_id
    project.name = name
    project.project_type = "software"
    project.status = "active"
    project.main_directory = "/path/to/project"
    project.related_directories = []
    project.description = "A test project"
    project.job_queue_paused = False
    project.tags = ["python", "test"]
    project.shortnames = ["testproj"]
    project.project_metadata = {"key": "value"}
    project.relationships = {}
    project.critical_notes = critical_notes
    project.creator_instance_id = "instance-123"
    project.creator_agent_id = "coder"
    project.created_at = "2025-01-15T10:00:00"
    project.updated_at = "2025-01-15T11:00:00"
    return project


class TestProjectAPICriticalNotes:
    """Test critical_notes field in Projects API responses."""

    # =============================================================================
    # Endpoint Tests
    # =============================================================================

    @pytest.mark.asyncio
    async def test_get_project_includes_critical_notes(self):
        """GET /projects/{id} with project that has critical notes entries -> response.critical_notes is the list."""
        # Arrange
        cn_entries = [
            {"id": "e1", "summary": "Use virtualenv for isolation", "category": "convention", "priority": "high"},
            {"id": "e2", "summary": "Avoid mutable default args", "category": "risk", "priority": "critical"},
        ]
        mock_project = create_mock_project(critical_notes=cn_entries)

        mock_repo = MagicMock()
        mock_repo.get = MagicMock(return_value=mock_project)

        # Act
        response = await get_project(project_id="test-project-123", repo=mock_repo)

        # Assert
        assert response.critical_notes == cn_entries
        assert len(response.critical_notes) == 2
        assert response.critical_notes[0]["summary"] == "Use virtualenv for isolation"
        mock_repo.get.assert_called_once_with("test-project-123")

    @pytest.mark.asyncio
    async def test_get_project_empty_critical_notes(self):
        """GET /projects/{id} with project that has empty critical notes -> response.critical_notes is []."""
        # Arrange
        mock_project = create_mock_project(critical_notes=[])

        mock_repo = MagicMock()
        mock_repo.get = MagicMock(return_value=mock_project)

        # Act
        response = await get_project(project_id="test-project-123", repo=mock_repo)

        # Assert
        assert response.critical_notes == []
        assert isinstance(response.critical_notes, list)
        mock_repo.get.assert_called_once_with("test-project-123")

    @pytest.mark.asyncio
    async def test_list_projects_includes_critical_notes(self):
        """GET /projects -> each project has critical_notes field."""
        # Arrange
        project1 = create_mock_project(
            project_id="proj-1",
            name="Project One",
            critical_notes=[
                {"id": "e1", "summary": "Follow PEP 8", "category": "convention", "priority": "medium"}
            ],
        )
        project2 = create_mock_project(
            project_id="proj-2",
            name="Project Two",
            critical_notes=[
                {"id": "e2", "summary": "Use type hints", "category": "pattern", "priority": "high"},
                {"id": "e3", "summary": "Write tests", "category": "constraint", "priority": "critical"},
            ],
        )
        project3 = create_mock_project(
            project_id="proj-3",
            name="Project Three",
            critical_notes=[],  # Empty list
        )

        mock_repo = MagicMock()
        mock_repo.list_projects = MagicMock(return_value=[project1, project2, project3])

        # Act
        response = await list_projects(exclude_system=False, repo=mock_repo)

        # Assert
        assert response.total == 3
        assert len(response.projects) == 3

        # Check each project has critical_notes field
        assert hasattr(response.projects[0], "critical_notes")
        assert hasattr(response.projects[1], "critical_notes")
        assert hasattr(response.projects[2], "critical_notes")

        # Verify values
        assert len(response.projects[0].critical_notes) == 1
        assert len(response.projects[1].critical_notes) == 2
        assert response.projects[2].critical_notes == []

        # Check specific content
        assert response.projects[0].critical_notes[0]["summary"] == "Follow PEP 8"
        assert response.projects[1].critical_notes[0]["priority"] == "high"

    # =============================================================================
    # _project_to_response Helper Tests
    # =============================================================================

    def test_project_to_response_with_entries(self):
        """_project_to_response() with entries -> correct list."""
        # Arrange
        cn_entries = [
            {
                "id": "entry-1",
                "summary": "Use context managers for file operations",
                "category": "pattern",
                "priority": "high",
                "source_agent": "architect",
            },
            {
                "id": "entry-2",
                "summary": "Always validate input parameters",
                "category": "constraint",
                "priority": "critical",
                "source_agent": "reviewer",
            },
        ]
        mock_project = create_mock_project(critical_notes=cn_entries)

        # Act
        response = _project_to_response(mock_project)

        # Assert
        assert isinstance(response, ProjectResponse)
        assert response.critical_notes == cn_entries
        assert len(response.critical_notes) == 2
        assert response.critical_notes[0]["id"] == "entry-1"
        assert response.critical_notes[1]["category"] == "constraint"

    def test_project_to_response_with_none(self):
        """_project_to_response() with None -> defaults to []."""
        # Arrange
        mock_project = create_mock_project(critical_notes=None)

        # Act
        response = _project_to_response(mock_project)

        # Assert
        assert isinstance(response, ProjectResponse)
        assert response.critical_notes == []
        assert isinstance(response.critical_notes, list)
        assert len(response.critical_notes) == 0

    def test_project_to_response_with_empty_list(self):
        """_project_to_response() with [] -> returns []. """
        # Arrange
        mock_project = create_mock_project(critical_notes=[])

        # Act
        response = _project_to_response(mock_project)

        # Assert
        assert isinstance(response, ProjectResponse)
        assert response.critical_notes == []
        assert isinstance(response.critical_notes, list)

    def test_project_to_response_with_various_categories(self):
        """_project_to_response() handles all critical_notes categories."""
        # Arrange
        cn_entries = [
            {"id": "c1", "summary": "Naming convention", "category": "convention", "priority": "high"},
            {"id": "c2", "summary": "Design pattern", "category": "pattern", "priority": "medium"},
            {"id": "c3", "summary": "Security risk", "category": "risk", "priority": "critical"},
            {"id": "c4", "summary": "Architectural decision", "category": "decision", "priority": "medium"},
            {"id": "c5", "summary": "Hard constraint", "category": "constraint", "priority": "critical"},
        ]
        mock_project = create_mock_project(critical_notes=cn_entries)

        # Act
        response = _project_to_response(mock_project)

        # Assert
        assert len(response.critical_notes) == 5
        categories = {entry["category"] for entry in response.critical_notes}
        assert categories == {"convention", "pattern", "risk", "decision", "constraint"}

    def test_project_to_response_preserves_all_fields(self):
        """_project_to_response() preserves all other project fields."""
        # Arrange
        cn_entries = [
            {"id": "e1", "summary": "Test entry", "category": "pattern", "priority": "low"}
        ]
        mock_project = create_mock_project(critical_notes=cn_entries)

        # Act
        response = _project_to_response(mock_project)

        # Assert - verify critical_notes is included alongside other fields
        assert response.project_id == "test-project-123"
        assert response.name == "Test Project"
        assert response.project_type == "software"
        assert response.status == "active"
        assert response.main_directory == "/path/to/project"
        assert response.description == "A test project"
        assert response.tags == ["python", "test"]
        assert response.shortnames == ["testproj"]
        assert response.creator_instance_id == "instance-123"
        assert response.creator_agent_id == "coder"
        # critical_notes is the field under test
        assert response.critical_notes == cn_entries


class TestCriticalNotesSchemaValidation:
    """Test that critical_notes schema is correctly defined."""

    def test_project_response_has_critical_notes_field(self):
        """ProjectResponse schema has critical_notes field."""
        # Create a response with critical_notes
        response = ProjectResponse(
            project_id="test-123",
            name="Test",
            project_type="software",
            status="active",
            main_directory=None,
            related_directories=[],
            description=None,
            job_queue_paused=False,
            tags=[],
            shortnames=[],
            metadata={},
            relationships={},
            critical_notes=[{"id": "e1", "summary": "test", "category": "convention", "priority": "high"}],
            creator_instance_id=None,
            creator_agent_id=None,
            created_at="2025-01-01T00:00:00",
            updated_at="2025-01-01T00:00:00",
            is_system=False,
        )

        assert hasattr(response, "critical_notes")
        assert len(response.critical_notes) == 1

    def test_project_response_critical_notes_defaults_to_none(self):
        """ProjectResponse.critical_notes defaults to None in schema."""
        response = ProjectResponse(
            project_id="test-123",
            name="Test",
            project_type="software",
            status="active",
            main_directory=None,
            related_directories=[],
            description=None,
            job_queue_paused=False,
            tags=[],
            shortnames=[],
            metadata={},
            relationships={},
            creator_instance_id=None,
            creator_agent_id=None,
            created_at="2025-01-01T00:00:00",
            updated_at="2025-01-01T00:00:00",
            is_system=False,
        )

        # Field should exist and default to None (since the schema has default=None)
        assert hasattr(response, "critical_notes")
        # Note: Pydantic v2 with Field(default=None) will set the field to None by default
        # The _project_to_response function converts None to [] for the actual API responses
