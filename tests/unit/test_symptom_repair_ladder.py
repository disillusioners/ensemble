"""Ladder wiring tests — hallucination-recovery ladder phase 1.

Covers:
* F-1/F-2: kill-switch resolvers + install + cold accessors (boot probe
  exercised via load_config end-to-end).
* B-2/B-3 (OQ5 PINNED): durable budget increment on successful repair
  only; reset to 0 on a new REAL (non-injected) HumanMessage; injected
  HumanMessages do NOT reset.
* B-4/D-1 (T-6): budget exhaustion → loud terminal under ON; shipped
  WARN+continue preserved under OFF.
* D-2/P-11 (T-8): kill-switch OFF golden routing — the shipped
  ``LoopRepairer`` path runs byte-identically (mock repairer awaited).
* A-3 (wiring): the repair prefix rides the agent_node return
  (sentinel element-0, doc + retained ids, response last).
* F-3: ``[SYMPTOM]`` telemetry shape — dual emit, OFF-mode still emits.

Harness mirrors ``tests/test_loop_breaker_integration.py``: build a real
``create_agent_node`` factory closure with stubbed LLM / slots and drive
``agent_node`` directly with crafted state. The engine's summarizer is
stubbed at the CLASS attribute (``SymptomRepairEngine._summarize``) so
the real engine surgery + budget + outcome flow is exercised.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    RemoveMessage,
    SystemMessage,
    ToolMessage,
)

from daemon.config import (
    LoopBreakerConfig,
    _install_symptom_repair_ladder_config,
    _resolve_repair_loop_durable,
    _resolve_symptom_repair_ladder,
    _reset_symptom_repair_ladder_for_tests,
)
from daemon.graph import RepairResult, create_agent_node
from daemon.services.symptom_repair_engine import (
    SYMPTOM_REPAIR_BUDGET,
    SymptomRepairEngine,
)

try:
    from langgraph.graph.message import REMOVE_ALL_MESSAGES
except (ImportError, ModuleNotFoundError):  # pragma: no cover
    REMOVE_ALL_MESSAGES = "__remove_all__"


# ---------------------------------------------------------------------------
# Harness (mirrors tests/test_loop_breaker_integration.py)
# ---------------------------------------------------------------------------


class _StubLoopBreakerSlot:
    def __init__(self, initial: dict[str, dict] | None = None):
        self._state: dict[str, dict] = dict(initial or {})
        self.record_calls: list[tuple[str, str]] = []

    def record_repair(self, instance_id: str, summary: str) -> int:
        self.record_calls.append((instance_id, summary))
        state = self._state.setdefault(instance_id, {"count": 0})
        state["count"] = state.get("count", 0) + 1
        return state["count"]

    def clear(self, instance_id: str) -> None:
        self._state.pop(instance_id, None)

    def get_repair_count(self, instance_id: str) -> int:
        return self._state.get(instance_id, {}).get("count", 0)


class _StubLLM:
    def __init__(self, response: Any = None):
        self.response = response if response is not None else AIMessage(content="ok")
        self.calls: list[list[Any]] = []

    def invoke(self, messages):
        self.calls.append(list(messages))
        return self.response


class _StubGraph:
    """Stand-in for ``graph_ref[0]`` — the SHIPPED repair path's
    recoverable guard requires a bound graph reference; the durable path
    never touches it (return-carried)."""

    def __init__(self):
        self.aupdate_state_calls: list = []

    async def aupdate_state(self, config, values, as_node=None):
        self.aupdate_state_calls.append((values, as_node))

    async def aget_state(self, config):
        state = MagicMock()
        state.values = {"messages": []}
        return state


def _loop_units(count: int, tool: str = "bash", args: dict | None = None):
    args = args or {"cmd": "ls"}
    out = []
    for i in range(count):
        tc_id = f"tc-{i}"
        out.append(
            AIMessage(
                content="",
                tool_calls=[{"id": tc_id, "name": tool, "args": args}],
                id=f"ai-{i}",
            )
        )
        out.append(
            ToolMessage(
                content=f"res-{i}", tool_call_id=tc_id, name=tool, id=f"tm-{i}"
            )
        )
    return out


def _make_agent(
    *,
    loop_breaker_slot=None,
    loop_repairer=None,
    loop_breaker_config=None,
    llm=None,
    graph_ref=None,
    compactor=None,
):
    from daemon.graph import create_agent_node as _can

    if llm is None:
        llm = _StubLLM()
    if graph_ref is None:
        graph_ref = [None]

    agent_node = create_agent_node(
        llm_with_tools=llm,
        system_prompt="you are a test assistant",
        compactor=compactor,
        graph_ref=graph_ref,
        config=None,
        llm_config={"model": "test-model", "model_vision": None},
        retry_config={"transient_attempts": 1, "timeout_attempts": 1},
        llm_standard=None,
        injection_slot=None,
        live_hub=None,
        throttle_slot=None,
        loop_breaker_slot=loop_breaker_slot,
        loop_repairer=loop_repairer,
        loop_breaker_config=loop_breaker_config or LoopBreakerConfig(),
    )
    return agent_node, llm


async def _ok_summarizer(context, symptom_class):
    return "LLM summary of the loop."


def _stub_engine_summarizer(monkeypatch):
    # staticmethod: a plain function set as a class attribute would bind
    # ``self`` and shift the argument positions.
    monkeypatch.setattr(
        SymptomRepairEngine, "_summarize", staticmethod(_ok_summarizer)
    )


async def _boom_summarizer(context, symptom_class):
    raise RuntimeError("facade down")


@pytest.fixture(autouse=True)
def _flag_isolation():
    _reset_symptom_repair_ladder_for_tests()
    yield
    _reset_symptom_repair_ladder_for_tests()


@pytest.fixture
def ladder_on(monkeypatch):
    monkeypatch.setenv("ENSEMBLE_SYMPTOM_REPAIR_LADDER", "1")
    monkeypatch.setenv("ENSEMBLE_REPAIR_LOOP_DURABLE", "1")
    _reset_symptom_repair_ladder_for_tests()
    yield
    _reset_symptom_repair_ladder_for_tests()


@pytest.fixture
def ladder_off(monkeypatch):
    monkeypatch.setenv("ENSEMBLE_SYMPTOM_REPAIR_LADDER", "0")
    monkeypatch.setenv("ENSEMBLE_REPAIR_LOOP_DURABLE", "0")
    _reset_symptom_repair_ladder_for_tests()
    yield
    _reset_symptom_repair_ladder_for_tests()


# ---------------------------------------------------------------------------
# F-1 / F-2 — resolvers, install, boot probe
# ---------------------------------------------------------------------------


class TestKillSwitchResolvers:
    def test_unset_defaults_on(self):
        assert _resolve_symptom_repair_ladder(None) is True
        assert _resolve_repair_loop_durable(None) is True

    def test_empty_string_normalizes_to_default(self):
        assert _resolve_symptom_repair_ladder("") is True
        assert _resolve_symptom_repair_ladder("   ") is True
        assert _resolve_repair_loop_durable("") is True
        assert _resolve_repair_loop_durable("   ") is True

    def test_false_vocabulary(self):
        for raw in ("0", "false", "no", "off", "False", " OFF "):
            assert _resolve_symptom_repair_ladder(raw) is False, raw
            assert _resolve_repair_loop_durable(raw) is False, raw

    def test_true_vocabulary(self):
        for raw in ("1", "true", "yes", "on", "TRUE", " on "):
            assert _resolve_symptom_repair_ladder(raw) is True, raw

    def test_invalid_value_raises_value_error(self):
        with pytest.raises(ValueError, match="ENSEMBLE_SYMPTOM_REPAIR_LADDER"):
            _resolve_symptom_repair_ladder("banana")
        with pytest.raises(ValueError, match="ENSEMBLE_REPAIR_LOOP_DURABLE"):
            _resolve_repair_loop_durable("banana")

    def test_cold_accessor_reads_env_and_raises_loud(self, monkeypatch):
        monkeypatch.setenv("ENSEMBLE_SYMPTOM_REPAIR_LADDER", "0")
        _reset_symptom_repair_ladder_for_tests()
        from daemon.config import get_symptom_repair_ladder_enabled

        assert get_symptom_repair_ladder_enabled() is False
        monkeypatch.setenv("ENSEMBLE_REPAIR_LOOP_DURABLE", "banana")
        _reset_symptom_repair_ladder_for_tests()
        from daemon.config import get_repair_loop_durable_enabled

        with pytest.raises(ValueError, match="ENSEMBLE_REPAIR_LOOP_DURABLE"):
            get_repair_loop_durable_enabled()

    def test_install_then_read(self):
        _install_symptom_repair_ladder_config(
            ladder_enabled=False, loop_durable_enabled=True
        )
        from daemon.config import (
            get_repair_loop_durable_enabled,
            get_symptom_repair_ladder_enabled,
        )

        assert get_symptom_repair_ladder_enabled() is False
        assert get_repair_loop_durable_enabled() is True

    def test_load_config_boot_probe_installs_and_logs(self, monkeypatch, tmp_path, caplog):
        """F-1 boot probe: load_config resolves + installs + logs the
        boot INFO line (grep-able at boot, never lazy)."""
        import logging

        config_yaml = tmp_path / "config.yaml"
        config_yaml.write_text(
            "llm:\n  model: test-model\n  api_key: sk-test\n"
        )
        monkeypatch.setenv("ENSEMBLE_CONFIG", str(config_yaml))
        monkeypatch.delenv("ENSEMBLE_SYMPTOM_REPAIR_LADDER", raising=False)
        monkeypatch.delenv("ENSEMBLE_REPAIR_LOOP_DURABLE", raising=False)
        _reset_symptom_repair_ladder_for_tests()

        from daemon.config import (
            get_repair_loop_durable_enabled,
            get_symptom_repair_ladder_enabled,
            load_config,
        )

        with caplog.at_level(logging.INFO):
            load_config()
        assert get_symptom_repair_ladder_enabled() is True
        assert get_repair_loop_durable_enabled() is True
        boot_lines = [
            r.getMessage()
            for r in caplog.records
            if "[SymptomRepair]" in r.getMessage()
        ]
        assert any(
            "symptom_repair_ladder=True" in line for line in boot_lines
        ), boot_lines


# ---------------------------------------------------------------------------
# Gate contract — ON owns the rung, OFF falls through (D-2 / T-8)
# ---------------------------------------------------------------------------


class TestGateContract:
    async def test_on_uses_engine_not_injected_repairer(self, ladder_on, monkeypatch):
        _stub_engine_summarizer(monkeypatch)
        slot = _StubLoopBreakerSlot()
        repairer = MagicMock()
        repairer.repair = AsyncMock(
            return_value=RepairResult(
                success=True,
                repaired_messages=[],
                summary="shipped",
                repair_message_id="shipped-id",
            )
        )
        agent_node, llm = _make_agent(
            loop_breaker_slot=slot,
            loop_repairer=repairer,
            llm=_StubLLM(response=AIMessage(content="post")),
        )
        result = await agent_node(
            {"messages": _loop_units(3)},
            config={"configurable": {"thread_id": "iid-on"}},
        )
        # The DURABLE engine owned the rung — the shipped repairer mock
        # must NOT be awaited.
        repairer.repair.assert_not_awaited()
        assert slot.record_calls == [("iid-on", "LLM summary of the loop.")]
        # A-3 wiring: sentinel-first prefix + response last.
        outgoing = result["messages"]
        assert isinstance(outgoing[0], RemoveMessage)
        assert outgoing[0].id == REMOVE_ALL_MESSAGES
        assert isinstance(outgoing[-1], AIMessage)
        doc_ids = [
            getattr(m, "id", "") for m in outgoing
        ]
        assert any(i.startswith("repair-iid-on-") for i in doc_ids)
        # B-2: durable budget incremented on the return.
        assert result["repair_budget_used"] == 1

    async def test_off_uses_shipped_repairer_byte_identical(
        self, ladder_off, monkeypatch
    ):
        slot = _StubLoopBreakerSlot()
        repairer = MagicMock()
        repairer.repair = AsyncMock(
            return_value=RepairResult(
                success=True,
                repaired_messages=[
                    HumanMessage(content="repaired", id="rep-1")
                ],
                summary="shipped summary",
                repair_message_id="repair-shipped",
            )
        )
        agent_node, llm = _make_agent(
            loop_breaker_slot=slot,
            loop_repairer=repairer,
            graph_ref=[_StubGraph()],
            llm=_StubLLM(response=AIMessage(content="post")),
        )
        result = await agent_node(
            {"messages": _loop_units(3)},
            config={"configurable": {"thread_id": "iid-off"}},
        )
        # T-8 golden routing: the shipped transient path ran (mock awaited,
        # no sentinel prefix, no budget increment).
        repairer.repair.assert_awaited_once()
        assert slot.record_calls == [("iid-off", "shipped summary")]
        outgoing = result["messages"]
        assert not any(isinstance(m, RemoveMessage) for m in outgoing)
        assert result.get("repair_budget_used", 0) == 0

    async def test_sub_flag_off_disables_durable_only(self, monkeypatch):
        """ADR-0008 surgical disable: master ON + sub OFF ⇒ shipped path."""
        monkeypatch.setenv("ENSEMBLE_SYMPTOM_REPAIR_LADDER", "1")
        monkeypatch.setenv("ENSEMBLE_REPAIR_LOOP_DURABLE", "0")
        _reset_symptom_repair_ladder_for_tests()
        slot = _StubLoopBreakerSlot()
        repairer = MagicMock()
        repairer.repair = AsyncMock(
            return_value=RepairResult(
                success=True,
                repaired_messages=[HumanMessage(content="r", id="rep-1")],
                summary="shipped",
                repair_message_id="repair-shipped",
            )
        )
        agent_node, _ = _make_agent(
            loop_breaker_slot=slot,
            loop_repairer=repairer,
            graph_ref=[_StubGraph()],
            llm=_StubLLM(response=AIMessage(content="post")),
        )
        await agent_node(
            {"messages": _loop_units(3)},
            config={"configurable": {"thread_id": "iid-sub"}},
        )
        repairer.repair.assert_awaited_once()


# ---------------------------------------------------------------------------
# B-2 — budget increment on success only
# ---------------------------------------------------------------------------


class TestBudgetIncrement:
    async def test_successful_repair_increments_once(self, ladder_on, monkeypatch):
        _stub_engine_summarizer(monkeypatch)
        agent_node, _ = _make_agent(
            loop_breaker_slot=_StubLoopBreakerSlot(),
            llm=_StubLLM(response=AIMessage(content="post")),
        )
        result = await agent_node(
            {"messages": _loop_units(3), "repair_budget_used": 0},
            config={"configurable": {"thread_id": "iid-b2"}},
        )
        assert result["repair_budget_used"] == 1

    async def test_preexisting_budget_accumulates(self, ladder_on, monkeypatch):
        _stub_engine_summarizer(monkeypatch)
        agent_node, _ = _make_agent(
            loop_breaker_slot=_StubLoopBreakerSlot(),
            llm=_StubLLM(response=AIMessage(content="post")),
        )
        result = await agent_node(
            {"messages": _loop_units(3), "repair_budget_used": 2},
            config={"configurable": {"thread_id": "iid-b2b"}},
        )
        assert result["repair_budget_used"] == 3

    async def test_aborted_repair_consumes_nothing(self, ladder_on, monkeypatch):
        """Fail-open abort (summarizer failed) → budget NOT consumed."""
        monkeypatch.setattr(
            SymptomRepairEngine, "_summarize", staticmethod(_boom_summarizer)
        )
        agent_node, llm = _make_agent(
            loop_breaker_slot=_StubLoopBreakerSlot(),
            llm=_StubLLM(response=AIMessage(content="fall-through")),
        )
        result = await agent_node(
            {"messages": _loop_units(3), "repair_budget_used": 1},
            config={"configurable": {"thread_id": "iid-abort"}},
        )
        assert result["repair_budget_used"] == 1  # unchanged
        # The turn FELL THROUGH to the normal LLM response (no wedge).
        assert isinstance(result["messages"][-1], AIMessage)
        assert result["messages"][-1].content == "fall-through"


# ---------------------------------------------------------------------------
# B-3 — OQ5 PINNED: reset on new REAL (non-injected) HumanMessage
# ---------------------------------------------------------------------------


class TestBudgetResetOQ5:
    """OQ5 ruling (c) — the durable budget resets on a new REAL
    (non-injected) HumanMessage, aligning budget lifetime with
    user-visible task episodes. PINNED per P1-R6: if the architect
    re-rules OQ5, this class + the ``real_human_boundary`` predicate in
    ``agent_node`` change together.

    Fixture note: the reset predicate inspects ``messages[-1]``, and a
    HumanMessage at the tail ALSO breaks the LoopDetector walk — so a
    reset turn and a repair turn are mutually exclusive by construction.
    These tests pin the reset DECISION directly (carried budget value);
    the increment side is pinned by TestBudgetIncrement."""

    async def test_real_human_message_resets_budget(self, ladder_on, monkeypatch):
        agent_node, _ = _make_agent(
            loop_breaker_slot=_StubLoopBreakerSlot(),
            llm=_StubLLM(response=AIMessage(content="fresh-episode")),
        )
        # A fresh episode: the budget carries 2 from prior episodes, and a
        # NEW real HumanMessage lands at the tail.
        result = await agent_node(
            {
                "messages": [
                    SystemMessage(content="sys", id="sys-1"),
                    AIMessage(content="earlier answer", id="old-ai"),
                    HumanMessage(content="fresh task", id="h-new"),
                ],
                "repair_budget_used": 2,
            },
            config={"configurable": {"thread_id": "iid-oq5"}},
        )
        # Reset fired: 2 → 0 (no repair this turn — the human message
        # breaks detection, so no increment either).
        assert result["repair_budget_used"] == 0

    async def test_injected_human_message_does_not_reset(self, ladder_on, monkeypatch):
        agent_node, _ = _make_agent(
            loop_breaker_slot=_StubLoopBreakerSlot(),
            llm=_StubLLM(response=AIMessage(content="continue")),
        )
        injected_tail = HumanMessage(
            content="operator note",
            id="inj-1",
            additional_kwargs={"injected_message": True},
        )
        result = await agent_node(
            {
                "messages": [
                    AIMessage(content="earlier answer", id="old-ai"),
                    injected_tail,
                ],
                "repair_budget_used": 2,
            },
            config={"configurable": {"thread_id": "iid-oq5b"}},
        )
        # NO reset: the injected tail must not open a new budget episode.
        assert result["repair_budget_used"] == 2

    async def test_context_kind_injection_does_not_reset(self, ladder_on, monkeypatch):
        agent_node, _ = _make_agent(
            loop_breaker_slot=_StubLoopBreakerSlot(),
            llm=_StubLLM(response=AIMessage(content="continue")),
        )
        context_block = HumanMessage(
            content="[SYSTEM CONTEXT: Task Context]\n\nbody",
            id="ctx-tail",
            additional_kwargs={
                "injected_message": True,
                "context_kind": "task_context",
            },
        )
        result = await agent_node(
            {
                "messages": [
                    AIMessage(content="earlier answer", id="old-ai"),
                    context_block,
                ],
                "repair_budget_used": 2,
            },
            config={"configurable": {"thread_id": "iid-oq5c"}},
        )
        assert result["repair_budget_used"] == 2


# ---------------------------------------------------------------------------
# D-1 — exhaustion escalation (T-6) / D-2 OFF preservation
# ---------------------------------------------------------------------------


class TestExhaustionEscalation:
    async def test_durable_cap_exhausted_loud_terminal(self, ladder_on, monkeypatch):
        agent_node, llm = _make_agent(
            loop_breaker_slot=_StubLoopBreakerSlot(),
            llm=_StubLLM(response=AIMessage(content="must not be reached")),
        )
        result = await agent_node(
            {
                "messages": _loop_units(3),
                "repair_budget_used": SYMPTOM_REPAIR_BUDGET,
            },
            config={"configurable": {"thread_id": "iid-term"}},
        )
        outgoing = result["messages"]
        terminal = outgoing[-1]
        assert isinstance(terminal, AIMessage)
        assert terminal.content and "LOOP TERMINATION" in str(terminal.content)
        assert not getattr(terminal, "tool_calls", None)  # routes to END
        # No LLM invoke happened (terminal short-circuit).
        assert len(llm.calls) == 0
        # Budget NOT incremented (no repair executed).
        assert result["repair_budget_used"] == SYMPTOM_REPAIR_BUDGET

    async def test_ram_per_turn_cap_loud_terminal_under_on(
        self, ladder_on, monkeypatch
    ):
        slot = _StubLoopBreakerSlot(
            {"iid-ram": {"count": 3}}  # at max_repairs
        )
        agent_node, llm = _make_agent(
            loop_breaker_slot=slot,
            llm=_StubLLM(response=AIMessage(content="must not be reached")),
        )
        result = await agent_node(
            {"messages": _loop_units(3), "repair_budget_used": 0},
            config={"configurable": {"thread_id": "iid-ram"}},
        )
        terminal = result["messages"][-1]
        assert isinstance(terminal, AIMessage)
        assert "LOOP TERMINATION" in str(terminal.content)
        assert len(llm.calls) == 0

    async def test_ram_per_turn_cap_shipped_warn_continue_under_off(
        self, ladder_off
    ):
        """T-6 OFF arm + D-2: the shipped WARN+continue is preserved
        byte-identically — the LLM IS invoked with the original
        messages."""
        slot = _StubLoopBreakerSlot({"iid-ram": {"count": 3}})
        repairer = MagicMock()
        repairer.repair = AsyncMock()
        agent_node, llm = _make_agent(
            loop_breaker_slot=slot,
            loop_repairer=repairer,
            llm=_StubLLM(response=AIMessage(content="continue-on")),
        )
        result = await agent_node(
            {"messages": _loop_units(3), "repair_budget_used": 0},
            config={"configurable": {"thread_id": "iid-ram"}},
        )
        repairer.repair.assert_not_awaited()  # shipped cap behavior: skip
        assert len(llm.calls) == 1  # continuation with original messages
        assert result["messages"][-1].content == "continue-on"


# ---------------------------------------------------------------------------
# T-10 — placement pins (shipped contracts untouched)
# ---------------------------------------------------------------------------


class TestPlacementPins:
    def test_s1_raise_still_inside_retry_scope(self):
        """T-10: ``classify_llm_errors`` → the nested
        ``_run_with_classification`` still calls ``validate_llm_response``
        INSIDE the retry try-scope (the review-approved S1 contract is
        untouched by phase 1 — source-level pin per the facade-forwarding
        seam style)."""
        import inspect

        import daemon.llm_error_classifier as lec

        src = inspect.getsource(lec.classify_llm_errors)
        assert "validate_llm_response(result, input_messages=messages)" in src
        # The raise inherits the retry → failover → loud-ERROR ladder from
        # this scope (the shipped comment documents it; keep it true).
        assert "INSIDE the retry scope" in src
        # The classifier module must not have grown a repair dependency.
        assert "symptom_repair" not in src

    def test_precall_skipped_when_repair_prefix_carried(self):
        """One durable channel rewrite per node return: the L2 precall
        hook is skipped when the durable loop repair landed this
        invocation (composing two sentinel-first prefixes would resurrect
        the removed loop units). Source-level pin on the wiring."""
        import inspect

        import daemon.graph as graph_module

        src = inspect.getsource(graph_module.create_agent_node)
        assert "_durable_loop.repair_prefix is not None" in src
        assert "_maybe_precall_compact_95" in src
        # The skip guard textually guards the precall call site.
        guard_pos = src.index("repair_prefix is not None")
        precall_pos = src.index("_maybe_precall_compact_95(")
        assert guard_pos < precall_pos


# ---------------------------------------------------------------------------
# W2 — L2-precall-skip BEHAVIORAL pin (stub compactor)
# ---------------------------------------------------------------------------


class TestL2PrecallSkipBehavioral:
    """The skip guard (agent_node, precall site) must be pinned
    BEHAVIORALLY, not only by source ordering: on the superstep where the
    durable loop repair lands, ``_maybe_precall_compact_95`` must NOT be
    consulted; on the next superstep it must. A regression deleting the
    skip would let L2 compact the PRE-repair checkpoint channel and
    return-carriedly resurrect the loop units the surgery just removed —
    while the repair prefix and the compaction prefix fight over the same
    node return (at most ONE durable channel rewrite may ride one
    commit).

    Mechanism: the precall hook is replaced by a counted AsyncMock
    returning the module's no-op outcome, and agent_node is driven across
    real supersteps (the harness mirrors the graph cycle: agent → tools →
    agent). A MagicMock compactor is wired so the wiring is honest; the
    mock at the seam is what makes the call COUNT observable without
    depending on compaction-internal gating."""

    async def test_precall_not_fired_on_repair_superstep_fired_on_next(
        self, ladder_on, monkeypatch
    ):
        import daemon.graph as graph_module

        def _ai_loop_response(seq: int) -> AIMessage:
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": f"tc-{seq}",
                        "name": "bash",
                        "args": {"cmd": "ls"},
                    }
                ],
                id=f"ai-{seq}",
            )

        def _tool_unit(seq: int) -> list:
            tc = f"tc-{seq}"
            return [
                _ai_loop_response(seq),
                ToolMessage(
                    content=f"res-{seq}",
                    tool_call_id=tc,
                    name="bash",
                    id=f"tm-{seq}",
                ),
            ]

        _stub_engine_summarizer(monkeypatch)
        precall_mock = AsyncMock(return_value=graph_module._PRECALL_NOOP)
        monkeypatch.setattr(
            graph_module, "_maybe_precall_compact_95", precall_mock
        )

        agent_node, _ = _make_agent(
            loop_breaker_slot=_StubLoopBreakerSlot(),
            compactor=MagicMock(),  # stub compactor — wiring is real
            llm=_StubLLM(response=_ai_loop_response(9)),
        )

        # Supersteps 1-3: loop units accumulate — precall consulted each
        # time (no repair yet).
        state: dict = {"messages": [HumanMessage(content="go", id="h1")]}
        for seq in range(3):
            await agent_node(
                {**state, "repair_budget_used": 0},
                config={"configurable": {"thread_id": "iid-w2"}},
            )
            state = {"messages": [*state["messages"], *_tool_unit(seq)]}
        assert precall_mock.await_count == 3

        # Superstep 4: THREE identical units → durable repair fires → the
        # precall hook must be SKIPPED on exactly this superstep.
        result4 = await agent_node(
            {**state, "repair_budget_used": 0},
            config={"configurable": {"thread_id": "iid-w2"}},
        )
        # Proof the repair fired on this superstep: sentinel-first return.
        assert isinstance(result4["messages"][0], RemoveMessage)
        assert precall_mock.await_count == 3, (
            "L2 precall hook must NOT be consulted on the repair superstep "
            "(a fire here would compose two sentinel-first channel rewrites)"
        )

        # Superstep 5 (next superstep, post-repair): the hook is consulted
        # again — the skip is scoped to the repair superstep only.
        repaired = result4["messages"]
        result5 = await agent_node(
            {"messages": list(repaired), "repair_budget_used": 1},
            config={"configurable": {"thread_id": "iid-w2"}},
        )
        assert precall_mock.await_count == 4
        # No second repair on the clean channel.
        assert not isinstance(result5["messages"][0], RemoveMessage)


# ---------------------------------------------------------------------------
# F-3 — [SYMPTOM] telemetry shape
# ---------------------------------------------------------------------------


class TestSymptomTelemetry:
    def _lines(self, caplog):
        return [r.getMessage() for r in caplog.records if "[SYMPTOM]" in r.getMessage()]

    async def test_on_mode_emits_detect_repair_and_budget(
        self, ladder_on, monkeypatch, caplog
    ):
        _stub_engine_summarizer(monkeypatch)
        agent_node, _ = _make_agent(
            loop_breaker_slot=_StubLoopBreakerSlot(),
            llm=_StubLLM(response=AIMessage(content="post")),
        )
        import logging

        with caplog.at_level(logging.INFO):
            await agent_node(
                {"messages": _loop_units(3)},
                config={"configurable": {"thread_id": "iid-tel"}},
            )
        lines = self._lines(caplog)
        joined = "\n".join(lines)
        assert "phase=detect action=fired" in joined
        assert "phase=repair action=fired" in joined
        assert "budget=1/3" in joined
        # W3: non-terminal durable-rung lines carry the budget axis.
        assert "axis=durable-task" in joined
        assert "instance=iid" in joined  # short id
        assert "turn=iid-tel" in joined

    async def test_off_mode_still_emits_telemetry(
        self, ladder_off, monkeypatch, caplog
    ):
        """W1 KEEP: OFF-mode storms stay visible — [SYMPTOM] lines emit
        alongside [LOOP BREAKER] with the ladder OFF."""
        slot = _StubLoopBreakerSlot()
        repairer = MagicMock()
        repairer.repair = AsyncMock(
            return_value=RepairResult(
                success=True,
                repaired_messages=[HumanMessage(content="r", id="rep-1")],
                summary="s",
                repair_message_id="repair-x",
            )
        )
        agent_node, _ = _make_agent(
            loop_breaker_slot=slot,
            loop_repairer=repairer,
            graph_ref=[_StubGraph()],
            llm=_StubLLM(response=AIMessage(content="post")),
        )
        import logging

        with caplog.at_level(logging.INFO):
            await agent_node(
                {"messages": _loop_units(3)},
                config={"configurable": {"thread_id": "iid-teloff"}},
            )
        joined = "\n".join(self._lines(caplog))
        assert "phase=detect action=fired" in joined
        assert "phase=repair action=fired" in joined
        assert "transient surgery (shipped path)" in joined
        # W3: shipped-path lines carry the RAM-per-turn axis.
        assert "axis=ram-per-turn" in joined
        assert "axis=durable-task" not in joined

    async def test_terminal_emits_escalate_reason(self, ladder_on, caplog):
        agent_node, _ = _make_agent(
            loop_breaker_slot=_StubLoopBreakerSlot(),
            llm=_StubLLM(),
        )
        import logging

        with caplog.at_level(logging.INFO):
            await agent_node(
                {
                    "messages": _loop_units(3),
                    "repair_budget_used": SYMPTOM_REPAIR_BUDGET,
                },
                config={"configurable": {"thread_id": "iid-telterm"}},
            )
        joined = "\n".join(self._lines(caplog))
        assert "phase=terminal action=escalate" in joined
        assert "repair-budget-exhausted" in joined
        # W3: terminal lines are self-labeled via detail= and carry NO axis.
        terminal_lines = [l for l in self._lines(caplog) if "phase=terminal" in l]
        assert terminal_lines and all("axis=" not in l for l in terminal_lines)

    async def test_abort_emits_repair_abort(self, ladder_on, monkeypatch, caplog):
        monkeypatch.setattr(
            SymptomRepairEngine, "_summarize", staticmethod(_boom_summarizer)
        )
        agent_node, _ = _make_agent(
            loop_breaker_slot=_StubLoopBreakerSlot(),
            llm=_StubLLM(response=AIMessage(content="ft")),
        )
        import logging

        with caplog.at_level(logging.INFO):
            await agent_node(
                {"messages": _loop_units(3), "repair_budget_used": 1},
                config={"configurable": {"thread_id": "iid-telabort"}},
            )
        joined = "\n".join(self._lines(caplog))
        assert "phase=repair_abort action=abort" in joined
        assert "reason=summarizer-failed" in joined
