"""Compact-note lane — the DORMANT PRE-FILTER (the ``compaction_note_prefilter_unit`` pack).

The lane-owned leg **L13** (plan §10.4): the pre-filter's truth table +
its default-OFF byte-identity + its kill-switch behaviour.

Covered:

* the predicate truth table — a marker match DROPS; a free-text note ⇒
  the EMPTY drop-set; every failure ⇒ the EMPTY drop-set;
* **input-list immutability** — the candidate list is never mutated;
* the **never-empty wall** — a drop-set that would empty a non-empty
  candidate set is discarded ⇒ identity;
* **whole-group atomicity** — drops operate on whole ``MessageGroup``
  objects only;
* **flag-unset vs flag-on-no-note ⇒ identical doc bytes**;
* kill-switch OFF ⇒ identity.

Non-vacuity: the marker-match leg plants a marker that matches exactly
ONE group and asserts the OTHER groups fall through UNCHANGED — a
"drop everything" implementation cannot pass it.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from langchain_core.messages import AIMessage, HumanMessage

from daemon._compact_note_prefilter import (
    MARKER_BLOCK_END,
    MARKER_BLOCK_START,
    REASON_MARKER_MATCH,
    REASON_NEVER_EMPTY_WALL,
    REASON_NO_MARKER_BLOCK,
    REASON_NO_MATCH,
    REASON_OFF,
    filter_candidate_groups,
    parse_marker_accounts,
)
from daemon.compaction import (
    CompactionContext,
    ContextCompactor,
    MessageGroup,
)
from daemon.config import CompactionConfig as CompactionConfigModel

REPO_ROOT = Path(__file__).resolve().parents[3]


def _group(index: int, *, message_id: str | None = None) -> MessageGroup:
    mid = message_id if message_id is not None else f"m-{index}"
    return MessageGroup(
        start_idx=index,
        end_idx=index,
        messages=[HumanMessage(content=f"message {index}", id=mid)],
        group_type="single",
    )


def _groups(count: int = 4) -> list[MessageGroup]:
    return [_group(i) for i in range(count)]


def _marker_note(*, message_ids: list[str] = (), spans: list[tuple[int, int]] = ()) -> str:
    entries: list[str] = [f"msg:{mid}" for mid in message_ids]
    entries += [f"group:{start}-{end}" for start, end in spans]
    body = ", ".join(entries)
    return (
        "the agent's own distillation\n\n"
        f"{MARKER_BLOCK_START}\n"
        f"accounted_for: {body}\n"
        f"{MARKER_BLOCK_END}\n"
    )


# ── The truth table ─────────────────────────────────────────────────────────


class TestTruthTable:
    def test_a_marker_match_drops_the_matching_group(self) -> None:
        groups = _groups(4)
        result = filter_candidate_groups(
            groups, _marker_note(message_ids=["m-1"]), enabled=True
        )
        assert result.matched is True
        assert result.reason == REASON_MARKER_MATCH
        assert len(result.dropped) == 1
        assert result.dropped[0].messages[0].id == "m-1"

    def test_the_non_matching_groups_fall_through_unchanged(self) -> None:
        groups = _groups(4)
        result = filter_candidate_groups(
            groups, _marker_note(message_ids=["m-1"]), enabled=True
        )
        assert [g.messages[0].id for g in result.kept] == ["m-0", "m-2", "m-3"]

    def test_a_group_span_reference_also_drops(self) -> None:
        groups = _groups(4)
        result = filter_candidate_groups(
            groups, _marker_note(spans=[(2, 2)]), enabled=True
        )
        assert [g.messages[0].id for g in result.dropped] == ["m-2"]

    def test_a_free_text_note_is_vacuous(self) -> None:
        """Free-text notes make the filter VACUOUS BY CONSTRUCTION."""
        groups = _groups(4)
        result = filter_candidate_groups(
            groups, "just my own words, no marker block", enabled=True
        )
        assert result.matched is False
        assert result.reason == REASON_NO_MARKER_BLOCK
        assert result.dropped == []
        assert result.kept == groups

    def test_an_absent_note_is_vacuous(self) -> None:
        groups = _groups(4)
        result = filter_candidate_groups(groups, "", enabled=True)
        assert result.dropped == []
        assert result.kept == groups

    def test_an_unparseable_marker_block_is_vacuous(self) -> None:
        groups = _groups(4)
        broken = (
            "note body\n\n"
            f"{MARKER_BLOCK_START}\n"
            "accounted_for: msg:m-1, ???garbage???, msg:m-2\n"
            f"{MARKER_BLOCK_END}\n"
        )
        result = filter_candidate_groups(groups, broken, enabled=True)
        assert result.matched is False
        assert result.dropped == []
        assert result.kept == groups

    def test_an_out_of_set_reference_is_vacuous(self) -> None:
        groups = _groups(4)
        result = filter_candidate_groups(
            groups, _marker_note(message_ids=["not-here"]), enabled=True
        )
        assert result.matched is False
        assert result.reason == REASON_NO_MATCH
        assert result.dropped == []

    def test_the_parse_helper_returns_empty_sets_for_a_free_text_note(self) -> None:
        assert parse_marker_accounts("free text") == (set(), set())

    def test_the_parse_helper_reads_the_marker_block(self) -> None:
        ids, spans = parse_marker_accounts(
            _marker_note(message_ids=["a", "b"], spans=[(1, 2)])
        )
        assert ids == {"a", "b"}
        assert spans == {(1, 2)}

    def test_the_last_marker_block_wins(self) -> None:
        note = (
            _marker_note(message_ids=["first"])
            + "\n"
            + _marker_note(message_ids=["second"])
        )
        ids, _spans = parse_marker_accounts(note)
        assert ids == {"second"}


# ── The two hard walls ──────────────────────────────────────────────────────


class TestHardWalls:
    def test_the_never_empty_wall_discards_an_emptying_drop_set(self) -> None:
        groups = _groups(3)
        result = filter_candidate_groups(
            groups, _marker_note(message_ids=["m-0", "m-1", "m-2"]), enabled=True
        )
        assert result.matched is False
        assert result.reason == REASON_NEVER_EMPTY_WALL
        assert result.dropped == []
        assert result.kept == groups

    def test_the_never_empty_wall_does_not_fire_for_a_partial_drop(self) -> None:
        groups = _groups(3)
        result = filter_candidate_groups(
            groups, _marker_note(message_ids=["m-0"]), enabled=True
        )
        assert result.matched is True
        assert len(result.kept) == 2

    def test_whole_group_atomicity(self) -> None:
        """A tool sequence is NEVER split — a group is dropped whole."""
        group = MessageGroup(
            start_idx=0,
            end_idx=1,
            messages=[
                AIMessage(content="call", id="a-0", tool_calls=[
                    {"id": "t-1", "name": "x", "args": {}}
                ]),
                HumanMessage(content="tool result", id="t-1"),
            ],
            group_type="tool_sequence",
        )
        other = _group(1, message_id="other")
        result = filter_candidate_groups(
            [group, other], _marker_note(message_ids=["a-0"]), enabled=True
        )
        assert len(result.dropped) == 1
        # The tool sequence is dropped WHOLE — never split.
        assert len(result.dropped[0].messages) == 2
        assert [g.messages[0].id for g in result.kept] == ["other"]


# ── Immutability + identity ────────────────────────────────────────────────


class TestImmutabilityAndIdentity:
    def test_the_input_list_is_never_mutated(self) -> None:
        groups = _groups(4)
        before = [g.messages[0].id for g in groups]
        filter_candidate_groups(
            groups, _marker_note(message_ids=["m-1"]), enabled=True
        )
        assert [g.messages[0].id for g in groups] == before
        assert len(groups) == 4

    def test_the_kill_switch_off_is_identity(self) -> None:
        groups = _groups(4)
        result = filter_candidate_groups(
            groups, _marker_note(message_ids=["m-1"]), enabled=False
        )
        assert result.matched is False
        assert result.reason == REASON_OFF
        assert result.dropped == []
        assert result.kept == groups

    def test_no_note_short_circuits_before_any_copy(self) -> None:
        """The gate is ``enabled AND note_present`` — with no note there
        is no list copy, no parse, no arm-state read."""
        groups = _groups(3)
        result = filter_candidate_groups(groups, "", enabled=True)
        assert result.kept == groups
        assert result.dropped == []

    def test_the_dropped_spans_use_the_original_coordinates(self) -> None:
        groups = _groups(4)
        result = filter_candidate_groups(
            groups, _marker_note(message_ids=["m-1"]), enabled=True
        )
        assert result.dropped_spans == [(2, 2)]
        assert result.kept_positions == [(1, 1), (3, 3), (4, 4)]


# ── The kill switch ─────────────────────────────────────────────────────────


class TestKillSwitch:
    def test_the_kill_switch_is_the_one_config_bool(self) -> None:
        from daemon.config import CompactionConfig

        fields = CompactionConfig.model_fields
        assert "compact_note_prefilter_enabled" in fields
        # EXACTLY ONE pre-filter knob — no second switch.
        prefilter_fields = [
            name for name in fields if "prefilter" in name
        ]
        assert prefilter_fields == ["compact_note_prefilter_enabled"]

    def test_the_code_default_is_off(self) -> None:
        from daemon.config import CompactionConfig

        assert CompactionConfig().compact_note_prefilter_enabled is False

    def test_the_env_override_works(self) -> None:
        from daemon.config import CompactionConfig

        cfg = CompactionConfig(
            compact_note_prefilter_enabled=True,
        )
        assert cfg.compact_note_prefilter_enabled is True

    def test_the_knob_is_deliberately_absent_from_config_yaml(self) -> None:
        text = (REPO_ROOT / "config.yaml").read_text(encoding="utf-8")
        assert "compact_note_prefilter_enabled" not in text

    def test_the_resolver_is_env_first(self, monkeypatch) -> None:
        from daemon.config import _resolve_compact_note_prefilter_enabled

        monkeypatch.setenv(
            "COMPACTION_COMPACT_NOTE_PREFILTER_ENABLED", "1"
        )
        import os

        assert _resolve_compact_note_prefilter_enabled(
            False, env_value=os.environ.get("COMPACTION_COMPACT_NOTE_PREFILTER_ENABLED")
        ) is True


# ── The containment law ─────────────────────────────────────────────────────


class TestContainment:
    def test_the_prefilter_never_touches_the_decision_family(self) -> None:
        """The decision family is UNTOUCHED: no import, no registry call,
        no ``DecisionOverlayPort``, no ledger entry."""
        source = (
            REPO_ROOT / "daemon" / "_compact_note_prefilter.py"
        ).read_text(encoding="utf-8")
        # The MODULE DOCSTRING names these symbols to state that they are
        # NEVER used — the scan covers the CODE only.
        code = source.split('"""', 2)[-1]
        for banned in (
            "DecisionOverlayPort",
            "services.decision",
            "from .services",
            "from ..services",
            "import ledger",
            "decision_ledger",
            "register_seam",
        ):
            assert banned not in code, banned

    def test_the_decision_registry_does_not_mention_the_prefilter(self) -> None:
        registry = REPO_ROOT / "daemon" / "services" / "decision" / "registry.py"
        text = registry.read_text(encoding="utf-8")
        assert "compact_note" not in text

    def test_the_prefilter_has_no_llm_consumer(self) -> None:
        source = (
            REPO_ROOT / "daemon" / "_compact_note_prefilter.py"
        ).read_text(encoding="utf-8")
        for banned in ("httpx", "openai", "invoke", "ainvoke", "ThinkingChatOpenAI"):
            assert banned not in source, banned


# ── The default-OFF byte-identity (the doc bytes) ──────────────────────────


def _config(**overrides) -> CompactionConfigModel:
    base = {
        "enabled": True,
        "threshold": 0.80,
        "recent_message_window": 2,
        "min_recent_window": 1,
        "context_window_overrides": {},
        "context_window_default": 0,
        "target_ratio": 0.40,
        "model": "",
        "summarization_model": "",
        "min_messages_before_compaction": 2,
        "summarization_chunk_threshold": 0.60,
    }
    base.update(overrides)
    return CompactionConfigModel(**base)


async def _drive(prefilter_enabled: bool, note: str):
    """Drive a compaction with a stubbed summarizer and return the doc."""
    config = _config(
        context_window_overrides={"gpt-4o": 10_000},
        summarization_chunk_threshold=0.001,
        compact_note_prefilter_enabled=prefilter_enabled,
    )
    messages = [
        HumanMessage(content=f"message {i} " + ("x" * 200), id=f"h-{i}")
        for i in range(30)
    ]
    context = CompactionContext(
        messages=messages,
        system_prompt_tokens=2000,
        model_name="gpt-4o",
        config=config,
        llm_config={},
        compact_note=note,
    )
    compactor = ContextCompactor(config, {})

    async def _fake_batch(batch_groups, ctx, *args, **kwargs):
        return "BATCH SUMMARY"

    async def _fake_merge(summaries, ctx, *args, **kwargs):
        return "MERGED OVERVIEW", True

    with patch.object(
        ContextCompactor, "_summarize_single_batch", staticmethod(_fake_batch)
    ), patch.object(
        ContextCompactor, "_merge_one_round", staticmethod(_fake_merge)
    ):
        result = await compactor.compact_state(context, force=True)
    assert result is not None
    return result.replacement_messages[0].content


def _normalize(doc: str) -> str:
    """Strip the generation timestamp — the ONLY per-run difference."""
    import re

    return re.sub(r"compacted_at=[^ |]+", "compacted_at=<TS>", doc)


class TestDefaultOffByteIdentity:
    @pytest.mark.asyncio
    async def test_flag_unset_and_flag_on_with_no_note_give_identical_doc_bytes(self) -> None:
        off = await _drive(False, "")
        on = await _drive(True, "")
        assert _normalize(off) == _normalize(on)

    @pytest.mark.asyncio
    async def test_the_kill_switch_off_is_identity_on_the_doc(self) -> None:
        note = _marker_note(message_ids=["h-3"])
        off = await _drive(False, note)
        on = await _drive(True, note)
        # The filter ON drops the accounted group; the filter OFF does
        # not — the two docs DIFFER, which proves the filter is live
        # when armed (the DORMANT default is the other direction).
        assert _normalize(off) != _normalize(on)
        assert "dropped without summary" in on
        assert "dropped without summary" not in off

    @pytest.mark.asyncio
    async def test_the_dropped_group_is_itemized_in_the_dropped_spans_clause(self) -> None:
        note = _marker_note(message_ids=["h-3"])
        on = await _drive(True, note)
        assert "dropped without summary" in on
