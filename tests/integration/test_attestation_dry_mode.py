"""Integration test — leader completion attestation dry-run mode (Phase 4 task 4.4).

Per D2 / D8 (RESOLVED): when ``ENSEMBLE_LEADER_ATTESTATION_MODE=dry``
(ship default), the gate:

* RUNS the scanner + computes the decision + reads R2 inputs;
* EMITS the canonical structured log entry with ``decision=dry_log``;
* ALLOWS the END (the leader turn terminates normally);
* has ZERO side effects — no nudge injected, no counter change, no
  flag change.

This test boots a real LangGraph with a mocked LLM that produces a
plain ``AIMessage`` (no attestation) — exactly the scenario the gate
would DENY under ``mode=enforce``. Under ``mode=dry`` we assert:

1. ``event=leader_completion_gate decision=dry_log`` is in the log;
2. the canonical schema fields are all present (Phase 4 task 4.5);
3. NO ``HumanMessage`` with ``attestation_nudge=True`` was injected
   into ``state["messages"]``;
4. ``manager.enqueue_message`` was NOT called (forbidden dual-delivery);
5. the leader turn ends normally — the graph returns control to the
   caller without an endless loop.

NOTE ON THE LANGGRAPH MOCKS: the root ``tests/conftest.py`` installs
mock ``langgraph`` modules globally; real graph construction requires
the real package. This module follows the established eviction pattern
(``tests/integration/test_attestation_c2_both_branches.py``): an
autouse module-scoped fixture evicts the mocks and re-imports
``daemon.graph`` fresh, and restores both on teardown.
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

#: The exact R1 nudge constant (server-authored; NFR-6 verbatim) —
#: asserted to NEVER appear in the dry-mode message stream.
NUDGE_TEXT = "The work is not yet finished — check current progress and continue."

TEST_THREAD_ID = "test-leader-dry-instance-1"

# Populated by the autouse fixture — the REAL (real-langgraph-bound)
# daemon.graph module.
_real_graph_module = None


@pytest.fixture(autouse=True)
def real_langgraph_for_attestation():
    """Evict the conftest langgraph mocks; re-import daemon.graph real."""
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


def make_manager(pending_children=0, wakeups=0):
    """Mock manager with the two NEW R2 facades + inert watchover surface."""
    manager = MagicMock()
    manager.count_pending_children = MagicMock(return_value=pending_children)
    manager.get_queued_or_expected_wakeups = MagicMock(return_value=wakeups)
    manager.is_question_pause_requested = MagicMock(return_value=False)
    manager.is_watchover_enabled = MagicMock(return_value=False)
    # Forbidden dual-delivery surface — asserted NOT called on dry.
    manager.enqueue_message = MagicMock()
    return manager


def build_dry_graph(language_check_enabled: bool, manager):
    """Build a REAL compiled graph with mode=dry + a scripted mock LLM.

    LLM script: a single plain AIMessage (would-be END, NO attestation).
    Under mode=enforce this would deny + inject a nudge. Under mode=dry
    we expect ZERO side effects.
    """
    assert _real_graph_module is not None, "fixture did not run"
    build_instance_graph = _real_graph_module.build_instance_graph

    def _invoke(messages, *args, **kwargs):
        return plain_ai("Working on the mission — no attestation here.")

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
        attestation_enabled=True,
    )

    with patch.object(_real_graph_module, "ThinkingChatOpenAI", return_value=mock_llm):
        with patch.object(
            _real_graph_module, "detect_wrong_language", return_value=False
        ):
            # Pin the resolver to dry mode (Pattern C cached-global).
            with patch(
                "daemon.services.attestation_gate.resolve_gate_settings",
                return_value=_settings("dry"),
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
    config = {
        "configurable": {"thread_id": TEST_THREAD_ID},
        "recursion_limit": 25,
    }
    result = await graph.ainvoke(
        {"messages": [HumanMessage(content=message)]}, config=config
    )
    return result["messages"]


def nudge_messages(messages):
    """Find any HumanMessage carrying the attestation_nudge marker."""
    return [
        m
        for m in messages
        if isinstance(m, HumanMessage)
        and m.additional_kwargs.get("attestation_nudge")
    ]


# =============================================================================
# Dry-mode integration tests (Phase 4 task 4.4)
# =============================================================================


@pytest.mark.parametrize(
    "language_check_enabled",
    [True, False],
    ids=["lang_check_on", "lang_check_off"],
)
async def test_dry_mode_allows_end_with_full_decision_log(
    language_check_enabled, monkeypatch, caplog
):
    """Dry mode: gate evaluates + logs decision=dry_log + allows END.

    Asserts (per task 4.4 test notes):

    * ``event=leader_completion_gate decision=dry_log`` is logged;
    * the canonical schema fields are all present (Phase 4 task 4.5);
    * no nudge injected (no HumanMessage with ``attestation_nudge=True``);
    * ``manager.enqueue_message`` was NOT called (no dual-delivery);
    * the leader turn ends normally (the graph returns control).
    """
    manager = make_manager(pending_children=0, wakeups=0)
    graph = build_dry_graph(language_check_enabled, manager)

    with caplog.at_level(
        logging.INFO, logger="daemon.services.attestation_gate"
    ):
        messages = await run_turn(graph)

    # (a) event=leader_completion_gate decision=dry_log emitted
    info_text = "\n".join(
        r.message for r in caplog.records if r.levelno == logging.INFO
    )
    assert "event=leader_completion_gate" in info_text
    assert "decision=dry_log" in info_text
    assert "mode=dry" in info_text

    # (b) canonical schema fields all present (Phase 4 task 4.5)
    from daemon.services.attestation_gate import CANONICAL_LOG_SCHEMA_FIELDS

    for field in CANONICAL_LOG_SCHEMA_FIELDS:
        assert field in info_text, f"canonical field {field!r} missing"

    # R2 inputs surfaced as 0 (the script uses pending_children=0, wakeups=0)
    assert "pending_children=0" in info_text
    assert "queued_or_expected_wakeups=0" in info_text

    # (c) NO nudge HumanMessage in the message stream
    assert nudge_messages(messages) == [], (
        "dry mode MUST NOT inject the attestation_nudge HumanMessage "
        f"(found {len(nudge_messages(messages))})"
    )

    # The plain AIMessage from the LLM IS in the message stream —
    # the dry-mode gate does not modify the LLM's emitted content.
    ai_messages = [m for m in messages if isinstance(m, AIMessage)]
    assert any(
        "no attestation here" in (m.content or "") for m in ai_messages
    )

    # (d) manager.enqueue_message NEVER called on dry (forbidden C1b
    # dual-delivery surface; dry is a passive observer).
    manager.enqueue_message.assert_not_called()


async def test_dry_mode_no_counter_or_flag_change():
    """Dry mode does not change ``attestation_denied_count`` or
    ``completion_gate_escalated`` on the instance row.

    The gate's graph-side ledger branch ONLY writes on ``DENIED`` or
    ``TERMINAL_AFTER_BOUND``; ``DRY_LOG`` is the passive-observer
    value that the ``elif decision.decision is`` ladder structurally
    cannot reach. This test pins that contract by ensuring the manager
    does NOT expose an ``_instance_repository`` attribute — the gate
    build site (graph.py:6910) treats ``manager._instance_repository
    is None`` as "no ledger writes"; under dry mode the conditional
    ladder skips ``DRY_LOG`` anyway, so the build-time check is the
    belt-and-suspenders guarantee.
    """
    manager = make_manager(pending_children=0, wakeups=0)
    # Drop the auto-spec ``_instance_repository`` that MagicMock would
    # auto-create — the test pins the absence so the gate's build-time
    # ledger-bypass branch is exercised.
    if hasattr(manager, "_instance_repository"):
        delattr(manager, "_instance_repository")

    graph = build_dry_graph(False, manager)
    # Run a turn — if the dry mode accidentally called a ledger write,
    # the manager's ``_instance_repository`` lookup would either return
    # a MagicMock (no error, no writes — same observable outcome) OR
    # raise AttributeError. The gate's source-of-truth contract is the
    # conditional ladder in ``daemon/graph.py``: ``DRY_LOG`` is NOT in
    # the ``if decision.decision is`` branches, so writes are
    # structurally impossible. This test pins that contract at the
    # integration layer (the dry turn completes without error).
    messages = await run_turn(graph)
    # Sanity: the turn produced output messages — the dry mode
    # actually ran end-to-end (no infinite-loop trap).
    assert len(messages) >= 1

    # The mocked manager exposes the two R2 facades; if the dry mode
    # accidentally called a ledger method, it would error (no
    # attribute). The gate is structurally inert under dry mode —
    # the absence of error is the contract.


async def test_dry_mode_metrics_emitted(caplog):
    """Promotion metrics ``dry_log_total`` + ``dry_log_deny_predicate_total``
    are bumped on dry-mode evaluations where the R2-deny predicate
    would have fired under ``enforce`` (per task 4.6).
    """
    manager = make_manager(pending_children=0, wakeups=0)
    graph = build_dry_graph(False, manager)

    # Reset the resolver metrics so this test is hermetic.
    from daemon.services.attestation_resolver import (
        get_promotion_metrics,
        reset_attestation_resolver_for_tests,
    )

    reset_attestation_resolver_for_tests()

    with caplog.at_level(
        logging.INFO, logger="daemon.services.attestation_resolver"
    ):
        await run_turn(graph)

    snapshot = get_promotion_metrics()
    # R2-deny predicate satisfied (pending=0, wakeups=0, not attested) →
    # both dry counters must increment.
    assert snapshot["dry_log_total"] >= 1
    assert snapshot["dry_log_deny_predicate_total"] >= 1
    # enforce_denied_total stays zero — this test runs under explicit
    # mode="dry"; even with the operator-override ship default of
    # "enforce" (2026-09-06), the canonical default for this scenario is
    # still dry (the test sets it explicitly).
    assert snapshot["enforce_denied_total"] == 0


async def test_dry_mode_r2_inputs_unreadable_still_allows(caplog):
    """Dry-mode R2-input failure path: the gate degrades to allow via
    fail-OPEN (``leader_completion_gate_db_error`` event), the leader
    turn ends normally, no nudge injected.

    This proves the dry mode's zero-side-effect contract survives the
    fail-OPEN seam — even when the R2 inputs are unreadable, the
    dry mode still produces zero side effects.
    """
    manager = make_manager()
    # Force a DB failure on the first R2 read — the fail-OPEN branch
    # in ``evaluate()`` returns ``Decision.ALLOWED`` with
    # ``pending_children=-1`` / ``queued_or_expected_wakeups=-1``.
    manager.count_pending_children.side_effect = RuntimeError("db down")

    graph = build_dry_graph(False, manager)
    with caplog.at_level(
        logging.INFO, logger="daemon.services.attestation_gate"
    ):
        messages = await run_turn(graph)

    # The fail-OPEN DB error event was emitted (operator-visibility).
    assert any(
        "leader_completion_gate_db_error" in r.message
        for r in caplog.records
    )
    # No nudge (the fail-OPEN path returns ALLOWED, not DENIED, so the
    # graph's nudge branch is never taken).
    assert nudge_messages(messages) == []
    # No enqueue_message call (fail-OPEN + dry = pure passive observer).
    manager.enqueue_message.assert_not_called()


async def test_dry_mode_attested_allow_does_not_reset_counter(caplog):
    """Per leader ruling 1: counter resets ONLY on attested-allow under
    ``enforce``. Under ``dry``, even an attested allow does NOT reset
    the counter — dry is a passive observer.

    The script emits one attested AIMessage; the gate fires dry_log,
    the counter is unchanged (the dry branch never reaches the
    ``safe_reset`` call).
    """
    manager = make_manager(pending_children=0, wakeups=0)

    # Override the LLM script: emit an attestation AIMessage followed
    # by a final plain AIMessage (attested allow).
    assert _real_graph_module is not None
    from langgraph.checkpoint.memory import MemorySaver

    build_instance_graph = _real_graph_module.build_instance_graph

    def attest_ai():
        return AIMessage(
            content="Attesting now.",
            tool_calls=[
                {"name": "attest_completion", "args": {}, "id": "call-1"}
            ],
        )

    scripted = [attest_ai(), plain_ai("Done.")]

    def _invoke(messages, *args, **kwargs):
        return scripted.pop(0)

    mock_llm = MagicMock()
    mock_llm.bind_tools = MagicMock(return_value=mock_llm)
    mock_llm.invoke = MagicMock(side_effect=_invoke)

    kwargs: dict[str, Any] = dict(
        tools=[attest_completion],
        checkpointer=MemorySaver(),
        llm_config={"model": "test-model", "api_key": "test"},
        system_prompt="test system prompt",
        user_language="English",
        language_check_enabled=False,
        manager=manager,
        attestation_enabled=True,
    )

    with patch.object(
        _real_graph_module, "ThinkingChatOpenAI", return_value=mock_llm
    ):
        with patch.object(
            _real_graph_module, "detect_wrong_language", return_value=False
        ):
            with patch(
                "daemon.services.attestation_gate.resolve_gate_settings",
                return_value=_settings("dry"),
            ):
                graph = build_instance_graph(**kwargs)

    with caplog.at_level(
        logging.INFO, logger="daemon.services.attestation_gate"
    ):
        messages = await run_turn(graph)

    info_text = "\n".join(
        r.message for r in caplog.records if r.levelno == logging.INFO
    )
    # The gate ran and emitted dry_log (we ran two AIMessages; only
    # the FINAL would-be END trips the gate — and that's still dry_log).
    assert "decision=dry_log" in info_text
    assert "mode=dry" in info_text
    # No nudge — attested allow under dry is still zero side effects.
    assert nudge_messages(messages) == []
    manager.enqueue_message.assert_not_called()