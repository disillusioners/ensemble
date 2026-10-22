"""Self-tests for the independent fake remote-machine client.

These tests exist so that a failure in ``test_mcps_gateway_protocol.py`` can
be attributed to the **gateway** rather than to the fake client.  They are
pure protocol logic — no daemon, no network, no server.

A failure in this file is a **CLIENT-BUG** (the test harness is at fault),
not a gateway finding.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from tests.integration.mcps_gateway.fake_remote_client import (
    CLIENT_CAPABILITIES,
    PROTOCOL_VERSION,
    FakeRemoteMachine,
    ToolSpec,
)

pytestmark = pytest.mark.integration


def _frame(**kwargs) -> dict:
    base = {"v": PROTOCOL_VERSION, "type": "request"}
    base.update(kwargs)
    return base


class TestEnvelopeValidation:
    """§6.1 — the recorder must catch the deviations it is meant to catch."""

    def test_valid_frame_records_no_violation(self):
        client = FakeRemoteMachine("mach_test1")
        client._request_ids_sent.add("abc")
        client.validate_envelope(
            {"v": "0.1", "type": "response", "id": "abc", "result": {}}
        )
        assert client.violations == []

    def test_missing_v_is_recorded(self):
        client = FakeRemoteMachine("mach_test1")
        client._request_ids_sent.add("abc")
        client.validate_envelope({"type": "response", "id": "abc", "result": {}})
        assert [v.reason for v in client.violations] == ["missing_v"]

    def test_wrong_v_is_recorded(self):
        client = FakeRemoteMachine("mach_test1")
        client._request_ids_sent.add("abc")
        client.validate_envelope(
            {"v": "0.2", "type": "response", "id": "abc", "result": {}}
        )
        assert [v.reason for v in client.violations] == ["bad_v"]
        assert "0.2" in client.violations[0].detail

    def test_bad_type_is_recorded(self):
        client = FakeRemoteMachine("mach_test1")
        client._request_ids_sent.add("abc")
        client.validate_envelope({"v": "0.1", "type": "weird", "id": "abc"})
        assert "bad_type" in [v.reason for v in client.violations]

    def test_orphan_response_is_recorded(self):
        """A response must echo an id the client actually sent (§6.1)."""
        client = FakeRemoteMachine("mach_test1")
        client.validate_envelope(
            {"v": "0.1", "type": "response", "id": "never-sent", "result": {}}
        )
        assert "orphan_response" in [v.reason for v in client.violations]

    def test_echoed_response_is_not_flagged(self):
        client = FakeRemoteMachine("mach_test1")
        client._request_ids_sent.add("sent-1")
        client.validate_envelope({"v": "0.1", "type": "response", "id": "sent-1", "result": {}})
        assert client.violations == []

    def test_missing_id_on_error_is_recorded(self):
        client = FakeRemoteMachine("mach_test1")
        client.validate_envelope({"v": "0.1", "type": "error", "error": {"code": "X"}})
        assert "missing_id" in [v.reason for v in client.violations]


class TestMachineSideExclusivity:
    """§8.3 — the machine itself must refuse a concurrent ``session.begin``."""

    async def test_second_session_begin_is_refused_with_machine_busy(self):
        client = FakeRemoteMachine("mach_test1")
        first = _frame(id="r1", method="session.begin", params={"machine_id": "mach_test1"})
        await client._handle_session_begin(first)
        assert client.active_session_id is not None
        first_session = client.active_session_id

        second = _frame(id="r2", method="session.begin", params={"machine_id": "mach_test1"})
        await client._handle_session_begin(second)

        refusals = [f for f in client.outbound_frames() if f.get("id") == "r2"]
        assert len(refusals) == 1, "the second session.begin must be answered exactly once"
        assert refusals[0]["type"] == "error", (
            f"§8.3: the refusal must be an error, got {refusals[0]!r}"
        )
        assert refusals[0]["error"]["code"] == "MACHINE_BUSY", (
            f"§6.5: the refusal code must be MACHINE_BUSY, got {refusals[0]!r}"
        )
        # The original session must survive the refusal.
        assert client.active_session_id == first_session, (
            "§8.3: the original session must survive a refused second session.begin"
        )
        assert client.busy_refusals == [first_session]


class TestSessionBeginShape:
    """§6.4 — the response shape the client produces."""

    async def test_response_shape_matches_spec(self):
        client = FakeRemoteMachine("mach_test1")
        await client._handle_session_begin(
            _frame(id="r1", method="session.begin", params={"machine_id": "mach_test1"})
        )
        response = client.outbound_frames()[-1]
        assert response["v"] == PROTOCOL_VERSION
        assert response["type"] == "response"
        assert response["id"] == "r1"
        envelope_sid = response["session_id"]
        result_sid = response["result"]["session_id"]
        assert envelope_sid == result_sid, "§6.4: the two session ids must match"
        assert envelope_sid.startswith("sess_")
        assert len(envelope_sid) == len("sess_") + 16, (
            "the minted session id must be 'sess_' + 16 hex chars, got "
            f"{envelope_sid!r}"
        )
        int(envelope_sid[len("sess_"):], 16)  # must be hex
        assert response["result"]["machine_id"] == "mach_test1"
        assert isinstance(response["result"]["expires_at_ms"], int)
        catalog = response["result"]["tools"]
        assert catalog and all("inputSchema" in t for t in catalog)
        assert {t["name"] for t in catalog} == {"echo", "fail_tool", "slow_tool"}


class TestToolsCallShape:
    """§6.6 — the raw MCP result is returned as-is."""

    async def test_echo_tool_result(self):
        client = FakeRemoteMachine("mach_test1")
        await client._handle_tools_call(
            _frame(
                id="r2",
                method="tools/call",
                session_id="sess_x",
                params={"name": "echo", "arguments": {"message": "hi"}},
            )
        )
        response = client.outbound_frames()[-1]
        assert response["type"] == "response"
        assert response["result"] == {
            "content": [{"type": "text", "text": "echo: hi"}],
            "isError": False,
        }

    async def test_fail_tool_returns_is_error_true(self):
        client = FakeRemoteMachine("mach_test1")
        await client._handle_tools_call(
            _frame(
                id="r3",
                method="tools/call",
                session_id="sess_x",
                params={"name": "fail_tool", "arguments": {}},
            )
        )
        response = client.outbound_frames()[-1]
        assert response["result"]["isError"] is True

    async def test_unknown_tool_is_a_typed_error(self):
        client = FakeRemoteMachine("mach_test1")
        await client._handle_tools_call(
            _frame(
                id="r4",
                method="tools/call",
                session_id="sess_x",
                params={"name": "no_such_tool", "arguments": {}},
            )
        )
        response = client.outbound_frames()[-1]
        assert response["type"] == "error"
        assert response["error"]["code"] == "TOOL_NOT_FOUND"


class TestSessionEndShape:
    """§6.8 — the acknowledgement."""

    async def test_ack_releases_the_lock_and_names_the_session(self):
        client = FakeRemoteMachine("mach_test1")
        await client._handle_session_begin(
            _frame(id="r1", method="session.begin", params={"machine_id": "mach_test1"})
        )
        session_id = client.active_session_id
        await client._handle_session_end(
            _frame(
                id="r5",
                method="session.end",
                params={"session_id": session_id},
            )
        )
        response = client.outbound_frames()[-1]
        assert response["type"] == "response"
        assert response["id"] == "r5"
        assert response["result"]["session_id"] == session_id
        assert client.active_session_id is None, (
            "§6.8: ending a session must release the machine-side lock"
        )
        assert client.ended_session_ids == [session_id]


class TestCapabilityMap:
    """§5.3 — the client's own map must match the SPEC table."""

    def test_capability_map_is_a_flat_boolean_map_with_the_spec_keys(self):
        assert set(CLIENT_CAPABILITIES) == {
            "session",
            "tools",
            "notifications",
            "heartbeat",
            "screenshot_delta",
            "multi_session",
            "client_cert",
        }
        assert all(isinstance(v, bool) for v in CLIENT_CAPABILITIES.values())
        for cap in ("session", "tools", "notifications", "heartbeat"):
            assert CLIENT_CAPABILITIES[cap] is True, (
                f"§5.3: {cap} is required in v0.1"
            )
        for cap in ("screenshot_delta", "multi_session", "client_cert"):
            assert CLIENT_CAPABILITIES[cap] is False


class TestCatalogContract:
    """§6.4 / §9.1 — the catalog is forwarded verbatim."""

    def test_input_schemas_are_intact(self):
        catalog = FakeRemoteMachine("m").tools
        by_name = {t.name: t for t in catalog}
        assert by_name["echo"].input_schema["required"] == ["message"]
        assert by_name["echo"].input_schema["type"] == "object"
        assert by_name["echo"].input_schema["additionalProperties"] is False
        assert by_name["echo"].input_schema["properties"]["message"]["type"] == "string"
