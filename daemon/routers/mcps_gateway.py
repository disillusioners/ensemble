"""The remote-hands gateway WebSocket route — the CENTER transport.

The route is the thin FastAPI adapter over
:class:`daemon.services.mcps_gateway.service.McpGatewayService`. It owns
three things and nothing else:

1. the UPGRADE authentication (§4.1) — the bearer enrollment token
   travels in the ``Authorization`` header of the upgrade request, and a
   refused upgrade produces NO protocol message at all;
2. the read loop — one JSON object per text frame (§6.1);
3. the teardown — a socket that dies kills the session it carried
   (§6.8).

THE 401 MATTERS
---------------

A refused upgrade answers **HTTP 401** — never 403. The shipped client
treats a 401 as fatal (``AUTH_FAILED`` → re-enroll out of band) and
treats anything else as a transient failure, so a 403 would put the
client into an endless reconnect loop. The denial response carries the
``WWW-Authenticate`` challenge and a plain-text body; the realm is
advisory (SPEC.md v0.1 is silent on the realm).

THE ROUTE IS NOT REGISTERED AT ALL WHEN THE GATEWAY IS OFF
-----------------------------------------------------------

``MCPS_GATEWAY_ENABLED`` is DEFAULT OFF and the registration site
(``daemon/api.py``) mirrors the conditional-registration precedent of the
auth router pair: when the switch is off the path does not exist and the
daemon answers 404. It is NOT "registered but rejecting".
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
from typing import Any, Callable

from fastapi import APIRouter, WebSocket
from fastapi.responses import Response

from daemon.services.mcps_gateway.config import AUTH_REALM
from daemon.services.mcps_gateway.service import (
    ClientConnection,
    McpGatewayService,
)

logger = logging.getLogger(__name__)

#: The body of a refused upgrade (plain text, as the reference refusal).
UNAUTHORIZED_BODY = b"unauthorized\n"

#: The DEFAULT cadence of the WebSocket-level liveness ping.
#:
#: §3.3: the client treats "the absence of any inbound frame of any
#: kind" as server death, so a center that only answers requests looks
#: dead to a CORRECT client — and the client then reconnects in a loop.
#: The evidence is a WebSocket **CONTROL** frame (a ping), which is
#: INVISIBLE to the protocol layer: §6.1 carries "one JSON object per
#: WebSocket text message", and a control frame is not a text message.
#: The reference center pings every 200 ms; this one does the same.
#:
#: The effective cadence is ``MCPS_GATEWAY_PING_INTERVAL_MS`` (the
#: default is this value) — the knob is wired into the route below.
PING_INTERVAL_SECONDS = 0.2


#: The peer host names Starlette's IN-PROCESS test client uses. A real
#: server connection carries a real socket peer, so there the absence of
#: an ASGI protocol object is a DEFECT; in the in-process client it is
#: the expected state (there is no protocol object at all).
_IN_PROCESS_PEER_HOSTS = ("testclient", "testserver")


def _websocket_protocol(websocket: WebSocket) -> Any | None:
    """The server's WebSocket protocol object, when it is reachable.

    Starlette's ``WebSocket`` exposes only the ASGI ``send`` callable,
    and uvicorn wraps that callable in a closure
    (``starlette.middleware.exceptions.wrap_app_handling_exceptions``),
    so the bound method that reaches the protocol object sits one closure
    cell deep. The protocol object is what can send a WebSocket PING
    CONTROL frame.

    ``None`` means the transport cannot ping (an in-process test client
    has no protocol object) — the liveness evidence is then simply absent,
    and NOTHING breaks: the pings are an optimisation of the transport,
    not part of the protocol.
    """
    send = getattr(websocket, "_send", None)
    candidates: list[Any] = [send]
    for cell in getattr(send, "__closure__", None) or ():
        try:
            candidates.append(cell.cell_contents)
        except ValueError:  # pragma: no cover — an empty cell
            continue
    for candidate in candidates:
        owner = getattr(candidate, "__self__", None)
        if owner is not None and hasattr(owner, "ping"):
            return owner
    return None


def _is_real_server_connection(websocket: WebSocket) -> bool:
    """Is this a REAL server connection rather than an in-process client?

    Starlette's in-process test client drives the ASGI app directly: it
    has no ASGI protocol object, so "the transport cannot ping" is the
    EXPECTED state there — a warning would be a false alarm on every
    test connection.

    A real server connection carries a real socket peer in the ASGI
    scope. There, a missing protocol object means the liveness evidence
    of §3.3 CANNOT be produced, and the degradation is SILENT: a correct
    client treats the inbound silence as server death and reconnects in
    a loop, with no daemon-side explanation. That is the case the
    warning exists for.
    """
    scope = getattr(websocket, "scope", None)
    if not isinstance(scope, dict):
        # Not a Starlette WebSocket at all (a hand-rolled fake): the
        # caller decides, so no production claim is made here.
        return False
    for key in ("client", "server"):
        peer = scope.get(key)
        if not isinstance(peer, (tuple, list)) or not peer:
            continue
        host = peer[0]
        if isinstance(host, str) and host.strip().lower() in _IN_PROCESS_PEER_HOSTS:
            return False
    return True


def _ping_interval_seconds(service: McpGatewayService) -> float:
    """The effective liveness-ping cadence, in seconds.

    The knob (``MCPS_GATEWAY_PING_INTERVAL_MS``) lives on the service's
    configuration — the service and the transport are constructed from
    the SAME boot-time config, so the cadence the route uses is the
    cadence the operator configured. The module constant is only the
    DEFAULT (the reference center's 200 ms).
    """
    interval_ms = getattr(
        getattr(service, "config", None), "ping_interval_ms", None
    )
    if not interval_ms or interval_ms <= 0:
        return PING_INTERVAL_SECONDS
    return interval_ms / 1000.0


class WebSocketGatewayTransport:
    """The :class:`GatewayTransport` over a Starlette ``WebSocket``.

    It also owns the **WebSocket-level liveness ping** — the transport
    evidence of §3.3. The ping is a CONTROL frame: it never reaches the
    protocol layer, so the gateway emits NO protocol frame that does not
    answer or initiate a real §6.3 exchange.
    """

    def __init__(self, websocket: WebSocket) -> None:
        self._websocket = websocket
        self._protocol = _websocket_protocol(websocket)
        self._ping_task: asyncio.Task | None = None
        self._ping_tasks: set[asyncio.Task] = set()
        #: How many WebSocket PING control frames this transport sent.
        #: The counter is the observable proof that the liveness evidence
        #: is produced at the TRANSPORT level (and not as a protocol
        #: frame).
        self.pings_sent = 0
        if self._protocol is None and _is_real_server_connection(websocket):
            # ONE warning per connection — never per frame and never per
            # tick. The degradation is otherwise SILENT: a correct client
            # reads the inbound silence as server death (§3.3) and
            # reconnects in a loop, and the daemon would have nothing to
            # say about it.
            logger.warning(
                "the remote-hands gateway cannot produce the WebSocket "
                "liveness ping on this connection: the ASGI protocol object "
                "is not reachable (the transport layout changed). A correct "
                "client reads the inbound silence as server death (§3.3) and "
                "will reconnect in a loop."
            )

    async def send_json(self, payload: dict[str, Any]) -> None:
        await self._websocket.send_text(json.dumps(payload))

    async def close(self, code: int = 1000, reason: str = "") -> None:
        await self.stop_liveness_pings()
        try:
            await self._websocket.close(code, reason)
        except Exception:  # noqa: BLE001 — a dead socket is not an error
            pass

    # ── the liveness ping (§3.3) ─────────────────────────────────────────

    @property
    def can_ping(self) -> bool:
        """Can this transport send a WebSocket ping at all?"""
        return self._protocol is not None

    def start_liveness_pings(self, interval_seconds: float = PING_INTERVAL_SECONDS) -> None:
        """Start the liveness ping loop (idempotent)."""
        if self._protocol is None or self._ping_task is not None:
            return
        self._ping_task = asyncio.create_task(self._ping_loop(interval_seconds))

    async def stop_liveness_pings(self) -> None:
        """Stop the liveness ping loop."""
        task, self._ping_task = self._ping_task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        for pending in list(self._ping_tasks):
            pending.cancel()
        self._ping_tasks.clear()

    async def _ping_loop(self, interval_seconds: float) -> None:
        try:
            while True:
                await asyncio.sleep(interval_seconds)
                await self._ping_once()
        except asyncio.CancelledError:  # pragma: no cover — shutdown
            raise
        except Exception as exc:  # noqa: BLE001 — the pings never break the socket
            logger.debug("the remote-hands liveness ping loop stopped: %s", exc)

    async def _ping_once(self) -> None:
        """Send ONE WebSocket ping control frame.

        The pong is awaited by the protocol object; the await is what
        makes it a coroutine, so the task is scheduled rather than
        awaited here — the ping frame goes out on the next loop turn.
        """
        protocol = self._protocol
        if protocol is None:
            return
        try:
            result = protocol.ping()
        except Exception as exc:  # noqa: BLE001 — a closed socket
            logger.debug("the remote-hands liveness ping could not be sent: %s", exc)
            return
        self.pings_sent += 1
        if not inspect.isawaitable(result):
            return
        task = asyncio.ensure_future(result)
        self._ping_tasks.add(task)
        task.add_done_callback(self._ping_done)

    def _ping_done(self, task: asyncio.Task) -> None:
        self._ping_tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.debug("the remote-hands liveness ping was not answered: %s", exc)


def _service_of(websocket: WebSocket) -> McpGatewayService | None:
    """The gateway service of the running app.

    ``app.state`` is the daemon-wide seam (the same shape the settings
    router and the workspace router use). ``None`` means the lifespan
    never wired it — a boot-order defect, answered with 503.
    """
    return getattr(websocket.app.state, "mcps_gateway_service", None)


async def mcp_gateway_endpoint(websocket: WebSocket) -> None:
    """The one route of the remote-hands gateway.

    The upgrade carries the credential; a refused upgrade exchanges NO
    protocol message (§3.1).
    """
    service = _service_of(websocket)
    if service is None:
        logger.error(
            "the remote-hands gateway route was reached without a wired service"
        )
        await websocket.send_denial_response(
            Response(
                status_code=503,
                content=b"gateway unavailable\n",
                media_type="text/plain",
            )
        )
        return

    authorization = websocket.headers.get("authorization")
    if not service.authenticate(authorization):
        logger.warning(
            "a remote-hands upgrade was refused: the credential is unknown "
            "or absent"
        )
        await websocket.send_denial_response(
            Response(
                status_code=401,
                headers={"WWW-Authenticate": f'Bearer realm="{AUTH_REALM}"'},
                content=UNAUTHORIZED_BODY,
                media_type="text/plain",
            )
        )
        return

    await websocket.accept()
    transport = WebSocketGatewayTransport(websocket)
    connection: ClientConnection | None = None
    try:
        # The liveness evidence of §3.3 — a WebSocket CONTROL frame, never
        # a protocol frame. It is started INSIDE the try: the setup below
        # can fail (a cancelled open), and a ping loop left running on a
        # dead socket would ping it FOREVER.
        transport.start_liveness_pings(_ping_interval_seconds(service))
        connection = await service.open_connection(transport)
        await service.ensure_monitor()
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                break
            text = message.get("text")
            if text is None:
                # A binary frame is not part of the protocol (§6.1: one
                # JSON object per WebSocket text message).
                continue
            await service.handle_frame(connection, text)
    except Exception as exc:  # noqa: BLE001 — the socket died
        logger.warning("the remote-hands socket of the machine failed: %s", exc)
    finally:
        try:
            if connection is not None:
                await service.close_connection(connection, "the client disconnected")
        finally:
            # The ping loop MUST stop on EVERY exit path. The normal path
            # stops it inside ``close_connection`` (which closes the
            # transport); this is the net for the paths where the
            # connection was never established, so no 200 ms ping loop
            # survives on a dead socket.
            await transport.stop_liveness_pings()


def build_mcps_gateway_router(
    *,
    path: str,
    service_provider: Callable[[], McpGatewayService | None] | None = None,
) -> tuple[APIRouter, bool]:
    """Build the router of the gateway.

    Args:
        path: The FULL path of the route (default
            ``/api/mcps-gateway/ws``).
        service_provider: An optional override of the service lookup —
            the tests use it to drive the route without a daemon app.
            The production path reads ``app.state``.

    Returns:
        ``(router, mounts_under_api)`` — ``mounts_under_api`` is True
        when the configured path lives under the ``/api`` namespace, so
        the registration site mounts the router inside ``api_router``
        (whose prefix is ``/api``) rather than at the app level.
    """
    normalized = "/" + path.strip("/")
    prefix, _, tail = normalized.rpartition("/")
    prefix = prefix or ""
    tail = ("/" + tail) if tail else "/ws"

    # The router is mounted inside ``api_router`` (whose prefix is
    # ``/api``) when the configured path lives under the /api namespace.
    # The /api segment is therefore STRIPPED from the router prefix —
    # keeping it would produce /api/api/mcps-gateway/ws.
    if prefix == "/api" or prefix.startswith("/api/"):
        under_api = True
        router_prefix = prefix[len("/api") :] or ""
    else:
        under_api = False
        router_prefix = prefix

    router = APIRouter(prefix=router_prefix, tags=["mcp-gateway"])

    if service_provider is None:

        async def _default_endpoint(websocket: WebSocket) -> None:
            await mcp_gateway_endpoint(websocket)

    else:

        async def _default_endpoint(websocket: WebSocket) -> None:
            service = service_provider()
            if service is None:
                await websocket.send_denial_response(
                    Response(
                        status_code=503,
                        content=b"gateway unavailable\n",
                        media_type="text/plain",
                    )
                )
                return
            authorization = websocket.headers.get("authorization")
            if not service.authenticate(authorization):
                await websocket.send_denial_response(
                    Response(
                        status_code=401,
                        headers={"WWW-Authenticate": f'Bearer realm="{AUTH_REALM}"'},
                        content=UNAUTHORIZED_BODY,
                        media_type="text/plain",
                    )
                )
                return
            await websocket.accept()
            transport = WebSocketGatewayTransport(websocket)
            connection: ClientConnection | None = None
            try:
                # Started inside the try: the setup below can fail, and a
                # ping loop left running on a dead socket would ping it
                # FOREVER.
                transport.start_liveness_pings(_ping_interval_seconds(service))
                connection = await service.open_connection(transport)
                await service.ensure_monitor()
                while True:
                    message = await websocket.receive()
                    if message["type"] == "websocket.disconnect":
                        break
                    text = message.get("text")
                    if text is None:
                        continue
                    await service.handle_frame(connection, text)
            finally:
                try:
                    if connection is not None:
                        await service.close_connection(
                            connection, "the client disconnected"
                        )
                finally:
                    # The ping loop MUST stop on EVERY exit path (the
                    # normal path stops it inside ``close_connection``).
                    await transport.stop_liveness_pings()

    router.add_api_websocket_route(
        tail,
        _default_endpoint,
        name="mcp_gateway_ws",
    )
    return router, under_api


__all__ = [
    "PING_INTERVAL_SECONDS",
    "UNAUTHORIZED_BODY",
    "WebSocketGatewayTransport",
    "build_mcps_gateway_router",
    "mcp_gateway_endpoint",
]
