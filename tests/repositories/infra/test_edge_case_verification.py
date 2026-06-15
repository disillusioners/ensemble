"""Independent edge-case verification suite for the infra asset storage feature.

This is a *second-opinion* test file (separate from the
``test_infra_repository.py`` already shipping with the feature) that
independently exercises the critical behaviors called out in the
Phase-1 design doc and the related code-review tickets.

Each scenario below maps 1:1 to a numbered requirement in the
verification request:

    1. Migration File Verification      (file content)
    2. Factory Wiring Verification     (factory function)
    3. Boolean Attributes              (the "True vs 'true'" bug)
    4. Pre-Update Snapshot Correctness (snapshot-before-write)
    5. parent_asset_id=None Filtering  (roots of the hierarchy)
    6. Project Isolation               (no cross-project leakage)
    7. Pagination                      (limit / offset)
    8. Type Registry                   (global, no project_id)
    9. All 9 Search Operators          (mongo-style operator dicts)
   10. History on Delete               (audit row survives the row)

Tests run against an in-memory SQLite database via the shared
``engine`` / ``infra_repository`` / ``seed_projects`` fixtures from
``tests/repositories/infra/conftest.py``. The factory-wiring test
uses an isolated ``create_engine`` so it does not need a shared
``projects`` table on its freshly-created schema.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from daemon.repositories.factory import create_infra_repository
from daemon.repositories.infra import (
    InfraAsset,
    InfraAssetHistory,
    InfraAssetType,
    InfraChangeType,
    SQLModelInfraRepository,
)


# Path to the migration file. Uses ``pathlib`` to keep the test
# independent of CWD, matching the pattern in
# ``tests/integration/test_migration.py``.
# Test file lives at <repo>/tests/repositories/infra/, so the
# repo root is three parents up.
REPO_ROOT = Path(__file__).resolve().parents[3]
MIGRATION_DIR = REPO_ROOT / "daemon" / "migrations" / "versions"
INFRA_MIGRATION_FILE = (
    MIGRATION_DIR / "20260616_000001_create_infra_asset_storage_tables.sql"
)


# ============================================================
# 1. Migration File Verification
# ============================================================


class TestMigrationFile:
    """Verify the migration SQL file is present and well-formed.

    These checks are pure file-system / regex assertions, not
    database-level. They guard against the most common production
    failure modes: missing file, table dropped, indexes forgotten.
    """

    def test_migration_file_exists(self):
        assert INFRA_MIGRATION_FILE.exists(), (
            f"Migration file missing: {INFRA_MIGRATION_FILE}"
        )
        assert INFRA_MIGRATION_FILE.is_file()

    def test_migration_creates_all_three_tables(self):
        content = INFRA_MIGRATION_FILE.read_text()
        # Each CREATE TABLE must appear at least once with the
        # expected fully-qualified / unqualified name.
        assert "CREATE TABLE IF NOT EXISTS infra_asset_types" in content
        assert "CREATE TABLE IF NOT EXISTS infra_assets" in content
        assert "CREATE TABLE IF NOT EXISTS infra_asset_history" in content

    def test_migration_creates_proper_indexes(self):
        content = INFRA_MIGRATION_FILE.read_text()
        # The migration must declare the project/type/parent
        # indexes and the UNIQUE(project_id, type, name) index.
        # We assert the index names rather than the literal CREATE
        # statement so a future re-style of the SQL does not break
        # the test for trivial reasons.
        expected_indexes = {
            "ix_infra_asset_types_updated_at",
            "ix_infra_assets_project_id",
            "ix_infra_assets_type",
            "ix_infra_assets_parent_asset_id",
            "ix_infra_assets_updated_at",
            "ix_infra_assets_name",
            "uq_infra_assets_project_type_name",
            "ix_infra_asset_history_asset_id",
            "ix_infra_asset_history_project_id",
            "ix_infra_asset_history_timestamp",
            "ix_infra_asset_history_change_type",
        }
        for idx_name in expected_indexes:
            assert f"CREATE INDEX IF NOT EXISTS {idx_name}" in content or (
                f"CREATE UNIQUE INDEX IF NOT EXISTS {idx_name}" in content
            ), f"Missing index declaration for {idx_name}"

    def test_migration_uses_correct_fk_on_delete(self):
        """The migration must match the model: SET NULL for history, CASCADE for project.

        These are the two FK semantics that have been the source
        of audit-trail bugs in the past. Catching a regression at
        the file level (rather than discovering it at runtime via
        a missing history row) is the whole point of this test.
        """
        content = INFRA_MIGRATION_FILE.read_text()
        # history.asset_id → infra_assets.id ON DELETE SET NULL
        history_fk_pattern = re.compile(
            r"infra_asset_history[\s\S]*?asset_id\s+TEXT\s+REFERENCES\s+infra_assets\(id\)\s+ON\s+DELETE\s+SET\s+NULL",
            re.IGNORECASE,
        )
        assert history_fk_pattern.search(content), (
            "infra_asset_history.asset_id must ON DELETE SET NULL "
            "(not CASCADE) so the audit row survives the asset's removal"
        )
        # infra_assets.project_id → projects.project_id ON DELETE CASCADE
        project_fk_pattern = re.compile(
            r"infra_assets[\s\S]*?project_id\s+TEXT\s+NOT\s+NULL\s+REFERENCES\s+projects\(project_id\)\s+ON\s+DELETE\s+CASCADE",
            re.IGNORECASE,
        )
        assert project_fk_pattern.search(content), (
            "infra_assets.project_id must ON DELETE CASCADE so a "
            "project wipe cleans up its assets"
        )


# ============================================================
# 2. Factory Wiring Verification
# ============================================================


class TestFactoryWiring:
    """Verify ``create_infra_repository`` is importable and behaves as documented.

    The factory is the canonical entry point for the rest of the
    daemon — the API layer instantiates the repository through it.
    A wrong return type or a broken ``create_tables`` flag would
    surface here, not in production.
    """

    def test_create_infra_repository_is_importable(self):
        # Importing here (not at module top) so that a circular-
        # import error in factory.py shows up against this test
        # name, not against a downstream test.
        from daemon.repositories.factory import create_infra_repository as fn

        assert callable(fn)

    def test_factory_returns_correct_repository_type(self):
        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        repo = create_infra_repository(engine=engine, create_tables=True)
        assert isinstance(repo, SQLModelInfraRepository)
        # Defensive: the factory must bind the same engine it was
        # given — a swap (e.g. ``SQLModelInfraRepository(create_engine(...))``)
        # would silently break cross-table consistency.
        assert repo.engine is engine
        engine.dispose()

    def test_factory_creates_tables_when_flag_true(self):
        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        # Sanity: no infra tables before the factory call.
        insp = inspect(engine)
        assert "infra_assets" not in insp.get_table_names()

        create_infra_repository(engine=engine, create_tables=True)

        insp = inspect(engine)
        tables = set(insp.get_table_names())
        for required in (
            "infra_assets",
            "infra_asset_types",
            "infra_asset_history",
        ):
            assert required in tables, f"Table {required!r} not created by factory"
        engine.dispose()

    def test_factory_create_tables_false_skips_schema(self):
        """With ``create_tables=False`` the factory must NOT call
        ``SQLModel.metadata.create_all`` — useful for tests that
        bring up their own schema.
        """
        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        repo = create_infra_repository(engine=engine, create_tables=False)
        assert isinstance(repo, SQLModelInfraRepository)
        # The tables must NOT be created when the flag is False.
        insp = inspect(engine)
        assert "infra_assets" not in insp.get_table_names()
        engine.dispose()

    def test_factory_requires_engine_or_config(self):
        with pytest.raises(ValueError, match="Either config or engine"):
            create_infra_repository(config=None, engine=None, create_tables=False)


# ============================================================
# 3. Boolean Attributes (the "True vs 'true'" bug)
# ============================================================


class TestBooleanAttributes:
    """The dialect-specific boolean encoding bug.

    PostgreSQL returns ``"true"`` / ``"false"`` from ``->>``;
    SQLite returns ``1`` / ``0`` from ``json_extract``. The
    repository's ``_json_eq_predicate`` has dialect branches
    precisely to handle this — but the bug has regressed in the
    past. This test pins down the contract on SQLite (the
    dev / CI backend).
    """

    def test_eq_true_finds_asset_with_true(
        self, infra_repository, seed_projects, project_id
    ):
        active = infra_repository.create_asset(
            project_id=project_id,
            type="server",
            name="active-web",
            attributes={"active": True, "count": 5},
        )
        inactive = infra_repository.create_asset(
            project_id=project_id,
            type="server",
            name="inactive-web",
            attributes={"active": False},
        )
        results = infra_repository.search_assets(
            project_id, {"attributes": {"active": {"$eq": True}}}
        )
        ids = {a.id for a in results}
        assert active.id in ids
        assert inactive.id not in ids

    def test_eq_false_finds_only_false(
        self, infra_repository, seed_projects, project_id
    ):
        active = infra_repository.create_asset(
            project_id=project_id,
            type="server",
            name="active-1",
            attributes={"active": True},
        )
        inactive = infra_repository.create_asset(
            project_id=project_id,
            type="server",
            name="inactive-1",
            attributes={"active": False},
        )
        results = infra_repository.search_assets(
            project_id, {"attributes": {"active": {"$eq": False}}}
        )
        ids = {a.id for a in results}
        assert inactive.id in ids
        assert active.id not in ids

    def test_ne_true_excludes_true(
        self, infra_repository, seed_projects, project_id
    ):
        active = infra_repository.create_asset(
            project_id=project_id,
            type="server",
            name="active-2",
            attributes={"active": True},
        )
        inactive = infra_repository.create_asset(
            project_id=project_id,
            type="server",
            name="inactive-2",
            attributes={"active": False},
        )
        results = infra_repository.search_assets(
            project_id, {"attributes": {"active": {"$ne": True}}}
        )
        ids = {a.id for a in results}
        assert active.id not in ids
        assert inactive.id in ids

    def test_two_assets_isolate_by_boolean(
        self, infra_repository, seed_projects, project_id
    ):
        """Same key, different values, search must isolate.

        Guards against the regression where SQLite's
        ``json_extract`` returns ``1`` for ``True`` but the
        comparison ran against the *string* ``"True"``,
        producing an empty result for every search.
        """
        a = infra_repository.create_asset(
            project_id=project_id,
            type="server",
            name="a-true",
            attributes={"active": True},
        )
        b = infra_repository.create_asset(
            project_id=project_id,
            type="server",
            name="b-false",
            attributes={"active": False},
        )
        # Sanity: both assets exist.
        assert infra_repository.get_asset(a.id) is not None
        assert infra_repository.get_asset(b.id) is not None

        true_results = infra_repository.search_assets(
            project_id, {"attributes": {"active": True}}
        )
        false_results = infra_repository.search_assets(
            project_id, {"attributes": {"active": False}}
        )

        # No overlap, both rows present, total accounts for both.
        true_ids = {r.id for r in true_results}
        false_ids = {r.id for r in false_results}
        assert true_ids == {a.id}
        assert false_ids == {b.id}
        assert true_ids & false_ids == set()


# ============================================================
# 4. Pre-Update Snapshot Correctness (CRITICAL)
# ============================================================


class TestPreUpdateSnapshot:
    """The "snapshot before write" pattern.

    A history row's ``snapshot`` column must capture the asset
    state *before* the update applied. The repository implements
    this by calling ``to_dict()`` *before* the mutation loop
    runs; this test pins that down so a future refactor that
    moves the snapshot capture to the wrong side of the
    ``setattr`` would break this test, not production.
    """

    def test_update_history_snapshot_contains_old_values(
        self, infra_repository, seed_projects, project_id
    ):
        original = infra_repository.create_asset(
            project_id=project_id,
            type="server",
            name="v1-server",
            attributes={"name": "v1", "count": 10},
        )
        infra_repository.update_asset(
            original.id,
            attributes={"name": "v2", "count": 20},
        )
        history = infra_repository.get_history(original.id)
        # Newest first → the "updated" row is at index 0.
        updated_rows = [h for h in history if h.change_type == "updated"]
        assert len(updated_rows) == 1
        row = updated_rows[0]

        # The snapshot must reflect the *pre*-update state, not
        # the new state. This is the "snapshot before write"
        # contract.
        assert row.snapshot is not None
        assert row.snapshot["attributes"]["name"] == "v1"
        assert row.snapshot["attributes"]["count"] == 10
        # And the diff metadata must also be correct.
        assert row.old_values == {"attributes": {"name": "v1", "count": 10}}
        assert row.new_values == {"attributes": {"name": "v2", "count": 20}}
        assert "attributes" in row.changed_fields


# ============================================================
# 5. parent_asset_id=None Returns Only Unparented Assets
# ============================================================


class TestParentAssetIdNoneFiltering:
    """``list_assets(parent_asset_id=None)`` returns only roots.

    The default of the ``parent_asset_id`` parameter is documented
    as "roots of the hierarchy" (``WHERE parent_asset_id IS NULL``).
    Callers wanting the full set use ``search_assets`` with no
    parent filter.
    """

    def test_list_assets_unparented_returns_only_roots(
        self, infra_repository, seed_projects, project_id
    ):
        a = infra_repository.create_asset(
            project_id=project_id,
            type="datacenter",
            name="dc-a",
        )
        b = infra_repository.create_asset(
            project_id=project_id,
            type="server",
            name="server-b",
            parent_asset_id=a.id,
        )
        c = infra_repository.create_asset(
            project_id=project_id,
            type="datacenter",
            name="dc-c",
        )
        unparented = infra_repository.list_assets(project_id)
        ids = {asset.id for asset in unparented}
        assert a.id in ids
        assert c.id in ids
        assert b.id not in ids, (
            "list_assets() (parent_asset_id=None default) must return "
            "only unparented assets; B has a parent and must be excluded"
        )


# ============================================================
# 6. Project Isolation
# ============================================================


class TestProjectIsolation:
    """Assets are project-scoped: project A cannot see project B's data.

    These checks are spelled out in the locked design doc as a
    non-negotiable. A regression here is a security issue, not a
    cosmetic one.
    """

    def test_list_does_not_include_other_project(
        self, infra_repository, seed_projects, project_id, other_project_id
    ):
        a_only = infra_repository.create_asset(
            project_id=project_id,
            type="server",
            name="a-only",
        )
        b_only = infra_repository.create_asset(
            project_id=other_project_id,
            type="server",
            name="b-only",
        )
        listed = infra_repository.list_assets(project_id)
        ids = {asset.id for asset in listed}
        assert a_only.id in ids
        assert b_only.id not in ids

    def test_get_asset_cross_project_returns_none(
        self, infra_repository, seed_projects, project_id, other_project_id
    ):
        b_only = infra_repository.create_asset(
            project_id=other_project_id,
            type="server",
            name="b-only-2",
        )
        # Calling get_asset from project A's context must hide the asset.
        assert infra_repository.get_asset(b_only.id, project_id=project_id) is None
        # But it must still be findable from its own project context.
        assert infra_repository.get_asset(b_only.id, project_id=other_project_id) is not None

    def test_search_does_not_include_other_project(
        self, infra_repository, seed_projects, project_id, other_project_id
    ):
        infra_repository.create_asset(
            project_id=other_project_id,
            type="server",
            name="hidden-server",
            attributes={"env": "production"},
        )
        # Even an attribute filter must not leak cross-project rows.
        results = infra_repository.search_assets(
            project_id,
            {"attributes": {"env": "production"}},
        )
        assert results == []


# ============================================================
# 7. Pagination (limit / offset)
# ============================================================


class TestPagination:
    """``limit`` / ``offset`` on ``list_assets`` must compose correctly."""

    def test_limit_offset_three_pages(
        self, infra_repository, seed_projects, project_id
    ):
        # Create 5 assets in deterministic order. We use distinct
        # names so the ``updated_at DESC`` ordering is stable on
        # the test clock — created_at == updated_at on create.
        names = [f"asset-{i}" for i in range(5)]
        for name in names:
            infra_repository.create_asset(
                project_id=project_id,
                type="server",
                name=name,
            )
        # All five are unparented, so list_assets() returns them.
        page1 = infra_repository.list_assets(project_id, limit=2, offset=0)
        page2 = infra_repository.list_assets(project_id, limit=2, offset=2)
        page3 = infra_repository.list_assets(project_id, limit=2, offset=4)

        assert len(page1) == 2
        assert len(page2) == 2
        assert len(page3) == 1

        # The three pages must be disjoint and union to the full
        # set of 5 assets — no duplicates, no missing.
        all_ids = {a.id for page in (page1, page2, page3) for a in page}
        assert len(all_ids) == 5


# ============================================================
# 8. Type Registry (Global — No project_id)
# ============================================================


class TestTypeRegistryIsGlobal:
    """The ``infra_asset_types`` table is intentionally project-less.

    A regression that adds a project_id (or filters types by
    project) would break every cross-project DevOps tool that
    resolves a type from the registry.
    """

    def test_type_visible_across_projects(
        self, infra_repository, seed_projects
    ):
        repo_a = infra_repository  # bound to the shared test engine
        # Register a type without a project_id (the registry has
        # no such column by design).
        repo_a.register_type(
            name="custom-server",
            description="A custom server type",
            schema_json={"properties": {"cpu": {"type": "integer"}}},
        )
        # Fetch from any project context — get_type does not
        # take a project_id, by design.
        got = infra_repository.get_type("custom-server")
        assert got is not None
        assert got.description == "A custom server type"
        assert got.schema_doc == {"properties": {"cpu": {"type": "integer"}}}

        # And the type must be listed by list_types (which is also
        # project-less).
        names = {t.name for t in infra_repository.list_types()}
        assert "custom-server" in names

    def test_type_registry_upsert_is_idempotent(
        self, infra_repository
    ):
        infra_repository.register_type(name="server", description="v1")
        infra_repository.register_type(name="server", description="v2")
        listed = infra_repository.list_types()
        servers = [t for t in listed if t.name == "server"]
        assert len(servers) == 1, "register_type must upsert, not duplicate"
        assert servers[0].description == "v2"


# ============================================================
# 9. All 9 Search Operators
# ============================================================


class TestAllNineSearchOperators:
    """The full mongo-style operator vocabulary, verified end-to-end.

    Each operator is exercised in isolation against a known
    fixture set so a regression on one does not mask a regression
    on another.
    """

    def _seed_numeric_range(
        self, infra_repository, project_id
    ) -> dict[str, InfraAsset]:
        """Seed assets with attributes that span the comparison space."""
        return {
            "small": infra_repository.create_asset(
                project_id=project_id,
                type="server",
                name="op-num-small",
                attributes={"cpus": 4, "region": "us-east-1a"},
            ),
            "medium": infra_repository.create_asset(
                project_id=project_id,
                type="server",
                name="op-num-medium",
                attributes={"cpus": 8, "region": "us-east-1b"},
            ),
            "large": infra_repository.create_asset(
                project_id=project_id,
                type="server",
                name="op-num-large",
                attributes={"cpus": 16, "region": "eu-west-1"},
            ),
        }

    def test_eq_operator(
        self, infra_repository, seed_projects, project_id
    ):
        assets = self._seed_numeric_range(infra_repository, project_id)
        results = infra_repository.search_assets(
            project_id, {"attributes": {"cpus": {"$eq": 8}}}
        )
        ids = {a.id for a in results}
        assert ids == {assets["medium"].id}

    def test_ne_operator(
        self, infra_repository, seed_projects, project_id
    ):
        assets = self._seed_numeric_range(infra_repository, project_id)
        results = infra_repository.search_assets(
            project_id, {"attributes": {"cpus": {"$ne": 8}}}
        )
        ids = {a.id for a in results}
        assert assets["small"].id in ids
        assert assets["large"].id in ids
        assert assets["medium"].id not in ids

    def test_gt_operator(
        self, infra_repository, seed_projects, project_id
    ):
        assets = self._seed_numeric_range(infra_repository, project_id)
        results = infra_repository.search_assets(
            project_id, {"attributes": {"cpus": {"$gt": 8}}}
        )
        ids = {a.id for a in results}
        assert ids == {assets["large"].id}

    def test_gte_operator(
        self, infra_repository, seed_projects, project_id
    ):
        assets = self._seed_numeric_range(infra_repository, project_id)
        results = infra_repository.search_assets(
            project_id, {"attributes": {"cpus": {"$gte": 8}}}
        )
        ids = {a.id for a in results}
        assert assets["medium"].id in ids
        assert assets["large"].id in ids
        assert assets["small"].id not in ids

    def test_lt_operator(
        self, infra_repository, seed_projects, project_id
    ):
        assets = self._seed_numeric_range(infra_repository, project_id)
        results = infra_repository.search_assets(
            project_id, {"attributes": {"cpus": {"$lt": 8}}}
        )
        ids = {a.id for a in results}
        assert ids == {assets["small"].id}

    def test_lte_operator(
        self, infra_repository, seed_projects, project_id
    ):
        assets = self._seed_numeric_range(infra_repository, project_id)
        results = infra_repository.search_assets(
            project_id, {"attributes": {"cpus": {"$lte": 8}}}
        )
        ids = {a.id for a in results}
        assert assets["small"].id in ids
        assert assets["medium"].id in ids
        assert assets["large"].id not in ids

    def test_contains_operator(
        self, infra_repository, seed_projects, project_id
    ):
        assets = self._seed_numeric_range(infra_repository, project_id)
        results = infra_repository.search_assets(
            project_id, {"attributes": {"region": {"$contains": "east"}}}
        )
        ids = {a.id for a in results}
        assert assets["small"].id in ids
        assert assets["medium"].id in ids
        assert assets["large"].id not in ids

    def test_exists_true_operator(
        self, infra_repository, seed_projects, project_id
    ):
        present = infra_repository.create_asset(
            project_id=project_id,
            type="server",
            name="has-cpus",
            attributes={"cpus": 4},
        )
        infra_repository.create_asset(
            project_id=project_id,
            type="server",
            name="no-cpus",
            attributes={"memory_gb": 16},
        )
        results = infra_repository.search_assets(
            project_id, {"attributes": {"cpus": {"$exists": True}}}
        )
        ids = {a.id for a in results}
        assert present.id in ids
        assert all(a.id != present.id or "cpus" in a.attributes for a in results)

    def test_in_operator(
        self, infra_repository, seed_projects, project_id
    ):
        assets = self._seed_numeric_range(infra_repository, project_id)
        results = infra_repository.search_assets(
            project_id, {"attributes": {"cpus": {"$in": [4, 16]}}}
        )
        ids = {a.id for a in results}
        assert assets["small"].id in ids
        assert assets["large"].id in ids
        assert assets["medium"].id not in ids


# ============================================================
# 10. History on Delete
# ============================================================


class TestHistoryOnDelete:
    """Deleting an asset must record a 'deleted' history row with the full snapshot.

    The ``deleted`` row is the last surviving link to the asset
    after the row is removed — without it, the audit trail has a
    gap exactly where it matters most.
    """

    def test_deleted_history_row_exists_with_full_snapshot(
        self, infra_repository, seed_projects, project_id
    ):
        asset = infra_repository.create_asset(
            project_id=project_id,
            type="server",
            name="to-be-deleted",
            attributes={"env": "staging", "cpus": 4},
        )
        asset_id = asset.id
        deleted = infra_repository.delete_asset(asset_id)
        assert deleted is True

        history = infra_repository.get_history(asset_id)
        deleted_rows = [h for h in history if h.change_type == "deleted"]
        assert len(deleted_rows) == 1
        row = deleted_rows[0]

        # The snapshot must contain the full asset state at delete time.
        assert row.snapshot is not None
        assert row.snapshot["id"] == asset_id
        assert row.snapshot["name"] == "to-be-deleted"
        assert row.snapshot["attributes"] == {"env": "staging", "cpus": 4}

        # ``asset_id`` is NULL after the row's removal (ON DELETE SET NULL).
        assert row.asset_id is None
