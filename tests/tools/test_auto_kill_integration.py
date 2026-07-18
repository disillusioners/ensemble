"""End-to-end integration tests for auto-kill background processes.

This is Phase 3 of the "auto-kill background processes on root instance
completion" plan. Phase 1 (``d4874719``) added the two-tier proc
cleanup + ``cleanup_all()`` on shutdown. Phase 2 (``9cb29096``) added the
matching bash tier (``BashProcessRegistry``), the ``_make_instance_id_aware``
wrapper, the eager PGID capture, and the CancelledError leak fix for both
await points. Phase 3 wires the two registries together in realistic
lifecycle scenarios.

What this pack covers
---------------------

Scenarios from ``phase3-plan.md``:

* **A — Tier 1 self-cleanup on child completion.** Child instance has
  its own background processes; on terminal it cleans them up. Tier 2
  is skipped because ``parent_id != None``. Sibling procs untouched.
* **B — Tier 2 root sweep kills descendants.** Root instance has its
  own procs; on terminal Tier 1 cleans root, Tier 2 walks the
  ``get_tree_ids`` subtree and cleans each descendant (skipping the
  root, which Tier 1 already handled).
* **C — Child-then-root ordering.** Child cleanup is immediate (no
  leak between child completion and root finalization). Root cleanup
  then sweeps the rest. Child 1's cleanup is idempotent on the second
  pass.
* **F — Nohup grandchild killed via process group.** ``nohup sleep &
  sleep 0.1`` produces a backgrounded grandchild that survives the
  foreground exit; the registry still has the entry; Tier 1 bash
  cleanup_instance calls ``os.killpg`` and reaps it.
* **G — Double-fire idempotency.** Running ``cleanup_instance``
  directly on root + children, then calling the dispatcher with
  ``terminal_status=TERMINATED``, must not raise — both Tier 1 and
  Tier 2 see empty buckets.
* **H — Best-effort failure isolation (3 sub-cases).**
    - **H1:** ``get_tree_ids`` raises → WARNING logged, Tier 2
      no-op, Tier 1 still ran, other side-effects still fire.
    - **H2:** ``cleanup_instance(child)`` raises in Tier 2 → root +
      other children still cleaned, child failure logged WARNING,
      no propagation.
    - **H3:** ``_instance_repository`` missing on manager → WARNING
      logged, Tier 2 skipped, Tier 1 still ran.

Tasks:

* **Task 4 — Daemon shutdown kills everything.** Both
  ``cleanup_all()`` invoked; both registries empty; all process groups
  dead.
* **Task 8 — No-op when no processes exist.** Instance with empty
  registries completes cleanly.
* **Task 11 — ERROR/FAILED paths.** Root reaches terminal via the
  error and failed paths — both converge on
  ``_dispatch_instance_post_commit_side_effects``. Tier 1 + Tier 2
  fire for both.
* **Task 12 — Real subprocess smoke.** ``sleep`` via both proc and
  bash tools; ``os.kill(pid, 0)`` verifies PIDs are dead after cleanup.
* **Parallel-call idempotency (approver suggestion).** Two concurrent
  ``cleanup_instance`` calls on the same instance.

Conventions
-----------

* Tests use ``pytest-asyncio`` (mode=auto via ``pyproject.toml``).
* Mock-based scenarios (A, B, C, G, H1, H2, H3, no-op, ERROR/FAILED)
  patch the singleton accessors (``get_background_process_manager``,
  ``get_bash_process_registry``) and assert the call shape.
* Real-subprocess scenarios (F, Task 12) use the actual bash /
  proc tools and verify with ``os.kill(pid, 0)``.
* An autouse fixture reaps any stray ``sleep`` processes spawned by
  this pack so a failed assertion cannot leak zombies. Each test
  adds the spawned PIDs it tracks to a module-level list and the
  fixture SIGKILLs any still alive at teardown.
* Windows-only code paths are guarded by
  ``pytest.mark.skipif(sys.platform == 'win32')``.
"""

from __future__ import annotations

import asyncio
import importlib
import os
import signal
import sys
import tempfile
import textwrap
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

# Reap registry + helpers + autouse fixture live in
# ``tests/tools/conftest.py`` so they apply to every test file under
# ``tests/tools/`` (including sibling ``test_bash_cancel.py``).
from tests.tools.conftest import (  # noqa: E402
    _register_pid,
    _pid_alive,
)


# =============================================================================
# Helpers — observer wiring
# =============================================================================


def _build_observer(
    *,
    proc_mgr_mock: Any | None = None,
    bash_reg_mock: Any | None = None,
    get_tree_ids_result: list[str] | None = None,
    instance_repository: Any | None = "auto",
) -> tuple[Any, MagicMock]:
    """Build a :class:`JobFeedbackObserver` with mocked dependencies.

    Returns ``(observer, instance_manager_mock)``. The dispatcher
    patches ``daemon.tools.proc_tools.get_background_process_manager``
    and ``daemon.tools.bash.get_bash_process_registry`` per-test (see
    the individual tests below) so this helper does not patch them.

    Args:
        proc_mgr_mock: Optional override for the proc manager (otherwise
            constructed as an ``AsyncMock`` with ``cleanup_instance``).
        bash_reg_mock: Optional override for the bash registry.
        get_tree_ids_result: Return value for
            ``_instance_repository.get_tree_ids``. ``None`` is
            interpreted as the empty subtree.
        instance_repository: ``"auto"`` (default) installs a MagicMock
            whose ``get_tree_ids`` returns
            ``get_tree_ids_result or []``. Pass ``None`` to delete the
            attribute (H3 sub-case).
    """
    from daemon.services.job_feedback_observer import JobFeedbackObserver

    mock_instance_manager = MagicMock()
    mock_instance_manager._live_hub = MagicMock()
    mock_instance_manager._live_hub.stream_status_change = AsyncMock()
    mock_instance_manager._events_service = MagicMock()
    mock_instance_manager._events_service._publish_instance_lifecycle_event = (
        AsyncMock()
    )
    mock_instance_manager._get_last_assistant_message_raw = AsyncMock(
        return_value="mock-content"
    )

    if instance_repository is None:
        # H3 sub-case: explicitly missing.
        if hasattr(mock_instance_manager, "_instance_repository"):
            del mock_instance_manager._instance_repository
        mock_instance_manager._instance_repository = None
    else:
        if instance_repository == "auto":
            mock_instance_manager._instance_repository = MagicMock()
        else:
            mock_instance_manager._instance_repository = instance_repository
        mock_instance_manager._instance_repository.get_tree_ids = MagicMock(
            return_value=get_tree_ids_result or []
        )

    observer = JobFeedbackObserver(
        event_bus=MagicMock(),
        job_queue_service=MagicMock(),
        job_repo=MagicMock(),
        lock_repo=MagicMock(),
        project_repo=MagicMock(),
        instance_manager=mock_instance_manager,
        config=None,
    )
    return observer, mock_instance_manager


def _patch_registries(
    monkeypatch: pytest.MonkeyPatch,
    *,
    proc_mgr: Any,
    bash_reg: Any,
) -> None:
    """Patch the two singleton accessors used by the dispatcher."""
    bash_pkg = importlib.import_module("daemon.tools.bash")
    proc_pkg = importlib.import_module("daemon.tools.proc_tools")

    monkeypatch.setattr(
        proc_pkg,
        "get_background_process_manager",
        lambda: proc_mgr,
    )
    monkeypatch.setattr(
        bash_pkg,
        "get_bash_process_registry",
        lambda: bash_reg,
    )


# =============================================================================
# Scenario A — Tier 1 self-cleanup on child completion
# =============================================================================


class TestScenarioATier1SelfCleanup:
    """Child instance cleanup: Tier 1 fires, Tier 2 skipped.

    Setup: ``root`` (parent_id=None) has a child ``child1`` with a
    proc process and a sibling ``child2`` with a bash grandchild.
    When ``child1`` reaches terminal, Tier 1 cleans ``child1``'s
    processes on both registries. Tier 2 is skipped because
    ``parent_id != None``.
    """

    @pytest.mark.asyncio
    async def test_tier1_fires_on_both_registries_for_child(self, monkeypatch):
        """Tier 1 calls ``cleanup_instance(child_id)`` on BOTH registries."""
        proc_mgr = AsyncMock(name="BackgroundProcessManager")
        bash_reg = AsyncMock(name="BashProcessRegistry")

        _patch_registries(monkeypatch, proc_mgr=proc_mgr, bash_reg=bash_reg)
        observer, im = _build_observer(
            get_tree_ids_result=None,
        )
        # Make get_tree_ids explode if accidentally called.
        im._instance_repository.get_tree_ids = MagicMock(
            side_effect=AssertionError(
                "get_tree_ids must NOT be called for non-root"
            )
        )

        child_id = "child-a-12345678"
        parent_id = "parent-a-87654321"

        await observer._dispatch_instance_post_commit_side_effects(
            instance_id=child_id,
            terminal_status="completed",
            error=None,
            parent_id=parent_id,
            agent_id="developer",
            last_content="hello",
        )

        # Tier 1 fired on BOTH registries.
        proc_mgr.cleanup_instance.assert_awaited_once_with(child_id)
        bash_reg.cleanup_instance.assert_awaited_once_with(child_id)

        # Tier 2 was skipped.
        assert not im._instance_repository.get_tree_ids.called, (
            "Tier 2 must not fire for a child instance"
        )

    @pytest.mark.asyncio
    async def test_tier1_does_not_touch_sibling_or_root(self, monkeypatch):
        """Tier 1 only cleans the terminating child; sibling + root untouched.

        We assert by call count: ``cleanup_instance`` must have been
        called exactly once on each registry, with the child's id.
        """
        proc_mgr = AsyncMock(name="BackgroundProcessManager")
        bash_reg = AsyncMock(name="BashProcessRegistry")
        _patch_registries(monkeypatch, proc_mgr=proc_mgr, bash_reg=bash_reg)
        observer, _im = _build_observer()

        child_id = "child-only"
        root_id = "root-other"
        sibling_id = "sibling-other"

        await observer._dispatch_instance_post_commit_side_effects(
            instance_id=child_id,
            terminal_status="completed",
            error=None,
            parent_id=root_id,
            agent_id="developer",
            last_content="hello",
        )

        # Cleanup was called exactly once with the child's id.
        proc_calls = [
            c.args[0] for c in proc_mgr.cleanup_instance.await_args_list
        ]
        bash_calls = [
            c.args[0] for c in bash_reg.cleanup_instance.await_args_list
        ]
        assert proc_calls == [child_id]
        assert bash_calls == [child_id]
        # Root and sibling are NOT in the cleanup list.
        assert root_id not in proc_calls
        assert root_id not in bash_calls
        assert sibling_id not in proc_calls
        assert sibling_id not in bash_calls


# =============================================================================
# Scenario B — Tier 2 root sweep kills descendants
# =============================================================================


class TestScenarioBTier2RootSweep:
    """Root cleanup: Tier 1 cleans root, Tier 2 sweeps descendants.

    Setup: ``root`` (parent_id=None) with proc processes; ``child1``
    with a proc process; ``child2`` with a bash grandchild. Root
    reaches terminal → Tier 1 cleans root → Tier 2 walks
    ``get_tree_ids`` → cleans child1 + child2 → skips root.
    """

    @pytest.mark.asyncio
    async def test_tier1_cleans_root_and_tier2_cleans_descendants(
        self, monkeypatch
    ):
        proc_mgr = AsyncMock(name="BackgroundProcessManager")
        bash_reg = AsyncMock(name="BashProcessRegistry")
        _patch_registries(monkeypatch, proc_mgr=proc_mgr, bash_reg=bash_reg)
        root_id = "root-b-aaaaaaaa"
        child1_id = "child-b1-bbbbbbbb"
        child2_id = "child-b2-cccccccc"
        observer, im = _build_observer(
            get_tree_ids_result=[root_id, child1_id, child2_id],
        )

        await observer._dispatch_instance_post_commit_side_effects(
            instance_id=root_id,
            terminal_status="completed",
            error=None,
            parent_id=None,
            agent_id="developer",
            last_content="hello",
        )

        # Tier 1 fired on root for both registries.
        # Tier 2 fired on child1 and child2 for both registries.
        proc_calls = [
            c.args[0] for c in proc_mgr.cleanup_instance.await_args_list
        ]
        bash_calls = [
            c.args[0] for c in bash_reg.cleanup_instance.await_args_list
        ]

        # Root appears exactly once across Tier 1 + Tier 2.
        assert proc_calls.count(root_id) == 1, (
            f"Root must be cleaned exactly once; proc_calls={proc_calls}"
        )
        assert bash_calls.count(root_id) == 1, (
            f"Root must be cleaned exactly once on bash; bash_calls={bash_calls}"
        )

        # Descendants appear in both registries' cleanup lists.
        assert child1_id in proc_calls
        assert child2_id in proc_calls
        assert child1_id in bash_calls
        assert child2_id in bash_calls

        # get_tree_ids was called exactly once.
        im._instance_repository.get_tree_ids.assert_called_once_with(root_id)

    @pytest.mark.asyncio
    async def test_root_is_skipped_on_second_pass_in_tier2(self, monkeypatch):
        """Tier 2 must ``continue`` past root so it isn't double-cleaned."""
        proc_mgr = AsyncMock(name="BackgroundProcessManager")
        bash_reg = AsyncMock(name="BashProcessRegistry")
        _patch_registries(monkeypatch, proc_mgr=proc_mgr, bash_reg=bash_reg)
        root_id = "root-skip"
        child_id = "child-skip"
        observer, _im = _build_observer(
            get_tree_ids_result=[root_id, child_id],
        )

        await observer._dispatch_instance_post_commit_side_effects(
            instance_id=root_id,
            terminal_status="completed",
            error=None,
            parent_id=None,
            agent_id="developer",
            last_content="hello",
        )

        # Both registries should clean root exactly once.
        proc_calls = [
            c.args[0] for c in proc_mgr.cleanup_instance.await_args_list
        ]
        bash_calls = [
            c.args[0] for c in bash_reg.cleanup_instance.await_args_list
        ]
        assert proc_calls.count(root_id) == 1
        assert bash_calls.count(root_id) == 1
        # Children are cleaned.
        assert child_id in proc_calls
        assert child_id in bash_calls


# =============================================================================
# Scenario C — Child-then-root ordering (C5 fix)
# =============================================================================


class TestScenarioCChildThenRootOrdering:
    """Two-step ordering: child cleans immediately, root sweeps the rest.

    Action 1: child1 reaches terminal → Tier 1 cleans child1's procs.
    Action 2: root reaches terminal → Tier 1 cleans root + Tier 2
    cleans child2 (child1 already empty, idempotent).
    """

    @pytest.mark.asyncio
    async def test_child_then_root_does_not_leak(self, monkeypatch):
        proc_mgr = AsyncMock(name="BackgroundProcessManager")
        bash_reg = AsyncMock(name="BashProcessRegistry")
        _patch_registries(monkeypatch, proc_mgr=proc_mgr, bash_reg=bash_reg)
        root_id = "root-c-aaaaaaaa"
        child1_id = "child-c1-bbbbbbbb"
        child2_id = "child-c2-cccccccc"

        # Start with child1's subtree (still in mock setup, no cleanups yet).
        observer, im = _build_observer(
            get_tree_ids_result=[root_id, child1_id, child2_id],
        )

        # Action 1: child1 reaches terminal BEFORE root.
        await observer._dispatch_instance_post_commit_side_effects(
            instance_id=child1_id,
            terminal_status="completed",
            error=None,
            parent_id=root_id,
            agent_id="developer",
            last_content="hello",
        )

        # After Action 1: Tier 1 cleans child1, Tier 2 NOT called.
        proc_calls = [
            c.args[0] for c in proc_mgr.cleanup_instance.await_args_list
        ]
        bash_calls = [
            c.args[0] for c in bash_reg.cleanup_instance.await_args_list
        ]
        assert proc_calls == [child1_id]
        assert bash_calls == [child1_id]
        assert not im._instance_repository.get_tree_ids.called, (
            "Tier 2 must NOT fire for child1 — its processes must be "
            "cleaned immediately, not deferred to the root's finalization"
        )

        # Action 2: root reaches terminal.
        await observer._dispatch_instance_post_commit_side_effects(
            instance_id=root_id,
            terminal_status="completed",
            error=None,
            parent_id=None,
            agent_id="developer",
            last_content="hello",
        )

        # After Action 2: Tier 1 cleans root + Tier 2 walks tree.
        # Child1 is in the tree but its cleanup is idempotent (mock
        # doesn't fail on extra calls).
        proc_calls = [
            c.args[0] for c in proc_mgr.cleanup_instance.await_args_list
        ]
        bash_calls = [
            c.args[0] for c in bash_reg.cleanup_instance.await_args_list
        ]

        # Root appears at least once.
        assert root_id in proc_calls
        assert root_id in bash_calls
        # Child1 is cleaned again (idempotent on the mock).
        assert child1_id in proc_calls
        # Child2 is in Tier 2.
        assert child2_id in proc_calls
        assert child2_id in bash_calls
        # get_tree_ids was called once (only on the root terminal).
        im._instance_repository.get_tree_ids.assert_called_once_with(root_id)

    @pytest.mark.asyncio
    async def test_child1_does_not_leak_between_actions(self, monkeypatch):
        """The C5 fix: child1's processes are cleaned at Action 1, no leak.

        With the OLD design (Tier 2 only on root), child1's processes
        would stay alive until root finalized — that's a leak window.
        Verify the new Tier 1-on-every-terminal design closes it.
        """
        proc_mgr = AsyncMock(name="BackgroundProcessManager")
        bash_reg = AsyncMock(name="BashProcessRegistry")
        _patch_registries(monkeypatch, proc_mgr=proc_mgr, bash_reg=bash_reg)

        child1_id = "child-c5-leak"

        # Build observer WITHOUT tree support to make Tier 2 a no-op
        # even on root (simulates a misconfigured tree repository).
        observer, _im = _build_observer()

        # Action 1: child1 reaches terminal — Tier 1 must clean it.
        await observer._dispatch_instance_post_commit_side_effects(
            instance_id=child1_id,
            terminal_status="completed",
            error=None,
            parent_id="root-c5",
            agent_id="developer",
            last_content="hello",
        )

        # Tier 1 cleanup happened — this is the C5 fix. Without it,
        # child1's procs would still be alive here.
        proc_calls = [
            c.args[0] for c in proc_mgr.cleanup_instance.await_args_list
        ]
        bash_calls = [
            c.args[0] for c in bash_reg.cleanup_instance.await_args_list
        ]
        assert child1_id in proc_calls, (
            "C5 fix: child1's procs MUST be cleaned on terminal, "
            "not deferred to root finalization"
        )
        assert child1_id in bash_calls


# =============================================================================
# Scenario F — Nohup grandchild killed via process group (REAL subprocess)
# =============================================================================


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only: killpg")
class TestScenarioFNohupGrandchild:
    """``nohup`` grandchild survives foreground exit; killed via pgid.

    Bash tool spawns ``bash -c 'nohup sh -c "echo $$ > /tmp/gc.pid;
    sleep 3600" & sleep 0.5; cat /tmp/gc.pid'``. The foreground exits
    after 0.5s but the ``nohup``'d grandchild keeps running in the
    same process group (it did not call ``setsid``). The bash registry
    still has the entry. Tier 1 ``cleanup_instance`` calls
    ``os.killpg`` → grandchild dead.
    """

    @pytest.mark.asyncio
    async def test_nohup_grandchild_killed_by_killpg(
        self, tmp_path, monkeypatch
    ):
        """Run a real bash command that nohup-detaches a grandchild.

        The grandchild pid is captured from the inner shell's ``echo $$``.
        After the foreground returns, the grandchild is still alive in
        the same pgid as the bash shell. We trigger Tier 1 cleanup on
        the registry, which calls ``os.killpg``, and verify the
        grandchild is dead.
        """
        bash_pkg = importlib.import_module("daemon.tools.bash")

        # ``bash_pkg.bash`` is a StructuredTool (BaseTool). The
        # underlying async function lives at ``.coroutine``. The LSP
        # doesn't know about the attribute, so we silence it.
        bash_tool = bash_pkg.bash.coroutine  # type: ignore[attr-defined]

        instance_id = f"inst-f-{os.urandom(4).hex()}"
        gc_pid_file = tmp_path / "grandchild.pid"

        # Use the SAME ``_kill_group`` (real os.killpg) so we can
        # verify the grandchild actually dies via process group kill.
        # No monkeypatching of the kill path.
        # Use a short-lived sleep for the grandchild: pytest-timeout
        # is configured to 30s and a longer sleep would interact with
        # the test runner's process cleanup (e.g., pytest-timeout
        # might SIGKILL the test's process tree). 5s is plenty since
        # the foreground `sleep 0.5` exits first and we just need
        # the grandchild alive long enough to verify cleanup.
        #
        # IMPORTANT: ``$$`` inside the outer bash -c evaluates to
        # the bash tool's outer sh pid (which exits when the
        # foreground returns), NOT the nohup'd grandchild's pid.
        # We must write the grandchild's pid from inside the
        # nohup'd sh using ``sh -c 'echo $PPID'`` (the PPID of the
        # nohup'd sh is the bash -c process, and ``$BASHPID`` gives
        # the current shell's own pid) — but the most portable
        # trick is to use ``$!`` from the *foreground* bash, which
        # gives the pid of the most recent background job. That pid
        # is the nohup'd sh's pid.
        command = textwrap.dedent(
            f"""\
            bash -c 'nohup sleep 5 >/dev/null 2>&1 & GPID=$!; echo $GPID > {gc_pid_file}; echo $GPID; sleep 0.5'
            """
        )

        result = await bash_tool(
            command=command,
            instance_id=instance_id,
            timeout=30,
        )

        # The bash tool returned (foreground exited after 0.5s).
        # Parse the grandchild pid from the output. The bash tool
        # formats output as ``STDOUT:\n<PID>\n\nEXIT CODE: 0`` — pull
        # the first numeric token in the STDOUT section.
        import re

        stdout_match = re.search(
            r"STDOUT:\s*\n([0-9]+)", result, re.MULTILINE
        )
        if not stdout_match:
            pytest.fail(
                f"Could not parse grandchild pid from bash output: "
                f"{result!r}"
            )
        gc_pid = int(stdout_match.group(1).strip())
        _register_pid(gc_pid, "grandchild", "scenario-f")

        # Bash returned cleanly (registry STILL has the entry — D5).
        registry = bash_pkg.get_bash_process_registry()
        assert instance_id in registry._entries, (
            "Bash registry should retain the entry — D5 documents that "
            "natural foreground exit does not unregister"
        )

        # Brief settle delay: the nohup'd grandchild may still be
        # finalizing its stdio setup right after the foreground exits.
        await asyncio.sleep(0.2)

        # Sanity: grandchild is alive right now.
        assert _pid_alive(gc_pid), (
            f"Grandchild {gc_pid} should be alive before cleanup; "
            "the nohup & sleep 0.5 wrapper should have detached it"
        )

        # Trigger Tier 1 cleanup on the bash registry.
        await registry.cleanup_instance(instance_id)

        # Give the kernel a moment to reap after SIGKILL.
        await asyncio.sleep(0.2)

        # Grandchild is dead.
        assert not _pid_alive(gc_pid), (
            f"Grandchild {gc_pid} must be killed via os.killpg after "
            "Tier 1 bash cleanup_instance"
        )
        # Registry is empty.
        assert instance_id not in registry._entries


# =============================================================================
# Scenario G — Double-fire idempotency
# =============================================================================


class TestScenarioGDoubleFireIdempotency:
    """terminate cascade + finalize dispatcher must be safe to call twice.

    Action 1: ``cleanup_instance`` directly on root + children (the
    cascade path). Action 2: ``_dispatch_instance_post_commit_side_effects``
    (the finalize path). Both must succeed without exceptions; the
    empty-pop is atomic.
    """

    @pytest.mark.asyncio
    async def test_double_fire_is_idempotent(self, monkeypatch):
        """Action 1 + Action 2 produce no exceptions; mock cleanup called."""
        proc_mgr = AsyncMock(name="BackgroundProcessManager")
        bash_reg = AsyncMock(name="BashProcessRegistry")
        _patch_registries(monkeypatch, proc_mgr=proc_mgr, bash_reg=bash_reg)

        root_id = "root-g"
        child1_id = "child-g-1"
        child2_id = "child-g-2"

        # Wire get_tree_ids to return the subtree for Action 2.
        observer, im = _build_observer(
            get_tree_ids_result=[root_id, child1_id, child2_id],
        )

        # Action 1: cascade cleanup (the actual code path is
        # ``terminate_instance`` → per-child ``cleanup_instance`` at
        # instance_lifecycle.py:1461, but for the unit test we just
        # call ``cleanup_instance`` directly to mirror what the
        # cascade does).
        await proc_mgr.cleanup_instance(root_id)
        await proc_mgr.cleanup_instance(child1_id)
        await proc_mgr.cleanup_instance(child2_id)
        await bash_reg.cleanup_instance(root_id)
        await bash_reg.cleanup_instance(child1_id)
        await bash_reg.cleanup_instance(child2_id)

        # Snapshot call counts after Action 1.
        proc_after_cascade = proc_mgr.cleanup_instance.await_count
        bash_after_cascade = bash_reg.cleanup_instance.await_count

        # Action 2: dispatcher path. Must not raise.
        await observer._dispatch_instance_post_commit_side_effects(
            instance_id=root_id,
            terminal_status="terminated",
            error=None,
            parent_id=None,
            agent_id="developer",
            last_content="hello",
        )

        # Action 2 made more calls (Tier 1 root + Tier 2 children,
        # for both registries). These are all on the AsyncMock —
        # they don't raise because mocks accept everything.
        assert (
            proc_mgr.cleanup_instance.await_count > proc_after_cascade
        ), "Dispatcher Tier 1 + Tier 2 must call proc cleanup"
        assert (
            bash_reg.cleanup_instance.await_count > bash_after_cascade
        ), "Dispatcher Tier 1 + Tier 2 must call bash cleanup"

    @pytest.mark.asyncio
    async def test_double_fire_on_real_registries_is_no_op(self):
        """On the REAL registries, a second cleanup_instance is benign.

        Use the real singletons to confirm the atomic empty-pop: a
        second call returns 0 and does not raise. No subprocesses
        are involved (the registries are empty).
        """
        bash_pkg = importlib.import_module("daemon.tools.bash")
        proc_pkg = importlib.import_module("daemon.tools.proc_tools")

        bash_reg = bash_pkg.get_bash_process_registry()
        proc_mgr = proc_pkg.get_background_process_manager()
        instance_id = "inst-g-double"

        # First call on empty registry: returns 0, no-op.
        first_bash = await bash_reg.cleanup_instance(instance_id)
        first_proc_ok = True
        try:
            await proc_mgr.cleanup_instance(instance_id)
        except Exception:
            first_proc_ok = False

        # Second call: still returns 0, no-op.
        second_bash = await bash_reg.cleanup_instance(instance_id)
        second_proc_ok = True
        try:
            await proc_mgr.cleanup_instance(instance_id)
        except Exception:
            second_proc_ok = False

        assert first_bash == 0
        assert second_bash == 0
        assert first_proc_ok
        assert second_proc_ok


# =============================================================================
# Scenario H — Best-effort failure isolation (3 sub-cases)
# =============================================================================


class TestScenarioHBestEffortIsolation:
    """Failure modes are isolated; Tier 1 and other side-effects survive."""

    @pytest.mark.asyncio
    async def test_h1_get_tree_ids_raises(self, monkeypatch, caplog):
        """H1: ``get_tree_ids`` raises → WARNING, Tier 1 + side-effects OK."""
        proc_mgr = AsyncMock(name="BackgroundProcessManager")
        bash_reg = AsyncMock(name="BashProcessRegistry")
        _patch_registries(monkeypatch, proc_mgr=proc_mgr, bash_reg=bash_reg)
        root_id = "root-h1"
        observer, im = _build_observer()
        im._instance_repository.get_tree_ids = MagicMock(
            side_effect=RuntimeError("DB locked (synthetic)")
        )

        with caplog.at_level("WARNING"):
            await observer._dispatch_instance_post_commit_side_effects(
                instance_id=root_id,
                terminal_status="completed",
                error=None,
                parent_id=None,
                agent_id="developer",
                last_content="hello",
            )

        # Tier 1 ran (root cleaned on both registries).
        proc_calls = [
            c.args[0] for c in proc_mgr.cleanup_instance.await_args_list
        ]
        bash_calls = [
            c.args[0] for c in bash_reg.cleanup_instance.await_args_list
        ]
        assert root_id in proc_calls
        assert root_id in bash_calls

        # WARNING logged for get_tree_ids failure.
        warning_texts = [
            r.getMessage() for r in caplog.records if r.levelname == "WARNING"
        ]
        assert any("get_tree_ids failed" in msg for msg in warning_texts), (
            f"Expected get_tree_ids WARNING, got: {warning_texts}"
        )

        # Other side-effects (SSE + lifecycle) still fired.
        im._live_hub.stream_status_change.assert_awaited_once()
        im._events_service._publish_instance_lifecycle_event.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_h2_cleanup_descendant_raises(self, monkeypatch, caplog):
        """H2: cleanup_instance(child) raises in Tier 2 → siblings still cleaned."""
        proc_mgr = AsyncMock(name="BackgroundProcessManager")
        bash_reg = AsyncMock(name="BashProcessRegistry")
        _patch_registries(monkeypatch, proc_mgr=proc_mgr, bash_reg=bash_reg)

        root_id = "root-h2"
        failing_child = "child-h2-fail"
        other_child = "child-h2-ok"

        # Build observer with the subtree.
        observer, im = _build_observer(
            get_tree_ids_result=[root_id, failing_child, other_child],
        )

        # Mock proc cleanup_instance to raise ONLY for failing_child.
        original_proc_cleanup = proc_mgr.cleanup_instance
        original_bash_cleanup = bash_reg.cleanup_instance

        async def proc_cleanup_selector(instance_id):
            if instance_id == failing_child:
                raise RuntimeError("synthetic Tier-2 proc failure")
            return await original_proc_cleanup.return_value  # type: ignore[attr-defined]

        async def bash_cleanup_selector(instance_id):
            if instance_id == failing_child:
                raise RuntimeError("synthetic Tier-2 bash failure")
            return await original_bash_cleanup.return_value  # type: ignore[attr-defined]

        proc_mgr.cleanup_instance.side_effect = proc_cleanup_selector
        bash_reg.cleanup_instance.side_effect = bash_cleanup_selector

        with caplog.at_level("WARNING"):
            # Must not propagate.
            await observer._dispatch_instance_post_commit_side_effects(
                instance_id=root_id,
                terminal_status="completed",
                error=None,
                parent_id=None,
                agent_id="developer",
                last_content="hello",
            )

        # other_child was still cleaned (Tier 2 loop continued).
        proc_calls = [
            c.args[0] for c in proc_mgr.cleanup_instance.await_args_list
        ]
        bash_calls = [
            c.args[0] for c in bash_reg.cleanup_instance.await_args_list
        ]
        assert other_child in proc_calls, (
            f"Tier 2 loop must continue past failing_child; "
            f"proc_calls={proc_calls}"
        )
        assert other_child in bash_calls, (
            f"Tier 2 loop must continue past failing_child; "
            f"bash_calls={bash_calls}"
        )

        # WARNINGs logged for the Tier-2 failures.
        warning_texts = [
            r.getMessage() for r in caplog.records if r.levelname == "WARNING"
        ]
        assert any(
            "Tier-2 proc cleanup failed" in msg for msg in warning_texts
        ), f"Expected Tier-2 proc failure WARNING, got: {warning_texts}"
        assert any(
            "Tier-2 bash cleanup failed" in msg for msg in warning_texts
        ), f"Expected Tier-2 bash failure WARNING, got: {warning_texts}"

    @pytest.mark.asyncio
    async def test_h3_instance_repository_missing(self, monkeypatch, caplog):
        """H3: ``_instance_repository`` is None → WARNING, Tier 2 skipped."""
        proc_mgr = AsyncMock(name="BackgroundProcessManager")
        bash_reg = AsyncMock(name="BashProcessRegistry")
        _patch_registries(monkeypatch, proc_mgr=proc_mgr, bash_reg=bash_reg)
        root_id = "root-h3"
        observer, im = _build_observer(instance_repository=None)

        with caplog.at_level("WARNING"):
            await observer._dispatch_instance_post_commit_side_effects(
                instance_id=root_id,
                terminal_status="completed",
                error=None,
                parent_id=None,
                agent_id="developer",
                last_content="hello",
            )

        # Tier 1 still ran (root cleaned).
        proc_calls = [
            c.args[0] for c in proc_mgr.cleanup_instance.await_args_list
        ]
        bash_calls = [
            c.args[0] for c in bash_reg.cleanup_instance.await_args_list
        ]
        assert proc_calls == [root_id]
        assert bash_calls == [root_id]

        # Tier 2 skipped — no descendants cleaned.
        assert proc_mgr.cleanup_instance.await_count == 1
        assert bash_reg.cleanup_instance.await_count == 1

        # WARNING logged for missing repository.
        warning_texts = [
            r.getMessage() for r in caplog.records if r.levelname == "WARNING"
        ]
        assert any("no _instance_repository" in msg for msg in warning_texts), (
            f"Expected missing-repo WARNING, got: {warning_texts}"
        )


# =============================================================================
# Task 4 — Daemon shutdown kills everything
# =============================================================================


class TestDaemonShutdownKillsEverything:
    """``manager.shutdown()`` invokes ``cleanup_all()`` on both registries.

    Populate both registries with real subprocess entries, call
    ``manager.shutdown()``, and verify:

      1. Both ``cleanup_all()`` were awaited.
      2. Both registries are empty.
      3. All subprocess PIDs are dead (``os.kill(pid, 0)``).
    """

    @pytest.fixture
    def mock_config(self):
        """Minimal Config-shaped mock — same shape as test_manager_shutdown."""
        from daemon.config import (
            AgentsConfig,
            Config,
            DaemonConfig,
            LimitsConfig,
            LLMConfig,
            PersistenceConfig,
        )

        return Config(
            llm=LLMConfig(
                base_url="https://api.openai.com/v1",
                api_key="test-key",
                model="gpt-4",
                temperature=0.7,
            ),
            limits=LimitsConfig(
                max_children_per_instance=3,
                instance_timeout_minutes=60,
                message_rate_limit=60,
            ),
            persistence=PersistenceConfig(
                db_path=":memory:",
                checkpoint_interval=1,
                checkpoint_ttl_hours=168,
                checkpoint_cleanup_interval=24,
                max_instance_history=300,
            ),
            daemon=DaemonConfig(host="0.0.0.0", port=8079),
            agents=AgentsConfig(directory="./agents"),
        )

    def _build_minimal_manager(self, mock_config):
        """Build a real InstanceManager with heavy components mocked.

        Mirrors ``tests/test_manager_shutdown.py:_build_minimal_manager`` —
        every step after the proc/bash cleanup_all hooks is stubbed so
        we only exercise the shutdown sequence's two real calls.
        """
        from unittest.mock import patch

        from daemon.manager import InstanceManager

        class _NoOpMigrationRunner:
            def __init__(self, engine):
                self._engine = engine

            def run_pending_migrations(self):
                return 0

        with patch("daemon.manager.PromptCache"), patch(
            "daemon.manager.build_instance_graph"
        ), patch(
            "daemon.manager.load_and_cache_prompt", return_value=("sp", 0)
        ), patch(
            "daemon.manager.create_instance_tools", return_value=[]
        ), patch(
            "daemon.migrations.runner.MigrationRunner", _NoOpMigrationRunner
        ):
            manager = InstanceManager(mock_config)

        # Stub post-cleanup-all shutdown steps so the test does not
        # attempt to close real DBs / event buses.
        manager.stop_sources = AsyncMock()
        manager._cancel_all_active_requests = AsyncMock()
        manager._wait_for_inflight = AsyncMock()
        manager.shutdown_worker_pool = MagicMock()
        manager._event_bus = MagicMock()
        manager._event_bus.shutdown = AsyncMock()
        manager._maintenance_service = MagicMock()
        manager._maintenance_service.stop = AsyncMock()
        manager._db_pool_manager = MagicMock()
        manager._db_pool_manager.dispose_all = MagicMock()
        manager.close_checkpointer = AsyncMock()
        manager._drain_warmup_pool = AsyncMock()
        manager._mcp_service = MagicMock()
        manager._mcp_service.close_all_connections = AsyncMock()
        manager._shutdown_opencode_registry = AsyncMock()
        manager.cleanup = MagicMock()

        class _StubCancellationService:
            @property
            def is_shutting_down(self) -> bool:
                return False

        manager._cancellation_service = _StubCancellationService()  # type: ignore[assignment]
        manager._background_tasks = []
        return manager

    @pytest.mark.asyncio
    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only: killpg")
    async def test_shutdown_sweeps_both_registries_with_real_processes(
        self, mock_config, monkeypatch
    ):
        """Populate bash AND proc registries with real subprocesses; shutdown kills all.

        We populate both registries with real ``sleep 30`` processes across
        the same 3 instances, then call ``manager.shutdown()`` and verify:

          1. ``cleanup_all()`` ran on BOTH registries.
          2. Both registries are empty.
          3. All spawned PIDs (bash + proc) are dead (``os.kill(pid, 0)``).

        The single shared ``try/finally`` reaps any pids that survive a
        failed assertion, so a hang can't leak zombies into later tests.
        """
        bash_mod = importlib.import_module("daemon.tools.bash")
        proc_mod = importlib.import_module("daemon.tools.proc_tools")
        bash = bash_mod.bash.coroutine
        registry = bash_mod.get_bash_process_registry()
        proc_mgr = proc_mod.get_background_process_manager()

        # Use 3 distinct instances each with one bash + one proc sleep.
        instance_ids = [f"shutdown-inst-{i}" for i in range(3)]
        bash_spawned_pids: list[int] = []
        proc_spawned_pids: list[int] = []

        try:
            # --- Populate bash registry ---
            for iid in instance_ids:
                # Start a long sleep via bash; the registry retains the
                # entry because the foreground never exits.
                task = asyncio.create_task(
                    bash(
                        command="sleep 30",
                        instance_id=iid,
                        timeout=60,
                    )
                )
                # Give the spawn a moment.
                await asyncio.sleep(0.5)
                # Pull the registered pid from the registry.
                entries = registry._entries.get(iid)
                assert entries, (
                    f"Bash registry should have an entry for {iid} "
                    f"after spawn; got: {list(registry._entries.keys())}"
                )
                pid = entries[0].pid
                bash_spawned_pids.append(pid)
                _register_pid(pid, "shutdown-bash", iid)
                # Sanity: pid is alive.
                assert _pid_alive(pid)

            # --- Populate proc registry with real subprocesses ---
            # Use the manager directly (start_process). Pattern mirrors
            # ``TestRealSubprocessSmoke.test_real_sleep_via_proc_killed_by_tier1``.
            for iid in instance_ids:
                process_id, err = await proc_mgr.start_process(
                    instance_id=iid,
                    command="sleep 30",
                    workdir=None,
                    timeout_seconds=0,
                )
                assert err is None, f"start_process failed for {iid}: {err}"
                assert process_id is not None
                info = proc_mgr._processes[iid][process_id]
                pid = info.proc.pid
                proc_spawned_pids.append(pid)
                _register_pid(pid, "shutdown-proc", iid)
                # Sanity: pid is alive.
                assert _pid_alive(pid)

            # Build the manager. The actual ``manager.shutdown()`` will
            # invoke ``get_bash_process_registry().cleanup_all()`` AND
            # ``get_background_process_manager().cleanup_all()`` on the
            # same singleton instances we populated.
            manager = self._build_minimal_manager(mock_config)

            await manager.shutdown(grace_period=0.01)

            # Both registries are empty after cleanup_all.
            assert registry._entries == {}, (
                f"Bash registry should be empty after shutdown; "
                f"got: {list(registry._entries.keys())}"
            )
            assert proc_mgr._processes == {}, (
                f"Proc registry should be empty after shutdown; "
                f"got: {list(proc_mgr._processes.keys())}"
            )

            # All spawned pids (bash + proc) are dead.
            await asyncio.sleep(0.2)  # let the kernel reap
            for pid, iid in zip(bash_spawned_pids, instance_ids):
                assert not _pid_alive(pid), (
                    f"Bash PID {pid} (from {iid}) must be dead after shutdown"
                )
            for pid, iid in zip(proc_spawned_pids, instance_ids):
                assert not _pid_alive(pid), (
                    f"Proc PID {pid} (from {iid}) must be dead after shutdown"
                )

            # Cancel the bash tasks (they're still awaiting wait_for).
            for task in [t for t in asyncio.all_tasks() if not t.done()]:
                # Don't cancel ourselves.
                if task is asyncio.current_task():
                    continue
                # Cancel leftover bash tasks so they don't hang.
                task.cancel()
        finally:
            # Belt-and-braces: reap any leftover pids (autouse fixture
            # also does this, but explicit here for clarity).
            for pid in bash_spawned_pids + proc_spawned_pids:
                if _pid_alive(pid):
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except (ProcessLookupError, OSError):
                        pass


# =============================================================================
# Task 8 — No-op when no processes exist
# =============================================================================


class TestNoOpWhenNoProcessesExist:
    """Empty registries terminate cleanly — no kill attempts, no errors."""

    @pytest.mark.asyncio
    async def test_no_op_for_child_with_empty_registries(self, monkeypatch):
        """Empty registries: Tier 1 returns 0 (mocked), Tier 2 skipped."""
        proc_mgr = AsyncMock(name="BackgroundProcessManager")
        bash_reg = AsyncMock(name="BashProcessRegistry")
        _patch_registries(monkeypatch, proc_mgr=proc_mgr, bash_reg=bash_reg)
        observer, _im = _build_observer()

        # Must not raise.
        await observer._dispatch_instance_post_commit_side_effects(
            instance_id="empty-child",
            terminal_status="completed",
            error=None,
            parent_id="empty-parent",
            agent_id="developer",
            last_content="hello",
        )

        # Tier 1 fired on both registries with the empty id (mocked).
        proc_mgr.cleanup_instance.assert_awaited_once_with("empty-child")
        bash_reg.cleanup_instance.assert_awaited_once_with("empty-child")

    @pytest.mark.asyncio
    async def test_no_op_for_root_with_empty_registries(self, monkeypatch):
        """Empty root: Tier 1 + Tier 2 fire, both no-op on empty buckets."""
        proc_mgr = AsyncMock(name="BackgroundProcessManager")
        bash_reg = AsyncMock(name="BashProcessRegistry")
        _patch_registries(monkeypatch, proc_mgr=proc_mgr, bash_reg=bash_reg)
        observer, im = _build_observer(get_tree_ids_result=[])

        # Must not raise.
        await observer._dispatch_instance_post_commit_side_effects(
            instance_id="empty-root",
            terminal_status="completed",
            error=None,
            parent_id=None,
            agent_id="developer",
            last_content="hello",
        )

        # Tier 1 fired once per registry.
        assert proc_mgr.cleanup_instance.await_count == 1
        assert bash_reg.cleanup_instance.await_count == 1
        # Tier 2 walked an empty tree.
        im._instance_repository.get_tree_ids.assert_called_once_with(
            "empty-root"
        )

    @pytest.mark.asyncio
    async def test_real_empty_registries_terminate_cleanly(self):
        """Real bash/proc registries: empty buckets, atomic pop, return 0."""
        bash_mod = importlib.import_module("daemon.tools.bash")
        proc_tools_mod = importlib.import_module("daemon.tools.proc_tools")
        bash_reg = bash_mod.get_bash_process_registry()
        proc_mgr = proc_tools_mod.get_background_process_manager()
        instance_id = "inst-empty-real"

        # Both empty-pop should return 0 / None and not raise.
        bash_killed = await bash_reg.cleanup_instance(instance_id)
        await proc_mgr.cleanup_instance(instance_id)

        assert bash_killed == 0


# =============================================================================
# Task 11 — ERROR / FAILED paths trigger Tier 1 + Tier 2
# =============================================================================


class TestErrorAndFailedPaths:
    """``terminal_status`` of ``"error"`` and ``"failed"`` both fire Tier 1+2.

    Both terminal statuses converge on
    ``_dispatch_instance_post_commit_side_effects``. Verify the
    cleanup tier is structural — independent of the status string.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "terminal_status,error_msg",
        [
            ("completed", None),
            ("error", "synthetic error"),
            ("failed", "synthetic failure"),
            ("terminated", "cascade-terminated"),
        ],
    )
    async def test_all_terminal_statuses_fire_tier1_and_tier2(
        self, monkeypatch, terminal_status, error_msg
    ):
        proc_mgr = AsyncMock(name="BackgroundProcessManager")
        bash_reg = AsyncMock(name="BashProcessRegistry")
        _patch_registries(monkeypatch, proc_mgr=proc_mgr, bash_reg=bash_reg)
        root_id = f"root-{terminal_status}"
        child_id = f"child-{terminal_status}"
        observer, im = _build_observer(
            get_tree_ids_result=[root_id, child_id],
        )

        await observer._dispatch_instance_post_commit_side_effects(
            instance_id=root_id,
            terminal_status=terminal_status,
            error=error_msg,
            parent_id=None,
            agent_id="developer",
            last_content="hello",
        )

        # Tier 1 fired on root.
        proc_calls = [
            c.args[0] for c in proc_mgr.cleanup_instance.await_args_list
        ]
        bash_calls = [
            c.args[0] for c in bash_reg.cleanup_instance.await_args_list
        ]
        assert root_id in proc_calls
        assert root_id in bash_calls
        # Tier 2 walked the tree (regardless of status).
        im._instance_repository.get_tree_ids.assert_called_once_with(root_id)
        # Tier 2 cleaned the child.
        assert child_id in proc_calls
        assert child_id in bash_calls


# =============================================================================
# Task 12 — Real subprocess smoke
# =============================================================================


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only: killpg")
class TestRealSubprocessSmoke:
    """Spawn real ``sleep`` via both proc and bash; verify dead after cleanup."""

    @pytest.mark.asyncio
    async def test_real_sleep_via_bash_killed_by_tier1(self):
        """Bash-spawned sleep is dead after ``cleanup_instance``."""
        bash_mod = importlib.import_module("daemon.tools.bash")
        bash = bash_mod.bash.coroutine
        registry = bash_mod.get_bash_process_registry()

        instance_id = f"smoke-bash-{os.urandom(4).hex()}"

        task = asyncio.create_task(
            bash(
                command="sleep 30",
                instance_id=instance_id,
                timeout=60,
            )
        )
        try:
            # Wait for spawn.
            await asyncio.sleep(0.5)
            entries = registry._entries.get(instance_id)
            assert entries, "Bash registry should have an entry after spawn"
            pid = entries[0].pid
            _register_pid(pid, "smoke-bash", instance_id)
            assert _pid_alive(pid)

            # Tier 1 cleanup.
            await registry.cleanup_instance(instance_id)
            await asyncio.sleep(0.2)

            assert not _pid_alive(pid), (
                f"PID {pid} (from bash smoke) must be dead after cleanup"
            )
            assert instance_id not in registry._entries
        finally:
            if not task.done():
                task.cancel()

    @pytest.mark.asyncio
    async def test_real_sleep_via_proc_killed_by_tier1(self):
        """Proc-spawned sleep is dead after ``cleanup_instance``."""
        proc_pkg = importlib.import_module("daemon.tools.proc_tools")
        manager = proc_pkg.get_background_process_manager()

        instance_id = f"smoke-proc-{os.urandom(4).hex()}"

        # Use the manager directly (start_process). Avoid the closure-
        # bound tools because we want to control the registration.
        process_id, err = await manager.start_process(
            instance_id=instance_id,
            command="sleep 30",
            workdir=None,
            timeout_seconds=0,
        )
        assert err is None, f"start_process failed: {err}"
        assert process_id is not None

        info = manager._processes[instance_id][process_id]
        pid = info.proc.pid
        _register_pid(pid, "smoke-proc", instance_id)
        assert _pid_alive(pid)

        await manager.cleanup_instance(instance_id)
        await asyncio.sleep(0.2)

        assert not _pid_alive(pid), (
            f"PID {pid} (from proc smoke) must be dead after cleanup"
        )
        assert instance_id not in manager._processes


# =============================================================================
# Approver suggestion — parallel-call idempotency
# =============================================================================


class TestParallelCallIdempotency:
    """Concurrent ``cleanup_instance`` calls on the same instance are safe."""

    @pytest.mark.asyncio
    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only: killpg")
    async def test_concurrent_bash_cleanup_instance_calls(self):
        """Two concurrent bash ``cleanup_instance`` on same instance: no error."""
        bash_mod = importlib.import_module("daemon.tools.bash")
        bash = bash_mod.bash.coroutine
        registry = bash_mod.get_bash_process_registry()

        instance_id = f"par-bash-{os.urandom(4).hex()}"

        task = asyncio.create_task(
            bash(
                command="sleep 30",
                instance_id=instance_id,
                timeout=60,
            )
        )
        try:
            await asyncio.sleep(0.5)
            entries = registry._entries.get(instance_id)
            assert entries, "Bash registry should have an entry after spawn"
            pid = entries[0].pid
            _register_pid(pid, "par-bash", instance_id)

            # Two concurrent cleanup_instance calls.
            results = await asyncio.gather(
                registry.cleanup_instance(instance_id),
                registry.cleanup_instance(instance_id),
                return_exceptions=True,
            )

            # Neither call should raise.
            for i, r in enumerate(results):
                assert not isinstance(r, BaseException), (
                    f"Concurrent cleanup_instance #{i} raised: {r}"
                )

            # Registry is empty after both calls.
            assert instance_id not in registry._entries
            await asyncio.sleep(0.2)
            assert not _pid_alive(pid)
        finally:
            if not task.done():
                task.cancel()

    @pytest.mark.asyncio
    async def test_concurrent_proc_cleanup_instance_calls(self):
        """Two concurrent proc ``cleanup_instance`` on same instance: no error."""
        proc_pkg = importlib.import_module("daemon.tools.proc_tools")
        manager = proc_pkg.get_background_process_manager()

        instance_id = f"par-proc-{os.urandom(4).hex()}"
        process_id, err = await manager.start_process(
            instance_id=instance_id,
            command="sleep 30",
            workdir=None,
            timeout_seconds=0,
        )
        assert err is None
        info = manager._processes[instance_id][process_id]
        pid = info.proc.pid
        _register_pid(pid, "par-proc", instance_id)

        results = await asyncio.gather(
            manager.cleanup_instance(instance_id),
            manager.cleanup_instance(instance_id),
            return_exceptions=True,
        )

        for i, r in enumerate(results):
            assert not isinstance(r, BaseException), (
                f"Concurrent proc cleanup_instance #{i} raised: {r}"
            )

        assert instance_id not in manager._processes
        await asyncio.sleep(0.2)
        assert not _pid_alive(pid)

    @pytest.mark.asyncio
    async def test_concurrent_proc_and_bash_cleanup_instance(self):
        """Proc + bash cleanup_instance concurrent: each takes its own registry."""
        bash_mod = importlib.import_module("daemon.tools.bash")
        proc_tools_mod = importlib.import_module("daemon.tools.proc_tools")
        bash = bash_mod.bash.coroutine
        bash_reg = bash_mod.get_bash_process_registry()
        proc_mgr = proc_tools_mod.get_background_process_manager()

        # Use the same instance_id for both registries to stress
        # cross-registry concurrency.
        instance_id = f"par-cross-{os.urandom(4).hex()}"

        # Spawn one bash + one proc.
        bash_task = asyncio.create_task(
            bash(
                command="sleep 30",
                instance_id=instance_id,
                timeout=60,
            )
        )
        proc_pid_info, err = await proc_mgr.start_process(
            instance_id=instance_id,
            command="sleep 30",
            workdir=None,
            timeout_seconds=0,
        )
        assert err is None
        proc_pid = proc_mgr._processes[instance_id][proc_pid_info].proc.pid
        _register_pid(proc_pid, "par-cross-proc", instance_id)

        try:
            await asyncio.sleep(0.5)
            bash_entries = bash_reg._entries.get(instance_id)
            assert bash_entries, "Bash entry should exist after spawn"
            bash_pid = bash_entries[0].pid
            _register_pid(bash_pid, "par-cross-bash", instance_id)

            # Concurrent cleanup of both registries on the same instance.
            results = await asyncio.gather(
                bash_reg.cleanup_instance(instance_id),
                proc_mgr.cleanup_instance(instance_id),
                return_exceptions=True,
            )
            for i, r in enumerate(results):
                assert not isinstance(r, BaseException), (
                    f"Concurrent cross-registry cleanup #{i} raised: {r}"
                )

            assert instance_id not in bash_reg._entries
            assert instance_id not in proc_mgr._processes
            await asyncio.sleep(0.2)
            assert not _pid_alive(bash_pid)
            assert not _pid_alive(proc_pid)
        finally:
            if not bash_task.done():
                bash_task.cancel()


# =============================================================================
# M1 regression — terminate_instance must drain the bash registry
# =============================================================================
#
# Mirror of the proc-cleanup block in instance_lifecycle.py. We assert that
# after M1, ``InstanceLifecycleService.terminate_instance`` calls
# ``BashProcessRegistry.cleanup_instance(instance_id)`` directly (not only
# via ``manager.shutdown()`` or the post-commit dispatcher). Without this, a
# TERMINATED instance would leak its bash-spawned process groups until root
# finalization or daemon shutdown.
#
# Implementation strategy:
#   1. Real in-memory SQLite engine (same shape as test_instance_hard_delete).
#   2. Seed one Instance row so ``_instance_repository.get`` returns it.
#   3. Mock every manager-level side-effect so the function exits cleanly.
#   4. Patch the two singleton accessors with AsyncMocks, observe calls.


@pytest.fixture
def _m1_engine():
    """Self-contained SQLite engine for the M1 regression test.

    Mirrors ``tests/test_instance_hard_delete.engine``. ``StaticPool`` so the
    engine survives across the ``asyncio.to_thread`` worker that
    ``_terminate_instance_db_sync`` may use.
    """
    from sqlalchemy import create_engine, event
    from sqlalchemy.pool import StaticPool
    from sqlmodel import SQLModel

    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(eng, "connect")
    def _enable_fk(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.mark.asyncio
async def test_terminate_instance_cleans_bash_registry(
    monkeypatch, _m1_engine
):
    """M1: ``InstanceLifecycleService.terminate_instance`` cleans both registries.

    Verifies the new ``bash cleanup_instance`` block introduced at
    ``daemon/services/instance_lifecycle.py:1469`` runs in the same
    function that cleans up the proc registry. Asserts:

    * ``proc cleanup_instance(instance_id)`` was awaited (preexisting).
    * ``bash cleanup_instance(instance_id)`` was awaited (M1).
    """
    instance_id = "m1-terminate-cleans-bash"

    # 1. Patch the two registry accessors with AsyncMocks so we can
    # observe the call without spawning anything.
    proc_mgr = AsyncMock(name="BackgroundProcessManager")
    bash_reg = AsyncMock(name="BashProcessRegistry")
    proc_mgr.cleanup_instance.return_value = 0
    bash_reg.cleanup_instance.return_value = 0
    _patch_registries(monkeypatch, proc_mgr=proc_mgr, bash_reg=bash_reg)

    # 2. Seed one Instance row with status != TERMINATED so the
    # ``_instance_repository.get`` early-return at line 1342 is not
    # taken.
    from datetime import datetime, timezone

    from daemon.repositories.instance.models import Instance
    from sqlmodel import Session

    now = datetime.now(timezone.utc).isoformat()
    with Session(_m1_engine) as s:
        s.add(
            Instance(
                instance_id=instance_id,
                agent_id="developer",
                agent_dir="/tmp/agents/developer",
                agent_name="developer",
                parent_id=None,
                status="running",
                version=1,
                created_at=now,
                updated_at=now,
            )
        )
        s.commit()

    # 3. Build a mock manager with every attribute ``terminate_instance``
    # touches during the pre-DB cleanup section. Heavy mocking by design
    # — we are not exercising the DB cascade, only the in-memory cleanup.
    from daemon.services.instance_lifecycle import InstanceLifecycleService

    fake_meta = MagicMock()
    fake_meta.status = "running"
    fake_meta.parent_id = None
    fake_meta.agent_id = "developer"

    manager = MagicMock()
    manager.engine = _m1_engine
    manager.write_guard = MagicMock()
    manager._instance_repository = MagicMock()
    manager._instance_repository.get = MagicMock(return_value=fake_meta)
    manager._request_registry.cancel_by_instance = MagicMock()
    manager.clear_injection = MagicMock(return_value=None)
    manager._live_hub = MagicMock()
    manager._live_hub.cleanup_instance = AsyncMock()
    manager._live_hub.stream_status_change = AsyncMock()
    manager._mcp_service = None
    manager.instances = {instance_id: object()}
    manager._todo_manager = None
    manager._watcher_repo = MagicMock()
    manager._watcher_repo.remove_all_watches_for_instance = MagicMock(
        return_value=0,
    )
    manager._queue_repository = MagicMock()
    manager._queue_repository.delete_by_instance = MagicMock(return_value=0)
    manager._job_queue_mgmt_service = MagicMock()
    manager.events_service = MagicMock()

    svc = InstanceLifecycleService(
        manager=manager,
        cancellation_service=MagicMock(),
        job_queue_service=MagicMock(
            _repository=MagicMock(
                find_jobs_by_instance=MagicMock(return_value=[])
            ),
            cancel_job=AsyncMock(return_value=True),
            complete_job=AsyncMock(return_value=None),
            release_lock_by_instance=AsyncMock(return_value=[]),
            trigger_next_job_sync=MagicMock(),
            get_job_by_instance_sync=MagicMock(return_value=None),
        ),
    )

    # 4. Stub the DB cascade so we don't have to seed watcher/event/queue
    # rows. The post-commit side effects are not in scope here.
    from collections import namedtuple

    _TerminateResult = namedtuple(
        "_TerminateResult",
        [
            "skip",
            "parent_id",
            "agent_id",
            "message_jobs_cancelled",
            "all_jobs_cancelled",
            "message_queue_removed",
            "tasks_removed",
        ],
    )
    svc._terminate_instance_db_sync = MagicMock(  # type: ignore[method-assign]
        return_value=_TerminateResult(
            skip=False,
            parent_id=None,
            agent_id="developer",
            message_jobs_cancelled=0,
            all_jobs_cancelled=0,
            message_queue_removed=0,
            tasks_removed=0,
        ),
    )

    # 5. Run it.
    result = await svc.terminate_instance(instance_id)
    assert result is True

    # 6. Assert both registries were called with the right instance_id.
    proc_calls = [
        call.args[0]
        for call in proc_mgr.cleanup_instance.await_args_list
    ]
    bash_calls = [
        call.args[0]
        for call in bash_reg.cleanup_instance.await_args_list
    ]
    assert instance_id in proc_calls, (
        f"proc cleanup_instance({instance_id!r}) not awaited. "
        f"Calls observed: {proc_calls}"
    )
    assert instance_id in bash_calls, (
        f"M1 regression: bash cleanup_instance({instance_id!r}) not "
        f"awaited during terminate_instance. Calls observed: {bash_calls}."
    )


# =============================================================================
# m8 characterization — setsid grandchild survives killpg (known limitation)
# =============================================================================
#
# D4 / approver note documents that ``killpg`` cannot reach a grandchild
# which detached itself by calling ``setsid()`` — it sits in its own process
# group as a new session leader. This test pins that boundary so a future
# claim of "we fixed this" fails here and forces a re-evaluation.


@pytest.mark.skipif(sys.platform == "win32", reason="setsid is Unix-only")
class TestSetsidOrphanSurvivesCleanup:
    """A grandchild that calls ``setsid()`` detaches into its own process
    group — ``killpg`` on the bash tool's PGID cannot reach it.

    Characterization test: if a future PR claims to fix this, this test
    will fail and force a re-evaluation of the bash kill model.
    """

    @pytest.mark.asyncio
    async def test_setsid_orphan_survives_killpg(self, monkeypatch):
        """Spawn a setsid grandchild, run cleanup, assert it survived.

        The grandchild ``setsid bash -c 'echo $BASHPID > gc.pid; sleep 5'``
        becomes a new session leader in its own process group. The bash
        tool (running start_new_session=True) sits in a different PGID.
        ``BashProcessRegistry.cleanup_instance`` calls
        ``os.killpg(bash_shell_pgid, SIGKILL)`` which DOES NOT reach the
        grandchild.

        Critical: the grandchild MUST be SIGKILLed in teardown regardless
        of which assertion path the test takes.
        """
        bash_mod = importlib.import_module("daemon.tools.bash")
        bash = bash_mod.bash.coroutine  # type: ignore[attr-defined]
        bash_reg = bash_mod.get_bash_process_registry()

        instance_id = f"m8-setsid-{os.urandom(4).hex()}"

        gc_pid_fd, gc_pid_path = tempfile.mkstemp(
            prefix="m8-setsid-gc-", suffix=".pid"
        )
        os.close(gc_pid_fd)

        orphan_pid: int | None = None
        bash_task: asyncio.Task | None = None

        try:
            bash_task = asyncio.create_task(
                bash(
                    # Use a Python fork+os.setsid() to detach into a NEW
                    # session/process group, then exec sleep. ``setsid``
                    # from coreutils is not guaranteed on every Unix
                    # (macOS lacks it without brew), but Python's
                    # ``os.setsid`` is POSIX-standard and works
                    # everywhere.
                    command=textwrap.dedent(
                        f"""\
                        python3 -c '
                        import os
                        pid = os.fork()
                        if pid == 0:
                            os.setsid()
                            with open("{gc_pid_path}", "w") as f:
                                f.write(str(os.getpid()))
                            os.execlp("sleep", "sleep", "5")
                        else:
                            os.waitpid(pid, 0)
                        '
                        """
                    ),
                    instance_id=instance_id,
                    timeout=30,
                )
            )

            gc_alive = False
            for _ in range(80):  # 80 * 50ms = 4s
                if os.path.exists(gc_pid_path):
                    try:
                        with open(gc_pid_path, "r") as f:
                            orphan_pid = int(f.read().strip())
                            if orphan_pid > 0:
                                gc_alive = True
                                break
                    except (OSError, ValueError):
                        pass
                await asyncio.sleep(0.05)

            assert gc_alive and orphan_pid is not None, (
                "Test setup failure: setsid grandchild pid not captured "
                "in time."
            )
            assert _pid_alive(orphan_pid), (
                "Test setup failure: setsid grandchild died before "
                "cleanup could be tested."
            )
            _register_pid(orphan_pid, "m8-setsid-grandchild", instance_id)

            assert instance_id in bash_reg._entries, (
                "Test setup failure: bash registry has no entry for "
                "instance_id."
            )

            killed = await bash_reg.cleanup_instance(instance_id)
            assert killed >= 1, (
                "Bash cleanup should have killed at least the bash "
                "shell's process group."
            )

            await asyncio.sleep(0.5)
            assert _pid_alive(orphan_pid), (
                "Characterization FAIL: setsid grandchild was killed by "
                "``os.killpg`` on the bash tool's PGID. This contradicts "
                "the documented D4 limitation."
            )

            try:
                await asyncio.wait_for(bash_task, timeout=10)
            except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
                if not bash_task.done():
                    bash_task.cancel()
        finally:
            if orphan_pid is not None and _pid_alive(orphan_pid):
                try:
                    os.kill(orphan_pid, signal.SIGKILL)
                except (ProcessLookupError, OSError):
                    pass
            if bash_task is not None and not bash_task.done():
                bash_task.cancel()
                try:
                    await asyncio.wait_for(bash_task, timeout=2)
                except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
                    pass
            if os.path.exists(gc_pid_path):
                try:
                    os.unlink(gc_pid_path)
                except OSError:
                    pass