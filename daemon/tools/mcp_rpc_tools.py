"""Agent-facing remote-hands tools — the ``mcp_rpc`` category (4 tools).

These are the agent-side surface of the remote-hands protocol (§2.2).
The agent never sees the WebSocket: it picks a machine, opens a session,
issues tool calls and closes the session.

    mcp_rpc_list_machine()              → roster of enrolled machines + presence
    mcp_rpc_connect("machine_id")       → opens a session; returns the catalog
    mcp_rpc_command("screenshot", args) → forwards a tool call into the session
    mcp_rpc_disconnect()                → ends the active session

Access posture — TWO layers, BOTH required
--------------------------------------------
1. The ``mcp_rpc`` category is PRIVILEGED, so it is opt-in-only and never
   joins the default-grant universe.
2. A privileged category alone is NOT sufficient — a LITERAL tool name in
   an allow list bypasses the category gate entirely. So every tool body
   ALSO carries its own ``agent_id`` gate, and the gate allows exactly
   ``ari`` and ``leader``.

Both gates are required; neither substitutes for the other.

THE KILL SWITCH
---------------

``MCPS_GATEWAY_ENABLED`` is DEFAULT OFF. The tools REFUSE with a typed
error when the transport is off — the tool layer must never look healthy
while the transport is dead. The refusal needs no listener: it is a
configuration read, not a connection attempt.

THE ARGUMENTS ARE NEVER AUDITED VERBATIM
-----------------------------------------

The audit of a forwarded command hashes the arguments (§8.4). This module
never puts the raw arguments into an audit row, into a log line, or into
an error message.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from ..services.mcps_gateway.protocol import (
    CODE_INTERNAL,
    GatewayProtocolError,
)
from ._tool_registry import register_tool_category

logger = logging.getLogger(__name__)

CATEGORY_NAME = "MCP RPC"
CATEGORY_DOC = """\
Remote-hands control of an ENROLLED machine (SPEC.md v0.1).

The daemon is the CENTER of the remote-hands protocol: a controlled
machine dials OUT to the daemon and the daemon brokers the session. You
never see the socket — you drive the machine through these four tools.

Tools in this category:
- ``mcp_rpc_list_machine`` — the roster of enrolled machines with presence
- ``mcp_rpc_connect``     — open a session on a machine; returns the remote tool catalog
- ``mcp_rpc_command``     — forward ONE remote tool call into the open session
- ``mcp_rpc_disconnect``  — end the open session (the machine stays connected)

ONE session at a time. A machine that already carries a session answers
``MACHINE_BUSY`` — pick another machine or wait for the session to end.
A command without a session answers ``SESSION_NOT_FOUND``.

The gateway is OFF by default (``MCPS_GATEWAY_ENABLED``); while it is
off every tool refuses with a typed error.
"""

#: The agents allowed to drive a remote machine.
#:
#: The privileged category gates VISIBILITY; this set gates AUTHORITY.
#: A literal tool name in an allow list bypasses the category gate, so
#: BOTH gates are required.
MCP_RPC_AUTHORIZED_AGENTS: frozenset[str] = frozenset({"ari", "leader"})


def _is_mcp_rpc_authorized(agent_id: str) -> bool:
    """Is this agent allowed to drive a remote machine?"""
    return agent_id in MCP_RPC_AUTHORIZED_AGENTS


def _error(code: str, message: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    """The typed error payload — the tools never raise.

    The refusal carries the stable machine-readable code of the protocol
    (§6.5) so an agent can branch on it; the message is advisory.
    """
    payload: dict[str, Any] = {"error": {"code": code, "message": message}}
    if data:
        payload["error"]["data"] = data
    return payload


def _format_protocol_error(exc: GatewayProtocolError) -> dict[str, Any]:
    """Map a typed protocol refusal onto the tool payload."""
    return _error(exc.code, exc.message, exc.data or None)


class McpRpcListMachineInput(BaseModel):
    """Input schema for mcp_rpc_list_machine (no arguments)."""


class McpRpcConnectInput(BaseModel):
    """Input schema for mcp_rpc_connect."""

    machine_id: Annotated[
        str,
        Field(description="The id of the enrolled machine, e.g. 'mach_9f3c21ab'"),
    ]
    reason: Annotated[
        str,
        Field(
            default="",
            description="Why the session is opened (recorded with the session).",
        ),
    ]


class McpRpcCommandInput(BaseModel):
    """Input schema for mcp_rpc_command."""

    tool: Annotated[
        str,
        Field(description="The name of the REMOTE tool, exactly as the catalog lists it"),
    ]
    arguments: Annotated[
        dict[str, Any] | None,
        Field(default=None, description="The arguments of the remote tool call"),
    ]


class McpRpcDisconnectInput(BaseModel):
    """Input schema for mcp_rpc_disconnect (no arguments)."""


def create_mcp_rpc_tools(
    gateway_service: Any,
    *,
    current_instance_id: str = "",
    agent_id: str = "",
    agent_tag: str | None = None,
) -> list:
    """Build the four remote-hands tools for one instance/agent.

    Args:
        gateway_service: The
            :class:`daemon.services.mcps_gateway.service.McpGatewayService`.
            Injected per-instance from ``InstanceManager.mcps_gateway_service``.
        current_instance_id: The caller's instance id. It is written into
            the audit row — the agent can NEVER supply an author value.
        agent_id: The caller's agent id. Captured at factory time and
            checked by :func:`_is_mcp_rpc_authorized` in every tool body.
        agent_tag: The caller's version tag. Currently unused by the
            tools themselves; reserved so the factory signature matches
            the other per-instance factories.

    Returns:
        A list of the four ``@tool``-decorated callables.
    """
    caller_instance_id = current_instance_id or ""
    caller_agent_id = agent_id or ""

    @register_tool_category("mcp_rpc")
    @tool(args_schema=McpRpcListMachineInput)
    async def mcp_rpc_list_machine() -> dict[str, Any]:
        """List the enrolled remote machines with presence. Use tool_help("mcp_rpc_list_machine") for details."""
        if not _is_mcp_rpc_authorized(caller_agent_id):
            return _error(
                CODE_INTERNAL,
                "remote-hands control is not permitted for this agent",
            )
        try:
            machines = gateway_service.roster()
        except GatewayProtocolError as exc:
            # R2: the typed refusal of the gateway (e.g. the gateway is
            # disabled) reaches the agent as a typed error.
            return _format_protocol_error(exc)
        except Exception:  # noqa: BLE001 — the tool boundary swallows all
            logger.exception("the remote-hands roster could not be read")
            return _error(CODE_INTERNAL, "the machine roster could not be read")
        return {
            "machines": machines,
            "count": len(machines),
            "presence": (
                "online — the heartbeat is within the timeout; "
                "stale — the heartbeat is late; "
                "offline — the socket is closed"
            ),
        }

    mcp_rpc_list_machine._full_doc_ = """List the enrolled remote machines.

Returns the roster of the machines that completed the remote-hands
handshake with this daemon, with the presence of each:

- ``online``  — the heartbeat is within the timeout
- ``stale``   — the heartbeat is late (the session may already be dead)
- ``offline`` — the socket is closed (the machine re-appears by itself
  when it reconnects — it does NOT re-enroll)

Args: none.

Returns:
    {"machines": [{"machine_id", "presence", "connected",
    "last_seen_epoch_ms", "session_id", "agent_id"}], "count": N}
"""

    @register_tool_category("mcp_rpc")
    @tool(args_schema=McpRpcConnectInput)
    async def mcp_rpc_connect(machine_id: str, reason: str = "") -> dict[str, Any]:
        """Open a remote session on a machine and return its tool catalog. Use tool_help("mcp_rpc_connect") for details."""
        if not _is_mcp_rpc_authorized(caller_agent_id):
            return _error(
                CODE_INTERNAL,
                "remote-hands control is not permitted for this agent",
            )
        try:
            return await gateway_service.connect(
                machine_id=machine_id,
                agent_id=caller_agent_id,
                instance_id=caller_instance_id,
                reason=reason,
            )
        except GatewayProtocolError as exc:
            return _format_protocol_error(exc)
        except Exception:  # noqa: BLE001 — the tool boundary swallows all
            logger.exception("the remote-hands session could not be opened")
            return _error(CODE_INTERNAL, "the remote session could not be opened")

    mcp_rpc_connect._full_doc_ = """Open a remote session on an enrolled machine.

The session is EXCLUSIVE: while a session is open on a machine, no
second session may open on it — the answer is ``MACHINE_BUSY``.

Args:
    machine_id: The id of the enrolled machine. Required.
    reason: Why the session is opened (recorded with the session).
        Optional, defaults to empty.

Returns:
    {"session_id", "machine_id", "tools": [ ...the remote catalog... ],
    "expires_at_epoch_ms"}
"""

    @register_tool_category("mcp_rpc")
    @tool(args_schema=McpRpcCommandInput)
    async def mcp_rpc_command(
        tool: str, arguments: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Forward one remote tool call into the open session. Use tool_help("mcp_rpc_command") for details."""
        if not _is_mcp_rpc_authorized(caller_agent_id):
            return _error(
                CODE_INTERNAL,
                "remote-hands control is not permitted for this agent",
            )
        try:
            return await gateway_service.command(
                agent_id=caller_agent_id,
                instance_id=caller_instance_id,
                tool=tool,
                arguments=arguments or {},
            )
        except GatewayProtocolError as exc:
            return _format_protocol_error(exc)
        except Exception:  # noqa: BLE001 — the tool boundary swallows all
            logger.exception("the remote command could not be forwarded")
            return _error(CODE_INTERNAL, "the remote command could not be forwarded")

    mcp_rpc_command._full_doc_ = """Forward ONE remote tool call into the open session.

The call is forwarded verbatim: the arguments go to the machine as they
are and the result comes back as the machine produced it. The catalog of
``mcp_rpc_connect`` lists the remote tool names.

Two names are RESERVED by the center's audit vocabulary —
``session.end`` and ``session.expire``. A remote tool carrying one of
them cannot be called: the ledger would give one value two meanings (a
forwarded command and a session-lifecycle event). Such a call is
refused with a typed error before it reaches the machine.

Args:
    tool: The name of the remote tool, exactly as the catalog lists it.
        Required.
    arguments: The arguments of the remote tool call. Optional, defaults
        to an empty object.

Returns:
    The raw result of the remote tool call, or
    {"error": {"code", "message"}} for a typed refusal
    (``SESSION_NOT_FOUND`` when no session is open, ``TOOL_TIMEOUT``
    when the machine did not answer in time, and the machine's own
    refusal relayed verbatim).
"""

    @register_tool_category("mcp_rpc")
    @tool(args_schema=McpRpcDisconnectInput)
    async def mcp_rpc_disconnect() -> dict[str, Any]:
        """End the open remote session (reason-coded answer). Use tool_help("mcp_rpc_disconnect") for details."""
        if not _is_mcp_rpc_authorized(caller_agent_id):
            return _error(
                CODE_INTERNAL,
                "remote-hands control is not permitted for this agent",
            )
        try:
            # The service answers a REASON-CODED ENVELOPE for every
            # session-state outcome — it never raises for one (the
            # kill-switch, i.e. a disabled gateway, is the only thing
            # that raises, and it reaches the agent as a typed error).
            return await gateway_service.disconnect(
                agent_id=caller_agent_id,
                instance_id=caller_instance_id,
            )
        except GatewayProtocolError as exc:
            return _format_protocol_error(exc)
        except Exception:  # noqa: BLE001 — the tool boundary swallows all
            logger.exception("the remote session could not be ended")
            return _error(CODE_INTERNAL, "the remote session could not be ended")

    mcp_rpc_disconnect._full_doc_ = """End the open remote session.

The machine keeps its socket open and returns to ``ready``: the next
session is opened on the same connection.

The answer is a REASON-CODED ENVELOPE and it is returned for EVERY
session-state outcome — the tool never raises for one (only the
kill-switch, i.e. a disabled gateway, answers ``{"error": {...}}``)::

    {session_id?, machine_id?, ended: bool, detail: str,
     machine_notified: bool, why_not_ended?, result?}

- ``why_not_ended`` is present IFF ``ended`` is false; its value is one
  of ``expired`` (the session was reaped by the expiry sweep),
  ``already_ended`` (it was ended before), ``unknown_session`` (this
  agent holds no session, and none is remembered) or ``machine_refused``
  (the machine refused the end — the session SURVIVES).
- ``result`` is the machine's VERBATIM acknowledgement and is present
  only when the machine acknowledged the end.
- ``session_id`` / ``machine_id`` are present only when they are known:
  the answer never leaks the existence of somebody else's session.
- ``machine_notified`` says whether the MACHINE was reached at all.

The tool takes NO argument: the end is AGENT-ADDRESSED — it ends the
session this agent holds, if any.

Args: none.

Returns:
    The reason-coded envelope above.
"""

    return [
        mcp_rpc_list_machine,
        mcp_rpc_connect,
        mcp_rpc_command,
        mcp_rpc_disconnect,
    ]


__all__ = [
    "CATEGORY_DOC",
    "CATEGORY_NAME",
    "MCP_RPC_AUTHORIZED_AGENTS",
    "create_mcp_rpc_tools",
]
