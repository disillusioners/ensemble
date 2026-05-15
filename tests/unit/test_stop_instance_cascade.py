"""Tests for stop_instance_cascade functionality.

Tests the cascade stop feature that recursively stops instances and their children
using DFS traversal. Verifies proper handling of:
- Single instances without children
- Instances with direct children
- Nested child hierarchies
- Already-idle instances (skip behavior)
- Mixed status children
- Non-existent instances
"""

import pytest
from unittest.mock import MagicMock, AsyncMock

from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.cancellation import CancellationReason


class TestStopInstanceCascade:
    """Test suite for stop_instance_cascade functionality."""

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
    ) -> Instance:
        """Create a mock Instance object.

        Note: The repository's _enrich_instance converts children from JSON string
        to a Python list, so we mock with a list directly.
        """
        instance = MagicMock(spec=Instance)
        instance.instance_id = instance_id
        instance.status = status
        instance.children = children if children is not None else []
        return instance

    @pytest.mark.asyncio
    async def test_stop_single_instance_no_children(self, lifecycle_service, mock_repo, mock_registry):
        """Test stopping a single instance with no children.

        Verifies:
        - stopped_ids contains the instance
        - skipped_ids is empty
        - cancel_by_instance called with USER_STOPPED
        - update_status called with "idle"
        """
        instance_id = "test-instance-123"
        # First call: get instance at line 415
        # Second call: _stop_single at line 390
        mock_repo.get.return_value = self._make_instance(instance_id, status="running")

        result = await lifecycle_service.stop_instance_cascade(instance_id)

        assert result["stopped_ids"] == [instance_id]
        assert result["skipped_ids"] == []
        mock_registry.cancel_by_instance.assert_called_once_with(
            instance_id, CancellationReason.USER_STOPPED
        )
        mock_repo.update_status.assert_called_once_with(instance_id, "idle")

    @pytest.mark.asyncio
    async def test_stop_instance_with_children(self, lifecycle_service, mock_repo, mock_registry):
        """Test stopping a parent instance with direct children.

        Verifies:
        - stopped_ids contains parent and all children
        - DFS traversal stops children first, then parent
        - update_status called 3 times (once per instance)
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

        result = await lifecycle_service.stop_instance_cascade(parent_id)

        # All instances should be stopped (children first, then parent)
        assert set(result["stopped_ids"]) == {parent_id, child1_id, child2_id}
        assert result["skipped_ids"] == []
        assert mock_repo.update_status.call_count == 3

    @pytest.mark.asyncio
    async def test_stop_instance_with_nested_children(self, lifecycle_service, mock_repo, mock_registry):
        """Test stopping an instance with nested children (grandchildren).

        Hierarchy:
            parent
            ├── child1
            │   └── grandchild1
            └── child2

        Verifies:
        - All 4 instances are stopped
        - DFS traversal: child1 → grandchild1 → child2 → parent
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

        result = await lifecycle_service.stop_instance_cascade(parent_id)

        assert set(result["stopped_ids"]) == {parent_id, child1_id, child2_id, grandchild_id}
        assert result["skipped_ids"] == []
        assert mock_repo.update_status.call_count == 4

    @pytest.mark.asyncio
    async def test_stop_already_idle_instance(self, lifecycle_service, mock_repo, mock_registry):
        """Test stopping an instance that is already idle.

        Verifies:
        - stopped_ids is empty
        - skipped_ids contains the instance
        - cancel_by_instance NOT called
        - update_status NOT called
        """
        instance_id = "idle-instance"
        mock_repo.get.return_value = self._make_instance(instance_id, status="idle")

        result = await lifecycle_service.stop_instance_cascade(instance_id)

        assert result["stopped_ids"] == []
        assert result["skipped_ids"] == [instance_id]
        mock_registry.cancel_by_instance.assert_not_called()
        mock_repo.update_status.assert_not_called()

    @pytest.mark.asyncio
    async def test_stop_mixed_status_children(self, lifecycle_service, mock_repo, mock_registry):
        """Test stopping a parent when children have mixed status.

        Scenario:
        - parent: running
        - child1: running
        - child2: idle

        Verifies:
        - parent and child1 are stopped
        - child2 is skipped
        """
        parent_id = "parent"
        child1_id = "child1-running"
        child2_id = "child2-idle"

        def get_side_effect(instance_id):
            if instance_id == parent_id:
                return self._make_instance(parent_id, status="running", children=[child1_id, child2_id])
            elif instance_id == child1_id:
                return self._make_instance(child1_id, status="running")
            elif instance_id == child2_id:
                return self._make_instance(child2_id, status="idle")
            return None

        mock_repo.get.side_effect = get_side_effect

        result = await lifecycle_service.stop_instance_cascade(parent_id)

        assert set(result["stopped_ids"]) == {parent_id, child1_id}
        assert result["skipped_ids"] == [child2_id]
        # Only 2 instances should have update_status called
        assert mock_repo.update_status.call_count == 2

    @pytest.mark.asyncio
    async def test_stop_nonexistent_instance(self, lifecycle_service, mock_repo, mock_registry):
        """Test stopping a non-existent instance.

        Verifies:
        - stopped_ids is empty
        - skipped_ids is empty
        - No crashes or errors
        - No interactions with registry or repo update
        """
        instance_id = "nonexistent-instance"
        mock_repo.get.return_value = None

        result = await lifecycle_service.stop_instance_cascade(instance_id)

        assert result["stopped_ids"] == []
        assert result["skipped_ids"] == []
        mock_registry.cancel_by_instance.assert_not_called()
        mock_repo.update_status.assert_not_called()

    @pytest.mark.asyncio
    async def test_stop_child_becomes_idle_during_cascade(self, lifecycle_service, mock_repo, mock_registry):
        """Test that an already-idle child is skipped during cascade.

        This tests the case where a child exists but is already idle when
        the cascade reaches it.
        """
        parent_id = "parent"
        child_id = "child"

        def get_side_effect(instance_id):
            if instance_id == parent_id:
                return self._make_instance(parent_id, status="running", children=[child_id])
            elif instance_id == child_id:
                return self._make_instance(child_id, status="idle")
            return None

        mock_repo.get.side_effect = get_side_effect

        result = await lifecycle_service.stop_instance_cascade(parent_id)

        assert result["stopped_ids"] == [parent_id]
        assert result["skipped_ids"] == [child_id]
        # Only parent should have status updated
        assert mock_repo.update_status.call_count == 1
        mock_repo.update_status.assert_called_with(parent_id, "idle")

    @pytest.mark.asyncio
    async def test_stop_child_with_grandchildren_mixed_status(self, lifecycle_service, mock_repo, mock_registry):
        """Test cascade stop with nested children having mixed status.

        Hierarchy:
            parent (running)
            └── child (running)
                └── grandchild (idle)

        Verifies:
        - parent and child are stopped
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
                return self._make_instance(grandchild_id, status="idle")
            return None

        mock_repo.get.side_effect = get_side_effect

        result = await lifecycle_service.stop_instance_cascade(parent_id)

        assert set(result["stopped_ids"]) == {parent_id, child_id}
        assert result["skipped_ids"] == [grandchild_id]
        # Parent and child should have status updated (not grandchild)
        assert mock_repo.update_status.call_count == 2
