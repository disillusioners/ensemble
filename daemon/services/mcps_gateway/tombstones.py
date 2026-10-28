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

THE REASON SETS
---------------

This module owns the vocabulary of the end contract. The sets below are
DISJOINT BY CONSTRUCTION:

* the RESPONSE-borne reasons — the value of ``why_not_ended`` in the
  agent-facing end envelope: :data:`REASON_EXPIRED`,
  :data:`REASON_ALREADY_ENDED`, :data:`REASON_UNKNOWN_SESSION` and
  :data:`REASON_MACHINE_REFUSED`;
* the LEDGER-only reasons — the value of ``error_code`` in an audit row
  that records a release the MACHINE did not confirm:
  :data:`LEDGER_MACHINE_UNREACHABLE` and :data:`LEDGER_MACHINE_TIMEOUT`;
* the v0.2 WIRE reasons — the value of ``reason`` in the Direction-A
  termination ack (SPEC v0.2 §6.10): :data:`CENTER_WIRE_REASONS`. It
  CARRIES the four response-borne reasons (a machine may ask about a
  session the agent already knows about) plus the two centre-cause
  tokens (:data:`REASON_REAPED_BY_CENTER`, :data:`REASON_SUPERSEDED`).
  It shares NO member with the LEDGER-only set.

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
from dataclasses import dataclass, replace

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

# ── the v0.2 WIRE reasons (the value of the Direction-A ``reason``) ────────
#
# The v0.2 termination receipt answers with a ``reason`` member (never a
# ``why_not_ended``): the machine reads it to decide whether it may
# RELEASE its local lock. The vocabulary below is the CENTRE-CAUSE half
# of it — the tokens the CENTRE mints. A machine-sent ``reason_code`` is
# NEVER validated against a set (liberal acceptance) and is echoed back
# VERBATIM; these tokens are the ones the CENTRE itself puts on the wire.

#: The centre's sweep reaped the session (its TTL or its idle window
#: passed) before the machine asked for the end.
REASON_REAPED_BY_CENTER = "reaped_by_center"

#: The session was superseded — the centre released it because a NEWER
#: connection or an explicit agent-requested end took its place.
REASON_SUPERSEDED = "superseded"

#: Every reason the CENTRE may put on the wire in a Direction-A ``reason``.
#:
#: The two LEDGER-only tokens are NOT here: they describe the centre's
#: own release path and must never reach a response (the disjointness
#: invariant below).
CENTER_WIRE_REASONS: frozenset[str] = frozenset(
    {
        REASON_ALREADY_ENDED,
        REASON_EXPIRED,
        REASON_UNKNOWN_SESSION,
        REASON_MACHINE_REFUSED,
        REASON_REAPED_BY_CENTER,
        REASON_SUPERSEDED,
    }
)

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

#: The default CAP of the tombstone store — how many ended sessions are
#: remembered. The oldest is evicted first.
#:
#: A named constant, not a bare literal in the signature: the runbook
#: pins this number (``docs/mcps-gateway.md`` §4.5) and the test must
#: not re-type it (a re-typed literal drifts from the code it claims
#: to pin).
DEFAULT_TOMBSTONE_MAX_ENTRIES = 512

#: The default AGE HORIZON of the tombstone store, in seconds — an entry
#: older than this is pruned and the honest answer reverts to
#: ``unknown_session``. Same reasoning as the cap above.
DEFAULT_TOMBSTONE_MAX_AGE_SECONDS = 3_600.0


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
        machine_notified: Whether the MACHINE was told about the end.
            ``True`` when it acknowledged it (or refused it, or already
            held no such session) — the machine answered. ``False``
            when the centre released the session on its own (the
            machine was unreachable, or the centre's deadline fired) or
            when the session died with the socket.

            It is FALSE until the sweep's forward completes (the
            tombstone is written BEFORE the forward — that is the
            two-pass invariant), so a late end that races the sweep
            sees the pre-forward value.
    """

    session_id: str
    reason: str
    at: float
    agent_id: str
    instance_id: str = ""
    machine_id: str = ""
    cause: str = ""
    machine_notified: bool = False


class TombstoneStore:
    """The bounded, in-memory memory of the ended sessions.

    The store is safe for concurrent use: the sweep, the agent tools and
    the connection teardown all touch it (the same posture as
    ``InMemoryMachineStore`` and ``SessionManager``).
    """

    def __init__(
        self,
        *,
        max_entries: int = DEFAULT_TOMBSTONE_MAX_ENTRIES,
        max_age_seconds: float = DEFAULT_TOMBSTONE_MAX_AGE_SECONDS,
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

    def mark_notified(self, session_id: str, notified: bool) -> None:
        """Record whether the MACHINE was told about an ended session.

        The sweep writes its tombstone BEFORE the forward (that is the
        two-pass invariant — the tombstone must exist before any await),
        so the outcome of the forward is known only afterwards. This
        records it, so a LATE end can answer truthfully whether the
        machine was notified.
        """
        with self._lock:
            for index, entry in enumerate(self._entries):
                if entry.session_id != session_id:
                    continue
                self._entries[index] = replace(entry, machine_notified=notified)
                return

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

    def get(self, session_id: str, now: float | None = None) -> SessionTombstone | None:
        """The tombstone of ONE session id, or ``None``.

        The v0.2 termination receipt needs it: the machine asks about a
        session by id (the id rides the ENVELOPE), and the answer
        distinguishes "the sweep reaped it" (``expired``) from "it was
        ended" — the two produce DIFFERENT wire reasons
        (``reaped_by_center`` vs ``already_ended``).

        The lookup is by SESSION id, never by agent: the machine knows
        the session id it minted, and the centre must not turn the
        answer into an oracle for somebody else's session — the id it
        is given is the id the machine itself minted.
        """
        with self._lock:
            if now is not None:
                self._prune(now)
            for entry in self._entries:
                if entry.session_id == session_id:
                    return entry
            return None

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


__all__ = [
    "CAUSE_IDLE",
    "CAUSE_TTL",
    "CENTER_WIRE_REASONS",
    "DEFAULT_TOMBSTONE_MAX_AGE_SECONDS",
    "DEFAULT_TOMBSTONE_MAX_ENTRIES",
    "EXPIRY_CAUSES",
    "LEDGER_MACHINE_TIMEOUT",
    "LEDGER_MACHINE_UNREACHABLE",
    "LEDGER_ONLY_REASONS",
    "REASON_ALREADY_ENDED",
    "REASON_EXPIRED",
    "REASON_MACHINE_REFUSED",
    "REASON_REAPED_BY_CENTER",
    "REASON_SUPERSEDED",
    "REASON_UNKNOWN_SESSION",
    "RESPONSE_REASONS",
    "SessionTombstone",
    "TombstoneStore",
]
