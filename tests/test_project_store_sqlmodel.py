"""Tests for daemon/repositories/project - SQLModelProjectRepository class."""

import pytest

from sqlmodel import Session, SQLModel, create_engine

from daemon.repositories import SQLModelProjectRepository as ProjectStore, ProjectStatus, ProjectType, Project


@pytest.fixture
def engine():
    """Create in-memory SQLite engine for testing."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def session(engine):
    """Create SQLModel Session for testing."""
    with Session(engine) as session:
        yield session


@pytest.fixture
def store(engine):
    """Create ProjectStore instance with SQLModel Engine."""
    return ProjectStore(engine)


class TestCreate:
    """Tests for create() method."""

    def test_create_basic_project(self, store):
        """Test creating a basic project."""
        project = store.create(name="Test Project")
        
        assert project is not None
        assert project.name == "Test Project"
        assert project.project_type == "general"
        assert project.status == "active"
        assert project.project_id is not None

    def test_create_with_all_fields(self, store):
        """Test creating a project with all fields."""
        project = store.create(
            name="Full Project",
            project_type="software",
            main_directory="/path/to/project",
            related_directories=["/path/to/docs", "/path/to/tests"],
            description="A test project",
            tags=["python", "fastapi"],
            metadata={"framework": "FastAPI"},
            creator_instance_id="instance-123",
            creator_agent_id="coder",
        )
        
        assert project.name == "Full Project"
        ...
        assert project.creator_agent_id == "coder"

    def test_create_duplicate_name_error(self, store):
        """Test that duplicate name raises error."""
        store.create(name="Duplicate Project")
        
        with pytest.raises(ValueError, match="already exists"):
            store.create(name="Duplicate Project")

    def test_create_invalid_type_error(self, store):
        """Test that invalid type raises error."""
        with pytest.raises(ValueError, match="Invalid project_type"):
            store.create(name="Test", project_type="")

    def test_create_invalid_type_error(self, store):
        """Test that invalid type raises error."""
        with pytest.raises(ValueError, match="Invalid project_type"):
            store.create(name="Test", project_type="invalid-type")

    def test_create_with_custom_id(self, store):
        """Test creating with custom project ID."""
        project = store.create(name="Custom ID", project_id="custom-id-123")
        
        assert project.project_id == "custom-id-123"


class TestGet:
    """Tests for get() and get_by_name() methods."""

    def test_get_by_id(self, store):
        """Test getting project by ID."""
        created = store.create(name="Get Test")
        retrieved = store.get(created.project_id)
        
        assert retrieved is not None
        assert retrieved.project_id == created.project_id
        assert retrieved.name == "Get Test"

    def test_get_not_found(self, store):
        """Test getting non-existent project returns None."""
        result = store.get("non-existent-id")
        
        assert result is None

    def test_get_by_name(self, store):
        """Test getting project by name."""
        store.create(name="By Name Test")
        retrieved = store.get_by_name("By Name Test")
        
        assert retrieved is not None
        assert retrieved.name == "By Name Test"

    def test_get_by_name_not_found(self, store):
        """Test getting by non-existent name returns None."""
        result = store.get_by_name("Non Existent")
        
        assert result is None


class TestGetByInstance:
    """Tests for get_by_instance() method."""

    def test_get_by_instance_creator(self, store):
        """Test getting projects created by an instance."""
        store.create(name="Project 1", creator_instance_id="instance-1")
        store.create(name="Project 2", creator_instance_id="instance-1")
        store.create(name="Project 3", creator_instance_id="instance-2")
        
        results = store.get_by_instance("instance-1")
        
        assert len(results) == 2

    def test_get_by_instance_relationship(self, store):
        """Test getting projects linked via relationships."""
        project = store.create(name="Related Project")
        store.add_relationship(project.project_id, "instances", "instance-xyz")
        
        results = store.get_by_instance("instance-xyz")
        
        assert len(results) == 1
        assert results[0].name == "Related Project"

    def test_get_by_instance_empty(self, store):
        """Test getting projects for instance with none."""
        results = store.get_by_instance("no-projects-instance")
        
        assert results == []


class TestGetByDirectory:
    """Tests for get_by_directory() method."""

    def test_get_by_main_directory(self, store):
        """Test getting projects by main directory."""
        store.create(name="Main Dir Project", main_directory="/home/user/project")
        
        results = store.get_by_directory("/home/user/project")
        
        assert len(results) == 1
        assert results[0].name == "Main Dir Project"

    def test_get_by_related_directory(self, store):
        """Test getting projects by related directory."""
        project = store.create(name="Related Dir Project")
        store.add_related_directory(project.project_id, "/home/user/docs")
        
        results = store.get_by_directory("/home/user/docs")
        
        assert len(results) == 1

    def test_get_by_directory_not_found(self, store):
        """Test getting projects for non-existent directory."""
        results = store.get_by_directory("/nonexistent/dir")
        
        assert results == []


class TestListProjects:
    """Tests for list_projects() method."""

    def test_list_all(self, store):
        """Test listing all projects."""
        store.create(name="Project 1")
        store.create(name="Project 2")
        store.create(name="Project 3")
        
        results = store.list_projects()
        
        assert len(results) == 3

    def test_list_empty(self, store):
        """Test listing with no projects."""
        results = store.list_projects()
        assert results == []
    
    def test_list_by_status(self, store):
        """Test filtering by status."""
        p1 = store.create(name="Active Project")
        p2 = store.create(name="Paused Project")
        store.update_status(p2.project_id, "paused")
        
        results = store.list_projects(status="paused")
        
        assert len(results) == 1
        assert results[0].name == "Paused Project"
    
    def test_list_by_type(self, store):
        """Test filtering by type."""
        store.create(name="Software Project", project_type="software")
        store.create(name="Doc Project", project_type="documentation")
        
        results = store.list_projects(project_type="software")
        
        assert len(results) == 1
        assert results[0].name == "Software Project"
    
    def test_list_by_tags(self, store):
        """Test filtering by tags (AND logic)."""
        store.create(name="Project A", tags=["python", "web"])
        store.create(name="Project B", tags=["python"])
        store.create(name="Project C", tags=["python", "web", "api"])
        
        # Must have ALL tags
        results = store.list_projects(tags=["python", "web"])
        
        assert len(results) == 2
    
    def test_list_pagination(self, store):
        """Test pagination with limit and offset."""
        for i in range(5):
            store.create(name=f"Project {i}")
        
        results = store.list_projects(limit=3)
        
        assert len(results) == 3


class TestSearch:
    """Tests for search() method."""

    def test_search_by_name(self, store):
        """Test searching by name."""
        store.create(name="Python Web App")
        store.create(name="Java Desktop App")
        
        results = store.search("Python")
        
        assert len(results) == 1
        assert results[0].name == "Python Web App"

    def test_search_by_description(self, store):
        """Test searching by description."""
        store.create(name="Project A", description="A FastAPI application")
        store.create(name="Project B", description="A React frontend")
        
        results = store.search("FastAPI")
        
        assert len(results) == 1

    def test_search_by_shortname(self, store):
        """Test searching by shortname."""
        store.create(name="Project A", shortnames=["proj-a", "alpha"])
        store.create(name="Project B", shortnames=["proj-b", "beta"])
        
        results = store.search("proj-a")
        
        assert len(results) == 1
        assert results[0].name == "Project A"

    def test_search_empty(self, store):
        """Test search with no matches."""
        store.create(name="Test")
        
        results = store.search("nomatch")
        
        assert results == []

    def test_search_limit(self, store):
        """Test search respects limit."""
        for i in range(25):
            store.create(name=f"Test Project {i}", description="test search")
        
        results = store.search("test", limit=5)
        
        assert len(results) == 5


class TestMatchByKeywords:
    """Tests for match_by_keywords() method."""

    def test_match_by_keywords_exact_name(self, store):
        """Test exact name match."""
        store.create(name="My Project", shortnames=["mp"])
        
        result = store.match_by_keywords(["My Project"])
        
        assert result is not None
        assert result.name == "My Project"

    def test_match_by_keywords_exact_shortname(self, store):
        """Test exact shortname match."""
        store.create(name="My Project", shortnames=["mp"])
        
        result = store.match_by_keywords(["mp"])
        
        assert result is not None
        assert result.name == "My Project"

    def test_match_by_keywords_partial(self, store):
        """Test partial match."""
        store.create(name="My Project")
        
        result = store.match_by_keywords(["my"])
        
        assert result is not None
        assert result.name == "My Project"

    def test_match_by_keywords_no_match(self, store):
        """Test no match returns None."""
        store.create(name="My Project")
        
        result = store.match_by_keywords(["nonexistent"])
        
        assert result is None


class TestUpdate:
    """Tests for update() method."""

    def test_update_name(self, store):
        """Test updating project name."""
        project = store.create(name="Old Name")
        
        updated = store.update(project.project_id, name="New Name")
        
        assert updated.name == "New Name"

    def test_update_duplicate_name_error(self, store):
        """Test that updating to duplicate name raises error."""
        store.create(name="Project A")
        project_b = store.create(name="Project B")
        
        with pytest.raises(ValueError, match="already exists"):
            store.update(project_b.project_id, name="Project A")

    def test_update_description(self, store):
        """Test updating description."""
        project = store.create(name="Test", description="Original")
        
        updated = store.update(project.project_id, description="Updated")
        
        assert updated.description == "Updated"

    def test_update_related_directories(self, store):
        """Test updating related directories list.

        Uses the atomic API (add_related_directory) — plain ``update()`` rejects
        ``related_directories`` to prevent JSON-clobber races (see H11).
        """
        project = store.create(name="Test")

        store.add_related_directory(project.project_id, "/dir1")
        updated = store.add_related_directory(project.project_id, "/dir2")

        assert updated.related_directories == ["/dir1", "/dir2"]

    def test_update_invalid_status_error(self, store):
        """Test that invalid status raises error."""
        project = store.create(name="Test")
        
        with pytest.raises(ValueError, match="Invalid status"):
            store.update(project.project_id, status="invalid_status")

    def test_update_not_found(self, store):
        """Test updating non-existent project returns None."""
        result = store.update("non-existent-id", name="Test")
        
        assert result is None


class TestUpdateStatus:
    """Tests for update_status() method."""

    def test_update_status_valid(self, store):
        """Test updating to valid status."""
        project = store.create(name="Test")
        
        updated = store.update_status(project.project_id, "paused")
        
        assert updated.status == "paused"

    def test_update_status_all_statuses(self, store):
        """Test all valid statuses."""
        project = store.create(name="Test")
        
        for status in ["active", "paused", "completed", "archived"]:
            updated = store.update_status(project.project_id, status)
            assert updated.status == status


class TestSetTags:
    """Tests for set_tags() method - atomic replacement."""

    def test_set_tags_replace_all(self, store):
        """Test that set_tags replaces all tags."""
        project = store.create(name="Test", tags=["old1", "old2"])
        
        updated = store.set_tags(project.project_id, ["new1", "new2"])
        
        assert set(updated.tags) == {"new1", "new2"}

    def test_set_tags_empty(self, store):
        """Test setting empty tags."""
        project = store.create(name="Test", tags=["existing"])
        
        updated = store.set_tags(project.project_id, [])
        
        assert updated.tags == []

    def test_set_tags_not_found(self, store):
        """Test setting tags on non-existent project returns None."""
        result = store.set_tags("non-existent-id", ["tag"])
        
        assert result is None


class TestAddTag:
    """Tests for add_tag() method."""

    def test_add_tag(self, store):
        """Test adding a tag."""
        project = store.create(name="Test", tags=["existing"])
        
        updated = store.add_tag(project.project_id, "new")
        
        assert "existing" in updated.tags
        assert "new" in updated.tags

    def test_add_tag_duplicate_noop(self, store):
        """Test adding duplicate tag is no-op."""
        project = store.create(name="Test", tags=["existing"])
        
        updated = store.add_tag(project.project_id, "existing")
        
        # Should only have one
        assert updated.tags.count("existing") == 1


class TestRemoveTag:
    """Tests for remove_tag() method."""

    def test_remove_tag(self, store):
        """Test removing a tag."""
        project = store.create(name="Test", tags=["keep", "remove"])
        
        updated = store.remove_tag(project.project_id, "remove")
        
        assert "remove" not in updated.tags
        assert "keep" in updated.tags

    def test_remove_tag_not_found_noop(self, store):
        """Test removing non-existent tag is no-op."""
        project = store.create(name="Test", tags=["existing"])
        
        updated = store.remove_tag(project.project_id, "nonexistent")
        
        assert updated.tags == ["existing"]


class TestShortnames:
    """Tests for shortname methods."""

    def test_set_shortnames(self, store):
        """Test setting shortnames."""
        project = store.create(name="Test", shortnames=["old"])
        
        updated = store.set_shortnames(project.project_id, ["new1", "new2"])
        
        assert set(updated.shortnames) == {"new1", "new2"}

    def test_add_shortname(self, store):
        """Test adding shortname."""
        project = store.create(name="Test", shortnames=["existing"])
        
        updated = store.add_shortname(project.project_id, "new")
        
        assert "existing" in updated.shortnames
        assert "new" in updated.shortnames

    def test_remove_shortname(self, store):
        """Test removing shortname."""
        project = store.create(name="Test", shortnames=["keep", "remove"])
        
        updated = store.remove_shortname(project.project_id, "remove")
        
        assert "remove" not in updated.shortnames
        assert "keep" in updated.shortnames


class TestRelatedDirectories:
    """Tests for add_related_directory() and remove_related_directory()."""

    def test_add_related_directory(self, store):
        """Test adding related directory."""
        project = store.create(name="Test")
        
        updated = store.add_related_directory(project.project_id, "/new/dir")
        
        assert "/new/dir" in updated.related_directories

    def test_remove_related_directory(self, store):
        """Test removing related directory."""
        project = store.create(name="Test", related_directories=["/keep", "/remove"])
        
        updated = store.remove_related_directory(project.project_id, "/remove")
        
        assert "/remove" not in updated.related_directories
        assert "/keep" in updated.related_directories


class TestMetadata:
    """Tests for set_metadata() and delete_metadata() methods."""

    def test_set_metadata(self, store):
        """Test setting metadata."""
        project = store.create(name="Test")
        
        updated = store.set_metadata(project.project_id, "priority", "high")
        
        assert updated.project_metadata["priority"] == "high"

    def test_set_metadata_update_existing(self, store):
        """Test updating existing metadata key."""
        project = store.create(name="Test", metadata={"key": "old"})
        
        updated = store.set_metadata(project.project_id, "key", "new")
        
        assert updated.project_metadata["key"] == "new"

    def test_delete_metadata(self, store):
        """Test deleting metadata."""
        project = store.create(name="Test", metadata={"keep": "value", "delete": "value"})
        
        updated = store.delete_metadata(project.project_id, "delete")
        
        assert "delete" not in updated.project_metadata
        assert "keep" in updated.project_metadata


class TestRelationships:
    """Tests for add_relationship() and remove_relationship() methods."""

    def test_add_relationship(self, store):
        """Test adding a relationship."""
        project = store.create(name="Test")
        
        updated = store.add_relationship(project.project_id, "instances", "instance-123")
        
        assert "instance-123" in updated.relationships["instances"]

    def test_remove_relationship(self, store):
        """Test removing a relationship."""
        project = store.create(name="Test")
        store.add_relationship(project.project_id, "instances", "instance-123")
        
        updated = store.remove_relationship(project.project_id, "instances", "instance-123")
        
        assert "instance-123" not in updated.relationships.get("instances", [])


class TestDelete:
    """Tests for delete() method."""

    def test_delete_project(self, store):
        """Test deleting a project."""
        project = store.create(name="To Delete")
        
        result = store.delete(project.project_id)
        
        assert result["deleted"] is True
        assert result["name"] == "To Delete"
        assert "counts" in result
        assert "project" in result["counts"]
        
        # Verify it's gone
        assert store.get(project.project_id) is None

    def test_delete_not_found(self, store):
        """Test deleting non-existent project."""
        with pytest.raises(ValueError, match="not found"):
            store.delete("non-existent-id")


class TestToDict:
    """Tests for to_dict() method on Project model."""

    def test_to_dict(self, store):
        """Test converting project to dictionary."""
        project = store.create(
            name="Dict Test",
            project_type="software",
            tags=["test"],
            metadata={"key": "value"},
        )
        
        result = project.to_dict()
        
        assert isinstance(result, dict)
        assert result["name"] == "Dict Test"
        assert result["project_type"] == "software"
        assert result["tags"] == project.tags
        assert result["metadata"] == {"key": "value"}

    def test_to_dict_includes_all_fields(self, store):
        """Test to_dict includes all expected fields."""
        project = store.create(
            name="Full Dict Test",
            project_type="research",
            main_directory="/home/test",
            related_directories=["/docs"],
            description="Test description",
            tags=["ai"],
            shortnames=["fdt"],
            metadata={"version": "1.0"},
            creator_instance_id="instance-1",
            creator_agent_id="agent-1",
        )
        # Add relationships via update
        project = store.add_relationship(project.project_id, "instances", "i1")
        
        result = project.to_dict()
        
        expected_keys = [
            "project_id", "name", "project_type", "status", "main_directory",
            "related_directories", "description", "tags", "shortnames",
            "metadata", "relationships", "creator_instance_id", "creator_agent_id",
            "created_at", "updated_at"
        ]
        for key in expected_keys:
            assert key in result, f"Missing key: {key}"


class TestProjectEnums:
    """Tests for ProjectStatus and ProjectType enums."""

    def test_project_status_is_valid(self):
        """Test ProjectStatus.is_valid()."""
        assert ProjectStatus.is_valid("active") is True
        assert ProjectStatus.is_valid("paused") is True
        assert ProjectStatus.is_valid("completed") is True
        assert ProjectStatus.is_valid("archived") is True
        assert ProjectStatus.is_valid("invalid") is False

    def test_project_type_is_valid(self):
        """Test ProjectType.is_valid()."""
        # Test valid enum values
        assert ProjectType.is_valid("software") is True
        assert ProjectType.is_valid("documentation") is True
        assert ProjectType.is_valid("research") is True
        assert ProjectType.is_valid("task") is True
        assert ProjectType.is_valid("general") is True
        # Test invalid values
        assert ProjectType.is_valid("") is False
        assert ProjectType.is_valid("   ") is False
        assert ProjectType.is_valid("invalid") is False


class TestGetByShortname:
    """Tests for get_by_shortname() method."""

    def test_get_by_shortname(self, store):
        """Test getting project by shortname."""
        store.create(name="Test Project", shortnames=["tp", "testproj"])
        
        result = store.get_by_shortname("tp")
        
        assert result is not None
        assert result.name == "Test Project"

    def test_get_by_shortname_not_found(self, store):
        """Test getting by non-existent shortname."""
        result = store.get_by_shortname("nonexistent")
        
        assert result is None
