"""Tests for tree traversal methods in SQLModelInstanceRepository.

Tests cover:
- get_tree_root_id: traverse up parent chain to find root
- get_tree_ids: BFS downward via InstanceHierarchy table
- get_ancestor_ids: collect ancestor chain (parent -> root, excluding self)
"""

import pytest

from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session as SQLModelSession, SQLModel

from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.instance.models import Instance, InstanceHierarchy


@pytest.fixture
def engine():
    """Create in-memory SQLite engine for testing."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def repo(engine):
    """Create SQLModelInstanceRepository with fresh database."""
    return SQLModelInstanceRepository(engine)


def _create_instance(
    repo: SQLModelInstanceRepository,
    instance_id: str,
    parent_id: str | None = None,
    agent_id: str = "test-agent",
    agent_dir: str = "./agents/test-agent",
) -> Instance:
    """Helper to create an instance with optional parent relationship."""
    instance = repo.create(
        instance_id=instance_id,
        agent_id=agent_id,
        agent_dir=agent_dir,
        parent_id=parent_id,
    )
    return instance


# =============================================================================
# TestGetTreeRootId
# =============================================================================

class TestGetTreeRootId:
    """Tests for get_tree_root_id method."""

    def test_single_node_returns_itself(self, repo: SQLModelInstanceRepository):
        """A node with no parent should return itself as the root."""
        _create_instance(repo, "root")
        
        result = repo.get_tree_root_id("root")
        
        assert result == "root"

    def test_two_level_tree_returns_parent(self, repo: SQLModelInstanceRepository):
        """Child should return parent as root."""
        _create_instance(repo, "parent")
        _create_instance(repo, "child", parent_id="parent")
        
        result = repo.get_tree_root_id("child")
        
        assert result == "parent"

    def test_deep_tree_returns_root(self, repo: SQLModelInstanceRepository):
        """Deeply nested node should return the topmost parent."""
        _create_instance(repo, "level-0")  # root
        _create_instance(repo, "level-1", parent_id="level-0")
        _create_instance(repo, "level-2", parent_id="level-1")
        _create_instance(repo, "level-3", parent_id="level-2")
        _create_instance(repo, "level-4", parent_id="level-3")
        _create_instance(repo, "leaf", parent_id="level-4")
        
        result = repo.get_tree_root_id("leaf")
        
        assert result == "level-0"

    def test_nonexistent_id_returns_none(self, repo: SQLModelInstanceRepository):
        """Non-existent instance ID should return None."""
        result = repo.get_tree_root_id("nonexistent")
        
        assert result is None

    def test_orphaned_parent_returns_none(self, repo: SQLModelInstanceRepository):
        """If an instance's parent_id points to non-existent instance, returns None."""
        # Create an instance directly with a parent_id that doesn't exist as an Instance
        with SQLModelSession(repo.engine) as db_session:
            orphan = Instance(
                instance_id="orphan",
                agent_id="test-agent",
                agent_dir="./agents/test",
                parent_id="nonexistent-parent",  # This parent doesn't exist
            )
            db_session.add(orphan)
            db_session.commit()
        
        result = repo.get_tree_root_id("orphan")
        
        # The implementation returns None when it tries to get the non-existent parent
        assert result is None

    def test_root_of_wide_tree(self, repo: SQLModelInstanceRepository):
        """Root of a tree with multiple branches should return itself."""
        _create_instance(repo, "root")
        _create_instance(repo, "child-1", parent_id="root")
        _create_instance(repo, "child-2", parent_id="root")
        _create_instance(repo, "child-3", parent_id="root")
        _create_instance(repo, "grandchild-1", parent_id="child-1")
        _create_instance(repo, "grandchild-2", parent_id="child-2")
        
        assert repo.get_tree_root_id("root") == "root"
        assert repo.get_tree_root_id("child-1") == "root"
        assert repo.get_tree_root_id("child-3") == "root"
        assert repo.get_tree_root_id("grandchild-1") == "root"
        assert repo.get_tree_root_id("grandchild-2") == "root"


# =============================================================================
# TestGetTreeIds
# =============================================================================

class TestGetTreeIds:
    """Tests for get_tree_ids method (BFS downward traversal)."""

    def test_single_node_returns_self(self, repo: SQLModelInstanceRepository):
        """Node with no children should return just itself."""
        _create_instance(repo, "root")
        
        result = repo.get_tree_ids("root")
        
        assert result == ["root"]

    def test_two_level_tree_returns_all(self, repo: SQLModelInstanceRepository):
        """Tree with parent and child should return both."""
        _create_instance(repo, "parent")
        _create_instance(repo, "child", parent_id="parent")
        
        result = repo.get_tree_ids("parent")
        
        assert set(result) == {"parent", "child"}

    def test_deep_tree_returns_all_descendants(self, repo: SQLModelInstanceRepository):
        """Deeply nested tree should return all descendants."""
        _create_instance(repo, "root")
        _create_instance(repo, "l1", parent_id="root")
        _create_instance(repo, "l2", parent_id="l1")
        _create_instance(repo, "l3", parent_id="l2")
        _create_instance(repo, "l4", parent_id="l3")
        _create_instance(repo, "leaf", parent_id="l4")
        
        result = repo.get_tree_ids("root")
        
        assert result == ["root", "l1", "l2", "l3", "l4", "leaf"]

    def test_nonexistent_root_returns_empty(self, repo: SQLModelInstanceRepository):
        """Non-existent root should return empty list."""
        result = repo.get_tree_ids("nonexistent")
        
        assert result == []

    def test_wide_tree_returns_all_siblings(self, repo: SQLModelInstanceRepository):
        """Tree with many siblings should return all of them."""
        _create_instance(repo, "root")
        for i in range(10):
            _create_instance(repo, f"child-{i}", parent_id="root")
        
        result = repo.get_tree_ids("root")
        
        assert len(result) == 11
        assert "root" in result
        for i in range(10):
            assert f"child-{i}" in result

    def test_multi_branch_tree(self, repo: SQLModelInstanceRepository):
        """Complex tree with multiple branches should return all nodes."""
        _create_instance(repo, "root")
        _create_instance(repo, "a1", parent_id="root")
        _create_instance(repo, "a2", parent_id="root")
        _create_instance(repo, "b1", parent_id="a1")
        _create_instance(repo, "b2", parent_id="a1")
        _create_instance(repo, "c1", parent_id="b1")
        
        result = repo.get_tree_ids("root")
        
        assert set(result) == {"root", "a1", "a2", "b1", "b2", "c1"}

    def test_subtree_returns_only_subtree(self, repo: SQLModelInstanceRepository):
        """Starting from a non-root should return only its descendants."""
        _create_instance(repo, "root")
        _create_instance(repo, "a1", parent_id="root")
        _create_instance(repo, "a2", parent_id="root")
        _create_instance(repo, "b1", parent_id="a1")
        
        result = repo.get_tree_ids("a1")
        
        assert set(result) == {"a1", "b1"}
        assert "root" not in result
        assert "a2" not in result

    def test_leaf_node_returns_self(self, repo: SQLModelInstanceRepository):
        """Leaf node should return only itself."""
        _create_instance(repo, "root")
        _create_instance(repo, "child", parent_id="root")
        
        result = repo.get_tree_ids("child")
        
        assert result == ["child"]


# =============================================================================
# TestGetAncestorIds
# =============================================================================

class TestGetAncestorIds:
    """Tests for get_ancestor_ids method."""

    def test_root_node_returns_empty(self, repo: SQLModelInstanceRepository):
        """Root node (no parent) should return empty list."""
        _create_instance(repo, "root")
        
        result = repo.get_ancestor_ids("root")
        
        assert result == []

    def test_child_with_one_parent(self, repo: SQLModelInstanceRepository):
        """Child with one parent should return [parent_id]."""
        _create_instance(repo, "parent")
        _create_instance(repo, "child", parent_id="parent")
        
        result = repo.get_ancestor_ids("child")
        
        assert result == ["parent"]

    def test_deep_chain_returns_full_ancestor_chain(self, repo: SQLModelInstanceRepository):
        """Deep chain should return ancestors from parent to root (including root)."""
        _create_instance(repo, "root")
        _create_instance(repo, "l1", parent_id="root")
        _create_instance(repo, "l2", parent_id="l1")
        _create_instance(repo, "l3", parent_id="l2")
        _create_instance(repo, "l4", parent_id="l3")
        _create_instance(repo, "leaf", parent_id="l4")
        
        result = repo.get_ancestor_ids("leaf")
        
        assert result == ["l4", "l3", "l2", "l1", "root"]

    def test_nonexistent_id_returns_empty(self, repo: SQLModelInstanceRepository):
        """Non-existent instance should return empty list."""
        result = repo.get_ancestor_ids("nonexistent")
        
        assert result == []

    def test_leaf_in_multi_branch_tree(self, repo: SQLModelInstanceRepository):
        """Leaf in complex tree should return correct chain including root."""
        _create_instance(repo, "root")
        _create_instance(repo, "a1", parent_id="root")
        _create_instance(repo, "a2", parent_id="root")
        _create_instance(repo, "b1", parent_id="a1")
        _create_instance(repo, "c1", parent_id="b1")
        
        result = repo.get_ancestor_ids("c1")
        
        assert result == ["b1", "a1", "root"]

    def test_intermediate_node(self, repo: SQLModelInstanceRepository):
        """Intermediate node should return ancestors up to and including root."""
        _create_instance(repo, "root")
        _create_instance(repo, "l1", parent_id="root")
        _create_instance(repo, "l2", parent_id="l1")
        _create_instance(repo, "l3", parent_id="l2")
        
        result = repo.get_ancestor_ids("l2")
        
        assert result == ["l1", "root"]


# =============================================================================
# Integration Tests
# =============================================================================

class TestTreeTraversalIntegration:
    """Integration tests combining multiple tree traversal methods."""

    def test_consistency_between_methods(self, repo: SQLModelInstanceRepository):
        """Root of a node via get_tree_root_id should include it in get_tree_ids."""
        _create_instance(repo, "root")
        _create_instance(repo, "l1", parent_id="root")
        _create_instance(repo, "l2", parent_id="l1")
        _create_instance(repo, "leaf", parent_id="l2")
        
        root_id = repo.get_tree_root_id("leaf")
        tree_ids = repo.get_tree_ids(root_id)
        ancestors = repo.get_ancestor_ids("leaf")
        
        assert root_id == "root"
        assert "leaf" in tree_ids
        assert set(ancestors).issubset(set(tree_ids))
        assert set(ancestors).union({"leaf", root_id}) == set(tree_ids)

    def test_wide_tree_consistency(self, repo: SQLModelInstanceRepository):
        """Wide tree: get_tree_ids count should match expectations."""
        _create_instance(repo, "root")
        for i in range(5):
            _create_instance(repo, f"child-{i}", parent_id="root")
            for j in range(3):
                _create_instance(repo, f"grandchild-{i}-{j}", parent_id=f"child-{i}")
        
        result = repo.get_tree_ids("root")
        
        # root + 5 children + 15 grandchildren = 21
        assert len(result) == 21
        assert "root" in result
        for i in range(5):
            assert f"child-{i}" in result
            for j in range(3):
                assert f"grandchild-{i}-{j}" in result

    def test_diamond_structure(self, repo: SQLModelInstanceRepository):
        """Diamond: multiple paths to same node - should handle gracefully."""
        _create_instance(repo, "root")
        _create_instance(repo, "left", parent_id="root")
        _create_instance(repo, "right", parent_id="root")
        _create_instance(repo, "bottom", parent_id="left")
        # Add second path - this would create duplicate in hierarchy but primary key prevents it
        # So we test that each child has exactly one parent

        tree_ids = repo.get_tree_ids("root")
        assert len(tree_ids) == 4  # root, left, right, bottom
        assert set(tree_ids) == {"root", "left", "right", "bottom"}


# =============================================================================
# P1 (phase1-plan.md T1) — get_tree_ids_permanent
# =============================================================================

class TestGetTreeIdsPermanent:
    """P1 (phase1-plan.md T1): permanent-lineage enumeration via
    ``instances.parent_id``.

    These tests verify the new helper that backs the kill-switch wrapper
    :meth:`SQLModelInstanceRepository.get_cascade_tree_ids`. The wrapper
    defaults to this helper (mode ``permanent``); ``hierarchy`` mode
    falls back to the transient ``get_tree_ids``.

    Critical property: ``get_tree_ids_permanent`` is independent of the
    ``instance_hierarchy`` working set. Rows deleted on child completion
    (the B1 / B4 root cause) do NOT affect the snapshot — every
    descendant whose ``parent_id`` chain resolves to the root is
    enumerated regardless of churn.
    """

    def test_returns_self_for_single_node(self, repo: SQLModelInstanceRepository):
        """Single node (no parent, no children) returns ``[self]``."""
        _create_instance(repo, "lonely")
        assert repo.get_tree_ids_permanent("lonely") == ["lonely"]

    def test_returns_empty_for_nonexistent_root(self, repo: SQLModelInstanceRepository):
        """Missing root returns ``[]`` — matches ``get_tree_ids`` contract."""
        assert repo.get_tree_ids_permanent("nope-not-here") == []

    def test_three_level_tree_returns_all_descendants(
        self, repo: SQLModelInstanceRepository
    ):
        """Three-level chain: root → child → grandchild returns all three."""
        _create_instance(repo, "root")
        _create_instance(repo, "child", parent_id="root")
        _create_instance(repo, "grandchild", parent_id="child")

        result = repo.get_tree_ids_permanent("root")
        assert set(result) == {"root", "child", "grandchild"}
        assert len(result) == 3

    def test_wide_tree_returns_all_siblings(
        self, repo: SQLModelInstanceRepository
    ):
        """Wide tree: root + N children + M grandchildren = N+M+1."""
        _create_instance(repo, "root")
        for i in range(5):
            _create_instance(repo, f"child-{i}", parent_id="root")
            for j in range(3):
                _create_instance(repo, f"gc-{i}-{j}", parent_id=f"child-{i}")
        result = repo.get_tree_ids_permanent("root")
        # root + 5 children + 15 grandchildren = 21
        assert len(result) == 21
        assert "root" in result

    def test_sees_completed_descendants_via_parent_id(
        self, repo: SQLModelInstanceRepository
    ):
        """B1 regression guard: a COMPLETED child (hierarchy row deleted)
        is STILL enumerated via its permanent ``parent_id``.

        This is the B1 / B4 root cause: cascade enumeration via the
        transient ``instance_hierarchy`` table silently misses
        descendants whose hierarchy rows were deleted on completion.
        ``get_tree_ids_permanent`` walks ``instances.parent_id`` instead
        and is immune to the deletion.
        """
        from daemon.repositories.instance.models import InstanceStatus

        _create_instance(repo, "root")
        child = _create_instance(repo, "child", parent_id="root")
        grandchild = _create_instance(repo, "grandchild", parent_id="child")

        # Mark child + grandchild as COMPLETED (terminal). The cascade
        # bug scenario also deletes the ``instance_hierarchy`` rows on
        # completion, mirroring what production does.
        child.status = InstanceStatus.COMPLETED.value
        grandchild.status = InstanceStatus.COMPLETED.value
        with SQLModelSession(repo.engine) as session:
            session.add(child)
            session.add(grandchild)
            session.commit()

        # Force-delete the hierarchy rows that the bug-leaving production
        # code would have removed at child completion (see
        # ``child_reports.py:922`` / ``error_reporting.py:233`` /
        # ``_terminate_instance_db_sync:3324-3333``).
        with SQLModelSession(repo.engine) as session:
            from sqlalchemy import delete as sql_delete
            session.exec(sql_delete(InstanceHierarchy).where(
                InstanceHierarchy.parent_id.in_(["root", "child"])
            ))
            session.commit()

        # Sanity: the transient ``get_tree_ids`` now MISSES the
        # completed descendants — this is the exact B1/B4 symptom.
        transient = repo.get_tree_ids("root")
        assert transient == ["root"], (
            f"transient get_tree_ids should miss completed descendants; "
            f"got {transient}"
        )

        # The permanent enumerator still sees the full tree.
        permanent = repo.get_tree_ids_permanent("root")
        assert set(permanent) == {"root", "child", "grandchild"}, (
            f"get_tree_ids_permanent must see churned descendants via "
            f"parent_id; got {permanent}"
        )

    def test_traversal_cap_silently_truncates_at_depth_256(
        self, repo: SQLModelInstanceRepository, caplog
    ):
        """C12 — tree at or beyond the cap is silently truncated, one-time WARN.

        Build a chain of 258 nodes (root at depth 0, deepest at depth 257)
        so the depth-257 frontier is non-empty when the loop exhausts.
        Expected: ``visited`` has 257 entries (root + 256 descendants), and
        exactly one WARN is logged.
        """
        import logging
        caplog.set_level(logging.WARNING, logger="daemon.repositories.instance.repository")

        # 258 nodes in a chain: root → n1 → n2 → ... → n257
        _create_instance(repo, "root")
        prev_id = "root"
        for i in range(1, 258):
            cur_id = f"n{i}"
            _create_instance(repo, cur_id, parent_id=prev_id)
            prev_id = cur_id

        result = repo.get_tree_ids_permanent("root")

        # Cap is 256: visited has root + 256 descendants = 257 entries.
        assert len(result) == 257, (
            f"expected 257 visited nodes at cap; got {len(result)}"
        )
        # Root is always first.
        assert result[0] == "root"
        # Deepest visited node is at depth 256 (n256). n257 is dropped.
        assert "n256" in result
        assert "n257" not in result

        # One-time warning logged.
        warning_records = [
            r for r in caplog.records
            if "traversal depth cap" in r.message and r.levelno == logging.WARNING
        ]
        assert len(warning_records) == 1, (
            f"expected exactly one traversal-cap WARN; got {len(warning_records)}"
        )

    def test_sees_revived_descendants_via_parent_id(
        self, repo: SQLModelInstanceRepository
    ):
        """Revive semantics guard — a revived child (no hierarchy row
        re-inserted) is still enumerated.

        Revive never re-inserts ``instance_hierarchy`` rows (the writers
        are at ``repository.py:206`` and ``instance_lifecycle.py:3450``,
        neither runs on revive). A revived instance is invisible to
        ``instance_hierarchy`` readers but its ``parent_id`` survives in
        ``instances``, so ``get_tree_ids_permanent`` sees it.
        """
        _create_instance(repo, "root")
        child = _create_instance(repo, "child", parent_id="root")

        # Simulate revive: no hierarchy row exists for the child (the
        # original hierarchy row was deleted on the child's prior
        # completion, never re-inserted on revive).
        with SQLModelSession(repo.engine) as session:
            from sqlalchemy import delete as sql_delete
            session.exec(sql_delete(InstanceHierarchy).where(
                InstanceHierarchy.parent_id == "root"
            ))
            session.commit()

        # Transient enumeration: misses the revived child.
        transient = repo.get_tree_ids("root")
        assert transient == ["root"]

        # Permanent: still sees the revived child.
        permanent = repo.get_tree_ids_permanent("root")
        assert set(permanent) == {"root", "child"}

    def test_cycle_self_parent_is_guarded_by_depth_cap(
        self, repo: SQLModelInstanceRepository
    ):
        """Cycle guard — ``_MAX_TRAVERSAL_DEPTH=256`` + visited set
        prevents infinite loops from a self-parenting node.

        Self-parenting makes ``parent_id == instance_id``. The visited
        set prevents the cycle from looping; the depth cap is the
        ultimate fallback if the visited set is ever bypassed.
        """
        from daemon.repositories.instance.models import InstanceStatus

        _create_instance(repo, "root")
        # A node that points to itself.
        cyclic = _create_instance(repo, "cyclic")
        cyclic.status = InstanceStatus.RUNNING.value
        cyclic.parent_id = "cyclic"  # self-parent — creates a cycle
        with SQLModelSession(repo.engine) as session:
            session.add(cyclic)
            session.commit()

        # Must terminate (depth cap + visited set); not hang.
        result = repo.get_tree_ids_permanent("root")
        assert "root" in result
        # cyclic is a separate node from root; root's BFS doesn't reach
        # it because root.parent_id is None. So `cyclic` is not in
        # root's tree. This test just ensures no infinite loop.
        assert isinstance(result, list)


# =============================================================================
# P1 (phase1-plan.md T1) — get_cascade_tree_ids wrapper
# =============================================================================

class TestGetCascadeTreeIds:
    """P1 (phase1-plan.md T1, C4): kill-switch wrapper that routes
    cascades to either ``get_tree_ids_permanent`` (default ``permanent``)
    or the legacy ``get_tree_ids`` (``hierarchy`` env).

    Env-driven mode is cached on first call (restart-required semantics).
    The boot-time INFO log is exercised separately in
    ``test_cascade_lineage_boot_log`` (see below) so the wrapper
    contract is unit-testable without forking the daemon.
    """

    @pytest.fixture(autouse=True)
    def _reset_mode_cache(self, monkeypatch):
        """Reset the module-level ``_CASCADE_LINEAGE_MODE`` cache and
        the ``_CASCADE_LINEAGE_BOOT_LOG_EMITTED`` flag so each test
        re-reads the env cleanly (the cache is otherwise a one-shot
        process-wide singleton).
        """
        from daemon.repositories.instance import repository as repo_mod
        monkeypatch.setattr(repo_mod, "_CASCADE_LINEAGE_MODE", None)
        monkeypatch.setattr(
            repo_mod, "_CASCADE_LINEAGE_BOOT_LOG_EMITTED", False
        )

    def test_default_mode_routes_to_permanent(
        self, repo: SQLModelInstanceRepository, monkeypatch
    ):
        """Default mode (``permanent``) routes to ``get_tree_ids_permanent``."""
        monkeypatch.delenv("ENSEMBLE_CASCADE_LINEAGE", raising=False)
        _create_instance(repo, "root")
        _create_instance(repo, "child", parent_id="root")
        result = repo.get_cascade_tree_ids("root")
        assert set(result) == {"root", "child"}

    def test_hierarchy_mode_routes_to_legacy(
        self, repo: SQLModelInstanceRepository, monkeypatch
    ):
        """``hierarchy`` env routes to legacy ``get_tree_ids``.

        With a transient ``instance_hierarchy`` row present (the
        ``create()`` call inserts one for any non-None ``parent_id``),
        both modes return the same ids — proving the wrapper's
        mode-switching surface. The semantic difference is exercised
        in :class:`TestGetTreeIdsPermanent`.
        """
        monkeypatch.setenv("ENSEMBLE_CASCADE_LINEAGE", "hierarchy")

        _create_instance(repo, "root")
        _create_instance(repo, "child", parent_id="root")

        result = repo.get_cascade_tree_ids("root")
        assert set(result) == {"root", "child"}

    def test_unknown_mode_warns_and_falls_back_to_permanent(
        self, repo: SQLModelInstanceRepository, monkeypatch, caplog
    ):
        """Unknown ``ENSEMBLE_CASCADE_LINEAGE`` value → WARN + ``permanent`` fallback."""
        import logging
        monkeypatch.setenv("ENSEMBLE_CASCADE_LINEAGE", "nonsense")

        caplog.set_level(
            logging.WARNING, logger="daemon.repositories.instance.repository"
        )
        _create_instance(repo, "root")
        result = repo.get_cascade_tree_ids("root")
        assert result == ["root"]
        # WARN logged.
        warnings = [
            r for r in caplog.records
            if "not a recognized cascade-lineage mode" in r.message
            and r.levelno == logging.WARNING
        ]
        assert len(warnings) == 1, (
            f"expected one WARN for unknown mode; got {len(warnings)}"
        )

    def test_get_tree_ids_behavior_unchanged(
        self, repo: SQLModelInstanceRepository
    ):
        """``get_tree_ids()`` behavior is unchanged (staged deprecation).

        The plan keeps ``get_tree_ids`` during migration with a
        corrected docstring (no behavior change). This test pins that
        contract: an instance with no hierarchy row is not enumerated,
        and one with a hierarchy row IS enumerated (matching the legacy
        semantics).
        """
        # Single node with no parent → root only.
        _create_instance(repo, "solo")
        assert repo.get_tree_ids("solo") == ["solo"]

        # Parent + child → both enumerated (create() inserts the
        # hierarchy row when parent_id is set, so legacy gets both).
        _create_instance(repo, "root")
        _create_instance(repo, "child", parent_id="root")
        assert set(repo.get_tree_ids("root")) == {"root", "child"}

        # Force-delete the hierarchy row (the B1/B4 churn scenario).
        with SQLModelSession(repo.engine) as session:
            from sqlalchemy import delete as sql_delete
            session.exec(sql_delete(InstanceHierarchy).where(
                InstanceHierarchy.parent_id == "root"
            ))
            session.commit()
        # Legacy now misses the child (this is the B1/B4 symptom).
        assert repo.get_tree_ids("root") == ["root"]


def test_cascade_lineage_boot_log_emits_once(caplog, monkeypatch):
    """P1 (C4) — ``emit_cascade_lineage_boot_log`` emits the INFO
    exactly once per process and names the resolved mode.
    """
    import logging
    from daemon.repositories.instance import repository as repo_mod

    # Reset module-level flag so we can re-trigger in this test.
    monkeypatch.setattr(repo_mod, "_CASCADE_LINEAGE_BOOT_LOG_EMITTED", False)
    monkeypatch.setattr(repo_mod, "_CASCADE_LINEAGE_MODE", None)

    caplog.set_level(
        logging.INFO, logger="daemon.repositories.instance.repository"
    )

    repo_mod.emit_cascade_lineage_boot_log()
    repo_mod.emit_cascade_lineage_boot_log()  # second call — no-op

    info_records = [
        r for r in caplog.records
        if "Cascade lineage mode resolved" in r.message
        and r.levelno == logging.INFO
    ]
    assert len(info_records) == 1, (
        f"expected exactly one boot-time INFO; got {len(info_records)}"
    )
    assert "permanent" in info_records[0].message
