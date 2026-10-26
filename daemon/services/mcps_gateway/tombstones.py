"""The ended-session tombstones — the centre's memory of a session that ENDED.

WHY A TOMBSTONE EXISTS
-----------------------

The centre keeps only LIVE sessions: a session belongs to its connection
and is killed with it — there is no resumption and no "ended" record
(``daemon/routers/mcps_gateway_read.py`` says so explicitly). That was
honest while every end answer was a typed refusal, but it made a LATE
end request indistinguishable from an end request for a session that
NEVER EXISTED: both answered ``SESSION_NOT_FOUND``.

A tombstone is the small, bounded memory that lets the centre answer the
late request honestly — ``expired`` (the sweep reaped it) or
``already_ended`` (it was ended) — WITHOUT turning the answer into an
existence oracle for somebody else's session.

THE TWO DISJOINT REASON SETS
----------------------------

This module owns the vocabulary of the end contract, and the two sets
below are DISJOINT BY CONSTRUCTION:

* the RESPONSE-borne reasons — the value of ``why_not_ended`` in the
  agent-facing end envelope: :data:`REASON_EXPIRED`,
  :data:`REASON_ALREADY_ENDED`, :data:`REASON_UNKNOWN_SESSION` and
  :data:`REASON_MACHINE_REFUSED`;
* the LEDGER-only reasons — the value of ``error_code`` in an audit row
  that records a release the MACHINE did not confirm:
  :data:`LEDGER_MACHINE_UNREACHABLE` and :data:`LEDGER_MACHINE_TIMEOUT`.

A RESPONSE that carries anything outside the first set is a BUG. The
ledger-only tokens describe what happened between the centre and the
machine (the machine was unreachable, the centre's own deadline fired) —
they are NOT answers to "why did the session not end", and an agent that
branched on them would be branching on an implementation detail of the
centre's release path. The invariant is asserted in the unit tests
(``tests/unit/test_mcps_gateway_session_expiry.py``).

PROVENANCE
----------

The tombstone store, the reason taxonomy and the agent-facing end
envelope are a CENTRE-SIDE CONVENTION ported from the sibling fleet's
remote-hands centre for parity. They are NOT SPEC v0.1 text (the SPEC
mandates the typed errors ``SESSION_EXPIRED`` / ``SESSION_NOT_FOUND`` and
the server-side ownership of the session timeouts, but it says nothing
about ``why_not_ended``), and the WIRE PROTOCOL is unchanged by them:
the ``session.end`` frame keeps exactly the params it had.

IN-MEMORY, BOUNDED, BY DESIGN
------------------------------

The tombstone store is IN-MEMORY: the v0.1 roster and the session table
are in-memory too (``docs/mcps-gateway.md`` §1.2), so a daemon restart
legitimately empties them — a tombstone that survived the restart would
describe a machine the centre no longer knows. There is NO migration and
NO new table.

It is BOUNDED twice: by COUNT (the oldest entries are evicted first) and
by AGE (an entry older than the horizon is pruned, and the honest answer
reverts to ``unknown_session``). An unbounded store would be a memory
leak with a very long fuse.

OWNERSHIP
---------

The ensemble has NO workspace id, so ownership is scoped to the OWNING
AGENT: a tombstone is only ever answered to the agent that held the
session. The lookup is therefore BY AGENT — the ensemble's end request
is AGENT-ADDRESSED (it carries no session id), so the centre resolves
the agent's most recent tombstone within the horizon.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Iterable

# ── the RESPONSE-borne reasons (the value of ``why_not_ended``) ─────────────

#: The session was reaped by the centre's sweep (its TTL or its idle
#: timeout passed).
REASON_EXPIRED = "expired"

#: The session was ended (explicitly, or because the machine became
#: unreachable and the centre released it).
REASON_ALREADY_ENDED = "already_ended"

#: There is no live session and no tombstone for this agent — the
#: session never existed, or it is not this agent's session. The ONE
#: neutral answer for both cases: it must never become an existence
#: oracle for somebody else's session.
REASON_UNKNOWN_SESSION = "unknown_session"

#: The machine was reached and REFUSED the end. The session SURVIVES.
REASON_MACHINE_REFUSED = "machine_refused"

#: Every reason a response may carry in ``why_not_ended``.
RESPONSE_REASONS: frozenset[str] = frozenset(
    {
        REASON_EXPIRED,
        REASON_ALREADY_ENDED,
        REASON_UNKNOWN_SESSION,
        REASON_MACHINE_REFUSED,
    }
)

# ── the LEDGER-only reasons (the value of an audit row's ``error_code``) ──

#: The machine could not be reached — the centre released the session
#: AUTHORITATIVELY. Never a response reason.
LEDGER_MACHINE_UNREACHABLE = "machine_unreachable"

#: The centre's OWN ack deadline fired while it waited for the machine.
#: Never a response reason.
LEDGER_MACHINE_TIMEOUT = "machine_timeout"

#: Every reason that may appear in an audit row and NEVER in a response.
LEDGER_ONLY_REASONS: frozenset[str] = frozenset(
    {
        LEDGER_MACHINE_UNREACHABLE,
        LEDGER_MACHINE_TIMEOUT,
    }
)

# ── the expiry causes (why the sweep reaped the session) ──────────────────

#: The session passed its IDLE timeout (no traffic for that long).
CAUSE_IDLE = "idle"

#: The session passed its TTL ceiling.
CAUSE_TTL = "ttl"

#: The causes the sweep can record.
EXPIRY_CAUSES: frozenset[str] = frozenset({CAUSE_IDLE, CAUSE_TTL})


@dataclass(frozen=True)
class SessionTombstone:
    """One ended session, remembered.

    Attributes:
        session_id: The id of the session that ended.
        reason: ``expired`` or ``already_ended`` — the RESPONSE-borne
            vocabulary of this module (never a ledger-only token).
        at: The epoch seconds of the end.
        agent_id: The agent that held the session — the OWNER. The
            tombstone is only ever answered to this agent.
        instance_id: The instance of that agent.
        machine_id: The machine the session ran on (empty when the
            centre never learned it).
        cause: Why the session ended — ``idle`` / ``ttl`` for an
            expiry, empty for an explicit end.
    """

    session_id: str
    reason: str
    at: float
    agent_id: str
    instance_id: str = ""
    machine_id: str = ""
    cause: str = ""


class TombstoneStore:
    """The bounded, in-memory memory of the ended sessions.

    The store is safe for concurrent use: the sweep, the agent tools and
    the connection teardown all touch it (the same posture as
    ``InMemoryMachineStore`` and ``SessionManager``).
    """

    def __init__(
        self,
        *,
        max_entries: int = 512,
        max_age_seconds: float = 3_600.0,
    ) -> None:
        self._entries: list[SessionTombstone] = []
        self._max_entries = max(1, int(max_entries))
        self._max_age_seconds = max(0.0, float(max_age_seconds))
        self._lock = threading.RLock()

    def record(self, tombstone: SessionTombstone) -> None:
        """Remember one ended session.

        The store is pruned on every write (the age horizon) and the
        oldest entries are evicted when the count cap is reached.
        """
        with self._lock:
            self._prune(tombstone.at)
            self._entries.append(tombstone)
            while len(self._entries) > self._max_entries:
                self._entries.pop(0)

    def latest_for_agent(
        self, agent_id: str, now: float
    ) -> SessionTombstone | None:
        """The agent's most recent tombstone within the horizon.

        ``None`` when the agent has no remembered session (or the one it
        had is older than the horizon) — the caller then answers
        ``unknown_session``.
        """
        with self._lock:
            self._prune(now)
            latest: SessionTombstone | None = None
            for entry in self._entries:
                if entry.agent_id != agent_id:
                    continue
                if latest is None or entry.at >= latest.at:
                    latest = entry
            return latest

    def entries(self) -> list[SessionTombstone]:
        """Every remembered tombstone, oldest first (the test seam)."""
        with self._lock:
            return list(self._entries)

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def _prune(self, now: float) -> None:
        """Drop the entries that fell out of the age horizon."""
        if self._max_age_seconds <= 0:
            self._entries = []
            return
        cutoff = now - self._max_age_seconds
        self._entries = [entry for entry in self._entries if entry.at >= cutoff]


def tombstone_reason_is_response_bearing(reason: str) -> bool:
    """Is this reason one a RESPONSE may carry?

    The single place the "a response carrying anything else is a bug"
    invariant is checked at runtime.
    """
    return reason in RESPONSE_REASONS


def tombstone_reason_is_ledger_only(reason: str) -> bool:
    """Is this reason one that may appear ONLY in an audit row?"""
    return reason in LEDGER_ONLY_REASONS


def all_reasons() -> Iterable[str]:
    """Every token of the vocabulary (the test seam)."""
    return sorted(RESPONSE_REASONS | LEDGER_ONLY_REASONS)


__all__ = [
    "CAUSE_IDLE",
    "CAUSE_TTL",
    "EXPIRY_CAUSES",
    "LEDGER_MACHINE_TIMEOUT",
    "LEDGER_MACHINE_UNREACHABLE",
    "LEDGER_ONLY_REASONS",
    "REASON_ALREADY_ENDED",
    "REASON_EXPIRED",
    "REASON_MACHINE_REFUSED",
    "REASON_UNKNOWN_SESSION",
    "RESPONSE_REASONS",
    "SessionTombstone",
    "TombstoneStore",
    "all_reasons",
    "tombstone_reason_is_ledger_only",
    "tombstone_reason_is_response_bearing",
]
