"""Tests for system default project bootstrap feature."""

import uuid
import pytest

from sqlmodel import SQLModel, create_engine

from daemon.repositories import SQLModelProjectRepository as ProjectStore
from daemon.constants import SYSTEM_DEFAULT_PROJECT_NAME, SYSTEM_DEFAULT_PROJECT_ID


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


class TestSystemProjectBootstrap:
    """Tests for ensure_system_default_project() method."""

    def test_first_call_creates_project(self, store):
        """Test that first call creates the system default project with correct fields."""
        project_id = store.ensure_system_default_project()

        assert project_id is not None

        project = store.get_by_name(SYSTEM_DEFAULT_PROJECT_NAME)
        assert project is not None
        assert project.name == SYSTEM_DEFAULT_PROJECT_NAME
        assert project.project_type == "system"
        assert project.status == "active"
        assert project.description == "System default project for jobs without an explicit project"
        assert project.project_metadata == {"is_system": True}

    def test_second_call_returns_same_project_id(self, store):
        """Test that calling ensure_system_default_project() twice returns the same ID."""
        first_id = store.ensure_system_default_project()
        second_id = store.ensure_system_default_project()

        assert first_id == second_id

        # Only one project with this name exists
        all_with_name = store.list_projects()
        matching = [p for p in all_with_name if p.name == SYSTEM_DEFAULT_PROJECT_NAME]
        assert len(matching) == 1

        # get_by_name returns exactly one project
        project = store.get_by_name(SYSTEM_DEFAULT_PROJECT_NAME)
        assert project is not None
        assert project.project_id == first_id

    def test_deterministic_uuid(self, store):
        """Test that the project ID is deterministic based on the project name."""
        expected_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, SYSTEM_DEFAULT_PROJECT_NAME))

        returned_id = store.ensure_system_default_project()

        assert returned_id == expected_id

    def test_system_default_project_id_starts_as_none(self):
        """Test that SYSTEM_DEFAULT_PROJECT_ID constant starts as None."""
        assert SYSTEM_DEFAULT_PROJECT_ID is None
