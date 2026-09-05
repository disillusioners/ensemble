"""Regression test — denial-epoch determinism on REAL gate-node replay.

Review must-fix 1 (branch feature/leader-completion-attestation). The
predecessor minted ``str(uuid.uuid4())`` per gate-node invocation, so a
checkpoint re-run of the node on IDENTICAL input state minted a NEW
epoch: the repository's O4 seen-epochs dedup never matched and ONE
logical deny counted TWICE (escalation fired 1-2 denials early). The
era test replayed a hand-fed literal epoch string — a replay the
production caller never produces — so the defect was invisible.

The epoch is now derived deterministically from checkpoint-stable
material (``daemon/graph.py::_derive_denial_epoch`` — uuid5 over
instance id + message count + last-two message fingerprints + the
in-state nudge count).

Acceptance property pinned HERE, against the ACTUAL gate node (not a
hand-fed epoch string): re-invoking the gate node on identical input
state increments ``attestation_denied_count`` EXACTLY once; a genuinely
new logical deny (new AIMessage) increments again under a DIFFERENT
epoch.
"""
from __future__ import annotations

import asyncio
import uuid
from copy import deepcopy
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from sqlalchemy import create_engine, event as sa_event
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel

from daemon.graph import (
    _derive_denial_epoch,
    create_attestation_gate_node,
)
from daemon.repositories.instance.models import Instance
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.services.attestation_gate import GateSettings, build_gate_config


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures — file-backed SQLite per the dispatch testing discipline
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def engine(tmp_path: Path):
    """tmp_path + NullPool + WAL + busy_timeout (mirrors the ledger tests).

    No migration chain on SQLite (fresh-SQLite boot trap); the
    attestation columns land via ``SQLModel.metadata.create_all()``.
    """
    db_path = tmp_path / "attestation_epoch_replay.sqlite"
    eng = create_engine(
        f"sqlite:///{db_path}",
        connect_args={
            "check_same_thread": False,
            "timeout": 30,
        },
        poolclass=NullPool,
    )

    @sa_event.listens_for(eng, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()

    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def repo(engine):
    return SQLModelInstanceRepository(engine)


LEADER_ID = "epoch-replay-leader"

#: The would-be-END input state: a HumanMessage instruction and the
#: leader's un-attested AIMessage. Built fresh (deepcopy) per invocation
#: so a test cannot accidentally share mutable message objects across
#: "replays" — a real checkpoint replay rehydrates EQUAL-BUT-DISTINCT
#: message objects, which is the scenario under test.
BASE_MESSAGES = [
    HumanMessage(content="ship the feature", id="hm-1"),
    AIMessage(content="done — final report follows", id="aim-1"),
]


def _build_node(repo: SQLModelInstanceRepository):
    manager = MagicMock()
    manager.count_pending_children.return_value = 0
    manager.get_queued_or_expected_wakeups.return_value = 0
    settings = GateSettings("enforce", 3, 3)
    config = build_gate_config(LEADER_ID, settings)
    return create_attestation_gate_node(
        config,
        settings,
        manager,
        LEADER_ID,
        denied_count_getter=lambda: repo.get_attestation_denied_count(LEADER_ID),
        ledger=repo,
    )


def _state() -> dict:
    return {"messages": deepcopy(BASE_MESSAGES)}


def _invoke(node, state: dict):
    return asyncio.run(
        node(deepcopy(state), config={"configurable": {"thread_id": LEADER_ID}})
    )


def _read_seen_epochs(repo: SQLModelInstanceRepository) -> list[str]:
    from sqlmodel import Session

    with Session(repo.engine) as session:
        inst = session.get(Instance, LEADER_ID)
        meta = (inst.instance_metadata or {}).get("attestation:denial_epochs")
        return [str(e) for e in meta] if meta else []


# ─────────────────────────────────────────────────────────────────────────────
# THE acceptance test — replay on identical input state counts ONCE
# ─────────────────────────────────────────────────────────────────────────────


class TestGateNodeReplayIdempotency:
    def test_replay_on_identical_state_increments_exactly_once(self, repo):
        """Re-invoke the ACTUAL gate node on identical input state.

        Checkpoint re-run semantics: same messages, same channel state,
        same thread_id. The deterministic epoch must make the O4 dedup
        engage — ``attestation_denied_count`` increments EXACTLY once
        and the seen-epochs ledger holds exactly ONE entry, no matter
        how many times the replay re-enters the node.
        """
        repo.create(instance_id=LEADER_ID, agent_id="leader", agent_dir="./agents/leader")
        node = _build_node(repo)

        results = [_invoke(node, _state()) for _ in range(3)]

        # THE acceptance assertion: one logical deny → one count.
        assert repo.get_attestation_denied_count(LEADER_ID) == 1
        # Dedup ENGAGED (not a count coincidence): exactly one epoch was
        # ever recorded for the three invocations.
        epochs = _read_seen_epochs(repo)
        assert len(epochs) == 1
        # Every replay still emits the in-graph nudge (the pre-crash
        # output was never committed, so the replay IS the delivery) and
        # claims the TRUE committed count (1), not a phantom increment.
        for result in results:
            assert result["attestation_route"] == "agent"
            assert result["attestation_nudge_denied_count"] == 1
            nudge = result["messages"][0]
            assert nudge.additional_kwargs["attestation_nudge"] is True
            assert nudge.additional_kwargs["attestation_nudge_denied_count"] == 1

    def test_new_logical_deny_after_replay_yields_new_epoch(self, repo):
        """A genuinely NEW deny (new AIMessage) must count as a new deny."""
        repo.create(instance_id=LEADER_ID, agent_id="leader", agent_dir="./agents/leader")
        node = _build_node(repo)

        # Deny #1 — plus two no-op replays of it.
        for _ in range(3):
            _invoke(node, _state())
        assert repo.get_attestation_denied_count(LEADER_ID) == 1

        # The leader tried again after the nudge: a NEW AIMessage (new
        # id AND new content) rides on top of the injected nudge.
        replayed_state = _state()
        replayed_state["messages"].append(
            HumanMessage(
                content="The work is not yet finished — check current "
                "progress and continue.",
                id="nudge-1",
                additional_kwargs={"attestation_nudge": True},
            )
        )
        replayed_state["messages"].append(
            AIMessage(content="still done, honest", id="aim-2")
        )
        result = _invoke(node, replayed_state)

        assert repo.get_attestation_denied_count(LEADER_ID) == 2
        assert result["attestation_nudge_denied_count"] == 2
        # Two DISTINCT epochs now recorded — one per logical deny.
        assert len(_read_seen_epochs(repo)) == 2

    def test_epoch_survives_node_rebuild(self, repo):
        """The epoch is state-derived, not closure-derived.

        A daemon restart rebuilds the gate node from the factory; the
        replay must STILL dedup against the pre-restart epoch.
        """
        repo.create(instance_id=LEADER_ID, agent_id="leader", agent_dir="./agents/leader")
        first_node = _build_node(repo)
        _invoke(first_node, _state())
        assert repo.get_attestation_denied_count(LEADER_ID) == 1

        rebuilt_node = _build_node(repo)
        _invoke(rebuilt_node, _state())
        assert repo.get_attestation_denied_count(LEADER_ID) == 1
        assert len(_read_seen_epochs(repo)) == 1


# ─────────────────────────────────────────────────────────────────────────────
# The derivation function's determinism properties (unit level)
# ─────────────────────────────────────────────────────────────────────────────


class TestDeriveDenialEpochProperties:
    def test_identical_state_yields_identical_epoch(self):
        state = {"attestation_nudge_denied_count": None}
        first = _derive_denial_epoch(LEADER_ID, deepcopy(BASE_MESSAGES), state)
        second = _derive_denial_epoch(LEADER_ID, deepcopy(BASE_MESSAGES), state)
        assert first == second
        # A uuid5 output is a well-formed UUID string (dedup-key shape).
        assert str(uuid.UUID(first)) == first

    def test_new_last_message_yields_different_epoch(self):
        state = {"attestation_nudge_denied_count": None}
        base = _derive_denial_epoch(LEADER_ID, deepcopy(BASE_MESSAGES), state)
        mutated = deepcopy(BASE_MESSAGES)
        mutated[-1] = AIMessage(content="done — final report follows", id="aim-2")
        assert _derive_denial_epoch(LEADER_ID, mutated, state) != base

        mutated_content = deepcopy(BASE_MESSAGES)
        mutated_content[-1] = AIMessage(content="different text", id="aim-1")
        assert _derive_denial_epoch(LEADER_ID, mutated_content, state) != base

    def test_different_nudge_channel_yields_different_epoch(self):
        messages = deepcopy(BASE_MESSAGES)
        assert _derive_denial_epoch(
            LEADER_ID, messages, {"attestation_nudge_denied_count": 1}
        ) != _derive_denial_epoch(
            LEADER_ID, messages, {"attestation_nudge_denied_count": 2}
        )

    def test_different_instance_yields_different_epoch(self):
        messages = deepcopy(BASE_MESSAGES)
        state = {"attestation_nudge_denied_count": None}
        assert (
            _derive_denial_epoch("inst-a", messages, state)
            != _derive_denial_epoch("inst-b", messages, state)
        )

    def test_content_only_messages_are_stable(self):
        """Hand-built states without ids still fingerprint stably."""
        id_less = [HumanMessage(content="go"), AIMessage(content="done")]
        state = {"attestation_nudge_denied_count": None}
        assert _derive_denial_epoch(
            LEADER_ID, deepcopy(id_less), state
        ) == _derive_denial_epoch(LEADER_ID, deepcopy(id_less), state)


# ─────────────────────────────────────────────────────────────────────────────
# Review fix 4a — absent build-time id falls back to the RUN-TIME read
# (lives here because this file owns the real-repo gate-node fixtures)
# ─────────────────────────────────────────────────────────────────────────────


class TestRuntimeIdGetterFallback:
    def test_missing_build_time_id_reads_runtime_count_not_zero(self, repo):
        """Read path mirrors the write path's id resolution.

        With the build-time thread_id absent, the wiring passes
        ``denied_count_getter=None``. The predecessor defaulted the read
        to 0 while the write path still resolved the run-time thread_id
        — read and write disagreed (every decision evaluated with 0).
        The node now falls back to a run-time ledger read: pre-seed the
        counter to 2 and the deny must decide from 2 (nudge claims 3),
        not from 0 (which would claim 1).
        """
        rid = "runtime-id-leader"
        repo.create(instance_id=rid, agent_id="leader", agent_dir="./agents/leader")
        repo.increment_attestation_denied_count(rid, denial_epoch="seed-1")
        repo.increment_attestation_denied_count(rid, denial_epoch="seed-2")

        manager = MagicMock()
        manager.count_pending_children.return_value = 0
        manager.get_queued_or_expected_wakeups.return_value = 0
        settings = GateSettings("enforce", 3, 3)
        config = build_gate_config(None, settings)  # build-time id ABSENT
        node = create_attestation_gate_node(
            config,
            settings,
            manager,
            None,  # build-time id absent — run-time thread_id is the truth
            denied_count_getter=None,
            ledger=repo,
        )

        state = {
            "messages": [
                HumanMessage(content="go", id="hm-r"),
                AIMessage(content="done, no attestation", id="aim-r"),
            ]
        }
        result = asyncio.run(
            node(deepcopy(state), config={"configurable": {"thread_id": rid}})
        )

        # deny decided from the TRUE runtime count (2): bound 3 → 2+1 <= 3
        # → DENIED, counter lands at 3, and the nudge claims 3.
        assert repo.get_attestation_denied_count(rid) == 3
        assert result["attestation_route"] == "agent"
        assert result["attestation_nudge_denied_count"] == 3
