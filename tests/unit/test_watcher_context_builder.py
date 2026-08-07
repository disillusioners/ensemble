"""Tests for the Watcher Context Builder (Phase 4 / 2026-08-07).

Covers:

  * **WatcherContextBuilder.build** — markdown guardrail production,
    requirement forwarding, available_tools in payload.
  * **Fallback chain** — raw-tail + static guardrail when the LLM
    times out / fails / returns empty.
  * **JSON payload shape** — message_window, requirement,
    available_tools are serialized correctly.

The builder is exercised through its public API. The LLM is mocked
via ``daemon.graph.ThinkingChatOpenAI`` (the same pattern as the
:mod:`tests.unit.test_watchover_decision` evaluator tests).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from daemon.services.watcher_context_builder import (
    DEFAULT_BUILDER_MESSAGE_WINDOW,
    DEFAULT_BUILDER_TIMEOUT_SECONDS,
    WatcherContextBuilder,
)


# =============================================================================
# Helpers
# =============================================================================


def _make_fake_llm_class(
    responses: list[Any] | None = None,
    side_effect: Any = None,
):
    """Build a ``ThinkingChatOpenAI`` factory mock with a queued response list.

    Mirrors the pattern in :mod:`tests.unit.test_watchover_decision`.
    """
    if responses is None:
        responses = []
    queue = list(responses)

    def _next(_messages):
        if not queue:
            raise AssertionError("LLM mock exhausted")
        item = queue.pop(0)
        if isinstance(item, BaseException) or (
            isinstance(item, type) and issubclass(item, BaseException)
        ):
            raise item
        return MagicMock(content=item)

    mock_instance = MagicMock()
    mock_instance.invoke.side_effect = _next

    def _factory(**kwargs):
        return mock_instance

    return _factory, mock_instance


def _msg(content: str, role: str = "user") -> MagicMock:
    """Build a mock message with ``.content`` and ``.type``."""
    msg = MagicMock()
    msg.content = content
    msg.type = role
    msg.tool_calls = None
    return msg


def _make_manager_with_llm(llm_factory) -> MagicMock:
    """Build a manager mock wired with a fake LLM factory.

    The manager exposes ``config.llm`` so
    :func:`_llm_config_from_manager` produces a valid LLM config
    dict that the builder can clean and pass to the mocked
    ``ThinkingChatOpenAI``.
    """
    manager = MagicMock()
    manager.config = MagicMock()
    manager.config.llm.base_url = "http://proxy"
    manager.config.llm.api_key = "k"
    manager.config.llm.model = "gpt-4o"
    manager.config.llm.model_vision = "gpt-4o"
    manager.config.llm.temperature = 0.0
    manager.config.llm.request_timeout = 60.0
    return manager


# =============================================================================
# Happy path — markdown guardrail returned
# =============================================================================


class TestWatcherContextBuilderHappyPath:
    """The LLM succeeds → markdown guardrail is returned verbatim."""

    @pytest.mark.asyncio
    async def test_returns_llm_markdown_verbatim(self):
        """A markdown document from the LLM is returned unchanged."""
        llm_response = (
            "## Agent Activity\nRefactor auth module.\n\n"
            "## Allowed\n- read auth/\n\n"
            "## Forbidden\n- rm -rf\n\n"
            "## Requirement\nbe nice"
        )
        factory, _ = _make_fake_llm_class([llm_response])
        manager = _make_manager_with_llm(factory)
        builder_prompt = "You are a builder."

        with patch("daemon.graph.ThinkingChatOpenAI", factory):
            builder = WatcherContextBuilder(
                manager=manager,
                llm_config={"base_url": "x", "api_key": "k", "model": "m"},
                builder_prompt=builder_prompt,
            )
            result = await builder.build(
                messages=[_msg("hello"), _msg("hi", role="assistant")],
                requirement="be nice",
            )

        assert result == llm_response

    @pytest.mark.asyncio
    async def test_forwards_requirement_into_payload(self):
        """The requirement is serialized into the JSON user payload."""
        llm_response = "## Agent Activity\nTest"
        factory, mock_instance = _make_fake_llm_class([llm_response])
        manager = _make_manager_with_llm(factory)

        captured_kwargs: dict[str, Any] = {}

        def _capture_factory(**kwargs):
            return mock_instance

        factory = _capture_factory
        with patch("daemon.graph.ThinkingChatOpenAI", factory):
            builder = WatcherContextBuilder(
                manager=manager,
                llm_config={"base_url": "x", "api_key": "k", "model": "m"},
                builder_prompt="builder prompt",
            )
            await builder.build(
                messages=[_msg("hello")],
                requirement="do not rm -rf",
            )

            # Inspect the call args — the user payload is the second
            # message (HumanMessage).
            call_args = mock_instance.invoke.call_args.args[0]
            # Find the HumanMessage in the call.
            from langchain_core.messages import HumanMessage

            human_msg = next(m for m in call_args if isinstance(m, HumanMessage))
            import json

            payload = json.loads(human_msg.content)
            assert payload["requirement"] == "do not rm -rf"

    @pytest.mark.asyncio
    async def test_none_requirement_serialized_as_marker(self):
        """A ``None`` requirement is serialized as ``"(none provided)"``."""
        factory, mock_instance = _make_fake_llm_class(["## Agent Activity\nx"])
        manager = _make_manager_with_llm(factory)

        with patch("daemon.graph.ThinkingChatOpenAI", factory):
            builder = WatcherContextBuilder(
                manager=manager,
                llm_config={"base_url": "x", "api_key": "k", "model": "m"},
                builder_prompt="builder prompt",
            )
            await builder.build(messages=[_msg("hi")], requirement=None)

            from langchain_core.messages import HumanMessage
            import json

            call_args = mock_instance.invoke.call_args.args[0]
            human_msg = next(m for m in call_args if isinstance(m, HumanMessage))
            payload = json.loads(human_msg.content)
            assert payload["requirement"] == "(none provided)"

    @pytest.mark.asyncio
    async def test_available_tools_passed_through(self):
        """``available_tools`` is serialized as a list in the payload."""
        factory, mock_instance = _make_fake_llm_class(["## Agent Activity\nx"])
        manager = _make_manager_with_llm(factory)

        with patch("daemon.graph.ThinkingChatOpenAI", factory):
            builder = WatcherContextBuilder(
                manager=manager,
                llm_config={"base_url": "x", "api_key": "k", "model": "m"},
                builder_prompt="builder prompt",
            )
            await builder.build(
                messages=[_msg("hi")],
                requirement="x",
                available_tools=["read_file", "bash", "edit_file"],
            )

            from langchain_core.messages import HumanMessage
            import json

            call_args = mock_instance.invoke.call_args.args[0]
            human_msg = next(m for m in call_args if isinstance(m, HumanMessage))
            payload = json.loads(human_msg.content)
            assert payload["available_tools"] == ["read_file", "bash", "edit_file"]

    @pytest.mark.asyncio
    async def test_no_available_tools_serializes_empty_list(self):
        """A ``None`` ``available_tools`` becomes ``[]`` in the payload."""
        factory, mock_instance = _make_fake_llm_class(["## Agent Activity\nx"])
        manager = _make_manager_with_llm(factory)

        with patch("daemon.graph.ThinkingChatOpenAI", factory):
            builder = WatcherContextBuilder(
                manager=manager,
                llm_config={"base_url": "x", "api_key": "k", "model": "m"},
                builder_prompt="builder prompt",
            )
            await builder.build(messages=[_msg("hi")], requirement="x")

            from langchain_core.messages import HumanMessage
            import json

            call_args = mock_instance.invoke.call_args.args[0]
            human_msg = next(m for m in call_args if isinstance(m, HumanMessage))
            payload = json.loads(human_msg.content)
            assert payload["available_tools"] == []

    @pytest.mark.asyncio
    async def test_message_window_serialized_as_string(self):
        """The message window is a string in the payload (not a list)."""
        factory, mock_instance = _make_fake_llm_class(["## Agent Activity\nx"])
        manager = _make_manager_with_llm(factory)

        with patch("daemon.graph.ThinkingChatOpenAI", factory):
            builder = WatcherContextBuilder(
                manager=manager,
                llm_config={"base_url": "x", "api_key": "k", "model": "m"},
                builder_prompt="builder prompt",
                message_window=10,
            )
            await builder.build(
                messages=[_msg(f"msg-{i}") for i in range(5)],
                requirement="x",
            )

            from langchain_core.messages import HumanMessage
            import json

            call_args = mock_instance.invoke.call_args.args[0]
            human_msg = next(m for m in call_args if isinstance(m, HumanMessage))
            payload = json.loads(human_msg.content)
            assert isinstance(payload["message_window"], str)
            # The serialized window includes our message texts.
            assert "msg-0" in payload["message_window"]
            assert "msg-4" in payload["message_window"]


# =============================================================================
# Fallback chain — LLM timeout / error / empty
# =============================================================================


class TestWatcherContextBuilderFallback:
    """Failure paths fall back to raw-tail + static guardrail."""

    @pytest.mark.asyncio
    async def test_llm_timeout_falls_back(self):
        """``asyncio.TimeoutError`` → static guardrail + raw-tail."""
        factory, mock_instance = _make_fake_llm_class()
        # Make the LLM call hang forever.
        async def _hang(*args, **kwargs):
            import asyncio
            await asyncio.sleep(60)

        # Replace side_effect to raise TimeoutError directly on invoke.
        mock_instance.invoke.side_effect = TimeoutError("timeout")

        manager = _make_manager_with_llm(factory)

        with patch("daemon.graph.ThinkingChatOpenAI", factory):
            builder = WatcherContextBuilder(
                manager=manager,
                llm_config={"base_url": "x", "api_key": "k", "model": "m"},
                builder_prompt="builder prompt",
                timeout_seconds=1,
            )
            # The factory mock raises immediately, so the
            # ``asyncio.wait_for`` raises ``TimeoutError``-equivalent
            # which is caught by the builder's fallback.
            result = await builder.build(
                messages=[_msg("hello")],
                requirement="be nice",
            )

        # Static guardrail prefix is present.
        assert "## Forbidden" in result
        # Requirement is spliced (the fallback always shows it).
        assert "[Requirement] be nice" in result
        # Raw-tail includes the message.
        assert "hello" in result

    @pytest.mark.asyncio
    async def test_llm_error_falls_back(self):
        """Provider error → static guardrail + raw-tail."""
        factory, mock_instance = _make_fake_llm_class()
        mock_instance.invoke.side_effect = ConnectionError("network")

        manager = _make_manager_with_llm(factory)

        with patch("daemon.graph.ThinkingChatOpenAI", factory):
            builder = WatcherContextBuilder(
                manager=manager,
                llm_config={"base_url": "x", "api_key": "k", "model": "m"},
                builder_prompt="builder prompt",
            )
            result = await builder.build(
                messages=[_msg("hello")],
                requirement="r",
            )

        assert "## Forbidden" in result
        assert "[Requirement] r" in result

    @pytest.mark.asyncio
    async def test_empty_response_falls_back(self):
        """Empty LLM response → static guardrail + raw-tail."""
        factory, mock_instance = _make_fake_llm_class([""])
        manager = _make_manager_with_llm(factory)

        with patch("daemon.graph.ThinkingChatOpenAI", factory):
            builder = WatcherContextBuilder(
                manager=manager,
                llm_config={"base_url": "x", "api_key": "k", "model": "m"},
                builder_prompt="builder prompt",
            )
            result = await builder.build(
                messages=[_msg("hello")],
                requirement="r",
            )

        # Empty content triggers fallback.
        assert "## Forbidden" in result
        assert "[Requirement] r" in result

    @pytest.mark.asyncio
    async def test_no_messages_no_requirement_returns_static_only(self):
        """Empty conversation + no requirement → static guardrail alone."""
        factory, mock_instance = _make_fake_llm_class()
        # LLM should NOT be called when there's nothing to build from.
        manager = _make_manager_with_llm(factory)

        with patch("daemon.graph.ThinkingChatOpenAI", factory):
            builder = WatcherContextBuilder(
                manager=manager,
                llm_config={"base_url": "x", "api_key": "k", "model": "m"},
                builder_prompt="builder prompt",
            )
            result = await builder.build(messages=[], requirement=None)

        # LLM was not invoked.
        mock_instance.invoke.assert_not_called()
        # Static guardrail is returned.
        assert "## Forbidden" in result

    @pytest.mark.asyncio
    async def test_fallback_includes_raw_tail(self):
        """The raw-tail snapshot of recent activity is in the fallback."""
        factory, mock_instance = _make_fake_llm_class()
        mock_instance.invoke.side_effect = ConnectionError()

        manager = _make_manager_with_llm(factory)

        with patch("daemon.graph.ThinkingChatOpenAI", factory):
            builder = WatcherContextBuilder(
                manager=manager,
                llm_config={"base_url": "x", "api_key": "k", "model": "m"},
                builder_prompt="builder prompt",
            )
            result = await builder.build(
                messages=[_msg("hello world"), _msg("foo bar")],
                requirement=None,
            )

        # No requirement → no [Requirement] line; raw-tail present.
        assert "hello world" in result
        assert "foo bar" in result
        assert "## Forbidden" in result  # static guardrail


# =============================================================================
# Configuration
# =============================================================================


class TestWatcherContextBuilderConfig:
    """Constructor defaults and overrides."""

    def test_default_timeout_is_300_seconds(self):
        """The default timeout is the design-doc default of 300s."""
        manager = _make_manager_with_llm(lambda **kw: MagicMock())
        builder = WatcherContextBuilder(
            manager=manager,
            llm_config={},
            builder_prompt="x",
        )
        assert builder._timeout_seconds == DEFAULT_BUILDER_TIMEOUT_SECONDS
        assert DEFAULT_BUILDER_TIMEOUT_SECONDS == 300

    def test_default_message_window_is_40(self):
        """The default message window is the design-doc default of 40."""
        manager = _make_manager_with_llm(lambda **kw: MagicMock())
        builder = WatcherContextBuilder(
            manager=manager,
            llm_config={},
            builder_prompt="x",
        )
        assert builder._message_window == DEFAULT_BUILDER_MESSAGE_WINDOW
        assert DEFAULT_BUILDER_MESSAGE_WINDOW == 40

    def test_custom_timeout_respected(self):
        manager = _make_manager_with_llm(lambda **kw: MagicMock())
        builder = WatcherContextBuilder(
            manager=manager,
            llm_config={},
            builder_prompt="x",
            timeout_seconds=5,
        )
        assert builder._timeout_seconds == 5

    def test_custom_message_window_respected(self):
        manager = _make_manager_with_llm(lambda **kw: MagicMock())
        builder = WatcherContextBuilder(
            manager=manager,
            llm_config={},
            builder_prompt="x",
            message_window=10,
        )
        assert builder._message_window == 10


# =============================================================================
# Lazy LLM construction
# =============================================================================


class TestWatcherContextBuilderLazyLlm:
    """LLM is built lazily — only on first ``build`` call."""

    @pytest.mark.asyncio
    async def test_llm_not_built_at_construction(self):
        """``ThinkingChatOpenAI`` is not called until ``build`` runs."""
        factory, mock_instance = _make_fake_llm_class(["## Agent Activity\nx"])
        manager = _make_manager_with_llm(factory)

        with patch("daemon.graph.ThinkingChatOpenAI", factory):
            builder = WatcherContextBuilder(
                manager=manager,
                llm_config={"base_url": "x", "api_key": "k", "model": "m"},
                builder_prompt="x",
            )
            # Factory not yet called.
            mock_instance.invoke.assert_not_called()
            await builder.build(messages=[_msg("hi")], requirement="r")
            # Now it has been called.
            mock_instance.invoke.assert_called_once()

    @pytest.mark.asyncio
    async def test_llm_cached_across_calls(self):
        """The LLM is built once and reused across multiple build() calls."""
        factory_calls: list[dict] = []

        def _tracking_factory(**kwargs):
            factory_calls.append(kwargs)
            return MagicMock(invoke=MagicMock(return_value=MagicMock(content="## Agent Activity")))

        manager = _make_manager_with_llm(_tracking_factory)

        with patch("daemon.graph.ThinkingChatOpenAI", _tracking_factory):
            builder = WatcherContextBuilder(
                manager=manager,
                llm_config={"base_url": "x", "api_key": "k", "model": "m"},
                builder_prompt="x",
            )
            await builder.build(messages=[_msg("hi")], requirement="r")
            await builder.build(messages=[_msg("hi")], requirement="r")

        # Factory called ONCE — the LLM is cached.
        assert len(factory_calls) == 1


# =============================================================================
# System-message preservation in window
# =============================================================================


class TestWatcherContextBuilderMessageWindow:
    """System messages are preserved when the window clips non-system."""

    @pytest.mark.asyncio
    async def test_system_messages_always_retained(self):
        """System messages survive the window even when non-system is clipped."""
        factory, mock_instance = _make_fake_llm_class(["## Agent Activity\nx"])
        manager = _make_manager_with_llm(factory)

        # 5 system + 50 user → message_window=10 → all 5 system + last 10
        # user are fed to the LLM.
        messages = [_msg(f"You are agent X (sys msg {i})", role="system") for i in range(5)] + [
            _msg(f"user-msg-{i}", role="user") for i in range(50)
        ]

        with patch("daemon.graph.ThinkingChatOpenAI", factory):
            builder = WatcherContextBuilder(
                manager=manager,
                llm_config={"base_url": "x", "api_key": "k", "model": "m"},
                builder_prompt="x",
                message_window=10,
            )
            await builder.build(messages=messages, requirement="r")

            from langchain_core.messages import HumanMessage
            import json

            call_args = mock_instance.invoke.call_args.args[0]
            human_msg = next(m for m in call_args if isinstance(m, HumanMessage))
            payload = json.loads(human_msg.content)
            window = payload["message_window"]

            # All 5 system messages preserved.
            for i in range(5):
                assert f"sys msg {i}" in window
            # Only the last 10 user messages — early ones clipped.
            assert "user-msg-0" not in window
            assert "user-msg-49" in window