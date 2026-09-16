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
    JUDGE_EXCERPT_MAX_CHARS,
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
    """Unparsable path: retry fired, attempt=2, reason non-empty,
    first_unparsable_excerpt carries the redacted+truncated raw text.

    The stub ``_stub_invoke_unparsable`` returns the SAME unparsable
    body on both attempts (the retry is internal — the LLM invoker
    is the same patched function). With my retry logic:

    * attempt 1 → unparsable (raw_text="Sorry, I cannot help with that.")
    * retry (attempt 2) → unparsable (same body)
    * final verdict: ``unparsable``, ``attempt=2``,
      ``first_unparsable_excerpt`` carries the redacted+truncated
      raw text from attempt 1 (incident 98b59dd7 forensic surface).

    The conservative semantics are preserved: ``is_complete_report=False``
    so the gate's deny+nudge fall-through fires on retry exhaustion.
    The brief explicitly closes the incident 98b59dd7 root cause:
    the ``reason`` field is no longer empty on unparsable rows (the
    empty ``reason`` on the unparsable verdict was the unrecoverable
    diagnostic gap).
    """
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
    # Retry fired (attempt 1 was unparsable, retry was also unparsable).
    assert result.attempt == 2
    # Reason is no longer empty on unparsable (incident 98b59dd7 fix).
    assert result.reason != ""
    assert "judge_response_unparsable" in result.reason
    # First-attempt excerpt is captured (forensic surface).
    assert result.first_unparsable_excerpt is not None
    assert "Sorry" in result.first_unparsable_excerpt
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
    # Excerpt cap (incident 98b59dd7 forensic surface) — pin the
    # module-level constant. The cap is bounded so the canonical
    # log row stays grep-friendly on the worst-case
    # unparseable-amplified output. Deliberately NOT env-tunable
    # (one knob fewer; the brief is explicit about the cap).
    assert JUDGE_EXCERPT_MAX_CHARS == 400
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
    * ``attempt=1`` is the default (single-attempt outcomes —
      the retry adds ``attempt=2`` for the retry-after-unparsable
      path; incident 98b59dd7, 2026-09-16);
    * ``first_unparsable_excerpt=None`` is the default (no
      unparsable happened on attempt 1 — only populated when the
      retry fired AND attempt 1 returned unparsable);
    * ``hashable`` — frozen dataclasses are hashable by default,
      required so callers can put ``JudgeResult`` instances into
      ``set`` / use as ``dict`` keys without surprise ``TypeError``s.
    * **Field-order pin** — the canonical eight fields are listed
      in EXACTLY this order so a future reorder / rename surfaces
      in code review (drift breaks the log-line format-string
      positional-arg binding in :mod:`daemon.graph` and the
      call-site keyword-only contracts).
    """
    from dataclasses import FrozenInstanceError, fields

    # Default error_class=None / attempt=1 / first_unparsable_excerpt=None
    # — direct construction with the six positional fields suffices.
    result = JudgeResult(
        is_complete_report=False,
        verdict="no",
        reason="short status",
        model="fake-quick",
        latency_ms=42,
    )
    assert result.error_class is None
    assert result.attempt == 1
    assert result.first_unparsable_excerpt is None
    # Field set is exactly the eight canonical fields — prevents a
    # silent rename from breaking log-line / kwargs contracts.
    field_names = [f.name for f in fields(JudgeResult)]
    assert field_names == [
        "is_complete_report",
        "verdict",
        "reason",
        "model",
        "latency_ms",
        "error_class",
        # 2026-09-16 (incident 98b59dd7 retry fix) — appended at the
        # END of the field list so existing positional-construction
        # call sites (which use the first six fields positionally) are
        # NOT broken. New fields default-friendly so backward compat
        # is preserved (constructors using only positional args get
        # ``attempt=1``, ``first_unparsable_excerpt=None``).
        "attempt",
        "first_unparsable_excerpt",
    ]
    # frozen=True — mutation raises FrozenInstanceError.
    import pytest

    with pytest.raises(FrozenInstanceError):
        result.verdict = "yes"  # type: ignore[misc]
    # hashable — the frozen dataclass generates ``__hash__``.
    assert hash(result) == hash(result)
    # Set membership proves hashability without extra ceremony.
    assert result in {result}


# ─────────────────────────────────────────────────────────────────────────────
# Judge retry semantics — incident 98b59dd7, 2026-09-16
# ─────────────────────────────────────────────────────────────────────────────


#: Stub counters — track how many times the LLM invoker is called
#: so we can pin the retry behavior exactly (call count == 1 when
#: no retry, == 2 when retry fired).
class _CallCounter:
    def __init__(self) -> None:
        self.count: int = 0


def _stub_unparsable_then_yes(payload_text: str):
    """Return a stub that emits unparsable on call 1 and 'yes' on call 2."""

    counter = _CallCounter()

    async def _stub(config, user_payload, *, timeout_s):
        counter.count += 1
        if counter.count == 1:
            return (payload_text, "fake-quick")
        return (
            '{"is_complete_report": true, "reason": "delivered final report"}',
            "fake-quick",
        )

    return _stub, counter


def _stub_unparsable_then_yes_no_tracking(payload_text: str):
    """Same shape but without the counter (for tests that don't care
    about call count)."""

    async def _stub(config, user_payload, *, timeout_s):
        if _stub_unparsable_then_yes_no_tracking.calls == 0:
            _stub_unparsable_then_yes_no_tracking.calls += 1
            return (payload_text, "fake-quick")
        return (
            '{"is_complete_report": true, "reason": "delivered final report"}',
            "fake-quick",
        )

    _stub_unparsable_then_yes_no_tracking.calls = 0
    return _stub


def test_judge_async_unparsable_retries_and_succeeds_on_attempt_2(monkeypatch):
    """Retry succeeds: attempt 1 unparsable → attempt 2 parses → ALLOWED.

    Pinned contract: when the retry fires AND parses, the final
    verdict is the retry's verdict (NOT a conservative fail-safe).
    ``is_complete_report=True`` lets the gate flip to ALLOWED without
    demanding the ``attest_completion`` toolcall. ``attempt=2`` and
    ``first_unparsable_excerpt`` carry the failed attempt 1 onto
    the result for operator forensics — even on the success-after-
    retry path, the first-attempt shape is preserved so operators
    can see "yes" was a RECOVERY, not a clean single-attempt pass.
    """
    stub, counter = _stub_unparsable_then_yes(
        "Sorry, I cannot help with that."
    )
    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", stub)
    result = asyncio.run(
        judge_completion_report_async(
            _messages_for_judge(), config=_FakeCfg()
        )
    )
    # Retry fired exactly once.
    assert counter.count == 2
    # Final verdict = retry verdict (success after retry).
    assert result.is_complete_report is True
    assert result.verdict == "yes"
    assert result.reason == "delivered final report"
    # attempt=2 + first_unparsable_excerpt preserved.
    assert result.attempt == 2
    assert result.first_unparsable_excerpt is not None
    assert "Sorry" in result.first_unparsable_excerpt
    assert result.error_class is None


def test_judge_async_unparsable_exhaust_retry_keeps_conservative(monkeypatch):
    """Retry exhausted: attempt 1 unparsable + attempt 2 unparsable →
    conservative fail-safe. The brief's deny-path symmetry guard relies
    on this — long-form + unparsable + unparsable-retry → nudge only
    after attempt 2 fires (the gate sees ``is_complete_report=False``
    AFTER the retry is exhausted, so the nudge is gated by retry
    exhaustion, not by single-attempt unparsable).
    """
    monkeypatch.setattr(
        judge_mod, "_invoke_judge_llm", _stub_invoke_unparsable
    )
    result = asyncio.run(
        judge_completion_report_async(
            _messages_for_judge(), config=_FakeCfg()
        )
    )
    assert result.is_complete_report is False
    assert result.verdict == "unparsable"
    assert result.attempt == 2
    assert result.first_unparsable_excerpt is not None
    # Reason is the new non-empty shape (incident 98b59dd7 fix).
    assert "judge_response_unparsable" in result.reason


def test_judge_async_timeout_does_not_retry(monkeypatch):
    """Timeout path: NO retry (incident 98b59dd7 contract).

    The retry fires ONLY when the model RESPONDED but the response
    did not parse. Timeout / HTTP / LLM errors keep the existing
    fail-safe semantics — no retry, single attempt, ``attempt=1``.
    """
    counter = _CallCounter()

    async def _stub(config, user_payload, *, timeout_s):
        counter.count += 1
        raise asyncio.TimeoutError()

    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", _stub)
    result = asyncio.run(
        judge_completion_report_async(
            _messages_for_judge(), config=_FakeCfg()
        )
    )
    assert counter.count == 1  # NO retry fired.
    assert result.verdict == "timeout"
    assert result.attempt == 1
    assert result.first_unparsable_excerpt is None
    assert result.error_class == "TimeoutError"


def test_judge_async_generic_error_does_not_retry(monkeypatch):
    """Generic error path: NO retry (incident 98b59dd7 contract)."""
    counter = _CallCounter()

    async def _stub(config, user_payload, *, timeout_s):
        counter.count += 1
        raise RuntimeError("boom")

    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", _stub)
    result = asyncio.run(
        judge_completion_report_async(
            _messages_for_judge(), config=_FakeCfg()
        )
    )
    assert counter.count == 1  # NO retry fired.
    assert result.verdict == "error"
    assert result.attempt == 1
    assert result.first_unparsable_excerpt is None
    assert result.error_class == "RuntimeError"


def test_judge_async_yes_does_not_retry(monkeypatch):
    """Success path: NO retry (the response parsed → no need to retry)."""
    counter = _CallCounter()

    async def _stub(config, user_payload, *, timeout_s):
        counter.count += 1
        return (
            '{"is_complete_report": true, "reason": "delivered"}',
            "fake-quick",
        )

    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", _stub)
    result = asyncio.run(
        judge_completion_report_async(
            _messages_for_judge(), config=_FakeCfg()
        )
    )
    assert counter.count == 1
    assert result.verdict == "yes"
    assert result.attempt == 1
    assert result.first_unparsable_excerpt is None


def test_judge_async_no_does_not_retry(monkeypatch):
    """Clean-no path: NO retry (the response parsed → no need to retry)."""
    counter = _CallCounter()

    async def _stub(config, user_payload, *, timeout_s):
        counter.count += 1
        return (
            '{"is_complete_report": false, "reason": "short"}',
            "fake-quick",
        )

    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", _stub)
    result = asyncio.run(
        judge_completion_report_async(
            _messages_for_judge(), config=_FakeCfg()
        )
    )
    assert counter.count == 1
    assert result.verdict == "no"
    assert result.attempt == 1
    assert result.first_unparsable_excerpt is None


def test_judge_async_first_unparsable_then_timeout_records_both(monkeypatch):
    """Retry-after-unparsable itself times out → conservative verdict
    is the retry's transport failure (``timeout``), but the
    ``first_unparsable_excerpt`` is preserved so operators see BOTH
    shapes (the first-attempt unparseable AND the second-attempt
    timeout) on the same log row.
    """
    counter = _CallCounter()

    async def _stub(config, user_payload, *, timeout_s):
        counter.count += 1
        if counter.count == 1:
            return ("Sorry, garbage response.", "fake-quick")
        raise asyncio.TimeoutError()

    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", _stub)
    result = asyncio.run(
        judge_completion_report_async(
            _messages_for_judge(), config=_FakeCfg()
        )
    )
    assert counter.count == 2
    assert result.verdict == "timeout"
    assert result.attempt == 2
    assert result.first_unparsable_excerpt is not None
    assert "Sorry" in result.first_unparsable_excerpt
    # Reason carries the dual-shape signal (operator forensics).
    assert "unparsable" in result.reason.lower()
    assert "timeout" in result.reason.lower()
    assert result.error_class == "TimeoutError"


def test_judge_async_first_unparsable_then_error_records_both(monkeypatch):
    """Retry-after-unparsable itself raises a non-timeout exception →
    same dual-shape forensic surface (the brief is explicit: where
    a response body exists on the timeout/error row, surface it).
    """
    counter = _CallCounter()

    async def _stub(config, user_payload, *, timeout_s):
        counter.count += 1
        if counter.count == 1:
            return ("Random non-JSON prose.", "fake-quick")
        raise RuntimeError("network blip")

    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", _stub)
    result = asyncio.run(
        judge_completion_report_async(
            _messages_for_judge(), config=_FakeCfg()
        )
    )
    assert counter.count == 2
    assert result.verdict == "error"
    assert result.attempt == 2
    assert result.first_unparsable_excerpt is not None
    assert "Random" in result.first_unparsable_excerpt
    assert result.error_class == "RuntimeError"


def test_redact_secrets_redacts_bearer_token():
    """Bearer-style tokens are redacted before logging."""
    from daemon.services.attestation_report_judge import _redact_secrets

    text = (
        "The auth was bearer abcdefghijklmnopqrstuvwxyz1234 in the header."
    )
    out = _redact_secrets(text)
    assert "abcdefghijklmnopqrstuvwxyz1234" not in out
    assert "[REDACTED]" in out


def test_redact_secrets_redacts_api_key_shapes():
    """API-key-shaped strings are redacted before logging."""
    from daemon.services.attestation_report_judge import _redact_secrets

    text = (
        "curl -H 'api_key=abcdefgh1234567890' "
        '{"api-key": "abcdefgh1234567890"} '
        "and token=abcdefgh1234567890"
    )
    out = _redact_secrets(text)
    # All three secret-shaped strings are gone.
    assert "abcdefgh1234567890" not in out
    assert out.count("[REDACTED]") == 3


def test_redact_secrets_redacts_secret_shapes():
    """Generic secret-shaped strings are redacted."""
    from daemon.services.attestation_report_judge import _redact_secrets

    text = "Configured secret=abcdefgh1234567890 for the run."
    out = _redact_secrets(text)
    assert "abcdefgh1234567890" not in out
    assert "[REDACTED]" in out


def test_redact_secrets_preserves_short_tokens():
    """Tokens below the 8-char minimum are NOT redacted (avoid
    false positives on common prose words like 'token economy')."""
    from daemon.services.attestation_report_judge import _redact_secrets

    text = "token economy and bearer of good news"
    out = _redact_secrets(text)
    # The short 'bearer' is matched but no token-shaped secret is
    # present; the prose shape stays intact (no [REDACTED]).
    assert out == text


def test_truncate_excerpt_caps_at_max_chars():
    """Excerpt is capped at ``JUDGE_EXCERPT_MAX_CHARS``."""
    from daemon.services.attestation_report_judge import (
        _truncate_excerpt,
    )

    long_text = "x" * 1000
    out = _truncate_excerpt(long_text)
    assert len(out) == 400
    assert out.endswith("[truncated]")


def test_truncate_excerpt_collapses_whitespace():
    """Whitespace runs are collapsed (a runaway LLM that emits 1000
    newlines does not stretch the log row)."""
    from daemon.services.attestation_report_judge import (
        _truncate_excerpt,
    )

    text = "a\n\n\nb\t\tc   d"
    out = _truncate_excerpt(text)
    assert out == "a b c d"


def test_truncate_excerpt_handles_empty_input():
    from daemon.services.attestation_report_judge import (
        _truncate_excerpt,
    )

    assert _truncate_excerpt("") == ""
    assert _truncate_excerpt("   ") == ""


def test_shape_unparsable_excerpt_composes_redact_then_truncate(monkeypatch):
    """End-to-end: shape_unparsable_excerpt redacts AND truncates.

    Pinned: the shape is the composition of redact → truncate (the
    canonical log-row excerpt surface). Order matters — redaction
    first operates on the raw value so we don't lose secret markers
    to whitespace collapse; truncation then bounds the size for
    log-row grep-friendliness.
    """
    from daemon.services.attestation_report_judge import (
        _shape_unparsable_excerpt,
    )

    # Construct an input that exercises both helpers: a secret token
    # embedded in long prose that overflows the cap.
    token = "abcdefgh1234567890"
    text = (
        "Bearer " + token + " here\n\n" + ("y" * 500)
    )
    out = _shape_unparsable_excerpt(text)
    # Token redacted.
    assert token not in out
    assert "[REDACTED]" in out
    # Whitespace collapsed (the double-newline becomes a single space).
    assert "\n" not in out
    # Capped at JUDGE_EXCERPT_MAX_CHARS.
    assert len(out) == 400
    # Truncation tail marker present.
    assert out.endswith("[truncated]")


# ─────────────────────────────────────────────────────────────────────────────
# Deny-path symmetry guard — incident 98b59dd7, brief §3
#
# On Decision.DENIED with a long-form final AIMessage (>150 words),
# the nudge fires only after the retry is exhausted. This is
# AUTOMATIC via the retry: a successful retry → is_complete_report=True
# → gate flips to ALLOWED, no nudge. A failed retry →
# is_complete_report=False → existing deny+nudge path. The brief
# pins (i) and (ii) below as regression tests.
# ─────────────────────────────────────────────────────────────────────────────


def test_deny_symmetry_long_form_unparsable_then_unparsable_nudge_after_retry(
    monkeypatch,
):
    """Brief pin (i): long-form final AIMessage + unparsable on
    attempt 1 + unparsable on attempt 2 (retry exhausted) →
    conservative verdict fires the deny+nudge path AFTER attempt 2.

    Concretely: the judge service returns
    ``is_complete_report=False, attempt=2`` after retry exhaustion,
    so the gate's deny+nudge machinery sees the post-retry state
    (NOT a single-attempt unparsable). This is the symmetry guard
    the brief mandates — the nudge is gated by retry exhaustion,
    not by single-attempt unparsable.
    """
    monkeypatch.setattr(
        judge_mod, "_invoke_judge_llm", _stub_invoke_unparsable
    )
    result = asyncio.run(
        judge_completion_report_async(
            _messages_for_judge(), config=_FakeCfg()
        )
    )
    # Retry exhausted → conservative fail-safe.
    assert result.is_complete_report is False
    assert result.verdict == "unparsable"
    assert result.attempt == 2  # The nudge gates on post-retry state.
    # Gate sees ``is_complete_report=False`` AFTER attempt 2 — the
    # deny+nudge machinery runs as documented. The first-attempt
    # excerpt is preserved for forensics.
    assert result.first_unparsable_excerpt is not None


def test_deny_symmetry_long_form_unparsable_then_parse_success_no_nudge(
    monkeypatch,
):
    """Brief pin (ii): long-form final AIMessage + unparsable on
    attempt 1 + parse-success on attempt 2 → ``is_complete_report=True``
    → gate flips to ALLOWED → NO nudge fires.

    The retry is what closes the false-positive class from
    incident 98b59dd7 — without the retry, a single-attempt
    unparsable would have flipped a genuine 2328-char final
    report into a deny+nudge path. With the retry, the parse-
    success on attempt 2 rescues the verdict.
    """
    stub, counter = _stub_unparsable_then_yes(
        "Sorry, I cannot help with that."
    )
    monkeypatch.setattr(judge_mod, "_invoke_judge_llm", stub)
    result = asyncio.run(
        judge_completion_report_async(
            _messages_for_judge(), config=_FakeCfg()
        )
    )
    # Parse success on retry → ALLOWED (no nudge path).
    assert result.is_complete_report is True
    assert result.verdict == "yes"
    assert result.attempt == 2
    assert counter.count == 2
    # First-attempt excerpt preserved on the result even though the
    # final verdict is success — operators can see the retry was
    # triggered (the incident 98b59dd7 forensic surface).
    assert result.first_unparsable_excerpt is not None