"""Tests for C10 — drop Instance.children JSON cache column writes.

The ``Instance.children`` JSON column is **doubly broken**:
  1. It has read-modify-write (RMW) race conditions at 4 sites.
  2. It is overridden on every read by ``_enrich_instance()``, which loads
     children from the ``instance_hierarchy`` junction table.

Writes to the JSON column are persistently useless (no code ever reads the
corrupted value), so C10 removed all 4 write sites:
  - ``daemon/services/instance_lifecycle.py`` (spawn)
  - ``daemon/services/child_reports.py`` (completion path 1)
  - ``daemon/services/child_reports.py`` (completion path 2)
  - ``daemon/services/error_reporting.py`` (error path)

These tests verify the junction table is the canonical source end-to-end:
spawn writes to ``instance_hierarchy`` (not the JSON column), reads via
``_load_children``/``_enrich_instance`` see the child, and the completion
DELETE removes the row.
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


def _raw_instance_row(engine: Engine, instance_id: str) -> Instance | None:
    """Fetch a raw Instance row (no enrichment)."""
    with Session(engine) as session:
        return session.get(Instance, instance_id)


# =============================================================================
# Tests
# =============================================================================


class TestSpawnWritesJunctionTable:
    """Spawning a child must insert into ``instance_hierarchy`` (not the JSON cache)."""

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

    def test_spawn_does_not_mutate_children_json_column(self, repo, engine):
        """The deprecated ``Instance.children`` JSON column MUST stay at its default
        (``"[]"``). After spawn, no JSON mutation should happen — that column is
        no longer written to anywhere in the codebase (C10)."""
        repo.create(
            instance_id="parent-2",
            agent_id="leader",
            agent_dir="./agents/leader",
        )
        repo.create(
            instance_id="child-2",
            agent_id="coder",
            agent_dir="./agents/coder",
            parent_id="parent-2",
        )

        # Read raw row — no enrichment — so we see the actual stored JSON.
        raw_parent = _raw_instance_row(engine, "parent-2")
        assert raw_parent is not None
        # The deprecated JSON column must be untouched (still the default "[]").
        assert raw_parent.children == "[]", (
            f"Expected Instance.children to remain at default '[]' after spawn "
            f"(C10 removed JSON mutations), got {raw_parent.children!r}"
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


class TestEnrichInstanceReadsJunctionTable:
    """``_enrich_instance`` / ``_load_children`` must read from the junction table."""

    def test_enrich_instance_populates_children_from_junction(self, repo, engine):
        """``instance.children`` after enrichment must equal the junction table."""
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

        # Real repository read (triggers _enrich_instance)
        fetched = repo.get("parent-4")
        assert fetched is not None
        assert isinstance(fetched.children, list), (
            f"Enriched children must be list[str], got {type(fetched.children)}"
        )
        assert sorted(fetched.children) == ["child-4a", "child-4b"]

    def test_load_children_returns_empty_when_no_children(self, repo, engine):
        """An instance with no children returns ``[]`` from the junction table."""
        repo.create(
            instance_id="lonely",
            agent_id="leader",
            agent_dir="./agents/leader",
        )

        fetched = repo.get("lonely")
        assert fetched is not None
        assert fetched.children == []

    def test_enriched_children_does_not_reflect_json_column_value(
        self, repo, engine
    ):
        """Even if ``Instance.children`` JSON contains stale data, the enriched
        children list must reflect ONLY the junction table. This proves the
        junction table is the canonical source (overriding any stale JSON)."""
        repo.create(
            instance_id="parent-5",
            agent_id="leader",
            agent_dir="./agents/leader",
        )
        repo.create(
            instance_id="child-5",
            agent_id="coder",
            agent_dir="./agents/coder",
            parent_id="parent-5",
        )

        # Tamper with the deprecated JSON column to simulate stale data from a
        # pre-C10 deployment. The enrichment MUST ignore it.
        with Session(engine) as session:
            parent = session.get(Instance, "parent-5")
            parent.children = '["ghost-1", "ghost-2"]'  # lies
            session.add(parent)
            session.commit()

        fetched = repo.get("parent-5")
        assert fetched is not None
        # Only the real junction-table child, not the ghosts.
        assert fetched.children == ["child-5"], (
            f"Enrichment must use junction table, not the JSON column. "
            f"Got {fetched.children}"
        )


class TestCompletionDeletesJunctionRow:
    """Completion paths must DELETE the ``instance_hierarchy`` row (the canonical
    cleanup). The completion site lives in ``child_reports.py`` /
    ``error_reporting.py`` and runs ``DELETE FROM instance_hierarchy WHERE child_id = :child_id``.
    Here we directly exercise that DELETE to verify the junction table supports
    cleanup and downstream reads return ``[]``."""

    def test_delete_from_junction_removes_child_from_enriched_list(self, repo, engine):
        """Simulate completion: DELETE the junction row, then re-fetch parent.
        The enriched children list must no longer include the completed child."""
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
        before = repo.get("parent-6")
        assert before is not None
        assert before.children == ["child-6"]

        # Simulate completion: this is the exact SQL the completion paths run
        # (daemon/services/child_reports.py:603-605, 1292-1294,
        # daemon/services/error_reporting.py:252-256).
        with Session(engine) as session:
            session.execute(
                text("DELETE FROM instance_hierarchy WHERE child_id = :child_id"),
                {"child_id": "child-6"},
            )
            session.commit()

        # After deletion: child gone from enriched list
        after = repo.get("parent-6")
        assert after is not None
        assert after.children == [], (
            f"Expected children=[] after junction row deletion, got {after.children}"
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

        fetched = repo.get("parent-7")
        assert fetched is not None
        assert fetched.children == ["child-7b"]

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

        fetched = repo.get("parent-8")
        assert fetched is not None
        assert fetched.children == []


class TestEndToEndSpawnThenComplete:
    """Full spawn → verify → complete → verify cycle."""

    def test_full_cycle_uses_only_junction_table(self, repo, engine):
        """The end-to-end happy path:
        1. Spawn parent (no parent)
        2. Spawn child → junction row created, JSON column untouched
        3. _enrich_instance returns [child]
        4. Complete child → junction row deleted
        5. _enrich_instance returns []
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

        # Junction row exists, JSON column untouched
        assert _hierarchy_child_ids(engine, "e2e-parent") == ["e2e-child"]
        raw_parent = _raw_instance_row(engine, "e2e-parent")
        assert raw_parent is not None
        assert raw_parent.children == "[]", (
            "JSON cache must not be mutated on spawn (C10)"
        )

        # 3. Enrichment sees the child
        enriched = repo.get("e2e-parent")
        assert enriched is not None
        assert enriched.children == ["e2e-child"]

        # 4. Simulate completion DELETE
        with Session(engine) as session:
            session.execute(
                text("DELETE FROM instance_hierarchy WHERE child_id = :child_id"),
                {"child_id": "e2e-child"},
            )
            session.commit()

        # 5. Enrichment no longer sees the child
        after = repo.get("e2e-parent")
        assert after is not None
        assert after.children == []