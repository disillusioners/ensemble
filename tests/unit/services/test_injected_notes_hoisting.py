"""Injected-notes hoisting contract — unit tests.

Contract (injected-notes hoisting fix, base feb5e915):

1. ``context_kind`` messages (real ``[SYSTEM CONTEXT]`` blocks stamped by
   ``_make_context_message``) are PERMANENTLY non-selectable: preserved
   verbatim and hoisted above the compaction doc — unchanged.
2. Bare-flag injected notes (operator notes via the FIFO injection
   drain) become SELECTABLE for summarization once ANSWERED — defined
   conservatively: an ``AIMessage`` exists at a LATER index in the
   channel order. Unanswered notes stay preserved verbatim and hoisted.
3. Answered notes join the selectable pool AND the relief budget; the
   hoist order ``[permanent-injected…][doc][tail]`` contains NO
   answered notes (they are absorbed into the compacted span).
4. SAFETY INVARIANT: an UNANSWERED note is NEVER summarized/absorbed —
   note as the last message, or followed only by ToolMessages.
5. Envelope observability: ``injected_preserved`` (context_kind +
   unanswered) vs ``injected_absorbed`` (answered notes summarized).

These tests stub the summarization LLM (``_call_summarization_llm``)
so batch, merge, condense, and bounded-GLOBAL paths all resolve
without network or ``ThinkingChatOpenAI`` construction.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    RemoveMessage,
    SystemMessage,
    ToolMessage,
)


PAD = "lorem ipsum dolor sit amet consectetur adipiscing elit " * 3


# ---------------------------------------------------------------------------
# Helpers (mirror tests/test_injection_compaction.py fixtures)
# ---------------------------------------------------------------------------


def _make_compactor(llm_response: str = "summary content"):
    """ContextCompactor with a stubbed summarization LLM and a tight
    200-token window so short padded fixtures cross the threshold."""
    from daemon.compaction import ContextCompactor

    config = MagicMock()
    config.threshold = 0.5
    config.min_messages_before_compaction = 3
    config.recent_message_window = 2
    config.min_recent_window = 1
    config.target_ratio = 0.5
    config.summarization_chunk_threshold = 1.0
    config.context_window_overrides = {"test-model": 200}
    config.context_window_default = 0

    compactor = ContextCompactor(config=config, llm_config={
        "model": "test-model",
        "base_url": "http://test",
        "api_key": "sk-test",
        "temperature": 0.0,
    })

    async def fake_call(prompt, ctx):
        return llm_response

    compactor._call_summarization_llm = fake_call  # type: ignore[method-assign]
    return compactor


def _build_context(messages: list, model: str = "test-model"):
    from daemon.compaction import CompactionContext

    config = MagicMock()
    config.threshold = 0.5
    config.min_messages_before_compaction = 3
    config.recent_message_window = 2
    config.min_recent_window = 1
    config.target_ratio = 0.5
    config.summarization_chunk_threshold = 1.0
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


def _bare_note(content: str, msg_id: str) -> HumanMessage:
    """Operator note: bare ``injected_message=True``, NO context_kind."""
    return HumanMessage(
        content=content,
        id=msg_id,
        additional_kwargs={"injected_message": True},
    )


def _ctx_note(content: str, msg_id: str) -> HumanMessage:
    """Real ``[SYSTEM CONTEXT]`` block: flag + context_kind."""
    return HumanMessage(
        content=content,
        id=msg_id,
        additional_kwargs={
            "injected_message": True,
            "context_kind": "task_context",
        },
    )


def _padded_regulars(prefix: str, ids: list[str]) -> list:
    return [
        HumanMessage(content=f"{prefix}-{i} {PAD}", id=ids[i])
        for i in range(len(ids))
    ]


def _spy_prompts(compactor) -> list[str]:
    seen: list[str] = []

    async def spy(prompt, ctx):
        seen.append(prompt)
        return "summary"

    compactor._call_summarization_llm = spy  # type: ignore[method-assign]
    return seen


# ---------------------------------------------------------------------------
# 1. Answered note absorbed
# ---------------------------------------------------------------------------


class TestAnsweredNoteAbsorbed:
    @pytest.mark.asyncio
    async def test_answered_note_joins_selectable_pool_and_is_absorbed(self):
        """A bare note followed by a later AIMessage is absorbed into
        the compacted span: its id vanishes from the replacement, its
        content reaches the summarizer, and the envelope counts it as
        ``injected_absorbed`` (not ``injected_preserved``).
        """
        compactor = _make_compactor()
        seen = _spy_prompts(compactor)
        note = _bare_note("OPS-NOTE-CONTENT-XYZ", "note-1")
        msgs = (
            _padded_regulars("early", ["e-0", "e-1", "e-2"])
            + [note, AIMessage(content="noted, thanks", id="ai-ack")]
            + _padded_regulars("late", ["l-0", "l-1", "l-2", "l-3"])
        )
        ctx = _build_context(msgs)

        result = await compactor.compact_state(ctx)

        assert result is not None
        assert result.compaction_type == "summarization", (
            "answered note + regular history must compact (no skip)"
        )
        kept_ids = {
            m.id
            for m in result.replacement_messages
            if not isinstance(m, RemoveMessage)
        }
        # Absorbed: the note's id no longer exists in the channel —
        # the doc replaced the span it lived in.
        assert "note-1" not in kept_ids
        # It joined the SELECTABLE pool: its content was sent to the
        # summarizer (the inverse of the old permanent-preserve pin).
        assert any("OPS-NOTE-CONTENT-XYZ" in p for p in seen), (
            "answered note never reached the summarizer — it did not "
            "join the selectable pool"
        )
        # Envelope: absorbed counted, preserved not.
        assert result.injected_absorbed == 1
        assert result.injected_preserved == 0


# ---------------------------------------------------------------------------
# 2. context_kind preserved EVEN WHEN answered
# ---------------------------------------------------------------------------


class TestContextKindPermanent:
    @pytest.mark.asyncio
    async def test_context_kind_note_preserved_even_when_answered(self):
        """A context_kind note sits at index 0 with MANY later
        AIMessages (answered by position) — it must STILL be preserved
        verbatim and hoisted, never summarized.
        """
        compactor = _make_compactor()
        seen = _spy_prompts(compactor)
        ctx_note = _ctx_note("[SYSTEM CONTEXT: T]\nCTX-BODY-XYZ", "ctx-1")
        msgs = [ctx_note]
        for i in range(6):
            msgs.append(HumanMessage(content=f"h-{i} {PAD}", id=f"h-{i}"))
            msgs.append(AIMessage(content=f"a-{i} reply to h-{i}", id=f"a-{i}"))
        ctx = _build_context(msgs)

        result = await compactor.compact_state(ctx)

        assert result is not None
        assert result.compaction_type == "summarization"
        kept = [
            m for m in result.replacement_messages
            if not isinstance(m, RemoveMessage)
        ]
        kept_ids = {m.id for m in kept}
        assert "ctx-1" in kept_ids, "context_kind note must survive"
        ctx_kept = next(m for m in kept if m.id == "ctx-1")
        assert ctx_kept.content == "[SYSTEM CONTEXT: T]\nCTX-BODY-XYZ"
        assert (ctx_kept.additional_kwargs or {}).get("context_kind") == "task_context"
        # Never summarized.
        for prompt in seen:
            assert "CTX-BODY-XYZ" not in prompt, (
                "context_kind note leaked into the summarization prompt"
            )
        # Envelope: preserved, not absorbed.
        assert result.injected_preserved == 1
        assert result.injected_absorbed == 0


# ---------------------------------------------------------------------------
# 3. SAFETY INVARIANT — unanswered notes are NEVER absorbed
# ---------------------------------------------------------------------------


class TestUnansweredNoteSafety:
    @pytest.mark.asyncio
    async def test_note_as_last_message_is_preserved_verbatim(self):
        """(a) The note is the LAST message — no later AIMessage →
        preserved verbatim, never summarized.
        """
        compactor = _make_compactor()
        seen = _spy_prompts(compactor)
        note = _bare_note("UNANSWERED-NOTE-TAIL", "note-last")
        msgs = (
            _padded_regulars("h", ["h-0", "h-1", "h-2", "h-3", "h-4", "h-5"])
            + [AIMessage(content="done", id="ai-0"), note]
        )
        ctx = _build_context(msgs)

        result = await compactor.compact_state(ctx)

        assert result is not None
        kept = [
            m for m in result.replacement_messages
            if not isinstance(m, RemoveMessage)
        ]
        kept_ids = {m.id for m in kept}
        assert "note-last" in kept_ids
        note_kept = next(m for m in kept if m.id == "note-last")
        assert note_kept.content == "UNANSWERED-NOTE-TAIL"
        for prompt in seen:
            assert "UNANSWERED-NOTE-TAIL" not in prompt, (
                "UNANSWERED note leaked into the summarization prompt"
            )
        assert result.injected_preserved == 1
        assert result.injected_absorbed == 0

    @pytest.mark.asyncio
    async def test_note_followed_only_by_tool_messages_is_preserved(self):
        """(b) The note is followed ONLY by ToolMessages (no later
        AIMessage) → preserved verbatim, never summarized.
        """
        compactor = _make_compactor()
        seen = _spy_prompts(compactor)
        note = _bare_note("UNANSWERED-NOTE-PRETOOL", "note-pre")
        msgs = (
            _padded_regulars("h", ["h-0", "h-1", "h-2", "h-3", "h-4"])
            + [
                AIMessage(
                    content="",
                    id="ai-tc",
                    tool_calls=[
                        {"name": "f", "args": {}, "id": "tc-1", "type": "tool_call"}
                    ],
                ),
                note,
                ToolMessage(tool_call_id="tc-1", content="tool out", id="tool-1"),
            ]
        )
        ctx = _build_context(msgs)

        result = await compactor.compact_state(ctx)

        assert result is not None
        kept = [
            m for m in result.replacement_messages
            if not isinstance(m, RemoveMessage)
        ]
        kept_ids = {m.id for m in kept}
        assert "note-pre" in kept_ids
        note_kept = next(m for m in kept if m.id == "note-pre")
        assert note_kept.content == "UNANSWERED-NOTE-PRETOOL"
        for prompt in seen:
            assert "UNANSWERED-NOTE-PRETOOL" not in prompt, (
                "note followed only by ToolMessages was summarized"
            )
        assert result.injected_preserved == 1
        assert result.injected_absorbed == 0


# ---------------------------------------------------------------------------
# 4. Hoist order excludes answered notes
# ---------------------------------------------------------------------------


class TestHoistOrderExcludesAnsweredNotes:
    @pytest.mark.asyncio
    async def test_hoisted_head_has_no_answered_notes(self):
        """The seam's hoisted head — the ``[permanent-injected…][doc]
        [tail…]`` prefix — contains the context_kind note and the
        unanswered bare note, and NEVER the answered bare note (which
        is absorbed into the doc).
        """
        from daemon.compaction import build_sentinel_replacement

        compactor = _make_compactor()
        ctx_note = _ctx_note("[SYSTEM CONTEXT: T]\nCTX", "ctx-1")
        answered = _bare_note("ANSWERED-NOTE", "note-ans")
        unanswered = _bare_note("UNANSWERED-NOTE", "note-un")
        msgs = (
            [ctx_note, answered, AIMessage(content="ack", id="ai-ack")]
            + _padded_regulars("h", ["h-0", "h-1", "h-2", "h-3"])
            + [unanswered]
        )
        ctx = _build_context(msgs)

        result = await compactor.compact_state(ctx)
        assert result is not None

        replacement = build_sentinel_replacement(
            result, list(ctx.messages), compacted_ids=result.compacted_ids
        )
        # Strip the sentinel.
        keepables = [
            m for m in replacement if not isinstance(m, RemoveMessage)
        ]
        keep_ids = [m.id for m in keepables]
        # Head = hoisted injections only: ctx + unanswered, in order.
        assert keep_ids[0] == "ctx-1"
        assert keep_ids[1] == "note-un"
        # Doc immediately after the head.
        assert isinstance(keepables[2], SystemMessage)
        assert (keepables[2].id or "").startswith("compaction-global-")
        # The answered note is NOT in the head (nor anywhere — it was
        # absorbed into the compacted span).
        assert "note-ans" not in keep_ids
        # And nothing after the head before the doc is an injected
        # note: the head contains NO answered notes by construction.
        head = keep_ids[:2]
        assert "note-ans" not in head


# ---------------------------------------------------------------------------
# 5. injections-dominate skip fires ONLY when permanent+unanswered dominate
# ---------------------------------------------------------------------------


class TestInjectionsDominateSkipScope:
    @pytest.mark.asyncio
    async def test_answered_notes_plus_history_does_not_skip(self):
        """All bare notes ANSWERED (+ regular history) → selectable
        pool non-empty → the injections-dominate skip must NOT fire;
        the engine compacts and counts every note as absorbed.
        """
        compactor = _make_compactor()
        msgs = []
        for i in range(3):
            msgs.append(_bare_note(f"NOTE-{i} {PAD}", f"note-{i}"))
            msgs.append(AIMessage(content=f"ack-{i}", id=f"ai-{i}"))
        msgs += _padded_regulars("h", ["h-0", "h-1", "h-2"])
        ctx = _build_context(msgs)

        result = await compactor.compact_state(ctx)

        assert result is not None
        assert result.compaction_type != "skipped_injections_dominate"
        assert result.compaction_type == "summarization"
        assert result.injected_absorbed == 3
        assert result.injected_preserved == 0

    @pytest.mark.asyncio
    async def test_permanent_dominated_channel_skips(self):
        """ALL messages permanent (context_kind) → nothing selectable →
        the skip fires with the anti-refire stamp.
        """
        compactor = _make_compactor()
        msgs = [_ctx_note(f"[SYSTEM CONTEXT: {i}]\nx", f"ctx-{i}") for i in range(5)]
        ctx = _build_context(msgs)

        result = await compactor.compact_state(ctx)

        assert result is not None
        assert result.compaction_type == "skipped_injections_dominate"
        assert result.replacement_messages == []
        assert result.compacted_at is not None
        assert result.injected_preserved == 5
        assert result.injected_absorbed == 0

    @pytest.mark.asyncio
    async def test_mixed_permanent_and_unanswered_bare_skips(self):
        """context_kind + UNANSWERED bare notes together with no
        selectable content → skip fires (both classes preserved).
        """
        compactor = _make_compactor()
        msgs = (
            [_ctx_note("[SYSTEM CONTEXT: T]\nx", f"ctx-{i}") for i in range(3)]
            + [_bare_note(f"NOTE-{i}", f"note-{i}") for i in range(2)]
        )
        ctx = _build_context(msgs)

        result = await compactor.compact_state(ctx)

        assert result is not None
        assert result.compaction_type == "skipped_injections_dominate"
        assert result.injected_preserved == 5
        assert result.injected_absorbed == 0


# ---------------------------------------------------------------------------
# 6. Envelope honesty — answered note in the preserved TAIL is in
#    NEITHER count (not hoisted, not summarized).
# ---------------------------------------------------------------------------


class TestEnvelopeHonesty:
    @pytest.mark.asyncio
    async def test_tail_resident_answered_note_in_neither_count(self):
        """An answered note that selection keeps in the preserved tail
        stays verbatim inline: not absorbed (never summarized), not
        preserved (not hoisted). The head carries no injected notes.
        """
        compactor = _make_compactor()
        seen = _spy_prompts(compactor)
        note = _bare_note("LATE-ANSWERED-NOTE", "note-late")
        msgs = _padded_regulars("h", ["h-0", "h-1", "h-2", "h-3", "h-4"]) + [
            note,
            AIMessage(content="ack", id="ai-ack"),
        ]
        ctx = _build_context(msgs)

        result = await compactor.compact_state(ctx)

        assert result is not None
        assert result.compaction_type == "summarization"
        kept = [
            m for m in result.replacement_messages
            if not isinstance(m, RemoveMessage)
        ]
        kept_ids = [m.id for m in kept]
        assert "note-late" in kept_ids, (
            "tail-resident answered note must stay verbatim inline"
        )
        for prompt in seen:
            assert "LATE-ANSWERED-NOTE" not in prompt, (
                "tail-resident note must not be summarized"
            )
        assert result.injected_preserved == 0
        assert result.injected_absorbed == 0


# ---------------------------------------------------------------------------
# 7. Kill-switch ENSEMBLE_INJECTED_NOTES_ABSORB (follow-up item 1)
# ---------------------------------------------------------------------------

_FLAG = "ENSEMBLE_INJECTED_NOTES_ABSORB"


def _unset_flag(monkeypatch) -> None:
    monkeypatch.delenv(_FLAG, raising=False)


class TestAbsorbKillSwitchResolver:
    """Resolver-level pins for ``resolve_injected_notes_absorb`` —
    mirrors the ``ENSEMBLE_PROACTIVE_COMPACTION`` falsy convention:
    unset/empty → documented ON default; 0/false/no/off (any case) →
    OFF; 1/true/yes/on → ON; anything else raises (loud typo, never a
    silent default).
    """

    def test_flag_unset_resolves_on(self, monkeypatch):
        _unset_flag(monkeypatch)
        from daemon.config import resolve_injected_notes_absorb

        assert resolve_injected_notes_absorb() is True

    @pytest.mark.parametrize("value", ["0", "false", "no", "off", "False", "OFF"])
    def test_flag_falsy_spellings_disable(self, monkeypatch, value):
        monkeypatch.setenv(_FLAG, value)
        from daemon.config import resolve_injected_notes_absorb

        assert resolve_injected_notes_absorb() is False

    @pytest.mark.parametrize("value", ["1", "true", "yes", "on", "TRUE"])
    def test_flag_truthy_spellings_enable(self, monkeypatch, value):
        monkeypatch.setenv(_FLAG, value)
        from daemon.config import resolve_injected_notes_absorb

        assert resolve_injected_notes_absorb() is True

    @pytest.mark.parametrize("value", ["", "   "])
    def test_flag_empty_string_safe_on(self, monkeypatch, value):
        """Bare ``KEY=`` in .env reaches os.environ as "" — treated as
        UNSET per ``_clean_env_value`` → documented ON default (safe).
        """
        monkeypatch.setenv(_FLAG, value)
        from daemon.config import resolve_injected_notes_absorb

        assert resolve_injected_notes_absorb() is True

    def test_flag_invalid_value_raises_loud(self, monkeypatch):
        monkeypatch.setenv(_FLAG, "maybe")
        from daemon.config import resolve_injected_notes_absorb

        with pytest.raises(ValueError, match="ENSEMBLE_INJECTED_NOTES_ABSORB"):
            resolve_injected_notes_absorb()


class TestAbsorbKillSwitchOffDegeneration:
    """Behavioral kill-switch pins. Contract: flag OFF ⇒ the absorbed-id
    set is empty ⇒ the three-bucket partition degenerates EXACTLY to the
    legacy two-bucket behavior — ALL bare-flag notes preserved verbatim
    and hoisted, answered or not; ``context_kind`` unchanged in BOTH
    states; the injections-dominate gate skips again under OFF.
    """

    @pytest.mark.asyncio
    async def test_flag_off_answered_note_returns_to_legacy_hoist_shape(
        self, monkeypatch
    ):
        """(ii) `=0` → the answered note from
        ``TestAnsweredNoteAbsorbed`` is preserved VERBATIM and hoisted
        at the head — the replacement-messages shape equals the legacy
        hoist shape: [note][doc][tail].
        """
        monkeypatch.setenv(_FLAG, "0")
        from daemon.compaction import build_sentinel_replacement

        compactor = _make_compactor()
        seen = _spy_prompts(compactor)
        note = _bare_note("OPS-NOTE-OFF-STATE", "note-1")
        msgs = (
            _padded_regulars("early", ["e-0", "e-1", "e-2"])
            + [note, AIMessage(content="noted, thanks", id="ai-ack")]
            + _padded_regulars("late", ["l-0", "l-1", "l-2", "l-3"])
        )
        ctx = _build_context(msgs)

        result = await compactor.compact_state(ctx)

        assert result is not None
        assert result.compaction_type == "summarization", (
            "regular history still compacts under flag OFF"
        )
        # Envelope flips to the legacy accounting: preserved, not absorbed.
        assert result.injected_preserved == 1
        assert result.injected_absorbed == 0

        replacement = build_sentinel_replacement(
            result, list(ctx.messages), compacted_ids=result.compacted_ids
        )
        keepables = [
            m for m in replacement if not isinstance(m, RemoveMessage)
        ]
        keep_ids = [m.id for m in keepables]
        # Legacy hoist shape: [hoisted note][compaction doc][tail…].
        assert keep_ids[0] == "note-1"
        assert isinstance(keepables[1], SystemMessage)
        assert (keepables[1].id or "").startswith("compaction-global-")
        # Verbatim: exactly one copy, unmodified content.
        assert keep_ids.count("note-1") == 1
        assert keepables[0].content == "OPS-NOTE-OFF-STATE"
        # Never summarized even though an AIMessage answered it.
        for prompt in seen:
            assert "OPS-NOTE-OFF-STATE" not in prompt, (
                "flag OFF must restore preserve-forever: the answered "
                "note leaked into the summarization prompt"
            )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("flag_value", [None, "0"])
    async def test_context_kind_preserved_in_both_flag_states(
        self, monkeypatch, flag_value
    ):
        """(iv) context_kind handling is flag-INDEPENDENT: preserved
        verbatim and hoisted under ON (unset) and OFF alike.
        """
        if flag_value is None:
            _unset_flag(monkeypatch)
        else:
            monkeypatch.setenv(_FLAG, flag_value)

        compactor = _make_compactor()
        seen = _spy_prompts(compactor)
        ctx_note = _ctx_note("[SYSTEM CONTEXT: T]\nCTX-BODY-XYZ", "ctx-1")
        msgs = [ctx_note]
        for i in range(6):
            msgs.append(HumanMessage(content=f"h-{i} {PAD}", id=f"h-{i}"))
            msgs.append(AIMessage(content=f"a-{i}", id=f"a-{i}"))
        ctx = _build_context(msgs)

        result = await compactor.compact_state(ctx)

        assert result is not None
        assert result.compaction_type == "summarization"
        kept = [
            m for m in result.replacement_messages
            if not isinstance(m, RemoveMessage)
        ]
        ctx_kept = next(m for m in kept if m.id == "ctx-1")
        assert ctx_kept.content == "[SYSTEM CONTEXT: T]\nCTX-BODY-XYZ"
        assert (ctx_kept.additional_kwargs or {}).get("context_kind") == "task_context"
        for prompt in seen:
            assert "CTX-BODY-XYZ" not in prompt
        assert result.injected_preserved == 1
        assert result.injected_absorbed == 0

    @pytest.mark.asyncio
    async def test_flag_off_all_bare_unanswered_channel_skips(self, monkeypatch):
        """(v, parity) An all-bare-injected channel (no AIMessage → all
        unanswered) skips under OFF exactly as legacy semantics did.
        """
        monkeypatch.setenv(_FLAG, "0")
        compactor = _make_compactor()
        msgs = [_bare_note(f"NOTE-{i} {PAD}", f"note-{i}") for i in range(5)]
        ctx = _build_context(msgs)

        result = await compactor.compact_state(ctx)

        assert result is not None
        assert result.compaction_type == "skipped_injections_dominate"
        assert result.replacement_messages == []
        assert result.injected_preserved == 5
        assert result.injected_absorbed == 0

    @pytest.mark.asyncio
    async def test_flag_off_gate_flips_back_to_injections_dominate(
        self, monkeypatch
    ):
        """(v, distinguishing) Injected-ack channel: under ON the
        answered notes make the pool selectable → engine compacts; the
        SAME channel under OFF hoists everything → the
        injections-dominate skip fires again (old semantics restored).
        """
        from daemon.compaction import CompactionContext  # noqa: F401 (parity with helpers)

        def _injected_ack(i: int) -> AIMessage:
            return AIMessage(
                content=f"ack-{i}",
                id=f"ai-{i}",
                additional_kwargs={"injected_message": True},
            )

        # ON (unset): notes answered by the later injected AIMessage →
        # selectable pool non-empty → compaction proceeds.
        compactor_on = _make_compactor()
        msgs = [_bare_note(f"NOTE-{i} {PAD}", f"note-{i}") for i in range(4)]
        msgs.append(_injected_ack(0))
        ctx_on = _build_context(msgs)
        result_on = await compactor_on.compact_state(ctx_on)
        assert result_on is not None
        # The point of the flip: the gate does NOT fire under ON — the
        # answered notes make the pool selectable and compaction runs.
        assert result_on.compaction_type == "summarization"
        assert result_on.injected_preserved == 1  # trailing injected ack (unanswered)
        # At least one answered note absorbed into the doc (exact split
        # is window-dependent: tail-resident answered notes count in
        # neither envelope figure — see TestEnvelopeHonesty).
        assert result_on.injected_absorbed >= 1

        # OFF ("0"): identical channel → nothing selectable → skip.
        monkeypatch.setenv(_FLAG, "0")
        compactor_off = _make_compactor()
        ctx_off = _build_context(list(msgs))
        result_off = await compactor_off.compact_state(ctx_off)
        assert result_off is not None
        assert result_off.compaction_type == "skipped_injections_dominate"
        assert result_off.injected_preserved == 5
        assert result_off.injected_absorbed == 0


# ---------------------------------------------------------------------------
# 8. id-less bare note → conservative UNANSWERED fallback (item 2a)
# ---------------------------------------------------------------------------


class TestIdLessBareNoteFallback:
    """An id-less bare note is conservatively treated as UNANSWERED —
    never absorbed in EITHER flag state: preserved verbatim and hoisted
    even when an AIMessage answers it positionally.
    """

    @staticmethod
    def _id_less_note() -> HumanMessage:
        return HumanMessage(
            content="ID-LESS-OPERATOR-NOTE",
            additional_kwargs={"injected_message": True},
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("flag_value", [None, "0"])
    async def test_id_less_note_never_absorbed_both_flag_states(
        self, monkeypatch, flag_value
    ):
        if flag_value is None:
            _unset_flag(monkeypatch)
        else:
            monkeypatch.setenv(_FLAG, flag_value)
        from daemon.compaction import build_sentinel_replacement

        compactor = _make_compactor()
        seen = _spy_prompts(compactor)
        note = self._id_less_note()
        msgs = (
            _padded_regulars("h", ["h-0", "h-1", "h-2", "h-3", "h-4", "h-5"])
            + [note, AIMessage(content="ack", id="ai-ack")]
        )
        ctx = _build_context(msgs)

        result = await compactor.compact_state(ctx)

        assert result is not None
        assert result.compaction_type == "summarization"
        # Envelope: conservative preserve in BOTH states.
        assert result.injected_preserved == 1
        assert result.injected_absorbed == 0
        for prompt in seen:
            assert "ID-LESS-OPERATOR-NOTE" not in prompt, (
                "id-less bare note leaked into the summarization prompt"
            )
        # Hoisted verbatim at the head via the seam.
        replacement = build_sentinel_replacement(
            result, list(ctx.messages), compacted_ids=result.compacted_ids
        )
        keepables = [
            m for m in replacement if not isinstance(m, RemoveMessage)
        ]
        assert keepables[0].content == "ID-LESS-OPERATOR-NOTE", (
            "id-less bare note must hoist verbatim at the head"
        )
        assert isinstance(keepables[1], SystemMessage)


# ---------------------------------------------------------------------------
# 9. Emergency-path envelope math with answered notes (item 2b)
# ---------------------------------------------------------------------------


def _build_emergency_context(messages: list):
    """CompactionContext shaped to force the emergency-truncation exit:
    recent window ≥ group count (compactable == []) and a tiny window ×
    threshold so preserved tokens blow past the threshold (mirrors the
    ``test_emergency_truncation_when_preserved_exceeds_threshold``
    fixture in tests/unit/test_compaction.py).
    """
    from daemon.compaction import CompactionContext

    config = MagicMock()
    config.threshold = 0.01
    config.min_messages_before_compaction = 3
    config.recent_message_window = 200
    config.min_recent_window = 200
    config.target_ratio = 0.5
    config.summarization_chunk_threshold = 1.0
    config.context_window_overrides = {"test-model": 100}
    config.context_window_default = 0

    return CompactionContext(
        messages=messages,
        system_prompt_tokens=0,
        model_name="test-model",
        config=config,
        llm_config={
            "model": "test-model",
            "base_url": "http://test",
            "api_key": "sk-test",
            "temperature": 0.0,
        },
        last_compacted_at=None,
    )


class TestEmergencyPathEnvelope:
    @pytest.mark.asyncio
    async def test_emergency_envelope_counts_answered_notes(self, monkeypatch):
        """Emergency exit with answered notes present: ``compactable
        == []`` means groups cover the ENTIRE selectable pool, so every
        group message (every answered note among them) is RemoveMessage'd
        and its truncated successor is re-id'd ``truncated-*`` — the
        ``injected_absorbed=len(absorbed_notes)`` claim at the emergency
        return is exact. Pinned with a real assertion.
        """
        _unset_flag(monkeypatch)
        compactor = _make_compactor()
        note = _bare_note("EMERGENCY-ANSWERED-NOTE", "note-em")
        msgs = (
            _padded_regulars("h", [f"h-{i}" for i in range(8)])
            + [note, AIMessage(content="ack", id="ai-ack")]
        )
        ctx = _build_emergency_context(msgs)

        result = await compactor.compact_state(ctx)

        assert result is not None
        assert result.compaction_type == "emergency_truncation"
        # Envelope math: the answered note is absorbed (its original id
        # is a RemoveMessage target), nothing is hoisted.
        assert result.injected_absorbed == 1
        assert result.injected_preserved == 0
        remove_ids = {
            m.id for m in result.replacement_messages
            if isinstance(m, RemoveMessage)
        }
        assert "note-em" in remove_ids, (
            "answered note's original id must be a RemoveMessage target "
            "on the emergency path — that IS the absorb contract there"
        )
        # Surviving non-removal messages carry only re-id'd truncated
        # ids (hoisted_injected is empty in this scenario).
        survivor_ids = [
            m.id for m in result.replacement_messages
            if not isinstance(m, RemoveMessage) and m.id
        ]
        assert all(sid.startswith("truncated-") for sid in survivor_ids)

