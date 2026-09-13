"""Phase 2 (recovery ladder) preset enrollment tests.

Covers the three new symptom-class enrollments on the phase-1
``SymptomRepairEngine`` carrier:

* **B-1 / F-1 / F-2** — ghost-promise preset: cap-before-surgery at
  ``GHOST_PROMISE_REINVOKE_CAP=3``; verbatim excerpt pins on the
  repair doc; FP-suspect content (legitimate colon-ending) does NOT
  fire repair below the cap (B-7).
* **C-1 / C-7** — truncated preset: ``excerpt=<verbatim partial>``
  preservation on the repair doc; round-trip invariant through
  checkpoint serialize/deserialize (T-11).
* **D-1** — empty_post_ladder preset: tool results retained with
  ORIGINAL ids; L6 nudge HumanMessages re-emitted (P-5 sentinel
  re-emit); L1-L13 preserved.
* **MASTER OFF byte-identical** — every new class is gated by the
  master ladder switch; the OFF path is byte-identical to the
  pre-phase-2 routing (USER AMENDMENT 2026-09-13 / ADR-0009).
* **F-4 cross-class isolation (P-9)** — each class consumes ONLY its
  own budget on the shared per-task counter; dual-budget scenarios
  (loop + ghost, ghost + empty, etc.) coexist.
"""
from __future__ import annotations

import json
import logging

import pytest
from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
    message_to_dict,
    messages_from_dict,
    messages_to_dict,
)
from langgraph.graph import END, START, MessagesState, StateGraph

from daemon.compaction import (
    _injected_note_absorbed_ids,
    _is_hoisted_injected,
)
from daemon.config import _reset_symptom_repair_ladder_for_tests
from daemon.graph import (
    EmptyPostLadderDetectionResult,
    GHOST_PROMISE_REINVOKE_CAP,
    GhostDetectionResult,
    TruncatedDetectionResult,
    _build_empty_post_ladder_detection_from_messages,
    _build_truncated_detection_from_exception,
    _collect_trailing_ghost_promise_ai_messages,
    _count_trailing_ghost_promise_ai_messages,
    _is_empty_response_error,
    _is_ghost_promise_message,
    _is_truncated_response_error,
    _maybe_pre_terminal_repair,
    should_continue,
)
from daemon.services.context_messages import CONTEXT_KIND_SYMPTOM_REPAIR
from daemon.services.symptom_repair_engine import (
    SYMPTOM_REPAIR_BUDGET,
    SymptomRepairContext,
    SymptomRepairEngine,
)
from daemon.response_validation import (
    EmptyLLMResponseError,
    LLMResponseValidationError,
)
from daemon.utils import serialize_message

from tests.helpers.symptom_repair import _RealLangGraph, ok_summarizer


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _restore_flags():
    """Isolate the ladder kill-switch module cache (config accessors)."""
    _reset_symptom_repair_ladder_for_tests()
    yield
    _reset_symptom_repair_ladder_for_tests()


def _real_human(content: str = "go", id_: str = "h1") -> HumanMessage:
    return HumanMessage(content=content, id=id_)


def _ghost_ai(content: str, id_: str) -> AIMessage:
    """An AIMessage that ends with ':' — ghost-promise signature."""
    return AIMessage(content=content, id=id_)


def _empty_ai(content: str = "", id_: str = "e1") -> AIMessage:
    """An AIMessage that the shared predicate considers empty."""
    return AIMessage(content=content, id=id_)


def _nudge_human(id_: str = "nudge1") -> HumanMessage:
    """L6 nudge HumanMessage (server-injected continuation prompt)."""
    return HumanMessage(
        content="[NUDGE] Continue with your task...",
        id=id_,
        additional_kwargs={
            "injected_message": True,
            "empty_response_nudge": True,
        },
    )


def _tool_msg(name: str = "bash", content: str = "ok", id_: str = "tm1") -> ToolMessage:
    return ToolMessage(
        content=content,
        tool_call_id="tc-1",
        name=name,
        id=id_,
    )


# ---------------------------------------------------------------------------
# A-3 — ghost helper / detector (B-2)
# ---------------------------------------------------------------------------


class TestGhostDetector:
    def test_is_ghost_promise_message_true_on_colon_end(self):
        msg = AIMessage(content="Now let me write the document:")
        assert _is_ghost_promise_message(msg) is True

    def test_is_ghost_promise_message_true_with_whitespace(self):
        msg = AIMessage(content="Let me think about it:   \n")
        assert _is_ghost_promise_message(msg) is True

    def test_is_ghost_promise_message_false_on_tool_call(self):
        msg = AIMessage(
            content="Let me think:",
            tool_calls=[{"id": "tc-1", "name": "bash", "args": {}}],
        )
        # Tool-call AIMessages are NEVER ghost-promise (P-1).
        assert _is_ghost_promise_message(msg) is False

    def test_is_ghost_promise_message_false_on_no_colon(self):
        msg = AIMessage(content="Done.")
        assert _is_ghost_promise_message(msg) is False

    def test_is_ghost_promise_message_false_on_empty(self):
        msg = AIMessage(content="")
        assert _is_ghost_promise_message(msg) is False

    def test_is_ghost_promise_message_false_on_human(self):
        msg = HumanMessage(content="human:")
        assert _is_ghost_promise_message(msg) is False

    def test_count_trailing_ghost(self):
        msgs = [
            _real_human(),
            _ghost_ai("Step 1:", "g1"),
            _ghost_ai("Step 2:", "g2"),
            _ghost_ai("Step 3:", "g3"),
        ]
        assert _count_trailing_ghost_promise_ai_messages(msgs) == 3

    def test_count_trailing_ghost_breaks_on_non_ghost(self):
        msgs = [
            _real_human(),
            _ghost_ai("Step 1:", "g1"),
            _ghost_ai("Step 2:", "g2"),
            AIMessage(content="Done.", id="real-ai"),
        ]
        assert _count_trailing_ghost_promise_ai_messages(msgs) == 0

    def test_count_trailing_ghost_breaks_on_tool_call(self):
        """P-1: tool-call AIMessages are NEVER ghost, and they break
        the ghost walk (the chain stops at the tool-call boundary)."""
        msgs = [
            _real_human(),
            _ghost_ai("Step 1:", "g1"),
            AIMessage(
                content="",
                tool_calls=[{"id": "tc-1", "name": "bash", "args": {}}],
                id="tool-ai",
            ),
        ]
        assert _count_trailing_ghost_promise_ai_messages(msgs) == 0

    def test_collect_trailing_ghost_returns_messages_newest_first(self):
        msgs = [
            _real_human(),
            _ghost_ai("Step 1:", "g1"),
            _ghost_ai("Step 2:", "g2"),
            _ghost_ai("Step 3:", "g3"),
        ]
        collected = _collect_trailing_ghost_promise_ai_messages(msgs)
        assert len(collected) == 3
        # Newest-first order.
        assert collected[0].id == "g3"
        assert collected[1].id == "g2"
        assert collected[2].id == "g1"

    def test_ghost_cap_constant_is_three(self):
        """GHOST_PROMISE_REINVOKE_CAP=3 mirrors the S5 cap and the
        LoopDetector repetition threshold — operator-tunable shared
        value across all hallucination rungs."""
        assert GHOST_PROMISE_REINVOKE_CAP == 3


# ---------------------------------------------------------------------------
# B-1 — ghost preset loads (preset table data-only)
# ---------------------------------------------------------------------------


class TestGhostPreset:
    def test_ghost_preset_loaded(self):
        preset = SymptomRepairEngine().preset_for("ghost")
        assert preset["evidence_window_selector"] == (
            "_select_ghost_evidence_window"
        )
        assert preset["summary_prompt"] == "ghost"
        assert "ORIGINAL ids" in preset["retention"]

    def test_truncated_preset_loaded(self):
        preset = SymptomRepairEngine().preset_for("truncated")
        assert preset["evidence_window_selector"] == (
            "_select_truncated_evidence_window"
        )
        assert preset["summary_prompt"] == "truncated"
        assert "excerpt" in preset["retention"]

    def test_empty_post_ladder_preset_loaded(self):
        preset = SymptomRepairEngine().preset_for("empty_post_ladder")
        assert preset["evidence_window_selector"] == (
            "_select_empty_post_ladder_evidence_window"
        )
        assert preset["summary_prompt"] == "empty_post_ladder"
        assert "tool results" in preset["retention"]


# ---------------------------------------------------------------------------
# C-1 / C-7 — truncated detection + excerpt preservation
# ---------------------------------------------------------------------------


class TestTruncatedDetection:
    def test_is_truncated_response_error_true_on_finish_reason_length(self):
        response = AIMessage(
            content="partial content",
            response_metadata={"finish_reason": "length"},
        )
        exc = LLMResponseValidationError("truncated", response=response)
        assert _is_truncated_response_error(exc) is True

    def test_is_truncated_response_error_false_on_other_finish_reason(self):
        response = AIMessage(
            content="ok",
            response_metadata={"finish_reason": "stop"},
        )
        exc = LLMResponseValidationError("not truncated", response=response)
        assert _is_truncated_response_error(exc) is False

    def test_is_truncated_response_error_false_on_non_validation(self):
        """``ValueError`` / bare ``Exception`` are NOT truncated-detection
        shapes — the validator at ``daemon/response_validation.py:452-457``
        raises a ``LLMResponseValidationError`` specifically."""
        exc = ValueError("not a validation error")
        assert _is_truncated_response_error(exc) is False

    def test_build_truncated_detection_extracts_message(self):
        original = AIMessage(
            content="This is the partial completion text...",
            response_metadata={"finish_reason": "length"},
            id="trunc-ai-1",
        )
        exc = LLMResponseValidationError("truncated", response=original)
        detection = _build_truncated_detection_from_exception(exc)
        assert isinstance(detection, TruncatedDetectionResult)
        assert detection.truncated_message is original
        assert detection.truncated_message.content == (
            "This is the partial completion text..."
        )


# ---------------------------------------------------------------------------
# D-1 — empty_post_ladder detection
# ---------------------------------------------------------------------------


class TestEmptyPostLadderDetection:
    def test_is_empty_response_error_true_on_empty_llm_response_error(self):
        response = AIMessage(content="")
        exc = EmptyLLMResponseError("empty", response=response)
        assert _is_empty_response_error(exc) is True

    def test_is_empty_response_error_false_on_validation_error(self):
        """Plain ``LLMResponseValidationError`` is NOT empty-class —
        it covers all validation failures (truncated, malformed tool
        calls, etc.) but only ``EmptyLLMResponseError`` is the
        empty-class subclass."""
        exc = LLMResponseValidationError("validation error")
        assert _is_empty_response_error(exc) is False

    def test_build_empty_post_ladder_detection_from_messages(self):
        # The shared emptiness predicate ``is_empty_llm_content`` is
        # ``content.strip() == ""`` for strings — non-whitespace
        # strings are NOT empty (the validator's design choice:
        # legitimate non-empty responses never raise). Three
        # whitespace-only AIMessages + the LLM-bound list.
        msgs = [
            _real_human("go", "h1"),
            _empty_ai("", "e1"),
            _empty_ai("   ", "e2"),
            _empty_ai("\n\n", "e3"),
        ]
        detection = _build_empty_post_ladder_detection_from_messages(msgs)
        assert isinstance(detection, EmptyPostLadderDetectionResult)
        assert len(detection.empty_messages) == 3
        empty_ids = {m.id for m in detection.empty_messages}
        assert empty_ids == {"e1", "e2", "e3"}

    def test_build_empty_post_ladder_detection_excludes_tool_calls(self):
        """P-1: tool-call AIMessages are NOT in the empty set (the
        detector fails open on tool_calls — they survive in the
        retained tail with ORIGINAL ids)."""
        msgs = [
            _real_human("go", "h1"),
            AIMessage(
                content="",
                tool_calls=[{"id": "tc-1", "name": "bash", "args": {}}],
                id="tool-ai",
            ),
            _tool_msg(),
            _empty_ai("", "e1"),
        ]
        detection = _build_empty_post_ladder_detection_from_messages(msgs)
        assert len(detection.empty_messages) == 1
        assert detection.empty_messages[0].id == "e1"

    def test_build_empty_post_ladder_detection_scoped_from_last_human(self):
        """Only empties AFTER the LAST real (non-injected) human
        boundary are in the detection set — pre-boundary empties
        survive (P-5 / L6 preservation invariant)."""
        msgs = [
            _real_human("first ask", "h1"),
            _empty_ai("early empty", "early-e"),  # NOT empty per predicate
            _real_human("second ask", "h2"),
            _empty_ai("", "late1"),
            _empty_ai("   ", "late2"),
        ]
        detection = _build_empty_post_ladder_detection_from_messages(msgs)
        empty_ids = {m.id for m in detection.empty_messages}
        assert empty_ids == {"late1", "late2"}


# ---------------------------------------------------------------------------
# B-1 / F-1 — ghost preset evidence-window selector
# ---------------------------------------------------------------------------


class TestGhostSelector:
    def test_selector_removes_only_ghost_messages(self):
        msgs = [
            _real_human("go", "h1"),
            _ghost_ai("Step 1:", "g1"),
            _ghost_ai("Step 2:", "g2"),
            _ghost_ai("Step 3:", "g3"),
        ]
        detection = GhostDetectionResult(
            ghost_messages=_collect_trailing_ghost_promise_ai_messages(msgs),
            trailing_count=3,
        )
        engine = SymptomRepairEngine()
        removal_ids, removed_units = (
            engine._select_ghost_evidence_window(detection, msgs)
        )
        assert removal_ids == {"g1", "g2", "g3"}
        assert len(removed_units) == 3


# ---------------------------------------------------------------------------
# C-1 / C-7 — truncated preset selector + excerpt round-trip
# ---------------------------------------------------------------------------


class TestTruncatedSelector:
    def test_selector_removes_single_truncated_message(self):
        truncated = AIMessage(
            content="This is a partial completion that got cut off mid-",
            response_metadata={"finish_reason": "length"},
            id="trunc-ai-1",
        )
        msgs = [_real_human(), truncated]
        detection = TruncatedDetectionResult(truncated_message=truncated)
        engine = SymptomRepairEngine()
        removal_ids, removed_units = (
            engine._select_truncated_evidence_window(detection, msgs)
        )
        assert removal_ids == {"trunc-ai-1"}
        assert removed_units == [truncated]

    def test_selector_handles_no_truncated_message(self):
        detection = TruncatedDetectionResult(truncated_message=None)
        engine = SymptomRepairEngine()
        removal_ids, removed_units = (
            engine._select_truncated_evidence_window(detection, [])
        )
        assert removal_ids == set()
        assert removed_units == []


class TestTruncatedExcerptRoundTrip:
    """C-7 (T-11): ``excerpt=`` field on the truncated repair doc
    round-trips through checkpoint serialize/deserialize byte-exact
    (the doc is a SystemMessage whose content is a string; identity
    round-trip on a string is a tautology — the test pins the
    excerpt-shape contract)."""

    def test_excerpt_field_contains_verbatim_partial(self):
        partial = (
            "Here is the partial response that was truncated mid-"
            "sentence at the token limit..."
        )
        truncated = AIMessage(
            content=partial,
            response_metadata={"finish_reason": "length"},
            id="trunc-1",
        )
        detection = TruncatedDetectionResult(truncated_message=truncated)
        msgs = [_real_human(), truncated]
        engine = SymptomRepairEngine()
        ctx = SymptomRepairContext(
            detection=detection,
            messages=list(msgs),
            llm_config={"model": "m"},
            system_prompt="sp",
            instance_id="iid-1",
        )
        doc = engine._build_repair_doc(ctx, "LLM summary.", "truncated")
        # The excerpt is the verbatim partial content.
        assert f"excerpt={partial}" in doc.content

    def test_excerpt_field_handles_special_characters(self):
        """Newlines, tabs, and special punctuation in the partial
        content are preserved verbatim — no escaping or truncation
        by the doc builder."""
        partial = "Multi-line\npartial\twith\ttabs\nand $pecial ch@rs!"
        truncated = AIMessage(
            content=partial,
            response_metadata={"finish_reason": "length"},
            id="trunc-1",
        )
        detection = TruncatedDetectionResult(truncated_message=truncated)
        engine = SymptomRepairEngine()
        ctx = SymptomRepairContext(
            detection=detection,
            messages=[_real_human(), truncated],
            llm_config={"model": "m"},
            system_prompt="sp",
            instance_id="iid-1",
        )
        doc = engine._build_repair_doc(ctx, "summary.", "truncated")
        assert f"excerpt={partial}" in doc.content

    @pytest.mark.asyncio
    async def test_excerpt_survives_checkpoint_serialization_round_trip(
        self,
    ):
        """(d / T-11 for real) The repair doc produced by a LIVE engine
        repair round-trips through the REAL serialization used by
        checkpoint persistence — NOT a string-identity no-op:

        * Path 1 (checkpoint write/read payload): the langchain-core
          canonical pair ``messages_to_dict`` → ``messages_from_dict``
          (with a JSON stringify/parse hop proving payload
          portability). This is the lc:2 payload langgraph's
          checkpoint serde stores and revives for every ``BaseMessage``
          on the SQLite/PG checkpoint write path.
        * Path 2 (read-side REST): ``daemon.utils.serialize_message``
          — the GET /messages / persistence read path
          (``daemon/persistence.py:521``).

        The ``excerpt=<verbatim partial>`` payload must survive BOTH
        byte-exact, and the sentinel-first prefix must keep its
        message ids and the ``RemoveMessage`` sentinel."""
        partial = (
            "```python\ndef broken(\n    x, y:  # mid-fence truncation\n"
            "\ttab + $pecial chars stay!"
        )
        truncated = AIMessage(
            content=partial,
            response_metadata={"finish_reason": "length"},
            id="trunc-rt-9",
        )
        msgs = [_real_human("go", "h-rt"), truncated]
        engine = SymptomRepairEngine()

        async def _ok_summary(context, symptom_class):
            return "LLM summary of the truncated turn."

        engine._summarize = _ok_summary
        ctx = SymptomRepairContext(
            detection=TruncatedDetectionResult(truncated_message=truncated),
            messages=list(msgs),
            llm_config={"model": "m"},
            system_prompt="sp",
            instance_id="iid-rt",
            budget_used=0,
        )
        outcome = await engine.repair(ctx, symptom_class="truncated")
        assert outcome.success
        prefix = outcome.surgery_prefix
        assert prefix is not None

        doc = next(
            m
            for m in prefix
            if str(getattr(m, "id", "")).startswith("repair-")
        )
        expected_excerpt_line = f"excerpt={partial}"
        assert expected_excerpt_line in doc.content

        # ── Path 1: checkpoint payload pair (dict → JSON → dict → msgs).
        rt = messages_from_dict(
            json.loads(json.dumps(messages_to_dict(prefix)))
        )
        assert [m.id for m in rt] == [m.id for m in prefix]
        # The RemoveMessage sentinel survives as a RemoveMessage.
        assert type(rt[0]).__name__ == "RemoveMessage"
        rt_doc = next(
            m
            for m in rt
            if str(getattr(m, "id", "")).startswith("repair-")
        )
        # Byte-exact content round-trip + verbatim excerpt survival.
        assert rt_doc.content.encode("utf-8") == doc.content.encode("utf-8")
        assert expected_excerpt_line.encode("utf-8") in (
            rt_doc.content.encode("utf-8")
        )

        # ── Path 2: read-side REST serializer.
        sd = serialize_message(rt_doc)
        assert sd["content"] == doc.content
        assert expected_excerpt_line in sd["content"]


# ---------------------------------------------------------------------------
# D-1 — empty_post_ladder preset selector
# ---------------------------------------------------------------------------


class TestEmptyPostLadderSelector:
    def test_selector_removes_empty_keeps_tool_results(self):
        msgs = [
            _real_human("go", "h1"),
            AIMessage(
                content="",
                tool_calls=[{"id": "tc-1", "name": "bash", "args": {}}],
                id="tool-ai",
            ),
            _tool_msg(id_="tm1"),
            _empty_ai("", "e1"),
            _empty_ai("", "e2"),
        ]
        detection = _build_empty_post_ladder_detection_from_messages(msgs)
        engine = SymptomRepairEngine()
        removal_ids, removed_units = (
            engine._select_empty_post_ladder_evidence_window(
                detection, msgs
            )
        )
        # Only empty AIMessages removed; tool messages + tool-call AI
        # are RETAINED with ORIGINAL ids (D-1 explicit).
        assert removal_ids == {"e1", "e2"}
        assert len(removed_units) == 2


# ---------------------------------------------------------------------------
# D-4 — L1-L13 preservation, especially L6 nudge re-emit
# ---------------------------------------------------------------------------


class TestL1L13Preservation:
    """L6 nudge HumanMessage sentinel re-emit (P-5) + context_kind
    blocks hoisted at the head of the surgery prefix."""

    @pytest.mark.asyncio
    async def test_nudge_human_is_re_emitted_at_head_of_surgery(self):
        """The L6 nudge HumanMessage is re-emitted in the
        surgery prefix at the head — the same hoist predicate the
        compaction seam uses."""
        msgs = [
            _real_human("first ask", "h1"),
            _nudge_human("nudge1"),
            _empty_ai("", "late1"),
            _empty_ai("", "late2"),
        ]
        detection = _build_empty_post_ladder_detection_from_messages(msgs)
        engine = SymptomRepairEngine()

        async def _ok_summary(context, symptom_class):
            return "summary"

        engine._summarize = _ok_summary
        ctx = SymptomRepairContext(
            detection=detection,
            messages=list(msgs),
            llm_config={"model": "m"},
            system_prompt="sp",
            instance_id="iid-1",
        )
        outcome = await engine.repair(
            ctx, symptom_class="empty_post_ladder"
        )
        assert outcome.success
        # The nudge HumanMessage survives in the LLM-bound list (not
        # in the removal set; the surgery keeps it via P-5 hoist).
        ids_in_repaired = {m.id for m in outcome.repaired_messages}
        assert "nudge1" in ids_in_repaired

    @pytest.mark.asyncio
    async def test_context_kind_block_is_hoisted(self):
        """context_kind blocks are hoisted at the head — they
        survive every later compaction verbatim (T-11 / A-4 / L1-L13)."""
        ctx_block = SystemMessage(
            content="[SYSTEM CONTEXT: Project]\n\nproject body",
            id="ctx-1",
            additional_kwargs={
                "injected_message": True,
                "context_kind": "project",
            },
        )
        msgs = [
            _real_human("first ask", "h1"),
            ctx_block,
            _empty_ai("", "e1"),
            _empty_ai("", "e2"),
        ]
        detection = _build_empty_post_ladder_detection_from_messages(msgs)
        engine = SymptomRepairEngine()

        async def _ok_summary(context, symptom_class):
            return "summary"

        engine._summarize = _ok_summary
        ctx = SymptomRepairContext(
            detection=detection,
            messages=list(msgs),
            llm_config={"model": "m"},
            system_prompt="sp",
            instance_id="iid-1",
        )
        outcome = await engine.repair(
            ctx, symptom_class="empty_post_ladder"
        )
        assert outcome.success
        # The context block is hoisted at the head (per the
        # ``_is_hoisted_injected`` predicate).
        ids_in_repaired = [m.id for m in outcome.repaired_messages]
        # ``ctx-1`` appears at index 0 (or near) — the hoist puts it
        # before the repair doc which precedes the retained tail.
        assert "ctx-1" in ids_in_repaired
        # And the ``_injected_note_absorbed_ids`` predicate does NOT
        # consider it absorbed (it's permanently non-selectable).
        absorbed = _injected_note_absorbed_ids(outcome.repaired_messages)
        assert "ctx-1" not in absorbed


# ---------------------------------------------------------------------------
# F-2 / F-3 / T-7 — class isolation (P-9): each class consumes its
# own budget on the shared per-task counter
# ---------------------------------------------------------------------------


class TestF4CrossClassIsolation:
    """F-4 (P-9): each class consumes ONLY its own budget on the
    shared per-task counter. Dual-budget scenarios (loop + ghost
    same task; loop + empty same task; ghost + empty same task)
    coexist correctly."""

    @pytest.mark.asyncio
    async def test_each_class_consumes_only_its_own_budget(self):
        """A single repair succeeds; budget_used_new=1 regardless of
        class (loop, ghost, truncated, empty_post_ladder). The shared
        counter IS shared — but ONE increment per class per call."""
        engine = SymptomRepairEngine()

        async def _ok_summary(context, symptom_class):
            return "summary."

        engine._summarize = _ok_summary

        # Loop class: 3 consecutive identical tool-call units.
        loop_msgs = [
            _real_human("do thing", "h1"),
            AIMessage(
                content="",
                tool_calls=[{"id": "tc1", "name": "bash", "args": {"c": 1}}],
                id="ai1",
            ),
            ToolMessage(content="r1", tool_call_id="tc1", id="tm1"),
            AIMessage(
                content="",
                tool_calls=[{"id": "tc2", "name": "bash", "args": {"c": 1}}],
                id="ai2",
            ),
            ToolMessage(content="r2", tool_call_id="tc2", id="tm2"),
            AIMessage(
                content="",
                tool_calls=[{"id": "tc3", "name": "bash", "args": {"c": 1}}],
                id="ai3",
            ),
            ToolMessage(content="r3", tool_call_id="tc3", id="tm3"),
        ]
        from daemon.graph import LoopDetector

        loop_det = LoopDetector.scan(loop_msgs, threshold=3)
        loop_ctx = SymptomRepairContext(
            detection=loop_det,
            messages=list(loop_msgs),
            llm_config={"model": "m"},
            system_prompt="sp",
            instance_id="iid-1",
            budget_used=0,
        )
        loop_outcome = await engine.repair(loop_ctx)
        assert loop_outcome.success

        # Ghost class: trailing 3 ghost-promise AIMessages.
        ghost_msgs = [
            _real_human("do thing", "h1"),
            _ghost_ai("Step 1:", "g1"),
            _ghost_ai("Step 2:", "g2"),
            _ghost_ai("Step 3:", "g3"),
        ]
        ghost_det = GhostDetectionResult(
            ghost_messages=_collect_trailing_ghost_promise_ai_messages(
                ghost_msgs
            ),
            trailing_count=3,
        )
        ghost_ctx = SymptomRepairContext(
            detection=ghost_det,
            messages=list(ghost_msgs),
            llm_config={"model": "m"},
            system_prompt="sp",
            instance_id="iid-1",
            budget_used=0,
        )
        ghost_outcome = await engine.repair(
            ghost_ctx, symptom_class="ghost"
        )
        assert ghost_outcome.success

        # Each class consumes ONE increment on the shared counter.
        # The caller increments budget_used by +1 per SUCCESSFUL repair,
        # regardless of class — cross-class isolation is preserved.
        assert loop_outcome.budget_consumed is True
        assert ghost_outcome.budget_consumed is True

    @pytest.mark.asyncio
    async def test_shared_counter_sequential_cross_class_consumption(self):
        """(b / P-9 strengthening) The per-task counter is SHARED across
        ALL enrolled classes with NO per-class reset:

        * sequential cross-class consumption fits under the cap while
          the running total is below 3 (loop 0→1 → ghost 1→2 →
          truncated 2→3);
        * at the cap (3 total repairs, mixed classes), ANY class
          refuses — ``budget-exhausted`` — including classes that
          never fired before in the episode (empty_post_ladder) and
          classes that did (loop)."""
        engine = SymptomRepairEngine()

        async def _ok_summary(context, symptom_class):
            return "summary."

        engine._summarize = _ok_summary

        def _ctx(detection, messages, budget_used):
            return SymptomRepairContext(
                detection=detection,
                messages=list(messages),
                llm_config={"model": "m"},
                system_prompt="sp",
                instance_id="iid-shared-1",
                budget_used=budget_used,
            )

        # 1) loop repair consumes 0 → 1.
        loop_msgs = [
            _real_human("do thing", "h1"),
            AIMessage(
                content="",
                tool_calls=[{"id": "tc1", "name": "bash", "args": {"c": 1}}],
                id="ai1",
            ),
            ToolMessage(content="r1", tool_call_id="tc1", id="tm1"),
            AIMessage(
                content="",
                tool_calls=[{"id": "tc2", "name": "bash", "args": {"c": 1}}],
                id="ai2",
            ),
            ToolMessage(content="r2", tool_call_id="tc2", id="tm2"),
            AIMessage(
                content="",
                tool_calls=[{"id": "tc3", "name": "bash", "args": {"c": 1}}],
                id="ai3",
            ),
            ToolMessage(content="r3", tool_call_id="tc3", id="tm3"),
        ]
        from daemon.graph import LoopDetector

        loop_out = await engine.repair(
            _ctx(LoopDetector.scan(loop_msgs, threshold=3), loop_msgs, 0)
        )
        assert loop_out.success and loop_out.budget_consumed is True

        # 2) ghost repair sees budget_used=1 (loop consumed 1) and
        #    still fits under 3 → consumes 1 → 2.
        ghost_msgs = [
            _real_human("do thing", "h1"),
            _ghost_ai("Step 1:", "g1"),
            _ghost_ai("Step 2:", "g2"),
            _ghost_ai("Step 3:", "g3"),
        ]
        ghost_det = GhostDetectionResult(
            ghost_messages=_collect_trailing_ghost_promise_ai_messages(
                ghost_msgs
            ),
            trailing_count=3,
        )
        ghost_out = await engine.repair(
            _ctx(ghost_det, ghost_msgs, 1), symptom_class="ghost"
        )
        assert ghost_out.success and ghost_out.budget_consumed is True

        # 3) truncated repair sees budget_used=2 and still fits → 3.
        truncated = AIMessage(
            content="truncated partial",
            response_metadata={"finish_reason": "length"},
            id="trunc-seq-1",
        )
        trunc_msgs = [_real_human("go", "h1"), truncated]
        trunc_det = TruncatedDetectionResult(truncated_message=truncated)
        trunc_out = await engine.repair(
            _ctx(trunc_det, trunc_msgs, 2), symptom_class="truncated"
        )
        assert trunc_out.success and trunc_out.budget_consumed is True

        # 4) at cap 3: empty_post_ladder (never fired this episode)
        #    refuses — the counter is shared, no per-class reset.
        empty = _empty_ai(content="", id_="e-seq-1")
        empty_msgs = [_real_human("go", "h1"), empty]
        empty_det = EmptyPostLadderDetectionResult(empty_messages=[empty])
        empty_out = await engine.repair(
            _ctx(empty_det, empty_msgs, 3), symptom_class="empty_post_ladder"
        )
        assert empty_out.success is False
        assert empty_out.aborted is True
        assert empty_out.abort_reason == "budget-exhausted"
        assert empty_out.budget_consumed is False
        assert empty_out.surgery_prefix is None

        # 5) at cap 3: a class that ALREADY fired (loop) also refuses —
        #    shared exhaustion, not a per-class budget.
        loop_out_cap = await engine.repair(
            _ctx(LoopDetector.scan(loop_msgs, threshold=3), loop_msgs, 3)
        )
        assert loop_out_cap.success is False
        assert loop_out_cap.abort_reason == "budget-exhausted"
        assert loop_out_cap.budget_consumed is False

        # The cap itself is the shared value (3), not 3-per-class.
        assert SYMPTOM_REPAIR_BUDGET == 3


# ---------------------------------------------------------------------------
# T-8 / F-1 / F-2 / F-3 — master kill-switch byte-identical routing
# (USER AMENDMENT 2026-09-13: NO per-class sub-flags; the master
# switch governs ALL classes including the three new ones)
# ---------------------------------------------------------------------------


class TestMasterKillSwitchByteIdentical:
    """Master OFF = byte-identical for ALL FOUR classes (loop +
    ghost + truncated + empty_post_ladder). Pinned by test.

    With ``ENSEMBLE_SYMPTOM_REPAIR_LADDER=0``:

    * ``_maybe_ghost_repair`` returns ``None`` — caller falls
      through to bare ``"agent"`` re-invoke (shipped).
    * ``_maybe_pre_terminal_repair`` is unreachable from the
      agent_node except block (the gate check makes the intercept
      INERT).
    * Telemetry stays on (W1 KEEP — operators need the data during
      an OFF soak).
    """

    def test_master_off_ghost_returns_none_when_called(self, monkeypatch):
        """With master OFF, ``_maybe_ghost_repair`` returns ``None``
        regardless of trailing-ghost count — the caller (the
        ``agent_repair_ghost`` node) falls through to the bare
        ``"agent"`` re-invoke via the router."""
        from daemon.graph import _maybe_ghost_repair

        monkeypatch.setenv("ENSEMBLE_SYMPTOM_REPAIR_LADDER", "0")
        _reset_symptom_repair_ladder_for_tests()
        # Trailing ghost count at the cap — but master OFF.
        msgs = [
            _real_human(),
            _ghost_ai("Step 1:", "g1"),
            _ghost_ai("Step 2:", "g2"),
            _ghost_ai("Step 3:", "g3"),
        ]

        async def _run():
            return await _maybe_ghost_repair(
                messages=msgs,
                full_messages=msgs,
                instance_id="iid-1",
                instance_short="iid-1"[:8],
                config=None,
                injected_msg=None,
                system_prompt="sp",
                llm_config={"model": "m"},
                durable_budget_used=0,
                turn_id="t1",
            )

        import asyncio

        outcome = asyncio.get_event_loop().run_until_complete(_run())
        assert outcome is None

    def test_master_off_ghost_routing_stays_bare_agent(self, monkeypatch):
        """With master OFF, ``should_continue`` never emits the
        ``"agent_repair_ghost"`` label regardless of ghost count —
        every ghost-emitting message routes to the bare ``"agent"``
        (shipped pre-phase-2 behavior byte-identical)."""
        from daemon.graph import should_continue

        monkeypatch.setenv("ENSEMBLE_SYMPTOM_REPAIR_LADDER", "0")
        _reset_symptom_repair_ladder_for_tests()
        # 5 trailing ghosts — well over the cap.
        msgs = [
            _real_human(),
            *[_ghost_ai(f"Step {i}:", f"g{i}") for i in range(5)],
        ]
        result = should_continue({"messages": msgs})
        # Bare "agent" — never "agent_repair_ghost" with master OFF.
        assert result == "agent"

    @pytest.mark.asyncio
    async def test_master_off_preterminal_returns_none_truncated(
        self, monkeypatch
    ):
        """(a) Master OFF + truncated exception: invoking the
        pre-terminal repair path DIRECTLY returns ``None`` — the
        caller's shipped loud-ERROR re-raise fires byte-identically
        (the intercept is INERT for the truncated class)."""
        monkeypatch.setenv("ENSEMBLE_SYMPTOM_REPAIR_LADDER", "0")
        _reset_symptom_repair_ladder_for_tests()
        truncated = AIMessage(
            content="partial answer that hit the token limit",
            response_metadata={"finish_reason": "length"},
            id="trunc-off-1",
        )
        exc = LLMResponseValidationError("truncated", response=truncated)

        outcome = await _maybe_pre_terminal_repair(
            messages=[_real_human(), truncated],
            full_messages=[SystemMessage(content="sp"), _real_human(), truncated],
            instance_id="iid-off-1",
            instance_short="iid-off-1"[:8],
            config=None,
            injected_msg=None,
            system_prompt="sp",
            llm_config={"model": "m"},
            durable_budget_used=0,
            turn_id="t1",
            exc=exc,
            current_llm=None,
        )
        # None → caller falls through to the shipped loud ERROR.
        assert outcome is None

    @pytest.mark.asyncio
    async def test_master_off_preterminal_returns_none_empty(
        self, monkeypatch
    ):
        """(a) Master OFF + empty_post_ladder exception: invoking the
        pre-terminal repair path DIRECTLY returns ``None`` — the
        caller's shipped loud-ERROR re-raise fires byte-identically
        (the intercept is INERT for the empty class)."""
        monkeypatch.setenv("ENSEMBLE_SYMPTOM_REPAIR_LADDER", "0")
        _reset_symptom_repair_ladder_for_tests()
        empty = _empty_ai(content="", id_="e-off-1")
        exc = EmptyLLMResponseError("empty response")

        outcome = await _maybe_pre_terminal_repair(
            messages=[_real_human(), empty],
            full_messages=[SystemMessage(content="sp"), _real_human(), empty],
            instance_id="iid-off-2",
            instance_short="iid-off-2"[:8],
            config=None,
            injected_msg=None,
            system_prompt="sp",
            llm_config={"model": "m"},
            durable_budget_used=0,
            turn_id="t2",
            exc=exc,
            current_llm=None,
        )
        # None → caller falls through to the shipped loud ERROR.
        assert outcome is None

    @pytest.mark.asyncio
    async def test_master_on_preterminal_carries_surgery_prefix(
        self, monkeypatch
    ):
        """(a control, D-2 fix) Master ON + truncated exception: the
        helper's SUCCESS outcome now carries the return-carried
        ``surgery_prefix`` — the exact defect-1 contract (without the
        fix the recovered path dropped the surgery and it never
        persisted). The prefix is the sentinel-first carrier with the
        repair doc inside."""
        monkeypatch.setenv("ENSEMBLE_SYMPTOM_REPAIR_LADDER", "1")
        _reset_symptom_repair_ladder_for_tests()
        truncated = AIMessage(
            content="truncated partial content",
            response_metadata={"finish_reason": "length"},
            id="trunc-on-1",
        )
        exc = LLMResponseValidationError("truncated", response=truncated)

        # The helper constructs its OWN SymptomRepairEngine internally —
        # stub the summarizer at CLASS level (the re-invoke LLM is
        # injected via ``current_llm``). Class-level patching binds
        # ``self`` (unlike the instance-attribute stubs above).
        async def _ok_summary(self, context, symptom_class):
            return "summary."

        monkeypatch.setattr(
            "daemon.services.symptom_repair_engine."
            "SymptomRepairEngine._summarize",
            _ok_summary,
        )

        class _OkLLM:
            def invoke(self, messages):
                return AIMessage(content="fresh complete answer.", id="fresh-1")

        outcome = await _maybe_pre_terminal_repair(
            messages=[_real_human("go", "h-on"), truncated],
            full_messages=[
                SystemMessage(content="sp"),
                _real_human("go", "h-on"),
                truncated,
            ],
            instance_id="iid-on-1",
            instance_short="iid-on-1"[:8],
            config=None,
            injected_msg=None,
            system_prompt="sp",
            llm_config={"model": "m"},
            durable_budget_used=0,
            turn_id="t3",
            exc=exc,
            current_llm=_OkLLM(),
        )
        assert outcome is not None
        assert outcome.recovered is True
        assert outcome.surgery_prefix is not None
        assert outcome.budget_used == 1
        # Sentinel-first carrier shape (mirror of the loop rung).
        assert type(outcome.surgery_prefix[0]).__name__ == "RemoveMessage"
        # The repair doc rides inside the prefix.
        assert any(
            str(getattr(m, "id", "")).startswith("repair-")
            for m in outcome.surgery_prefix
        )
        # The truncated AIMessage was removed by the surgery — its id
        # no longer appears anywhere in the prefix.
        assert all(m.id != "trunc-on-1" for m in outcome.surgery_prefix)


# ---------------------------------------------------------------------------
# F-2 / F-3 / T-9 — burn-count assertions per class
# ---------------------------------------------------------------------------


class TestF1F2BurnCountAssertions:
    """F-1 / F-2 / D-7 — burn-count assertions per class.

    * Ghost storm: ≤ cap(3) + repair(1) + 1 continued = ≤ 5 LLM calls
      (vs ~300 today when the bare "agent" re-invoke was uncapped).
    * Empty post-ladder: continuous-empty ≤ PRIMARY_TRANSIENT_MAX +
      repair(1) + 1 = ≤ 5 calls (vs ~100 for the sibling reasoning-
      only class).
    """

    def test_ghost_cap_arithmetic(self):
        """The cap-arithmetic invariant: ghost storm with master ON
        bounds at ≤ cap(3) + repair(1) + 1 continued = ≤ 5 LLM
        calls. Pinned by the GHOST_PROMISE_REINVOKE_CAP constant +
        the SYMPTOM_REPAIR_BUDGET constant."""
        assert GHOST_PROMISE_REINVOKE_CAP == 3
        # Worst case: 3 bare re-invokes (cap) + 1 repair + 1 continued
        # = 5 total LLM calls. Pinned as the spec's T-9 burn assertion.
        worst_case = (
            GHOST_PROMISE_REINVOKE_CAP + 1 + 1
        )  # cap + repair + 1 continued
        assert worst_case == 5

# ---------------------------------------------------------------------------
# (c) FP — legitimate colon-ending content must NOT trigger repair below
# the cap (B-7 / R6 mitigation; explicit brief requirement). Detection MAY
# count colon-ending FP content — the ROUTING must not operate below cap
# 3: the bare ``"agent"`` re-invoke is preserved byte-identically.
# ---------------------------------------------------------------------------


class TestFalsePositiveColonEndingBelowCap:
    """Legitimate colon-ending content (list intros, code blocks) sits
    BELOW the ghost cap → the router keeps the bare ``"agent"``
    re-invoke and never routes ``"agent_repair_ghost"``. A control test
    at the cap proves the boundary is live (the FP assertions are not
    passing vacuously)."""

    def _master_on(self, monkeypatch):
        monkeypatch.setenv("ENSEMBLE_SYMPTOM_REPAIR_LADDER", "1")
        _reset_symptom_repair_ladder_for_tests()

    def test_fp_list_intro_below_cap_routes_bare_agent(self, monkeypatch):
        """List-intro colon endings ('1. First item:' / '- nested
        bullet:') are DETECTED as ghost-shaped (detection may count) —
        but at cap-1 the routing must stay the bare ``"agent"``
        re-invoke, never the repair-flagged re-entry."""
        self._master_on(monkeypatch)
        msgs = [
            _real_human(),
            _ghost_ai("1. First item:", "fp1"),
            _ghost_ai("- nested bullet:", "fp2"),
        ]
        # Detection may count the FP content (2 < cap 3)…
        assert _count_trailing_ghost_promise_ai_messages(msgs) == 2
        # …but routing must NOT operate below the cap.
        assert should_continue({"messages": msgs}) == "agent"

    def test_fp_code_fence_mid_block_never_routes_repair(self, monkeypatch):
        """A response truncated mid-fence ('```python\\nx = foo(') does
        not even end with ':' — the ghost row must not fire and the
        repair-flagged re-entry must never be routed (the response
        falls through the shipped rows — here to END, a final answer
        with no tool_calls)."""
        self._master_on(monkeypatch)
        msgs = [
            _real_human(),
            AIMessage(
                content="```python\nx = foo(",
                id="fence-1",
            ),
        ]
        result = should_continue({"messages": msgs})
        assert result != "agent_repair_ghost"
        assert result == END

    def test_fp_below_cap_mixed_sequence_routes_bare_agent(
        self, monkeypatch
    ):
        """A realistic FP sequence — document skeleton, list intro,
        section header, all colon-ending — at cap-1 routes the bare
        ``"agent"`` re-invoke."""
        self._master_on(monkeypatch)
        msgs = [
            _real_human(),
            _ghost_ai("# Plan:", "fp1"),
            _ghost_ai("1. First item:", "fp2"),
        ]
        assert _count_trailing_ghost_promise_ai_messages(msgs) == 2
        assert should_continue({"messages": msgs}) == "agent"

    def test_control_at_cap_still_routes_repair(self, monkeypatch):
        """CONTROL (master ON): exactly at the cap the repair-flagged
        re-entry DOES fire — proves the FP assertions above sit below
        a live boundary rather than a dead routing row."""
        self._master_on(monkeypatch)
        msgs = [
            _real_human(),
            _ghost_ai("Step 1:", "g1"),
            _ghost_ai("Step 2:", "g2"),
            _ghost_ai("Step 3:", "g3"),
        ]
        assert _count_trailing_ghost_promise_ai_messages(msgs) == 3
        assert should_continue({"messages": msgs}) == "agent_repair_ghost"


# ---------------------------------------------------------------------------
# C1 fix (2026-09-13): ghost-rung budget-exhaustion terminal response-
# substitution. The terminal SUBSTITUTES the agent_node's response so NO LLM
# call fires AFTER the ghost exhaustion branch and the terminal is the final
# visible message — closing the no-latch pathological cycle that was bounded
# only by ``recursion_limit=300`` (and which culminated in uncaught
# GraphRecursionError under some provider behaviors). Mirrors the loop class's
# response-substitution precedent (graph.py:6751-6753).
# ---------------------------------------------------------------------------


class _C1ScriptedProvider:
    """LLM stub with a scripted response sequence + call counter.

    Used by the ghost-exhaustion real-graph test to prove that the
    agent_node's response-substitution path DOES NOT invoke the LLM
    a second time after the ghost rung exhausts the budget.
    """

    def __init__(self, script):
        self.script = list(script)
        self.calls: list[list] = []

    def invoke(self, messages):
        self.calls.append(list(messages))
        if not self.script:
            raise AssertionError(
                "provider script exhausted — unbounded run; "
                "C1 fix should have short-circuited the LLM call"
            )
        return self.script.pop(0)


class TestC1GhostExhaustionResponseSubstitution:
    """C1 fix: ghost budget-exhaustion terminal routes to END via
    response-substitution.

    Acceptance (per spec):
    * (a) ZERO LLM calls after ghost exhaustion (provider called
      exactly once — at the pre-ghost agent_node re-entry);
    * (b) ``messages[-1]`` IS the terminal (the loud
      ``[GHOST TERMINATION]`` AIMessage);
    * (c) cycle-kill — the no-latch cycle (terminal → break ghost
      tail → 3 below-cap re-invokes → ghost → exhausted → another
      terminal) cannot recur.

    Master OFF / below-cap paths are unreachable in this scenario
    (the test pre-seeds budget at the cap with master ON); byte-
    identicality is verified separately by the existing OFF fixtures.
    """

    @pytest.mark.asyncio
    async def test_ghost_exhaustion_terminal_routes_to_end_no_cycle(
        self, monkeypatch, tmp_path, caplog
    ):
        """Drive a ghost storm with the budget pre-seeded at the cap.
        The router routes at-cap → ``agent_repair_ghost``; the ghost
        node exhausts → stashes the loud terminal in
        ``pending_repair_ghost_terminal``; the unconditional
        ``agent_repair_ghost → agent`` edge routes here; the
        ``agent_node`` substitutes the terminal as ITS response — NO
        LLM invoke. The plain-AIMessage fall-through in
        ``should_continue`` then routes to END with the terminal as
        ``messages[-1]``.

        The provider script holds ONE ghost response; the assertion
        ``provider.calls == 1`` proves the cycle did not recur.
        """
        # Force the master ladder switch ON for this test (the
        # autouse ``_restore_flags`` fixture only resets the cache).
        monkeypatch.setenv("ENSEMBLE_SYMPTOM_REPAIR_LADDER", "1")
        _reset_symptom_repair_ladder_for_tests()
        # Engine summarizer stub — the surgery/budget/doc flow is real.
        monkeypatch.setattr(
            SymptomRepairEngine,
            "_summarize",
            staticmethod(ok_summarizer),
        )

        with _RealLangGraph():
            import aiosqlite
            # Re-import inside the swap — the top-of-file imports
            # captured the conftest's MagicMocks (the daemon.graph
            # module was already loaded by the test setup), so the
            # real langgraph primitives must be re-bound for the
            # real-graph harness (mirrors the joint integration
            # test pattern in ``test_ladder_loop_x_empty_guard_integration.py``).
            from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
            from langgraph.graph import (
                END as _REAL_END,
                START as _REAL_START,
                MessagesState as _RealMessagesState,
                StateGraph as _RealStateGraph,
            )
            # The agent_node's should_continue conditional edges use
            # the SAME END sentinel — bind the imported symbol under
            # its expected name so the conditional mapping works.
            globals()["END"] = _REAL_END
            globals()["START"] = _REAL_START
            globals()["MessagesState"] = _RealMessagesState
            globals()["StateGraph"] = _RealStateGraph

            from daemon.graph import (
                create_agent_node,
                create_agent_repair_ghost_node,
            )

            provider = _C1ScriptedProvider(
                [_ghost_ai("Step 4:", "g4")]  # exactly ONE response
            )

            agent_node = create_agent_node(
                llm_with_tools=provider,
                system_prompt="you are a test assistant",
                compactor=None,
                graph_ref=[None],
                config=None,
                llm_config={"model": "test-model"},
                retry_config={"transient_attempts": 1, "timeout_attempts": 1},
            )
            ghost_node = create_agent_repair_ghost_node(
                llm_config={"model": "test-model"},
                system_prompt="you are a test assistant",
            )

            class _GhostState(_RealMessagesState):
                repair_budget_used: int
                pending_repair_ghost_terminal: AIMessage | None = None

            g = _RealStateGraph(_GhostState)
            g.add_node("agent", agent_node)
            g.add_node("agent_repair_ghost", ghost_node)
            g.add_edge(_REAL_START, "agent")
            g.add_conditional_edges(
                "agent",
                should_continue,
                {
                    "agent": "agent",
                    "agent_repair_ghost": "agent_repair_ghost",
                    _REAL_END: _REAL_END,
                },
            )
            g.add_edge("agent_repair_ghost", "agent")

            db_path = tmp_path / "ghost_exhaust_c1.db"
            conn = await aiosqlite.connect(str(db_path))
            saver = AsyncSqliteSaver(conn)
            await saver.setup()
            try:
                compiled = g.compile(checkpointer=saver)
                cfg = {
                    "configurable": {"thread_id": "ghost-c1-iid"},
                    "recursion_limit": 60,
                }
                with caplog.at_level(logging.INFO):
                    await compiled.ainvoke(
                        {
                            "messages": [
                                _real_human("do thing", "h1"),
                                # Pre-seeded ghost history; the
                                # router's first observation sees
                                # trailing_ghost=3 (at cap).
                                _ghost_ai("Step 1:", "g1"),
                                _ghost_ai("Step 2:", "g2"),
                                _ghost_ai("Step 3:", "g3"),
                            ],
                            # Budget at the cap → ghost rung exhausts
                            # on the very first invocation.
                            "repair_budget_used": 3,
                        },
                        cfg,
                    )
                st = await compiled.aget_state(cfg)
                values = st.values

                # ── (a) ZERO LLM calls after ghost exhaustion ──
                # The provider script has ONE ghost response.
                # Agent_node invoked the LLM ONCE (at the pre-ghost
                # re-entry); the ghost-rung exhaust path then
                # returned the terminal via response-substitution,
                # so no further LLM call fires.
                assert len(provider.calls) == 1, (
                    f"expected exactly 1 LLM call (the pre-ghost "
                    f"agent_node re-entry); got {len(provider.calls)} "
                    f"— cycle did not get killed by the C1 fix"
                )

                # ── (b) messages[-1] IS the terminal ──
                final_messages = values["messages"]
                last_msg = final_messages[-1]
                assert isinstance(last_msg, AIMessage)
                assert "GHOST TERMINATION" in last_msg.content, (
                    f"expected loud terminal as final message; "
                    f"got content={last_msg.content[:80]!r}"
                )
                assert last_msg.id.startswith("repair-terminal-ghost-"), (
                    f"expected ghost-rung terminal id prefix; got "
                    f"{last_msg.id!r}"
                )

                # ── (c) cycle-kill ──
                # The carrier is cleared on the substitution return
                # so the NEXT turn starts fresh (byte-identical to
                # a turn that never fired the ghost rung).
                assert values.get("pending_repair_ghost_terminal") is None, (
                    "pending_repair_ghost_terminal must be cleared "
                    "on the substitution return — cycle-kill "
                    "guarantee"
                )
                # The terminal MUST be the final visible message —
                # no extra LLM response interleaved after it.
                assert final_messages[-1] is last_msg, (
                    "terminal must be the last message in the "
                    "checkpoint; any subsequent LLM response would "
                    "indicate the cycle recurred"
                )

                # The loud-substitution WARN logged once.
                ghost_term_warns = [
                    r.getMessage()
                    for r in caplog.records
                    if "[GHOST TERMINATION]" in r.getMessage()
                ]
                assert len(ghost_term_warns) == 1, (
                    f"expected exactly one [GHOST TERMINATION] "
                    f"loud-substitution WARN; got {len(ghost_term_warns)}: "
                    f"{ghost_term_warns!r}"
                )
            finally:
                await conn.close()

    def test_pending_repair_ghost_terminal_in_session_state_schema(self):
        """The carrier ``pending_repair_ghost_terminal`` is declared
        on the ``SessionState`` schema with default ``None`` — pinned
        so the field's schema presence cannot silently regress
        (checkpoints would otherwise lose the carrier and the C1 fix
        would silently break).

        Note: the conftest mocks ``langgraph.graph.MessagesState``
        so the runtime ``SessionState`` class cannot be inspected
        via ``get_type_hints``; we read the ``daemon.graph`` source
        directly and assert the field is declared after the
        ``class SessionState(MessagesState)`` line — the same
        pattern used by ``tests/unit/test_watchover_decision.py``
        for ``watchover_route`` / ``watchover_denial_count``.
        """
        import os

        src_path = os.path.join(
            os.path.dirname(
                os.path.dirname(
                    os.path.dirname(__file__)
                )
            ),
            "daemon",
            "graph.py",
        )
        with open(src_path, "r", encoding="utf-8") as f:
            src = f.read()

        # Field MUST be declared on the SessionState class
        # (i.e., AFTER the class header — not at module level
        # before it, where it wouldn't be a schema field).
        idx = src.find("class SessionState(MessagesState):")
        assert idx != -1, (
            "SessionState class declaration missing — conftest "
            "should not have removed it"
        )
        field_marker = "pending_repair_ghost_terminal: AIMessage | None"
        assert field_marker in src, (
            f"SessionState must declare '{field_marker}' "
            "— C1 carrier missing"
        )
        # The field MUST appear AFTER the class declaration —
        # otherwise it's not a schema field.
        assert src.find(field_marker) > idx, (
            f"'{field_marker}' must be declared inside "
            "SessionState (after its class header)"
        )
        # Default MUST be ``None`` (no field-default means pydantic
        # raises on missing; the carrier must default cleanly so
        # old checkpoints deserialize unchanged).
        # Locate the field declaration and check the next line
        # carries ``= None``.
        field_idx = src.find(field_marker)
        # Find the next newline after the field declaration.
        next_newline = src.find("\n", field_idx)
        field_line = src[field_idx:next_newline]
        assert "= None" in field_line, (
            f"SessionState.pending_repair_ghost_terminal must "
            f"default to None (backward-compat); got: {field_line!r}"
        )

    def test_agent_repair_ghost_node_exhaustion_returns_pending_field(
        self, monkeypatch
    ):
        """Source-level pin: ``create_agent_repair_ghost_node``'s
        exhaustion branch returns the loud terminal via the new
        ``pending_repair_ghost_terminal`` field, NOT the
        ``messages`` channel (which is what allowed the round-0
        review miss — the old code looked correct in isolation but
        the channel-append terminal was never observed by
        should_continue).
        """
        import inspect

        from daemon.graph import create_agent_repair_ghost_node

        src = inspect.getsource(create_agent_repair_ghost_node)
        # Exhaustion branch returns the new field (cycle-kill carrier),
        # NOT the messages channel — the original channel-append
        # shape was the root cause of the round-0 review miss.
        assert (
            '"pending_repair_ghost_terminal": outcome.terminal_message'
            in src
        ), (
            "create_agent_repair_ghost_node exhaustion branch must "
            "return pending_repair_ghost_terminal (cycle-kill carrier)"
        )
        # Source MUST NOT contain the old channel-append shape for the
        # terminal_message — that was the defect.
        assert (
            '"messages": [outcome.terminal_message]' not in src
        ), (
            "create_agent_repair_ghost_node MUST NOT channel-append "
            "the terminal_message into messages — that was the C1 "
            "defect (cycle unbounded)"
        )

    def test_agent_node_response_substitution_pin(self):
        """Source-level pin: ``create_agent_node``'s agent_node reads
        ``pending_repair_ghost_terminal`` at node entry and
        substitutes the terminal as its response — mirroring the
        loop class's ``response = _durable_loop.terminal_message``
        precedent (graph.py:6751-6753). Pinned so the substitution
        path cannot silently regress (e.g., a future refactor that
        moves the check below the LLM invoke site).
        """
        import inspect

        from daemon.graph import create_agent_node

        src = inspect.getsource(create_agent_node)
        # The carrier check MUST appear in the agent_node source.
        assert (
            'state.get("pending_repair_ghost_terminal")' in src
        ), (
            "create_agent_node must read "
            "pending_repair_ghost_terminal at node entry "
            "(response-substitution site)"
        )
        # The loud-substitution WARN line MUST exist — proves the
        # operator-grep surface is preserved.
        assert (
            "[GHOST TERMINATION]" in src
        ), (
            "create_agent_node must emit the "
            "[GHOST TERMINATION] loud-substitution WARN line "
            "(operator grep surface)"
        )


class TestC1BudgetCompositionSameInvocationPin:
    """C1 fix secondary pin (developer-declared pre-merge gap):
    budget-composition same-invocation arithmetic is correct by
    construction but currently unpinned.

    Sequence under ONE ``agent_node`` invocation:
    * Loop rung fires (D-2 review fix) → folds ``+1`` into
      ``_durable_budget_current`` at ``graph.py:6654-6660``;
    * Pre-terminal intercept reads the RESET-AWARE budget at
      ``graph.py:7064`` (post-loop, so it sees the loop's
      ``+1`` — at-cap refusal cannot be bypassed);
    * ``max()`` return at ``graph.py:7371-7387`` selects the
      largest budget value (pre-terminal's recovered value, else
      loop's ``budget_used_new``, else the entry-time value) to
      carry on the node return — the durable per-task counter
      survives across the entire same-invocation chain.

    Source-level pins assert the THREE sites stay in lockstep so
    a future refactor cannot silently break the budget composition.
    """

    def test_loop_fold_at_graph_6654(self):
        """The loop rung's ``+1`` MUST be folded into
        ``_durable_budget_current`` BEFORE the pre-terminal
        intercept can read it (D-2 review fix).
        """
        from daemon.graph import (
            _maybe_durable_loop_repair as _loop_fn,
        )
        import inspect

        # Read the loop-fold site from the agent_node body directly
        # (the fold lives inside ``create_agent_node``, not
        # ``_maybe_durable_loop_repair``).
        from daemon.graph import create_agent_node

        agent_src = inspect.getsource(create_agent_node)
        # The fold site is the ``if _durable_loop.budget_used_new is
        # not None`` block that assigns to ``_durable_budget_current``
        # — pin both the conditional and the assignment shape.
        assert (
            "if _durable_loop.budget_used_new is not None:" in agent_src
        ), "loop fold conditional missing"
        assert (
            "_durable_budget_current = int(" in agent_src
        ), (
            "loop fold MUST reassign _durable_budget_current from "
            "_durable_loop.budget_used_new — at-cap refusal bypass "
            "class is closed only by this fold"
        )

    def test_intercept_read_at_graph_7064(self):
        """The pre-terminal intercept MUST read the RESET-AWARE
        ``_durable_budget_current`` (post-loop-fold), NOT the raw
        state value — so the loop rung's same-invocation ``+1`` is
        visible to the intercept and at-cap refusal cannot be
        bypassed.
        """
        import inspect

        from daemon.graph import create_agent_node

        agent_src = inspect.getsource(create_agent_node)
        # The intercept call's ``durable_budget_used`` argument must
        # be threaded from ``_durable_budget_current`` (the
        # post-loop-fold value).
        assert (
            "durable_budget_used=int(_durable_budget_current)" in agent_src
        ), (
            "pre-terminal intercept must read "
            "_durable_budget_current (post-loop-fold), not the raw "
            "state value"
        )

    def test_max_return_at_graph_7371(self):
        """The node return MUST carry the largest of the three
        budget values on the channel — pre-terminal's recovered,
        loop's ``budget_used_new``, or the entry-time value. The
        ``max()`` selection is not literally used (the if/elif/else
        picks the explicit winner); the pin asserts the explicit
        winner selection stays correct.
        """
        import inspect

        from daemon.graph import create_agent_node

        agent_src = inspect.getsource(create_agent_node)
        # Pre-terminal's recovered value MUST be the highest
        # priority (it includes both the loop's ``+1`` AND the
        # intercept's own ``+1``).
        assert (
            "_pre_terminal_outcome.budget_used" in agent_src
        ), (
            "pre-terminal recovered budget must participate in the "
            "return-value selection"
        )
        # Loop's ``budget_used_new`` is the next priority.
        assert (
            "_durable_loop.budget_used_new" in agent_src
        ), (
            "loop budget_used_new must participate in the "
            "return-value selection"
        )
        # The entry-time ``_durable_budget_current`` is the
        # fallback.
        assert (
            "_budget_return = int(_durable_budget_current)" in agent_src
        ), (
            "entry-time _durable_budget_current must be the "
            "fallback in the return-value selection"
        )
