"""The session manager — one active session per machine (§8.3).

The v1 policy is EXCLUSIVITY: while a session is open on a machine, no
second session may open on it. The SPEC leaves the center two legal
behaviors — QUEUE the request or REJECT it with ``MACHINE_BUSY`` — and
the leader ruling for this phase is REJECT.

The lock is enforced **by the center** and NOT by sending a second
``session.begin`` to the machine: the machine refuses a concurrent
``session.begin`` for its own ``machine_id`` (strict equality), so a
second begin would be answered with an error the center would have to
translate. The center checks its own lock FIRST and answers the agent
with ``MACHINE_BUSY`` without touching the socket.

A second dimension of the same policy lives in §8.2: one AGENT controls
one remote at a time. The manager therefore also tracks the session of
each agent, so ``mcp_rpc_command`` knows which session the call belongs
to and a second ``mcp_rpc_connect`` from the same agent does not open a
second session on another machine.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

from .protocol import (
    CODE_MACHINE_BUSY,
    CODE_SESSION_EXPIRED,
    CODE_SESSION_NOT_FOUND,
    GatewayProtocolError,
)


@dataclass
class GatewaySession:
    """One open session.

    Attributes:
        session_id: The id the MACHINE minted (§6.4 — the client returns
            it; the center adopts it). The center NEVER mints one.
        machine_id: The machine the session runs on.
        agent_id: The agent that holds the session.
        instance_id: The instance of that agent.
        connection_id: The connection the session belongs to — a session
            belongs to its connection (§6.8: there is no resumption).
        catalog: The remote tool catalog, exactly as the machine reported
            it (§6.4 — the wrapper MUST NOT rewrite, rename or drop
            schemas; the center MUST NOT rewrite them either).
        started_at: Epoch seconds.
        last_activity: Epoch seconds of the last traffic.
        expires_at: Epoch seconds of the TTL ceiling.
    """

    session_id: str
    machine_id: str
    agent_id: str
    instance_id: str
    connection_id: str
    catalog: list[dict[str, Any]] = field(default_factory=list)
    started_at: float = 0.0
    last_activity: float = 0.0
    expires_at: float = 0.0

    def to_public_dict(self) -> dict[str, Any]:
        """The session view handed back to an agent."""
        return {
            "session_id": self.session_id,
            "machine_id": self.machine_id,
            "agent_id": self.agent_id,
            "instance_id": self.instance_id,
            "started_at_epoch_ms": int(self.started_at * 1000),
            "expires_at_epoch_ms": int(self.expires_at * 1000),
            "tools": [t.get("name") for t in self.catalog if isinstance(t, dict)],
        }


class SessionManager:
    """The live sessions of the center."""

    def __init__(
        self,
        *,
        session_ttl_ms: int = 3_600_000,
        session_idle_timeout_ms: int = 300_000,
    ) -> None:
        self._sessions: dict[str, GatewaySession] = {}
        self._by_machine: dict[str, str] = {}
        self._by_agent: dict[str, str] = {}
        self._lock = threading.RLock()
        self._session_ttl_ms = session_ttl_ms
        self._session_idle_timeout_ms = session_idle_timeout_ms

    # ── reads ────────────────────────────────────────────────────────────

    def get(self, session_id: str) -> GatewaySession | None:
        with self._lock:
            return self._sessions.get(session_id)

    def for_agent(self, agent_id: str) -> GatewaySession | None:
        with self._lock:
            session_id = self._by_agent.get(agent_id)
            return self._sessions.get(session_id) if session_id else None

    def for_machine(self, machine_id: str) -> GatewaySession | None:
        with self._lock:
            session_id = self._by_machine.get(machine_id)
            return self._sessions.get(session_id) if session_id else None

    def all(self) -> list[GatewaySession]:
        with self._lock:
            return list(self._sessions.values())

    # ── mutations ────────────────────────────────────────────────────────

    def assert_machine_free(self, machine_id: str) -> None:
        """Raise ``MACHINE_BUSY`` when the machine already has a session.

        This is the center-side half of the §8.3 lock. The check happens
        BEFORE any frame reaches the machine.
        """
        with self._lock:
            session_id = self._by_machine.get(machine_id)
            session = self._sessions.get(session_id) if session_id else None
        if session is None:
            return
        raise GatewayProtocolError(
            CODE_MACHINE_BUSY,
            f"machine {machine_id!r} already has an active session",
            {"machine_id": machine_id, "session_id": session.session_id},
        )

    def begin(
        self,
        *,
        session_id: str,
        machine_id: str,
        agent_id: str,
        instance_id: str,
        connection_id: str,
        catalog: list[dict[str, Any]],
        now: float | None = None,
    ) -> GatewaySession:
        """Record a session the machine has just opened.

        THE SLOT IS TAKEN ATOMICALLY (the §8.3 lock).

        ``assert_machine_free`` runs BEFORE the round-trip to the
        machine, so between that check and this write another agent can
        take the machine. A blind overwrite would hand the loser the
        MACHINE'S refusal (the machine answers the second
        ``session.begin`` with its own ``MACHINE_BUSY``) — which would
        make the center's exclusivity rest on the conformANTness of the
        machine instead of on the center itself.

        So the slot is RE-CHECKED HERE, under the same lock that writes
        it, and the loser is refused with ``MACHINE_BUSY`` by the
        CENTER. The slot is never blind-overwritten.
        """
        now = time.time() if now is None else now
        session = GatewaySession(
            session_id=session_id,
            machine_id=machine_id,
            agent_id=agent_id,
            instance_id=instance_id,
            connection_id=connection_id,
            catalog=list(catalog or []),
            started_at=now,
            last_activity=now,
            expires_at=now + self._session_ttl_ms / 1000.0,
        )
        with self._lock:
            held_id = self._by_machine.get(machine_id)
            held = self._sessions.get(held_id) if held_id else None
            if held is not None and held.session_id != session_id:
                raise GatewayProtocolError(
                    CODE_MACHINE_BUSY,
                    f"machine {machine_id!r} already has an active session",
                    {"machine_id": machine_id, "session_id": held.session_id},
                )
            agent_id_held = self._by_agent.get(agent_id)
            agent_session = (
                self._sessions.get(agent_id_held) if agent_id_held else None
            )
            if (
                agent_session is not None
                and agent_session.session_id != session_id
                and agent_session.machine_id != machine_id
            ):
                raise GatewayProtocolError(
                    CODE_MACHINE_BUSY,
                    f"the agent already drives the machine "
                    f"{agent_session.machine_id!r}",
                    {
                        "reason": "agent_already_holds_session",
                        "agent_id": agent_id,
                        "session_id": agent_session.session_id,
                        "machine_id": agent_session.machine_id,
                    },
                )
            self._sessions[session_id] = session
            self._by_machine[machine_id] = session_id
            self._by_agent[agent_id] = session_id
        return session

    def end(self, session_id: str) -> GatewaySession | None:
        """Drop a session. Idempotent — an unknown id returns ``None``."""
        with self._lock:
            session = self._sessions.pop(session_id, None)
            if session is None:
                return None
            if self._by_machine.get(session.machine_id) == session_id:
                self._by_machine.pop(session.machine_id, None)
            if self._by_agent.get(session.agent_id) == session_id:
                self._by_agent.pop(session.agent_id, None)
            return session

    def end_for_connection(self, connection_id: str) -> list[GatewaySession]:
        """Drop every session that belongs to a connection.

        A session belongs to its connection (§6.8): when the socket dies,
        the session is KILLED, never paused, and the id is dead — a later
        use of it is a ``SESSION_NOT_FOUND``.
        """
        with self._lock:
            doomed = [s for s in self._sessions.values() if s.connection_id == connection_id]
        return [s for s in doomed if self.end(s.session_id) is not None]

    def touch(self, session_id: str, now: float | None = None) -> None:
        """Refresh the idle clock of a session."""
        now = time.time() if now is None else now
        with self._lock:
            session = self._sessions.get(session_id)
            if session is not None:
                session.last_activity = now

    def expire_due(self, now: float | None = None) -> list[GatewaySession]:
        """Drop the sessions that passed their TTL or their idle timeout.

        The CENTER owns the TTL and the idle timeout (§3.3 lists them among
        the parameters the server advertises; the shipped client PUBLISHES
        ``expires_at_ms`` but never enforces it — §5.10 of the Phase-A
        interpretations). An expired session is a ``SESSION_EXPIRED``
        refusal, not a silent disappearance.
        """
        now = time.time() if now is None else now
        idle_s = self._session_idle_timeout_ms / 1000.0
        with self._lock:
            doomed = [
                s
                for s in self._sessions.values()
                if now >= s.expires_at or (now - s.last_activity) >= idle_s
            ]
        return [s for s in doomed if self.end(s.session_id) is not None]

    # ── refusals ─────────────────────────────────────────────────────────

    @staticmethod
    def session_not_found(session_id: str) -> GatewayProtocolError:
        """The typed refusal for an unknown / dead session id.

        The ``data.session_id`` carries the SESSION id — the contract
        member is a session id, never an agent id.
        """
        return GatewayProtocolError(
            CODE_SESSION_NOT_FOUND,
            f"there is no live session {session_id!r}",
            {"session_id": session_id},
        )

    @staticmethod
    def no_session_for_agent(agent_id: str) -> GatewayProtocolError:
        """The typed refusal for "this agent holds no session at all".

        There is NO session id to report, so ``data.session_id`` is
        ABSENT (the contract member carries a session id) — the agent is
        named in its own member instead.
        """
        return GatewayProtocolError(
            CODE_SESSION_NOT_FOUND,
            f"the agent {agent_id!r} has no open session",
            {"agent_id": agent_id},
        )

    @staticmethod
    def session_expired(session_id: str) -> GatewayProtocolError:
        """The typed refusal for a session that passed its TTL."""
        return GatewayProtocolError(
            CODE_SESSION_EXPIRED,
            f"the session {session_id!r} passed its TTL or its idle timeout",
            {"session_id": session_id},
        )


__all__ = ["GatewaySession", "SessionManager"]
