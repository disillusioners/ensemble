"""Integration tests for the lifecycle hook dispatch in the completion path.

Covers Phase 5 tasks 8–11 of the Instance Lifecycle Hooks plan:

* Task 8 — outcome-gating: hook fires only for ``regular_child_completed``
  with a non-empty ``lifecycle_hooks["on_complete"]`` config.
* Task 9 — W7: missing/None ``context_key`` falls back to ``instance_id``,
  and a missing ``instance_repository`` skips the dispatch silently.
* Task 10 — W2 + W3: a hanging hook times out (no block beyond timeout);
  ``asyncio.CancelledError`` propagates correctly.
* Task 11 — W8 #6: end-to-end heuristic injection.  A sibling instance can
  discover the first instance's report via the shared-context heuristic
  matcher.

These tests follow the same pattern as
``tests/unit/test_root_instance_completion.py``: build a
``ChildReportsService`` via ``__new__`` and patch the surface area
(:class:`_ChildCompletionDbResult`, manager, registry, instance repository,
bus hook helpers).
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from daemon.services.child_reports import (
    ChildReportsService,
    _ChildCompletionDbResult,
)
from daemon.services.context_injection import (
    MATCH_THRESHOLD,
    _score_context_files,
    get_shared_context,
)
from daemon.services.context_tools import list_context_files
from daemon.services.lifecycle_hooks import (
    LifecycleHookContext,
    _HOOK_REGISTRY,
    _add_to_shared_context_md_files,
    dispatch_lifecycle_hooks,
    register_lifecycle_hook,
)


# ─── Fixtures & helpers ──────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clear_hook_registry():
    """Snapshot and restore the module-level hook registry around each test.

    The registry is mutated in place by ``register_lifecycle_hook`` and
    :func:`dispatch_lifecycle_hooks` reads from it.  Without this fixture
    registrations from earlier tests would leak into later ones.

    The built-in ``add_to_shared_context_md_files`` hook is re-registered
    after the clear so integration tests that exercise the real hook
    function can find it.
    """
    saved = {k: dict(v) for k, v in _HOOK_REGISTRY.items()}
    _HOOK_REGISTRY.clear()
    # Re-register the built-in so real-hook tests have a working registry.
    register_lifecycle_hook(
        "on_complete",
        "add_to_shared_context_md_files",
        _add_to_shared_context_md_files,
    )
    try:
        yield
    finally:
        _HOOK_REGISTRY.clear()
        _HOOK_REGISTRY.update(saved)


def _make_service(mock_manager) -> ChildReportsService:
    """Build a bare ``ChildReportsService`` with patched dependencies.

    Mirrors the pattern in ``tests/unit/test_root_instance_completion.py``:
    avoid running ``__init__`` (which expects a real ``InstanceManager``)
    by going through ``__new__`` and then wiring the few attributes that
    ``_dispatch_post_commit_side_effects`` reads.
    """
    service = ChildReportsService.__new__(ChildReportsService)
    service._manager = mock_manager
    # Provide no events service so the lifecycle-event try/except is a no-op.
    service._events_service = None
    # Stub the post-commit helpers to no-ops.  We assert against the bus
    # helper in the relevant tests, but most of these are not what we are
    # verifying here.
    service._trigger_title_generation = MagicMock()
    return service


def _make_db_result(
    outcome: str,
    *,
    instance_id: str = "child-001",
    agent_id: str = "wanderer",
    parent_id: str | None = "parent-001",
    child_agent_id: str | None = "wanderer",
) -> _ChildCompletionDbResult:
    """Construct a ``_ChildCompletionDbResult`` with sensible defaults."""
    return _ChildCompletionDbResult(
        outcome=outcome,
        instance_id=instance_id,
        agent_id=agent_id,
        parent_id=parent_id,
        child_agent_id=child_agent_id,
    )


def _make_mock_manager(
    instance_repo: MagicMock | None = None,
) -> MagicMock:
    """Build a manager mock with the minimal surface the hook path needs."""
    manager = MagicMock()
    manager._instance_repository = instance_repo
    manager._live_hub = None
    manager._worker_pool = None
    manager._task_repo = None
    return manager


# ─── Task 8: outcome-gating integration tests ────────────────────────────────


class TestOutcomeGating:
    """W6 + C1: hook dispatch fires only inside the
    ``regular_child_completed`` branch."""

    @pytest.mark.asyncio
    async def test_hook_dispatched_for_regular_child_completed(self):
        """Regular child with non-empty config → hook fires."""
        hook = AsyncMock()
        register_lifecycle_hook("on_complete", "spy_hook", hook)

        agent_meta = MagicMock(lifecycle_hooks={"on_complete": ["spy_hook"]})
        registry = MagicMock()
        registry.get_version.return_value = agent_meta
        registry.get_resolved.return_value = agent_meta

        instance_repo = MagicMock()
        instance_repo.get_tree_root_id.return_value = "root-x"
        manager = _make_mock_manager(instance_repo)

        # Stub out the bus hooks to no-ops (we only care about the
        # lifecycle hook path).
        service = _make_service(manager)
        service._emit_terminal_via_bus = AsyncMock()
        service._emit_terminal_for_child_instance_via_bus = AsyncMock()

        result = _make_db_result("regular_child_completed")
        with patch("daemon.services.child_reports.get_registry", return_value=registry):
            await service._dispatch_post_commit_side_effects(
                result, last_content="# Heading\n\nbody", completed_message_id="msg-1"
            )

        hook.assert_awaited_once()
        ctx = hook.await_args.args[0]
        assert isinstance(ctx, LifecycleHookContext)
        assert ctx.instance_id == "child-001"
        assert ctx.context_key == "root-x"

    @pytest.mark.asyncio
    async def test_no_dispatch_when_lifecycle_hooks_empty(self):
        """An agent without ``lifecycle_hooks`` is a no-op."""
        hook = AsyncMock()
        register_lifecycle_hook("on_complete", "spy_hook", hook)

        agent_meta = MagicMock(lifecycle_hooks={})
        registry = MagicMock()
        registry.get_version.return_value = agent_meta

        manager = _make_mock_manager(MagicMock())
        service = _make_service(manager)
        service._emit_terminal_via_bus = AsyncMock()
        service._emit_terminal_for_child_instance_via_bus = AsyncMock()

        result = _make_db_result("regular_child_completed")
        with patch("daemon.services.child_reports.get_registry", return_value=registry):
            await service._dispatch_post_commit_side_effects(
                result, last_content="body", completed_message_id="msg-1"
            )
        hook.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_dispatch_for_root_completed(self):
        """W6: root completion branch returns BEFORE the hook site."""
        hook = AsyncMock()
        register_lifecycle_hook("on_complete", "spy_hook", hook)

        agent_meta = MagicMock(lifecycle_hooks={"on_complete": ["spy_hook"]})
        registry = MagicMock()
        registry.get_version.return_value = agent_meta

        manager = _make_mock_manager(MagicMock())
        service = _make_service(manager)
        service._emit_terminal_via_bus = AsyncMock()
        service._emit_terminal_for_child_instance_via_bus = AsyncMock()

        result = _make_db_result("root_completed")
        with patch("daemon.services.child_reports.get_registry", return_value=registry):
            await service._dispatch_post_commit_side_effects(
                result, last_content="body", completed_message_id="msg-1"
            )
        hook.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_dispatch_for_tool_invocation_completed(self):
        """W6: tool-invocation branch returns BEFORE the hook site."""
        hook = AsyncMock()
        register_lifecycle_hook("on_complete", "spy_hook", hook)

        agent_meta = MagicMock(lifecycle_hooks={"on_complete": ["spy_hook"]})
        registry = MagicMock()
        registry.get_version.return_value = agent_meta

        manager = _make_mock_manager(MagicMock())
        service = _make_service(manager)
        service._emit_terminal_via_bus = AsyncMock()
        service._emit_terminal_for_child_instance_via_bus = AsyncMock()

        result = _make_db_result("tool_invocation_completed")
        with patch("daemon.services.child_reports.get_registry", return_value=registry):
            await service._dispatch_post_commit_side_effects(
                result, last_content="body", completed_message_id="msg-1"
            )
        hook.assert_not_called()

    @pytest.mark.asyncio
    async def test_hook_exception_does_not_block_bus_terminal(self):
        """W8 #7: a failing hook must not prevent the bus terminal hook
        from having fired.  We mock the bus helpers and assert they were
        awaited even when the lifecycle hook raises."""
        bus_emit = AsyncMock()
        corrective_emit = AsyncMock()

        async def failing_hook(ctx):
            raise RuntimeError("boom")

        register_lifecycle_hook("on_complete", "failing", failing_hook)

        agent_meta = MagicMock(lifecycle_hooks={"on_complete": ["failing"]})
        registry = MagicMock()
        registry.get_version.return_value = agent_meta

        manager = _make_mock_manager(MagicMock())
        service = _make_service(manager)
        service._emit_terminal_via_bus = bus_emit
        service._emit_terminal_for_child_instance_via_bus = corrective_emit

        result = _make_db_result("regular_child_completed")
        with patch("daemon.services.child_reports.get_registry", return_value=registry):
            # Must NOT propagate.
            await service._dispatch_post_commit_side_effects(
                result, last_content="body", completed_message_id="msg-1"
            )

        # Both bus helpers were awaited before the hook fired.
        bus_emit.assert_awaited_once()
        corrective_emit.assert_awaited_once()


# ─── Task 9: W7 — context_key resolution / fallback policy ───────────────────


class TestContextKeyFallback:
    """W7: ``context_key`` resolution with three branches."""

    @pytest.mark.asyncio
    async def test_tree_root_id_is_used(self, tmp_path, monkeypatch):
        """``_resolve_tree_root_id`` returns the tree-root id → file lands
        under that directory."""
        monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))
        instance_repo = MagicMock()
        instance_repo.get_tree_root_id.return_value = "root-abc"

        manager = _make_mock_manager(instance_repo)
        service = _make_service(manager)
        service._emit_terminal_via_bus = AsyncMock()
        service._emit_terminal_for_child_instance_via_bus = AsyncMock()

        agent_meta = MagicMock(
            lifecycle_hooks={"on_complete": ["add_to_shared_context_md_files"]}
        )
        registry = MagicMock()
        registry.get_version.return_value = agent_meta

        result = _make_db_result(
            "regular_child_completed",
            instance_id="child-001",
            parent_id="parent-001",
        )
        with patch("daemon.services.child_reports.get_registry", return_value=registry):
            await service._dispatch_post_commit_side_effects(
                result, last_content="# Heading\n\nbody", completed_message_id="msg"
            )

        root_dir = tmp_path / "ensemble" / "context" / "root-abc"
        assert root_dir.is_dir(), f"expected dir {root_dir}"
        assert any(root_dir.glob("*.md"))

    @pytest.mark.asyncio
    async def test_resolve_returns_none_falls_back_to_parent_id(
        self, tmp_path, monkeypatch
    ):
        """When the repository returns ``None`` for ``get_tree_root_id``,
        the helper falls back to ``parent_id`` (W7 spirit: a stable
        id, never ``None``).  The file lands under the parent_id dir.
        """
        monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))

        instance_repo = MagicMock()
        instance_repo.get_tree_root_id.return_value = None  # None → fallback

        manager = _make_mock_manager(instance_repo)
        service = _make_service(manager)
        service._emit_terminal_via_bus = AsyncMock()
        service._emit_terminal_for_child_instance_via_bus = AsyncMock()

        agent_meta = MagicMock(
            lifecycle_hooks={"on_complete": ["add_to_shared_context_md_files"]}
        )
        registry = MagicMock()
        registry.get_version.return_value = agent_meta

        result = _make_db_result(
            "regular_child_completed", instance_id="child-001"
        )
        with patch("daemon.services.child_reports.get_registry", return_value=registry):
            await service._dispatch_post_commit_side_effects(
                result,
                last_content="# Heading\n\nbody",
                completed_message_id="msg",
            )

        # Helper's contract: None → parent_id. So the file lands under
        # the parent_id dir.
        parent_dir = tmp_path / "ensemble" / "context" / "parent-001"
        assert parent_dir.is_dir(), f"expected dir {parent_dir}"
        assert any(parent_dir.glob("*.md"))

    @pytest.mark.asyncio
    async def test_no_instance_repository_skips_dispatch_silently(
        self, caplog
    ):
        """Manager lacks ``_instance_repository`` → DEBUG log, no file."""
        manager = _make_mock_manager(instance_repo=None)

        agent_meta = MagicMock(
            lifecycle_hooks={"on_complete": ["add_to_shared_context_md_files"]}
        )
        registry = MagicMock()
        registry.get_version.return_value = agent_meta

        service = _make_service(manager)
        service._emit_terminal_via_bus = AsyncMock()
        service._emit_terminal_for_child_instance_via_bus = AsyncMock()

        result = _make_db_result("regular_child_completed")

        with patch("daemon.services.child_reports.get_registry", return_value=registry):
            with caplog.at_level(
                logging.DEBUG, logger="daemon.services.child_reports"
            ):
                # Must not raise.
                await service._dispatch_post_commit_side_effects(
                    result, last_content="body", completed_message_id="msg"
                )

        # DEBUG log mentions the skip.
        assert any(
            "context_key unavailable" in r.message for r in caplog.records
        )


# ─── Task 10: W2 + W3 — timeout + cancellation behavior ──────────────────────


class TestTimeoutAndCancellation:
    """W2 + W3: ``asyncio.wait_for`` + ``except asyncio.CancelledError: raise``."""

    @pytest.mark.asyncio
    async def test_hanging_hook_times_out_at_short_deadline(self):
        """W2: a hook that sleeps longer than the deadline is canceled by
        ``asyncio.wait_for``.  We use 0.1s here (production is 5s) to keep
        the test fast.
        """
        async def hanging(ctx):
            await asyncio.sleep(10)
            return None

        register_lifecycle_hook("on_complete", "hang", hanging)

        ctx = LifecycleHookContext(
            instance_id="child-1",
            agent_id="a",
            parent_id=None,
            last_content="body",
            outcome="regular_child_completed",
            context_key="ctx-1",
            manager=MagicMock(),
        )

        start = time.monotonic()
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(
                dispatch_lifecycle_hooks("on_complete", ["hang"], ctx),
                timeout=0.1,
            )
        elapsed = time.monotonic() - start
        # Way below the 10s sleep.
        assert elapsed < 1.0, f"expected fast timeout, took {elapsed:.2f}s"

    @pytest.mark.asyncio
    async def test_cancelled_error_propagates_through_dispatcher(self):
        """W3: ``asyncio.CancelledError`` is re-raised, not swallowed."""
        async def cancel_hook(ctx):
            raise asyncio.CancelledError()

        register_lifecycle_hook("on_complete", "cancel", cancel_hook)

        ctx = LifecycleHookContext(
            instance_id="child-1",
            agent_id="a",
            parent_id=None,
            last_content="body",
            outcome="regular_child_completed",
            context_key="ctx-1",
            manager=MagicMock(),
        )

        with pytest.raises(asyncio.CancelledError):
            await dispatch_lifecycle_hooks("on_complete", ["cancel"], ctx)

    @pytest.mark.asyncio
    async def test_post_commit_chain_not_blocked_by_slow_hook(self):
        """W8 #5: a slow hook does not block the post-commit chain past the
        timeout.  We register a hook that does a real ``asyncio.sleep`` for
        longer than the (test) deadline; the production wrapper catches the
        ``TimeoutError`` and the bus terminal hook still fires.
        """
        async def slow_hook(ctx):
            await asyncio.sleep(1.0)

        register_lifecycle_hook("on_complete", "slow", slow_hook)

        agent_meta = MagicMock(lifecycle_hooks={"on_complete": ["slow"]})
        registry = MagicMock()
        registry.get_version.return_value = agent_meta

        manager = _make_mock_manager(MagicMock())
        bus_emit = AsyncMock()
        corrective_emit = AsyncMock()
        service = _make_service(manager)
        service._emit_terminal_via_bus = bus_emit
        service._emit_terminal_for_child_instance_via_bus = corrective_emit

        result = _make_db_result("regular_child_completed")

        # Override the production ``asyncio.wait_for`` deadline to 0.1s
        # so the test does not block for the full 5s.  We replace the
        # symbol ``child_reports.asyncio.wait_for`` with a real coroutine
        # function (not a side_effect lambda) so Python's awaited
        # coroutine bookkeeping is correct.
        original_wait_for = asyncio.wait_for

        async def short_wait_for(coro, timeout=None):
            return await original_wait_for(coro, timeout=0.1)

        with patch("daemon.services.child_reports.get_registry", return_value=registry):
            with patch(
                "daemon.services.child_reports.asyncio.wait_for",
                side_effect=short_wait_for,
            ):
                start = time.monotonic()
                # Must not raise.
                await service._dispatch_post_commit_side_effects(
                    result, last_content="body", completed_message_id="msg"
                )
                elapsed = time.monotonic() - start

        # The bus hooks still fired before the slow hook was timed out.
        bus_emit.assert_awaited_once()
        corrective_emit.assert_awaited_once()
        # 5s production timeout is well above 0.1s; with our override the
        # whole chain finishes well under 2s.
        assert elapsed < 2.0, f"expected fast return, took {elapsed:.2f}s"


# ─── Task 11: W8 #6 — End-to-end heuristic injection ────────────────────────


class TestHeuristicInjectionEndToEnd:
    """W8 #6: complete loop — child completion → file write → sibling
    discovery via the heuristic matcher."""

    @pytest.mark.asyncio
    async def test_sibling_discovers_sibling_report_via_heuristic(
        self, tmp_path, monkeypatch
    ):
        """Instance A completes with a report whose heading is
        ``# Distributed Consensus Algorithms``.  When the heuristic matcher
        is asked for files matching ``consensus`` under the shared
        ``context_key``, A's report must appear with score
        >= :data:`MATCH_THRESHOLD`."""
        # Redirect tempdir so we don't pollute the user's real one.
        monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))

        shared_key = "tree-root-xyz"
        ctx = LifecycleHookContext(
            instance_id="a-instance-1",
            agent_id="wanderer",
            parent_id=shared_key,
            last_content=(
                "# Distributed Consensus Algorithms\n\n"
                "body about consensus algorithms for distributed systems."
            ),
            outcome="regular_child_completed",
            context_key=shared_key,
            manager=MagicMock(),
        )

        await _add_to_shared_context_md_files(ctx)

        # 1. The file is visible in the shared context dir.
        shared_dir = tmp_path / "ensemble" / "context" / shared_key
        assert shared_dir.is_dir()
        files = list(shared_dir.glob("*.md"))
        assert files, "expected at least one .md under shared_key"

        # 2. The file lists under ``list_context_files`` for the same key.
        listed = list_context_files(shared_key)
        assert any(
            entry["filename"] == files[0].name for entry in listed
        ), f"file not listed; got: {[e['filename'] for e in listed]}"

        # 3. The heuristic matcher scores it above threshold for a
        # query that mentions "consensus".
        scored = _score_context_files("consensus", shared_dir)
        assert scored, "scorer returned empty"
        top_score, top_path = scored[0]
        assert top_path.name == files[0].name
        assert top_score >= MATCH_THRESHOLD, (
            f"top score {top_score} below threshold {MATCH_THRESHOLD}"
        )

    @pytest.mark.asyncio
    async def test_get_shared_context_includes_sibling_report(
        self, tmp_path, monkeypatch
    ):
        """The same file, when fed through the real ``get_shared_context``
        injection builder, must end up in the returned text for a query
        that mentions a topic keyword from the report's heading.
        """
        monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))

        shared_key = "tree-root-xyz-2"
        ctx = LifecycleHookContext(
            instance_id="a-instance-2",
            agent_id="wanderer",
            parent_id=shared_key,
            last_content=(
                "# Distributed Consensus Algorithms\n\n"
                "deep dive into Raft and Paxos consensus algorithms."
            ),
            outcome="regular_child_completed",
            context_key=shared_key,
            manager=MagicMock(),
        )
        await _add_to_shared_context_md_files(ctx)

        # Query mentions the topic keyword from the heading.
        text = get_shared_context(
            context_key=shared_key,
            query="consensus",
            audience="internal",
        )
        assert text, "expected non-empty injection text"
        # The injected text must reference the heading we wrote.
        assert "Consensus" in text or "consensus" in text.lower()
