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

WHY THIS MODULE IS LARGE
------------------------

It is the CENTER of the protocol, and the protocol has grown a second
surface: the session LIFECYCLE. The module carries, in order: the
transport/connection lifecycle; the hello exchange; the outbound
requests (the centre is the requestor); the housekeeping sweep (the
heartbeat kill and the expiry reap); the ops read surface; the
agent-facing surface (roster / connect / command / disconnect); the
audit (the command rows and the session-lifecycle rows); and the
shutdown path.

The package is already split along its seams — ``protocol`` (the wire
vocabulary), ``config`` (the knobs), ``store`` (the roster),
``sessions`` (the live sessions), ``tombstones`` (the ended sessions),
``audit`` (the ledger) — and the END CONTRACT (the reason-coded
envelope, the expiry sweep, the tombstone lookup) is the piece that
would justify ONE more split (``session_end.py``). It is a RECORDED
FOLLOW-UP, not this branch's work: the extraction would introduce a
five-dependency collaborator (the sessions, the store, the connections,
the tombstones and the private request/audit helpers) on a defect
branch, which is a worse trade than a longer module.

The machine MINTS the ``session_id`` (§6.4) and the center adopts it.

Every frame the center writes carries ``v: "0.1"`` — the shipped client
checks the version of EVERY inbound message and closes the socket
silently (no error frame) on a missing or wrong ``v``.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
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
    AUDIT_TOOL_SESSION_END,
    AUDIT_TOOL_SESSION_EXPIRE,
    CENTER_EMITTED_CODES,
    CODE_CAPABILITY_MISSING,
    CODE_HEARTBEAT_TIMEOUT,
    CODE_INTERNAL,
    CODE_MACHINE_BUSY,
    CODE_MACHINE_NOT_ENROLLED,
    CODE_PROTOCOL_VERSION_UNSUPPORTED,
    CODE_SESSION_NOT_FOUND,
    CODE_TOOL_TIMEOUT,
    METHOD_HEARTBEAT,
    METHOD_HELLO,
    METHOD_NOTIFICATIONS_PREFIX,
    METHOD_SESSION_BEGIN,
    METHOD_SESSION_END,
    METHOD_TOOLS_CALL,
    PROTOCOL_VERSION,
    RELAYED_CODES,
    RESERVED_AUDIT_TOOL_NAMES,
    REQUIRED_CLIENT_CAPABILITIES,
    SERVER_CAPABILITIES,
    SERVER_NAME,
    TYPE_ERROR,
    TYPE_NOTIFICATION,
    TYPE_REQUEST,
    TYPE_RESPONSE,
    GatewayAckTimeout,
    GatewayProtocolError,
    make_envelope,
    make_error_envelope,
)
from .sessions import GatewaySession, SessionManager
from .store import InMemoryMachineStore, MachineStore
from .tombstones import (
    CAUSE_IDLE,
    CAUSE_TTL,
    EXPIRY_CAUSES,
    LEDGER_MACHINE_TIMEOUT,
    LEDGER_MACHINE_UNREACHABLE,
    REASON_ALREADY_ENDED,
    REASON_EXPIRED,
    REASON_MACHINE_REFUSED,
    REASON_UNKNOWN_SESSION,
    RESPONSE_REASONS,
    SessionTombstone,
    TombstoneStore,
)

logger = logging.getLogger(__name__)

#: The audit detail CLASSIFICATIONS.
#:
#: The audit row NEVER carries peer text. A hostile machine can quote the
#: command arguments in its error message, and the ledger is exactly the
#: place raw arguments must never reach (spec-interpretations §6.4 names
#: this threat: "a typed password should not end up in a log file because
#: it was the argument to a tool"). The peer text stays AGENT-FACING (the
#: tool result carries it); the AUDIT carries a fixed classification plus
#: the error code — and the error code is itself checked against the
#: protocol vocabulary, because it arrives from the peer too.
AUDIT_DETAIL_OK = "the remote command completed"
AUDIT_DETAIL_ERROR = "the machine refused the command"
AUDIT_DETAIL_TIMEOUT = "the machine did not answer in time"
AUDIT_DETAIL_UNRECOGNIZED_CODE = (
    "the machine answered with an unrecognized error code"
)

#: The neutral marker recorded (and reported) when a code arrives from
#: the peer and is outside the vocabulary it is validated against.
UNRECOGNIZED_ERROR_CODE = "UNRECOGNIZED_ERROR_CODE"

#: The error codes a COMMAND row is allowed to record verbatim.
#:
#: Everything else (a peer's free text that happens to sit in the code
#: slot) is replaced by :data:`UNRECOGNIZED_ERROR_CODE`. The set is the
#: PROTOCOL vocabulary only — the centre-emitted codes plus the relayed
#: ones. It is deliberately NOT widened with the centre-only tokens of
#: the session-lifecycle vocabulary: a hostile machine could otherwise
#: forge ``machine_timeout`` / ``idle`` / ``expired`` into a COMMAND row
#: and make the ledger lie about a command it never refused.
AUDIT_ALLOWED_ERROR_CODES: frozenset[str] = frozenset(
    CENTER_EMITTED_CODES | RELAYED_CODES
) | {UNRECOGNIZED_ERROR_CODE}

#: The error codes a SESSION-LIFECYCLE row is allowed to record.
#:
#: The session surfaces speak the SAME protocol vocabulary (a machine
#: may answer ``SESSION_NOT_FOUND``) PLUS the centre-only tokens: the
#: two LEDGER-ONLY reasons (a release the machine did not confirm), the
#: two EXPIRY CAUSES (why the sweep reaped a session) and the
#: RESPONSE-borne reasons (why an end did not end). Those tokens are
#: MINTED BY THE CENTRE — they can never arrive from the peer, so they
#: cannot be forged into a command row (the command rows keep the
#: protocol-only set above).
AUDIT_ALLOWED_SESSION_ERROR_CODES: frozenset[str] = (
    AUDIT_ALLOWED_ERROR_CODES
    | {
        LEDGER_MACHINE_UNREACHABLE,
        LEDGER_MACHINE_TIMEOUT,
        REASON_EXPIRED,
        REASON_ALREADY_ENDED,
        REASON_UNKNOWN_SESSION,
        REASON_MACHINE_REFUSED,
    }
    | EXPIRY_CAUSES
)

#: The audit detail CLASSIFICATIONS of the SESSION-LIFECYCLE rows.
#:
#: Same rule as the command rows: a FIXED classification, never the
#: peer's free text and never the raw arguments. The reason token is
#: appended in brackets by the write path, exactly like the command
#: rows do.
AUDIT_DETAIL_SESSION_ENDED = "the remote session was ended"
AUDIT_DETAIL_SESSION_ENDED_ALREADY_GONE = (
    "the remote session was ended; the machine held no such session"
)
AUDIT_DETAIL_SESSION_REFUSED = "the machine refused to end the remote session"
AUDIT_DETAIL_SESSION_UNREACHABLE = (
    "the machine was unreachable; the center released the remote session"
)
AUDIT_DETAIL_SESSION_TIMEOUT = "the machine did not answer the session end in time"
AUDIT_DETAIL_SESSION_ALREADY_ENDED = "the remote session had already ended"
AUDIT_DETAIL_SESSION_EXPIRED = "the remote session had already expired"
AUDIT_DETAIL_EXPIRE_RELEASED = (
    "the remote session expired and the machine released it"
)
AUDIT_DETAIL_EXPIRE_ALREADY_GONE = (
    "the remote session expired and the machine held no such session"
)
AUDIT_DETAIL_EXPIRE_UNREACHABLE = (
    "the remote session expired and the machine was unreachable"
)
AUDIT_DETAIL_EXPIRE_TIMEOUT = (
    "the remote session expired and the machine did not answer the end in time"
)
AUDIT_DETAIL_EXPIRE_REFUSED = (
    "the remote session expired and the machine refused the end"
)

# ── the outcomes of a ``session.end`` FORWARD ─────────────────────────────
#
# The forward is best-effort and NEVER raises: the sweep must survive a
# wedged machine, and the sweep is the only owner of the expiry reap.

#: The machine acknowledged the end — it released its local lock.
FORWARD_ACK = "ack"

#: The machine answered ``SESSION_NOT_FOUND`` — it never held the
#: session, so there is no local lock to release. The machine WAS
#: notified (it answered), and the session IS over.
FORWARD_ALREADY_GONE = "already_gone"

#: The CENTRE's own ack deadline fired (``GatewayAckTimeout``).
FORWARD_TIMEOUT = "timeout"

#: There is no connection, or it is closed — the machine is unreachable.
FORWARD_UNREACHABLE = "unreachable"

#: The machine answered with any other error envelope — it REFUSED.
FORWARD_REFUSED = "refused"

#: The deadline of the audit write (R1). A stalled PostgreSQL must not
#: hang an agent tool call indefinitely: the write is abandoned, the
#: failure is counted and logged, and the command result stands.
DEFAULT_AUDIT_WRITE_TIMEOUT_SECONDS = 5.0

#: The deadline of the HELLO exchange for a connection that upgraded but
#: never said hello (R2a). A token-holder that holds a socket without
#: ever speaking is reaped after this window.
DEFAULT_PRE_HELLO_TIMEOUT_MS = 30_000

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


#: The outcome of a ``session.end`` FORWARD that carries no
#: acknowledgement payload. A sentinel, not ``None``: the machine's ack
#: may legitimately BE ``{}``, and an empty acknowledgement is still an
#: acknowledgement.
_NO_RESULT = object()


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
        tombstones: TombstoneStore | None = None,
        clock: Callable[[], float] = time.time,
        monitor_interval_seconds: float = DEFAULT_MONITOR_INTERVAL_SECONDS,
        audit_write_timeout_seconds: float = DEFAULT_AUDIT_WRITE_TIMEOUT_SECONDS,
        pre_hello_timeout_ms: int = DEFAULT_PRE_HELLO_TIMEOUT_MS,
        server_version: str = "0.1.0",
    ) -> None:
        self.config = config if config is not None else load_gateway_config()
        self._store = store if store is not None else InMemoryMachineStore()
        self._audit = audit_sink if audit_sink is not None else NullAuditSink()
        self._tombstones = tombstones if tombstones is not None else TombstoneStore()
        self._clock = clock
        self._server_version = server_version
        self._audit_write_timeout_seconds = audit_write_timeout_seconds
        self._pre_hello_timeout_ms = pre_hello_timeout_ms
        # The bound of the CENTRE's own wait for a ``session.end``
        # acknowledgement — its own deadline, tight and separate from the
        # command timeout (a wedged machine must never hold an agent for
        # the whole command timeout).
        self._session_end_ack_timeout_ms = self.config.session_end_ack_timeout_ms

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
    def tombstones(self) -> TombstoneStore:
        """The bounded memory of the ENDED sessions.

        The read surface does NOT expose it: ``GET /sessions`` shows the
        LIVE sessions only (a tombstone answers a late end, it is not a
        session anybody can drive).
        """
        return self._tombstones

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
        """Register a freshly upgraded socket.

        The id is a UUID4, NOT a counter: a counter derived from
        ``len(self._connections)`` collides as soon as a connection is
        closed (the length drops), and a colliding id would make the
        dictionary OVERWRITE the live entry — after which
        ``close_connection`` could cross-kill an unrelated socket.
        """
        connection_id = f"conn-{uuid.uuid4().hex[:12]}"
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
            # The session died WITH its connection: it leaves an
            # ``already_ended`` tombstone, so a LATE end answers
            # ``already_ended`` instead of a bare ``unknown_session``.
            # The machine was NOT notified (its socket is gone).
            self._record_tombstone(
                session, reason=REASON_ALREADY_ENDED, machine_notified=False
            )
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
            if connection.machine_id:
                # P4 (roster-release ordering): the §8.3 roster pointer is
                # released BEFORE the awaited socket close. The close
                # handshake of a WEDGED peer can stretch for seconds, and
                # the read surface kept reporting the dead session as
                # ``busy: true`` through that whole window (found by the
                # interop gate #16c failing honestly in P1/P2). The session
                # manager already released the slot above, so the §8.3 lock
                # is never held twice — this only closes the STALE
                # READ-SURFACE window. The connection_id guard inside
                # mark_offline keeps a reconnect that raced the close safe.
                self._store.mark_offline(connection.machine_id, connection.connection_id)
        finally:
            # fd-leak guard (QA round, C10): even if the roster release
            # RAISES, the socket close below still runs — a skipped close
            # leaks the transport fd for the life of the process. The
            # RELEASE ORDER is unchanged: mark_offline (the §8.3 release)
            # runs FIRST, the awaited close second.
            try:
                await connection.transport.close()
            except Exception:  # noqa: BLE001 — a dead socket is not an error
                pass

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
        if version != PROTOCOL_VERSION:
            # §6.1: "v | string | yes | Protocol version of this message
            # ("0.1")". A frame that does not name the version cannot be
            # read as a v0.1 message, and §5.2 forbids a silent
            # downgrade — so the frame is REFUSED and the connection
            # goes down. This mirrors the shipped client, which checks
            # the version of EVERY inbound message and tears the
            # connection down on a mismatch (interpretation §2.4).
            #
            # NO error frame goes out: there is nothing to correlate the
            # refusal with (the frame's id belongs to a message the
            # center never accepted), and the socket close is the
            # refusal.
            logger.warning(
                "the machine sent a frame with the protocol version %r "
                "(expected %r); the connection is closed",
                version,
                PROTOCOL_VERSION,
            )
            await self.close_connection(connection, "protocol version violation")
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

        # A RECONNECT supersedes the previous socket (S3). The old
        # connection's session must die WITH it: a session belongs to its
        # connection (§6.8), and a machine that reconnects starts over
        # from the beginning (§6.8 — no resumption in v0.1). Without this
        # the superseded session would live on until the heartbeat
        # timeout, and the reconnected machine would be told
        # ``MACHINE_BUSY`` for up to 45 s by a session nobody can use.
        stale = self._by_machine.get(machine_id)
        if stale is not None and stale is not connection and not stale.closed:
            logger.warning(
                "the machine %r reconnected on a new socket; the previous "
                "connection and its session are closed",
                machine_id,
            )
            await self.close_connection(stale, "the machine reconnected")

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
            # The CENTRE's OWN deadline fired — a distinct TYPE, not a
            # distinct code. ``TOOL_TIMEOUT`` is both a centre-emitted
            # and a RELAYED code, so the code cannot tell this apart from
            # a machine that relayed its own tool timeout; the TYPE can
            # (and it must: the centre's own deadline authorises an
            # authoritative release, a relayed timeout is a REFUSAL and
            # the session survives it).
            #
            # The TYPE is session-end SPECIFIC: its contract is "the
            # centre may release the session AUTHORITATIVELY", and only
            # the session.end path may do that. A ``session.begin`` or a
            # ``tools/call`` that runs out of time must not carry a
            # contract it does not own, so those keep the plain
            # GatewayProtocolError (the command path discriminates on
            # ``exc.code``, so its behaviour is unchanged).
            message = (
                f"{method} exceeded the center deadline of "
                f"{timeout_ms or self.config.command_timeout_ms} ms"
            )
            data = {
                "method": method,
                "timeout_ms": timeout_ms or self.config.command_timeout_ms,
            }
            if method == METHOD_SESSION_END:
                raise GatewayAckTimeout(CODE_TOOL_TIMEOUT, message, data) from None
            raise GatewayProtocolError(CODE_TOOL_TIMEOUT, message, data) from None
        finally:
            connection.pending.pop(request_id, None)

    # ── the housekeeping sweep ───────────────────────────────────────────

    async def ensure_monitor(self) -> None:
        """Start the housekeeping sweep (idempotent).

        The sweep is started with the FIRST connection and then runs for
        the PROCESS LIFETIME: it is NOT stopped when the last machine
        disconnects — only :meth:`close_all` (the daemon shutdown path)
        cancels it. It is a cheap loop (the heartbeat check, the session
        expiry and the reaping of the connections that never said
        hello), and it is the ONLY thing that enforces the §3.3 timeouts
        while the daemon is up.
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

        It also REAPS the connections that upgraded and never said
        hello (R2a): the hello exchange is mandatory and happens once
        per connection (§5.1), so a connection that stays silent through
        it holds a socket and a roster entry forever. Such a connection
        is closed after the pre-hello deadline.
        """
        now = self._clock()
        # The kill fires at EXACTLY the advertised ``heartbeat_timeout_ms``
        # (§3.3) — there is no grace on top of it.
        limit_ms = self.config.heartbeat_timeout_ms
        killed: list[ClientConnection] = []
        for connection in list(self._connections.values()):
            if connection.closed:
                continue
            if not connection.hello_received:
                # R2a: a socket that never completed the hello exchange.
                age_ms = (now - connection.connected_at) * 1000.0
                if age_ms <= self._pre_hello_timeout_ms:
                    continue
                killed.append(connection)
                logger.warning(
                    "the connection %s never completed the hello exchange "
                    "(%d ms); it is closed",
                    connection.connection_id,
                    int(age_ms),
                )
                await self.close_connection(
                    connection, "the hello exchange never completed"
                )
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
            for session in self._sessions.end_for_connection(
                connection.connection_id
            ):
                # The session died with the machine: it leaves an
                # ``already_ended`` tombstone, so a LATE end answers
                # ``already_ended`` instead of a bare ``unknown_session``.
                # The machine was NOT notified (it is gone).
                self._record_tombstone(
                    session, reason=REASON_ALREADY_ENDED, machine_notified=False
                )
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
        """Reap the sessions that passed their TTL or their idle timeout.

        THE SINGLE OWNER OF THE EXPIRY REAP
        ----------------------------------

        This method is the ONLY place that reaps an EXPIRED session. The
        command path REFUSES an expired session (``SESSION_EXPIRED``) but
        never releases it, and the explicit end path is not a reap at
        all. The reason is mechanical: the sweep can only see the
        sessions that are STILL in the session manager — a session
        released by another owner is invisible to it, so the machine
        would keep its local lock while the centre's roster already said
        the slot was free.

        TWO PASSES, ON PURPOSE
        ---------------------

        PASS 1 does NOT await anything: for the WHOLE expired batch it
        clears the centre-side roster pointer and writes the ``expired``
        tombstone. PASS 2 then forwards the ``session.end`` to each
        machine, one after the other, under its own bounded deadline.

        The order is not cosmetic. While the sweep waits for machine A's
        acknowledgement, machine B must ALREADY be released centre-side:
        otherwise the roster would report ``busy: true`` with a DEAD
        session id, and B would have no tombstone — so a late end for B
        would answer ``unknown_session`` instead of ``expired``.

        The centre-side release of the whole batch is therefore COMPLETE
        before ANY forward starts; the machine-side forward is the
        bounded part (N x the ack deadline in the worst case).

        THE SOCKET STAYS OPEN
        ----------------------

        The forward is the ONLY thing the sweep does to the machine. The
        socket is NOT closed: the machine releases its local lock when it
        acknowledges the ``session.end`` and returns to ``ready`` on the
        SAME connection, so the next session opens on that connection.
        """
        now = self._clock()
        expired = self._sessions.expire_due(now)
        if not expired:
            return []
        batch = [(session, self._expiry_cause(session, now)) for session in expired]

        # PASS 1 — NO await at all: the centre-side release of the WHOLE
        # batch (the roster pointer + the tombstone).
        for session, cause in batch:
            logger.warning(
                "the session %s expired (%s)", session.session_id, cause
            )
            # The roster release is session-id-guarded: a machine that
            # re-helloed and bound a NEW session keeps that new session.
            self._store.clear_session(session.machine_id, session.session_id)
            self._record_tombstone(session, reason=REASON_EXPIRED, cause=cause)

        # PASS 2 — serial, awaiting: the machine-side release (bounded).
        for session, cause in batch:
            await self._reap_expired_session(session, cause)
        return expired

    @staticmethod
    def _expiry_cause(session: GatewaySession, now: float) -> str:
        """Why the sweep reaped this session — ``ttl`` or ``idle``.

        The TTL ceiling wins when both passed: it is the harder bound.

        THIS MUST MIRROR ``SessionManager.expire_due``
        (``daemon/services/mcps_gateway/sessions.py``) — that is the
        predicate that decides WHICH sessions are due; this only says
        WHY one of them was. If the two ever disagree, the ledger would
        name a cause the reaper did not apply.
        """
        if now >= session.expires_at:
            return CAUSE_TTL
        return CAUSE_IDLE

    def _record_tombstone(
        self,
        session: GatewaySession,
        *,
        reason: str,
        cause: str = "",
        at: float | None = None,
        machine_notified: bool = False,
    ) -> None:
        """Remember an ended session in the bounded tombstone store.

        The tombstone is what lets a LATE end request answer ``expired``
        or ``already_ended`` instead of a bare ``unknown_session`` — and
        it is bounded in BOTH count and age, so it cannot grow into a
        memory leak (see ``daemon/services/mcps_gateway/tombstones.py``).

        ``machine_notified`` records whether the MACHINE was told about
        the end, so the late end's envelope can answer it truthfully
        instead of hard-coding a value.
        """
        self._tombstones.record(
            SessionTombstone(
                session_id=session.session_id,
                reason=reason,
                at=self._clock() if at is None else at,
                agent_id=session.agent_id,
                instance_id=session.instance_id,
                machine_id=session.machine_id,
                cause=cause,
                machine_notified=machine_notified,
            )
        )

    async def _forward_session_end_outcome(self, session: GatewaySession) -> str:
        """The one forward, classified — the SWEEP arm.

        The mirror of the explicit end's forward for the sweep path; the
        asymmetry between the two is DELIBERATE: the sweep releases the
        session centre-side FIRST (the two-pass invariant), so its
        "refusal" can only be a ledger fact, while the explicit end
        keeps the session ALIVE on a refusal. The shared-helper variant
        is a recorded follow-up (the seam is named here on purpose).

        The FRAME keeps EXACTLY the params it has always had —
        ``{"session_id": ...}``. The reason of the end never travels on
        the wire: it lives in the agent-facing response envelope and in
        the audit ledger.
        """
        connection = self._connections.get(session.connection_id)
        if connection is None or connection.closed:
            logger.warning(
                "the machine %r is unreachable; the session %s stays "
                "released centre-side",
                session.machine_id,
                session.session_id,
            )
            return FORWARD_UNREACHABLE
        try:
            await self._request(
                connection,
                METHOD_SESSION_END,
                {"session_id": session.session_id},
                session_id=session.session_id,
                timeout_ms=self._session_end_ack_timeout_ms,
            )
        except GatewayAckTimeout:
            # The CENTRE's own deadline fired (the TYPE, never the code
            # string: a RELAYED timeout is a plain GatewayProtocolError
            # and falls through to the refusal branch below).
            logger.warning(
                "the machine %r did not acknowledge the session end of %s "
                "within %d ms",
                session.machine_id,
                session.session_id,
                self._session_end_ack_timeout_ms,
            )
            return FORWARD_TIMEOUT
        except GatewayProtocolError as exc:
            if exc.code == CODE_SESSION_NOT_FOUND:
                # The machine does not know the session: its local lock
                # is already gone, so there is nothing left to release.
                return FORWARD_ALREADY_GONE
            # B-4 (the sweep twin of the explicit end): the connection
            # may have died DURING the ack window — ``close_connection``
            # fails the pending futures with a synthetic INTERNAL refusal
            # and closes the socket. The machine answered NOTHING, so the
            # sweep must not record a false REFUSAL: the ledger says
            # ``machine_unreachable``.
            #
            # ONLY the socket is the signal here: in the sweep the
            # session was ALREADY released centre-side (PASS 1), so
            # "the session is gone" would be true for EVERY forward and
            # would swallow the genuine refusals.
            if connection.closed:
                logger.warning(
                    "the machine %r went away while the session end of %s "
                    "was in flight",
                    session.machine_id,
                    session.session_id,
                )
                return FORWARD_UNREACHABLE
            logger.warning(
                "the machine %r refused the session end of %s (%s)",
                session.machine_id,
                session.session_id,
                exc.code,
            )
            return FORWARD_REFUSED
        except Exception as exc:  # noqa: BLE001 — the forward is best-effort
            logger.warning(
                "the session end of %s could not be forwarded to the "
                "machine %r: %s",
                session.session_id,
                session.machine_id,
                exc,
            )
            return FORWARD_UNREACHABLE
        return FORWARD_ACK

    async def _reap_expired_session(self, session: GatewaySession, cause: str) -> None:
        """Forward one reaped session's end and write its audit row.

        The audit row is what answers the operator question "why did this
        session not end?" — the sweep's own release leaves no other
        trace in the ledger.

        The tombstone was written BEFORE the forward (the two-pass
        invariant), so the forward's outcome is recorded on it AFTER the
        fact — that is what lets a LATE end answer truthfully whether
        the machine was notified.
        """
        outcome = await self._forward_session_end_outcome(session)
        self._tombstones.mark_notified(
            session.session_id,
            outcome in (FORWARD_ACK, FORWARD_ALREADY_GONE, FORWARD_REFUSED),
        )
        if outcome == FORWARD_ACK:
            audit_outcome = OUTCOME_OK
            error_code = cause
            detail = AUDIT_DETAIL_EXPIRE_RELEASED
        elif outcome == FORWARD_ALREADY_GONE:
            audit_outcome = OUTCOME_OK
            error_code = cause
            detail = AUDIT_DETAIL_EXPIRE_ALREADY_GONE
        elif outcome == FORWARD_TIMEOUT:
            audit_outcome = OUTCOME_TIMEOUT
            error_code = LEDGER_MACHINE_TIMEOUT
            detail = AUDIT_DETAIL_EXPIRE_TIMEOUT
        elif outcome == FORWARD_UNREACHABLE:
            audit_outcome = OUTCOME_ERROR
            error_code = LEDGER_MACHINE_UNREACHABLE
            detail = AUDIT_DETAIL_EXPIRE_UNREACHABLE
        else:
            audit_outcome = OUTCOME_ERROR
            error_code = REASON_MACHINE_REFUSED
            detail = AUDIT_DETAIL_EXPIRE_REFUSED
        await self._audit_session(
            agent_id=session.agent_id,
            instance_id=session.instance_id,
            machine_id=session.machine_id,
            session_id=session.session_id,
            tool=AUDIT_TOOL_SESSION_EXPIRE,
            outcome=audit_outcome,
            error_code=error_code,
            detail=detail,
        )

    # ── the ops read surface (Phase C) ────────────────────────────────────
    #
    # The agent-facing surface above REFUSES while the transport is off
    # (R2: a healthy-looking answer while the transport is dead would
    # mislead the agent). The read surface below is the opposite: it
    # REPORTS the state of the center and therefore answers even while
    # the gateway is disabled — the operator page must be able to say
    # "the gateway is off", and an empty roster is exactly that
    # statement.

    def now(self) -> float:
        """The current instant, per the service clock.

        The read surface needs the SAME clock the presence computation
        uses: a router-side ``time.time()`` would disagree with the
        service's injectable clock and would mark every machine
        ``stale`` under a pinned clock.
        """
        return self._clock()

    def audit_tail(
        self, limit: int = 50, offset: int = 0
    ) -> tuple[list[dict[str, Any]], bool, str | None]:
        """The remote-command audit tail, NEWEST FIRST — the CLASSIFIED read.

        Returns ``(rows, degraded, reason)``:

        * ``degraded=False`` for a HEALTHY read **and** for a write-only
          sink (no ``list_recent`` ⇒ an EMPTY tail, never degraded — a
          write-only sink is not an outage);
        * ``degraded=True`` + a SHORT NON-SECRET classification
          (``"audit_unavailable"``) when the reader RAISED. The raw
          exception never crosses this boundary (a DB error string can
          quote DSNs and paths).

        AD5 — exactly ONE swallow layer: the repository no longer
        swallows, and the classification lives HERE (the service), not
        in the router. The ROUTER owns the other leg of the same
        classification — the READ TIMEOUT (``asyncio.wait_for`` around
        this call, ``MCPS_GATEWAY_READ_TIMEOUT_MS``): a reader that
        hangs instead of raising produces the same degraded body. The
        WRITE path's swallow (``_audit`` command flow) is a different,
        correct rule — an audit-write failure must never abort a
        command — and it stays.
        """
        reader = getattr(self._audit, "list_recent", None)
        if not callable(reader):
            return [], False, None
        try:
            rows = [dict(row) for row in reader(limit=limit, offset=offset)]
        except Exception as exc:  # noqa: BLE001 — classified HERE, one layer up from the repo
            logger.warning("the remote-command audit could not be read: %s", exc)
            return [], True, "audit_unavailable"
        return rows, False, None

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
        # The center RESERVES two names for its own audit vocabulary
        # (RESERVED_AUDIT_TOOL_NAMES). Advertising a tool the center
        # would then refuse is a lie, so the reserved names never reach
        # the catalogue the agent sees. Only the NAME is dropped: every
        # other tool's schema travels verbatim (§6.4).
        catalog = [
            tool
            for tool in catalog
            if not (
                isinstance(tool, dict)
                and str(tool.get("name") or "") in RESERVED_AUDIT_TOOL_NAMES
            )
        ]
        try:
            session = self._sessions.begin(
                session_id=session_id,
                machine_id=machine_id,
                agent_id=agent_id,
                instance_id=instance_id,
                connection_id=connection.connection_id,
                catalog=catalog,
                now=self._clock(),
            )
        except GatewayProtocolError:
            # S2: the slot was taken between the pre-flight check and
            # this write (a concurrent connect). The machine DID open a
            # session for us — the center refuses it, so the machine-side
            # session is released best-effort, or the machine would keep
            # its local lock until its own timeout.
            await self._release_machine_session(
                connection, session_id, machine_id
            )
            raise
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

        The remote tool names come from the machine's catalog — verbatim
        (§6.4) — so one of them could carry a name the CENTRE reserves
        for its own audit vocabulary (``session.end`` /
        ``session.expire``). Such a call is refused BEFORE it reaches
        the machine: otherwise the ledger would carry one ``tool`` value
        with two meanings and the rows would be indistinguishable for an
        operator filtering on ``tool``.
        """
        self._assert_enabled()

        if tool in RESERVED_AUDIT_TOOL_NAMES:
            raise GatewayProtocolError(
                CODE_INTERNAL,
                f"the remote tool name {tool!r} is reserved by the center's "
                "audit vocabulary and cannot be called",
                {"tool": tool, "reserved": sorted(RESERVED_AUDIT_TOOL_NAMES)},
            )

        session = self._sessions.for_agent(agent_id)
        if session is None:
            # The sweep may have reaped this session already. The refusal
            # then answers SESSION_EXPIRED — with the session id of the
            # reaped session — instead of SESSION_NOT_FOUND: the SAME
            # expiry must not produce two different codes depending on
            # which path noticed it.
            tombstone = self._tombstones.latest_for_agent(agent_id, self._clock())
            if tombstone is not None and tombstone.reason == REASON_EXPIRED:
                raise SessionManager.session_expired(tombstone.session_id)
            # P1: the refusal names the AGENT (there is no session id to
            # name) — the contract's ``data.session_id`` carries a
            # SESSION id, so it is absent here, not the agent id.
            raise SessionManager.no_session_for_agent(agent_id)

        connection = self._connections.get(session.connection_id)
        if connection is None or connection.closed:
            raise SessionManager.session_not_found(session.session_id)

        now = self._clock()
        if now >= session.expires_at or (
            now - session.last_activity
        ) >= (self.config.session_idle_timeout_ms / 1000.0):
            # SINGLE OWNER: the SWEEP reaps an expired session — this
            # path only REFUSES. Releasing here would hide the session
            # from the sweep (it only sees the sessions still in the
            # manager), so the machine would keep its local lock while
            # the roster already said the slot was free.
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

    async def _release_machine_session(
        self,
        connection: ClientConnection,
        session_id: str,
        machine_id: str,
    ) -> None:
        """Best-effort release of a machine-side session the center refuses.

        The machine opened a session for a request the center is about to
        refuse (the §8.3 slot was taken in the meantime). The machine
        keeps its local lock until the session is ended, so the center
        ends it — best-effort and BOUNDED: a machine that does not
        answer simply keeps the session until its own timeout, which is
        no worse than the refusal itself.
        """
        try:
            await self._request(
                connection,
                METHOD_SESSION_END,
                {"session_id": session_id},
                session_id=session_id,
                timeout_ms=2000,
            )
        except Exception as exc:  # noqa: BLE001 — the release is best-effort
            logger.warning(
                "the machine-side session %s of the machine %r could not be "
                "released after a refused connect: %s",
                session_id,
                machine_id,
                exc,
            )

    async def disconnect(
        self, *, agent_id: str, instance_id: str = ""
    ) -> dict[str, Any]:
        """End the active session of the agent.

        The machine keeps the SOCKET OPEN and returns to ``ready``: the
        next session is opened on the same connection.

        THE END CONTRACT
        ----------------

        ``disconnect`` answers a dict for EVERY outcome — it never
        raises for a session-state reason (the kill switch, i.e. a
        disabled gateway, is the ONLY thing that raises). The shape is::

            {
              "session_id": str,      # ONLY when it is known
              "machine_id": str,      # ONLY when it is known
              "ended": bool,
              "detail": str,
              "machine_notified": bool,
              "why_not_ended": str,   # present IFF "ended" is False
              "result": Any,          # ONLY on the acknowledged path
            }

        ``why_not_ended`` carries one of the four RESPONSE-borne reasons
        (``expired``, ``already_ended``, ``unknown_session``,
        ``machine_refused`` — see
        ``daemon/services/mcps_gateway/tombstones.py``). The two
        LEDGER-only tokens (``machine_unreachable``, ``machine_timeout``)
        describe the CENTRE's release path and NEVER appear in a
        response: they are audit-row reasons only.

        ``machine_notified`` says whether the machine ACKNOWLEDGED the
        session end. ``False`` means the CENTRE released the session
        AUTHORITATIVELY — the machine was never reached, or it was
        reached and did not answer in time — so the machine's LOCAL LOCK
        may persist until it processes the frame or the connection dies.
        An agent must read ``machine_notified: false`` as "do NOT assume
        the machine is free".

        ``session_id`` / ``machine_id`` are included ONLY when they are
        known — an unknown session must never leak the existence of
        somebody else's session.

        PROVENANCE: the envelope and the reason taxonomy are a
        CENTRE-SIDE CONVENTION ported from the sibling fleet's
        remote-hands centre for parity. They are NOT SPEC v0.1 text (the
        SPEC mandates the typed errors ``SESSION_EXPIRED`` /
        ``SESSION_NOT_FOUND`` and the server-side ownership of the
        session timeouts, and says nothing about ``why_not_ended``), and
        the WIRE PROTOCOL is unchanged by them: the ``session.end``
        frame keeps exactly the params it always had. An amendment
        request to the SPEC owner is pending — it is not this repo's job.

        R2: it refuses while the transport is off, like the other three
        operations.
        """
        self._assert_enabled()

        session = self._sessions.for_agent(agent_id)
        if session is None:
            return await self._end_without_session(agent_id, instance_id)

        connection = self._connections.get(session.connection_id)
        if connection is None or connection.closed:
            # (d) The machine is unreachable. Guarded against a DOUBLE
            # RELEASE: the sweep may have reaped this session while the
            # caller was on its way here — then the session is gone and
            # the tombstone answers.
            if self._sessions.get(session.session_id) is None:
                return await self._end_without_session(agent_id, instance_id)
            return await self._end_without_machine(
                session,
                agent_id=agent_id,
                instance_id=instance_id,
                error_code=LEDGER_MACHINE_UNREACHABLE,
                detail=(
                    "the machine is unreachable; the center released the "
                    "session authoritatively"
                ),
                audit_detail=AUDIT_DETAIL_SESSION_UNREACHABLE,
            )

        # The EXPLICIT arm — the mirror of the sweep's
        # ``_forward_session_end_outcome`` for the explicit path. The
        # asymmetry between the two is DELIBERATE: a refusal here keeps
        # the session ALIVE (the centre does not invent a release the
        # peer did not acknowledge), while the sweep has already
        # released centre-side before it forwards. The shared-helper
        # variant is a recorded follow-up (the seam is named here on
        # purpose).
        try:
            result = await self._request(
                connection,
                METHOD_SESSION_END,
                {"session_id": session.session_id},
                session_id=session.session_id,
                timeout_ms=self._session_end_ack_timeout_ms,
            )
        except GatewayAckTimeout:
            # (e) The CENTRE's OWN ack deadline fired. The TYPE is the
            # discrimination — a RELAYED timeout is a plain
            # GatewayProtocolError and falls through to the refusal
            # branch below, where the session SURVIVES.
            return await self._end_without_machine(
                session,
                agent_id=agent_id,
                instance_id=instance_id,
                error_code=LEDGER_MACHINE_TIMEOUT,
                detail=(
                    "the machine did not acknowledge the end of the session "
                    "within the center deadline of "
                    f"{self._session_end_ack_timeout_ms} ms; the center "
                    "released the session authoritatively"
                ),
                audit_detail=AUDIT_DETAIL_SESSION_TIMEOUT,
            )
        except GatewayProtocolError as exc:
            if exc.code == CODE_SESSION_NOT_FOUND:
                # (b) The machine no longer holds the session — its lock
                # is already gone, so there is nothing left to release.
                return await self._end_acknowledged(
                    session,
                    agent_id=agent_id,
                    instance_id=instance_id,
                    error_code=CODE_SESSION_NOT_FOUND,
                    detail=(
                        "machine_session_already_gone — the machine no longer "
                        "held the session"
                    ),
                    audit_detail=AUDIT_DETAIL_SESSION_ENDED_ALREADY_GONE,
                )
            # B-4: the connection may have died DURING the ack window.
            # ``close_connection`` fails the pending futures with a
            # synthetic INTERNAL refusal and kills the session with the
            # connection — so the machine answered NOTHING. This is NOT
            # a refusal (whose documented meaning is "the session
            # SURVIVES"): it is an unreachable machine, and the session
            # is already gone.
            if self._sessions.get(session.session_id) is None:
                # The session died with the connection — the tombstone
                # (``already_ended``) answers.
                return await self._end_without_session(agent_id, instance_id)
            if connection.closed:
                return await self._end_without_machine(
                    session,
                    agent_id=agent_id,
                    instance_id=instance_id,
                    error_code=LEDGER_MACHINE_UNREACHABLE,
                    detail=(
                        "the machine is unreachable; the center released the "
                        "session authoritatively"
                    ),
                    audit_detail=AUDIT_DETAIL_SESSION_UNREACHABLE,
                )
            # (c) Any other refusal — including a RELAYED TOOL_TIMEOUT.
            # The machine WAS notified and it REFUSED: the session
            # SURVIVES, and the centre does not invent a local release
            # the peer did not acknowledge.
            #
            # B-3: the peer's code slot is PEER-CONTROLLED, so it is
            # sanitised before it reaches the response — only the
            # PROTOCOL vocabulary is echoed. Otherwise a hostile machine
            # could park a LEDGER-ONLY token (or any free text) in the
            # response, which the disjointness contract calls a bug.
            safe_code = self._safe_peer_code(exc.code)
            await self._audit_session(
                agent_id=agent_id,
                instance_id=instance_id,
                machine_id=session.machine_id,
                session_id=session.session_id,
                tool=AUDIT_TOOL_SESSION_END,
                outcome=OUTCOME_ERROR,
                error_code=REASON_MACHINE_REFUSED,
                detail=AUDIT_DETAIL_SESSION_REFUSED,
            )
            return {
                "session_id": session.session_id,
                "machine_id": session.machine_id,
                "ended": False,
                # Every response-borne reason passes the SAME disjointness
                # guard — the refusal arm is not an exception to it.
                "why_not_ended": self._response_reason(REASON_MACHINE_REFUSED),
                "detail": f"the machine refused the session end ({safe_code})",
                "machine_notified": True,
            }
        except Exception as exc:  # noqa: BLE001 — a dead socket is not a crash
            # B-2: the socket died BETWEEN the liveness check above and
            # the send (the transport raised). The machine answered
            # NOTHING, so the bare-error failure mode this branch exists
            # to remove must not happen: the centre releases the session
            # AUTHORITATIVELY and says so.
            logger.warning(
                "the session end of %s could not be sent to the machine %r: %s",
                session.session_id,
                session.machine_id,
                exc,
            )
            return await self._end_without_machine(
                session,
                agent_id=agent_id,
                instance_id=instance_id,
                error_code=LEDGER_MACHINE_UNREACHABLE,
                detail=(
                    "the machine is unreachable; the center released the "
                    "session authoritatively"
                ),
                audit_detail=AUDIT_DETAIL_SESSION_UNREACHABLE,
            )

        # (a) The machine acknowledged the end.
        return await self._end_acknowledged(
            session,
            agent_id=agent_id,
            instance_id=instance_id,
            error_code="",
            detail="the machine acknowledged the end of the session",
            audit_detail=AUDIT_DETAIL_SESSION_ENDED,
            result=result,
        )

    async def _end_acknowledged(
        self,
        session: GatewaySession,
        *,
        agent_id: str,
        instance_id: str,
        error_code: str,
        detail: str,
        audit_detail: str,
        result: Any = _NO_RESULT,
    ) -> dict[str, Any]:
        """The end the MACHINE confirmed (or confirmed it no longer holds).

        The centre-side state is released and the session leaves an
        ``already_ended`` tombstone, so a LATE end answers
        ``already_ended`` instead of a bare ``unknown_session``.
        """
        # The machine ANSWERED (it acknowledged the end, or it held no
        # such session) — so it WAS notified.
        self._release_centre_side(session, machine_notified=True)
        envelope: dict[str, Any] = {
            "session_id": session.session_id,
            "machine_id": session.machine_id,
            "ended": True,
            "detail": detail,
            "machine_notified": True,
        }
        if result is not _NO_RESULT:
            # The machine's VERBATIM acknowledgement — only on the
            # acknowledged path.
            envelope["result"] = result
        await self._audit_session(
            agent_id=agent_id,
            instance_id=instance_id,
            machine_id=session.machine_id,
            session_id=session.session_id,
            tool=AUDIT_TOOL_SESSION_END,
            outcome=OUTCOME_OK,
            error_code=error_code,
            detail=audit_detail,
        )
        return envelope

    async def _end_without_machine(
        self,
        session: GatewaySession,
        *,
        agent_id: str,
        instance_id: str,
        error_code: str,
        detail: str,
        audit_detail: str,
    ) -> dict[str, Any]:
        """The end the CENTRE released AUTHORITATIVELY.

        The machine was not there (or did not answer in time): the
        centre's release is the truth, and the ledger records WHY — the
        ledger-only token (``machine_unreachable`` / ``machine_timeout``),
        never a response reason.

        ``machine_notified`` is ``False`` here because the machine did
        not ACKNOWLEDGE the end — and the machine's LOCAL LOCK may
        therefore persist until it processes the frame or the connection
        dies. The agent must not assume the machine is free.
        """
        # The machine was not there (or did not answer) — it was NOT
        # notified of the end.
        self._release_centre_side(session, machine_notified=False)
        await self._audit_session(
            agent_id=agent_id,
            instance_id=instance_id,
            machine_id=session.machine_id,
            session_id=session.session_id,
            tool=AUDIT_TOOL_SESSION_END,
            outcome=(
                OUTCOME_TIMEOUT
                if error_code == LEDGER_MACHINE_TIMEOUT
                else OUTCOME_ERROR
            ),
            error_code=error_code,
            detail=audit_detail,
        )
        return {
            "session_id": session.session_id,
            "machine_id": session.machine_id,
            "ended": True,
            "detail": detail,
            "machine_notified": False,
        }

    async def _end_without_session(
        self, agent_id: str, instance_id: str
    ) -> dict[str, Any]:
        """The end of a session that is NOT (or no longer) live.

        The agent's most recent tombstone decides the answer: ``expired``
        (the sweep reaped it) or ``already_ended`` (it was ended). With
        no tombstone the honest answer is ``unknown_session`` — ONE
        neutral text for "never existed" and "not yours", so the answer
        can never become an existence oracle for somebody else's
        session.

        NO audit row is written for the unknown case: there is no
        machine to attribute the row to, and the ledger attributes EVERY
        row.
        """
        tombstone = self._tombstones.latest_for_agent(agent_id, self._clock())
        if tombstone is None:
            return {
                "ended": False,
                "why_not_ended": REASON_UNKNOWN_SESSION,
                "detail": "there is no open session for this agent",
                "machine_notified": False,
            }
        if tombstone.reason == REASON_EXPIRED:
            detail = "the session expired and was reaped by the center"
            audit_detail = AUDIT_DETAIL_SESSION_EXPIRED
        else:
            detail = "the session was already ended"
            audit_detail = AUDIT_DETAIL_SESSION_ALREADY_ENDED
        # The RESPONSE carries a response-borne reason only — the guard
        # is the runtime half of the "two disjoint sets" invariant.
        response_reason = self._response_reason(tombstone.reason)
        if tombstone.machine_id:
            # The ledger attributes EVERY row — a row without a machine
            # cannot be attributed, so it is not written.
            await self._audit_session(
                agent_id=tombstone.agent_id,
                instance_id=tombstone.instance_id or instance_id,
                machine_id=tombstone.machine_id,
                session_id=tombstone.session_id,
                tool=AUDIT_TOOL_SESSION_END,
                outcome=OUTCOME_ERROR,
                error_code=tombstone.reason,
                detail=audit_detail,
            )
        envelope: dict[str, Any] = {
            "ended": False,
            "why_not_ended": response_reason,
            "detail": detail,
            # Whether the MACHINE was told when the session ended — the
            # tombstone knows it (the explicit end records the ack, the
            # sweep records the forward's outcome). It is FALSE while
            # the sweep's forward is still in flight (the tombstone is
            # written before it) and for a session that died with the
            # socket.
            "machine_notified": tombstone.machine_notified,
        }
        if tombstone.session_id:
            envelope["session_id"] = tombstone.session_id
        if tombstone.machine_id:
            envelope["machine_id"] = tombstone.machine_id
        return envelope

    @staticmethod
    def _safe_peer_code(code: str) -> str:
        """The peer's error code, sanitised for an agent-facing detail.

        The code slot of the machine's error frame is PEER-CONTROLLED:
        echoing it verbatim would let a hostile machine put ANY string
        into the response — including a LEDGER-ONLY token
        (``machine_timeout`` / ``machine_unreachable``), which the
        disjointness contract says is a bug by construction, and
        including free text that would end up in an agent-facing string.

        So only the PROTOCOL vocabulary is echoed; everything else
        becomes the neutral :data:`UNRECOGNIZED_ERROR_CODE`.

        The TYPE is checked first: a code that is not a string (or is
        unhashable) would make the membership test raise, and a raise
        HERE would land in the broad transport arm of the end — i.e. it
        would resurrect the bare-error failure mode this sanitiser
        exists to prevent. It is unreachable from a peer (the code slot
        is coerced to ``str`` when the frame is delivered), but the
        guard costs one line.
        """
        if not isinstance(code, str):
            return UNRECOGNIZED_ERROR_CODE
        if code in CENTER_EMITTED_CODES or code in RELAYED_CODES:
            return code
        return UNRECOGNIZED_ERROR_CODE

    @staticmethod
    def _response_reason(reason: str) -> str:
        """Guard the DISJOINT reason sets at runtime.

        A response may carry ONLY a response-borne reason. A ledger-only
        token here would be a BUG — those describe the CENTRE's release
        path (``machine_unreachable`` / ``machine_timeout``), not the
        agent's session, and an agent that branched on them would be
        branching on an implementation detail. The guard logs the leak
        loudly and falls back to the neutral reason.
        """
        if reason in RESPONSE_REASONS:
            return reason
        logger.error(
            "the ledger-only reason %r reached an end response — this is a "
            "bug (the two reason sets must stay disjoint)",
            reason,
        )
        return REASON_UNKNOWN_SESSION

    def _release_centre_side(
        self, session: GatewaySession, *, machine_notified: bool = False
    ) -> None:
        """Release the centre-side state of a session and remember it.

        The roster release is session-id-guarded (``store.clear_session``
       ): a machine that re-helloed and bound a NEW session keeps that
        new session.

        ``machine_notified`` is what the caller KNOWS: ``True`` when the
        machine answered (it acknowledged the end, refused it, or held no
        such session), ``False`` when the centre released the session on
        its own (unreachable / deadline) or the session died with the
        socket. The late end's envelope reads it back.
        """
        self._sessions.end(session.session_id)
        self._store.clear_session(session.machine_id, session.session_id)
        self._record_tombstone(
            session, reason=REASON_ALREADY_ENDED, machine_notified=machine_notified
        )

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

        # S1: the row carries a FIXED classification and the error code
        # — NEVER the peer's free text. The peer text stays agent-facing
        # (the tool result carries it); the ledger must not become the
        # place a hostile machine parks the command arguments.
        if error_code and error_code not in AUDIT_ALLOWED_ERROR_CODES:
            # The code came from the PEER. Anything outside the protocol
            # vocabulary is not recorded verbatim.
            error_code = UNRECOGNIZED_ERROR_CODE
        if outcome == OUTCOME_OK:
            detail = AUDIT_DETAIL_OK
        elif outcome == OUTCOME_TIMEOUT:
            detail = AUDIT_DETAIL_TIMEOUT
        elif error_code == UNRECOGNIZED_ERROR_CODE:
            detail = AUDIT_DETAIL_UNRECOGNIZED_CODE
        else:
            detail = AUDIT_DETAIL_ERROR
        if error_code:
            detail = f"{detail} [{error_code}]"

        record = AuditRecord(
            agent_id=agent_id,
            instance_id=instance_id,
            machine_id=machine_id,
            tool=tool,
            args_hash=hash_args(arguments),
            outcome=outcome,
            session_id=session_id,
            error_code=error_code,
            detail=detail,
            created_at=now_utc_iso(),
        )
        await self._write_audit_record(record)

    async def _audit_session(
        self,
        *,
        agent_id: str,
        instance_id: str,
        machine_id: str,
        session_id: str,
        tool: str,
        outcome: str,
        error_code: str = "",
        detail: str = "",
    ) -> None:
        """Write ONE audit row for a SESSION-LIFECYCLE event.

        The session surfaces (``session.end`` — the explicit end, and
        ``session.expire`` — the sweep's reap) have no remote tool, so
        their rows carry the SURFACE name in the ``tool`` column and the
        hash of the session.end PARAMS in ``args_hash`` — the arguments
        are hashed, never stored (the same rule as a command row).

        The ledger is where the operator asks "why did this session not
        end?", so it needs the reason. The ``error_code`` carries a
        token of the session surface's OWN vocabulary: the PROTOCOL codes
        (the machine may answer ``SESSION_NOT_FOUND``) PLUS the
        centre-minted tokens — the RESPONSE-borne reasons (``expired``,
        ``already_ended``, ``unknown_session``, ``machine_refused``), the
        LEDGER-only reasons (``machine_unreachable``, ``machine_timeout``)
        and the expiry causes (``idle``, ``ttl``). It NEVER carries the
        peer's free text — the ``detail`` is a FIXED classification.

        The vocabulary is :data:`AUDIT_ALLOWED_SESSION_ERROR_CODES` — the
        protocol codes PLUS the centre-only tokens. The COMMAND rows keep
        the protocol-only :data:`AUDIT_ALLOWED_ERROR_CODES`, so a hostile
        machine cannot forge a centre token into a command row.

        The write inherits the command path's discipline: it is off the
        event loop, it is bounded, and it NEVER aborts the operation.
        """
        if not self.config.audit_enabled:
            return
        if error_code and error_code not in AUDIT_ALLOWED_SESSION_ERROR_CODES:
            error_code = UNRECOGNIZED_ERROR_CODE
        if error_code:
            detail = f"{detail} [{error_code}]" if detail else f"[{error_code}]"
        record = AuditRecord(
            agent_id=agent_id,
            instance_id=instance_id,
            machine_id=machine_id,
            tool=tool,
            args_hash=hash_args({"session_id": session_id}),
            outcome=outcome,
            session_id=session_id,
            error_code=error_code,
            detail=detail,
            created_at=now_utc_iso(),
        )
        await self._write_audit_record(record)

    async def _write_audit_record(self, record: AuditRecord) -> None:
        """Write ONE audit row — off the event loop, bounded, never fatal.

        An audit failure NEVER aborts the operation the row describes: a
        command that was forwarded, or a session that was ended, has
        already happened — refusing to report it because the
        OBSERVABILITY write failed would turn an audit problem into an
        availability problem. The failure is logged loudly and counted.

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
        durable before the operation returns, so there is no race between
        the audit and the answer, and a shutdown cannot lose the row of
        an operation that already happened. Nothing else is held while
        it runs (no lock, no session), so the offload cannot deadlock.
        """
        try:
            # R1: the write is BOUNDED. A stalled PostgreSQL must not
            # hang an agent tool call indefinitely — the write is
            # abandoned, the failure is counted and logged, and the
            # operation result stands.
            await asyncio.wait_for(
                asyncio.to_thread(self._audit.record, record),
                timeout=self._audit_write_timeout_seconds,
            )
        except asyncio.TimeoutError:
            self._audit_failures += 1
            logger.error(
                "the remote-command audit write timed out after %.1fs (%d "
                "failures so far); the operation result stands",
                self._audit_write_timeout_seconds,
                self._audit_failures,
            )
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
