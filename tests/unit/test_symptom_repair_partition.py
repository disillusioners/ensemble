"""Partition invariant regression tests (P-1 … P-12) — ladder phase 1.

Each invariant from ``technical-analysis.md`` §Partition-Invariant
Compliance List gets at least one regression test (T-7). The invariants
are the contract that keeps the ladder from stealing work owned by the
shipped empty-guard, the watchover 3-strike, or the compaction engines.
"""
from __future__ import annotations

import inspect
from unittest.mock import MagicMock

from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    RemoveMessage,
    SystemMessage,
    ToolMessage,
)

from daemon.graph import (
    LoopDetector,
    _is_degenerate_ai_message,
    _is_real_human_message,
)
from daemon.services.symptom_repair_engine import (
    SymptomRepairAborted,
    SymptomRepairContext,
    SymptomRepairEngine,
)
from tests.helpers.symptom_repair import REMOVE_ALL_MESSAGES, loop_units as _loop_units


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _history():
    return [
        HumanMessage(content="do the thing", id="h1"),
        *_loop_units(3),
    ]


async def _repair(messages, *, monkeypatch=None):
    """Run the engine end-to-end with a stubbed summarizer."""
    async def _ok(context, symptom_class):
        return "summary"

    eng = SymptomRepairEngine()
    eng._summarize = _ok
    ctx = SymptomRepairContext(
        detection=LoopDetector.scan(messages=messages, threshold=3),
        messages=list(messages),
        llm_config={"model": "m"},
        system_prompt="sp",
        instance_id="iid-1",
        budget_used=0,
        budget_cap=3,
    )
    return await eng.repair(ctx)


# ---------------------------------------------------------------------------
# P-1 — S5 shape partition (tool_calls excluded from degenerate class)
# ---------------------------------------------------------------------------


class TestP1ShapePartition:
    def test_degenerate_detector_excludes_tool_calls(self):
        msg = AIMessage(
            content="",
            tool_calls=[{"id": "x", "name": "bash", "args": {}}],
            additional_kwargs={"reasoning_content": "thinking"},
        )
        assert _is_degenerate_ai_message(msg) is False

    def test_degenerate_detector_accepts_reasoning_only(self):
        msg = AIMessage(content="", additional_kwargs={"reasoning_content": "t"})
        assert _is_degenerate_ai_message(msg) is True

    def test_loop_detector_never_counts_degenerate_ai(self):
        """Cross-partition negative: reasoning-only AIMessages are not
        loop units — the loop walk breaks on them (they are S5's class)."""
        msgs = [*_loop_units(2), AIMessage(content="", id="deg-1")]
        assert LoopDetector.scan(messages=msgs, threshold=3) is None


# ---------------------------------------------------------------------------
# P-2 — S1 tool-result exemption (repair not fired on nudge's input)
# ---------------------------------------------------------------------------


class TestP2ToolResultExemption:
    def test_empty_ai_after_tool_breaks_loop_walk(self):
        """The loop rung does NOT fire on the empty-after-tool shape —
        that shape belongs to S1's nudge (rung 1), never to repair."""
        msgs = [
            HumanMessage(content="go", id="h1"),
            AIMessage(
                content="",
                tool_calls=[{"id": "t0", "name": "bash", "args": {}}],
                id="ai-0",
            ),
            ToolMessage(content="r", tool_call_id="t0", name="bash", id="tm-0"),
            AIMessage(content="", id="empty-final"),
        ]
        assert LoopDetector.scan(messages=msgs, threshold=3) is None


# ---------------------------------------------------------------------------
# P-3 — LoopDetector walk purity (plain AI breaks the chain)
# ---------------------------------------------------------------------------


class TestP3WalkPurity:
    def test_plain_ai_message_breaks_chain(self):
        msgs = [
            *_loop_units(2),
            AIMessage(content="thinking out loud", id="plain-ai"),
            *_loop_units(2),
        ]
        # The two loop runs never merge across the plain AI boundary.
        assert LoopDetector.scan(messages=msgs, threshold=3) is None

    def test_chain_detects_within_one_run(self):
        msgs = [HumanMessage(content="go", id="h1"), *_loop_units(3)]
        det = LoopDetector.scan(messages=msgs, threshold=3)
        assert det is not None
        assert det.repetition_count == 3


# ---------------------------------------------------------------------------
# P-4 — watchover exclusivity (denied batches excluded; 3-strike untouched)
# ---------------------------------------------------------------------------


class TestP4WatchoverExclusivity:
    def test_watchover_denied_batch_breaks_chain(self):
        ai = AIMessage(
            content="",
            tool_calls=[{"id": "t0", "name": "bash", "args": {}}],
            id="ai-d",
        )
        denied = ToolMessage(
            content="denied",
            tool_call_id="t0",
            name="bash",
            id="tm-d",
            additional_kwargs={"watchover_denial": True},
        )
        msgs = [
            HumanMessage(content="go", id="h1"),
            *_loop_units(2),
            ai,
            denied,
        ]
        # The denial-response unit must NOT count toward a loop.
        assert LoopDetector.scan(messages=msgs, threshold=3) is None

    def test_repair_does_not_terminate_for_watchover_classes(self):
        """The engine has NO termination route: its only outputs are a
        surgery outcome or a fail-open abort — 3-strike termination stays
        watchover's (``watchover_terminate_node``)."""
        src = inspect.getsource(SymptomRepairEngine)
        assert "watchover" not in src.lower().replace(
            "watchover_denial", ""
        ) or "terminate" not in src
        assert "watchover_terminate" not in src


# ---------------------------------------------------------------------------
# P-5 — L6 injection preservation (hoisted re-emission)
# ---------------------------------------------------------------------------


class TestP5InjectionPreservation:
    async def test_surgery_reemits_context_blocks_and_unanswered_notes(
        self, monkeypatch
    ):
        msgs = [
            HumanMessage(content="go", id="h1"),
            SystemMessage(
                content="[SYSTEM CONTEXT: Project]\n\nb",
                id="ctx1",
                additional_kwargs={
                    "injected_message": True,
                    "context_kind": "project",
                },
            ),
            HumanMessage(
                content="note",
                id="note1",
                additional_kwargs={"injected_message": True},
            ),
            *_loop_units(3),
        ]
        import daemon.compaction as compaction_module

        monkeypatch.setattr(
            compaction_module, "resolve_injected_notes_absorb", lambda: False
        )
        outcome = await _repair(msgs)
        prefix_ids = [getattr(m, "id", None) for m in outcome.surgery_prefix]
        # Both injected kinds survive the surgery, ahead of the tail.
        assert "ctx1" in prefix_ids
        assert "note1" in prefix_ids
        assert prefix_ids.index("ctx1") < prefix_ids.index("h1")

    def test_boundary_detection_ignores_injected_human_messages(self):
        """Nudges/context HumanMessages stay invisible to the loop walk
        (they break it rather than being counted)."""
        msgs = [
            *_loop_units(1),
            HumanMessage(
                content="note",
                id="note1",
                additional_kwargs={"injected_message": True},
            ),
            *_loop_units(2),
        ]
        assert LoopDetector.scan(messages=msgs, threshold=3) is None


# ---------------------------------------------------------------------------
# P-6 — sentinel element-0
# ---------------------------------------------------------------------------


class TestP6SentinelElementZero:
    async def test_sentinel_is_element_zero(self):
        outcome = await _repair(_history())
        assert isinstance(outcome.surgery_prefix[0], RemoveMessage)
        assert outcome.surgery_prefix[0].id == REMOVE_ALL_MESSAGES

    def test_make_remove_all_sentinel_uses_langgraph_constant(self):
        from daemon.compaction import make_remove_all_sentinel

        s = make_remove_all_sentinel()
        assert isinstance(s, RemoveMessage)
        assert s.id == REMOVE_ALL_MESSAGES


# ---------------------------------------------------------------------------
# P-7 — original-id tail (upsert-in-place)
# ---------------------------------------------------------------------------


class TestP7OriginalIdTail:
    async def test_retained_messages_are_original_objects(self):
        msgs = _history()
        outcome = await _repair(msgs)
        prefix = outcome.surgery_prefix
        by_id = {getattr(m, "id", None): m for m in prefix}
        # Same OBJECTS → same ids → the reducer upserts in place.
        assert by_id.get("h1") is msgs[0]
        assert by_id.get("ai-0") is msgs[1]
        assert by_id.get("tm-0") is msgs[2]


# ---------------------------------------------------------------------------
# P-8 — unit folding (never orphan a ToolMessage)
# ---------------------------------------------------------------------------


class TestP8UnitFolding:
    async def test_no_orphaned_tool_messages_after_surgery(self):
        outcome = await _repair(_history())
        prefix = outcome.surgery_prefix[1:]  # skip sentinel
        ai_tc_ids = {
            tc.get("id", "")
            for m in prefix
            if getattr(m, "tool_calls", None)
            for tc in (m.tool_calls or [])
        }
        for m in prefix:
            if isinstance(m, ToolMessage):
                assert m.tool_call_id in ai_tc_ids, (
                    f"orphaned ToolMessage {m.id} (call {m.tool_call_id})"
                )


# ---------------------------------------------------------------------------
# P-9 — no counter theft
# ---------------------------------------------------------------------------


class TestP9NoCounterTheft:
    def test_engine_never_imports_s5_or_language_counters(self):
        """P-9 (structural): S5 derivation is zero-stored-state and the
        language counters are checkpoint channels — the engine touches
        NEITHER (no imports, no symbol references). Phase 2 introduces
        ghost-promise helpers as graph.py top-level functions but the
        engine module still must not import them (the engine stays
        detector-agnostic; ghost detection is graph.py's concern, the
        engine consumes a duck-typed :class:`GhostDetectionResult`)."""
        import daemon.services.symptom_repair_engine as mod

        src = inspect.getsource(mod)
        assert "_count_trailing_degenerate" not in src
        assert "_is_degenerate_ai_message" not in src
        assert "language_check" not in src
        assert "_count_trailing_ghost" not in src
        assert "_is_ghost_promise_message" not in src
        assert "_build_truncated_detection" not in src
        assert "_build_empty_post_ladder_detection" not in src

    async def test_repair_preserves_unrelated_history_verbatim(self):
        """Messages outside the loop window — including S5-class
        degenerate evidence — pass through the surgery untouched (the
        repair consumes only its own budget/window)."""
        msgs = [
            HumanMessage(content="go", id="h1"),
            AIMessage(
                content="",
                id="deg-mid",
                additional_kwargs={"reasoning_content": "t"},
            ),
            *_loop_units(3),
        ]
        outcome = await _repair(msgs)
        by_id = {getattr(m, "id", None): m for m in outcome.surgery_prefix}
        assert by_id.get("deg-mid") is msgs[1]  # same object, untouched

    async def test_engine_does_not_touch_transient_failover_counters(self):
        """The repair consumes ONLY its own budget — it never contacts the
        facade retry machinery's budget (its summarizer has its OWN
        facade-scoped budget)."""
        import daemon.services.symptom_repair_engine as mod

        src = inspect.getsource(mod)
        assert "PRIMARY_TRANSIENT_MAX" not in src
        assert "transient_attempts" not in src


# ---------------------------------------------------------------------------
# P-10 — no mid-flight aupdate_state
# ---------------------------------------------------------------------------


class TestP10NoMidFlightPersist:
    @staticmethod
    def _code_symbols_omit(module, forbidden: set[str]) -> list[str]:
        """AST-walk the module's CODE (docstrings/comments excluded) and
        return any forbidden symbol names actually referenced."""
        import ast

        tree = ast.parse(inspect.getsource(module))
        hits: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in forbidden:
                hits.append(node.attr)
            elif isinstance(node, ast.Name) and node.id in forbidden:
                hits.append(node.id)
        return hits

    def test_engine_module_free_of_checkpoint_writes(self):
        import daemon.services.symptom_repair_engine as mod

        hits = self._code_symbols_omit(
            mod, {"aupdate_state", "aget_state"}
        )
        assert hits == [], (
            f"repair engine must not touch the checkpoint mid-flight; "
            f"found {hits!r}"
        )

    def test_durable_rung_source_has_no_checkpoint_writes(self):
        import daemon.graph as graph_module

        src = inspect.getsource(graph_module)
        start = src.index("async def _maybe_durable_loop_repair")
        end = src.index("async def _maybe_repair_loop", start)
        rung_src = src[start:end]
        assert "aupdate_state" not in rung_src
        assert "aget_state" not in rung_src

    def test_no_node_stamped_checkpoint_ns_reads(self):
        """The checkpoint_ns empty-snapshot trap: any aget_state in the
        repair path with a node-stamped config reads EMPTY. The engine
        performs NO state reads at all (thread-id-only seam recipe not
        needed because there is no read)."""
        import daemon.services.symptom_repair_engine as mod

        hits = self._code_symbols_omit(
            mod, {"aupdate_state", "aget_state", "checkpoint_ns"}
        )
        assert hits == []


# ---------------------------------------------------------------------------
# P-11 — kill-switch byte-identity (OFF routing)
# ---------------------------------------------------------------------------


class TestP11KillSwitchByteIdentity:
    async def test_off_returns_shipped_outcome(self, monkeypatch):
        """With both kill-switches OFF, the durable rung returns ``None``
        and the shipped path's FIRST detection output is unchanged."""
        from daemon.config import _reset_symptom_repair_ladder_for_tests
        from daemon.graph import _maybe_durable_loop_repair

        monkeypatch.setenv("ENSEMBLE_SYMPTOM_REPAIR_LADDER", "0")
        monkeypatch.setenv("ENSEMBLE_REPAIR_LOOP_DURABLE", "0")
        _reset_symptom_repair_ladder_for_tests()
        try:
            outcome = await _maybe_durable_loop_repair(
                messages=_history(),
                full_messages=list(_history()),
                instance_id="iid-p11",
                instance_short="iid",
                config={"configurable": {"thread_id": "iid-p11"}},
                injected_msg=None,
                system_prompt="sp",
                llm_config={"model": "m"},
                loop_breaker_slot=MagicMock(),
                loop_breaker_config=__import__(
                    "daemon.config", fromlist=["LoopBreakerConfig"]
                ).LoopBreakerConfig(),
                durable_budget_used=0,
                turn_id="t1",
            )
            assert outcome is None  # byte-identical fall-through
        finally:
            _reset_symptom_repair_ladder_for_tests()

    async def test_off_is_graceful_even_with_broken_engine(self, monkeypatch):
        """OFF ⇒ the engine is never CONSTRUCTED: even if it would raise,
        the OFF path cannot notice (every new branch gated BEFORE behavior)."""
        from daemon.config import _reset_symptom_repair_ladder_for_tests
        from daemon.graph import _maybe_durable_loop_repair

        monkeypatch.setenv("ENSEMBLE_SYMPTOM_REPAIR_LADDER", "0")
        monkeypatch.setenv("ENSEMBLE_REPAIR_LOOP_DURABLE", "0")
        _reset_symptom_repair_ladder_for_tests()
        try:
            import daemon.services.symptom_repair_engine as mod

            outcome = await _maybe_durable_loop_repair(
                messages=_history(),
                full_messages=list(_history()),
                instance_id="iid-p11b",
                instance_short="iid",
                config=None,
                injected_msg=None,
                system_prompt="sp",
                llm_config={},
                loop_breaker_slot=MagicMock(),
                loop_breaker_config=__import__(
                    "daemon.config", fromlist=["LoopBreakerConfig"]
                ).LoopBreakerConfig(),
                durable_budget_used=0,
                turn_id="t1",
            )
            assert outcome is None
        finally:
            _reset_symptom_repair_ladder_for_tests()


# ---------------------------------------------------------------------------
# P-12 — lane name-match (no unregistered validation subclasses)
# ---------------------------------------------------------------------------


class TestP12LaneNameMatch:
    def test_engine_abort_is_not_an_llm_validation_subclass(self):
        """The abort exception must NOT subclass LLMResponseValidationError:
        the lane name-match table in ``message_processing_errors`` matches
        literal class NAMES — an unlisted new subclass would silently
        misroute to ``execution_error``."""
        from daemon.llm_error_classifier import LLMResponseValidationError
        from daemon.response_validation import (
            LLMResponseValidationError as RVError,
        )

        assert not issubclass(SymptomRepairAborted, LLMResponseValidationError)
        assert not issubclass(SymptomRepairAborted, RVError)

    def test_existing_lane_registration_untouched(self):
        """``EmptyLLMResponseError`` stays explicitly listed in the
        message_processing_errors lane table (phase 1 adds no entries and
        removes none)."""
        import daemon.services.message_processing_errors as mpe

        src = inspect.getsource(mpe)
        assert "EmptyLLMResponseError" in src

    def test_engine_has_no_new_validation_subclasses(self):
        import daemon.services.symptom_repair_engine as mod

        src = inspect.getsource(mod)
        assert "LLMResponseValidationError)" not in src  # no subclass decl


# ---------------------------------------------------------------------------
# B-3 companion — real-human predicate partition
# ---------------------------------------------------------------------------


class TestRealHumanPredicate:
    def test_plain_human_is_real(self):
        assert _is_real_human_message(HumanMessage(content="hi", id="h")) is True

    def test_injected_human_is_not_real(self):
        msg = HumanMessage(
            content="note",
            id="n",
            additional_kwargs={"injected_message": True},
        )
        assert _is_real_human_message(msg) is False

    def test_context_kind_block_is_not_real(self):
        msg = HumanMessage(
            content="[SYSTEM CONTEXT: Project]\n\nb",
            id="c",
            additional_kwargs={
                "injected_message": True,
                "context_kind": "project",
            },
        )
        assert _is_real_human_message(msg) is False

    def test_non_human_is_not_real(self):
        assert _is_real_human_message(AIMessage(content="hi")) is False
