"""Repair-doc invariant tests (T-11 / G-3) — ladder phase 1.

The repair doc is INJECTED CONTEXT and must obey house rules
(technical-analysis.md DQ2-e):

1. Construction-time STABLE ``id=`` (the message-id invariant — the
   read-side ``serialize_message`` fallback cannot heal checkpointed
   messages), in the ``repair-{instance_id}-{seq}`` namespace.
2. Non-selectable / hoisted partition treatment: the doc carries
   ``injected_message=True`` + ``context_kind`` so the compaction
   three-bucket partition treats it as permanently hoisted context
   (every later compaction re-emits it verbatim).
3. Verbatim-excerpt pinning: tool name / args JSON / repetition count
   appear VERBATIM; the LLM summary is present but clearly labeled.
"""
from __future__ import annotations

import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from daemon.compaction import (
    _has_context_kind,
    _injected_note_absorbed_ids,
    _is_hoisted_injected,
    _next_compaction_seq,
)
from daemon.services.context_messages import CONTEXT_KIND_SYMPTOM_REPAIR
from daemon.services.symptom_repair_engine import (
    REPAIR_DOC_ID_PREFIX,
    SymptomRepairContext,
    SymptomRepairEngine,
)


def _loop_units(count: int):
    out = []
    for i in range(count):
        tc_id = f"tc-{i}"
        out.append(
            AIMessage(
                content="",
                tool_calls=[{"id": tc_id, "name": "bash", "args": {"cmd": "ls -la"}}],
                id=f"ai-{i}",
            )
        )
        out.append(
            ToolMessage(
                content=f"res-{i}", tool_call_id=tc_id, name="bash", id=f"tm-{i}"
            )
        )
    return out


def _build_doc():
    msgs = [HumanMessage(content="go", id="h1"), *_loop_units(3)]
    ctx = SymptomRepairContext(
        detection=__import__("daemon.graph", fromlist=["LoopDetector"]).LoopDetector.scan(
            messages=msgs, threshold=3
        ),
        messages=msgs,
        llm_config={"model": "m"},
        system_prompt="sp",
        instance_id="inst-77",
    )
    eng = SymptomRepairEngine()
    doc = eng._build_repair_doc(ctx, "LLM summary of the loop.", "loop")
    return doc, ctx, msgs


class TestConstructionTimeId:
    def test_doc_carries_construction_time_id(self):
        doc, _, _ = _build_doc()
        assert doc.id
        assert isinstance(doc.id, str)

    def test_id_follows_repair_namespace(self):
        doc, ctx, _ = _build_doc()
        assert doc.id == f"{REPAIR_DOC_ID_PREFIX}{ctx.instance_id}-1"

    def test_id_is_instance_scoped_and_monotonic(self):
        doc, ctx, msgs = _build_doc()
        # A second doc in the same channel parses the prior seq and bumps.
        eng = SymptomRepairEngine()
        nxt = eng._next_doc_seq([*msgs, doc], ctx.instance_id)
        assert nxt == 2

    def test_id_distinct_from_compaction_namespace(self):
        doc, ctx, msgs = _build_doc()
        assert not doc.id.startswith("compaction-global-")
        # The compaction seq parser ignores repair-doc ids entirely —
        # the two namespaces never collide on the reducer's id axis.
        assert _next_compaction_seq(msgs, ctx.instance_id) == 1


class TestPartitionTreatment:
    def test_doc_stamped_injected_plus_context_kind(self):
        doc, _, _ = _build_doc()
        kwargs = doc.additional_kwargs
        assert kwargs.get("injected_message") is True
        assert kwargs.get("context_kind") == CONTEXT_KIND_SYMPTOM_REPAIR
        assert _has_context_kind(doc)

    def test_doc_is_hoisted_by_the_compaction_partition(self):
        doc, _, msgs = _build_doc()
        channel = [*msgs, doc]
        absorbed = _injected_note_absorbed_ids(channel)
        assert _is_hoisted_injected(doc, absorbed) is True

    def test_doc_never_absorbed_as_answered_note(self):
        """Even with an AIMessage AFTER the doc, the context_kind stamp
        keeps the doc out of the absorbed (selectable) pool — it is
        permanently non-selectable, unlike bare-flag notes."""
        doc, _, msgs = _build_doc()
        channel = [*msgs, doc, AIMessage(content="answer", id="ai-after")]
        absorbed = _injected_note_absorbed_ids(channel)
        assert doc.id not in absorbed

    def test_doc_not_confused_with_answered_bare_notes(self):
        doc, _, _ = _build_doc()
        assert doc.additional_kwargs.get("context_kind") is not None


class TestVerbatimExcerptPinning:
    def test_doc_contains_verbatim_tool_name(self):
        doc, _, _ = _build_doc()
        assert "bash" in doc.content

    def test_doc_contains_verbatim_args_json(self):
        doc, _, _ = _build_doc()
        assert json.dumps({"cmd": "ls -la"}, sort_keys=True) in doc.content

    def test_doc_contains_repetition_count(self):
        doc, ctx, _ = _build_doc()
        assert "2" in doc.content  # 3 repetitions − evidence unit = 2 removed

    def test_llm_summary_labeled_as_generated(self):
        doc, _, _ = _build_doc()
        assert "LLM-generated" in doc.content
        assert "verify against" in doc.content

    def test_doc_header_names_the_symptom_class(self):
        doc, _, _ = _build_doc()
        assert "[SYMPTOM REPAIR — loop]" in doc.content
