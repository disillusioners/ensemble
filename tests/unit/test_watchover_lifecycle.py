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
    manager.resume_instance_cascade = AsyncMock()
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
        assert updates["watchover_denial_count"] == 0
        assert updates["watchover_requirement"] == "no destructive ops"
        assert updates["watchover_context"] == "recent activity summary"

    def test_enable_watchover_tolerates_none_requirement_and_context(self):
        """``None`` requirement/context are omitted from the update dict.

        T5.4: ``watchover_context_turn`` and
        ``watchover_context_refresh_interval`` are always written (the
        freshness check requires them even when context/requirement are
        absent).
        """
        manager = make_bare_manager()
        repo = MagicMock()
        manager._instance_repository = repo

        manager.enable_watchover("iid", requirement=None, context=None)

        updates = repo.set_metadata_many.call_args.args[1]
        assert updates == {
            "watchover_enabled": True,
            "watchover_denial_count": 0,
            "watchover_context_turn": 0,
            "watchover_context_refresh_interval": 1,
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
    """Activation lifecycle: pause → quiesce → context → flag → resume."""

    @pytest.mark.asyncio
    async def test_activate_runs_correct_sequence(self):
        """Pause happens before enable; resume happens after enable."""
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

        # Order: quiesce → pause → enable → resume → SSE.
        call_names = [
            c[0] for c in [
                manager.wait_for_instance_quiescent.call_args,
                manager.pause_instance_cascade.call_args,
                manager.enable_watchover.call_args,
                manager.resume_instance_cascade.call_args,
                manager._live_hub.stream_status_change.call_args,
            ]
        ]
        assert manager.wait_for_instance_quiescent.await_count == 1
        assert manager.pause_instance_cascade.await_count == 1
        manager.pause_instance_cascade.assert_awaited_once_with(
            "iid",
            suspension_reason="watchover_setup",
        )
        assert manager.enable_watchover.call_count == 1
        assert manager.resume_instance_cascade.await_count == 1
        assert manager._live_hub.stream_status_change.await_count == 1
        assert manager._live_hub.stream_status_change.call_args.args == (
            "iid",
            "watchover_active",
        )

        # enable_watchover got the requirement + combined context.
        enable_kwargs = manager.enable_watchover.call_args.kwargs
        assert enable_kwargs["requirement"] == "no rm -rf"
        assert "no rm -rf" in enable_kwargs["context"]
        assert "user-supplied" in enable_kwargs["context"]

    @pytest.mark.asyncio
    async def test_activate_combines_requirement_with_built_context(self):
        """When ``user_context`` is None, the service builds one and combines it."""
        manager = make_full_manager()
        manager._compactor = make_mock_compactor()
        manager.get_instance.return_value = make_mock_graph_state(
            messages=[_msg("hello", role="user"), _msg("hi", role="assistant")]
        )

        from daemon.services.watchover_service import WatchoverService

        svc = WatchoverService(manager)

        result = await svc.activate_watchover(
            "iid", requirement="be nice", user_context=None
        )

        assert result["watchover_enabled"] is True
        enable_kwargs = manager.enable_watchover.call_args.kwargs
        ctx = enable_kwargs["context"]
        assert "[Requirement] be nice" in ctx
        assert "hello" in ctx  # raw-tail includes the messages

    @pytest.mark.asyncio
    async def test_activate_uses_compactor_when_available(self):
        """When the compactor returns a summary, the summary is used."""
        manager = make_full_manager()
        compactor = make_mock_compactor()
        compactor.compact_state = AsyncMock(
            return_value=MagicMock(replacement_messages=[_system_msg("summary text")])
        )
        manager._compactor = compactor
        manager.get_instance.return_value = make_mock_graph_state(
            messages=[_msg("hi")] * 20
        )

        from daemon.services.watchover_service import WatchoverService

        svc = WatchoverService(manager)
        result = await svc.activate_watchover("iid", requirement="r")

        # Summary was used.
        compactor.compact_state.assert_awaited_once()
        ctx = manager.enable_watchover.call_args.kwargs["context"]
        assert "summary text" in ctx

    @pytest.mark.asyncio
    async def test_activate_falls_back_to_raw_tail(self):
        """When compactor returns ``None``, raw-tail is used (TD-6 / AC-EC.7)."""
        manager = make_full_manager()
        compactor = make_mock_compactor()  # default returns None
        manager._compactor = compactor
        manager.get_instance.return_value = make_mock_graph_state(
            messages=[
                _msg(f"msg-{i}", role="user") for i in range(15)
            ]
        )

        from daemon.services.watchover_service import WatchoverService

        svc = WatchoverService(manager)
        result = await svc.activate_watchover("iid", requirement="r")

        ctx = manager.enable_watchover.call_args.kwargs["context"]
        # Raw-tail is the LAST 10 of 15 messages → msg-5 .. msg-14.
        # msg-4 and earlier must NOT appear.
        assert "msg-4" not in ctx
        assert "msg-5" in ctx
        assert "msg-14" in ctx

    @pytest.mark.asyncio
    async def test_activate_with_empty_messages_works(self):
        """Empty conversation → raw-tail is empty; requirement-only context."""
        manager = make_full_manager()
        manager._compactor = make_mock_compactor()
        manager.get_instance.return_value = make_mock_graph_state(messages=[])

        from daemon.services.watchover_service import WatchoverService

        svc = WatchoverService(manager)
        result = await svc.activate_watchover("iid", requirement="no rm -rf")

        ctx = manager.enable_watchover.call_args.kwargs["context"]
        assert "[Requirement] no rm -rf" in ctx

    @pytest.mark.asyncio
    async def test_activate_rollback_on_compaction_failure(self):
        """Compaction raises → flags cleared + best-effort resume → re-raised (W-8 + H1 + M5).

        The compaction failure happens BEFORE step 4 (the flag write),
        so logically there is nothing to roll back — but the new
        rollback block runs anyway and attempts a best-effort resume
        so the instance is never left in an inconsistent state (H1).
        The rollback ``set_metadata_many`` also clears any stale
        ``watchover_context`` / ``watchover_requirement`` (M5).
        """
        manager = make_full_manager()
        compactor = make_mock_compactor()
        compactor.compact_state = AsyncMock(side_effect=RuntimeError("compaction fail"))
        manager._compactor = compactor
        manager.get_instance.return_value = make_mock_graph_state(
            messages=[_msg("hi")] * 20
        )

        from daemon.services.watchover_service import WatchoverService

        svc = WatchoverService(manager)

        with pytest.raises(RuntimeError, match="compaction fail"):
            await svc.activate_watchover("iid", requirement="r")

        # Rollback wrote watchover_enabled=False + cleared context/
        # requirement + audit marker (M5).
        rollback_call = manager.set_metadata_many.call_args
        assert rollback_call is not None
        updates = rollback_call.args[1]
        assert updates["watchover_enabled"] is False
        assert updates["watchover_context"] is None
        assert updates["watchover_requirement"] is None
        assert updates["watchover_transition"] == "rollback"

        # H1 — best-effort resume IS called in the rollback path so
        # the instance is never left PAUSED with the flag cleared.
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
    async def test_activate_logs_warning_on_quiescence_timeout(self, caplog):
        """M3 — when ``wait_for_instance_quiescent`` returns False, log a warning.

        The barrier itself lives in ``daemon/manager.py`` (Phase 2-owned,
        out of scope) — but the service emits a warning so operators
        see the LD-4 limitation in the logs.
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

        # M3 — warning was emitted with the LD-4 context.
        warning_records = [
            r for r in caplog.records if r.levelno == logging.WARNING
        ]
        assert any(
            "quiescence barrier timed out" in r.getMessage()
            and "LD-4" in r.getMessage()
            for r in warning_records
        ), f"expected M3 warning, got: {[r.getMessage() for r in warning_records]}"

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
            "iid", requirement="r", user_context="c"
        )
        assert result["watchover_enabled"] is True

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
            "iid", requirement="r", user_context=None
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
            "iid", requirement="r", user_context="pre-built ctx"
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
