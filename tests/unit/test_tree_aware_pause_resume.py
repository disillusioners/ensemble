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
from daemon.services.instance_lifecycle import _CascadeUpdateResult


# ─────────────────────────────────────────────────────────────────────────────
# L14 batching: test-helpers
#
# The L14 fix replaced per-node ``repo.update(...)`` calls inside
# ``pause_instance_cascade`` / ``resume_instance_cascade`` with a SINGLE
# batched SQL ``UPDATE ... WHERE instance_id IN (...)`` issued via the
# ``_pause_cascade_db_sync`` / ``_resume_cascade_db_sync`` helpers. The old
# tests asserted on ``mock_repo.update.call_count`` and per-call kwargs,
# which are now implementation details of the batched SQL path — not
# observable behavior.
#
# The public *behavior* these tests actually want to verify is:
#   1. The right set of instance IDs ends up in ``paused_ids`` /
#      ``resumed_ids`` / ``skipped_ids`` / ``target_id``.
#   2. The per-instance ``waiting_for`` values are computed correctly
#      (reset to 0 on pause; 1 for ancestors on non-root resume).
#   3. Already-paused / already-running nodes are skipped, not re-written.
#   4. Per-node exceptions don't block siblings.
#
# To verify (2) without coupling to the SQL layer, the fixtures below
# monkey-patch ``_pause_cascade_db_sync`` and ``_resume_cascade_db_sync``
# on the service instance. The mocks capture the helper's arguments
# (which carry the per-instance ``waiting_for`` decisions made by the
# cascade loop) and return a ``_CascadeUpdateResult`` that mirrors what
# the real helper would return. Tests then assert on the captured data.
# ─────────────────────────────────────────────────────────────────────────────


def _build_pause_db_sync_mock(captured: dict) -> MagicMock:
    """Build a mock ``_pause_cascade_db_sync`` that captures batch args.

    The real helper takes ``(engine, write_guard, *, tree_ids,
    paused_at_iso, paused_instances_data, use_legacy_cascade)`` and runs
    a batched SQL UPDATE. This mock captures the args and synthesizes
    the result the real helper would return (updated_ids from
    paused_instances_data, skipped_ids = tree_ids − updated_ids, plus
    per-node agent_id / waiting_for maps).

    A6: ``use_legacy_cascade`` is captured but does not change the
    synthesized ``waiting_for`` map — the test asserts on the
    ``paused_instances_data`` tuple the cascade loop produced, which
    is the same data the real helper sees.
    """

    def _mock(
        engine,
        write_guard,
        *,
        tree_ids,
        paused_at_iso,
        paused_instances_data,
        use_legacy_cascade: bool = False,
    ):
        updated_ids = [iid for iid, _agent, _wf in paused_instances_data]
        updated_set = set(updated_ids)
        result = _CascadeUpdateResult(
            updated_ids=updated_ids,
            skipped_ids=[iid for iid in tree_ids if iid not in updated_set],
            agent_ids_by_instance={
                iid: agent for iid, agent, _wf in paused_instances_data
            },
            waiting_for_by_instance={
                iid: wf for iid, _agent, wf in paused_instances_data
            },
        )
        captured["pause_calls"].append(
            {"tree_ids": list(tree_ids), "paused_at_iso": paused_at_iso,
             "paused_instances_data": list(paused_instances_data),
             "use_legacy_cascade": use_legacy_cascade, "result": result}
        )
        return result

    return MagicMock(side_effect=_mock)


def _build_resume_db_sync_mock(captured: dict) -> MagicMock:
    """Build a mock ``_resume_cascade_db_sync`` that captures batch args.

    The real helper takes ``(engine, write_guard, *, tree_ids,
    ancestor_ids, is_root_resume, use_legacy_cascade)`` and runs a
    batched SQL UPDATE (status=paused→running, waiting_for=0 for
    everyone when the A6 kill switch is ON, then a follow-up UPDATE
    bumping ``waiting_for=1`` for ancestors on non-root resume).
    This mock captures the args and synthesizes the result with the
    correct ``waiting_for_by_instance`` map so SSE / logger side
    effects receive the right values.

    A6: when ``use_legacy_cascade`` is False (default, CM-authoritative
    path), the synthesized ``waiting_for_by_instance`` map is all
    zeros — the real helper would not have touched ``waiting_for`` in
    the SQL, so the log line gets a neutral placeholder. When the
    kill switch is ON, the synthesized map matches the legacy
    behavior (1 for ancestors on non-root resume, 0 otherwise).
    """

    def _mock(
        engine,
        write_guard,
        *,
        tree_ids,
        ancestor_ids,
        is_root_resume,
        use_legacy_cascade: bool = False,
    ):
        waiting_for_by_instance: dict[str, int] = {}
        for iid in tree_ids:
            if use_legacy_cascade and not is_root_resume and iid in ancestor_ids:
                waiting_for_by_instance[iid] = 1
            else:
                waiting_for_by_instance[iid] = 0
        result = _CascadeUpdateResult(
            updated_ids=list(tree_ids),
            skipped_ids=[],
            agent_ids_by_instance={},
            waiting_for_by_instance=waiting_for_by_instance,
        )
        captured["resume_calls"].append(
            {"tree_ids": list(tree_ids), "ancestor_ids": set(ancestor_ids),
             "is_root_resume": is_root_resume,
             "use_legacy_cascade": use_legacy_cascade, "result": result}
        )
        return result

    return MagicMock(side_effect=_mock)


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
        """Create an InstanceLifecycleService with mocked manager.

        L14: ``_pause_cascade_db_sync`` is monkey-patched with a mock
        that captures ``paused_instances_data`` (which carries the
        per-node ``waiting_for`` decisions) and synthesizes the result.
        """
        from daemon.services.instance_lifecycle import InstanceLifecycleService

        service = InstanceLifecycleService.__new__(InstanceLifecycleService)
        service._manager = mock_manager
        captured: dict = {"pause_calls": [], "resume_calls": []}
        service._pause_cascade_db_sync = _build_pause_db_sync_mock(captured)
        service._resume_cascade_db_sync = _build_resume_db_sync_mock(captured)
        service._captured_db_sync = captured
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

        # L14: verify the batched db_sync helper was called once with
        # all 4 nodes in ``paused_instances_data`` (no skipped IDs since
        # every node was running).
        captured = lifecycle_service._captured_db_sync
        assert len(captured["pause_calls"]) == 1
        pause_call = captured["pause_calls"][0]
        data_ids = {iid for iid, _agent, _wf in pause_call["paused_instances_data"]}
        assert data_ids == {root_id, child1_id, child2_id, grandchild_id}

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

        # L14: single batched UPDATE carries all 4 nodes
        captured = lifecycle_service._captured_db_sync
        assert len(captured["pause_calls"]) == 1
        pause_call = captured["pause_calls"][0]
        data_ids = {iid for iid, _agent, _wf in pause_call["paused_instances_data"]}
        assert data_ids == {root_id, child1_id, child2_id, grandchild_id}

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

        # L14: single batched UPDATE carries all 6 nodes
        captured = lifecycle_service._captured_db_sync
        assert len(captured["pause_calls"]) == 1
        pause_call = captured["pause_calls"][0]
        data_ids = {iid for iid, _agent, _wf in pause_call["paused_instances_data"]}
        assert data_ids == set(all_ids)

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

        # L14: batched UPDATE only carries the 2 nodes we actually want
        # to pause — child2 (already paused) is filtered out by the
        # cascade loop before the helper runs and reported in skipped_ids.
        captured = lifecycle_service._captured_db_sync
        assert len(captured["pause_calls"]) == 1
        pause_call = captured["pause_calls"][0]
        data_ids = {iid for iid, _agent, _wf in pause_call["paused_instances_data"]}
        assert data_ids == {root_id, child1_id}

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

        # L14: verify the batched db_sync was called with all 4 nodes,
        # and that each node carries the correct per-instance waiting_for.
        captured = lifecycle_service._captured_db_sync
        assert len(captured["pause_calls"]) == 1
        pause_call = captured["pause_calls"][0]
        assert len(pause_call["paused_instances_data"]) == 4
        # The cascade loop encodes per-node waiting_for in the third
        # tuple element. child2 had waiting_for=0 already, so the loop
        # still includes it (it gets reset to 0 on pause, which is a
        # no-op write but keeps the SQL single-shot).
        waiting_for_by_id = {
            iid: wf for iid, _agent, wf in pause_call["paused_instances_data"]
        }
        assert waiting_for_by_id[root_id] == 0
        assert waiting_for_by_id[child1_id] == 0
        assert waiting_for_by_id[child2_id] == 0
        assert waiting_for_by_id[child3_id] == 0

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

        # L14: batched UPDATE was called exactly once with the single
        # instance. The helper carries ``paused_instances_data`` with
        # one tuple (instance_id, agent_id, waiting_for=0).
        captured = lifecycle_service._captured_db_sync
        assert len(captured["pause_calls"]) == 1
        pause_call = captured["pause_calls"][0]
        assert len(pause_call["paused_instances_data"]) == 1
        iid, _agent, wf = pause_call["paused_instances_data"][0]
        assert iid == instance_id
        assert wf == 0

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
        """Create an InstanceLifecycleService with mocked manager.

        L14: ``_resume_cascade_db_sync`` is monkey-patched with a mock
        that captures ``tree_ids`` / ``ancestor_ids`` / ``is_root_resume``
        (which carry the per-node ``waiting_for`` decisions made by the
        cascade logic) and synthesizes the result.
        """
        from daemon.services.instance_lifecycle import InstanceLifecycleService

        service = InstanceLifecycleService.__new__(InstanceLifecycleService)
        service._manager = mock_manager
        captured: dict = {"pause_calls": [], "resume_calls": []}
        service._pause_cascade_db_sync = _build_pause_db_sync_mock(captured)
        service._resume_cascade_db_sync = _build_resume_db_sync_mock(captured)
        service._captured_db_sync = captured
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

        # L14: verify the batched resume helper was called with all 3
        # nodes and ``is_root_resume=True``. The helper's contract is
        # ``waiting_for=0`` for everyone when resuming from root (no
        # ancestors exist).
        captured = lifecycle_service._captured_db_sync
        assert len(captured["resume_calls"]) == 1
        resume_call = captured["resume_calls"][0]
        assert set(resume_call["tree_ids"]) == {root_id, child1_id, child2_id}
        assert resume_call["is_root_resume"] is True
        assert resume_call["ancestor_ids"] == set()
        # Synthesized waiting_for map mirrors the real helper: all 0
        # when resuming from root.
        wf_map = resume_call["result"].waiting_for_by_instance
        for iid in {root_id, child1_id, child2_id}:
            assert wf_map[iid] == 0

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

        # L14: verify the batched resume helper received the correct
        # ancestor_ids and the synthesized waiting_for map matches the
        # contract: ancestors=1, siblings/resumed_node=0.
        captured = lifecycle_service._captured_db_sync
        assert len(captured["resume_calls"]) == 1
        resume_call = captured["resume_calls"][0]
        assert resume_call["ancestor_ids"] == {root_id}
        assert resume_call["is_root_resume"] is False
        wf_map = resume_call["result"].waiting_for_by_instance
        assert wf_map[root_id] == 1   # Ancestor
        assert wf_map[child1_id] == 0  # Sibling
        assert wf_map[child2_id] == 0  # Resumed node

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

        # L14: verify the full ancestor chain was passed and the waiting_for
        # map correctly bumps every ancestor to 1.
        captured = lifecycle_service._captured_db_sync
        assert len(captured["resume_calls"]) == 1
        resume_call = captured["resume_calls"][0]
        assert resume_call["ancestor_ids"] == {level3_id, level2_id, level1_id, root_id}
        assert resume_call["is_root_resume"] is False
        wf_map = resume_call["result"].waiting_for_by_instance
        assert wf_map[root_id] == 1    # Ancestor
        assert wf_map[level1_id] == 1  # Ancestor
        assert wf_map[level2_id] == 1  # Ancestor
        assert wf_map[level3_id] == 1  # Ancestor
        assert wf_map[leaf_id] == 0    # Resumed node

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

        # L14: all ancestors get waiting_for=1 in the synthesized map.
        captured = lifecycle_service._captured_db_sync
        assert len(captured["resume_calls"]) == 1
        resume_call = captured["resume_calls"][0]
        wf_map = resume_call["result"].waiting_for_by_instance
        for ancestor in ids[:-1]:
            assert wf_map[ancestor] == 1, f"{ancestor} should have waiting_for=1"
        # Resumed node gets waiting_for=0
        assert wf_map["l5"] == 0

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

        # L14: only root is an ancestor; siblings and resumed node get
        # waiting_for=0.
        captured = lifecycle_service._captured_db_sync
        assert len(captured["resume_calls"]) == 1
        resume_call = captured["resume_calls"][0]
        assert resume_call["ancestor_ids"] == {root_id}
        wf_map = resume_call["result"].waiting_for_by_instance
        assert wf_map[root_id] == 1  # Only ancestor
        for sibling in ["c1", "c3", "c4"]:
            assert wf_map[sibling] == 0  # Siblings
        assert wf_map["c2"] == 0  # Resumed node

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
        """Create an InstanceLifecycleService with mocked manager.

        L14: ``_pause_cascade_db_sync`` and ``_resume_cascade_db_sync``
        are monkey-patched with mocks that capture the per-instance
        waiting_for decisions made by the cascade logic.
        """
        from daemon.services.instance_lifecycle import InstanceLifecycleService

        service = InstanceLifecycleService.__new__(InstanceLifecycleService)
        service._manager = mock_manager
        captured: dict = {"pause_calls": [], "resume_calls": []}
        service._pause_cascade_db_sync = _build_pause_db_sync_mock(captured)
        service._resume_cascade_db_sync = _build_resume_db_sync_mock(captured)
        service._captured_db_sync = captured
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

        # L14: pause ALWAYS resets waiting_for to 0, regardless of the
        # previous value. The cascade loop encodes the post-pause
        # waiting_for in the ``paused_instances_data`` tuple (the third
        # element), so we verify it there.
        captured = lifecycle_service._captured_db_sync
        assert len(captured["pause_calls"]) == 1
        pause_call = captured["pause_calls"][0]
        wf_by_id = {iid: wf for iid, _agent, wf in pause_call["paused_instances_data"]}
        assert wf_by_id[root_id] == 0
        assert wf_by_id[child_id] == 0

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

        # L14: resume from root → no ancestors → all waiting_for=0.
        captured = lifecycle_service._captured_db_sync
        assert len(captured["resume_calls"]) == 1
        resume_call = captured["resume_calls"][0]
        assert resume_call["is_root_resume"] is True
        assert resume_call["ancestor_ids"] == set()
        wf_map = resume_call["result"].waiting_for_by_instance
        assert wf_map[root_id] == 0
        assert wf_map[child_id] == 0

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

        # L14: only root is an ancestor → waiting_for=1; siblings/resumed
        # node get 0.
        captured = lifecycle_service._captured_db_sync
        assert len(captured["resume_calls"]) == 1
        resume_call = captured["resume_calls"][0]
        wf_map = resume_call["result"].waiting_for_by_instance
        assert wf_map[root_id] == 1
        assert wf_map[child1_id] == 0
        assert wf_map[child3_id] == 0
        assert wf_map[child2_id] == 0

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

        # L14: full ancestor chain → waiting_for=1 for each ancestor.
        captured = lifecycle_service._captured_db_sync
        assert len(captured["resume_calls"]) == 1
        resume_call = captured["resume_calls"][0]
        wf_map = resume_call["result"].waiting_for_by_instance
        assert wf_map[root_id] == 1
        assert wf_map[l1_id] == 1
        assert wf_map[l2_id] == 1
        assert wf_map[leaf_id] == 0

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

        # L14: ancestors [l1, root] → waiting_for=1; everyone else → 0.
        captured = lifecycle_service._captured_db_sync
        assert len(captured["resume_calls"]) == 1
        resume_call = captured["resume_calls"][0]
        wf_map = resume_call["result"].waiting_for_by_instance
        assert wf_map[root_id] == 1
        assert wf_map[l1_id] == 1
        assert wf_map[l2_id] == 0
        assert wf_map[l3_id] == 0
        assert wf_map[m1_id] == 0
        assert wf_map[m3_id] == 0
        assert wf_map[m2_id] == 0


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
        """Create an InstanceLifecycleService with mocked manager.

        L14: ``_pause_cascade_db_sync`` and ``_resume_cascade_db_sync``
        are monkey-patched with mocks that capture the batched
        arguments so exception-handling and empty-tree tests can verify
        which nodes survive to the helper.
        """
        from daemon.services.instance_lifecycle import InstanceLifecycleService

        service = InstanceLifecycleService.__new__(InstanceLifecycleService)
        service._manager = mock_manager
        captured: dict = {"pause_calls": [], "resume_calls": []}
        service._pause_cascade_db_sync = _build_pause_db_sync_mock(captured)
        service._resume_cascade_db_sync = _build_resume_db_sync_mock(captured)
        service._captured_db_sync = captured
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

        # L14: batched db_sync was called once with the 2 surviving
        # nodes (root and child2). child1 was filtered out by the
        # cascade loop's try/except before the helper ran.
        captured = lifecycle_service._captured_db_sync
        assert len(captured["pause_calls"]) == 1
        pause_call = captured["pause_calls"][0]
        data_ids = {iid for iid, _agent, _wf in pause_call["paused_instances_data"]}
        assert data_ids == {root_id, child2_id}

    @pytest.mark.asyncio
    async def test_resume_exception_handling_does_not_block_siblings(self, lifecycle_service, mock_repo):
        """Test that exception during resume doesn't block siblings.

        The implementation catches exceptions per-instance, so siblings should still be processed.

        L14 note: the OLD per-node ``repo.update(...)`` failure path no
        longer exists — the L14 batched SQL UPDATE bypasses
        ``repo.update`` entirely. To exercise the per-node exception
        path we now fail ``repo.get(child1_id)`` (which the cascade
        loop's ``try/except`` catches), and verify the batched helper
        still receives the surviving siblings.
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
                # Simulate a failure fetching child1 — the cascade
                # loop's try/except adds child1 to skipped_ids.
                raise RuntimeError("Database error")
            elif instance_id == child2_id:
                return self._make_instance(child2_id, status="paused")
            return None

        mock_repo.get.side_effect = get_side_effect

        result = await lifecycle_service.resume_instance_cascade(root_id)

        # root and child2 should be resumed, child1 skipped due to exception
        assert set(result["resumed_ids"]) == {root_id, child2_id}
        assert result["skipped_ids"] == [child1_id]

        # L14: batched db_sync was called once with the 2 surviving nodes.
        captured = lifecycle_service._captured_db_sync
        assert len(captured["resume_calls"]) == 1
        resume_call = captured["resume_calls"][0]
        assert set(resume_call["tree_ids"]) == {root_id, child2_id}

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

        # L14: batched db_sync was called exactly once with the single
        # instance in ``paused_instances_data``.
        captured = lifecycle_service._captured_db_sync
        assert len(captured["pause_calls"]) == 1
        pause_call = captured["pause_calls"][0]
        assert len(pause_call["paused_instances_data"]) == 1
        assert pause_call["paused_instances_data"][0][0] == instance_id

        # Reset for resume test
        mock_repo.get.return_value = self._make_instance(instance_id, status="paused")
        mock_repo.get_ancestor_ids.return_value = []
        captured["pause_calls"].clear()
        captured["resume_calls"].clear()

        result = await lifecycle_service.resume_instance_cascade(instance_id)

        assert result["resumed_ids"] == [instance_id]
        assert result["skipped_ids"] == []
        assert result["target_id"] == instance_id

        # L14: batched resume db_sync was called exactly once with the
        # single instance; synthesized waiting_for=0 (is_root_resume=True).
        assert len(captured["resume_calls"]) == 1
        resume_call = captured["resume_calls"][0]
        assert resume_call["tree_ids"] == [instance_id]
        assert resume_call["is_root_resume"] is True
        assert resume_call["result"].waiting_for_by_instance[instance_id] == 0
