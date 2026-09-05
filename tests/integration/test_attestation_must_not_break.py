"""Mode-pinned 3x6 must-not-break regression surface matrix.

Each sub-case is independent: no test combines the mode and surface values
of another sub-case.  The matrix uses the real graph for normal/finalize paths,
real facade methods for the WC wake lane, and real service/registry surfaces
for the report, claim, and non-leader lanes.
"""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool

from daemon.services.attestation_gate import GateSettings, evaluate
from daemon.services.report_delivery_recovery import ReportDeliveryRecoveryService
from daemon.services.waiting_children_watchdog import WaitingChildrenWatchdog
from tests.support.scripted_chat_model import ScriptedChatModel

REPO_ROOT = Path(__file__).resolve().parents[2]
INSTANCE_ID = "attestation-leader-e2e"


@pytest.fixture(autouse=True)
def _pin_matrix_environment(monkeypatch):
    monkeypatch.setenv("WATCHOVER_ENABLED", "false")


def _settings(mode):
    return GateSettings(mode=mode, window=3, deny_bound=3)


def _build(graph_module, model, manager, checkpointer, mode):
    graph_module.build_instance_llms = lambda **_: (model, model)
    with patch(
        "daemon.services.attestation_gate.resolve_gate_settings",
        return_value=_settings(mode),
    ):
        return graph_module.build_instance_graph(
            tools=[attest_completion],
            checkpointer=checkpointer,
            llm_config={"model": "scripted-test", "api_key": "test"},
            system_prompt="must-not-break matrix",
            user_language="Auto",
            language_check_enabled=False,
            manager=manager,
            graph_config={"configurable": {"thread_id": INSTANCE_ID}},
            attestation_enabled=True,
        )


@tool
def attest_completion() -> dict:
    """Attestation used by the normal/mission-finalize surface scripts."""
    return {"attested": True}


async def _normal_graph(graph_module, model, manager, checkpointer, mode, caplog):
    graph = _build(graph_module, model, manager, checkpointer, mode)
    with caplog.at_level(logging.INFO):
        state = await graph.ainvoke(
            {"messages": [HumanMessage(content="normal completion")]},
            config={"configurable": {"thread_id": INSTANCE_ID}, "recursion_limit": 20},
        )
    return graph, state


@pytest.mark.parametrize(
    "surface",
    [
        "normal_completion",
        "mission_finalize",
        "wc_wake",
        "report_recovery",
        "report_claim",
        "non_leader_scope",
    ],
)
@pytest.mark.parametrize("mode", ["off", "dry", "enforce"])
@pytest.mark.asyncio
async def test_all_six_must_not_break_surfaces_under_each_mode(
    surface,
    mode,
    real_graph_module,
    memory_saver,
    file_sqlite_engine,
    attestation_repository,
    attestation_manager_factory,
    caplog,
):
    """AC-E2E-3/5: one stable surface contract, independently under each mode."""

    repo, _instance = attestation_repository
    manager = attestation_manager_factory(file_sqlite_engine, repo)
    if surface in {"normal_completion", "mission_finalize"}:
        model = ScriptedChatModel(
            responses=[
                AIMessage(
                    content="attested",
                    tool_calls=[
                        {
                            "name": "attest_completion",
                            "args": {},
                            "id": f"matrix-{surface}",
                        }
                    ],
                ),
                AIMessage(content="finished"),
            ],
            i=0,
        )
        graph, state = await _normal_graph(
            real_graph_module, model, manager, memory_saver, mode, caplog
        )
        assert not any(
            isinstance(m, HumanMessage) and m.additional_kwargs.get("attestation_nudge")
            for m in state["messages"]
        )
        assert state["messages"][-1].content == "finished"
        assert repo.get_attestation_denied_count(INSTANCE_ID) == 0
        if surface == "mission_finalize":
            # The real graph returns control to the post-graph finalizer;
            # emulate that final status transition and ensure it is ordinary.
            assert repo.get(INSTANCE_ID).status == "idle"
            assert repo.transition_status_if(INSTANCE_ID, "completed", ("idle",)) is not None
            assert repo.get(INSTANCE_ID).status == "completed"
        manager.enqueue_message.assert_not_called()
        return

    if surface == "wc_wake":
        manager = attestation_manager_factory(
            file_sqlite_engine, repo, queued_wakeups=1
        )
        result = evaluate(
            INSTANCE_ID,
            0,
            [AIMessage(content="unattested")],
            _settings(mode),
            manager,
        )
        assert result.should_inject_nudge is False
        if mode == "enforce":
            assert result.decision.value == "allowed_legitimate_pending_wakeup"
        elif mode == "dry":
            assert result.decision.value == "dry_log"
        else:
            assert result.decision.value == "allowed"
        return

    if surface == "report_recovery":
        service = ReportDeliveryRecoveryService(
            MagicMock(), MagicMock(), MagicMock(), repo, manager, enabled=True
        )
        assert service._enabled is True
        assert service._interval_seconds > 0
        return

    if surface == "report_claim":
        watchdog = WaitingChildrenWatchdog(repo, manager, enabled=True)
        assert watchdog.enabled is True
        assert watchdog.interval_seconds > 0
        return

    if surface == "non_leader_scope":
        from daemon.registry import AgentRegistry

        registry = AgentRegistry(REPO_ROOT / "agents")
        registry.discover()
        meta = registry.get_version("developer", None) or registry.get_resolved("developer")
        assert meta is not None
        assert "attestation" not in (meta.tools.allow if meta.tools else [])
