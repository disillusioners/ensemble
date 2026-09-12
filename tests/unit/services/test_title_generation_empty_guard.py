"""Unit tests for the empty-response-guard secondary surface: title generation.

Spec §10 (``.agents/shared/planning/empty-response-guard/
architecture-recommendation.md``) calls out title-gen as one of the 5
facade-wrapped secondary surfaces. The behavioral contract: an empty
LLM response must trigger the retry ladder and, after exhaustion,
leave the instance's title UNSTORED — never crash, never silently
swallow. The keyword-extraction test
(``tests/unit/services/test_keyword_extraction.py::TestExtractKeywords::
test_empty_response_returns_empty``) is the model for this style:
patch the LLM class, let the real facade retry, assert the
never-raises fallback.

These tests are the §10 behavioral pins for the title-gen surface —
no daemon edits, test-only authoring per leader constraint.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import AIMessage

from daemon.response_validation import (
    EmptyLLMResponseError,
    LLMResponseValidationError,
    install_empty_guard_config,
    validate_llm_response,
)


class _FakeAIMessage:
    """AIMessage stand-in that accepts ``content=None`` and ``content=""``.

    LangChain's ``AIMessage`` is Pydantic-validated and rejects
    ``content=None`` at construction (``.venv/lib/python3.13/site-packages
    /langchain_core/messages/ai.py:228``). Non-streaming providers can
    nonetheless yield a ``None`` content — the spec pins both shapes
    (``.agents/shared/planning/empty-response-guard/
    architecture-recommendation.md`` §11(a)). This stand-in mirrors
    the keyword-extraction test's ``_FakeResponse`` (``test_keyword_
    extraction.py:319``) and exposes just the attributes the title-gen
    site and the validator consume (``content``, ``additional_kwargs``,
    ``tool_calls``, ``response_metadata``).
    """

    def __init__(self, content: Any) -> None:
        self.content = content
        self.additional_kwargs: dict = {}
        self.tool_calls: list = []
        self.response_metadata: dict = {}
        self.id: str | None = None
        self.type: str = "ai"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_title_manager():
    """Build a minimal mock InstanceManager facade for ``TitleGenerationService``.

    The title-gen code touches only ``self._config`` (an LLMConfig-like
    property), ``self._instance_repository.get`` (the TOCTOU-window
    metadata read), and ``self._instance_repository.update_title`` (the
    skip-store assertion target). Everything else is stubbed.
    """
    manager = MagicMock()
    cfg = MagicMock()
    cfg.llm.base_url = "http://localhost:1234/v1"
    cfg.llm.base_url_backup = "http://localhost:1234/v2"
    cfg.llm.api_key = "test-key"
    cfg.llm.model_title = "gpt-4o-mini"
    cfg.llm.buffer_response_header = True
    manager.config = cfg
    # TOCTOU-window read: returns metadata WITHOUT a pre-existing title
    # (so title-gen proceeds to the LLM call).
    manager._instance_repository.get = MagicMock(
        return_value=MagicMock(instance_metadata={})
    )
    manager._instance_repository.update_title = MagicMock()
    return manager


class _CountingFailoverWrapper:
    """Stand-in for ``wrap_langchain_failover`` with bounded retry-on-validation.

    Mirrors the real facade's retry contract for this test surface:
    each ``invoke`` re-runs ``validate_llm_response`` (the S1 guard);
    ``EmptyLLMResponseError`` / ``LLMResponseValidationError`` are
    classified as transient by the real ``RetryByCategory`` predicate
    and trigger a bounded retry loop. We DON'T use the real facade
    here — its 45s default ``wall_clock_cap_s`` plus
    ``wait_exponential_jitter`` initial=1s backoff is too slow for a
    fast unit test. The real facade's retry-and-fail behavior is
    pinned in ``tests/unit/services/test_keyword_extraction.py`` and
    ``tests/unit/test_empty_response_guard.py`` — these tests verify
    the SITE-level contract (title-gen's ``except Exception`` catches
    the eventual re-raise and skips the store).

    Counts attempts so the test can assert the retry ladder FIRED.
    """

    def __init__(
        self,
        inner_llm: Any,
        *,
        empty_content: Any,
        max_attempts: int = 4,
    ) -> None:
        self._inner = inner_llm
        self._empty = empty_content
        self._max = max_attempts
        self.attempt_count = 0
        self.last_messages: list | None = None

    def invoke(self, messages: list) -> AIMessage:
        self.last_messages = messages
        last_exc: Exception | None = None
        for attempt in range(self._max):
            self.attempt_count += 1
            try:
                result = self._inner.invoke(messages)
                # Replicate the real facade's classify step: validate
                # the response INSIDE the retry scope. An empty
                # response raises ``EmptyLLMResponseError`` (S1).
                validate_llm_response(result, input_messages=messages)
                return result
            except (EmptyLLMResponseError, LLMResponseValidationError) as e:
                last_exc = e
                # Continue the bounded retry loop (real tenacity
                # stops after ``max_attempts`` OR ``wall_clock_cap_s``;
                # we approximate with attempts only — the wall-clock
                # cap is exercised by the keyword-extraction test).
                continue
        # Exhaustion: re-raise the last validation error. Title-gen's
        # ``except Exception`` block at
        # ``daemon/services/title_generation.py:190`` must catch this
        # and leave the title UNSTORED.
        assert last_exc is not None  # only reachable if a raise fired
        raise last_exc


def _build_wrapper_with_backup(
    inner_llm: Any, *, empty_content: Any, max_attempts: int = 4
) -> _CountingFailoverWrapper:
    """Build a wrapper variant for the with-backup-config case."""
    wrapper = _CountingFailoverWrapper(
        inner_llm,
        empty_content=empty_content,
        max_attempts=max_attempts,
    )
    wrapper.backup_configured = True
    return wrapper


def _build_wrapper_no_backup(
    inner_llm: Any, *, empty_content: Any, max_attempts: int = 3
) -> _CountingFailoverWrapper:
    """Build a wrapper variant for the no-backup-config case.

    No-backup retry budget is smaller (the facade derives
    ``max_attempts`` from the operator-configured transient/timeout
    slices without the HA split — see
    ``daemon/services/llm_failover.py:545``). 3 attempts is the
    canonical single-endpoint budget.
    """
    wrapper = _CountingFailoverWrapper(
        inner_llm,
        empty_content=empty_content,
        max_attempts=max_attempts,
    )
    wrapper.backup_configured = False
    return wrapper


# ---------------------------------------------------------------------------
# Tests — scenario 1: title-gen empty → retry ladder → skip-store
# ---------------------------------------------------------------------------


class TestTitleGenEmptyResponseSkipStore:
    """Spec §10 — title-gen empty LLM response: retry-then-skip-store.

    The behavioral contract for an empty LLM response at the title-gen
    surface is:

    1. The retry ladder (bounded tenacity retries inside the facade)
       must fire — at least one re-attempt after the initial invoke.
    2. After exhaustion, an ``EmptyLLMResponseError`` (or its parent
       ``LLMResponseValidationError``) MUST escape to the title-gen
       caller.
    3. The title-gen call site at
       ``daemon/services/title_generation.py:190`` wraps the call in
       ``except Exception`` — the re-raise is caught, logged at
       WARNING, and the function returns without storing a title.
    4. The repository's ``update_title`` method must NEVER be invoked
       (the "skip-store" half of the contract).
    5. No crash propagates to the caller (the function is fire-and-
       forget — the caller awaits the task and a raised exception
       would surface as an unhandled task warning).

    Both ``content=""`` and ``content=None`` shapes are covered (per
    spec §11(a) — non-streaming providers may yield either).
    Both backup-configured and no-backup configurations are covered
    (the facade's retry budget differs slightly between the two; the
    site-level skip-store contract is identical).
    """

    @pytest.mark.asyncio
    async def test_empty_string_response_no_title_stored_with_backup(self):
        """``content=""`` shape, backup configured: retry ladder fires,
        no title stored, no crash."""
        install_empty_guard_config(enabled=True, compaction_skip=False)
        manager = _make_title_manager()
        cfg = manager.config
        cfg.llm.base_url_backup = "http://localhost:1234/v2"

        # LLM-side mock: always returns an empty AIMessage (the spec
        # shape most likely to come from a streaming aggregation).
        inner_llm = MagicMock()
        inner_llm.invoke = MagicMock(return_value=AIMessage(content=""))

        wrapper = _build_wrapper_with_backup(
            inner_llm, empty_content="", max_attempts=4
        )

        # Build the title-gen service against the mocked manager.
        from daemon.services.title_generation import TitleGenerationService

        svc = TitleGenerationService(manager=manager)

        with patch(
            "daemon.graph.ThinkingChatOpenAI",
            return_value=inner_llm,
            create=True,
        ), patch(
            "daemon.services.title_generation.wrap_langchain_failover",
            return_value=wrapper,
        ):
            # Must not raise — the site-level except swallows the
            # exhausted-retry re-raise.
            await svc._generate_and_broadcast_title(
                instance_id="inst-empty-str-bk",
                message_content="hello world",
            )

        # 1. Retry ladder FIRED — at least 2 attempts (1 initial + >=1 retry).
        assert wrapper.attempt_count >= 2, (
            f"retry ladder did not fire: attempt_count={wrapper.attempt_count}"
        )
        # 2. Skip-store — update_title NEVER called for an empty title.
        manager._instance_repository.update_title.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_string_response_no_title_stored_no_backup(self):
        """``content=""`` shape, no backup: same contract; the retry
        budget is smaller (no failover HA split) but the
        site-level skip-store contract holds."""
        install_empty_guard_config(enabled=True, compaction_skip=False)
        manager = _make_title_manager()
        manager.config.llm.base_url_backup = None

        inner_llm = MagicMock()
        inner_llm.invoke = MagicMock(return_value=AIMessage(content=""))

        wrapper = _build_wrapper_no_backup(
            inner_llm, empty_content="", max_attempts=3
        )

        from daemon.services.title_generation import TitleGenerationService

        svc = TitleGenerationService(manager=manager)

        with patch(
            "daemon.graph.ThinkingChatOpenAI",
            return_value=inner_llm,
            create=True,
        ), patch(
            "daemon.services.title_generation.wrap_langchain_failover",
            return_value=wrapper,
        ):
            await svc._generate_and_broadcast_title(
                instance_id="inst-empty-str-nobk",
                message_content="hello world",
            )

        assert wrapper.attempt_count >= 2, (
            f"retry ladder did not fire (no-backup): "
            f"attempt_count={wrapper.attempt_count}"
        )
        manager._instance_repository.update_title.assert_not_called()

    @pytest.mark.asyncio
    async def test_none_content_response_no_title_stored_with_backup(self):
        """``content=None`` shape (non-streaming yield): same retry-
        then-skip-store contract. The shared predicate
        ``is_empty_llm_content(None) is True`` is pinned in
        ``tests/unit/test_response_validation.py``; this test pins
        the SITE-level reaction to that shape."""
        install_empty_guard_config(enabled=True, compaction_skip=False)
        manager = _make_title_manager()

        inner_llm = MagicMock()
        inner_llm.invoke = MagicMock(return_value=_FakeAIMessage(content=None))

        wrapper = _build_wrapper_with_backup(
            inner_llm, empty_content=None, max_attempts=4
        )

        from daemon.services.title_generation import TitleGenerationService

        svc = TitleGenerationService(manager=manager)

        with patch(
            "daemon.graph.ThinkingChatOpenAI",
            return_value=inner_llm,
            create=True,
        ), patch(
            "daemon.services.title_generation.wrap_langchain_failover",
            return_value=wrapper,
        ):
            # Must not raise — site-level except catches and logs.
            await svc._generate_and_broadcast_title(
                instance_id="inst-none-content-bk",
                message_content="hello world",
            )

        assert wrapper.attempt_count >= 2, (
            f"retry ladder did not fire for content=None: "
            f"attempt_count={wrapper.attempt_count}"
        )
        manager._instance_repository.update_title.assert_not_called()

    @pytest.mark.asyncio
    async def test_think_tag_only_response_no_title_stored(self):
        """``<think>...</think>``-only content is DELIBERATELY non-empty
        per spec §11(b) row 3 — the title-strip path
        (``parse_think_tags`` at title_generation.py:172) leaves the
        title empty AFTER stripping, so the ``if not title: return``
        gate at :176-177 fires and the title is skipped WITHOUT
        consuming the retry ladder. This pins the exemption contract:
        think-tag-only is a designed-empty, never raises, never
        stores, never retries."""
        install_empty_guard_config(enabled=True, compaction_skip=False)
        manager = _make_title_manager()

        inner_llm = MagicMock()
        # ``AIMessage(content="<think>…</think>")`` after think-tag
        # strip yields an empty string → ``if not title: return``.
        inner_llm.invoke = MagicMock(
            return_value=AIMessage(content="<think>…just thinking</think>")
        )

        wrapper = _CountingFailoverWrapper(
            inner_llm, empty_content="", max_attempts=4
        )

        from daemon.services.title_generation import TitleGenerationService

        svc = TitleGenerationService(manager=manager)

        with patch(
            "daemon.graph.ThinkingChatOpenAI",
            return_value=inner_llm,
            create=True,
        ), patch(
            "daemon.services.title_generation.wrap_langchain_failover",
            return_value=wrapper,
        ):
            await svc._generate_and_broadcast_title(
                instance_id="inst-think-only",
                message_content="hello world",
            )

        # Designed-empty: NO retries — the response is non-empty under
        # the shared predicate (think-tag-only), validation passes,
        # the title is then stripped to "" by parse_think_tags, and
        # the ``if not title: return`` gate swallows it.
        assert wrapper.attempt_count == 1, (
            f"think-tag-only exemption violated: a designed-empty must "
            f"NOT consume retry budget; attempt_count={wrapper.attempt_count}"
        )
        manager._instance_repository.update_title.assert_not_called()

    @pytest.mark.asyncio
    async def test_existing_title_skips_llm_entirely(self):
        """TOCTOU-window short-circuit (title_generation.py:78-81): a
        pre-existing title must short-circuit BEFORE the LLM is
        invoked at all. This pins the early-return guard that
        prevents redundant LLM spend."""
        install_empty_guard_config(enabled=True, compaction_skip=False)
        manager = _make_title_manager()
        manager._instance_repository.get = MagicMock(
            return_value=MagicMock(instance_metadata={"title": "Existing"})
        )

        inner_llm = MagicMock()
        inner_llm.invoke = MagicMock(
            return_value=AIMessage(content="a new title")
        )

        wrapper = _CountingFailoverWrapper(
            inner_llm, empty_content="", max_attempts=4
        )

        from daemon.services.title_generation import TitleGenerationService

        svc = TitleGenerationService(manager=manager)

        with patch(
            "daemon.graph.ThinkingChatOpenAI",
            return_value=inner_llm,
            create=True,
        ), patch(
            "daemon.services.title_generation.wrap_langchain_failover",
            return_value=wrapper,
        ):
            await svc._generate_and_broadcast_title(
                instance_id="inst-existing-title",
                message_content="hello world",
            )

        # No LLM call (early return), no retry ladder, no update.
        assert wrapper.attempt_count == 0
        manager._instance_repository.update_title.assert_not_called()

    @pytest.mark.asyncio
    async def test_non_empty_title_stores_after_retry_ladder_unaffected(
        self,
    ):
        """Sanity / regression pin: a NON-empty response on the FIRST
        attempt must NOT trigger the retry ladder and MUST store the
        title. This closes the inverse contract — the retry ladder is
        scoped to validation failures, not all responses."""
        install_empty_guard_config(enabled=True, compaction_skip=False)
        manager = _make_title_manager()

        inner_llm = MagicMock()
        inner_llm.invoke = MagicMock(
            return_value=AIMessage(content="Real Title")
        )

        wrapper = _CountingFailoverWrapper(
            inner_llm, empty_content="", max_attempts=4
        )

        from daemon.services.title_generation import TitleGenerationService

        svc = TitleGenerationService(manager=manager)

        with patch(
            "daemon.graph.ThinkingChatOpenAI",
            return_value=inner_llm,
            create=True,
        ), patch(
            "daemon.services.title_generation.wrap_langchain_failover",
            return_value=wrapper,
        ):
            await svc._generate_and_broadcast_title(
                instance_id="inst-real-title",
                message_content="hello world",
            )

        # Exactly one call (no retry), title stored.
        assert wrapper.attempt_count == 1
        manager._instance_repository.update_title.assert_called_once_with(
            "inst-real-title", "Real Title"
        )


class TestTitleGenEmptyGuardKillSwitch:
    """ENSEMBLE_EMPTY_RESPONSE_GUARD=0 disables the S1 raise at the
    validator (default ON).

    With the master kill-switch OFF, the pre-guard pass-through
    contract holds: an empty response no longer raises
    ``EmptyLLMResponseError``, so the retry ladder does NOT fire, and
    the title-gen site observes the empty content as a "no title"
    early-return (``if not title: return`` at :176-177). This pins
    the kill-switch's effect on the title-gen surface — the test
    must NOT regress by accidentally coupling the site to the
    validator without honoring the master switch.
    """

    @pytest.mark.asyncio
    async def test_guard_off_empty_response_no_retry_no_store(self):
        install_empty_guard_config(enabled=False, compaction_skip=False)
        manager = _make_title_manager()

        inner_llm = MagicMock()
        inner_llm.invoke = MagicMock(return_value=AIMessage(content=""))

        wrapper = _CountingFailoverWrapper(
            inner_llm, empty_content="", max_attempts=4
        )

        from daemon.services.title_generation import TitleGenerationService

        svc = TitleGenerationService(manager=manager)

        with patch(
            "daemon.graph.ThinkingChatOpenAI",
            return_value=inner_llm,
            create=True,
        ), patch(
            "daemon.services.title_generation.wrap_langchain_failover",
            return_value=wrapper,
        ):
            await svc._generate_and_broadcast_title(
                instance_id="inst-guard-off",
                message_content="hello world",
            )

        # OFF: validate passes through → no retry → site sees empty
        # title → if-not-title return → no store.
        assert wrapper.attempt_count == 1, (
            f"kill-switch OFF must suppress the retry ladder; "
            f"attempt_count={wrapper.attempt_count}"
        )
        manager._instance_repository.update_title.assert_not_called()