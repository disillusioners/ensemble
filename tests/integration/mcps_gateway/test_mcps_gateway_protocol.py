"""Integration tests for the agents-ensemble remote-hands center gateway.

INDEPENDENCE
------------
These tests were written by an **independent fake-client author** who
transcribed the client side of the wire protocol from the specification
(``mcps_client/SPEC.md``, v0.1) alone.  No gateway implementation source
was read.  See ``COVERAGE.md`` in this directory for the exact SPEC-section
coverage declaration and the independence attestation.

The fake client lives in ``fake_remote_client.py`` (self-contained: stdlib +
``websockets`` only, imports nothing from ``daemon.*``).  The mounting jig
lives in ``harness.py`` (the only module allowed to import ``daemon.*``).

A failure in this file is a FINDING about the gateway, not a bug in the
test — unless the failure message says ``CLIENT-BUG``.
"""

from __future__ import annotations

import asyncio
import json
import time

import httpx
import pytest
from websockets.exceptions import InvalidStatus

from tests.integration.mcps_gateway.fake_remote_client import (
    CLIENT_CAPABILITIES,
    PROTOCOL_VERSION,
    SERVER_TO_CLIENT,
    FakeRemoteMachine,
    TranscriptEntry,
)
from tests.integration.mcps_gateway.harness import (
    CONTRACT_WS_PATH,
    GatewayEnv,
    boot_gateway,
    connect_ws,
    port_is_listening,
)

pytestmark = pytest.mark.integration

#: The capability set the shipped reference client treats as required
#: (Phase-A spec-interpretations §3.1; leader ruling 2026-10-22).
#:
#: NOTE ON ``notifications``: SPEC §5.3's capability table lists
#: ``notifications`` as "required" for v0.1, but that row is UNENFORCEABLE
#: against the shipped reference client — the reference client itself
#: advertises ``notifications: false`` and its required set is
#: {session, tools, heartbeat}.  Advertising ``true`` without actually
#: relaying MCP notifications would be dishonest.  Per the leader ruling
#: (2026-10-22) this is DELIBERATE v0.1 SCOPING, NOT drift: the tests below
#: PIN the implemented behaviour rather than asserting the SPEC's "required"
#: row, and the SPEC §5.3 tension is reported to the SPEC owner (morning
#: report).  SPEC.md is NOT edited here.
REQUIRED_CAPABILITIES = ("session", "tools", "heartbeat")

#: §5.3 — capabilities the gateway deliberately advertises ``false`` in v0.1.
UNSUPPORTED_CAPABILITIES = ("notifications",)

#: §5.3 — optional / v0.2-reserved capabilities, listed explicitly as false.
OPTIONAL_CAPABILITIES = ("screenshot_delta", "multi_session", "client_cert")

#: §6.5 — every error code the v0.1 kit must be able to see.
KNOWN_ERROR_CODES = {
    "PROTOCOL_VERSION_UNSUPPORTED",
    "CAPABILITY_MISSING",
    "AUTH_FAILED",
    "MACHINE_NOT_ENROLLED",
    "MACHINE_BUSY",
    "SESSION_NOT_FOUND",
    "SESSION_EXPIRED",
    "TOOL_NOT_FOUND",
    "TOOL_TIMEOUT",
    "TOOL_ERROR",
    "PAYLOAD_TOO_LARGE",
    "RATE_LIMITED",
    "HEARTBEAT_TIMEOUT",
    "INTERNAL",
}


def _base_env(**overrides) -> GatewayEnv:
    """Fast-but-legal gateway environment (§3.3 minimums are respected for
    the heartbeat *relations*; the absolute interval is lowered so the
    heartbeat-kill test stays inside the 30 s per-test budget)."""
    env = GatewayEnv(
        enabled=True,
        tokens=("tok-alpha", "tok-beta"),
        heartbeat_interval_ms=1000,
        heartbeat_timeout_ms=3000,
        client_dead_detect_ms=3000,
        command_timeout_ms=3000,
        allowed_machines=("mach_test1",),
    )
    for key, value in overrides.items():
        setattr(env, key, value)
    return env


def _describe(client: FakeRemoteMachine) -> str:
    return "\n--- transcript ---\n" + client.transcript_dump()


async def _wait_for(predicate, timeout_s: float, what: str) -> None:
    """Bounded monotonic poll for an observable event.

    Replaces hard sleeps on *pollable* events (a known flake class): the
    wait is bounded and fails loudly on deadline expiry rather than
    sleeping a fixed amount and hoping.  Time-based behaviour waits (the
    heartbeat / quiet-window sleeps) are deliberately NOT routed through
    this helper — they measure elapsed time, they do not wait for an event.
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.02)
    raise AssertionError(
        f"timed out after {timeout_s:.1f}s waiting for {what}"
    )


# ==========================================================================
# A. Transport & authentication (§3.1, §4.1)
# ==========================================================================


class TestTransportAndAuth:
    async def test_route_lifecycle_enabled_and_disabled(self):
        """§3.1 / task contract — the WS route exists only when enabled.

        * enabled  -> the route is mounted at the contract path
          ``/api/mcps-gateway/ws``;
        * disabled -> the route is NOT mounted, and the path answers 404.

        The brief phrases the disabled case as "no route at all -> 404".
        A *WebSocket upgrade* to the absent path is answered 403 by the
        daemon's SPA catch-all route (``/{path:path}``) — that is a
        daemon-wide artifact, so the test proves it is generic by dialling
        a second, unrelated, equally-absent path and requiring the same
        answer.  The route-absence property itself is asserted on the
        application's route table and on a plain HTTP GET.
        """
        async with boot_gateway(_base_env(enabled=True)) as gw:
            assert CONTRACT_WS_PATH in gw.ws_route_paths, (
                f"contract route {CONTRACT_WS_PATH} not mounted; "
                f"mounted WS routes: {gw.ws_route_paths}"
            )
            async with httpx.AsyncClient(base_url=gw.http_base) as http:
                r = await http.get(CONTRACT_WS_PATH)
                assert r.status_code == 404, (
                    "an HTTP GET to the WS path must not succeed as a REST route "
                    f"(got {r.status_code})"
                )

        async with boot_gateway(_base_env(enabled=False)) as gw:
            assert CONTRACT_WS_PATH not in gw.ws_route_paths, (
                "the gateway route must NOT be registered when "
                "MCPS_GATEWAY_ENABLED is off"
            )
            async with httpx.AsyncClient(base_url=gw.http_base) as http:
                r = await http.get(CONTRACT_WS_PATH)
                assert r.status_code == 404, (
                    f"disabled gateway: expected 404 for {CONTRACT_WS_PATH}, "
                    f"got {r.status_code}"
                )
                # Control: an equally-absent path behaves identically, so a
                # 403 on a WS upgrade is the daemon catch-all, not the gateway.
                control = await http.get("/api/definitely-not-a-route-xyz")
                assert control.status_code == r.status_code

            ws, status = await connect_ws(gw.ws_url, "tok-alpha")
            assert ws is None, "the upgrade must not succeed when disabled"
            assert status != 101, (
                f"disabled gateway: the upgrade must not be accepted (got {status})"
            )

    async def test_upgrade_auth_matrix(self):
        """§3.1 / §4.1 / §6.5 — the credential rides the upgrade header.

        A refused upgrade exchanges **no** protocol message: the fake
        client records zero outbound frames.

        Matrix: an unenrolled token, a missing token, a non-ASCII bearer,
        a garbage-bytes bearer and plain ASCII garbage must all be refused
        with a clean HTTP 401 and zero protocol frames, and the server must
        still serve an enrolled token afterwards.
        """
        async with boot_gateway(_base_env()) as gw:
            async def refuse(token: str | None, label: str):
                """Attempt the upgrade through the fake client itself, so the
                "zero protocol frames" observation is real: the client really
                did dial, and really did send nothing."""
                peer = FakeRemoteMachine("mach_test1")
                with pytest.raises(InvalidStatus) as excinfo:
                    await peer.connect(gw.ws_url, token)
                status = getattr(getattr(excinfo.value, "response", None), "status_code", None)
                assert status == 401, (
                    f"{label} must yield a clean HTTP 401, got {status!r}"
                )
                assert peer.transcript == [], (
                    f"{label}: no protocol frame may be exchanged on a refused "
                    f"upgrade, got {peer.transcript_dump()!r}"
                )
                return peer

            # (a) an unenrolled token -> 401, zero protocol frames.
            await refuse("not-a-real-token", "an unenrolled token")
            # (b) no token at all -> 401.
            await refuse(None, "a missing token")
            # (c) a NON-ASCII bearer -> a clean 401, zero protocol frames.
            #      (latin-1 representable, so the request is well-formed and
            #      the 401 is the SERVER's answer, not a client-side encoding
            #      failure.)
            await refuse("béta-tøken-ñ", "a non-ASCII bearer token")
            # (d) a garbage-bytes bearer -> a clean 401, zero protocol frames.
            await refuse("ÿþý-garbage-bytes", "a garbage-bytes bearer token")
            # (e) plain ASCII garbage -> a clean 401, zero protocol frames.
            await refuse("%%%not-a-token%%%", "an ASCII-garbage bearer token")

            # (f) an enrolled token -> the upgrade is still accepted, i.e. the
            #     refusals above did not take the server down.
            good = FakeRemoteMachine("mach_test1")
            await good.connect(gw.ws_url, "tok-alpha")
            assert good._ws is not None
            await good.close()

            # (g) the second enrolled token is accepted too.
            other = FakeRemoteMachine("mach_test1")
            await other.connect(gw.ws_url, "tok-beta")
            assert other._ws is not None
            await other.close()


# ==========================================================================
# B. Handshake (§5)
# ==========================================================================


class TestHandshake:
    async def test_hello_response_shape_capabilities_and_heartbeat_params(self):
        """§5.1 / §5.2 / §5.3 / §5.4 — the hello exchange.

        Asserted against the SPEC, not against the implementation:
          * the envelope carries ``v``, ``type=response`` and echoes the
            request ``id`` verbatim (§5.1, §6.1);
          * ``result.protocol_version`` is the dotted version string (§5.2);
          * ``result.server`` names the peer (§5.4);
          * ``result.capabilities`` is a **flat map of booleans** whose key
            set is exactly §5.3's seven keys, and every **required**
            capability is ``true`` (§5.3);
          * **all three** heartbeat parameters are present and satisfy the
            §3.3 relations (``timeout >= 3 x interval``,
            ``dead-detect >= 3 x interval``).
        """
        async with boot_gateway(_base_env()) as gw:
            client = FakeRemoteMachine("mach_test1")
            await client.connect(gw.ws_url, "tok-alpha")
            response = await client.send_hello()
            try:
                assert response is not None, "no hello response was received"
                assert response.get("v") == PROTOCOL_VERSION, (
                    f"§6.1: hello response must carry v={PROTOCOL_VERSION!r}, "
                    f"got {response.get('v')!r}"
                )
                assert response.get("type") == "response", (
                    f"§6.1: expected type='response', got {response.get('type')!r}"
                )
                assert isinstance(response.get("id"), str) and response["id"], (
                    "§6.1: the response must echo the request id"
                )
                assert client.transcript[0].id == response["id"], (
                    "§6.1: the response id must echo the request id verbatim"
                )

                result = response.get("result")
                assert isinstance(result, dict), "§5.4: hello.result must be an object"
                assert result.get("protocol_version") == PROTOCOL_VERSION, (
                    f"§5.2: the server must speak the requested version, "
                    f"got {result.get('protocol_version')!r}"
                )
                server = result.get("server")
                assert isinstance(server, dict) and server.get("name"), (
                    f"§5.4: hello.result.server must name the peer, got {server!r}"
                )

                caps = result.get("capabilities")
                assert isinstance(caps, dict), (
                    f"§5.3: capabilities must be a map, got {caps!r}"
                )
                assert all(isinstance(v, bool) for v in caps.values()), (
                    f"§5.3: capability values must be booleans, got {caps!r}"
                )
                expected_keys = set(CLIENT_CAPABILITIES)
                assert set(caps) == expected_keys, (
                    f"§5.3 (Convention): both peers' maps must carry the same "
                    f"key set; server has {sorted(caps)}, expected {sorted(expected_keys)}"
                )
                for cap in REQUIRED_CAPABILITIES:
                    assert caps.get(cap) is True, (
                        f"the gateway must advertise {cap!r} as supported, "
                        f"got {caps.get(cap)!r}"
                    )
                # Leader ruling 2026-10-22 / Phase-A spec-interpretations §3.1:
                # ``notifications`` is DELIBERATELY advertised false in v0.1
                # (the shipped reference client does the same and its required
                # set is {session, tools, heartbeat}; advertising true without a
                # relay would be dishonest).  This pins the implemented
                # behaviour.  The SPEC §5.3 "required" row for notifications is
                # unenforceable against the shipped client and goes to the SPEC
                # owner — SPEC.md is not edited here.
                for cap in UNSUPPORTED_CAPABILITIES:
                    assert caps.get(cap) is False, (
                        f"the gateway must advertise {cap!r} as NOT supported "
                        f"(deliberate v0.1 scoping — leader ruling 2026-10-22), "
                        f"got {caps.get(cap)!r}"
                    )

                for knob in (
                    "heartbeat_interval_ms",
                    "heartbeat_timeout_ms",
                    "client_dead_detect_ms",
                ):
                    assert knob in result, (
                        f"§5.4/§3.3: hello.result must advertise {knob!r}; "
                        f"got {sorted(result)}"
                    )
                    assert isinstance(result[knob], int) and result[knob] > 0, (
                        f"§3.3: {knob} must be a positive integer, "
                        f"got {result[knob]!r}"
                    )
                interval = result["heartbeat_interval_ms"]
                assert result["heartbeat_timeout_ms"] >= 3 * interval, (
                    f"§3.3: heartbeat_timeout_ms must be >= 3x the interval "
                    f"(interval={interval}, timeout={result['heartbeat_timeout_ms']})"
                )
                assert result["client_dead_detect_ms"] >= 3 * interval, (
                    f"§3.3: client_dead_detect_ms must be >= 3x the interval "
                    f"(interval={interval}, dead_detect={result['client_dead_detect_ms']})"
                )
            finally:
                await client.close()

            # -- (ii) the gateway must NOT enforce the notifications
            # capability against the CLIENT's capability map.  §5.3: "A peer
            # MUST treat a missing key as not-supported, exactly as it treats
            # an explicit false", and the reference client's required set is
            # {session, tools, heartbeat} (Phase-A spec-interpretations §3.1;
            # leader ruling 2026-10-22).  A client whose map omits
            # ``notifications`` — or carries it as an explicit false — must
            # still complete the hello exchange and be driven through
            # session.begin normally.
            for label, caps_map in (
                (
                    "omits notifications",
                    {k: v for k, v in CLIENT_CAPABILITIES.items() if k != "notifications"},
                ),
                ("carries notifications=false", {**CLIENT_CAPABILITIES, "notifications": False}),
            ):
                peer = FakeRemoteMachine("mach_test1", capabilities=caps_map)
                await peer.connect(gw.ws_url, "tok-alpha")
                try:
                    peer_response = await peer.send_hello()
                    assert peer_response is not None, (
                        f"§5.3: a client that {label} must still receive a hello "
                        "response — the gateway must not enforce the notifications "
                        "capability against the client's capability map"
                    )
                    assert peer_response.get("type") == "response", (
                        f"§5.3: a client that {label} must complete the hello "
                        f"exchange, got {peer_response!r}"
                    )
                    assert isinstance(peer_response.get("result"), dict), (
                        f"§5.3: the hello result must be present for a client that "
                        f"{label}, got {peer_response!r}"
                    )
                    opened = await asyncio.wait_for(
                        gw.service.connect(machine_id="mach_test1", agent_id="ari"),
                        timeout=10,
                    )
                    assert opened.get("session_id"), (
                        f"§5.3: a client that {label} must be able to open a "
                        f"session, got {opened!r}"
                    )
                    assert peer.frames_with_method("session.begin"), (
                        f"§5.3: session.begin must have been exchanged with a "
                        f"client that {label}"
                    )
                    await asyncio.wait_for(
                        gw.service.disconnect(agent_id="ari"), timeout=10
                    )
                finally:
                    await peer.close()


# ==========================================================================
# C. Session lifecycle (§6.2, §6.4, §6.6, §6.8)
# ==========================================================================


class TestSessionLifecycle:
    async def test_session_begin_returns_matching_session_id_and_catalog(self):
        """§6.4 — ``session.begin`` returns the session id and the catalog.

        The envelope ``session_id`` is canonical and **MUST** equal the one
        repeated inside ``result``; the catalog entries are the MCP tool
        objects with their ``inputSchema`` **intact** (§6.4, §9.1).
        """
        async with boot_gateway(_base_env()) as gw:
            client = FakeRemoteMachine("mach_test1")
            await client.connect(gw.ws_url, "tok-alpha")
            await client.send_hello()
            client.adopt_heartbeat_interval(client.hello_result)
            await client.start_heartbeat()
            try:
                opened = await asyncio.wait_for(
                    gw.service.connect(machine_id="mach_test1", agent_id="ari"), timeout=10
                )

                begins = client.frames_with_method("session.begin")
                assert len(begins) == 1, (
                    f"§5.1/§6.3: exactly one session.begin expected, got {len(begins)}"
                )
                begin = begins[0]
                assert begin.get("v") == PROTOCOL_VERSION, "§6.1: session.begin must carry v"
                assert begin.get("type") == "request", "§6.1: session.begin must be a request"
                params = begin.get("params")
                assert isinstance(params, dict), "§6.1: session.begin must carry params"
                assert params.get("machine_id") == "mach_test1", (
                    f"§6.4: session.begin must name the machine, got {params!r}"
                )

                # The response the client sent.
                responses = [
                    f
                    for f in client.outbound_frames()
                    if f.get("id") == begin.get("id")
                ]
                assert len(responses) == 1, "the client must answer session.begin exactly once"
                response = responses[0]
                envelope_sid = response.get("session_id")
                result_sid = (response.get("result") or {}).get("session_id")
                assert envelope_sid, "§6.4: the response must carry session_id"
                assert envelope_sid == result_sid, (
                    "§6.4: the envelope session_id and result.session_id MUST match "
                    f"({envelope_sid!r} vs {result_sid!r})"
                )
                assert result_sid == opened.get("session_id"), (
                    "§6.4: the session id the center reported must be the one the "
                    f"machine minted ({result_sid!r} vs {opened.get('session_id')!r})"
                )

                # §6.4 — the catalog is forwarded with inputSchema intact.
                catalog = (response.get("result") or {}).get("tools")
                assert isinstance(catalog, list) and catalog, (
                    f"§6.4: session.begin must return the tool catalog, got {catalog!r}"
                )
                by_name = {t.get("name"): t for t in catalog if isinstance(t, dict)}
                for expected in client.tools:
                    entry = by_name.get(expected.name)
                    assert entry is not None, (
                        f"§6.4: catalog must list {expected.name!r}; got {sorted(by_name)}"
                    )
                    assert entry.get("inputSchema") == expected.input_schema, (
                        f"§6.4/§9.1: the inputSchema of {expected.name!r} must be "
                        f"forwarded intact; got {entry.get('inputSchema')!r}"
                    )
            finally:
                await client.close()

    async def test_tools_call_success_and_is_error_results(self):
        """§6.6 — ``tools/call`` is forwarded unchanged and the raw MCP
        result comes back as-is (both for a success and for an
        ``isError: true`` result)."""
        async with boot_gateway(_base_env()) as gw:
            client = FakeRemoteMachine("mach_test1")
            await client.connect(gw.ws_url, "tok-alpha")
            await client.send_hello()
            client.adopt_heartbeat_interval(client.hello_result)
            await client.start_heartbeat()
            try:
                await asyncio.wait_for(
                    gw.service.connect(machine_id="mach_test1", agent_id="ari"), timeout=10
                )

                # -- success ------------------------------------------------
                ok = await asyncio.wait_for(
                    gw.service.command(
                        agent_id="ari", tool="echo", arguments={"message": "hello-spec"}
                    ),
                    timeout=10,
                )
                calls = client.frames_with_method("tools/call")
                assert calls, "the gateway must forward the call to the machine"
                params = calls[0].get("params")
                assert isinstance(params, dict), "§6.1: tools/call must carry params"
                assert params.get("name") == "echo", (
                    f"§6.6: params.name must be the tool name, got {params!r}"
                )
                assert params.get("arguments") == {"message": "hello-spec"}, (
                    "§6.6: params.arguments must be the MCP arguments object, "
                    f"unchanged; got {params.get('arguments')!r}"
                )
                # The envelope must not wrap the MCP call in a second layer.
                assert "method" not in params and "result" not in params, (
                    "§6.1: the envelope carries MCP fields directly — no second "
                    f"layer of indirection; got {params!r}"
                )
                assert isinstance(ok, dict), "the call must return the MCP result"
                assert ok.get("isError") is False, (
                    f"§6.6: the raw MCP result must come back as-is, got {ok!r}"
                )
                assert ok.get("content"), f"§6.6: the result must carry content, got {ok!r}"

                # -- isError result ----------------------------------------
                err = await asyncio.wait_for(
                    gw.service.command(
                        agent_id="ari", tool="fail_tool", arguments={"reason": "nope"}
                    ),
                    timeout=10,
                )
                assert isinstance(err, dict), "the call must return the MCP result"
                assert err.get("isError") is True, (
                    "§6.6: an MCP isError:true result must be forwarded as-is, "
                    f"got {err!r}"
                )
            finally:
                await client.close()

    async def test_session_end_acknowledges_and_keeps_the_socket_open(self):
        """§6.8 — ``session.end`` acknowledges, releases the session and the
        connection returns to ``ready`` with **the socket still open**, ready
        for the next session."""
        async with boot_gateway(_base_env()) as gw:
            client = FakeRemoteMachine("mach_test1")
            await client.connect(gw.ws_url, "tok-alpha")
            await client.send_hello()
            client.adopt_heartbeat_interval(client.hello_result)
            await client.start_heartbeat()
            try:
                opened = await asyncio.wait_for(
                    gw.service.connect(machine_id="mach_test1", agent_id="ari"), timeout=10
                )
                session_id = opened["session_id"]

                ended = await asyncio.wait_for(
                    gw.service.disconnect(agent_id="ari"), timeout=10
                )
                assert ended.get("session_id") == session_id, (
                    f"§6.8: the acknowledgement must name the ended session, got {ended!r}"
                )

                ends = client.frames_with_method("session.end")
                assert len(ends) == 1, f"§6.3: one session.end expected, got {len(ends)}"
                end_params = ends[0].get("params")
                assert isinstance(end_params, dict), "§6.1: session.end must carry params"
                assert end_params.get("session_id") == session_id, (
                    f"§6.8: session.end must name the session to close, got {end_params!r}"
                )

                acks = [
                    f
                    for f in client.outbound_frames()
                    if f.get("id") == ends[0].get("id")
                ]
                assert len(acks) == 1, "the client must answer session.end exactly once"
                assert acks[0].get("type") == "response", (
                    f"§6.8: session.end must be acknowledged, got {acks[0]!r}"
                )

                # The socket must still be open.
                await asyncio.sleep(0.2)
                assert not client.closed.is_set(), (
                    "§6.8: ending a session must not close the socket"
                )
                # ...and the connection must be ready for the next session.
                reopened = await asyncio.wait_for(
                    gw.service.connect(machine_id="mach_test1", agent_id="bob"), timeout=10
                )
                assert reopened.get("session_id") != session_id, (
                    "§6.8: a new session after session.end must get a fresh id"
                )
                assert len(client.frames_with_method("session.begin")) == 2, (
                    "§6.8: the connection must be ready for the next session"
                )
            finally:
                await client.close()


# ==========================================================================
# D. Session exclusivity (§8.3)
# ==========================================================================


class TestSessionExclusivity:
    async def test_second_concurrent_session_begin_is_refused_machine_busy(self):
        """§8.3 — one active session per machine.

        A second ``session.begin`` for a machine that already has a session
        must be refused with ``MACHINE_BUSY`` (§6.5), and the **original**
        session must survive the refusal.
        """
        async with boot_gateway(_base_env()) as gw:
            client = FakeRemoteMachine("mach_test1")
            await client.connect(gw.ws_url, "tok-alpha")
            await client.send_hello()
            client.adopt_heartbeat_interval(client.hello_result)
            await client.start_heartbeat()
            try:
                first = await asyncio.wait_for(
                    gw.service.connect(machine_id="mach_test1", agent_id="ari"), timeout=10
                )

                busy_code = None
                with pytest.raises(Exception) as excinfo:
                    await asyncio.wait_for(
                        gw.service.connect(machine_id="mach_test1", agent_id="bob"), timeout=10
                    )
                busy_code = getattr(excinfo.value, "code", None) or str(excinfo.value)
                assert "MACHINE_BUSY" in busy_code, (
                    "§8.3/§6.5: a concurrent session.begin on a busy machine must be "
                    f"refused with MACHINE_BUSY, got {busy_code!r}"
                )

                # The original session must survive.
                result = await asyncio.wait_for(
                    gw.service.command(
                        agent_id="ari", tool="echo", arguments={"message": "survivor"}
                    ),
                    timeout=10,
                )
                assert result.get("isError") is False, (
                    "§8.3: the original session must survive a refused second "
                    f"session.begin, got {result!r}"
                )
                assert first.get("session_id"), "the first session must still exist"
            finally:
                await client.close()

    async def test_machine_side_exclusivity_over_the_wire(self):
        """§8.3 — "the machine itself MUST refuse a concurrent
        ``session.begin`` for the same ``machine_id``".

        In the live flow the *center* refuses first, so the machine-side
        lock is driven directly here: a second ``session.begin`` is pushed
        into the machine's handler while it already holds a session.  The
        machine must answer with ``MACHINE_BUSY`` (§6.5) and the original
        session must survive.
        """
        async with boot_gateway(_base_env()) as gw:
            client = FakeRemoteMachine("mach_test1")
            await client.connect(gw.ws_url, "tok-alpha")
            await client.send_hello()
            client.adopt_heartbeat_interval(client.hello_result)
            await client.start_heartbeat()
            try:
                opened = await asyncio.wait_for(
                    gw.service.connect(machine_id="mach_test1", agent_id="ari"), timeout=10
                )
                first_session = opened["session_id"]

                # A second, concurrent session.begin for the same machine.
                await client._handle_session_begin(
                    {
                        "v": PROTOCOL_VERSION,
                        "type": "request",
                        "id": "synthetic-begin-2",
                        "method": "session.begin",
                        "params": {"machine_id": "mach_test1"},
                    }
                )
                await asyncio.sleep(0.3)

                refusals = [
                    f
                    for f in client.outbound_frames()
                    if f.get("id") == "synthetic-begin-2"
                ]
                assert len(refusals) == 1, (
                    "§8.3: the machine must answer the concurrent session.begin "
                    f"exactly once, got {refusals!r}"
                )
                assert refusals[0].get("type") == "error", (
                    f"§8.3: the refusal must be an error frame, got {refusals[0]!r}"
                )
                assert (refusals[0].get("error") or {}).get("code") == "MACHINE_BUSY", (
                    f"§6.5: the refusal code must be MACHINE_BUSY, got {refusals[0]!r}"
                )
                assert client.active_session_id == first_session, (
                    "§8.3: the machine's original session must survive the refusal"
                )

                # The original session still works.
                result = await asyncio.wait_for(
                    gw.service.command(
                        agent_id="ari", tool="echo", arguments={"message": "survivor"}
                    ),
                    timeout=10,
                )
                assert result.get("isError") is False, (
                    "§8.3: the original session must survive a refused concurrent "
                    f"session.begin, got {result!r}"
                )
            finally:
                await client.close()


# ==========================================================================
# E. Heartbeat (§3.3, §6.8)
# ==========================================================================


class TestHeartbeat:
    async def test_heartbeat_silence_kills_the_session(self):
        """§3.3 / §6.8 — heartbeat loss is a **kill**, not a pause.

        After the client stops heartbeating the gateway must declare the
        session dead: a ``HEARTBEAT_TIMEOUT`` error (§6.5) and/or a close.
        """
        async with boot_gateway(_base_env()) as gw:
            client = FakeRemoteMachine("mach_test1")
            await client.connect(gw.ws_url, "tok-alpha")
            await client.send_hello()
            client.adopt_heartbeat_interval(client.hello_result)
            await client.start_heartbeat()
            try:
                await asyncio.wait_for(
                    gw.service.connect(machine_id="mach_test1", agent_id="ari"), timeout=10
                )
                # Let at least one heartbeat flow so the server sees the
                # machine alive before the silence starts.
                await asyncio.sleep(1.2)

                await client.stop_heartbeat()
                t0 = time.monotonic()
                error = await client.wait_for(
                    lambda f: f.get("type") == "error"
                    and (f.get("error") or {}).get("code") == "HEARTBEAT_TIMEOUT",
                    timeout=15,
                )
                elapsed_ms = (time.monotonic() - t0) * 1000
                closed = await client.wait_for_close(timeout=5)

                assert error is not None or closed, (
                    "§3.3/§6.8: heartbeat silence must kill the session — "
                    f"expected a HEARTBEAT_TIMEOUT error and/or a close; "
                    f"frames={client.inbound_frames()}\n{_describe(client)}"
                )
                if error is not None:
                    code = (error.get("error") or {}).get("code")
                    assert code == "HEARTBEAT_TIMEOUT", (
                        f"§6.5: the kill must be signalled with HEARTBEAT_TIMEOUT, got {code!r}"
                    )
                    assert isinstance(error.get("error"), dict), "§6.5: error must be an object"
                    assert isinstance((error.get("error") or {}).get("message"), str), (
                        "§6.5: an error must carry a human-readable message"
                    )
                assert closed, (
                    "§3.3/§6.8: the socket must be closed once the session is dead"
                )
                assert elapsed_ms < 20000, (
                    f"the kill took {elapsed_ms:.0f} ms — too slow for the test budget"
                )
            finally:
                await client.close()

    async def test_advertised_heartbeat_timeout_matches_the_actual_tolerance(self):
        """§3.3 — ``heartbeat_timeout_ms`` is "how long the server tolerates
        silence before it declares the session dead".

        The advertised value and the *observed* tolerance must agree: a
        client that trusts the advertisement must not be wrong about when
        the server will declare the session dead.  The silence is measured
        from the last heartbeat the client actually sent, so the number is
        the server's true tolerance and not an artefact of when the test
        stopped the heartbeat.
        """
        async with boot_gateway(_base_env()) as gw:
            client = FakeRemoteMachine("mach_test1")
            await client.connect(gw.ws_url, "tok-alpha")
            await client.send_hello()
            client.adopt_heartbeat_interval(client.hello_result)
            advertised = int(client.hello_result["heartbeat_timeout_ms"])
            await client.start_heartbeat()
            try:
                await asyncio.wait_for(
                    gw.service.connect(machine_id="mach_test1", agent_id="ari"), timeout=10
                )
                await asyncio.sleep(1.2)
                await client.stop_heartbeat()
                killed = await client.wait_for(
                    lambda f: f.get("type") == "error"
                    and (f.get("error") or {}).get("code") == "HEARTBEAT_TIMEOUT",
                    timeout=15,
                )
                assert killed is not None, "the session was never declared dead"
                assert client.last_heartbeat_monotonic is not None, (
                    "the client must have sent at least one heartbeat before the silence"
                )
                silence_ms = (time.monotonic() - client.last_heartbeat_monotonic) * 1000
            finally:
                await client.close()

            tolerance = max(600.0, advertised * 0.2)
            assert abs(silence_ms - advertised) <= tolerance, (
                f"§3.3: the gateway advertises heartbeat_timeout_ms={advertised} but "
                f"actually tolerated {silence_ms:.0f} ms of silence before declaring "
                f"the session dead (difference {silence_ms - advertised:+.0f} ms, "
                f"allowed {tolerance:.0f} ms) — the advertised value is not the real "
                "tolerance"
            )


# ==========================================================================
# F. Quiet window (O1 pin — leader ruling 2026-10-22)
# ==========================================================================


class TestQuietWindow:
    async def test_quiet_window_emits_no_protocol_frames(self):
        """O1 PIN (leader ruling 2026-10-22) + ZOMBIE-SOCKET PIN (n4) — the
        gateway must emit ZERO protocol frames outside real §6.3 exchanges.

        Liveness evidence is carried by WebSocket **control frames**
        (ping / pong), and control frames never surface as WebSocket
        *messages*.  A healthy connection that is simply quiet must therefore
        deliver nothing at the protocol layer.

        The window below is sized generously against the fast heartbeat env
        knobs (interval = 1000 ms), so several ping cycles would have elapsed
        inside it if liveness were still being emitted as protocol frames.

        The client's own §3.3 heartbeat notifications legitimately occupy the
        OUTBOUND half of the transcript — the pin is on the INBOUND half,
        which must stay empty for the whole window.

        ZOMBIE-SOCKET SCENARIO (n4): connection **A** completes a full
        lifecycle — hello → session.begin → session.end — and its socket is
        then deliberately **kept open client-side**.  Connection **B** (a
        fresh dial on its own machine, with its own live session) is opened
        and stays active.  Connection A must then receive **no frames at
        all** for the whole window: a connection whose session is over must
        not keep a zombie liveness/ping loop alive on the wire.

        Observability note: WebSocket control frames (ping / pong) are
        invisible to the client — websockets 16's asyncio ``ClientProtocol``
        exposes no ping/pong frame counters — so the assertion is made over
        the observable *message* layer, which is the layer a zombie
        protocol-frame emitter would violate.
        """
        quiet_window_s = 6.0
        async with boot_gateway(
            _base_env(allowed_machines=("mach_test1", "mach_test2"))
        ) as gw:
            # --- connection A: full lifecycle, then deliberately left open ---
            client = FakeRemoteMachine("mach_test1")
            await client.connect(gw.ws_url, "tok-alpha")
            await client.send_hello()
            client.adopt_heartbeat_interval(client.hello_result)
            await client.start_heartbeat()
            try:
                opened = await asyncio.wait_for(
                    gw.service.connect(machine_id="mach_test1", agent_id="ari"),
                    timeout=10,
                )
                assert opened.get("session_id"), (
                    f"§6.4: the session must open, got {opened!r}"
                )
                ended = await asyncio.wait_for(
                    gw.service.disconnect(agent_id="ari"), timeout=10
                )
                assert ended.get("session_id") == opened["session_id"], (
                    f"§6.8: the acknowledgement must name the ended session, got {ended!r}"
                )
                # Bounded poll for the expected frame — the gateway's
                # session.end request and this client's acknowledgement of it —
                # instead of a fixed settle sleep.
                def _session_end_acked() -> bool:
                    ends = client.frames_with_method("session.end")
                    if not ends:
                        return False
                    end_id = ends[0].get("id")
                    return any(f.get("id") == end_id for f in client.outbound_frames())

                await _wait_for(
                    _session_end_acked,
                    timeout_s=5.0,
                    what=(
                        "the session.end request and connection A's "
                        "acknowledgement of it"
                    ),
                )

                # --- connection B: fresh dial, its own machine, live session ---
                peer = FakeRemoteMachine("mach_test2")
                await peer.connect(gw.ws_url, "tok-alpha")
                await peer.send_hello()
                peer.adopt_heartbeat_interval(peer.hello_result)
                await peer.start_heartbeat()
                try:
                    peer_session = await asyncio.wait_for(
                        gw.service.connect(machine_id="mach_test2", agent_id="bob"),
                        timeout=10,
                    )
                    assert peer_session.get("session_id"), (
                        "§6.4: connection B must own a live session, got "
                        f"{peer_session!r}"
                    )

                    # --- the quiet window, with B active ---
                    mark = len(client.transcript)
                    await asyncio.sleep(quiet_window_s)

                    window = client.transcript[mark:]
                    inbound = [e for e in window if e.direction == SERVER_TO_CLIENT]
                    outbound = [e for e in window if e.direction != SERVER_TO_CLIENT]

                    assert inbound == [], (
                        "O1 + zombie pin (leader ruling 2026-10-22): a quiet, "
                        f"healthy connection must receive ZERO protocol-layer "
                        f"frames during a {quiet_window_s:.0f} s window, but "
                        f"{len(inbound)} arrived:\n"
                        + "\n".join(f"    {e.raw}" for e in inbound)
                        + "\n(WebSocket control frames — ping/pong — never surface "
                        "as messages, so any frame above is a protocol-layer "
                        "emission)"
                    )
                    # The zombie property: A's socket is still open but the
                    # gateway must have stopped talking on it.
                    assert not client.closed.is_set(), (
                        "zombie pin (n4): connection A's socket must not be closed "
                        "by the gateway — a session that ended leaves the "
                        "connection in §6.2 'ready', not dead"
                    )
                    # The window must genuinely have been quiet on the client's
                    # side too: only its own §3.3 heartbeat notifications.
                    assert all(
                        e.frame and e.frame.get("method") == "heartbeat"
                        for e in outbound
                    ), (
                        "the quiet window must contain only the client's own §3.3 "
                        "heartbeat notifications, got:\n"
                        + "\n".join(f"    {e.raw}" for e in outbound)
                    )
                    # B must still be genuinely alive while A sits quiet.
                    result = await asyncio.wait_for(
                        gw.service.command(
                            agent_id="bob", tool="echo", arguments={"message": "alive"}
                        ),
                        timeout=10,
                    )
                    assert result.get("isError") is False, (
                        "connection B must remain serviceable while connection A "
                        f"sits quiet, got {result!r}"
                    )
                finally:
                    await peer.close()
            finally:
                await client.close()


# ==========================================================================
# G. Typed errors (§6.5)
# ==========================================================================


class TestTypedErrors:
    async def test_unknown_method_is_rejected_in_band(self):
        """§6.3 — the method set has exactly seven members; an unknown method
        must be refused in band (never by silently dropping the request)."""
        async with boot_gateway(_base_env()) as gw:
            client = FakeRemoteMachine("mach_test1")
            await client.connect(gw.ws_url, "tok-alpha")
            await client.send_hello()
            client.adopt_heartbeat_interval(client.hello_result)
            await client.start_heartbeat()
            try:
                request_id = await client.send_unknown_method()
                response = await client.wait_for(lambda f: f.get("id") == request_id, timeout=8)
                assert response is not None, (
                    "§6.3: an unknown method must be answered in band, not dropped"
                )
                assert response.get("type") == "error", (
                    f"§6.1/§6.5: an unknown method must produce type='error', "
                    f"got {response.get('type')!r}"
                )
                error = response.get("error")
                assert isinstance(error, dict), "§6.5: the error must be an object"
                code = error.get("code")
                assert code in KNOWN_ERROR_CODES, (
                    f"§6.5: the error code must be one of the documented codes, got {code!r}"
                )
                assert code == "INTERNAL", (
                    f"§6.5: an unknown method is an unexpected peer condition — the "
                    f"expected code is INTERNAL, got {code!r}"
                )
                assert isinstance(error.get("message"), str), (
                    "§6.5: the error must carry a human-readable message"
                )
                # The connection must survive an unknown method.
                await asyncio.sleep(0.2)
                assert not client.closed.is_set(), (
                    "an unknown method must not tear the connection down"
                )
            finally:
                await client.close()

    async def test_error_frames_carry_the_session_id_when_a_session_is_active(self):
        """§6.1 — ``session_id`` is carried "after a session exists"; it is
        absent only on ``hello`` and on pre-session errors."""
        async with boot_gateway(_base_env()) as gw:
            client = FakeRemoteMachine("mach_test1")
            await client.connect(gw.ws_url, "tok-alpha")
            await client.send_hello()
            client.adopt_heartbeat_interval(client.hello_result)
            await client.start_heartbeat()
            try:
                opened = await asyncio.wait_for(
                    gw.service.connect(machine_id="mach_test1", agent_id="ari"), timeout=10
                )
                session_id = opened["session_id"]
                request_id = await client.send_unknown_method()
                response = await client.wait_for(lambda f: f.get("id") == request_id, timeout=8)
                assert response is not None, "expected an in-band error"
                assert response.get("session_id") == session_id, (
                    "§6.1: an error frame sent while a session is active must carry "
                    f"session_id={session_id!r}; got {response.get('session_id')!r}"
                )
            finally:
                await client.close()

    async def test_disallowed_machine_is_refused(self):
        """§4.1 / §6.5 — a machine that is not enrolled is refused with
        ``MACHINE_NOT_ENROLLED``."""
        async with boot_gateway(_base_env()) as gw:
            client = FakeRemoteMachine("mach_test1")
            await client.connect(gw.ws_url, "tok-alpha")
            await client.send_hello()
            try:
                with pytest.raises(Exception) as excinfo:
                    await asyncio.wait_for(
                        gw.service.connect(machine_id="mach_not_allowed", agent_id="ari"),
                        timeout=10,
                    )
                message = f"{getattr(excinfo.value, 'code', '')} {excinfo.value}"
                assert "MACHINE_NOT_ENROLLED" in message, (
                    f"§6.5: an unenrolled machine must be refused with "
                    f"MACHINE_NOT_ENROLLED, got {message!r}"
                )
            finally:
                await client.close()


# ==========================================================================
# H. Audit (§8.4)
# ==========================================================================


class TestAudit:
    async def test_audit_records_an_args_hash_and_never_the_raw_arguments(self):
        """§8.4 — "who / machine / tool / args-hash"; args are **hashed**,
        not stored verbatim, so a typed password must not end up in a log."""
        secret = "s3cr3t-password-value-xyzzy"
        async with boot_gateway(_base_env()) as gw:
            client = FakeRemoteMachine("mach_test1")
            await client.connect(gw.ws_url, "tok-alpha")
            await client.send_hello()
            client.adopt_heartbeat_interval(client.hello_result)
            await client.start_heartbeat()
            try:
                await asyncio.wait_for(
                    gw.service.connect(machine_id="mach_test1", agent_id="ari"), timeout=10
                )
                await asyncio.wait_for(
                    gw.service.command(
                        agent_id="ari", tool="echo", arguments={"message": secret}
                    ),
                    timeout=10,
                )
            finally:
                await client.close()

            records = list(getattr(gw.audit_sink, "records", []) or [])
            assert records, (
                "§8.4: the forwarded command must be audited; the audit sink is empty"
            )
            record = records[-1]
            assert record.tool == "echo", (
                f"§8.4: the audit must name the tool, got {record.tool!r}"
            )
            assert record.machine_id == "mach_test1", (
                f"§8.4: the audit must name the machine, got {record.machine_id!r}"
            )
            assert record.agent_id == "ari", (
                f"§8.4: the audit must name the agent, got {record.agent_id!r}"
            )
            args_hash = record.args_hash
            assert isinstance(args_hash, str) and args_hash.strip(), (
                "§8.4: the audit must carry a non-empty args-hash, got "
                f"{args_hash!r}"
            )
            serialized = json.dumps(record.__dict__, default=str)
            assert secret not in serialized, (
                "§8.4: the raw arguments must never be stored verbatim — the "
                f"audit record contains {secret!r}: {serialized!r}"
            )
            assert secret not in args_hash, (
                "§8.4: the args-hash must be a hash, not the arguments themselves"
            )


# ==========================================================================
# I. Envelope recording (brief (f))
# ==========================================================================


class TestEnvelopeRecording:
    async def test_every_inbound_frame_is_transcribed_and_violations_recorded(self):
        """The brief's (f): envelope violations are **recorded, not failed**.

        This test asserts the *evidence* is complete — every inbound frame
        is in the transcript, and every violation observed is recorded in
        the violation list (which the report quotes).  It deliberately does
        NOT fail on a recorded violation.
        """
        async with boot_gateway(_base_env()) as gw:
            client = FakeRemoteMachine("mach_test1")
            await client.connect(gw.ws_url, "tok-alpha")
            await client.send_hello()
            client.adopt_heartbeat_interval(client.hello_result)
            await client.start_heartbeat()
            try:
                await asyncio.wait_for(
                    gw.service.connect(machine_id="mach_test1", agent_id="ari"), timeout=10
                )
                await asyncio.wait_for(
                    gw.service.command(
                        agent_id="ari", tool="echo", arguments={"message": "record-me"}
                    ),
                    timeout=10,
                )
                await asyncio.sleep(0.4)
            finally:
                await client.close()

            inbound = [e for e in client.transcript if e.direction == "server->client"]
            assert inbound, "the transcript must contain the inbound frames"
            for entry in inbound:
                assert entry.parsed_ok, (
                    f"every inbound frame must parse as JSON: {entry.raw!r}"
                )
            # Every violation the recorder saw is a first-class record.
            for violation in client.violations:
                assert violation.reason and violation.spec_section, (
                    "every recorded violation must name a reason and a SPEC section"
                )
            # Cross-check: the recorder and the transcript must agree.  Any
            # inbound response whose id the client never sent is an orphan
            # under §6.1 and MUST appear in the recorded violations.
            orphans = [
                e
                for e in inbound
                if e.type == "response"
                and isinstance(e.id, str)
                and e.id not in {f.get("id") for f in client.outbound_frames()}
            ]
            recorded = client.violations_by_reason("orphan_response")
            assert len(recorded) == len(orphans), (
                "the recorder must record one 'orphan_response' violation per "
                f"uncorrelatable inbound response frame; transcript shows "
                f"{len(orphans)} orphans, recorder has {len(recorded)}\n"
                f"{_describe(client)}"
            )
            # The evidence is quotable: the violations dump names the frames.
            dump = client.violations_dump()
            for entry in orphans:
                assert entry.id and entry.id in dump, (
                    f"the violation record must quote the offending frame id "
                    f"{entry.id!r}; dump:\n{dump}"
                )


# ==========================================================================
# J. Environment knobs (task contract)
# ==========================================================================


class TestEnvironmentKnobs:
    async def test_contract_env_knobs_are_honoured(self):
        """The task contract promises these knobs are env-overridable.

        Every knob must reach the gateway's effective configuration.  The
        gateway is booted with deliberately distinct values so that a knob
        that is ignored (or that mirrors another knob) is detectable.
        """
        env = GatewayEnv(
            enabled=True,
            tokens=("tok-alpha", "tok-beta"),
            heartbeat_interval_ms=1234,
            heartbeat_timeout_ms=7000,
            client_dead_detect_ms=4321,
            command_timeout_ms=2500,
            session_ttl_ms=120_000,
            session_idle_timeout_ms=90_000,
            allowed_machines=("mach_allowed",),
        )
        async with boot_gateway(env) as gw:
            config = gw.config
            assert config.heartbeat_interval_ms == 1234, (
                "MCPS_GATEWAY_HEARTBEAT_INTERVAL_MS must be honoured, got "
                f"{config.heartbeat_interval_ms!r}"
            )
            assert config.client_dead_detect_ms == 4321, (
                "MCPS_GATEWAY_CLIENT_DEAD_DETECT_MS must be honoured, got "
                f"{config.client_dead_detect_ms!r}"
            )
            assert config.session_ttl_ms == 120_000, (
                "MCPS_GATEWAY_SESSION_TTL_MS must be honoured, got "
                f"{config.session_ttl_ms!r}"
            )
            assert config.session_idle_timeout_ms == 90_000, (
                "MCPS_GATEWAY_SESSION_IDLE_TIMEOUT_MS must be honoured, got "
                f"{config.session_idle_timeout_ms!r}"
            )
            assert tuple(config.allowed_machines) == ("mach_allowed",), (
                "MCPS_GATEWAY_ALLOWED_MACHINES must be honoured, got "
                f"{config.allowed_machines!r}"
            )
            assert tuple(config.tokens) == ("tok-alpha", "tok-beta"), (
                "MCPS_GATEWAY_TOKENS must be honoured, got {config.tokens!r}"
            )
            assert getattr(config, "heartbeat_timeout_ms", None) == 7000, (
                "MCPS_GATEWAY_HEARTBEAT_TIMEOUT_MS must be honoured — the "
                "gateway's effective configuration must expose the advertised "
                f"timeout; got {getattr(config, 'heartbeat_timeout_ms', None)!r} "
                f"(config fields: {sorted(vars(config))})"
            )

    async def test_advertised_heartbeat_params_reflect_the_env_knobs(self):
        """The knobs must reach the wire, not just the configuration."""
        env = GatewayEnv(
            enabled=True,
            tokens=("tok-alpha",),
            heartbeat_interval_ms=1500,
            heartbeat_timeout_ms=7000,
            client_dead_detect_ms=6000,
        )
        async with boot_gateway(env) as gw:
            client = FakeRemoteMachine("mach_test1")
            await client.connect(gw.ws_url, "tok-alpha")
            await client.send_hello()
            try:
                result = client.hello_result
                assert result["heartbeat_interval_ms"] == 1500, (
                    "the advertised heartbeat_interval_ms must reflect "
                    f"MCPS_GATEWAY_HEARTBEAT_INTERVAL_MS, got {result!r}"
                )
                assert result["client_dead_detect_ms"] == 6000, (
                    "the advertised client_dead_detect_ms must reflect "
                    f"MCPS_GATEWAY_CLIENT_DEAD_DETECT_MS, got {result!r}"
                )
                assert result["heartbeat_timeout_ms"] == 7000, (
                    "the advertised heartbeat_timeout_ms must reflect "
                    f"MCPS_GATEWAY_HEARTBEAT_TIMEOUT_MS, got {result!r}"
                )
            finally:
                await client.close()


# ==========================================================================
# K. Teardown
# ==========================================================================


class TestTeardown:
    async def test_no_listener_survives_the_test(self):
        """Nothing may be left listening after the harness tears down."""
        async with boot_gateway(_base_env()) as gw:
            port = gw.port
            assert port_is_listening("127.0.0.1", port), (
                "the server must be listening while the test runs"
            )
        await asyncio.sleep(0.3)
        assert not port_is_listening("127.0.0.1", port), (
            f"a listener survived the teardown on 127.0.0.1:{port}"
        )
