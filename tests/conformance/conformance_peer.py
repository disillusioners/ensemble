"""The conformance kit's protocol module — the machine-side wire peer, the
FINDING machinery, and the in-kit reference / mutated centers.

INDEPENDENCE CONTRACT
---------------------
This module imports **stdlib + ``websockets`` (+ ``urllib`` for the
begin-hook URL lane) only** — nothing from ``daemon.*``, nothing from any
gateway implementation.  Every wire shape below is transcribed from
``mcps_client/SPEC.md`` v0.1 (the protocol authority), with the known
current behaviours of the reference center pinned where the SPEC is
silent (each such pin carries a named marker — G1, S2 — and a comment
explaining that a future SPEC change flips it deliberately).

SPEC anchors (section numbers = SPEC.md v0.1):

* §3.1  one JSON object per WebSocket text frame; the bearer credential
  travels in the ``Authorization`` header of the UPGRADE; a refused
  upgrade exchanges no protocol message.
* §3.3  the SERVER advertises the heartbeat parameters in
  ``hello.result``; the client MUST adopt them.
* §5.1/§5.2  ``hello`` — once per connection; version negotiation with
  no silent downgrade (``PROTOCOL_VERSION_UNSUPPORTED`` + close).
* §5.3  capability map — flat booleans; the kit encodes the
  OFFERING-side reading of the "required" column (``notifications:
  false`` is accepted — enforcing the literal table against the machine
  would kill every shipped client connection).
* §5.4  the hello result shape.
* §6.1  the envelope (``v``/``type``/``id``/``session_id``/``ts``/
  ``method``/``params``/``result``/``error``).
* §6.3  the seven methods; ``heartbeat`` is a notification (no ``id``,
  no response).
* §6.4  the MACHINE mints the ``session_id``; the catalog rides the
  ``session.begin`` answer.
* §6.5  typed errors — stable ``code``; ``message`` advisory; ``data``
  optional; ``TOOL_DENIED`` is reserved for v0.2+ and must NEVER appear.
* §6.6  ``tools/call`` params are the MCP object unchanged
  (``{"name", "arguments"}``); the result is returned raw.
* §6.7  the frame-size limit is a CLIENT-side MUST
  (``internal/remote/transport.go:63-90``): the machine's socket accepts
  an inbound frame above the protocol limit (up to a ceiling of
  4x limit + 1 MiB, capped 32 MiB) and answers ``PAYLOAD_TOO_LARGE``;
  the SPEC places no enforcement duty on the CENTER, so the kit never
  reds center-side on frame size (the center's behaviour is RECORDED).
* §6.8  ``session.end`` acks; the socket stays open; no resumption.
* §8.3  one active session per machine; the CENTER rejects with
  ``MACHINE_BUSY`` before any frame reaches the machine.

File-size rationale (~1670 lines):
    The kit's independence contract pins this module to ONE file: it is
    the machine-side peer, the FINDING machinery and the in-kit
    reference/mutated centers, all transcribed from the SPEC with no
    ``daemon.*`` import — a second module would be a second place the
    transcription can drift from the SPEC, with no shared authority to
    arbitrate.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import socket
import ssl
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

import websockets
from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed, InvalidStatus

# ── constants transcribed from SPEC.md v0.1 / v0.2 ───────────────────────

PROTOCOL_VERSION = "0.1"  # §5.2 / §6.1

#: The v0.2 protocol version — the session-termination generation
#: (§6.10). The kit's peer can negotiate it; the kit's reference centre
#: implements it.
PROTOCOL_VERSION_V02 = "0.2"

#: The versions the kit's reference centre ACCEPTS (§5.2 — newest
#: first; the centre never silently downgrades).
SUPPORTED_PROTOCOL_VERSIONS = (PROTOCOL_VERSION_V02, PROTOCOL_VERSION)

#: §5.3 — the capabilities the machine advertises.  ``notifications`` is
#: True here only as the capability-map table entry; the OFFERING-side
#: reading means a peer answering ``notifications: false`` is ACCEPTED.
CLIENT_CAPABILITIES: dict[str, bool] = {
    "session": True,
    "tools": True,
    "notifications": False,  # the shipped client relays none — honest
    "heartbeat": True,
    "screenshot_delta": False,
    "multi_session": False,
    "client_cert": False,
}

#: §5.3 / §6.10 — the v0.2 capability map of a machine that can end a
#: session from its own side.  ``client_terminate`` is the ONLY
#: difference: the centre advertises the capability only when the
#: machine advertised it too.
CLIENT_CAPABILITIES_V02: dict[str, bool] = {
    **CLIENT_CAPABILITIES,
    "client_terminate": True,
}

#: §5.3 — the entries the CENTER demands of the machine (offering-side).
REQUIRED_CAPS = ("session", "tools", "heartbeat")

#: §6.5 — every typed error code of v0.1.
ALL_ERROR_CODES = (
    "PROTOCOL_VERSION_UNSUPPORTED",
    "CAPABILITY_MISSING",
    "AUTH_FAILED",
    "MACHINE_NOT_ENROLLED",
    "MACHINE_BUSY",
    "SESSION_NOT_FOUND",
    "SESSION_EXPIRED",
    "TOOL_NOT_FOUND",
    "TOOL_DENIED",  # reserved v0.2+ — must NEVER appear on the v0.1 wire
    "TOOL_TIMEOUT",
    "TOOL_ERROR",
    "PAYLOAD_TOO_LARGE",
    "RATE_LIMITED",
    "HEARTBEAT_TIMEOUT",
    "INTERNAL",
)

#: §6.7 — the protocol frame limit and the client-side inbound ceiling
#: (``internal/remote/transport.go:63-90``): ``max(4 x limit,
#: limit + 1 MiB)``, capped at 32 MiB.
DEFAULT_MAX_FRAME_BYTES = 8 << 20
HARD_MAX_FRAME_BYTES = 32 << 20


def inbound_ceiling(limit: int = DEFAULT_MAX_FRAME_BYTES) -> int:
    ceiling = max(4 * limit, limit + (1 << 20))
    return min(ceiling, HARD_MAX_FRAME_BYTES)


#: §9.1 / the shipped client's policy lane — a denied tool is OMITTED
#: from the catalog ("omission is the only permitted mutation") and a
#: call to it is refused machine-side with the shipped client's denial
#: code, which is ``TOOL_NOT_FOUND`` (``DenialErrorCode`` in
#: ``internal/remote/client.go:107`` — ``TOOL_DENIED`` is v0.2-reserved).
DENIAL_ERROR_CODE = "TOOL_NOT_FOUND"

#: The center-side close code pin (S2): every server-initiated close of
#: the reference center is WebSocket code 1000.  The SPEC names NO close
#: code anywhere, so this is a PIN of current behaviour, gated behind
#: ``CONFORMANCE_STRICT_CLOSE_CODE`` (default: a recorded warning).
REFERENCE_CLOSE_CODE = 1000


# ── the pointing surface (B8d) — exactly these eight env keys ────────────

#: The FIVE pointing keys — the complete surface for pointing the kit at
#: a center.  No sixth pointing key may accrete.
POINTING_KEYS: tuple[str, ...] = (
    "CONFORMANCE_CENTER_URL",
    "CONFORMANCE_TOKEN",
    "CONFORMANCE_CA_PEM",
    "CONFORMANCE_MACHINE_ID",
    "CONFORMANCE_EXPECTED_AGENT",
)

#: The TWO begin-hook keys (B8a) — how a foreign center is told to begin
#: a session.  Exactly one is used per run.
BEGIN_HOOK_KEYS: tuple[str, ...] = (
    "CONFORMANCE_BEGIN_CMD",
    "CONFORMANCE_BEGIN_URL",
)

#: The ONE strict knob (S2) — closes the close-code pin to a hard assert.
STRICT_KEYS: tuple[str, ...] = ("CONFORMANCE_STRICT_CLOSE_CODE",)

#: The TOTAL enumerated key set — 5 + 2 + 1 = 8.  An undocumented knob
#: cannot appear and a documented one cannot vanish (pinned by
#: ``test_peer_selftest.py``).
CONFORMANCE_ENV_KEYS: tuple[str, ...] = POINTING_KEYS + BEGIN_HOOK_KEYS + STRICT_KEYS

_KIT_NAME = "ensemble-conformance-kit/0.1"


# ── the FINDING machinery ─────────────────────────────────────────────────


class ConformanceFinding(AssertionError):
    """A center-side conformance failure, labelled for the report.

    ``label`` is the stable machine-readable tag (``FINDING <area>/<check>``);
    ``spec_section`` cites the SPEC clause; ``detail`` carries the observed
    deviation; ``excerpt`` quotes the offending transcript frames.
    """

    def __init__(
        self,
        area: str,
        check: str,
        spec_section: str,
        detail: str,
        excerpt: str = "",
    ) -> None:
        self.area = area
        self.check = check
        self.spec_section = spec_section
        self.detail = detail
        self.excerpt = excerpt
        message = (
            f"FINDING {area}/{check} — SPEC {spec_section}: {detail}"
        )
        if excerpt:
            message = f"{message} — transcript: {excerpt}"
        super().__init__(message)


class HookError(RuntimeError):
    """The begin HOOK failed — an operator error, NOT a center defect.

    Deliberately a different class from :class:`ConformanceFinding`: a
    broken hook must never be reported as a center conformance failure.
    """

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(f"HOOK-ERROR (operator, not the center): {detail}")


class SessionBeginGap(RuntimeError):
    """No begin hook is configured and the run needs the session areas.

    The kit FAILS FAST with this message rather than silently skipping
    (the plan's B8a contract); the machine-facing subset is the scoped
    run for a center that cannot provide a hook (see COVERAGE.md).
    """

    def __init__(self) -> None:
        super().__init__(
            "SESSION-BEGIN-GAP: this check needs the center to begin a "
            "session, and no begin hook is configured. Set exactly one of "
            "CONFORMANCE_BEGIN_CMD (a shell command that asks the center "
            "to open a session) or CONFORMANCE_BEGIN_URL (an HTTP "
            "endpoint) — see scripts/conformance/README.md. A center that "
            "cannot provide a hook is tested by the machine-facing subset "
            "only (COVERAGE.md names the gap)."
        )


# ── the args-hash canonicalisations (the §8.4 divergence) ────────────────


def hash_args_python_center(args: Any) -> str:
    """The reference CENTER's documented canonicalisation.

    ``json.dumps(sort_keys=True, separators=(",", ":"), default=str)`` —
    keys sorted, tight separators, non-JSON types folded by ``str()``,
    and Python's default ``ensure_ascii=True`` (non-ASCII escaped).
    """

    def _default(value: Any) -> str:
        return str(value)

    return hashlib.sha256(
        json.dumps(args, sort_keys=True, separators=(",", ":"), default=_default).encode()
    ).hexdigest()


def hash_args_go_client(args: Any) -> str:
    """The shipped CLIENT's canonicalisation, as closely as Python can.

    ``encoding/json.Marshal`` over the decoded value: map keys sorted by
    bytes, ``<``/``>``/``&`` escaped as ``\\u003c``/``\\u003e``/``\\u0026``,
    non-ASCII emitted raw (no ``ensure_ascii`` escape), and floats in
    shortest-roundtrip form (``1.0`` → ``1``).  The remaining deliberate
    approximations (documented in the kit report): Go decodes EVERY number
    as float64, so integers above 2^53 lose precision and ``1`` and
    ``1.0`` collide to one hash — the kit REPRODUCES the collision
    because that is the documented consequence, not a defect.
    """

    def number(value: float) -> str:
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError("non-finite numbers are not JSON in Go")
        if float(value).is_integer() and abs(value) < 1e21:
            return str(int(value))
        return repr(value)

    def string(value: str) -> str:
        encoded = json.dumps(value, ensure_ascii=False)
        return (
            encoded.replace("&", "\\u0026")
            .replace("<", "\\u003c")
            .replace(">", "\\u003e")
        )

    def encode(value: Any) -> str:
        if value is None:
            return "null"
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (int, float)):
            return number(float(value))
        if isinstance(value, str):
            return string(value)
        if isinstance(value, (list, tuple)):
            return "[" + ",".join(encode(v) for v in value) + "]"
        if isinstance(value, dict):
            pairs = sorted(((str(k), v) for k, v in value.items()), key=lambda kv: kv[0].encode())
            return "{" + ",".join(f"{string(k)}:{encode(v)}" for k, v in pairs) + "}"
        return string(str(value))

    return hashlib.sha256(encode(args).encode()).hexdigest()


# ── the transcript ────────────────────────────────────────────────────────


@dataclass
class Frame:
    direction: str  # "machine->center" | "center->machine"
    raw: str
    at_ms: int = field(default_factory=lambda: int(time.time() * 1000))

    @property
    def frame(self) -> dict[str, Any] | None:
        try:
            payload = json.loads(self.raw)
        except Exception:  # noqa: BLE001
            return None
        return payload if isinstance(payload, dict) else None

    def __str__(self) -> str:  # pragma: no cover — report rendering
        return f"[{self.direction}] {self.raw[:400]}"


class Transcript:
    """Every frame exchanged, verbatim, both directions."""

    def __init__(self) -> None:
        self.frames: list[Frame] = []
        self.close_code: int | None = None
        self.close_reason: str = ""
        self.warnings: list[str] = []

    def record(self, direction: str, raw: str) -> Frame:
        frame = Frame(direction=direction, raw=raw)
        self.frames.append(frame)
        return frame

    def inbound(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for f in self.frames:
            if f.direction == "center->machine":
                parsed = f.frame
                if parsed is not None:
                    out.append(parsed)
        return out

    def sent(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for f in self.frames:
            if f.direction == "machine->center":
                parsed = f.frame
                if parsed is not None:
                    out.append(parsed)
        return out

    def warn(self, text: str) -> None:
        self.warnings.append(f"WARNING {time.strftime('%H:%M:%S')} {text}")

    def excerpt(self, last: int = 3) -> str:
        return " | ".join(str(f) for f in self.frames[-last:])

    def tool_denied_seen(self) -> bool:
        return any("TOOL_DENIED" in f.raw for f in self.frames)


# ── the catalog the peer serves (§6.4) ────────────────────────────────────


def kit_catalog(deny: Iterable[str] = ()) -> list[dict[str, Any]]:
    """The machine's advertised catalog, with denied tools OMITTED (§9.1).

    Two tools: one plain, one carrying the exact argument shapes the
    fidelity checks compare and hash.
    """

    denied = set(deny)
    tools = [
        {
            "name": "kit_echo",
            "description": "echoes the arguments back as the MCP result",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "payload": {"type": ["string", "number", "object", "array", "boolean", "null"]},
                },
            },
        },
        {
            "name": "kit_probe",
            "description": "returns a fixed content block",
            "inputSchema": {"type": "object", "properties": {}},
        },
    ]
    return [t for t in tools if t["name"] not in denied]


KIT_CALL_ARGUMENTS: dict[str, Any] = {
    "payload": {"text": "conformance", "n": 1},
    "tag": "ascii-only",
}


def kit_call_result() -> dict[str, Any]:
    """The raw MCP result the machine answers a ``kit_echo`` call with (§6.6)."""

    return {
        "content": [{"type": "text", "text": "conformance-echo"}],
        "isError": False,
    }


# ── the machine-side wire peer ────────────────────────────────────────────


@dataclass
class BeginOutcome:
    """What a begin trigger learned (the hook contract's return)."""

    ok: bool
    session_id: str = ""
    echo: dict[str, Any] = field(default_factory=dict)
    output: str = ""
    error: str = ""


class ConformancePeer:
    """The MACHINE role: dial out, hello, heartbeat, serve the center's requests.

    The peer implements the client-side MUSTs of the SPEC — most notably
    the §6.7 frame-size lane (``transport.go:63-90``): an inbound frame
    above the protocol limit (and under the ceiling) is ANSWERED with a
    ``PAYLOAD_TOO_LARGE`` error response and the connection SURVIVES.
    """

    def __init__(
        self,
        *,
        url: str,
        token: str,
        machine_id: str,
        ca_pem: str | Path | None = None,
        server_pin_sha256: str = "",
        deny_tools: Iterable[str] = (),
        max_frame_bytes: int = DEFAULT_MAX_FRAME_BYTES,
    ) -> None:
        self.url = url
        self.token = token
        self.machine_id = machine_id
        self.ca_pem = str(ca_pem) if ca_pem else ""
        self.server_pin_sha256 = server_pin_sha256
        self.deny_tools = list(deny_tools)
        self.max_frame_bytes = max_frame_bytes
        self.transcript = Transcript()
        self._ws: Any = None
        self._heartbeat_task: asyncio.Task | None = None
        self._hello_params_override: dict[str, Any] | None = None
        self.adopted: dict[str, Any] = {}
        self.session_ids: list[str] = []
        self.sent_catalog: list[dict[str, Any]] = []
        self.calls_received: list[dict[str, Any]] = []
        self.ended_sessions: list[str] = []
        #: The raw ``session.end`` REQUEST envelopes the center sent — the
        #: wire-level evidence that the machine was TOLD (and of the exact
        #: params it was told with: the wire contract does not change).
        self.session_end_frames: list[dict[str, Any]] = []
        self.machine_audit: list[dict[str, Any]] = []
        #: The machine's OWN session lock (SPEC §6.8/§8.3).  The shipped
        #: client refuses a concurrent ``session.begin`` with
        #: ``MACHINE_BUSY`` (``internal/remote/client.go:987-991``) and
        #: releases the lock ONLY on a ``session.end`` frame
        #: (``client.go:1040`` — ``clearSession``).  Modelling the lock
        #: here is what makes the machine's RELEASE observable: a centre
        #: that reaps a session silently leaves the machine locked, and
        #: the next begin is refused.
        self._live_session_id: str = ""
        #: The protocol version this peer NEGOTIATED (§5.2). Every frame
        #: the peer writes carries it, and every frame it reads must
        #: carry it. It is ``"0.1"`` until a hello says otherwise.
        self.version: str = PROTOCOL_VERSION

    # ── TLS / pinning ────────────────────────────────────────────────────

    def _ssl_context(self) -> ssl.SSLContext:
        if self.ca_pem:
            ctx = ssl.create_default_context(cafile=self.ca_pem)
        else:
            ctx = ssl.create_default_context()
        return ctx

    def _check_pin(self, ctx: ssl.SSLContext) -> None:
        """The wrong-server-pin lane (§4.1): the chain must verify AND the pin
        must match — a wrong pin fails the handshake even though the chain
        verifies (``internal/remote/transport.go:283-303``)."""
        if not self.server_pin_sha256:
            return
        parsed = websockets.uri.parse_uri(self.url)
        host = parsed.host
        port = parsed.port or (443 if self.url.startswith("wss") else 80)
        raw = socket.create_connection((host, port), timeout=10)
        try:
            wrapped = ctx.wrap_socket(raw, server_hostname=host)
            try:
                der = wrapped.getpeercert(binary_form=True) or b""
            finally:
                wrapped.close()
        finally:
            try:
                raw.close()
            except OSError:
                pass
        digest = hashlib.sha256(der).hexdigest()
        if digest != self.server_pin_sha256:
            raise ConnectionError(
                f"PIN-MISMATCH: the server certificate's SHA-256 {digest} "
                f"does not match the configured pin {self.server_pin_sha256} "
                "(the chain verified — the PIN refused the handshake)"
            )

    # ── connection lifecycle ─────────────────────────────────────────────

    async def connect(self, *, send_hello: bool = True) -> None:
        """Upgrade with the bearer (§3.1/§4.1) and optionally run the hello."""

        ctx = self._ssl_context()
        # The pin probe is SYNCHRONOUS (a raw TLS handshake) — run it off
        # the event loop: in self-host mode the center lives in THIS
        # process, so blocking the loop would deadlock the handshake.
        await asyncio.to_thread(self._check_pin, ctx)
        headers = [("Authorization", f"Bearer {self.token}"), ("User-Agent", _KIT_NAME)]
        try:
            self._ws = await websockets.connect(
                self.url,
                additional_headers=headers,
                ssl=ctx,
                max_size=inbound_ceiling(self.max_frame_bytes) + (1 << 20),
                open_timeout=15,
                close_timeout=5,
            )
        except InvalidStatus as exc:
            response = getattr(exc, "response", None)
            status = getattr(response, "status_code", None)
            raise UpgradeRefused(status) from exc
        if send_hello:
            await self.hello()

    async def close(self) -> None:
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._heartbeat_task = None
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:  # noqa: BLE001
                pass
            self._ws = None

    async def wait_closed(self, timeout: float) -> bool:
        """Wait for the CENTER to close the socket; record the close code."""

        if self._ws is None:
            return True
        try:
            await asyncio.wait_for(self._ws.wait_closed(), timeout=timeout)
            return True
        except (asyncio.TimeoutError, TimeoutError):
            return False
        finally:
            exc = getattr(self._ws, "close_code", None) if self._ws else None
            if exc is not None and self.transcript.close_code is None:
                self.transcript.close_code = exc
                reason = getattr(self._ws, "close_reason", None) or ""
                self.transcript.close_reason = str(reason)

    # ── frames ───────────────────────────────────────────────────────────

    async def _send_frame(self, payload: dict[str, Any]) -> None:
        raw = json.dumps(payload)
        self.transcript.record("machine->center", raw)
        await self._ws.send(raw)

    async def _recv_frame(self, timeout: float = 10.0) -> dict[str, Any]:
        raw = await asyncio.wait_for(self._ws.recv(), timeout=timeout)
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", "replace")
        self.transcript.record("center->machine", raw)
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError(f"the frame is not a JSON object: {raw[:200]}")
        return payload

    async def _recv_response(self, request_id: str, timeout: float = 15.0) -> dict[str, Any]:
        """Wait for the answer to ONE request, skipping the frames that
        are not its answer.

        The centre's REQUESTS (its ``session.end`` forward, for instance)
        arrive on the same socket — a terminate request can be answered
        only after the centre's own forward was read.  Every skipped
        frame stays in the transcript (it is evidence), and the answer is
        the frame that echoes the request id.
        """
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise asyncio.TimeoutError(
                    f"the centre did not answer {request_id!r} within {timeout}s"
                )
            frame = await self._recv_frame(timeout=remaining)
            if frame.get("id") != request_id:
                continue
            if frame.get("type") in ("response", "error"):
                return frame

    # ── hello (§5.1/§5.2) ────────────────────────────────────────────────

    async def hello(
        self,
        *,
        protocol_version: str | None = None,
        capabilities: dict[str, bool] | None = None,
        machine_id: str | None = None,
        expect_error: bool = False,
    ) -> dict[str, Any]:
        """Send ``hello`` and return the center's answer envelope.

        §5.2 — the version in ``params.protocol_version`` is the version
        the machine REQUESTS; the machine stamps the SAME version on the
        frame itself (``v``), and on every frame it writes afterwards.
        """
        request_id = f"hello-{uuid.uuid4().hex[:8]}"
        self.version = protocol_version if protocol_version is not None else PROTOCOL_VERSION
        params: dict[str, Any] = {
            "protocol_version": self.version,
            "machine_id": self.machine_id if machine_id is None else machine_id,
            "client": {"name": "ensemble-conformance-kit", "version": "0.1"},
            "capabilities": (
                dict(CLIENT_CAPABILITIES) if capabilities is None else capabilities
            ),
        }
        await self._send_frame(
            {
                "v": self.version,
                "type": "request",
                "id": request_id,
                "method": "hello",
                "params": params,
            }
        )
        answer = await self._recv_frame(timeout=15)
        if not expect_error and answer.get("type") == "response":
            self.adopted = dict(answer.get("result") or {})
        return answer

    async def terminate(
        self,
        *,
        session_id: str,
        reason_code: str = "operator_force",
        params: dict[str, Any] | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        """Send a ``session.terminate`` receipt and return the ack.

        §6.10 — the ``session_id`` rides the ENVELOPE; the params carry
        the machine's ``reason_code`` (and optionally a ``detail`` the
        centre never parses).
        """
        rid = request_id or f"term-{uuid.uuid4().hex[:8]}"
        payload_params = (
            dict(params) if params is not None else {"reason_code": reason_code}
        )
        await self._send_frame(
            {
                "v": self.version,
                "type": "request",
                "id": rid,
                "method": "session.terminate",
                "session_id": session_id,
                "params": payload_params,
            }
        )
        return await self._recv_response(rid, timeout=15)

    # ── heartbeat (§3.3/§6.3) ────────────────────────────────────────────

    def start_heartbeats(self, interval_ms: int) -> None:
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop(interval_ms))

    async def _heartbeat_loop(self, interval_ms: int) -> None:
        interval = max(0.05, interval_ms / 1000.0)
        while True:
            await asyncio.sleep(interval)
            try:
                await self._send_frame(
                    {"v": self.version, "type": "notification", "method": "heartbeat"}
                )
            except (ConnectionClosed, Exception):  # noqa: BLE001
                return

    # ── serving the center's requests (§6.3: the center is the REQUESTOR) ─

    async def serve(
        self,
        *,
        on_session_begin: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        on_tools_call: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        on_session_end: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        stop_when: Callable[[], bool] | None = None,
        silence: bool = False,
        budget_s: float = 10.0,
    ) -> None:
        """Answer the center's requests until ``stop_when()`` or the budget.

        ``silence=True`` stops the heartbeat loop and answers nothing —
        the kill-on-silence probe.  Oversized INBOUND requests (above the
        protocol limit, under the ceiling) are answered
        ``PAYLOAD_TOO_LARGE`` and the connection survives — the §6.7
        client-side MUST (``transport.go:63-90``).
        """

        if silence and self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            self._heartbeat_task = None
        deadline = time.monotonic() + budget_s
        while time.monotonic() < deadline:
            if stop_when is not None and stop_when():
                return
            if self._ws is None:
                return
            try:
                raw = await asyncio.wait_for(self._ws.recv(), timeout=0.25)
            except (asyncio.TimeoutError, TimeoutError):
                continue
            except ConnectionClosed:
                self._note_close()
                return
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", "replace")
            self.transcript.record("center->machine", raw)
            if len(raw) > self.max_frame_bytes:
                # §6.7 client-side MUST: answer PAYLOAD_TOO_LARGE, survive.
                try:
                    payload = json.loads(raw)
                    req_id = payload.get("id") if isinstance(payload, dict) else None
                except Exception:  # noqa: BLE001
                    req_id = None
                await self._send_frame(
                    {
                        "v": self.version,
                        "type": "error",
                        **({"id": req_id} if req_id is not None else {}),
                        "error": {
                            "code": "PAYLOAD_TOO_LARGE",
                            "message": "the frame exceeded the protocol limit",
                        },
                    }
                )
                continue
            try:
                envelope = json.loads(raw)
            except Exception:  # noqa: BLE001
                continue
            if silence:
                continue
            await self._answer(envelope, on_session_begin, on_tools_call, on_session_end)

    async def _answer(
        self,
        envelope: dict[str, Any],
        on_session_begin: Callable[[dict[str, Any]], dict[str, Any]] | None,
        on_tools_call: Callable[[dict[str, Any]], dict[str, Any]] | None,
        on_session_end: Callable[[dict[str, Any]], dict[str, Any]] | None,
    ) -> None:
        method = envelope.get("method") or ""
        request_id = envelope.get("id")
        base: dict[str, Any] = {"v": self.version, "type": "response", "id": request_id}
        if envelope.get("session_id"):
            base["session_id"] = envelope["session_id"]
        if method == "session.begin":
            if self._live_session_id:
                # §8.3 — the machine's OWN lock.  The shipped client
                # refuses a concurrent session.begin with MACHINE_BUSY
                # (``internal/remote/client.go:987-991``); modelling it
                # here is what makes the lock's RELEASE observable.
                await self._send_frame(
                    {
                        **base,
                        "type": "error",
                        "error": {
                            "code": "MACHINE_BUSY",
                            "message": "a session is already open on this machine",
                        },
                    }
                )
                return
            session_id = f"sess_{uuid.uuid4().hex[:12]}"
            result = {
                "session_id": session_id,
                "machine_id": (envelope.get("params") or {}).get("machine_id", self.machine_id),
                "tools": kit_catalog(deny=self.deny_tools),
                "expires_at_ms": int(time.time() * 1000) + 3_600_000,
            }
            if on_session_begin is not None:
                override = on_session_begin(envelope)
                if override:
                    result.update(override)
            self.session_ids.append(str(result.get("session_id") or session_id))
            self.sent_catalog = list(result.get("tools") or [])
            self._live_session_id = str(result.get("session_id") or session_id)
            await self._send_frame({**base, "result": result})
        elif method == "tools/call":
            params = dict(envelope.get("params") or {})
            self.calls_received.append(
                {
                    "params": params,
                    "session_id": envelope.get("session_id"),
                    "id": request_id,
                }
            )
            name = str(params.get("name") or "")
            if name in self.deny_tools:
                # The shipped client's policy lane: deny → TOOL_NOT_FOUND
                # (``DenialErrorCode``), audited machine-side as "denied".
                self.machine_audit.append({"tool": name, "outcome": "denied"})
                await self._send_frame(
                    {
                        **base,
                        "type": "error",
                        "error": {
                            "code": DENIAL_ERROR_CODE,
                            "message": f"the tool {name!r} is not available in this session",
                            "data": {"tool": name, "gated": True},
                        },
                    }
                )
                return
            result = kit_call_result() if name == "kit_echo" else None
            if on_tools_call is not None:
                result = on_tools_call(envelope)
            if result is None:
                await self._send_frame(
                    {
                        **base,
                        "type": "error",
                        "error": {"code": "TOOL_NOT_FOUND", "message": f"unknown tool {name!r}"},
                    }
                )
                self.machine_audit.append({"tool": name, "outcome": "denied"})
                return
            self.machine_audit.append({"tool": name, "outcome": "ok"})
            await self._send_frame({**base, "result": result})
        elif method == "session.end":
            sid = str((envelope.get("params") or {}).get("session_id") or "")
            self.ended_sessions.append(sid)
            self.session_end_frames.append(dict(envelope))
            # §6.8 — the machine releases its LOCAL lock on a session.end
            # frame (``internal/remote/client.go:1040`` — ``clearSession``)
            # and the connection returns to ``ready``.  This is the ONLY
            # thing that frees the lock, which is why a centre that reaps
            # a session WITHOUT telling the machine strands it.
            if not self._live_session_id or sid == self._live_session_id:
                self._live_session_id = ""
            result = {"session_id": sid}
            if on_session_end is not None:
                override = on_session_end(envelope)
                if override:
                    result.update(override)
            await self._send_frame({**base, "result": result})
        else:
            # Unknown method: the reference center answers INTERNAL.
            await self._send_frame(
                {
                    **base,
                    "type": "error",
                    "error": {"code": "INTERNAL", "message": f"unknown method {method!r}"},
                }
            )

    def transcript_sent_catalog(self) -> list[dict[str, Any]]:
        """The catalog the machine answered its LAST session.begin with."""

        return list(self.sent_catalog)

    def _note_close(self) -> None:
        if self._ws is not None and self.transcript.close_code is None:
            self.transcript.close_code = self._ws.close_code
            self.transcript.close_reason = str(self._ws.close_reason or "")


class UpgradeRefused(Exception):
    """The WSS upgrade was refused — the HTTP status is all we get (§4.1)."""

    def __init__(self, status: int | None) -> None:
        self.status = status
        super().__init__(f"the upgrade was refused with HTTP {status}")


# ── the begin hook (B8a) ──────────────────────────────────────────────────


def parse_hook_output(output: str) -> tuple[str, dict[str, Any]]:
    """The hook's stdout contract: line 1 = session id; the remaining
    non-empty lines, when they parse as one JSON object, are the ECHO
    document (the agent-visible catalog / the center's audit row —
    consumed by the fidelity checks when a center provides it)."""

    lines = [ln.strip() for ln in output.splitlines() if ln.strip()]
    session_id = lines[0] if lines else ""
    echo: dict[str, Any] = {}
    if len(lines) > 1:
        try:
            parsed = json.loads("\n".join(lines[1:]))
        except Exception:  # noqa: BLE001
            parsed = None
        if isinstance(parsed, dict):
            echo = parsed
    return session_id, echo


def run_begin_hook(cmd: str | None, url: str | None, *, timeout_s: float = 30.0) -> BeginOutcome:
    """Trigger the center to begin a session (the operator contract).

    ``CONFORMANCE_BEGIN_CMD`` — a shell command; exit 0 = triggered; its
    stdout is the hook document (see :func:`parse_hook_output`).
    ``CONFORMANCE_BEGIN_URL`` — an HTTP endpoint; 2xx = triggered; the
    body is the hook document.  NOTE: the URL lane uses a PLAIN HTTP
    client against a REAL endpoint the operator runs — it is not an
    in-process ASGI app.  (Lesson carried from the daemon lane: a
    starlette ``TestClient``'s anyio portal JOINS the default executor,
    so a bounded ``to_thread`` read would measure the thread JOIN, not
    the bound — never reach for TestClient here.)

    Failures raise :class:`HookError` — an operator error, never a
    center finding.
    """

    if cmd:
        try:
            proc = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout_s,
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired as exc:
            raise HookError(f"the begin command timed out after {timeout_s}s") from exc
        if proc.returncode != 0:
            raise HookError(
                f"the begin command exited {proc.returncode}: "
                f"{(proc.stderr or proc.stdout).strip()[:300]}"
            )
        session_id, echo = parse_hook_output(proc.stdout)
        return BeginOutcome(ok=True, session_id=session_id, echo=echo, output=proc.stdout)
    if url:
        try:
            with urlopen(url, timeout=timeout_s) as response:  # noqa: S310 — operator URL
                body = response.read().decode("utf-8", "replace")
                status = getattr(response, "status", 200) or 200
        except HTTPError as exc:
            raise HookError(f"the begin endpoint answered HTTP {exc.code}") from exc
        except URLError as exc:
            raise HookError(f"the begin endpoint was unreachable: {exc.reason}") from exc
        if status < 200 or status >= 300:
            raise HookError(f"the begin endpoint answered HTTP {status}")
        session_id, echo = parse_hook_output(body)
        return BeginOutcome(ok=True, session_id=session_id, echo=echo, output=body)
    raise SessionBeginGap()


# ── the checks (shared by the area tests AND the selftest) ────────────────


def check_hello_result(
    area: str,
    answer: dict[str, Any],
    request_id: str,
    *,
    transcript: Transcript | None = None,
) -> dict[str, Any]:
    """§5.2/§5.4 — the hello answer: echoed id, version, caps, heartbeats."""

    excerpt = transcript.excerpt() if transcript else json.dumps(answer)[:300]
    if answer.get("type") == "error":
        raise ConformanceFinding(
            area,
            "hello-accepted",
            "§5.1",
            f"the hello was refused: {json.dumps(answer.get('error'))}",
            excerpt,
        )
    if answer.get("id") != request_id:
        raise ConformanceFinding(
            area,
            "hello-id-echo",
            "§6.1",
            f"the response id {answer.get('id')!r} does not echo the request id {request_id!r}",
            excerpt,
        )
    result = answer.get("result")
    if not isinstance(result, dict):
        raise ConformanceFinding(
            area, "hello-result-shape", "§5.4", f"the result is not an object: {result!r}", excerpt
        )
    if result.get("protocol_version") != PROTOCOL_VERSION:
        raise ConformanceFinding(
            area,
            "hello-protocol-version",
            "§5.2",
            f"protocol_version is {result.get('protocol_version')!r}, want {PROTOCOL_VERSION!r}",
            excerpt,
        )
    caps = result.get("capabilities")
    if not isinstance(caps, dict) or not caps:
        raise ConformanceFinding(
            area, "hello-capability-map", "§5.3", f"the capability map is missing: {caps!r}", excerpt
        )
    for name in REQUIRED_CAPS:
        if not caps.get(name):
            raise ConformanceFinding(
                area,
                "hello-capability-map",
                "§5.3",
                f"the center advertises {name!r} falsy/absent",
                excerpt,
            )
    for key in ("heartbeat_interval_ms", "heartbeat_timeout_ms", "client_dead_detect_ms"):
        value = result.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            # MUTATED-CENTER CONTROL: a center omitting heartbeat_timeout_ms
            # reds HERE, with this label.
            raise ConformanceFinding(
                area,
                "hello-heartbeat-params",
                "§3.3",
                f"{key} is {value!r} — the center must advertise a positive integer",
                excerpt,
            )
    return result


def check_error_envelope(
    area: str,
    envelope: dict[str, Any],
    expected_code: str,
    *,
    expect_data: bool = False,
    transcript: Transcript | None = None,
) -> dict[str, Any]:
    """§6.5 — a typed error frame: code exact, message advisory, data optional."""

    excerpt = transcript.excerpt() if transcript else json.dumps(envelope)[:300]
    if envelope.get("type") != "error":
        raise ConformanceFinding(
            area,
            "error-envelope-type",
            "§6.1",
            f"expected an error envelope, got type={envelope.get('type')!r}",
            excerpt,
        )
    error = envelope.get("error")
    if not isinstance(error, dict):
        raise ConformanceFinding(
            area, "error-member", "§6.5", f"the error member is not an object: {error!r}", excerpt
        )
    code = error.get("code")
    if code != expected_code:
        raise ConformanceFinding(
            area,
            f"error-code-{expected_code.lower()}",
            "§6.5",
            f"the code is {code!r}, want {expected_code!r}",
            excerpt,
        )
    message = error.get("message")
    if not isinstance(message, str) or not message:
        raise ConformanceFinding(
            area,
            f"error-message-{expected_code.lower()}",
            "§6.5",
            f"the advisory message is missing: {message!r}",
            excerpt,
        )
    # `data` is OPTIONAL (§6.5) — an error with NO data member is legal;
    # when the SPEC row promises data, its presence is checked.
    if expect_data and "data" not in error:
        raise ConformanceFinding(
            area,
            f"error-data-{expected_code.lower()}",
            "§6.5",
            "the SPEC row promises a data member; it is absent",
            excerpt,
        )
    return error


def check_close_code(
    transcript: Transcript,
    *,
    strict: bool,
    area: str = "transport",
) -> None:
    """The close-code pin (S2): the reference center closes with 1000.

    The SPEC names NO close code anywhere, so the DEFAULT is a recorded
    warning; ``CONFORMANCE_STRICT_CLOSE_CODE=1`` turns the pin hard.
    """

    code = transcript.close_code
    if code == REFERENCE_CLOSE_CODE or code is None:
        return
    if strict:
        raise ConformanceFinding(
            area,
            "close-code-1000",
            "§6.8 (no close code named)",
            f"the center closed with {code}, the reference pins {REFERENCE_CLOSE_CODE} "
            "(strict mode)",
            transcript.excerpt(),
        )
    transcript.warn(
        f"close-code: the center closed with {code}, the reference center pins "
        f"{REFERENCE_CLOSE_CODE} — the SPEC names no close code (S2); reported, not failed"
    )


def check_no_tool_denied(transcript: Transcript) -> None:
    """§6.5 — ``TOOL_DENIED`` is reserved for v0.2+ and must NEVER appear."""

    if transcript.tool_denied_seen():
        raise ConformanceFinding(
            "errors",
            "tool-denied-never-emitted",
            "§6.5",
            "TOOL_DENIED appeared on the v0.1 wire — it is reserved for v0.2+",
            transcript.excerpt(),
        )


def check_upgrade_refused(status: int | None, *, area: str = "auth") -> None:
    """§4.1/§6.5 — a refused upgrade is HTTP **401**, never 403.

    Only the 401 is fatal to the shipped client; a 403 would loop it
    forever.  A mutated center answering 403 reds HERE.
    """

    if status != 401:
        raise ConformanceFinding(
            area,
            "upgrade-401-never-403",
            "§4.1/§6.5",
            f"the refused upgrade answered HTTP {status}, want 401 "
            "(AUTH_FAILED is the one code signalled outside the envelope)",
        )


def check_g1_heartbeat_timeout_frame(envelope: dict[str, Any], transcript: Transcript) -> None:
    """G1 PIN (current behaviour, with marker): the reference center's
    ``HEARTBEAT_TIMEOUT`` error frame carries NO ``id``.

    SPEC §6.1 says the ``id`` is required on request/response/error
    envelopes; the reference center deliberately omits it (there is no
    request to correlate) and the shipped client DROPS uncorrelated
    errors.  This pin documents CURRENT behaviour — a future SPEC change
    flips it deliberately (see the mission findings, G1).
    """

    check_error_envelope(
        "transport", envelope, "HEARTBEAT_TIMEOUT", transcript=transcript
    )
    if envelope.get("id") is not None:
        raise ConformanceFinding(
            "transport",
            "g1-heartbeat-timeout-no-id",
            "§6.1 (G1 tension)",
            "the HEARTBEAT_TIMEOUT frame carries an id — the reference center "
            "deliberately sends none (no request to correlate); this pin "
            "tracks the reference behaviour and flips only with a SPEC change. "
            "G1 OPEN-SPEC: the reference centre omits id today; if your centre "
            "includes id per SPEC §6.1 this FINDING is the known SPEC tension — "
            "flip this pin when the SPEC decision lands.",
            transcript.excerpt(),
        )


# ── the in-kit centers (the reference behaviour + deliberate mutations) ──


@dataclass
class CenterMutations:
    """The deliberate defects a mutated center exhibits (B8e) — each one
    must make the kit RED with the CORRECT FINDING label."""

    hello_version_reply: str = ""  # answer protocol_version "0.2" → handshake red
    omit_heartbeat_timeout_ms: bool = False  # → hello-heartbeat-params red
    close_code: int = REFERENCE_CLOSE_CODE  # a non-1000 close exercises S2
    oversized_request: bool = False  # send a >limit request → the peer's §6.7 MUST
    refuse_token: bool = False  # 403 instead of 401 (auth red)


class InKitCenter:
    """A minimal reference center transcribed from SPEC.md v0.1.

    Serves WSS on an ephemeral loopback port with a per-run self-signed
    certificate (``IP:127.0.0.1`` SAN — a DNS SAN would not match a
    dial to 127.0.0.1).  The agent side is an in-process driver the
    self-host begin hook calls: ``agent_begin`` / ``agent_call`` /
    ``agent_end`` / ``agent_disconnect``.
    """

    #: Compressed profile (satisfies the client's own validation:
    #: timeout >= 3 x interval — ``internal/remote/client.go:651-665``).
    HEARTBEAT_INTERVAL_MS = 500
    HEARTBEAT_TIMEOUT_MS = 1500
    CLIENT_DEAD_DETECT_MS = 1500
    COMMAND_TIMEOUT_MS = 4000
    SESSION_IDLE_TIMEOUT_MS = 2000
    #: The cadence of the housekeeping sweep (the heartbeat check + the
    #: expiry reap).  The sweep is the centre's SOLE owner of the expiry
    #: reap — the call path refuses a late call but never releases.
    SESSION_SWEEP_INTERVAL_S = 0.1

    def __init__(
        self,
        *,
        mutations: CenterMutations | None = None,
        token: str = "conf-token",
        machine_id: str = "conf-machine-1",
        agent_id: str = "conf-agent",
        tmp_dir: Path | None = None,
    ) -> None:
        self.mutations = mutations or CenterMutations()
        self.token = token
        self.machine_id = machine_id
        self.agent_id = agent_id
        # The temp dir holds the SELF-SIGNED CERTIFICATE **AND ITS
        # PRIVATE KEY** — it must not outlive the center (QA C13: the
        # old bare ``mkdtemp`` never got removed). When the caller owns
        # the dir (pytest ``tmp_path``) we leave cleanup to them; when
        # WE created it, a ``TemporaryDirectory`` reclaims it in
        # ``stop()``.
        self._tmp_owner: "tempfile.TemporaryDirectory | None" = None
        if tmp_dir is not None:
            self.tmp = Path(tmp_dir)
        else:
            self._tmp_owner = tempfile.TemporaryDirectory(prefix="conformance-center-")
            self.tmp = Path(self._tmp_owner.name)
        self.cert_path, self.key_path = generate_self_signed_cert(self.tmp)
        self.port = 0
        self.url = ""
        self._server: Any = None
        self._task: asyncio.Task | None = None
        self._conns: dict[str, ServerConnection] = {}
        self._hello_done: dict[str, bool] = {}
        self._last_heartbeat: dict[str, float] = {}
        self._machine_session: dict[str, str] = {}
        #: The NEGOTIATED protocol version per connection (§5.2) — every
        #: frame the centre writes on a connection carries it.
        self._conn_version: dict[str, str] = {}
        #: The machine id per connection, and the connection that holds
        #: each machine id (§5.6 — the duplicate-id refusal).
        self._conn_machine: dict[str, str] = {}
        self._machine_conn: dict[str, str] = {}
        #: The session the SWEEP reaped per connection — the centre's own
        #: memory of an expiry, so a call that arrives after the reap is
        #: still answered SESSION_EXPIRED (the §6.5 code for "the session
        #: passed its TTL or idle timeout") instead of a bare
        #: SESSION_NOT_FOUND.
        self._expired_sessions: dict[str, str] = {}
        self.agent_catalog: list[dict[str, Any]] | None = None
        self.agent_audit: list[dict[str, Any]] = []
        self.agent_results: list[Any] = []
        self.agent_errors: list[str] = []
        self._awaitable_events: list[tuple[str, asyncio.Future]] = []
        self.received: list[dict[str, Any]] = []

    # ── lifecycle ────────────────────────────────────────────────────────

    async def start(self) -> "InKitCenter":
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(str(self.cert_path), str(self.key_path))

        def process_request(connection: Any, request: Any) -> Any:
            """The bearer gate (§3.1/§4.1): refused BEFORE the upgrade
            completes — the refusal is an HTTP status (401; the mutated
            center answers 403), and NO protocol frame is exchanged."""

            auth = request.headers.get("Authorization", "")
            if auth != f"Bearer {self.token}":
                status = 403 if self.mutations.refuse_token else 401
                return connection.respond(status, "unauthorized\n")
            return None

        self._server = await serve(
            self._handler,
            "127.0.0.1",
            0,
            ssl=ctx,
            max_size=inbound_ceiling() + (1 << 20),
            process_request=process_request,
        )
        self.port = self._server.sockets[0].getsockname()[1]
        self.url = f"wss://127.0.0.1:{self.port}/api/mcps-gateway/ws"
        self._task = asyncio.create_task(self._monitor())
        return self

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None
        if self._server is not None:
            self._server.close()
            with_cancel = getattr(self._server, "wait_closed", None)
            if with_cancel is not None:
                try:
                    await asyncio.wait_for(with_cancel(), timeout=5)
                except Exception:  # noqa: BLE001
                    pass
            self._server = None
        if self._tmp_owner is not None:
            # The cert + PRIVATE KEY live under ``self.tmp`` — reclaim
            # the self-created dir (QA C13). Caller-owned dirs (pytest
            # ``tmp_path``) stay the caller's problem. Exception-safe
            # against an ALREADY-REMOVED dir (a crashed stop, a concurrent
            # cleanup): a vanished tmp must not mask whatever exception
            # ``stop()`` is unwinding through.
            try:
                self._tmp_owner.cleanup()
            except FileNotFoundError:
                pass
            self._tmp_owner = None

    # ── the wire side (what the machine sees) ───────────────────────────

    async def _handler(self, ws: ServerConnection) -> None:
        conn_id = uuid.uuid4().hex[:8]
        self._conns[conn_id] = ws
        self._last_heartbeat[conn_id] = time.monotonic()
        try:
            async for raw in ws:
                if isinstance(raw, bytes):
                    continue
                await self._on_frame(conn_id, ws, raw)
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            self._conns.pop(conn_id, None)
            self._hello_done.pop(conn_id, None)
            self._last_heartbeat.pop(conn_id, None)
            self._machine_session.pop(conn_id, None)
            self._conn_version.pop(conn_id, None)
            machine_id = self._conn_machine.pop(conn_id, "")
            if machine_id and self._machine_conn.get(machine_id) == conn_id:
                self._machine_conn.pop(machine_id, None)

    async def _send(self, ws: ServerConnection, payload: dict[str, Any]) -> None:
        await ws.send(json.dumps(payload))

    async def _on_frame(self, conn_id: str, ws: ServerConnection, raw: str) -> None:
        # §6.7 mirrored: a frame above the protocol limit is ANSWERED with
        # PAYLOAD_TOO_LARGE and the connection survives (the SPEC puts the
        # MUST on the client; a fully-conformant center mirrors it).
        version = self._conn_version.get(conn_id, PROTOCOL_VERSION)
        if len(raw) > DEFAULT_MAX_FRAME_BYTES:
            try:
                parsed = json.loads(raw)
                req_id = parsed.get("id") if isinstance(parsed, dict) else None
            except Exception:  # noqa: BLE001
                req_id = None
            error: dict[str, Any] = {
                "code": "PAYLOAD_TOO_LARGE",
                "message": "the frame exceeded the protocol limit",
            }
            envelope: dict[str, Any] = {"v": version, "type": "error", "error": error}
            if req_id is not None:
                envelope["id"] = req_id
            await self._send(ws, envelope)
            return
        try:
            envelope = json.loads(raw)
        except Exception:  # noqa: BLE001
            await self._send(
                ws,
                {
                    "v": version,
                    "type": "error",
                    "error": {"code": "INTERNAL", "message": "unparseable frame"},
                },
            )
            return
        if not isinstance(envelope, dict):
            return
        self.received.append(envelope)
        kind = envelope.get("type")
        method = envelope.get("method")
        if kind == "notification" and method == "heartbeat":
            self._last_heartbeat[conn_id] = time.monotonic()
            return
        if kind == "request" and method == "hello":
            await self._on_hello(conn_id, ws, envelope)
            return
        if kind == "request" and method == "session.terminate":
            await self._on_session_terminate(conn_id, ws, envelope)
            return
        if kind in ("response", "error"):
            # A response to one of OUR requests: route by id.
            await self._resolve_pending(str(envelope.get("id")), envelope)
            return
        # Any other request is outside the machine→center vocabulary.
        await self._send(
            ws,
            {
                "v": version,
                "type": "error",
                "id": envelope.get("id"),
                "error": {"code": "INTERNAL", "message": f"unknown method {method!r}"},
            },
        )

    async def _on_session_terminate(
        self, conn_id: str, ws: ServerConnection, envelope: dict[str, Any]
    ) -> None:
        """The client-originated termination receipt (§6.10).

        The centre NEVER refuses: the machine owns the machine. The
        session is closed and the slot released, and the ack names the
        machine's own ``reason_code`` VERBATIM (no validation gate).

        The IDEMPOTENT goal state is NOT an error: a repeat (or a race
        with the sweep) answers ``ended: false`` with a MANDATORY
        ``reason`` — the machine releases its local lock on a known
        reason and keeps it on an unknown or missing one.
        """
        request_id = envelope.get("id")
        version = self._conn_version.get(conn_id, PROTOCOL_VERSION)
        session_id = str(envelope.get("session_id") or "")
        params = envelope.get("params") or {}
        raw_reason = params.get("reason_code")
        reason = raw_reason if isinstance(raw_reason, str) else ("" if raw_reason is None else str(raw_reason))
        live = self._machine_session.get(conn_id)
        if live is not None and live == session_id:
            self._machine_session.pop(conn_id, None)
            self._expired_sessions.pop(conn_id, None)
            ack: dict[str, Any] = {
                "session_id": session_id,
                "ended": True,
                "reason": reason,
                "machine_notified": True,
            }
        else:
            # The IDEMPOTENT goal state: the session is not live.  The
            # REASON is mandatory — the machine releases its local lock on
            # a known reason and keeps it on an unknown or missing one.
            # A session the SWEEP reaped says so (``reaped_by_center``);
            # any other not-live session says ``already_ended``.
            reaped = self._expired_sessions.get(conn_id)
            ack = {
                "session_id": session_id,
                "ended": False,
                "reason": "reaped_by_center" if reaped == session_id else "already_ended",
                "machine_notified": True,
            }
        await self._send(
            ws, {"v": version, "type": "response", "id": request_id, "result": ack}
        )

    async def _on_hello(self, conn_id: str, ws: ServerConnection, envelope: dict[str, Any]) -> None:
        request_id = envelope.get("id")
        params = envelope.get("params") or {}
        version = str(params.get("protocol_version") or "")

        async def refuse(code: str, message: str, data: dict[str, Any] | None) -> None:
            error: dict[str, Any] = {"code": code, "message": message}
            if data is not None:
                error["data"] = data
            await self._send(
                ws,
                {
                    "v": self._conn_version.get(conn_id, PROTOCOL_VERSION),
                    "type": "error",
                    "id": request_id,
                    "error": error,
                },
            )
            await ws.close(code=self.mutations.close_code, reason="hello refused")

        if self._hello_done.get(conn_id):
            await refuse("INTERNAL", "the hello exchange happens once per connection", None)
            return
        if version not in SUPPORTED_PROTOCOL_VERSIONS:
            await refuse(
                "PROTOCOL_VERSION_UNSUPPORTED",
                f"protocol version {version or '(none)'} is not supported",
                {"requested": version, "supported": list(SUPPORTED_PROTOCOL_VERSIONS)},
            )
            return
        # The version is known to be supported: the connection RUNS it, and
        # every frame the centre writes on it carries it (§5.2).
        self._conn_version[conn_id] = version
        machine_id = str(params.get("machine_id") or "")
        if not machine_id:
            await refuse(
                "MACHINE_NOT_ENROLLED", "no machine_id", {"machine_id": machine_id}
            )
            return
        caps = params.get("capabilities") or {}
        missing = [c for c in REQUIRED_CAPS if not caps.get(c)]
        if missing:
            await refuse(
                "CAPABILITY_MISSING",
                f"the machine does not support the capabilities {missing}",
                {"missing": missing, "required": list(REQUIRED_CAPS)},
            )
            return
        # §5.6 — a DUPLICATE machine id is refused; the incumbent keeps
        # its socket AND its session.
        incumbent = self._machine_conn.get(machine_id)
        if incumbent is not None and incumbent != conn_id:
            await refuse(
                "MACHINE_ID_CONFLICT",
                f"the machine id {machine_id!r} is already held by a live connection",
                {
                    "machine_id": machine_id,
                    "incumbent_machine_id": machine_id,
                    "incumbent_connection": "open",
                },
            )
            return
        self._hello_done[conn_id] = True
        # §5.2 — the connection RUNS the version the machine REQUESTED.
        self._conn_machine[conn_id] = machine_id
        self._machine_conn[machine_id] = conn_id
        client_terminate = (
            version == PROTOCOL_VERSION_V02 and bool(caps.get("client_terminate"))
        )
        capabilities: dict[str, bool] = {
            "session": True,
            "tools": True,
            "notifications": False,
            "heartbeat": True,
            "screenshot_delta": False,
            "multi_session": False,
            "client_cert": False,
        }
        if version == PROTOCOL_VERSION_V02:
            capabilities["client_terminate"] = client_terminate
        result: dict[str, Any] = {
            "protocol_version": self.mutations.hello_version_reply or version,
            "server": {"name": "in-kit-conformance-center", "version": "0.1"},
            "capabilities": capabilities,
            "heartbeat_interval_ms": self.HEARTBEAT_INTERVAL_MS,
            "client_dead_detect_ms": self.CLIENT_DEAD_DETECT_MS,
        }
        if not self.mutations.omit_heartbeat_timeout_ms:
            result["heartbeat_timeout_ms"] = self.HEARTBEAT_TIMEOUT_MS
        await self._send(
            ws, {"v": version, "type": "response", "id": request_id, "result": result}
        )
        if self.mutations.oversized_request:
            # A deliberate >limit inbound request — the PEER's §6.7 MUST
            # must answer PAYLOAD_TOO_LARGE and survive.
            await ws.send(json.dumps({"v": PROTOCOL_VERSION, "type": "request", "id": "big-1", "method": "tools/call", "session_id": "sess_oversize", "params": {"name": "kit_echo", "arguments": {"pad": "x" * (DEFAULT_MAX_FRAME_BYTES + 65536)}}}))

    # ── pending request routing (the center is the REQUESTOR) ───────────

    async def _request(
        self,
        ws: ServerConnection,
        method: str,
        params: dict[str, Any],
        *,
        session_id: str = "",
        version: str = PROTOCOL_VERSION,
    ) -> dict[str, Any]:
        request_id = f"c-{uuid.uuid4().hex[:8]}"
        envelope: dict[str, Any] = {
            "v": version,
            "type": "request",
            "id": request_id,
            "method": method,
            "params": params,
        }
        if session_id:
            envelope["session_id"] = session_id
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._awaitable_events.append((request_id, fut))
        await self._send(ws, envelope)
        try:
            answer = await asyncio.wait_for(fut, timeout=self.COMMAND_TIMEOUT_MS / 1000.0)
        finally:
            self._awaitable_events = [(k, f) for k, f in self._awaitable_events if k != request_id]
        if answer.get("type") == "error":
            raise CenterRefused(str((answer.get("error") or {}).get("code") or "ERROR"))
        return dict(answer.get("result") or {})

    async def _resolve_pending(self, request_id: str, envelope: dict[str, Any]) -> None:
        for key, fut in list(self._awaitable_events):
            if key == request_id and not fut.done():
                fut.set_result(envelope)

    # ── the heartbeat monitor (§3.3 kill-on-silence) ─────────────────────

    async def _monitor(self) -> None:
        while True:
            await asyncio.sleep(self.SESSION_SWEEP_INTERVAL_S)
            now = time.monotonic()
            for conn_id, last in list(self._last_heartbeat.items()):
                ws = self._conns.get(conn_id)
                if ws is None:
                    continue
                hello_done = self._hello_done.get(conn_id)
                silence = (now - last) * 1000.0
                if hello_done and silence > self.HEARTBEAT_TIMEOUT_MS:
                    # G1 PIN: the HEARTBEAT_TIMEOUT error carries NO id —
                    # there is no request to correlate (current reference
                    # behaviour; the shipped client drops uncorrelated
                    # errors).  A future SPEC change flips this.
                    await self._send(
                        ws,
                        {
                            "v": self._conn_version.get(conn_id, PROTOCOL_VERSION),
                            "type": "error",
                            "error": {
                                "code": "HEARTBEAT_TIMEOUT",
                                "message": "the machine stopped heartbeating; the session is dead",
                                "data": {"machine_id": self.machine_id},
                            },
                        },
                    )
                    await ws.close(code=self.mutations.close_code, reason="heartbeat timeout")
                    self._last_heartbeat.pop(conn_id, None)
            await self._expire_due_sessions()

    # ── the expiry sweep (the centre's SOLE owner of the reap) ───────────

    async def _expire_due_sessions(self) -> None:
        """Reap the sessions that passed the centre's idle window.

        The expiry window is the SERVER's policy (SPEC §3.3: "A session
        with no traffic for this long is ended by the server"), and the
        SWEEP is the centre's only owner of the reap.

        TWO PASSES, in this order:

        * **PASS 1** (no awaits) releases the centre-side slot for the
          WHOLE expired batch;
        * **PASS 2** (serial, awaiting, bounded) forwards ``session.end``
          to each machine — the machine releases its own local lock and
          the connection returns to ``ready`` (SPEC §6.8).

        The centre-side release of the batch therefore completes BEFORE
        any machine forward starts; the machine-side forward is the
        bounded part.  The forwarded frame carries EXACTLY the
        ``{"session_id": ...}`` params — the wire contract does not change
        (no reason/cause member rides along).
        """

        now = time.monotonic()
        idle_limit = self.SESSION_IDLE_TIMEOUT_MS / 1000.0
        last_activity = getattr(self, "_session_last_activity", now)
        due: list[tuple[str, str]] = [
            (conn_key, session_id)
            for conn_key, session_id in list(self._machine_session.items())
            if (now - last_activity) > idle_limit
        ]
        if not due:
            return
        # PASS 1 — no awaits: the centre-side release of the WHOLE batch.
        for conn_key, session_id in due:
            self._machine_session.pop(conn_key, None)
            self._expired_sessions[conn_key] = session_id
        # PASS 2 — serial, awaiting, bounded: tell the machine.
        for conn_key, session_id in due:
            ws = self._conns.get(conn_key)
            if ws is None:
                self.agent_audit.append(
                    {
                        "tool": "session.end",
                        "outcome": "error",
                        "code": "machine_unreachable",
                        "session_id": session_id,
                    }
                )
                continue
            conn_version = self._conn_version.get(conn_key, PROTOCOL_VERSION)
            end_params: dict[str, Any] = {"session_id": session_id}
            if conn_version == PROTOCOL_VERSION_V02:
                # §6.9.2 group (ii) — the v0.2 forward names the centre
                # cause.  A v0.1 connection keeps the historical params.
                end_params["reason"] = "reaped_by_center"
            try:
                await self._request(
                    ws,
                    "session.end",
                    end_params,
                    session_id=session_id,
                    version=conn_version,
                )
            except CenterRefused as exc:
                self.agent_audit.append(
                    {
                        "tool": "session.end",
                        "outcome": "error",
                        "code": str(exc),
                        "session_id": session_id,
                    }
                )
            except Exception:  # noqa: BLE001 — the sweep never dies
                self.agent_audit.append(
                    {
                        "tool": "session.end",
                        "outcome": "error",
                        "code": "machine_unreachable",
                        "session_id": session_id,
                    }
                )
            else:
                self.agent_audit.append(
                    {
                        "tool": "session.end",
                        "outcome": "ok",
                        "session_id": session_id,
                    }
                )

    # ── the agent-side driver (what the self-host begin hook calls) ─────

    def _live_ws(self) -> ServerConnection:
        for conn_id, done in self._hello_done.items():
            if done:
                ws = self._conns.get(conn_id)
                if ws is not None:
                    return ws
        raise CenterRefused("MACHINE_NOT_ENROLLED")

    async def agent_begin(self, agent_id: str | None = None, reason: str = "conformance") -> dict[str, Any]:
        ws = self._live_ws()
        key = _conn_key(self._conns, ws)
        if self._machine_session.get(key):
            raise CenterRefused("MACHINE_BUSY")
        result = await self._request(
            ws,
            "session.begin",
            {
                "machine_id": self.machine_id,
                "context": {"agent_id": agent_id or self.agent_id, "reason": reason},
            },
            version=self._conn_version.get(key, PROTOCOL_VERSION),
        )
        session_id = str(result.get("session_id") or "")
        self._machine_session[key] = session_id
        # A fresh session supersedes the expiry record of this connection.
        self._expired_sessions.pop(key, None)
        self.agent_catalog = list(result.get("tools") or [])
        self._session_last_activity = time.monotonic()
        return {
            "session_id": session_id,
            "machine_id": str(result.get("machine_id") or self.machine_id),
            "tools": self.agent_catalog,
        }

    async def agent_call(self, tool: str, arguments: dict[str, Any]) -> Any:
        ws = self._live_ws()
        key = _conn_key(self._conns, ws)
        session_id = self._machine_session.get(key, "")
        if not session_id:
            # The session is gone.  If the SWEEP reaped it, the answer is
            # SESSION_EXPIRED (the §6.5 code for a session that passed
            # its TTL / idle window); an explicitly ended session leaves
            # SESSION_NOT_FOUND.
            expired = key in self._expired_sessions
            code = "SESSION_EXPIRED" if expired else "SESSION_NOT_FOUND"
            self.agent_audit.append({"tool": tool, "outcome": "error", "code": code})
            raise CenterRefused(code)
        # §3.3 — the center owns the TTL/idle window and refuses past it
        # (the machine's own TTL is advisory).  The in-kit center's window
        # is compressed so a run can observe the refusal.  The refusal
        # does NOT release the slot: the SWEEP owns the reap (it is the
        # one that tells the machine and frees the machine-side lock).
        idle_s = time.monotonic() - getattr(self, "_session_last_activity", time.monotonic())
        if idle_s > (self.SESSION_IDLE_TIMEOUT_MS / 1000.0):
            self.agent_audit.append({"tool": tool, "outcome": "error", "code": "SESSION_EXPIRED"})
            raise CenterRefused("SESSION_EXPIRED")
        try:
            result = await self._request(
                ws,
                "tools/call",
                {"name": tool, "arguments": arguments},
                session_id=session_id,
                version=self._conn_version.get(key, PROTOCOL_VERSION),
            )
        except CenterRefused as exc:
            self.agent_audit.append({"tool": tool, "outcome": "error", "code": str(exc)})
            raise
        self._session_last_activity = time.monotonic()
        self.agent_audit.append(
            {
                "tool": tool,
                "outcome": "ok",
                "args_hash": hash_args_python_center(arguments),
                "session_id": session_id,
            }
        )
        self.agent_results.append(result)
        return result

    async def agent_end(self) -> dict[str, Any]:
        ws = self._live_ws()
        key = _conn_key(self._conns, ws)
        session_id = self._machine_session.pop(key, "")
        if not session_id:
            raise CenterRefused("SESSION_NOT_FOUND")
        end_params: dict[str, Any] = {"session_id": session_id}
        if self._conn_version.get(key, PROTOCOL_VERSION) == PROTOCOL_VERSION_V02:
            # §6.9.2 group (ii) — the explicit end names the centre cause.
            end_params["reason"] = "superseded"
        return await self._request(
            ws,
            "session.end",
            end_params,
            session_id=session_id,
            version=self._conn_version.get(key, PROTOCOL_VERSION),
        )

    async def agent_echo_document(self) -> dict[str, Any]:
        """The hook ECHO: the agent-visible catalog + the center's audit rows."""

        return {
            "agent_catalog": self.agent_catalog or [],
            "agent_audit": self.agent_audit,
        }


class CenterRefused(Exception):
    """The agent-side driver was refused (the center's own typed refusal)."""


def _conn_key(conns: dict[str, ServerConnection], ws: ServerConnection) -> str:
    for key, value in conns.items():
        if value is ws:
            return key
    return ""


# ── the per-run self-signed certificate (IP SAN — loopback shape) ────────


def generate_self_signed_cert(tmp_dir: Path, *, common_name: str = "127.0.0.1") -> tuple[Path, Path]:
    """A per-run self-signed cert with ``IP:127.0.0.1`` in the SAN.

    A DNS SAN would NOT match a dial to ``127.0.0.1`` — the loopback
    shape is an IP SAN (the same recipe the interop harness uses).
    """

    tmp_dir.mkdir(parents=True, exist_ok=True)
    cert_path = tmp_dir / "center-cert.pem"
    key_path = tmp_dir / "center-key.pem"
    subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
            "-days", "1", "-keyout", str(key_path), "-out", str(cert_path),
            "-subj", f"/CN={common_name}",
            "-addext", f"subjectAltName=IP:{common_name}",
        ],
        check=True,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
    )
    return cert_path, key_path


# ── pointing resolution ───────────────────────────────────────────────────


@dataclass
class Target:
    """Where the kit points and how it begins sessions there."""

    mode: str  # "pointed" | "self-host"
    url: str = ""
    token: str = ""
    ca_pem: str = ""
    machine_id: str = "conf-machine-1"
    expected_agent: str = ""
    begin_cmd: str = ""
    begin_url: str = ""
    strict_close_code: bool = False
    center: InKitCenter | None = None  # self-host mode only

    @property
    def has_begin_hook(self) -> bool:
        return bool(self.begin_cmd or self.begin_url or self.mode == "self-host")


def resolve_target(env: dict[str, str] | None = None) -> Target:
    """Resolve the pointing from the enumerated env keys (5 + 2 + 1).

    No ``CONFORMANCE_CENTER_URL`` ⇒ SELF-HOST mode: the kit boots its
    own in-kit reference center and tests ITSELF (self-consistency —
    documented in COVERAGE.md; it proves the kit, not your center).
    """

    e = dict(os.environ if env is None else env)
    strict = e.get("CONFORMANCE_STRICT_CLOSE_CODE", "") in ("1", "true", "yes", "on")
    url = e.get("CONFORMANCE_CENTER_URL", "").strip()
    if url:
        return Target(
            mode="pointed",
            url=url,
            token=e.get("CONFORMANCE_TOKEN", ""),
            ca_pem=e.get("CONFORMANCE_CA_PEM", ""),
            machine_id=e.get("CONFORMANCE_MACHINE_ID", "") or "conf-machine-1",
            expected_agent=e.get("CONFORMANCE_EXPECTED_AGENT", ""),
            begin_cmd=e.get("CONFORMANCE_BEGIN_CMD", ""),
            begin_url=e.get("CONFORMANCE_BEGIN_URL", ""),
            strict_close_code=strict,
        )
    return Target(mode="self-host", strict_close_code=strict)


async def begin_on_target(target: Target) -> BeginOutcome:
    """Trigger a session begin on the target (hook contract / in-process).

    The pointed-mode hook runs in a THREAD (``asyncio.to_thread``): the
    hook is a blocking subprocess / HTTP call, and the peer's serving
    loop must stay free to answer the ``session.begin`` the center sends
    the moment the hook fires — a synchronous hook would deadlock the
    first session (the loop would be blocked when the frame arrives).
    """

    if target.mode == "self-host":
        center = target.center
        if center is None:
            raise HookError("self-host mode: the in-kit center is not booted")
        try:
            begun = await center.agent_begin()
        except CenterRefused as exc:
            raise HookError(f"the in-kit center refused the begin: {exc}") from exc
        echo = await center.agent_echo_document()
        return BeginOutcome(
            ok=True,
            session_id=str(begun.get("session_id") or ""),
            echo=echo,
            output=json.dumps(begun),
        )
    return await asyncio.to_thread(
        run_begin_hook, target.begin_cmd or None, target.begin_url or None
    )


async def wait_until(predicate: Callable[[], bool], *, timeout: float = 10.0, poll: float = 0.05) -> bool:
    """Poll a predicate until true or the timeout (loop-yield each poll)."""

    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(poll)
    return predicate()


async def open_session(peer: ConformancePeer, target: Target) -> BeginOutcome:
    """Trigger a begin AND wait for the center's ``session.begin`` to land.

    The caller must already be running ``peer.serve(...)``.
    """

    outcome = await begin_on_target(target)
    if target.mode == "self-host" or outcome.session_id:
        await wait_until(lambda: bool(peer.session_ids), timeout=10)
    if not peer.session_ids:
        raise HookError(
            "the begin hook reported success but no session.begin frame "
            "reached the machine — check the hook actually asks THIS center "
            "to open a session (the operator contract, README 'Begin hook')"
        )
    return outcome


__all__ = [
    "ALL_ERROR_CODES",
    "BEGIN_HOOK_KEYS",
    "BeginOutcome",
    "CenterMutations",
    "CenterRefused",
    "CLIENT_CAPABILITIES",
    "CLIENT_CAPABILITIES_V02",
    "CONFORMANCE_ENV_KEYS",
    "ConformanceFinding",
    "ConformancePeer",
    "DEFAULT_MAX_FRAME_BYTES",
    "DENIAL_ERROR_CODE",
    "HARD_MAX_FRAME_BYTES",
    "HookError",
    "InKitCenter",
    "KIT_CALL_ARGUMENTS",
    "POINTING_KEYS",
    "PROTOCOL_VERSION",
    "PROTOCOL_VERSION_V02",
    "REFERENCE_CLOSE_CODE",
    "REQUIRED_CAPS",
    "SessionBeginGap",
    "STRICT_KEYS",
    "SUPPORTED_PROTOCOL_VERSIONS",
    "Target",
    "Transcript",
    "UpgradeRefused",
    "begin_on_target",
    "check_close_code",
    "check_error_envelope",
    "check_g1_heartbeat_timeout_frame",
    "check_hello_result",
    "check_no_tool_denied",
    "check_upgrade_refused",
    "generate_self_signed_cert",
    "hash_args_go_client",
    "hash_args_python_center",
    "inbound_ceiling",
    "kit_call_result",
    "kit_catalog",
    "open_session",
    "parse_hook_output",
    "resolve_target",
    "run_begin_hook",
    "wait_until",
]
