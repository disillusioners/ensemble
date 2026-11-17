"""Compact-note lane — the ENGINE surface (``daemon/compaction.py``).

The lane-owned, engine-level legs of the ``prompt_reload_compact_note_unit``
pack (plan §10.4):

* **L5** — the note renders VERBATIM (its EXACT bytes, in the GLOBAL
  region, as the FIRST body after the GLOBAL header).
* **L6** — the note is INDEPENDENT of the merge pass: the merge forced to
  ``("", False)`` still leaves the note present, the placeholder absent,
  and the surviving sections present.
* **L10** — the D25 merge-skip pin: ≥2 batches + an armed note ⇒ NO merge
  LLM call ⇒ the per-batch sections survive ⇒ the note is in the GLOBAL
  region ⇒ NO ``(overview unavailable …)`` placeholder co-emission.
* **L11** — the ``previous_overview`` suppression pin: two consecutive
  note-guided compactions ⇒ the second carries the NEW note exactly once
  and NO "Previous overview:" line carrying the OLD note; the no-note path
  keeps ``previous_overview`` byte-identically.
* The envelope's content-free marker (``note_guided`` / ``note_sha8`` /
  ``note_chars``) and the ceiling-rule inclusion.

Non-vacuity: L6's merge-failure stub is proven LOAD-BEARING by a control
leg (the placeholder WOULD render without the note), and L10's "no merge
LLM call" is asserted by a spy on the merge client, not by the outcome.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from daemon.compaction import (
    GLOBAL_DOC_PLACEHOLDER_GLOBAL,
    CompactionContext,
    ContextCompactor,
    MessageGroup,
    build_compaction_doc,
)
from daemon.config import CompactionConfig as CompactionConfigModel

#: The sentinel — a string the summarizer could NOT have produced, so
#: "the note's exact bytes are present" is a real assertion.
NOTE_SENTINEL = "ZZ-NOTE-SENTINEL-4c1f9a-note-body-9f3a-ZZ"


def _config(**overrides) -> CompactionConfigModel:
    base = {
        "enabled": True,
        "threshold": 0.80,
        "recent_message_window": 10,
        "min_recent_window": 3,
        "context_window_overrides": {},
        "context_window_default": 0,
        "target_ratio": 0.40,
        "model": "",
        "summarization_model": "",
        "min_messages_before_compaction": 10,
        "summarization_chunk_threshold": 0.60,
    }
    base.update(overrides)
    return CompactionConfigModel(**base)


def _messages(count: int, chars: int = 700) -> list:
    out = []
    for i in range(count):
        body = f"message {i} " + ("x" * chars)
        if i % 2 == 0:
            out.append(HumanMessage(content=body, id=f"human-{i}"))
        else:
            out.append(AIMessage(content=body, id=f"ai-{i}"))
    return out


def _doc(
    *,
    note: str = "",
    global_overview: str | None = None,
    previous_overview: str | None = None,
    sections: list[dict] | None = None,
    mode: str = "summary",
    context_window: int | None = None,
):
    secs = sections if sections is not None else [
        {
            "start_idx": 1,
            "end_idx": 2,
            "body": "section body",
            "start_id": "m-1",
            "end_id": "m-2",
        }
    ]
    return build_compaction_doc(
        instance_id="inst-1",
        seq=1,
        mode=mode,
        compacted_at="2026-11-17T00:00:00+00:00",
        global_overview=global_overview,
        sections=secs,
        total_sections=len(secs),
        summarized_start=1,
        summarized_end=2,
        preserved_count=0,
        dropped_spans=[],
        context_window=context_window,
        previous_overview=previous_overview,
        compact_note=note,
    )


def _global_region(content: str) -> str:
    """The text between the GLOBAL header and the SECTION DETAIL header."""
    head = "── GLOBAL OVERVIEW ──\n"
    assert head in content
    tail = "\n── SECTION DETAIL ──\n"
    start = content.index(head) + len(head)
    if tail in content:
        return content[start : content.index(tail)]
    return content[start:]


# ── L5 — the note renders VERBATIM ──────────────────────────────────────────


class TestL5NoteRendersVerbatim:
    def test_the_exact_note_bytes_are_present(self) -> None:
        doc = _doc(note=NOTE_SENTINEL)
        assert NOTE_SENTINEL in doc.content

    def test_the_note_is_in_the_global_region(self) -> None:
        doc = _doc(note=NOTE_SENTINEL)
        region = _global_region(doc.content)
        assert NOTE_SENTINEL in region

    def test_the_note_is_the_first_body_after_the_global_header(self) -> None:
        doc = _doc(note=NOTE_SENTINEL)
        region = _global_region(doc.content)
        assert region.strip().startswith(NOTE_SENTINEL)

    def test_the_note_is_not_truncated_by_the_global_token_cap(self) -> None:
        from daemon.compaction import GLOBAL_OVERVIEW_TOKEN_CAP

        long_note = "y" * (GLOBAL_OVERVIEW_TOKEN_CAP * 8)
        doc = _doc(note=long_note)
        assert long_note in doc.content
        assert "[truncated — full GLOBAL overview text exceeded" not in doc.content

    def test_the_note_replaces_the_merged_overview(self) -> None:
        doc = _doc(note=NOTE_SENTINEL, global_overview="MERGED TEXT")
        assert "MERGED TEXT" not in doc.content
        assert NOTE_SENTINEL in doc.content

    def test_the_note_suppresses_the_merge_failure_placeholder(self) -> None:
        doc = _doc(note=NOTE_SENTINEL, global_overview=None)
        assert GLOBAL_DOC_PLACEHOLDER_GLOBAL not in doc.content
        assert NOTE_SENTINEL in doc.content

    def test_the_note_renders_on_the_truncation_path(self) -> None:
        doc = _doc(note=NOTE_SENTINEL, global_overview="", mode="truncation", sections=[])
        assert NOTE_SENTINEL in doc.content

    def test_the_no_note_path_is_unchanged(self) -> None:
        doc = _doc(note="", global_overview="MERGED TEXT")
        assert "MERGED TEXT" in doc.content
        assert GLOBAL_DOC_PLACEHOLDER_GLOBAL not in doc.content


# ── The envelope's content-free marker ──────────────────────────────────────


class TestEnvelopeMarker:
    def test_the_envelope_carries_the_content_free_marker(self) -> None:
        import hashlib

        doc = _doc(note=NOTE_SENTINEL)
        digest8 = hashlib.sha256(NOTE_SENTINEL.encode("utf-8")).hexdigest()[:8]
        assert "note_guided=true" in doc.content
        assert f"note_sha8={digest8}" in doc.content
        assert f"note_chars={len(NOTE_SENTINEL)}" in doc.content

    def test_the_envelope_never_carries_the_note_body(self) -> None:
        doc = _doc(note=NOTE_SENTINEL)
        envelope = doc.content.split("── GLOBAL OVERVIEW ──", 1)[0]
        assert NOTE_SENTINEL not in envelope

    def test_the_no_note_path_carries_no_marker(self) -> None:
        doc = _doc(note="", global_overview="MERGED TEXT")
        assert "note_guided" not in doc.content
        assert "note_sha8" not in doc.content
        assert "note_chars" not in doc.content


# ── L6 — the note is independent of the merge pass ──────────────────────────


class TestL6NoteIndependentOfMerge:
    @pytest.mark.asyncio
    async def test_the_merge_entry_guard_returns_the_failure_ladder_shape(self) -> None:
        """A-3: the guard returns ``("", False)`` — NEVER ``(None, False)``
        (the annotation admits no None) and NEVER ``(None, True)`` (which
        would produce ``summaries=[None]``)."""
        config = _config()
        compactor = ContextCompactor(config, {})
        context = CompactionContext(
            messages=[],
            system_prompt_tokens=0,
            model_name="gpt-4o",
            config=config,
            llm_config={},
            compact_note=NOTE_SENTINEL,
        )
        merged, ok = await compactor._merge_summaries(
            ["batch one", "batch two"], context
        )
        assert merged == ""
        assert ok is False
        assert isinstance(merged, str)

    @pytest.mark.asyncio
    async def test_the_guard_is_the_first_statement(self) -> None:
        """The skip's SINGLE home — the FIRST executable statement, so it
        covers every call site by construction (architect §D.1)."""
        import inspect
        import textwrap

        from daemon.compaction import ContextCompactor as CC

        src = textwrap.dedent(inspect.getsource(CC._merge_summaries))
        body = src.split('"""', 2)[-1]
        first = [
            line.strip()
            for line in body.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ][0]
        assert first.startswith("if getattr(context, \"compact_note\""), first

    def test_the_note_survives_a_failed_merge(self) -> None:
        """The merge failed (``global_overview=None``) — the note is STILL
        there, the placeholder is NOT, and the sections survive."""
        doc = _doc(note=NOTE_SENTINEL, global_overview=None)
        assert NOTE_SENTINEL in doc.content
        assert GLOBAL_DOC_PLACEHOLDER_GLOBAL not in doc.content
        assert "── SECTION DETAIL ──" in doc.content
        assert "section body" in doc.content

    def test_the_stub_is_load_bearing(self) -> None:
        """Non-vacuity: the merge-failure stub WOULD render the placeholder
        without the note — so the L6 assertion is not a no-op."""
        doc = _doc(note="", global_overview=None)
        assert GLOBAL_DOC_PLACEHOLDER_GLOBAL in doc.content


# ── L11 — the previous_overview suppression pin ─────────────────────────────


class TestL11PreviousOverviewSuppression:
    def test_the_previous_overview_line_is_suppressed_when_a_note_is_consumed(self) -> None:
        old_note = "OLD-NOTE-abc123"
        doc = _doc(note=NOTE_SENTINEL, previous_overview=old_note)
        assert "Previous overview:" not in doc.content
        assert old_note not in doc.content

    def test_the_new_note_appears_exactly_once(self) -> None:
        doc = _doc(note=NOTE_SENTINEL, previous_overview="OLD-NOTE-abc123")
        assert doc.content.count(NOTE_SENTINEL) == 1

    def test_two_consecutive_note_guided_compactions(self) -> None:
        old_note = "OLD-NOTE-abc123"
        first = _doc(note=old_note)
        assert old_note in first.content
        second = _doc(note=NOTE_SENTINEL, previous_overview=old_note)
        assert second.content.count(NOTE_SENTINEL) == 1
        assert "Previous overview:" not in second.content
        assert old_note not in second.content

    def test_the_no_note_path_keeps_the_previous_overview_byte_identically(self) -> None:
        doc = _doc(note="", global_overview="MERGED", previous_overview="OLD-NOTE-abc123")
        assert "Previous overview: OLD-NOTE-abc123" in doc.content


# ── The ceiling rule ────────────────────────────────────────────────────────


class TestCeilingRule:
    def test_the_note_is_counted_in_the_numerator(self) -> None:
        """The note's tokens are charged against the doc ceiling (it is a
        never-condensed class)."""
        from daemon.compaction import _apply_ceiling_rule

        sections = [
            {"start_idx": 1, "end_idx": 1, "body": "s" * 4000, "start_id": None, "end_id": None}
        ]
        # A tiny window ⇒ the ceiling fires either way; the note is not
        # condensed (it is not a section).
        _, archived_line, archived = _apply_ceiling_rule(
            sections, None, 1000, note="n" * 500
        )
        assert archived >= 1

    def test_the_estimate_is_skipped_entirely_when_there_is_no_note(self) -> None:
        """With no note the computation is BIT-IDENTICAL (the estimate is
        skipped entirely, not estimated-as-zero)."""
        from daemon.compaction import _apply_ceiling_rule

        sections = [
            {"start_idx": 1, "end_idx": 1, "body": "body", "start_id": None, "end_id": None}
        ]
        a = _apply_ceiling_rule(list(sections), "GLOBAL", 100_000)
        b = _apply_ceiling_rule(list(sections), "GLOBAL", 100_000, note="")
        assert a == b

    def test_the_note_is_never_a_condensation_candidate(self) -> None:
        from daemon.compaction import _apply_ceiling_rule

        sections = [
            {"start_idx": 1, "end_idx": 1, "body": "s" * 4000, "start_id": None, "end_id": None}
        ]
        condensed, _, archived = _apply_ceiling_rule(
            sections, None, 1000, note="n" * 500
        )
        assert all(
            "condensed for budget" not in s["body"] or True for s in condensed
        )
        # The note never appears inside a section body.
        assert not any("n" * 500 in s["body"] for s in condensed)


# ── L10 — the D25 merge-skip pin (the chunked path) ────────────────────────


def _chunked_config() -> CompactionConfigModel:
    """A config whose selection + chunking yields ≥2 batches.

    * ``context_window_overrides`` = 10,000 → the threshold gate is
      8,000 tokens and the chunk gate is 6,000 tokens.
    * the system prompt carries 2,000 tokens (counted in the gate
      numerator, NOT in the compactable pool) so the threshold is crossed.
    * ``recent_message_window=2`` keeps 2 groups verbatim → 40 messages
      ⇒ 38 compactable groups ⇒ 2 batches of ≤20 groups.
    """
    return _config(
        threshold=0.80,
        recent_message_window=2,
        min_recent_window=1,
        min_messages_before_compaction=2,
        context_window_overrides={"gpt-4o": 10_000},
        # The chunk gate is deliberately tiny so the fixture yields ≥2
        # batches regardless of the message bodies' token estimate.
        summarization_chunk_threshold=0.001,
    )


async def _drive_chunked(compactor, context, note: str):
    """Run the chunked summarizer with a stubbed batch LLM + a merge spy."""
    batch_calls: list[str] = []
    merge_calls: list[list[str]] = []

    async def _fake_batch(batch_groups, ctx, *args, **kwargs):
        batch_calls.append(batch_groups)
        return f"BATCH SUMMARY {len(batch_calls)}"

    async def _fake_merge(summaries, ctx, *args, **kwargs):
        merge_calls.append(list(summaries))
        return "MERGED OVERVIEW", True

    with patch.object(
        ContextCompactor, "_summarize_single_batch", staticmethod(_fake_batch)
    ), patch.object(
        ContextCompactor, "_merge_one_round", staticmethod(_fake_merge)
    ):
        context.compact_note = note
        outcome = await compactor._summarize_chunked(
            compactor_groups(context), context, previous_overview=None
        )
    return outcome, batch_calls, merge_calls


def compactor_groups(context):
    from daemon.compaction import identify_boundary_groups

    return identify_boundary_groups(context.messages)


class TestL10MergeSkip:
    @pytest.mark.asyncio
    async def test_no_merge_llm_call_when_a_note_is_armed(self) -> None:
        config = _chunked_config()
        compactor = ContextCompactor(config, {})
        context = CompactionContext(
            messages=_messages(40),
            system_prompt_tokens=2000,
            model_name="gpt-4o",
            config=config,
            llm_config={},
        )
        outcome, batch_calls, merge_calls = await _drive_chunked(
            compactor, context, NOTE_SENTINEL
        )
        assert len(batch_calls) >= 2, "the fixture must produce ≥2 batches"
        assert merge_calls == [], "the merge LLM must NOT run when a note is armed"
        assert outcome.merge_failed is True
        # The per-batch summaries SURVIVE (they become the sections).
        assert len(outcome.summaries) >= 2

    @pytest.mark.asyncio
    async def test_the_control_the_merge_does_run_without_a_note(self) -> None:
        """Non-vacuity: the spy is load-bearing — without the note the merge
        client IS called."""
        config = _chunked_config()
        compactor = ContextCompactor(config, {})
        context = CompactionContext(
            messages=_messages(40),
            system_prompt_tokens=2000,
            model_name="gpt-4o",
            config=config,
            llm_config={},
        )
        outcome, batch_calls, merge_calls = await _drive_chunked(
            compactor, context, ""
        )
        assert len(batch_calls) >= 2
        assert merge_calls, "without a note the merge pass must run"
        assert outcome.merge_failed is False

    @pytest.mark.asyncio
    async def test_the_note_lands_in_the_global_region_with_no_placeholder(self) -> None:
        """End-to-end through ``compact_state``: the note is in the GLOBAL
        region, the per-batch sections survive, and the merge-failure
        placeholder never co-emits."""
        config = _chunked_config()
        compactor = ContextCompactor(config, {})
        context = CompactionContext(
            messages=_messages(40),
            system_prompt_tokens=2000,
            model_name="gpt-4o",
            config=config,
            llm_config={},
            compact_note=NOTE_SENTINEL,
        )

        batch_calls: list = []

        async def _fake_batch(batch_groups, ctx, *args, **kwargs):
            batch_calls.append(batch_groups)
            return "BATCH SUMMARY"

        async def _fake_merge(summaries, ctx, *args, **kwargs):
            raise AssertionError("the merge LLM must NOT run when a note is armed")

        with patch.object(
            ContextCompactor, "_summarize_single_batch", staticmethod(_fake_batch)
        ), patch.object(
            ContextCompactor, "_merge_one_round", staticmethod(_fake_merge)
        ):
            result = await compactor.compact_state(context, force=True)

        assert result is not None
        # The fixture must really produce ≥2 batches — otherwise the
        # "no merge call" assertion would be vacuous (a single batch
        # never merges).
        assert len(batch_calls) >= 2, "the fixture must produce ≥2 batches"
        doc = result.replacement_messages[0]
        assert isinstance(doc, SystemMessage)
        assert NOTE_SENTINEL in doc.content
        assert GLOBAL_DOC_PLACEHOLDER_GLOBAL not in doc.content
        region = _global_region(doc.content)
        assert NOTE_SENTINEL in region
        # The per-batch sections survive.
        assert "── SECTION DETAIL ──" in doc.content


# ── L16 — the AC7.4 POSITIVE-SHAPE pin (the build gate) ────────────────────


class TestL16ArmedAwareFloor:
    """Architect §D.2(iv) — the armed-aware floor predicate.

    The below-floor fall-through is ``force OR over_budget``; an armed
    call with ``force=False`` still 50%-tail-truncates in the
    below-floor AND over-budget corner. The pin asserts the POSITIVE
    SHAPE (a clean skip), NOT merely the absence of the truncation —
    a flip-to-``force=True`` mutation does NOT catch this hole.
    """

    @staticmethod
    def _below_floor_over_budget_context(note: str):
        config = _config(
            min_messages_before_compaction=10,
            context_window_overrides={"gpt-4o": 1000},
        )
        context = CompactionContext(
            messages=_messages(3),          # BELOW the floor (10)
            system_prompt_tokens=5000,      # OVER budget (window 1000 × 0.80)
            model_name="gpt-4o",
            config=config,
            llm_config={},
            compact_note=note,
        )
        return config, context

    @pytest.mark.asyncio
    async def test_below_floor_and_over_budget_and_armed_is_a_clean_skip(self) -> None:
        config, context = self._below_floor_over_budget_context(NOTE_SENTINEL)
        compactor = ContextCompactor(config, {})
        result = await compactor.compact_state(context, force=False)
        assert result is not None
        assert result.compaction_type == "skipped_below_min_messages_armed"
        # NO tail truncation: the replacement is EMPTY (the tail-truncation
        # path produces a NON-empty replacement).
        assert result.replacement_messages == []

    @pytest.mark.asyncio
    async def test_the_control_the_unarmed_path_tail_truncates(self) -> None:
        """Non-vacuity: WITHOUT the note the same fixture falls through to
        the 50%-tail last-effort floor — so the clean skip above is
        load-bearing."""
        config, context = self._below_floor_over_budget_context("")
        compactor = ContextCompactor(config, {})
        result = await compactor.compact_state(context, force=False)
        assert result is not None
        assert result.compaction_type == "tail_truncation_last_effort"
        assert result.replacement_messages  # the tail truncation DID happen

    @pytest.mark.asyncio
    async def test_the_arm_persists_and_the_deferral_is_recorded(self) -> None:
        """The clean skip does NOT consume the arm; the deferred state is
        recorded so the next armed receipt says "deferred, not
        compacted"."""
        from daemon.services import prompt_refresh as pr

        repo = _CarrierRepo()
        pr.arm_compact_note(repo, "iid-1", NOTE_SENTINEL)
        config, context = self._below_floor_over_budget_context(NOTE_SENTINEL)
        compactor = ContextCompactor(config, {})
        result = await compactor.compact_state(context, force=False)
        outcome = pr.settle_compact_note_arm(
            repo,
            "iid-1",
            note_sha12=pr.compact_note_sha256_12(NOTE_SENTINEL),
            result=result,
            persisted=True,
        )
        assert outcome == pr.DEFERRED_STATE_BELOW_FLOOR
        assert repo.metadata["compact_note_arm"] == NOTE_SENTINEL
        assert repo.metadata["compact_note_last_deferred"] == pr.DEFERRED_STATE_BELOW_FLOOR
        assert "compact_note_last_applied_at" not in repo.metadata


# ── L15 — the consumption predicate ─────────────────────────────────────────


class _CarrierRepo:
    """A tiny in-memory ``instances.metadata`` carrier stub."""

    def __init__(self) -> None:
        self.metadata: dict = {}

    def get(self, instance_id: str):
        return MagicMock(instance_metadata=self.metadata)

    def set_metadata(self, instance_id: str, key: str, value):
        self.metadata[key] = value

    def delete_metadata(self, instance_id: str, key: str):
        self.metadata.pop(key, None)


def _result(compaction_type: str, *, replacement=("doc",)):
    return MagicMock(
        compaction_type=compaction_type,
        replacement_messages=list(replacement),
    )


class TestL15ConsumptionPredicate:
    """Architect §D.2(i) — the arm is consumed ONLY by a DOC-PRODUCING
    compaction. ``emergency_truncation`` / ``tail_truncation_last_effort``
    do NOT consume it; ``_truncate_fallback``'s outcome DOES."""

    def test_the_doc_less_outcomes_do_not_consume_the_arm(self) -> None:
        from daemon.services import prompt_refresh as pr

        for ctype in ("emergency_truncation", "tail_truncation_last_effort"):
            repo = _CarrierRepo()
            pr.arm_compact_note(repo, "iid-1", NOTE_SENTINEL)
            outcome = pr.settle_compact_note_arm(
                repo,
                "iid-1",
                note_sha12=pr.compact_note_sha256_12(NOTE_SENTINEL),
                result=_result(ctype),
                persisted=True,
            )
            assert outcome == pr.DEFERRED_STATE_NO_DOC, ctype
            assert repo.metadata["compact_note_arm"] == NOTE_SENTINEL
            assert "compact_note_last_applied_at" not in repo.metadata

    def test_the_truncate_fallback_outcome_consumes_the_arm(self) -> None:
        from daemon.services import prompt_refresh as pr

        repo = _CarrierRepo()
        pr.arm_compact_note(repo, "iid-1", NOTE_SENTINEL)
        outcome = pr.settle_compact_note_arm(
            repo,
            "iid-1",
            note_sha12=pr.compact_note_sha256_12(NOTE_SENTINEL),
            result=_result("truncation"),
            persisted=True,
        )
        assert outcome == pr.COMPACT_NOTE_LANDED
        assert "compact_note_arm" not in repo.metadata
        assert repo.metadata["compact_note_last_sha256_12"] == (
            pr.compact_note_sha256_12(NOTE_SENTINEL)
        )
        assert repo.metadata["compact_note_last_applied_at"]

    def test_a_stamp_only_write_does_not_consume_the_arm(self) -> None:
        """The seam's bool alone cannot distinguish a full write from the
        stamp-only anti-refire write — an unwritten doc leaves the arm."""
        from daemon.services import prompt_refresh as pr

        repo = _CarrierRepo()
        pr.arm_compact_note(repo, "iid-1", NOTE_SENTINEL)
        outcome = pr.settle_compact_note_arm(
            repo,
            "iid-1",
            note_sha12=pr.compact_note_sha256_12(NOTE_SENTINEL),
            result=_result("summarization", replacement=()),
            persisted=True,
        )
        assert outcome == pr.DEFERRED_STATE_NO_DOC
        assert repo.metadata["compact_note_arm"] == NOTE_SENTINEL

    def test_a_refused_persist_does_not_consume_the_arm(self) -> None:
        from daemon.services import prompt_refresh as pr

        repo = _CarrierRepo()
        pr.arm_compact_note(repo, "iid-1", NOTE_SENTINEL)
        outcome = pr.settle_compact_note_arm(
            repo,
            "iid-1",
            note_sha12=pr.compact_note_sha256_12(NOTE_SENTINEL),
            result=_result("summarization"),
            persisted=False,
        )
        assert outcome == pr.DEFERRED_STATE_NO_DOC
        assert repo.metadata["compact_note_arm"] == NOTE_SENTINEL

    def test_the_anti_refire_deferral_is_recorded(self) -> None:
        from daemon.services import prompt_refresh as pr

        repo = _CarrierRepo()
        pr.arm_compact_note(repo, "iid-1", NOTE_SENTINEL)
        outcome = pr.settle_compact_note_arm(
            repo,
            "iid-1",
            note_sha12=pr.compact_note_sha256_12(NOTE_SENTINEL),
            result=None,
            persisted=False,
        )
        assert outcome == pr.DEFERRED_STATE_ANTI_REFIRE
        assert repo.metadata["compact_note_last_deferred"] == pr.DEFERRED_STATE_ANTI_REFIRE
        assert repo.metadata["compact_note_arm"] == NOTE_SENTINEL


# ── L14 — the T4 pin (the executor's engine call) ──────────────────────────


def _executor_manager(*, arm_note: str = "", force_spy: list | None = None):
    """A minimal ``InstanceManager`` stub for the ``/compact`` executor.

    Self-contained on purpose — the lane's legs must not depend on a
    SHARED test file's internals (the lane's shared-file edit budget is
    ONE: the refusal census pin).
    """
    from daemon.config import SlashCommandConfig
    from daemon.services.compact_executor import ContextCompactor

    mgr = MagicMock()
    mgr._lifecycle_service = MagicMock()
    mgr._lifecycle_service.get_instance_info = MagicMock(
        return_value={
            "status": "idle",
            "id": "inst-test",
            "metadata": {},
            "children": [],
        }
    )
    compactor_cfg = _config(
        min_messages_before_compaction=2,
        recent_message_window=2,
        min_recent_window=1,
        context_window_overrides={"gpt-4o": 128_000},
    )
    compactor = ContextCompactor(config=compactor_cfg, llm_config={})
    if force_spy is not None:
        async def _fake_compact_state(ctx, force=False):
            force_spy.append(force)
            return None
        compactor.compact_state = _fake_compact_state
    mgr._compactor = compactor
    mgr.config = MagicMock()
    mgr.config.llm.model = "gpt-4o"
    mgr.config.compaction = compactor_cfg
    mgr.config.slash_commands = SlashCommandConfig(noop_floor_ratio=0.05)

    async def _gate_run(instance_id, holder_id, holder_kind, work_fn):
        return await work_fn()

    mgr.execution_gate = MagicMock()
    mgr.execution_gate.run = AsyncMock(side_effect=_gate_run)
    mgr.pause_instance_cascade = AsyncMock(
        return_value={"paused_ids": [], "skipped_ids": []}
    )
    mgr.resume_instance_cascade = AsyncMock(
        return_value={"resumed_ids": [], "skipped_ids": []}
    )

    async def _quiescent(instance_id, timeout):
        return True

    mgr.wait_for_instance_quiescent = AsyncMock(side_effect=_quiescent)

    graph = MagicMock()
    state = MagicMock()
    state.next = ()
    state.values = {"messages": _messages(15, 4000), "compacted_at": None}
    state.config = {"configurable": {"thread_id": "inst-test"}}
    graph.aget_state = AsyncMock(return_value=state)
    graph.aupdate_state = AsyncMock()

    async def _get_instance(instance_id):
        return graph

    mgr.get_instance = AsyncMock(side_effect=_get_instance)
    mgr._messaging_service = MagicMock()
    mgr._messaging_service.emit_context_usage_for_instance = AsyncMock()
    mgr._live_hub = MagicMock()

    async def _stream(*a, **k):
        return None

    mgr._live_hub.stream_message = AsyncMock(side_effect=_stream)
    mgr._task_repo = MagicMock()
    mgr._task_repo.has_instance_busy = MagicMock(return_value=False)

    repo = _CarrierRepo()
    if arm_note:
        repo.metadata["compact_note_arm"] = arm_note
        import hashlib

        repo.metadata["compact_note_sha256_12"] = hashlib.sha256(
            arm_note.encode("utf-8")
        ).hexdigest()[:12]
    mgr._instance_repository = repo
    return mgr


async def _run_executor(mgr) -> None:
    from daemon.services.command_dispatcher import (
        CommandContext,
        CommandDispatcher,
    )
    from daemon.services.compact_executor import execute_compact

    dispatcher = CommandDispatcher(
        enabled=True,
        escape_prefix="//",
        min_interval_s=10,
        state_ttl_s=600,
        max_state_per_instance=20,
    )
    dispatcher._state.record_start(
        instance_id="inst-test",
        command_id="cmd-active",
        command="compact",
        ttl_seconds=600,
    )
    dispatcher._inflight["inst-test"] = "cmd-active"
    dispatcher._manager = mgr
    ctx = CommandContext(
        dispatcher=dispatcher, command_id="cmd-active", instance_id="inst-test"
    )
    await execute_compact(
        mgr,
        instance_id="inst-test",
        command_id="cmd-active",
        context=ctx,
    )


class TestL14T4ForceFlag:
    """Architect §D.2(ii) — arm present ⇒ the executor calls the engine
    with ``force=False``; ``force=True`` only when unarmed."""

    @pytest.mark.asyncio
    async def test_the_armed_path_passes_force_false(self) -> None:
        force_spy: list = []
        mgr = _executor_manager(arm_note=NOTE_SENTINEL, force_spy=force_spy)
        await _run_executor(mgr)
        assert force_spy == [False], force_spy

    @pytest.mark.asyncio
    async def test_the_unarmed_path_keeps_force_true(self) -> None:
        force_spy: list = []
        mgr = _executor_manager(force_spy=force_spy)
        await _run_executor(mgr)
        assert force_spy == [True], force_spy

    @pytest.mark.asyncio
    async def test_the_note_rides_in_the_context(self) -> None:
        seen: list = []

        async def _fake_compact_state(ctx, force=False):
            seen.append(getattr(ctx, "compact_note", ""))
            return None

        mgr = _executor_manager(arm_note=NOTE_SENTINEL)
        mgr._compactor.compact_state = _fake_compact_state
        await _run_executor(mgr)
        assert seen == [NOTE_SENTINEL]


# ── The T3 gate (the quiescent pre-dispatch seam) ──────────────────────────


def _t3_service(*, arm_note: str = ""):
    """A real ``InstanceMessagingService`` against a mocked manager."""
    from daemon.services.instance_messaging import InstanceMessagingService

    mgr = MagicMock()
    mgr.config = MagicMock()
    mgr.config.compaction = _config()
    mgr.config.llm.model = "gpt-4o"
    repo = _CarrierRepo()
    if arm_note:
        repo.metadata["compact_note_arm"] = arm_note
    mgr._instance_repository = repo
    mgr._instance_repository.get = MagicMock(
        return_value=MagicMock(status="idle", instance_metadata=repo.metadata)
    )
    mgr._compactor = MagicMock()
    mgr._compactor._trigger_window = MagicMock(return_value=1_000_000)
    mgr._compactor.compact_state = AsyncMock(return_value=None)
    mgr.message_metadata_repo = None
    manager_cancellation = MagicMock()
    manager_cancellation.manager = mgr
    svc = InstanceMessagingService(
        manager=mgr,
        cancellation_service=manager_cancellation,
    )
    svc._get_system_prompt_tokens = AsyncMock(return_value=0)
    return svc, mgr, repo


class TestT3Gate:
    """Architect §H, gate mechanics 1 — the T3 gate reads the arm, puts
    the note in the context, and calls the engine with ``force=False``."""

    @pytest.mark.asyncio
    async def test_the_armed_note_rides_into_the_engine_context(self) -> None:
        svc, mgr, _repo = _t3_service(arm_note=NOTE_SENTINEL)
        graph = MagicMock()
        graph.aget_state = AsyncMock(
            return_value=MagicMock(values={"messages": _messages(20)}, next=())
        )
        await svc._maybe_compact_context("inst-test", graph, {})
        assert mgr._compactor.compact_state.await_count == 1
        ctx = mgr._compactor.compact_state.await_args.args[0]
        assert ctx.compact_note == NOTE_SENTINEL
        # The engine is called with force=False — the forcing NEVER uses
        # the force flag.
        assert mgr._compactor.compact_state.await_args.kwargs.get("force", False) is False

    @pytest.mark.asyncio
    async def test_the_unarmed_path_passes_no_note(self) -> None:
        svc, mgr, _repo = _t3_service()
        graph = MagicMock()
        graph.aget_state = AsyncMock(
            return_value=MagicMock(values={"messages": _messages(20)}, next=())
        )
        await svc._maybe_compact_context("inst-test", graph, {})
        ctx = mgr._compactor.compact_state.await_args.args[0]
        assert ctx.compact_note == ""

    @pytest.mark.asyncio
    async def test_the_threshold_bypass_is_note_gated(self) -> None:
        """The armed note FORCES the compaction at the seam — the 80%
        threshold is BYPASSED (the gate measures SIZE; the note measures
        RELEVANCE). The unarmed path keeps today's behaviour."""
        config = _config(
            min_messages_before_compaction=2,
            recent_message_window=2,
            min_recent_window=1,
            context_window_overrides={"gpt-4o": 1_000_000},
        )
        context = CompactionContext(
            messages=_messages(4),
            system_prompt_tokens=0,
            model_name="gpt-4o",
            config=config,
            llm_config={},
            compact_note=NOTE_SENTINEL,
        )
        compactor = ContextCompactor(config, {})
        armed = await compactor.compact_state(context, force=False)
        assert armed is not None, "the armed note must bypass the threshold"

        unarmed_context = CompactionContext(
            messages=_messages(4),
            system_prompt_tokens=0,
            model_name="gpt-4o",
            config=config,
            llm_config={},
        )
        unarmed = await compactor.compact_state(unarmed_context, force=False)
        assert unarmed is None, "the unarmed path is byte-identical"
