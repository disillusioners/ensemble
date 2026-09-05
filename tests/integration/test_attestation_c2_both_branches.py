"""Phase 2 EXIT CRITERION — C2 both-branches activation test (task 2.5).

Leader completion attestation, D1=B in-graph pre-END gate.

The structural hazard (C2): ``create_should_continue(language_check_
enabled=False)`` returns the ORIGINAL ``should_continue`` UNCHANGED —
a gate wired into only ONE branch is structurally inert for
auto-language leaders. These tests build REAL compiled graphs (mocked
LLM, in-memory checkpointer, no DB) for ``language_check_enabled ∈
{True, False}`` and assert the attestation gate is INVOKED on BOTH
branches, with the two NEW manager facades mocked to ``(0, 0)`` so R2
evaluates as a denial.

Scenarios:

* parameterized both-branches activation — deny nudge lands for
  ``language_check_enabled=True`` AND ``=False``;
* (i)  ``attestation_enabled=False`` → gate NEVER invoked (facades
       uncalled, no nudge) regardless of inputs;
* (ii) ``mode="dry"`` → dry_log decision logged, nudge NOT injected,
       run completes on the first would-be END (zero side effects);
* (iii) C3 fail-open — injected scanner exception → allowed END +
       ``event=leader_completion_gate_error``;
* R2 in-graph sanity — non-zero ``pending_children`` allows the
  un-attested END with no nudge.

NOTE ON THE LANGGRAPH MOCKS: the root ``tests/conftest.py`` installs
mock ``langgraph`` modules globally; real graph construction requires
the real package. This module follows the established eviction pattern
(``tests/integration/checkpoint_prune_real_saver.py``): an autouse
module-scoped fixture evicts the mocks and re-imports ``daemon.graph``
fresh (so it binds the REAL langgraph), and restores both on teardown
so neighbouring test files keep their mock-bound identity.

E2E coverage of the full deny → nudge → attest → allow cycle here is
deliberately minimal (the deny nudge landing in ``state["messages"]``
+ routing back to ``agent``); the exhaustive E2E matrix is Phase 5
task 5.5. NO test asserts ``manager.enqueue_message`` is called with a
deny — that dual-delivery pattern is FORBIDDEN (C1b/R1).
"""

from __future__ import annotations

import importlib
import logging
import sys
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool

from tests.helpers.checkpoint_prune_pg import (
    evict_langgraph_mocks,
    restore_langgraph_mocks,
)

#: The exact R1 nudge constant (server-authored; NFR-6 verbatim).
NUDGE_TEXT = "The work is not yet finished — check current progress and continue."

TEST_THREAD_ID = "test-leader-instance-1"

# Populated by the autouse fixture — the REAL (real-langgraph-bound)
# daemon.graph module.
_real_graph_module = None


@pytest.fixture(autouse=True)
def real_langgraph_for_attestation():
    """Evict the conftest langgraph mocks; re-import daemon.graph real.

    Mirrors the repo eviction pattern used by the real-saver integration
    tests. Teardown drops the real-bound ``daemon.graph`` from
    ``sys.modules`` so the next test file re-imports it against the
    mocks the root conftest re-installs (identity hygiene).
    """
    global _real_graph_module
    saved = evict_langgraph_mocks()
    saved_daemon_graph = sys.modules.pop("daemon.graph", None)
    try:
        _real_graph_module = importlib.import_module("daemon.graph")
        yield
    finally:
        sys.modules.pop("daemon.graph", None)
        if saved_daemon_graph is not None:
            sys.modules["daemon.graph"] = saved_daemon_graph
        restore_langgraph_mocks(saved)
        _real_graph_module = None


@tool
def attest_completion() -> dict:
    """Test stub of the Phase 1 attestation tool (no-op confirmation)."""
    return {"attested": True}


def plain_ai(text="Working on the mission."):
    return AIMessage(content=text)


def attest_ai():
    return AIMessage(
        content="Attesting now.",
        tool_calls=[{"name": "attest_completion", "args": {}, "id": "call-1"}],
    )


def make_manager(pending_children=0, wakeups=0):
    """Mock manager with the two NEW R2 facades + inert watchover surface."""
    manager = MagicMock()
    manager.count_pending_children = MagicMock(return_value=pending_children)
    manager.get_queued_or_expected_wakeups = MagicMock(return_value=wakeups)
    # keep the question-pause router inert
    manager.is_question_pause_requested = MagicMock(return_value=False)
    # keep watchover passthrough inert (slot reads this via getattr)
    manager.is_watchover_enabled = MagicMock(return_value=False)
    # forbidden dual-delivery surface — asserted NOT called on deny
    manager.enqueue_message = MagicMock()
    return manager


def build_graph(
    language_check_enabled: bool,
    attestation_enabled: bool,
    manager,
    mode: str = "enforce",
):
    """Build a REAL compiled graph with a scripted mock LLM.

    LLM script: first call — plain AIMessage (would-be END, NO
    attestation → deny path); second call — ``attest_completion`` tool
    call; third call — plain final AIMessage (attested END → allow).
    """
    assert _real_graph_module is not None, "fixture did not run"
    build_instance_graph = _real_graph_module.build_instance_graph

    scripted = [plain_ai(), attest_ai(), plain_ai("Done.")]

    def _invoke(messages, *args, **kwargs):
        return scripted.pop(0)

    mock_llm = MagicMock()
    mock_llm.bind_tools = MagicMock(return_value=mock_llm)
    mock_llm.invoke = MagicMock(side_effect=_invoke)

    kwargs: dict[str, Any] = dict(
        tools=[attest_completion],
        checkpointer=_memory_saver(),
        llm_config={"model": "test-model", "api_key": "test"},
        system_prompt="test system prompt",
        user_language="English",
        language_check_enabled=language_check_enabled,
        manager=manager,
        attestation_enabled=attestation_enabled,
    )

    with patch.object(_real_graph_module, "ThinkingChatOpenAI", return_value=mock_llm):
        # Deterministic language-check pass-through: the test targets
        # routing, not language detection.
        with patch.object(
            _real_graph_module, "detect_wrong_language", return_value=False
        ):
            with patch(
                "daemon.services.attestation_gate.resolve_gate_settings",
                return_value=_settings(mode),
            ):
                return build_instance_graph(**kwargs)


def _settings(mode):
    from daemon.services.attestation_gate import GateSettings

    return GateSettings(mode=mode, window=3, deny_bound=3)


def _memory_saver():
    from langgraph.checkpoint.memory import MemorySaver

    return MemorySaver()


async def run_turn(graph, message="Do the mission."):
    """Invoke one user turn; returns the final message list."""
    config = {"configurable": {"thread_id": TEST_THREAD_ID}, "recursion_limit": 25}
    result = await graph.ainvoke(
        {"messages": [HumanMessage(content=message)]}, config=config
    )
    return result["messages"]


def nudge_messages(messages):
    return [
        m
        for m in messages
        if isinstance(m, HumanMessage)
        and m.additional_kwargs.get("attestation_nudge")
    ]


def ai_contents(messages):
    return [m.content for m in messages if isinstance(m, AIMessage)]


@pytest.fixture(autouse=True)
def _inert_watchover(monkeypatch):
    # Watchover global kill-switch — keeps the tools-lane passthrough
    # instant for the mocked manager (zero-cost path).
    monkeypatch.setenv("WATCHOVER_ENABLED", "false")


# =============================================================================
# THE exit criterion — gate invoked on BOTH create_should_continue branches
# =============================================================================


class TestC2BothBranchesActivation:
    @pytest.mark.parametrize("language_check_enabled", [True, False])
    async def test_gate_invoked_on_both_branches(self, language_check_enabled, caplog):
        manager = make_manager(0, 0)  # R2 deny predicate satisfied
        graph = build_graph(
            language_check_enabled, attestation_enabled=True, manager=manager
        )

        with caplog.at_level(logging.INFO, logger="daemon.services.attestation_gate"):
            messages = await run_turn(graph)

        # R2 facades were consulted (gate ran — C2 activation on this branch)
        manager.count_pending_children.assert_called()
        manager.get_queued_or_expected_wakeups.assert_called()

        # deny nudge landed in state and routed back through agent
        # (scripted flow: deny nudge → agent attests → tools → agent
        # final prose → gate allow → END)
        nudges = nudge_messages(messages)
        assert len(nudges) == 1, f"expected exactly 1 nudge, got {len(nudges)}"
        assert nudges[0].content == NUDGE_TEXT

        # the attestation tool actually executed (route-back worked)
        assert any(
            getattr(m, "name", None) == "attest_completion" for m in messages
        )

        # canonical decision logs emitted: one denied + one allowed
        assert "decision=denied" in caplog.text
        assert "decision=allowed" in caplog.text

    @pytest.mark.parametrize("language_check_enabled", [True, False])
    async def test_disabled_gate_never_invoked(self, language_check_enabled):
        manager = make_manager(0, 0)
        graph = build_graph(
            language_check_enabled, attestation_enabled=False, manager=manager
        )
        messages = await run_turn(graph)

        manager.count_pending_children.assert_not_called()
        manager.get_queued_or_expected_wakeups.assert_not_called()
        assert nudge_messages(messages) == []
        # the run ends after the FIRST plain AI message (no gate, no
        # attestation loop) — the scripted attest turn is never reached
        assert ai_contents(messages) == ["Working on the mission."]

    @pytest.mark.parametrize("language_check_enabled", [True, False])
    async def test_dry_mode_passive_observer(self, language_check_enabled, caplog):
        manager = make_manager(0, 0)
        graph = build_graph(
            language_check_enabled,
            attestation_enabled=True,
            manager=manager,
            mode="dry",
        )
        with caplog.at_level(logging.INFO, logger="daemon.services.attestation_gate"):
            messages = await run_turn(graph)

        # gate evaluated (facades consulted) ...
        manager.count_pending_children.assert_called()
        # ... but ZERO side effects: dry_log logged, no nudge, and the
        # run ends on the first would-be END (no continuation loop)
        assert "decision=dry_log" in caplog.text
        assert nudge_messages(messages) == []
        assert ai_contents(messages) == ["Working on the mission."]
        # the forbidden dual-delivery surface stays untouched
        manager.enqueue_message.assert_not_called()


# =============================================================================
# C3 fail-open — injected scanner exception
# =============================================================================


class TestC3FailOpen:
    @pytest.mark.parametrize("language_check_enabled", [True, False])
    async def test_scanner_exception_allows_end_with_error_log(
        self, language_check_enabled, caplog, monkeypatch
    ):
        manager = make_manager(0, 0)
        graph = build_graph(
            language_check_enabled, attestation_enabled=True, manager=manager
        )

        def boom(*args, **kwargs):
            raise ValueError("injected scanner failure")

        monkeypatch.setattr(
            "daemon.services.attestation_gate.scan_for_attestation_detailed",
            boom,
        )
        with caplog.at_level(logging.ERROR, logger="daemon.services.attestation_gate"):
            messages = await run_turn(graph)

        # fail-open: the END proceeded — no nudge, single AI turn
        assert nudge_messages(messages) == []
        assert ai_contents(messages) == ["Working on the mission."]
        # structured error event with the error class
        assert "event=leader_completion_gate_error" in caplog.text
        assert "error_class=ValueError" in caplog.text


# =============================================================================
# R2 sanity in-graph — non-zero pending input allows the un-attested END
# =============================================================================


class TestR2AllowInGraph:
    @pytest.mark.parametrize("language_check_enabled", [True, False])
    async def test_pending_children_allow_end_without_nudge(
        self, language_check_enabled
    ):
        manager = make_manager(pending_children=2, wakeups=0)
        graph = build_graph(
            language_check_enabled, attestation_enabled=True, manager=manager
        )
        messages = await run_turn(graph)
        manager.count_pending_children.assert_called()
        assert nudge_messages(messages) == []
        assert ai_contents(messages) == ["Working on the mission."]
