"""Tests for the Watchover activation / deactivation lifecycle (Phase 3).

Covers:

  * **T3.1 — ``SuspensionReason.WATCHOVER_SETUP`` enum value exists.**
    Pure-Python enum append; no migration required (TEXT/VARCHAR column).
  * **T3.3 — Atomic flag management.** ``set_metadata_many`` writes
    all keys; ``enable_watchover`` / ``disable_watchover`` round-trip.
  * **T3.3b — ``set_metadata_many`` SQL shape.** Generated SQL
    contains the right dialect-aware constructs (jsonb_set chain on
    PG, json_set chain on SQLite).
  * **T3.5b — Quiescence barrier.** ``wait_for_instance_quiescent``
    returns ``True`` when no task, ``True`` when task done, ``False``
    on timeout, and never raises.
  * **T3.5 — Activation lifecycle.** ``enable_watchover_lifecycle``
    runs the pause → quiesce → context → flag → resume sequence in
    the right order with the right arguments; rollback on compaction
    failure clears partial flags and re-raises.
  * **T3.6 — Deactivation lifecycle.** ``disable_watchover_lifecycle``
    runs the pause → clear → resume sequence.
  * **T3.4 — Context construction.** ``_build_watchover_context``
    uses the compactor when available, falls back to raw-tail when
    ``compact_state`` returns ``None``, and combines with the user
    requirement.
  * **T3.7 — API endpoint.** ``POST /instances/{id}/watchover``
    reaches the right manager method with the right body.

All tests mock the LLM + LangGraph surface (same pattern as
``tests/unit/test_watchover_graph.py``) so they run without a real
server or database.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# =============================================================================
# Helpers
# =============================================================================


def make_bare_manager() -> Any:
    """Build a bare ``InstanceManager`` (skip ``__init__``) seeded for flag tests."""
    from daemon.manager import InstanceManager

    manager = InstanceManager.__new__(InstanceManager)
    manager._deferred_watchover_terminate = set()
    manager._graph_tasks = {}
    manager._live_hub = MagicMock()
    manager._live_hub.stream_status_change = AsyncMock()
    return manager


def make_full_manager() -> MagicMock:
    """Build a manager mock with the watchover lifecycle surface wired.

    Wires the methods the WatchoverService consumes:

      * ``pause_instance_cascade``, ``resume_instance_cascade`` — async
      * ``wait_for_instance_quiescent`` — async, default True
      * ``enable_watchover``, ``disable_watchover`` — sync, no return
      * ``set_metadata_many`` — sync, used for rollback
      * ``get_instance`` — async, returns a mock graph
      * ``_compactor`` — set/unset via test fixture
      * ``config`` (PUBLIC attribute) — config object with llm and
        compaction fields. The earlier ``_config`` typo passed CI
        only because MagicMock auto-creates missing attributes;
        production would crash on first activation with compaction
        enabled (C1 fix).
      * ``_instance_repository`` — MagicMock for direct flag tests
      * ``_live_hub`` — async stream_status_change

    Returns:
        A ``MagicMock`` with the watchover surface wired.
    """
    manager = MagicMock()
    manager.pause_instance_cascade = AsyncMock()
    # ``resume_instance_cascade`` returns a dict with
    # ``target_id`` and ``resumed_ids`` so the watchover resume
    # helper can fan-out ``resume_processing_job`` over every
    # resumed instance. Default = single-instance cascade (only
    # the target, no children). Tests that exercise children
    # override the return value.
    manager.resume_instance_cascade = AsyncMock(
        return_value={"target_id": "iid", "resumed_ids": ["iid"], "skipped_ids": []}
    )
    # ``resume_processing_job`` returns a non-None dict on the
    # success path; returning ``None`` exercises the
    # ``enqueue_message`` fallback in the helper. Tests that need
    # either path set the return value explicitly.
    manager.resume_processing_job = AsyncMock(
        return_value={"status": "resumed", "instance_id": "iid"}
    )
    # ``enqueue_message`` is the fallback path when
    # ``resume_processing_job`` returns None. Default = OK.
    manager.enqueue_message = AsyncMock(
        return_value=MagicMock(message_id="m1", job_id="j1")
    )
    manager.wait_for_instance_quiescent = AsyncMock(return_value=True)
    manager.enable_watchover = MagicMock()
    manager.disable_watchover = MagicMock()
    manager.set_metadata_many = MagicMock()
    manager.get_instance = AsyncMock()

    # Live hub
    manager._live_hub = MagicMock()
    manager._live_hub.stream_status_change = AsyncMock()

    # Config — use the PUBLIC ``config`` attribute (matches
    # InstanceManager.__init__ which sets ``self.config = config``).
    manager.config = MagicMock()
    manager.config.llm.base_url = "http://proxy"
    manager.config.llm.api_key = "k"
    manager.config.llm.model = "gpt-4o"
    manager.config.llm.model_vision = "gpt-4o"
    manager.config.llm.temperature = 0.0
    manager.config.llm.request_timeout = 60.0
    manager.config.compaction = MagicMock()

    # Default — compactor set to None to force raw-tail fallback.
    manager._compactor = None

    # Default — repo with no instance.
    manager._instance_repository = MagicMock()

    # Default — message queue repository. The watchover resume
    # helper dedupes against pending messages on this repo. Tests
    # that need a non-empty queue override ``list_pending``.
    manager._queue_repository = MagicMock()
    manager._queue_repository.list_pending = MagicMock(return_value=[])
    return manager


def make_mock_compactor() -> MagicMock:
    """Build a mock ``ContextCompactor``.

    Returns:
        A ``MagicMock`` whose ``compact_state`` is an ``AsyncMock``
        that defaults to returning ``None`` (i.e. no compaction).
    """
    compactor = MagicMock()
    compactor.compact_state = AsyncMock(return_value=None)
    return compactor


def make_mock_builder(
    *,
    response: str = "## Agent Activity\nTest\n\n## Available Tools\n\n## Allowed\n- test\n\n## Forbidden\n- test\n\n## Requirement\n(none provided)",
    side_effect: Any = None,
) -> MagicMock:
    """Build a mock :class:`WatcherContextBuilder`.

    Phase 4: replaces the compactor fixture for the activation
    lifecycle tests. The builder is patched at the import path
    :func:`daemon.services.watchover_service` uses. Returns a
    :class:`MagicMock` whose ``build`` is an ``AsyncMock`` that
    defaults to returning ``response`` (a stub markdown document).
    Pass ``side_effect`` to simulate builder failure (the activation
    lifecycle expects the builder to swallow its own errors and
    return the fallback — see :class:`WatcherContextBuilder.build`).

    Args:
        response: Markdown string returned by ``build`` when no
            ``side_effect`` is supplied.
        side_effect: Optional exception or callable for the
            ``AsyncMock`` — when set, ``build`` raises instead of
            returning ``response``.

    Returns:
        A ``MagicMock`` whose ``build`` is an ``AsyncMock``.
    """
    builder = MagicMock()
    if side_effect is not None:
        builder.build = AsyncMock(side_effect=side_effect)
    else:
        builder.build = AsyncMock(return_value=response)
    return builder


def make_mock_graph_state(messages: list[Any] | None = None) -> MagicMock:
    """Build a mock compiled graph with a state snapshot.

    Returns:
        A ``MagicMock`` whose ``aget_state`` is an ``AsyncMock``
        returning a snapshot with the supplied messages.
    """
    graph = MagicMock()
    state = MagicMock()
    state.values = {"messages": messages or []}
    graph.aget_state = AsyncMock(return_value=state)
    return graph


# =============================================================================
# T3.1 — SuspensionReason.WATCHOVER_SETUP enum value
# =============================================================================


class TestSuspensionReasonWatchoverSetup:
    """``SuspensionReason.WATCHOVER_SETUP`` enum value exists.

    The ``suspension_reason`` column on ``task`` is TEXT/VARCHAR (not
    a PostgreSQL native enum), so this is a pure-Python enum append.
    No SQL migration is needed.
    """

    def test_watchover_setup_value_exists(self):
        from daemon.repositories.task.models import SuspensionReason

        assert hasattr(SuspensionReason, "WATCHOVER_SETUP")

    def test_watchover_setup_string_value(self):
        from daemon.repositories.task.models import SuspensionReason

        assert SuspensionReason.WATCHOVER_SETUP.value == "watchover_setup"

    def test_watchover_setup_is_str_enum(self):
        """Inherits from ``str`` so it serializes identically to the literal."""
        from daemon.repositories.task.models import SuspensionReason

        assert isinstance(SuspensionReason.WATCHOVER_SETUP, str)

    def test_existing_values_still_present(self):
        """Append-only — the original members are not regressed."""
        from daemon.repositories.task.models import SuspensionReason

        assert SuspensionReason.AWAITING_ANSWER.value == "awaiting_answer"
        assert SuspensionReason.AWAITING_CHILDREN.value == "awaiting_children"
        assert SuspensionReason.PAUSED_EXTERNAL.value == "paused_external"


# =============================================================================
# T3.3b — set_metadata_many atomic helper (repository)
# =============================================================================


class TestSetMetadataMany:
    """``InstanceRepository.set_metadata_many`` writes multiple keys in ONE UPDATE.

    Builds and executes a single dialect-aware SQL statement that
    composes ``jsonb_set`` (PG) or ``json_set`` (SQLite) chains. The
    key safety property is that all keys land in the same UPDATE —
    a partial crash mid-write cannot expose torn state.
    """

    def test_postgres_sql_uses_nested_jsonb_set_chain(self):
        """PG path emits a nested jsonb_set chain, one layer per key."""
        from sqlalchemy import create_engine

        from daemon.repositories.instance.repository import (
            SQLModelInstanceRepository,
        )

        repo = SQLModelInstanceRepository(engine=create_engine("sqlite:///:memory:"))
        # Use the PG dialect by passing a PG engine — but for SQL-shape
        # verification we can patch the dialect name to "postgresql"
        # and capture the SQL via SQLAlchemy's dialect compilation.
        # Easier: build the SQL string manually the way the method does
        # by exercising the public method with a mocked session.
        db_session = MagicMock()
        db_session.bind = MagicMock()
        db_session.bind.dialect.name = "postgresql"
        db_session.execute = MagicMock()
        db_session.commit = MagicMock()
        db_session.get = MagicMock(return_value=None)

        with patch.object(repo, "_enrich_instance", return_value=None):
            with patch(
                "daemon.repositories.instance.repository.SQLModelSession"
            ) as mock_session_cls:
                mock_session_cls.return_value.__enter__.return_value = db_session
                result = repo.set_metadata_many("iid", {"a": 1, "b": "two", "c": True})

        # Result is None because db_session.get returned None.
        assert result is None

        # Verify the executed SQL has the nested jsonb_set chain.
        call = db_session.execute.call_args
        sql_text = str(call.args[0])
        assert "jsonb_set" in sql_text
        # Three keys → three jsonb_set calls in the chain.
        assert sql_text.count("jsonb_set") == 3
        # Three :path parameters and three :value parameters.
        params = call.args[1]
        for i in range(3):
            assert f"path{i}" in params
            assert f"value{i}" in params

    def test_sqlite_sql_uses_nested_json_set_chain(self):
        """SQLite path emits a nested json_set chain, one layer per key."""
        from sqlalchemy import create_engine

        from daemon.repositories.instance.repository import (
            SQLModelInstanceRepository,
        )

        repo = SQLModelInstanceRepository(engine=create_engine("sqlite:///:memory:"))
        db_session = MagicMock()
        db_session.bind = None  # Forces the fallback to "sqlite"
        db_session.execute = MagicMock()
        db_session.commit = MagicMock()
        db_session.get = MagicMock(return_value=None)

        with patch.object(repo, "_enrich_instance", return_value=None):
            with patch(
                "daemon.repositories.instance.repository.SQLModelSession"
            ) as mock_session_cls:
                mock_session_cls.return_value.__enter__.return_value = db_session
                result = repo.set_metadata_many("iid", {"x": 1, "y": 2})

        assert result is None

        sql_text = str(db_session.execute.call_args.args[0])
        # Two keys → two json_set calls.
        assert "json_set" in sql_text
        assert sql_text.count("json_set") == 2
        # $. path format.
        params = db_session.execute.call_args.args[1]
        assert params["path0"] == "$.x"
        assert params["path1"] == "$.y"

    def test_empty_updates_raises(self):
        """Empty dict → ``ValueError`` (caller bug — silent no-op is ambiguous)."""
        from sqlalchemy import create_engine

        from daemon.repositories.instance.repository import (
            SQLModelInstanceRepository,
        )

        repo = SQLModelInstanceRepository(engine=create_engine("sqlite:///:memory:"))
        with pytest.raises(ValueError, match="at least one key"):
            repo.set_metadata_many("iid", {})


# =============================================================================
# T3.3 — Manager flag management (enable/disable/get/set_metadata_many)
# =============================================================================


class TestManagerFlagManagement:
    """Manager flag accessors — ``set_metadata_many`` + ``enable_watchover`` + ``disable_watchover`` + ``get_watchover_context``."""

    def test_manager_set_metadata_many_delegates(self):
        """``manager.set_metadata_many`` is a thin delegator to the repo."""
        manager = make_bare_manager()
        repo = MagicMock()
        repo.set_metadata_many = MagicMock(return_value="enriched")
        manager._instance_repository = repo

        assert manager.set_metadata_many("iid", {"a": 1}) == "enriched"
        repo.set_metadata_many.assert_called_once_with("iid", {"a": 1})

    def test_enable_watchover_writes_all_keys(self):
        """``enable_watchover`` writes the full config in one ``set_metadata_many`` call."""
        manager = make_bare_manager()
        repo = MagicMock()
        manager._instance_repository = repo

        manager.enable_watchover(
            "iid",
            requirement="no destructive ops",
            context="recent activity summary",
        )

        repo.set_metadata_many.assert_called_once()
        args = repo.set_metadata_many.call_args.args
        updates = args[1]
        assert updates["watchover_enabled"] is True
        assert updates["watchover_requirement"] == "no destructive ops"
        assert updates["watchover_context"] == "recent activity summary"

    def test_enable_watchover_tolerates_none_requirement_and_context(self):
        """``None`` requirement/context are omitted from the update dict.

        T5.4: ``watchover_context_turn`` and
        ``watchover_context_refresh_interval`` are always written (the
        freshness check requires them even when context/requirement are
        absent).

        C1 fix: the default ``refresh_interval`` is 20 (was 1) so the
        LLM-built guardrail survives many turns.
        """
        manager = make_bare_manager()
        repo = MagicMock()
        manager._instance_repository = repo

        manager.enable_watchover("iid", requirement=None, context=None)

        updates = repo.set_metadata_many.call_args.args[1]
        assert updates == {
            "watchover_enabled": True,
            "watchover_context_turn": 0,
            "watchover_context_refresh_interval": 20,
        }

    def test_disable_watchover_only_clears_enabled(self):
        """``disable_watchover`` only sets ``watchover_enabled=False`` (audit keeps rest)."""
        manager = make_bare_manager()
        repo = MagicMock()
        manager._instance_repository = repo

        manager.disable_watchover("iid")

        repo.set_metadata_many.assert_called_once_with(
            "iid", {"watchover_enabled": False}
        )

    def test_get_watchover_context_returns_value(self):
        """Returns ``watchover_context`` string when present."""
        manager = make_bare_manager()
        instance = MagicMock()
        instance.instance_metadata = {"watchover_context": "abc"}
        repo = MagicMock()
        repo.get.return_value = instance
        manager._instance_repository = repo

        assert manager.get_watchover_context("iid") == "abc"

    def test_get_watchover_context_returns_none_when_absent(self):
        manager = make_bare_manager()
        instance = MagicMock()
        instance.instance_metadata = {}
        repo = MagicMock()
        repo.get.return_value = instance
        manager._instance_repository = repo

        assert manager.get_watchover_context("iid") is None

    def test_get_watchover_context_returns_none_when_metadata_empty(self):
        manager = make_bare_manager()
        instance = MagicMock()
        instance.instance_metadata = None
        repo = MagicMock()
        repo.get.return_value = instance
        manager._instance_repository = repo

        assert manager.get_watchover_context("iid") is None

    def test_get_watchover_context_returns_none_when_instance_missing(self):
        manager = make_bare_manager()
        repo = MagicMock()
        repo.get.return_value = None
        manager._instance_repository = repo

        assert manager.get_watchover_context("iid") is None

    def test_get_watchover_context_returns_none_on_exception(self):
        """Repository exception → ``None`` (defensive read)."""
        manager = make_bare_manager()
        repo = MagicMock()
        repo.get.side_effect = RuntimeError("DB down")
        manager._instance_repository = repo

        assert manager.get_watchover_context("iid") is None


# =============================================================================
# T3.5b — Quiescence barrier
# =============================================================================


class TestWaitForInstanceQuiescent:
    """``manager.wait_for_instance_quiescent`` best-effort barrier."""

    @pytest.mark.asyncio
    async def test_returns_true_when_no_task(self):
        """No entry in ``_graph_tasks`` → ``True`` immediately."""
        manager = make_bare_manager()
        manager._graph_tasks = {}

        result = await manager.wait_for_instance_quiescent("iid", timeout=30.0)
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_true_when_task_done(self):
        """Task already done → ``True`` without waiting."""
        manager = make_bare_manager()
        task = MagicMock()
        task.done.return_value = True
        manager._graph_tasks = {"iid": task}

        result = await manager.wait_for_instance_quiescent("iid", timeout=30.0)
        assert result is True
        task.done.assert_called_once()

    @pytest.mark.asyncio
    async def test_returns_true_when_task_completes_within_timeout(self):
        """Task completes within timeout → ``True``."""
        import asyncio

        manager = make_bare_manager()

        async def _quick():
            await asyncio.sleep(0.01)
            return "done"

        task = asyncio.create_task(_quick())
        manager._graph_tasks = {"iid": task}
        try:
            result = await manager.wait_for_instance_quiescent("iid", timeout=2.0)
            assert result is True
        finally:
            if not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

    @pytest.mark.asyncio
    async def test_returns_false_on_timeout(self):
        """Task still in flight at timeout → ``False`` (best-effort)."""
        manager = make_bare_manager()

        # Build a real asyncio task that we can cancel.
        async def _long_running():
            import asyncio

            await asyncio.sleep(10)

        import asyncio

        task = asyncio.create_task(_long_running())
        manager._graph_tasks = {"iid": task}
        try:
            result = await manager.wait_for_instance_quiescent("iid", timeout=0.05)
            assert result is False
        finally:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    @pytest.mark.asyncio
    async def test_returns_false_on_unexpected_error(self):
        """Unexpected exception → ``False`` (never raises)."""
        manager = make_bare_manager()

        async def _long_running():
            import asyncio

            await asyncio.sleep(10)

        import asyncio

        task = asyncio.create_task(_long_running())
        # Patch shield to raise.
        original_shield = asyncio.shield
        asyncio.shield = MagicMock(side_effect=RuntimeError("boom"))
        manager._graph_tasks = {"iid": task}
        try:
            result = await manager.wait_for_instance_quiescent("iid", timeout=0.05)
            assert result is False
        finally:
            asyncio.shield = original_shield
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    @pytest.mark.asyncio
    async def test_zero_timeout_returns_false_when_task_in_flight(self):
        """``timeout<=0`` with a live task → ``False`` (do not wait)."""
        manager = make_bare_manager()

        async def _long_running():
            import asyncio

            await asyncio.sleep(10)

        import asyncio

        task = asyncio.create_task(_long_running())
        task.done = MagicMock(return_value=False)
        manager._graph_tasks = {"iid": task}
        try:
            result = await manager.wait_for_instance_quiescent("iid", timeout=0)
            assert result is False
        finally:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass


# =============================================================================
# T3.4 + T3.5 — WatchoverService.activate_watchover
# =============================================================================


class TestActivateWatchover:
    """Activation lifecycle: pause → bounded_barrier → context → flag → resume."""

    @pytest.mark.asyncio
    async def test_activate_runs_correct_sequence(self):
        """Pause happens before enable; resume happens after enable.

        Phase 4 update + pause-first ordering fix: the activation
        lifecycle uses the ``WatcherContextBuilder`` instead of the
        compactor. When a ``user_context`` is supplied the builder is
        bypassed entirely and the requirement is NOT post-spliced into
        the user-supplied context (the requirement is woven into the
        builder's output only on the builder path). The lifecycle
        ordering — pause → bounded_barrier(2s) → enable → resume →
        SSE — matches the new pause-first ordering. Pause comes FIRST
        so the user sees the instance flip to PAUSED immediately
        (the bug fix for the M-2 in-flight hang).
        """
        manager = make_full_manager()
        manager.get_instance.return_value = make_mock_graph_state(messages=[])

        from daemon.services.watchover_service import WatchoverService

        svc = WatchoverService(manager)

        result = await svc.activate_watchover(
            "iid",
            requirement="no rm -rf",
            user_context="user-supplied",
        )

        assert result["watchover_enabled"] is True
        assert result["instance_id"] == "iid"
        assert result["quiescent"] is True

        # Order: pause → bounded_barrier(2s) → enable → resume → SSE.
        assert manager.wait_for_instance_quiescent.await_count == 1
        assert manager.pause_instance_cascade.await_count == 1
        manager.pause_instance_cascade.assert_awaited_once_with(
            "iid",
            suspension_reason="watchover_setup",
        )
        # Bounded barrier runs AFTER pause with timeout=2.0.
        manager.wait_for_instance_quiescent.assert_awaited_once_with(
            "iid", timeout=2.0
        )
        assert manager.enable_watchover.call_count == 1
        assert manager.resume_instance_cascade.await_count == 1
        assert manager._live_hub.stream_status_change.await_count == 1
        assert manager._live_hub.stream_status_change.call_args.args == (
            "iid",
            "watchover_active",
        )

        # enable_watchover got the requirement + user-supplied context.
        # Phase 4: requirement is NOT spliced into a user-supplied
        # context — the requirement is a builder INPUT, applied only
        # when the builder runs.
        enable_kwargs = manager.enable_watchover.call_args.kwargs
        assert enable_kwargs["requirement"] == "no rm -rf"
        assert enable_kwargs["context"] == "user-supplied"

    @pytest.mark.asyncio
    async def test_activate_combines_requirement_with_built_context(self):
        """When ``user_context`` is None, the service invokes the builder.

        Phase 4 update: the builder receives ``requirement`` as an
        INPUT, not a post-splice. The returned markdown guardrail
        document contains the requirement verbatim (the builder
        echoes it into ``## Requirement``) and the raw-tail snapshot
        of recent activity.
        """
        manager = make_full_manager()
        manager.get_instance.return_value = make_mock_graph_state(
            messages=[_msg("hello", role="user"), _msg("hi", role="assistant")]
        )

        from daemon.services.watchover_service import WatchoverService
        from daemon.services.watcher_context_builder import (
            WatcherContextBuilder,
        )

        # Builder returns a stub markdown document that echoes the
        # requirement. The activation lifecycle passes the requirement
        # as an INPUT to ``builder.build`` — assert it was forwarded.
        builder_response = (
            "## Agent Activity\nTest\n\n"
            "## Allowed\n- test\n\n"
            "## Forbidden\n- test\n\n"
            "## Requirement\nbe nice"
        )
        with patch.object(
            WatcherContextBuilder,
            "build",
            AsyncMock(return_value=builder_response),
        ) as patched_build:
            svc = WatchoverService(manager)
            result = await svc.activate_watchover(
                "iid", requirement="be nice", user_context=None
            )

            # Builder was invoked with the requirement as input.
            patched_build.assert_awaited_once()
            build_args = patched_build.call_args.args
            # Positional: (messages, requirement); the 2nd positional is
            # the requirement (the lifecycle forwards it directly).
            assert build_args[1] == "be nice"

        assert result["watchover_enabled"] is True
        enable_kwargs = manager.enable_watchover.call_args.kwargs
        ctx = enable_kwargs["context"]
        # Builder's output is the watchover_context — requirement is
        # woven into it, not appended.
        assert ctx == builder_response
        assert "be nice" in ctx

    @pytest.mark.asyncio
    async def test_activate_uses_builder_when_available(self):
        """The WatcherContextBuilder produces the markdown guardrail document.

        Phase 4: the activation lifecycle delegates context-building
        to the LLM-driven builder. The returned markdown is stored
        verbatim on ``watchover_context``.
        """
        manager = make_full_manager()
        manager.get_instance.return_value = make_mock_graph_state(
            messages=[_msg("hi")] * 20
        )

        from daemon.services.watchover_service import WatchoverService
        from daemon.services.watcher_context_builder import (
            WatcherContextBuilder,
        )

        builder_response = (
            "## Agent Activity\nRefactor auth module.\n\n"
            "## Allowed\n- read auth/\n\n"
            "## Forbidden\n- rm -rf\n\n"
            "## Requirement\nr"
        )
        with patch.object(
            WatcherContextBuilder,
            "build",
            AsyncMock(return_value=builder_response),
        ) as patched_build:
            svc = WatchoverService(manager)
            result = await svc.activate_watchover("iid", requirement="r")

            # Builder was invoked once with the requirement as input.
            patched_build.assert_awaited_once()
            # Positional: (messages, requirement); the 2nd positional is
            # the requirement (the lifecycle forwards it directly).
            assert patched_build.call_args.args[1] == "r"

        ctx = manager.enable_watchover.call_args.kwargs["context"]
        # Builder output is stored verbatim — no post-splice.
        assert ctx == builder_response

    @pytest.mark.asyncio
    async def test_activate_falls_back_to_raw_tail(self):
        """When the builder returns a degraded fallback, that fallback is used.

        Phase 4: the builder's internal fallback (raw-tail + static
        guardrail prefix) replaces the compactor's role. When the
        builder returns the fallback string, the activation lifecycle
        stores it verbatim on ``watchover_context`` — the watcher
        still sees structured guidance even on degraded mode.
        """
        manager = make_full_manager()
        manager.get_instance.return_value = make_mock_graph_state(
            messages=[_msg(f"msg-{i}", role="user") for i in range(15)]
        )

        from daemon.services.watchover_service import WatchoverService
        from daemon.services.watcher_context_builder import (
            WatcherContextBuilder,
        )

        fallback_response = (
            "## Static Guardrail (degraded mode)\n"
            "## Forbidden\n- rm -rf\n\n"
            "[Requirement] r\n\n"
            "[Recent activity]\nmsg-5 ... msg-14"
        )
        with patch.object(
            WatcherContextBuilder,
            "build",
            AsyncMock(return_value=fallback_response),
        ):
            svc = WatchoverService(manager)
            result = await svc.activate_watchover("iid", requirement="r")

        ctx = manager.enable_watchover.call_args.kwargs["context"]
        # Builder's degraded-mode output is stored verbatim.
        assert ctx == fallback_response
        assert "Static Guardrail" in ctx
        assert "msg-5" in ctx

    @pytest.mark.asyncio
    async def test_activate_with_empty_messages_works(self):
        """Empty conversation → builder returns a sentinel; activation still writes."""
        manager = make_full_manager()
        manager.get_instance.return_value = make_mock_graph_state(messages=[])

        from daemon.services.watchover_service import WatchoverService
        from daemon.services.watcher_context_builder import (
            WatcherContextBuilder,
        )

        # Builder returns an empty response — the lifecycle applies
        # the empty-context guard.
        with patch.object(
            WatcherContextBuilder, "build", AsyncMock(return_value="")
        ):
            svc = WatchoverService(manager)
            result = await svc.activate_watchover("iid", requirement="no rm -rf")

        assert result["watchover_enabled"] is True
        ctx = manager.enable_watchover.call_args.kwargs["context"]
        # Empty-context guard: when the builder returns empty AND a
        # requirement was supplied, the context falls back to a
        # ``[Requirement]`` line so the watcher sees SOMETHING.
        assert "no rm -rf" in ctx

    @pytest.mark.asyncio
    async def test_activate_rollback_on_builder_unavailability(self):
        """Builder class unimportable → belt-and-suspenders fallback runs.

        Phase 4 update: the builder is designed to NEVER propagate
        failures (its internal fallback chain handles timeouts,
        infra errors, and judgment errors). The activation lifecycle
        therefore does NOT see a builder error — it sees the
        builder's degraded-mode output, and the activation succeeds.

        The belt-and-suspenders fallback in
        :meth:`WatchoverService._build_watchover_context` only triggers
        when the BUILDER ITSELF cannot be imported (a programmer
        mistake, not a runtime failure). In that rare case the
        service falls back to a static guardrail + raw-tail, and the
        activation still succeeds — the rollback path is NOT taken
        because the fallback produced a usable context.
        """
        manager = make_full_manager()
        manager.get_instance.return_value = make_mock_graph_state(
            messages=[_msg("hi")] * 20
        )

        import sys
        from daemon.services import watchover_service as ws_module
        from daemon.services.watchover_service import WatchoverService

        # Simulate builder module being unimportable — patch
        # ``__import__`` for the builder path. Cleaner: hide the
        # builder module so the local import in
        # ``_build_watchover_context`` raises ImportError.
        real_import = ws_module.__builtins__["__import__"] if hasattr(
            ws_module, "__builtins__"
        ) else None

        def _raise_on_builder_import(name, *args, **kwargs):
            if "watcher_context_builder" in name:
                raise ImportError("simulated builder unavailable")
            return real_import(name, *args, **kwargs) if real_import else __import__(name, *args, **kwargs)

        with patch.object(ws_module, "__builtins__", {"__import__": _raise_on_builder_import}):
            svc = WatchoverService(manager)
            result = await svc.activate_watchover("iid", requirement="r")

        # Activation still succeeds with the belt-and-suspenders fallback.
        assert result["watchover_enabled"] is True
        # No rollback — the lifecycle produced a usable context.
        manager.set_metadata_many.assert_not_called()
        manager.resume_instance_cascade.assert_awaited_once_with("iid")

    @pytest.mark.asyncio
    async def test_activate_rollback_on_enable_failure(self):
        """``enable_watchover`` raises → rollback clears flag + resume + SSE → re-raise (H1 + M4 + M5)."""
        manager = make_full_manager()
        manager.enable_watchover.side_effect = RuntimeError("write fail")
        manager.get_instance.return_value = make_mock_graph_state(messages=[])

        from daemon.services.watchover_service import WatchoverService

        svc = WatchoverService(manager)

        with pytest.raises(RuntimeError, match="write fail"):
            await svc.activate_watchover("iid", requirement="r")

        # Rollback clears the flag + context/requirement + audit marker.
        manager.set_metadata_many.assert_called_once()
        updates = manager.set_metadata_many.call_args.args[1]
        assert updates["watchover_enabled"] is False
        assert updates["watchover_context"] is None
        assert updates["watchover_requirement"] is None
        assert updates["watchover_transition"] == "rollback"

        # H1 — best-effort resume in the rollback path.
        manager.resume_instance_cascade.assert_awaited_once_with("iid")

        # M4 — rollback emits a watchover_failed SSE.
        sse_calls = manager._live_hub.stream_status_change.await_args_list
        rollback_sse = [
            c for c in sse_calls if c.args == ("iid", "watchover_failed")
        ]
        assert len(rollback_sse) == 1

    @pytest.mark.asyncio
    async def test_activate_rollback_on_resume_failure(self):
        """Resume raises → rollback clears flag + best-effort resume + re-raise (H1).

        The original resume failed, but the rollback block attempts
        resume AGAIN (best-effort) so the instance is never left
        PAUSED with the flag cleared. The rollback's resume may also
        fail (the same mock raises again) — that is logged but the
        original ``resume fail`` error is re-raised.
        """
        manager = make_full_manager()
        manager.resume_instance_cascade.side_effect = RuntimeError("resume fail")
        manager.get_instance.return_value = make_mock_graph_state(messages=[])

        from daemon.services.watchover_service import WatchoverService

        svc = WatchoverService(manager)

        with pytest.raises(RuntimeError, match="resume fail"):
            await svc.activate_watchover("iid", requirement="r")

        # H1 — the rollback block attempted resume again (the same
        # mock raises each time, so total await count is 2).
        assert manager.resume_instance_cascade.await_count == 2
        # First call was the original step-5 attempt; subsequent calls
        # are the best-effort rollback resume.
        assert manager.resume_instance_cascade.await_args_list[0].args == ("iid",)
        assert manager.resume_instance_cascade.await_args_list[1].args == ("iid",)

        # Rollback also clears context/requirement (M5) and writes
        # the audit marker.
        manager.set_metadata_many.assert_called_once()
        updates = manager.set_metadata_many.call_args.args[1]
        assert updates["watchover_enabled"] is False
        assert updates["watchover_context"] is None
        assert updates["watchover_requirement"] is None
        assert updates["watchover_transition"] == "rollback"

        # M4 — rollback emits a watchover_failed SSE best-effort.
        # The call_args list may include both the rollback SSE and the
        # would-be success SSE — in this failure path only the
        # rollback SSE should have been emitted.
        sse_calls = manager._live_hub.stream_status_change.await_args_list
        rollback_sse = [
            c for c in sse_calls if c.args == ("iid", "watchover_failed")
        ]
        assert len(rollback_sse) == 1

    @pytest.mark.asyncio
    async def test_activate_rollback_on_cancelled_error(self):
        """W1 fix: ``asyncio.CancelledError`` triggers the rollback path.

        On Python 3.13+ ``CancelledError`` inherits from
        ``BaseException`` (not ``Exception``). The route layer
        ``routers/instances.py`` wraps activation in
        ``asyncio.wait_for(timeout=30)`` and on timeout cancels the
        inner task with ``CancelledError``. Without the W1 fix the
        ``except Exception`` clause did NOT catch ``CancelledError``
        and the rollback (clear flags + resume + SSE) was skipped —
        the instance was left PAUSED with no recovery path.

        Regression test: when ``enable_watchover`` raises
        ``CancelledError``, the rollback path must fire and the
        original ``CancelledError`` must be re-raised.
        """
        import asyncio

        manager = make_full_manager()
        manager.enable_watchover.side_effect = asyncio.CancelledError()
        manager.get_instance.return_value = make_mock_graph_state(messages=[])

        from daemon.services.watchover_service import WatchoverService

        svc = WatchoverService(manager)

        with pytest.raises(asyncio.CancelledError):
            await svc.activate_watchover("iid", requirement="r")

        # W1 fix: the rollback path must fire on CancelledError —
        # clear the flag + context/requirement + audit marker.
        manager.set_metadata_many.assert_called_once()
        updates = manager.set_metadata_many.call_args.args[1]
        assert updates["watchover_enabled"] is False
        assert updates["watchover_context"] is None
        assert updates["watchover_requirement"] is None
        assert updates["watchover_transition"] == "rollback"

        # H1 — best-effort resume in the rollback path so the
        # instance is not left PAUSED.
        manager.resume_instance_cascade.assert_awaited_once_with("iid")

        # M4 — rollback emits a watchover_failed SSE so the frontend
        # is not stuck in a stale state.
        sse_calls = manager._live_hub.stream_status_change.await_args_list
        rollback_sse = [
            c for c in sse_calls if c.args == ("iid", "watchover_failed")
        ]
        assert len(rollback_sse) == 1

    @pytest.mark.asyncio
    async def test_activate_sse_failure_does_not_rollback(self):
        """SSE emit failure is best-effort — does NOT trigger rollback."""
        manager = make_full_manager()
        manager._live_hub.stream_status_change.side_effect = RuntimeError("sse boom")
        manager.get_instance.return_value = make_mock_graph_state(messages=[])

        from daemon.services.watchover_service import WatchoverService

        svc = WatchoverService(manager)

        # Should NOT raise.
        result = await svc.activate_watchover("iid", requirement="r")
        assert result["watchover_enabled"] is True

        # No rollback call.
        manager.set_metadata_many.assert_not_called()

    @pytest.mark.asyncio
    async def test_activate_logs_warning_on_barrier_post_pause_timeout(self, caplog):
        """Pause-first ordering: when the post-pause barrier returns False, log a warning.

        The barrier runs AFTER pause with a bounded 2s timeout. The
        cancellation was already issued by ``pause_instance_cascade``,
        so the activation proceeds regardless — the warning is purely
        informational so operators see the LD-4-style edge case in the
        logs even though the activation proceeds.
        """
        import logging

        manager = make_full_manager()
        manager.wait_for_instance_quiescent.return_value = False
        manager.get_instance.return_value = make_mock_graph_state(messages=[])

        from daemon.services.watchover_service import WatchoverService

        svc = WatchoverService(manager)

        with caplog.at_level(logging.WARNING, logger="daemon.services.watchover_service"):
            result = await svc.activate_watchover(
                "iid", requirement="r", user_context="prebuilt"
            )

        assert result["watchover_enabled"] is True
        assert result["quiescent"] is False

        # M3 — warning was emitted with the post-pause context.
        warning_records = [
            r for r in caplog.records if r.levelno == logging.WARNING
        ]
        assert any(
            "post-pause barrier settled with timeout" in r.getMessage()
            for r in warning_records
        ), f"expected post-pause barrier warning, got: {[r.getMessage() for r in warning_records]}"

    @pytest.mark.asyncio
    async def test_activate_no_warning_when_quiescence_succeeds(self, caplog):
        """No M3 warning when the barrier returns True (success path)."""
        import logging

        manager = make_full_manager()
        # Default: wait_for_instance_quiescent returns True.
        manager.get_instance.return_value = make_mock_graph_state(messages=[])

        from daemon.services.watchover_service import WatchoverService

        svc = WatchoverService(manager)

        with caplog.at_level(logging.WARNING, logger="daemon.services.watchover_service"):
            result = await svc.activate_watchover("iid", requirement="r")

        assert result["quiescent"] is True
        warning_records = [
            r for r in caplog.records if r.levelno == logging.WARNING
        ]
        assert not any(
            "quiescence barrier timed out" in r.getMessage()
            for r in warning_records
        ), f"unexpected M3 warning on success path: {[r.getMessage() for r in warning_records]}"

    @pytest.mark.asyncio
    async def test_activate_rollback_emits_watchover_failed_sse(self):
        """M4 — rollback emits ``watchover_failed`` SSE so frontend is not stale."""
        manager = make_full_manager()
        manager.enable_watchover.side_effect = RuntimeError("write fail")
        manager.get_instance.return_value = make_mock_graph_state(messages=[])

        from daemon.services.watchover_service import WatchoverService

        svc = WatchoverService(manager)

        with pytest.raises(RuntimeError, match="write fail"):
            await svc.activate_watchover("iid", requirement="r")

        # Exactly one watchover_failed SSE was emitted (no
        # watchover_active on the failure path).
        sse_calls = manager._live_hub.stream_status_change.await_args_list
        failed = [c for c in sse_calls if c.args == ("iid", "watchover_failed")]
        active = [c for c in sse_calls if c.args == ("iid", "watchover_active")]
        assert len(failed) == 1
        assert len(active) == 0

    @pytest.mark.asyncio
    async def test_activate_rollback_sse_failure_does_not_mask_original(self):
        """M4 — even if the watchover_failed SSE emit also fails, the original error propagates."""
        manager = make_full_manager()
        manager.enable_watchover.side_effect = RuntimeError("write fail")
        manager._live_hub.stream_status_change.side_effect = RuntimeError(
            "sse boom"
        )
        manager.get_instance.return_value = make_mock_graph_state(messages=[])

        from daemon.services.watchover_service import WatchoverService

        svc = WatchoverService(manager)

        # Original ``write fail`` must propagate, NOT the SSE error.
        with pytest.raises(RuntimeError, match="write fail"):
            await svc.activate_watchover("iid", requirement="r")


# =============================================================================
# Pause-first ordering regression tests (M-2 in-flight hang fix)
# =============================================================================


class TestActivatePauseFirstOrdering:
    """Regression tests for the pause-first ordering fix (M-2 in-flight hang).

    The previous ordering ran ``wait_for_instance_quiescent`` (30s default)
    BEFORE pause. When the instance was mid-LLM-call the barrier blocked
    the full 30s, eating the route-level ``asyncio.wait_for(timeout=30)``
    budget and producing a 504 — and the user never saw the pause because
    pause came after the barrier. The fix swaps the order:
    ``pause → bounded_barrier(2s) → context → flag → resume``.

    These tests pin the new ordering, the in-flight completion speed, the
    rollback-on-cancel behavior, the 300s timeout, and the visible-pause
    contract.
    """

    @pytest.mark.asyncio
    async def test_activate_pause_first_then_barrier(self):
        """Pause is invoked BEFORE the bounded barrier; barrier uses timeout=2.0.

        Regression test for the M-2 in-flight hang. The old ordering
        invoked ``wait_for_instance_quiescent`` (30s default) FIRST,
        which blocked the route ceiling and produced a 504. The new
        ordering invokes pause FIRST (cancels the in-flight task),
        then a 2s bounded barrier just to confirm cancellation
        settled.
        """
        manager = make_full_manager()
        manager.get_instance.return_value = make_mock_graph_state(messages=[])

        from daemon.services.watchover_service import WatchoverService

        svc = WatchoverService(manager)

        call_order: list[str] = []

        # Track call order via a sentinel list — each mock appends on
        # entry. We use a synchronous wrapper (side_effect) so the
        # append runs before the await returns.
        orig_pause = manager.pause_instance_cascade.side_effect

        def _track_pause(*args, **kwargs):
            call_order.append("pause")
            if orig_pause is not None:
                return orig_pause(*args, **kwargs)
            return None

        def _track_barrier(*args, **kwargs):
            call_order.append("barrier")
            return True

        manager.pause_instance_cascade.side_effect = _track_pause
        manager.wait_for_instance_quiescent.side_effect = _track_barrier

        result = await svc.activate_watchover(
            "iid", requirement="r", user_context="prebuilt"
        )

        # Success metrics.
        assert result["watchover_enabled"] is True
        assert result["quiescent"] is True

        # Ordering: pause runs BEFORE barrier.
        assert call_order.index("pause") < call_order.index("barrier"), (
            f"Expected pause before barrier, got order: {call_order}"
        )

        # Barrier uses the bounded 2s timeout — NOT the 30s default.
        manager.wait_for_instance_quiescent.assert_awaited_once_with(
            "iid", timeout=2.0
        )

    @pytest.mark.asyncio
    async def test_in_flight_completes_under_ceiling(self):
        """A long in-flight graph task does NOT block the activation.

        The previous ordering blocked the full 30s quiescence barrier
        on a running task, eating the 30s route ceiling and producing
        a 504. The fix cancels the task via pause, so activation
        returns near-instantly regardless of how long the in-flight
        task would have taken.

        This is the core regression test for the bug — it asserts
        that even with a ``not done`` task mock, ``activate_watchover``
        completes in well under the 330s ceiling (the test asserts
        < 5s, which is generous).
        """
        import asyncio
        import time

        manager = make_full_manager()
        manager.get_instance.return_value = make_mock_graph_state(messages=[])

        from daemon.services.watchover_service import WatchoverService

        # Simulate an in-flight task: the barrier returns False (the
        # task would have taken the full 30s in the old ordering).
        # Crucially, the pause call is what makes the activation
        # return — pause is a mock that resolves immediately.
        manager.wait_for_instance_quiescent.return_value = False

        svc = WatchoverService(manager)

        start = time.monotonic()
        result = await svc.activate_watchover(
            "iid", requirement="r", user_context="prebuilt"
        )
        elapsed = time.monotonic() - start

        # Activation completes near-instantly despite the in-flight
        # task. The previous ordering would have hung for ~30s here.
        assert elapsed < 5.0, (
            f"Activation took {elapsed:.2f}s — pause-first ordering "
            f"not effective. Barrier should be bounded to 2s and pause "
            f"should cancel the in-flight task immediately."
        )
        assert result["watchover_enabled"] is True

        # Pause ran (it cancelled the task) — without pause-first,
        # the barrier would have blocked the full 30s.
        manager.pause_instance_cascade.assert_awaited_once_with(
            "iid", suspension_reason="watchover_setup"
        )

    @pytest.mark.asyncio
    async def test_rollback_runs_on_cancellation(self):
        """When the context build is cancelled (e.g. route wait_for timeout), rollback still runs.

        Regression test for the Change 4 fix. The route layer wraps
        activation in ``asyncio.wait_for(timeout=330)`` and on timeout
        cancels the inner task with ``CancelledError``. The rollback
        MUST fire (clear flags + resume + SSE) so the instance is
        never left PAUSED with no recovery path.

        The previous rollback had ``except Exception`` on the nested
        sub-steps (clear-flags, resume, SSE). On Python 3.13+,
        ``CancelledError`` is a ``BaseException`` subclass and was
        NOT caught — rollback was skipped.
        """
        import asyncio

        manager = make_full_manager()
        manager.get_instance.return_value = make_mock_graph_state(messages=[])

        from daemon.services.watchover_service import WatchoverService
        from daemon.services.watcher_context_builder import (
            WatcherContextBuilder,
        )

        # Simulate the route's wait_for timeout canceling the inner
        # task DURING the context build. The builder raises
        # CancelledError on its async await.
        with patch.object(
            WatcherContextBuilder,
            "build",
            AsyncMock(side_effect=asyncio.CancelledError()),
        ):
            svc = WatchoverService(manager)

            with pytest.raises(asyncio.CancelledError):
                await svc.activate_watchover(
                    "iid", requirement="r", user_context=None
                )

        # Change 4 fix: rollback fired even after CancelledError.
        # (1) Flags cleared.
        manager.set_metadata_many.assert_called_once()
        updates = manager.set_metadata_many.call_args.args[1]
        assert updates["watchover_enabled"] is False
        assert updates["watchover_context"] is None
        assert updates["watchover_requirement"] is None
        assert updates["watchover_transition"] == "rollback"

        # (2) Best-effort resume in rollback.
        manager.resume_instance_cascade.assert_awaited_once_with("iid")

        # (3) watchover_failed SSE emitted.
        sse_calls = manager._live_hub.stream_status_change.await_args_list
        rollback_sse = [
            c for c in sse_calls if c.args == ("iid", "watchover_failed")
        ]
        assert len(rollback_sse) == 1, (
            f"Expected rollback SSE, got: {sse_calls}"
        )

    @pytest.mark.asyncio
    async def test_context_build_timeout_is_300(self):
        """The WatcherContextBuilder is constructed with timeout_seconds=300.

        Regression test for the Change 2 fix. The previous default was
        15s — too short for long devops/ops conversations. The new
        default is 300s (and the route's wait_for ceiling is 330s).
        """
        manager = make_full_manager()
        manager.get_instance.return_value = make_mock_graph_state(
            messages=[_msg("hi")] * 5
        )

        from daemon.services.watchover_service import WatchoverService
        from daemon.services.watcher_context_builder import (
            WatcherContextBuilder,
        )

        # Capture the constructor arguments.
        captured: dict = {}

        real_init = WatcherContextBuilder.__init__

        def capturing_init(self, *args, **kwargs):
            captured.update(kwargs)
            captured["args"] = args
            # Do not call real __init__ — we only need the kwargs.

        with patch.object(
            WatcherContextBuilder, "__init__", capturing_init
        ):
            svc = WatchoverService(manager)
            await svc.activate_watchover("iid", requirement="r")

        # The timeout_seconds keyword passed to the builder is 300.
        assert captured.get("timeout_seconds") == 300, (
            f"Expected timeout_seconds=300, got {captured.get('timeout_seconds')!r}. "
            f"Full kwargs: {captured}"
        )

    @pytest.mark.asyncio
    async def test_instance_paused_during_activation(self):
        """The instance is visibly PAUSED while the context snapshot is being built.

        This is the user-facing contract: when ``activate_watchover``
        is called, the user should IMMEDIATELY see the instance flip
        to ``PAUSED`` (the bug fix for the M-2 "no visible pause"
        report). The previous ordering ran the barrier first, so the
        pause was not visible until 30s later — by which point the
        route had timed out.

        Assertion: while ``_build_watchover_context`` is running, the
        instance MUST already be PAUSED. We capture the instance
        status from inside the builder's side_effect.
        """
        import asyncio

        manager = make_full_manager()
        manager.get_instance.return_value = make_mock_graph_state(
            messages=[_msg("hi")] * 5
        )

        # The manager's status getter is what the test asserts on.
        # We use a side_effect on pause_instance_cascade to capture
        # the state at the moment the lifecycle reads the instance.
        captured: dict = {}

        real_pause = manager.pause_instance_cascade

        async def capture_pause(*args, **kwargs):
            # While pause is being invoked, the lifecycle has NOT yet
            # reached the context build. The user-visible state is
            # this: the instance is mid-pause. After pause returns,
            # the lifecycle proceeds to the context build where the
            # instance is stably PAUSED.
            captured["pause_called"] = True
            captured["pause_args"] = args
            captured["pause_kwargs"] = kwargs
            # The pause function itself returns; the caller
            # (lifecycle) immediately proceeds to the barrier then
            # the context build. We use a side_effect on the builder
            # to assert the state at THAT moment.
            return None

        manager.pause_instance_cascade.side_effect = capture_pause

        from daemon.services.watchover_service import WatchoverService
        from daemon.services.watcher_context_builder import (
            WatcherContextBuilder,
        )

        # The builder's side_effect captures the state at the moment
        # the context build runs. By then, pause has already returned,
        # so the instance is stably PAUSED.
        builder_status_capture: dict = {}

        async def capture_build(messages, requirement, **kwargs):
            # At this point in the lifecycle:
            #   1. pause_instance_cascade has returned.
            #   2. wait_for_instance_quiescent has returned.
            #   3. The instance is PAUSED (pause sets the state).
            # The capture is best-effort — we record pause_called
            # as evidence that pause ran BEFORE the build.
            builder_status_capture["pause_called_before_build"] = (
                captured.get("pause_called", False)
            )
            return "## Allowed\n- test\n\n## Forbidden\n- test"

        with patch.object(
            WatcherContextBuilder, "build", side_effect=capture_build
        ):
            svc = WatchoverService(manager)
            result = await svc.activate_watchover(
                "iid", requirement="r", user_context=None
            )

        # The user-facing contract: pause ran BEFORE the context
        # build. The instance is visibly PAUSED while the snapshot
        # is being built.
        assert result["watchover_enabled"] is True
        assert builder_status_capture["pause_called_before_build"] is True, (
            "Pause must be called BEFORE the context build — the user "
            "needs to see the instance flip to PAUSED immediately, "
            "not 30s later after the quiescence barrier."
        )
        # And pause used the WATCHOVER_SETUP suspension reason.
        assert captured["pause_kwargs"].get("suspension_reason") == (
            "watchover_setup"
        )


# =============================================================================
# T3.6 — WatchoverService.deactivate_watchover
# =============================================================================


class TestDeactivateWatchover:
    """Deactivation lifecycle: pause → clear flag → resume."""

    @pytest.mark.asyncio
    async def test_deactivate_runs_correct_sequence(self):
        """Pause → disable → resume in order; SSE emitted."""
        manager = make_full_manager()

        from daemon.services.watchover_service import WatchoverService

        svc = WatchoverService(manager)

        result = await svc.deactivate_watchover("iid")

        assert result["watchover_enabled"] is False
        assert result["instance_id"] == "iid"

        manager.pause_instance_cascade.assert_awaited_once_with(
            "iid", suspension_reason=None
        )
        manager.disable_watchover.assert_called_once_with("iid")
        manager.resume_instance_cascade.assert_awaited_once_with("iid")
        manager._live_hub.stream_status_change.assert_awaited_once_with(
            "iid", "watchover_inactive"
        )

    @pytest.mark.asyncio
    async def test_deactivate_propagates_pause_failure(self):
        """Pause failure → raise; no rollback needed (no partial state)."""
        manager = make_full_manager()
        manager.pause_instance_cascade.side_effect = RuntimeError("pause fail")

        from daemon.services.watchover_service import WatchoverService

        svc = WatchoverService(manager)

        with pytest.raises(RuntimeError, match="pause fail"):
            await svc.deactivate_watchover("iid")

        manager.disable_watchover.assert_not_called()

    @pytest.mark.asyncio
    async def test_deactivate_propagates_disable_failure(self):
        """Disable failure → raise + best-effort resume in rollback (H2)."""
        manager = make_full_manager()
        manager.disable_watchover.side_effect = RuntimeError("disable fail")

        from daemon.services.watchover_service import WatchoverService

        svc = WatchoverService(manager)

        with pytest.raises(RuntimeError, match="disable fail"):
            await svc.deactivate_watchover("iid")

        # H2 — when disable raises, the rollback block attempts a
        # best-effort resume so the instance is never left PAUSED.
        manager.resume_instance_cascade.assert_awaited_once_with("iid")

    @pytest.mark.asyncio
    async def test_deactivate_failure_resumes_instance(self):
        """H2 full coverage: disable raises → resume still attempted.

        Per the task spec, this covers both sub-scenarios:

          (a) disable raises → rollback attempts resume (best-effort)
              → original error re-raised.
          (b) disable succeeds but resume raises → rollback attempts
              resume AGAIN → original resume error re-raised.

        Both must leave the instance resume-attempted; neither must
        suppress the original error.
        """
        # ---- Sub-scenario (a): disable raises ----
        manager_a = make_full_manager()
        manager_a.disable_watchover.side_effect = RuntimeError("disable fail")

        from daemon.services.watchover_service import WatchoverService

        svc_a = WatchoverService(manager_a)

        with pytest.raises(RuntimeError, match="disable fail"):
            await svc_a.deactivate_watchover("iid")

        # pause ran (1x), then disable raised, then rollback resume ran (1x).
        manager_a.pause_instance_cascade.assert_awaited_once_with(
            "iid", suspension_reason=None
        )
        manager_a.resume_instance_cascade.assert_awaited_once_with("iid")

        # ---- Sub-scenario (b): disable succeeds but resume raises ----
        manager_b = make_full_manager()
        manager_b.resume_instance_cascade.side_effect = RuntimeError(
            "resume fail"
        )

        svc_b = WatchoverService(manager_b)

        with pytest.raises(RuntimeError, match="resume fail"):
            await svc_b.deactivate_watchover("iid")

        # pause ran (1x), disable ran (1x), resume raised, rollback
        # resume ran AGAIN (best-effort, also fails) → total 2.
        manager_b.pause_instance_cascade.assert_awaited_once_with(
            "iid", suspension_reason=None
        )
        manager_b.disable_watchover.assert_called_once_with("iid")
        assert manager_b.resume_instance_cascade.await_count == 2


# =============================================================================
# T3.5 / T3.6 facade — manager.enable/disable_watchover_lifecycle
# =============================================================================


class TestManagerLifecycleFacade:
    """``manager.enable_watchover_lifecycle`` / ``disable_watchover_lifecycle``
    are thin facade methods over the WatchoverService."""

    @pytest.mark.asyncio
    async def test_enable_lifecycle_delegates_to_service(self):
        manager = make_bare_manager()
        # Stub the service.
        manager._watchover_service = MagicMock()
        manager._watchover_service.activate_watchover = AsyncMock(
            return_value={"instance_id": "iid", "watchover_enabled": True}
        )

        result = await manager.enable_watchover_lifecycle(
            "iid", requirement="r", user_context="c"
        )

        manager._watchover_service.activate_watchover.assert_awaited_once_with(
            "iid",
            requirement="r",
            user_context="c",
            resume_message=None,
        )
        assert result["watchover_enabled"] is True

    @pytest.mark.asyncio
    async def test_enable_lifecycle_threads_resume_message(self):
        """``resume_message`` is forwarded to the service. (resume-doesn't-restart-graph fix)"""
        manager = make_bare_manager()
        manager._watchover_service = MagicMock()
        manager._watchover_service.activate_watchover = AsyncMock(
            return_value={"instance_id": "iid", "watchover_enabled": True}
        )

        await manager.enable_watchover_lifecycle(
            "iid", requirement="r", user_context="c", resume_message="go"
        )

        manager._watchover_service.activate_watchover.assert_awaited_once_with(
            "iid",
            requirement="r",
            user_context="c",
            resume_message="go",
        )

    @pytest.mark.asyncio
    async def test_disable_lifecycle_delegates_to_service(self):
        manager = make_bare_manager()
        manager._watchover_service = MagicMock()
        manager._watchover_service.deactivate_watchover = AsyncMock(
            return_value={"instance_id": "iid", "watchover_enabled": False}
        )

        result = await manager.disable_watchover_lifecycle("iid")

        manager._watchover_service.deactivate_watchover.assert_awaited_once_with("iid")
        assert result["watchover_enabled"] is False


# =============================================================================
# T3.7 — POST /instances/{id}/watchover endpoint
# =============================================================================


class TestWatchoverEndpoint:
    """The ``POST /instances/{id}/watchover`` endpoint."""

    @pytest.mark.asyncio
    async def test_enable_calls_enable_lifecycle(self):
        """``{enabled: true, requirement: 'r'}`` → enable_watchover_lifecycle."""
        from daemon.routers.instances import (
            WatchoverRequest,
            toggle_watchover,
        )
        from fastapi import HTTPException

        manager = MagicMock()
        manager.is_write_paused = False
        manager.get_instance = AsyncMock()
        manager.enable_watchover_lifecycle = AsyncMock(
            return_value={"instance_id": "iid", "watchover_enabled": True}
        )

        body = WatchoverRequest(enabled=True, requirement="r")
        request = MagicMock()
        request.app.state.manager = manager

        result = await toggle_watchover("iid", body, request)

        assert result["watchover_enabled"] is True
        assert result["instance_id"] == "iid"
        manager.enable_watchover_lifecycle.assert_awaited_once_with(
            "iid",
            requirement="r",
            user_context=None,
            resume_message=None,
        )

    @pytest.mark.asyncio
    async def test_disable_calls_disable_lifecycle(self):
        """``{enabled: false}`` → disable_watchover_lifecycle."""
        from daemon.routers.instances import (
            WatchoverRequest,
            toggle_watchover,
        )

        manager = MagicMock()
        manager.is_write_paused = False
        manager.get_instance = AsyncMock()
        manager.disable_watchover_lifecycle = AsyncMock(
            return_value={"instance_id": "iid", "watchover_enabled": False}
        )

        body = WatchoverRequest(enabled=False)
        request = MagicMock()
        request.app.state.manager = manager

        result = await toggle_watchover("iid", body, request)

        assert result["watchover_enabled"] is False
        manager.disable_watchover_lifecycle.assert_awaited_once_with("iid")

    @pytest.mark.asyncio
    async def test_404_when_instance_missing(self):
        """``get_instance`` raises ``KeyError`` → 404 with ErrorResponse shape."""
        from daemon.routers.instances import (
            WatchoverRequest,
            toggle_watchover,
        )
        from fastapi import HTTPException

        manager = MagicMock()
        manager.is_write_paused = False
        manager.get_instance = AsyncMock(side_effect=KeyError("missing"))

        body = WatchoverRequest(enabled=True, requirement="r")
        request = MagicMock()
        request.app.state.manager = manager

        with pytest.raises(HTTPException) as exc_info:
            await toggle_watchover("missing", body, request)

        assert exc_info.value.status_code == 404
        assert exc_info.value.detail["code"] == "INSTANCE_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_503_when_writes_paused(self):
        """``is_write_paused=True`` → 503."""
        from daemon.routers.instances import (
            WatchoverRequest,
            toggle_watchover,
        )
        from fastapi import HTTPException

        manager = MagicMock()
        manager.is_write_paused = True

        body = WatchoverRequest(enabled=True, requirement="r")
        request = MagicMock()
        request.app.state.manager = manager

        with pytest.raises(HTTPException) as exc_info:
            await toggle_watchover("iid", body, request)

        assert exc_info.value.status_code == 503

    @pytest.mark.asyncio
    async def test_enable_with_explicit_context(self):
        """When ``context`` is supplied on the body, it is forwarded as ``user_context``."""
        from daemon.routers.instances import (
            WatchoverRequest,
            toggle_watchover,
        )

        manager = MagicMock()
        manager.is_write_paused = False
        manager.get_instance = AsyncMock()
        manager.enable_watchover_lifecycle = AsyncMock(
            return_value={"instance_id": "iid", "watchover_enabled": True}
        )

        body = WatchoverRequest(
            enabled=True, requirement="r", context="pre-built ctx"
        )
        request = MagicMock()
        request.app.state.manager = manager

        await toggle_watchover("iid", body, request)

        manager.enable_watchover_lifecycle.assert_awaited_once_with(
            "iid",
            requirement="r",
            user_context="pre-built ctx",
            resume_message=None,
        )

    @pytest.mark.asyncio
    async def test_enable_forwards_resume_message(self):
        """``resume_message`` on the body is forwarded to the lifecycle. (resume-doesn't-restart-graph fix)"""
        from daemon.routers.instances import (
            WatchoverRequest,
            toggle_watchover,
        )

        manager = MagicMock()
        manager.is_write_paused = False
        manager.get_instance = AsyncMock()
        manager.enable_watchover_lifecycle = AsyncMock(
            return_value={"instance_id": "iid", "watchover_enabled": True}
        )

        body = WatchoverRequest(
            enabled=True, requirement="r", resume_message="custom"
        )
        request = MagicMock()
        request.app.state.manager = manager

        await toggle_watchover("iid", body, request)

        manager.enable_watchover_lifecycle.assert_awaited_once_with(
            "iid",
            requirement="r",
            user_context=None,
            resume_message="custom",
        )


# =============================================================================
# Resume-doesn't-restart-graph fix (2026-08-07)
# =============================================================================
#
# Bug: ``watchover_service.activate_watchover`` (and
# ``deactivate_watchover``) called only
# ``manager.resume_instance_cascade`` — which only flips DB status
# PAUSED → RUNNING. The graph re-trigger is the SEPARATE
# ``manager.resume_processing_job`` call. Every other resume path in
# the codebase pairs both calls (``/resume``, ``/answer``,
# ``/question/dismiss``, ``POST /messages``-on-paused). The watchover
# resume was the lone exception, leaving the instance in ``RUNNING``
# with no Task being processed.
#
# Fix: a shared helper ``_resume_with_graph_restart`` pairs the
# cascade with the graph re-trigger, falls back to
# ``enqueue_message`` when ``resume_processing_job`` returns None,
# and dedupes against a pending "continue" already in the message
# queue so a double-tap does not enqueue a second copy of the same
# message.


def _queue_msg(content: str) -> MagicMock:
    """Build a mock ``MessageQueue`` row with ``.content``."""
    msg = MagicMock()
    msg.content = content
    return msg


class TestResumeWithGraphRestart:
    """The ``_resume_with_graph_restart`` helper at all 4 watchover resume sites."""

    @pytest.mark.asyncio
    async def test_activate_success_calls_resume_processing_job_after_cascade(self):
        """The helper calls ``resume_processing_job`` AFTER the cascade.

        Regression test for the resume-doesn't-restart-graph bug —
        the watchover resume previously called only the cascade,
        leaving the instance in RUNNING with no Task being
        processed. The fix pairs both calls so the graph actually
        restarts.
        """
        manager = make_full_manager()
        manager.get_instance.return_value = make_mock_graph_state(messages=[])

        # Shared ordering signal: side_effect on both mocks appends
        # to a single list in the order the methods are awaited. This
        # gives us a real ordering assertion (the previous
        # ``rpj_call_order`` snippet only concatenated
        # ``await_args_list`` and never asserted on it — a regression
        # that called ``resume_processing_job`` BEFORE the cascade
        # would still pass). We keep the mocks' default return values
        # so the success path is otherwise unchanged.
        call_order: list[str] = []

        async def _track_cascade(*args, **kwargs):
            call_order.append("cascade")
            return {"target_id": "iid", "resumed_ids": ["iid"], "skipped_ids": []}

        async def _track_rpj(*args, **kwargs):
            call_order.append("rpj")
            return {"status": "resumed", "instance_id": "iid"}

        manager.resume_instance_cascade.side_effect = _track_cascade
        manager.resume_processing_job.side_effect = _track_rpj

        from daemon.services.watchover_service import WatchoverService

        svc = WatchoverService(manager)
        await svc.activate_watchover("iid", requirement="r", user_context="prebuilt")

        # Cascade was called exactly once.
        manager.resume_instance_cascade.assert_awaited_once_with("iid")
        # Graph re-trigger was called exactly once.
        manager.resume_processing_job.assert_awaited_once()
        # SSE was emitted last (on success).
        assert manager._live_hub.stream_status_change.await_count == 1
        # Real ordering assertion: cascade must precede resume_processing_job.
        # If a regression causes the helper to call resume_processing_job
        # before the cascade, ``call_order.index("rpj")`` will be < the
        # cascade index and this assertion fails.
        assert call_order.index("cascade") < call_order.index("rpj"), (
            f"resume_processing_job must be called AFTER resume_instance_cascade; "
            f"actual call order: {call_order}"
        )

    @pytest.mark.asyncio
    async def test_activate_success_target_gets_continue_children_get_resume(self):
        """Target instance gets the resume_message; cascade children resume silently.

        Mirrors the fan-out in ``POST /resume`` and ``POST /answer``:
        target gets the user-supplied message (or ``"continue"``
        default), children get the fixed token ``"resume"`` and
        ``silent=True`` so their checkpoint is replayed without a
        new message.
        """
        manager = make_full_manager()
        # Cascade returns 3 resumed instances — 1 target + 2 children.
        manager.resume_instance_cascade.return_value = {
            "target_id": "iid",
            "resumed_ids": ["iid", "child-1", "child-2"],
            "skipped_ids": [],
        }
        manager.get_instance.return_value = make_mock_graph_state(messages=[])

        from daemon.services.watchover_service import WatchoverService

        svc = WatchoverService(manager)
        await svc.activate_watchover("iid", requirement="r", user_context="prebuilt")

        # 3 instances resumed → 3 calls to resume_processing_job.
        assert manager.resume_processing_job.await_count == 3

        # Build a {(instance_id, message, silent): call} map.
        calls = {}
        for c in manager.resume_processing_job.await_args_list:
            # Args: (instance_id, message, silent) — but the
            # helper passes silent= as kwarg, so look at kwargs.
            args, kwargs = c.args, c.kwargs
            iid = args[0] if args else kwargs.get("instance_id")
            msg = args[1] if len(args) > 1 else kwargs.get("message")
            silent = kwargs.get("silent", False)
            calls[iid] = (msg, silent)

        # Target gets "continue" (default — no resume_message passed).
        assert calls["iid"] == ("continue", False)
        # Children get "resume" + silent=True.
        assert calls["child-1"] == ("resume", True)
        assert calls["child-2"] == ("resume", True)

    @pytest.mark.asyncio
    async def test_activate_success_target_gets_resume_message_when_provided(self):
        """``resume_message`` arg is forwarded to the target's resume_processing_job call.

        Threads through ``activate_watchover(..., resume_message=...)``:
        target receives the user-supplied string instead of the
        default ``"continue"``; children are unaffected.
        """
        manager = make_full_manager()
        manager.resume_instance_cascade.return_value = {
            "target_id": "iid",
            "resumed_ids": ["iid", "child-1"],
            "skipped_ids": [],
        }
        manager.get_instance.return_value = make_mock_graph_state(messages=[])

        from daemon.services.watchover_service import WatchoverService

        svc = WatchoverService(manager)
        await svc.activate_watchover(
            "iid",
            requirement="r",
            user_context="prebuilt",
            resume_message="proceed with care",
        )

        # Two resumed → two calls.
        assert manager.resume_processing_job.await_count == 2
        target_call = next(
            c for c in manager.resume_processing_job.await_args_list
            if (c.args[0] if c.args else c.kwargs.get("instance_id")) == "iid"
        )
        target_msg = (
            target_call.args[1]
            if len(target_call.args) > 1
            else target_call.kwargs.get("message")
        )
        assert target_msg == "proceed with care"

    @pytest.mark.asyncio
    async def test_activate_enqueue_fallback_when_resume_processing_job_returns_none(self):
        """When ``resume_processing_job`` returns None, ``enqueue_message`` fires.

        Mirrors the F10 ``api_resume_fallback`` path in
        ``daemon/routers/messages.py``: the selector returns no
        handle (e.g. WATCHOVER_SETUP did not persist a
        ``resume_target_turn_id`` because the task was still
        PENDING when pause fired). The fallback enqueues the
        message directly via the message queue so the user's
        intent is not silently dropped.
        """
        manager = make_full_manager()
        # Selector returns None → enqueue_message fallback path.
        manager.resume_processing_job.return_value = None
        manager.get_instance.return_value = make_mock_graph_state(messages=[])

        from daemon.services.watchover_service import WatchoverService

        svc = WatchoverService(manager)
        await svc.activate_watchover("iid", requirement="r", user_context="prebuilt")

        # resume_processing_job was called.
        manager.resume_processing_job.assert_awaited_once()
        # enqueue_message was called as the fallback.
        manager.enqueue_message.assert_awaited_once()
        enq_kwargs = manager.enqueue_message.call_args.kwargs
        assert enq_kwargs["message"] == "continue"
        assert enq_kwargs["source"] == "cascade_resume"

    @pytest.mark.asyncio
    async def test_activate_non_target_child_with_none_resume_skips_enqueue(self):
        """When ``resume_processing_job`` returns None for a NON-TARGET child,
        ``enqueue_message`` is NOT called for that child.

        Mirrors the ``if is_target:`` gate in
        ``daemon/routers/messages.py:248`` — the watchover resume
        helper must match that gate. Non-target cascade children are
        silent (§9.3 ``internal_child_noop`` contract in
        ``daemon/manager.py``); fabricating a Task for them by
        falling through to ``enqueue_message`` would violate the
        silent-resume contract. The target's fallback enqueue is
        preserved (the previous test's coverage); the fix is the
        non-target gate.
        """
        manager = make_full_manager()
        # Cascade returns 1 target + 2 children.
        manager.resume_instance_cascade.return_value = {
            "target_id": "iid",
            "resumed_ids": ["iid", "child-1", "child-2"],
            "skipped_ids": [],
        }
        # resume_processing_job returns None for ALL resumed
        # instances — exercises the fallback enqueue path on the
        # target and the silent-skip path on the children.
        manager.resume_processing_job.return_value = None
        manager.get_instance.return_value = make_mock_graph_state(messages=[])

        from daemon.services.watchover_service import WatchoverService

        svc = WatchoverService(manager)
        await svc.activate_watchover("iid", requirement="r", user_context="prebuilt")

        # resume_processing_job was called for all 3 instances
        # (target + 2 children).
        assert manager.resume_processing_job.await_count == 3
        # enqueue_message was called ONLY for the target. The two
        # non-target children that returned None must NOT have
        # triggered a fallback enqueue (silent-resume contract).
        manager.enqueue_message.assert_awaited_once()
        enq_kwargs = manager.enqueue_message.call_args.kwargs
        assert enq_kwargs["instance_id"] == "iid"
        assert enq_kwargs["message"] == "continue"
        assert enq_kwargs["source"] == "cascade_resume"
        # No enqueue_message call for child-1 or child-2.
        called_iids = [
            (c.args[0] if c.args else c.kwargs.get("instance_id"))
            for c in manager.enqueue_message.await_args_list
        ]
        assert "child-1" not in called_iids
        assert "child-2" not in called_iids

    @pytest.mark.asyncio
    async def test_activate_dedupes_against_pending_resume_message(self):
        """When a "continue" is already pending, the enqueue fallback is skipped.

        The duplicate-message prevention: if the message queue
        already has a "continue" (or "resume") pending for the
        instance, do NOT enqueue a second copy. The dedup check is
        case-insensitive substring match on the content.
        """
        manager = make_full_manager()
        manager.resume_processing_job.return_value = None
        # Pending message: a "continue" already in the queue.
        manager._queue_repository.list_pending.return_value = [
            _queue_msg("please continue from where you left off")
        ]
        manager.get_instance.return_value = make_mock_graph_state(messages=[])

        from daemon.services.watchover_service import WatchoverService

        svc = WatchoverService(manager)
        await svc.activate_watchover("iid", requirement="r", user_context="prebuilt")

        # resume_processing_job was called (it returned None).
        manager.resume_processing_job.assert_awaited_once()
        # enqueue_message was NOT called because a "continue" is
        # already pending — dedup hit.
        manager.enqueue_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_activate_dedup_against_resume_message_variant(self):
        """Dedup also catches a custom ``resume_message`` already in the queue.

        The candidate-message parameter is matched: a pending
        message containing the exact candidate string is treated
        as a duplicate even if it does not literally say
        "continue" or "resume".
        """
        manager = make_full_manager()
        manager.resume_processing_job.return_value = None
        manager._queue_repository.list_pending.return_value = [
            _queue_msg("proceed with care please")
        ]
        manager.get_instance.return_value = make_mock_graph_state(messages=[])

        from daemon.services.watchover_service import WatchoverService

        svc = WatchoverService(manager)
        await svc.activate_watchover(
            "iid",
            requirement="r",
            user_context="prebuilt",
            resume_message="proceed with care",
        )

        manager.enqueue_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_activate_enqueue_fires_when_no_pending_message(self):
        """No pending message → enqueue fallback runs as expected.

        Counterpart to the dedup test: when the queue is empty,
        the fallback enqueue is NOT blocked.
        """
        manager = make_full_manager()
        manager.resume_processing_job.return_value = None
        manager._queue_repository.list_pending.return_value = []  # empty
        manager.get_instance.return_value = make_mock_graph_state(messages=[])

        from daemon.services.watchover_service import WatchoverService

        svc = WatchoverService(manager)
        await svc.activate_watchover("iid", requirement="r", user_context="prebuilt")

        manager.enqueue_message.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_activate_dedup_failure_skips_dedup_not_resume(self):
        """When ``list_pending`` raises, dedup is skipped (resume proceeds).

        Conservative on query failure: a transient DB error must
        not block the resume. False negatives (skipping dedup
        when a pending message exists) are preferable to false
        positives (blocking a real resume because dedup cannot
        query).
        """
        manager = make_full_manager()
        manager.resume_processing_job.return_value = None
        manager._queue_repository.list_pending.side_effect = RuntimeError("db down")
        manager.get_instance.return_value = make_mock_graph_state(messages=[])

        from daemon.services.watchover_service import WatchoverService

        svc = WatchoverService(manager)
        await svc.activate_watchover("iid", requirement="r", user_context="prebuilt")

        # The dedup error was swallowed; the enqueue fallback
        # proceeded.
        manager.enqueue_message.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_activate_dedup_no_queue_repo_skips_dedup(self):
        """When ``_queue_repository`` is missing, dedup is skipped (no AttributeError).

        Defensive against a misconfigured manager: missing
        ``_queue_repository`` (None) must not crash the resume.
        """
        manager = make_full_manager()
        manager.resume_processing_job.return_value = None
        manager._queue_repository = None
        manager.get_instance.return_value = make_mock_graph_state(messages=[])

        from daemon.services.watchover_service import WatchoverService

        svc = WatchoverService(manager)
        # No exception raised.
        await svc.activate_watchover("iid", requirement="r", user_context="prebuilt")

        manager.enqueue_message.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_deactivate_success_calls_resume_processing_job(self):
        """Deactivation: helper pairs the cascade with resume_processing_job.

        Symmetric with the activation path — the bug applies to
        both the activation and deactivation lifecycle.
        """
        manager = make_full_manager()

        from daemon.services.watchover_service import WatchoverService

        svc = WatchoverService(manager)
        await svc.deactivate_watchover("iid")

        manager.resume_instance_cascade.assert_awaited_once_with("iid")
        manager.resume_processing_job.assert_awaited_once()
        # Deactivation target gets "continue" by default.
        target_call = manager.resume_processing_job.call_args
        msg = (
            target_call.args[1]
            if len(target_call.args) > 1
            else target_call.kwargs.get("message")
        )
        assert msg == "continue"

    @pytest.mark.asyncio
    async def test_activate_rollback_also_pairs_cascade_with_resume(self):
        """The rollback resume path also pairs both calls (not just success)."""
        manager = make_full_manager()
        manager.enable_watchover.side_effect = RuntimeError("write fail")
        manager.get_instance.return_value = make_mock_graph_state(messages=[])

        from daemon.services.watchover_service import WatchoverService

        svc = WatchoverService(manager)
        with pytest.raises(RuntimeError, match="write fail"):
            await svc.activate_watchover("iid", requirement="r")

        # Rollback: 1 cascade call + 1 resume_processing_job call.
        manager.resume_instance_cascade.assert_awaited_once_with("iid")
        manager.resume_processing_job.assert_awaited_once()


class TestResumeMessageFieldOnRequest:
    """The ``WatchoverRequest.resume_message`` field is accepted and forwarded."""

    @pytest.mark.asyncio
    async def test_watchover_request_accepts_resume_message(self):
        """The Pydantic field accepts a string up to 2000 chars."""
        from daemon.routers.instances import WatchoverRequest

        body = WatchoverRequest(
            enabled=True, requirement="r", resume_message="custom prompt"
        )
        assert body.resume_message == "custom prompt"

    @pytest.mark.asyncio
    async def test_watchover_request_resume_message_optional(self):
        """The Pydantic field defaults to None (backwards-compatible)."""
        from daemon.routers.instances import WatchoverRequest

        body = WatchoverRequest(enabled=True, requirement="r")
        assert body.resume_message is None

    @pytest.mark.asyncio
    async def test_watchover_request_resume_message_max_length(self):
        """The Pydantic field enforces the 2000-char cap (Pydantic ValidationError)."""
        from pydantic import ValidationError

        from daemon.routers.instances import WatchoverRequest

        with pytest.raises(ValidationError):
            WatchoverRequest(
                enabled=True, requirement="r", resume_message="x" * 2001
            )


# =============================================================================
# Message-shape helpers
# =============================================================================


def _msg(content: str, role: str = "user") -> MagicMock:
    """Build a mock message with ``.content``, ``.type``, ``.tool_calls``."""
    msg = MagicMock()
    msg.content = content
    msg.type = role
    msg.tool_calls = None
    return msg


def _system_msg(content: str) -> MagicMock:
    """Build a mock ``SystemMessage`` for compactor-returned summaries."""
    msg = MagicMock()
    msg.content = content
    msg.type = "system"
    return msg
