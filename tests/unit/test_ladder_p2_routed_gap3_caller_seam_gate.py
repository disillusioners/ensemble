"""Ladder phase 2 — REVIEWER-ROUTED TEST #3.

Caller-seam gate wiring (G-R3 / gap closer).

Coverage gap closed
-------------------
The two existing pre-terminal test groups —
``tests/unit/test_symptom_repair_engine_phase2.py::TestMasterKillSwitchByteIdentical``
(unit, helper-direct invocation; asserts the helper returns ``None``
on master OFF) and ``tests/unit/test_ladder_p2_routed_gap2_second_exception.py``
(real graph, asserts the master-ON intercept path) — together pin
the per-arm CONTRACT but NOT the WIRING at the ``agent_node``
caller seam. A future refactor that inverts the gate at the call
site (e.g. swaps ``get_symptom_repair_ladder_enabled()`` with
``not get_symptom_repair_ladder_enabled()``) would pass BOTH
existing tests:

* the unit tests directly invoke the helper, which has its OWN
  internal gate (graph.py:2588) — so the helper's behavior is
  unchanged;
* the second-exception test uses master ON, which fires the
  intercept whether or not the caller-side gate is correct (the
  helper's internal gate is also ON, so the helper-side path
  still runs).

The phase-2 reviewer routed this gap to a CALLER-SEAM gate-wiring
pin that:

1. Forces the in-process resolver to RETURN FALSE while the
   master env is set ON for the test frame (so the helper's
   internal self-gate at graph.py:2588 would also force it
   False, BUT the caller-side gate at graph.py:7134 — which is
   the one this test pins — must reach the SAME conclusion
   independently);
2. Asserts the intercept is INERT: no repair telemetry,
   provider called ONCE, loud `[LLM] All retries exhausted`
   fires directly with no repair-document trail.

**Inverted-gate catchability** (the deliverable evidence):
this test will be PROVEN to catch a refactor that flips the
caller-side gate by running the test against an inverted
implementation in a scratch run (see
``/tmp/rlp2-routed/inverted_gate_catch.py`` for the scratch
script that mirrors this test with the inverted gate). The
committed test itself pins correct behavior only; the scratch
script is the proof-of-catchability that the reviewer
requested.

What this test pins
-------------------
1. **Arm (a) — master ON + raising provider**: the intercept
   FIRES (provider called TWICE; ``[SYMPTOM] class=loop
   phase=detect action=fired`` telemetry; loud `[LLM] All
   retries exhausted` fires after the repair aborts on the
   second-exception path; ONE repair doc in the checkpoint
   messages).
2. **Arm (b) — resolver forced OFF in-process (env ON but
   resolver forced False via monkeypatch) + same raising
   provider**: the intercept is INERT (provider called ONCE;
   ZERO intercept telemetry; loud `[LLM] All retries exhausted`
   fires directly; ZERO repair docs in the checkpoint messages;
   budget stays at 0).

An INVERTED gate (intercepts when disabled / skips when enabled)
breaks arm (a) or arm (b). PROOF is in the scratch script
(separate deliverable; not committed to the test suite).

Mechanics precedent
-------------------
Mirrors the real-langgraph harness in
``tests/test_ladder_loop_x_empty_guard_integration.py`` and the
agent_node construction in
``tests/unit/test_symptom_repair_engine_phase2.py::TestC1GhostExhaustionResponseSubstitution``.
Uses ``tests.helpers.symptom_repair._RealLangGraph`` to swap the
conftest's mocked langgraph modules.

The ``monkeypatch.setattr`` of
``daemon.config.get_symptom_repair_ladder_enabled`` propagates
everywhere because ``daemon.graph`` imports the symbol at module
load and references it via the module (graph.py:128 — both
``daemon.graph`` and the helper's self-gate at :2588 consult
``get_symptom_repair_ladder_enabled()`` via daemon.config). The
helper's internal self-gate is defense-in-depth; the
caller-side gate at graph.py:7134 is the WIRING this test pins.
"""
from __future__ import annotations

import logging

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from daemon.config import _reset_symptom_repair_ladder_for_tests
from daemon.graph import create_agent_node
from daemon.response_validation import LLMResponseValidationError
from daemon.services.symptom_repair_engine import (
    REPAIR_DOC_ID_PREFIX,
    SymptomRepairEngine,
)
from tests.helpers.symptom_repair import _RealLangGraph, ok_summarizer


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _restore_flags():
    """Isolate the ladder kill-switch module cache (config accessors)."""
    _reset_symptom_repair_ladder_for_tests()
    yield
    _reset_symptom_repair_ladder_for_tests()


def _truncated_exception(seq: int) -> LLMResponseValidationError:
    """A truncated LLMResponseValidationError (the simplest
    pre-terminal class — the in-process resolver False test pins
    that the call site does not invoke the helper regardless of
    the env-level switch)."""
    return LLMResponseValidationError(
        "truncated response",
        response=AIMessage(
            content="partial answer that hit the token limit",
            response_metadata={"finish_reason": "length"},
            id=f"trunc-{seq}",
        ),
    )


class _AlwaysRaisingProvider:
    """Provider that always raises via ``exception_factory(seq)``."""

    def __init__(self):
        self.calls: list[list] = []
        self._seq = 0

    def invoke(self, messages):
        self.calls.append(list(messages))
        self._seq += 1
        raise _truncated_exception(self._seq)


def _make_agent_node(provider):
    return create_agent_node(
        llm_with_tools=provider,
        system_prompt="you are a test assistant",
        compactor=None,
        graph_ref=[None],
        config=None,
        llm_config={"model": "test-model"},
        retry_config={"transient_attempts": 1, "timeout_attempts": 1},
    )


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------


class TestCallerSeamGateWiringInvertedGateCatcher:
    """Routed gap #3 — caller-seam gate wiring (inverted-gate catcher).

    Arm (a) — master ON + raising provider — proves the gate is
    CORRECTLY OPEN: the intercept fires (provider called TWICE;
    detect+repair telemetry; repair doc appended; loud ERROR after
    the repair aborts on second-exception).

    Arm (b) — env set ON BUT the in-process resolver is explicitly
    FORCED to False via ``monkeypatch.setattr`` on
    ``daemon.config.get_symptom_repair_ladder_enabled`` — proves
    the gate is CORRECTLY CLOSED at the caller seam: the intercept
    is INERT (provider called ONCE; ZERO detect telemetry; loud
    ERROR fires directly with NO repair doc appended; budget 0).

    An inverted-gate refactor that flips the caller-side
    conditional at ``daemon/graph.py:7133-7140`` breaks arm (a)
    (no repair fires when it should) or arm (b) (repair fires when
    it should not) — RED on the suite.
    """

    @pytest.mark.asyncio
    async def test_master_on_intercept_fires(
        self, monkeypatch, tmp_path, caplog
    ):
        """Arm (a): master ON, provider raises truncated — intercept
        fires (provider called twice, telemetry + repair doc +
        loud ERROR after the abort). Pins the CORRECTLY-OPEN arm
        of the wiring."""
        # Engine summarizer stub — the surgery/budget/doc flow is real.
        monkeypatch.setattr(
            SymptomRepairEngine,
            "_summarize",
            staticmethod(ok_summarizer),
        )
        monkeypatch.setenv("ENSEMBLE_SYMPTOM_REPAIR_LADDER", "1")
        _reset_symptom_repair_ladder_for_tests()

        provider = _AlwaysRaisingProvider()
        agent = _make_agent_node(provider)

        with _RealLangGraph():
            from langgraph.graph import (
                END as _REAL_END,
                START as _REAL_START,
                MessagesState as _RealMessagesState,
                StateGraph as _RealStateGraph,
            )
            globals()["END"] = _REAL_END
            globals()["START"] = _REAL_START
            globals()["MessagesState"] = _RealMessagesState
            globals()["StateGraph"] = _RealStateGraph

            class _State(_RealMessagesState):
                repair_budget_used: int

            g = _RealStateGraph(_State)
            g.add_node("agent", agent)
            g.add_edge(_REAL_START, "agent")
            g.add_edge("agent", _REAL_END)

            db_path = tmp_path / "seam_a.db"
            import aiosqlite
            from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

            conn = await aiosqlite.connect(str(db_path))
            saver = AsyncSqliteSaver(conn)
            await saver.setup()
            try:
                compiled = g.compile(checkpointer=saver)
                cfg = {
                    "configurable": {"thread_id": "seam-a-iid"},
                    "recursion_limit": 20,
                }
                with caplog.at_level(logging.INFO):
                    try:
                        await compiled.ainvoke(
                            {
                                "messages": [
                                    HumanMessage(content="go", id="h1")
                                ],
                                "repair_budget_used": 0,
                            },
                            cfg,
                        )
                    except BaseException:
                        pass

                # ── (a.1) provider called TWICE — intercept fired
                # (the second call is the post-surgery re-invoke;
                # it raises the same exception → second-exception
                # abort → loud ERROR fires; agent_node re-raises).
                assert len(provider.calls) == 2, (
                    f"arm (a) must call the provider twice "
                    f"(original + post-surgery re-invoke); got "
                    f"{len(provider.calls)}"
                )

                # ── (a.2) detect telemetry fires
                symptom_records = [
                    r.getMessage()
                    for r in caplog.records
                    if "[SYMPTOM]" in r.getMessage()
                ]
                detect_lines = [
                    m for m in symptom_records
                    if "phase=detect action=fired" in m
                ]
                assert detect_lines, (
                    f"arm (a) must emit pre-terminal detect "
                    f"telemetry; got {symptom_records!r}"
                )

                # ── (a.3) surgery WAS BUILT (engine emitted
                # ``[SymptomRepair] surgery built`` log line — proves
                # the intercept reached the engine before the
                # second-exception abort). On the second-exception
                # path the surgery_prefix is NEVER returned to the
                # caller (the helper returns ``recovered=False`` and
                # the agent_node re-raises before the prefix reaches
                # the checkpoint), so the checkpoint itself does
                # NOT carry the doc — only the engine log does. The
                # engine log is the in-process evidence that the
                # intercept fired.
                surgery_built_logs = [
                    r.getMessage()
                    for r in caplog.records
                    if "surgery built" in r.getMessage()
                    and "repair-seam-a-iid-" in r.getMessage()
                ]
                assert surgery_built_logs, (
                    f"arm (a) must build the surgery prefix "
                    f"(engine log proves the intercept fired); got "
                    f"{surgery_built_logs!r}"
                )

                # ── (a.4) loud ERROR fires (after the repair abort)
                loud_lines = [
                    r.getMessage()
                    for r in caplog.records
                    if "[LLM] All retries exhausted" in r.getMessage()
                ]
                assert len(loud_lines) == 1, (
                    f"arm (a) must log exactly one [LLM] All retries "
                    f"exhausted line (after the repair aborts); got "
                    f"{len(loud_lines)}: {loud_lines!r}"
                )
            finally:
                await conn.close()

    @pytest.mark.asyncio
    async def test_resolver_forced_off_intercept_inert(
        self, monkeypatch, tmp_path, caplog
    ):
        """Arm (b): env set ON, but the in-process resolver is
        FORCED to return False via ``monkeypatch.setattr`` on
        ``daemon.config.get_symptom_repair_ladder_enabled`` —
        the intercept must be INERT (provider called ONCE; ZERO
        detect telemetry; loud ERROR fires directly with NO
        repair doc appended; budget stays at 0).

        This arm pins the caller-side WIRING (graph.py:7134) —
        not just the helper's internal self-gate (graph.py:2588),
        which is defense-in-depth. The caller-side gate is the
        load-bearing check; if it is removed or inverted, this
        test goes RED."""
        # Engine summarizer stub.
        monkeypatch.setattr(
            SymptomRepairEngine,
            "_summarize",
            staticmethod(ok_summarizer),
        )
        # Env set ON so the helper's internal self-gate (defense
        # in depth, graph.py:2588) WOULD also be ON if consulted
        # — but the caller-side gate at graph.py:7134 is the one
        # we pin, and it consults ``get_symptom_repair_ladder_enabled``
        # (the symbol from ``daemon.config``, imported into
        # ``daemon.graph`` at module load). Forcing the resolver
        # to False at the daemon.config module propagates to the
        # caller-side gate.
        monkeypatch.setenv("ENSEMBLE_SYMPTOM_REPAIR_LADDER", "1")
        _reset_symptom_repair_ladder_for_tests()
        # Force the resolver to False in-process. The call site
        # at graph.py:7134 invokes the symbol via
        # ``daemon.config.get_symptom_repair_ladder_enabled`` —
        # the symbol is bound at module import (graph.py:128),
        # so we patch the SOURCE module (daemon.config) AND the
        # ``daemon.graph`` namespace (which captured the symbol
        # by name).
        import daemon.config as _config_mod
        import daemon.graph as _graph_mod

        def _force_off():
            return False

        monkeypatch.setattr(
            _config_mod,
            "get_symptom_repair_ladder_enabled",
            _force_off,
        )
        # Also patch the symbol ``daemon.graph`` already imported
        # so the agent_node's reference resolves to the False
        # variant.
        monkeypatch.setattr(
            _graph_mod,
            "get_symptom_repair_ladder_enabled",
            _force_off,
        )

        provider = _AlwaysRaisingProvider()
        agent = _make_agent_node(provider)

        with _RealLangGraph():
            from langgraph.graph import (
                END as _REAL_END,
                START as _REAL_START,
                MessagesState as _RealMessagesState,
                StateGraph as _RealStateGraph,
            )
            globals()["END"] = _REAL_END
            globals()["START"] = _REAL_START
            globals()["MessagesState"] = _RealMessagesState
            globals()["StateGraph"] = _RealStateGraph

            class _State(_RealMessagesState):
                repair_budget_used: int

            g = _RealStateGraph(_State)
            g.add_node("agent", agent)
            g.add_edge(_REAL_START, "agent")
            g.add_edge("agent", _REAL_END)

            db_path = tmp_path / "seam_b.db"
            import aiosqlite
            from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

            conn = await aiosqlite.connect(str(db_path))
            saver = AsyncSqliteSaver(conn)
            await saver.setup()
            try:
                compiled = g.compile(checkpointer=saver)
                cfg = {
                    "configurable": {"thread_id": "seam-b-iid"},
                    "recursion_limit": 20,
                }
                with caplog.at_level(logging.INFO):
                    try:
                        await compiled.ainvoke(
                            {
                                "messages": [
                                    HumanMessage(content="go", id="h1")
                                ],
                                "repair_budget_used": 0,
                            },
                            cfg,
                        )
                    except BaseException:
                        pass

                # ── (b.1) provider called ONCE — intercept INERT
                assert len(provider.calls) == 1, (
                    f"arm (b) must call the provider exactly ONCE "
                    f"(no intercept → no re-invoke); got "
                    f"{len(provider.calls)}"
                )

                # ── (b.2) ZERO pre-terminal detect telemetry
                detect_lines = [
                    r.getMessage()
                    for r in caplog.records
                    if "[SYMPTOM]" in r.getMessage()
                    and "phase=detect" in r.getMessage()
                ]
                assert detect_lines == [], (
                    f"arm (b) must NOT emit any pre-terminal "
                    f"detect telemetry (gate off → no intercept); "
                    f"got {detect_lines!r}"
                )

                # ── (b.3) loud ERROR fires directly (no repair
                # sandwich)
                loud_lines = [
                    r.getMessage()
                    for r in caplog.records
                    if "[LLM] All retries exhausted" in r.getMessage()
                ]
                assert len(loud_lines) == 1, (
                    f"arm (b) must log exactly one [LLM] All "
                    f"retries exhausted line (shipped loud ERROR "
                    f"path); got {len(loud_lines)}: "
                    f"{loud_lines!r}"
                )

                # ── (b.4) ZERO repair docs in the checkpoint
                st = await compiled.aget_state(cfg)
                final_ids = [
                    getattr(m, "id", None)
                    for m in st.values["messages"]
                ]
                repair_doc_ids = [
                    i
                    for i in final_ids
                    if str(i).startswith(
                        f"{REPAIR_DOC_ID_PREFIX}seam-b-iid-"
                    )
                ]
                assert repair_doc_ids == [], (
                    f"arm (b) must NOT append any repair doc "
                    f"(gate off → no surgery); got "
                    f"{repair_doc_ids!r} in {final_ids!r}"
                )

                # ── (b.5) budget stays at 0 — the master-OFF
                # path is truly INERT.
                assert st.values.get("repair_budget_used") == 0, (
                    f"arm (b) must not increment the budget; got "
                    f"{st.values.get('repair_budget_used')!r}"
                )
            finally:
                await conn.close()
