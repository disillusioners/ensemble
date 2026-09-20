"""Mode: enforce — stale pre-revive attestations do not satisfy a new mission.

Each sub-case seeds a real LangGraph checkpoint, then a new mission uses a
fresh scripted model.  The old attestation is either absent or outside the
last-N AIMessage window.
"""

from __future__ import annotations

import logging
from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool

from tests.support.scripted_chat_model import ScriptedChatModel

INSTANCE_ID = "attestation-leader-e2e"
from daemon.graph import ATTESTATION_NUDGE_TEXT as NUDGE_TEXT


@pytest.fixture(autouse=True)
def _enforce_mode(monkeypatch):
    monkeypatch.setenv("ENSEMBLE_LEADER_ATTESTATION_MODE", "enforce")
    monkeypatch.setenv("WATCHOVER_ENABLED", "false")


def _settings():
    from daemon.services.attestation_gate import GateSettings

    return GateSettings(mode="enforce", window=3, deny_bound=3)


@tool
def attest_completion() -> str:
    """Record a fresh completion attestation for the scripted graph.

    2026-09-19 (attest-first contract, c5d9a38a remediation):
    returns the clean-call teacher text the leader reads via the
    ToolMessage (the runtime hook sets the per-thread caller-
    AIMessage state before invocation)."""
    from daemon.tools.attestation import (
        ATTEST_CLEAN_RESULT_TEXT as _clean,
    )

    return _clean


# 2026-09-19 (attest-first contract, c5d9a38a remediation):
# the FINAL AIMessage MUST be a standalone text report (no tool
# calls, >= ``SHORT_REPORT_WORD_THRESHOLD`` = 150 words) for the
# gate to allow END via ``Decision.ALLOWED``. The OLD
# ``content="new mission done"`` short prose was below the
# threshold and would have produced ``Decision.HOLD`` under the
# new contract.
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
            system_prompt="scripted stale watermark test",
            user_language="Auto",
            language_check_enabled=False,
            manager=manager,
            graph_config={"configurable": {"thread_id": INSTANCE_ID}},
            attestation_enabled=True,
        )


def _nudge_count(messages) -> int:
    return sum(
        isinstance(m, HumanMessage) and bool(m.additional_kwargs.get("attestation_nudge"))
        for m in messages
    )


def _ai(content: str) -> AIMessage:
    return AIMessage(content=content)


@pytest.mark.parametrize("seed", ["empty", "stale"])
@pytest.mark.asyncio
async def test_stale_attestation_watermark_does_not_cross_mission_boundary(
    seed,
    real_graph_module,
    memory_saver,
    file_sqlite_engine,
    attestation_repository,
    attestation_manager_factory,
    caplog,
):
    from langchain_core.messages import ToolMessage

    repo, _instance = attestation_repository
    manager = attestation_manager_factory(file_sqlite_engine, repo)
    # Seed an empty checkpoint.  The stale variant replaces it with old
    # messages, including an attestation followed by enough newer AIs to move it
    # outside N=3.
    # An empty checkpoint can be created in a separate saver; the stale
    # variant needs the real seeded state from the first graph.
    if seed == "empty":
        from langgraph.checkpoint.memory import MemorySaver
        seed_checkpoint = MemorySaver()
    else:
        seed_checkpoint = memory_saver
    seed_graph = _build(
        real_graph_module,
        ScriptedChatModel(responses=[AIMessage(content="seed")], i=0),
        manager,
        seed_checkpoint,
    )
    seed_config = {"configurable": {"thread_id": INSTANCE_ID}}
    if seed == "stale":
        # The OLD seed attestation had ``content="old attestation"``
        # (the bundled c5d9a38a shape under the new contract).
        # The stale-watermark assertion is independent of the
        # OLD shape — the stale attestation is far outside the
        # last-N window (4+ newer AIs), so the gate doesn't see
        # it. The shape preservation here is just to keep the
        # seed messages realistic; the new contract still
        # detects it as a stale attestation either way.
        messages = [
            AIMessage(
                content="old attestation",
                tool_calls=[
                    {"name": "attest_completion", "args": {}, "id": "old-attest"}
                ],
            ),
            ToolMessage(content="old result", tool_call_id="old-attest"),
            _ai("old one"),
            _ai("old two"),
            _ai("old three"),
            _ai("old four"),
        ]
        await seed_graph.aupdate_state(seed_config, {"messages": messages}, as_node="agent")
    else:
        pass

    # 2026-09-19 (attest-first contract, c5d9a38a remediation):
    # the new mission's script emits the CLEAN attest_call (empty
    # content + tool_call — the attest-first pure toolcall turn)
    # + the long standalone text report. The OLD pre-fix script
    # had ``content="attesting now"`` (bundled) +
    # ``content="new mission done"`` (short prose) — under the
    # new contract the bundled shape is ``Decision.HOLD`` and
    # the short prose is below the report-length threshold.
    # The PRIMARY intent of this test (stale attestations from
    # a prior mission don't carry over to the new mission —
    # the new mission's deny ladder fires because the old
    # attestation is outside the window) is preserved.
    model = ScriptedChatModel(
        responses=[
            # 2026-09-06 amendment: anchor as delegated so the
            # stale-watermark deny path is exercised.
            AIMessage(
                content="delegating",
                tool_calls=[
                    {"name": "send_message", "args": {"target": "child"}, "id": "d"}
                ],
            ),
            AIMessage(content="new mission without a fresh attestation"),
            # CLEAN attest_call (empty content + tool_call) — the
            # 2026-09-19 attest-first pure toolcall turn.
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "attest_completion",
                        "args": {},
                        "id": "fresh-attest",
                    }
                ],
            ),
            # Long standalone text report — the FINAL AIMessage
            # (no tool calls, >= 150 words) for the gate to
            # allow END via ``Decision.ALLOWED``. Replaces the
            # OLD ``content="new mission done"`` short prose.
            AIMessage(content=LONG_REPORT_TEXT),
        ],
        i=0,
    )
    graph = _build(real_graph_module, model, manager, seed_checkpoint)
    with caplog.at_level(logging.INFO):
        state = await graph.ainvoke(
            {"messages": [HumanMessage(content="new mission")]},
            config={"configurable": {"thread_id": INSTANCE_ID}, "recursion_limit": 30},
        )

    nudges = [
        m
        for m in state["messages"]
        if isinstance(m, HumanMessage) and m.additional_kwargs.get("attestation_nudge")
    ]
    assert len(nudges) == 1
    assert nudges[0].additional_kwargs["attestation_nudge_denied_count"] == 1
    assert "decision=denied" in caplog.text
    assert "decision=allowed" in caplog.text
    assert repo.get_attestation_denied_count(INSTANCE_ID) == 0
    # 2026-09-19 (attest-first contract): the FINAL AIMessage is
    # the long standalone text report (the OLD short prose was
    # below the threshold and would have produced HOLD). The
    # PRIMARY intent of the test (stale attestations don't
    # carry over, the new mission's deny ladder fires, the
    # fresh attestation resets the counter) is preserved.
    assert any(
        m.content == LONG_REPORT_TEXT
        for m in state["messages"]
        if isinstance(m, AIMessage)
    )
    manager.enqueue_message.assert_not_called()
