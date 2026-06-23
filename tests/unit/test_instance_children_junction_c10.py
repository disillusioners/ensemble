"""Tests for C10 — Instance.children JSON column removed; instance_hierarchy is canonical.

After Phase 4 the ``Instance.children`` JSON column was removed from the
model entirely (it had 4 RMW race sites and was overridden on every read
by ``_enrich_instance()`` / ``list_child_ids()`` which loads from
``instance_hierarchy``). The canonical working set is the junction table.

These tests verify:
  * Spawn writes to ``instance_hierarchy`` (not the JSON column).
  * ``list_child_ids()`` returns the junction-table children.
  * Completion paths DELETE from ``instance_hierarchy`` (junction cleanup).
  * The ``Instance`` model exposes NO ``children`` attribute (column is
    gone, not just unwritten).
  * The end-to-end spawn→complete cycle uses only the junction table.
"""

from __future__ import annotations

import pytest
from sqlmodel import Session, SQLModel, create_engine, select, text
from sqlalchemy.engine import Engine

from daemon.repositories.instance.models import Instance, InstanceHierarchy
from daemon.repositories.instance.repository import SQLModelInstanceRepository


# =============================================================================
# FIXTURES
# =============================================================================


@pytest.fixture
def engine() -> Engine:
    """In-memory SQLite engine with full schema."""
    eng = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(eng)
    return eng


@pytest.fixture
def repo(engine: Engine) -> SQLModelInstanceRepository:
    """Real repository against the in-memory engine."""
    return SQLModelInstanceRepository(engine)


def _hierarchy_child_ids(engine: Engine, parent_id: str) -> list[str]:
    """Read raw rows from the ``instance_hierarchy`` table (canonical source)."""
    with Session(engine) as session:
        return list(
            session.exec(
                select(InstanceHierarchy.child_id).where(
                    InstanceHierarchy.parent_id == parent_id
                )
            )
        )


# =============================================================================
# Tests
# =============================================================================


class TestSpawnWritesJunctionTable:
    """Spawning a child must insert into ``instance_hierarchy`` (canonical source)."""

    def test_spawn_inserts_into_instance_hierarchy(self, repo, engine):
        """``create(parent_id=X)`` must add a row to ``instance_hierarchy``."""
        repo.create(
            instance_id="parent-1",
            agent_id="leader",
            agent_dir="./agents/leader",
        )
        repo.create(
            instance_id="child-1",
            agent_id="coder",
            agent_dir="./agents/coder",
            parent_id="parent-1",
        )

        children = _hierarchy_child_ids(engine, "parent-1")
        assert children == ["child-1"], (
            f"Expected one row in instance_hierarchy for parent-1 → child-1, "
            f"got {children}"
        )

    def test_instance_model_has_no_children_attribute(self, repo, engine):
        """Phase 4: the ``Instance.children`` JSON column was dropped from the
        model. Verify the column is gone (not just unwritten)."""
        repo.create(
            instance_id="parent-2",
            agent_id="leader",
            agent_dir="./agents/leader",
        )
        with Session(engine) as session:
            raw_parent = session.get(Instance, "parent-2")
        assert raw_parent is not None
        # The deprecated JSON column must not exist as an attribute.
        assert not hasattr(raw_parent, "children"), (
            "Instance.children column should be removed in Phase 4"
        )

    def test_multiple_children_all_appear_in_junction_table(self, repo, engine):
        """Spawning 3 children of the same parent should produce 3 junction rows."""
        repo.create(
            instance_id="parent-3",
            agent_id="leader",
            agent_dir="./agents/leader",
        )
        for cid in ("child-3a", "child-3b", "child-3c"):
            repo.create(
                instance_id=cid,
                agent_id="coder",
                agent_dir="./agents/coder",
                parent_id="parent-3",
            )

        children = sorted(_hierarchy_child_ids(engine, "parent-3"))
        assert children == ["child-3a", "child-3b", "child-3c"]

    def test_spawn_without_parent_does_not_create_hierarchy_row(self, repo, engine):
        """Root instances (parent_id=None) must not appear in any junction row."""
        repo.create(
            instance_id="root",
            agent_id="leader",
            agent_dir="./agents/leader",
        )

        with Session(engine) as session:
            count = len(session.exec(select(InstanceHierarchy)).all())
        assert count == 0, "Root instances must not create instance_hierarchy rows"


class TestListChildIdsReadsJunctionTable:
    """``list_child_ids()`` must read from the junction table (the canonical
    source after Phase 4 dropped the JSON column)."""

    def test_list_child_ids_returns_junction_children(self, repo, engine):
        """``repo.list_child_ids(parent)`` returns junction-table children."""
        repo.create(
            instance_id="parent-4",
            agent_id="leader",
            agent_dir="./agents/leader",
        )
        repo.create(
            instance_id="child-4a",
            agent_id="coder",
            agent_dir="./agents/coder",
            parent_id="parent-4",
        )
        repo.create(
            instance_id="child-4b",
            agent_id="coder",
            agent_dir="./agents/coder",
            parent_id="parent-4",
        )

        children = sorted(repo.list_child_ids("parent-4"))
        assert children == ["child-4a", "child-4b"], (
            f"list_child_ids must return junction-table children, got {children}"
        )

    def test_list_child_ids_returns_empty_when_no_children(self, repo, engine):
        """An instance with no children returns ``[]`` from the junction table."""
        repo.create(
            instance_id="lonely",
            agent_id="leader",
            agent_dir="./agents/leader",
        )

        assert repo.list_child_ids("lonely") == []


class TestCompletionDeletesJunctionRow:
    """Completion paths must DELETE the ``instance_hierarchy`` row (the canonical
    cleanup). The completion site lives in ``child_reports.py`` /
    ``error_reporting.py`` and runs ``DELETE FROM instance_hierarchy WHERE child_id = :child_id``.
    Here we directly exercise that DELETE to verify the junction table supports
    cleanup and downstream reads return ``[]``."""

    def test_delete_from_junction_removes_child_from_list_child_ids(self, repo, engine):
        """Simulate completion: DELETE the junction row, then re-read children.
        The junction table no longer includes the completed child."""
        repo.create(
            instance_id="parent-6",
            agent_id="leader",
            agent_dir="./agents/leader",
        )
        repo.create(
            instance_id="child-6",
            agent_id="coder",
            agent_dir="./agents/coder",
            parent_id="parent-6",
        )

        # Before deletion: child visible
        before = repo.list_child_ids("parent-6")
        assert before == ["child-6"]

        # Simulate completion: this is the exact SQL the completion paths run
        # (daemon/services/child_reports.py:603-605, 1292-1294,
        # daemon/services/error_reporting.py:252-256).
        with Session(engine) as session:
            session.execute(
                text("DELETE FROM instance_hierarchy WHERE child_id = :child_id"),
                {"child_id": "child-6"},
            )
            session.commit()

        # After deletion: child gone from list_child_ids
        after = repo.list_child_ids("parent-6")
        assert after == [], (
            f"Expected children=[] after junction row deletion, got {after}"
        )

    def test_delete_one_child_keeps_others(self, repo, engine):
        """Deleting one junction row must not affect siblings."""
        repo.create(
            instance_id="parent-7",
            agent_id="leader",
            agent_dir="./agents/leader",
        )
        for cid in ("child-7a", "child-7b"):
            repo.create(
                instance_id=cid,
                agent_id="coder",
                agent_dir="./agents/coder",
                parent_id="parent-7",
            )

        with Session(engine) as session:
            session.execute(
                text("DELETE FROM instance_hierarchy WHERE child_id = :child_id"),
                {"child_id": "child-7a"},
            )
            session.commit()

        assert repo.list_child_ids("parent-7") == ["child-7b"]

    def test_double_delete_is_safe(self, repo, engine):
        """Idempotent DELETE: removing a junction row twice must not raise."""
        repo.create(
            instance_id="parent-8",
            agent_id="leader",
            agent_dir="./agents/leader",
        )
        repo.create(
            instance_id="child-8",
            agent_id="coder",
            agent_dir="./agents/coder",
            parent_id="parent-8",
        )

        delete_sql = text(
            "DELETE FROM instance_hierarchy WHERE child_id = :child_id"
        )
        with Session(engine) as session:
            session.execute(delete_sql, {"child_id": "child-8"})
            session.commit()
            # Second delete — must succeed (0 rows affected, no error).
            session.execute(delete_sql, {"child_id": "child-8"})
            session.commit()

        assert repo.list_child_ids("parent-8") == []


class TestEndToEndSpawnThenComplete:
    """Full spawn → verify → complete → verify cycle."""

    def test_full_cycle_uses_only_junction_table(self, repo, engine):
        """The end-to-end happy path:
        1. Spawn parent (no parent)
        2. Spawn child → junction row created
        3. list_child_ids() returns [child]
        4. Complete child → junction row deleted
        5. list_child_ids() returns []
        """
        # 1. Spawn parent
        repo.create(
            instance_id="e2e-parent",
            agent_id="leader",
            agent_dir="./agents/leader",
        )

        # 2. Spawn child
        repo.create(
            instance_id="e2e-child",
            agent_id="coder",
            agent_dir="./agents/coder",
            parent_id="e2e-parent",
        )

        # Junction row exists
        assert _hierarchy_child_ids(engine, "e2e-parent") == ["e2e-child"]

        # 3. list_child_ids sees the child
        assert repo.list_child_ids("e2e-parent") == ["e2e-child"]

        # 4. Simulate completion DELETE
        with Session(engine) as session:
            session.execute(
                text("DELETE FROM instance_hierarchy WHERE child_id = :child_id"),
                {"child_id": "e2e-child"},
            )
            session.commit()

        # 5. list_child_ids no longer sees the child
        assert repo.list_child_ids("e2e-parent") == []
