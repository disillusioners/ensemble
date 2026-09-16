"""FIX-3 acceptance: stable attestation-nudge id — consecutive denies
supersede to ONE block (incident 6a0d60c9).

Incident 6a0d60c9: 115 deny+nudge injections over 27 min — with the
fresh ``uuid4`` per nudge, every injection appended a NEW nudge block
to the leader's context. Post-FIX-3 the nudge carries the stable
per-instance id ``attestation_nudge:{instance_id}`` (minted via
``_stable_id_for`` — the canonical id-format table, mirroring the
``completion_check_note:{instance_id}`` F1 Shape A contract), so
LangGraph's ``add_messages`` reducer SUPERSEDES the prior block in
place: consecutive denies collapse to ONE nudge block in the channel.

ALL deny producers funnel through the single nudge construction site
in ``daemon/graph.py`` — the plain ``decide()`` deny AND the
marker-path (a)/(d) allow→deny conversions — so all three mint the
SAME id and supersede each other. ``additional_kwargs``
(``attestation_nudge``, ``injected_message``,
``attestation_nudge_denied_count``) are unchanged.

Verified against the REAL ``langgraph.graph.message.add_messages``
(this conftest does NOT mock langgraph — pinned on langgraph 1.0.9
upsert semantics, ``merged[existing_idx] = m``).
"""

from __future__ import annotations

import asyncio
import importlib
import sys
from typing import Any
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from daemon.graph import ATTESTATION_NUDGE_TEXT, create_attestation_gate_node
from daemon.services import attestation_report_judge as judge_mod
from daemon.services.attestation_gate import (
    GateSettings,
    build_gate_config,
)
from daemon.services.attestation_judge_resolver import (
    reset_llm_judge_resolver_for_tests,
)
from daemon.services.attestation_resolver import (
    reset_attestation_resolver_for_tests,
)


# ─────────────────────────────────────────────────────────────────────────────
# Real-langgraph swap (module-scoped; restores conftest mocks after).
# Mirrors ``tests/unit/test_attestation_marker_supersede_lca.py`` — the root
# conftest installs mock langgraph modules session-wide, so the REAL
# ``add_messages`` must be evicted-in at setup and the mocks restored at
# teardown for neighbouring files.
# ─────────────────────────────────────────────────────────────────────────────

_MOCKED_LANGGRAPH_KEYS = [
    "langgraph",
    "langgraph.graph",
    "langgraph.graph.state",
    "langgraph.graph.message",
    "langgraph.prebuilt",
    "langgraph.constants",
    "langgraph.checkpoint",
    "langgraph.checkpoint.memory",
    "langgraph.checkpoint.sqlite",
    "langgraph.checkpoint.sqlite.aio",
]

_saved_mocks: dict[str, Any] = {}
_real_add_messages: Any = None


def setup_module(module: Any) -> None:
    global _saved_mocks, _real_add_messages

    for key in _MOCKED_LANGGRAPH_KEYS:
        if key in sys.modules:
            _saved_mocks[key] = sys.modules[key]
            del sys.modules[key]
    for key in [k for k in sys.modules if k.startswith("langgraph")]:
        del sys.modules[key]

    lg_message = importlib.import_module("langgraph.graph.message")
    _real_add_messages = lg_message.add_messages
    # Sanity: the REAL function, not a conftest MagicMock.
    assert not isinstance(_real_add_messages, type(MagicMock())), (
        "langgraph.graph.message.add_messages MUST be the REAL function"
    )


def teardown_module(module: Any) -> None:
    global _saved_mocks
    for key in [k for k in sys.modules if k.startswith("langgraph")]:
        del sys.modules[key]
    sys.modules.update(_saved_mocks)
    _saved_mocks = {}


@pytest.fixture(autouse=True)
def _reset_resolvers(monkeypatch):
    monkeypatch.delenv(
        "ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED", raising=False
    )
    reset_attestation_resolver_for_tests()
    reset_llm_judge_resolver_for_tests()
    yield
    reset_attestation_resolver_for_tests()
    reset_llm_judge_resolver_for_tests()


async def _judge_incomplete(config, user_payload, *, timeout_s):
    return (
        '{"is_complete_report": false, "reason": "mid-work"}',
        "fake-quick",
    )


def _deny_node(instance_id: str, denied_count: int):
    """Gate node wired for the marker-path-(a) deny shape (the
    incident's producer)."""
    manager = MagicMock()
    manager.count_pending_children.return_value = 0
    manager.get_queued_or_expected_wakeups.return_value = 0
    manager.count_live_descendants.return_value = 0
    manager.count_busy_descendants.return_value = 0
    ledger = MagicMock()
    # The nudge kwargs carry the ledger's committed count (the
    # safe_increment return) — mirror the climbing counter here.
    ledger.increment.side_effect = (
        lambda instance_id, denial_epoch, **_: denied_count + 1
    )
    ledger.reset.return_value = True
    ledger.set_escalated_and_reset.return_value = True
    settings = GateSettings("enforce", 3, 3)
    config = build_gate_config(instance_id, settings, llm_judge_enabled=True)
    node = create_attestation_gate_node(
        config,
        settings,
        manager,
        instance_id,
        denied_count_getter=(lambda: denied_count),
        ledger=ledger,
    )
    return node


def _produce_nudge(instance_id: str, denied_count: int) -> HumanMessage:
    """Run one deny evaluation through the production closure and
    return the injected nudge.

    LCA Stage-2 flip re-contract (2026-09-16): the mission is
    DELEGATED — the unified predicate's D10 meta-bypass exempts
    non-delegated missions from the deny family entirely. The fused
    judge's conservative not-complete/error verdict leaves the decide()
    DENIED in place and the SAME nudge machinery runs (the FIX-3
    stable-id contract under test)."""
    delegation_ai = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "send_message",
                "args": {"target": "child-id"},
                "id": "c1",
            }
        ],
    )
    node = _deny_node(instance_id, denied_count)
    result = asyncio.run(
        node(
            {
                "messages": [
                    HumanMessage(content="please advise"),
                    delegation_ai,
                    AIMessage(
                        content="Ending turn, awaiting your go/no-go."
                    ),
                ]
            },
            config={"configurable": {"thread_id": instance_id}},
        )
    )
    assert "messages" in result, "expected the deny path to inject a nudge"
    return result["messages"][0]


def test_nudge_mints_stable_per_instance_id():
    """The nudge id is ``attestation_nudge:{instance_id}`` — not uuid4.

    Both marker paths funnel into the SAME construction site, so the
    marker-path (a)/(d) nudges and the decide()-path nudges mint the
    SAME id by construction (this test exercises the marker-path (a)
    producer — the incident's shape).
    """
    instance_id = "fix3-stable-id"
    nudge_1 = _produce_nudge(instance_id, 0)
    nudge_2 = _produce_nudge(instance_id, 1)

    assert nudge_1.id == f"attestation_nudge:{instance_id}", (
        f"FIX-3: nudge id MUST be the stable per-instance id — got "
        f"{nudge_1.id!r}"
    )
    assert nudge_2.id == nudge_1.id, (
        "FIX-3: consecutive denies MUST mint the SAME stable id so the "
        "add_messages reducer can supersede"
    )
    # Content + kwargs contract unchanged.
    assert nudge_1.content == ATTESTATION_NUDGE_TEXT
    assert nudge_1.additional_kwargs.get("attestation_nudge") is True
    assert nudge_1.additional_kwargs.get("injected_message") is True
    assert (
        nudge_1.additional_kwargs.get("attestation_nudge_denied_count") == 1
    )
    assert (
        nudge_2.additional_kwargs.get("attestation_nudge_denied_count") == 2
    ), "the superseding block carries the LATEST deny's counter stamp"


def test_consecutive_denies_supersede_to_one_block_real_add_messages():
    """REAL ``add_messages``: N consecutive deny nudges → ONE block.

    Mirrors the ``completion_check_note`` supersede pin
    (``tests/unit/test_attestation_marker_supersede_lca.py``) on the
    nudge: the reducer's upsert rule (same id → right replaces left)
    collapses the incident's unbounded nudge accumulation.
    """
    instance_id = "fix3-supersede"
    # Counts stay BELOW the bound (bound 3 ⇒ denied_counts 0..2):
    # three consecutive deny events. denied_count=3 would hit the
    # FIX-1 bound escalation (terminal, no nudge) — that interaction
    # is pinned in test_attestation_marker_bound_enforcement_lca.py.
    nudges = [
        _produce_nudge(instance_id, denied_count)
        for denied_count in (0, 1, 2)
    ]

    # Fold the three deny cycles through the real reducer (left fold —
    # the same shape a checkpoint channel sees across three turns).
    channel: list = []
    for nudge in nudges:
        channel = _real_add_messages(channel, [nudge])

    assert len(channel) == 1, (
        f"FIX-3: three consecutive deny nudges MUST supersede to ONE "
        f"block in the channel — got {len(channel)}"
    )
    assert channel[0].id == f"attestation_nudge:{instance_id}"
    assert channel[0].content == ATTESTATION_NUDGE_TEXT
    # The surviving block carries the LAST deny's counter stamp (3).
    assert (
        channel[0].additional_kwargs.get("attestation_nudge_denied_count")
        == 3
    )


def test_distinct_instances_do_not_supersede_each_other():
    """Supersede granularity matches the instance: two leaders' nudges
    are distinct blocks (the id suffix IS the owning instance)."""
    nudge_a = _produce_nudge("fix3-instance-a", 0)
    nudge_b = _produce_nudge("fix3-instance-b", 0)
    assert nudge_a.id != nudge_b.id

    merged = _real_add_messages([nudge_a], [nudge_b])
    assert len(merged) == 2, (
        "different instances' nudges MUST NOT supersede each other"
    )
