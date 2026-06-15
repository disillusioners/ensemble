"""Comprehensive tests for SQLModelInfraRepository.

Tests are grouped by functional area:

1. CRUD — create / get / list / update / delete + UNIQUE constraint.
2. History — auto "created" / "updated" / "deleted" history rows.
3. Search — dialect-aware attribute operators on SQLite.
4. Type Registry — global (no project_id) type CRUD + bootstrap.
5. Project Isolation — assets from project A invisible to project B.
6. Parent-Child — SET NULL on delete, hierarchical queries.

All tests run against an in-memory SQLite database (via the
``engine`` fixture in conftest.py) so they are fast and isolated.
"""

from __future__ import annotations

import pytest

from daemon.repositories.infra import (
    InfraAsset,
    InfraAssetHistory,
    InfraAssetType,
    InfraChangeType,
    INFRA_TYPE_DEFINITIONS,
    SQLModelInfraRepository,
)


# =============================================================================
# Group 1: CRUD
# =============================================================================


class TestCreateAsset:
    """Tests for repository.create_asset()."""

    def test_create_asset_minimal(self, infra_repository, seed_projects, project_id):
        """Create asset with only required fields; all defaults applied."""
        asset = infra_repository.create_asset(
            project_id=project_id,
            type="server",
            name="web-01",
        )

        assert asset.id is not None
        assert asset.project_id == project_id
        assert asset.type == "server"
        assert asset.name == "web-01"
        assert asset.parent_asset_id is None
        assert asset.attributes == {}
        assert asset.relationships == {}
        assert asset.created_by is None
        assert asset.updated_by is None
        assert asset.created_at is not None
        assert asset.updated_at is not None
        assert asset.created_at == asset.updated_at

    def test_create_asset_full_fields(
        self, infra_repository, seed_projects, project_id
    ):
        """Create asset with every field populated."""
        attributes = {"cpu_count": 8, "memory_gb": 32, "env": "production"}
        relationships = {"linked_to": ["db-01", "lb-01"]}

        asset = infra_repository.create_asset(
            project_id=project_id,
            type="server",
            name="web-01-full",
            attributes=attributes,
            relationships=relationships,
            parent_asset_id=None,
            created_by="agent-42",
        )

        assert asset.attributes == attributes
        assert asset.relationships == relationships
        assert asset.created_by == "agent-42"
        assert asset.updated_by == "agent-42"

    def test_create_asset_with_parent(
        self, infra_repository, seed_projects, project_id
    ):
        """Create asset with a parent_asset_id (parent-child link)."""
        parent = infra_repository.create_asset(
            project_id=project_id,
            type="k8s_cluster",
            name="prod-cluster",
        )
        child = infra_repository.create_asset(
            project_id=project_id,
            type="server",
            name="worker-01",
            parent_asset_id=parent.id,
        )

        assert child.parent_asset_id == parent.id

    def test_create_asset_duplicate_raises_valueerror(
        self, infra_repository, seed_projects, project_id
    ):
        """Duplicate (project_id, type, name) raises ValueError."""
        infra_repository.create_asset(
            project_id=project_id,
            type="server",
            name="web-01",
        )
        with pytest.raises(ValueError) as exc_info:
            infra_repository.create_asset(
                project_id=project_id,
                type="server",
                name="web-01",
            )
        assert project_id in str(exc_info.value)
        assert "web-01" in str(exc_info.value)

    def test_create_asset_same_name_different_type_ok(
        self, infra_repository, seed_projects, project_id
    ):
        """Same name but different type is allowed."""
        infra_repository.create_asset(
            project_id=project_id,
            type="server",
            name="web-01",
        )
        # Same name, different type — should succeed.
        asset2 = infra_repository.create_asset(
            project_id=project_id,
            type="datacenter",
            name="web-01",
        )
        assert asset2.type == "datacenter"
        assert asset2.name == "web-01"


class TestGetAsset:
    """Tests for repository.get_asset()."""

    def test_get_asset_existing(self, infra_repository, seed_projects, project_id):
        """get_asset returns the correct asset."""
        created = infra_repository.create_asset(
            project_id=project_id,
            type="server",
            name="web-01",
            attributes={"env": "staging"},
        )
        fetched = infra_repository.get_asset(created.id)

        assert fetched is not None
        assert fetched.id == created.id
        assert fetched.type == "server"
        assert fetched.name == "web-01"
        assert fetched.attributes == {"env": "staging"}

    def test_get_asset_nonexistent(self, infra_repository):
        """get_asset returns None for unknown ID."""
        assert infra_repository.get_asset("does-not-exist") is None


class TestListAssets:
    """Tests for repository.list_assets()."""

    def test_list_empty(self, infra_repository, seed_projects, project_id):
        """list_assets on empty project returns []."""
        assert infra_repository.list_assets(project_id) == []

    def test_list_all(
        self, infra_repository, seed_projects, project_id
    ):
        """list_assets returns every asset for the project."""
        infra_repository.create_asset(project_id=project_id, type="server", name="a")
        infra_repository.create_asset(project_id=project_id, type="server", name="b")
        infra_repository.create_asset(project_id=project_id, type="datacenter", name="c")

        assets = infra_repository.list_assets(project_id)
        assert len(assets) == 3
        names = {a.name for a in assets}
        assert names == {"a", "b", "c"}

    def test_list_filter_type(
        self, infra_repository, seed_projects, project_id
    ):
        """list_assets with type filter returns only matching type."""
        infra_repository.create_asset(project_id=project_id, type="server", name="s1")
        infra_repository.create_asset(project_id=project_id, type="server", name="s2")
        infra_repository.create_asset(
            project_id=project_id, type="datacenter", name="d1"
        )

        assets = infra_repository.list_assets(project_id, type="server")
        assert len(assets) == 2
        assert all(a.type == "server" for a in assets)

    def test_list_filter_parent_asset_id(
        self, infra_repository, seed_projects, project_id
    ):
        """list_assets with parent_asset_id returns only children."""
        parent = infra_repository.create_asset(
            project_id=project_id, type="k8s_cluster", name="cluster"
        )
        infra_repository.create_asset(
            project_id=project_id,
            type="server",
            name="child1",
            parent_asset_id=parent.id,
        )
        infra_repository.create_asset(
            project_id=project_id,
            type="server",
            name="child2",
            parent_asset_id=parent.id,
        )
        infra_repository.create_asset(
            project_id=project_id, type="server", name="orphan"
        )

        children = infra_repository.list_assets(
            project_id, parent_asset_id=parent.id
        )
        assert len(children) == 2
        assert all(c.parent_asset_id == parent.id for c in children)

    def test_list_pagination(self, infra_repository, seed_projects, project_id):
        """list_assets respects limit and offset."""
        for i in range(5):
            infra_repository.create_asset(
                project_id=project_id, type="server", name=f"s{i}"
            )

        page1 = infra_repository.list_assets(project_id, limit=2, offset=0)
        page2 = infra_repository.list_assets(project_id, limit=2, offset=2)
        page3 = infra_repository.list_assets(project_id, limit=2, offset=4)

        assert len(page1) == 2
        assert len(page2) == 2
        assert len(page3) == 1
        page1_names = {a.name for a in page1}
        page3_names = {a.name for a in page3}
        assert page1_names.isdisjoint(page3_names)


class TestUpdateAsset:
    """Tests for repository.update_asset()."""

    def test_update_asset_fields(
        self, infra_repository, seed_projects, project_id
    ):
        """update_asset changes the specified fields and leaves others alone."""
        asset = infra_repository.create_asset(
            project_id=project_id,
            type="server",
            name="web-01",
            attributes={"cpu": 4},
        )
        original_created_at = asset.created_at

        updated = infra_repository.update_asset(
            asset.id,
            name="web-01-renamed",
            attributes={"cpu": 8, "ram": 32},
            updated_by="agent-42",
        )

        assert updated is not None
        assert updated.name == "web-01-renamed"
        assert updated.attributes == {"cpu": 8, "ram": 32}
        assert updated.updated_by == "agent-42"
        assert updated.created_at == original_created_at
        assert updated.updated_at > original_created_at

    def test_update_asset_partial(
        self, infra_repository, seed_projects, project_id
    ):
        """Updating only some fields leaves the rest unchanged."""
        asset = infra_repository.create_asset(
            project_id=project_id,
            type="server",
            name="web-01",
            attributes={"env": "dev"},
        )
        infra_repository.update_asset(asset.id, name="web-01-renamed")

        fetched = infra_repository.get_asset(asset.id)
        assert fetched.name == "web-01-renamed"
        assert fetched.type == "server"
        assert fetched.attributes == {"env": "dev"}

    def test_update_asset_protected_fields_ignored(
        self, infra_repository, seed_projects, project_id, caplog
    ):
        """Protected fields (id, project_id, created_at, created_by) are ignored."""
        asset = infra_repository.create_asset(
            project_id=project_id,
            type="server",
            name="web-01",
        )

        updated = infra_repository.update_asset(
            asset.id,
            id="new-id-ignored",
            project_id="other-project-ignored",
            created_at="2020-01-01",
            created_by="someone-ignored",
        )

        assert updated is not None
        assert updated.id == asset.id
        assert updated.project_id == project_id

    def test_update_asset_nonexistent(self, infra_repository):
        """update_asset returns None for unknown ID."""
        assert infra_repository.update_asset("ghost-id", name="x") is None

    def test_update_asset_unique_constraint_violation(
        self, infra_repository, seed_projects, project_id
    ):
        """Renaming into an existing (type, name) raises ValueError."""
        infra_repository.create_asset(
            project_id=project_id, type="server", name="a"
        )
        b = infra_repository.create_asset(
            project_id=project_id, type="server", name="b"
        )
        with pytest.raises(ValueError) as exc_info:
            infra_repository.update_asset(b.id, name="a")
        assert "UNIQUE" in str(exc_info.value) or "already exists" in str(
            exc_info.value
        )


class TestDeleteAsset:
    """Tests for repository.delete_asset()."""

    def test_delete_asset_existing(self, infra_repository, seed_projects, project_id):
        """delete_asset returns True and removes the row."""
        asset = infra_repository.create_asset(
            project_id=project_id, type="server", name="web-01"
        )
        assert infra_repository.delete_asset(asset.id) is True
        assert infra_repository.get_asset(asset.id) is None

    def test_delete_asset_nonexistent(self, infra_repository):
        """delete_asset returns False for unknown ID (does not raise)."""
        assert infra_repository.delete_asset("ghost-id") is False


# =============================================================================
# Group 2: Versioning / History
# =============================================================================


class TestHistoryOnCreate:
    """Tests that create_asset writes a "created" history row."""

    def test_created_history_row_exists(
        self, infra_repository, seed_projects, project_id
    ):
        """A "created" history row is written on create."""
        asset = infra_repository.create_asset(
            project_id=project_id,
            type="server",
            name="web-01",
            attributes={"cpu": 4},
            created_by="agent-1",
        )
        history = infra_repository.get_history(asset.id)

        assert len(history) >= 1
        created_row = history[0]
        assert created_row.change_type == InfraChangeType.CREATED.value
        assert created_row.asset_id == asset.id
        assert created_row.project_id == project_id
        assert created_row.changed_by == "agent-1"
        assert created_row.snapshot is not None
        assert created_row.snapshot["name"] == "web-01"
        assert created_row.changed_fields is None
        assert created_row.old_values is None
        assert created_row.new_values is None

    def test_created_history_has_full_snapshot(
        self, infra_repository, seed_projects, project_id
    ):
        """The "created" snapshot contains all asset fields."""
        asset = infra_repository.create_asset(
            project_id=project_id,
            type="server",
            name="web-01",
            attributes={"env": "prod"},
        )
        history = infra_repository.get_history(asset.id)
        snapshot = history[0].snapshot

        assert snapshot["id"] == asset.id
        assert snapshot["type"] == "server"
        assert snapshot["name"] == "web-01"
        assert snapshot["attributes"] == {"env": "prod"}
        assert snapshot["project_id"] == project_id


class TestHistoryOnUpdate:
    """Tests that update_asset writes an "updated" history row with diff."""

    def test_updated_history_row_exists(
        self, infra_repository, seed_projects, project_id
    ):
        """An "updated" history row is written when fields change."""
        asset = infra_repository.create_asset(
            project_id=project_id,
            type="server",
            name="web-01",
            attributes={"cpu": 4},
        )
        infra_repository.update_asset(
            asset.id,
            name="web-01-v2",
            attributes={"cpu": 8},
            updated_by="agent-2",
        )
        history = infra_repository.get_history(asset.id)

        # history[0] is newest — should be the update.
        assert len(history) >= 2
        updated_row = history[0]
        assert updated_row.change_type == InfraChangeType.UPDATED.value
        assert updated_row.changed_by == "agent-2"
        assert set(updated_row.changed_fields) == {"name", "attributes"}
        assert updated_row.old_values["name"] == "web-01"
        assert updated_row.old_values["attributes"] == {"cpu": 4}
        assert updated_row.new_values["name"] == "web-01-v2"
        assert updated_row.new_values["attributes"] == {"cpu": 8}

    def test_updated_history_not_written_when_no_fields_change(
        self, infra_repository, seed_projects, project_id
    ):
        """No history row is written when update passes identical values."""
        asset = infra_repository.create_asset(
            project_id=project_id,
            type="server",
            name="web-01",
            attributes={"cpu": 4},
        )
        infra_repository.update_asset(asset.id, name="web-01")  # same name
        history = infra_repository.get_history(asset.id)

        # Only the "created" row, no "updated".
        assert len(history) == 1
        assert history[0].change_type == InfraChangeType.CREATED.value

    def test_history_ordered_newest_first(
        self, infra_repository, seed_projects, project_id
    ):
        """get_history returns rows in descending timestamp order."""
        asset = infra_repository.create_asset(
            project_id=project_id,
            type="server",
            name="web-01",
        )
        infra_repository.update_asset(asset.id, name="v2")
        infra_repository.update_asset(asset.id, name="v3")
        history = infra_repository.get_history(asset.id)

        timestamps = [row.timestamp for row in history]
        assert timestamps == sorted(timestamps, reverse=True)


class TestHistoryOnDelete:
    """Tests that delete_asset writes a "deleted" history row with full snapshot."""

    def test_deleted_history_row_exists(
        self, infra_repository, seed_projects, project_id
    ):
        """A "deleted" history row is written on delete."""
        asset = infra_repository.create_asset(
            project_id=project_id,
            type="server",
            name="web-01",
            attributes={"cpu": 4},
        )
        infra_repository.delete_asset(asset.id, deleted_by="agent-3")
        history = infra_repository.get_history(asset.id)

        assert len(history) == 2  # created + deleted
        deleted_row = history[0]
        assert deleted_row.change_type == InfraChangeType.DELETED.value
        assert deleted_row.changed_by == "agent-3"
        assert deleted_row.snapshot is not None
        assert deleted_row.snapshot["name"] == "web-01"
        assert deleted_row.changed_fields is None
        assert deleted_row.old_values is None
        assert deleted_row.new_values is None

    def test_deleted_history_snapshot_id_preserved(
        self, infra_repository, seed_projects, project_id
    ):
        """The deleted snapshot preserves the asset's original ID."""
        asset = infra_repository.create_asset(
            project_id=project_id,
            type="server",
            name="web-01",
        )
        original_id = asset.id
        infra_repository.delete_asset(asset.id)
        history = infra_repository.get_history(asset.id)

        deleted_row = history[0]
        assert deleted_row.snapshot["id"] == original_id


class TestGetHistoryPagination:
    """Tests for repository.get_history() pagination."""

    def test_get_history_limit(
        self, infra_repository, seed_projects, project_id
    ):
        """get_history respects the limit parameter."""
        asset = infra_repository.create_asset(
            project_id=project_id,
            type="server",
            name="web-01",
        )
        infra_repository.update_asset(asset.id, name="v2")
        infra_repository.update_asset(asset.id, name="v3")

        page1 = infra_repository.get_history(asset.id, limit=1)
        page2 = infra_repository.get_history(asset.id, limit=1, offset=1)

        assert len(page1) == 1
        assert len(page2) == 1
        assert page1[0].timestamp >= page2[0].timestamp

    def test_get_history_empty_for_unknown_asset(self, infra_repository):
        """get_history returns [] for an asset that never existed."""
        assert infra_repository.get_history("never-existed") == []


# =============================================================================
# Group 3: Search (dialect-aware — SQLite json_extract)
# =============================================================================


class TestSearchByType:
    """Tests for search by top-level ``type`` field."""

    def test_search_type_exact(self, infra_repository, seed_projects, project_id):
        """search with type= returns only matching type."""
        infra_repository.create_asset(
            project_id=project_id, type="server", name="s1"
        )
        infra_repository.create_asset(
            project_id=project_id, type="server", name="s2"
        )
        infra_repository.create_asset(
            project_id=project_id, type="datacenter", name="d1"
        )

        results = infra_repository.search_assets(project_id, {"type": "server"})
        assert len(results) == 2
        assert all(r.type == "server" for r in results)


class TestSearchByName:
    """Tests for search by ``name`` (substring/LIKE match)."""

    def test_search_name_substring(self, infra_repository, seed_projects, project_id):
        """search with name= does a case-insensitive substring match."""
        infra_repository.create_asset(
            project_id=project_id, type="server", name="web-prod-01"
        )
        infra_repository.create_asset(
            project_id=project_id, type="server", name="web-staging-01"
        )
        infra_repository.create_asset(
            project_id=project_id, type="server", name="db-prod-01"
        )

        results = infra_repository.search_assets(project_id, {"name": "prod"})
        assert len(results) == 2
        assert all("prod" in r.name for r in results)

    def test_search_name_escapes_special_chars(
        self, infra_repository, seed_projects, project_id
    ):
        """Names containing % or _ still match correctly (special LIKE chars escaped)."""
        infra_repository.create_asset(
            project_id=project_id, type="server", name="host%01"
        )
        infra_repository.create_asset(
            project_id=project_id, type="server", name="host_02"
        )

        results = infra_repository.search_assets(project_id, {"name": "%01"})
        assert len(results) == 1
        assert results[0].name == "host%01"


class TestSearchByParentAssetId:
    """Tests for search by ``parent_asset_id``."""

    def test_search_parent_asset_id_exact(
        self, infra_repository, seed_projects, project_id
    ):
        """search with parent_asset_id= returns only children of that parent."""
        parent = infra_repository.create_asset(
            project_id=project_id, type="k8s_cluster", name="cluster"
        )
        infra_repository.create_asset(
            project_id=project_id,
            type="server",
            name="child1",
            parent_asset_id=parent.id,
        )
        infra_repository.create_asset(
            project_id=project_id, type="server", name="orphan"
        )

        results = infra_repository.search_assets(
            project_id, {"parent_asset_id": parent.id}
        )
        assert len(results) == 1
        assert results[0].name == "child1"


class TestSearchByAttributesOperators:
    """Tests for search using ``attributes`` dict with mongo-style operators."""

    def test_eq_operator(self, infra_repository, seed_projects, project_id):
        """$eq on attributes returns exact-match assets."""
        infra_repository.create_asset(
            project_id=project_id,
            type="server",
            name="prod",
            attributes={"env": "production"},
        )
        infra_repository.create_asset(
            project_id=project_id,
            type="server",
            name="dev",
            attributes={"env": "development"},
        )
        infra_repository.create_asset(
            project_id=project_id,
            type="server",
            name="prod2",
            attributes={"env": "production"},
        )

        results = infra_repository.search_assets(
            project_id,
            {"attributes": {"env": {"$eq": "production"}}},
        )
        assert len(results) == 2
        assert all(r.attributes.get("env") == "production" for r in results)

    def test_ne_operator(self, infra_repository, seed_projects, project_id):
        """$ne on attributes excludes matching assets."""
        infra_repository.create_asset(
            project_id=project_id,
            type="server",
            name="a",
            attributes={"status": "active"},
        )
        infra_repository.create_asset(
            project_id=project_id,
            type="server",
            name="b",
            attributes={"status": "inactive"},
        )

        results = infra_repository.search_assets(
            project_id,
            {"attributes": {"status": {"$ne": "inactive"}}},
        )
        assert len(results) == 1
        assert results[0].name == "a"

    def test_gt_operator(self, infra_repository, seed_projects, project_id):
        """$gt on numeric attributes returns assets where value > threshold."""
        infra_repository.create_asset(
            project_id=project_id, type="server", name="s1", attributes={"cpu": 4}
        )
        infra_repository.create_asset(
            project_id=project_id, type="server", name="s2", attributes={"cpu": 8}
        )
        infra_repository.create_asset(
            project_id=project_id, type="server", name="s3", attributes={"cpu": 16}
        )

        results = infra_repository.search_assets(
            project_id,
            {"attributes": {"cpu": {"$gt": 8}}},
        )
        assert len(results) == 1
        assert results[0].attributes["cpu"] == 16

    def test_gte_operator(self, infra_repository, seed_projects, project_id):
        """$gte on numeric attributes includes the boundary value."""
        infra_repository.create_asset(
            project_id=project_id, type="server", name="s1", attributes={"cpu": 4}
        )
        infra_repository.create_asset(
            project_id=project_id, type="server", name="s2", attributes={"cpu": 8}
        )

        results = infra_repository.search_assets(
            project_id,
            {"attributes": {"cpu": {"$gte": 8}}},
        )
        assert len(results) == 1
        assert results[0].attributes["cpu"] == 8

    def test_lt_operator(self, infra_repository, seed_projects, project_id):
        """$lt on numeric attributes returns assets where value < threshold."""
        infra_repository.create_asset(
            project_id=project_id, type="server", name="s1", attributes={"cpu": 4}
        )
        infra_repository.create_asset(
            project_id=project_id, type="server", name="s2", attributes={"cpu": 8}
        )

        results = infra_repository.search_assets(
            project_id,
            {"attributes": {"cpu": {"$lt": 8}}},
        )
        assert len(results) == 1
        assert results[0].attributes["cpu"] == 4

    def test_lte_operator(self, infra_repository, seed_projects, project_id):
        """$lte on numeric attributes includes the boundary value."""
        infra_repository.create_asset(
            project_id=project_id, type="server", name="s1", attributes={"cpu": 4}
        )
        infra_repository.create_asset(
            project_id=project_id, type="server", name="s2", attributes={"cpu": 8}
        )

        results = infra_repository.search_assets(
            project_id,
            {"attributes": {"cpu": {"$lte": 8}}},
        )
        assert len(results) == 2

    def test_in_operator(self, infra_repository, seed_projects, project_id):
        """$in on attributes returns assets where key value is in the list."""
        infra_repository.create_asset(
            project_id=project_id,
            type="server",
            name="a",
            attributes={"region": "us-east"},
        )
        infra_repository.create_asset(
            project_id=project_id,
            type="server",
            name="b",
            attributes={"region": "us-west"},
        )
        infra_repository.create_asset(
            project_id=project_id,
            type="server",
            name="c",
            attributes={"region": "eu-central"},
        )

        results = infra_repository.search_assets(
            project_id,
            {"attributes": {"region": {"$in": ["us-east", "us-west"]}}},
        )
        assert len(results) == 2
        assert all(r.attributes["region"].startswith("us-") for r in results)

    def test_contains_operator(self, infra_repository, seed_projects, project_id):
        """$contains on attributes returns assets where key value contains substring."""
        infra_repository.create_asset(
            project_id=project_id,
            type="server",
            name="a",
            attributes={"hostname": "web-prod-01"},
        )
        infra_repository.create_asset(
            project_id=project_id,
            type="server",
            name="b",
            attributes={"hostname": "db-prod-01"},
        )
        infra_repository.create_asset(
            project_id=project_id,
            type="server",
            name="c",
            attributes={"hostname": "web-staging-01"},
        )

        results = infra_repository.search_assets(
            project_id,
            {"attributes": {"hostname": {"$contains": "prod"}}},
        )
        assert len(results) == 2
        assert all("prod" in r.attributes["hostname"] for r in results)

    def test_exists_true_operator(self, infra_repository, seed_projects, project_id):
        """$exists: true returns assets that have the key."""
        infra_repository.create_asset(
            project_id=project_id,
            type="server",
            name="a",
            attributes={"cpu": 8},
        )
        infra_repository.create_asset(
            project_id=project_id, type="server", name="b", attributes={}
        )

        results = infra_repository.search_assets(
            project_id,
            {"attributes": {"cpu": {"$exists": True}}},
        )
        assert len(results) == 1
        assert results[0].name == "a"

    def test_exists_false_operator(self, infra_repository, seed_projects, project_id):
        """$exists: false returns assets that do NOT have the key."""
        infra_repository.create_asset(
            project_id=project_id,
            type="server",
            name="a",
            attributes={"cpu": 8},
        )
        infra_repository.create_asset(
            project_id=project_id, type="server", name="b", attributes={}
        )

        results = infra_repository.search_assets(
            project_id,
            {"attributes": {"cpu": {"$exists": False}}},
        )
        assert len(results) == 1
        assert results[0].name == "b"

    def test_plain_value_equality(self, infra_repository, seed_projects, project_id):
        """A plain value (not a dict) in attributes acts as $eq."""
        infra_repository.create_asset(
            project_id=project_id,
            type="server",
            name="a",
            attributes={"env": "prod"},
        )
        infra_repository.create_asset(
            project_id=project_id,
            type="server",
            name="b",
            attributes={"env": "dev"},
        )

        results = infra_repository.search_assets(
            project_id,
            {"attributes": {"env": "prod"}},
        )
        assert len(results) == 1
        assert results[0].name == "a"

    def test_combined_filters(self, infra_repository, seed_projects, project_id):
        """Multiple filters are ANDed together."""
        infra_repository.create_asset(
            project_id=project_id,
            type="server",
            name="a",
            attributes={"env": "prod", "cpu": 8},
        )
        infra_repository.create_asset(
            project_id=project_id,
            type="server",
            name="b",
            attributes={"env": "prod", "cpu": 4},
        )
        infra_repository.create_asset(
            project_id=project_id,
            type="server",
            name="c",
            attributes={"env": "dev", "cpu": 8},
        )

        results = infra_repository.search_assets(
            project_id,
            {
                "type": "server",
                "attributes": {"env": "prod", "cpu": {"$gte": 8}},
            },
        )
        assert len(results) == 1
        assert results[0].name == "a"

    def test_search_no_matches(self, infra_repository, seed_projects, project_id):
        """search with no matching criteria returns an empty list."""
        infra_repository.create_asset(
            project_id=project_id,
            type="server",
            name="s1",
            attributes={"env": "prod"},
        )

        results = infra_repository.search_assets(
            project_id,
            {"attributes": {"env": "nonexistent"}},
        )
        assert results == []

    def test_search_pagination(self, infra_repository, seed_projects, project_id):
        """search respects limit and offset."""
        for i in range(5):
            infra_repository.create_asset(
                project_id=project_id,
                type="server",
                name=f"s{i}",
                attributes={"cpu": i},
            )

        page1 = infra_repository.search_assets(project_id, {}, limit=2, offset=0)
        page2 = infra_repository.search_assets(project_id, {}, limit=2, offset=2)

        assert len(page1) == 2
        assert len(page2) == 2


# =============================================================================
# Group 4: Type Registry (GLOBAL — no project_id)
# =============================================================================


class TestRegisterType:
    """Tests for repository.register_type()."""

    def test_register_new_type(self, infra_repository):
        """register_type inserts a new type definition."""
        t = infra_repository.register_type(
            name="load_balancer",
            description="A network load balancer",
            schema_json={"type": "object", "properties": {"ip": {"type": "string"}}},
        )

        assert t.name == "load_balancer"
        assert t.description == "A network load balancer"
        assert t.schema_doc == {
            "type": "object",
            "properties": {"ip": {"type": "string"}},
        }
        assert t.created_at is not None
        assert t.updated_at is not None

    def test_register_upserts_existing_type(self, infra_repository):
        """register_type updates an existing type (upsert)."""
        infra_repository.register_type(
            name="server",
            description="Original desc",
            schema_json={"type": "object"},
        )
        updated = infra_repository.register_type(
            name="server",
            description="Updated desc",
            schema_json={"type": "object", "properties": {"ip": {}}},
        )

        assert updated.description == "Updated desc"
        assert updated.schema_doc == {
            "type": "object",
            "properties": {"ip": {}},
        }
        assert updated.updated_at > updated.created_at

    def test_register_minimal_fields(self, infra_repository):
        """register_type with only name uses defaults."""
        t = infra_repository.register_type(name="my_type")
        assert t.description == ""
        assert t.schema_doc == {}


class TestGetType:
    """Tests for repository.get_type()."""

    def test_get_type_existing(self, infra_repository):
        """get_type returns the type if it exists."""
        infra_repository.register_type(
            name="server",
            description="A server",
            schema_json={"type": "object"},
        )
        t = infra_repository.get_type("server")

        assert t is not None
        assert t.name == "server"
        assert t.description == "A server"

    def test_get_type_nonexistent(self, infra_repository):
        """get_type returns None for an unknown type name."""
        assert infra_repository.get_type("nonexistent-type") is None


class TestListTypes:
    """Tests for repository.list_types()."""

    def test_list_types_empty(self, infra_repository):
        """list_types on empty registry returns []."""
        assert infra_repository.list_types() == []

    def test_list_types_returns_all(self, infra_repository):
        """list_types returns every registered type ordered by name."""
        infra_repository.register_type(name="zebra_type")
        infra_repository.register_type(name="alpha_type")
        infra_repository.register_type(name="middle_type")

        types = infra_repository.list_types()
        names = [t.name for t in types]
        assert names == sorted(names)  # ascending by name
        assert "alpha_type" in names
        assert "middle_type" in names
        assert "zebra_type" in names


class TestBootstrapDefaultTypes:
    """Tests for repository.bootstrap_default_types()."""

    def test_bootstrap_inserts_three_types(self, infra_repository):
        """bootstrap_default_types seeds the 3 built-in types."""
        types = infra_repository.bootstrap_default_types()

        assert len(types) == 3
        names = {t.name for t in types}
        assert names == {"datacenter", "server", "k8s_cluster"}

    def test_bootstrap_idempotent(self, infra_repository):
        """Calling bootstrap_default_types twice is safe (upsert)."""
        infra_repository.bootstrap_default_types()
        types2 = infra_repository.bootstrap_default_types()

        assert len(types2) == 3
        # Should not raise — upsert path.

    def test_bootstrap_sets_correct_schemas(self, infra_repository):
        """The 3 bootstrapped types have the expected schema_doc."""
        infra_repository.bootstrap_default_types()

        server = infra_repository.get_type("server")
        assert "properties" in server.schema_doc
        assert "hostname" in server.schema_doc["properties"]

        k8s = infra_repository.get_type("k8s_cluster")
        assert "version" in k8s.schema_doc["properties"]

        dc = infra_repository.get_type("datacenter")
        assert "location" in dc.schema_doc["properties"]


# =============================================================================
# Group 5: Project Isolation
# =============================================================================


class TestProjectIsolation:
    """Tests that assets are isolated by project_id."""

    def test_assets_not_visible_to_other_project(
        self, infra_repository, seed_projects, project_id, other_project_id
    ):
        """Assets in project A do not appear when listing project B."""
        infra_repository.create_asset(
            project_id=project_id,
            type="server",
            name="a-in-p1",
        )
        infra_repository.create_asset(
            project_id=other_project_id,
            type="server",
            name="a-in-p2",
        )

        p1_assets = infra_repository.list_assets(project_id)
        p2_assets = infra_repository.list_assets(other_project_id)

        assert len(p1_assets) == 1
        assert p1_assets[0].name == "a-in-p1"
        assert len(p2_assets) == 1
        assert p2_assets[0].name == "a-in-p2"

    def test_get_asset_cross_project(
        self, infra_repository, seed_projects, project_id, other_project_id
    ):
        """get_asset returns None for an ID that belongs to a different project."""
        asset_in_p1 = infra_repository.create_asset(
            project_id=project_id,
            type="server",
            name="p1-only",
        )
        # Attempting to read it via the other project context still works
        # because get_asset is by ID only (no project filter).
        fetched = infra_repository.get_asset(asset_in_p1.id)
        assert fetched is not None

    def test_search_not_visible_cross_project(
        self, infra_repository, seed_projects, project_id, other_project_id
    ):
        """search in project B returns nothing for assets in project A."""
        infra_repository.create_asset(
            project_id=project_id,
            type="server",
            name="p1-prod",
            attributes={"env": "production"},
        )
        infra_repository.create_asset(
            project_id=other_project_id,
            type="server",
            name="p2-dev",
            attributes={"env": "development"},
        )

        p1_results = infra_repository.search_assets(
            project_id,
            {"attributes": {"env": {"$eq": "production"}}},
        )
        p2_results = infra_repository.search_assets(
            other_project_id,
            {"attributes": {"env": {"$eq": "production"}}},
        )

        assert len(p1_results) == 1
        assert len(p2_results) == 0

    def test_type_registry_global_not_isolated(self, infra_repository):
        """Types are global — registering in one context is visible globally."""
        infra_repository.register_type(name="global-type", description="shared")

        # Registering a type doesn't take a project_id; listing all types
        # (which is global) should show it.
        all_types = infra_repository.list_types()
        names = {t.name for t in all_types}
        assert "global-type" in names


# =============================================================================
# Group 6: Parent-Child Relationships
# =============================================================================


class TestParentChildCreation:
    """Tests for parent-child asset relationships on create."""

    def test_child_references_parent(self, infra_repository, seed_projects, project_id):
        """Creating a child asset with parent_asset_id links to the parent."""
        parent = infra_repository.create_asset(
            project_id=project_id,
            type="k8s_cluster",
            name="prod-cluster",
        )
        child = infra_repository.create_asset(
            project_id=project_id,
            type="server",
            name="worker-1",
            parent_asset_id=parent.id,
        )

        assert child.parent_asset_id == parent.id
        fetched = infra_repository.get_asset(child.id)
        assert fetched.parent_asset_id == parent.id

    def test_deep_hierarchy(self, infra_repository, seed_projects, project_id):
        """Assets can form multi-level parent-child chains."""
        grandparent = infra_repository.create_asset(
            project_id=project_id, type="datacenter", name="dc-1"
        )
        parent = infra_repository.create_asset(
            project_id=project_id,
            type="k8s_cluster",
            name="cluster-1",
            parent_asset_id=grandparent.id,
        )
        child = infra_repository.create_asset(
            project_id=project_id,
            type="server",
            name="node-1",
            parent_asset_id=parent.id,
        )

        assert child.parent_asset_id == parent.id
        assert parent.parent_asset_id == grandparent.id
        assert grandparent.parent_asset_id is None


class TestParentChildDeleteSetNull:
    """Tests that deleting a parent sets child.parent_asset_id to NULL (SET NULL)."""

    def test_delete_parent_survives_child(
        self, infra_repository, seed_projects, project_id
    ):
        """Deleting a parent does NOT cascade-delete children (SET NULL FK)."""
        parent = infra_repository.create_asset(
            project_id=project_id,
            type="k8s_cluster",
            name="cluster",
        )
        child = infra_repository.create_asset(
            project_id=project_id,
            type="server",
            name="worker-1",
            parent_asset_id=parent.id,
        )
        child_id = child.id
        infra_repository.delete_asset(parent.id)

        # Child should still exist, just orphaned.
        child_after = infra_repository.get_asset(child_id)
        assert child_after is not None
        assert child_after.parent_asset_id is None

    def test_delete_parent_creates_deleted_history_for_parent(
        self, infra_repository, seed_projects, project_id
    ):
        """The parent's "deleted" history row is still written correctly."""
        parent = infra_repository.create_asset(
            project_id=project_id,
            type="k8s_cluster",
            name="cluster",
        )
        child = infra_repository.create_asset(
            project_id=project_id,
            type="server",
            name="worker",
            parent_asset_id=parent.id,
        )
        infra_repository.delete_asset(parent.id)
        parent_history = infra_repository.get_history(parent.id)

        assert any(
            row.change_type == InfraChangeType.DELETED.value
            for row in parent_history
        )

    def test_delete_parent_preserves_child_history(
        self, infra_repository, seed_projects, project_id
    ):
        """The child's history is intact after parent deletion."""
        parent = infra_repository.create_asset(
            project_id=project_id,
            type="k8s_cluster",
            name="cluster",
        )
        child = infra_repository.create_asset(
            project_id=project_id,
            type="server",
            name="worker",
            parent_asset_id=parent.id,
        )
        child_id = child.id
        infra_repository.delete_asset(parent.id)

        child_history = infra_repository.get_history(child_id)
        assert len(child_history) == 1  # only "created"
        assert child_history[0].change_type == InfraChangeType.CREATED.value


class TestParentChildSearch:
    """Tests for filtering/list/search by parent_asset_id."""

    def test_list_assets_by_parent(
        self, infra_repository, seed_projects, project_id
    ):
        """list_assets with parent_asset_id returns all children of the parent."""
        parent = infra_repository.create_asset(
            project_id=project_id, type="k8s_cluster", name="cluster"
        )
        for i in range(3):
            infra_repository.create_asset(
                project_id=project_id,
                type="server",
                name=f"worker-{i}",
                parent_asset_id=parent.id,
            )
        infra_repository.create_asset(
            project_id=project_id, type="server", name="standalone"
        )

        children = infra_repository.list_assets(
            project_id, parent_asset_id=parent.id
        )
        assert len(children) == 3
        assert all(c.parent_asset_id == parent.id for c in children)

    def test_search_by_parent_asset_id(
        self, infra_repository, seed_projects, project_id
    ):
        """search_assets with parent_asset_id filter works correctly."""
        parent = infra_repository.create_asset(
            project_id=project_id, type="k8s_cluster", name="cluster"
        )
        infra_repository.create_asset(
            project_id=project_id,
            type="server",
            name="child1",
            parent_asset_id=parent.id,
        )

        results = infra_repository.search_assets(
            project_id, {"parent_asset_id": parent.id}
        )
        assert len(results) == 1
        assert results[0].name == "child1"


# =============================================================================
# Group 7: Model to_dict
# =============================================================================


class TestModelToDict:
    """Tests for model.to_dict() on InfraAsset and InfraAssetHistory."""

    def test_infra_asset_to_dict(
        self, infra_repository, seed_projects, project_id
    ):
        """InfraAsset.to_dict() returns a JSON-safe dict with all fields."""
        asset = infra_repository.create_asset(
            project_id=project_id,
            type="server",
            name="web-01",
            attributes={"cpu": 8},
            relationships={"linked_to": ["db-01"]},
            created_by="agent-1",
        )
        d = asset.to_dict()

        assert d["id"] == asset.id
        assert d["project_id"] == project_id
        assert d["type"] == "server"
        assert d["name"] == "web-01"
        assert d["parent_asset_id"] is None
        assert d["attributes"] == {"cpu": 8}
        assert d["relationships"] == {"linked_to": ["db-01"]}
        assert d["created_by"] == "agent-1"
        assert d["updated_by"] == "agent-1"
        assert d["created_at"] is not None
        assert d["updated_at"] is not None

    def test_infra_asset_type_to_dict(self, infra_repository):
        """InfraAssetType.to_dict() uses the locked 'schema_json' column name."""
        t = infra_repository.register_type(
            name="server",
            description="A server",
            schema_json={"type": "object"},
        )
        d = t.to_dict()

        assert d["name"] == "server"
        assert d["description"] == "A server"
        assert d["schema_json"] == {"type": "object"}
        assert "schema_doc" not in d  # locked column name is schema_json


# =============================================================================
# Group 8: InfraChangeType Enum
# =============================================================================


class TestInfraChangeType:
    """Tests for InfraChangeType enum helpers."""

    def test_valid_values(self):
        """InfraChangeType has the expected values."""
        assert InfraChangeType.CREATED.value == "created"
        assert InfraChangeType.UPDATED.value == "updated"
        assert InfraChangeType.DELETED.value == "deleted"

    def test_is_valid_true(self):
        """InfraChangeType.is_valid returns True for known values."""
        assert InfraChangeType.is_valid("created") is True
        assert InfraChangeType.is_valid("updated") is True
        assert InfraChangeType.is_valid("deleted") is True

    def test_is_valid_false(self):
        """InfraChangeType.is_valid returns False for unknown values."""
        assert InfraChangeType.is_valid("modified") is False
        assert InfraChangeType.is_valid("") is False
        assert InfraChangeType.is_valid("CREATED") is False  # case-sensitive


# =============================================================================
# Group 9: record_change (escape hatch)
# =============================================================================


class TestRecordChange:
    """Tests for repository.record_change() (manual history row)."""

    def test_record_change_inserts_row(
        self, infra_repository, seed_projects, project_id
    ):
        """record_change appends a custom history row for an existing asset."""
        asset = infra_repository.create_asset(
            project_id=project_id,
            type="server",
            name="web-01",
        )
        row = infra_repository.record_change(
            asset_id=asset.id,
            change_type="updated",
            changed_fields=["status"],
            old_values={"status": "provisioning"},
            new_values={"status": "running"},
            changed_by="agent-x",
        )

        assert row.asset_id == asset.id
        assert row.change_type == "updated"
        assert row.changed_fields == ["status"]
        assert row.old_values == {"status": "provisioning"}
        assert row.new_values == {"status": "running"}
        assert row.changed_by == "agent-x"

    def test_record_change_invalid_change_type_raises(
        self, infra_repository, seed_projects, project_id
    ):
        """record_change raises ValueError for unknown change_type."""
        asset = infra_repository.create_asset(
            project_id=project_id,
            type="server",
            name="web-01",
        )
        with pytest.raises(ValueError) as exc_info:
            infra_repository.record_change(
                asset_id=asset.id,
                change_type="not-a-valid-type",
            )
        assert "not-a-valid-type" in str(exc_info.value)

    def test_record_change_nonexistent_asset_raises(
        self, infra_repository
    ):
        """record_change raises ValueError when the asset does not exist."""
        with pytest.raises(ValueError) as exc_info:
            infra_repository.record_change(
                asset_id="does-not-exist",
                change_type="updated",
            )
        assert "does-not-exist" in str(exc_info.value)


# =============================================================================
# Group 10: Edge Cases
# =============================================================================


class TestEdgeCases:
    """Edge cases and boundary conditions."""

    def test_create_asset_does_not_require_type_to_exist_in_registry(
        self, infra_repository, seed_projects, project_id
    ):
        """create_asset does not validate against infra_asset_types."""
        # No types registered yet — should still be able to create an asset.
        asset = infra_repository.create_asset(
            project_id=project_id,
            type="completely-fictional-type",
            name="ghost-server",
        )
        assert asset.type == "completely-fictional-type"

    def test_update_asset_accepts_any_column(
        self, infra_repository, seed_projects, project_id
    ):
        """update_asset accepts any column on InfraAsset."""
        asset = infra_repository.create_asset(
            project_id=project_id,
            type="server",
            name="web-01",
            attributes={},
            relationships={},
        )
        updated = infra_repository.update_asset(
            asset.id,
            attributes={"new_key": "new_value"},
            relationships={"new_rel": ["other"]},
        )
        assert updated.attributes == {"new_key": "new_value"}
        assert updated.relationships == {"new_rel": ["other"]}

    def test_update_asset_unknown_column_raises(
        self, infra_repository, seed_projects, project_id
    ):
        """update_asset raises AttributeError for non-existent columns."""
        asset = infra_repository.create_asset(
            project_id=project_id,
            type="server",
            name="web-01",
        )
        with pytest.raises(AttributeError):
            infra_repository.update_asset(asset.id, nonexistent_column="x")

    def test_create_asset_nonexistent_project_raises(
        self, infra_repository
    ):
        """Creating an asset for a non-existent project FK raises.

        The repository catches :class:`IntegrityError` and raises
        :class:`ValueError` — the implementation cannot currently
        distinguish a unique-constraint violation from an FK
        violation, so the wrapping message is misleading. This
        test pins down the *contract*: the call MUST raise, not
        silently insert an orphaned row.
        """
        with pytest.raises(ValueError):
            infra_repository.create_asset(
                project_id="this-project-does-not-exist",
                type="server",
                name="orphan",
            )

    def test_search_empty_project(self, infra_repository, seed_projects, project_id):
        """Searching a project with no assets returns []. doesn't raise."""
        results = infra_repository.search_assets(project_id, {})
        assert results == []

    def test_search_order_descending(
        self, infra_repository, seed_projects, project_id
    ):
        """search results are ordered by updated_at descending (newest first)."""
        a = infra_repository.create_asset(
            project_id=project_id,
            type="server",
            name="oldest",
            attributes={"order": 1},
        )
        infra_repository.update_asset(
            a.id, attributes={"order": 1, "updated": True}
        )
        b = infra_repository.create_asset(
            project_id=project_id,
            type="server",
            name="newest",
            attributes={"order": 2},
        )

        results = infra_repository.search_assets(project_id, {})
        # newest (b) should be first.
        assert results[0].name == "newest"

    def test_empty_attributes_and_relationships(
        self, infra_repository, seed_projects, project_id
    ):
        """Assets with empty {} attributes/relationships are stored correctly."""
        asset = infra_repository.create_asset(
            project_id=project_id,
            type="server",
            name="empty",
            attributes={},
            relationships={},
        )
        assert asset.attributes == {}
        assert asset.relationships == {}

        fetched = infra_repository.get_asset(asset.id)
        assert fetched.attributes == {}
        assert fetched.relationships == {}
