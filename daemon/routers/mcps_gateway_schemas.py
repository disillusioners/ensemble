"""Pydantic response models for the remote-hands gateway READ surface.

Sibling module — the house convention (``daemon/routers/snapshot_schemas.py``,
``daemon/routers/workspace_schemas.py``, ``daemon/routers/skill_schemas.py``):
the catch-all ``daemon/routers/schemas.py`` is 2136+ lines and new domain
schemas land in their own sibling instead of growing it.

THE MODELS ARE THE FE-FACING WIRE CONTRACT
------------------------------------------

The frontend types against these field names VERBATIM, so they are
deliberately minimal, stable and snake_case:

* timestamps are **ISO-8601 UTC strings with an explicit offset**
  (``2026-10-22T07:31:04.512000+00:00``) — the repo-wide wire
  convention (``daemon/services/timestamps.py`` — ``now_utc_iso`` /
  ``to_utc_iso``), NOT epoch milliseconds. The agent-facing surface
  (the ``mcp_rpc_*`` tools) uses epoch milliseconds because it is a
  machine contract; a browser page is not.
* identities keep the repo's ``*_id`` naming (``agent_id``,
  ``machine_id``, ``session_id``) — the same names the audit table, the
  session record and the machine roster already use.
* every response carries ``gateway_enabled`` — the kill-switch state
  (``MCPS_GATEWAY_ENABLED``) — so the page can tell "the gateway is
  OFF" apart from "nothing is enrolled" instead of guessing from an
  empty list.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

__all__ = [
    "GatewayAuditEntry",
    "GatewayAuditResponse",
    "GatewayMachine",
    "GatewayMachinesResponse",
    "GatewayReadEnvelope",
    "GatewaySession",
    "GatewaySessionsResponse",
]


class GatewayReadEnvelope(BaseModel):
    """The shared envelope of the three read responses — the degraded
    signal (AD4).

    A DB outage must be DISTINGUISHABLE from an empty ledger, and the
    house convention for that is **200 + flag, never 503**: 503 stays
    reserved for "surface not wired" (the read routes' own 503), where
    the FE maps it to ``gateway_unavailable``. ``degraded`` is
    ORTHOGONAL to ``gateway_enabled`` — a disabled gateway is NOT
    degraded (it answers a perfectly healthy empty payload).

    ``error`` carries a SHORT, NON-SECRET classification (e.g.
    ``"audit_unavailable"``) — never a raw exception string (a DB error
    message can quote DSNs and paths).

    TRUTHFUL SYMMETRY: all three envelopes carry the flag, but only
    ``/audit`` can set it today — the machines and sessions lanes read
    IN-MEMORY state and cannot fail on a DB outage, so they answer
    ``degraded: false`` permanently (and honestly). The symmetry exists
    so the FE can treat one shape everywhere and so a future persisted
    roster does not need a breaking change.
    """

    degraded: bool = False
    error: str | None = None


class GatewayMachine(BaseModel):
    """One enrolled machine of the roster (SPEC §8.1).

    Attributes:
        machine_id: The stable key of the machine.
        presence: ``online`` / ``stale`` / ``offline`` — the presence
            state the roster derives from the heartbeat.
        online: The boolean face of ``presence`` (``presence ==
            "online"``) — the flag the page renders as a live badge.
        connected: Whether the socket is open (the machine upgraded
            and has not disconnected).
        last_seen_at: ISO-8601 UTC stamp of the last heartbeat, or
            ``None`` when the machine never heartbeated.
        session_id: The ACTIVE session on this machine, or ``None`` —
            the §8.3 lock pointer.
        agent_id: The agent holding that active session, or ``None``.
        busy: Whether a session holds the machine's exclusive lock —
            i.e. a second ``session.begin`` would be refused with
            ``MACHINE_BUSY``.
    """

    machine_id: str
    presence: str
    online: bool = False
    connected: bool = False
    last_seen_at: str | None = None
    session_id: str | None = None
    agent_id: str | None = None
    busy: bool = False


class GatewayMachinesResponse(GatewayReadEnvelope):
    """Body of ``GET /api/mcps-gateway/machines``."""

    machines: list[GatewayMachine] = Field(default_factory=list)
    count: int = 0
    gateway_enabled: bool = False


class GatewaySession(BaseModel):
    """One live session of the center (SPEC §6.4 / §8.3).

    Attributes:
        session_id: The id the MACHINE minted (§6.4 — the center adopts
            it; it never mints one).
        machine_id: The machine the session runs on.
        agent_id: The agent that holds the session.
        instance_id: The instance of that agent.
        state: The lifecycle state of the session. The center keeps
            only LIVE sessions (a session is killed with its
            connection, §6.8 — there is no resumption), so the value
            is ``live``; the field exists so the page can render the
            state without a second lookup.
        machine_locked: Whether this session holds the machine's
            exclusive §8.3 lock — the ``MACHINE_BUSY`` indicator: when
            it is true, no other agent may open a session on that
            machine.
        started_at: ISO-8601 UTC stamp of ``session.begin``.
        last_activity_at: ISO-8601 UTC stamp of the last traffic on
            the session (the idle clock of §3.3).
        expires_at: ISO-8601 UTC stamp of the TTL ceiling (§3.3).
        tools: The names of the remote tools the machine reported in
            its catalog (§6.4 — the center never rewrites them).
    """

    session_id: str
    machine_id: str
    agent_id: str
    instance_id: str
    state: str = "live"
    machine_locked: bool = False
    started_at: str | None = None
    last_activity_at: str | None = None
    expires_at: str | None = None
    tools: list[str] = Field(default_factory=list)


class GatewaySessionsResponse(GatewayReadEnvelope):
    """Body of ``GET /api/mcps-gateway/sessions``."""

    sessions: list[GatewaySession] = Field(default_factory=list)
    count: int = 0
    gateway_enabled: bool = False


class GatewayAuditEntry(BaseModel):
    """One row of the remote-command audit (SPEC §8.4).

    Attributes:
        timestamp: ISO-8601 UTC stamp of the row.
        agent_id: WHO asked.
        instance_id: The instance of that agent, when known.
        machine_id: The machine the command ran on.
        tool: The name of the remote tool.
        outcome: ``ok`` / ``error`` / ``denied`` / ``timeout``.
        session_id: The session the command ran in, when known.
        error_code: The typed error code, when the command failed.
        args_hash: The SHA-256 of the canonicalised arguments — the
            arguments themselves are NEVER stored (§8.4), so this hash
            is the only trace of them.
    """

    timestamp: str | None = None
    agent_id: str = ""
    instance_id: str | None = None
    machine_id: str = ""
    tool: str = ""
    outcome: str = ""
    session_id: str | None = None
    error_code: str | None = None
    args_hash: str = ""


class GatewayAuditResponse(GatewayReadEnvelope):
    """Body of ``GET /api/mcps-gateway/audit``.

    The audit tail is paginated (``limit`` / ``offset``) and ordered
    NEWEST FIRST — the order the ledger is written in. This is the one
    envelope whose lane can actually DEGRADE: the tail is read from the
    DB, bounded by ``MCPS_GATEWAY_READ_TIMEOUT_MS`` — a dead or hung
    reader answers ``degraded: true`` with the LAST classification in
    ``error`` (still 200, never 503).
    """

    audit: list[GatewayAuditEntry] = Field(default_factory=list)
    count: int = 0
    limit: int = 0
    offset: int = 0
    gateway_enabled: bool = False
