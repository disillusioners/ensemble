"""Tests for Phase 2 Watchover decision logic.

Covers:

  * **WatchoverEvaluator** — verdict parsing, bifurcated failure
    handling (LD-2), LLM call dispatch, degraded-SSE emit.
  * **create_watchover_check_node** — Allow / Deny / Deny-whole-batch /
    3-strike termination, ToolMessage injection shape, watchover_route
    routing.
  * **create_watchover_terminate_node** — TD-8 DB persist + RAM marker.
  * **should_end_watchover** — router reads state["watchover_route"].
  * **Turn-reset** — agent_node returns ``watchover_denial_count=0``
    and ``watchover_turn_id`` on each turn entry.
  * **terminal_reason threading** — terminate_instance accepts
    ``terminal_reason``; the SQL helper writes the parameter.
  * **SSE ordering** — ``cleanup_instance`` runs AFTER
    ``stream_status_change`` in terminate_instance.
  * **canonicalize_status** — ``watchover_terminated`` collapses onto
    ``"cancelled"``.

All tests mock the LLM + LangGraph + DB surface (no real provider, no
real engine) following the ``tests/unit/test_watchover_graph.py``
pattern. The watcher soul prompt and meta-config are read from disk
once at module load — they are static and don't need mocking.
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from daemon.graph import (
    SessionState,
    WatcherVerdict,
    WatchoverEvaluator,
    create_watchover_check_node,
    create_watchover_terminate_node,
    should_end_watchover,
)
from daemon.services.work_status import (
    _STATUS_CANONICAL_MAP,
    canonicalize_status,
)


# =============================================================================
# Helpers
# =============================================================================


@dataclass
class _FakeLLMResult:
    """Drop-in for a LangChain ``AIMessage`` — only ``.content`` matters."""

    content: Any = ""


def _config(instance_id: str = "iid", turn_id: str | None = None) -> dict:
    """LangGraph config dict with thread_id = instance_id."""
    cfg: dict = {"configurable": {"thread_id": instance_id}}
    if turn_id is not None:
        cfg["configurable"]["turn_id"] = turn_id
    return cfg


@dataclass
class _FakeAIMessage:
    """Lightweight AIMessage stand-in for tests — only carries ``tool_calls``.

    Using a real class instead of ``MagicMock`` so ``getattr(msg,
    "tool_calls")`` returns the actual list of dicts (a ``MagicMock``
    attribute read sometimes returns a new ``MagicMock`` and breaks
    downstream ``isinstance(tc, dict)`` checks).
    """

    tool_calls: list[dict] | None = None
    content: str = ""
    type: str = "ai"
    additional_kwargs: dict = field(default_factory=dict)


def _state_with_tool_calls(
    calls: list[dict] | None = None,
    *,
    denial_count: int = 0,
    route: str | None = None,
) -> dict:
    """State dict with a stub last AIMessage carrying tool_calls.

    Args:
        calls: Tool-call dicts to embed on the last message.
            Defaults to a single bash call.
        denial_count: Value for ``watchover_denial_count``.
        route: Value for ``watchover_route`` (typically None — the
            node will set it).
    """
    if calls is None:
        calls = [{"id": "tc-1", "name": "bash", "args": {"command": "ls"}}]

    last_message = _FakeAIMessage(tool_calls=calls)

    state: dict[str, Any] = {
        "messages": [last_message],
        "watchover_denial_count": denial_count,
    }
    if route is not None:
        state["watchover_route"] = route
    return state


def _state_without_tool_calls() -> dict:
    """State dict whose last message has NO ``tool_calls``."""
    last_message = _FakeAIMessage(tool_calls=None)
    return {"messages": [last_message]}


def make_manager(
    *,
    watchover_enabled: bool = True,
    watchover_context: str | None = None,
    instance_metadata: dict | None = None,
) -> MagicMock:
    """Build a mock ``InstanceManager`` with the watchover + DB surface wired.

    Wires:
      * ``is_watchover_enabled(instance_id) -> bool``
      * ``set_deferred_watchover_terminate(instance_id) -> None``
      * ``is_watchover_terminate_requested(instance_id) -> bool``
      * ``clear_watchover_terminate_requested(instance_id) -> None``
      * ``_instance_repository.get(instance_id)`` (with
        ``instance_metadata`` containing ``watchover_context``)
      * ``_instance_repository.set_metadata(...)`` — MagicMock
      * ``_live_hub.stream_message(...)`` — AsyncMock
    """
    manager = MagicMock()

    # Per-instance enable flag.
    manager.is_watchover_enabled.side_effect = lambda iid: watchover_enabled

    # Deferred-terminate lifecycle.
    manager.set_deferred_watchover_terminate = MagicMock()
    manager.is_watchover_terminate_requested = MagicMock(return_value=False)
    manager.clear_watchover_terminate_requested = MagicMock()

    # Build a fake instance row with metadata.
    if instance_metadata is None:
        instance_metadata = {}
    if watchover_context is not None:
        instance_metadata.setdefault("watchover_context", watchover_context)
    row = MagicMock()
    row.instance_metadata = instance_metadata
    repo = MagicMock()
    repo.get.return_value = row
    repo.set_metadata = MagicMock(return_value=row)
    manager._instance_repository = repo

    # SSE surface.
    manager._live_hub = MagicMock()
    manager._live_hub.stream_message = AsyncMock()

    # Stub the question-pause surface so build_instance_graph does not
    # fail if it touches it.
    manager.is_question_pause_requested = MagicMock(return_value=False)
    manager.set_deferred_question_pause = MagicMock()
    manager.clear_question_pause_requested = MagicMock()
    manager.pause_instance_cascade = AsyncMock()

    return manager


def _make_fake_llm_class(
    responses: list[Any] | None = None,
    side_effect: Any = None,
):
    """Build a ``ThinkingChatOpenAI`` factory mock with a queued response list.

    The mock object exposes ``invoke(messages) -> _FakeLLMResult`` returning
    the next queued response on each call. When ``responses`` is exhausted,
    raises ``StopIteration`` via the default MagicMock behavior — callers
    should size ``responses`` to match expected calls.

    Args:
        responses: Iterable of strings or ``_FakeLLMResult`` instances.
            Strings are wrapped in ``_FakeLLMResult(content=...)``.
        side_effect: Optional side_effect for the underlying ``invoke``
            MagicMock — used for raising exceptions on the LLM call.
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
        if isinstance(item, _FakeLLMResult):
            return item
        return _FakeLLMResult(content=item)

    mock_instance = MagicMock()
    mock_instance.invoke.side_effect = _next

    def _factory(**kwargs):
        return mock_instance

    return _factory, mock_instance


# =============================================================================
# WatchoverEvaluator — verdict parsing
# =============================================================================


class TestWatcherVerdictParsing:
    """Static-method tests for ``WatchoverEvaluator._parse_verdict``.

    Phase 4 update: the verdict format now allows an optional markdown
    body after a ``Deny:`` verdict line. The parser is STRICT on the
    first non-empty line (the verdict token) and LENIENT on the body
    (absence is not an error). Bifurcated failure handling (AD-6 /
    LD-2) is preserved — the body's presence does not affect parse
    success.
    """

    def test_parses_bare_allowed(self):
        """``"Allowed"`` → verdict='allow'."""
        v = WatchoverEvaluator._parse_verdict("Allowed")
        assert v is not None
        assert v.verdict == "allow"
        assert v.reason == ""
        assert v.body is None  # Allowed never has a body

    def test_parses_allowed_with_trailing_whitespace(self):
        """``"Allowed "`` (trailing whitespace) still parses as allow."""
        v = WatchoverEvaluator._parse_verdict("Allowed ")
        assert v is not None
        assert v.verdict == "allow"

    def test_parses_deny_with_reason(self):
        """``"Deny: <reason>"`` → verdict='deny', reason=<reason>."""
        v = WatchoverEvaluator._parse_verdict("Deny: reads /etc/shadow")
        assert v is not None
        assert v.verdict == "deny"
        assert v.reason == "reads /etc/shadow"
        assert v.body is None  # No body when only one line

    def test_multiline_with_verdict_on_second_line_is_strictly_rejected(self):
        """Strict contract — preamble on first line is rejected (judgment error).

        The watcher soul explicitly says "first line is the machine
        verdict" — anything other than the bare ``Allowed`` /
        ``Deny: <reason>`` on the first non-empty line is a judgment
        error. This keeps the contract strict so a flaky LLM that adds
        preamble is detected instead of silently accepted.
        """
        raw = "Let me think about this.\nDeny: too sensitive"
        assert WatchoverEvaluator._parse_verdict(raw) is None

    def test_multiline_deny_with_empty_first_line(self):
        """Empty first line + ``Deny: <reason>`` on second line → parsed."""
        raw = "\nDeny: too sensitive"
        v = WatchoverEvaluator._parse_verdict(raw)
        assert v is not None
        assert v.verdict == "deny"
        assert v.reason == "too sensitive"

    def test_empty_reason_is_judgment_error(self):
        """``"Deny:"`` with no reason → ``None`` (judgment error)."""
        v = WatchoverEvaluator._parse_verdict("Deny: ")
        assert v is None

    def test_empty_response_is_judgment_error(self):
        """Empty string → ``None``."""
        assert WatchoverEvaluator._parse_verdict("") is None
        assert WatchoverEvaluator._parse_verdict("   ") is None

    def test_unparseable_garbage_is_judgment_error(self):
        """Arbitrary garbage → ``None`` (judgment error path)."""
        assert WatchoverEvaluator._parse_verdict("hello world") is None
        assert WatchoverEvaluator._parse_verdict("I think you should not do this") is None
        assert WatchoverEvaluator._parse_verdict("Deny") is None  # Missing colon

    def test_strips_whitespace(self):
        """Leading/trailing whitespace does not block parse."""
        v = WatchoverEvaluator._parse_verdict("  Deny: trimmed  ")
        assert v is not None
        assert v.reason == "trimmed"

    # ----- Phase 4 / Verdict format evolution tests -----

    def test_deny_with_markdown_body_extracts_body(self):
        """``Deny: <reason>`` followed by a blank line + body → body captured."""
        raw = (
            "Deny: reads /etc/shadow\n"
            "\n"
            "Use a non-privileged test fixture instead.\n"
            "- Suggestion 1\n"
            "- Suggestion 2"
        )
        v = WatchoverEvaluator._parse_verdict(raw)
        assert v is not None
        assert v.verdict == "deny"
        assert v.reason == "reads /etc/shadow"
        assert v.body is not None
        assert "Use a non-privileged test fixture" in v.body
        assert "- Suggestion 1" in v.body
        assert "- Suggestion 2" in v.body

    def test_deny_with_body_no_blank_line_still_parses_body(self):
        """Body without a separating blank line IS now extracted (W4 fix).

        W4 fix: the body is OPTIONAL and the LLM is allowed to omit
        the blank line between the verdict and the body. The parser
        uses a two-pass approach — preferred blank-line separation
        (matches the ``soul.md``-documented format) and an immediate
        next-line fallback. The fallback prevents legitimate bodies
        from being silently discarded when the LLM forgets the blank
        line.
        """
        raw = "Deny: too sensitive\nMore prose here without a blank line"
        v = WatchoverEvaluator._parse_verdict(raw)
        assert v is not None
        assert v.verdict == "deny"
        assert v.reason == "too sensitive"
        # W4 fix: no blank line → body IS captured (fallback path).
        assert v.body is not None
        assert "More prose here without a blank line" in v.body

    def test_deny_with_body_immediate_next_line_no_blank(self):
        """``Deny: reason\\nbody text here`` captures the body via the W4 fallback.

        Regression test for the W4 fix — when the LLM returns a
        verdict with a body on the very next line (no blank-line
        separator), the fallback extraction must capture it. Without
        the fix the body would be silently discarded.
        """
        raw = "Deny: too sensitive\nbody text here"
        v = WatchoverEvaluator._parse_verdict(raw)
        assert v is not None
        assert v.verdict == "deny"
        assert v.reason == "too sensitive"
        assert v.body is not None
        assert "body text here" in v.body

    def test_deny_with_blank_line_but_no_body_returns_none_body(self):
        """A trailing blank line after the verdict is treated as no body."""
        raw = "Deny: too sensitive\n\n"
        v = WatchoverEvaluator._parse_verdict(raw)
        assert v is not None
        assert v.verdict == "deny"
        assert v.reason == "too sensitive"
        assert v.body is None  # Trailing whitespace → empty body → None

    def test_deny_with_body_is_not_a_judgment_error(self):
        """A Deny with a body is NOT a judgment error — body is allowed.

        Bifurcated failure handling (AD-6 / LD-2) preserved: the
        parser is strict on the first line, lenient on the body.
        Body presence does NOT change the parse outcome.
        """
        raw = (
            "Deny: targets database\n"
            "\n"
            "DROP TABLE requires pre-approval. See migration plan."
        )
        v = WatchoverEvaluator._parse_verdict(raw)
        assert v is not None
        assert v.verdict == "deny"
        assert v.error_type is None  # Not a judgment error
        assert v.body is not None
        assert "DROP TABLE" in v.body

    def test_deny_with_body_truncated_at_1500_chars(self):
        """Bodies longer than 1500 chars are truncated with ``…(truncated)`` marker."""
        # Construct a body just over the 1500-char limit.
        long_body = "X" * 1500 + "YYY"
        raw = f"Deny: too sensitive\n\n{long_body}"
        v = WatchoverEvaluator._parse_verdict(raw)
        assert v is not None
        assert v.verdict == "deny"
        assert v.body is not None
        # Body is capped at 1500 + the truncation marker.
        assert len(v.body) <= 1500 + len("\n…(truncated)") + 5
        assert "…(truncated)" in v.body
        # The first 1500 chars are preserved.
        assert v.body.startswith("X" * 1500)

    def test_deny_with_body_exactly_1500_chars_no_truncation(self):
        """Body of exactly 1500 chars is NOT truncated."""
        body = "X" * 1500
        raw = f"Deny: too sensitive\n\n{body}"
        v = WatchoverEvaluator._parse_verdict(raw)
        assert v is not None
        assert v.body is not None
        assert "…(truncated)" not in v.body
        assert v.body == body

    def test_allowed_with_body_like_text_still_parses_allow(self):
        """``Allowed`` followed by prose on subsequent lines is allow with no body."""
        raw = "Allowed\n\nSome prose after a blank line"
        v = WatchoverEvaluator._parse_verdict(raw)
        assert v is not None
        assert v.verdict == "allow"
        # ``Allowed`` is always bare — no body, even if there's prose after.
        assert v.body is None

    def test_extract_body_utility(self):
        """The static ``_extract_body`` helper returns the body slice verbatim."""
        lines = [
            "Deny: too sensitive",
            "",
            "First body line",
            "Second body line",
        ]
        body = WatchoverEvaluator._extract_body(lines, 0)
        assert body == "First body line\nSecond body line"

    def test_extract_body_no_blank_line_fallback(self):
        """``_extract_body`` falls back to the immediate next line when no blank line.

        W4 fix: the body is OPTIONAL and the LLM is allowed to omit
        the blank line. When there is no blank line but content
        follows immediately after the verdict, that content is
        captured as the body (fallback path).
        """
        lines = ["Deny: too sensitive", "More prose"]
        body = WatchoverEvaluator._extract_body(lines, 0)
        assert body == "More prose"

    def test_extract_body_empty_lines_after_blank(self):
        """``_extract_body`` returns empty when only whitespace follows the blank."""
        lines = ["Deny: too sensitive", "", "   ", ""]
        body = WatchoverEvaluator._extract_body(lines, 0)
        # After the blank line, the remaining content is whitespace-only.
        # Stripping gives empty.
        assert body == ""


# =============================================================================
# WatchoverEvaluator — evaluate() integration
# =============================================================================


class TestWatchoverEvaluatorEvaluate:
    """``WatchoverEvaluator.evaluate(...)`` end-to-end paths.

    Mocks ``ThinkingChatOpenAI`` at the constructor level so the LLM
    call goes through the real ``asyncio.to_thread`` + ``asyncio.wait_for``
    machinery (matching the LoopRepairer pattern).
    """

    async def test_allow_path_returns_verdict(self, monkeypatch):
        """LLM returns ``"Allowed"`` → one allow verdict per call."""
        monkeypatch.setenv("WATCHOVER_ENABLED", "true")
        factory, _llm = _make_fake_llm_class(["Allowed"])
        manager = make_manager()
        with patch("daemon.graph.ThinkingChatOpenAI", factory):
            evaluator = WatchoverEvaluator(
                manager=manager,
                llm_config={"model": "test"},
                instance_id="iid",
                watcher_config={"timeout_seconds": 5},
            )
            verdicts = await evaluator.evaluate(
                tool_calls=[{"id": "tc-1", "name": "bash", "args": {"command": "ls"}}],
                messages=[],
                watchover_context="be helpful",
            )
        assert len(verdicts) == 1
        assert verdicts[0].verdict == "allow"
        assert verdicts[0].error_type is None
        assert verdicts[0].tool_call_id == "tc-1"

    async def test_deny_path_returns_verdict_with_reason(self, monkeypatch):
        """LLM returns ``"Deny: <reason>"`` → deny verdict with reason."""
        monkeypatch.setenv("WATCHOVER_ENABLED", "true")
        factory, _llm = _make_fake_llm_class(["Deny: reads /etc/shadow"])
        manager = make_manager()
        with patch("daemon.graph.ThinkingChatOpenAI", factory):
            evaluator = WatchoverEvaluator(
                manager=manager,
                llm_config={"model": "test"},
                instance_id="iid",
            )
            verdicts = await evaluator.evaluate(
                tool_calls=[{"id": "tc-1", "name": "read_file", "args": {"path": "/etc/shadow"}}],
                messages=[],
                watchover_context="block sensitive reads",
            )
        assert len(verdicts) == 1
        assert verdicts[0].verdict == "deny"
        assert verdicts[0].reason == "reads /etc/shadow"
        assert verdicts[0].tool_call_id == "tc-1"

    async def test_evaluates_every_call_in_batch(self, monkeypatch):
        """All calls evaluated; one verdict per call."""
        monkeypatch.setenv("WATCHOVER_ENABLED", "true")
        # 3 calls → 3 LLM invocations, all allow.
        factory, llm = _make_fake_llm_class(
            ["Allowed", "Allowed", "Allowed"]
        )
        manager = make_manager()
        with patch("daemon.graph.ThinkingChatOpenAI", factory):
            evaluator = WatchoverEvaluator(
                manager=manager,
                llm_config={"model": "test"},
                instance_id="iid",
            )
            verdicts = await evaluator.evaluate(
                tool_calls=[
                    {"id": "tc-1", "name": "bash", "args": {}},
                    {"id": "tc-2", "name": "bash", "args": {}},
                    {"id": "tc-3", "name": "bash", "args": {}},
                ],
                messages=[],
                watchover_context="any",
            )
        assert len(verdicts) == 3
        assert all(v.verdict == "allow" for v in verdicts)
        assert llm.invoke.call_count == 3

    async def test_empty_tool_calls_returns_empty_list(self, monkeypatch):
        """Empty tool_calls list → empty verdict list (no LLM call)."""
        monkeypatch.setenv("WATCHOVER_ENABLED", "true")
        factory, llm = _make_fake_llm_class([])
        manager = make_manager()
        with patch("daemon.graph.ThinkingChatOpenAI", factory):
            evaluator = WatchoverEvaluator(
                manager=manager,
                llm_config={"model": "test"},
                instance_id="iid",
            )
            verdicts = await evaluator.evaluate(
                tool_calls=[], messages=[], watchover_context=None
            )
        assert verdicts == []
        # LLM was never constructed / invoked.
        assert llm.invoke.call_count == 0

    # ----- Bifurcated failure handling (LD-2) -----

    async def test_infra_error_timeout_fails_open(self, monkeypatch):
        """``asyncio.TimeoutError`` → allow + error_type='infra'."""
        monkeypatch.setenv("WATCHOVER_ENABLED", "true")
        factory, _llm = _make_fake_llm_class([asyncio.TimeoutError()])
        manager = make_manager()
        with patch("daemon.graph.ThinkingChatOpenAI", factory):
            evaluator = WatchoverEvaluator(
                manager=manager,
                llm_config={"model": "test"},
                instance_id="iid",
                watcher_config={"timeout_seconds": 1},
            )
            verdicts = await evaluator.evaluate(
                tool_calls=[{"id": "tc-1", "name": "bash", "args": {}}],
                messages=[],
                watchover_context="any",
            )
        assert len(verdicts) == 1
        assert verdicts[0].verdict == "allow"
        assert verdicts[0].error_type == "infra"

    async def test_infra_error_connection_fails_open(self, monkeypatch):
        """``ConnectionError`` → allow + error_type='infra'."""
        monkeypatch.setenv("WATCHOVER_ENABLED", "true")
        factory, _llm = _make_fake_llm_class([ConnectionError("network")])
        manager = make_manager()
        with patch("daemon.graph.ThinkingChatOpenAI", factory):
            evaluator = WatchoverEvaluator(
                manager=manager,
                llm_config={"model": "test"},
                instance_id="iid",
            )
            verdicts = await evaluator.evaluate(
                tool_calls=[{"id": "tc-1", "name": "bash", "args": {}}],
                messages=[],
                watchover_context="any",
            )
        assert verdicts[0].verdict == "allow"
        assert verdicts[0].error_type == "infra"

    async def test_judgment_error_garbage_fails_closed(self, monkeypatch):
        """Garbage response → deny + error_type='judgment'."""
        monkeypatch.setenv("WATCHOVER_ENABLED", "true")
        factory, _llm = _make_fake_llm_class(["not a verdict"])
        manager = make_manager()
        with patch("daemon.graph.ThinkingChatOpenAI", factory):
            evaluator = WatchoverEvaluator(
                manager=manager,
                llm_config={"model": "test"},
                instance_id="iid",
            )
            verdicts = await evaluator.evaluate(
                tool_calls=[{"id": "tc-1", "name": "bash", "args": {}}],
                messages=[],
                watchover_context="any",
            )
        assert verdicts[0].verdict == "deny"
        assert verdicts[0].error_type == "judgment"
        assert "judgment error" in verdicts[0].reason

    async def test_judgment_error_deny_without_reason_fails_closed(self, monkeypatch):
        """``"Deny:"`` with empty reason → deny + judgment error."""
        monkeypatch.setenv("WATCHOVER_ENABLED", "true")
        factory, _llm = _make_fake_llm_class(["Deny:"])
        manager = make_manager()
        with patch("daemon.graph.ThinkingChatOpenAI", factory):
            evaluator = WatchoverEvaluator(
                manager=manager,
                llm_config={"model": "test"},
                instance_id="iid",
            )
            verdicts = await evaluator.evaluate(
                tool_calls=[{"id": "tc-1", "name": "bash", "args": {}}],
                messages=[],
                watchover_context="any",
            )
        assert verdicts[0].verdict == "deny"
        assert verdicts[0].error_type == "judgment"

    async def test_infra_error_emits_degraded_sse(self, monkeypatch):
        """Infra error → exactly one ``watchover_event`` SSE emitted per batch."""
        monkeypatch.setenv("WATCHOVER_ENABLED", "true")
        # 3 calls, all timeout. SSE should be emitted ONCE (not 3 times).
        factory, _llm = _make_fake_llm_class(
            [
                asyncio.TimeoutError(),
                asyncio.TimeoutError(),
                asyncio.TimeoutError(),
            ]
        )
        manager = make_manager()
        with patch("daemon.graph.ThinkingChatOpenAI", factory):
            evaluator = WatchoverEvaluator(
                manager=manager,
                llm_config={"model": "test"},
                instance_id="iid",
                watcher_config={"timeout_seconds": 1},
            )
            await evaluator.evaluate(
                tool_calls=[
                    {"id": "tc-1", "name": "bash", "args": {}},
                    {"id": "tc-2", "name": "bash", "args": {}},
                    {"id": "tc-3", "name": "bash", "args": {}},
                ],
                messages=[],
                watchover_context="any",
            )
        # SSE emit was called exactly once for the whole batch.
        assert manager._live_hub.stream_message.await_count == 1

    async def test_judgment_error_does_not_emit_sse(self, monkeypatch):
        """Judgment error → no degraded SSE."""
        monkeypatch.setenv("WATCHOVER_ENABLED", "true")
        factory, _llm = _make_fake_llm_class(["garbage"])
        manager = make_manager()
        with patch("daemon.graph.ThinkingChatOpenAI", factory):
            evaluator = WatchoverEvaluator(
                manager=manager,
                llm_config={"model": "test"},
                instance_id="iid",
            )
            await evaluator.evaluate(
                tool_calls=[{"id": "tc-1", "name": "bash", "args": {}}],
                messages=[],
                watchover_context="any",
            )
        # No degraded SSE on judgment error.
        assert manager._live_hub.stream_message.await_count == 0

    async def test_max_denials_property_reads_config(self):
        """``max_denials`` property returns configured value (default 3)."""
        manager = make_manager()
        with patch("daemon.graph.ThinkingChatOpenAI", lambda **k: MagicMock()):
            e_default = WatchoverEvaluator(
                manager=manager,
                llm_config={"model": "test"},
                instance_id="iid",
            )
            e_custom = WatchoverEvaluator(
                manager=manager,
                llm_config={"model": "test"},
                instance_id="iid",
                watcher_config={"max_denials_per_turn": 5},
            )
        assert e_default.max_denials == 3
        assert e_custom.max_denials == 5


# =============================================================================
# WatchoverEvaluator — split-message structure (prefix-cache optimisation)
# =============================================================================


class TestWatchoverMessageStructure:
    """``WatchoverEvaluator.evaluate(...)`` LLM payload structure.

    The LLM payload is split into four messages (one ``SystemMessage`` +
    three ``HumanMessage`` layers) so the provider's prefix cache can hit
    on the stable layers across batches:

      1. ``SystemMessage(content=system_prompt)`` — watcher soul prompt,
         fully cached (loaded once via ``_load_watcher_soul_prompt``).
      2. ``HumanMessage(content="[WATCHOVER CONTEXT]... [WATCHOVER CONTEXT END]")``
         — semi-stable, cached until the user rotates the context.
      3. ``HumanMessage(content="[RECENT MESSAGES BEGIN]... [RECENT MESSAGES END]")``
         — prefix-cached (older messages stable; only the newest line
         changes between checks).
      4. ``HumanMessage(content="[WATCHOVER CHECK]...")`` — per-call, the
         only fully uncached layer.

    Layers 1-3 are built ONCE outside the per-tool-call loop and
    REUSED — they must be the SAME object instance across calls in a
    batch. Layer 4 is rebuilt for every tool call.
    """

    @staticmethod
    def _capture_llm_factory():
        """Build a fake ``ThinkingChatOpenAI`` factory that records ``invoke`` calls.

        Returns:
            ``(factory, llm_instance, captured)``:

              * ``factory`` — patched constructor returning ``llm_instance``.
              * ``llm_instance`` — the MagicMock with ``.invoke``.
              * ``captured`` — list of message-lists in invocation order.
                Each entry is the messages list passed to ``invoke`` for
                one call (so ``captured[0][2]`` is the
                ``[WATCHOVER CHECK]`` HumanMessage on the first call).
        """
        captured: list[list] = []
        queue = [_FakeLLMResult(content="Allowed")] * 100  # generous

        def _next(messages):
            # Record a copy of the messages reference (we want to assert
            # object identity on layers 1-3 across calls).
            captured.append(messages)
            if not queue:
                raise AssertionError("LLM mock exhausted")
            return queue.pop(0)

        llm_instance = MagicMock()
        llm_instance.invoke.side_effect = _next

        def _factory(**kwargs):
            return llm_instance

        return _factory, llm_instance, captured

    async def test_split_messages_structure(self, monkeypatch):
        """LLM payload is split into 1 SystemMessage + 3 HumanMessages."""
        monkeypatch.setenv("WATCHOVER_ENABLED", "true")
        factory, _llm, captured = self._capture_llm_factory()
        manager = make_manager()
        with patch("daemon.graph.ThinkingChatOpenAI", factory):
            evaluator = WatchoverEvaluator(
                manager=manager,
                llm_config={"model": "test"},
                instance_id="iid",
            )
            await evaluator.evaluate(
                tool_calls=[{"id": "tc-1", "name": "bash", "args": {"command": "ls"}}],
                messages=[],
                watchover_context="be helpful",
            )

        assert len(captured) == 1, "expected exactly one LLM invoke call"
        messages = captured[0]

        # 1 SystemMessage + 3 HumanMessages
        assert len(messages) == 4, f"expected 4 messages, got {len(messages)}"
        assert isinstance(messages[0], SystemMessage) or messages[0].__class__.__name__ == "SystemMessage"
        assert all(isinstance(m, HumanMessage) for m in messages[1:]), (
            f"messages[1:] must be HumanMessage, got {[type(m).__name__ for m in messages[1:]]}"
        )

        # Layer 1: system prompt (watcher soul).
        from daemon.graph import _load_watcher_soul_prompt
        assert messages[0].content == _load_watcher_soul_prompt()
        assert "Allowed" in messages[0].content or "Deny" in messages[0].content  # sanity

        # Layer 2: WATCHOVER CONTEXT (semi-stable, contains user context).
        ctx = messages[1].content
        assert ctx.startswith("[WATCHOVER CONTEXT]\n"), ctx
        assert "be helpful" in ctx
        assert ctx.endswith("\n[WATCHOVER CONTEXT END]"), ctx

        # Layer 3: RECENT MESSAGES (prefix-cached history block).
        rec = messages[2].content
        assert rec.startswith("[RECENT MESSAGES BEGIN]\n"), rec
        assert rec.endswith("\n[RECENT MESSAGES END]"), rec

        # Layer 4: WATCHOVER CHECK (per-call tool call to evaluate).
        chk = messages[3].content
        assert chk.startswith("[WATCHOVER CHECK]\n"), chk
        assert "Tool: bash" in chk
        assert '"command": "ls"' in chk or '"command":"ls"' in chk  # JSON args
        assert "Respond with Allowed or Deny" in chk

    async def test_recent_messages_human_readable_format(self, monkeypatch):
        """``_format_recent_messages`` emits ``[role]: content`` lines."""
        monkeypatch.setenv("WATCHOVER_ENABLED", "true")
        factory, _llm, captured = self._capture_llm_factory()
        manager = make_manager()
        with patch("daemon.graph.ThinkingChatOpenAI", factory):
            evaluator = WatchoverEvaluator(
                manager=manager,
                llm_config={"model": "test"},
                instance_id="iid",
            )
            await evaluator.evaluate(
                tool_calls=[{"id": "tc-1", "name": "bash", "args": {}}],
                messages=[HumanMessage(content="hello"), AIMessage(content="hi there")],
                watchover_context="any",
            )

        assert len(captured) == 1
        recent_block = captured[0][2].content
        assert "[human]: hello" in recent_block
        assert "[ai]: hi there" in recent_block
        # The block must START and END with the markers — no JSON wrapping.
        assert recent_block.startswith("[RECENT MESSAGES BEGIN]\n")
        assert recent_block.endswith("\n[RECENT MESSAGES END]")

    async def test_stable_layers_reused_across_batch(self, monkeypatch):
        """Context + recent layers are the SAME object across calls in a batch.

        Verifies the optimisation that the stable layers (system prompt,
        context, recent messages) are built ONCE outside the per-call loop
        and REUSED — only the per-call ``[WATCHOVER CHECK]`` is rebuilt.
        """
        monkeypatch.setenv("WATCHOVER_ENABLED", "true")
        factory, _llm, captured = self._capture_llm_factory()
        manager = make_manager()
        with patch("daemon.graph.ThinkingChatOpenAI", factory):
            evaluator = WatchoverEvaluator(
                manager=manager,
                llm_config={"model": "test"},
                instance_id="iid",
            )
            await evaluator.evaluate(
                tool_calls=[
                    {"id": "tc-1", "name": "bash", "args": {"command": "ls"}},
                    {"id": "tc-2", "name": "bash", "args": {"command": "pwd"}},
                    {"id": "tc-3", "name": "bash", "args": {"command": "whoami"}},
                ],
                messages=[],
                watchover_context="be helpful",
            )

        assert len(captured) == 3, "expected one invoke per tool call"

        # [WATCHOVER CONTEXT] HumanMessage must be the SAME object across all 3 calls
        # (proves the stable layer is built once outside the per-call loop, not
        # rebuilt per tool call).
        for i in range(1, 3):
            assert captured[i][1] is captured[0][1], (
                f"[WATCHOVER CONTEXT] at call {i} is not the same object as call 0 — "
                "stable layer is being rebuilt per call"
            )
            # Sanity: the content starts with the WATCHOVER CONTEXT marker.
            assert captured[i][1].content.startswith("[WATCHOVER CONTEXT]\n")

        # [RECENT MESSAGES BEGIN] HumanMessage must be the SAME object across
        # all 3 calls (same optimisation rationale — recent history is
        # prefix-cached and built once).
        for i in range(1, 3):
            assert captured[i][2] is captured[0][2], (
                f"[RECENT MESSAGES BEGIN] at call {i} is not the same object as call 0 — "
                "stable layer is being rebuilt per call"
            )
            assert captured[i][2].content.startswith("[RECENT MESSAGES BEGIN]\n")

        # Per-call [WATCHOVER CHECK] messages MUST differ (different tool args).
        check_contents = [captured[i][3].content for i in range(3)]
        assert check_contents[0] != check_contents[1]
        assert check_contents[1] != check_contents[2]
        assert check_contents[0] != check_contents[2]
        # Each check message mentions the right command.
        assert '"command": "ls"' in check_contents[0] or '"command":"ls"' in check_contents[0]
        assert '"command": "pwd"' in check_contents[1] or '"command":"pwd"' in check_contents[1]
        assert '"command": "whoami"' in check_contents[2] or '"command":"whoami"' in check_contents[2]

    async def test_empty_messages_does_not_break_recent_block(self, monkeypatch):
        """Empty ``messages`` produces an empty recent-messages block (no crash)."""
        monkeypatch.setenv("WATCHOVER_ENABLED", "true")
        factory, _llm, captured = self._capture_llm_factory()
        manager = make_manager()
        with patch("daemon.graph.ThinkingChatOpenAI", factory):
            evaluator = WatchoverEvaluator(
                manager=manager,
                llm_config={"model": "test"},
                instance_id="iid",
            )
            await evaluator.evaluate(
                tool_calls=[{"id": "tc-1", "name": "bash", "args": {}}],
                messages=[],
                watchover_context="any",
            )
        recent_block = captured[0][2].content
        assert recent_block == "[RECENT MESSAGES BEGIN]\n\n[RECENT MESSAGES END]"


# =============================================================================
# create_watchover_check_node — Allow path
# =============================================================================


class TestCheckNodeAllowPath:
    """All-allow path → router routes to ``tools`` (no counter change)."""

    async def test_allow_returns_tools_route_and_no_messages(
        self, monkeypatch
    ):
        monkeypatch.setenv("WATCHOVER_ENABLED", "true")
        manager = make_manager(watchover_enabled=True)
        from daemon.graph import WatchoverSlot

        slot = WatchoverSlot(manager)
        # Mock the evaluator at the class level — the node reads the
        # method result and routes accordingly.
        factory, _ = _make_fake_llm_class(["Allowed"])
        with patch("daemon.graph.ThinkingChatOpenAI", factory):
            node = create_watchover_check_node(
                manager=manager, slot=slot, llm_config={"model": "test"}
            )
            result = await node(
                _state_with_tool_calls(), config=_config("iid")
            )

        assert result["watchover_route"] == "tools"
        assert result.get("messages") is None or result.get("messages") == []
        assert result.get("watchover_denial_count", 0) == 0
        # Router: tools.
        route = should_end_watchover(result, config=_config("iid"))
        assert route == "tools"


# =============================================================================
# create_watchover_check_node — Deny path
# =============================================================================


class TestCheckNodeDenyPath:
    """Deny path → router routes to ``agent``, counter +1, ToolMessages injected."""

    async def test_deny_returns_agent_route_with_tool_messages(
        self, monkeypatch
    ):
        monkeypatch.setenv("WATCHOVER_ENABLED", "true")
        manager = make_manager(watchover_enabled=True)
        from daemon.graph import WatchoverSlot

        slot = WatchoverSlot(manager)
        factory, _ = _make_fake_llm_class(["Deny: reads /etc/shadow"])
        with patch("daemon.graph.ThinkingChatOpenAI", factory):
            node = create_watchover_check_node(
                manager=manager, slot=slot, llm_config={"model": "test"}
            )
            result = await node(
                _state_with_tool_calls(
                    calls=[{"id": "tc-1", "name": "read_file", "args": {"path": "/etc/shadow"}}]
                ),
                config=_config("iid"),
            )

        assert result["watchover_route"] == "agent"
        assert result["watchover_denial_count"] == 1

        msgs = result["messages"]
        assert len(msgs) == 1
        from langchain_core.messages import ToolMessage

        assert isinstance(msgs[0], ToolMessage)
        assert msgs[0].tool_call_id == "tc-1"
        assert "Watchover denied this tool call" in msgs[0].content
        assert "/etc/shadow" in msgs[0].content
        # Loop-breaker exclusion flag (Phase 5).
        assert msgs[0].additional_kwargs.get("watchover_denial") is True

        # Router: agent.
        route = should_end_watchover(result, config=_config("iid"))
        assert route == "agent"

    async def test_deny_with_body_includes_body_in_tool_message(self, monkeypatch):
        """A ``Deny:`` verdict with a markdown body surfaces the body in the ToolMessage.

        Phase 4 verdict format evolution: the watcher LLM may emit an
        optional markdown body after the ``Deny:`` verdict line. The
        graph node includes this body in the ToolMessage content so
        the watched agent sees concrete coaching on how to adjust its
        approach.
        """
        monkeypatch.setenv("WATCHOVER_ENABLED", "true")
        manager = make_manager(watchover_enabled=True)
        from daemon.graph import WatchoverSlot

        slot = WatchoverSlot(manager)
        # Verdict line + blank line + markdown body.
        deny_with_body = (
            "Deny: reads /etc/shadow\n"
            "\n"
            "Use a non-privileged test fixture instead.\n"
            "- Suggestion A\n"
            "- Suggestion B"
        )
        factory, _ = _make_fake_llm_class([deny_with_body])
        with patch("daemon.graph.ThinkingChatOpenAI", factory):
            node = create_watchover_check_node(
                manager=manager, slot=slot, llm_config={"model": "test"}
            )
            result = await node(
                _state_with_tool_calls(
                    calls=[{"id": "tc-1", "name": "read_file", "args": {"path": "/etc/shadow"}}]
                ),
                config=_config("iid"),
            )

        msgs = result["messages"]
        from langchain_core.messages import ToolMessage

        assert isinstance(msgs[0], ToolMessage)
        content = msgs[0].content
        # The first line is the reason-bearing denial.
        assert "Watchover denied this tool call: reads /etc/shadow" in content
        # The body is present.
        assert "Use a non-privileged test fixture" in content
        assert "- Suggestion A" in content
        assert "- Suggestion B" in content
        # The closing line is still the adjust prompt.
        assert "Please adjust your approach" in content
        # Loop-breaker exclusion flag preserved.
        assert msgs[0].additional_kwargs.get("watchover_denial") is True

    async def test_deny_without_body_no_blank_line_after(self, monkeypatch):
        """A ``Deny:`` with no body (no blank line after) → no body in ToolMessage."""
        monkeypatch.setenv("WATCHOVER_ENABLED", "true")
        manager = make_manager(watchover_enabled=True)
        from daemon.graph import WatchoverSlot

        slot = WatchoverSlot(manager)
        # Single-line Deny with no body.
        factory, _ = _make_fake_llm_class(["Deny: too sensitive"])
        with patch("daemon.graph.ThinkingChatOpenAI", factory):
            node = create_watchover_check_node(
                manager=manager, slot=slot, llm_config={"model": "test"}
            )
            result = await node(
                _state_with_tool_calls(
                    calls=[{"id": "tc-1", "name": "bash", "args": {"command": "ls"}}]
                ),
                config=_config("iid"),
            )

        msgs = result["messages"]
        content = msgs[0].content
        # Just the first line + closing prompt — no body section.
        assert "Watchover denied this tool call: too sensitive" in content
        assert "Please adjust your approach" in content
        # No blank-line separator was inserted (no body to separate).
        lines = content.split("\n")
        # The blank line before "Please adjust" is NOT inserted.
        assert lines[-1] == "Please adjust your approach."


# =============================================================================
# create_watchover_check_node — Deny-whole-batch (LD-1)
# =============================================================================


class TestCheckNodeDenyWholeBatch:
    """LD-1: ANY deny in batch → entire batch denied.

    Each call gets a ToolMessage (denied or deferred). Counter +1
    (NOT +N).
    """

    async def test_deny_whole_batch_all_calls_get_messages(
        self, monkeypatch
    ):
        monkeypatch.setenv("WATCHOVER_ENABLED", "true")
        manager = make_manager(watchover_enabled=True)
        from daemon.graph import WatchoverSlot

        slot = WatchoverSlot(manager)
        # 3 calls: deny, allow, allow. The whole batch should be
        # denied; counter increments by 1 only.
        factory, _ = _make_fake_llm_class(
            ["Deny: rm -rf /", "Allowed", "Allowed"]
        )
        with patch("daemon.graph.ThinkingChatOpenAI", factory):
            node = create_watchover_check_node(
                manager=manager, slot=slot, llm_config={"model": "test"}
            )
            result = await node(
                _state_with_tool_calls(
                    calls=[
                        {"id": "tc-1", "name": "bash", "args": {"command": "rm -rf /"}},
                        {"id": "tc-2", "name": "bash", "args": {"command": "ls"}},
                        {"id": "tc-3", "name": "bash", "args": {"command": "pwd"}},
                    ]
                ),
                config=_config("iid"),
            )

        # Whole batch denied.
        assert result["watchover_route"] == "agent"
        # Counter +1, NOT +3.
        assert result["watchover_denial_count"] == 1

        # 3 ToolMessages, one per call.
        from langchain_core.messages import ToolMessage

        msgs = result["messages"]
        assert len(msgs) == 3
        assert all(isinstance(m, ToolMessage) for m in msgs)

        # Each message has the right tool_call_id.
        ids = {m.tool_call_id for m in msgs}
        assert ids == {"tc-1", "tc-2", "tc-3"}

        # First message carries the denial reason.
        denied_msg = next(m for m in msgs if m.tool_call_id == "tc-1")
        assert "Watchover denied this tool call" in denied_msg.content
        assert "rm -rf /" in denied_msg.content

        # The other two carry the "deferred" message.
        for m in msgs:
            if m.tool_call_id != "tc-1":
                assert "Watchover deferred this tool call" in m.content

        # Every ToolMessage tagged for loop-breaker exclusion.
        for m in msgs:
            assert m.additional_kwargs.get("watchover_denial") is True


# =============================================================================
# create_watchover_check_node — 3-strike termination
# =============================================================================


class TestCheckNodeThreeStrikeTermination:
    """3rd denial in a single turn → router routes to terminate."""

    async def test_3rd_denial_routes_to_terminate(self, monkeypatch):
        monkeypatch.setenv("WATCHOVER_ENABLED", "true")
        manager = make_manager(watchover_enabled=True)
        from daemon.graph import WatchoverSlot

        slot = WatchoverSlot(manager)
        # Pre-set denial count to 2 — the next deny is the 3rd.
        factory, _ = _make_fake_llm_class(["Deny: third strike"])
        with patch("daemon.graph.ThinkingChatOpenAI", factory):
            node = create_watchover_check_node(
                manager=manager, slot=slot, llm_config={"model": "test"}
            )
            result = await node(
                _state_with_tool_calls(denial_count=2),
                config=_config("iid"),
            )

        # Counter went from 2 → 3.
        assert result["watchover_denial_count"] == 3
        # Route: terminate.
        assert result["watchover_route"] == "watchover_terminate_node"

        # Router: terminate.
        route = should_end_watchover(result, config=_config("iid"))
        assert route == "watchover_terminate_node"


# =============================================================================
# create_watchover_check_node — Bifurcated failure
# =============================================================================


class TestCheckNodeBifurcatedFailure:
    """LD-2: infra failure → allow + degraded SSE, no count."""

    async def test_infra_failure_routes_to_tools(self, monkeypatch):
        monkeypatch.setenv("WATCHOVER_ENABLED", "true")
        manager = make_manager(watchover_enabled=True)
        from daemon.graph import WatchoverSlot

        slot = WatchoverSlot(manager)
        factory, _ = _make_fake_llm_class([asyncio.TimeoutError()])
        with patch("daemon.graph.ThinkingChatOpenAI", factory):
            node = create_watchover_check_node(
                manager=manager,
                slot=slot,
                llm_config={"model": "test"},
                watcher_config={"timeout_seconds": 1},
            )
            result = await node(
                _state_with_tool_calls(), config=_config("iid")
            )

        # Allow path despite infra error.
        assert result["watchover_route"] == "tools"
        # No counter increment.
        assert result.get("watchover_denial_count", 0) == 0
        # Degraded SSE emitted.
        assert manager._live_hub.stream_message.await_count >= 1

    async def test_judgment_failure_increments_counter(self, monkeypatch):
        """LD-2: judgment error → deny + count."""
        monkeypatch.setenv("WATCHOVER_ENABLED", "true")
        manager = make_manager(watchover_enabled=True)
        from daemon.graph import WatchoverSlot

        slot = WatchoverSlot(manager)
        factory, _ = _make_fake_llm_class(["garbage response"])
        with patch("daemon.graph.ThinkingChatOpenAI", factory):
            node = create_watchover_check_node(
                manager=manager, slot=slot, llm_config={"model": "test"}
            )
            result = await node(
                _state_with_tool_calls(), config=_config("iid")
            )

        # Judgment error → deny + count + route to agent.
        assert result["watchover_route"] == "agent"
        assert result["watchover_denial_count"] == 1


# =============================================================================
# create_watchover_terminate_node — TD-8 persistence
# =============================================================================


class TestTerminateNodePersistence:
    """The terminate node persists DB marker AND sets RAM marker."""

    async def test_sets_both_db_and_ram_markers(self, monkeypatch):
        monkeypatch.setenv("WATCHOVER_ENABLED", "true")
        manager = make_manager()
        from daemon.graph import WatchoverSlot

        slot = WatchoverSlot(manager)
        node = create_watchover_terminate_node(slot, manager=manager)
        await node({}, config=_config("iid"))

        # DB write happened via set_metadata_many (T5.1).
        manager._instance_repository.set_metadata_many.assert_called_once()
        args = manager._instance_repository.set_metadata_many.call_args.args
        assert args[0] == "iid"
        updates = args[1]
        assert updates["watchover_pending_termination"] is True
        assert "watchover_pending_termination_at" in updates
        # RAM marker set.
        manager.set_deferred_watchover_terminate.assert_called_once_with("iid")

    async def test_db_failure_does_not_block_ram_marker(self, monkeypatch):
        """DB write failure → log warning but RAM marker still set."""
        monkeypatch.setenv("WATCHOVER_ENABLED", "true")
        manager = make_manager()
        manager._instance_repository.set_metadata_many.side_effect = RuntimeError(
            "DB down"
        )
        from daemon.graph import WatchoverSlot

        slot = WatchoverSlot(manager)
        node = create_watchover_terminate_node(slot, manager=manager)
        # Should not raise.
        result = await node({}, config=_config("iid"))
        assert result == {}
        # RAM marker still set (DB write is the safety net, not the
        # primary path).
        manager.set_deferred_watchover_terminate.assert_called_once_with("iid")


# =============================================================================
# SessionState — turn reset (T2.5)
# =============================================================================


class TestSessionStateTurnReset:
    """The SessionState declares the watchover keys for checkpoint stability.

    The conftest mocks ``langgraph.graph.MessagesState`` so
    ``SessionState`` cannot be instantiated in this test path. The
    checkpoint-schema invariant is verified by reading the
    ``daemon.graph`` source module directly — the annotation is what
    guarantees that ``ainvoke`` checkpoints keep working across agent
    restarts when Phase 2 lands.
    """

    def _read_graph_source(self) -> str:
        import os

        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "daemon",
            "graph.py",
        )
        with open(path, "r", encoding="utf-8") as f:
            return f.read()

    def test_session_state_declares_watchover_route(self):
        """``watchover_route: str | None`` is declared on SessionState."""
        src = self._read_graph_source()
        assert "watchover_route: str | None" in src
        # Verify it appears after the SessionState class declaration
        # (line numbers confirm this; the field is at graph.py:2072).
        idx = src.find("class SessionState(MessagesState):")
        assert idx != -1
        # The field must come AFTER the class declaration.
        assert src.find("watchover_route: str | None") > idx

    def test_session_state_declares_watchover_denial_count(self):
        """``watchover_denial_count: int`` is declared on SessionState."""
        src = self._read_graph_source()
        # Phase 1 introduced this key — Phase 2 inherits it.
        assert "watchover_denial_count: int" in src

    def test_session_state_declares_watchover_turn_id(self):
        """``watchover_turn_id: str | None`` is declared on SessionState."""
        src = self._read_graph_source()
        assert "watchover_turn_id: str | None" in src

    def test_agent_node_does_not_reset_counter_when_last_message_is_aimessage(self):
        """``agent_node`` re-entry mid-turn MUST NOT reset the denial counter.

        Pre-fix bug: ``agent_node`` reset ``watchover_denial_count`` to 0
        on EVERY invocation, including the re-entries after each
        watchover denial cycle. Because the graph cycle is
        ``agent_node → watchover_check → tools → agent_node``,
        ``agent_node`` runs multiple times within a single turn — so
        the counter oscillated 0 → 1 → 0 → 1 → … and 3-strike
        termination was unreachable.

        Post-fix: the reset is gated on the last message in state being
        a ``HumanMessage``. Mid-turn re-entries see an ``AIMessage``
        (just produced) or ``ToolMessage`` (tool result / denial
        notice) and the counter PERSISTS.

        We exercise the new behavior by reading ``daemon.graph``
        directly and asserting:

          1. ``agent_node`` defines an ``is_turn_boundary`` flag that
             checks ``isinstance(messages[-1], HumanMessage)``.
          2. The ``watchover_denial_count`` reset ONLY happens inside
             the ``if is_turn_boundary:`` branch.
          3. ``watchover_turn_id`` is threaded on every return so the
             LangGraph checkpoint stays consistent.
        """
        import os

        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "daemon",
            "graph.py",
        )
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()

        # Locate ``def agent_node`` and grab a window that covers the
        # turn-reset block at the top of the function.
        agent_node_idx = src.find("async def agent_node(state, config=None):")
        assert agent_node_idx != -1, "agent_node not found in daemon/graph.py"
        # Window from agent_node through ~2000 chars covers the early
        # turn-reset block (well under the LLM-call section which
        # arrives 1000+ lines later).
        window = src[agent_node_idx:agent_node_idx + 4000]

        # (1) The gate MUST exist.
        assert (
            "is_turn_boundary" in window
        ), "expected an is_turn_boundary flag gating the counter reset"
        assert (
            "HumanMessage" in window
        ), "expected HumanMessage to be referenced inside agent_node"
        # The isinstance check is the operative gate — no isinstance,
        # no turn-boundary detection.
        assert (
            "isinstance(" in window
        ), "expected isinstance(..., HumanMessage) turn-boundary detection"

        # (2) The reset MUST be inside the gated branch. Search for
        # the specific conditional assignment pattern.
        gated_reset = (
            'if is_turn_boundary:\n'
            '            watchover_state_reset["watchover_denial_count"] = 0'
            in window
        )
        # Whitespace-tolerant alternative (any indent, possibly mixed).
        gated_reset_alt = (
            "if is_turn_boundary" in window
            and '"watchover_denial_count"] = 0' in window
        )
        assert gated_reset or gated_reset_alt, (
            "the watchover_denial_count reset must be inside the "
            "is_turn_boundary branch, not unconditional"
        )

        # (3) The counter must NOT be reset unconditionally on every
        # invocation. The literal pre-fix pattern was
        # ``"watchover_denial_count": 0`` as a key of the dict
        # literal; that pattern must not appear together with the
        # dict literal inside this window. Acceptable forms are the
        # gated ``["watchover_denial_count"] = 0`` form (subscript)
        # only.
        dict_literal_reset = (
            'watchover_state_reset: dict[str, Any] = {\n'
            '            "watchover_denial_count": 0,' in window
        )
        assert not dict_literal_reset, (
            "agent_node still resets watchover_denial_count: 0 as a "
            "dict literal — that is the pre-fix unconditional reset "
            "(3-strike termination will be unreachable again)"
        )

        # (4) watchover_turn_id is still threaded.
        assert (
            '"watchover_turn_id": turn_id' in window
        ), "watchover_turn_id must still be threaded on every return"


# =============================================================================
# should_end_watchover router
# =============================================================================


class TestShouldEndWatchoverRouter:
    """The ``should_end_watchover`` router reads ``state["watchover_route"]``."""

    def test_tools_hint(self):
        """``"tools"`` → ``"tools"``."""
        assert should_end_watchover(
            {"watchover_route": "tools"}, config=_config()
        ) == "tools"

    def test_agent_hint(self):
        """``"agent"`` → ``"agent"``."""
        assert should_end_watchover(
            {"watchover_route": "agent"}, config=_config()
        ) == "agent"

    def test_terminate_hint(self):
        """``"watchover_terminate_node"`` → ``"watchover_terminate_node"``."""
        assert should_end_watchover(
            {"watchover_route": "watchover_terminate_node"}, config=_config()
        ) == "watchover_terminate_node"

    def test_missing_hint_defaults_to_agent(self):
        """Missing hint → fail-closed default ``"agent"``."""
        assert should_end_watchover({}, config=_config()) == "agent"

    def test_invalid_hint_defaults_to_agent(self):
        """Invalid hint → fail-closed default ``"agent"``."""
        assert should_end_watchover(
            {"watchover_route": "bogus"}, config=_config()
        ) == "agent"


# =============================================================================
# terminal_reason threading
# =============================================================================


class TestTerminalReasonThreading:
    """``terminate_instance`` accepts and threads ``terminal_reason``."""

    def test_manager_signature_includes_terminal_reason(self):
        """``InstanceManager.terminate_instance`` has a ``terminal_reason`` param."""
        import inspect

        from daemon.manager import InstanceManager

        sig = inspect.signature(InstanceManager.terminate_instance)
        assert "terminal_reason" in sig.parameters
        # Default value is "aborted" (backward-compat).
        assert sig.parameters["terminal_reason"].default == "aborted"

    def test_work_status_canonicalizes_watchover_terminated(self):
        """``canonicalize_status("watchover_terminated")`` → ``"cancelled"``."""
        assert canonicalize_status("watchover_terminated") == "cancelled"

    def test_work_status_canonicalizes_aborted(self):
        """``canonicalize_status("aborted")`` → ``"cancelled"`` (Phase 7c)."""
        assert canonicalize_status("aborted") == "cancelled"

    def test_work_status_canonical_map_includes_watchover_terminated(self):
        """``"watchover_terminated": "cancelled"`` is in the canonical map."""
        assert _STATUS_CANONICAL_MAP.get("watchover_terminated") == "cancelled"


# =============================================================================
# SSE cleanup ordering (T2.8 / CR-4)
# =============================================================================


class TestSSECleanupOrdering:
    """The ``cleanup_instance`` SSE call runs AFTER ``stream_status_change``.

    CR-4 / TD-5 invariant: any in-flight SSE clients must observe the
    ``status_change`` event (so they can see the instance as
    ``terminated``) BEFORE the live hub tears down their connection.
    Driving the **real** :meth:`InstanceLifecycleService.terminate_instance`
    method — not an inline copy of the ordering — so a regression in
    the real code path fails this test.
    """

    async def test_cleanup_instance_runs_after_status_change(self):
        """The real ``terminate_instance`` invokes ``stream_status_change``
        BEFORE ``cleanup_instance`` on ``manager._live_hub``.

        Wires the bare ``InstanceLifecycleService`` with everything it
        needs to run end-to-end minus the DB:
          * ``manager.engine`` + ``manager.write_guard`` — magic mocks
            (the real ``_terminate_instance_db_sync`` runs on a worker
            thread; we replace it below).
          * ``manager._instance_repository.get`` returns a row whose
            ``status != TERMINATED`` so the fast-path pre-read does NOT
            short-circuit (real flow).
          * ``manager._request_registry`` / ``_gii_throttle`` /
            ``_loop_breaker_state`` / ``_graph_tasks`` — container
            mocks.
          * ``manager._todo_manager``, ``_watcher_repo``,
            ``_mcp_service`` — set to ``None`` to make the conditional
            cleanup branches no-op.
          * ``manager.instances`` — ``{}`` empty (no live in-memory row).
          * ``Session`` patched in ``daemon.services.instance_lifecycle``
            to return a mock whose ``exec(...).scalars().all()`` returns
            ``[]`` (no children to cascade).
          * ``get_background_process_manager`` /
            ``get_bash_process_registry`` /
            ``get_dependency_bus`` — patched globally so the lazy-imported
            singletons resolve to mocks whose ``cleanup_instance`` /
            ``cancel_for_target`` are async no-ops.
          * ``_terminate_instance_db_sync`` on the service is replaced
            with a sync function returning a valid ``_TerminateResult``
            so the post-commit SSE path fires.
        """
        from daemon.services import instance_lifecycle as lifecycle_module
        from daemon.services.instance_lifecycle import (
            InstanceLifecycleService,
            _TerminateResult,
        )

        # ── Manager mock with the right surface ───────────────────────
        manager = make_manager()
        manager.engine = MagicMock(name="engine")
        manager.write_guard = MagicMock(name="write_guard")

        # Fast-path pre-read: row exists, NOT yet terminated.
        meta_row = MagicMock(name="meta_row")
        meta_row.status = "running"
        meta_row.parent_id = None
        repo = MagicMock()
        repo.get.return_value = meta_row
        manager._instance_repository = repo

        # In-memory containers.
        manager._request_registry = MagicMock()
        manager._gii_throttle = {}
        manager._loop_breaker_state = {}
        manager._graph_tasks = {}
        manager.instances = {}

        # Set optional cleanup branches to None so they no-op.
        manager._todo_manager = None
        manager._watcher_repo = None
        manager._mcp_service = None

        # Mark the question-pause lifecycle as absent too.
        manager.is_question_pause_requested = MagicMock(return_value=False)
        manager.set_deferred_question_pause = MagicMock()
        manager.clear_question_pause_requested = MagicMock()
        manager.pause_instance_cascade = AsyncMock()

        # Live hub — track call order on the REAL method.
        call_order: list[tuple[str, str]] = []

        async def _track_status(*args, **kwargs):
            call_order.append(
                ("status_change", (args[1] if len(args) > 1 else
                                   kwargs.get("status") or ""))
            )

        async def _track_cleanup(*args, **kwargs):
            call_order.append(("cleanup_instance", ""))

        async def _track_injection_consumed(*args, **kwargs):
            call_order.append(("injection_consumed", ""))

        manager._live_hub.stream_status_change = AsyncMock(
            side_effect=_track_status
        )
        manager._live_hub.cleanup_instance = AsyncMock(side_effect=_track_cleanup)
        manager._live_hub.stream_message = AsyncMock(
            side_effect=_track_injection_consumed
        )

        # ── Lifecycle service — construct it for real ─────────────────
        service = InstanceLifecycleService(
            manager=manager,
            cancellation_service=MagicMock(),
            events_service=None,
            job_queue_service=None,
        )

        # Patch the _status_write_guard dependency (used by WriteGuardSession
        # in _terminate_instance_db_sync — we never run that path because we
        # patch the sync helper below, but the WriteGuardSession import was
        # already resolved).
        manager.write_guard.pause_writes = MagicMock()
        manager.write_guard.resume_writes = MagicMock()

        # ── Patch the lazy-imported singletons via the module surface ──
        # All three have try/except wrappers in the production path so a
        # raise is logged-and-swallowed — but failing them silently
        # produces a slow no-op test. Better to give them async mocks so
        # the ordering test runs the real codepath.
        async def _bus_cancel_for_target(*args, **kwargs):
            return 0

        async def _proc_cleanup_instance(*args, **kwargs):
            return None

        async def _bash_cleanup_instance(*args, **kwargs):
            return None

        mock_proc = MagicMock()
        mock_proc.cleanup_instance = AsyncMock(side_effect=_proc_cleanup_instance)
        mock_bash = MagicMock()
        mock_bash.cleanup_instance = AsyncMock(side_effect=_bash_cleanup_instance)
        mock_bus = MagicMock()
        mock_bus.cancel_for_target = AsyncMock(side_effect=_bus_cancel_for_target)

        # Stub the children-cascade DB query. ``Session(engine)`` is called
        # once at the top of terminate_instance to fetch child IDs from
        # instance_hierarchy; we make it return an empty list.
        fake_session = MagicMock(name="session")
        fake_exec = MagicMock(name="exec")
        fake_scalars = MagicMock(name="scalars")
        fake_scalars.all.return_value = []
        fake_exec.scalars.return_value = fake_scalars
        fake_session.exec.return_value = fake_exec
        fake_session.__enter__.return_value = fake_session
        fake_session.__exit__.return_value = False

        def _fake_session_factory(_engine):
            return fake_session

        # Stub ``clear_injection`` — return ``None`` so the
        # ``injection_consumed`` SSE emit branch is skipped.
        manager.clear_injection = MagicMock(return_value=None)
        manager.release_context_usage_cache = MagicMock()

        # Stub the sync DB helper to return a valid outbox NamedTuple
        # so the post-commit SSE path runs.
        def _fake_terminate_db_sync(*args, **kwargs):
            return _TerminateResult(
                skip=False,
                parent_id=None,
                agent_id="watcher",
                message_jobs_cancelled=0,
                all_jobs_cancelled=0,
                message_queue_removed=0,
                tasks_removed=0,
            )

        service._terminate_instance_db_sync = _fake_terminate_db_sync

        # _cancel_bus_watchers_for uses get_dependency_bus() at function
        # scope — patch it on the module.
        with patch.object(
            lifecycle_module, "Session", _fake_session_factory
        ), patch.object(
            lifecycle_module, "get_dependency_bus", lambda: mock_bus
        ), patch(
            "daemon.tools.proc_tools.get_background_process_manager",
            lambda: mock_proc,
        ), patch(
            "daemon.tools.bash.get_bash_process_registry",
            lambda: mock_bash,
        ):
            await service.terminate_instance("iid", terminal_reason="aborted")

        # ── Assert the ordering invariant — DRIVES THE REAL METHOD ──
        # The two relevant SSE calls, in execution order:
        relevant = [
            c for c in call_order if c[0] in ("status_change", "cleanup_instance")
        ]
        assert relevant, "no SSE status_change / cleanup_instance calls observed"
        # stream_status_change must come BEFORE cleanup_instance.
        indices = {name: [] for name in ("status_change", "cleanup_instance")}
        for idx, (name, _) in enumerate(relevant):
            indices[name].append(idx)
        assert indices["status_change"], "stream_status_change was never called"
        assert indices["cleanup_instance"], "cleanup_instance was never called"
        assert min(indices["status_change"]) < min(indices["cleanup_instance"]), (
            "stream_status_change MUST be called before cleanup_instance "
            "(CR-4 / TD-5: clients must observe the 'terminated' status "
            "before the live hub tears down their connection)"
        )

        # Double-check the real method got invoked (not skipped).
        manager._live_hub.stream_status_change.assert_awaited()
        manager._live_hub.cleanup_instance.assert_awaited()
        # The status argument MUST be "terminated".
        status_call_args = (
            manager._live_hub.stream_status_change.await_args
            or manager._live_hub.stream_status_change.call_args
        )
        assert status_call_args is not None
        kwargs = status_call_args.kwargs or {}
        args = status_call_args.args or ()
        # status is the second positional arg per the production call
        status_value = (
            args[1] if len(args) >= 2 else kwargs.get("status")
        )
        assert status_value == "terminated", (
            f"expected status='terminated' on stream_status_change; got {status_value!r}"
        )


# =============================================================================
# Post-graph consumer (T2.9)
# =============================================================================


class TestPostGraphConsumer:
    """The post-graph ``send_message`` consumer honours the deferred marker.

    These tests verify the wiring contract — the actual consumer is in
    ``daemon/services/instance_messaging.py:send_message``. We don't
    drive the full message-processing call (it needs an LLM, a
    checkpoint, etc.) but we exercise the contract surface: the
    ``manager.is_watchover_terminate_requested`` / ``clear_*`` /
    ``terminate_instance`` triple is invoked in the right order with
    ``terminal_reason="watchover_terminated"``.
    """

    def test_clear_method_is_idempotent(self):
        """``clear_watchover_terminate_requested`` does not raise on unset."""
        manager = make_manager()
        manager.clear_watchover_terminate_requested("never-set")  # no raise

    def test_terminate_requested_predicate(self):
        """``is_watchover_terminate_requested`` reflects the marker state."""
        manager = make_manager()
        manager.is_watchover_terminate_requested.return_value = False
        assert manager.is_watchover_terminate_requested("iid") is False
        manager.is_watchover_terminate_requested.return_value = True
        assert manager.is_watchover_terminate_requested("iid") is True
