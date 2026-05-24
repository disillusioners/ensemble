"""Tests for project_metadata_records table in SQLModelProjectRepository."""

import time
from pathlib import Path

import pytest

from sqlmodel import Session, SQLModel, create_engine

from daemon.repositories import SQLModelProjectRepository as ProjectStore
from daemon.repositories.project.models import ProjectMetadataRecord


# ============================================================================
# FIXTURES
# ============================================================================

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


@pytest.fixture
def project_with_id(session, store):
    """Create a project and return its ID (for low-level session tests)."""
    project = store.create(name="Test Project")
    return project.project_id


# ============================================================================
# TEST CLASS 1: TestSetMetadataRecord - Low-level CRUD on session
# ============================================================================

class TestSetMetadataRecord:
    """Tests for low-level metadata record CRUD operations using session."""

    def test_create_new_record(self, session, project_with_id):
        """set_metadata_record() creates new record with all fields set."""
        record = session.get(ProjectMetadataRecord, 1)
        assert record is None  # Not yet created

        result = ProjectStore(None).set_metadata_record(
            session, project_with_id, "key1", "value1"
        )

        assert result is not None
        assert result.project_id == project_with_id
        assert result.meta_key == "key1"
        assert result.meta_value == "value1"
        assert result.created_at is not None
        assert result.updated_at is not None

    def test_upsert_existing_key(self, session, project_with_id):
        """Setting same key again updates value (atomic upsert); updated_at changes."""
        repo = ProjectStore(None)

        # Create initial record
        repo.set_metadata_record(session, project_with_id, "key1", "old_value")
        session.commit()

        # Get fresh record to capture original updated_at
        first = repo.get_metadata_record(session, project_with_id, "key1")
        first_updated_at = first.updated_at
        time.sleep(0.01)  # Small delay to ensure different timestamp

        # Update same key
        repo.set_metadata_record(session, project_with_id, "key1", "new_value")
        session.commit()

        # Get fresh record to verify update
        second = repo.get_metadata_record(session, project_with_id, "key1")

        assert second.meta_value == "new_value"
        assert second.updated_at != first_updated_at
        assert second.created_at == first.created_at  # created_at unchanged

    def test_get_metadata_record_exists(self, session, project_with_id):
        """get_metadata_record() returns correct record."""
        ProjectStore(None).set_metadata_record(
            session, project_with_id, "key1", "value1"
        )

        result = ProjectStore(None).get_metadata_record(
            session, project_with_id, "key1"
        )

        assert result is not None
        assert result.meta_key == "key1"
        assert result.meta_value == "value1"

    def test_get_metadata_record_not_exists(self, session, project_with_id):
        """Returns None for non-existent key."""
        result = ProjectStore(None).get_metadata_record(
            session, project_with_id, "nonexistent_key"
        )

        assert result is None

    def test_delete_metadata_record_exists(self, session, project_with_id):
        """delete_metadata_record() returns True and record is gone."""
        ProjectStore(None).set_metadata_record(
            session, project_with_id, "key1", "value1"
        )

        deleted = ProjectStore(None).delete_metadata_record(
            session, project_with_id, "key1"
        )

        assert deleted is True

        # Verify it's gone
        record = ProjectStore(None).get_metadata_record(
            session, project_with_id, "key1"
        )
        assert record is None

    def test_delete_metadata_record_not_exists(self, session, project_with_id):
        """Returns False for non-existent key."""
        deleted = ProjectStore(None).delete_metadata_record(
            session, project_with_id, "nonexistent_key"
        )

        assert deleted is False

    def test_list_metadata_records(self, session, project_with_id):
        """Returns all records for a project."""
        ProjectStore(None).set_metadata_record(session, project_with_id, "key1", "v1")
        ProjectStore(None).set_metadata_record(session, project_with_id, "key2", "v2")
        ProjectStore(None).set_metadata_record(session, project_with_id, "key3", "v3")

        results = ProjectStore(None).list_metadata_records(session, project_with_id)

        assert len(results) == 3
        keys = {r.meta_key for r in results}
        assert keys == {"key1", "key2", "key3"}

    def test_list_metadata_records_empty(self, session, project_with_id):
        """Returns empty list when no metadata."""
        results = ProjectStore(None).list_metadata_records(session, project_with_id)

        assert results == []

    def test_empty_key_raises_error(self, session, project_with_id):
        """set_metadata_record() with empty string key raises ValueError."""
        with pytest.raises(ValueError, match="meta_key cannot be empty"):
            ProjectStore(None).set_metadata_record(
                session, project_with_id, "", "value"
            )

    def test_whitespace_key_raises_error(self, session, project_with_id):
        """set_metadata_record() with whitespace-only key raises ValueError."""
        with pytest.raises(ValueError, match="meta_key cannot be empty"):
            ProjectStore(None).set_metadata_record(
                session, project_with_id, "   ", "value"
            )


# ============================================================================
# TEST CLASS 2: TestSetMetadata - High-level set_metadata method
# ============================================================================

class TestSetMetadata:
    """Tests for high-level set_metadata() method."""

    def test_set_metadata_new_key(self, store):
        """store.set_metadata(project_id, key, value) works, returns enriched project."""
        project = store.create(name="Test Project")

        result = store.set_metadata(project.project_id, "priority", "high")

        assert result is not None
        assert result.project_metadata["priority"] == "high"

    def test_set_metadata_updates_existing(self, store):
        """Setting same key again updates value."""
        project = store.create(name="Test Project", metadata={"key": "old"})
        original_updated_at = project.updated_at
        time.sleep(0.01)

        result = store.set_metadata(project.project_id, "key", "new")

        assert result.project_metadata["key"] == "new"
        assert result.updated_at != original_updated_at

    def test_set_metadata_not_found_project(self, store):
        """Returns None for non-existent project."""
        result = store.set_metadata("nonexistent-id", "key", "value")

        assert result is None


# ============================================================================
# TEST CLASS 3: TestDeleteMetadata - High-level delete_metadata method
# ============================================================================

class TestDeleteMetadata:
    """Tests for high-level delete_metadata() method."""

    def test_delete_metadata_exists(self, store):
        """store.delete_metadata() removes the key."""
        project = store.create(
            name="Test Project",
            metadata={"keep": "v1", "delete": "v2"}
        )

        result = store.delete_metadata(project.project_id, "delete")

        assert result is not None
        assert "delete" not in result.project_metadata
        assert result.project_metadata["keep"] == "v1"

    def test_delete_metadata_not_exists(self, store):
        """Returns project unchanged when key doesn't exist."""
        project = store.create(name="Test Project", metadata={"key": "value"})

        result = store.delete_metadata(project.project_id, "nonexistent")

        assert result is not None
        assert result.project_metadata == {"key": "value"}

    def test_delete_metadata_not_found_project(self, store):
        """Returns None for non-existent project."""
        result = store.delete_metadata("nonexistent-id", "key")

        assert result is None


# ============================================================================
# TEST CLASS 4: TestUpdateWithMetadata - update() method with project_metadata
# ============================================================================

class TestUpdateWithMetadata:
    """Tests for update() method with project_metadata parameter."""

    def test_update_with_metadata_dict(self, store):
        """store.update(project_id, project_metadata={"k": "v"}) creates metadata records."""
        project = store.create(name="Test Project")

        result = store.update(project.project_id, project_metadata={"key": "value"})

        assert result is not None
        assert result.project_metadata["key"] == "value"

    def test_update_metadata_adds_to_existing(self, store):
        """Adding new keys preserves existing ones."""
        project = store.create(name="Test Project", metadata={"existing": "kept"})

        result = store.update(
            project.project_id,
            project_metadata={"new_key": "new_value"}
        )

        assert result.project_metadata["existing"] == "kept"
        assert result.project_metadata["new_key"] == "new_value"

    def test_update_metadata_overwrites_existing(self, store):
        """Setting existing key updates value."""
        project = store.create(name="Test Project", metadata={"key": "old"})

        result = store.update(
            project.project_id,
            project_metadata={"key": "updated"}
        )

        assert result.project_metadata["key"] == "updated"

    def test_update_empty_dict_clears_all(self, store):
        """store.update(project_id, project_metadata={}) deletes all metadata records."""
        project = store.create(
            name="Test Project",
            metadata={"key1": "v1", "key2": "v2"}
        )

        result = store.update(project.project_id, project_metadata={})

        assert result is not None
        assert result.project_metadata == {}

    def test_update_without_metadata(self, store):
        """Calling update without project_metadata doesn't touch metadata table."""
        project = store.create(name="Test Project", metadata={"key": "value"})

        result = store.update(project.project_id, description="New description")

        assert result is not None
        assert result.project_metadata == {"key": "value"}  # Preserved


# ============================================================================
# TEST CLASS 5: TestCreateWithMetadata - create() method with metadata
# ============================================================================

class TestCreateWithMetadata:
    """Tests for create() method with metadata parameter."""

    def test_create_with_metadata(self, store):
        """store.create(name="X", metadata={"k": "v"}) stores in new table."""
        project = store.create(name="Test Project", metadata={"key": "value"})

        assert project.project_metadata["key"] == "value"

    def test_create_with_multiple_metadata_keys(self, store):
        """Multiple keys stored correctly."""
        project = store.create(
            name="Test Project",
            metadata={
                "priority": "high",
                "version": "1.0",
                "active": True
            }
        )

        assert project.project_metadata["priority"] == "high"
        assert project.project_metadata["version"] == "1.0"
        assert project.project_metadata["active"] is True

    def test_create_without_metadata(self, store):
        """No metadata records created."""
        project = store.create(name="Test Project")

        assert project.project_metadata == {}


# ============================================================================
# TEST CLASS 6: TestDeleteProject - delete() method cleans up metadata
# ============================================================================

class TestDeleteProject:
    """Tests for delete() method cleanup of metadata records."""

    def test_delete_project_removes_metadata(self, store):
        """After store.delete(project_id), metadata records are gone."""
        project = store.create(
            name="Test Project",
            metadata={"key1": "v1", "key2": "v2"}
        )
        project_id = project.project_id

        result = store.delete(project_id)

        assert result["deleted"] is True

        # Verify metadata is gone by checking enriched project
        assert store.get(project_id) is None

        # Also verify directly via session that records are deleted
        from sqlmodel import select
        with Session(store.engine) as session:
            records = list(session.exec(
                select(ProjectMetadataRecord).where(
                    ProjectMetadataRecord.project_id == project_id
                )
            ))
            assert len(records) == 0


# ============================================================================
# TEST CLASS 7: TestEnrichment - _enrich_project and _enrich_projects
# ============================================================================

class TestEnrichment:
    """Tests for _enrich_project and _enrich_projects methods."""

    def test_enrich_project_loads_metadata(self, store):
        """After enrichment, project.project_metadata is dict from new table."""
        project = store.create(name="Test Project", metadata={"key": "value"})

        # Get project (which triggers enrichment)
        result = store.get(project.project_id)

        assert result.project_metadata == {"key": "value"}

    def test_enrich_project_no_metadata(self, store):
        """Project with no metadata returns {}."""
        project = store.create(name="Test Project")

        result = store.get(project.project_id)

        assert result.project_metadata == {}

    def test_enrich_projects_loads_metadata(self, store):
        """Multiple projects each get their own metadata."""
        p1 = store.create(name="Project 1", metadata={"key": "p1_value"})
        p2 = store.create(name="Project 2", metadata={"key": "p2_value"})
        p3 = store.create(name="Project 3")  # No metadata

        results = store.list_projects()

        # Find each project in results
        project_dict = {p.name: p for p in results}

        assert project_dict["Project 1"].project_metadata == {"key": "p1_value"}
        assert project_dict["Project 2"].project_metadata == {"key": "p2_value"}
        assert project_dict["Project 3"].project_metadata == {}

    def test_get_by_id_returns_metadata(self, store):
        """store.get(project_id) returns project with metadata dict."""
        project = store.create(
            name="Test Project",
            metadata={"priority": "high", "version": "2.0"}
        )

        result = store.get(project.project_id)

        assert isinstance(result.project_metadata, dict)
        assert result.project_metadata["priority"] == "high"
        assert result.project_metadata["version"] == "2.0"

    def test_list_returns_metadata(self, store):
        """store.list_projects() returns projects with metadata dicts."""
        store.create(name="Project 1", metadata={"a": "1"})
        store.create(name="Project 2", metadata={"b": "2"})

        results = store.list_projects()

        assert len(results) == 2
        for project in results:
            assert isinstance(project.project_metadata, dict)


# ============================================================================
# TEST CLASS 8: TestAtomicUpsert - Race condition protection
# ============================================================================

class TestAtomicUpsert:
    """Tests for atomic upsert behavior on project_metadata_records."""

    def test_upsert_uses_on_conflict(self, session, project_with_id):
        """Setting same key twice results in ONE record (not two)."""
        ProjectStore(None).set_metadata_record(
            session, project_with_id, "key", "first"
        )
        ProjectStore(None).set_metadata_record(
            session, project_with_id, "key", "second"
        )

        # Should only have one record
        records = ProjectStore(None).list_metadata_records(
            session, project_with_id
        )
        matching = [r for r in records if r.meta_key == "key"]

        assert len(matching) == 1
        assert matching[0].meta_value == "second"

    def test_concurrent_same_key(self, session, project_with_id):
        """Set same key rapidly, verify only one record exists with last value."""
        # Rapidly set the same key multiple times
        for i in range(10):
            ProjectStore(None).set_metadata_record(
                session, project_with_id, "rapid_key", f"value_{i}"
            )

        records = ProjectStore(None).list_metadata_records(
            session, project_with_id
        )
        matching = [r for r in records if r.meta_key == "rapid_key"]

        assert len(matching) == 1
        assert matching[0].meta_value == "value_9"  # Last value wins


# ============================================================================
# TEST CLASS 9: TestValueTypes - Various value types stored as JSON
# ============================================================================

class TestValueTypes:
    """Tests for various value types stored as JSON in metadata."""

    def test_string_value(self, store):
        """String value roundtrips."""
        project = store.create(name="Test", metadata={"key": "string value"})

        result = store.get(project.project_id)
        assert result.project_metadata["key"] == "string value"

    def test_number_value(self, store):
        """Integer/float roundtrips."""
        project = store.create(
            name="Test",
            metadata={
                "int_key": 42,
                "float_key": 3.14159
            }
        )

        result = store.get(project.project_id)
        assert result.project_metadata["int_key"] == 42
        assert result.project_metadata["float_key"] == 3.14159

    def test_boolean_value(self, store):
        """True/False roundtrips."""
        project = store.create(
            name="Test",
            metadata={
                "true_key": True,
                "false_key": False
            }
        )

        result = store.get(project.project_id)
        assert result.project_metadata["true_key"] is True
        assert result.project_metadata["false_key"] is False

    def test_list_value(self, store):
        """List roundtrips."""
        project = store.create(
            name="Test",
            metadata={
                "tags": ["python", "fastapi", "testing"]
            }
        )

        result = store.get(project.project_id)
        assert result.project_metadata["tags"] == ["python", "fastapi", "testing"]

    def test_dict_value(self, store):
        """Nested dict roundtrips."""
        project = store.create(
            name="Test",
            metadata={
                "config": {
                    "database": {
                        "host": "localhost",
                        "port": 5432
                    },
                    "cache": {"enabled": True}
                }
            }
        )

        result = store.get(project.project_id)
        assert result.project_metadata["config"]["database"]["host"] == "localhost"
        assert result.project_metadata["config"]["database"]["port"] == 5432
        assert result.project_metadata["config"]["cache"]["enabled"] is True

    def test_null_value(self, store):
        """None/null roundtrips."""
        project = store.create(
            name="Test",
            metadata={"null_key": None, "empty_key": ""}
        )

        result = store.get(project.project_id)
        assert result.project_metadata["null_key"] is None
        assert result.project_metadata["empty_key"] == ""


# ============================================================================
# TEST CLASS 10: TestMigration - Migration file validation
# ============================================================================

class TestMigration:
    """Tests for project_metadata_records migration file."""

    def test_migration_file_exists(self):
        """Migration file exists at expected path."""
        migration_path = Path(
            "daemon/migrations/versions/20260524_000003_create_project_metadata_records_table.sql"
        )
        assert migration_path.exists(), f"Migration file not found: {migration_path}"

    def test_migration_has_up_and_down(self):
        """File contains CREATE TABLE and DROP TABLE."""
        migration_path = Path(
            "daemon/migrations/versions/20260524_000003_create_project_metadata_records_table.sql"
        )
        content = migration_path.read_text()

        assert "CREATE TABLE" in content, "Missing CREATE TABLE"
        assert "DROP TABLE" in content, "Missing DROP TABLE"
        assert "-- UP" in content, "Missing -- UP section"
        assert "-- DOWN" in content, "Missing -- DOWN section"

    def test_migration_has_unique_constraint(self):
        """File creates unique index on (project_id, meta_key)."""
        migration_path = Path(
            "daemon/migrations/versions/20260524_000003_create_project_metadata_records_table.sql"
        )
        content = migration_path.read_text()

        assert "UNIQUE" in content or "unique" in content, "Missing unique constraint"
        assert "project_id" in content, "Missing project_id in constraint"
        assert "meta_key" in content, "Missing meta_key in constraint"

    def test_migration_has_cascade(self):
        """FK references projects(project_id) ON DELETE CASCADE."""
        migration_path = Path(
            "daemon/migrations/versions/20260524_000003_create_project_metadata_records_table.sql"
        )
        content = migration_path.read_text()

        assert "ON DELETE CASCADE" in content, "Missing ON DELETE CASCADE"
        assert "projects" in content, "Missing projects reference"
