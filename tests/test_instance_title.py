"""Tests for instance title persistence functions in daemon/repositories/instance."""

import pytest
import sys
import json
from pathlib import Path

from sqlmodel import SQLModel, create_engine

from daemon.repositories.instance import SQLModelInstanceRepository


@pytest.fixture
def engine(tmp_path):
    """Create an in-memory SQLite engine for testing."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def repo(engine):
    """Create SQLModelInstanceRepository for testing."""
    return SQLModelInstanceRepository(engine)


class TestUpdateInstanceTitle:
    """Tests for update_title method on SQLModelInstanceRepository."""

    def test_update_title_for_existing_instance(self, repo):
        """Test updating title for existing instance."""
        # Create instance first
        instance = repo.create(
            instance_id="test-instance",
            agent_id="test",
            agent_dir="agents/test"
        )
        
        # Update title
        updated = repo.update_title("test-instance", "My Test Instance")
        
        # Verify title was updated
        assert updated is not None
        assert updated.instance_metadata.get("title") == "My Test Instance"
        
        # Verify via get
        fetched = repo.get("test-instance")
        assert fetched is not None
        assert fetched.instance_metadata.get("title") == "My Test Instance"

    def test_update_title_with_no_prior_metadata(self, repo):
        """Test updating title when instance has no prior metadata."""
        # Create instance with no additional metadata
        instance = repo.create(
            instance_id="test-instance",
            agent_id="test",
            agent_dir="agents/test"
        )
        
        # Update title
        updated = repo.update_title("test-instance", "New Instance Title")
        
        # Verify title was set
        assert updated is not None
        assert updated.instance_metadata.get("title") == "New Instance Title"

    def test_update_title_overwrites_existing(self, repo):
        """Test that updating title overwrites existing title."""
        # Create instance with initial title
        repo.create(
            instance_id="test-instance",
            agent_id="test",
            agent_dir="agents/test"
        )
        repo.update_title("test-instance", "Initial Title")
        
        # Verify initial title
        instance = repo.get("test-instance")
        assert instance is not None
        assert instance.instance_metadata.get("title") == "Initial Title"
        
        # Update to new title
        repo.update_title("test-instance", "Updated Title")
        
        # Verify title was overwritten
        instance = repo.get("test-instance")
        assert instance is not None
        assert instance.instance_metadata.get("title") == "Updated Title"

    def test_update_title_for_nonexistent_instance(self, repo):
        """Test updating title for non-existent instance returns None."""
        # Update title for non-existent instance - should return None
        result = repo.update_title("nonexistent-instance", "Some Title")
        
        # Should return None
        assert result is None
        
        # List should be empty
        instances, total = repo.list()
        assert total == 0


class TestGetInstanceWithTitle:
    """Tests for get returning title correctly in instance_metadata."""

    def test_get_instance_returns_title(self, repo):
        """Test that get returns title in instance_metadata."""
        # Create instance with title
        repo.create(
            instance_id="test-instance",
            agent_id="test",
            agent_dir="agents/test"
        )
        repo.update_title("test-instance", "Test Title")
        
        # Get instance
        instance = repo.get("test-instance")
        
        assert instance is not None
        assert "title" in instance.instance_metadata
        assert instance.instance_metadata["title"] == "Test Title"

    def test_get_instance_title_none_when_not_set(self, repo):
        """Test that title is None when not set."""
        # Create instance without title
        repo.create(
            instance_id="test-instance",
            agent_id="test",
            agent_dir="agents/test"
        )
        
        # Get instance
        instance = repo.get("test-instance")
        
        assert instance is not None
        # Title key may not exist if never set, or may be None
        # Either is acceptable for "not set"
        title = instance.instance_metadata.get("title")
        assert title is None


class TestListAllInstancesWithTitle:
    """Tests for list including title in response."""

    def test_list_all_instances_includes_title(self, repo):
        """Test that list includes title in instance_metadata."""
        # Create instances with titles
        repo.create(
            instance_id="instance-1",
            agent_id="test1",
            agent_dir="agents/test1"
        )
        repo.update_title("instance-1", "First Instance")
        
        repo.create(
            instance_id="instance-2",
            agent_id="test2",
            agent_dir="agents/test2"
        )
        # instance-2 has no title
        
        # List all instances
        instances, total = repo.list()
        
        assert total == 2
        
        # Find instance-1 and verify title
        s1 = next(s for s in instances if s.instance_id == "instance-1")
        assert s1.instance_metadata.get("title") == "First Instance"
        
        # Find instance-2 and verify no title
        s2 = next(s for s in instances if s.instance_id == "instance-2")
        assert s2.instance_metadata.get("title") is None

    def test_list_all_instances_title_in_metadata_json(self, repo):
        """Test that title is also available in metadata JSON."""
        # Create instance with title
        repo.create(
            instance_id="test-instance",
            agent_id="test",
            agent_dir="agents/test"
        )
        repo.update_title("test-instance", "Metadata Test")
        
        # List instances
        instances, total = repo.list()
        
        assert total == 1
        instance = instances[0]
        
        # Title should be in metadata dict
        assert instance.instance_metadata.get("title") == "Metadata Test"
