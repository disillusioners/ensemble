"""D2(a) — the delegation-census widening (completion-subsystem Phase 1).

Defect D2 (premature settle): the leader's conditional-attestation
census counted exactly ONE delegation tool — ``send_message``. A leader
(or an agent acting for it) that commissioned work with
``instance_nudge`` was INVISIBLE to the census: ``attestation_required``
stayed False and the mission settled while the nudged worker was still
running. That is the demonstrated fd302262 shape (the mission settled
5m06s early).

D2(a) widens the census to a SET of delegation tool names —
``send_message`` AND ``instance_nudge`` — and the counting loop from
name-EQUALITY to MEMBERSHIP.

``spawn_instance`` stays EXCLUDED: an instantiation is inert by design
(the child does nothing until it is messaged), so spawn alone does NOT
make the mission completion-conditional. The user spec pins this and
the scanner docstring carries the pin.

NO LOG-SCHEMA CHANGE: the canonical gate log row keeps its 17 fields
and ``attestation_required`` stays at #11 — only the VALUE's semantics
change, in the intended direction (more evaluation).

MUTATION NOTE: the behavioural pin (the gate's ``attestation_required``
for an ``instance_nudge``-only mission) is proven to go RED when the
census widening is reverted — see the commit message.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from daemon.services.attestation_gate import (
    CANONICAL_LOG_SCHEMA_FIELDS,
    Decision,
    GateSettings,
    evaluate,
)
from daemon.services.attestation_scanner import (
    DEFAULT_DELEGATION_TOOL_NAME,
    DEFAULT_DELEGATION_TOOL_NAMES,
    scan_delegation_after_last_user,
)


def _real(content: str = "do the work") -> HumanMessage:
    return HumanMessage(content=content)


def _ai(tool_name: str, tc_id: str = "t1") -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": tool_name, "args": {}, "id": tc_id}],
    )


def _make_manager(pending=0, wakeups=0, live_descendants=0):
    m = MagicMock()
    m.count_pending_children = MagicMock(return_value=pending)
    m.get_queued_or_expected_wakeups = MagicMock(return_value=wakeups)
    m.count_live_descendants = MagicMock(return_value=live_descendants)
    m.enqueue_message = MagicMock()
    m.revive = MagicMock()
    m.send_message = MagicMock()
    return m


class TestCensusConstants:
    def test_the_delegation_tool_set_has_the_exact_membership(self):
        """EXACT-membership pin (fork-prevention)."""
        assert DEFAULT_DELEGATION_TOOL_NAMES == frozenset(
            {"send_message", "instance_nudge"}
        )

    def test_the_legacy_alias_is_kept(self):
        """The old single-name constant survives as an alias — every
        existing consumer keeps working."""
        assert DEFAULT_DELEGATION_TOOL_NAME == "send_message"
        assert DEFAULT_DELEGATION_TOOL_NAME in DEFAULT_DELEGATION_TOOL_NAMES

    def test_spawn_instance_is_not_a_delegation_tool(self):
        """Spawn is INERT by design — it must NOT make the mission
        completion-conditional."""
        assert "spawn_instance" not in DEFAULT_DELEGATION_TOOL_NAMES


class TestScannerMembership:
    def test_send_message_counts(self):
        msgs = [_real(), _ai("send_message")]
        result = scan_delegation_after_last_user(msgs)
        assert result.delegation_since_last_user is True
        assert result.delegation_tool_call_total == 1

    def test_instance_nudge_counts(self):
        """THE D2(a) POINT — a nudge is a commissioning act."""
        msgs = [_real(), _ai("instance_nudge")]
        result = scan_delegation_after_last_user(msgs)
        assert result.delegation_since_last_user is True
        assert result.first_delegation_after_last_user_index == 1
        assert result.delegation_tool_call_total == 1

    def test_spawn_instance_does_not_count(self):
        msgs = [_real(), _ai("spawn_instance")]
        result = scan_delegation_after_last_user(msgs)
        assert result.delegation_since_last_user is False
        assert result.delegation_tool_call_total == 0

    def test_a_single_name_override_narrows_the_set(self):
        """The legacy seam — an explicit single name REPLACES the
        default set (it does not union with it)."""
        msgs = [_real(), _ai("custom_send")]
        result = scan_delegation_after_last_user(
            msgs, delegation_tool_name="custom_send"
        )
        assert result.delegation_since_last_user is True
        # The DEFAULT now includes instance_nudge — but NOT custom_send.
        default_result = scan_delegation_after_last_user(msgs)
        assert default_result.delegation_since_last_user is False

    def test_an_iterable_override_is_a_membership_set(self):
        msgs = [_real(), _ai("custom_send")]
        result = scan_delegation_after_last_user(
            msgs, delegation_tool_name={"custom_send", "instance_nudge"}
        )
        assert result.delegation_since_last_user is True

    def test_the_scan_still_stops_at_the_last_real_user_message(self):
        """A stale pre-mission delegation must not re-arm the flag."""
        msgs = [
            _ai("instance_nudge", "pre-1"),
            _real(),
            AIMessage(content="just prose", id="a-2"),
        ]
        result = scan_delegation_after_last_user(msgs)
        assert result.delegation_since_last_user is False
        assert result.last_real_user_index == 1


class TestGateCensusWiring:
    """The gate-level behavioural pin.

    A leader whose ONLY delegation was an ``instance_nudge`` is now
    completion-CONDITIONAL: ``attestation_required`` is True. Before
    D2(a) it was False — the mission settled un-attested while the
    nudged worker was still running.
    """

    def test_a_nudge_only_mission_is_completion_conditional(self):
        msgs = [_real(), _ai("instance_nudge"), AIMessage(content="done", id="a-2")]
        result = evaluate(
            "inst-nudge",
            0,
            msgs,
            GateSettings("enforce", 3, 3),
            _make_manager(),
        )
        assert result.attestation_required is True
        assert result.decision is not Decision.ALLOWED

    def test_a_spawn_only_mission_is_not_completion_conditional(self):
        """The NEGATIVE half — spawn alone stays inert."""
        msgs = [_real(), _ai("spawn_instance"), AIMessage(content="done", id="a-2")]
        result = evaluate(
            "inst-spawn",
            0,
            msgs,
            GateSettings("enforce", 3, 3),
            _make_manager(),
        )
        assert result.attestation_required is False
        assert result.decision is Decision.ALLOWED

    def test_a_plain_mission_is_not_completion_conditional(self):
        msgs = [_real(), AIMessage(content="just prose", id="a-2")]
        result = evaluate(
            "inst-plain",
            0,
            msgs,
            GateSettings("enforce", 3, 3),
            _make_manager(),
        )
        assert result.attestation_required is False
        assert result.decision is Decision.ALLOWED


class TestLogSchemaUnchanged:
    def test_the_canonical_log_row_keeps_its_17_fields(self):
        """NO field/format change — only the VALUE semantics change."""
        assert len(CANONICAL_LOG_SCHEMA_FIELDS) == 17

    def test_attestation_required_is_field_eleven(self):
        assert CANONICAL_LOG_SCHEMA_FIELDS[10] == "attestation_required"
