"""Unit tests for the inline-LLM completion-report judge.

The judge has two surfaces:

1. **Pure functions** (``resolve_judge_model``, ``_slice_judge_window``,
   ``_format_window_for_judge``, ``_parse_judge_response``) — directly
   exercised here, no async / no LLM involved.
2. **Async entry point** (``judge_completion_report_async`` /
   ``judge_completion_report_sync``) — tested via a patched
   ``_invoke_judge_llm`` so no real LLM call is needed. The patch seam
   keeps the test hermetic (no network, no provider timing).
"""
from __future__ import annotations

import asyncio
import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from daemon.services import attestation_report_judge as judge_mod
from daemon.services.attestation_report_judge import (
    JUDGE_DEFAULT_WINDOW,
    JUDGE_MAX_INPUT_CHARS,
    JUDGE_MAX_OUTPUT_CHARS,
    JUDGE_SYSTEM_PROMPT,
    JUDGE_TIMEOUT_S,
    JudgeResult,
    _format_window_for_judge,
    _parse_judge_response,
    _slice_judge_window,
    judge_completion_report_async,
    judge_completion_report_sync,
    resolve_judge_model,
)


# ─────────────────────────────────────────────────────────────────────────────
# resolve_judge_model — honors OPENAI_MODEL_KEYWORDS with fallback
# ─────────────────────────────────────────────────────────────────────────────


class _FakeConfig:
    """Minimal config stand-in. Mirrors ``Config.llm.{model,
    model_keywords}`` so :func:`resolve_judge_model` reads through it."""

    class _LLM:
        model = "fake-main"
        model_keywords = "fake-quick"

    llm = _LLM()


def _fake_config(model: str = "fake-main", model_keywords: str | None = "fake-quick") -> _FakeConfig:
    cfg = _FakeConfig()
    cfg.llm.model = model
    cfg.llm.model_keywords = model_keywords
    return cfg


def test_resolve_judge_model_uses_model_keywords_when_set():
    """``model_keywords`` non-empty ⇒ that wins (operator intent)."""
    cfg = _fake_config(model="fake-main", model_keywords="quick")
    assert resolve_judge_model(cfg) == "quick"


def test_resolve_judge_model_falls_back_to_main_model_when_keywords_empty():
    """Empty ``model_keywords`` ⇒ falls back to ``model``."""
    cfg = _fake_config(model="fake-main", model_keywords="")
    assert resolve_judge_model(cfg) == "fake-main"


def test_resolve_judge_model_falls_back_when_keywords_none():
    """``None`` ``model_keywords`` ⇒ falls back to ``model``."""
    cfg = _fake_config(model="fake-main", model_keywords=None)
    assert resolve_judge_model(cfg) == "fake-main"


def test_resolve_judge_model_strips_whitespace_from_keywords():
    """Whitespace-only ``model_keywords`` ⇒ falls back to ``model``."""
    cfg = _fake_config(model="fake-main", model_keywords="   ")
    assert resolve_judge_model(cfg) == "fake-main"


# ─────────────────────────────────────────────────────────────────────────────
# _slice_judge_window — bounded tail-AIMessages slice
# ─────────────────────────────────────────────────────────────────────────────


def test_slice_judge_window_returns_last_n_ai_messages():
    """Only AIMessages are considered; non-AI messages are skipped."""
    msgs = [
        HumanMessage(content="user says hi"),
        AIMessage(content="first ai"),
        HumanMessage(content="tool result"),
        AIMessage(content="second ai"),
        AIMessage(content="third ai"),
        HumanMessage(content="user again"),
        AIMessage(content="fourth ai"),
    ]
    slice_ = _slice_judge_window(msgs, window=2)
    assert len(slice_) == 2
    assert [m.content for m in slice_] == ["third ai", "fourth ai"]


def test_slice_judge_window_handles_window_larger_than_input():
    """When fewer AIMessages than window, all AIMessages are returned."""
    msgs = [
        HumanMessage(content="hi"),
        AIMessage(content="only one"),
    ]
    slice_ = _slice_judge_window(msgs, window=10)
    assert len(slice_) == 1
    assert slice_[0].content == "only one"


def test_slice_judge_window_clamps_window_to_one_min():
    """``window < 1`` is clamped to 1 (defensive)."""
    msgs = [AIMessage(content=f"ai {i}") for i in range(5)]
    slice_ = _slice_judge_window(msgs, window=0)
    assert len(slice_) == 1


def test_slice_judge_window_handles_empty_input():
    msgs = [HumanMessage(content="hi")]
    assert _slice_judge_window(msgs, window=3) == []


# ─────────────────────────────────────────────────────────────────────────────
# _format_window_for_judge — payload assembly + per-message budget
# ─────────────────────────────────────────────────────────────────────────────


def test_format_window_for_judge_joins_with_separators():
    msgs = [
        AIMessage(content="first"),
        AIMessage(content="second"),
    ]
    formatted = _format_window_for_judge(msgs, per_message_budget=1000)
    assert "[1] first" in formatted
    assert "[2] second" in formatted
    # Each entry on its own block (double newline separator).
    assert formatted.count("\n\n") == 1


def test_format_window_for_judge_truncates_per_message():
    """Oversized content is truncated to fit the per-message budget.

    S4 review fix — the per-message tail marker is
    ``"... [truncated]"`` (not the prior plain ``"..."``). The
    explicit ``[truncated]`` tag lets the LLM distinguish a
    truncation from an ellipsis that happened to land at the
    message end; operators can also grep the judge-input log for
    the marker when investigating a misfire.
    """
    long_content = "x" * 5000
    msgs = [AIMessage(content=long_content)]
    formatted = _format_window_for_judge(msgs, per_message_budget=120)
    assert formatted.endswith("... [truncated]")
    # Truncated marker + at most budget-3 chars + "... [truncated]"
    # (16-char suffix) = total length at most budget + len("[1] ") +
    # len("\n\n")... capped under per_message_budget + a constant
    # for the prefix + the marker.
    assert len(formatted) < 130


def test_format_window_for_judge_flattens_list_content():
    """LangChain list-of-blocks content is flattened to text."""
    msgs = [
        AIMessage(
            content=[
                {"type": "text", "text": "block one"},
                {"type": "text", "text": "block two"},
            ]
        )
    ]
    formatted = _format_window_for_judge(msgs, per_message_budget=1000)
    assert "block one" in formatted
    assert "block two" in formatted


def test_format_window_for_judge_handles_empty_content():
    msgs = [AIMessage(content="")]
    formatted = _format_window_for_judge(msgs, per_message_budget=100)
    assert "[1] " in formatted


# ─────────────────────────────────────────────────────────────────────────────
# _parse_judge_response — strict JSON, conservative on ambiguity
# ─────────────────────────────────────────────────────────────────────────────


def test_parse_judge_response_strict_json_yes():
    parsed = _parse_judge_response(
        '{"is_complete_report": true, "reason": "detailed outcomes delivered"}'
    )
    assert parsed == (True, "detailed outcomes delivered")


def test_parse_judge_response_strict_json_no():
    parsed = _parse_judge_response(
        '{"is_complete_report": false, "reason": "short recap only"}'
    )
    assert parsed == (False, "short recap only")


def test_parse_judge_response_tolerates_code_fence_leakage():
    """An LLM occasionally wraps JSON in ```json fences — tolerate."""
    parsed = _parse_judge_response(
        '```json\n{"is_complete_report": true, "reason": "x"}\n```'
    )
    assert parsed == (True, "x")


def test_parse_judge_response_tolerates_substring_json_match():
    """JSON embedded in surrounding prose (defensive)."""
    parsed = _parse_judge_response(
        'Some preamble {"is_complete_report": false, "reason": "no"} trailing'
    )
    assert parsed == (False, "no")


def test_parse_judge_response_unparsable_when_no_json():
    """Prose-only response is unparsable."""
    assert _parse_judge_response("I think this is done.") is None


def test_parse_judge_response_unparsable_when_multiple_objects():
    """Multiple JSON objects — only one match allowed; the regex
    matches the FIRST one, so the parser accepts it. Pin the actual
    shape so future changes are deliberate."""
    parsed = _parse_judge_response(
        '{"is_complete_report": true, "reason": "first"} '
        '{"is_complete_report": false, "reason": "second"}'
    )
    # First match wins — caller treats as unparsable when verdict is
    # ambiguous. Today: first-match accepted (regex is non-greedy).
    # The test pins the behavior so a future change is conscious.
    assert parsed == (True, "first")


def test_parse_judge_response_unparsable_when_not_object():
    """Top-level JSON array / scalar is unparsable (object required)."""
    assert _parse_judge_response('[1, 2, 3]') is None
    assert _parse_judge_response('"a string"') is None
    assert _parse_judge_response('42') is None


def test_parse_judge_response_unparsable_when_field_missing():
    """Required ``is_complete_report`` field is missing."""
    assert _parse_judge_response('{"reason": "x"}') is None


def test_parse_judge_response_unparsable_when_field_wrong_type():
    """``is_complete_report`` must be a boolean — string fails strict."""
    assert _parse_judge_response('{"is_complete_report": "yes"}') is None


def test_parse_judge_response_caps_reason_length():
    """Long ``reason`` is truncated to 240 chars."""
    long_reason = "x" * 1000
    parsed = _parse_judge_response(
        json.dumps({"is_complete_report": True, "reason": long_reason})
    )
    assert parsed is not None
    is_complete, reason = parsed
    assert is_complete is True
    assert len(reason) <= 240
    assert reason.endswith("...")


def test_parse_judge_response_handles_empty_input():
    assert _parse_judge_response("") is None
    assert _parse_judge_response("   ") is None


# ─────────────────────────────────────────────────────────────────────────────
# judge_completion_report_async — verdict handling, observability fields
# ─────────────────────────────────────────────────────────────────────────────


class _FakeCfg:
    """Tiny config stub for judge calls (just ``llm.model``)."""

    class _LLM:
        model = "fake-main"
        model_keywords = "fake-quick"

    llm = _LLM()


async def _stub_invoke_yes(config, user_payload, *, timeout_s):
    return (
        '{"is_complete_report": true, "reason": "delivered final report"}',
        "fake-quick",
    )


async def _stub_invoke_no(config, user_payload, *, timeout_s):
    return (
        '{"is_complete_report": false, "reason": "short status only"}',
        "fake-quick",
    )


async def _stub_invoke_unparsable(config, user_payload, *, timeout_s):
    return ("Sorry, I cannot help with that.", "fake-quick")


async def _stub_invoke_codefence(config, user_payload, *, timeout_s):
    return (
        '```json\n{"is_complete_report": true, "reason": "fenced yes"}\n```',
        "fake-quick",
    )


async def _stub_invoke_timeout_raises(config, user_payload, *, timeout_s):
    raise asyncio.TimeoutError()


async def _stub_invoke_generic_error(config, user_payload, *, timeout_s):
    raise RuntimeError("boom")


def _messages_for_judge() -> list:
    """A standard tail for the judge to inspect."""
    return [
        HumanMessage(content="please do the work"),
        AIMessage(
            content="",
            tool_calls=[
                {"name": "send_message", "args": {"target": "child"}, "id": "c1"}
            ],
        ),
        AIMessage(content="Delegated. Final report follows."),
        AIMessage(content="Outcomes: x, y, z. Evidence: file paths. Follow-ups: none."),
    ]


def test_judge_async_returns_yes_verdict(monkeypatch):
    monkeypatch.setattr(
        judge_mod, "_invoke_judge_llm", _stub_invoke_yes
    )
    result = asyncio.run(
        judge_completion_report_async(
            _messages_for_judge(), config=_FakeCfg()
        )
    )
    assert isinstance(result, JudgeResult)
    assert result.is_complete_report is True
    assert result.verdict == "yes"
    assert result.reason == "delivered final report"
    assert result.model == "fake-quick"
    assert result.latency_ms >= 0
    assert result.error_class is None


def test_judge_async_returns_no_verdict(monkeypatch):
    monkeypatch.setattr(
        judge_mod, "_invoke_judge_llm", _stub_invoke_no
    )
    result = asyncio.run(
        judge_completion_report_async(
            _messages_for_judge(), config=_FakeCfg()
        )
    )
    assert result.is_complete_report is False
    assert result.verdict == "no"
    assert result.reason == "short status only"
    assert result.error_class is None


def test_judge_async_unparsable_response_is_conservative(monkeypatch):
    monkeypatch.setattr(
        judge_mod, "_invoke_judge_llm", _stub_invoke_unparsable
    )
    result = asyncio.run(
        judge_completion_report_async(
            _messages_for_judge(), config=_FakeCfg()
        )
    )
    # Conservative: unparsable != report (gate fall-through to nudge).
    assert result.is_complete_report is False
    assert result.verdict == "unparsable"
    assert result.reason == ""
    assert result.error_class is None


def test_judge_async_tolerates_codefence_leakage(monkeypatch):
    """A fenced JSON response still parses as 'yes'."""
    monkeypatch.setattr(
        judge_mod, "_invoke_judge_llm", _stub_invoke_codefence
    )
    result = asyncio.run(
        judge_completion_report_async(
            _messages_for_judge(), config=_FakeCfg()
        )
    )
    assert result.is_complete_report is True
    assert result.verdict == "yes"


def test_judge_async_timeout_is_conservative(monkeypatch):
    monkeypatch.setattr(
        judge_mod, "_invoke_judge_llm", _stub_invoke_timeout_raises
    )
    result = asyncio.run(
        judge_completion_report_async(
            _messages_for_judge(), config=_FakeCfg()
        )
    )
    assert result.is_complete_report is False
    assert result.verdict == "timeout"
    assert result.error_class == "TimeoutError"
    assert result.latency_ms >= 0


def test_judge_async_generic_error_is_conservative(monkeypatch):
    monkeypatch.setattr(
        judge_mod, "_invoke_judge_llm", _stub_invoke_generic_error
    )
    result = asyncio.run(
        judge_completion_report_async(
            _messages_for_judge(), config=_FakeCfg()
        )
    )
    assert result.is_complete_report is False
    assert result.verdict == "error"
    assert result.error_class == "RuntimeError"


def test_judge_async_handles_empty_ai_messages(monkeypatch):
    """No AIMessages at all → conservative no-report verdict."""
    monkeypatch.setattr(
        judge_mod, "_invoke_judge_llm", _stub_invoke_yes
    )
    msgs = [HumanMessage(content="only human, no AI yet")]
    result = asyncio.run(
        judge_completion_report_async(msgs, config=_FakeCfg())
    )
    assert result.is_complete_report is False
    assert result.verdict == "error"
    assert result.error_class == "NoAIMessages"
    assert result.model == "<none>"


def test_judge_sync_wrapper_returns_same_shape(monkeypatch):
    """Sync wrapper delegates to async and returns the same shape."""
    monkeypatch.setattr(
        judge_mod, "_invoke_judge_llm", _stub_invoke_yes
    )
    result = judge_completion_report_sync(
        _messages_for_judge(), config=_FakeCfg()
    )
    assert result.is_complete_report is True
    assert result.verdict == "yes"


def test_judge_async_truncates_oversized_output(monkeypatch):
    """Output longer than JUDGE_MAX_OUTPUT_CHARS is truncated before parse."""
    captured = {}

    async def invoke_truncated(config, user_payload, *, timeout_s):
        # 800 chars of garbage + a JSON at the end. After the cap, the
        # JSON is gone and parse fails — but we want to assert the
        # truncation happened.
        payload = "x" * JUDGE_MAX_OUTPUT_CHARS + json.dumps(
            {"is_complete_report": True, "reason": "deep in tail"}
        )
        captured["raw"] = payload
        return (payload, "fake-quick")

    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", invoke_truncated)
    result = asyncio.run(
        judge_completion_report_async(
            _messages_for_judge(), config=_FakeCfg()
        )
    )
    # Truncated payload has no JSON → unparsable (conservative).
    assert result.is_complete_report is False
    assert result.verdict == "unparsable"


# ─────────────────────────────────────────────────────────────────────────────
# Constants — pin the bounds (regression guard)
# ─────────────────────────────────────────────────────────────────────────────


def test_constants_pinned():
    """Pin the canonical bounds so a refactor can't drift them silently."""
    # JUDGE_TIMEOUT_S is the canonical DOCUMENTED default reference
    # (kept for backward compatibility with this pin + the bounds
    # docstring at attestation_report_judge.py :51). The runtime
    # value comes from
    # :mod:`daemon.services.attestation_judge_timeout_resolver` (Pattern C
    # cached-global; default :data:`DEFAULT_JUDGE_TIMEOUT_S` = 25.0s,
    # min clamp 5.0s). Bumped from 10.0s → 25.0s on 2026-09-07
    # (operator tuning decision grounded in the tester live-LLM probe —
    # see ``docs/setup.md`` rationale).
    assert JUDGE_TIMEOUT_S == 25.0
    assert JUDGE_MAX_INPUT_CHARS == 12_000
    assert JUDGE_MAX_OUTPUT_CHARS == 400
    assert JUDGE_DEFAULT_WINDOW == 3
    # System prompt shape — must lead with "You are a strict" so the
    # LLM recognizes the role; must end with "no commentary" so the
    # format constraint is the LAST instruction.
    assert JUDGE_SYSTEM_PROMPT.startswith("You are a strict")
    assert JUDGE_SYSTEM_PROMPT.endswith("no commentary.")
    assert "is_complete_report" in JUDGE_SYSTEM_PROMPT
    assert "reason" in JUDGE_SYSTEM_PROMPT
    # Strict-mode prompt must demand NO markdown / fences — the
    # conservative parsing tolerates a single layer (defense-in-depth)
    # but the prompt makes it explicit.
    assert "No markdown" in JUDGE_SYSTEM_PROMPT or "no markdown" in JUDGE_SYSTEM_PROMPT


# ─────────────────────────────────────────────────────────────────────────────
# JudgeResult — frozen dataclass shape contract (W4 review punch-list)
# ─────────────────────────────────────────────────────────────────────────────


def test_judge_result_is_frozen_and_error_class_defaults_none():
    """Direct ``JudgeResult(...)`` construction pins:

    * ``frozen=True`` — mutation raises ``FrozenInstanceError``;
    * ``error_class=None`` is the default (success / parse-failure
      paths set it ``None`` explicitly; only the exception paths
      stamp the exception class name);
    * ``hashable`` — frozen dataclasses are hashable by default,
      required so callers can put ``JudgeResult`` instances into
      ``set`` / use as ``dict`` keys without surprise ``TypeError``s.
    """
    from dataclasses import FrozenInstanceError, fields

    # Default error_class=None — direct construction with the six
    # positional fields suffices.
    result = JudgeResult(
        is_complete_report=False,
        verdict="no",
        reason="short status",
        model="fake-quick",
        latency_ms=42,
    )
    assert result.error_class is None
    # Field set is exactly the six canonical fields — prevents a
    # silent rename from breaking log-line / kwargs contracts.
    field_names = [f.name for f in fields(JudgeResult)]
    assert field_names == [
        "is_complete_report",
        "verdict",
        "reason",
        "model",
        "latency_ms",
        "error_class",
    ]
    # frozen=True — mutation raises FrozenInstanceError.
    import pytest

    with pytest.raises(FrozenInstanceError):
        result.verdict = "yes"  # type: ignore[misc]
    # hashable — the frozen dataclass generates ``__hash__``.
    assert hash(result) == hash(result)
    # Set membership proves hashability without extra ceremony.
    assert result in {result}