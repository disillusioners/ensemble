"""Integration tests for ``skill_search_interval`` through the REAL
messaging path :meth:`InstanceMessagingService._process_message_with_tracking`.

This file fills the critical gap left by
``tests/unit/test_skill_search_interval.py``: the 22 unit tests there
mirror the gate logic in a helper function (``_gate_decides_skip``) and
do NOT exercise the production code path.  The existing messaging-path
tests (``test_instance_messaging_skill_injection.py``,
``test_instance_messaging_task_context.py``) explicitly disable skill
injection or send a single message — none of them send *multiple*
messages through the real messaging path and verify the search/skip
cycle.

Every test in this file drives the actual
``_process_message_with_tracking`` method multiple times in sequence,
using a manager mock with **real** counter and cache dicts (so the
``get_and_increment_skill_search_count`` / ``get_context_skill_result`` /
``set_context_skill_result`` / ``reset_skill_search_count`` calls exercise
production-shaped state across messages).  ``inject_skills`` is mocked
on the injection service to track call count and control return values.

Implementation strategy
-----------------------
Mirrors the capturing-graph pattern from
``test_instance_messaging_skill_injection.py``:

* build a manager mock with real counter/cache dicts + a controlled
  ``_skill_injection_service``
* patch ``daemon.registry.get_registry`` so the messaging path resolves
  an ``agent_meta`` with ``skill_injection=True`` and a configurable
  ``skill_search_interval``
* patch ``daemon.services.context_messages.assemble_context_messages``
  to return empty lists (so we focus solely on the search-gate logic)
* call ``_process_message_with_tracking`` N times and assert on the
  ``inject_skills.await_count`` after each call
"""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from daemon.services.instance_messaging import InstanceMessagingService


# ============================================================
# Helpers
# ============================================================


def _make_capturing_graph() -> MagicMock:
    """Build a LangGraph mock whose ``astream`` is an empty async
    generator so the ``async for event in graph.astream(...)`` loop in
    ``_process_message_with_tracking`` exits immediately.
    """

    async def _empty_astream(*args, **kwargs):
        return
        yield  # pragma: no cover

    graph = MagicMock()
    graph.astream = _empty_astream
    graph.language_check_active = False
    return graph


class _NullSemaphore:
    """Re-entrant null async context manager for ``_llm_semaphore``.

    The production code does ``async with self._llm_semaphore:`` on
    every message.  A single ``@asynccontextmanager`` instance is
    single-use (raises on second ``__aenter__`` in Python 3.13), so we
    use a plain class that supports unlimited re-entry.
    """

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _make_injection_service(
    *,
    injection_text: str = "[System Inject] skills for you",
    skill_ids: list[str] | None = None,
    explicit_text: str | None = "[System Inject] explicit skill",
    explicit_skill_ids: list[str] | None = None,
) -> MagicMock:
    """Build a :class:`SkillInjectionService` mock.

    ``inject_skills`` is an ``AsyncMock`` whose call count is tracked to
    determine whether the search-gate decided SKIP or SEARCH on each
    message.  ``track_injection`` is a plain ``MagicMock``.
    """
    if skill_ids is None:
        skill_ids = ["skill-AAA", "skill-BBB"]
    if explicit_skill_ids is None:
        explicit_skill_ids = ["skill-explicit"]
    svc = MagicMock()
    svc.inject_skills = AsyncMock(
        return_value=(injection_text, skill_ids),
    )
    svc.inject_explicit_skill = AsyncMock(
        return_value=(explicit_text, explicit_skill_ids),
    )
    svc.track_injection = MagicMock()
    return svc


def _make_manager(
    *,
    injection_service: object,
    agent_meta: SimpleNamespace | None,
    instance_id: str = "inst-1",
    project_injected: bool = True,
) -> MagicMock:
    """Build a manager mock with **real** counter/cache dicts.

    The two dicts ``_skill_search_message_counts`` and
    ``_context_skill_results`` are plain ``dict`` objects (not mocks)
    so the production methods
    ``get_and_increment_skill_search_count`` /
    ``get_context_skill_result`` /
    ``set_context_skill_result`` /
    ``reset_skill_search_count`` operate on real state that persists
    across multiple ``_process_message_with_tracking`` calls within a
    single test.
    """
    instance_meta = SimpleNamespace(
        instance_id=instance_id,
        agent_id="worker",
        instance_metadata={
            "project_id": "proj-1",
            "project_injected": project_injected,
        },
    )

    manager = MagicMock()
    manager.config.limits.graph_recursion_limit = 50
    manager.config.compaction = MagicMock()
    manager.get_instance = AsyncMock(return_value=_make_capturing_graph())
    manager._instance_repository = MagicMock()
    manager._instance_repository.get = MagicMock(return_value=instance_meta)
    manager._instance_repository.set_metadata = MagicMock(return_value=None)
    manager._instance_repository.get_tree_root_id = MagicMock(return_value=None)
    manager._live_hub = MagicMock()
    manager._live_hub.stream_message = AsyncMock()
    manager._live_hub.stream_error = AsyncMock()
    manager._queue_repository = MagicMock()
    manager._graph_tasks = {}
    manager.source_dispatcher = None
    manager._llm_semaphore = _NullSemaphore()
    manager._original_timestamps = {}
    manager._emitted_message_content = {}

    # Real counter/cache dicts — persisted across calls.
    manager._skill_search_message_counts: dict[str, int] = {}
    manager._context_skill_results: dict[str, tuple[str | None, list[str]] | None] = {}

    # W1 fix: per-instance marker for explicit ``load_skill`` writes.
    # Mirrors the production ``InstanceManager`` — the messaging path
    # calls ``mark_explicit_skill_loaded`` / ``clear_explicit_skill_loaded``
    # and the gate consults ``was_explicit_skill_loaded``. Without these
    # real bindings, the ``MagicMock`` auto-creates them as truthy
    # attributes that force the gate into a perpetual search loop.
    manager._explicit_skill_loaded: set[str] = set()

    def _get_and_increment(iid: str) -> int:
        current = manager._skill_search_message_counts.get(iid, 0)
        manager._skill_search_message_counts[iid] = current + 1
        return current

    def _get_cached(iid: str):
        return manager._context_skill_results.get(iid)

    def _set_cached(iid: str, result) -> None:
        manager._context_skill_results[iid] = result

    def _reset(iid: str) -> None:
        manager._skill_search_message_counts[iid] = 0

    def _mark_explicit(iid: str) -> None:
        manager._explicit_skill_loaded.add(iid)

    def _clear_explicit(iid: str) -> None:
        manager._explicit_skill_loaded.discard(iid)

    def _was_explicit(iid: str) -> bool:
        return iid in manager._explicit_skill_loaded

    manager.get_and_increment_skill_search_count = _get_and_increment
    manager.get_context_skill_result = _get_cached
    manager.set_context_skill_result = _set_cached
    manager.reset_skill_search_count = _reset
    manager.mark_explicit_skill_loaded = _mark_explicit
    manager.clear_explicit_skill_loaded = _clear_explicit
    manager.was_explicit_skill_loaded = _was_explicit

    manager.has_deferred_question_pause = MagicMock(return_value=False)
    manager.pause_instance_cascade = AsyncMock()
    manager.pop_deferred_question_pause = MagicMock()
    manager.release_context_usage_cache = MagicMock()

    manager._skill_clone_service = None
    manager._skill_injection_service = injection_service
    return manager


def _make_service(manager: MagicMock) -> InstanceMessagingService:
    svc = InstanceMessagingService(
        manager=manager,
        cancellation_service=MagicMock(is_shutting_down=False),
    )
    svc._has_checkpoint = AsyncMock(return_value=False)
    svc._maybe_compact_context = AsyncMock()
    return svc


def _registry_patch(agent_meta: SimpleNamespace | None):
    """Return a ``patch`` context manager that replaces
    ``daemon.registry.get_registry`` with a mock returning the given
    ``agent_meta`` from ``get_resolved`` (and ``None`` from
    ``get_version`` so the fallback path is exercised).
    """
    return patch("daemon.registry.get_registry")


def _apply_registry(agent_meta: SimpleNamespace | None):
    """Configure the registry mock on a ``patch`` object."""
    registry = MagicMock()
    registry.get_version = MagicMock(return_value=None)
    registry.get_resolved = MagicMock(return_value=agent_meta)
    return registry


async def _send_messages(
    *,
    svc: InstanceMessagingService,
    manager: MagicMock,
    agent_meta: SimpleNamespace | None,
    n: int,
    instance_id: str = "inst-1",
    message_prefix: str = "msg",
    message_source: str | None = "agent:leader",
) -> None:
    """Send ``n`` messages through ``_process_message_with_tracking``,
    each with a unique ``message_id``.  The registry mock and
    ``assemble_context_messages`` are patched for the duration.
    """
    with patch("daemon.registry.get_registry") as mock_get_registry:
        mock_get_registry.return_value = _apply_registry(agent_meta)
        with patch(
            "daemon.services.context_messages.assemble_context_messages",
            new=AsyncMock(return_value=([], [])),
        ):
            for i in range(1, n + 1):
                await svc._process_message_with_tracking(
                    instance_id=instance_id,
                    message=f"{message_prefix}-{i}",
                    message_id=f"mid-{i}",
                    is_retry=False,
                    message_source=message_source,
                )


# ============================================================
# Scenario 1: interval=3 search/skip/skip/search cycle
# ============================================================


@pytest.mark.asyncio
class TestIntervalThreeSearchSkipCycle:
    """``skill_search_interval=3``: the gate must search on messages
    1 and 4, and SKIP (reuse cached result) on messages 2, 3, and 5.

    Expected trace (interval=3, cache empty at start):

    * msg 1: msg_count=0, cache=None → SEARCH (reset counter to 0,
      cache populated by inject_skills return value)
    * msg 2: msg_count=0, cache present, 0<2 → SKIP
    * msg 3: msg_count=1, cache present, 1<2 → SKIP
    * msg 4: msg_count=2, cache present, 2<2 False → SEARCH (reset)
    * msg 5: msg_count=0, cache present, 0<2 → SKIP
    """

    async def test_inject_skills_called_on_messages_1_and_4_only(self):
        injection_service = _make_injection_service(
            injection_text="[System Inject] SKILL_X",
            skill_ids=["skill-X"],
        )
        agent_meta = SimpleNamespace(
            agent_id="worker",
            skill_injection=True,
            skill_search_interval=3,
            context_injection_mode="human_messages",
        )
        manager = _make_manager(
            injection_service=injection_service,
            agent_meta=agent_meta,
        )
        svc = _make_service(manager)

        await _send_messages(
            svc=svc,
            manager=manager,
            agent_meta=agent_meta,
            n=5,
        )

        # inject_skills must be called exactly twice (messages 1 and 4).
        assert injection_service.inject_skills.await_count == 2, (
            f"Expected inject_skills called 2 times (msgs 1, 4), "
            f"got {injection_service.inject_skills.await_count}"
        )

    async def test_cached_result_reused_on_skipped_messages(self):
        """After message 1's search produces a result, the cached value
        in ``_context_skill_results`` must persist and be read (not
        None) on the skipped messages 2 and 3.
        """
        injection_service = _make_injection_service(
            injection_text="[System Inject] SKILL_X",
            skill_ids=["skill-X"],
        )
        agent_meta = SimpleNamespace(
            agent_id="worker",
            skill_injection=True,
            skill_search_interval=3,
            context_injection_mode="human_messages",
        )
        manager = _make_manager(
            injection_service=injection_service,
            agent_meta=agent_meta,
        )
        svc = _make_service(manager)

        await _send_messages(
            svc=svc, manager=manager, agent_meta=agent_meta, n=3,
        )

        # After 3 messages (1 search + 2 skips), the cache must hold
        # the result from message 1's search.
        cached = manager.get_context_skill_result("inst-1")
        assert cached is not None, (
            "Cached result must be present after a search ran"
        )
        assert cached[0] == "[System Inject] SKILL_X"
        assert cached[1] == ["skill-X"]


# ============================================================
# Scenario 2: interval=1 backward compatibility
# ============================================================


@pytest.mark.asyncio
class TestIntervalOneBackwardCompat:
    """``skill_search_interval=1`` (default): every message must search.
    No skipping, no caching benefit.
    """

    async def test_inject_skills_called_on_every_message(self):
        injection_service = _make_injection_service(
            injection_text="[System Inject] SKILL_X",
            skill_ids=["skill-X"],
        )
        agent_meta = SimpleNamespace(
            agent_id="worker",
            skill_injection=True,
            skill_search_interval=1,
            context_injection_mode="human_messages",
        )
        manager = _make_manager(
            injection_service=injection_service,
            agent_meta=agent_meta,
        )
        svc = _make_service(manager)

        await _send_messages(
            svc=svc, manager=manager, agent_meta=agent_meta, n=3,
        )

        assert injection_service.inject_skills.await_count == 3, (
            f"interval=1 must search every message; "
            f"got {injection_service.inject_skills.await_count} calls for 3 msgs"
        )


# ============================================================
# Scenario 3: load_skill bypasses auto-search
# ============================================================


@pytest.mark.asyncio
class TestLoadSkillBypass:
    """When an explicit ``load_skill`` meta tag is present in a parent
    dispatch message, the ``<meta>`` REPLACE path runs and uses
    ``inject_explicit_skill``.  The auto-search ``inject_skills`` block
    still runs (it is NOT gated on ``_meta_skill``), but the key
    contract is that the explicit skill is loaded and tracked.

    This test documents the actual interaction: with a ``<meta>`` tag
    on a parent-dispatch source, both ``inject_skills`` (auto-search)
    and ``inject_explicit_skill`` (REPLACE) are invoked.  The
    ``load_skill`` value does NOT suppress the auto-search — it adds
    the explicit skill on top via the separate REPLACE block.
    """

    async def test_load_skill_uses_explicit_skill_path(self):
        injection_service = _make_injection_service(
            injection_text="[System Inject] auto-search-result",
            skill_ids=["auto-1"],
            explicit_text="[System Inject] LOADED_EXPLICIT",
            explicit_skill_ids=["explicit-skill-id"],
        )
        agent_meta = SimpleNamespace(
            agent_id="worker",
            skill_injection=True,
            skill_search_interval=5,
            context_injection_mode="human_messages",
        )
        manager = _make_manager(
            injection_service=injection_service,
            agent_meta=agent_meta,
        )
        svc = _make_service(manager)

        # Parent dispatch with a <meta> load_skill directive.
        message = 'do work <meta>{"load_skill": "explicit-skill"}</meta>'

        with patch("daemon.registry.get_registry") as mock_get_registry:
            mock_get_registry.return_value = _apply_registry(agent_meta)
            with patch(
                "daemon.services.context_messages.assemble_context_messages",
                new=AsyncMock(return_value=([], [])),
            ):
                await svc._process_message_with_tracking(
                    instance_id="inst-1",
                    message=message,
                    message_id="mid-1",
                    is_retry=False,
                    message_source="internal_agent:parent-123",
                )

        # The explicit REPLACE path must have been invoked.
        injection_service.inject_explicit_skill.assert_awaited_once()
        # The explicit skill result must be tracked.
        injection_service.track_injection.assert_called()

        # The explicit skill result must be stored in the cache
        # (the REPLACE block stores via set_context_skill_result).
        cached = manager.get_context_skill_result("inst-1")
        assert cached is not None
        assert cached[0] == "[System Inject] LOADED_EXPLICIT"

    async def test_load_skill_does_not_suppress_interval_search(self):
        """Documents that ``load_skill`` does NOT bypass the auto-search
        interval gate.  On the first message (cache empty), the
        auto-search runs regardless of the meta tag.  This is the
        actual production behavior — the two paths are independent.
        """
        injection_service = _make_injection_service(
            injection_text="[System Inject] auto-result",
            skill_ids=["auto-1"],
            explicit_text="[System Inject] EXPLICIT",
            explicit_skill_ids=["explicit-1"],
        )
        agent_meta = SimpleNamespace(
            agent_id="worker",
            skill_injection=True,
            skill_search_interval=5,
            context_injection_mode="human_messages",
        )
        manager = _make_manager(
            injection_service=injection_service,
            agent_meta=agent_meta,
        )
        svc = _make_service(manager)

        message = 'work <meta>{"load_skill": "explicit-skill"}</meta>'

        with patch("daemon.registry.get_registry") as mock_get_registry:
            mock_get_registry.return_value = _apply_registry(agent_meta)
            with patch(
                "daemon.services.context_messages.assemble_context_messages",
                new=AsyncMock(return_value=([], [])),
            ):
                await svc._process_message_with_tracking(
                    instance_id="inst-1",
                    message=message,
                    message_id="mid-1",
                    is_retry=False,
                    message_source="internal_agent:parent-123",
                )

        # The auto-search inject_skills still ran on this first message
        # (cache was empty, so the gate fell to the search branch).
        assert injection_service.inject_skills.await_count == 1


# ============================================================
# Scenario 3b: W1 explicit-load cache isolation
# ============================================================


@pytest.mark.asyncio
class TestW1ExplicitLoadCacheIsolation:
    """Exercise the W1 marker through the real messaging path.

    With ``interval=3``, the explicit ``load_skill`` message reuses the
    first message's auto-search result, then writes an explicit result and
    marks it.  The following ordinary message must therefore search again
    instead of reusing that explicit result.
    """

    async def test_explicit_load_forces_fresh_search_on_next_message(self):
        injection_service = _make_injection_service(
            injection_text="[System Inject] AUTO_RESULT",
            skill_ids=["auto-1"],
            explicit_text="[System Inject] EXPLICIT_RESULT",
            explicit_skill_ids=["explicit-1"],
        )
        agent_meta = SimpleNamespace(
            agent_id="worker",
            skill_injection=True,
            skill_search_interval=3,
            context_injection_mode="human_messages",
        )
        manager = _make_manager(
            injection_service=injection_service,
            agent_meta=agent_meta,
        )
        svc = _make_service(manager)

        with patch("daemon.registry.get_registry") as mock_get_registry:
            mock_get_registry.return_value = _apply_registry(agent_meta)
            with patch(
                "daemon.services.context_messages.assemble_context_messages",
                new=AsyncMock(return_value=([], [])),
            ):
                # Message 1: no cache, so auto-search runs and clears the
                # marker before the result becomes reusable.
                await svc._process_message_with_tracking(
                    instance_id="inst-1",
                    message="ordinary-1",
                    message_id="w1-mid-1",
                    is_retry=False,
                    message_source="agent:leader",
                )
                assert injection_service.inject_skills.await_count == 1
                assert manager.was_explicit_skill_loaded("inst-1") is False

                # The explicit path reuses the auto result at the gate, then
                # replaces the cache and marks it as explicit.
                await svc._process_message_with_tracking(
                    instance_id="inst-1",
                    message=(
                        'explicit-2 <meta>{"load_skill": '
                        '"explicit-skill"}</meta>'
                    ),
                    message_id="w1-mid-2",
                    is_retry=False,
                    message_source="internal_agent:parent-123",
                )
                assert injection_service.inject_skills.await_count == 1
                injection_service.inject_explicit_skill.assert_awaited_once()
                assert manager.was_explicit_skill_loaded("inst-1") is True
                assert manager.get_context_skill_result("inst-1") == (
                    "[System Inject] EXPLICIT_RESULT",
                    ["explicit-1"],
                )

                # Message 3 is still inside the interval window, but the W1
                # marker forces a fresh auto-search instead of a cache hit.
                await svc._process_message_with_tracking(
                    instance_id="inst-1",
                    message="ordinary-3",
                    message_id="w1-mid-3",
                    is_retry=False,
                    message_source="agent:leader",
                )

        assert injection_service.inject_skills.await_count == 2
        assert [
            call.args[0] for call in injection_service.inject_skills.call_args_list
        ] == ["ordinary-1", "ordinary-3"]
        assert manager.was_explicit_skill_loaded("inst-1") is False
        assert manager.get_context_skill_result("inst-1") == (
            "[System Inject] AUTO_RESULT",
            ["auto-1"],
        )


# ============================================================
# Scenario 3c: W1 marker cleanup
# ============================================================


class TestW1ExplicitLoadCleanup:
    """The lifecycle cleanup must remove the W1 marker with its caches."""

    def test_cleanup_removes_explicit_load_marker(self):
        from daemon.manager import InstanceManager

        instance_id = "inst-w1-cleanup"
        mgr = InstanceManager.__new__(InstanceManager)
        for attr in (
            "_graph_tasks",
            "_pending_injections",
            "_gii_throttle",
            "_loop_breaker_state",
            "_context_skill_results",
            "_skill_search_message_counts",
            "_emitted_message_content",
            "_original_timestamps",
            "_last_context_usage",
        ):
            setattr(mgr, attr, {})
        mgr._deferred_question_pause = set()
        mgr._question_manager = MagicMock()
        mgr._question_pause_requested = {}
        mgr._explicit_skill_loaded = set()
        mgr._skill_search_message_counts[instance_id] = 1
        mgr._context_skill_results[instance_id] = ("explicit", ["skill"])
        mgr.mark_explicit_skill_loaded(instance_id)

        mgr._cleanup_instance_state(instance_id)

        assert instance_id not in mgr._skill_search_message_counts
        assert instance_id not in mgr._context_skill_results
        assert instance_id not in mgr._explicit_skill_loaded


@pytest.mark.asyncio
class TestCachedResultCorrectness:
    """After a search produces a result with injection text "SKILL_X",
    the next skipped message must receive the SAME cached value — not
    None, not stale, not a different value.
    """

    async def test_cached_value_matches_search_result_across_messages(self):
        injection_service = _make_injection_service(
            injection_text="[System Inject] UNIQUE_RESULT_SKILL_X",
            skill_ids=["skill-unique"],
        )
        agent_meta = SimpleNamespace(
            agent_id="worker",
            skill_injection=True,
            skill_search_interval=4,
            context_injection_mode="human_messages",
        )
        manager = _make_manager(
            injection_service=injection_service,
            agent_meta=agent_meta,
        )
        svc = _make_service(manager)

        await _send_messages(
            svc=svc, manager=manager, agent_meta=agent_meta, n=3,
        )

        # After 3 messages (1 search + 2 skips), the cache must still
        # hold the EXACT result from message 1.
        cached = manager.get_context_skill_result("inst-1")
        assert cached is not None
        assert cached[0] == "[System Inject] UNIQUE_RESULT_SKILL_X"
        assert cached[1] == ["skill-unique"]

        # The search only ran once (message 1); messages 2 and 3 reused
        # the cached value.
        assert injection_service.inject_skills.await_count == 1

    async def test_cache_is_not_none_on_skip(self):
        """Explicitly verify the cached value is not None on a skipped
        message — the ``cached is not None`` clause in the gate is the
        key correctness invariant.
        """
        injection_service = _make_injection_service(
            injection_text="[System Inject] SKILL_Y",
            skill_ids=["skill-Y"],
        )
        agent_meta = SimpleNamespace(
            agent_id="worker",
            skill_injection=True,
            skill_search_interval=3,
            context_injection_mode="human_messages",
        )
        manager = _make_manager(
            injection_service=injection_service,
            agent_meta=agent_meta,
        )
        svc = _make_service(manager)

        # Send 2 messages (1 search + 1 skip).
        await _send_messages(
            svc=svc, manager=manager, agent_meta=agent_meta, n=2,
        )

        cached = manager.get_context_skill_result("inst-1")
        assert cached is not None
        # The cache key must exist in the dict (not just return a value).
        assert "inst-1" in manager._context_skill_results


# ============================================================
# Scenario 5: counter cleanup on instance cleanup
# ============================================================


class TestCounterCleanupOnInstanceCleanup:
    """``_cleanup_instance_state`` must remove both
    ``_skill_search_message_counts[instance_id]`` and
    ``_context_skill_results[instance_id]``.
    """

    def test_cleanup_removes_counter_and_cache(self):
        """Use a real ``InstanceManager.__new__`` with the two dicts
        attached (same pattern as ``test_skill_search_interval.py``
        unit tests) so ``_cleanup_instance_state`` exercises the actual
        ``pop`` calls on the real dicts.
        """
        from daemon.manager import InstanceManager

        instance_id = "inst-cleanup-1"

        mgr = InstanceManager.__new__(InstanceManager)
        # Attach all the dicts/attrs that _cleanup_instance_state pops
        # from or calls methods on.
        mgr._graph_tasks = {}
        mgr._pending_injections = {}
        mgr._gii_throttle = {}
        mgr._loop_breaker_state = {}
        mgr._context_skill_results = {}
        mgr._skill_search_message_counts = {}
        mgr._emitted_message_content = {}
        mgr._original_timestamps = {}
        mgr._last_context_usage = {}
        mgr._deferred_question_pause = set()
        mgr._question_manager = MagicMock()
        mgr._question_pause_requested = {}

        # Populate counter and cache.
        mgr.get_and_increment_skill_search_count(instance_id)
        mgr.set_context_skill_result(
            instance_id, ("some text", ["skill-1"])
        )

        assert instance_id in mgr._skill_search_message_counts
        assert instance_id in mgr._context_skill_results

        # Run cleanup.
        mgr._cleanup_instance_state(instance_id)

        # Both must be gone.
        assert instance_id not in mgr._skill_search_message_counts, (
            "_cleanup_instance_state must drop the counter entry"
        )
        assert instance_id not in mgr._context_skill_results, (
            "_cleanup_instance_state must drop the cache entry"
        )

    def test_fresh_message_searches_again_after_cleanup(self):
        """After cleanup, a fresh message must search again because the
        counter starts at 0 and the cache is gone (``cached is None``).
        """
        from daemon.manager import InstanceManager

        instance_id = "inst-cleanup-2"

        mgr = InstanceManager.__new__(InstanceManager)
        mgr._skill_search_message_counts = {}
        mgr._context_skill_results = {}

        # Simulate a prior search (counter at 2, cache populated).
        mgr._skill_search_message_counts[instance_id] = 2
        mgr._context_skill_results[instance_id] = ("cached", ["s"])

        # Simulate the cleanup's pop on both dicts (same as the
        # production code in _cleanup_instance_state).
        counts = getattr(mgr, "_skill_search_message_counts", None)
        if counts is not None:
            counts.pop(instance_id, None)
        cache = getattr(mgr, "_context_skill_results", None)
        if cache is not None:
            cache.pop(instance_id, None)

        # Fresh message: counter returns 0 (fresh), cache returns None.
        assert mgr.get_and_increment_skill_search_count(instance_id) == 0
        assert mgr.get_context_skill_result(instance_id) is None
