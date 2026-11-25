"""Tests for daemon/tools/project.py - 21 project management tools."""

import json
import pytest

from sqlmodel import Session, SQLModel, create_engine

from daemon.tools.project import create_project_tools
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
    """Create ProjectStore instance with SQLModel engine."""
    return ProjectStore(engine)


@pytest.fixture
def tools(store):
    """Create project tools with injected ProjectStore."""
    return create_project_tools(
        store, 
        current_instance_id="test-instance", 
        agent_id="test"
    )


@pytest.fixture
def tool_map(tools):
    """Map tool names to tool functions."""
    return {tool.name: tool for tool in tools}


class TestProjectCreate:
    """Tests for project_create tool."""

    def test_create_basic(self, tool_map):
        """Test creating a basic project."""
        result = tool_map["project_create"].invoke({
            "name": "Test Project",
        })
        
        assert "project_id" in result
        assert result["name"] == "Test Project"
        assert result["status"] == "active"

    def test_create_with_all_params(self, tool_map):
        """Test creating project with all parameters."""
        result = tool_map["project_create"].invoke({
            "name": "Full Project",
            "project_type": "software",
            "main_directory": "/path/to/project",
            "related_directories": ["/path/to/docs"],
            "description": "A test project",
            "tags": ["python", "fastapi"],
            "metadata": {"framework": "FastAPI"},
        })
        
        assert result["name"] == "Full Project"
        assert result["project_type"] == "software"
        assert result["main_directory"] == "/path/to/project"
        assert result["related_directories"] == ["/path/to/docs"]
        assert result["description"] == "A test project"
        # Tags order may vary
        assert set(result["tags"]) == {"python", "fastapi"}
        assert result["metadata"] == {"framework": "FastAPI"}

    def test_create_duplicate_name_error(self, tool_map):
        """Test duplicate name returns error dict."""
        tool_map["project_create"].invoke({"name": "Duplicate"})
        
        result = tool_map["project_create"].invoke({"name": "Duplicate"})
        
        assert "error" in result
        assert "already exists" in result["error"]

    def test_create_invalid_type_error(self, tool_map):
        """Test invalid project_type returns error."""
        result = tool_map["project_create"].invoke({
            "name": "Test",
            "project_type": "",
        })
        
        assert "error" in result
        assert "Invalid project_type" in result["error"]


class TestProjectGet:
    """Tests for project_get tool."""

    def test_get_by_id(self, tool_map):
        """Test getting project by ID."""
        created = tool_map["project_create"].invoke({"name": "Get Test"})
        
        result = tool_map["project_get"].invoke({
            "project_id": created["project_id"]
        })
        
        assert result["name"] == "Get Test"

    def test_get_by_name(self, tool_map):
        """Test getting project by name."""
        tool_map["project_create"].invoke({"name": "By Name"})
        
        result = tool_map["project_get"].invoke({
            "name": "By Name"
        })
        
        assert result["name"] == "By Name"

    def test_get_not_found(self, tool_map):
        """Test getting non-existent project returns error dict."""
        result = tool_map["project_get"].invoke({
            "project_id": "nonexistent-id"
        })
        
        assert "error" in result
        assert "not found" in result["error"].lower()

    def test_get_requires_id_or_name(self, tool_map):
        """Test that either project_id or name is required."""
        result = tool_map["project_get"].invoke({})
        
        assert "error" in result
        assert "Must provide either" in result["error"]


class TestProjectList:
    """Tests for project_list tool."""

    def test_list_all(self, tool_map):
        """Test listing all projects."""
        tool_map["project_create"].invoke({"name": "Project 1"})
        tool_map["project_create"].invoke({"name": "Project 2"})
        
        result = tool_map["project_list"].invoke({})
        
        assert len(result["projects"]) == 2

    def test_list_empty(self, tool_map):
        """Test listing with no projects."""
        result = tool_map["project_list"].invoke({})
        
        assert result["projects"] == []

    def test_list_by_status(self, tool_map):
        """Test filtering by status."""
        p1 = tool_map["project_create"].invoke({"name": "Active"})
        tool_map["project_create"].invoke({"name": "Paused"})
        tool_map["project_set_status"].invoke({
            "project_id": p1["project_id"],
            "status": "paused"
        })
        
        result = tool_map["project_list"].invoke({"status": "paused"})
        
        assert len(result["projects"]) == 1

    def test_list_by_type(self, tool_map):
        """Test filtering by project type."""
        tool_map["project_create"].invoke({"name": "Software", "project_type": "software"})
        tool_map["project_create"].invoke({"name": "Docs", "project_type": "documentation"})
        
        result = tool_map["project_list"].invoke({"project_type": "software"})
        
        assert len(result["projects"]) == 1
        assert result["projects"][0]["project_type"] == "software"

    def test_list_by_tags(self, tool_map):
        """Test filtering by tags."""
        tool_map["project_create"].invoke({"name": "Web App", "tags": ["python", "web"]})
        tool_map["project_create"].invoke({"name": "CLI App", "tags": ["python"]})
        
        result = tool_map["project_list"].invoke({"tags": ["python", "web"]})
        
        assert len(result["projects"]) == 1


class TestProjectSearch:
    """Tests for project_search tool."""

    def test_search_by_name(self, tool_map):
        """Test searching by name."""
        tool_map["project_create"].invoke({"name": "Python Web App"})
        
        result = tool_map["project_search"].invoke({"query": "Python"})
        
        assert len(result) == 1

    def test_search_by_description(self, tool_map):
        """Test searching by description."""
        tool_map["project_create"].invoke({"name": "App", "description": "A FastAPI application"})
        
        result = tool_map["project_search"].invoke({"query": "FastAPI"})
        
        assert len(result) == 1

    def test_search_no_results(self, tool_map):
        """Test search with no matches."""
        result = tool_map["project_search"].invoke({"query": "nonexistent"})
        
        assert result == []


class TestProjectGetByInstance:
    """Tests for project_get_by_instance tool."""

    def test_get_by_instance(self, tool_map):
        """Test getting projects by instance."""
        tool_map["project_create"].invoke({"name": "Instance Project"})
        
        result = tool_map["project_get_by_instance"].invoke({
            "instance_id": "test-instance"
        })
        
        assert len(result) == 1


class TestProjectGetByDirectory:
    """Tests for project_get_by_directory tool."""

    def test_get_by_main_directory(self, tool_map):
        """Test getting projects by main directory."""
        # Use a path that doesn't get resolved differently on macOS
        dir_path = "/Users/Shared/test_project_dir_12345"
        tool_map["project_create"].invoke({
            "name": "Main Dir Project",
            "main_directory": dir_path
        })
        
        result = tool_map["project_get_by_directory"].invoke({
            "directory": dir_path
        })
        
        assert len(result) == 1

    def test_get_by_directory_not_found(self, tool_map):
        """Test getting non-existent directory returns empty list."""
        result = tool_map["project_get_by_directory"].invoke({
            "directory": "/nonexistent"
        })
        
        assert result == []


class TestProjectUpdate:
    """Tests for project_update tool."""

    def test_update_name(self, tool_map):
        """Test updating project name."""
        project = tool_map["project_create"].invoke({"name": "Old Name"})
        
        result = tool_map["project_update"].invoke({
            "project_id": project["project_id"],
            "name": "New Name"
        })
        
        assert result["name"] == "New Name"

    def test_update_description(self, tool_map):
        """Test updating description."""
        project = tool_map["project_create"].invoke({"name": "Test"})
        
        result = tool_map["project_update"].invoke({
            "project_id": project["project_id"],
            "description": "New description"
        })
        
        assert result["description"] == "New description"

    def test_update_duplicate_name_error(self, tool_map):
        """Test duplicate name returns error."""
        tool_map["project_create"].invoke({"name": "Project A"})
        project_b = tool_map["project_create"].invoke({"name": "Project B"})
        
        result = tool_map["project_update"].invoke({
            "project_id": project_b["project_id"],
            "name": "Project A"
        })
        
        assert "error" in result
        assert "already exists" in result["error"]

    def test_update_not_found(self, tool_map):
        """Test updating non-existent project returns error."""
        result = tool_map["project_update"].invoke({
            "project_id": "nonexistent-id",
            "name": "Test"
        })
        
        assert "error" in result
        assert "not found" in result["error"].lower()


class TestProjectSetStatus:
    """Tests for project_set_status tool."""

    def test_set_status_valid(self, tool_map):
        """Test setting valid status."""
        project = tool_map["project_create"].invoke({"name": "Test"})
        
        result = tool_map["project_set_status"].invoke({
            "project_id": project["project_id"],
            "status": "paused"
        })
        
        assert result["status"] == "paused"

    def test_set_status_invalid_error(self, tool_map):
        """Test invalid status returns error."""
        project = tool_map["project_create"].invoke({"name": "Test"})
        
        result = tool_map["project_set_status"].invoke({
            "project_id": project["project_id"],
            "status": "invalid_status"
        })
        
        assert "error" in result
        assert "Invalid status" in result["error"]

    def test_set_status_not_found(self, tool_map):
        """Test setting status on non-existent project returns error."""
        result = tool_map["project_set_status"].invoke({
            "project_id": "nonexistent-id",
            "status": "active"
        })
        
        assert "error" in result
        assert "not found" in result["error"].lower()


class TestProjectAddDirectory:
    """Tests for project_add_directory tool."""

    def test_add_related_directory(self, tool_map):
        """Test adding related directory."""
        project = tool_map["project_create"].invoke({"name": "Test"})
        
        result = tool_map["project_add_directory"].invoke({
            "project_id": project["project_id"],
            "directory": "/new/dir"
        })
        
        assert "/new/dir" in result["related_directories"]

    def test_add_as_main_directory(self, tool_map):
        """Test adding as main directory."""
        project = tool_map["project_create"].invoke({"name": "Test"})
        
        result = tool_map["project_add_directory"].invoke({
            "project_id": project["project_id"],
            "directory": "/main/dir",
            "as_main": True
        })
        
        assert result["main_directory"] == "/main/dir"

    def test_add_directory_not_found(self, tool_map):
        """Test adding directory to non-existent project returns error."""
        result = tool_map["project_add_directory"].invoke({
            "project_id": "nonexistent-id",
            "directory": "/dir"
        })
        
        assert "error" in result
        assert "not found" in result["error"].lower()


class TestProjectRemoveDirectory:
    """Tests for project_remove_directory tool."""

    def test_remove_related_directory(self, tool_map):
        """Test removing related directory."""
        project = tool_map["project_create"].invoke({
            "name": "Test",
            "related_directories": ["/keep", "/remove"]
        })
        
        result = tool_map["project_remove_directory"].invoke({
            "project_id": project["project_id"],
            "directory": "/remove"
        })
        
        assert "/remove" not in result["related_directories"]

    def test_remove_directory_not_found(self, tool_map):
        """Test removing directory from non-existent project returns error."""
        result = tool_map["project_remove_directory"].invoke({
            "project_id": "nonexistent-id",
            "directory": "/dir"
        })
        
        assert "error" in result
        assert "not found" in result["error"].lower()


class TestProjectSetTags:
    """Tests for project_set_tags tool."""

    def test_set_tags_replace_all(self, tool_map):
        """Test that set_tags replaces all tags."""
        project = tool_map["project_create"].invoke({
            "name": "Test",
            "tags": ["old1", "old2"]
        })
        
        result = tool_map["project_set_tags"].invoke({
            "project_id": project["project_id"],
            "tags": ["new1", "new2"]
        })
        
        assert result["tags"] == ["new1", "new2"]

    def test_set_tags_not_found(self, tool_map):
        """Test setting tags on non-existent project returns error."""
        result = tool_map["project_set_tags"].invoke({
            "project_id": "nonexistent-id",
            "tags": ["tag"]
        })
        
        assert "error" in result
        assert "not found" in result["error"].lower()


class TestProjectAddTag:
    """Tests for project_add_tag tool."""

    def test_add_tag(self, tool_map):
        """Test adding a tag."""
        project = tool_map["project_create"].invoke({
            "name": "Test",
            "tags": ["existing"]
        })
        
        result = tool_map["project_add_tag"].invoke({
            "project_id": project["project_id"],
            "tag": "new"
        })
        
        assert "new" in result["tags"]

    def test_add_tag_not_found(self, tool_map):
        """Test adding tag to non-existent project returns error."""
        result = tool_map["project_add_tag"].invoke({
            "project_id": "nonexistent-id",
            "tag": "tag"
        })
        
        assert "error" in result
        assert "not found" in result["error"].lower()


class TestProjectRemoveTag:
    """Tests for project_remove_tag tool."""

    def test_remove_tag(self, tool_map):
        """Test removing a tag."""
        project = tool_map["project_create"].invoke({
            "name": "Test",
            "tags": ["keep", "remove"]
        })
        
        result = tool_map["project_remove_tag"].invoke({
            "project_id": project["project_id"],
            "tag": "remove"
        })
        
        assert "remove" not in result["tags"]

    def test_remove_tag_not_found(self, tool_map):
        """Test removing tag from non-existent project returns error."""
        result = tool_map["project_remove_tag"].invoke({
            "project_id": "nonexistent-id",
            "tag": "tag"
        })
        
        assert "error" in result
        assert "not found" in result["error"].lower()


class TestProjectSetMetadata:
    """Tests for project_set_metadata tool."""

    def test_set_metadata(self, tool_map):
        """Test setting metadata."""
        project = tool_map["project_create"].invoke({"name": "Test"})
        
        result = tool_map["project_set_metadata"].invoke({
            "project_id": project["project_id"],
            "key": "priority",
            "value": "high"
        })
        
        assert result["metadata"]["priority"] == "high"

    def test_set_metadata_complex_value(self, tool_map):
        """Test setting complex metadata value."""
        project = tool_map["project_create"].invoke({"name": "Test"})
        
        result = tool_map["project_set_metadata"].invoke({
            "project_id": project["project_id"],
            "key": "tech_stack",
            "value": ["Python", "FastAPI", "React"]
        })
        
        assert result["metadata"]["tech_stack"] == ["Python", "FastAPI", "React"]

    def test_set_metadata_not_found(self, tool_map):
        """Test setting metadata on non-existent project returns error."""
        result = tool_map["project_set_metadata"].invoke({
            "project_id": "nonexistent-id",
            "key": "key",
            "value": "value"
        })
        
        assert "error" in result
        assert "not found" in result["error"].lower()


class TestProjectDeleteMetadata:
    """Tests for project_delete_metadata tool."""

    def test_delete_metadata(self, tool_map):
        """Test deleting metadata."""
        project = tool_map["project_create"].invoke({
            "name": "Test",
            "metadata": {"keep": "value", "delete": "value"}
        })
        
        result = tool_map["project_delete_metadata"].invoke({
            "project_id": project["project_id"],
            "key": "delete"
        })
        
        assert "delete" not in result["metadata"]

    def test_delete_metadata_not_found(self, tool_map):
        """Test deleting metadata from non-existent project returns error."""
        result = tool_map["project_delete_metadata"].invoke({
            "project_id": "nonexistent-id",
            "key": "key"
        })
        
        assert "error" in result
        assert "not found" in result["error"].lower()


class TestProjectLink:
    """Tests for project_link tool."""

    def test_link_project(self, tool_map):
        """Test linking a project to an entity."""
        project = tool_map["project_create"].invoke({"name": "Test"})
        
        result = tool_map["project_link"].invoke({
            "project_id": project["project_id"],
            "entity_type": "instances",
            "entity_id": "instance-123"
        })
        
        assert "instance-123" in result["relationships"]["instances"]

    def test_link_not_found(self, tool_map):
        """Test linking non-existent project returns error."""
        result = tool_map["project_link"].invoke({
            "project_id": "nonexistent-id",
            "entity_type": "instances",
            "entity_id": "i1"
        })
        
        assert "error" in result
        assert "not found" in result["error"].lower()


class TestProjectUnlink:
    """Tests for project_unlink tool."""

    def test_unlink_project(self, tool_map):
        """Test unlinking a project from an entity."""
        project = tool_map["project_create"].invoke({"name": "Test"})
        tool_map["project_link"].invoke({
            "project_id": project["project_id"],
            "entity_type": "instances",
            "entity_id": "instance-123"
        })
        
        result = tool_map["project_unlink"].invoke({
            "project_id": project["project_id"],
            "entity_type": "instances",
            "entity_id": "instance-123"
        })
        
        assert "instance-123" not in result["relationships"].get("instances", [])

    def test_unlink_not_found(self, tool_map):
        """Test unlinking non-existent project returns error."""
        result = tool_map["project_unlink"].invoke({
            "project_id": "nonexistent-id",
            "entity_type": "instances",
            "entity_id": "i1"
        })
        
        assert "error" in result
        assert "not found" in result["error"].lower()


class TestProjectDelete:
    """Tests for project_delete tool."""

    def test_delete_project(self, tool_map):
        """Test deleting a project."""
        project = tool_map["project_create"].invoke({"name": "To Delete"})
        
        result = tool_map["project_delete"].invoke({
            "project_id": project["project_id"]
        })
        
        assert result["deleted"] is True
        assert result["name"] == "To Delete"
        assert "counts" in result

    def test_delete_not_found(self, tool_map):
        """Test deleting non-existent project."""
        with pytest.raises(Exception):
            tool_map["project_delete"].invoke({
                "project_id": "nonexistent-id"
            })


class TestToolCount:
    """Verify all 21 tools are created."""

    def test_all_21_tools_exist(self, tools):
        """Test that exactly 21 tools are created."""
        tool_names = [t.name for t in tools]
        
        expected_tools = [
            "project_create",
            "project_get",
            "project_list",
            "project_search",
            "project_get_by_instance",
            "project_get_by_directory",
            "project_update",
            "project_set_status",
            "project_add_directory",
            "project_remove_directory",
            "project_set_tags",
            "project_add_tag",
            "project_remove_tag",
            "project_set_shortnames",
            "project_add_shortname",
            "project_remove_shortname",
            "project_set_metadata",
            "project_delete_metadata",
            "project_link",
            "project_unlink",
            "project_delete",
        ]
        
        assert len(tools) == 21
        for name in expected_tools:
            assert name in tool_names


class TestReturnTypeConsistency:
    """Test that tools follow the dict | None pattern."""

    def test_get_returns_dict_or_none(self, tool_map):
        """Test project_get returns dict or error dict for non-existent."""
        # Create a project first
        created = tool_map["project_create"].invoke({"name": "Test"})
        
        # Should return dict
        result = tool_map["project_get"].invoke({"project_id": created["project_id"]})
        assert isinstance(result, dict)
        assert "error" not in result
        
        # Non-existent should return error dict
        result = tool_map["project_get"].invoke({"project_id": "nonexistent"})
        assert "error" in result

    def test_update_returns_dict_or_none(self, tool_map):
        """Test project_update returns dict or error dict for non-existent."""
        project = tool_map["project_create"].invoke({"name": "Test"})
        
        # Should return dict
        result = tool_map["project_update"].invoke({
            "project_id": project["project_id"],
            "name": "Updated"
        })
        assert isinstance(result, dict)
        assert "error" not in result
        
        # Non-existent should return error dict
        result = tool_map["project_update"].invoke({
            "project_id": "nonexistent",
            "name": "Test"
        })
        assert "error" in result

    def test_status_returns_dict_or_none(self, tool_map):
        """Test project_set_status returns dict or error dict for non-existent."""
        project = tool_map["project_create"].invoke({"name": "Test"})
        
        # Should return dict
        result = tool_map["project_set_status"].invoke({
            "project_id": project["project_id"],
            "status": "completed"
        })
        assert isinstance(result, dict)
        assert "error" not in result
        
        # Non-existent should return error dict
        result = tool_map["project_set_status"].invoke({
            "project_id": "nonexistent",
            "status": "active"
        })
        assert "error" in result


class TestErrorHandling:
    """Test error handling across tools."""

    def test_duplicate_name_on_create(self, tool_map):
        """Test duplicate name error on create."""
        tool_map["project_create"].invoke({"name": "Duplicate"})
        
        result = tool_map["project_create"].invoke({"name": "Duplicate"})
        
        assert "error" in result

    def test_invalid_status(self, tool_map):
        """Test invalid status error."""
        project = tool_map["project_create"].invoke({"name": "Test"})
        
        result = tool_map["project_set_status"].invoke({
            "project_id": project["project_id"],
            "status": "not_a_valid_status"
        })
        
        assert "error" in result

    def test_invalid_type_on_create(self, tool_map):
        """Test invalid type error on create."""
        result = tool_map["project_create"].invoke({
            "name": "Test",
            "project_type": ""
        })
        
        assert "error" in result


# ---------------------------------------------------------------------------
# project_get resolution ladder — tool-level directional pins
#
# COMMISSION: project-get-resolution
# LANE:       feature/projres-tool-pins (this module; tests only)
# BASE SHA:   8894f86a853c5fcba2173ab29405f02c71c1f3ef
#
# FROZEN CONTRACT (implementation-owned; read as spec — the implementation
# lands in the sibling module feature/projres-repo-core, so the ladder cases
# below are EXPECTED RED in this worktree):
#
#   project_get(project_id=None, name=None, shortname=None) -> dict | None
#   - project_id -> exact PK lookup, unchanged.
#   - name -> ladder:
#       L1 exact name (case-sensitive)
#       L2 exact shortname (case-sensitive)
#       L3 CI exact name
#       L4 CI exact shortname
#       L5 CI substring over NAME + SHORTNAMES ONLY
#       L5: exactly 1 candidate -> that project; >1 -> refusal with
#           error_code "AMBIGUOUS_PROJECT" listing the candidates;
#           0 -> refusal with error_code "NOT_FOUND".
#   - shortname= -> shortname-only ladder (never matches the project NAME).
#   - no args -> {"error": "Must provide either project_id, name, or shortname"}
#   - success -> the project dict (shape unchanged).
#   - NOT_FOUND -> {"error": "Project not found", "error_code": "NOT_FOUND",
#                   "query": ..., "candidates": []} — "not found" MUST be present.
#   - AMBIGUOUS -> {"error": ..., "error_code": "AMBIGUOUS_PROJECT",
#                   "candidates": [{"project_id","name","shortnames"}...],
#                   "candidate_count": N} and NO top-level project_id/name.
#   - The fuzzy tier MUST NOT match a project's description.
#   - No status filter: a paused/archived project stays resolvable.
#
# The tests exercise the ladder THROUGH THE TOOL (tool_map["project_get"]
# .invoke({...})) against the REAL SQLModelProjectRepository on in-memory
# SQLite — no PG, no live daemon, no network.
# ---------------------------------------------------------------------------


class TestProjectGetResolutionLadder:
    """Tool-level directional pins for the project_get resolution ladder."""

    @staticmethod
    def _seed(tool_map, name, shortnames=None, description=None, status=None):
        """Create a project, then attach shortnames / status via the tools.

        project_create has NO shortnames parameter — shortnames are set
        afterwards through project_set_shortnames / project_add_shortname.
        """
        created = tool_map["project_create"].invoke({
            "name": name,
            "description": description,
        })
        assert "error" not in created, created
        project_id = created["project_id"]
        if shortnames:
            tool_map["project_set_shortnames"].invoke({
                "project_id": project_id,
                "shortnames": list(shortnames),
            })
        if status:
            tool_map["project_set_status"].invoke({
                "project_id": project_id,
                "status": status,
            })
        return created

    # -- CASE 1 — a SHORTNAME resolves to the right project_id ---------------

    def test_get_by_shortname_resolves_the_right_project(self, tool_map):
        """CASE 1 — an exact shortname resolves to the right project_id."""
        created = self._seed(tool_map, "agents-ensemble")
        tool_map["project_add_shortname"].invoke({
            "project_id": created["project_id"],
            "shortname": "ensemble",
        })

        result = tool_map["project_get"].invoke({"shortname": "ensemble"})

        assert "error" not in result, result
        assert result["project_id"] == created["project_id"]
        assert result["name"] == "agents-ensemble"
        assert "ensemble" in result["shortnames"]

    # -- CASE 2 — the exact name still resolves ------------------------------

    def test_get_by_exact_name_still_resolves(self, tool_map):
        """CASE 2 — the exact name still resolves (L1 unchanged)."""
        created = self._seed(tool_map, "agents-ensemble", shortnames=["ensemble"])

        result = tool_map["project_get"].invoke({"name": "agents-ensemble"})

        assert "error" not in result, result
        assert result["project_id"] == created["project_id"]
        assert result["name"] == "agents-ensemble"

    # -- CASE 3 — case-insensitivity -----------------------------------------

    def test_get_by_name_is_case_insensitive(self, tool_map):
        """CASE 3 — "ENSEMBLE" and "Ensemble" resolve the same project."""
        created = self._seed(tool_map, "Ensemble")

        upper = tool_map["project_get"].invoke({"name": "ENSEMBLE"})
        mixed = tool_map["project_get"].invoke({"name": "Ensemble"})

        assert "error" not in upper, upper
        assert "error" not in mixed, mixed
        assert upper["project_id"] == created["project_id"]
        assert mixed["project_id"] == created["project_id"]
        assert upper["project_id"] == mixed["project_id"]

    def test_get_by_name_ci_exact_shortname(self, tool_map):
        """L4 — a case-insensitive EXACT shortname resolves via the name ladder."""
        created = self._seed(tool_map, "agents-ensemble", shortnames=["ensemble"])

        result = tool_map["project_get"].invoke({"name": "ENSEMBLE"})

        assert "error" not in result, result
        assert result["project_id"] == created["project_id"]

    # -- CASE 4 — "ens" (shortname) hits -------------------------------------

    def test_get_by_shortname_partial_hits(self, tool_map):
        """CASE 4a — the shortname-only ladder substring tier: "ens" hits."""
        created = self._seed(tool_map, "agents-ensemble", shortnames=["ensemble"])

        result = tool_map["project_get"].invoke({"shortname": "ens"})

        assert "error" not in result, result
        assert result["project_id"] == created["project_id"]

    def test_get_by_name_partial_matches_name(self, tool_map):
        """L5 — the name ladder substring tier matches over the project NAME."""
        created = self._seed(tool_map, "agents-ensemble", shortnames=["ae"])

        result = tool_map["project_get"].invoke({"name": "ens"})

        assert "error" not in result, result
        assert result["project_id"] == created["project_id"]

    def test_get_by_name_partial_matches_shortname(self, tool_map):
        """L5 — the name ladder substring tier also matches over SHORTNAMES."""
        created = self._seed(tool_map, "orchestrator", shortnames=["ens-suite"])

        result = tool_map["project_get"].invoke({"name": "ens"})

        assert "error" not in result, result
        assert result["project_id"] == created["project_id"]

    # -- CASE 5 — ambiguous partial -> refusal listing the candidates --------

    def test_get_ambiguous_partial_lists_candidates(self, tool_map):
        """CASE 5 — an ambiguous partial refuses and LISTS the candidates."""
        first = self._seed(tool_map, "alpha-service")
        second = self._seed(tool_map, "alpha-worker")

        result = tool_map["project_get"].invoke({"name": "alpha"})

        assert "error" in result, result
        assert result["error_code"] == "AMBIGUOUS_PROJECT", result
        # A refusal NEVER returns a project.
        assert "project_id" not in result, result
        assert "name" not in result, result
        # The candidates ARE listed.
        candidates = result["candidates"]
        assert len(candidates) == 2, result
        assert result["candidate_count"] == 2, result
        assert {c["project_id"] for c in candidates} == {
            first["project_id"], second["project_id"],
        }, result
        for candidate in candidates:
            assert "project_id" in candidate, result
            assert "name" in candidate, result
            assert "shortnames" in candidate, result
        assert {c["name"] for c in candidates} == {"alpha-service", "alpha-worker"}

    # -- CASE 6 — unknown name -> NOT_FOUND refusal -------------------------

    def test_get_unknown_name_refuses_not_found(self, tool_map):
        """CASE 6 — an unknown name refuses with "not found" + NOT_FOUND."""
        result = tool_map["project_get"].invoke({"name": "no-such-project-xyz"})

        assert "error" in result, result
        assert "not found" in result["error"].lower(), result
        assert result["error_code"] == "NOT_FOUND", result
        # A refusal NEVER returns a project.
        assert "project_id" not in result, result

    def test_get_unknown_name_refusal_carries_query_and_empty_candidates(self, tool_map):
        """NOT_FOUND refusal shape — carries the query and an empty candidate list."""
        result = tool_map["project_get"].invoke({"name": "no-such-project-xyz"})

        assert "error" in result, result
        assert result["error_code"] == "NOT_FOUND", result
        assert "query" in result, result
        assert "no-such-project-xyz" in str(result["query"]), result
        assert result["candidates"] == [], result

    # -- CASE 7 — a substring matching exactly ONE candidate resolves -------

    def test_get_substring_with_single_candidate_resolves(self, tool_map):
        """CASE 7 — a substring matching exactly ONE candidate returns it."""
        wanted = self._seed(tool_map, "alpha-service")
        self._seed(tool_map, "beta-worker")

        result = tool_map["project_get"].invoke({"name": "alpha"})

        assert "error" not in result, result
        assert result["project_id"] == wanted["project_id"]
        assert result["name"] == "alpha-service"

    # -- CASE 8 — NEGATIVE: description-only text creates NO fuzzy hit ------

    def test_get_ignores_description_only_text(self, tool_map):
        """CASE 8 — the fuzzy tier MUST NOT match a project's description."""
        self._seed(
            tool_map,
            "agents-ensemble",
            shortnames=["ensemble"],
            description="orchestration platform for multi-agent systems",
        )

        result = tool_map["project_get"].invoke({"name": "orchestration"})

        assert "error" in result, result
        assert result["error_code"] == "NOT_FOUND", result
        assert "project_id" not in result, result

    # -- contract: no status filter ------------------------------------------

    def test_paused_project_stays_resolvable(self, tool_map):
        """No status filter — a paused project stays resolvable."""
        created = self._seed(tool_map, "agents-ensemble", shortnames=["ensemble"], status="paused")

        by_name = tool_map["project_get"].invoke({"name": "agents-ensemble"})
        by_shortname = tool_map["project_get"].invoke({"shortname": "ensemble"})

        assert "error" not in by_name, by_name
        assert by_name["project_id"] == created["project_id"]
        assert "error" not in by_shortname, by_shortname
        assert by_shortname["project_id"] == created["project_id"]


class TestProjectGetResolutionRegression:
    """CASE 9 — the pre-existing project_get surfaces still behave."""

    def test_project_get_legacy_paths_unchanged(self, tool_map):
        """CASE 9 — id / exact-name / not-found / requires-args paths unchanged."""
        created = tool_map["project_create"].invoke({"name": "Legacy Path Project"})

        by_id = tool_map["project_get"].invoke({"project_id": created["project_id"]})
        assert by_id["name"] == "Legacy Path Project"

        by_name = tool_map["project_get"].invoke({"name": "Legacy Path Project"})
        assert by_name["project_id"] == created["project_id"]

        missing = tool_map["project_get"].invoke({"project_id": "nonexistent-id"})
        assert "error" in missing
        assert "not found" in missing["error"].lower()

        no_args = tool_map["project_get"].invoke({})
        assert "error" in no_args
        assert no_args["error"] == "Must provide either project_id, name, or shortname"

    def test_tool_registration_still_21_tools(self, tools):
        """CASE 9 — the 21-tool registration census is unchanged."""
        tool_names = [t.name for t in tools]

        assert len(tools) == 21
        for name in (
            "project_get",
            "project_create",
            "project_set_shortnames",
            "project_add_shortname",
            "project_remove_shortname",
        ):
            assert name in tool_names
