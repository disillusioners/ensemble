"""Mode: enforce — per-mission ledger reset and reset-on-attested-allow."""

from __future__ import annotations

import logging
from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool

from tests.support.scripted_chat_model import ScriptedChatModel

INSTANCE_ID = "attestation-leader-e2e"


@pytest.fixture(autouse=True)
def _enforce_mode(monkeypatch):
    monkeypatch.setenv("ENSEMBLE_LEADER_ATTESTATION_MODE", "enforce")
    monkeypatch.setenv("WATCHOVER_ENABLED", "false")


@tool
def attest_completion() -> str:
    """Record a scripted mission attestation (2026-09-19 contract —
    returns the teacher text the leader reads via the ToolMessage)."""
    from daemon.tools.attestation import (
        ATTEST_CLEAN_RESULT_TEXT as _clean,
    )

    return _clean


def _settings():
    from daemon.services.attestation_gate import GateSettings

    return GateSettings(mode="enforce", window=3, deny_bound=3)


def _build(graph_module, model, manager, checkpointer):
    graph_module.build_instance_llms = lambda **_: (model, model)
    with patch(
        "daemon.services.attestation_gate.resolve_gate_settings",
        return_value=_settings(),
    ):
        return graph_module.build_instance_graph(
            tools=[attest_completion],
            checkpointer=checkpointer,
            llm_config={"model": "scripted-test", "api_key": "test"},
            system_prompt="reset matrix",
            user_language="Auto",
            language_check_enabled=False,
            manager=manager,
            graph_config={"configurable": {"thread_id": INSTANCE_ID}},
            attestation_enabled=True,
        )


# Long standalone text report — required for the 2026-09-19
# attest-first contract's ALLOWED path (the FINAL AIMessage must
# be a standalone text report, no tool calls, >=
# ``SHORT_REPORT_WORD_THRESHOLD`` = 150 words). Mirrors the unit
# fixture ``report_ai()``.
LONG_REPORT_TEXT = (
    "The work is finished. All four patches shipped; the test "
    "matrix is green; the integration tests pass on every "
    "environment we maintain. Patch 1 fixed the off-by-one in "
    "the cache TTL calculator; the unit tests now exercise both "
    "the elapsed-second and wall-clock-second boundaries at the "
    "second and minute granularity. Patch 2 cleaned up the dead "
    "imports in the worker pool module after the migration, "
    "removing the legacy compatibility shim and the related "
    "test scaffolding. Patch 3 refactored the error-reporting "
    "decorator so the stack-frame metadata is consistent across "
    "all four call sites in the graph node and the manager "
    "facade. Patch 4 added the missing operator-boot log line "
    "for the new resolver module so operators can grep the "
    "boot summary for the resolved effective values. All four "
    "patches passed their respective suites on the first run "
    "with no flake; the integration matrix is green end-to-end "
    "across all environments we maintain. No follow-ups "
    "outstanding; the mission is complete and ready for review "
    "by the next teammate in the chain."
)


def _script():
    return ScriptedChatModel(
        responses=[
            # 2026-09-06 conditional gate: anchor the mission as
            # delegated (send_message tool call) so the
            # ``attestation_required==True`` branch is exercised
            # and the deny/allow mix still produces
            # ``decision=denied`` ×2 + ``decision=allowed`` ×2
            # across the two mission calls below. Without the
            # anchor the conditional gate routes everything to
            # the ALLOWED-when-not-required branch and the deny
            # counter is never incremented.
            AIMessage(
                content="delegating to a child",
                tool_calls=[
                    {
                        "name": "send_message",
                        "args": {"target": "child"},
                        "id": "ledger-reset-anchor",
                    }
                ],
            ),
            AIMessage(content="plain completion"),
            # 2026-09-19 attest-first contract: the CLEAN
            # attest_call (empty content + tool_call) replaces
            # the OLD ``content="attest"`` shape. The OLD shape
            # was the bundled c5d9a38a class under the new
            # contract.
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "attest_completion",
                        "args": {},
                        "id": "reset-script-attest",
                    }
                ],
            ),
            # 2026-09-19 attest-first contract: the standalone
            # text report (the FINAL AIMessage, no tool calls,
            # >= SHORT_REPORT_WORD_THRESHOLD = 150 words) is the
            # LAST AI message. The OLD ``content="done"``
            # short prose was below the threshold and would
            # produce ``Decision.HOLD`` under the new contract.
            AIMessage(content=LONG_REPORT_TEXT),
        ],
        i=0,
    )


@pytest.mark.asyncio
async def test_attested_allow_resets_and_next_mission_starts_clean(
    real_graph_module,
    file_sqlite_engine,
    attestation_repository,
    attestation_manager_factory,
    caplog,
):
    from langgraph.checkpoint.memory import MemorySaver

    repo, _instance = attestation_repository
    manager = attestation_manager_factory(file_sqlite_engine, repo)

    first_graph = _build(real_graph_module, _script(), manager, MemorySaver())
    with caplog.at_level(logging.INFO):
        await first_graph.ainvoke(
            {"messages": [HumanMessage(content="mission a")]},
            config={"configurable": {"thread_id": INSTANCE_ID}, "recursion_limit": 20},
        )
    assert repo.get_attestation_denied_count(INSTANCE_ID) == 0

    second_graph = _build(real_graph_module, _script(), manager, MemorySaver())
    with caplog.at_level(logging.INFO):
        await second_graph.ainvoke(
            {"messages": [HumanMessage(content="mission b")]},
            config={"configurable": {"thread_id": INSTANCE_ID}, "recursion_limit": 20},
        )

    # The second mission sees the DB value, not residue from mission A.
    assert repo.get_attestation_denied_count(INSTANCE_ID) == 0
    assert caplog.text.count("decision=denied") == 2
    assert caplog.text.count("decision=allowed") == 2
    manager.enqueue_message.assert_not_called()
