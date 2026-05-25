"""Tests for pause_instance_cascade functionality.

Tests the cascade pause feature that recursively pauses instances and their children
using DFS traversal. Verifies proper handling of:
- Single instances without children
- Instances with direct children
- Nested child hierarchies
- Already-paused instances (skip behavior)
- Mixed status children
- Non-existent instances
"""

import pytest
from unittest.mock import MagicMock, AsyncMock

from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.cancellation import CancellationReason


class TestPauseInstanceCascade:
    """Test suite for pause_instance_cascade functionality."""

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
        # Mock live_hub with async stream_status_change
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
        status: str = InstanceStatus.RUNNING.value,
        children: list[str] | None = None,
        waiting_for: int = 0,
    ) -> Instance:
        """Create a mock Instance object.

        Note: The repository's _enrich_instance converts children from JSON string
        to a Python list, so we mock with a list directly.
        """
        instance = MagicMock(spec=Instance)
        instance.instance_id = instance_id
        instance.status = status
        instance.children = children if children is not None else []
        instance.waiting_for = waiting_for
        return instance

    @pytest.mark.asyncio
    async def test_pause_single_instance_no_children(self, lifecycle_service, mock_repo, mock_registry):
        """Test pausing a single instance with no children.

        Verifies:
        - paused_ids contains the instance
        - skipped_ids is empty
        - cancel_by_instance called with USER_STOPPED
        - update called with status='paused' and paused_at
        """
        instance_id = "test-instance-123"
        # First call: get instance at line 485
        # Second call: _pause_single at line 433
        mock_repo.get.return_value = self._make_instance(instance_id, status="running")

        result = await lifecycle_service.pause_instance_cascade(instance_id)

        assert result["paused_ids"] == [instance_id]
        assert result["skipped_ids"] == []
        mock_registry.cancel_by_instance.assert_called_once_with(
            instance_id, CancellationReason.USER_STOPPED
        )
        # Now uses update() with paused_at instead of update_status()
        mock_repo.update.assert_called_once()
        call_kwargs = mock_repo.update.call_args[1]
        assert call_kwargs["status"] == "paused"
        assert "paused_at" in call_kwargs
        assert call_kwargs["paused_at"] is not None

    @pytest.mark.asyncio
    async def test_pause_instance_with_children(self, lifecycle_service, mock_repo, mock_registry):
        """Test pausing a parent instance with direct children.

        Verifies:
        - paused_ids contains parent and all children
        - DFS traversal pauses children first, then parent
        - update called 3 times (once per instance) with paused_at
        """
        parent_id = "parent-instance"
        child1_id = "child-1"
        child2_id = "child-2"

        def get_side_effect(instance_id):
            if instance_id == parent_id:
                return self._make_instance(parent_id, status="running", children=[child1_id, child2_id])
            elif instance_id == child1_id:
                return self._make_instance(child1_id, status="running")
            elif instance_id == child2_id:
                return self._make_instance(child2_id, status="running")
            return None

        mock_repo.get.side_effect = get_side_effect

        result = await lifecycle_service.pause_instance_cascade(parent_id)

        # All instances should be paused (children first, then parent)
        assert set(result["paused_ids"]) == {parent_id, child1_id, child2_id}
        assert result["skipped_ids"] == []
        # Now uses update() with paused_at instead of update_status()
        assert mock_repo.update.call_count == 3
        # Verify all updates have paused_at
        for call in mock_repo.update.call_args_list:
            assert "paused_at" in call[1]
            assert call[1]["status"] == "paused"

    @pytest.mark.asyncio
    async def test_pause_instance_with_nested_children(self, lifecycle_service, mock_repo, mock_registry):
        """Test pausing an instance with nested children (grandchildren).

        Hierarchy:
            parent
            ├── child1
            │   └── grandchild1
            └── child2

        Verifies:
        - All 4 instances are paused
        - DFS traversal: child1 → grandchild1 → child2 → parent
        - All updates include paused_at
        """
        parent_id = "parent"
        child1_id = "child1"
        child2_id = "child2"
        grandchild_id = "grandchild1"

        def get_side_effect(instance_id):
            if instance_id == parent_id:
                return self._make_instance(parent_id, status="running", children=[child1_id, child2_id])
            elif instance_id == child1_id:
                return self._make_instance(child1_id, status="running", children=[grandchild_id])
            elif instance_id == child2_id:
                return self._make_instance(child2_id, status="running")
            elif instance_id == grandchild_id:
                return self._make_instance(grandchild_id, status="running")
            return None

        mock_repo.get.side_effect = get_side_effect

        result = await lifecycle_service.pause_instance_cascade(parent_id)

        assert set(result["paused_ids"]) == {parent_id, child1_id, child2_id, grandchild_id}
        assert result["skipped_ids"] == []
        # Now uses update() with paused_at
        assert mock_repo.update.call_count == 4

    @pytest.mark.asyncio
    async def test_pause_already_paused_instance(self, lifecycle_service, mock_repo, mock_registry):
        """Test pausing an instance that is already paused.

        Verifies:
        - paused_ids is empty
        - skipped_ids contains the instance
        - cancel_by_instance NOT called
        - update NOT called (no change needed)
        """
        instance_id = "paused-instance"
        mock_repo.get.return_value = self._make_instance(instance_id, status="paused")

        result = await lifecycle_service.pause_instance_cascade(instance_id)

        assert result["paused_ids"] == []
        assert result["skipped_ids"] == [instance_id]
        mock_registry.cancel_by_instance.assert_not_called()
        # No update when already paused
        mock_repo.update.assert_not_called()

    @pytest.mark.asyncio
    async def test_pause_mixed_status_children(self, lifecycle_service, mock_repo, mock_registry):
        """Test pausing a parent when children have mixed status.

        Scenario:
        - parent: running
        - child1: running
        - child2: paused

        Verifies:
        - parent and child1 are paused
        - child2 is skipped
        """
        parent_id = "parent"
        child1_id = "child1-running"
        child2_id = "child2-paused"

        def get_side_effect(instance_id):
            if instance_id == parent_id:
                return self._make_instance(parent_id, status="running", children=[child1_id, child2_id])
            elif instance_id == child1_id:
                return self._make_instance(child1_id, status="running")
            elif instance_id == child2_id:
                return self._make_instance(child2_id, status="paused")
            return None

        mock_repo.get.side_effect = get_side_effect

        result = await lifecycle_service.pause_instance_cascade(parent_id)

        assert set(result["paused_ids"]) == {parent_id, child1_id}
        assert result["skipped_ids"] == [child2_id]
        # Only 2 instances should have update called with paused_at
        assert mock_repo.update.call_count == 2

    @pytest.mark.asyncio
    async def test_pause_nonexistent_instance(self, lifecycle_service, mock_repo, mock_registry):
        """Test pausing a non-existent instance.

        Verifies:
        - paused_ids is empty
        - skipped_ids is empty
        - No crashes or errors
        - No interactions with registry or repo update
        """
        instance_id = "nonexistent-instance"
        mock_repo.get.return_value = None

        result = await lifecycle_service.pause_instance_cascade(instance_id)

        assert result["paused_ids"] == []
        assert result["skipped_ids"] == []
        mock_registry.cancel_by_instance.assert_not_called()
        mock_repo.update.assert_not_called()

    @pytest.mark.asyncio
    async def test_pause_child_becomes_paused_during_cascade(self, lifecycle_service, mock_repo, mock_registry):
        """Test that an already-paused child is skipped during cascade.

        This tests the case where a child exists but is already paused when
        the cascade reaches it.
        """
        parent_id = "parent"
        child_id = "child"

        def get_side_effect(instance_id):
            if instance_id == parent_id:
                return self._make_instance(parent_id, status="running", children=[child_id])
            elif instance_id == child_id:
                return self._make_instance(child_id, status="paused")
            return None

        mock_repo.get.side_effect = get_side_effect

        result = await lifecycle_service.pause_instance_cascade(parent_id)

        assert result["paused_ids"] == [parent_id]
        assert result["skipped_ids"] == [child_id]
        # Only parent should have status updated
        assert mock_repo.update.call_count == 1
        call_kwargs = mock_repo.update.call_args[1]
        assert call_kwargs["status"] == "paused"
        assert "paused_at" in call_kwargs

    @pytest.mark.asyncio
    async def test_pause_child_with_grandchildren_mixed_status(self, lifecycle_service, mock_repo, mock_registry):
        """Test cascade pause with nested children having mixed status.

        Hierarchy:
            parent (running)
            └── child (running)
                └── grandchild (paused)

        Verifies:
        - parent and child are paused
        - grandchild is skipped
        """
        parent_id = "parent"
        child_id = "child"
        grandchild_id = "grandchild"

        def get_side_effect(instance_id):
            if instance_id == parent_id:
                return self._make_instance(parent_id, status="running", children=[child_id])
            elif instance_id == child_id:
                return self._make_instance(child_id, status="running", children=[grandchild_id])
            elif instance_id == grandchild_id:
                return self._make_instance(grandchild_id, status="paused")
            return None

        mock_repo.get.side_effect = get_side_effect

        result = await lifecycle_service.pause_instance_cascade(parent_id)

        assert set(result["paused_ids"]) == {parent_id, child_id}
        assert result["skipped_ids"] == [grandchild_id]
        # Parent and child should have status updated (not grandchild)
        assert mock_repo.update.call_count == 2

    @pytest.mark.asyncio
    async def test_pause_circular_reference_detected(self, lifecycle_service, mock_repo, mock_registry):
        """Test that circular references are detected and skipped.

        Scenario: Parent's children includes itself (A -> [A]).

        Verifies:
        - No infinite recursion (warning is logged)
        - The circular child is added to skipped_ids during cascade
        - Parent still gets paused (root call proceeds)
        """
        instance_id = "circular-instance"

        # Create an instance where children includes itself
        mock_repo.get.return_value = self._make_instance(
            instance_id, status="running", children=[instance_id]
        )

        result = await lifecycle_service.pause_instance_cascade(instance_id)

        # Parent is paused (root call proceeds)
        assert result["paused_ids"] == [instance_id]
        # The circular reference is detected during recursion and skipped
        assert result["skipped_ids"] == [instance_id]
        # Only one update should be called
        assert mock_repo.update.call_count == 1

    @pytest.mark.asyncio
    async def test_pause_child_exception_does_not_block_siblings(self, lifecycle_service, mock_repo, mock_registry):
        """Test that an exception when pausing one child doesn't block siblings.

        Scenario:
        - parent: running, children = [child1, child2, child3]
        - child2 raises an exception when accessed

        Verifies:
        - child1 and child3 are paused successfully
        - child2 is in skipped_ids
        - No crash occurs
        """
        parent_id = "parent"
        child1_id = "child1"
        child2_id = "child2"
        child3_id = "child3"

        def get_side_effect(instance_id):
            if instance_id == parent_id:
                return self._make_instance(parent_id, status="running", children=[child1_id, child2_id, child3_id])
            elif instance_id == child1_id:
                return self._make_instance(child1_id, status="running")
            elif instance_id == child2_id:
                # Simulate exception on child2 access
                raise RuntimeError(f"Failed to get instance {child2_id}")
            elif instance_id == child3_id:
                return self._make_instance(child3_id, status="running")
            return None

        mock_repo.get.side_effect = get_side_effect

        result = await lifecycle_service.pause_instance_cascade(parent_id)

        # child1 and child3 should be paused, child2 skipped due to exception
        assert set(result["paused_ids"]) == {parent_id, child1_id, child3_id}
        assert result["skipped_ids"] == [child2_id]
        # Parent, child1, and child3 should have status updated (child2 not)
        assert mock_repo.update.call_count == 3

    @pytest.mark.asyncio
    async def test_pause_depth_limit_protection(self, lifecycle_service, mock_repo, mock_registry):
        """Test that depth limit protection prevents excessive recursion.

        Scenario: Call with _depth=257 (exceeds limit of 256).

        Verifies:
        - Returns immediately with skipped_ids containing the instance
        - No further processing occurs
        """
        instance_id = "deep-instance"
        mock_repo.get.return_value = self._make_instance(instance_id, status="running")

        # Call with _depth exceeding the limit (257 > 256)
        result = await lifecycle_service.pause_instance_cascade(instance_id, _depth=257)

        # Should skip due to depth limit
        assert result["skipped_ids"] == [instance_id]
        assert result["paused_ids"] == []
        # Should not attempt to pause or update status
        mock_registry.cancel_by_instance.assert_not_called()
        mock_repo.update.assert_not_called()

    @pytest.mark.asyncio
    async def test_pause_parent_with_waiting_for_resets_counter(self, lifecycle_service, mock_repo, mock_registry):
        """Test that pausing a parent with waiting_for=3 resets waiting_for to 0.

        Scenario:
        - parent: running, waiting_for=3 (waiting for 3 children)
        - Child is not paused (only parent is tested)

        Verifies:
        - waiting_for is reset to 0 to prevent deadlock on resume
        - update() is called with status='paused', waiting_for=0, and paused_at
        """
        parent_id = "parent-waiting"

        mock_repo.get.return_value = self._make_instance(
            parent_id, status="running", waiting_for=3
        )

        result = await lifecycle_service.pause_instance_cascade(parent_id)

        assert result["paused_ids"] == [parent_id]
        assert result["skipped_ids"] == []
        # Should use update() with status, waiting_for=0, and paused_at
        mock_repo.update.assert_called_once()
        call_kwargs = mock_repo.update.call_args[1]
        assert call_kwargs["status"] == "paused"
        assert call_kwargs["waiting_for"] == 0
        assert "paused_at" in call_kwargs

    @pytest.mark.asyncio
    async def test_pause_parent_with_waiting_for_zero_no_change(self, lifecycle_service, mock_repo, mock_registry):
        """Test that pausing a parent with waiting_for=0 works correctly.

        Scenario:
        - parent: running, waiting_for=0 (not waiting for children)

        Verifies:
        - update() is called with status='paused' and paused_at
        """
        parent_id = "parent-not-waiting"

        mock_repo.get.return_value = self._make_instance(
            parent_id, status="running", waiting_for=0
        )

        result = await lifecycle_service.pause_instance_cascade(parent_id)

        assert result["paused_ids"] == [parent_id]
        assert result["skipped_ids"] == []
        # Should use update() with status and paused_at
        mock_repo.update.assert_called_once()
        call_kwargs = mock_repo.update.call_args[1]
        assert call_kwargs["status"] == "paused"
        assert "paused_at" in call_kwargs

    @pytest.mark.asyncio
    async def test_pause_instance_without_children_no_change(self, lifecycle_service, mock_repo, mock_registry):
        """Test that pausing an instance without children (not a parent) works.

        Scenario:
        - instance: running, no children, waiting_for=0

        Verifies:
        - Normal pause behavior, no errors
        - update() is called with status='paused' and paused_at
        """
        instance_id = "leaf-instance"

        mock_repo.get.return_value = self._make_instance(
            instance_id, status="running", children=[], waiting_for=0
        )

        result = await lifecycle_service.pause_instance_cascade(instance_id)

        assert result["paused_ids"] == [instance_id]
        assert result["skipped_ids"] == []
        # Should use update() with status and paused_at
        mock_repo.update.assert_called_once()
        call_kwargs = mock_repo.update.call_args[1]
        assert call_kwargs["status"] == "paused"
        assert "paused_at" in call_kwargs


class TestResumeInstanceCascade:
    """Test suite for resume_instance_cascade functionality."""

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
        # Mock live_hub with async stream_status_change
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
    async def test_resume_single_instance_no_children(self, lifecycle_service, mock_repo):
        """Test resuming a single paused instance with no children.
        
        Verifies:
        - resumed_ids contains the instance
        - skipped_ids is empty
        - update called with status='running' and paused_at=None
        """
        instance_id = "test-instance-123"
        mock_repo.get.return_value = self._make_instance(instance_id, status="paused")

        result = await lifecycle_service.resume_instance_cascade(instance_id)

        assert result["resumed_ids"] == [instance_id]
        assert result["skipped_ids"] == []
        mock_repo.update.assert_called_once()
        call_kwargs = mock_repo.update.call_args[1]
        assert call_kwargs["status"] == "running"
        assert call_kwargs["paused_at"] is None

    @pytest.mark.asyncio
    async def test_resume_instance_with_children(self, lifecycle_service, mock_repo):
        """Test resuming a parent instance with direct children."""
        parent_id = "parent-instance"
        child1_id = "child-1"
        child2_id = "child-2"

        def get_side_effect(instance_id):
            if instance_id == parent_id:
                return self._make_instance(parent_id, status="paused", children=[child1_id, child2_id])
            elif instance_id == child1_id:
                return self._make_instance(child1_id, status="paused")
            elif instance_id == child2_id:
                return self._make_instance(child2_id, status="paused")
            return None

        mock_repo.get.side_effect = get_side_effect

        result = await lifecycle_service.resume_instance_cascade(parent_id)

        assert set(result["resumed_ids"]) == {parent_id, child1_id, child2_id}
        assert result["skipped_ids"] == []
        assert mock_repo.update.call_count == 3
        for call in mock_repo.update.call_args_list:
            assert call[1]["status"] == "running"
            assert call[1]["paused_at"] is None

    @pytest.mark.asyncio
    async def test_resume_already_running_instance(self, lifecycle_service, mock_repo):
        """Test resuming an instance that is already running (not paused).
        
        Verifies:
        - resumed_ids is empty
        - skipped_ids contains the instance
        - update NOT called
        """
        instance_id = "running-instance"
        mock_repo.get.return_value = self._make_instance(instance_id, status="running")

        result = await lifecycle_service.resume_instance_cascade(instance_id)

        assert result["resumed_ids"] == []
        assert result["skipped_ids"] == [instance_id]
        mock_repo.update.assert_not_called()

    @pytest.mark.asyncio
    async def test_resume_mixed_status_children(self, lifecycle_service, mock_repo):
        """Test resuming when children have mixed status."""
        parent_id = "parent"
        child1_id = "child1-paused"
        child2_id = "child2-running"

        def get_side_effect(instance_id):
            if instance_id == parent_id:
                return self._make_instance(parent_id, status="paused", children=[child1_id, child2_id])
            elif instance_id == child1_id:
                return self._make_instance(child1_id, status="paused")
            elif instance_id == child2_id:
                return self._make_instance(child2_id, status="running")
            return None

        mock_repo.get.side_effect = get_side_effect

        result = await lifecycle_service.resume_instance_cascade(parent_id)

        assert set(result["resumed_ids"]) == {parent_id, child1_id}
        assert result["skipped_ids"] == [child2_id]
        assert mock_repo.update.call_count == 2

    @pytest.mark.asyncio
    async def test_resume_nonexistent_instance(self, lifecycle_service, mock_repo):
        """Test resuming a non-existent instance."""
        instance_id = "nonexistent-instance"
        mock_repo.get.return_value = None

        result = await lifecycle_service.resume_instance_cascade(instance_id)

        assert result["resumed_ids"] == []
        assert result["skipped_ids"] == []
        mock_repo.update.assert_not_called()
