"""Tests for tree traversal methods in SQLModelInstanceRepository.

Tests cover:
- get_tree_root_id: traverse up parent chain to find root
- get_tree_ids: BFS downward via InstanceHierarchy table
- get_ancestor_ids: collect ancestor chain (parent -> root, excluding self)
"""

import pytest
from datetime import datetime

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
