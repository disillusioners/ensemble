"""Regression tests for ``WatchoverService._recover_inflight_human_message``.

These tests cover the watchover-mid-flight context gap:

  When ``activate_watchover`` is invoked while an instance is RUNNING,
  ``pause_instance_cascade`` cancels the in-flight graph task at a node
  boundary. LangGraph only commits state at node boundaries, so the
  input ``HumanMessage`` for the current super-step is a PENDING WRITE
  that is rolled back when the cancel fires mid-``agent_node`` LLM
  call. The result: ``graph.aget_state().values["messages"]`` does NOT
  contain the message that triggered the current turn — exactly the
  message the watcher most needs to see.

The ``message_queue`` DB table is the reliable source. While a graph
task is running, its triggering message is in ``status == processing``
with ``type == human``; pause only cancels the graph task and flips
the instance to ``PAUSED`` — it does NOT delete the queue row.

The recovery helper reads those processing HUMAN rows and returns them
as ``HumanMessage`` objects ready to be appended to the conversation
the builder sees. This file covers:

  * Recovery returns the in-flight HumanMessage when checkpoint rolled
    back.
  * Dedup when the message content already matches the last human
    message in the checkpoint (rare race where the node committed
    before cancel fired).
  * Graceful degradation when the queue repository is missing.
  * Graceful degradation when the queue repository raises.
  * Filter: only rows with ``status == processing`` AND
    ``type == human`` are recovered; other rows ignored.
  * Integration into ``_build_watchover_context``: the builder path
    sees the extended ``messages`` list.

All tests are unit-level — no real DB, no real LLM, no real LangGraph
run. Mocks only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from daemon.services.watchover_service import WatchoverService


# =============================================================================
# Helpers
# =============================================================================


@dataclass
class _FakeQueueRow:
    """Drop-in for a ``MessageQueue`` ORM row."""

    content: str
    type: str = "human"
    status: str = "processing"


def _make_manager_with_repo(rows: list[_FakeQueueRow] | None) -> Any:
    """Build a manager mock with a ``_queue_repository`` attribute."""
    manager = MagicMock()
    if rows is None:
        # Simulate a manager that has no _queue_repository at all.
        del manager._queue_repository
    else:
        repo = MagicMock()
        repo.get_by_instance = MagicMock(return_value=rows)
        manager._queue_repository = repo
    return manager


def _make_service(manager: Any) -> WatchoverService:
    """Construct ``WatchoverService`` directly with a mock manager."""
    return WatchoverService(manager)


# =============================================================================
# Tests — _recover_inflight_human_message
# =============================================================================


class TestRecoverInflightHumanMessage:
    """Direct unit tests for the recovery helper."""

    async def test_recovers_inflight_human_when_checkpoint_missing_it(self):
        """The canonical bug case.

        Checkpoint rolled back the input HumanMessage (rolled back due to
        pause-cancel-mid-node); the message_queue still has the row in
        PROCESSING with type=human. The helper must return a HumanMessage
        mirroring that content.
        """
        in_flight_text = "Please deploy the v2 schema"
        manager = _make_manager_with_repo(
            [_FakeQueueRow(content=in_flight_text)]
        )
        svc = _make_service(manager)

        recovered = await svc._recover_inflight_human_message(
            instance_id="iid-1",
            checkpoint_messages=[],
        )

        assert len(recovered) == 1
        assert isinstance(recovered[0], HumanMessage)
        assert recovered[0].content == in_flight_text

    async def test_dedup_when_checkpoint_already_has_same_content(self):
        """If the message is already in the checkpoint, do not double-insert.

        Rare race: the node committed before cancel fired, so the
        HumanMessage is already in the checkpoint messages list. The
        helper must NOT return a duplicate.
        """
        shared_text = "Already-committed user message"
        manager = _make_manager_with_repo(
            [_FakeQueueRow(content=shared_text)]
        )
        svc = _make_service(manager)

        checkpoint = [HumanMessage(content=shared_text)]
        recovered = await svc._recover_inflight_human_message(
            instance_id="iid-1",
            checkpoint_messages=checkpoint,
        )

        assert recovered == []

    async def test_dedup_handles_multimodal_dict_messages(self):
        """Checkpoint may hold dicts (test mocks, snapshot paths) — dedup
        must still work via stringified content equality."""
        shared_text = "Dict-form human message"
        manager = _make_manager_with_repo(
            [_FakeQueueRow(content=shared_text)]
        )
        svc = _make_service(manager)

        checkpoint: list[Any] = [
            {"type": "human", "content": shared_text},
            {"type": "ai", "content": "ok"},
        ]
        recovered = await svc._recover_inflight_human_message(
            instance_id="iid-1",
            checkpoint_messages=checkpoint,
        )

        assert recovered == []

    async def test_recovers_when_only_earlier_human_in_checkpoint(self):
        """Earlier human + different in-flight human → recover.

        The dedup check is against the LATEST human in the checkpoint,
        not the earliest. A new human message mid-conversation must
        still be recovered even when an older human exists.
        """
        manager = _make_manager_with_repo(
            [_FakeQueueRow(content="Newest user request")]
        )
        svc = _make_service(manager)

        checkpoint = [
            HumanMessage(content="Older user request"),
            AIMessage(content="Old AI response"),
        ]
        recovered = await svc._recover_inflight_human_message(
            instance_id="iid-1",
            checkpoint_messages=checkpoint,
        )

        assert len(recovered) == 1
        assert recovered[0].content == "Newest user request"

    async def test_graceful_when_repo_missing(self):
        """No ``_queue_repository`` on manager → log warning, return empty.

        Activation must not crash on a misconfigured manager.
        """
        manager = _make_manager_with_repo(rows=None)
        svc = _make_service(manager)

        recovered = await svc._recover_inflight_human_message(
            instance_id="iid-1",
            checkpoint_messages=[],
        )

        assert recovered == []

    async def test_graceful_when_repo_raises(self):
        """DB hiccup in ``get_by_instance`` → log warning, return empty."""
        manager = MagicMock()
        repo = MagicMock()
        repo.get_by_instance = MagicMock(
            side_effect=RuntimeError("DB connection lost")
        )
        manager._queue_repository = repo
        svc = _make_service(manager)

        recovered = await svc._recover_inflight_human_message(
            instance_id="iid-1",
            checkpoint_messages=[],
        )

        assert recovered == []

    async def test_filters_non_processing_rows(self):
        """Only ``status == processing`` rows are eligible."""
        manager = _make_manager_with_repo(
            [
                _FakeQueueRow(
                    content="Old ready msg", status="ready"
                ),
                _FakeQueueRow(
                    content="Old completed msg", status="completed"
                ),
                _FakeQueueRow(
                    content="Active msg", status="processing"
                ),
            ]
        )
        svc = _make_service(manager)

        recovered = await svc._recover_inflight_human_message(
            instance_id="iid-1",
            checkpoint_messages=[],
        )

        assert len(recovered) == 1
        assert recovered[0].content == "Active msg"

    async def test_filters_non_human_rows(self):
        """Only ``type == human`` rows are eligible (skip agent/system/etc)."""
        manager = _make_manager_with_repo(
            [
                _FakeQueueRow(
                    content="Agent report", type="agent"
                ),
                _FakeQueueRow(
                    content="System note", type="system"
                ),
                _FakeQueueRow(
                    content="Real user request", type="human"
                ),
            ]
        )
        svc = _make_service(manager)

        recovered = await svc._recover_inflight_human_message(
            instance_id="iid-1",
            checkpoint_messages=[],
        )

        assert len(recovered) == 1
        assert recovered[0].content == "Real user request"

    async def test_skips_empty_content_rows(self):
        """Empty ``content`` rows must not produce empty HumanMessage."""
        manager = _make_manager_with_repo(
            [
                _FakeQueueRow(content=""),
                _FakeQueueRow(content="Real content"),
            ]
        )
        svc = _make_service(manager)

        recovered = await svc._recover_inflight_human_message(
            instance_id="iid-1",
            checkpoint_messages=[],
        )

        assert len(recovered) == 1
        assert recovered[0].content == "Real content"


# =============================================================================
# Tests — _build_watchover_context integration
# =============================================================================


class TestBuildContextWithInflightRecovery:
    """Verify the recovered message reaches the builder.

    The integration test exercises ``_build_watchover_context`` end-to-end
    with a mocked graph + builder. The graph checkpoint rolled back the
    in-flight HumanMessage; the queue still has it. The builder must
    observe the recovered message in its ``messages`` argument.
    """

    async def test_builder_sees_recovered_inflight_human_message(self):
        manager = MagicMock()
        # Mock graph + checkpoint state (checkpoint rolled back the input)
        graph = MagicMock()
        state = MagicMock()
        state.values = {
            "messages": [
                HumanMessage(content="Earlier user msg"),
                AIMessage(content="Earlier AI reply"),
            ]
        }
        graph.aget_state = AsyncMock(return_value=state)
        manager.get_instance = AsyncMock(return_value=graph)
        manager.config = MagicMock()
        manager.config.llm = MagicMock()
        manager.config.llm.api_key = "test"
        manager.config.llm.model = "test-model"
        manager.config.llm.model_vision = "test-model"
        manager.config.llm.temperature = 0.0
        manager.config.llm.request_timeout = 30

        # Queue repo with a row for the in-flight message that was rolled back.
        in_flight_text = "Trigger for current turn (rolled back)"
        repo = MagicMock()
        repo.get_by_instance = MagicMock(
            return_value=[_FakeQueueRow(content=in_flight_text)]
        )
        manager._queue_repository = repo

        # Mock the builder so we can capture the messages it receives.
        captured: dict[str, Any] = {}

        async def fake_build(messages, requirement):
            captured["messages"] = messages
            captured["requirement"] = requirement
            return "## Built context"

        with patch(
            "daemon.services.watcher_context_builder.WatcherContextBuilder"
        ) as BuilderCls:
            builder_instance = MagicMock()
            builder_instance.build = AsyncMock(side_effect=fake_build)
            BuilderCls.return_value = builder_instance

            svc = WatchoverService(manager)
            result = await svc._build_watchover_context(
                "iid-1", requirement=None
            )

        assert result == "## Built context"
        msgs = captured["messages"]
        # Last message in the list passed to the builder must be the
        # recovered in-flight human message.
        assert msgs[-1].content == in_flight_text
        assert isinstance(msgs[-1], HumanMessage)
        # Original checkpoint messages are preserved.
        assert msgs[0].content == "Earlier user msg"
        assert msgs[1].content == "Earlier AI reply"
        # Three messages total (2 from checkpoint + 1 recovered).
        assert len(msgs) == 3

    async def test_fallback_path_also_sees_recovered_message(self):
        """Belt-and-suspenders fallback (``except Exception`` branch)
        must also include the recovered message in its raw-tail view.
        """
        manager = MagicMock()
        graph = MagicMock()
        state = MagicMock()
        state.values = {"messages": []}  # empty checkpoint
        graph.aget_state = AsyncMock(return_value=state)
        manager.get_instance = AsyncMock(return_value=graph)
        manager.config = MagicMock()
        manager.config.llm = MagicMock()
        manager.config.llm.api_key = "test"
        manager.config.llm.model = "test-model"
        manager.config.llm.model_vision = "test-model"
        manager.config.llm.temperature = 0.0
        manager.config.llm.request_timeout = 30

        in_flight_text = "In-flight user request (fallback case)"
        repo = MagicMock()
        repo.get_by_instance = MagicMock(
            return_value=[_FakeQueueRow(content=in_flight_text)]
        )
        manager._queue_repository = repo

        svc = WatchoverService(manager)

        # Force the except branch: import the builder class so the
        # ``from daemon.services.watcher_context_builder import
        # WatcherContextBuilder`` succeeds, then raise inside ``build``.
        with patch(
            "daemon.services.watcher_context_builder.WatcherContextBuilder"
        ) as BuilderCls:
            builder_instance = MagicMock()
            builder_instance.build = AsyncMock(
                side_effect=RuntimeError("Builder unavailable")
            )
            BuilderCls.return_value = builder_instance

            result = await svc._build_watchover_context(
                "iid-1", requirement=None
            )

        # Fallback path renders the recovered message into the raw tail.
        assert in_flight_text in result


# =============================================================================
# Tests — _build_watchover_context extra_messages parameter
# =============================================================================
#
# Terminal-activation fix (2026-08-08):
#   ``_activate_terminal`` enqueues ``next_command`` AFTER building the
#   watchover context. To prevent the builder from running against an
#   outdated conversation the lifecycle now threads ``next_command``
#   through ``_build_watchover_context(extra_messages=...)``. These
#   tests verify the seam works end-to-end:
#
#     1. The builder sees the extra message as the LAST element in the
#        ``messages`` list it receives.
#     2. Ordering is preserved: checkpoint → recovered inflight → extra.
#     3. Default ``extra_messages=None`` preserves prior behavior.
#     4. The fallback (raw-tail) path also picks up extra messages
#        because the local ``messages`` list is extended BEFORE the
#        ``except Exception`` branch reads it.


class TestBuildContextWithExtraMessages:
    """Verify the ``extra_messages`` parameter is honored."""

    def _make_manager_with_graph_and_queue(
        self,
        checkpoint_messages: list,
        queue_rows: list[_FakeQueueRow] | None,
    ) -> Any:
        """Build a manager mock for ``_build_watchover_context`` tests.

        The manager has a graph whose ``aget_state`` returns
        ``checkpoint_messages`` and a queue repo that returns
        ``queue_rows`` (or no repo if ``queue_rows`` is ``None``).
        LLM config is wired so the builder path can run.
        """
        manager = MagicMock()
        graph = MagicMock()
        state = MagicMock()
        state.values = {"messages": list(checkpoint_messages)}
        graph.aget_state = AsyncMock(return_value=state)
        manager.get_instance = AsyncMock(return_value=graph)
        manager.config = MagicMock()
        manager.config.llm = MagicMock()
        manager.config.llm.api_key = "test"
        manager.config.llm.model = "test-model"
        manager.config.llm.model_vision = "test-model"
        manager.config.llm.temperature = 0.0
        manager.config.llm.request_timeout = 30
        if queue_rows is None:
            del manager._queue_repository
        else:
            repo = MagicMock()
            repo.get_by_instance = MagicMock(return_value=queue_rows)
            manager._queue_repository = repo
        return manager

    async def test_builder_sees_next_command_as_extra_message(self):
        """The ``extra_messages`` parameter is forwarded to the builder
        and the LAST element is the ``HumanMessage`` the caller passed.
        """
        manager = self._make_manager_with_graph_and_queue(
            checkpoint_messages=[
                HumanMessage(content="Earlier user msg"),
                AIMessage(content="Earlier AI reply"),
            ],
            queue_rows=None,
        )

        captured: dict[str, Any] = {}

        async def fake_build(messages, requirement):
            captured["messages"] = messages
            captured["requirement"] = requirement
            return "## Built context"

        with patch(
            "daemon.services.watcher_context_builder.WatcherContextBuilder"
        ) as BuilderCls:
            builder_instance = MagicMock()
            builder_instance.build = AsyncMock(side_effect=fake_build)
            BuilderCls.return_value = builder_instance

            svc = WatchoverService(manager)
            result = await svc._build_watchover_context(
                "iid-1",
                requirement=None,
                extra_messages=[HumanMessage(content="check high memory pods")],
            )

        assert result == "## Built context"
        msgs = captured["messages"]
        # Last message in the list passed to the builder is the
        # extra_messages entry.
        assert msgs[-1].content == "check high memory pods"
        assert isinstance(msgs[-1], HumanMessage)
        # Original checkpoint messages are preserved.
        assert msgs[0].content == "Earlier user msg"
        assert msgs[1].content == "Earlier AI reply"
        # Three messages total (2 from checkpoint + 1 extra).
        assert len(msgs) == 3

    async def test_extra_messages_appended_after_inflight_recovery(self):
        """Ordering is preserved: checkpoint → recovered → extra.

        This is the critical invariant for the terminal fix — the
        ``next_command`` (extra) MUST come AFTER the recovered in-flight
        message so the builder sees them in chronological order with
        ``next_command`` at the tail.
        """
        in_flight_text = "Trigger for current turn (rolled back)"
        next_command_text = "Now check high memory pods"
        manager = self._make_manager_with_graph_and_queue(
            checkpoint_messages=[
                HumanMessage(content="Earlier user msg"),
                AIMessage(content="Earlier AI reply"),
            ],
            queue_rows=[_FakeQueueRow(content=in_flight_text)],
        )

        captured: dict[str, Any] = {}

        async def fake_build(messages, requirement):
            captured["messages"] = messages
            return "## Built context"

        with patch(
            "daemon.services.watcher_context_builder.WatcherContextBuilder"
        ) as BuilderCls:
            builder_instance = MagicMock()
            builder_instance.build = AsyncMock(side_effect=fake_build)
            BuilderCls.return_value = builder_instance

            svc = WatchoverService(manager)
            await svc._build_watchover_context(
                "iid-1",
                requirement=None,
                extra_messages=[HumanMessage(content=next_command_text)],
            )

        msgs = captured["messages"]
        # 2 checkpoint + 1 recovered + 1 extra = 4 total.
        assert len(msgs) == 4
        # Checkpoint first.
        assert msgs[0].content == "Earlier user msg"
        assert msgs[1].content == "Earlier AI reply"
        # Recovered inflight next.
        assert msgs[2].content == in_flight_text
        assert isinstance(msgs[2], HumanMessage)
        # Extra (next_command) LAST — most recent in the tail window.
        assert msgs[3].content == next_command_text
        assert isinstance(msgs[3], HumanMessage)

    async def test_no_extra_messages_works_same_as_before(self):
        """Default ``extra_messages=None`` preserves prior behavior.

        Verifies the optional parameter is backward-compatible: callers
        that omit it (notably the running-path ``activate_watchover``
        call at watchover_service.py:1018) see the same message
        ordering they did before this change.
        """
        manager = self._make_manager_with_graph_and_queue(
            checkpoint_messages=[
                HumanMessage(content="Earlier user msg"),
                AIMessage(content="Earlier AI reply"),
            ],
            queue_rows=None,
        )

        captured: dict[str, Any] = {}

        async def fake_build(messages, requirement):
            captured["messages"] = messages
            return "## Built context"

        with patch(
            "daemon.services.watcher_context_builder.WatcherContextBuilder"
        ) as BuilderCls:
            builder_instance = MagicMock()
            builder_instance.build = AsyncMock(side_effect=fake_build)
            BuilderCls.return_value = builder_instance

            svc = WatchoverService(manager)
            # Call WITHOUT the extra_messages kwarg.
            await svc._build_watchover_context(
                "iid-1", requirement=None
            )

        msgs = captured["messages"]
        # Only the 2 checkpoint messages, no extra, no recovered.
        assert len(msgs) == 2
        assert msgs[0].content == "Earlier user msg"
        assert msgs[1].content == "Earlier AI reply"

    async def test_fallback_path_includes_extra_messages(self):
        """The raw-tail fallback also includes the extra_messages.

        When the builder raises (or is unavailable) the local
        ``messages`` list — which has been extended with
        ``extra_messages`` — is fed into the raw-tail formatter. This
        test forces the ``except Exception`` branch and asserts the
        extra content surfaces in the rendered fallback.
        """
        manager = self._make_manager_with_graph_and_queue(
            checkpoint_messages=[],
            queue_rows=None,
        )

        svc = WatchoverService(manager)

        next_command_text = "next command raw tail fallback"
        # Force the except branch: raise inside ``build``.
        with patch(
            "daemon.services.watcher_context_builder.WatcherContextBuilder"
        ) as BuilderCls:
            builder_instance = MagicMock()
            builder_instance.build = AsyncMock(
                side_effect=RuntimeError("Builder unavailable")
            )
            BuilderCls.return_value = builder_instance

            result = await svc._build_watchover_context(
                "iid-1",
                requirement=None,
                extra_messages=[HumanMessage(content=next_command_text)],
            )

        # Fallback path renders the extra message into the raw tail.
        assert next_command_text in result

    async def test_empty_extra_messages_list_is_noop(self):
        """An empty list (``extra_messages=[]``) is treated as no-op.

        The implementation guards on truthiness (``if extra_messages:``)
        so an empty list skips the extend — matches the ``None`` default
        behavior exactly.
        """
        manager = self._make_manager_with_graph_and_queue(
            checkpoint_messages=[
                HumanMessage(content="only checkpoint msg"),
            ],
            queue_rows=None,
        )

        captured: dict[str, Any] = {}

        async def fake_build(messages, requirement):
            captured["messages"] = messages
            return "## Built context"

        with patch(
            "daemon.services.watcher_context_builder.WatcherContextBuilder"
        ) as BuilderCls:
            builder_instance = MagicMock()
            builder_instance.build = AsyncMock(side_effect=fake_build)
            BuilderCls.return_value = builder_instance

            svc = WatchoverService(manager)
            await svc._build_watchover_context(
                "iid-1",
                requirement=None,
                extra_messages=[],
            )

        msgs = captured["messages"]
        # Only the 1 checkpoint message; empty list = no extension.
        assert len(msgs) == 1
        assert msgs[0].content == "only checkpoint msg"
