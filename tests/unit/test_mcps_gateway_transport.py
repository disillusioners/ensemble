"""Unit test for the WebSocket-level liveness mechanism (the transport).

WHITE BOX ON PURPOSE
--------------------

The independent conformance suite proves the CONTRACT — "the gateway emits
no protocol-layer frames outside a real §6.3 exchange". It cannot prove
the MECHANISM: from the wire, a gateway that merely went SILENT (no
pings at all) would satisfy the same contract, and the failure mode
would be a client reconnect-loop in production rather than a red test.

So this file tests the mechanism directly: the transport schedules and
sends WebSocket PING **control frames** on the expected cadence, and it
sends NOTHING at the protocol level to do it.

A WebSocket ping is a CONTROL frame (§7.5 of RFC 6455) — it is not a
text message, so §6.1 ("one JSON object per WebSocket text message") does
not apply to it, and the client library never surfaces it to the
protocol layer.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from daemon.routers.mcps_gateway import (
    PING_INTERVAL_SECONDS,
    UNAUTHORIZED_BODY,
    WebSocketGatewayTransport,
    _websocket_protocol,
    mcp_gateway_endpoint,
)


class _FakeWebSocket:
    """A Starlette-shaped WebSocket whose ASGI ``send`` reaches a protocol.

    The shape matters: the transport finds the protocol object through
    the ASGI ``send`` callable (``send.__self__``), so the fake puts a
    BOUND METHOD there — exactly what uvicorn hands to the app.
    """

    def __init__(self) -> None:
        self.sent: list[object] = []
        self.pings: list[object] = []
        self.closed = False
        self._send = self._sender

    # -- the ASGI send callable (a bound method, like uvicorn's) ---------
    def _sender(self, message: object) -> None:
        self.sent.append(message)

    # -- the Starlette surface the transport uses -------------------------
    async def send_text(self, text: str) -> None:
        self.sent.append(text)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = True

    # -- the WebSocket CONTROL frame --------------------------------------
    def ping(self, data: object = None) -> None:
        """A WebSocket ping control frame.

        The real protocol object's ``ping`` is a coroutine; this one
        returns ``None``, which is the "not awaitable" branch of the
        transport — so the test exercises the frame scheduling without
        the pong bookkeeping.
        """
        self.pings.append(data)


class _PinglessWebSocket(_FakeWebSocket):
    """A transport that CANNOT ping (an in-process test client)."""

    def __init__(self, scope: dict[str, Any] | None = None) -> None:
        super().__init__()
        # A plain function (not a bound method) → no ``__self__`` → the
        # protocol object is not reachable.
        self._send = lambda message: self.sent.append(message)  # noqa: E731
        #: The ASGI scope — the peer identity the liveness-degradation
        #: check reads (a real server connection has a real peer).
        self.scope = scope


class TestWebSocketLivenessMechanism:
    async def test_the_protocol_object_is_reachable_through_the_asgi_send(self):
        websocket = _FakeWebSocket()
        protocol = _websocket_protocol(websocket)
        assert protocol is websocket
        assert hasattr(protocol, "ping")

    async def test_the_transport_reports_that_it_can_ping(self):
        transport = WebSocketGatewayTransport(_FakeWebSocket())
        assert transport.can_ping is True

    async def test_the_transport_sends_pings_on_the_expected_cadence(self):
        """THE MECHANISM PROOF.

        The pings go out on the expected cadence (the reference center
        pings every 200 ms; the test uses 20 ms to stay fast), and they
        are CONTROL frames: NOT ONE protocol frame is written for them.
        """
        websocket = _FakeWebSocket()
        transport = WebSocketGatewayTransport(websocket)
        transport.start_liveness_pings(0.02)
        try:
            await asyncio.sleep(0.12)
        finally:
            await transport.stop_liveness_pings()

        assert len(websocket.pings) >= 3, (
            f"the transport sent {len(websocket.pings)} WebSocket pings in "
            "120 ms — the liveness mechanism is not running"
        )
        # The cadence is bounded: not a flood, not a single frame.
        assert len(websocket.pings) <= 20, (
            f"the transport sent {len(websocket.pings)} pings in 120 ms — "
            "the cadence is wrong"
        )
        # A ping is a CONTROL frame — it never reaches the protocol layer.
        assert websocket.sent == [], (
            f"the liveness mechanism wrote protocol frames: {websocket.sent}"
        )
        assert transport.pings_sent == len(websocket.pings)

    async def test_stopping_the_pings_stops_the_cadence(self):
        websocket = _FakeWebSocket()
        transport = WebSocketGatewayTransport(websocket)
        transport.start_liveness_pings(0.02)
        await asyncio.sleep(0.06)
        await transport.stop_liveness_pings()
        after_stop = len(websocket.pings)
        await asyncio.sleep(0.08)
        assert len(websocket.pings) == after_stop, "the pings kept going"

    async def test_a_transport_that_cannot_ping_does_not_break(self):
        """No protocol object (an in-process client): the transport still
        works, and it simply produces no evidence."""
        websocket = _PinglessWebSocket()
        transport = WebSocketGatewayTransport(websocket)
        assert transport.can_ping is False
        transport.start_liveness_pings(0.02)
        await asyncio.sleep(0.06)
        await transport.stop_liveness_pings()
        assert websocket.pings == []
        assert websocket.sent == []

    async def test_the_default_cadence_is_the_reference_cadence(self):
        """The reference center pings every 200 ms."""
        assert PING_INTERVAL_SECONDS == 0.2

    async def test_the_refusal_body_is_plain_text(self):
        """The refusal of the upgrade carries a plain-text body (the
        reference refusal shape)."""
        assert UNAUTHORIZED_BODY == b"unauthorized\n"


# ─────────────────────────────────────────────────────────────────────────────
# The route adapter — the liveness ping loop and its degradation
# ─────────────────────────────────────────────────────────────────────────────


class _FakeAppState:
    """The ``app.state`` seam the route reads the service from."""

    def __init__(self, service: Any) -> None:
        self.mcps_gateway_service = service


class _FakeApp:
    def __init__(self, service: Any) -> None:
        self.state = _FakeAppState(service)


class _RouteWebSocket(_FakeWebSocket):
    """A Starlette-shaped WebSocket for the ROUTE (not just the transport).

    It is the surface the route adapter uses: ``accept``, ``headers``,
    ``app.state`` and ``receive``.
    """

    def __init__(self, service: Any, *, scope: dict[str, Any] | None = None) -> None:
        super().__init__()
        self.app = _FakeApp(service)
        self.headers = {"authorization": "Bearer tok-alpha"}
        self.accepted = False
        self.scope = (
            scope
            if scope is not None
            else {
                "type": "websocket",
                "client": ("10.0.0.7", 51515),
                "server": ("10.0.0.1", 8079),
            }
        )

    async def accept(self) -> None:
        self.accepted = True

    async def receive(self) -> dict[str, Any]:
        return {"type": "websocket.disconnect"}


class _FailingSetupService:
    """A service whose connection setup FAILS — after a while.

    The delay is what makes the leak observable: the liveness ping loop
    is already running when the setup blows up.
    """

    def __init__(self, *, setup_seconds: float = 0.25) -> None:
        self.transport: Any = None
        self._setup_seconds = setup_seconds

    def authenticate(self, authorization: str | None) -> bool:
        return True

    async def open_connection(self, transport: Any) -> Any:
        self.transport = transport
        await asyncio.sleep(self._setup_seconds)
        raise RuntimeError("the connection setup failed")

    async def ensure_monitor(self) -> None:
        return None

    async def close_connection(self, connection: Any, reason: str = "") -> None:
        return None


class TestThePingLoopNeverSurvivesTheConnection:
    async def test_the_ping_loop_stops_when_the_connection_setup_fails(self):
        """FIX 3 — the ping loop must stop on EVERY exit path.

        The liveness ping loop used to be started BEFORE the connection
        setup, and the setup sat OUTSIDE the read loop's try/finally: a
        failure there (a cancelled open) left the 200 ms ping loop
        running on a dead socket FOREVER — only ``close_connection``
        stopped it, and it was never reached.
        """
        service = _FailingSetupService()
        websocket = _RouteWebSocket(service)

        await mcp_gateway_endpoint(websocket)

        assert service.transport is not None
        # The loop WAS running: it pinged while the setup was in flight.
        assert len(websocket.pings) >= 1, (
            "the liveness ping never fired — the test proves nothing"
        )
        assert service.transport._ping_task is None, (
            "the ping loop survived the failed connection setup"
        )
        pings_at_failure = len(websocket.pings)
        await asyncio.sleep(PING_INTERVAL_SECONDS * 2)
        assert len(websocket.pings) == pings_at_failure, (
            "the ping loop is still running on a dead socket"
        )


class TestTheLivenessDegradationIsVisible:
    def test_a_warning_is_emitted_when_the_pings_cannot_be_produced(self, caplog):
        """FIX 4 — the degradation must not be SILENT.

        A correct client reads the inbound silence as server death (§3.3)
        and reconnects in a loop; the daemon must be able to say WHY it
        went quiet. ONE warning per connection — never per frame and
        never per tick.
        """
        websocket = _PinglessWebSocket(
            scope={
                "type": "websocket",
                "client": ("10.0.0.7", 51515),
                "server": ("10.0.0.1", 8079),
            }
        )
        with caplog.at_level(logging.WARNING, logger="daemon.routers.mcps_gateway"):
            transport = WebSocketGatewayTransport(websocket)

        assert transport.can_ping is False
        warnings = [
            record
            for record in caplog.records
            if record.name == "daemon.routers.mcps_gateway"
            and record.levelno >= logging.WARNING
        ]
        assert len(warnings) == 1, (
            f"the liveness degradation produced {len(warnings)} warnings — "
            "it must be reported exactly once per connection"
        )
        assert "liveness" in warnings[0].getMessage()

    def test_the_in_process_test_client_is_not_a_degradation(self, caplog):
        """The in-process test client has NO protocol object by design —
        warning there would be a false alarm on every test connection."""
        websocket = _PinglessWebSocket(
            scope={
                "type": "websocket",
                "client": ("testclient", 50000),
                "server": ["testserver", 80],
            }
        )
        with caplog.at_level(logging.WARNING, logger="daemon.routers.mcps_gateway"):
            transport = WebSocketGatewayTransport(websocket)

        assert transport.can_ping is False
        warnings = [
            record
            for record in caplog.records
            if record.name == "daemon.routers.mcps_gateway"
            and record.levelno >= logging.WARNING
        ]
        assert warnings == []
