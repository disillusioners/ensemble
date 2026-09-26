"""SymptomRepairEngine unit tests — hallucination-recovery ladder phase 1.

Covers workstreams A (carrier: A-1..A-6), B-4 (durable budget consult),
C (facade summarizer + fail-open abort + persist refusal + timeout),
and the T-5 / T-12 test-critical invariants.

Harness notes:
* The summarizer leg is stubbed at ``SymptomRepairEngine._summarize``
  (or its dependencies are monkeypatched at their lazy-import sources:
  ``daemon.graph.ThinkingChatOpenAI`` and
  ``daemon.services.llm_failover.wrap_langchain_failover``) — no network.
* Fixtures reuse the ``LoopDetector`` scan to build realistic loop
  detections (same detector the shipped path uses — detection semantics
  are NOT this phase's change surface).
"""
from __future__ import annotations

import inspect
from typing import Any

import pytest
from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    RemoveMessage,
    SystemMessage,
    ToolMessage,
)

from daemon.config import _reset_symptom_repair_ladder_for_tests
from daemon.graph import LoopDetector, LoopRepairer
from daemon.services.symptom_repair_engine import (
    REPAIR_DOC_ID_PREFIX,
    SYMPTOM_REPAIR_BUDGET,
    SymptomRepairAborted,
    SymptomRepairContext,
    SymptomRepairEngine,
)
from tests.helpers.symptom_repair import (
    REMOVE_ALL_MESSAGES,
    loop_units as _loop_units,
    ok_summarizer as _ok_summarizer,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _history():
    """Realistic loop history: user ask + context block + 3-loop tail."""
    return [
        HumanMessage(content="do the thing", id="h1"),
        SystemMessage(
            content="[SYSTEM CONTEXT: Project]\n\nproject body",
            id="ctx1",
            additional_kwargs={
                "injected_message": True,
                "context_kind": "project",
            },
        ),
        *_loop_units(3),
    ]


def _detect(messages):
    det = LoopDetector.scan(messages=messages, threshold=3)
    assert det is not None, "fixture must produce a loop detection"
    return det


def _context(messages, *, budget_used: int = 0, instance_id: str = "iid-1"):
    return SymptomRepairContext(
        detection=_detect(messages),
        messages=list(messages),
        llm_config={"model": "test-model", "model_vision": None},
        system_prompt="you are a test assistant",
        instance_id=instance_id,
        budget_used=budget_used,
        budget_cap=SYMPTOM_REPAIR_BUDGET,
    )


@pytest.fixture(autouse=True)
def _restore_flags():
    """Isolate the ladder kill-switch module cache (config accessors)."""
    _reset_symptom_repair_ladder_for_tests()
    yield
    _reset_symptom_repair_ladder_for_tests()


# ---------------------------------------------------------------------------
# A-1 — engine skeleton + preset table
# ---------------------------------------------------------------------------


class TestEngineSkeleton:
    def test_instantiation(self):
        assert SymptomRepairEngine() is not None

    def test_loop_preset_loaded_by_symbol_name(self):
        eng = SymptomRepairEngine()
        assert "loop" in eng.PRESETS
        preset = eng.preset_for("loop")
        # The four ADR-0001 preset axes are present.
        assert "evidence_window_selector" in preset
        assert "summary_prompt" in preset
        assert "retention" in preset
        assert preset["post_repair_routing"] == "continue"

    def test_unknown_class_raises_loud(self):
        with pytest.raises(ValueError, match="symptom class"):
            SymptomRepairEngine().preset_for("not_a_real_class_xyz")

    def test_no_phase2_surfaces_preimplemented(self):
        # Phase-1 enrollment is the LOOP class ONLY (ADR-0002); phase-2
        # adds ghost / truncated / empty_post_ladder (USER AMENDMENT
        # 2026-09-13). After phase-2 enrollment, all four classes are
        # present and the gate-style "no fake classes are preimplemented"
        # invariant is checked by ``test_unknown_class_raises_loud``
        # above (``not_a_real_class_xyz`` is rejected loud).
        assert sorted(SymptomRepairEngine.PRESETS) == sorted(
            ["loop", "ghost", "truncated", "empty_post_ladder"]
        )


# ---------------------------------------------------------------------------
# A-2 — removal-builder reroute: surgery equivalence with the shipped ids
# ---------------------------------------------------------------------------


class TestRemovalBuilderReroute:
    async def test_surgery_removal_set_matches_shipped_builder(self):
        msgs = _history()
        ctx = _context(msgs)
        shipped_ids = {
            r.id for r in LoopRepairer._build_removal_list(ctx.detection) if r.id
        }
        eng = SymptomRepairEngine()
        eng._summarize = _ok_summarizer
        outcome = await eng.repair(ctx)
        assert outcome.success
        prefix_ids = {
            getattr(m, "id", None) for m in outcome.surgery_prefix[1:]
        } - {None}
        snap_ids = {getattr(m, "id", None) for m in msgs} - {None}
        # Same set of removed message ids as the shipped removal builder.
        assert snap_ids - prefix_ids == shipped_ids


# ---------------------------------------------------------------------------
# A-3 / A-4 / A-5 / A-6 — surgery shape
# ---------------------------------------------------------------------------


class TestSurgeryShape:
    async def test_sentinel_is_element_zero(self):
        eng = SymptomRepairEngine()
        eng._summarize = _ok_summarizer
        outcome = await eng.repair(_context(_history()))
        prefix = outcome.surgery_prefix
        assert isinstance(prefix[0], RemoveMessage)
        assert prefix[0].id == REMOVE_ALL_MESSAGES

    async def test_retained_tail_keeps_original_ids_and_objects(self):
        msgs = _history()
        eng = SymptomRepairEngine()
        eng._summarize = _ok_summarizer
        outcome = await eng.repair(_context(msgs))
        prefix = outcome.surgery_prefix
        # Evidence unit (oldest loop occurrence) retained with ORIGINAL id
        # and the ORIGINAL message OBJECT (upsert-in-place, not a copy).
        prefix_ids = [getattr(m, "id", None) for m in prefix]
        assert "ai-0" in prefix_ids
        assert "tm-0" in prefix_ids
        evidence_ai = next(m for m in prefix if getattr(m, "id", None) == "ai-0")
        assert evidence_ai is msgs[2]  # same object → original id preserved

    async def test_repair_doc_id_namespace(self):
        eng = SymptomRepairEngine()
        eng._summarize = _ok_summarizer
        outcome = await eng.repair(
            _context(_history(), instance_id="inst-abc")
        )
        ids = [getattr(m, "id", None) for m in outcome.surgery_prefix]
        assert "repair-inst-abc-1" in ids
        # Distinct from the compaction namespace.
        assert not any(
            str(i).startswith("compaction-global-") for i in ids if i
        )

    def test_doc_seq_increments_with_prior_repair_docs(self):
        msgs = _history() + [
            SystemMessage(
                content="[SYMPTOM REPAIR — loop]\n\nprior repair",
                id=f"{REPAIR_DOC_ID_PREFIX}inst-abc-1",
                additional_kwargs={
                    "injected_message": True,
                    "context_kind": "symptom_repair",
                },
            ),
            SystemMessage(
                content="[SYMPTOM REPAIR — loop]\n\nprior repair 2",
                id=f"{REPAIR_DOC_ID_PREFIX}inst-abc-3",
                additional_kwargs={
                    "injected_message": True,
                    "context_kind": "symptom_repair",
                },
            ),
        ]
        eng = SymptomRepairEngine()
        assert eng._next_doc_seq(msgs, "inst-abc") == 4
        assert eng._next_doc_seq(msgs, "other-inst") == 1

    async def test_removed_units_take_their_tool_messages(self):
        eng = SymptomRepairEngine()
        eng._summarize = _ok_summarizer
        outcome = await eng.repair(_context(_history()))
        prefix_ids = {getattr(m, "id", None) for m in outcome.surgery_prefix}
        # Every removed AIMessage's ToolMessages are gone too (unit folding).
        for removed_ai in ("ai-1", "ai-2"):
            assert removed_ai not in prefix_ids
            assert removed_ai.replace("ai-", "tm-") not in prefix_ids

    async def test_orphan_sweep_folds_stray_tool_messages(self):
        """P-8 defense-in-depth: a ToolMessage paired with a REMOVED
        AIMessage but NOT included by the detector's unit collection is
        folded out by the engine's sweep (never orphaned)."""
        msgs = _history()
        det = _detect(msgs)
        # Simulate detector divergence: drop the last ToolMessage from the
        # detection's loop_messages (the sweep must still remove it).
        det.loop_messages = [
            m for m in det.loop_messages if getattr(m, "id", None) != "tm-2"
        ]
        assert any(getattr(m, "id", None) == "ai-2" for m in det.loop_messages)

        eng = SymptomRepairEngine()
        eng._summarize = _ok_summarizer
        ctx = SymptomRepairContext(
            detection=det,
            messages=list(msgs),
            llm_config={"model": "m"},
            system_prompt="sp",
            instance_id="iid-1",
            budget_used=0,
            budget_cap=SYMPTOM_REPAIR_BUDGET,
        )
        outcome = await eng.repair(ctx)
        prefix_ids = {getattr(m, "id", None) for m in outcome.surgery_prefix}
        assert "ai-2" not in prefix_ids
        assert "tm-2" not in prefix_ids  # swept — not orphaned

    async def test_hoisted_context_reemitted_at_head(self, monkeypatch):
        msgs = _history()
        # An unanswered bare-flag injected note rides mid-history. With the
        # absorb kill-switch OFF, bare-flag notes are permanently preserved
        # + hoisted (documented legacy mode) — the surgery must mirror the
        # seam's hoist decision via the SAME predicates.
        msgs.insert(
            2,
            HumanMessage(
                content="operator note",
                id="note1",
                additional_kwargs={"injected_message": True},
            ),
        )
        import daemon.compaction as compaction_module
        # PR1: the absorb-contract gate moved to ``daemon._content_hardening``
        # (the canonical home of the partition/hoist predicates). Patch the
        # resolver on BOTH the old compaction_module (kept around as a
        # back-compat alias for tests / future imports) AND the new home so
        # the kill-switch OFF path is exercised regardless of which module
        # holds the consulted binding. Idempotent — both names point at the
        # same underlying ``daemon.config.resolve_injected_notes_absorb`` in
        # production; this is the correct test seam post-extraction.
        import daemon._content_hardening as content_hardening_module

        monkeypatch.setattr(
            content_hardening_module, "resolve_injected_notes_absorb", lambda: False
        )
        monkeypatch.setattr(
            compaction_module, "resolve_injected_notes_absorb", lambda: False
        )
        eng = SymptomRepairEngine()
        eng._summarize = _ok_summarizer
        outcome = await eng.repair(_context(msgs))
        head_ids = [
            getattr(m, "id", None) for m in outcome.surgery_prefix[1:4]
        ]
        # Both the context_kind block AND the bare-flag note are hoisted
        # into the head positions (before the doc / tail).
        assert "ctx1" in head_ids
        assert "note1" in head_ids

    async def test_repaired_messages_llm_bound_list_excludes_sentinel(self):
        eng = SymptomRepairEngine()
        eng._summarize = _ok_summarizer
        outcome = await eng.repair(_context(_history()))
        assert all(
            not isinstance(m, RemoveMessage) for m in outcome.repaired_messages
        )
        assert len(outcome.repaired_messages) == len(outcome.surgery_prefix) - 1


# ---------------------------------------------------------------------------
# B-4 — durable budget consult
# ---------------------------------------------------------------------------


class TestBudgetGate:
    async def test_budget_at_cap_refuses_without_surgery(self):
        eng = SymptomRepairEngine()
        eng._summarize = _ok_summarizer
        outcome = await eng.repair(
            _context(_history(), budget_used=SYMPTOM_REPAIR_BUDGET)
        )
        assert outcome.success is False
        assert outcome.aborted is True
        assert outcome.abort_reason == "budget-exhausted"
        assert outcome.surgery_prefix is None
        assert outcome.budget_consumed is False

    async def test_budget_below_cap_attempted(self):
        eng = SymptomRepairEngine()
        eng._summarize = _ok_summarizer
        outcome = await eng.repair(
            _context(_history(), budget_used=SYMPTOM_REPAIR_BUDGET - 1)
        )
        assert outcome.success is True
        assert outcome.budget_consumed is True

    def test_budget_constant_mirrors_max_repairs_semantics(self):
        assert SYMPTOM_REPAIR_BUDGET == 3


# ---------------------------------------------------------------------------
# C-2 / T-5 — summarizer degeneracy → fail-open abort
# ---------------------------------------------------------------------------


class TestSummarizerFailOpenAbort:
    async def test_summarizer_exception_aborts_fail_open(self):
        msgs = _history()

        async def boom(context, symptom_class):
            raise RuntimeError("facade exhausted after retries + failover")

        eng = SymptomRepairEngine()
        eng._summarize = boom
        outcome = await eng.repair(_context(msgs))
        assert outcome.success is False
        assert outcome.aborted is True
        assert outcome.abort_reason == "summarizer-failed"
        assert outcome.surgery_prefix is None
        assert outcome.budget_consumed is False
        # Original messages returned — no surgery happened.
        assert outcome.repaired_messages == list(msgs)

    async def test_empty_summary_text_aborts(self, monkeypatch):
        """Degenerate (whitespace-only) summarizer output → fail-open
        abort. Stubbed one level DEEPER (the facade wrapper's invoke) so
        the REAL ``_summarize`` degeneracy check runs."""
        import daemon.graph as graph_module
        import daemon.services.llm_failover as failover_module

        monkeypatch.setattr(
            failover_module,
            "wrap_langchain_failover",
            lambda client, cfg, **kw: _StubWrapper(client, "   "),
        )
        monkeypatch.setattr(graph_module, "ThinkingChatOpenAI", _StubClient)

        eng = SymptomRepairEngine()
        outcome = await eng.repair(_context(_history()))
        assert outcome.aborted is True
        assert outcome.abort_reason == "summarizer-failed"
        assert outcome.budget_consumed is False

    def test_never_static_fallback_in_engine_source(self):
        """T-5: the shipped static truncation fallback shape must NEVER be
        producible by the engine (ADR-0006 — removal chosen over
        last-resort-with-telemetry; the abort path already falls through to
        the shipped backstops)."""
        src = inspect.getsource(SymptomRepairEngine)
        assert "without progress." not in src


# ---------------------------------------------------------------------------
# C-1 — facade routing (wrap_langchain_failover)
# ---------------------------------------------------------------------------


class _StubWrapper:
    def __init__(self, client, content):
        self.client = client
        self.content = content

    def invoke(self, messages):
        return AIMessage(content=self.content)


class _StubClient:
    # Class attrs touched by ``clean_llm_config`` (gzip / streaming knobs).
    default_streaming = False
    default_request_timeout = 610
    default_request_gzip = False

    def __init__(self, **kwargs):
        self.kwargs = kwargs


class TestFacadeRouting:
    async def test_summarizer_built_via_wrap_langchain_failover(
        self, monkeypatch
    ):
        """C-1: the summarizer client must be wrapped by the HA facade —
        pinned functionally (the wrapper instance receives the invoke)
        AND source-level (the engine's source references
        ``wrap_langchain_failover`` — the facade-forwarding seam style)."""
        import daemon.graph as graph_module
        import daemon.services.llm_failover as failover_module

        seen: dict[str, Any] = {}

        def fake_wrap(client, llm_config, **kwargs):
            seen["client"] = client
            seen["config"] = llm_config
            seen["kwargs"] = kwargs
            return _StubWrapper(client, "a real summary")

        monkeypatch.setattr(
            failover_module, "wrap_langchain_failover", fake_wrap
        )
        monkeypatch.setattr(graph_module, "ThinkingChatOpenAI", _StubClient)

        eng = SymptomRepairEngine()
        summary = await eng._summarize(_context(_history()), "loop")
        assert summary == "a real summary"
        assert isinstance(seen["client"], _StubClient)
        # The facade reads base_url_backup from the RAW config dict.
        assert seen["config"].get("model") == "test-model"

    def test_facade_wall_clock_cap_inherited_not_overridden(self):
        """C-4: the engine must NOT pass ``wall_clock_cap_s`` — the
        facade's canonical default is inherited."""
        src = inspect.getsource(SymptomRepairEngine)
        assert "wrap_langchain_failover" in src
        assert "wall_clock_cap_s=" not in src

    def test_site_timeout_defaults_to_config_120s(self):
        """C-4: the 120s summarizer timeout (config.py
        LoopBreakerConfig.summarization_timeout_seconds) flows through as
        the site-level wait_for cap — config unchanged."""
        from daemon.config import LoopBreakerConfig

        assert LoopBreakerConfig().summarization_timeout_seconds == 120


# ---------------------------------------------------------------------------
# C-3 / T-12 — persist-refusal abort
# ---------------------------------------------------------------------------


class TestPersistRefusal:
    def test_guard_refusal_aborts_with_persist_refused_reason(self):
        tampered = [m for m in _history() if getattr(m, "id", None) != "h1"]
        # The retained human message is missing from the replacement and
        # not in the removal set → silent-loss hazard → refuse.
        removals = {"ai-1", "ai-2", "tm-1", "tm-2"}
        doc = SystemMessage(content="doc", id="repair-x-1")
        with pytest.raises(SymptomRepairAborted, match="persist-refused"):
            SymptomRepairEngine._prewrite_guard(
                tampered,
                [RemoveMessage(id=REMOVE_ALL_MESSAGES), doc],
                removals,
            )

    async def test_guard_passes_on_valid_surgery(self):
        msgs = _history()
        eng = SymptomRepairEngine()
        eng._summarize = _ok_summarizer
        outcome = await eng.repair(_context(msgs))
        # Re-running the guard on the produced surgery must pass (the
        # repair path itself already ran it).
        eng._prewrite_guard(msgs, outcome.surgery_prefix, {"ai-1", "ai-2", "tm-1", "tm-2"})


# ---------------------------------------------------------------------------
# P-10 support — no checkpoint writes in the engine
# ---------------------------------------------------------------------------


class TestNoCheckpointWrites:
    def test_engine_source_has_no_aupdate_state(self):
        src = inspect.getsource(SymptomRepairEngine)
        assert "aupdate_state" not in src
        assert "aget_state" not in src
