"""Integration tests — the real gateway against an INDEPENDENT fake client.

THE FAKE CLIENT IS NOT MINE
---------------------------

``tests/integration/mcps_gateway/fake_remote_client.py`` was transcribed
from ``mcps_client/SPEC.md`` v0.1 ALONE by a separate tester instance. It
imports NOTHING from ``daemon.*`` — every wire shape in it is a
transcription of the specification, not a mirror of the implementation.
That is the whole point: a mirror of the implementation only proves
self-consistency. This suite is the INTEROP PROOF — the gateway must
satisfy a client that was written from the document alone.

``tests/integration/mcps_gateway/harness.py`` is the only module allowed
to import ``daemon.*``: it boots the real FastAPI application on an
EPHEMERAL port and tears the server down afterwards (no listener may
survive a test).

WHAT THE FAKE CLIENT EXERCISES
------------------------------

* §3.1  — the credential on the UPGRADE request; a refused upgrade
          exchanges no protocol message.
* §4.1  — the bearer enrollment token.
* §5.1/§5.2/§5.4 — the hello exchange, the version negotiation and the
          three advertised heartbeat parameters.
* §5.3  — the capability map (the fake client demands
          ``{session, tools, heartbeat}``).
* §6.1  — the envelope: ``v`` / ``type`` / ``id`` / ``session_id``.
* §6.3  — the seven methods; the center is the REQUESTOR of
          ``session.begin`` / ``tools/list`` / ``tools/call`` /
          ``session.end``.
* §6.4  — ``session.begin`` → the session id + the tool catalog.
* §6.5  — the typed error codes.
* §6.6  — ``tools/call``: the MCP parameter object unchanged, the raw
          MCP result returned as-is.
* §6.8  — ``session.end`` keeps the socket open; heartbeat loss kills
          the session.
* §8.3  — one active session per machine.

WHAT IT DOES NOT EXERCISE
-------------------------

* §3.2 — local mode (plain MCP on the loopback): the center does not
  speak it; the local mode belongs to the wrapper.
* §4.2 / §4.3 — the mTLS target tier, the certificate pin, the wrapper's
  internal policy layer: v0.1 is the POC bearer tier, and the pin lives
  in the CLIENT.
* §5.3 ``notifications`` — the center relays no notification in v0.1, so
  there is no notification traffic to exercise (the client relays none
  either).
* §6.7 ``PAYLOAD_TOO_LARGE`` — the payload limits are enforced by the
  CLIENT (§6.7 is written for the frame the client produces); the center
  never produces a large frame.
* §8.4 — the audit is asserted from the center's own sink, not from the
  client (the client keeps no audit of the center's traffic).
* §9 — the computer-use specifics (``screenshot`` payloads, the
  ``cu_`` prefix): the catalog is generic MCP in this phase.
* §10 — the conformance kit: it does not exist yet.
"""

from __future__ import annotations

import asyncio

import pytest

from tests.integration.mcps_gateway.fake_remote_client import FakeRemoteMachine
from tests.integration.mcps_gateway.harness import GatewayEnv, boot_gateway

#: The env of every test in this file — fast heartbeats so the tests stay
#: well inside the 30 s per-test budget.
FAST_ENV = GatewayEnv(
    enabled=True,
    tokens=("tok-alpha", "tok-beta"),
    heartbeat_interval_ms=200,
    heartbeat_timeout_ms=600,
    client_dead_detect_ms=600,
    command_timeout_ms=1500,
    session_ttl_ms=60_000,
    session_idle_timeout_ms=30_000,
)


# ─────────────────────────────────────────────────────────────────────────────
# The route and the kill switch
# ─────────────────────────────────────────────────────────────────────────────


class TestRouteRegistration:
    async def test_the_route_is_registered_when_the_gateway_is_enabled(self):
        async with boot_gateway(FAST_ENV) as booted:
            assert "/api/mcps-gateway/ws" in booted.ws_route_paths

    async def test_the_route_is_absent_when_the_gateway_is_disabled(self):
        """DEFAULT OFF — the route does not EXIST, it is not "registered
        but rejecting"."""
        env = GatewayEnv(**{**FAST_ENV.__dict__, "enabled": False})
        async with boot_gateway(env) as booted:
            assert "/api/mcps-gateway/ws" not in booted.ws_route_paths

    async def test_the_disabled_route_answers_404(self):
        import httpx

        env = GatewayEnv(**{**FAST_ENV.__dict__, "enabled": False})
        async with boot_gateway(env) as booted:
            async with httpx.AsyncClient(base_url=booted.http_base) as client:
                response = await client.get("/api/mcps-gateway/ws")
            assert response.status_code == 404


# ─────────────────────────────────────────────────────────────────────────────
# The upgrade authentication (§3.1 / §4.1)
# ─────────────────────────────────────────────────────────────────────────────


class TestUpgradeAuth:
    async def test_a_known_token_is_accepted(self):
        async with boot_gateway(FAST_ENV) as booted:
            machine = FakeRemoteMachine(machine_id="mach_test1")
            await machine.connect(booted.ws_url, "tok-alpha")
            try:
                assert machine.closed.is_set() is False
            finally:
                await machine.close()

    async def test_an_unknown_token_is_refused_with_401(self):
        """401 — never 403: the shipped client treats a 403 as transient
        and would reconnect forever."""
        from websockets.exceptions import InvalidStatus

        async with boot_gateway(FAST_ENV) as booted:
            machine = FakeRemoteMachine(machine_id="mach_test1")
            with pytest.raises(InvalidStatus) as excinfo:
                await machine.connect(booted.ws_url, "not-a-token")
            status = getattr(getattr(excinfo.value, "response", None), "status_code", None)
            assert status == 401
            # No protocol frame was exchanged.
            assert machine.inbound_frames() == []

    async def test_a_missing_token_is_refused_with_401(self):
        from websockets.exceptions import InvalidStatus

        async with boot_gateway(FAST_ENV) as booted:
            machine = FakeRemoteMachine(machine_id="mach_test1")
            with pytest.raises(InvalidStatus) as excinfo:
                await machine.connect(booted.ws_url, None)
            status = getattr(getattr(excinfo.value, "response", None), "status_code", None)
            assert status == 401

    async def test_no_configured_token_refuses_everybody(self):
        from websockets.exceptions import InvalidStatus

        env = GatewayEnv(**{**FAST_ENV.__dict__, "tokens": ()})
        async with boot_gateway(env) as booted:
            machine = FakeRemoteMachine(machine_id="mach_test1")
            with pytest.raises(InvalidStatus):
                await machine.connect(booted.ws_url, "tok-alpha")


# ─────────────────────────────────────────────────────────────────────────────
# The hello exchange (§5)
# ─────────────────────────────────────────────────────────────────────────────


class TestHello:
    async def test_the_hello_exchange_completes(self):
        async with boot_gateway(FAST_ENV) as booted:
            machine = FakeRemoteMachine(machine_id="mach_test1")
            await machine.connect(booted.ws_url, "tok-alpha")
            try:
                response = await machine.send_hello()
                assert response["type"] == "response"
                result = response["result"]
                assert result["protocol_version"] == "0.1"
                assert result["server"]["name"]
                # ALL THREE heartbeat parameters — a partial triple is
                # fatal on the client side.
                assert result["heartbeat_interval_ms"] == 200
                assert result["heartbeat_timeout_ms"] == 600
                assert result["client_dead_detect_ms"] == 600
                assert result["capabilities"]["session"] is True
                assert result["capabilities"]["tools"] is True
                assert result["capabilities"]["heartbeat"] is True
                # The client may not be told a lie about notifications.
                assert result["capabilities"]["notifications"] is False
                # No envelope violation was observed by the client.
                assert machine.violations == [], machine.violations_dump()
            finally:
                await machine.close()

    async def test_a_machine_outside_the_allowlist_is_refused_in_band(self):
        env = GatewayEnv(**{**FAST_ENV.__dict__, "allowed_machines": ("mach_other",)})
        async with boot_gateway(env) as booted:
            machine = FakeRemoteMachine(machine_id="mach_test1")
            await machine.connect(booted.ws_url, "tok-alpha")
            try:
                response = await machine.send_hello()
                assert response["type"] == "error"
                assert response["error"]["code"] == "MACHINE_NOT_ENROLLED"
                assert response["error"]["data"]["machine_id"] == "mach_test1"
            finally:
                await machine.close()

    async def test_an_unsupported_protocol_version_is_refused_in_band(self):
        async with boot_gateway(FAST_ENV) as booted:
            machine = FakeRemoteMachine(
                machine_id="mach_test1", protocol_version="0.9"
            )
            await machine.connect(booted.ws_url, "tok-alpha")
            try:
                response = await machine.send_hello()
                assert response["type"] == "error"
                assert response["error"]["code"] == "PROTOCOL_VERSION_UNSUPPORTED"
                assert response["error"]["data"]["supported"] == ["0.1"]
            finally:
                await machine.close()

    async def test_a_client_without_the_required_capabilities_is_refused(self):
        async with boot_gateway(FAST_ENV) as booted:
            machine = FakeRemoteMachine(
                machine_id="mach_test1",
                capabilities={
                    "session": True,
                    "tools": True,
                    "notifications": False,
                    "heartbeat": False,
                },
            )
            await machine.connect(booted.ws_url, "tok-alpha")
            try:
                response = await machine.send_hello()
                assert response["type"] == "error"
                assert response["error"]["code"] == "CAPABILITY_MISSING"
                assert "heartbeat" in response["error"]["data"]["missing"]
            finally:
                await machine.close()


# ─────────────────────────────────────────────────────────────────────────────
# The session lifecycle (§6.4 / §6.6 / §6.8 / §8.3)
# ─────────────────────────────────────────────────────────────────────────────


class TestSessionFlow:
    async def _hello_machine(self, booted, machine_id="mach_test1"):
        machine = FakeRemoteMachine(machine_id=machine_id)
        await machine.connect(booted.ws_url, "tok-alpha")
        await machine.send_hello()
        return machine

    async def test_the_full_session_flow(self):
        async with boot_gateway(FAST_ENV) as booted:
            machine = await self._hello_machine(booted)
            try:
                # ── connect: the CENTER requests the session ──────────
                opened = await booted.service.connect(
                    machine_id="mach_test1", agent_id="ari"
                )
                assert opened["session_id"]
                assert opened["machine_id"] == "mach_test1"
                names = {t["name"] for t in opened["tools"]}
                assert {"echo", "fail_tool", "slow_tool"} <= names
                # The catalog is forwarded verbatim (§6.4).
                assert all("inputSchema" in t for t in opened["tools"])

                # ── command: one tools/call, raw result ──────────────
                result = await booted.service.command(
                    agent_id="ari", tool="echo", arguments={"message": "hi"}
                )
                assert result == {
                    "content": [{"type": "text", "text": "echo: hi"}],
                    "isError": False,
                }

                # ── disconnect: the session ends, the socket stays ────
                ended = await booted.service.disconnect(agent_id="ari")
                assert ended["ended"] is True
                assert machine.closed.is_set() is False
                assert machine.ended_session_ids == [opened["session_id"]]

                assert machine.violations == [], machine.violations_dump()
            finally:
                await machine.close()

    async def test_the_session_is_exclusive_on_the_machine(self):
        async with boot_gateway(FAST_ENV) as booted:
            machine = await self._hello_machine(booted)
            try:
                await booted.service.connect(machine_id="mach_test1", agent_id="ari")
                # A second agent is refused by the CENTER — and the
                # refusal never reaches the machine as a second
                # session.begin.
                from daemon.services.mcps_gateway.protocol import GatewayProtocolError

                with pytest.raises(GatewayProtocolError) as excinfo:
                    await booted.service.connect(
                        machine_id="mach_test1", agent_id="leader"
                    )
                assert excinfo.value.code == "MACHINE_BUSY"
                # The machine never saw a second session.begin.
                begins = [
                    f
                    for f in machine.inbound_frames()
                    if f.get("method") == "session.begin"
                ]
                assert len(begins) == 1
            finally:
                await machine.close()

    async def test_a_command_after_the_disconnect_is_session_not_found(self):
        from daemon.services.mcps_gateway.protocol import GatewayProtocolError

        async with boot_gateway(FAST_ENV) as booted:
            machine = await self._hello_machine(booted)
            try:
                await booted.service.connect(machine_id="mach_test1", agent_id="ari")
                await booted.service.disconnect(agent_id="ari")
                with pytest.raises(GatewayProtocolError) as excinfo:
                    await booted.service.command(agent_id="ari", tool="echo")
                assert excinfo.value.code == "SESSION_NOT_FOUND"
            finally:
                await machine.close()

    async def test_a_refusal_of_the_machine_is_relayed_verbatim(self):
        from daemon.services.mcps_gateway.protocol import GatewayProtocolError

        async with boot_gateway(FAST_ENV) as booted:
            machine = await self._hello_machine(booted)
            try:
                await booted.service.connect(machine_id="mach_test1", agent_id="ari")
                with pytest.raises(GatewayProtocolError) as excinfo:
                    await booted.service.command(agent_id="ari", tool="not_a_tool")
                assert excinfo.value.code == "TOOL_NOT_FOUND"
            finally:
                await machine.close()

    async def test_a_command_that_outlives_the_deadline_is_a_tool_timeout(self):
        from daemon.services.mcps_gateway.protocol import GatewayProtocolError

        env = GatewayEnv(**{**FAST_ENV.__dict__, "command_timeout_ms": 400})
        async with boot_gateway(env) as booted:
            machine = await self._hello_machine(booted)
            try:
                await booted.service.connect(machine_id="mach_test1", agent_id="ari")
                with pytest.raises(GatewayProtocolError) as excinfo:
                    await booted.service.command(
                        agent_id="ari", tool="slow_tool", arguments={"seconds": 3}
                    )
                assert excinfo.value.code == "TOOL_TIMEOUT"
            finally:
                await machine.close()


# ─────────────────────────────────────────────────────────────────────────────
# The heartbeat kill (§3.3 / §6.8)
# ─────────────────────────────────────────────────────────────────────────────


class TestHeartbeatKill:
    async def test_a_silent_machine_is_killed_and_the_socket_is_closed(self):
        async with boot_gateway(FAST_ENV) as booted:
            machine = FakeRemoteMachine(machine_id="mach_test1")
            await machine.connect(booted.ws_url, "tok-alpha")
            await machine.send_hello()
            try:
                # The machine never heartbeats.
                closed = await machine.wait_for_close(timeout=10.0)
                assert closed is True
                codes = machine.error_codes()
                assert "HEARTBEAT_TIMEOUT" in codes, machine.transcript_dump()
                # The refusal carries no id — it is not the answer of a
                # request.
                for frame in machine.frames_of_type("error"):
                    if frame.get("error", {}).get("code") == "HEARTBEAT_TIMEOUT":
                        assert "id" not in frame
            finally:
                await machine.close()

    async def test_a_heartbeating_machine_survives(self):
        async with boot_gateway(FAST_ENV) as booted:
            machine = FakeRemoteMachine(machine_id="mach_test1")
            await machine.connect(booted.ws_url, "tok-alpha")
            await machine.send_hello()
            machine.adopt_heartbeat_interval(machine.hello_result)
            await machine.start_heartbeat()
            try:
                await asyncio.sleep(1.5)
                assert machine.closed.is_set() is False
                assert booted.service.roster()
            finally:
                await machine.close()


# ─────────────────────────────────────────────────────────────────────────────
# The audit (§8.4)
# ─────────────────────────────────────────────────────────────────────────────


class TestAudit:
    async def test_the_audit_hashes_the_arguments(self):
        from daemon.services.mcps_gateway.audit import hash_args

        async with boot_gateway(FAST_ENV) as booted:
            machine = FakeRemoteMachine(machine_id="mach_test1")
            await machine.connect(booted.ws_url, "tok-alpha")
            await machine.send_hello()
            try:
                await booted.service.connect(machine_id="mach_test1", agent_id="ari")
                await booted.service.command(
                    agent_id="ari", tool="echo", arguments={"message": "s3cret"}
                )
                records = booted.audit_sink.records
                assert len(records) == 1
                record = records[0]
                assert record.agent_id == "ari"
                assert record.machine_id == "mach_test1"
                assert record.tool == "echo"
                assert record.args_hash == hash_args({"message": "s3cret"})
                assert "s3cret" not in record.args_hash
            finally:
                await machine.close()


# ─────────────────────────────────────────────────────────────────────────────
# O1 — the protocol layer stays clean
# ─────────────────────────────────────────────────────────────────────────────


class TestProtocolHygiene:
    async def test_the_gateway_emits_no_orphan_protocol_frames(self):
        """O1: every inbound frame either answers a request the client
        sent or initiates a real §6.3 exchange.

        The center used to emit a liveness "response" whose id nobody had
        asked for — an orphan frame that is not part of the protocol
        (§6.1: a response id echoes a request). The liveness evidence is
        a WebSocket CONTROL frame now, so the protocol layer carries only
        real exchanges.
        """
        async with boot_gateway(FAST_ENV) as booted:
            machine = FakeRemoteMachine(machine_id="mach_test1")
            await machine.connect(booted.ws_url, "tok-alpha")
            await machine.send_hello()
            try:
                await booted.service.connect(machine_id="mach_test1", agent_id="ari")
                await booted.service.command(
                    agent_id="ari", tool="echo", arguments={"message": "hi"}
                )
                # Let the housekeeping sweep run several times.
                await asyncio.sleep(1.0)
            finally:
                await machine.close()

            client_ids = {f.get("id") for f in machine.outbound_frames()}
            for frame in machine.inbound_frames():
                kind = frame.get("type")
                if kind == "response":
                    assert frame.get("id") in client_ids, (
                        f"orphan response frame: {frame}"
                    )
                elif kind == "request":
                    assert frame.get("method") in (
                        "session.begin",
                        "session.end",
                        "tools/list",
                        "tools/call",
                    ), f"non-§6.3 request frame: {frame}"
                elif kind == "error":
                    assert (
                        frame.get("id") in client_ids or "id" not in frame
                    ), f"orphan error frame: {frame}"
                else:
                    raise AssertionError(f"unknown frame type: {frame}")

            # The fake client's own recorder agrees: no orphan response.
            assert machine.violations_by_reason("orphan_response") == []

    async def test_the_liveness_evidence_is_a_websocket_ping(self):
        """The liveness evidence is a WebSocket CONTROL frame, not a
        protocol frame."""
        async with boot_gateway(FAST_ENV) as booted:
            machine = FakeRemoteMachine(machine_id="mach_test1")
            await machine.connect(booted.ws_url, "tok-alpha")
            await machine.send_hello()
            # The machine heartbeats, so it survives the sweep.
            machine.adopt_heartbeat_interval(machine.hello_result)
            await machine.start_heartbeat()
            try:
                await asyncio.sleep(1.0)
                transports = [
                    connection.transport
                    for connection in booted.service._connections.values()
                ]
                assert transports, "the machine is not connected"
                for transport in transports:
                    assert transport.can_ping is True
                    # The reference center pings every 200 ms.
                    assert transport.pings_sent >= 3, (
                        f"the transport sent {transport.pings_sent} WebSocket "
                        "pings in one second — the liveness evidence is not "
                        "being produced"
                    )
            finally:
                await machine.close()
