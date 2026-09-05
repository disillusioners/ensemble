"""Unit seam: the in-graph nudge is state-only, never a manager delivery.

Mode pinned: ``enforce`` (the deny branch is only meaningful there).
"""

from __future__ import annotations

import asyncio
import inspect
from unittest.mock import MagicMock
from langchain_core.messages import AIMessage, HumanMessage

from daemon.graph import create_attestation_gate_node, should_end_attestation
from daemon.services.attestation_gate import GateSettings, build_gate_config
from daemon.services.attestation_ledger import safe_increment, safe_reset


def test_deny_injects_checkpoint_plain_dict_and_routes_to_agent():
    manager = MagicMock()
    manager.count_pending_children.return_value = 0
    manager.get_queued_or_expected_wakeups.return_value = 0
    manager.enqueue_message = MagicMock()
    manager.revive = MagicMock()
    manager.send_message = MagicMock()

    ledger = MagicMock()
    ledger.increment.return_value = 1
    ledger.reset.return_value = True
    config = build_gate_config("nudge-unit", GateSettings("enforce", 3, 3))
    node = create_attestation_gate_node(
        config,
        GateSettings("enforce", 3, 3),
        manager,
        "nudge-unit",
        denied_count_getter=lambda: 0,
        ledger=ledger,
    )

    result = asyncio.run(
        node(
            {
                "messages": [
                    HumanMessage(content="do it"),
                    AIMessage(content="I am done without attesting"),
                ]
            },
            config={"configurable": {"thread_id": "nudge-unit"}},
        )
    )

    nudge = result["messages"][0]
    assert isinstance(nudge, HumanMessage)
    assert nudge.content == "The work is not yet finished — check current progress and continue."
    assert nudge.additional_kwargs["attestation_nudge"] is True
    assert nudge.additional_kwargs["attestation_nudge_denied_count"] == 1
    assert result["attestation_route"] == "agent"
    assert result["attestation_nudge_denied_count"] == 1
    ledger.increment.assert_called_once()
    assert ledger.increment.call_args.args[0] == "nudge-unit"

    # The MVP's negative assertion lock: no durable delivery or revive seam.
    manager.enqueue_message.assert_not_called()
    manager.revive.assert_not_called()
    manager.send_message.assert_not_called()


def test_route_back_is_a_plain_conditional_edge_not_a_command():
    # Keep this assertion at the routing seam: the gate writes a state hint
    # and the graph's conditional edge decides the next node.
    source = inspect.getsource(should_end_attestation)
    assert "return \"agent\"" in source
    assert "Command" not in source
    assert should_end_attestation({"attestation_route": "agent"}) == "agent"
    assert should_end_attestation({"attestation_route": None}).endswith("__end__")


def test_attested_allow_only_resets_and_dry_skip_writes_are_not_denial_delivery():
    # These are protocol-shape checks; the integration matrix owns the full
    # graph/DB side effects and mode behavior.
    ledger = MagicMock()
    safe_increment(ledger, "inst", "epoch")
    safe_reset(ledger, "inst")
    ledger.increment.assert_called_once_with("inst", "epoch")
    ledger.reset.assert_called_once_with("inst")
