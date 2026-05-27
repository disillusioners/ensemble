"""Tests for tree-aware pause/resume behavior (Phase 4).

This test suite covers comprehensive edge cases for cascade pause/resume
behavior not covered by the basic test_pause_instance_cascade.py suite.

Key behaviors tested:
- Cascade pause from any node (child, leaf) pauses entire tree
- Cascade resume from any node resumes entire tree
- waiting_for semantics: Pause resets ALL to 0, Resume sets ancestors to 1
- Resume router: silent=True for non-targets, silent=False for target
- Mixed terminal states handling
- Wide and deep tree scenarios
"""

import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from daemon.repositories.instance.models import Instance, InstanceStatus


class TestTreeAwarePauseCascade:
    """Test suite for tree-aware pause cascade behavior."""

    @pytest.fixture
    def mock_repo(self):
        """Create a mock instance repository."""
        return MagicMock()

    @pytest.fixture
    def mock_registry(self):
        """Create a mock request registry."""
        registry = MagicMock()
        registry.cancel_by_instance = MagicMock(return_value=0)
        return registry

    @pytest.fixture
    def mock_manager(self, mock_repo, mock_registry):
        """Create a mock manager with mocked repository and registry."""
        manager = MagicMock()
        manager._instance_repository = mock_repo
        manager._request_registry = mock_registry
        manager._live_hub = MagicMock()
        manager._live_hub.stream_status_change = AsyncMock()
        manager._graph_tasks = {}
        return manager

    @pytest.fixture
    def lifecycle_service(self, mock_manager):
        """Create an InstanceLifecycleService with mocked manager."""
        from daemon.services.instance_lifecycle import InstanceLifecycleService
        service = InstanceLifecycleService.__new__(InstanceLifecycleService)
        service._manager = mock_manager
        return service

    def _make_instance(
        self,
        instance_id: str,
        status: str = InstanceStatus.RUNNING.value,
        children: list[str] | None = None,
        waiting_for: int = 0,
    ) -> Instance:
        """Create a mock Instance object."""
        instance = MagicMock(spec=Instance)
        instance.instance_id = instance_id
        instance.status = status
        instance.children = children if children is not None else []
        instance.waiting_for = waiting_for
        instance.agent_id = "test-agent"
        return instance

    @pytest.mark.asyncio
    async def test_pause_from_child_pauses_entire_tree(self, lifecycle_service, mock_repo, mock_registry):
        """Test that pausing from a child pauses the ENTIRE tree, not just subtree.

        Hierarchy:
            root
            ├── child1 (pausing from here)
            │   └── grandchild1
            └── child2

        Expected: All 4 instances are paused.
        """
        root_id = "root"
        child1_id = "child1"
        child2_id = "child2"
        grandchild_id = "grandchild1"

        # Mock: child1 is the entry point, but root is the tree root
        mock_repo.get_tree_root_id.return_value = root_id
        mock_repo.get_tree_ids.return_value = [root_id, child1_id, grandchild_id, child2_id]

        def get_side_effect(instance_id):
            if instance_id == root_id:
                return self._make_instance(root_id, status="running", children=[child1_id, child2_id])
            elif instance_id == child1_id:
                return self._make_instance(child1_id, status="running", children=[grandchild_id])
            elif instance_id == child2_id:
                return self._make_instance(child2_id, status="running")
            elif instance_id == grandchild_id:
                return self._make_instance(grandchild_id, status="running")
            return None

        mock_repo.get.side_effect = get_side_effect

        # Pause FROM child1 (not root)
        result = await lifecycle_service.pause_instance_cascade(child1_id)

        # Entire tree should be paused
        assert set(result["paused_ids"]) == {root_id, child1_id, child2_id, grandchild_id}
        assert result["skipped_ids"] == []
        assert mock_repo.update.call_count == 4

    @pytest.mark.asyncio
    async def test_pause_from_leaf_pauses_entire_tree(self, lifecycle_service, mock_repo, mock_registry):
        """Test that pausing from a leaf node pauses the ENTIRE tree.

        Hierarchy:
            root
            ├── child1
            │   └── grandchild (pausing from here - leaf)
            └── child2

        Expected: All 4 instances are paused.
        """
        root_id = "root"
        child1_id = "child1"
        child2_id = "child2"
        grandchild_id = "grandchild"

        mock_repo.get_tree_root_id.return_value = root_id
        mock_repo.get_tree_ids.return_value = [root_id, child1_id, grandchild_id, child2_id]

        def get_side_effect(instance_id):
            if instance_id == root_id:
                return self._make_instance(root_id, status="running", children=[child1_id, child2_id])
            elif instance_id == child1_id:
                return self._make_instance(child1_id, status="running", children=[grandchild_id])
            elif instance_id == child2_id:
                return self._make_instance(child2_id, status="running")
            elif instance_id == grandchild_id:
                return self._make_instance(grandchild_id, status="running")
            return None

        mock_repo.get.side_effect = get_side_effect

        # Pause FROM grandchild (leaf)
        result = await lifecycle_service.pause_instance_cascade(grandchild_id)

        # Entire tree should be paused
        assert set(result["paused_ids"]) == {root_id, child1_id, child2_id, grandchild_id}
        assert result["skipped_ids"] == []
        assert mock_repo.update.call_count == 4

    @pytest.mark.asyncio
    async def test_pause_wide_tree_with_many_siblings(self, lifecycle_service, mock_repo, mock_registry):
        """Test pausing a wide tree with many sibling children.

        Hierarchy:
            root
            └── children: [c1, c2, c3, c4, c5]

        Expected: All 6 instances are paused.
        """
        root_id = "root"
        child_ids = ["c1", "c2", "c3", "c4", "c5"]
        all_ids = [root_id] + child_ids

        mock_repo.get_tree_root_id.return_value = root_id
        mock_repo.get_tree_ids.return_value = all_ids

        def get_side_effect(instance_id):
            if instance_id == root_id:
                return self._make_instance(root_id, status="running", children=child_ids)
            elif instance_id in child_ids:
                return self._make_instance(instance_id, status="running")
            return None

        mock_repo.get.side_effect = get_side_effect

        result = await lifecycle_service.pause_instance_cascade(root_id)

        assert set(result["paused_ids"]) == set(all_ids)
        assert result["skipped_ids"] == []
        assert mock_repo.update.call_count == 6

    @pytest.mark.asyncio
    async def test_pause_with_mixed_status_children(self, lifecycle_service, mock_repo, mock_registry):
        """Test pausing a tree where some children have different statuses.

        Hierarchy:
            root (running)
            ├── child1 (running)     -> will be paused
            └── child2 (paused)     -> will be skipped (already paused)

        Note: The implementation only skips PAUSED status. COMPLETED/ERROR
        children would also be paused (they're not skipped).
        """
        root_id = "root"
        child1_id = "child1-running"
        child2_id = "child2-paused"

        mock_repo.get_tree_root_id.return_value = root_id
        mock_repo.get_tree_ids.return_value = [root_id, child1_id, child2_id]

        def get_side_effect(instance_id):
            if instance_id == root_id:
                return self._make_instance(root_id, status="running", children=[child1_id, child2_id])
            elif instance_id == child1_id:
                return self._make_instance(child1_id, status="running")
            elif instance_id == child2_id:
                return self._make_instance(child2_id, status="paused")
            return None

        mock_repo.get.side_effect = get_side_effect

        result = await lifecycle_service.pause_instance_cascade(root_id)

        # Root and child1 should be paused
        assert set(result["paused_ids"]) == {root_id, child1_id}
        # Already paused child should be skipped
        assert set(result["skipped_ids"]) == {child2_id}
        assert mock_repo.update.call_count == 2

    @pytest.mark.asyncio
    async def test_pause_resets_waiting_for_when_positive(self, lifecycle_service, mock_repo, mock_registry):
        """Test that pause resets waiting_for to 0 when original value was positive.

        Hierarchy with various waiting_for values:
            root (waiting_for=5)
            ├── child1 (waiting_for=3) -> waiting_for will be reset to 0
            ├── child2 (waiting_for=0) -> waiting_for not passed (already 0)
            └── child3 (waiting_for=2) -> waiting_for will be reset to 0

        After pause: Instances with waiting_for > 0 get waiting_for=0 in update call.
        """
        root_id = "root"
        child1_id = "child1"
        child2_id = "child2"
        child3_id = "child3"

        mock_repo.get_tree_root_id.return_value = root_id
        mock_repo.get_tree_ids.return_value = [root_id, child1_id, child2_id, child3_id]

        def get_side_effect(instance_id):
            if instance_id == root_id:
                return self._make_instance(root_id, status="running", waiting_for=5)
            elif instance_id == child1_id:
                return self._make_instance(child1_id, status="running", waiting_for=3)
            elif instance_id == child2_id:
                return self._make_instance(child2_id, status="running", waiting_for=0)
            elif instance_id == child3_id:
                return self._make_instance(child3_id, status="running", waiting_for=2)
            return None

        mock_repo.get.side_effect = get_side_effect

        result = await lifecycle_service.pause_instance_cascade(root_id)

        assert set(result["paused_ids"]) == {root_id, child1_id, child2_id, child3_id}
        assert mock_repo.update.call_count == 4

        # Verify that instances with waiting_for > 0 get waiting_for=0
        # Note: child2 already had waiting_for=0, so waiting_for is not passed for it
        update_calls = {call[0][0]: call[1] for call in mock_repo.update.call_args_list}
        assert update_calls[root_id]["waiting_for"] == 0
        assert update_calls[child1_id]["waiting_for"] == 0
        assert update_calls[child3_id]["waiting_for"] == 0
        # child2 had waiting_for=0, so waiting_for is not in kwargs
        assert "waiting_for" not in update_calls[child2_id]

    @pytest.mark.asyncio
    async def test_pause_single_instance_no_tree(self, lifecycle_service, mock_repo, mock_registry):
        """Test pausing a single instance with no children (no tree).

        This is the simplest case - just one instance.
        """
        instance_id = "single-instance"

        mock_repo.get_tree_root_id.return_value = instance_id
        mock_repo.get_tree_ids.return_value = [instance_id]
        mock_repo.get.return_value = self._make_instance(instance_id, status="running")

        result = await lifecycle_service.pause_instance_cascade(instance_id)

        assert result["paused_ids"] == [instance_id]
        assert result["skipped_ids"] == []
        mock_repo.update.assert_called_once()
        call_kwargs = mock_repo.update.call_args[1]
        assert call_kwargs["status"] == "paused"

    @pytest.mark.asyncio
    async def test_pause_already_paused_entire_tree_skipped(self, lifecycle_service, mock_repo, mock_registry):
        """Test pausing when entire tree is already paused.

        All instances should be skipped, not paused again.
        """
        root_id = "root"
        child1_id = "child1"
        child2_id = "child2"

        mock_repo.get_tree_root_id.return_value = root_id
        mock_repo.get_tree_ids.return_value = [root_id, child1_id, child2_id]

        def get_side_effect(instance_id):
            if instance_id == root_id:
                return self._make_instance(root_id, status="paused", children=[child1_id, child2_id])
            elif instance_id in [child1_id, child2_id]:
                return self._make_instance(instance_id, status="paused")
            return None

        mock_repo.get.side_effect = get_side_effect

        result = await lifecycle_service.pause_instance_cascade(root_id)

        # Nothing should be paused (all skipped)
        assert result["paused_ids"] == []
        assert set(result["skipped_ids"]) == {root_id, child1_id, child2_id}
        # update should NOT be called
        mock_repo.update.assert_not_called()


class TestTreeAwareResumeCascade:
    """Test suite for tree-aware resume cascade behavior."""

    @pytest.fixture
    def mock_repo(self):
        """Create a mock instance repository."""
        return MagicMock()

    @pytest.fixture
    def mock_manager(self, mock_repo):
        """Create a mock manager with mocked repository and registry."""
        manager = MagicMock()
        manager._instance_repository = mock_repo
        manager._request_registry = MagicMock()
        manager._live_hub = MagicMock()
        manager._live_hub.stream_status_change = AsyncMock()
        return manager

    @pytest.fixture
    def lifecycle_service(self, mock_manager):
        """Create an InstanceLifecycleService with mocked manager."""
        from daemon.services.instance_lifecycle import InstanceLifecycleService
        service = InstanceLifecycleService.__new__(InstanceLifecycleService)
        service._manager = mock_manager
        return service

    def _make_instance(
        self,
        instance_id: str,
        status: str = InstanceStatus.PAUSED.value,
        children: list[str] | None = None,
        agent_id: str = "test-agent",
    ) -> Instance:
        """Create a mock Instance object."""
        instance = MagicMock(spec=Instance)
        instance.instance_id = instance_id
        instance.status = status
        instance.children = children if children is not None else []
        instance.agent_id = agent_id
        return instance

    @pytest.mark.asyncio
    async def test_resume_from_root_all_waiting_for_zero(self, lifecycle_service, mock_repo):
        """Test that resuming from root sets ALL waiting_for to 0.

        When resuming from root, no ancestors exist, so all get waiting_for=0.
        """
        root_id = "root"
        child1_id = "child1"
        child2_id = "child2"

        mock_repo.get_tree_root_id.return_value = root_id
        mock_repo.get_tree_ids.return_value = [root_id, child1_id, child2_id]
        mock_repo.get_ancestor_ids.return_value = []

        def get_side_effect(instance_id):
            if instance_id == root_id:
                return self._make_instance(root_id, status="paused", children=[child1_id, child2_id])
            elif instance_id in [child1_id, child2_id]:
                return self._make_instance(instance_id, status="paused")
            return None

        mock_repo.get.side_effect = get_side_effect

        result = await lifecycle_service.resume_instance_cascade(root_id)

        assert set(result["resumed_ids"]) == {root_id, child1_id, child2_id}
        assert result["target_id"] == root_id

        # ALL should have waiting_for=0 when resuming from root
        for call in mock_repo.update.call_args_list:
            assert call[1]["waiting_for"] == 0

    @pytest.mark.asyncio
    async def test_resume_from_child_only_ancestors_get_waiting_for_one(self, lifecycle_service, mock_repo):
        """Test that resuming from child sets ONLY ancestors to waiting_for=1.

        Hierarchy:
            root (ancestor of child2)
            ├── child1 (sibling of child2)
            └── child2 (resuming from here)

        Expected:
        - root: waiting_for=1 (ancestor)
        - child1: waiting_for=0 (sibling, not ancestor)
        - child2: waiting_for=0 (resumed node)
        """
        root_id = "root"
        child1_id = "child1"
        child2_id = "child2"

        mock_repo.get_tree_root_id.return_value = root_id
        mock_repo.get_tree_ids.return_value = [root_id, child1_id, child2_id]
        mock_repo.get_ancestor_ids.return_value = [root_id]  # child2's ancestors

        def get_side_effect(instance_id):
            if instance_id == root_id:
                return self._make_instance(root_id, status="paused", children=[child1_id, child2_id])
            elif instance_id in [child1_id, child2_id]:
                return self._make_instance(instance_id, status="paused")
            return None

        mock_repo.get.side_effect = get_side_effect

        result = await lifecycle_service.resume_instance_cascade(child2_id)

        assert set(result["resumed_ids"]) == {root_id, child1_id, child2_id}
        assert result["target_id"] == child2_id

        # Check waiting_for values
        update_calls = {call[0][0]: call[1] for call in mock_repo.update.call_args_list}
        assert update_calls[root_id]["waiting_for"] == 1  # Ancestor
        assert update_calls[child1_id]["waiting_for"] == 0  # Sibling
        assert update_calls[child2_id]["waiting_for"] == 0  # Resumed node

    @pytest.mark.asyncio
    async def test_resume_from_leaf_full_ancestor_chain_gets_waiting_for_one(self, lifecycle_service, mock_repo):
        """Test that resuming from leaf sets full ancestor chain to waiting_for=1.

        Hierarchy (5 levels deep):
            root
            └── level1
                └── level2
                    └── level3
                        └── leaf (resuming from here)

        Expected: root, level1, level2, level3 all get waiting_for=1.
        """
        root_id = "root"
        level1_id = "level1"
        level2_id = "level2"
        level3_id = "level3"
        leaf_id = "leaf"

        mock_repo.get_tree_root_id.return_value = root_id
        mock_repo.get_tree_ids.return_value = [root_id, level1_id, level2_id, level3_id, leaf_id]
        mock_repo.get_ancestor_ids.return_value = [level3_id, level2_id, level1_id, root_id]

        def get_side_effect(instance_id):
            if instance_id == root_id:
                return self._make_instance(root_id, status="paused", children=[level1_id])
            elif instance_id == level1_id:
                return self._make_instance(level1_id, status="paused", children=[level2_id])
            elif instance_id == level2_id:
                return self._make_instance(level2_id, status="paused", children=[level3_id])
            elif instance_id == level3_id:
                return self._make_instance(level3_id, status="paused", children=[leaf_id])
            elif instance_id == leaf_id:
                return self._make_instance(leaf_id, status="paused")
            return None

        mock_repo.get.side_effect = get_side_effect

        result = await lifecycle_service.resume_instance_cascade(leaf_id)

        assert set(result["resumed_ids"]) == {root_id, level1_id, level2_id, level3_id, leaf_id}
        assert result["target_id"] == leaf_id

        # Check waiting_for values
        update_calls = {call[0][0]: call[1] for call in mock_repo.update.call_args_list}
        assert update_calls[root_id]["waiting_for"] == 1  # Ancestor
        assert update_calls[level1_id]["waiting_for"] == 1  # Ancestor
        assert update_calls[level2_id]["waiting_for"] == 1  # Ancestor
        assert update_calls[level3_id]["waiting_for"] == 1  # Ancestor
        assert update_calls[leaf_id]["waiting_for"] == 0  # Resumed node

    @pytest.mark.asyncio
    async def test_resume_deep_tree_five_plus_levels(self, lifecycle_service, mock_repo):
        """Test resuming a deeply nested tree (5+ levels).

        Verifies that waiting_for propagation works correctly for deep trees.
        """
        ids = ["l0", "l1", "l2", "l3", "l4", "l5"]  # 6 levels

        mock_repo.get_tree_root_id.return_value = ids[0]
        mock_repo.get_tree_ids.return_value = ids
        # l5's ancestors (from immediate parent to root): [l4, l3, l2, l1, l0]
        mock_repo.get_ancestor_ids.return_value = ["l4", "l3", "l2", "l1", "l0"]

        def get_side_effect(instance_id):
            idx = ids.index(instance_id) if instance_id in ids else -1
            if idx >= 0:
                children = [ids[idx + 1]] if idx < len(ids) - 1 else []
                return self._make_instance(instance_id, status="paused", children=children)
            return None

        mock_repo.get.side_effect = get_side_effect

        # Resume from deepest level (l5)
        result = await lifecycle_service.resume_instance_cascade("l5")

        assert set(result["resumed_ids"]) == set(ids)
        assert result["target_id"] == "l5"

        # All ancestors should get waiting_for=1
        update_calls = {call[0][0]: call[1] for call in mock_repo.update.call_args_list}
        for ancestor in ids[:-1]:
            assert update_calls[ancestor]["waiting_for"] == 1, f"{ancestor} should have waiting_for=1"
        # Resumed node should get waiting_for=0
        assert update_calls["l5"]["waiting_for"] == 0

    @pytest.mark.asyncio
    async def test_resume_from_middle_of_wide_tree(self, lifecycle_service, mock_repo):
        """Test resuming from middle child of wide tree.

        Hierarchy:
            root
            ├── c1
            ├── c2 (resuming from here)
            ├── c3
            └── c4

        Expected: Only root gets waiting_for=1 (only ancestor).
        """
        root_id = "root"
        child_ids = ["c1", "c2", "c3", "c4"]

        mock_repo.get_tree_root_id.return_value = root_id
        mock_repo.get_tree_ids.return_value = [root_id] + child_ids
        mock_repo.get_ancestor_ids.return_value = [root_id]  # Only root is ancestor of c2

        def get_side_effect(instance_id):
            if instance_id == root_id:
                return self._make_instance(root_id, status="paused", children=child_ids)
            elif instance_id in child_ids:
                return self._make_instance(instance_id, status="paused")
            return None

        mock_repo.get.side_effect = get_side_effect

        # Resume from c2
        result = await lifecycle_service.resume_instance_cascade("c2")

        assert set(result["resumed_ids"]) == {root_id} | set(child_ids)
        assert result["target_id"] == "c2"

        # Check waiting_for values
        update_calls = {call[0][0]: call[1] for call in mock_repo.update.call_args_list}
        assert update_calls[root_id]["waiting_for"] == 1  # Only ancestor
        for sibling in ["c1", "c3", "c4"]:
            assert update_calls[sibling]["waiting_for"] == 0  # Siblings
        assert update_calls["c2"]["waiting_for"] == 0  # Resumed node

    @pytest.mark.asyncio
    async def test_resume_when_some_already_running(self, lifecycle_service, mock_repo):
        """Test resuming when some instances are already running.

        Only paused instances should be resumed.
        """
        root_id = "root"
        child1_id = "child1"
        child2_id = "child2"

        mock_repo.get_tree_root_id.return_value = root_id
        mock_repo.get_tree_ids.return_value = [root_id, child1_id, child2_id]
        mock_repo.get_ancestor_ids.return_value = []

        def get_side_effect(instance_id):
            if instance_id == root_id:
                return self._make_instance(root_id, status="paused", children=[child1_id, child2_id])
            elif instance_id == child1_id:
                return self._make_instance(child1_id, status="paused")
            elif instance_id == child2_id:
                return self._make_instance(child2_id, status="running")  # Already running
            return None

        mock_repo.get.side_effect = get_side_effect

        result = await lifecycle_service.resume_instance_cascade(root_id)

        # Only root and child1 should be resumed
        assert set(result["resumed_ids"]) == {root_id, child1_id}
        assert result["skipped_ids"] == [child2_id]

    @pytest.mark.asyncio
    async def test_resume_already_running_entire_tree_skipped(self, lifecycle_service, mock_repo):
        """Test resuming when entire tree is already running.

        All instances should be skipped.
        """
        root_id = "root"
        child1_id = "child1"
        child2_id = "child2"

        mock_repo.get_tree_root_id.return_value = root_id
        mock_repo.get_tree_ids.return_value = [root_id, child1_id, child2_id]
        mock_repo.get_ancestor_ids.return_value = []

        def get_side_effect(instance_id):
            if instance_id == root_id:
                return self._make_instance(root_id, status="running", children=[child1_id, child2_id])
            elif instance_id in [child1_id, child2_id]:
                return self._make_instance(instance_id, status="running")
            return None

        mock_repo.get.side_effect = get_side_effect

        result = await lifecycle_service.resume_instance_cascade(root_id)

        # Nothing should be resumed
        assert result["resumed_ids"] == []
        assert set(result["skipped_ids"]) == {root_id, child1_id, child2_id}
        mock_repo.update.assert_not_called()


class TestResumeRouterBehavior:
    """Test suite for resume endpoint router behavior (silent=True/False)."""

    @pytest.fixture
    def mock_repo(self):
        """Create a mock instance repository."""
        return MagicMock()

    @pytest.fixture
    def mock_manager(self, mock_repo):
        """Create a mock manager with mocked dependencies."""
        manager = MagicMock()
        manager._instance_repository = mock_repo
        manager._instance_repository.get.return_value = MagicMock(
            instance_id="test-id",
            agent_id="test-agent",
            status="paused",
        )
        manager._live_hub = MagicMock()
        manager._live_hub.stream_status_change = AsyncMock()
        return manager

    def _make_instance(
        self,
        instance_id: str,
        status: str = InstanceStatus.PAUSED.value,
        children: list[str] | None = None,
        agent_id: str = "test-agent",
    ) -> Instance:
        """Create a mock Instance object."""
        instance = MagicMock(spec=Instance)
        instance.instance_id = instance_id
        instance.status = status
        instance.children = children if children is not None else []
        instance.agent_id = agent_id
        return instance

    @pytest.mark.asyncio
    async def test_resume_processing_job_called_with_silent_for_non_targets(self, mock_manager, mock_repo):
        """Test that resume_processing_job is called with silent=True for non-targets.

        When resuming a tree with 3 instances (root, c1, c2):
        - Target instance: silent=False (gets the user message)
        - Non-target instances: silent=True (resume silently from checkpoint)
        """
        from daemon.services.instance_lifecycle import InstanceLifecycleService

        root_id = "root"
        child1_id = "child1"
        child2_id = "child2"
        target_id = child1_id  # Resuming from child1

        # Setup tree
        mock_repo.get_tree_root_id.return_value = root_id
        mock_repo.get_tree_ids.return_value = [root_id, child1_id, child2_id]
        mock_repo.get_ancestor_ids.return_value = [root_id]  # child1's ancestors

        def get_side_effect(instance_id):
            if instance_id == root_id:
                return self._make_instance(root_id, status="paused", children=[child1_id, child2_id])
            elif instance_id in [child1_id, child2_id]:
                return self._make_instance(instance_id, status="paused")
            return None

        mock_repo.get.side_effect = get_side_effect

        # Mock resume_processing_job
        mock_manager.resume_processing_job = AsyncMock(return_value={"status": "ok"})

        service = InstanceLifecycleService.__new__(InstanceLifecycleService)
        service._manager = mock_manager

        # Call the router logic manually (simulating the endpoint)
        instance_id = target_id
        message_text = "user resume message"

        # Simulate the router's resume logic
        result = await service.resume_instance_cascade(instance_id)
        target = result.get("target_id", instance_id)

        resume_results = {}
        for rid in result["resumed_ids"]:
            is_target = rid == target
            job_result = await mock_manager.resume_processing_job(
                rid,
                message=message_text if is_target else "resume",
                silent=not is_target,
            )
            resume_results[rid] = job_result

        # Verify resume_processing_job calls
        calls = mock_manager.resume_processing_job.call_args_list
        assert len(calls) == 3

        # Check silent parameter for each call
        for call in calls:
            instance_id_arg = call[0][0]
            silent_arg = call[1]["silent"]

            if instance_id_arg == target:
                assert silent_arg is False, f"Target {target} should have silent=False"
            else:
                assert silent_arg is True, f"Non-target {instance_id_arg} should have silent=True"

    @pytest.mark.asyncio
    async def test_resume_processing_job_target_gets_user_message(self, mock_manager, mock_repo):
        """Test that the target instance receives the user message, non-targets get 'resume'."""
        from daemon.services.instance_lifecycle import InstanceLifecycleService

        root_id = "root"
        child_id = "child"

        mock_repo.get_tree_root_id.return_value = root_id
        mock_repo.get_tree_ids.return_value = [root_id, child_id]
        mock_repo.get_ancestor_ids.return_value = [root_id]

        def get_side_effect(instance_id):
            if instance_id == root_id:
                return self._make_instance(root_id, status="paused", children=[child_id])
            elif instance_id == child_id:
                return self._make_instance(child_id, status="paused")
            return None

        mock_repo.get.side_effect = get_side_effect

        mock_manager.resume_processing_job = AsyncMock(return_value={"status": "ok"})

        service = InstanceLifecycleService.__new__(InstanceLifecycleService)
        service._manager = mock_manager

        user_message = "my custom resume message"

        # Simulate the router's resume logic
        result = await service.resume_instance_cascade(child_id)
        target = result.get("target_id", child_id)

        for rid in result["resumed_ids"]:
            is_target = rid == target
            await mock_manager.resume_processing_job(
                rid,
                message=user_message if is_target else "resume",
                silent=not is_target,
            )

        # Verify messages
        calls = mock_manager.resume_processing_job.call_args_list
        for call in calls:
            instance_id_arg = call[0][0]
            message_arg = call[1]["message"]

            if instance_id_arg == target:
                assert message_arg == user_message, f"Target should get user message"
            else:
                assert message_arg == "resume", f"Non-target should get 'resume' message"


class TestWaitingForSemantics:
    """Test suite for waiting_for counter semantics (critical design requirement)."""

    @pytest.fixture
    def mock_repo(self):
        """Create a mock instance repository."""
        return MagicMock()

    @pytest.fixture
    def mock_manager(self, mock_repo):
        """Create a mock manager with mocked dependencies."""
        manager = MagicMock()
        manager._instance_repository = mock_repo
        manager._request_registry = MagicMock()
        manager._live_hub = MagicMock()
        manager._live_hub.stream_status_change = AsyncMock()
        manager._graph_tasks = {}
        return manager

    @pytest.fixture
    def lifecycle_service(self, mock_manager):
        """Create an InstanceLifecycleService with mocked manager."""
        from daemon.services.instance_lifecycle import InstanceLifecycleService
        service = InstanceLifecycleService.__new__(InstanceLifecycleService)
        service._manager = mock_manager
        return service

    def _make_instance(
        self,
        instance_id: str,
        status: str = InstanceStatus.RUNNING.value,
        children: list[str] | None = None,
        waiting_for: int = 0,
    ) -> Instance:
        """Create a mock Instance object."""
        instance = MagicMock(spec=Instance)
        instance.instance_id = instance_id
        instance.status = status
        instance.children = children if children is not None else []
        instance.waiting_for = waiting_for
        instance.agent_id = "test-agent"
        return instance

    @pytest.mark.asyncio
    async def test_pause_resets_all_waiting_for_regardless_of_previous_value(self, lifecycle_service, mock_repo):
        """Test that pause ALWAYS resets waiting_for to 0, regardless of previous value.

        This is critical to prevent deadlock on resume when children are also paused.
        """
        root_id = "root"
        child_id = "child"

        mock_repo.get_tree_root_id.return_value = root_id
        mock_repo.get_tree_ids.return_value = [root_id, child_id]

        # Parent has high waiting_for value (waiting for many children)
        def get_side_effect(instance_id):
            if instance_id == root_id:
                return self._make_instance(root_id, status="running", children=[child_id], waiting_for=99)
            elif instance_id == child_id:
                return self._make_instance(child_id, status="running", waiting_for=5)
            return None

        mock_repo.get.side_effect = get_side_effect

        await lifecycle_service.pause_instance_cascade(root_id)

        # Both should have waiting_for=0
        update_calls = {call[0][0]: call[1] for call in mock_repo.update.call_args_list}
        assert update_calls[root_id]["waiting_for"] == 0
        assert update_calls[child_id]["waiting_for"] == 0

    @pytest.mark.asyncio
    async def test_resume_from_root_all_waiting_for_stay_zero(self, lifecycle_service, mock_repo):
        """Test that resume from root leaves all waiting_for at 0.

        No ancestors exist when resuming from root, so no waiting is needed.
        """
        root_id = "root"
        child_id = "child"

        mock_repo.get_tree_root_id.return_value = root_id
        mock_repo.get_tree_ids.return_value = [root_id, child_id]
        mock_repo.get_ancestor_ids.return_value = []

        def get_side_effect(instance_id):
            if instance_id == root_id:
                return self._make_instance(root_id, status="paused", children=[child_id])
            elif instance_id == child_id:
                return self._make_instance(child_id, status="paused")
            return None

        mock_repo.get.side_effect = get_side_effect

        await lifecycle_service.resume_instance_cascade(root_id)

        # Both should have waiting_for=0
        update_calls = {call[0][0]: call[1] for call in mock_repo.update.call_args_list}
        assert update_calls[root_id]["waiting_for"] == 0
        assert update_calls[child_id]["waiting_for"] == 0

    @pytest.mark.asyncio
    async def test_resume_from_child_ancestors_get_waiting_for_one(self, lifecycle_service, mock_repo):
        """Test that resuming from child sets ONLY ancestors to waiting_for=1.

        Non-ancestors (siblings, the resumed node itself) get waiting_for=0.
        """
        root_id = "root"
        child1_id = "child1"
        child2_id = "child2"
        child3_id = "child3"

        mock_repo.get_tree_root_id.return_value = root_id
        mock_repo.get_tree_ids.return_value = [root_id, child1_id, child2_id, child3_id]
        # Resuming from child2, so ancestors = [root]
        mock_repo.get_ancestor_ids.return_value = [root_id]

        def get_side_effect(instance_id):
            if instance_id == root_id:
                return self._make_instance(root_id, status="paused", children=[child1_id, child2_id, child3_id])
            elif instance_id in [child1_id, child2_id, child3_id]:
                return self._make_instance(instance_id, status="paused")
            return None

        mock_repo.get.side_effect = get_side_effect

        await lifecycle_service.resume_instance_cascade(child2_id)

        update_calls = {call[0][0]: call[1] for call in mock_repo.update.call_args_list}

        # Only root (ancestor) gets waiting_for=1
        assert update_calls[root_id]["waiting_for"] == 1
        # Siblings get waiting_for=0
        assert update_calls[child1_id]["waiting_for"] == 0
        assert update_calls[child3_id]["waiting_for"] == 0
        # Resumed node gets waiting_for=0
        assert update_calls[child2_id]["waiting_for"] == 0

    @pytest.mark.asyncio
    async def test_resume_from_leaf_full_ancestor_chain_waiting_for_one(self, lifecycle_service, mock_repo):
        """Test that resuming from leaf sets FULL ancestor chain to waiting_for=1.

        This ensures parent instances wait for their children to complete
        after a deep resume operation.
        """
        root_id = "root"
        l1_id = "level1"
        l2_id = "level2"
        leaf_id = "leaf"

        mock_repo.get_tree_root_id.return_value = root_id
        mock_repo.get_tree_ids.return_value = [root_id, l1_id, l2_id, leaf_id]
        # Leaf's ancestors: [l2, l1, root]
        mock_repo.get_ancestor_ids.return_value = [l2_id, l1_id, root_id]

        def get_side_effect(instance_id):
            children_map = {root_id: [l1_id], l1_id: [l2_id], l2_id: [leaf_id]}
            if instance_id in children_map:
                return self._make_instance(instance_id, status="paused", children=children_map[instance_id])
            elif instance_id == leaf_id:
                return self._make_instance(leaf_id, status="paused")
            return None

        mock_repo.get.side_effect = get_side_effect

        await lifecycle_service.resume_instance_cascade(leaf_id)

        update_calls = {call[0][0]: call[1] for call in mock_repo.update.call_args_list}

        # All ancestors get waiting_for=1
        assert update_calls[root_id]["waiting_for"] == 1
        assert update_calls[l1_id]["waiting_for"] == 1
        assert update_calls[l2_id]["waiting_for"] == 1
        # Resumed node gets waiting_for=0
        assert update_calls[leaf_id]["waiting_for"] == 0

    @pytest.mark.asyncio
    async def test_waiting_for_semantics_in_complex_tree(self, lifecycle_service, mock_repo):
        """Test waiting_for semantics in a complex tree with multiple branches.

        Hierarchy:
                    root
                   / | \
                  l1 l2 l3
                 /|     |
                m1 m2   m3

        Resuming from m2 (leaf under l1):
        - ancestors: [l1, root]
        - l1 gets waiting_for=1 (direct parent)
        - root gets waiting_for=1 (grandparent)
        - l2, l3 get waiting_for=0 (not ancestors)
        - m1, m3 get waiting_for=0 (not ancestors, not resumed node)
        - m2 gets waiting_for=0 (resumed node)
        """
        root_id = "root"
        l1_id, l2_id, l3_id = "l1", "l2", "l3"
        m1_id, m2_id, m3_id = "m1", "m2", "m3"

        mock_repo.get_tree_root_id.return_value = root_id
        mock_repo.get_tree_ids.return_value = [root_id, l1_id, l2_id, l3_id, m1_id, m2_id, m3_id]
        # m2's ancestors: [l1, root]
        mock_repo.get_ancestor_ids.return_value = [l1_id, root_id]

        def get_side_effect(instance_id):
            children_map = {
                root_id: [l1_id, l2_id, l3_id],
                l1_id: [m1_id, m2_id],
                l2_id: [],  # l2 has no children
                l3_id: [m3_id],
            }
            if instance_id in children_map:
                return self._make_instance(instance_id, status="paused", children=children_map[instance_id])
            elif instance_id in [m1_id, m2_id, m3_id]:
                return self._make_instance(instance_id, status="paused")
            return None

        mock_repo.get.side_effect = get_side_effect

        await lifecycle_service.resume_instance_cascade(m2_id)

        update_calls = {call[0][0]: call[1] for call in mock_repo.update.call_args_list}

        # Ancestors get waiting_for=1
        assert update_calls[root_id]["waiting_for"] == 1
        assert update_calls[l1_id]["waiting_for"] == 1
        # Non-ancestors get waiting_for=0
        assert update_calls[l2_id]["waiting_for"] == 0
        assert update_calls[l3_id]["waiting_for"] == 0
        assert update_calls[m1_id]["waiting_for"] == 0
        assert update_calls[m3_id]["waiting_for"] == 0
        # Resumed node gets waiting_for=0
        assert update_calls[m2_id]["waiting_for"] == 0


class TestEdgeCases:
    """Test suite for edge cases and error handling."""

    @pytest.fixture
    def mock_repo(self):
        """Create a mock instance repository."""
        return MagicMock()

    @pytest.fixture
    def mock_manager(self, mock_repo):
        """Create a mock manager with mocked dependencies."""
        manager = MagicMock()
        manager._instance_repository = mock_repo
        manager._request_registry = MagicMock()
        manager._live_hub = MagicMock()
        manager._live_hub.stream_status_change = AsyncMock()
        manager._graph_tasks = {}
        return manager

    @pytest.fixture
    def lifecycle_service(self, mock_manager):
        """Create an InstanceLifecycleService with mocked manager."""
        from daemon.services.instance_lifecycle import InstanceLifecycleService
        service = InstanceLifecycleService.__new__(InstanceLifecycleService)
        service._manager = mock_manager
        return service

    def _make_instance(
        self,
        instance_id: str,
        status: str = InstanceStatus.RUNNING.value,
        children: list[str] | None = None,
        waiting_for: int = 0,
    ) -> Instance:
        """Create a mock Instance object."""
        instance = MagicMock(spec=Instance)
        instance.instance_id = instance_id
        instance.status = status
        instance.children = children if children is not None else []
        instance.waiting_for = waiting_for
        instance.agent_id = "test-agent"
        return instance

    @pytest.mark.asyncio
    async def test_instance_not_found_pause_graceful_handling(self, lifecycle_service, mock_repo):
        """Test that pausing a non-existent instance handles gracefully."""
        instance_id = "nonexistent-instance"

        mock_repo.get_tree_root_id.return_value = None
        mock_repo.get_tree_ids.return_value = [instance_id]
        mock_repo.get.return_value = None

        result = await lifecycle_service.pause_instance_cascade(instance_id)

        assert result["paused_ids"] == []
        assert result["skipped_ids"] == [instance_id]
        mock_repo.update.assert_not_called()

    @pytest.mark.asyncio
    async def test_instance_not_found_resume_graceful_handling(self, lifecycle_service, mock_repo):
        """Test that resuming a non-existent instance handles gracefully."""
        instance_id = "nonexistent-instance"

        mock_repo.get_tree_root_id.return_value = None
        mock_repo.get_tree_ids.return_value = [instance_id]
        mock_repo.get_ancestor_ids.return_value = []
        mock_repo.get.return_value = None

        result = await lifecycle_service.resume_instance_cascade(instance_id)

        assert result["resumed_ids"] == []
        assert result["skipped_ids"] == [instance_id]
        assert result["target_id"] == instance_id
        mock_repo.update.assert_not_called()

    @pytest.mark.asyncio
    async def test_pause_exception_handling_does_not_block_siblings(self, lifecycle_service, mock_repo):
        """Test that exception during pause doesn't block siblings.

        When one instance fails to pause, others should still be paused.
        """
        root_id = "root"
        child1_id = "child1"
        child2_id = "child2"

        mock_repo.get_tree_root_id.return_value = root_id
        mock_repo.get_tree_ids.return_value = [root_id, child1_id, child2_id]

        def get_side_effect(instance_id):
            if instance_id == root_id:
                return self._make_instance(root_id, status="running", children=[child1_id, child2_id])
            elif instance_id == child1_id:
                # Simulate failure for child1
                raise RuntimeError("Database error")
            elif instance_id == child2_id:
                return self._make_instance(child2_id, status="running")
            return None

        mock_repo.get.side_effect = get_side_effect

        result = await lifecycle_service.pause_instance_cascade(root_id)

        # root and child2 should be paused, child1 should be skipped
        assert set(result["paused_ids"]) == {root_id, child2_id}
        assert result["skipped_ids"] == [child1_id]
        assert mock_repo.update.call_count == 2

    @pytest.mark.asyncio
    async def test_resume_exception_handling_does_not_block_siblings(self, lifecycle_service, mock_repo):
        """Test that exception during resume doesn't block siblings.

        The implementation catches exceptions per-instance, so siblings should still be processed.
        """
        root_id = "root"
        child1_id = "child1"
        child2_id = "child2"

        mock_repo.get_tree_root_id.return_value = root_id
        mock_repo.get_tree_ids.return_value = [root_id, child1_id, child2_id]
        mock_repo.get_ancestor_ids.return_value = []

        call_count = [0]

        def get_side_effect(instance_id):
            if instance_id == root_id:
                return self._make_instance(root_id, status="paused", children=[child1_id, child2_id])
            elif instance_id == child1_id:
                call_count[0] += 1
                # First call for child1 returns instance, second call throws
                if call_count[0] == 1:
                    return self._make_instance(child1_id, status="paused")
                else:
                    raise RuntimeError("Database error")
            elif instance_id == child2_id:
                return self._make_instance(child2_id, status="paused")
            return None

        mock_repo.get.side_effect = get_side_effect

        # Make update raise exception for child1
        update_results = {}
        def update_side_effect(*args, **kwargs):
            instance_id = args[0]
            if instance_id == child1_id:
                raise RuntimeError("Database error")
            update_results[instance_id] = kwargs
            return None

        mock_repo.update.side_effect = update_side_effect

        result = await lifecycle_service.resume_instance_cascade(root_id)

        # root and child2 should be resumed, child1 skipped due to exception
        assert set(result["resumed_ids"]) == {root_id, child2_id}
        assert result["skipped_ids"] == [child1_id]

    @pytest.mark.asyncio
    async def test_pause_with_already_paused_children_skips_them(self, lifecycle_service, mock_repo):
        """Test that pause skips children that are already paused.

        Note: The implementation only skips PAUSED status. COMPLETED/ERROR/TERMINATED
        children would be attempted to be paused (though they may fail silently).
        """
        root_id = "root"
        running_child_id = "running-child"
        paused_child_id = "paused-child"

        mock_repo.get_tree_root_id.return_value = root_id
        mock_repo.get_tree_ids.return_value = [root_id, running_child_id, paused_child_id]

        def get_side_effect(instance_id):
            if instance_id == root_id:
                return self._make_instance(
                    root_id,
                    status="running",
                    children=[running_child_id, paused_child_id],
                )
            elif instance_id == running_child_id:
                return self._make_instance(running_child_id, status="running")
            elif instance_id == paused_child_id:
                return self._make_instance(paused_child_id, status="paused")
            return None

        mock_repo.get.side_effect = get_side_effect

        result = await lifecycle_service.pause_instance_cascade(root_id)

        # Only root and running child should be paused
        assert set(result["paused_ids"]) == {root_id, running_child_id}
        # Already paused child should be skipped
        assert set(result["skipped_ids"]) == {paused_child_id}

    @pytest.mark.asyncio
    async def test_empty_tree_single_instance_pause_resume(self, lifecycle_service, mock_repo):
        """Test pause/resume for a single instance with no children (no tree)."""
        instance_id = "single"

        # Single instance is its own root
        mock_repo.get_tree_root_id.return_value = instance_id
        mock_repo.get_tree_ids.return_value = [instance_id]

        # Pause test
        mock_repo.get.return_value = self._make_instance(instance_id, status="running")
        result = await lifecycle_service.pause_instance_cascade(instance_id)

        assert result["paused_ids"] == [instance_id]
        assert result["skipped_ids"] == []
        mock_repo.update.assert_called_once()

        # Reset mock for resume test
        mock_repo.get.return_value = self._make_instance(instance_id, status="paused")
        mock_repo.update.reset_mock()
        mock_repo.get_ancestor_ids.return_value = []

        result = await lifecycle_service.resume_instance_cascade(instance_id)

        assert result["resumed_ids"] == [instance_id]
        assert result["skipped_ids"] == []
        assert result["target_id"] == instance_id
        mock_repo.update.assert_called_once()
        assert mock_repo.update.call_args[1]["waiting_for"] == 0
