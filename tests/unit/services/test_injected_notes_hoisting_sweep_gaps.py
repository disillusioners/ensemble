"""Sweep-gap coverage for the UNANSWERED-note safety invariant.

TEST GATE §3 — Safety-invariant sweep:
    UNANSWERED bare injected note (injected_message=True, NO
    context_kind, NO later AIMessage) is NEVER absorbed by any
    compaction exit, in EITHER flag state (absorb ON / OFF).

This module covers the cells that ``test_injected_notes_hoisting.py``
does NOT touch (the existing 32 tests cover summarization ×
last-message / tool-only × ON; id-less × both flags;
``skipped_injections_dominate`` × both flags; ``emergency_truncation``
× ANSWERED × ON; and resolver-level kill-switch pins). The remaining
gaps addressed here:

  × ``emergency_truncation``  + UNANSWERED note (last, tool-only)
  × ``emergency_truncation``  + OFF flag (last, tool-only)
  × ``emergency_truncation``  + id-less bare note (both flags)
  × ``summarization``         + OFF flag (last, tool-only)
  × ``partial_summary``       + UNANSWERED note (both flags)
  × ``truncation``            + UNANSWERED note (both flags)
  × ``skipped_preserved_within_threshold`` + UNANSWERED note (both)
  × ``CompactionAborted``     + UNANSWERED note (seam-level, both)

Each test is REAL: it invokes ``ContextCompactor.compact_state`` (or
the seam ``build_sentinel_replacement`` directly with a hand-crafted
result), then asserts on ``replacement_messages`` / the envelope
fields (``injected_preserved`` / ``injected_absorbed``). Mocks are
restricted to the summarization LLM (mirrors the existing module's
fixtures — same boundary); the engine's exit-routing decisions are
exercised end-to-end.

No DB / file I/O is touched (the invariant lives entirely in
``daemon.compaction``); no file-backed SQLite needed.
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

from daemon.compaction import (
    ChunkedOutcome,
    CompactionAborted,
    CompactionContext,
    CompactionResult,
    ContextCompactor,
    build_sentinel_replacement,
)


PAD = "lorem ipsum dolor sit amet consectetur adipiscing elit " * 3

_FLAG = "ENSEMBLE_INJECTED_NOTES_ABSORB"


# ---------------------------------------------------------------------------
# Helpers (mirrors the existing module's idioms verbatim)
# ---------------------------------------------------------------------------


def _make_compactor(llm_response: str = "summary content") -> ContextCompactor:
    """ContextCompactor with a stubbed summarization LLM and a tight
    200-token window so short padded fixtures cross the threshold."""
    config = MagicMock()
    config.threshold = 0.5
    config.min_messages_before_compaction = 3
    config.recent_message_window = 2
    config.min_recent_window = 1
    config.target_ratio = 0.5
    config.summarization_chunk_threshold = 1.0
    config.context_window_overrides = {"test-model": 200}
    config.context_window_default = 0

    compactor = ContextCompactor(
        config=config,
        llm_config={
            "model": "test-model",
            "base_url": "http://test",
            "api_key": "sk-test",
            "temperature": 0.0,
        },
    )

    async def fake_call(prompt, ctx):
        return llm_response

    compactor._call_summarization_llm = fake_call  # type: ignore[method-assign]
    return compactor


def _build_context(messages: list, model: str = "test-model") -> CompactionContext:
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
    """Operator note: bare ``injected_message=True``, NO context_kind,
    with a stable construction-time id (per the project message-id
    invariant)."""
    return HumanMessage(
        content=content,
        id=msg_id,
        additional_kwargs={"injected_message": True},
    )


def _id_less_bare_note(content: str) -> HumanMessage:
    """Bare injected note WITHOUT a stable id — the conservative
    fallback test cell. Mirrors the production id-less path that
    MessageTap silently drops the metadata row for; the safety
    invariant under test still holds because selection sees
    ``msg_id is None`` and refuses to mark the note absorbed."""
    return HumanMessage(
        content=content,
        additional_kwargs={"injected_message": True},
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


def _build_emergency_context(messages: list) -> CompactionContext:
    """Forces the emergency-truncation exit: large ``recent_window``
    (compactable == []) and a tiny window × threshold so preserved
    tokens blow past the threshold. Mirrors
    ``test_emergency_envelope_counts_answered_notes`` in the
    existing module."""
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


def _unset_flag(monkeypatch) -> None:
    monkeypatch.delenv(_FLAG, raising=False)


# ---------------------------------------------------------------------------
# 1. Summarization × OFF flag — last-message + tool-only
# ---------------------------------------------------------------------------


class TestSummarizationOffFlagUnanswered:
    """The OFF flag degenerates answered notes to the legacy
    hoist-forever behavior, so EVERY bare note (answered or not) must
    be preserved verbatim. This is the parity contract — the
    unanswered-note invariant is trivially upheld because the OFF
    branch makes every note's absorbed-id membership empty.

    These tests also exercise the OFF branch on the regular
    summarization path (not just the skip gate as the existing
    ``test_flag_off_all_bare_unanswered_channel_skips`` does)."""

    @pytest.mark.asyncio
    async def test_summarization_off_flag_unanswered_note_last_message(
        self, monkeypatch
    ):
        """OFF flag, normal summarization, note as the LAST message →
        preserved verbatim, NEVER summarized, never absorbed."""
        monkeypatch.setenv(_FLAG, "0")
        compactor = _make_compactor()
        seen = _spy_prompts(compactor)
        note = _bare_note("UNANSWERED-OFF-TAIL", "note-tail")
        msgs = (
            _padded_regulars("h", ["h-0", "h-1", "h-2", "h-3", "h-4", "h-5"])
            + [AIMessage(content="done", id="ai-0"), note]
        )
        ctx = _build_context(msgs)

        result = await compactor.compact_state(ctx)

        assert result is not None
        assert result.compaction_type == "summarization"
        kept = [
            m for m in result.replacement_messages
            if not isinstance(m, RemoveMessage)
        ]
        kept_ids = {m.id for m in kept}
        assert "note-tail" in kept_ids
        note_kept = next(m for m in kept if m.id == "note-tail")
        assert note_kept.content == "UNANSWERED-OFF-TAIL"
        for prompt in seen:
            assert "UNANSWERED-OFF-TAIL" not in prompt, (
                "OFF-flag tail-resident unanswered note leaked into the "
                "summarization prompt"
            )
        # Envelope: legacy accounting under OFF — preserved, not absorbed.
        assert result.injected_preserved == 1
        assert result.injected_absorbed == 0

    @pytest.mark.asyncio
    async def test_summarization_off_flag_unanswered_note_tool_only(
        self, monkeypatch
    ):
        """OFF flag, normal summarization, note followed ONLY by
        ToolMessages → preserved verbatim, NEVER summarized."""
        monkeypatch.setenv(_FLAG, "0")
        compactor = _make_compactor()
        seen = _spy_prompts(compactor)
        note = _bare_note("UNANSWERED-OFF-PRETOOL", "note-pretool")
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
        assert "note-pretool" in kept_ids
        note_kept = next(m for m in kept if m.id == "note-pretool")
        assert note_kept.content == "UNANSWERED-OFF-PRETOOL"
        for prompt in seen:
            assert "UNANSWERED-OFF-PRETOOL" not in prompt, (
                "OFF-flag note followed only by ToolMessages was "
                "summarized"
            )
        assert result.injected_preserved == 1
        assert result.injected_absorbed == 0


# ---------------------------------------------------------------------------
# 2. Emergency truncation × UNANSWERED note (last + tool-only, ON/OFF)
# ---------------------------------------------------------------------------


class TestEmergencyUnansweredNote:
    """The emergency-truncation exit is the silent-loss hot zone:
    RemoveMessages target EVERY group message and the survivor list
    is re-id'd to ``truncated-*``. The unanswered note MUST land in
    ``hoisted_injected`` (verbatim) and survive the seam reordering
    — never as a RemoveMessage target, never re-id'd."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("position", ["last", "tool_only"])
    async def test_emergency_unanswered_note_preserved_on_flag(
        self, position
    ):
        """ON flag × emergency_truncation × UNANSWERED note (last
        message OR followed only by ToolMessages) → note preserved
        verbatim; NOT in any RemoveMessage target list; the id
        survives into the replacement as a verbatim message object."""
        if position == "last":
            note = _bare_note("EMERGENCY-UNANSWERED-TAIL", "note-em-tail")
            msgs = (
                _padded_regulars("h", [f"h-{i}" for i in range(8)])
                + [AIMessage(content="done", id="ai-done"), note]
            )
        else:
            note = _bare_note(
                "EMERGENCY-UNANSWERED-PRETOOL", "note-em-pretool"
            )
            msgs = (
                _padded_regulars("h", [f"h-{i}" for i in range(6)])
                + [
                    AIMessage(
                        content="",
                        id="ai-tc",
                        tool_calls=[
                            {
                                "name": "f",
                                "args": {},
                                "id": "tc-1",
                                "type": "tool_call",
                            }
                        ],
                    ),
                    note,
                    ToolMessage(
                        tool_call_id="tc-1", content="tool out", id="tool-1"
                    ),
                ]
            )

        compactor = _make_compactor()
        ctx = _build_emergency_context(msgs)

        result = await compactor.compact_state(ctx)

        assert result is not None
        assert result.compaction_type == "emergency_truncation", (
            "fixture must route to emergency_truncation — if this "
            "fires, the recent_window / threshold shape regressed"
        )

        # Envelope math: the unanswered note is hoisted, nothing absorbed.
        assert result.injected_preserved == 1, (
            f"emergency_truncation × ON × UNANSWERED ({position}) "
            f"must hoist the note verbatim"
        )
        assert result.injected_absorbed == 0, (
            f"emergency_truncation × ON × UNANSWERED ({position}) "
            f"must NEVER absorb"
        )

        # Note id is NOT a RemoveMessage target — that IS absorb.
        remove_ids = {
            m.id for m in result.replacement_messages
            if isinstance(m, RemoveMessage)
        }
        note_id = "note-em-tail" if position == "last" else "note-em-pretool"
        assert note_id not in remove_ids, (
            f"emergency_truncation × ON × UNANSWERED ({position}): "
            f"the note's id appeared in RemoveMessage targets — that "
            f"is the absorb contract and is FORBIDDEN for an "
            f"unanswered note"
        )

        # Verbatim preservation: the note survives into the
        # replacement as a full message object carrying its
        # original id and content. The content byte-for-byte matches.
        survivors = [
            m for m in result.replacement_messages
            if not isinstance(m, RemoveMessage) and not (
                isinstance(m, SystemMessage)
                and (m.id or "").startswith("compaction-global-")
            )
        ]
        note_msg = next((m for m in survivors if m.id == note_id), None)
        assert note_msg is not None, (
            f"emergency_truncation × ON × UNANSWERED ({position}): "
            f"the note id {note_id!r} is missing from the survivor "
            f"list — the note was silently lost"
        )
        expected_content = (
            "EMERGENCY-UNANSWERED-TAIL"
            if position == "last"
            else "EMERGENCY-UNANSWERED-PRETOOL"
        )
        assert note_msg.content == expected_content

        # And the seam reorders it to the head — the unresolved
        # invariants of the build_sentinel_replacement helper also
        # hold for the emergency path's replacement_messages.
        replacement = build_sentinel_replacement(
            result, list(ctx.messages), compacted_ids=result.compacted_ids
        )
        # First element after sentinel is the hoisted note
        # (the only preserved injected in this scenario).
        non_sentinel = [
            m for m in replacement if not isinstance(m, RemoveMessage)
        ]
        assert non_sentinel[0].id == note_id
        assert non_sentinel[0].content == expected_content

    @pytest.mark.asyncio
    @pytest.mark.parametrize("position", ["last", "tool_only"])
    async def test_emergency_unanswered_note_preserved_off_flag(
        self, position, monkeypatch
    ):
        """OFF flag × emergency_truncation × UNANSWERED note →
        preserved verbatim. Under OFF, EVERY bare note is in
        preserved_injected (legacy two-bucket behavior); the
        emergency path's ``replacement.extend(hoisted_injected)`` must
        carry the note through unchanged."""
        monkeypatch.setenv(_FLAG, "0")
        if position == "last":
            note = _bare_note("EMERGENCY-OFF-TAIL", "note-em-off-tail")
            msgs = (
                _padded_regulars("h", [f"h-{i}" for i in range(8)])
                + [AIMessage(content="done", id="ai-done"), note]
            )
        else:
            note = _bare_note(
                "EMERGENCY-OFF-PRETOOL", "note-em-off-pretool"
            )
            msgs = (
                _padded_regulars("h", [f"h-{i}" for i in range(6)])
                + [
                    AIMessage(
                        content="",
                        id="ai-tc",
                        tool_calls=[
                            {
                                "name": "f",
                                "args": {},
                                "id": "tc-1",
                                "type": "tool_call",
                            }
                        ],
                    ),
                    note,
                    ToolMessage(
                        tool_call_id="tc-1", content="tool out", id="tool-1"
                    ),
                ]
            )

        compactor = _make_compactor()
        ctx = _build_emergency_context(msgs)

        result = await compactor.compact_state(ctx)

        assert result is not None
        assert result.compaction_type == "emergency_truncation"

        # OFF: every bare note is in preserved_injected.
        assert result.injected_preserved == 1
        assert result.injected_absorbed == 0

        note_id = (
            "note-em-off-tail" if position == "last" else "note-em-off-pretool"
        )
        remove_ids = {
            m.id for m in result.replacement_messages
            if isinstance(m, RemoveMessage)
        }
        assert note_id not in remove_ids

        survivors = [
            m for m in result.replacement_messages
            if not isinstance(m, RemoveMessage) and not (
                isinstance(m, SystemMessage)
                and (m.id or "").startswith("compaction-global-")
            )
        ]
        note_msg = next((m for m in survivors if m.id == note_id), None)
        assert note_msg is not None, (
            f"emergency_truncation × OFF × UNANSWERED ({position}): "
            f"the note id {note_id!r} is missing from the survivor "
            f"list — silently lost"
        )
        expected_content = (
            "EMERGENCY-OFF-TAIL"
            if position == "last"
            else "EMERGENCY-OFF-PRETOOL"
        )
        assert note_msg.content == expected_content


# ---------------------------------------------------------------------------
# 3. Emergency × id-less bare note (both flags)
# ---------------------------------------------------------------------------


class TestEmergencyIdLessBareNote:
    """Id-less bare note under emergency_truncation: the
    conservative fallback (id-less ⇒ conservatively UNANSWERED) must
    still hold on the emergency path — the note is hoisted verbatim,
    never absorbed, even when a later AIMessage would have answered
    it positionally if it had an id."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("flag_value", [None, "0"])
    async def test_emergency_id_less_bare_note_preserved_both_flags(
        self, monkeypatch, flag_value
    ):
        if flag_value is None:
            _unset_flag(monkeypatch)
        else:
            monkeypatch.setenv(_FLAG, flag_value)

        # Id-less note: the constructor omits `id=` so MessageTap
        # would silently drop the metadata row in production. The
        # selection guard (``if not msg_id: continue``) refuses to
        # mark it absorbed — invariant under test.
        note = _id_less_bare_note("ID-LESS-EMERGENCY")
        # NOTE: the note has no id, so we can't reference it by id
        # in the assertions — we identify by content + object identity.
        msgs = (
            _padded_regulars("h", [f"h-{i}" for i in range(8)])
            + [AIMessage(content="ack", id="ai-ack"), note]
        )
        compactor = _make_compactor()
        seen = _spy_prompts(compactor)
        ctx = _build_emergency_context(msgs)

        result = await compactor.compact_state(ctx)

        assert result is not None
        assert result.compaction_type == "emergency_truncation"
        # Conservative preserve in both states — id-less NEVER absorbed.
        assert result.injected_preserved == 1
        assert result.injected_absorbed == 0
        for prompt in seen:
            assert "ID-LESS-EMERGENCY" not in prompt, (
                "id-less bare note leaked into the summarization "
                "prompt on the emergency path"
            )
        # The id-less note must appear in the survivor list with
        # its content byte-for-byte preserved.
        survivors = [
            m for m in result.replacement_messages
            if not isinstance(m, RemoveMessage)
        ]
        # Find by content (the only stable handle for an id-less msg).
        matching = [
            m for m in survivors if m.content == "ID-LESS-EMERGENCY"
        ]
        assert len(matching) >= 1, (
            "id-less emergency note was silently dropped from the "
            "replacement list"
        )


# ---------------------------------------------------------------------------
# 4. CompactionAborted × UNANSWERED note — seam pre-write guard
# ---------------------------------------------------------------------------


class TestCompactionAbortedUnansweredNote:
    """Defense-in-depth at the seam: even if the engine were buggy
    and returned a CompactionResult whose replacement_messages OMITS
    an UNANSWERED note's id, ``build_sentinel_replacement`` must
    RAISE CompactionAborted — the silent-loss class is forbidden by
    the pre-write guard. Verified in BOTH flag states because the
    guard is identity-based, not absorb-based.

    The seam receives ``compacted_ids=None`` (strict mode): any
    snapshot id absent from the replacement and not in
    ``compacted_ids`` triggers abort. The unanswered note is in
    ``current_messages`` but NOT in the crafted result's
    replacement_messages and NOT in compacted_ids → abort."""

    @pytest.mark.asyncio
    async def test_seam_raises_compaction_aborted_when_unanswered_note_missing_on_flag(
        self, monkeypatch
    ):
        """ON flag: the seam rejects a buggy result that omits the
        unanswered note's id. Without this guard, the note would
        vanish under the sentinel recipe."""
        _unset_flag(monkeypatch)
        note = _bare_note("UNANSWERED-MISSING-ON", "note-miss-on")
        current_messages = _padded_regulars("h", ["h-0", "h-1", "h-2"]) + [
            note
        ]
        # Buggy result: a doc and a tail message, but NOT the note.
        buggy_result = CompactionResult(
            replacement_messages=[
                SystemMessage(
                    id="compaction-global-test-1",
                    content="doc body",
                ),
                HumanMessage(content="h-2", id="h-2"),
            ],
            tokens_before=1000,
            tokens_after=500,
            tokens_saved=500,
            messages_before=4,
            messages_after=2,
            compaction_type="summarization",
            compacted_at="2026-09-06T00:00:00+00:00",
            # Engine-derived compacted_ids: only h-0 / h-1 — note
            # is NOT in this set (it was supposed to be hoisted).
            compacted_ids=frozenset({"h-0", "h-1"}),
            injected_preserved=0,
            injected_absorbed=0,
        )

        with pytest.raises(CompactionAborted) as excinfo:
            build_sentinel_replacement(
                buggy_result, current_messages, compacted_ids=None
            )
        # The error message should mention the lost id.
        assert "note-miss-on" in str(excinfo.value), (
            "pre-write guard error must enumerate the lost id(s)"
        )

    @pytest.mark.asyncio
    async def test_seam_raises_compaction_aborted_when_unanswered_note_missing_off_flag(
        self, monkeypatch
    ):
        """OFF flag: the same pre-write guard fires — the guard is
        identity-based, not flag-dependent, so the silent-loss class
        is forbidden regardless of absorb mode."""
        monkeypatch.setenv(_FLAG, "0")
        note = _bare_note("UNANSWERED-MISSING-OFF", "note-miss-off")
        current_messages = _padded_regulars("h", ["h-0", "h-1", "h-2"]) + [
            note
        ]
        buggy_result = CompactionResult(
            replacement_messages=[
                SystemMessage(
                    id="compaction-global-test-2",
                    content="doc body",
                ),
                HumanMessage(content="h-2", id="h-2"),
            ],
            tokens_before=1000,
            tokens_after=500,
            tokens_saved=500,
            messages_before=4,
            messages_after=2,
            compaction_type="summarization",
            compacted_at="2026-09-06T00:00:00+00:00",
            compacted_ids=frozenset({"h-0", "h-1"}),
            injected_preserved=0,
            injected_absorbed=0,
        )

        with pytest.raises(CompactionAborted) as excinfo:
            build_sentinel_replacement(
                buggy_result, current_messages, compacted_ids=None
            )
        assert "note-miss-off" in str(excinfo.value)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("flag_value", [None, "0"])
    async def test_seam_preserves_unanswered_note_when_correctly_attached(
        self, monkeypatch, flag_value
    ):
        """POSITIVE control: when the engine correctly includes the
        note in the replacement, the seam returns it at the HEAD of
        the channel (sentinel → hoisted injected → doc + tail). The
        pre-write guard does NOT abort on a well-formed result.
        Verified in both flag states."""
        if flag_value is None:
            _unset_flag(monkeypatch)
        else:
            monkeypatch.setenv(_FLAG, flag_value)
        note = _bare_note("UNANSWERED-CORRECT", "note-correct")
        current_messages = _padded_regulars("h", ["h-0", "h-1", "h-2"]) + [
            note
        ]
        # Well-formed result: note IS in replacement_messages.
        well_formed_result = CompactionResult(
            replacement_messages=[
                SystemMessage(
                    id="compaction-global-test-3",
                    content="doc body",
                ),
                HumanMessage(content="h-2", id="h-2"),
                note,  # ← note correctly re-attached
            ],
            tokens_before=1000,
            tokens_after=500,
            tokens_saved=500,
            messages_before=4,
            messages_after=3,
            compaction_type="summarization",
            compacted_at="2026-09-06T00:00:00+00:00",
            compacted_ids=frozenset({"h-0", "h-1"}),
            injected_preserved=1,
            injected_absorbed=0,
        )

        out = build_sentinel_replacement(
            well_formed_result,
            current_messages,
            compacted_ids=well_formed_result.compacted_ids,
        )
        # First element: sentinel (RemoveMessage).
        assert isinstance(out[0], RemoveMessage)
        # Second element: hoisted injected — the note.
        assert out[1].id == "note-correct"
        assert out[1].content == "UNANSWERED-CORRECT"
        # Then doc, then tail.
        assert isinstance(out[2], SystemMessage)
        assert (out[2].id or "").startswith("compaction-global-")
        assert out[3].id == "h-2"


# ---------------------------------------------------------------------------
# 5. partial_summary × UNANSWERED note (engine-level, both flags)
# ---------------------------------------------------------------------------


def _build_chunked_context(messages: list) -> CompactionContext:
    """Wired so the chunked path runs end-to-end. Mirrors the
    existing module's ``TestChunkedOutcomeDataclass`` / partial-
    summary fixtures (``tests/unit/test_compaction.py:2095``-)."""
    config = MagicMock()
    config.threshold = 0.01
    config.min_messages_before_compaction = 2
    config.recent_message_window = 2
    config.min_recent_window = 1
    config.target_ratio = 0.5
    config.summarization_chunk_threshold = 0.01
    config.context_window_overrides = {"test-model": 1000}
    config.context_window_default = 0
    config.operation_budget_s = 60.0

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


def _make_compactor_with_partial_summary(partial_summaries):
    """A compactor whose ``_summarize_chunked`` returns a synthetic
    partial-summary ``ChunkedOutcome`` (one batch succeeded,
    stop_reason=timeout). This is the engine seam that routes to
    ``compaction_type="partial_summary"``."""
    compactor = _make_compactor()

    async def _fake_chunked(compactable, context, previous_overview=None):
        return ChunkedOutcome(
            summaries=partial_summaries,
            failed_batches=[1],
            stop_reason="timeout",
        )

    compactor._summarize_chunked = _fake_chunked  # type: ignore[method-assign]
    return compactor


class TestPartialSummaryUnansweredNote:
    """``partial_summary`` is the |S|>=1 / stop_reason∈{timeout,
    budget} branch — exactly one doc is emitted with the surviving
    batches as embedded sections. The unanswered note must still be
    in ``hoisted_injected`` and survive into the replacement."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("flag_value", [None, "0"])
    async def test_partial_summary_unanswered_note_preserved(
        self, monkeypatch, flag_value
    ):
        if flag_value is None:
            _unset_flag(monkeypatch)
        else:
            monkeypatch.setenv(_FLAG, flag_value)

        note = _bare_note(
            "PARTIAL-UNANSWERED" + ("-OFF" if flag_value else "-ON"),
            "note-partial",
        )
        # The unanswered note sits AFTER an answered AI — but no
        # AIMessage comes after the note itself.
        msgs = (
            _padded_regulars("h", [f"h-{i}" for i in range(12)])
            + [AIMessage(content="mid-stream reply", id="ai-mid"), note]
        )

        compactor = _make_compactor_with_partial_summary(
            partial_summaries=["batch-0 summary"],
        )

        ctx = _build_chunked_context(msgs)
        result = await compactor.compact_state(ctx)

        assert result is not None
        assert result.compaction_type == "partial_summary", (
            "fixture must route to partial_summary — if this fires "
            "the chunked stub regressed"
        )

        # Envelope: the unanswered note is hoisted, never absorbed.
        assert result.injected_preserved == 1, (
            f"partial_summary × UNANSWERED note: the note must be "
            f"hoisted, but injected_preserved={result.injected_preserved}"
        )
        assert result.injected_absorbed == 0, (
            f"partial_summary × UNANSWERED note: the note was "
            f"absorbed (injected_absorbed={result.injected_absorbed})"
        )

        # Verbatim preservation in the replacement.
        keepables = [
            m for m in result.replacement_messages
            if not isinstance(m, RemoveMessage)
        ]
        matching = [m for m in keepables if m.id == "note-partial"]
        assert matching, (
            "partial_summary × UNANSWERED note: the note id was "
            "dropped from replacement_messages"
        )
        assert matching[0].content.startswith("PARTIAL-UNANSWERED")
        # And the seam puts it at the head.
        replacement = build_sentinel_replacement(
            result, list(ctx.messages), compacted_ids=result.compacted_ids
        )
        non_sentinel = [
            m for m in replacement if not isinstance(m, RemoveMessage)
        ]
        assert non_sentinel[0].id == "note-partial"


# ---------------------------------------------------------------------------
# 6. truncation (|S|=0) × UNANSWERED note (engine-level, both flags)
# ---------------------------------------------------------------------------


def _make_compactor_with_all_batches_failed():
    """A compactor whose ``_summarize_chunked`` returns an empty-summary
    outcome (|S|=0). The engine's outer branch routes this to
    ``_truncate_fallback`` which returns ``compaction_type="truncation"``."""
    compactor = _make_compactor()

    async def _fake_chunked(compactable, context, previous_overview=None):
        return ChunkedOutcome(
            summaries=[],
            failed_batches=[0],
            stop_reason="timeout",
        )

    compactor._summarize_chunked = _fake_chunked  # type: ignore[method-assign]
    return compactor


class TestTruncationFallbackUnansweredNote:
    """``truncation`` is the |S|=0 fallback path — single doc with
    envelope + dropped-spans, no GLOBAL OVERVIEW. The unanswered note
    must still be hoisted verbatim on this path."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("flag_value", [None, "0"])
    async def test_truncation_unanswered_note_preserved(
        self, monkeypatch, flag_value
    ):
        if flag_value is None:
            _unset_flag(monkeypatch)
        else:
            monkeypatch.setenv(_FLAG, flag_value)

        note = _bare_note(
            "TRUNCATION-UNANSWERED" + ("-OFF" if flag_value else "-ON"),
            "note-trunc",
        )
        msgs = (
            _padded_regulars("h", [f"h-{i}" for i in range(12)])
            + [AIMessage(content="mid-stream reply", id="ai-mid"), note]
        )

        compactor = _make_compactor_with_all_batches_failed()
        ctx = _build_chunked_context(msgs)

        result = await compactor.compact_state(ctx)

        assert result is not None
        assert result.compaction_type == "truncation", (
            f"fixture must route to truncation — got "
            f"{result.compaction_type!r}"
        )

        assert result.injected_preserved == 1
        assert result.injected_absorbed == 0

        # Note id survives into the replacement list.
        keepables = [
            m for m in result.replacement_messages
            if not isinstance(m, RemoveMessage)
        ]
        matching = [m for m in keepables if m.id == "note-trunc"]
        assert matching, (
            "truncation × UNANSWERED note: the note id was dropped "
            "from replacement_messages"
        )
        assert matching[0].content.startswith("TRUNCATION-UNANSWERED")

        # And the seam puts it at the head.
        replacement = build_sentinel_replacement(
            result, list(ctx.messages), compacted_ids=result.compacted_ids
        )
        non_sentinel = [
            m for m in replacement if not isinstance(m, RemoveMessage)
        ]
        assert non_sentinel[0].id == "note-trunc"


# ---------------------------------------------------------------------------
# 7. skipped_preserved_within_threshold × UNANSWERED note (seam-level)
# ---------------------------------------------------------------------------
#
# The engine path ``compaction_type="skipped_preserved_within_threshold"``
# (compaction.py:2286) is unreachable through normal ``compact_state``
# flow: it requires ``compactable == []`` (so the emergency branch is
# taken) AND ``preserved_tokens <= threshold`` (so the bail fires).
# When ``compactable == []`` because ``len(groups) <= window``,
# ``select_compactable_groups`` returns
# ``preserved == selectable_messages``, which makes
# ``preserved_tokens == total_tokens``. The first threshold gate at
# :2209-2217 returns ``None`` whenever
# ``total_tokens <= threshold_tokens``, so this combination cannot
# occur end-to-end.
#
# The ``CompactionResult`` itself is real (the engine emits it on the
# emergency-bail path when ``preserved_tokens`` exactly fits). The
# production caller in ``daemon/services/compact_executor.py``
# treats it as a USER-FACING noop (``_ENGINE_SKIPPED_TYPES_TO_NOOP_REASON``
# at :243-247 + "the seam-skip call-site at :func:`execute_compact`
# keys on this same dict membership, so the seam is NOT invoked on
# this path"). The invariant under test therefore reduces to:
#
#   1. The result shape carries the right envelope counts.
#   2. The channel is unchanged (empty replacement, empty
#      ``compacted_ids``).
#   3. If the seam were ever invoked on this shape with a non-empty
#      snapshot, the pre-write guard would raise ``CompactionAborted``
#      (defense-in-depth — even if the executor's noop branch ever
#      regressed to skip the guard, the seam would still catch the
#      silent-loss class).


class TestSkippedPreservedWithinThresholdUnansweredNote:
    """The ``skipped_preserved_within_threshold`` CompactionResult has
    ``replacement_messages == []`` (the channel is unchanged). The
    unanswered note must therefore survive verbatim: no
    RemoveMessage targets it, and ``injected_preserved >= 1``.

    The executor's noop branch (compact_executor.py:243-247 +
    :1460-1479) skips the seam entirely, so this test verifies the
    RESULT shape and the seam's defense-in-depth contract."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("flag_value", [None, "0"])
    async def test_engine_result_shape_unanswered_note_intact(
        self, monkeypatch, flag_value
    ):
        """Hand-craft the engine's ``skipped_preserved_within_threshold``
        result with an UNANSWERED note in the snapshot. Verify the
        envelope counts the note as preserved (under BOTH flag states
        — the answer set is empty in BOTH, so ON and OFF converge to
        the same hoisting outcome)."""
        if flag_value is None:
            _unset_flag(monkeypatch)
        else:
            monkeypatch.setenv(_FLAG, flag_value)
        from daemon.config import resolve_injected_notes_absorb

        note = _bare_note("WITHIN-THRESHOLD-NOTE", "note-within")
        current_messages = (
            _padded_regulars("h", ["h-0", "h-1", "h-2"])
            + [note]
        )
        # Hand-crafted result matching the engine's
        # ``skipped_preserved_within_threshold`` shape: empty
        # replacement, anti-refire stamp engaged, injected_preserved
        # counts the hoisted set, injected_absorbed=0.
        result = CompactionResult(
            replacement_messages=[],
            tokens_before=1000,
            tokens_after=500,
            tokens_saved=0,
            messages_before=len(current_messages),
            messages_after=len(current_messages),
            compaction_type="skipped_preserved_within_threshold",
            compacted_at="2026-09-06T00:00:00+00:00",
            compacted_ids=frozenset(),  # nothing was removed
            injected_preserved=1,
            injected_absorbed=0,
        )

        # Sanity-check the flag resolution matches our parametrization
        # (catches a regression where the test would silently run on
        # the wrong branch).
        expected_absorb = flag_value is None
        assert resolve_injected_notes_absorb() is expected_absorb

        # The result shape: the channel is unchanged.
        assert result.replacement_messages == []
        assert result.compacted_ids == frozenset()
        # Envelope counts the hoisted set correctly under both flags.
        assert result.injected_preserved == 1
        assert result.injected_absorbed == 0
        # The note's id is NOT in the compacted_ids — it was NEVER
        # removed, so it will land in the head of the channel under
        # any downstream processing.
        assert "note-within" not in result.compacted_ids

    @pytest.mark.asyncio
    @pytest.mark.parametrize("flag_value", [None, "0"])
    async def test_seam_defense_in_depth_raises_for_unsafe_invoke(
        self, monkeypatch, flag_value
    ):
        """Defense-in-depth: if the executor's noop-skip ever regressed
        and the seam were invoked on this shape, the pre-write guard
        must RAISE ``CompactionAborted`` (the silent-loss class is
        forbidden under the sentinel recipe). The unanswered note's
        id MUST appear in the lost-ids list — the guard enumerates
        it explicitly. Verified in BOTH flag states (the guard is
        identity-based)."""
        if flag_value is None:
            _unset_flag(monkeypatch)
        else:
            monkeypatch.setenv(_FLAG, flag_value)

        note = _bare_note("WITHIN-DEFENSE-DEPTH", "note-defense")
        current_messages = (
            _padded_regulars("h", ["h-0", "h-1", "h-2"])
            + [note]
        )
        result = CompactionResult(
            replacement_messages=[],
            tokens_before=1000,
            tokens_after=500,
            tokens_saved=0,
            messages_before=len(current_messages),
            messages_after=len(current_messages),
            compaction_type="skipped_preserved_within_threshold",
            compacted_at="2026-09-06T00:00:00+00:00",
            compacted_ids=frozenset(),  # empty: nothing explicitly removed
            injected_preserved=1,
            injected_absorbed=0,
        )

        with pytest.raises(CompactionAborted) as excinfo:
            build_sentinel_replacement(
                result,
                current_messages,
                compacted_ids=result.compacted_ids,
            )
        # The pre-write guard MUST enumerate the unanswered note's
        # id among the lost ids — confirming the guard would refuse
        # to write this shape.
        assert "note-defense" in str(excinfo.value)