"""The remote-hands gateway service — the CENTER side of SPEC.md v0.1.

The module is importable WITHOUT the FastAPI app, so the unit tests can
drive the whole protocol against a fake transport. The FastAPI router
(``daemon/routers/mcps_gateway.py``) is a thin adapter over it.

Direction of the protocol (dossier correction #2)
-------------------------------------------------

* The MACHINE sends exactly two protocol messages: ``hello`` (a request)
  and ``heartbeat`` (a notification).
* The CENTER sends the four MCP methods — ``session.begin``,
  ``session.end``, ``tools/list``, ``tools/call`` — as REQUESTS, and the
  machine answers with a ``response`` envelope.

The machine MINTS the ``session_id`` (§6.4) and the center adopts it.

Every frame the center writes carries ``v: "0.1"`` — the shipped client
checks the version of EVERY inbound message and closes the socket
silently (no error frame) on a missing or wrong ``v``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from daemon.services.timestamps import now_utc_iso

from .audit import (
    OUTCOME_ERROR,
    OUTCOME_OK,
    OUTCOME_TIMEOUT,
    AuditRecord,
    AuditSink,
    NullAuditSink,
    hash_args,
)
from .config import GatewayConfig, load_gateway_config
from .protocol import (
    CODE_CAPABILITY_MISSING,
    CODE_HEARTBEAT_TIMEOUT,
    CODE_INTERNAL,
    CODE_MACHINE_BUSY,
    CODE_MACHINE_NOT_ENROLLED,
    CODE_PROTOCOL_VERSION_UNSUPPORTED,
    CODE_TOOL_TIMEOUT,
    METHOD_HEARTBEAT,
    METHOD_HELLO,
    METHOD_NOTIFICATIONS_PREFIX,
    METHOD_SESSION_BEGIN,
    METHOD_SESSION_END,
    METHOD_TOOLS_CALL,
    PROTOCOL_VERSION,
    REQUIRED_CLIENT_CAPABILITIES,
    SERVER_CAPABILITIES,
    SERVER_NAME,
    TYPE_ERROR,
    TYPE_NOTIFICATION,
    TYPE_REQUEST,
    TYPE_RESPONSE,
    GatewayProtocolError,
    make_envelope,
    make_error_envelope,
)
from .sessions import GatewaySession, SessionManager
from .store import InMemoryMachineStore, MachineStore

logger = logging.getLogger(__name__)

#: The cadence of the housekeeping sweep (the heartbeat check and the
#: session expiry). It is SHORT — and it is DERIVED from the heartbeat
#: timeout — so that the kill fires ON SCHEDULE rather than at the next
#: poll: the value the center advertises as ``heartbeat_timeout_ms``
#: (§3.3 — "how long the server tolerates silence before it declares the
#: session dead") has to BE the tolerance, and the only slack allowed is
#: the sweep granularity.
#:
#: There is NO grace on top of the timeout: a kill at "timeout + grace"
#: would make the advertisement a lie, and a client that trusts the
#: advertisement must not be wrong about when the server will declare it
#: dead. A heartbeat that is IN FLIGHT when the sweep runs is not
#: punished: the sweep reads the CURRENT last-heartbeat stamp, and
#: §3.3's timeout is already three times the heartbeat interval.
DEFAULT_MONITOR_INTERVAL_SECONDS = 0.1


class GatewayTransport(Protocol):
    """What the service needs from a live client socket."""

    async def send_json(self, payload: dict[str, Any]) -> None:
        """Write one envelope as one text frame."""

    async def close(self, code: int = 1000, reason: str = "") -> None:
        """Close the socket."""


@dataclass
class ClientConnection:
    """One live client socket."""

    connection_id: str
    transport: GatewayTransport
    machine_id: str = ""
    hello_received: bool = False
    connected_at: float = 0.0
    last_heartbeat: float = 0.0
    pending: dict[str, asyncio.Future] = field(default_factory=dict)
    closed: bool = False


class McpGatewayService:
    """The center of the remote-hands protocol."""

    def __init__(
        self,
        config: GatewayConfig | None = None,
        *,
        store: MachineStore | None = None,
        audit_sink: AuditSink | None = None,
        clock: Callable[[], float] = time.time,
        monitor_interval_seconds: float = DEFAULT_MONITOR_INTERVAL_SECONDS,
        server_version: str = "0.1.0",
    ) -> None:
        self.config = config if config is not None else load_gateway_config()
        self._store = store if store is not None else InMemoryMachineStore()
        self._audit = audit_sink if audit_sink is not None else NullAuditSink()
        self._clock = clock
        self._server_version = server_version

        self._sessions = SessionManager(
            session_ttl_ms=self.config.session_ttl_ms,
            session_idle_timeout_ms=self.config.session_idle_timeout_ms,
        )
        self._connections: dict[str, ClientConnection] = {}
        self._by_machine: dict[str, ClientConnection] = {}
        self._request_counter = 0
        self._audit_failures = 0
        self._lock = asyncio.Lock()
        self._monitor_task: asyncio.Task | None = None
        # The sweep granularity is derived from the timeout so that the
        # detection latency stays a small FRACTION of the advertised
        # tolerance (a 3 s timeout is detected within 100 ms — 3 %).
        # With a static interval a short timeout would be detected late
        # and the advertised value would not be the real one.
        timeout_seconds = max(0.05, (self.config.heartbeat_timeout_ms or 1) / 1000.0)
        self._monitor_interval_seconds = min(
            monitor_interval_seconds, max(0.02, timeout_seconds / 10.0)
        )

    def _assert_enabled(self) -> None:
        """Refuse while the transport is off.

        The kill switch (``MCPS_GATEWAY_ENABLED``) is read at BOOT time:
        the service is constructed with the boot-time config (the
        ``load_gateway_config()`` snapshot taken in ``manager.__init__``),
        so the gate answers the value the daemon BOOTED with — NOT the
        CURRENT value of the environment. That is deliberate and it
        matches the boot-gated route registration: the WebSocket route
        is registered (or not) at boot, so the service and the transport
        can never disagree about whether the gateway is on. Flipping the
        knob therefore requires a daemon restart, exactly like the
        route it guards.
        """
        if not self.config.enabled:
            raise GatewayProtocolError(
                CODE_INTERNAL,
                "the remote-hands gateway is disabled",
                {"enabled": False},
            )

    # ── properties ───────────────────────────────────────────────────────

    @property
    def sessions(self) -> SessionManager:
        """The session manager of the center."""
        return self._sessions

    @property
    def store(self) -> MachineStore:
        """The machine roster of the center."""
        return self._store

    @property
    def audit_failures(self) -> int:
        """How many audit writes have failed (the counter of §audit)."""
        return self._audit_failures

    # ── upgrade auth (§4.1) ──────────────────────────────────────────────

    def authenticate(self, authorization: str | None) -> bool:
        """Is the credential on the upgrade request acceptable?

        The credential travels in the ``Authorization: Bearer <token>``
        header of the WSS UPGRADE request (§3.1) — never in a protocol
        message. The comparison is CONSTANT TIME (``hmac.compare_digest``)
        over EVERY configured token, and there is no plaintext downgrade.

        An EMPTY token set means NOBODY may enroll: the gate is fail
        closed, and a gateway that is enabled without tokens refuses
        every upgrade.
        """
        if not self.config.has_enrollment_tokens:
            logger.warning(
                "the remote-hands gateway has no enrollment tokens; every "
                "upgrade is refused (fail closed)"
            )
            return False
        if not authorization:
            return False
        parts = authorization.split(None, 1)
        if len(parts) != 2 or parts[0].lower() != "bearer":
            return False
        candidate = parts[1].strip()
        if not candidate:
            return False
        return self.config.token_is_valid(candidate)

    # ── connection lifecycle ─────────────────────────────────────────────

    async def open_connection(self, transport: GatewayTransport) -> ClientConnection:
        """Register a freshly upgraded socket."""
        connection_id = f"conn-{len(self._connections) + 1:04d}-{int(self._clock() * 1000) % 100000}"
        connection = ClientConnection(
            connection_id=connection_id,
            transport=transport,
            connected_at=self._clock(),
            last_heartbeat=self._clock(),
        )
        async with self._lock:
            self._connections[connection_id] = connection
        return connection

    async def close_connection(self, connection: ClientConnection, reason: str = "") -> None:
        """Tear a connection down: kill its session, then close the socket.

        A session belongs to its connection (§6.8): the session is KILLED,
        never paused, and its id is dead afterwards.
        """
        if connection.closed:
            return
        connection.closed = True
        async with self._lock:
            self._connections.pop(connection.connection_id, None)
            if self._by_machine.get(connection.machine_id) is connection:
                self._by_machine.pop(connection.machine_id, None)
        killed = self._sessions.end_for_connection(connection.connection_id)
        for session in killed:
            logger.warning(
                "the session %s was killed: the connection of the machine %s is gone (%s)",
                session.session_id,
                session.machine_id,
                reason or "connection closed",
            )
        for future in list(connection.pending.values()):
            if not future.done():
                future.set_exception(
                    GatewayProtocolError(
                        CODE_INTERNAL,
                        f"the connection was closed ({reason or 'closed'})",
                    )
                )
        connection.pending.clear()
        try:
            await connection.transport.close()
        except Exception:  # noqa: BLE001 — a dead socket is not an error
            pass
        if connection.machine_id:
            self._store.mark_offline(connection.machine_id, connection.connection_id)

    # ── inbound frames ───────────────────────────────────────────────────

    async def handle_frame(self, connection: ClientConnection, raw: str) -> None:
        """Handle one text frame from the client.

        One JSON object per WebSocket message (§6.1); no batching in v0.1.
        """
        import json

        try:
            payload = json.loads(raw)
        except (ValueError, TypeError):
            logger.warning("the machine sent an unreadable frame; it is ignored")
            return
        if not isinstance(payload, dict):
            logger.warning("the machine sent a frame that is not an object; it is ignored")
            return

        version = payload.get("v")
        if version is not None and version != PROTOCOL_VERSION:
            # §5.2 forbids a silent downgrade. The strict refusal is the
            # hello exchange; a later frame of a wrong version is refused
            # too — but the connection is NOT killed, because the spec
            # names no failure mode for it.
            logger.warning(
                "the machine sent a frame of the protocol version %r; it is ignored",
                version,
            )
            return

        message_type = payload.get("type")
        if message_type == TYPE_REQUEST:
            await self._handle_request(connection, payload)
        elif message_type == TYPE_NOTIFICATION:
            self._handle_notification(connection, payload)
        elif message_type in (TYPE_RESPONSE, TYPE_ERROR):
            self._deliver(connection, payload)
        else:
            logger.warning(
                "the machine sent a frame of an unknown type %r; it is ignored",
                message_type,
            )

    async def _handle_request(
        self, connection: ClientConnection, payload: dict[str, Any]
    ) -> None:
        method = payload.get("method")
        request_id = payload.get("id")
        if method == METHOD_HELLO:
            await self._handle_hello(connection, payload)
            return
        # The machine sends no request other than hello. The reference
        # client answers an unknown method with INTERNAL; so does the
        # center.
        logger.warning("the machine sent the request %r; it is not part of the protocol", method)
        await self._send(
            connection,
            make_error_envelope(
                id=request_id,
                # §6.1: the session_id is required "after a session
                # exists" — an error frame sent while a session is open
                # carries it.
                session_id=self._live_session_id(connection),
                error=GatewayProtocolError(
                    CODE_INTERNAL,
                    f"the method {method!r} is not part of the protocol",
                ),
            ),
        )

    def _live_session_id(self, connection: ClientConnection) -> str | None:
        """The id of the LIVE session of the connection's machine.

        §6.1: ``session_id`` is "the session this message belongs to …
        absent on hello and on pre-session errors". An error frame sent
        while a session is open carries it; an error for an UNKNOWN
        session carries none (the id was never validated).
        """
        if not connection.machine_id:
            return None
        session = self._sessions.for_machine(connection.machine_id)
        if session is None or session.connection_id != connection.connection_id:
            return None
        return session.session_id

    def _handle_notification(
        self, connection: ClientConnection, payload: dict[str, Any]
    ) -> None:
        method = payload.get("method")
        if method == METHOD_HEARTBEAT:
            # The heartbeat is the ONLY liveness refresh of the machine.
            # The shipped client heartbeats on a ticker regardless of the
            # other traffic, so a refresh on any frame would keep a
            # machine alive that stopped heartbeating.
            connection.last_heartbeat = self._clock()
            if connection.machine_id:
                self._store.heartbeat(connection.machine_id, connection.last_heartbeat)
            return
        if isinstance(method, str) and method.startswith(METHOD_NOTIFICATIONS_PREFIX):
            # The v0.1 center relays no notification (§5.3 advertises
            # ``notifications: false``). The frame is accepted and dropped.
            logger.debug("a forwarded notification was dropped: %s", method)
            return
        logger.warning("the machine sent the notification %r; it is ignored", method)

    def _deliver(self, connection: ClientConnection, payload: dict[str, Any]) -> None:
        """Route a response/error of the machine to the waiting request."""
        request_id = payload.get("id") or ""
        future = connection.pending.get(request_id)
        if future is None or future.done():
            return
        if payload.get("type") == TYPE_ERROR:
            error = payload.get("error") or {}
            future.set_exception(
                GatewayProtocolError(
                    str(error.get("code") or CODE_INTERNAL),
                    str(error.get("message") or ""),
                    error.get("data") if isinstance(error.get("data"), dict) else None,
                )
            )
            return
        result = payload.get("result")
        future.set_result(result if isinstance(result, dict) else {"result": result})

    # ── the hello exchange (§5) ──────────────────────────────────────────

    async def _handle_hello(
        self, connection: ClientConnection, payload: dict[str, Any]
    ) -> None:
        request_id = payload.get("id")
        params = payload.get("params")
        params = params if isinstance(params, dict) else {}

        if connection.hello_received:
            # §5.1: the hello exchange happens ONCE per connection.
            await self._send(
                connection,
                make_error_envelope(
                    id=request_id,
                    error=GatewayProtocolError(
                        CODE_INTERNAL,
                        "the hello exchange happens once per connection",
                    ),
                ),
            )
            await self.close_connection(connection, "a second hello")
            return

        requested_version = str(params.get("protocol_version") or "")
        if requested_version != PROTOCOL_VERSION:
            # §5.2: the server does NOT silently downgrade.
            await self._send(
                connection,
                make_error_envelope(
                    id=request_id,
                    error=GatewayProtocolError(
                        CODE_PROTOCOL_VERSION_UNSUPPORTED,
                        f"protocol version {requested_version or '(none)'} is not "
                        f"supported by this center",
                        {"requested": requested_version, "supported": [PROTOCOL_VERSION]},
                    ),
                ),
            )
            await self.close_connection(connection, "unsupported protocol version")
            return

        machine_id = str(params.get("machine_id") or "")
        if not machine_id or not self.config.machine_allowed(machine_id):
            await self._send(
                connection,
                make_error_envelope(
                    id=request_id,
                    error=GatewayProtocolError(
                        CODE_MACHINE_NOT_ENROLLED,
                        f"the machine {machine_id or '(none)'!r} is not enrolled at this center",
                        {"machine_id": machine_id},
                    ),
                ),
            )
            await self.close_connection(connection, "machine not enrolled")
            return

        capabilities = params.get("capabilities")
        capabilities = capabilities if isinstance(capabilities, dict) else {}
        missing = [
            name for name in REQUIRED_CLIENT_CAPABILITIES if not capabilities.get(name)
        ]
        if missing:
            await self._send(
                connection,
                make_error_envelope(
                    id=request_id,
                    error=GatewayProtocolError(
                        CODE_CAPABILITY_MISSING,
                        f"the machine does not support the capabilities {missing}",
                        {
                            "missing": missing,
                            "required": list(REQUIRED_CLIENT_CAPABILITIES),
                        },
                    ),
                ),
            )
            await self.close_connection(connection, "capability missing")
            return

        # The handshake is over: the machine owes the center a heartbeat
        # (§3.3), and the silence of the machine starts now.
        now = self._clock()
        connection.machine_id = machine_id
        connection.hello_received = True
        connection.last_heartbeat = now
        self._store.upsert(machine_id, connection.connection_id, now)
        async with self._lock:
            self._by_machine[machine_id] = connection

        client = params.get("client")
        client = client if isinstance(client, dict) else {}
        logger.info(
            "the machine %r (%s %s) completed the hello exchange",
            machine_id,
            client.get("name", "?"),
            client.get("version", "?"),
        )

        await self._send(
            connection,
            make_envelope(
                type=TYPE_RESPONSE,
                id=request_id,
                result={
                    "protocol_version": PROTOCOL_VERSION,
                    "server": {"name": SERVER_NAME, "version": self._server_version},
                    "capabilities": dict(SERVER_CAPABILITIES),
                    "heartbeat_interval_ms": self.config.heartbeat_interval_ms,
                    "heartbeat_timeout_ms": self.config.heartbeat_timeout_ms,
                    "client_dead_detect_ms": self.config.client_dead_detect_ms,
                },
            ),
        )

    # ── outbound requests (the center is the REQUESTOR) ──────────────────

    def _next_request_id(self) -> str:
        self._request_counter += 1
        return f"ctr-{self._request_counter:04d}"

    async def _send(self, connection: ClientConnection, payload: dict[str, Any]) -> None:
        try:
            await connection.transport.send_json(payload)
        except Exception as exc:  # noqa: BLE001 — a dead socket is not a crash
            logger.warning("the frame could not be sent to the machine: %s", exc)

    async def _request(
        self,
        connection: ClientConnection,
        method: str,
        params: dict[str, Any],
        *,
        session_id: str | None = None,
        timeout_ms: int | None = None,
    ) -> dict[str, Any]:
        """Send one request to the machine and await its answer."""
        request_id = self._next_request_id()
        envelope = make_envelope(
            type=TYPE_REQUEST,
            id=request_id,
            session_id=session_id,
            method=method,
            params=params,
        )
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        connection.pending[request_id] = future
        try:
            await connection.transport.send_json(envelope)
            timeout_s = (timeout_ms or self.config.command_timeout_ms) / 1000.0
            return await asyncio.wait_for(future, timeout=timeout_s)
        except asyncio.TimeoutError:
            raise GatewayProtocolError(
                CODE_TOOL_TIMEOUT,
                f"{method} exceeded the center deadline of {timeout_ms or self.config.command_timeout_ms} ms",
                {"method": method, "timeout_ms": timeout_ms or self.config.command_timeout_ms},
            ) from None
        finally:
            connection.pending.pop(request_id, None)

    # ── the housekeeping sweep ───────────────────────────────────────────

    async def ensure_monitor(self) -> None:
        """Start the housekeeping sweep (idempotent).

        The sweep is started with the FIRST connection and lives until the
        last one is gone — the center has no background task when no
        machine is connected.
        """
        if self._monitor_task is not None and not self._monitor_task.done():
            return
        self._monitor_task = asyncio.create_task(self._monitor_loop())

    async def _monitor_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(self._monitor_interval_seconds)
                await self.check_heartbeats()
                await self.expire_sessions()
            except asyncio.CancelledError:  # pragma: no cover — shutdown
                raise
            except Exception as exc:  # noqa: BLE001 — the sweep never dies
                logger.error("the remote-hands housekeeping sweep failed: %s", exc)

    # ── the heartbeat monitor (§3.3 / §6.8) ──────────────────────────────

    async def check_heartbeats(self) -> list[ClientConnection]:
        """Kill the machines that stopped heartbeating.

        Heartbeat loss is a KILL, not a pause (§6.8): the session is not
        resumable, so the session dies with the machine and the machine
        is expected to reconnect and start over.
        """
        now = self._clock()
        # The kill fires at EXACTLY the advertised ``heartbeat_timeout_ms``
        # (§3.3) — there is no grace on top of it.
        limit_ms = self.config.heartbeat_timeout_ms
        killed: list[ClientConnection] = []
        for connection in list(self._connections.values()):
            if connection.closed or not connection.hello_received:
                continue
            silence_ms = (now - connection.last_heartbeat) * 1000.0
            if silence_ms <= limit_ms:
                continue
            killed.append(connection)
            logger.warning(
                "the machine %r was silent for %d ms; its session is killed",
                connection.machine_id,
                int(silence_ms),
            )
            # The session dies FIRST (it is not resumable), then the
            # refusal goes out in-band, then the socket closes.
            self._sessions.end_for_connection(connection.connection_id)
            await self._send(
                connection,
                make_error_envelope(
                    id=None,
                    error=GatewayProtocolError(
                        CODE_HEARTBEAT_TIMEOUT,
                        "the machine stopped heartbeating; the session is dead",
                        {"machine_id": connection.machine_id},
                    ),
                ),
            )
            await self.close_connection(connection, "heartbeat timeout")
        return killed

    async def expire_sessions(self) -> list[GatewaySession]:
        """Drop the sessions that passed their TTL or their idle timeout."""
        expired = self._sessions.expire_due(self._clock())
        for session in expired:
            logger.warning(
                "the session %s expired (ttl or idle timeout)", session.session_id
            )
        return expired

    # ── the agent-facing surface (§2.2) ──────────────────────────────────

    def roster(self) -> list[dict[str, Any]]:
        """The roster of the enrolled machines, with presence.

        R2: the roster is part of the agent-facing surface, so it
        REFUSES while the transport is off — exactly like connect,
        command and disconnect. A roster that answers while the gateway
        is disabled would tell the agent that the surface is healthy
        when the transport is dead.
        """
        self._assert_enabled()
        now = self._clock()
        return [
            record.to_public_dict(now, self.config.heartbeat_timeout_ms)
            for record in self._store.roster()
        ]

    async def connect(
        self,
        *,
        machine_id: str,
        agent_id: str,
        instance_id: str = "",
        reason: str = "",
    ) -> dict[str, Any]:
        """Open a session on a machine and return its catalog.

        The center sends the ``session.begin`` request; the machine
        answers with the ``session_id`` it minted and the tool catalog
        (§6.4).
        """
        self._assert_enabled()

        record = self._store.get(machine_id)
        if record is None:
            # R3: the code is the same (the v0.1 taxonomy has no
            # MACHINE_OFFLINE), but the DETAIL says which case it is:
            # a machine that is NOT on the roster has to be enrolled.
            raise GatewayProtocolError(
                CODE_MACHINE_NOT_ENROLLED,
                f"the machine {machine_id!r} is not in the enrolled roster — "
                "re-enrollment is required",
                {"machine_id": machine_id, "enrolled": False},
            )
        connection = self._by_machine.get(machine_id)
        if connection is None or connection.closed:
            # R3: the machine IS enrolled — it is merely offline. An
            # agent that reads this must NOT think a re-enrollment is
            # needed; it must wait for the machine to reconnect (§8.1
            # reconnect tolerance).
            raise GatewayProtocolError(
                CODE_MACHINE_NOT_ENROLLED,
                f"the machine {machine_id!r} is enrolled but currently "
                "offline — reconnect pending",
                {"machine_id": machine_id, "enrolled": True, "presence": "offline"},
            )

        # §8.3 — the center enforces the lock itself and refuses BEFORE
        # any frame reaches the machine.
        self._sessions.assert_machine_free(machine_id)

        # §8.2 — one agent drives one remote at a time.
        held = self._sessions.for_agent(agent_id)
        if held is not None and held.machine_id != machine_id:
            raise GatewayProtocolError(
                CODE_MACHINE_BUSY,
                f"the agent already drives the machine {held.machine_id!r}",
                {
                    "reason": "agent_already_holds_session",
                    "agent_id": agent_id,
                    "session_id": held.session_id,
                    "machine_id": held.machine_id,
                },
            )

        result = await self._request(
            connection,
            METHOD_SESSION_BEGIN,
            {"machine_id": machine_id, "context": {"agent_id": agent_id, "reason": reason}},
        )

        session_id = str(result.get("session_id") or "")
        answered_machine = str(result.get("machine_id") or machine_id)
        if not session_id:
            raise GatewayProtocolError(
                CODE_INTERNAL,
                "the machine answered the session.begin without a session id",
                {"machine_id": machine_id},
            )
        if answered_machine != machine_id:
            raise GatewayProtocolError(
                CODE_INTERNAL,
                f"the machine answered the session.begin for {answered_machine!r}",
                {"requested": machine_id, "answered": answered_machine},
            )

        catalog = result.get("tools")
        catalog = list(catalog) if isinstance(catalog, list) else []
        session = self._sessions.begin(
            session_id=session_id,
            machine_id=machine_id,
            agent_id=agent_id,
            instance_id=instance_id,
            connection_id=connection.connection_id,
            catalog=catalog,
            now=self._clock(),
        )
        self._store.bind_session(machine_id, session_id, agent_id)
        return {
            "session_id": session.session_id,
            "machine_id": session.machine_id,
            "tools": catalog,
            "expires_at_epoch_ms": int(session.expires_at * 1000),
        }

    async def command(
        self,
        *,
        agent_id: str,
        tool: str,
        arguments: dict[str, Any] | None = None,
        instance_id: str = "",
    ) -> dict[str, Any]:
        """Forward ONE ``tools/call`` into the active session of the agent.

        The ``params`` of the forwarded call is the MCP object UNWRAPPED
        (``{"name": ..., "arguments": ...}``) and the result is the RAW
        MCP result — the center adds no wrapping layer in either
        direction (§7: MCP travels unchanged through the middle).
        """
        self._assert_enabled()

        session = self._sessions.for_agent(agent_id)
        if session is None:
            raise SessionManager.session_not_found(agent_id)

        connection = self._connections.get(session.connection_id)
        if connection is None or connection.closed:
            raise SessionManager.session_not_found(session.session_id)

        now = self._clock()
        if now >= session.expires_at or (
            now - session.last_activity
        ) >= (self.config.session_idle_timeout_ms / 1000.0):
            self._sessions.end(session.session_id)
            self._store.clear_session(session.machine_id, session.session_id)
            raise SessionManager.session_expired(session.session_id)

        try:
            result = await self._request(
                connection,
                METHOD_TOOLS_CALL,
                {"name": tool, "arguments": arguments or {}},
                session_id=session.session_id,
            )
        except GatewayProtocolError as exc:
            outcome = OUTCOME_TIMEOUT if exc.code == CODE_TOOL_TIMEOUT else OUTCOME_ERROR
            await self._audit_command(
                agent_id=agent_id,
                instance_id=instance_id,
                machine_id=session.machine_id,
                tool=tool,
                arguments=arguments,
                session_id=session.session_id,
                outcome=outcome,
                error_code=exc.code,
                detail=exc.message,
            )
            raise
        self._sessions.touch(session.session_id, now)
        await self._audit_command(
            agent_id=agent_id,
            instance_id=instance_id,
            machine_id=session.machine_id,
            tool=tool,
            arguments=arguments,
            session_id=session.session_id,
            outcome=OUTCOME_OK,
        )
        return result

    async def disconnect(
        self, *, agent_id: str, instance_id: str = ""
    ) -> dict[str, Any]:
        """End the active session of the agent.

        The machine keeps the SOCKET OPEN and returns to ``ready``: the
        next session is opened on the same connection.

        R2: it refuses while the transport is off, like the other three
        operations.
        """
        self._assert_enabled()
        session = self._sessions.for_agent(agent_id)
        if session is None:
            raise SessionManager.session_not_found(agent_id)

        connection = self._connections.get(session.connection_id)
        if connection is None or connection.closed:
            self._sessions.end(session.session_id)
            self._store.clear_session(session.machine_id, session.session_id)
            raise SessionManager.session_not_found(session.session_id)

        # The machine acknowledges the end with the session id (§6.8).
        # A refusal (e.g. SESSION_NOT_FOUND — the machine does not know
        # the session) propagates UNCHANGED and the session SURVIVES:
        # the center mirrors the machine here, it does not invent a
        # local release the peer did not acknowledge.
        result = await self._request(
            connection,
            METHOD_SESSION_END,
            {"session_id": session.session_id},
            session_id=session.session_id,
        )

        self._sessions.end(session.session_id)
        self._store.clear_session(session.machine_id, session.session_id)
        return {
            "session_id": session.session_id,
            "machine_id": session.machine_id,
            "ended": True,
            "result": result,
        }

    # ── the audit ────────────────────────────────────────────────────────

    async def _audit_command(
        self,
        *,
        agent_id: str,
        instance_id: str,
        machine_id: str,
        tool: str,
        arguments: Any,
        session_id: str,
        outcome: str,
        error_code: str = "",
        detail: str = "",
    ) -> None:
        """Write ONE audit row.

        An audit failure NEVER aborts the command: the command describes a
        REMOTE side effect that has already happened, and refusing to
        report it because the OBSERVABILITY write failed would turn an
        audit problem into an availability problem on a path that drives a
        user's desktop. The failure is logged loudly and counted.

        THE WRITE IS OFF THE EVENT LOOP
        --------------------------------

        The DB-backed sink (:class:`McpGatewayAuditRepository`) is
        SYNCHRONOUS — it opens a ``Session(engine)`` and commits, and a
        slow or stalled PostgreSQL would freeze the WHOLE daemon event
        loop (every agent, every socket, the gateway's own sweep). So the
        write is bridged through ``asyncio.to_thread`` — the repo's
        established convention for calling a blocking repository from
        async code (``daemon/services/auth_service.py``,
        ``daemon/services/work_notifier.py``).

        The write is still AWAITED, not fired and forgotten: the row is
        durable before the command returns, so there is no race between
        the audit and the answer, and a shutdown cannot lose the row of a
        command that already happened. Nothing else is held while it
        runs (no lock, no session), so the offload cannot deadlock.
        """
        if not self.config.audit_enabled:
            return
        record = AuditRecord(
            agent_id=agent_id,
            instance_id=instance_id,
            machine_id=machine_id,
            tool=tool,
            args_hash=hash_args(arguments),
            outcome=outcome,
            session_id=session_id,
            error_code=error_code,
            detail=detail[:500],
            created_at=now_utc_iso(),
        )
        try:
            await asyncio.to_thread(self._audit.record, record)
        except Exception as exc:  # noqa: BLE001 — the audit must not wedge the gateway
            self._audit_failures += 1
            logger.error(
                "the remote-command audit row could not be written (%d failures so far): %s",
                self._audit_failures,
                exc,
            )

    # ── shutdown ─────────────────────────────────────────────────────────

    async def close_all(self) -> None:
        """Close every connection (the daemon shutdown path)."""
        if self._monitor_task is not None and not self._monitor_task.done():
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._monitor_task = None
        for connection in list(self._connections.values()):
            await self.close_connection(connection, "the gateway is shutting down")


__all__ = [
    "ClientConnection",
    "DEFAULT_MONITOR_INTERVAL_SECONDS",
    "GatewayTransport",
    "McpGatewayService",
]
