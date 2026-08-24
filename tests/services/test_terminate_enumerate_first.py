"""P1 (phase1-plan.md T3) — terminate enumerate-first restructure tests.

Critical correctness invariants pinned here:

* **B4 one-level-down fix**: a TERMINATED child with a LIVE grandchild
  must NOT short-circuit the cascade. The grandchild is terminated;
  the child's ``status`` and ``terminal_reason`` are untouched.

* **Canonical-terminal_reason invariant**: a COMPLETED child must NOT
  be re-terminated (the OLD code's re-entrancy guard checked only
  TERMINATED, so a COMPLETED child got re-stamped with
  ``terminal_reason="aborted"`` — a direct violation).

* **Terminal-skip rule (NORMATIVE)**: classification gates ACTING on a
  node, NEVER traversal. Each entry in the snapshot is independent —
  terminal entries are skipped as nodes while their descendants are
  visited normally.

These tests use mocked manager surfaces (no real DB) to focus on the
enumerate-first control flow.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from daemon.services.instance_lifecycle import InstanceLifecycleService


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _make_manager(
    meta_for: dict[str, Any] | None = None,
) -> MagicMock:
    """Build a mock manager with the minimum surface ``terminate_instance``
    needs to drive the enumerate-first cascade.

    `meta_for[id] -> MagicMock` is the per-node meta the repo's
    ``get(id)`` will return. ``get_cascade_tree_ids`` is wired to return
    the full snapshot the test wants the cascade to enumerate.
    """
    manager = MagicMock()
    manager._instance_repository = MagicMock()
    manager._graph_tasks = {}
    manager._request_registry = MagicMock()
    manager._live_hub = MagicMock()
    manager._live_hub.cleanup_instance = AsyncMock()
    manager._live_hub.stream_status_change = AsyncMock()
    manager._live_hub.stream_message = AsyncMock()
    manager._watcher_repo = MagicMock()
    manager._watcher_repo.remove_all_watches_for_instance = MagicMock(return_value=0)
    manager._mcp_service = None
    manager.instances = {}
    manager._queue_repository = MagicMock()
    manager._queue_repository.delete_by_instance = MagicMock(return_value=0)
    manager._job_queue_mgmt_service = MagicMock()
    manager._job_queue_mgmt_service._dispatch_bus = MagicMock()
    manager._job_queue_mgmt_service._dispatch_bus.notify_all = MagicMock()
    manager.engine = MagicMock()
    manager.write_guard = MagicMock()
    manager._todo_manager = MagicMock()
    manager._todo_manager.clear = MagicMock()
    manager._gii_throttle = {}
    manager._loop_breaker_state = {}
    manager._events_service = None

    if meta_for:
        manager._instance_repository.get.side_effect = lambda iid: meta_for.get(iid)

    return manager


def _make_lifecycle_service(manager: MagicMock) -> InstanceLifecycleService:
    """Build a lifecycle service with all required attributes wired
    to mocks so ``terminate_instance`` can run end-to-end.
    """
    svc = InstanceLifecycleService.__new__(InstanceLifecycleService)
    svc._manager = manager
    svc._cancellation_service = MagicMock()
    svc._events_service = None
    svc._job_queue_service = None
    return svc


def _make_instance(instance_id: str, status: str, terminal_reason: str | None = None):
    """Build a minimal mock Instance row with the given status."""
    meta = MagicMock()
    meta.instance_id = instance_id
    meta.status = status
    meta.terminal_reason = terminal_reason
    meta.agent_id = "test-agent"
    meta.parent_id = None
    return meta


# ─────────────────────────────────────────────────────────────────────────────
# Test 1 — TERMINAL_STATUSES widening (P1 T3 acceptance, part 1)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_terminate_terminal_self_does_not_return_early(
    caplog: pytest.LogCaptureFixture,
):
    """🔴 REGRESSION GUARD (plan T3, B4 one-level-down):

    The OLD re-entrancy guard at ``:1362-1370`` returned ``True``
    immediately when ``status == TERMINATED`` — BEFORE enumerating
    descendants. The result: a TERMINATED child with live grandchildren
    left the grandchildren orphaned (B4 one level down).

    The NEW guard widens to ``status in TERMINAL_STATUSES`` and does NOT
    return early; the snapshot iteration still visits the live
    grandchild. This test pins the new shape: a TERMINATED root's
    cascade still recurses into its descendants.
    """
    caplog.set_level("INFO")

    manager = _make_manager()
    # Pre-mark root as TERMINATED with a non-default reason to ensure
    # the cascade does NOT re-stamp it.
    manager._instance_repository.get.side_effect = lambda iid: {
        "root-terminated": _make_instance(
            "root-terminated", "terminated", terminal_reason="user_deleted"
        ),
        "live-grandchild": _make_instance(
            "live-grandchild", "running"
        ),
    }[iid]
    # Snapshot includes both: root (terminal) + live grandchild.
    manager._instance_repository.get_cascade_tree_ids = MagicMock(
        return_value=["root-terminated", "live-grandchild"]
    )

    svc = _make_lifecycle_service(manager)
    svc._manager = manager

    # The recursive call inside the iteration is also routed through the
    # same `terminate_instance` — record which IDs were recursed into.
    recursed: list[str] = []
    real = svc.terminate_instance

    async def spying_terminate(iid, terminal_reason="aborted"):
        recursed.append(iid)
        return True

    svc.terminate_instance = spying_terminate  # type: ignore[method-assign]

    await real("root-terminated")

    # The live grandchild MUST have been recursed into (the B4 one-level-
    # down fix). The terminal root itself is processed inline (no
    # recursion), but its descendants are visited.
    assert "live-grandchild" in recursed, (
        f"live grandchild must be recursed into even when the root is "
        f"already TERMINATED (B4 one-level-down fix); got recursed={recursed}"
    )
    # Root itself is NOT recursed into (handled inline by step 2).
    assert "root-terminated" not in recursed, (
        f"root is handled inline; should NOT appear in recursion list; "
        f"got recursed={recursed}"
    )

    # The "already terminated" log line is emitted at the per-node guard
    # (widened). This is the new shape — the log is a per-node decision,
    # not a fast-path-return signal.
    already_logged = any(
        "already terminated" in r.message and "root-terminated"[:8] in r.message
        for r in caplog.records
    )
    assert already_logged, "expected 'already terminated' info log"


# ─────────────────────────────────────────────────────────────────────────────
# Test 2 — COMPLETED child not re-stamped (canonical-terminal_reason)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_completed_child_is_not_re_terminated(
    caplog: pytest.LogCaptureFixture,
):
    """🔴 REGRESSION GUARD (plan T3, canonical-terminal_reason):

    The OLD guard checked only ``status == TERMINATED``. A COMPLETED
    child (e.g., finished its work) would get re-terminated and have
    its canonical ``terminal_reason`` overwritten with ``"aborted"`` —
    a direct violation of the canonical-terminal_reason hard
    constraint.

    The NEW guard widens to ``status in TERMINAL_STATUSES`` so
    COMPLETED children skip without re-stamping.
    """
    caplog.set_level("INFO")

    manager = _make_manager()
    # Root (live) → child (COMPLETED, with a canonical reason) →
    # grandchild (live).
    manager._instance_repository.get.side_effect = lambda iid: {
        "root-live": _make_instance("root-live", "running"),
        "child-completed": _make_instance(
            "child-completed",
            "completed",
            terminal_reason="work_finished_successfully",
        ),
        "grandchild-live": _make_instance("grandchild-live", "running"),
    }[iid]
    manager._instance_repository.get_cascade_tree_ids = MagicMock(
        return_value=["root-live", "child-completed", "grandchild-live"]
    )

    svc = _make_lifecycle_service(manager)
    svc._manager = manager

    recursed: list[str] = []
    real = svc.terminate_instance

    async def spying_terminate(iid, terminal_reason="aborted"):
        recursed.append(iid)
        return True

    svc.terminate_instance = spying_terminate  # type: ignore[method-assign]

    await real("root-live")

    # The COMPLETED child must NOT be recursed into (no re-stamp).
    assert "child-completed" not in recursed, (
        f"COMPLETED child must skip without re-stamping "
        f"(canonical-terminal_reason invariant); got recursed={recursed}"
    )
    # The live grandchild MUST be recursed into — visiting it is the
    # whole point of the snapshot iteration; the COMPLETED child
    # doesn't gate the grandchild's visit (terminal-skip rule).
    assert "grandchild-live" in recursed, (
        f"live grandchild must be visited even when its parent is "
        f"COMPLETED (terminal-skip rule); got recursed={recursed}"
    )

    # The terminal-skip log line for the COMPLETED child.
    skip_logged = any(
        "skipping as node" in r.message and "child-completed"[:8] in r.message
        for r in caplog.records
    )
    assert skip_logged, (
        "expected terminal-skip log line for the COMPLETED child "
        "(skipping as node, descendants still visited)"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Test 3 — Mandatory case (plan §Test Strategy T3):
#         TERMINATED child with LIVE grandchild → grandchild terminated,
#         terminal child's status AND terminal_reason UNTOUCHED.
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_terminal_child_with_live_grandchild_grandchild_terminated_child_untouched(
    caplog: pytest.LogCaptureFixture,
):
    """🔴 MANDATORY CASE (plan Test Strategy T3, missing in Rev 1):

    Setup: root (live) → child (TERMINATED with canonical reason) →
    grandchild (live).

    Expected post-terminate(root):
    * grandchild is RECURSED INTO (terminal-skip rule: classification
      gates ACTING, never traversal).
    * child is NOT recursed into (re-entrancy guard widened to
      TERMINAL_STATUSES — would otherwise re-stamp its reason).

    This pins the load-bearing invariant the new shape fixes: the old
    enumerate-by-recursion design would return True from the
    TERMINATED child without enumerating its descendants, leaving the
    live grandchild orphaned.
    """
    caplog.set_level("INFO")

    manager = _make_manager()
    manager._instance_repository.get.side_effect = lambda iid: {
        "root-live": _make_instance("root-live", "running"),
        "child-terminated": _make_instance(
            "child-terminated",
            "terminated",
            terminal_reason="watchover_terminated",  # canonical, distinctive
        ),
        "grandchild-live": _make_instance("grandchild-live", "running"),
    }[iid]
    manager._instance_repository.get_cascade_tree_ids = MagicMock(
        return_value=["root-live", "child-terminated", "grandchild-live"]
    )

    svc = _make_lifecycle_service(manager)
    svc._manager = manager

    recursed: list[str] = []
    real = svc.terminate_instance

    async def spying_terminate(iid, terminal_reason="aborted"):
        recursed.append(iid)
        return True

    svc.terminate_instance = spying_terminate  # type: ignore[method-assign]

    await real("root-live")

    # The TERMINATED child must NOT be recursed into — its status and
    # terminal_reason are NOT touched. (Re-stamping would clobber
    # "watchover_terminated" with "aborted".)
    assert "child-terminated" not in recursed, (
        f"TERMINATED child must skip without re-stamp (canonical "
        f"terminal_reason invariant); got recursed={recursed}"
    )

    # The LIVE grandchild MUST be recursed into. This is the B4
    # one-level-down fix: the OLD shape returned True at the child
    # and never reached the grandchild.
    assert "grandchild-live" in recursed, (
        f"LIVE grandchild must be recursed into even though its parent "
        f"is TERMINATED (B4 one-level-down fix); got recursed={recursed}"
    )

    # The terminal-skip log line for the TERMINATED child.
    skip_logged = any(
        "skipping as node" in r.message and "child-terminated"[:8] in r.message
        for r in caplog.records
    )
    assert skip_logged, (
        "expected terminal-skip log line for the TERMINATED child"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Test 4 — FAILED / ERROR children also skip without re-stamp
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_failed_and_error_children_also_skip_without_re_stamp(
    caplog: pytest.LogCaptureFixture,
):
    """Widened guard covers ALL ``TERMINAL_STATUSES`` (not just
    ``TERMINATED``). FAILED and ERROR children must also skip without
    re-stamping their ``terminal_reason``.
    """
    caplog.set_level("INFO")

    manager = _make_manager()
    manager._instance_repository.get.side_effect = lambda iid: {
        "root": _make_instance("root", "running"),
        "child-failed": _make_instance(
            "child-failed", "failed", terminal_reason="exception_raised"
        ),
        "child-error": _make_instance(
            "child-error", "error", terminal_reason="agent_error"
        ),
        "grandchild-live": _make_instance("grandchild-live", "running"),
    }[iid]
    manager._instance_repository.get_cascade_tree_ids = MagicMock(
        return_value=["root", "child-failed", "child-error", "grandchild-live"]
    )

    svc = _make_lifecycle_service(manager)
    svc._manager = manager

    recursed: list[str] = []
    real = svc.terminate_instance

    async def spying_terminate(iid, terminal_reason="aborted"):
        recursed.append(iid)
        return True

    svc.terminate_instance = spying_terminate  # type: ignore[method-assign]

    await real("root")

    assert "child-failed" not in recursed
    assert "child-error" not in recursed
    assert "grandchild-live" in recursed, (
        "live grandchild must be recursed even though ancestors are "
        "FAILED / ERROR (terminal-skip rule)"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Test 5 — Snapshot from get_cascade_tree_ids (not inline hierarchy query)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_snapshot_is_obtained_via_get_cascade_tree_ids_not_legacy():
    """P1 (T3): the snapshot is taken from ``get_cascade_tree_ids``
    (the kill-switch wrapper), not from the OLD inline hierarchy query.

    Verifies that the new enumerate-first path actually invokes the
    new wrapper. The OLD path queried ``instance_hierarchy`` directly
    via ``Session(manager.engine)``.
    """
    manager = _make_manager()
    manager._instance_repository.get.side_effect = lambda iid: _make_instance(
        iid, "running"
    )
    manager._instance_repository.get_cascade_tree_ids = MagicMock(
        return_value=["root"]
    )

    svc = _make_lifecycle_service(manager)

    await svc.terminate_instance("root")

    manager._instance_repository.get_cascade_tree_ids.assert_called_with("root")


# ─────────────────────────────────────────────────────────────────────────────
# Test 6 — All-children-terminal short-circuit (regression guard)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_all_terminal_snapshot_short_circuits_with_no_recursion():
    """If the snapshot has only terminal descendants, no recursion
    happens — the iteration sees every node is skipped and returns
    True at the end without any recursive calls.
    """
    manager = _make_manager()
    manager._instance_repository.get.side_effect = lambda iid: {
        "root-live": _make_instance("root-live", "running"),
        "child-done": _make_instance("child-done", "completed", terminal_reason="ok"),
    }[iid]
    manager._instance_repository.get_cascade_tree_ids = MagicMock(
        return_value=["root-live", "child-done"]
    )

    svc = _make_lifecycle_service(manager)
    svc._manager = manager

    recursed: list[str] = []
    real = svc.terminate_instance

    async def spying_terminate(iid, terminal_reason="aborted"):
        recursed.append(iid)
        return True

    svc.terminate_instance = spying_terminate  # type: ignore[method-assign]

    await real("root-live")

    # The child is COMPLETED → skipped (no recursion).
    assert "child-done" not in recursed
    assert recursed == [], (
        f"no recursion expected when all descendants are terminal; "
        f"got recursed={recursed}"
    )
