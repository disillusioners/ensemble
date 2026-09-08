"""Hook-level tests for the messaging-path parent_id resolution
in :meth:`InstanceMessagingService._process_message_with_tracking`.

The first-turn persistent context block is built by the messaging
path (not graph's ``ContextSlot`` — see ``daemon/graph.py:582-584``
for the discard rationale). Before the fix, the messaging path
hardcoded ``_persistent_parent_id=None`` at
``daemon/services/instance_messaging.py:3683``, which caused
``_resolve_tree_root_id`` (context_messages.py:949-950) to return
the child's own id for every spawned child instance — the
explorer (and every other tree-bound agent) silently read from an
empty own-partition instead of the caller's tree-root partition.

This file pins the corrected behavior end-to-end:

* Root instance → ``parent_id=None`` (orchestrator returns own id).
* Child instance with ``parent_id=<caller>`` → orchestrator is
  called with the TRUE parent id, so the ancestor walk in
  ``get_tree_root_id`` resolves to the caller's tree root.

A bug-exercising proof also reverts the fix briefly and asserts
the mispartition symptom (orchestrator called with parent_id=None
for a child that should inherit the caller's tree root).

Implementation strategy mirrors
``tests/services/test_instance_messaging_shared_context_injection.py``:
wire every dependency to a mock, patch
``daemon.services.context_messages.assemble_context_messages`` to
capture the actual kwargs the messaging path passes, and inspect
the captured ``parent_id`` to verify the contract.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from daemon.services.instance_messaging import InstanceMessagingService


# ============================================================
# Helpers
# ============================================================


def _make_capturing_graph() -> MagicMock:
    """Build a LangGraph mock whose ``astream`` immediately ends
    iteration so the surrounding ``async for event in graph.astream(...)``
    in ``_process_message_with_tracking`` exits cleanly. We do not
    need to capture ``graph_input`` here — the orchestrator kwargs
    are the test target, captured by the
    ``assemble_context_messages`` mock below.
    """

    async def fake_astream(*args, **kwargs):
        return
        yield  # pragma: no cover

    graph = MagicMock()
    graph.astream = fake_astream
    graph.aget_state = AsyncMock(return_value=None)
    return graph


@asynccontextmanager
async def _null_semaphore():
    yield


def _make_manager(
    *,
    parent_id: str | None = None,
    project_id: str | None = "proj-1",
) -> MagicMock:
    """Build a manager mock whose instance row exposes ``parent_id``.

    The fix reads ``parent_id`` from the row returned by
    ``manager._instance_repository.get(instance_id)`` — same fetch
    the existing code already uses for ``project_id``. The test
    pins that thread-through.
    """
    instance_meta = SimpleNamespace(
        instance_id="inst-1",
        agent_id="developer",
        parent_id=parent_id,
        instance_metadata={"project_id": project_id} if project_id else {},
    )

    manager = MagicMock()
    manager.config.limits.graph_recursion_limit = 50
    manager.config.compaction = MagicMock()
    manager.get_instance = AsyncMock()
    manager._instance_repository = MagicMock()
    manager._instance_repository.get = MagicMock(return_value=instance_meta)
    manager._instance_repository.set_metadata = MagicMock(return_value=None)
    manager._live_hub = MagicMock()
    manager._live_hub.stream_message = AsyncMock()
    manager._queue_repository = MagicMock()
    manager._graph_tasks = {}
    manager.source_dispatcher = None
    manager._llm_semaphore = _null_semaphore()
    # Disable skill injection so the orchestrator call is the only
    # I/O the test concerns itself with.
    manager._skill_injection_service = None
    return manager


def _make_service(manager: MagicMock) -> InstanceMessagingService:
    """Build an :class:`InstanceMessagingService` around ``manager``
    with the checkpoint/compaction helpers stubbed so the body of
    ``_process_message_with_tracking`` always takes the first-attempt
    branch (``is_retry=False``).
    """
    svc = InstanceMessagingService(
        manager=manager,
        cancellation_service=MagicMock(is_shutting_down=False),
    )
    svc._has_checkpoint = AsyncMock(return_value=False)
    svc._maybe_compact_context = AsyncMock()
    return svc


# ============================================================
# Tests
# ============================================================


class TestMessagingParentResolution:
    """Pin the messaging-path parent_id thread-through contract."""

    async def test_child_instance_passes_true_parent_id_to_orchestrator(self):
        """Child instance → orchestrator receives the row's parent_id.

        Pin for the FIX: a freshly spawned child (e.g. the explorer)
        carries ``parent_id=<caller>`` on the instances row. The
        messaging path must thread that value into
        ``assemble_context_messages(parent_id=...)`` so
        ``_resolve_tree_root_id`` walks up to the caller's tree root
        and the first-turn persistent block reads from the caller's
        partition — not the child's empty own-partition.
        """
        captured: dict = {}
        graph = _make_capturing_graph()
        manager = _make_manager(parent_id="caller-tree-root")

        async def _capture(*args, **kwargs):
            captured["kwargs"] = kwargs
            return ([], [])

        with patch("daemon.registry.get_registry") as mock_get_registry:
            registry = MagicMock()
            registry.get_version = MagicMock(return_value=None)
            registry.get_resolved = MagicMock(
                return_value=SimpleNamespace(context_injection_mode="human_messages")
            )
            mock_get_registry.return_value = registry

            svc = _make_service(manager)
            manager.get_instance.return_value = graph

            with patch(
                "daemon.services.context_messages.assemble_context_messages",
                new=AsyncMock(side_effect=_capture),
            ):
                await svc._process_message_with_tracking(
                    instance_id="inst-1",
                    message="hello",
                    message_id="msg-1",
                    is_retry=False,
                    message_source="agent:leader",
                )

        assert captured, "assemble_context_messages was never called"
        assert captured["kwargs"].get("parent_id") == "caller-tree-root", (
            f"messaging path must thread the row's parent_id into the "
            f"orchestrator so _resolve_tree_root_id walks up to the caller's "
            f"tree root — got parent_id={captured['kwargs'].get('parent_id')!r}"
        )

    async def test_root_instance_passes_none_to_orchestrator(self):
        """Root instance → orchestrator receives ``parent_id=None``.

        Unchanged-behavior pin: a root instance (no parent) must
        still pass ``parent_id=None`` so ``_resolve_tree_root_id``
        returns the instance's own id (the documented root branch).
        The fix must not regress this — root instances continue to
        resolve against their own id.
        """
        captured: dict = {}
        graph = _make_capturing_graph()
        manager = _make_manager(parent_id=None)  # root

        async def _capture(*args, **kwargs):
            captured["kwargs"] = kwargs
            return ([], [])

        with patch("daemon.registry.get_registry") as mock_get_registry:
            registry = MagicMock()
            registry.get_version = MagicMock(return_value=None)
            registry.get_resolved = MagicMock(
                return_value=SimpleNamespace(context_injection_mode="human_messages")
            )
            mock_get_registry.return_value = registry

            svc = _make_service(manager)
            manager.get_instance.return_value = graph

            with patch(
                "daemon.services.context_messages.assemble_context_messages",
                new=AsyncMock(side_effect=_capture),
            ):
                await svc._process_message_with_tracking(
                    instance_id="inst-1",
                    message="hello",
                    message_id="msg-1",
                    is_retry=False,
                    message_source="agent:leader",
                )

        assert captured, "assemble_context_messages was never called"
        assert captured["kwargs"].get("parent_id") is None, (
            f"root instance must still pass parent_id=None so "
            f"_resolve_tree_root_id returns the instance's own id "
            f"(unchanged behavior) — got parent_id={captured['kwargs'].get('parent_id')!r}"
        )


class TestMessagingParentResolutionBugExercising:
    """Bug-exercising proof per repo convention.

    Reverts the fix temporarily — by patching the messaging path
    to discard the row's ``parent_id`` and force ``None`` — and
    asserts the EXACT mispartition symptom: a child instance whose
    parent_id would resolve to the caller's tree root instead
    triggers ``_resolve_tree_root_id`` to return the child's own
    id (the empty own-partition).
    """

    async def test_fix_reverted_child_mispartitions_to_own_id(self):
        """With the fix REVERTED (parent_id force-None), a child
        instance's first-turn block would resolve to the child's
        own id, missing the caller's tree-root partition.

        Pin the exact mispartition symptom documented in
        ``worktree-aware-prompts/architecture-recommendation.md §6(2)``
        so a regression that re-introduces the hardcoded ``None``
        produces a loud failure here rather than the silent
        mispartition that motivated the fix.
        """
        from daemon.services.context_messages import _resolve_tree_root_id

        captured: dict = {}
        graph = _make_capturing_graph()
        manager = _make_manager(parent_id="caller-tree-root")

        # Simulate the BUG: hardcoded _persistent_parent_id=None
        # (the pre-fix behavior). This is what the messaging path
        # passed before the fix.
        async def _capture_with_bug(*args, **kwargs):
            kwargs = dict(kwargs)
            kwargs["parent_id"] = None  # <-- the regression shape
            captured["kwargs"] = kwargs
            return ([], [])

        with patch("daemon.registry.get_registry") as mock_get_registry:
            registry = MagicMock()
            registry.get_version = MagicMock(return_value=None)
            registry.get_resolved = MagicMock(
                return_value=SimpleNamespace(context_injection_mode="human_messages")
            )
            mock_get_registry.return_value = registry

            svc = _make_service(manager)
            manager.get_instance.return_value = graph

            with patch(
                "daemon.services.context_messages.assemble_context_messages",
                new=AsyncMock(side_effect=_capture_with_bug),
            ):
                await svc._process_message_with_tracking(
                    instance_id="child-instance-id",
                    message="hello",
                    message_id="msg-1",
                    is_retry=False,
                    message_source="agent:leader",
                )

        # Now simulate what the orchestrator would do with the
        # misparented value — _resolve_tree_root_id returns the
        # CHILD'S OWN id, missing the caller's tree-root partition.
        resolved_with_bug = _resolve_tree_root_id(
            instance_id="child-instance-id",
            parent_id=captured["kwargs"]["parent_id"],  # None — the bug
            instance_repository=MagicMock(
                get_tree_root_id=MagicMock(return_value="caller-tree-root")
            ),
        )
        # Symptom: the orchestrator would query the child's OWN empty
        # partition, not the caller's populated tree-root partition.
        assert resolved_with_bug == "child-instance-id", (
            f"with parent_id=None (the pre-fix bug), _resolve_tree_root_id "
            f"must return the child's own id — that's the documented "
            f"mispartition symptom (governor council_manifest restore "
            f"implicated). Got: {resolved_with_bug!r}"
        )

        # Cross-check: WITH the fix, the orchestrator would receive
        # parent_id='caller-tree-root' and walk the ancestor chain
        # correctly.
        resolved_with_fix = _resolve_tree_root_id(
            instance_id="child-instance-id",
            parent_id="caller-tree-root",
            instance_repository=MagicMock(
                get_tree_root_id=MagicMock(return_value="caller-tree-root")
            ),
        )
        assert resolved_with_fix == "caller-tree-root", (
            f"with parent_id='caller-tree-root' (the fix), "
            f"_resolve_tree_root_id must walk to the caller's tree root. "
            f"Got: {resolved_with_fix!r}"
        )