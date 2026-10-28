"""The remote-hands wire vocabulary — SPEC.md v0.1 (``mcps_client``).

Every literal in this module is a transcription of the SPEC text, NOT an
alias of a constant that lives somewhere else. The shipped reference
client (``mcps_client/internal/remote``) is the interop ground truth, and
where the shipped client deviates from a literal reading of SPEC.md the
shipped client wins — the deviations are recorded in the mission report.

What this module owns
---------------------

* the envelope of §6.1 (``v`` / ``type`` / ``id`` / ``session_id`` /
  ``ts`` / ``method`` / ``params`` / ``result`` / ``error``),
* the seven methods of §6.3 (``hello``, ``heartbeat``, ``session.begin``,
  ``session.end``, ``tools/list``, ``tools/call``, ``notifications/*``)
  plus the v0.2 client-originated ``session.terminate``,
* the capability map of §5.3,
* the typed error codes of §6.5.

Direction of the four MCP methods (dossier correction #2): the GATEWAY is
the REQUESTOR on ``session.begin`` / ``session.end`` / ``tools/list`` /
``tools/call``. The machine answers with a ``response`` envelope. The
only two protocol messages a machine ever sends are ``hello`` (a request)
and ``heartbeat`` (a notification).
"""

from __future__ import annotations

import json
from typing import Any

#: The protocol version exchanged in the hello handshake (§5.2).
#:
#: This is the DEFAULT — the version the centre stamps on a frame whose
#: connection has not negotiated one yet, and the version every v0.1
#: connection runs. It is NOT the only version the centre speaks: see
#: :data:`SUPPORTED_PROTOCOL_VERSIONS`.
PROTOCOL_VERSION = "0.1"

#: The v0.2 protocol version — the session-termination generation.
#:
#: v0.2 adds the CLIENT-ORIGINATED ``session.terminate`` receipt (§6.10),
#: the ``client_terminate`` capability and the ``reason`` member of the
#: centre's ``session.end`` params. Everything v0.2 adds is
#: VERSION-SCOPED: a connection that negotiated ``"0.1"`` behaves
#: byte-identically to the v0.1 centre.
PROTOCOL_VERSION_V02 = "0.2"

#: The protocol versions this centre ACCEPTS in the hello handshake.
#:
#: The order is the PREFERENCE order (newest first): the ``supported``
#: member of a ``PROTOCOL_VERSION_UNSUPPORTED`` refusal lists them, and a
#: client that can speak several picks the first one it speaks.
#:
#: The centre NEVER silently downgrades (§5.2): a client that asks for a
#: version outside this tuple is refused with
#: :data:`CODE_PROTOCOL_VERSION_UNSUPPORTED` and its socket is closed.
#: The version a connection RUNS is the one the client REQUESTED (§5.2)
#: — never one the centre picked for it.
SUPPORTED_PROTOCOL_VERSIONS: tuple[str, ...] = (
    PROTOCOL_VERSION_V02,
    PROTOCOL_VERSION,
)

#: The server name advertised in the hello result (§5.4).
SERVER_NAME = "ensemble-gateway"

# ── Envelope types (§6.1) ────────────────────────────────────────────────
TYPE_REQUEST = "request"
TYPE_RESPONSE = "response"
TYPE_ERROR = "error"
TYPE_NOTIFICATION = "notification"

# ── The seven methods (§6.3) ─────────────────────────────────────────────
METHOD_HELLO = "hello"
METHOD_HEARTBEAT = "heartbeat"
METHOD_SESSION_BEGIN = "session.begin"
METHOD_SESSION_END = "session.end"
#: The CLIENT-ORIGINATED termination receipt (SPEC v0.2 §6.10).
#:
#: The machine asks the centre to end a session it owns. The centre
#: NEVER refuses it (the client owns the machine — §6.10.1 forbids a
#: refusal of an operator-forced end), and the connection STAYS OPEN:
#: the socket may still be carrying a late ``tools/call`` answer.
#:
#: It is a v0.2 method: a connection that negotiated ``"0.1"`` never
#: advertises the ``client_terminate`` capability, so a ``session.terminate``
#: on such a connection falls through to the unknown-method arm and has
#: NO session effect.
METHOD_SESSION_TERMINATE = "session.terminate"
#: Defined for protocol completeness: the center serves its catalog via
#: ``session.begin`` (§6.4 — the catalog rides the session) and never
#: ISSUES ``tools/list`` (§5.9 makes it optional for the center; the
#: machine may be asked for a mid-session refresh, but the v0.1 center
#: has no need for one).
METHOD_TOOLS_LIST = "tools/list"
METHOD_TOOLS_CALL = "tools/call"
METHOD_NOTIFICATIONS_PREFIX = "notifications/"

#: The methods the GATEWAY originates (center → client).
CENTER_ORIGINATED_METHODS = (
    METHOD_SESSION_BEGIN,
    METHOD_SESSION_END,
    METHOD_TOOLS_CALL,
)

#: The methods the MACHINE originates (client → center). ``hello`` is a
#: request; ``heartbeat`` is a notification; ``session.terminate`` is a
#: request (SPEC v0.2 — see :data:`METHOD_SESSION_TERMINATE`).
CLIENT_ORIGINATED_METHODS = (
    METHOD_HELLO,
    METHOD_HEARTBEAT,
    METHOD_SESSION_TERMINATE,
)

# ── Capability flags (§5.3) ──────────────────────────────────────────────
CAP_SESSION = "session"
CAP_TOOLS = "tools"
CAP_NOTIFICATIONS = "notifications"
CAP_HEARTBEAT = "heartbeat"
CAP_SCREENSHOT_DELTA = "screenshot_delta"
CAP_MULTI_SESSION = "multi_session"
CAP_CLIENT_CERT = "client_cert"
#: The v0.2 capability that lets the MACHINE terminate a session from its
#: own side (SPEC v0.2 §5.3 / §6.10).
#:
#: It is NOT part of :data:`REQUIRED_CLIENT_CAPABILITIES`: a v0.1
#: machine does not know the name, and demanding it would refuse every
#: shipped client. The centre advertises it only on a v0.2-negotiated
#: connection, and only when the machine advertised it too.
CAP_CLIENT_TERMINATE = "client_terminate"

#: The capabilities the CENTER demands of the machine.
#:
#: ``notifications`` is NOT in this set. The shipped client advertises
#: ``notifications: false`` (it relays none) and the Phase-A reading of
#: §5.3 binds the "required" column to the OFFERING side — enforcing it
#: against the machine would kill every connection.
REQUIRED_CLIENT_CAPABILITIES: tuple[str, ...] = (CAP_SESSION, CAP_TOOLS, CAP_HEARTBEAT)

#: The capability map the CENTER advertises.
#:
#: Honest by construction: the v0.1 center relays NO MCP notification
#: (``notifications: false``), supports no delta screenshots, runs one
#: session per machine (``multi_session: false``) and speaks the bearer
#: tier, not mTLS (``client_cert: false``).
#:
#: ``client_terminate`` is DELIBERATELY ABSENT from this map: it is a
#: v0.2 capability and this map is what a v0.1-negotiated connection
#: advertises — adding the key here would change the v0.1 hello result
#: (it must stay byte-identical). The v0.2 map is built per connection
#: in the service (:func:`_capabilities_for`).
SERVER_CAPABILITIES: dict[str, bool] = {
    CAP_SESSION: True,
    CAP_TOOLS: True,
    CAP_NOTIFICATIONS: False,
    CAP_HEARTBEAT: True,
    CAP_SCREENSHOT_DELTA: False,
    CAP_MULTI_SESSION: False,
    CAP_CLIENT_CERT: False,
}

# ── Typed error codes (§6.5) ─────────────────────────────────────────────
CODE_PROTOCOL_VERSION_UNSUPPORTED = "PROTOCOL_VERSION_UNSUPPORTED"
CODE_CAPABILITY_MISSING = "CAPABILITY_MISSING"
CODE_AUTH_FAILED = "AUTH_FAILED"
CODE_MACHINE_NOT_ENROLLED = "MACHINE_NOT_ENROLLED"
CODE_MACHINE_BUSY = "MACHINE_BUSY"
#: Two connections claim the SAME ``machine_id`` (SPEC v0.2 §5.6).
#:
#: The centre refuses the NEWCOMER and leaves the INCUMBENT alone — the
#: incumbent keeps its socket AND its live session. The centre never
#: evicts a live connection to make room for a duplicate id.
CODE_MACHINE_ID_CONFLICT = "MACHINE_ID_CONFLICT"
CODE_SESSION_NOT_FOUND = "SESSION_NOT_FOUND"
CODE_SESSION_EXPIRED = "SESSION_EXPIRED"
CODE_TOOL_NOT_FOUND = "TOOL_NOT_FOUND"
CODE_TOOL_DENIED = "TOOL_DENIED"  # reserved for v0.2+ — NEVER emitted
CODE_TOOL_TIMEOUT = "TOOL_TIMEOUT"
CODE_TOOL_ERROR = "TOOL_ERROR"
CODE_PAYLOAD_TOO_LARGE = "PAYLOAD_TOO_LARGE"
CODE_RATE_LIMITED = "RATE_LIMITED"
CODE_HEARTBEAT_TIMEOUT = "HEARTBEAT_TIMEOUT"
CODE_INTERNAL = "INTERNAL"

#: The codes the CENTER originates.
#:
#: ``TOOL_DENIED`` is reserved for v0.2+ (§6.5) and is never emitted.
#: ``AUTH_FAILED`` travels as an HTTP 401 on the upgrade — never in-band.
CENTER_EMITTED_CODES: frozenset[str] = frozenset(
    {
        CODE_PROTOCOL_VERSION_UNSUPPORTED,
        CODE_CAPABILITY_MISSING,
        CODE_MACHINE_NOT_ENROLLED,
        CODE_MACHINE_BUSY,
        CODE_MACHINE_ID_CONFLICT,
        CODE_SESSION_NOT_FOUND,
        CODE_SESSION_EXPIRED,
        CODE_HEARTBEAT_TIMEOUT,
        CODE_INTERNAL,
        CODE_TOOL_TIMEOUT,
    }
)

#: The codes that arrive from the MACHINE and are relayed verbatim.
RELAYED_CODES: frozenset[str] = frozenset(
    {
        CODE_TOOL_NOT_FOUND,
        CODE_TOOL_TIMEOUT,
        CODE_TOOL_ERROR,
        CODE_PAYLOAD_TOO_LARGE,
        CODE_RATE_LIMITED,
    }
)

#: The audit ``tool`` values of the SESSION-LIFECYCLE surfaces.
#:
#: A command row carries the name of the REMOTE TOOL that was called. The
#: two session-lifecycle surfaces have no remote tool, so they carry the
#: name of the SURFACE instead — a stable, filterable value that lets an
#: operator ask the ledger "why did this session not end?".
AUDIT_TOOL_SESSION_END = "session.end"
AUDIT_TOOL_SESSION_EXPIRE = "session.expire"
#: The audit ``tool`` value of the CLIENT-ORIGINATED termination receipt
#: (SPEC v0.2 §6.10). The machine asked for the end, so the row records
#: a termination the centre did NOT originate.
AUDIT_TOOL_SESSION_TERMINATE = "session.terminate"
#: The audit ``tool`` value of the DUPLICATE-MACHINE-ID refusal
#: (SPEC v0.2 §5.6). The row records which id was refused and which
#: incumbent it collided with.
AUDIT_TOOL_HELLO_CONFLICT = "hello.conflict"

#: The audit ``tool`` values the CENTRE RESERVES for the session-lifecycle
#: surfaces.
#:
#: The remote tool names come from the MACHINE's catalog — verbatim
#: (§6.4: the centre MUST NOT rewrite, rename or drop schemas) — so a
#: remote tool could carry the name ``session.end``. That would make one
#: ledger value carry two meanings (a forwarded command and a lifecycle
#: event) and the rows indistinguishable for an operator filtering on
#: ``tool``. The CENTRE therefore owns this namespace and RESERVES the two
#: names: a command whose requested tool collides is refused before it
#: reaches the machine.
#:
#: The refusal changes neither the wire contract (the frame is unchanged
#: for every other tool) nor the tool input contract (the schema is
#: unchanged) — it only keeps the ledger's namespace honest.
RESERVED_AUDIT_TOOL_NAMES: frozenset[str] = frozenset(
    {
        AUDIT_TOOL_SESSION_END,
        AUDIT_TOOL_SESSION_EXPIRE,
        AUDIT_TOOL_SESSION_TERMINATE,
        AUDIT_TOOL_HELLO_CONFLICT,
    }
)

#: The protocol limit of a single frame (§6.7) and the hard ceiling above
#: it — the same shape the shipped client uses.
DEFAULT_MAX_FRAME_BYTES = 8 << 20
HARD_MAX_FRAME_BYTES = 32 << 20


class GatewayProtocolError(Exception):
    """A typed refusal of the remote-hands protocol (§6.5).

    The ``code`` is the stable machine-readable value; the ``message`` is
    advisory human text that a client MUST NOT parse; ``data`` is
    optional and its shape is explicitly NOT part of the contract.
    """

    def __init__(
        self,
        code: str,
        message: str = "",
        data: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code
        self.message = message
        self.data = data or {}

    def to_error_object(self) -> dict[str, Any]:
        """The ``error`` member of an error envelope (§6.5)."""
        error: dict[str, Any] = {"code": self.code}
        if self.message:
            error["message"] = self.message
        if self.data:
            error["data"] = self.data
        return error


class GatewayAckTimeout(GatewayProtocolError):
    """The CENTRE's own ack deadline fired while it waited for the machine.

    A distinct TYPE, never a distinct CODE. ``TOOL_TIMEOUT`` is BOTH a
    centre-emitted code (the centre's own deadline) and a RELAYED code
    (the machine relayed its own tool timeout), so the code alone cannot
    tell the two apart — and the two must be treated differently: the
    centre's own deadline means the centre may release the session
    AUTHORITATIVELY, while a RELAYED timeout is the machine's refusal to
    end the session, and the session SURVIVES it.

    This subclass is raised ONLY at the centre's ``wait_for`` site (the
    one place the centre gives up on its OWN wait), so
    ``isinstance(exc, GatewayAckTimeout)`` is the only honest
    discrimination — never a comparison against the code string.
    """

    def __init__(
        self,
        code: str,
        message: str = "",
        data: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(code, message, data)


def make_envelope(
    *,
    type: str,
    id: str | None = None,
    session_id: str | None = None,
    method: str | None = None,
    params: dict[str, Any] | None = None,
    result: Any = None,
    error: dict[str, Any] | None = None,
    v: str = PROTOCOL_VERSION,
) -> dict[str, Any]:
    """Build one envelope of §6.1.

    Every frame the center sends carries ``v`` — the shipped client checks
    the version of EVERY inbound message and closes the socket SILENTLY
    (no error frame) on a missing or wrong ``v``.

    ``v`` is the version the frame is stamped with: the DEFAULT is
    :data:`PROTOCOL_VERSION` (``"0.1"``), which keeps every v0.1 frame
    byte-identical. A v0.2-negotiated connection stamps its OWN
    negotiated version on EVERY outbound frame — the centre's single
    ``_send`` choke point does it, so a frame can never carry the
    default by accident.
    """
    envelope: dict[str, Any] = {"v": v, "type": type}
    if id is not None:
        envelope["id"] = id
    if session_id:
        envelope["session_id"] = session_id
    if method is not None:
        envelope["method"] = method
    if params is not None:
        envelope["params"] = params
    if result is not None:
        envelope["result"] = result
    if error is not None:
        envelope["error"] = error
    return envelope


def make_error_envelope(
    *,
    id: str | None,
    error: GatewayProtocolError,
    session_id: str | None = None,
    v: str = PROTOCOL_VERSION,
) -> dict[str, Any]:
    """Build an error envelope (§6.5).

    An error for an unknown session carries NO ``session_id`` — the id of
    the request was never validated against a live session, and echoing it
    back would confirm a session that does not exist.
    """
    return make_envelope(
        type=TYPE_ERROR,
        id=id,
        session_id=session_id,
        error=error.to_error_object(),
        v=v,
    )


def parse_envelope(raw: str) -> dict[str, Any]:
    """Parse one text frame into an envelope.

    Raises:
        ValueError: the frame is not a JSON object.
    """
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("the frame is not a JSON object")
    return payload


def canonical_json(value: Any) -> str:
    """The canonical JSON form used for the args hash.

    The keys are sorted and the separators are tight, so two calls with
    the same arguments hash the same whatever the key order.
    """
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


__all__ = [
    "AUDIT_TOOL_HELLO_CONFLICT",
    "AUDIT_TOOL_SESSION_END",
    "AUDIT_TOOL_SESSION_EXPIRE",
    "AUDIT_TOOL_SESSION_TERMINATE",
    "CAP_CLIENT_TERMINATE",
    "CENTER_EMITTED_CODES",
    "CENTER_ORIGINATED_METHODS",
    "CLIENT_ORIGINATED_METHODS",
    "CODE_AUTH_FAILED",
    "CODE_CAPABILITY_MISSING",
    "CODE_HEARTBEAT_TIMEOUT",
    "CODE_INTERNAL",
    "CODE_MACHINE_BUSY",
    "CODE_MACHINE_ID_CONFLICT",
    "CODE_MACHINE_NOT_ENROLLED",
    "CODE_PAYLOAD_TOO_LARGE",
    "CODE_PROTOCOL_VERSION_UNSUPPORTED",
    "CODE_RATE_LIMITED",
    "CODE_SESSION_EXPIRED",
    "CODE_SESSION_NOT_FOUND",
    "CODE_TOOL_DENIED",
    "CODE_TOOL_ERROR",
    "CODE_TOOL_NOT_FOUND",
    "CODE_TOOL_TIMEOUT",
    "DEFAULT_MAX_FRAME_BYTES",
    "GatewayAckTimeout",
    "GatewayProtocolError",
    "HARD_MAX_FRAME_BYTES",
    "METHOD_HEARTBEAT",
    "METHOD_HELLO",
    "METHOD_NOTIFICATIONS_PREFIX",
    "METHOD_SESSION_BEGIN",
    "METHOD_SESSION_END",
    "METHOD_SESSION_TERMINATE",
    "METHOD_TOOLS_CALL",
    "METHOD_TOOLS_LIST",
    "PROTOCOL_VERSION",
    "PROTOCOL_VERSION_V02",
    "RELAYED_CODES",
    "RESERVED_AUDIT_TOOL_NAMES",
    "REQUIRED_CLIENT_CAPABILITIES",
    "SERVER_CAPABILITIES",
    "SERVER_NAME",
    "SUPPORTED_PROTOCOL_VERSIONS",
    "TYPE_ERROR",
    "TYPE_NOTIFICATION",
    "TYPE_REQUEST",
    "TYPE_RESPONSE",
    "canonical_json",
    "make_envelope",
    "make_error_envelope",
    "parse_envelope",
]
