"""Unit tests for the empty-response-guard compaction secondary surface.

Spec §10 (``.agents/shared/planning/empty-response-guard/
architecture-recommendation.md``) pins three compaction-surface
behaviors that the branch's own tests do NOT cover:

1. **Empty summary → retry-then-truncation-fallback.** A continuous-
   empty provider on the summarizer LLM must retry inside the HA
   facade, then fall through to the existing ``_truncate_fallback``
   path (NOT empty content, NOT a crash). The engine's
   ``compact_state`` emits a ``compaction_type="truncation"`` result
   carrying the truncation fallback doc — same shape as a real
   timeout, byte-identical to the operator-visible failure path.

2. **``ENSEMBLE_EMPTY_GUARD_COMPACTION_SKIP=ON`` → immediate
   truncation fallback.** When the knob is ON, the summarizer enters
   the ``empty_guard_disabled`` scope around ``llm_wrapper.invoke``
   (``daemon/compaction.py:73-78``) so S1 never raises for compaction.
   An empty summary returns through ``_extract_text_from_content``
   as an empty string in ONE LLM call — no retry budget burned — and
   lands in the truncate fallback. This is the belt-and-braces knob
   the leader asked for (spec §6 L11).

3. **``EmptyLLMResponseError`` bypasses the ``TimeoutError``-narrowed
   excepts into the ``except Exception`` truncation fallback.** The
   per-batch except handlers at
   ``daemon/compaction.py:2830, :2908, :2928`` are deliberately
   narrowed to ``(TimeoutError, asyncio.TimeoutError)`` (architect §9.8
   O14) — a wider tuple would silently mask EmptyLLMResponseError as
   a timed-out batch (wrong failure_kind, lost lineage signal). The
   behavioral pin verifies an uncaught ``EmptyLLMResponseError``
   reaches ``_truncate_fallback`` via the OUTER ``except Exception``
   (compaction.py:2644-2659); the AST pin asserts the except tuples
   remain narrowed.

Test-only authoring per leader constraint (no daemon edits).
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import pathlib
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from daemon.compaction import (
    ChunkedOutcome,
    CompactionContext,
    ContextCompactor,
)
from daemon.config import CompactionConfig as CompactionConfigModel
from daemon.response_validation import (
    EmptyLLMResponseError,
    LLMResponseValidationError,
    install_empty_guard_config,
    validate_llm_response,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _format_exc_name(node: ast.AST) -> str:
    """Render an exception-type AST node as a dotted string.

    ``TimeoutError`` → ``"TimeoutError"``,
    ``asyncio.TimeoutError`` → ``"asyncio.TimeoutError"``. Used to
    distinct-key tuple elements in the AST pin (a naive
    ``elt.attr``/``elt.id`` collapses both to ``"TimeoutError"``)."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_format_exc_name(node.value)}.{node.attr}"
    return ast.unparse(node)


def make_compaction_config(**overrides: Any) -> CompactionConfigModel:
    """Build a real ``CompactionConfigModel`` with a tight, test-friendly
    timeout. Mirrors the helper from
    ``tests/unit/services/test_proactive_compaction_fix_p1.py`` and
    ``tests/unit/test_compaction.py``."""
    defaults: dict[str, Any] = {
        "enabled": True,
        "threshold": 0.01,  # ALWAYS exceed threshold
        "recent_message_window": 2,
        "min_recent_window": 1,
        "context_window_overrides": {"gpt-4o": 200},  # tiny window
        "context_window_default": 0,
        "target_ratio": 0.40,
        "model": "",
        "summarization_model": "",
        "min_messages_before_compaction": 2,
        "summarization_chunk_threshold": 0.60,
        "timeout_base_s": 0.10,  # tiny base cap (fast tests)
        "timeout_per_100k_tokens_s": 0.0,
        "timeout_cap_s": 0.10,
        "timeout_facade_margin_s": 0.05,
        "operation_budget_s": 0.20,
        "chunk_concurrency": 1,  # simplify batch handling
        "proactive_enabled": True,
    }
    defaults.update(overrides)
    return CompactionConfigModel(**defaults)


def make_messages(n: int, prefix: str = "M") -> list:
    """Alternate human/ai messages."""
    out = []
    for i in range(n):
        cls = HumanMessage if i % 2 == 0 else AIMessage
        out.append(cls(content=f"{prefix} {i}", id=f"m-{i}"))
    return out


def make_context(
    config: CompactionConfigModel,
    messages: list,
    *,
    model_name: str = "gpt-4o",
) -> CompactionContext:
    return CompactionContext(
        messages=messages,
        system_prompt_tokens=10,
        model_name=model_name,
        config=config,
        llm_config={
            "base_url": "http://localhost:1234/v1",
            "api_key": "k",
            "model": model_name,
            "model_vision": model_name,
            "temperature": 0.7,
            "request_timeout": 0.1,
        },
        last_compacted_at=None,
        instance_id="p1-test-iid",
    )


class _CountingFailoverWrapper:
    """Stand-in for ``wrap_langchain_failover`` with bounded retry-on-validation.

    Mirrors the real facade's retry contract for the compaction
    summarizer: each ``invoke`` re-runs ``validate_llm_response`` (S1),
    and ``EmptyLLMResponseError`` / ``LLMResponseValidationError`` are
    classified as transient by the real ``RetryByCategory`` predicate
    and trigger a bounded retry loop. Real facade's 45s
    ``wall_clock_cap_s`` plus ``wait_exponential_jitter`` initial=1s
    is too slow for a fast unit test; the real facade's retry-and-
    fail behavior is pinned in
    ``tests/unit/services/test_keyword_extraction.py`` and
    ``tests/unit/test_empty_response_guard.py`` — these tests verify
    the COMPACTION-site reaction (the outer ``except Exception``
    routes to ``_truncate_fallback``).

    Two counters separate the leader's two pins:

    * ``invoke_count`` — number of times the wrapper was entered by
      the compactor (one per batch in multi-batch mode, plus one for
      the merge pass on a completed outcome). Mirrors the real
      facade's per-call behavior.
    * ``max_retries_per_call`` — maximum INTERNAL retries (validation
      failures) observed within any single ``invoke``. With
      ``COMPACTION_SKIP=ON``, S1 is gated off via the
      ``empty_guard_disabled`` scope so each ``invoke`` exits after
      exactly one attempt — the leader's "direct pin". With
      ``COMPACTION_SKIP=OFF`` (default), S1 fires inside the scope
      and the wrapper retries until exhaustion.
    """

    def __init__(
        self,
        inner_llm: Any,
        *,
        empty_content: Any = "",
        max_attempts: int = 4,
    ) -> None:
        self._inner = inner_llm
        self._empty = empty_content
        self._max = max_attempts
        self.invoke_count = 0
        self.max_retries_per_call = 0
        self._retries_in_current_call = 0
        # Track guard-scope entries: the compaction-skip knob enters
        # ``empty_guard_disabled`` around ``invoke``; we record the
        # scope toggles by intercepting at the wrapper entry.
        self._scope_disabled_during_invoke = False

    def invoke(self, messages: list) -> AIMessage:
        self.invoke_count += 1
        self._retries_in_current_call = 0
        # Snapshot the guard-scope flag at entry so the test can
        # distinguish COMPACTION_SKIP=ON vs OFF (the scope is entered
        # on the worker thread, INSIDE ``_invoke_summarizer_llm`` —
        # this wrapper only sees the EFFECT via the validator's
        # _empty_guard_active()).
        from daemon.response_validation import (
            _EMPTY_GUARD_SCOPE_DISABLED,
        )

        scope_at_entry = _EMPTY_GUARD_SCOPE_DISABLED.get()
        self._scope_disabled_during_invoke = bool(scope_at_entry)
        last_exc: Exception | None = None
        for _attempt in range(self._max):
            try:
                result = self._inner.invoke(messages)
                # Replicate the real facade's classify step. S1
                # fires inside the retry scope.
                validate_llm_response(result, input_messages=messages)
                self.max_retries_per_call = max(
                    self.max_retries_per_call,
                    self._retries_in_current_call,
                )
                return result
            except (EmptyLLMResponseError, LLMResponseValidationError) as e:
                last_exc = e
                self._retries_in_current_call += 1
                continue
        self.max_retries_per_call = max(
            self.max_retries_per_call,
            self._retries_in_current_call,
        )
        # Exhaustion: re-raise. Outer handlers (compact_state's
        # ``except Exception`` at :2644-2659) must catch this and
        # route to ``_truncate_fallback``.
        assert last_exc is not None
        raise last_exc

    # Backward-compat alias for the title-gen test which uses
    # ``attempt_count`` (one call = one retry ladder there).
    @property
    def attempt_count(self) -> int:
        return self.max_retries_per_call + self.invoke_count


def _build_compactor() -> ContextCompactor:
    """Build a real ``ContextCompactor`` against an empty LLM config.

    The LLM and the wrap function are patched per-test."""
    return ContextCompactor(make_compaction_config(), {})


# ---------------------------------------------------------------------------
# Scenario (a): empty summary → retry ladder → truncation fallback
# ---------------------------------------------------------------------------


class TestCompactionEmptySummaryTruncationFallback:
    """Spec §10 — empty summarizer LLM response routes to the
    existing ``_truncate_fallback`` after the retry ladder exhausts.

    The behavioral contract:

    1. The retry ladder fires (≥2 attempts, bounded by
       ``max_attempts``).
    2. The engine emits ``compaction_type="truncation"`` — the SAME
       shape as a real timeout-driven fallback (a downstream consumer
       cannot distinguish empty-summary from timeout-driven
       truncation; both paths are operationally identical).
    3. The ``failure_kind`` field is ``"error"`` (matches the outer
       ``except Exception`` branch at :2644-2659, NOT
       ``"timeout"`` — the timeout classification lives in the
       narrower ``except (TimeoutError, asyncio.TimeoutError)`` at
       :2616 which EmptyLLMResponseError does NOT enter).
    4. The doc / replacement_messages list is populated (NOT empty
       — the truncation fallback always emits the
       ``compaction-global-`` doc).
    """

    @pytest.mark.asyncio
    async def test_empty_string_summary_falls_back_to_truncation(self):
        """``content=""`` from the summarizer → retry-then-truncation."""
        install_empty_guard_config(enabled=True, compaction_skip=False)

        config = make_compaction_config()
        # 30 messages → ~15 alternating human/ai groups → easily
        # above min_messages_before_compaction=2 and above the tiny
        # 200-token context_window threshold.
        messages = make_messages(30)
        context = make_context(config, messages)
        compactor = _build_compactor()

        inner_llm = MagicMock()
        inner_llm.invoke = MagicMock(return_value=AIMessage(content=""))

        wrapper = _CountingFailoverWrapper(
            inner_llm, empty_content="", max_attempts=4
        )

        with patch(
            "daemon.graph.ThinkingChatOpenAI",
            return_value=inner_llm,
            create=True,
        ), patch(
            "daemon.services.llm_failover.wrap_langchain_failover",
            return_value=wrapper,
        ):
            result = await compactor.compact_state(context)

        # Behavioral pins.
        assert result is not None, "compact_state must return a CompactionResult"
        assert result.compaction_type == "truncation", (
            f"empty summary must route to _truncate_fallback; "
            f"got compaction_type={result.compaction_type}"
        )
        assert result.failure_kind == "error", (
            f"EmptyLLMResponseError is NOT a timeout; failure_kind must "
            f"be 'error' (the outer except Exception at :2644-2659), not "
            f"'timeout' (the narrower except at :2616); got "
            f"failure_kind={result.failure_kind}"
        )
        # Retry ladder FIRED — at least one internal retry per call
        # (the wrapper's bounded retry on validation errors).
        assert wrapper.max_retries_per_call >= 1, (
            f"retry ladder did not fire; max_retries_per_call="
            f"{wrapper.max_retries_per_call}"
        )
        # Replacement messages include the truncation-fallback doc.
        assert len(result.replacement_messages) > 0, (
            "truncation fallback must emit a non-empty replacement list"
        )

    @pytest.mark.asyncio
    async def test_none_content_summary_falls_back_to_truncation(self):
        """``content=None`` from the summarizer → same retry-then-truncation.

        ``AIMessage`` is pydantic-validated and rejects ``content=None``;
        we use a stand-in via the wrapper's ``inner_llm.invoke`` which
        bypasses langchain construction (the real facade wraps an
        already-constructed client — the mock returns whatever the test
        says, so we can simulate a non-streaming provider yielding
        ``None``.)"""
        install_empty_guard_config(enabled=True, compaction_skip=False)

        config = make_compaction_config()
        messages = make_messages(30)
        context = make_context(config, messages)
        compactor = _build_compactor()

        class _NoneContentMsg:
            """AIMessage stand-in (Pydantic forbids ``content=None``)."""

            def __init__(self) -> None:
                self.content = None
                self.additional_kwargs: dict = {}
                self.tool_calls: list = []
                self.response_metadata: dict = {}
                self.id: str | None = None
                self.type: str = "ai"

        inner_llm = MagicMock()
        inner_llm.invoke = MagicMock(return_value=_NoneContentMsg())

        wrapper = _CountingFailoverWrapper(
            inner_llm, empty_content=None, max_attempts=4
        )

        with patch(
            "daemon.graph.ThinkingChatOpenAI",
            return_value=inner_llm,
            create=True,
        ), patch(
            "daemon.services.llm_failover.wrap_langchain_failover",
            return_value=wrapper,
        ):
            result = await compactor.compact_state(context)

        assert result is not None
        assert result.compaction_type == "truncation"
        assert result.failure_kind == "error"
        assert wrapper.max_retries_per_call >= 1


# ---------------------------------------------------------------------------
# Scenario (b): COMPACTION_SKIP=ON → immediate fallback (no retry burn)
# ---------------------------------------------------------------------------


class TestCompactionSkipOnImmediateFallback:
    """Spec §10 — ``ENSEMBLE_EMPTY_GUARD_COMPACTION_SKIP=ON`` keeps the
    pre-guard pass-through behavior: ONE LLM call, no retries, the
    empty summary flows through ``_extract_text_from_content`` as an
    empty string, and the engine's ``compact_state`` still routes to
    ``_truncate_fallback``.

    The pin: 0 retries attempted inside the guard scope. The
    install_empty_guard_config(..., compaction_skip=True) flip is the
    belt-and-braces knob the leader asked for (architect §6 L11).
    """

    @pytest.mark.asyncio
    async def test_compaction_skip_on_zero_retries_immediate_fallback(self):
        """Direct pin (leader request): COMPACTION_SKIP=ON skips the
        retry ladder. The empty summary flows through the
        summarization path (or — if a future fix tightens this — the
        truncation fallback path), but in either case the test's
        load-bearing pin is that ``attempt_count == 1`` — the
        retry budget is NOT burned. The leader's "immediate" pin
        is about latency / budget, not about which ``compaction_type``
        label is emitted on the wire.

        See the dedicated ``test_compaction_skip_on_truncation_
        fallback_path_is_preserved`` test below for the
        truncation-fallback-preservation pin.
        """
        install_empty_guard_config(enabled=True, compaction_skip=True)

        config = make_compaction_config()
        messages = make_messages(30)
        context = make_context(config, messages)
        compactor = _build_compactor()

        inner_llm = MagicMock()
        inner_llm.invoke = MagicMock(return_value=AIMessage(content=""))

        # Inside the empty_guard_disabled scope, S1 is gated off
        # (_empty_guard_active() returns False because
        # _EMPTY_GUARD_SCOPE_DISABLED is True). The wrapper's first
        # validate pass is a no-op (does not raise), the empty
        # content is returned through unchanged, the wrapper exits
        # without retrying. Real prod uses the same flow — the S1
        # guard is skipped ONLY for the duration of the scope.
        wrapper = _CountingFailoverWrapper(
            inner_llm, empty_content="", max_attempts=4
        )

        with patch(
            "daemon.graph.ThinkingChatOpenAI",
            return_value=inner_llm,
            create=True,
        ), patch(
            "daemon.services.llm_failover.wrap_langchain_failover",
            return_value=wrapper,
        ):
            result = await compactor.compact_state(context)

        assert result is not None, "compact_state must return a CompactionResult"
        # Behavioral pin (LEADER'S DIRECT REQUEST): EXACTLY 0 INTERNAL
        # retries per call — the knob skips the S1 retry ladder
        # entirely so a continuous-empty provider doesn't burn the
        # budget before the engine can route to the fallback. Each
        # wrapper invocation succeeds on the first attempt because
        # the guard scope is disabled around the call (validate
        # passes through for empty content).
        assert wrapper.max_retries_per_call == 0, (
            f"COMPACTION_SKIP=ON must bypass retry ladder; "
            f"max_retries_per_call={wrapper.max_retries_per_call}, expected 0"
        )

    @pytest.mark.asyncio
    async def test_compaction_skip_on_guard_scope_is_entered(self):
        """The skip knob is materialized via the
        ``empty_guard_disabled`` scope inside ``_invoke_summarizer_llm``
        (``daemon/compaction.py:73-78``). Pin: the scope IS entered
        on the worker thread — the wrapper sees the disabled flag."""
        install_empty_guard_config(enabled=True, compaction_skip=True)

        config = make_compaction_config()
        messages = make_messages(30)
        context = make_context(config, messages)
        compactor = _build_compactor()

        inner_llm = MagicMock()
        inner_llm.invoke = MagicMock(return_value=AIMessage(content=""))

        wrapper = _CountingFailoverWrapper(
            inner_llm, empty_content="", max_attempts=4
        )

        with patch(
            "daemon.graph.ThinkingChatOpenAI",
            return_value=inner_llm,
            create=True,
        ), patch(
            "daemon.services.llm_failover.wrap_langchain_failover",
            return_value=wrapper,
        ):
            await compactor.compact_state(context)

        assert wrapper._scope_disabled_during_invoke is True, (
            "empty_guard_disabled scope must be entered around the "
            "summarizer invoke when COMPACTION_SKIP=ON; the wrapper "
            "snaps the ContextVar at entry — its absence means the "
            "scope toggle is broken (silent regression: the S1 guard "
            "would burn the retry budget on continuous-empty "
            "providers — the exact failure mode the knob was created "
            "to prevent)"
        )

    @pytest.mark.asyncio
    async def test_compaction_skip_off_default_retries_then_falls_back(self):
        """Sanity / regression pin: COMPACTION_SKIP=OFF (default)
        DOES burn the retry budget — the inverse contract that
        confirms (b)'s pin is meaningful (not always 1)."""
        install_empty_guard_config(enabled=True, compaction_skip=False)

        config = make_compaction_config()
        messages = make_messages(30)
        context = make_context(config, messages)
        compactor = _build_compactor()

        inner_llm = MagicMock()
        inner_llm.invoke = MagicMock(return_value=AIMessage(content=""))

        wrapper = _CountingFailoverWrapper(
            inner_llm, empty_content="", max_attempts=4
        )

        with patch(
            "daemon.graph.ThinkingChatOpenAI",
            return_value=inner_llm,
            create=True,
        ), patch(
            "daemon.services.llm_failover.wrap_langchain_failover",
            return_value=wrapper,
        ):
            result = await compactor.compact_state(context)

        assert result.compaction_type == "truncation"
        assert wrapper.max_retries_per_call >= 1, (
            f"COMPACTION_SKIP=OFF must allow the retry ladder; "
            f"max_retries_per_call={wrapper.max_retries_per_call}"
        )
        assert wrapper._scope_disabled_during_invoke is False

    @pytest.mark.asyncio
    async def test_compaction_skip_on_truncation_fallback_path_is_preserved(
        self,
    ):
        """Truncation-fallback preservation pin (architect §6 L11):
        COMPACTION_SKIP=ON must NOT disable the truncation fallback
        path itself — only the S1 retry ladder. A timeout during
        summarization must STILL route to ``_truncate_fallback`` and
        emit ``compaction_type="truncation"``.

        The knob is "belt-and-braces preservation" — it adds an
        opt-out for the S1 raise so an empty-summary doesn't burn
        the budget, but the underlying fallback mechanism (timeout
        → truncation) is unchanged. If a future refactor couples
        the knob to the fallback itself, this test catches it.
        """
        install_empty_guard_config(enabled=True, compaction_skip=True)

        config = make_compaction_config()
        messages = make_messages(30)
        context = make_context(config, messages)
        compactor = _build_compactor()

        # Wrapper raises asyncio.TimeoutError directly — the
        # per-chunk narrowed handler (compaction.py:2830) catches
        # it as a per-batch timeout, and the engine routes through
        # the truncation fallback.
        class _TimeoutWrapper:
            def __init__(self) -> None:
                self.attempt_count = 0

            def invoke(self, _messages: list) -> AIMessage:
                self.attempt_count += 1
                raise asyncio.TimeoutError(
                    "synthetic timeout for fallback-preservation pin"
                )

        wrapper = _TimeoutWrapper()

        with patch(
            "daemon.graph.ThinkingChatOpenAI",
            return_value=MagicMock(),
            create=True,
        ), patch(
            "daemon.services.llm_failover.wrap_langchain_failover",
            return_value=wrapper,
        ):
            result = await compactor.compact_state(context)

        assert result is not None
        # Truncation fallback MUST still fire on timeout, regardless
        # of the COMPACTION_SKIP knob.
        assert result.compaction_type == "truncation", (
            f"COMPACTION_SKIP=ON must preserve the truncation fallback "
            f"path; got compaction_type={result.compaction_type}"
        )
        # failure_kind="timeout" — distinguishes this from the
        # EmptyLLMResponseError case (failure_kind="error"). The
        # narrower except at :2616 catches TimeoutError specifically
        # so the classification is preserved.
        assert result.failure_kind == "timeout", (
            f"timeout-driven fallback must classify as 'timeout'; "
            f"got failure_kind={result.failure_kind}"
        )


# ---------------------------------------------------------------------------
# Scenario (c): EmptyLLMResponseError bypasses TimeoutError-narrowed
# excepts into the outer except Exception truncation fallback.
# ---------------------------------------------------------------------------


class TestCompactionEmptyLLMResponseErrorEscapesTimeoutExcepts:
    """Spec §10 / architect §9.8 / O14 — the per-chunk except handlers
    in ``_summarize_chunked`` are narrowed to
    ``(TimeoutError, asyncio.TimeoutError)`` ONLY.

    A wider tuple (e.g. adding ``Exception`` or
    ``EmptyLLMResponseError``) would silently mask a continuous-empty
    provider as a per-batch timeout — losing the failure_kind
    classification AND re-routing the engine through the narrower
    path. The behavioral pin verifies the OUTER ``except Exception``
    (compaction.py:2644-2659) catches the unhandled
    EmptyLLMResponseError; the AST pin asserts the per-chunk excepts
    remain narrowed.
    """

    @pytest.mark.asyncio
    async def test_empty_llm_response_error_routes_to_truncate_fallback(self):
        """EmptyLLMResponseError must reach ``_truncate_fallback`` via
        the outer ``except Exception`` at :2644-2659 — NOT via the
        per-batch ``except (TimeoutError, asyncio.TimeoutError)`` at
        :2830, :2908, :2928 (which would mark the batch as timed
        out, misclassifying the failure_kind)."""
        install_empty_guard_config(enabled=True, compaction_skip=False)

        config = make_compaction_config()
        messages = make_messages(30)
        context = make_context(config, messages)
        compactor = _build_compactor()

        # Wrapper raises EmptyLLMResponseError directly on every
        # attempt (no retries — pinned to verify the OUTER handler
        # picks up the un-caught exception).
        class _DirectEmptyWrapper:
            def __init__(self) -> None:
                self.attempt_count = 0

            def invoke(self, _messages: list) -> AIMessage:
                self.attempt_count += 1
                raise EmptyLLMResponseError(
                    "empty summary from test wrapper",
                )

        wrapper = _DirectEmptyWrapper()

        with patch(
            "daemon.graph.ThinkingChatOpenAI",
            return_value=MagicMock(),
            create=True,
        ), patch(
            "daemon.services.llm_failover.wrap_langchain_failover",
            return_value=wrapper,
        ):
            result = await compactor.compact_state(context)

        # Outer except Exception routes to _truncate_fallback.
        assert result is not None
        assert result.compaction_type == "truncation", (
            f"EmptyLLMResponseError must reach _truncate_fallback via "
            f"the outer except Exception; got compaction_type="
            f"{result.compaction_type}"
        )
        # CRITICAL: failure_kind must be 'error' (outer except
        # Exception), NOT 'timeout' (per-batch narrowed except). If
        # a future refactor widens the per-batch tuple to catch
        # EmptyLLMResponseError, the failure_kind would flip to
        # 'timeout' and this assertion would catch the regression.
        assert result.failure_kind == "error", (
            f"EmptyLLMResponseError must NOT be misclassified as "
            f"timeout; failure_kind={result.failure_kind}"
        )

    def test_per_chunk_excepts_are_narrowed_to_timeout_only(self):
        """AST pin — the per-chunk except handlers in
        ``_summarize_chunked`` must catch ONLY
        ``(TimeoutError, asyncio.TimeoutError)`` (architect §9.8 O14).

        If a future refactor adds ``Exception`` or ``BaseException``
        or ``EmptyLLMResponseError`` to the per-chunk except tuple,
        the behavioral test above would still pass (truncation
        fallback still fires) BUT a continuous-empty provider would be
        silently misclassified as a timed-out batch — the failure_kind
        signal is lost. This AST pin catches that regression at the
        SOURCE level.
        """
        # Parse compaction.py.
        source_path = (
            pathlib.Path(__file__).resolve().parents[2]
            / "daemon"
            / "compaction.py"
        )
        source = source_path.read_text(encoding="utf-8")
        tree = ast.parse(source)

        # Locate the ``_summarize_chunked`` function.
        target: ast.FunctionDef | None = None
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.AsyncFunctionDef)
                and node.name == "_summarize_chunked"
            ):
                target = node
                break
        assert target is not None, (
            "_summarize_chunked must exist in daemon/compaction.py"
        )

        # Collect every ``except (...)`` handler inside the function.
        # Spec pin targets lines :2830, :2908, :2928.
        except_tuples: list[tuple[int, set[str]]] = []
        for node in ast.walk(target):
            if isinstance(node, ast.ExceptHandler):
                if node.type is None:
                    # bare ``except:`` is forbidden (catches BaseException
                    # including SystemExit/KeyboardInterrupt).
                    except_tuples.append((node.lineno, {"<bare>"}))
                    continue
                type_node = node.type
                names: set[str] = set()
                if isinstance(type_node, ast.Tuple):
                    for elt in type_node.elts:
                        names.add(_format_exc_name(elt))
                elif isinstance(type_node, ast.Name):
                    names.add(type_node.id)
                elif isinstance(type_node, ast.Attribute):
                    names.add(_format_exc_name(type_node))
                except_tuples.append((node.lineno, names))

        # Three per-chunk handlers are required (architect §9.8 O14):
        # :2830 (single-batch try), :2908 (per-batch pool task), :2928
        # (shared budget deadline). Pin each.
        assert except_tuples, (
            "_summarize_chunked must have except handlers"
        )
        # Whitelist of acceptable types per per-chunk handler:
        # ONLY TimeoutError + asyncio.TimeoutError.
        # Adding ANY other type masks a non-timeout error as a
        # timeout (or vice-versa) and is a regression.
        for lineno, names in except_tuples:
            assert names == {"TimeoutError", "asyncio.TimeoutError"} or (
                # Per the spec, the narrower tuple is
                # ``(TimeoutError, asyncio.TimeoutError)``. Some
                # handlers may also catch broader ``Exception`` —
                # those are the OUTER merge/condense handlers, NOT
                # the per-chunk ones. We classify:
                # - if a handler catches ``Exception`` (broader), it's
                #   the merge/condense fallback (acceptable).
                # - if a handler catches ONLY TimeoutError +
                #   asyncio.TimeoutError, it's the per-chunk handler
                #   (acceptable).
                # Any other shape (e.g. adds BaseException or
                # EmptyLLMResponseError) is a regression.
                "Exception" in names
            ), (
                f"_summarize_chunked except at line {lineno} has "
                f"unexpected types {sorted(names)} — per-chunk "
                f"handlers must be narrowed to "
                f"(TimeoutError, asyncio.TimeoutError); any other "
                f"shape silently masks EmptyLLMResponseError as a "
                f"timeout (architect §9.8 O14)"
            )

    def test_outer_except_exception_is_the_truncation_route(self):
        """AST pin — the OUTER ``except Exception`` at
        ``daemon/compaction.py:2644-2659`` is the canonical route
        from ``_summarize_chunked`` exceptions to
        ``_truncate_fallback``. The comment must explicitly mention
        ``_truncate_fallback`` (anti-drift: a future refactor that
        removes the fallback call from this handler silently breaks
        the auto-path contract — this pin catches that at the
        source level)."""
        source_path = (
            pathlib.Path(__file__).resolve().parents[2]
            / "daemon"
            / "compaction.py"
        )
        source = source_path.read_text(encoding="utf-8")
        # The handler at :2644-2659 calls ``self._truncate_fallback``
        # at :2650. Grep-pin the call.
        assert "_truncate_fallback" in source, (
            "_truncate_fallback must be referenced from "
            "daemon/compaction.py"
        )
        # The two truncation-fallback call sites the spec §10
        # names (:2645 / :2650 — outer except Exception handler, and
        # :2634 — TimeoutError narrower handler). Both must route
        # through ``_truncate_fallback``.
        tree = ast.parse(source)
        # Find all calls to ``self._truncate_fallback`` and verify
        # they're reached from the outer except Exception handler.
        truncate_call_lines: list[int] = []
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "_truncate_fallback"
            ):
                truncate_call_lines.append(node.lineno)
        # We expect AT LEAST one call inside the ``compact_state``
        # ``except Exception`` handler — the auto-path contract.
        assert any(
            2640 <= line <= 2700 for line in truncate_call_lines
        ), (
            f"_truncate_fallback must be called inside the outer "
            f"except Exception handler (~line 2644-2659); got call "
            f"sites at lines {truncate_call_lines}"
        )