"""Unit tests for compaction preservation of injected messages (Phase 1 / C3).

Covers the proactive (proactive = before LLM call, scheduled) compaction
path in ``daemon.compaction.ContextCompactor.compact_state``:

    * Messages flagged with ``additional_kwargs={'injected_message': True}``
      are NOT included in summarization — they survive verbatim in the
      replacement list.
    * When ALL messages are injected, compaction is skipped (no
      summarizable content).
    * When only some messages are injected, the regular ones are
      summarized and the injected ones are appended to the result.
    * Injected messages are NOT touched by ``RemoveMessage`` entries in
      the replacement list — they remain in the conversation.

These tests stub the summarization LLM so we exercise the partitioning
logic without actually invoking an LLM.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, SystemMessage


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_compactor(llm_response: str = "summary content"):
    """Build a ContextCompactor with a stubbed summarization LLM.

    Threshold / context window are tuned so a small set of long
    regular messages (>= ~150 tokens) reliably triggers compaction in
    unit tests, without needing to actually invoke a real LLM.
    """
    from daemon.compaction import ContextCompactor

    config = MagicMock()
    # Tight window + low threshold so even short test messages trigger
    config.threshold = 0.5
    config.min_messages_before_compaction = 3
    config.recent_message_window = 2
    config.min_recent_window = 1
    config.target_ratio = 0.5
    config.summarization_chunk_threshold = 1.0  # never chunk in these tests
    # Use a small effective context window so threshold is reachable.
    config.context_window_overrides = {"test-model": 200}
    config.context_window_default = 0

    llm_config = {
        "model": "test-model",
        "base_url": "http://test",
        "api_key": "sk-test",
        "temperature": 0.0,
    }

    compactor = ContextCompactor(config=config, llm_config=llm_config)

    # Stub the summarization LLM so _call_summarization_llm returns a fixed string.
    async def fake_call(prompt, ctx):
        return llm_response

    compactor._call_summarization_llm = fake_call  # type: ignore[method-assign]
    return compactor


def _build_messages(
    n_regular: int,
    n_injected: int,
    prefix_regular: str = "regular",
    prefix_injected: str = "USER-INJECT",
) -> list:
    """Build a flat list of n_regular + n_injected messages, alternating."""
    msgs = []
    for i in range(n_regular):
        msgs.append(HumanMessage(content=f"{prefix_regular}-{i}"))
    for i in range(n_injected):
        msgs.append(
            HumanMessage(
                content=f"{prefix_injected}-{i}",
                additional_kwargs={"injected_message": True},
            )
        )
    return msgs


def _build_context(messages: list, model: str = "test-model"):
    from daemon.compaction import CompactionContext

    config = MagicMock()
    config.threshold = 0.5
    config.min_messages_before_compaction = 3
    config.recent_message_window = 2
    config.min_recent_window = 1
    config.target_ratio = 0.5
    config.summarization_chunk_threshold = 1.0
    # Match _make_compactor: small context window so threshold is reachable
    # by short test messages. Without this, get_model_context_limit falls
    # back to DEFAULT_CONTEXT_LIMIT=180000 and 6 short messages never
    # exceed the threshold.
    config.context_window_overrides = {"test-model": 200}
    config.context_window_default = 0

    return CompactionContext(
        messages=messages,
        system_prompt_tokens=0,
        model_name=model,
        config=config,
        llm_config={
            "model": model,
            "base_url": "http://test",
            "api_key": "sk-test",
            "temperature": 0.0,
        },
        last_compacted_at=None,
    )


# ---------------------------------------------------------------------------
# Proactive compaction: skip injected_message flagged messages (C3)
# ---------------------------------------------------------------------------


class TestProactiveCompactionPreservesInjection:
    """The proactive compaction path must NOT summarize injected messages."""

    @pytest.mark.asyncio
    async def test_injected_messages_appear_in_replacement(self):
        """Injected messages survive in the replacement list verbatim.

        Each regular message is padded to ~30 tokens (well above the
        ``threshold = 0.5 * 200 = 100`` token minimum) so the proactive
        compaction path actually triggers under the test config.
        """
        compactor = _make_compactor()
        pad = "lorem ipsum dolor sit amet consectetur adipiscing elit " * 3
        msgs = (
            [HumanMessage(content=f"r-{i} {pad}", id=f"r-{i}") for i in range(6)]
            + [
                HumanMessage(
                    content="INJECT-A",
                    id="inject-a",
                    additional_kwargs={"injected_message": True},
                ),
                HumanMessage(
                    content="INJECT-B",
                    id="inject-b",
                    additional_kwargs={"injected_message": True},
                ),
            ]
        )
        ctx = _build_context(msgs)

        result = await compactor.compact_state(ctx)

        # Compaction must have occurred
        assert result is not None
        replacement = result.replacement_messages
        # All non-RemoveMessage entries
        kept = [m for m in replacement if not isinstance(m, RemoveMessage)]

        # The two injected HumanMessages must be present in the kept set
        kept_ids = {getattr(m, "id", None) for m in kept}
        assert "inject-a" in kept_ids
        assert "inject-b" in kept_ids

        # The injected messages' content is intact (verbatim preservation)
        inject_a = next(m for m in kept if getattr(m, "id", None) == "inject-a")
        assert inject_a.content == "INJECT-A"
        assert (inject_a.additional_kwargs or {}).get("injected_message") is True

        # The injected messages must NOT have RemoveMessage entries
        # (they were never in the compactable set, so no removal needed).
        removed_ids = {m.id for m in replacement if isinstance(m, RemoveMessage)}
        assert "inject-a" not in removed_ids
        assert "inject-b" not in removed_ids

    @pytest.mark.asyncio
    async def test_injected_messages_not_summarized(self):
        """The summarization LLM must NOT receive injected message content.

        Each regular message is padded so the proactive compaction path
        actually triggers (token threshold check must pass).
        """
        compactor = _make_compactor()
        pad = "lorem ipsum dolor sit amet consectetur adipiscing elit " * 3
        inject_content = "SECRET_USER_DATA_DO_NOT_SUMMARIZE"
        msgs = [
            HumanMessage(content=f"r-{i} {pad}", id=f"r-{i}") for i in range(6)
        ] + [
            HumanMessage(
                content=inject_content,
                id="secret",
                additional_kwargs={"injected_message": True},
            ),
        ]
        ctx = _build_context(msgs)

        # Capture every prompt passed to the summarization LLM
        seen_prompts: list[str] = []

        async def spy(prompt, ctx):
            seen_prompts.append(prompt)
            return "summary"

        compactor._call_summarization_llm = spy  # type: ignore[method-assign]

        result = await compactor.compact_state(ctx)
        assert result is not None

        # At least one summarization call happened
        assert seen_prompts, "expected summarization call"
        # None of the prompts contain the injected content
        for prompt in seen_prompts:
            assert inject_content not in prompt, (
                "Injected message leaked into summarization prompt"
            )

    @pytest.mark.asyncio
    async def test_all_injected_messages_skips_compaction(self):
        """When every message is injected, compaction is skipped entirely."""
        compactor = _make_compactor()
        msgs = [
            HumanMessage(
                content=f"inject-{i}",
                id=f"i-{i}",
                additional_kwargs={"injected_message": True},
            )
            for i in range(5)
        ]
        ctx = _build_context(msgs)

        result = await compactor.compact_state(ctx)

        # No compaction should occur — there's nothing to summarize
        assert result is None


# ---------------------------------------------------------------------------
# Reactive compaction re-append (C3) — already exercised in test_injection_graph
# but we add a minimal sanity check here for the partition helper.
# ---------------------------------------------------------------------------


class TestInjectionPartitioningHelper:
    """Verify the _partition_injected_messages helper directly."""

    def test_partitions_by_injected_flag(self):
        from daemon.compaction import _partition_injected_messages

        regular = [HumanMessage(content="r1"), HumanMessage(content="r2")]
        injected = [
            HumanMessage(content="i1", additional_kwargs={"injected_message": True}),
            HumanMessage(content="i2", additional_kwargs={"injected_message": True}),
        ]
        msgs = regular + injected

        non_inj, inj = _partition_injected_messages(msgs)
        assert len(non_inj) == 2
        assert len(inj) == 2
        # Order preserved within each bucket
        assert [m.content for m in non_inj] == ["r1", "r2"]
        assert [m.content for m in inj] == ["i1", "i2"]

    def test_handles_missing_additional_kwargs(self):
        from daemon.compaction import _partition_injected_messages

        # Old/edge-case messages without additional_kwargs at all
        msg_no_kwargs = HumanMessage(content="legacy")
        # LangChain usually defaults additional_kwargs to {}
        msgs = [msg_no_kwargs]

        non_inj, inj = _partition_injected_messages(msgs)
        assert len(non_inj) == 1
        assert len(inj) == 0

    def test_handles_empty_list(self):
        from daemon.compaction import _partition_injected_messages

        non_inj, inj = _partition_injected_messages([])
        assert non_inj == []
        assert inj == []

    def test_is_injected_message_helper(self):
        from daemon.compaction import _is_injected_message

        # Truthy
        m_inj = HumanMessage(content="x", additional_kwargs={"injected_message": True})
        assert _is_injected_message(m_inj) is True

        # Falsy variants
        assert _is_injected_message(HumanMessage(content="x")) is False
        assert (
            _is_injected_message(
                HumanMessage(content="x", additional_kwargs={"other": True})
            )
            is False
        )
        assert (
            _is_injected_message(
                HumanMessage(content="x", additional_kwargs={"injected_message": False})
            )
            is False
        )

        # language_check_reminder messages are NOT injected (different feature)
        m_lc = HumanMessage(
            content="x",
            additional_kwargs={"language_check_reminder": True},
        )
        assert _is_injected_message(m_lc) is False