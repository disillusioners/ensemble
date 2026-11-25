"""TIER-A de-dup (row2-2a) — the context-usage path's cheap pre-filter.

The usage path counted the WHOLE history through tiktoken on EVERY
emit (every node update of a turn, and every SSE connect) BEFORE the
dedup comparison decided whether the event was worth broadcasting.

TIER-A moves a cheap "did the history change?" comparison AHEAD of the
count so an unchanged history skips the count. INVARIANT: the EMITTED
value is unchanged — only the recomputation of an unchanged value is
skipped.

The skip is only sound when the pre-filter PROVES the history is
unchanged, so the pre-filter compares the estimator's INPUTS (the
message count first, then a fingerprint of every field the estimator
reads). This module pins:

* an unchanged history ⇒ no re-count, same emitted value,
* a CHANGED history (same message COUNT, different content — the
  operator-note replacement path) ⇒ re-counted, new emitted value. A
  message-count-only proxy would silently emit a stale value here,
* ``force=True`` ⇒ still broadcasts, same emitted value.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

import daemon.services.instance_messaging as messaging_module
from daemon.loader import estimate_messages_tokens


def _make_service(system_tokens: int = 0):
    from daemon.services.instance_messaging import InstanceMessagingService

    service = InstanceMessagingService.__new__(InstanceMessagingService)
    service._manager = MagicMock()
    service._manager._last_context_usage = {}
    service._manager._live_hub = MagicMock()
    service._manager._live_hub.stream_context_usage = AsyncMock()
    service._manager.config = MagicMock()
    service._manager.config.llm.model = "gpt-4o"
    service._manager.config.compaction = MagicMock()
    service._get_system_prompt_tokens = AsyncMock(return_value=system_tokens)
    return service


class _Counter:
    """Counts the calls to the token estimator."""

    def __init__(self):
        self.calls = 0

    def __call__(self, msgs):
        self.calls += 1
        return estimate_messages_tokens(msgs)


@pytest.fixture
def counted(monkeypatch):
    counter = _Counter()
    monkeypatch.setattr(
        messaging_module, "estimate_messages_tokens", counter,
    )
    return counter


class TestUsagePathDedup:
    """The emitted value never changes; only the work does."""

    @pytest.mark.asyncio
    async def test_unchanged_history_is_not_recounted(self, counted):
        """A second emit of the SAME history must not re-count.

        The broadcast is still suppressed (the dedup), and the value the
        first emit produced is unchanged.
        """
        service = _make_service()
        messages = [
            HumanMessage(content="hello", id="m1"),
            AIMessage(content="hi there", id="m2"),
        ]

        await service._emit_context_usage("inst-1", messages)
        assert counted.calls == 1

        await service._emit_context_usage("inst-1", messages)
        assert counted.calls == 1, (
            "an unchanged history must not be re-counted "
            f"(counted {counted.calls}×)"
        )
        # The dedup still suppresses the duplicate broadcast.
        assert service._manager._live_hub.stream_context_usage.await_count == 1

    @pytest.mark.asyncio
    async def test_emitted_value_is_unchanged(self, counted):
        """The emitted token value is the pre-change value."""
        service = _make_service(system_tokens=100)
        messages = [
            HumanMessage(content="hello", id="m1"),
            AIMessage(content="hi there", id="m2"),
            ToolMessage(content="tool output", tool_call_id="c1", name="t", id="m3"),
        ]

        await service._emit_context_usage("inst-1", messages)

        kwargs = service._manager._live_hub.stream_context_usage.await_args.kwargs
        expected = estimate_messages_tokens(messages) + 100
        assert kwargs["tokens"] == expected

    @pytest.mark.asyncio
    async def test_same_count_different_content_is_recounted(self, counted):
        """Same message COUNT, different content ⇒ re-count.

        This is the operator-note replacement path (a message is
        REPLACED in place, so the message count does not move but the
        token count does). A message-count-only proxy would emit a
        STALE value here — the fingerprint catches it.
        """
        service = _make_service()
        messages = [HumanMessage(content="a", id="m1")]

        await service._emit_context_usage("inst-1", messages)
        assert counted.calls == 1

        # Replace the message IN PLACE (same count, different content).
        messages[0] = HumanMessage(content="a much longer body", id="m1")
        await service._emit_context_usage("inst-1", messages)

        assert counted.calls == 2, (
            "a changed history must be re-counted even when the message "
            f"count is unchanged (counted {counted.calls}×)"
        )
        kwargs = service._manager._live_hub.stream_context_usage.await_args.kwargs
        assert kwargs["tokens"] == estimate_messages_tokens(messages)

    @pytest.mark.asyncio
    async def test_appended_message_is_recounted(self, counted):
        """An appended message changes the count ⇒ re-count + new value."""
        service = _make_service()
        messages = [HumanMessage(content="a", id="m1")]

        await service._emit_context_usage("inst-1", messages)
        messages.append(HumanMessage(content="b", id="m2"))
        await service._emit_context_usage("inst-1", messages)

        assert counted.calls == 2
        kwargs = service._manager._live_hub.stream_context_usage.await_args.kwargs
        assert kwargs["tokens"] == estimate_messages_tokens(messages)

    @pytest.mark.asyncio
    async def test_force_true_emits_without_recounting(self, counted):
        """force=True broadcasts even for an unchanged history.

        The SSE-connect path relies on it; the VALUE must be the same
        as the pre-change implementation.
        """
        service = _make_service(system_tokens=50)
        messages = [HumanMessage(content="hello", id="m1")]

        await service._emit_context_usage("inst-1", messages)
        await service._emit_context_usage("inst-1", messages, force=True)

        assert service._manager._live_hub.stream_context_usage.await_count == 2
        assert counted.calls == 1, (
            "force=True on an unchanged history must not re-count "
            f"(counted {counted.calls}×)"
        )
        kwargs = service._manager._live_hub.stream_context_usage.await_args.kwargs
        assert kwargs["tokens"] == estimate_messages_tokens(messages) + 50

    @pytest.mark.asyncio
    async def test_tool_call_fields_are_part_of_the_fingerprint(self, counted):
        """A changed tool-call payload must be detected.

        ``estimate_messages_tokens`` counts the tool-call name and
        arguments — the fingerprint must cover them, or a changed tool
        call would emit a stale value.
        """
        from langchain_core.messages.tool import ToolCall

        service = _make_service()
        tc = ToolCall(name="search", args={"q": "alpha"}, id="call-1")
        messages = [AIMessage(content="", tool_calls=[tc], id="m1")]

        await service._emit_context_usage("inst-1", messages)
        assert counted.calls == 1

        messages[0] = AIMessage(
            content="",
            tool_calls=[ToolCall(name="search", args={"q": "beta"}, id="call-1")],
            id="m1",
        )
        await service._emit_context_usage("inst-1", messages)

        assert counted.calls == 2, (
            "a changed tool-call payload must be re-counted "
            f"(counted {counted.calls}×)"
        )
        kwargs = service._manager._live_hub.stream_context_usage.await_args.kwargs
        assert kwargs["tokens"] == estimate_messages_tokens(messages)

    @pytest.mark.asyncio
    async def test_count_failure_stays_silent(self, monkeypatch):
        """A failing count must not raise — the emit is skipped."""
        service = _make_service()

        def boom(_msgs):
            raise RuntimeError("boom")

        monkeypatch.setattr(messaging_module, "estimate_messages_tokens", boom)
        await service._emit_context_usage("inst-1", [HumanMessage(content="hi")])
        service._manager._live_hub.stream_context_usage.assert_not_awaited()


class TestF3FingerprintBeforeCount:
    """F3 (row2-fix2a) — the miss-path window is closed.

    On the cache-MISS path the fingerprint must be taken BEFORE the count,
    so a mutation in the window cannot pair a NEW fingerprint with an OLD
    count (and a later equal-fingerprint skip could never serve a stale
    count). This pin REDDENS if someone reorders the reads back to the
    buggy shape (fingerprint after the count).
    """

    @pytest.mark.asyncio
    async def test_f3_fingerprint_precedes_count_on_miss_path(self, monkeypatch):
        """On a cache miss, the fingerprint is read BEFORE the count."""
        service = _make_service()
        # Force a cache miss: no cached entry for this instance.
        assert getattr(service, "_usage_history_cache", {}).get("inst-1") is None

        order: list[tuple[str, int]] = []

        real_estimate = messaging_module.estimate_messages_tokens
        real_fingerprint = messaging_module._usage_messages_fingerprint

        def tracked_estimate(msgs):
            order.append(("count", id(msgs)))
            return real_estimate(msgs)

        def tracked_fingerprint(msgs):
            order.append(("fingerprint", id(msgs)))
            return real_fingerprint(msgs)

        monkeypatch.setattr(
            messaging_module, "estimate_messages_tokens", tracked_estimate,
        )
        monkeypatch.setattr(
            messaging_module, "_usage_messages_fingerprint", tracked_fingerprint,
        )

        service._usage_history_tokens(
            "inst-1", [HumanMessage(content="hello", id="m1")],
        )

        fp_idx = next(
            i for i, (kind, _) in enumerate(order) if kind == "fingerprint"
        )
        count_idx = next(
            i for i, (kind, _) in enumerate(order) if kind == "count"
        )
        assert fp_idx < count_idx, (
            "F3 window OPEN: the fingerprint was taken AFTER the count on "
            "the cache-miss path — a mutation in that window could pair a "
            "NEW fingerprint with an OLD count, and a later "
            "equal-fingerprint skip would serve a stale count"
        )

    @pytest.mark.asyncio
    async def test_f3_fingerprint_precedes_count_on_count_mismatch_miss(self, monkeypatch):
        """Even when the message COUNT differs (cached entry present but
        stale), the fingerprint is still taken before the count.
        """
        service = _make_service()
        # Seed a cached entry with a DIFFERENT count so the miss path
        # takes the "count differs" branch.
        cache = getattr(service, "_usage_history_cache", None)
        if cache is None:
            cache = {}
            service._usage_history_cache = cache
        cache["inst-1"] = (
            999,  # a count that cannot match a 1-message list
            ("stale", "fingerprint"),
            12345,
        )

        order: list[tuple[str, int]] = []

        real_estimate = messaging_module.estimate_messages_tokens
        real_fingerprint = messaging_module._usage_messages_fingerprint

        def tracked_estimate(msgs):
            order.append(("count", id(msgs)))
            return real_estimate(msgs)

        def tracked_fingerprint(msgs):
            order.append(("fingerprint", id(msgs)))
            return real_fingerprint(msgs)

        monkeypatch.setattr(
            messaging_module, "estimate_messages_tokens", tracked_estimate,
        )
        monkeypatch.setattr(
            messaging_module, "_usage_messages_fingerprint", tracked_fingerprint,
        )

        service._usage_history_tokens(
            "inst-1", [HumanMessage(content="hello", id="m1")],
        )

        fp_idx = next(
            i for i, (kind, _) in enumerate(order) if kind == "fingerprint"
        )
        count_idx = next(
            i for i, (kind, _) in enumerate(order) if kind == "count"
        )
        assert fp_idx < count_idx, (
            "F3 window OPEN on the count-mismatch miss path: the "
            "fingerprint was taken AFTER the count"
        )

    @pytest.mark.asyncio
    async def test_f3_fingerprint_precedes_count_on_fingerprint_mismatch_miss(self, monkeypatch):
        """Even when the count matches but the fingerprint differs (a
        content change), the fingerprint is still taken before the count.
        """
        service = _make_service()
        msgs = [HumanMessage(content="hello", id="m1")]
        # Seed a cached entry whose count matches but whose fingerprint
        # differs (simulating a content change).
        cache = getattr(service, "_usage_history_cache", None)
        if cache is None:
            cache = {}
            service._usage_history_cache = cache
        cache["inst-1"] = (
            len(msgs),
            ("a", "different", "fingerprint"),
            99999,
        )

        order: list[tuple[str, int]] = []

        real_estimate = messaging_module.estimate_messages_tokens
        real_fingerprint = messaging_module._usage_messages_fingerprint

        def tracked_estimate(msgs):
            order.append(("count", id(msgs)))
            return real_estimate(msgs)

        def tracked_fingerprint(msgs):
            order.append(("fingerprint", id(msgs)))
            return real_fingerprint(msgs)

        monkeypatch.setattr(
            messaging_module, "estimate_messages_tokens", tracked_estimate,
        )
        monkeypatch.setattr(
            messaging_module, "_usage_messages_fingerprint", tracked_fingerprint,
        )

        service._usage_history_tokens("inst-1", msgs)

        fp_idx = next(
            i for i, (kind, _) in enumerate(order) if kind == "fingerprint"
        )
        count_idx = next(
            i for i, (kind, _) in enumerate(order) if kind == "count"
        )
        assert fp_idx < count_idx, (
            "F3 window OPEN on the fingerprint-mismatch miss path: the "
            "fingerprint was taken AFTER the count"
        )


class TestF3MirroredWindow:
    """F3 (row2-fix2a residual) — the MIRRORED fingerprint read.

    The 2A-F3 fix reads the fingerprint BEFORE the count, so a mutation
    in that (no-await) window can no longer pair a NEW fingerprint with
    an OLD count. The residual it leaves: a mutation in the SAME window
    can still pair a PRE-mutation fingerprint with a POST-mutation count
    — and a later equal-fingerprint skip would then serve that
    mismatched count.

    The MIRRORED read re-reads the fingerprint AFTER the count and
    DISCARDS the cache write when the two disagree (the next cycle
    recomputes). The EMITTED value is untouched either way.

    Pins:
    * a mutation BETWEEN the pre-read and the end of the count ⇒ NO
      cache write lands with the stale pair, and the stale count is
      never served by a later equal-fingerprint skip,
    * a STABLE history ⇒ the pair IS written, the later equal-
      fingerprint skip still works, and the emitted value stays
      byte-identical,
    * the mirrored read really happens AFTER the count.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize("mutation", ["append", "replace_content"])
    async def test_mutation_in_count_window_discards_cache_write(
        self, monkeypatch, mutation,
    ):
        """A mutation inside the count window must not land a cache write.

        The mutation lands BETWEEN the pre-count fingerprint read and the
        end of the count, so the pre-read fingerprint describes the OLD
        history while the count describes the NEW one. Caching that pair
        would let a later equal-fingerprint skip serve a count that does
        not belong to that fingerprint.
        """
        service = _make_service()
        msgs = [HumanMessage(content="hello", id="m1")]
        pre_fingerprint = messaging_module._usage_messages_fingerprint(msgs)

        real_estimate = messaging_module.estimate_messages_tokens
        state = {"mutated": False}

        def _mutate(m):
            if mutation == "append":
                # The message COUNT changes inside the window.
                m.append(HumanMessage(content="injected mid-count", id="m2"))
            else:
                # Same COUNT, different content (the replacement path).
                m[0] = HumanMessage(content="hello (mutated)", id="m1")

        def mutating_estimate(m):
            if not state["mutated"]:
                state["mutated"] = True
                _mutate(m)
            return real_estimate(m)

        monkeypatch.setattr(
            messaging_module, "estimate_messages_tokens", mutating_estimate,
        )

        tokens = service._usage_history_tokens("inst-1", msgs)

        # The EMITTED value is unchanged by the mirror: it is the count
        # of the list as it was at COUNT TIME (i.e. post-mutation here).
        assert tokens == real_estimate(msgs)

        cache = getattr(service, "_usage_history_cache", {})
        stale = [entry for entry in cache.values() if entry[1] == pre_fingerprint]
        assert not stale, (
            "F3 window OPEN: the PRE-mutation fingerprint was cached "
            "together with the POST-mutation count — a later "
            "equal-fingerprint skip would serve a mismatched count "
            f"(cached entries: {list(cache.values())!r})"
        )

    @pytest.mark.asyncio
    async def test_stale_count_is_never_served_by_a_later_skip(self, monkeypatch):
        """The residual, end to end: the stale count is never served.

        Cycle 1 mutates inside the count window. Cycle 2 asks for an
        EQUAL-fingerprint history (the pre-mutation state) — it must be
        RECOMPUTED, not served from a mismatched cache entry.
        """
        service = _make_service()
        msgs = [HumanMessage(content="hello", id="m1")]

        real_estimate = messaging_module.estimate_messages_tokens
        calls = {"n": 0}

        def mutating_estimate(m):
            calls["n"] += 1
            if calls["n"] == 1:
                m.append(HumanMessage(content="injected mid-count", id="m2"))
            return real_estimate(m)

        monkeypatch.setattr(
            messaging_module, "estimate_messages_tokens", mutating_estimate,
        )

        stale_tokens = service._usage_history_tokens("inst-1", msgs)
        pre_state_tokens = real_estimate([HumanMessage(content="hello", id="m1")])

        # A later call with an equal-fingerprint (pre-mutation) history.
        later = service._usage_history_tokens(
            "inst-1", [HumanMessage(content="hello", id="m1")],
        )

        assert calls["n"] == 2, (
            "the equal-fingerprint history was served from the cache "
            "instead of being recomputed — the mismatched count would "
            "be served"
        )
        assert later == pre_state_tokens
        assert later != stale_tokens, (
            "the stale (post-mutation) count was served for a history it "
            "does not describe"
        )

    @pytest.mark.asyncio
    async def test_stable_history_writes_the_pair_and_the_skip_still_works(self, counted):
        """A stable history ⇒ the pair IS cached and the skip still works.

        The mirrored read must NOT discard a write on an unchanged
        history — otherwise the tier-A de-dup silently degrades into a
        full re-count on every emit.
        """
        service = _make_service()
        msgs = [HumanMessage(content="hello", id="m1")]

        tokens = service._usage_history_tokens("inst-1", msgs)
        assert counted.calls == 1
        # The emitted value stays byte-identical to the direct estimate.
        assert tokens == estimate_messages_tokens(msgs)

        cache = getattr(service, "_usage_history_cache", {})
        assert "inst-1" in cache, (
            "the stable pair was NOT cached — the mirror read must not "
            "discard the write on an unchanged history"
        )
        entry = cache["inst-1"]
        assert entry[0] == len(msgs)
        assert entry[1] == messaging_module._usage_messages_fingerprint(msgs)
        assert entry[2] == tokens

        # A later equal-fingerprint history is served from the cache.
        later = service._usage_history_tokens(
            "inst-1", [HumanMessage(content="hello", id="m1")],
        )
        assert counted.calls == 1, (
            "the later equal-fingerprint history was re-counted — the "
            "cache write must survive a stable history "
            f"(counted {counted.calls}×)"
        )
        assert later == tokens

    @pytest.mark.asyncio
    async def test_mirror_reads_the_fingerprint_after_the_count(self, monkeypatch):
        """The mirrored read must come AFTER the count.

        Without it the pre-count fingerprint is the only one read, and a
        mutation inside the count window goes unnoticed.
        """
        service = _make_service()
        order: list[tuple[str, int]] = []

        real_estimate = messaging_module.estimate_messages_tokens
        real_fingerprint = messaging_module._usage_messages_fingerprint

        def tracked_estimate(msgs):
            order.append(("count", id(msgs)))
            return real_estimate(msgs)

        def tracked_fingerprint(msgs):
            order.append(("fingerprint", id(msgs)))
            return real_fingerprint(msgs)

        monkeypatch.setattr(
            messaging_module, "estimate_messages_tokens", tracked_estimate,
        )
        monkeypatch.setattr(
            messaging_module, "_usage_messages_fingerprint", tracked_fingerprint,
        )

        service._usage_history_tokens("inst-1", [HumanMessage(content="hello", id="m1")])

        kinds = [kind for kind, _ in order]
        assert kinds.count("fingerprint") >= 2, (
            "F3 mirror MISSING: the fingerprint was not re-read after the "
            f"count (read order: {kinds!r})"
        )
        count_idx = kinds.index("count")
        last_fp_idx = max(i for i, (kind, _) in enumerate(order) if kind == "fingerprint")
        assert last_fp_idx > count_idx, (
            "F3 mirror MISSING: the fingerprint was not re-read AFTER the "
            f"count (read order: {kinds!r})"
        )
