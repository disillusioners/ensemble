"""The machine roster — presence and liveness of the enrolled machines.

SPEC.md §8.1 asks for three things from the center:

* a **roster** of enrolled machines, keyed by a stable ``machine_id``,
* **presence** derived from the heartbeat — ``online`` while the heartbeat
  is within ``heartbeat_timeout_ms``, ``stale`` just past it, ``offline``
  after the socket closes,
* **reconnect tolerance** — a machine that reconnects after a network
  blip re-appears in the roster; it does NOT re-enroll.

The store is behind an interface so that a database-backed implementation
can replace the in-memory one without touching the service. The v0.1
implementation is IN-MEMORY (the POC tier): a daemon restart empties the
roster, and the machines re-appear on their next upgrade.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Protocol

#: Presence states (§8.1).
PRESENCE_ONLINE = "online"
PRESENCE_STALE = "stale"
PRESENCE_OFFLINE = "offline"


@dataclass
class MachineRecord:
    """One enrolled machine.

    Attributes:
        machine_id: The stable key of the machine.
        connection_id: The id of the LIVE connection, or ``None`` when the
            machine is not connected.
        connected_at: The epoch seconds of the current (or last) upgrade.
        last_seen: The epoch seconds of the last ``heartbeat`` notification
            (or of the upgrade, when the machine has not heartbeated yet).
        session_id: The id of the ACTIVE session on this machine, or
            ``None``.
        agent_id: The agent that holds the active session.
    """

    machine_id: str
    connection_id: str | None = None
    connected_at: float = 0.0
    last_seen: float = 0.0
    session_id: str | None = None
    agent_id: str | None = None

    def presence(self, now: float, heartbeat_timeout_ms: int) -> str:
        """The presence of the machine at ``now``.

        ``offline`` when there is no live socket; ``online`` while the
        last heartbeat is within the timeout; ``stale`` just past it.
        """
        if self.connection_id is None:
            return PRESENCE_OFFLINE
        silence_ms = (now - self.last_seen) * 1000.0
        if silence_ms > heartbeat_timeout_ms:
            return PRESENCE_STALE
        return PRESENCE_ONLINE

    def to_public_dict(self, now: float, heartbeat_timeout_ms: int) -> dict:
        """The roster view handed to an agent by ``mcp_rpc_list_machine``."""
        return {
            "machine_id": self.machine_id,
            "presence": self.presence(now, heartbeat_timeout_ms),
            "connected": self.connection_id is not None,
            "last_seen_epoch_ms": int(self.last_seen * 1000),
            "session_id": self.session_id,
            "agent_id": self.agent_id,
        }


class MachineStore(Protocol):
    """The roster interface.

    An implementation MUST be safe for concurrent use — the websocket
    reader, the heartbeat monitor and the agent tools all touch it.
    """

    def upsert(self, machine_id: str, connection_id: str, now: float) -> MachineRecord:
        """Record a fresh (or reconnected) machine."""

    def heartbeat(self, machine_id: str, now: float) -> None:
        """Refresh the liveness clock of a machine."""

    def mark_offline(self, machine_id: str, connection_id: str) -> None:
        """Record that the socket of the machine is gone.

        The ``connection_id`` guards against a stale socket clearing the
        state of a NEWER connection of the same machine (a reconnect that
        raced the close of the old socket).
        """

    def get(self, machine_id: str) -> MachineRecord | None:
        """One machine, or ``None`` when the center does not know it."""

    def roster(self) -> list[MachineRecord]:
        """Every enrolled machine, ordered by ``machine_id``."""

    def bind_session(
        self, machine_id: str, session_id: str, agent_id: str
    ) -> None:
        """Record the active session of a machine."""

    def clear_session(self, machine_id: str, session_id: str) -> None:
        """Release the session of a machine (guarded by the session id)."""


class InMemoryMachineStore:
    """The in-memory roster of the v0.1 POC tier."""

    def __init__(self) -> None:
        self._machines: dict[str, MachineRecord] = {}
        self._lock = threading.RLock()

    def upsert(self, machine_id: str, connection_id: str, now: float) -> MachineRecord:
        with self._lock:
            record = self._machines.get(machine_id)
            if record is None:
                record = MachineRecord(machine_id=machine_id)
                self._machines[machine_id] = record
            record.connection_id = connection_id
            record.connected_at = now
            record.last_seen = now
            return record

    def heartbeat(self, machine_id: str, now: float) -> None:
        with self._lock:
            record = self._machines.get(machine_id)
            if record is not None:
                record.last_seen = now

    def mark_offline(self, machine_id: str, connection_id: str) -> None:
        with self._lock:
            record = self._machines.get(machine_id)
            if record is None:
                return
            if record.connection_id != connection_id:
                # A newer connection won the race — the stale close must
                # NOT clear the live state.
                return
            record.connection_id = None
            record.session_id = None
            record.agent_id = None

    def get(self, machine_id: str) -> MachineRecord | None:
        with self._lock:
            return self._machines.get(machine_id)

    def roster(self) -> list[MachineRecord]:
        with self._lock:
            return sorted(self._machines.values(), key=lambda r: r.machine_id)

    def bind_session(self, machine_id: str, session_id: str, agent_id: str) -> None:
        with self._lock:
            record = self._machines.get(machine_id)
            if record is not None:
                record.session_id = session_id
                record.agent_id = agent_id

    def clear_session(self, machine_id: str, session_id: str) -> None:
        with self._lock:
            record = self._machines.get(machine_id)
            if record is None:
                return
            if record.session_id != session_id:
                return
            record.session_id = None
            record.agent_id = None


def now_seconds() -> float:
    """The wall clock, in epoch seconds."""
    return time.time()


__all__ = [
    "InMemoryMachineStore",
    "MachineRecord",
    "MachineStore",
    "PRESENCE_OFFLINE",
    "PRESENCE_ONLINE",
    "PRESENCE_STALE",
    "now_seconds",
]
