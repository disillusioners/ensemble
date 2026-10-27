"""Per-boot quarantine registry + init-path circuit breaker for MCP servers.

THE ONE HOME for the C1/C2 state machine of the
``soak-gate-mcp-warmup`` commission (decisions §3e E4/E5/E6, §3f F1-F5,
§3h H3/H4/H5/H6, §3g G1-G6). It answers three questions for the whole
daemon:

* **Is this MCP server allowed to be initialised right now?** —
  ``is_quarantined(name)``. A quarantined server is skipped at EVERY
  consult site (bootstrap, warmup-pool registration, warm path, cold
  discovery, cold spawn) — the ``is_available()`` classmethod of
  ``daemon/mcp/builtin_servers/base.py`` is the seam that carries the
  answer, and it consults THIS module.
* **Why is it quarantined, since when, and can it come back?** —
  ``quarantine_entries()`` / ``state_snapshot()``. The entries feed the
  ``detail.mcp`` advisory map of the ``/readyz`` payload.
* **Did the DNS pre-check resolve the host the child will actually
  use?** — ``dns_precheck(url)``.

MODULARITY (W-4). This module is a LEAF: its module-level imports are
``daemon.sources.circuit_breaker`` + the standard library ONLY. The
``daemon.services.promote_txn_hold`` import (the C4 journal-suppression
predicate) and the ``daemon.tools.upgrade_journal`` import (the journal
WRITE) are **function-local**, so importing this module from anywhere
cannot create an import cycle.

THE BREAKER (E4). There is exactly ONE ``CircuitBreaker`` class in the
codebase (``daemon/sources/circuit_breaker.py:20``). This module reuses
it — it does NOT define a second implementation. The init-path breakers
are NEW INSTANCES with their own parameters (the tool-call
``ResilienceManager`` breakers are a different layer and are not reused):

* ``failure_threshold = 3`` (the default failure count that trips the
  quarantine),
* ``recovery_timeout = 300.0`` seconds — the operator-visible recovery
  window: after it elapses, the breaker's half-open single-probe guard
  lets ONE probe through, so a recovered server returns **without a
  daemon restart**.

``CircuitBreaker.reset()`` (``daemon/sources/circuit_breaker.py:114-124``)
is the ONLY unsynchronised mutator on the breaker (it writes ``state``,
``failure_count`` and ``_probe_in_flight`` WITHOUT taking the lock,
unlike ``record_success`` / ``record_failure`` at ``:78-108``). **This
module NEVER calls ``reset()``** — the manual reset path
(``clear_quarantine``) REPLACES the breaker object instead of mutating it
in place. The prohibition is pinned by
``tests/unit/tools/test_mcp_init_guard.py``.

FAILURE-CLASS TAXONOMY (the classes this module records):

=========================  =================================  ==========
class                      meaning                            quarantine
=========================  =================================  ==========
``dns_nxdomain``           NXDOMAIN — the name does not exist   IMMEDIATE
``dns_servfail``           SERVFAIL / no A/AAAA answer          threshold 3
``resolver_timeout``       the resolver did not answer in time  NEVER
``init_timeout``           the child's init handshake timed out threshold 3
``init_failed``            the child's init failed otherwise    threshold 3
=========================  =================================  ==========

``dns_nxdomain`` is **threshold-1** (leader-ratified deviation): it is a
DEFINITIVE authoritative answer, so a dead host is quarantined after
~2.5 s instead of ~180 s. ``dns_servfail`` and the init failures count
toward the threshold of 3. ``resolver_timeout`` is NOT
quarantine-eligible — a resolver hiccup says nothing about the host and
must never permanently disable a server; the attempt PROCEEDS (the
child does its own resolution).

THE DNS PRE-CHECK IS A PROBE, NOT A PIN (F1). It resolves the host once,
records the OUTCOME, and **DISCARDS the address** — the address is never
written anywhere (not to the registry, not to the DB, not to the child
env). The child still performs its own normal resolution. It runs OFF
the event loop (``asyncio.to_thread``) and is bounded at
``DNS_PRECHECK_TIMEOUT_S = 2.0`` seconds (requirements §4.5 P5).

THE JOURNAL WRITE IS SUPPRESSED DURING A LIVE PROMOTE (H3a / BLOCKING 4).
The daemon-side state-transition journal write is an UNLOCKED
read→mutate→whole-document write to the promote-owned
``releases/state.json``; a daemon-side write during a live promote txn
would take a stale snapshot and DROP ``in_flight`` — releasing the A3
deferred resumes mid-promote. The write is therefore SUPPRESSED while
``promote_txn_live()`` is true and BUFFERED in memory (one entry per
``server`` + ``state``, newest wins); the buffer is flushed when the
predicate flips false or at graceful shutdown. A buffered entry is never
silently dropped.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import urlsplit

from daemon.sources.circuit_breaker import CircuitBreaker

logger = logging.getLogger(__name__)


# ── the pinned parameters ──────────────────────────────────────────────

#: Init failures that trip the quarantine (the breaker threshold).
INIT_FAILURE_THRESHOLD: int = 3

#: The recovery window — the operator-visible "a quarantined server
#: returns WITHOUT a restart after this long" contract (arch-pinned).
INIT_RECOVERY_TIMEOUT_S: float = 300.0

#: The DNS pre-check bound (requirements §4.5 P5 — the SAME figure
#: everywhere; the previously stated "≤ ~2.5 s" is superseded by this).
DNS_PRECHECK_TIMEOUT_S: float = 2.0


# ── the failure classes ────────────────────────────────────────────────

FAILURE_CLASS_DNS_NXDOMAIN = "dns_nxdomain"
FAILURE_CLASS_DNS_SERVFAIL = "dns_servfail"
FAILURE_CLASS_RESOLVER_TIMEOUT = "resolver_timeout"
FAILURE_CLASS_INIT_TIMEOUT = "init_timeout"
FAILURE_CLASS_INIT_FAILED = "init_failed"

#: The classes that trip the quarantine at the THRESHOLD (3).
THRESHOLD_CLASSES = frozenset(
    {
        FAILURE_CLASS_DNS_SERVFAIL,
        FAILURE_CLASS_INIT_TIMEOUT,
        FAILURE_CLASS_INIT_FAILED,
    }
)

#: The class that trips the quarantine IMMEDIATELY (threshold-1).
IMMEDIATE_CLASSES = frozenset({FAILURE_CLASS_DNS_NXDOMAIN})

#: The class that is NEVER quarantine-eligible (a resolver hiccup).
NON_QUARANTINE_CLASSES = frozenset({FAILURE_CLASS_RESOLVER_TIMEOUT})

#: The classes a server can RECOVER from without a daemon restart (the
#: breaker's half-open probe after ``INIT_RECOVERY_TIMEOUT_S``). The
#: mapping is the ``recoverable`` field of the ``detail.mcp`` surface —
#: derived at READ time from the class, never restated per server.
RECOVERABLE_BY_CLASS: dict[str, bool] = {
    FAILURE_CLASS_DNS_NXDOMAIN: True,
    FAILURE_CLASS_DNS_SERVFAIL: True,
    FAILURE_CLASS_RESOLVER_TIMEOUT: True,
    FAILURE_CLASS_INIT_TIMEOUT: True,
    FAILURE_CLASS_INIT_FAILED: True,
}

#: The published states (decisions §3h H4).
STATE_OK = "ok"
STATE_DEGRADED = "degraded"
STATE_QUARANTINED = "quarantined"

#: The journal event name — one line per STATE TRANSITION.
JOURNAL_EVENT = "mcp_server_state"


class McpServerQuarantined(RuntimeError):
    """Raised when an init is refused because the server is quarantined.

    A distinct type so the callers (the warmup pool's two failure
    loops) can tell "this server is disabled for this boot" from "this
    spawn failed" and log the former at INFO (not ERROR) — a quarantined
    server is an EXPECTED state, not a fault.
    """

    def __init__(self, server: str, reason: str) -> None:
        super().__init__(f"MCP server '{server}' is quarantined ({reason})")
        self.server = server
        self.reason = reason


class McpInitRefused(RuntimeError):
    """Raised when the DNS pre-check refuses the init (no child spawned)."""

    def __init__(self, server: str, error_class: str, target: str) -> None:
        super().__init__(
            f"MCP server '{server}' init refused by the DNS pre-check "
            f"({error_class}: {target})"
        )
        self.server = server
        self.error_class = error_class
        self.target = target


# ── the per-server state ───────────────────────────────────────────────


@dataclass
class ServerState:
    """The per-boot state of ONE MCP server.

    ``state`` is one of ``ok`` / ``degraded`` / ``quarantined``.
    ``since`` is the ISO stamp of the CURRENT state (not of the boot).
    ``attempts`` counts the init attempts of this boot.
    ``retry_at`` is the monotonic deadline of the next recovery probe
    (only meaningful for an immediate — NXDOMAIN — quarantine).
    """

    server: str
    state: str = STATE_OK
    reason: Optional[str] = None
    since: Optional[str] = None
    attempts: int = 0
    last_error_class: Optional[str] = None
    breaker_state: Optional[str] = None
    retry_at: Optional[float] = None
    probe_claimed: bool = False

    def as_dict(self) -> dict[str, Any]:
        """The published projection (``detail.mcp.<server>``)."""
        return {
            "state": self.state,
            "reason": self.reason,
            "since": self.since,
            "init_attempts": self.attempts,
            "last_error_class": self.last_error_class,
            "breaker_state": self.breaker_state,
            "recoverable": self.recoverable,
        }

    @property
    def recoverable(self) -> bool:
        """Can this server come back WITHOUT a daemon restart?

        Derived at READ time from the failure class (H4): every class the
        guard records is recoverable through the breaker's half-open
        probe after ``INIT_RECOVERY_TIMEOUT_S`` (or an operator reset).
        """
        if self.state == STATE_OK:
            return True
        return RECOVERABLE_BY_CLASS.get(self.last_error_class or "", True)


# ── module state (per-process ⇒ per-boot) ──────────────────────────────

_SERVERS: dict[str, ServerState] = {}
_BREAKERS: dict[str, CircuitBreaker] = {}
#: The bounded suppression buffer — one entry per (server, state); the
#: newest wins (H3a). Flushed on txn close / graceful shutdown.
_JOURNAL_BUFFER: dict[tuple[str, str], str] = {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure(server: str) -> ServerState:
    entry = _SERVERS.get(server)
    if entry is None:
        entry = ServerState(server=server, since=_now_iso())
        _SERVERS[server] = entry
    return entry


def _breaker(server: str) -> CircuitBreaker:
    """The per-server init breaker (a NEW instance, never a shared one).

    ``CircuitBreaker.reset()`` is never called (S11) — a fresh instance
    is the only way this module clears a breaker.
    """
    breaker = _BREAKERS.get(server)
    if breaker is None:
        breaker = CircuitBreaker(
            failure_threshold=INIT_FAILURE_THRESHOLD,
            recovery_timeout=INIT_RECOVERY_TIMEOUT_S,
        )
        _BREAKERS[server] = breaker
    return breaker


# ── the registry surface ───────────────────────────────────────────────


def is_quarantined(server: str) -> bool:
    """Is this server disabled for THIS boot?

    The single predicate the ``is_available()`` seam consults. A
    quarantined server is skipped at EVERY consult site — no DB row, no
    pool registration, no warm, no cold discovery, no cold spawn.
    """
    entry = _SERVERS.get(server)
    return entry is not None and entry.state == STATE_QUARANTINED


def quarantine_entries() -> list[dict[str, Any]]:
    """The quarantine entries (newest-first is NOT guaranteed — the map
    order is the insertion order)."""
    return [
        {
            "server": entry.server,
            "reason": entry.reason,
            "since": entry.since,
            "attempts": entry.attempts,
            "last_error_class": entry.last_error_class,
            "breaker_state": entry.breaker_state,
        }
        for entry in _SERVERS.values()
        if entry.state == STATE_QUARANTINED
    ]


def server_state(server: str) -> Optional[ServerState]:
    """The raw state entry of one server (``None`` when never touched)."""
    return _SERVERS.get(server)


def state_snapshot() -> dict[str, dict[str, Any]]:
    """The published projection of every server the guard knows about.

    The ``detail.mcp`` surface consumes this. Servers the guard has
    never seen are absent — the caller (the readiness wiring) merges the
    builtin registry's names in so every built-in is published.
    """
    return {name: entry.as_dict() for name, entry in _SERVERS.items()}


def quarantine_server(
    server: str,
    reason: str,
    error_class: str | None = None,
) -> None:
    """Quarantine a server for THIS boot (the explicit entry point).

    The equivalent of a breach of the breaker — used by an operator
    path or a test. The entry carries the machine class in ``reason``
    and (optionally) the failure class in ``error_class``.

    The recovery window is armed the same way the failure path arms it:
    the server returns via the breaker's single recovery probe after
    ``INIT_RECOVERY_TIMEOUT_S`` (or immediately after
    ``clear_quarantine``).
    """
    entry = _ensure(server)
    entry.state = STATE_QUARANTINED
    entry.reason = reason
    entry.since = _now_iso()
    entry.last_error_class = error_class or reason
    entry.retry_at = time.monotonic() + INIT_RECOVERY_TIMEOUT_S
    entry.probe_claimed = False
    entry.breaker_state = _breaker(server).get_state()
    logger.warning(
        "MCP server '%s' QUARANTINED for this boot (reason=%s) — recovery "
        "probe after %.0fs",
        server,
        reason,
        INIT_RECOVERY_TIMEOUT_S,
    )
    _emit_transition(
        server,
        STATE_QUARANTINED,
        f"class={entry.last_error_class} reason={reason} "
        f"recovery_timeout_s={INIT_RECOVERY_TIMEOUT_S}",
    )


def clear_quarantine(server: str) -> bool:
    """Manually clear a server's quarantine (the operator reset path).

    Mirrors ``ResilienceManager.reset_circuit`` — but it goes through the
    lock-protected path: the breaker object is REPLACED (never mutated in
    place), because ``CircuitBreaker.reset()`` is the only
    unsynchronised mutator and must never be called concurrently with a
    half-open probe (S11).

    Returns True when a quarantine was actually cleared.
    """
    entry = _SERVERS.get(server)
    if entry is None or entry.state != STATE_QUARANTINED:
        return False
    entry.state = STATE_OK
    entry.reason = None
    entry.since = _now_iso()
    entry.last_error_class = None
    entry.retry_at = None
    entry.probe_claimed = False
    entry.breaker_state = CircuitBreaker(
        failure_threshold=INIT_FAILURE_THRESHOLD,
        recovery_timeout=INIT_RECOVERY_TIMEOUT_S,
    ).get_state()
    # Replace the breaker object — never call ``reset()`` on it.
    _BREAKERS[server] = CircuitBreaker(
        failure_threshold=INIT_FAILURE_THRESHOLD,
        recovery_timeout=INIT_RECOVERY_TIMEOUT_S,
    )
    _emit_transition(server, STATE_OK, "operator reset cleared the quarantine")
    return True


def reset_for_tests() -> None:
    """Clear ALL module state (the registry, the breakers, the buffer).

    TEST-ONLY API (W-4a): the registry is per-process state, so without
    an explicit reset a test that quarantines a server leaks that state
    into every later test in the same process. Production code never
    calls this.
    """
    _SERVERS.clear()
    _BREAKERS.clear()
    _JOURNAL_BUFFER.clear()


# ── the journal write (suppressed + buffered during a live promote) ────


def _promote_txn_live() -> bool:
    """Is a promote transaction live right now? (requirements §4.7 P7).

    LAZY import — ``daemon.services.promote_txn_hold`` pulls the tools
    package in lazily, and this module must stay importable from
    anywhere without creating a cycle.
    """
    try:
        from daemon.services import promote_txn_hold

        return bool(promote_txn_hold.promote_txn_live())
    except Exception:  # noqa: BLE001 — the predicate must never raise
        return False


def _resolve_install_dir() -> Any:
    """The install root whose ``releases/state.json`` is written.

    Reuses the hold module's resolution (it owns the SELF-ENV topology)
    and falls back to the tools helpers directly.
    """
    try:
        from daemon.services import promote_txn_hold

        install_dir = getattr(promote_txn_hold, "_resolve_install_root", None)
        if callable(install_dir):
            resolved = install_dir()
            if resolved is not None:
                return resolved
    except Exception:  # noqa: BLE001
        pass
    try:
        from daemon.tools.upgrade_tools import (
            _canonical_install_root,
            _resolve_install_dir,
            _self_env_marker,
        )

        resolved = _resolve_install_dir(_self_env_marker())
        if resolved is not None:
            return resolved
        return _canonical_install_root("agents-ensemble")
    except Exception:  # noqa: BLE001
        return None


def _write_journal(server: str, state: str, detail: str) -> None:
    """Append ONE history line to the promote journal. Never raises."""
    try:
        from daemon.tools import upgrade_journal as _uj

        install_dir = _resolve_install_dir()
        if install_dir is None:
            logger.debug(
                "MCP state transition not journaled (no install dir): "
                "server=%s state=%s %s",
                server,
                state,
                detail,
            )
            return
        _uj.journal_history_append(
            install_dir,
            JOURNAL_EVENT,
            f"server={server} state={state} {detail}",
        )
    except Exception as exc:  # noqa: BLE001 — observability never breaks init
        logger.warning(
            "MCP state-transition journal write failed (server=%s state=%s): %s",
            server,
            state,
            exc,
        )


def _emit_transition(server: str, state: str, detail: str) -> None:
    """Emit the journal line for a STATE TRANSITION.

    SUPPRESSED while a promote txn is live (H3a / BLOCKING 4): the entry
    is buffered (one per ``server`` + ``state``, newest wins) and flushed
    when the predicate flips false or at graceful shutdown. A buffered
    entry is NEVER dropped.
    """
    if _promote_txn_live():
        _JOURNAL_BUFFER[(server, state)] = detail
        return
    # The txn is not live — flush whatever was buffered while it WAS
    # live before writing the new entry (the flush-on-close rule).
    if _JOURNAL_BUFFER:
        flush_buffered_transitions()
    _write_journal(server, state, detail)


def flush_buffered_transitions() -> int:
    """Flush the suppressed journal entries. Returns the number flushed.

    Called when the promote-txn predicate flips false and from the
    daemon's graceful-shutdown path. NEVER drops an entry: a flush
    failure is logged and the entry is put back.
    """
    if not _JOURNAL_BUFFER:
        return 0
    entries = list(_JOURNAL_BUFFER.items())
    _JOURNAL_BUFFER.clear()
    flushed = 0
    for (server, state), detail in entries:
        try:
            _write_journal(server, state, detail)
            flushed += 1
        except Exception as exc:  # noqa: BLE001 — defensive
            logger.warning(
                "MCP buffered-transition flush failed (server=%s state=%s): %s",
                server,
                state,
                exc,
            )
            _JOURNAL_BUFFER[(server, state)] = detail
    return flushed


def buffered_transition_count() -> int:
    """The number of buffered (suppressed) journal entries."""
    return len(_JOURNAL_BUFFER)


# ── the state transitions ──────────────────────────────────────────────


def _set_state(entry: ServerState, state: str, reason: Optional[str]) -> bool:
    """Set the state; return True when this was a STATE TRANSITION.

    A reason change INSIDE the same state is NOT a transition (it does
    not re-stamp ``since`` and does not emit a journal line) — the
    journal carries one line per STATE TRANSITION, never per attempt.
    """
    if entry.state == state:
        if reason is not None:
            entry.reason = reason
        return False
    entry.state = state
    entry.reason = reason
    entry.since = _now_iso()
    return True


async def can_attempt(server: str) -> bool:
    """MAY an init attempt be made for this server right now?

    A PURE query — it never claims the recovery probe and never mutates
    the breaker. The spawn path uses :func:`claim_attempt` (which does
    claim the probe); observers (the readiness surface, the tests) use
    this one.

    * Not quarantined → True (the ordinary path).
    * Quarantined → True only once the recovery window
      (``INIT_RECOVERY_TIMEOUT_S``) has elapsed AND no recovery probe is
      already in flight.
    """
    entry = _SERVERS.get(server)
    if entry is None or entry.state != STATE_QUARANTINED:
        return True
    if entry.retry_at is not None:
        if time.monotonic() < entry.retry_at:
            return False
        if entry.probe_claimed:
            return False
    return True


async def claim_attempt(server: str) -> bool:
    """Claim the right to make an init attempt (the SPAWN path's gate).

    Same predicate as :func:`can_attempt` PLUS the probe claim: the
    breaker's own gate (OPEN → HALF_OPEN, claiming the CR-4 single
    probe slot) and the entry's ``probe_claimed`` flag.

    A quarantined server is NEVER spawned outside a recovery probe —
    the prevention (no spawn) is the primary fix; the probe is how it
    comes back without a restart.
    """
    entry = _SERVERS.get(server)
    if entry is None or entry.state != STATE_QUARANTINED:
        return True

    breaker = _breaker(server)
    # The breaker's own gate: in the OPEN state it returns False until
    # the recovery window elapses, then transitions to HALF_OPEN and
    # claims the single probe slot (the CR-4 guard). In the CLOSED state
    # (the NXDOMAIN-immediate quarantine, which did not count toward the
    # threshold) it returns True WITHOUT claiming a probe — the entry's
    # own window + ``probe_claimed`` flag provides the single-probe
    # discipline there.
    breaker_allowed = await breaker.can_execute()
    if not breaker_allowed:
        return False
    # The entry's recovery window ALWAYS governs the probe (it is set
    # when the quarantine is entered and re-armed after every failed
    # probe), so a quarantined server is never spawned outside a
    # recovery probe.
    if entry.retry_at is not None:
        if time.monotonic() < entry.retry_at:
            return False
        if entry.probe_claimed:
            return False
        entry.probe_claimed = True
    return True


async def record_failure(server: str, error_class: str) -> str:
    """Record an init failure; return the resulting state.

    The class decides the routing (see the taxonomy in the module
    docstring):

    * ``dns_nxdomain`` → IMMEDIATE quarantine (threshold-1),
    * ``resolver_timeout`` → degraded, NEVER quarantined,
    * everything else → counted toward the threshold of 3.
    """
    entry = _ensure(server)
    entry.attempts += 1
    entry.last_error_class = error_class
    breaker = _breaker(server)

    if error_class not in NON_QUARANTINE_CLASSES:
        # The breaker counts INIT failures. ``resolver_timeout`` is
        # EXCLUDED: a resolver hiccup says nothing about the host and
        # must never count towards the quarantine threshold.
        await breaker.record_failure()
    entry.breaker_state = breaker.get_state()

    if entry.state == STATE_QUARANTINED:
        # Already quarantined — STAY quarantined (a later failure never
        # un-quarantines a server). Only the details are refreshed and
        # the recovery window is re-armed (the probe that just failed
        # must not be retried immediately).
        entry.reason = error_class
        entry.retry_at = time.monotonic() + INIT_RECOVERY_TIMEOUT_S
        entry.probe_claimed = False
        return entry.state

    if error_class in NON_QUARANTINE_CLASSES:
        # A resolver hiccup says NOTHING about the host. It never
        # quarantines; the attempt proceeds and the child does its own
        # resolution.
        transition = _set_state(entry, STATE_DEGRADED, error_class)
        if transition:
            logger.warning(
                "MCP server '%s' degraded (class=%s, attempts=%d) — "
                "resolver timeout is NOT quarantine-eligible; the attempt "
                "proceeds",
                server,
                error_class,
                entry.attempts,
            )
            _emit_transition(
                server,
                STATE_DEGRADED,
                f"class={error_class} attempts={entry.attempts} "
                "quarantine_eligible=false",
            )
        return entry.state

    if error_class in IMMEDIATE_CLASSES:
        # Definitive authoritative answer — quarantine NOW.
        entry.retry_at = time.monotonic() + INIT_RECOVERY_TIMEOUT_S
        entry.probe_claimed = False
        transition = _set_state(entry, STATE_QUARANTINED, error_class)
        if transition:
            logger.warning(
                "MCP server '%s' QUARANTINED for this boot (class=%s, "
                "attempts=%d) — a definitive DNS answer; recovery probe "
                "after %.0fs",
                server,
                error_class,
                entry.attempts,
                INIT_RECOVERY_TIMEOUT_S,
            )
            _emit_transition(
                server,
                STATE_QUARANTINED,
                f"class={error_class} attempts={entry.attempts} "
                f"recovery_timeout_s={INIT_RECOVERY_TIMEOUT_S}",
            )
        return entry.state

    # Threshold classes.
    if breaker.get_state() == "open":
        entry.retry_at = time.monotonic() + INIT_RECOVERY_TIMEOUT_S
        entry.probe_claimed = False
        transition = _set_state(entry, STATE_QUARANTINED, error_class)
        if transition:
            logger.warning(
                "MCP server '%s' QUARANTINED for this boot (class=%s, "
                "attempts=%d, threshold=%d) — recovery probe after %.0fs",
                server,
                error_class,
                entry.attempts,
                INIT_FAILURE_THRESHOLD,
                INIT_RECOVERY_TIMEOUT_S,
            )
            _emit_transition(
                server,
                STATE_QUARANTINED,
                f"class={error_class} attempts={entry.attempts} "
                f"threshold={INIT_FAILURE_THRESHOLD} "
                f"recovery_timeout_s={INIT_RECOVERY_TIMEOUT_S}",
            )
        return entry.state

    transition = _set_state(entry, STATE_DEGRADED, error_class)
    if transition:
        logger.warning(
            "MCP server '%s' degraded (class=%s, attempts=%d/%d)",
            server,
            error_class,
            entry.attempts,
            INIT_FAILURE_THRESHOLD,
        )
        _emit_transition(
            server,
            STATE_DEGRADED,
            f"class={error_class} attempts={entry.attempts} "
            f"threshold={INIT_FAILURE_THRESHOLD}",
        )
    return entry.state


async def record_success(server: str, *, scope: str = "init") -> None:
    """Record a success.

    ``scope="init"`` — the child's init handshake succeeded: the breaker
    is reset (lock-protected ``record_success``) and the server returns
    to ``ok``.

    ``scope="dns"`` — the DNS PRE-CHECK resolved the host. This clears a
    DNS-class error marker but deliberately does NOT reset the breaker:
    the child's init can still fail afterwards, and a DNS success must
    not MASK the init failures that follow it.
    """
    entry = _ensure(server)
    breaker = _breaker(server)
    if scope == "dns":
        # The DNS leg succeeded. It clears a DNS-class DEGRADED marker,
        # but it NEVER lifts a QUARANTINE — the quarantine is lifted by
        # a successful INIT (``scope="init"``), not by a DNS answer.
        if (
            entry.state == STATE_DEGRADED
            and entry.reason
            in (
                FAILURE_CLASS_DNS_NXDOMAIN,
                FAILURE_CLASS_DNS_SERVFAIL,
                FAILURE_CLASS_RESOLVER_TIMEOUT,
            )
        ):
            _set_state(entry, STATE_OK, None)
            entry.last_error_class = None
            _emit_transition(server, STATE_OK, "dns pre-check resolved")
        entry.breaker_state = breaker.get_state()
        return

    await breaker.record_success()
    entry.breaker_state = breaker.get_state()
    entry.retry_at = None
    entry.probe_claimed = False
    transition = _set_state(entry, STATE_OK, None)
    entry.last_error_class = None
    if transition:
        logger.info(
            "MCP server '%s' recovered (state=ok, attempts=%d)",
            server,
            entry.attempts,
        )
        _emit_transition(
            server,
            STATE_OK,
            f"recovered attempts={entry.attempts}",
        )


# ── the DNS pre-check (C2) ─────────────────────────────────────────────


def classify_dns_failure(exc: BaseException) -> str:
    """Classify a resolution failure.

    ``socket.gaierror`` with ``EAI_NONAME`` (or an equivalent
    "name does not exist" answer) is the DEFINITIVE ``dns_nxdomain``
    class — it trips the quarantine immediately. Every other resolution
    failure (SERVFAIL, EAI_AGAIN, "no A/AAAA answer") is
    ``dns_servfail`` and counts toward the threshold of 3.
    """
    if isinstance(exc, socket.gaierror):
        code = getattr(exc, "errno", None)
        noname = getattr(socket, "EAI_NONAME", None)
        if code is not None and noname is not None and code == noname:
            return FAILURE_CLASS_DNS_NXDOMAIN
        # ``EAI_NODATA`` / ``EAI_AGAIN`` / anything else: the resolver
        # answered, but not with an address.
        return FAILURE_CLASS_DNS_SERVFAIL
    return FAILURE_CLASS_DNS_SERVFAIL


def _host_of(url: str) -> Optional[str]:
    """The host of a URL (``None`` when there is none to resolve)."""
    text = (url or "").strip()
    if not text:
        return None
    if "//" not in text:
        # A bare host (no scheme) — treat it as a host.
        return text.split("/", 1)[0].split(":", 1)[0] or None
    try:
        parts = urlsplit(text)
    except ValueError:
        return None
    return parts.hostname or None


async def dns_precheck(url: str) -> str:
    """Resolve the host of ``url`` OFF the event loop, bounded at 2.0 s.

    Returns the outcome class:

    * ``"ok"`` — the host resolved (the ADDRESS IS DISCARDED — never
      written anywhere, never pinned into the child's env),
    * ``"no_target"`` — there is no host to resolve (the pre-check is
      skipped),
    * ``"dns_nxdomain"`` — the name does not exist (definitive),
    * ``"dns_servfail"`` — the resolver answered without an address,
    * ``"resolver_timeout"`` — the resolver did not answer within
      ``DNS_PRECHECK_TIMEOUT_S`` (NOT quarantine-eligible).

    The pre-check is a PROBE, never a pin (F1): the resolved address is
    discarded and the child performs its own resolution.
    """
    host = _host_of(url)
    if not host:
        return "no_target"
    try:
        await asyncio.wait_for(
            asyncio.to_thread(
                socket.getaddrinfo,
                host,
                None,
                family=socket.AF_UNSPEC,
                type=socket.SOCK_STREAM,
            ),
            timeout=DNS_PRECHECK_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        # The resolver did not answer in time — a resolver hiccup, NOT a
        # statement about the host.
        return FAILURE_CLASS_RESOLVER_TIMEOUT
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 — every resolution failure is a class
        return classify_dns_failure(exc)
    # F1: the resolved address is DISCARDED here — it is never stored,
    # never logged, never written to the child's env.
    return "ok"


__all__ = [
    "DNS_PRECHECK_TIMEOUT_S",
    "FAILURE_CLASS_DNS_NXDOMAIN",
    "FAILURE_CLASS_DNS_SERVFAIL",
    "FAILURE_CLASS_INIT_FAILED",
    "FAILURE_CLASS_INIT_TIMEOUT",
    "FAILURE_CLASS_RESOLVER_TIMEOUT",
    "INIT_FAILURE_THRESHOLD",
    "INIT_RECOVERY_TIMEOUT_S",
    "JOURNAL_EVENT",
    "McpInitRefused",
    "McpServerQuarantined",
    "STATE_DEGRADED",
    "STATE_OK",
    "STATE_QUARANTINED",
    "buffered_transition_count",
    "can_attempt",
    "claim_attempt",
    "classify_dns_failure",
    "clear_quarantine",
    "dns_precheck",
    "flush_buffered_transitions",
    "is_quarantined",
    "quarantine_entries",
    "quarantine_server",
    "record_failure",
    "record_success",
    "reset_for_tests",
    "server_state",
    "state_snapshot",
]
