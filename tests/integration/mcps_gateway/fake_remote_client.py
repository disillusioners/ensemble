"""Independent fake remote-machine client for the remote-hands protocol (v0.1).

INDEPENDENCE CONTRACT
---------------------
This module is **self-contained**: standard library + ``websockets`` only.
It imports NOTHING from ``daemon.*`` and nothing from any gateway
implementation.  Every wire shape encoded below was transcribed from the
**specification** (``mcps_client/SPEC.md``, v0.1) — never from gateway
source.  The test harness (``harness.py``) is a separate module and is the
only place allowed to touch ``daemon.*``.

SPEC anchors used by this module (section numbers = SPEC.md v0.1):

* §3.1  Transport — one JSON object per WebSocket text frame; the bearer
  credential travels in the ``Authorization`` header of the *upgrade*
  request; a refused upgrade exchanges **no** protocol message.
* §3.3  Heartbeat parameters — the **server** advertises them in
  ``hello.result`` and the client **MUST adopt** the server's values.
* §5.1  ``hello`` — mandatory, once per connection, after a successful
  upgrade; it is a protocol/version exchange, *not* authentication.
* §5.3  Capability flags — a flat map of booleans; ``true`` = supported,
  ``false``/absent = not supported.
* §5.4  ``hello`` / ``hello.result`` shapes.
* §6.1  Message envelope — ``v`` / ``type`` / ``id`` / ``session_id`` /
  ``ts`` / ``method`` / ``params`` / ``result`` / ``error``.
* §6.3  The method set — seven methods; ``heartbeat`` is a *notification*
  with no ``id`` and **no response required**.
* §6.4  ``session.begin`` → ``session_id`` + tool catalog.  The
  ``session_id`` in the envelope is canonical and **MUST** match the one
  repeated inside ``result``.
* §6.5  Typed errors — stable machine-readable ``code``; ``message`` is
  advisory and must not be parsed.
* §6.6  ``tools/call`` — ``params`` is the MCP ``tools/call`` parameter
  object **unchanged** (``{"name", "arguments"}``); the raw MCP result is
  returned as-is.
* §6.8  ``session.end`` — explicit close; the connection returns to
  ``ready`` and **the socket stays open**.
* §8.3  v1 session policy — one active session per machine; the **machine**
  must refuse a concurrent ``session.begin`` for the same ``machine_id``
  (§6.5 ``MACHINE_BUSY``).

The transcript buffer is the evidentiary artefact: every frame exchanged in
either direction is recorded verbatim, together with any envelope
violations observed.  Violations are **recorded, never raised** — the brief
requires observation without failing on them.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Iterable

import websockets
from websockets.exceptions import ConnectionClosed, InvalidStatus

# --------------------------------------------------------------------------
# Constants transcribed from SPEC.md
# --------------------------------------------------------------------------

PROTOCOL_VERSION = "0.1"  # §5.2 / §6.1

#: §5.3 capability table — every key listed explicitly, both peers carry the
#: same key set.  The four "required" ones are ``True``; the optional /
#: v0.2-reserved ones are explicitly ``False``.
CLIENT_CAPABILITIES: dict[str, bool] = {
    "session": True,
    "tools": True,
    "notifications": True,
    "heartbeat": True,
    "screenshot_delta": False,
    "multi_session": False,
    "client_cert": False,
}

#: §6.1 — the only legal ``type`` values.
ENVELOPE_TYPES = {"request", "response", "error", "notification"}

#: §6.5 — error codes this client may emit or must understand.
CODE_MACHINE_BUSY = "MACHINE_BUSY"
CODE_SESSION_NOT_FOUND = "SESSION_NOT_FOUND"
CODE_TOOL_NOT_FOUND = "TOOL_NOT_FOUND"
CODE_TOOL_TIMEOUT = "TOOL_TIMEOUT"
CODE_INTERNAL = "INTERNAL"

CLIENT_TO_SERVER = "client->server"
SERVER_TO_CLIENT = "server->client"


# --------------------------------------------------------------------------
# Recording structures
# --------------------------------------------------------------------------


@dataclass
class TranscriptEntry:
    """One frame, verbatim, in either direction."""

    direction: str
    raw: str
    frame: dict[str, Any] | None = None
    parsed_ok: bool = True
    parse_error: str | None = None
    at_ms: int = field(default_factory=lambda: int(time.time() * 1000))

    @property
    def method(self) -> str | None:
        if isinstance(self.frame, dict):
            m = self.frame.get("method")
            return m if isinstance(m, str) else None
        return None

    @property
    def type(self) -> str | None:
        if isinstance(self.frame, dict):
            t = self.frame.get("type")
            return t if isinstance(t, str) else None
        return None

    @property
    def id(self) -> str | None:
        if isinstance(self.frame, dict):
            i = self.frame.get("id")
            return i if isinstance(i, str) else None
        return None


@dataclass
class EnvelopeViolation:
    """A recorded (never raised) deviation from the SPEC envelope rules."""

    reason: str
    detail: str
    frame: dict[str, Any] | None
    spec_section: str


@dataclass
class ToolSpec:
    """An entry of the remote tool catalog (MCP tool object, §6.4)."""

    name: str
    description: str
    input_schema: dict[str, Any]


def default_catalog() -> list[ToolSpec]:
    """The catalog this fake machine advertises.

    §6.4 requires the tool entries to be the MCP tool objects as returned by
    the embedded server, with their ``inputSchema`` intact.  The catalog
    contains the three shapes the brief requires:

    * ``echo`` — an ordinary tool with a real ``inputSchema``;
    * ``fail_tool`` — returns an MCP result carrying ``isError: true``;
    * ``slow_tool`` — sleeps past the caller's timeout.
    """
    return [
        ToolSpec(
            name="echo",
            description="Echo the supplied message back to the caller.",
            input_schema={
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "text to echo"},
                    "count": {"type": "integer", "minimum": 1, "maximum": 10},
                },
                "required": ["message"],
                "additionalProperties": False,
            },
        ),
        ToolSpec(
            name="fail_tool",
            description="Always returns an MCP result flagged isError=true.",
            input_schema={
                "type": "object",
                "properties": {"reason": {"type": "string"}},
                "additionalProperties": False,
            },
        ),
        ToolSpec(
            name="slow_tool",
            description="Sleeps for a long time before answering.",
            input_schema={
                "type": "object",
                "properties": {"seconds": {"type": "number"}},
                "additionalProperties": False,
            },
        ),
    ]


# --------------------------------------------------------------------------
# The fake machine
# --------------------------------------------------------------------------


class FakeRemoteMachine:
    """A fake controlled machine that dials the center and answers requests.

    The center is the *request initiator* for ``session.begin``,
    ``tools/list``, ``tools/call`` and ``session.end``; this class is the
    responder for all of them.
    """

    def __init__(
        self,
        machine_id: str = "mach_test1",
        *,
        catalog: Iterable[ToolSpec] | None = None,
        client_name: str = "mcps_client",
        client_version: str = "0.1.0",
        protocol_version: str = PROTOCOL_VERSION,
        capabilities: dict[str, bool] | None = None,
        heartbeat_enabled: bool = True,
        slow_sleep_seconds: float = 3.0,
        session_ttl_ms: int = 3_600_000,
    ) -> None:
        self.machine_id = machine_id
        self.client_name = client_name
        self.client_version = client_version
        self.protocol_version = protocol_version
        self.capabilities = dict(CLIENT_CAPABILITIES if capabilities is None else capabilities)
        self.heartbeat_enabled = heartbeat_enabled
        self.slow_sleep_seconds = slow_sleep_seconds
        self.session_ttl_ms = session_ttl_ms

        self.tools: list[ToolSpec] = list(default_catalog() if catalog is None else catalog)

        # --- evidence buffers -------------------------------------------
        self.transcript: list[TranscriptEntry] = []
        self.violations: list[EnvelopeViolation] = []
        self.requests_received: list[dict[str, Any]] = []

        # --- protocol state ---------------------------------------------
        self.active_session_id: str | None = None
        self.session_catalog: list[dict[str, Any]] = []
        self.ended_session_ids: list[str] = []
        self.busy_refusals: list[str] = []
        self.hello_result: dict[str, Any] | None = None
        self.server_hello_result: dict[str, Any] | None = None
        self.closed = asyncio.Event()
        self.close_code: int | None = None
        self.close_reason: str | None = None
        self.last_error_frame: dict[str, Any] | None = None
        #: monotonic timestamp of the last heartbeat this client sent — used to
        #: measure the *actual* silence the server tolerated (§3.3).
        self.last_heartbeat_monotonic: float | None = None

        # --- internals ----------------------------------------------------
        self._ws: Any = None
        self._reader_task: asyncio.Task | None = None
        self._heartbeat_task: asyncio.Task | None = None
        self._heartbeat_interval_ms: int | None = None
        self._pending: dict[str, asyncio.Future] = {}
        self._request_ids_sent: set[str] = set()
        self._counter = 0

    # -- transcript helpers -------------------------------------------------

    def _record(
        self,
        direction: str,
        raw: str,
        frame: dict[str, Any] | None,
        parsed_ok: bool = True,
        parse_error: str | None = None,
    ) -> TranscriptEntry:
        entry = TranscriptEntry(
            direction=direction, raw=raw, frame=frame, parsed_ok=parsed_ok, parse_error=parse_error
        )
        self.transcript.append(entry)
        return entry

    def inbound_frames(self) -> list[dict[str, Any]]:
        return [e.frame for e in self.transcript if e.direction == SERVER_TO_CLIENT and e.frame]

    def outbound_frames(self) -> list[dict[str, Any]]:
        return [e.frame for e in self.transcript if e.direction == CLIENT_TO_SERVER and e.frame]

    def frames_of_type(self, frame_type: str) -> list[dict[str, Any]]:
        return [f for f in self.inbound_frames() if f.get("type") == frame_type]

    def frames_with_method(self, method: str) -> list[dict[str, Any]]:
        return [f for f in self.inbound_frames() if f.get("method") == method]

    def error_codes(self) -> list[str]:
        out: list[str] = []
        for f in self.inbound_frames():
            if f.get("type") == "error":
                err = f.get("error")
                if isinstance(err, dict) and isinstance(err.get("code"), str):
                    out.append(err["code"])
        return out

    def violations_by_reason(self, reason: str) -> list[EnvelopeViolation]:
        return [v for v in self.violations if v.reason == reason]

    # -- envelope validation (record, never raise) --------------------------

    def validate_envelope(self, frame: Any) -> list[EnvelopeViolation]:
        """Check one inbound frame against the §6.1 envelope rules.

        Violations are appended to :attr:`violations` and returned; the frame
        is still processed.  This is the (f) requirement of the brief.
        """
        found: list[EnvelopeViolation] = []

        if not isinstance(frame, dict):
            v = EnvelopeViolation(
                reason="not_an_object",
                detail=f"frame is {type(frame).__name__}, expected a JSON object",
                frame=None,
                spec_section="§6.1",
            )
            self.violations.append(v)
            found.append(v)
            return found

        # §6.1 — "v" is required and is the protocol version of this message.
        v = frame.get("v")
        if v is None:
            viol = EnvelopeViolation(
                reason="missing_v",
                detail="frame has no 'v' member",
                frame=frame,
                spec_section="§6.1",
            )
            self.violations.append(viol)
            found.append(viol)
        elif v != PROTOCOL_VERSION:
            viol = EnvelopeViolation(
                reason="bad_v",
                detail=f"expected v=={PROTOCOL_VERSION!r}, got {v!r}",
                frame=frame,
                spec_section="§6.1",
            )
            self.violations.append(viol)
            found.append(viol)

        # §6.1 — "type" is required and one of four values.
        t = frame.get("type")
        if t is None:
            viol = EnvelopeViolation(
                reason="missing_type",
                detail="frame has no 'type' member",
                frame=frame,
                spec_section="§6.1",
            )
            self.violations.append(viol)
            found.append(viol)
        elif t not in ENVELOPE_TYPES:
            viol = EnvelopeViolation(
                reason="bad_type",
                detail=f"type={t!r} is not one of {sorted(ENVELOPE_TYPES)}",
                frame=frame,
                spec_section="§6.1",
            )
            self.violations.append(viol)
            found.append(viol)

        # §6.1 — "id" is required on request/response/error, and a response
        # echoes the request id *verbatim*.  A response whose id was never
        # requested by this peer cannot be correlated → recorded.
        if t in {"response", "error"}:
            fid = frame.get("id")
            if fid is None:
                viol = EnvelopeViolation(
                    reason="missing_id",
                    detail=f"type={t!r} frame carries no 'id'",
                    frame=frame,
                    spec_section="§6.1",
                )
                self.violations.append(viol)
                found.append(viol)
            elif isinstance(fid, str) and fid not in self._request_ids_sent:
                viol = EnvelopeViolation(
                    reason="orphan_response",
                    detail=(
                        f"type={t!r} frame id={fid!r} does not echo any id this "
                        f"client sent (sent: {sorted(self._request_ids_sent)})"
                    ),
                    frame=frame,
                    spec_section="§6.1",
                )
                self.violations.append(viol)
                found.append(viol)

        # §6.1 — a response must carry "result"; an error must carry "error".
        if t == "response" and "result" not in frame:
            viol = EnvelopeViolation(
                reason="response_without_result",
                detail="type='response' frame has no 'result' member",
                frame=frame,
                spec_section="§6.1",
            )
            self.violations.append(viol)
            found.append(viol)
        if t == "error" and "error" not in frame:
            viol = EnvelopeViolation(
                reason="error_without_error",
                detail="type='error' frame has no 'error' member",
                frame=frame,
                spec_section="§6.1",
            )
            self.violations.append(viol)
            found.append(viol)

        # §6.3 — only seven methods appear on the wire.
        m = frame.get("method")
        if m is not None and not isinstance(m, str):
            viol = EnvelopeViolation(
                reason="bad_method_type",
                detail=f"'method' must be a string, got {type(m).__name__}",
                frame=frame,
                spec_section="§6.1",
            )
            self.violations.append(viol)
            found.append(viol)

        return found

    # -- connection ---------------------------------------------------------

    async def connect(self, url: str, token: str | None) -> None:
        """Perform the WSS/WS upgrade.

        §3.1/§4.1 — the credential is carried in the ``Authorization: Bearer``
        header of the upgrade request.  A refused upgrade raises
        :class:`websockets.exceptions.InvalidStatus` and **no** protocol
        message is exchanged.
        """
        headers = {}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        self._ws = await websockets.connect(url, additional_headers=headers)
        self._reader_task = asyncio.create_task(self._reader_loop())

    async def close(self) -> None:
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            self._heartbeat_task = None
        if self._reader_task is not None:
            self._reader_task.cancel()
            self._reader_task = None
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

    async def __aenter__(self) -> "FakeRemoteMachine":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    # -- raw send -----------------------------------------------------------

    async def send_frame(self, frame: dict[str, Any]) -> None:
        raw = json.dumps(frame)
        self._record(CLIENT_TO_SERVER, raw, frame)
        if isinstance(frame.get("id"), str):
            self._request_ids_sent.add(frame["id"])
        if self._ws is None:
            # Detached mode: the client is exercised without a socket (the
            # self-tests drive the protocol logic directly).  The frame is
            # still recorded, so the transcript remains the evidence.
            return
        await self._ws.send(raw)

    def _next_id(self, prefix: str = "cli") -> str:
        self._counter += 1
        return f"{prefix}-{self._counter:04d}-{uuid.uuid4().hex[:6]}"

    # -- reader -------------------------------------------------------------

    async def _reader_loop(self) -> None:
        try:
            async for raw in self._ws:
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8", errors="replace")
                try:
                    frame = json.loads(raw)
                except Exception as exc:  # noqa: BLE001
                    self._record(SERVER_TO_CLIENT, raw, None, False, f"{type(exc).__name__}: {exc}")
                    continue
                self._record(SERVER_TO_CLIENT, raw, frame)
                # §6.1 — record (never raise) envelope violations.
                self.validate_envelope(frame)
                await self._dispatch(frame)
        except ConnectionClosed as exc:
            self.close_code = getattr(exc, "code", None) or getattr(exc, "rcvd", None) and getattr(
                exc.rcvd, "code", None
            )
            self.close_reason = str(exc)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            self.close_reason = f"{type(exc).__name__}: {exc}"
        finally:
            self.closed.set()

    # -- dispatch -----------------------------------------------------------

    async def _dispatch(self, frame: dict[str, Any]) -> None:
        ftype = frame.get("type")

        if ftype == "response" or ftype == "error":
            fid = frame.get("id")
            if ftype == "error":
                self.last_error_frame = frame
            fut = self._pending.pop(fid, None) if isinstance(fid, str) else None
            if fut is not None and not fut.done():
                fut.set_result(frame)
            return

        if ftype != "request":
            return

        method = frame.get("method")
        self.requests_received.append(frame)

        if method == "session.begin":
            await self._handle_session_begin(frame)
        elif method == "tools/list":
            await self._handle_tools_list(frame)
        elif method == "tools/call":
            await self._handle_tools_call(frame)
        elif method == "session.end":
            await self._handle_session_end(frame)
        elif method == "heartbeat":
            # §6.3 — heartbeat is a client→center notification; the center
            # should never send one to us.  Answer politely but record it.
            await self._send_error(frame, CODE_INTERNAL, "heartbeat is a client→center notification")
        else:
            await self._send_error(
                frame,
                CODE_INTERNAL,
                f"unknown method {method!r}",
            )

    # -- request handlers (the center is the requester) ---------------------

    def _mint_session_id(self) -> str:
        # §6.4 — the SPEC's illustrative id is "sess_<...>"; the brief asks
        # for "sess_" + 16 hex chars.
        return "sess_" + uuid.uuid4().hex[:16]

    async def _handle_session_begin(self, frame: dict[str, Any]) -> None:
        """§6.4 / §8.3 — adopt the session and return the tool catalog.

        §8.3 — "the machine itself MUST refuse a concurrent ``session.begin``
        for the same ``machine_id``".  A refusal is signalled with
        ``MACHINE_BUSY`` (§6.5) and the *original* session must survive.
        """
        if self.active_session_id is not None:
            self.busy_refusals.append(self.active_session_id)
            await self._send_error(
                frame,
                CODE_MACHINE_BUSY,
                f"machine {self.machine_id} already has an active session",
                session_id=self.active_session_id,
            )
            return

        session_id = self._mint_session_id()
        expires_at_ms = int(time.time() * 1000) + self.session_ttl_ms
        catalog = [
            {
                "name": t.name,
                "description": t.description,
                "inputSchema": t.input_schema,
            }
            for t in self.tools
        ]
        self.active_session_id = session_id
        self.session_catalog = catalog

        # §6.4 — the envelope session_id is canonical and MUST match the one
        # repeated inside result.
        await self.send_frame(
            {
                "v": PROTOCOL_VERSION,
                "type": "response",
                "id": frame.get("id"),
                "session_id": session_id,
                "result": {
                    "session_id": session_id,
                    "machine_id": self.machine_id,
                    "expires_at_ms": expires_at_ms,
                    "tools": catalog,
                },
            }
        )

    async def _handle_tools_list(self, frame: dict[str, Any]) -> None:
        """§6.3 — ``tools/list`` is MCP core forwarded verbatim."""
        catalog = [
            {"name": t.name, "description": t.description, "inputSchema": t.input_schema}
            for t in self.tools
        ]
        await self.send_frame(
            {
                "v": PROTOCOL_VERSION,
                "type": "response",
                "id": frame.get("id"),
                "session_id": self.active_session_id,
                "result": {"tools": catalog},
            }
        )

    async def _handle_tools_call(self, frame: dict[str, Any]) -> None:
        """§6.6 — ``params`` is the MCP ``tools/call`` parameter object,
        unchanged; the raw MCP result is returned as-is."""
        params = frame.get("params")
        if not isinstance(params, dict):
            await self._send_error(frame, CODE_INTERNAL, "tools/call params must be an object")
            return
        name = params.get("name")
        arguments = params.get("arguments")
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            await self._send_error(frame, CODE_INTERNAL, "tools/call arguments must be an object")
            return

        if name == "fail_tool":
            result = {
                "content": [{"type": "text", "text": "the tool refused to run"}],
                "isError": True,
            }
        elif name == "slow_tool":
            secs = arguments.get("seconds")
            if not isinstance(secs, (int, float)):
                secs = self.slow_sleep_seconds
            await asyncio.sleep(float(secs))
            result = {
                "content": [{"type": "text", "text": f"slept {secs}s"}],
                "isError": False,
            }
        elif name == "echo":
            message = arguments.get("message")
            result = {
                "content": [{"type": "text", "text": f"echo: {message}"}],
                "isError": False,
            }
        else:
            await self._send_error(
                frame, CODE_TOOL_NOT_FOUND, f"unknown tool {name!r}", session_id=self.active_session_id
            )
            return

        # §6.6 — the raw MCP result goes out unchanged.
        await self.send_frame(
            {
                "v": PROTOCOL_VERSION,
                "type": "response",
                "id": frame.get("id"),
                "session_id": self.active_session_id,
                "result": result,
            }
        )

    async def _handle_session_end(self, frame: dict[str, Any]) -> None:
        """§6.8 — acknowledge, release the local lock, keep the socket open."""
        params = frame.get("params") if isinstance(frame.get("params"), dict) else {}
        session_id = params.get("session_id") or frame.get("session_id") or self.active_session_id
        if session_id:
            self.ended_session_ids.append(session_id)
        self.active_session_id = None
        await self.send_frame(
            {
                "v": PROTOCOL_VERSION,
                "type": "response",
                "id": frame.get("id"),
                "session_id": session_id,
                "result": {"session_id": session_id, "ended": True},
            }
        )

    # -- error emission -----------------------------------------------------

    async def _send_error(
        self,
        frame: dict[str, Any],
        code: str,
        message: str,
        *,
        session_id: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> None:
        """§6.5 — typed error object.  ``message`` is advisory."""
        await self.send_frame(
            {
                "v": PROTOCOL_VERSION,
                "type": "error",
                "id": frame.get("id"),
                "session_id": session_id if session_id is not None else frame.get("session_id"),
                "error": {"code": code, "message": message, "data": data or {}},
            }
        )

    # -- client-originated traffic ------------------------------------------

    async def send_hello(self) -> dict[str, Any]:
        """§5.1/§5.4 — the mandatory, once-per-connection hello exchange."""
        request_id = self._next_id("hello")
        await self.send_frame(
            {
                "v": PROTOCOL_VERSION,
                "type": "request",
                "id": request_id,
                "method": "hello",
                "params": {
                    "protocol_version": self.protocol_version,
                    "client": {"name": self.client_name, "version": self.client_version},
                    "machine_id": self.machine_id,
                    "capabilities": dict(self.capabilities),
                },
            }
        )
        response = await self.wait_for(lambda f: f.get("id") == request_id, timeout=5.0)
        if isinstance(response, dict) and response.get("type") == "response":
            self.hello_result = response.get("result")
            self.server_hello_result = response.get("result")
        return response

    async def send_heartbeat(self) -> None:
        """§3.3/§6.3 — heartbeat is a notification; no id, no response."""
        self.last_heartbeat_monotonic = time.monotonic()
        await self.send_frame(
            {
                "v": PROTOCOL_VERSION,
                "type": "notification",
                "method": "heartbeat",
                "params": {"machine_id": self.machine_id},
                **({"session_id": self.active_session_id} if self.active_session_id else {}),
            }
        )

    async def send_unknown_method(self, method: str = "totally.unknown.method") -> str:
        """Send a request the center cannot possibly understand (§6.3 method
        set has exactly seven members).  Used to observe the center's
        reaction to an unknown method."""
        request_id = self._next_id("unk")
        await self.send_frame(
            {
                "v": PROTOCOL_VERSION,
                "type": "request",
                "id": request_id,
                "method": method,
                "params": {},
            }
        )
        return request_id

    # -- heartbeat ----------------------------------------------------------

    def adopt_heartbeat_interval(self, hello_result: dict[str, Any] | None) -> int | None:
        """§3.3 — the client MUST adopt the server's advertised interval."""
        if not isinstance(hello_result, dict):
            return None
        value = hello_result.get("heartbeat_interval_ms")
        if isinstance(value, int):
            self._heartbeat_interval_ms = value
        return self._heartbeat_interval_ms

    async def start_heartbeat(self, interval_ms: int | None = None) -> None:
        if interval_ms is not None:
            self._heartbeat_interval_ms = interval_ms
        if self._heartbeat_task is not None:
            return
        if not self.heartbeat_enabled:
            return
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    async def _heartbeat_loop(self) -> None:
        try:
            while True:
                interval = self._heartbeat_interval_ms or 15000
                await asyncio.sleep(interval / 1000.0)
                if self._ws is None:
                    return
                try:
                    await self.send_heartbeat()
                except Exception:
                    return
        except asyncio.CancelledError:
            raise

    async def stop_heartbeat(self) -> None:
        """Stop heartbeating — the §3.3/§6.8 kill-switch trigger."""
        self.heartbeat_enabled = False
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._heartbeat_task = None

    # -- waiting helpers ----------------------------------------------------

    async def wait_for(
        self,
        predicate: Callable[[dict[str, Any]], bool],
        timeout: float = 5.0,
    ) -> dict[str, Any] | None:
        """Wait for an inbound frame matching ``predicate``."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for f in self.inbound_frames():
                try:
                    if predicate(f):
                        return f
                except Exception:  # noqa: BLE001
                    continue
            if self.closed.is_set():
                # one last sweep
                for f in self.inbound_frames():
                    if predicate(f):
                        return f
                return None
            await asyncio.sleep(0.02)
        for f in self.inbound_frames():
            if predicate(f):
                return f
        return None

    async def wait_for_close(self, timeout: float = 5.0) -> bool:
        try:
            await asyncio.wait_for(self.closed.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    # -- evidence -----------------------------------------------------------

    def transcript_dump(self) -> str:
        lines = []
        for e in self.transcript:
            lines.append(f"{e.direction:14} {e.raw}")
        return "\n".join(lines)

    def violations_dump(self) -> str:
        if not self.violations:
            return "(no envelope violations recorded)"
        return "\n".join(
            f"[{v.spec_section}] {v.reason}: {v.detail}" for v in self.violations
        )
