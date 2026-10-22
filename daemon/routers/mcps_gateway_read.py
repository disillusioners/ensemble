"""The remote-hands gateway READ surface — the OAuth API lane (Phase C).

Three READ-ONLY GET endpoints over the CENTER of the remote-hands
protocol (SPEC.md v0.1):

* ``GET /api/mcps-gateway/machines`` — the machine roster with the
  presence state, the last heartbeat and the ACTIVE-session pointer;
* ``GET /api/mcps-gateway/sessions`` — the live sessions with their
  §8.3 lock state;
* ``GET /api/mcps-gateway/audit`` — the remote-command audit tail
  (§8.4), paginated and NEWEST FIRST.

THE READ SURFACE IS NOT THE TRANSPORT
--------------------------------------

The WebSocket route (``daemon/routers/mcps_gateway.py``) is the
MODULE-LANE: a machine authenticates with the module's own bearer
ENROLLMENT token on the upgrade request (§3.1 / §4.1) and the OAuth2
middleware must never own that path.

The endpoints in THIS module are the opposite: they are ordinary
``/api`` read routers on the OAUTH2 lane — the same lane every other
``/api`` family rides. They carry the standard ``@require_scope``
authorization (the ``mcp:read`` scope) on top of the middleware's
authentication gate, exactly like the MCP-servers family.

THE ROUTES ARE REGISTERED UNCONDITIONALLY
------------------------------------------

The kill switch (``MCPS_GATEWAY_ENABLED``, DEFAULT OFF) governs the
TRANSPORT — the WebSocket route is not registered at all while it is
off, and the ``mcp_rpc_*`` tools refuse with a typed error. The READ
routes stay registered unconditionally, which is the dominant
convention for read-only ``/api`` routers (32 of the 34
``api_router.include_router(`` mounts are unconditional), and it gives
the operator page a STABLE contract: with the gateway OFF the payloads
degrade to EMPTY lists and ``gateway_enabled: false`` — the page can
say "the gateway is disabled" instead of guessing from a 404 it cannot
interpret.

READ-ONLY BY CONSTRUCTION
--------------------------

There is no POST / PUT / DELETE here, and there never will be: the
gateway is driven by the AGENT TOOLS (``mcp_rpc_*``), not by the
browser. This module only REPORTS the state the center already keeps.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from daemon.auth.decorators import require_scope
from daemon.services.mcps_gateway.service import McpGatewayService
from daemon.services.mcps_gateway.store import PRESENCE_ONLINE

from .mcps_gateway_schemas import (
    GatewayAuditEntry,
    GatewayAuditResponse,
    GatewayMachine,
    GatewayMachinesResponse,
    GatewaySession,
    GatewaySessionsResponse,
)

logger = logging.getLogger(__name__)

#: The router — mounted INSIDE ``api_router`` (whose prefix is ``/api``),
#: so the paths are ``/api/mcps-gateway/{machines,sessions,audit}``.
#:
#: The prefix is FIXED: the configurable ``MCPS_GATEWAY_PATH`` knob
#: governs the WebSocket transport (the URL a controlled machine dials
#: OUT to), not the operator-facing read surface.
router = APIRouter(prefix="/mcps-gateway", tags=["mcps-gateway"])

#: The lifecycle state of a session in the center.
#:
#: The center keeps only LIVE sessions: a session belongs to its
#: connection (§6.8) and is KILLED with it — there is no resumption and
#: no "ended" tombstone. The field is nevertheless reported, so the
#: page renders a state without a second lookup.
SESSION_STATE_LIVE = "live"

#: The pagination contract of the audit tail — the same shape as the
#: read-only snapshots family (``daemon/routers/snapshots.py``:
#: ``limit`` default 50, ``ge=1``, ``le=200``; ``offset`` default 0,
#: ``ge=0``). Out-of-range values are rejected by FastAPI with 422
#: BEFORE the handler runs; an offset beyond the end of the ledger
#: answers 200 with an EMPTY list (the ledger has no row there).
AUDIT_DEFAULT_LIMIT = 50
AUDIT_MAX_LIMIT = 200


def _service_of(request: Request) -> McpGatewayService:
    """The gateway service of the running app.

    ``app.state`` is the daemon-wide seam (the same shape the WebSocket
    route and the settings/workspace routers use). ``None`` means the
    lifespan never wired it — a boot-order defect, answered LOUDLY
    with 503 (the same posture as the WebSocket route's 503): a silent
    empty list would read as "nothing is enrolled" and hide the defect.
    """
    service = getattr(request.app.state, "mcps_gateway_service", None)
    if service is None:
        logger.error(
            "the remote-hands gateway read surface was reached without a "
            "wired service"
        )
        raise HTTPException(
            status_code=503,
            detail={
                "error": "gateway_unavailable",
                "message": "The remote-hands gateway service is not initialized",
                "state": "not_initialized",
            },
        )
    return service


def _epoch_to_iso(value: float | None) -> str | None:
    """Epoch seconds → an ISO-8601 UTC stamp with an explicit offset.

    The wire convention of the repo (``daemon/services/timestamps.py``:
    ``now_utc_iso`` / ``to_utc_iso`` produce ``...+00:00`` strings).
    ``0.0`` and ``None`` mean "never" and answer ``None``.
    """
    if not value:
        return None
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):  # pragma: no cover — defensive
        return None


@router.get("/machines", response_model=GatewayMachinesResponse)
@require_scope("mcp:read")
async def list_machines(request: Request) -> GatewayMachinesResponse:
    """The machine roster with presence, liveness and the active session.

    Reads the IN-MEMORY roster of the center (``MachineStore``) — the
    same store the agent tools read through ``mcp_rpc_list_machine``.
    The v0.1 roster does not survive a daemon restart (§1.2 of
    ``docs/mcps-gateway.md``), so an empty list after a restart is
    EXPECTED, not a defect.
    """
    service = _service_of(request)
    now = service.now()
    timeout_ms = service.config.heartbeat_timeout_ms
    machines: list[GatewayMachine] = []
    for record in service.store.roster():
        presence = record.presence(now, timeout_ms)
        machines.append(
            GatewayMachine(
                machine_id=record.machine_id,
                presence=presence,
                online=presence == PRESENCE_ONLINE,
                connected=record.connection_id is not None,
                last_seen_at=_epoch_to_iso(record.last_seen),
                session_id=record.session_id,
                agent_id=record.agent_id,
                # A machine whose slot is taken is BUSY: a second
                # session.begin would be refused with MACHINE_BUSY
                # (§8.3 — the center refuses, never the machine).
                busy=record.session_id is not None,
            )
        )
    return GatewayMachinesResponse(
        machines=machines,
        count=len(machines),
        gateway_enabled=service.config.enabled,
    )


@router.get("/sessions", response_model=GatewaySessionsResponse)
@require_scope("mcp:read")
async def list_sessions(request: Request) -> GatewaySessionsResponse:
    """The live sessions of the center, with their §8.3 lock state.

    ``machine_locked`` is the MACHINE_BUSY indicator: it is true when
    this session holds the machine's exclusive slot — no other agent
    may open a session on that machine while it is held.
    """
    service = _service_of(request)
    sessions = service.sessions
    items: list[GatewaySession] = []
    for session in sessions.all():
        held = sessions.for_machine(session.machine_id)
        items.append(
            GatewaySession(
                session_id=session.session_id,
                machine_id=session.machine_id,
                agent_id=session.agent_id,
                instance_id=session.instance_id,
                state=SESSION_STATE_LIVE,
                machine_locked=held is not None
                and held.session_id == session.session_id,
                started_at=_epoch_to_iso(session.started_at),
                last_activity_at=_epoch_to_iso(session.last_activity),
                expires_at=_epoch_to_iso(session.expires_at),
                tools=[
                    tool.get("name")
                    for tool in session.catalog
                    if isinstance(tool, dict) and tool.get("name")
                ],
            )
        )
    return GatewaySessionsResponse(
        sessions=items,
        count=len(items),
        gateway_enabled=service.config.enabled,
    )


@router.get("/audit", response_model=GatewayAuditResponse)
@require_scope("mcp:read")
async def list_audit(
    request: Request,
    limit: int = Query(default=AUDIT_DEFAULT_LIMIT, ge=1, le=AUDIT_MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
) -> GatewayAuditResponse:
    """The remote-command audit tail — NEWEST FIRST (§8.4).

    ``limit`` / ``offset`` paginate the ledger from its head (the
    newest row). The read is a synchronous DB call, so it is bridged
    to async with ``asyncio.to_thread`` — the project's standard
    pattern for the synchronous repositories.
    """
    service = _service_of(request)
    rows = await asyncio.to_thread(service.audit_tail, limit, offset)
    entries = [_audit_entry(row) for row in rows]
    return GatewayAuditResponse(
        audit=entries,
        count=len(entries),
        limit=limit,
        offset=offset,
        gateway_enabled=service.config.enabled,
    )


def _audit_entry(row: dict[str, Any]) -> GatewayAuditEntry:
    """One ledger row → the FE-facing audit entry.

    The row keys are the audit table's own column names
    (``daemon/repositories/mcps_gateway/models.py``); the wire renames
    ``created_at`` to ``timestamp`` (the brief's name for the time
    field) and keeps every other name.
    """
    return GatewayAuditEntry(
        timestamp=row.get("created_at"),
        agent_id=row.get("agent_id") or "",
        instance_id=row.get("instance_id"),
        machine_id=row.get("machine_id") or "",
        tool=row.get("tool") or "",
        outcome=row.get("outcome") or "",
        session_id=row.get("session_id"),
        error_code=row.get("error_code"),
        args_hash=row.get("args_hash") or "",
    )


__all__ = [
    "AUDIT_DEFAULT_LIMIT",
    "AUDIT_MAX_LIMIT",
    "SESSION_STATE_LIVE",
    "list_audit",
    "list_machines",
    "list_sessions",
    "router",
]
