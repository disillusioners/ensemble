"""Slash-command subsystem dispatcher (Phase 1 / WS-1 + WS-5 ack/GET parts).

Extensible in-process command dispatch for instance-scoped commands
(``/compact`` etc.). Parses a leading ``/name`` prefix out of incoming
user messages, looks up a registered ``CommandSpec``, applies
availability + rate-limit guards, then spawns a background task to run
the spec's handler. Rejections are answerable at ack time
(``200 state:"rejected" + reason``) or as terminal SSE phases
(emitted by the WS-2 executor).

Pattern source: ``daemon/sources/registry.py:47-159`` (register/get/list,
duplicate-raise) — mirrored for the command registry.

**WS-1 scope (this file):**

- ``parse_slash_command`` (free function) — ``//``-escape BEFORE
  ``/`` (O-B1), case-insensitive resolve, unknown / no-leading-``/``
  ⇒ ``None``.
- ``CommandRegistry`` (case-insensitive resolve, unknown → ``None``,
  duplicate raises).
- ``CommandStateRegistry`` — O10: one **active slot per instance** +
  **daemon-wide terminal ring LRU** (``max_state_per_instance``,
  default 20) with **TTL** (``state_ttl_s``, default 600). Eviction
  triggers: terminal event, TTL expiry, instance delete/terminate
  (mirrors ``manager._cleanup_instance_state``).
- ``CommandDispatcher`` — parse → registry → availability →
  pending-injections guard → rate-limit → record_start → ack →
  background task spawn. Rate-limit is checked **BEFORE** the bg
  task is spawned, which guarantees a rate-limited request never
  acquires the ExecutionGate (test-visible seam, plan 1.4).

**NOT in this slice (intentionally):**

- The ``/compact`` executor (WS-2 / executor coder). This file
  registers NOTHING builtin; the registry starts empty.
- The SSE ``command_progress`` emitter + 10s heartbeat (WS-5
  executor-side, plan 5.2). This file owns the **state store** and
  the ack/event payload TYPES the executor drives.
- The actual ExecutionGate acquisition. The executor (WS-2) takes
  the gate; the dispatcher only owns the dispatch-time guards.

The ``command_id`` + ``handler: Callable`` pair is the **O-B7
durability seam**: a future durable variant wraps ``handler`` in a
``JobItem('command')`` enqueue without touching ``CommandSpec``.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Awaitable, Callable
from uuid import uuid4


logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────
# Enums + types — WS-5 normative schema (architect §7, O8–O13)
# ─────────────────────────────────────────────────────────────────────────


class RejectionReason(str, Enum):
    """Seven rejection reasons for the WS-5 CommandAck envelope.

    The enum was closed at six values per the architect verdict
    (architecture-recommendation.md §7 / decisions.md) and gained
    the 7th value ``UNAVAILABLE`` per W-2.5 (leader-approved, 2026-08-31)
    — the per-agent availability predicate (O-B6) was always
    designed-in but the rejection-reason enum had no slot for
    "agent policy denied"; surfacing ``availability=False`` as
    ``pending_injections`` was the original workaround that the
    reviewer flagged as incorrect.

    The dispatcher produces ``terminal_instance`` (defect #2,
    2026-08-31 — the ack-time instance-status gate), ``busy`` /
    ``rate_limited`` / ``pending_injections`` / ``unavailable``; the
    executor (WS-2) keeps ``terminal_instance`` as a defense-in-depth
    guard and is responsible for producing
    ``compaction_disabled`` and ``quiescence_timeout`` as terminal
    phase details.
    """

    TERMINAL_INSTANCE = "terminal_instance"
    BUSY = "busy"
    RATE_LIMITED = "rate_limited"
    PENDING_INJECTIONS = "pending_injections"
    UNAVAILABLE = "unavailable"  # W-2.5 (leader-approved 2026-08-31)
    COMPACTION_DISABLED = "compaction_disabled"
    QUIESCENCE_TIMEOUT = "quiescence_timeout"


# ─────────────────────────────────────────────────────────────────────────
# Ack-time instance-status gate (defect #2, 2026-08-31 e2e gate)
# ─────────────────────────────────────────────────────────────────────────


# The compact-specific reject set (compact-on-COMPLETED, 2026-08-31 —
# .agents/shared/planning/compact-on-completed/architecture-recommendation.md
# step 1). LOCAL to the dispatcher, deliberately NOT derived from the
# canonical ``daemon.constants.TERMINAL_INSTANCE_STATUSES`` (that set
# keeps ``completed`` and its 5+ downstream consumers stay
# byte-untouched). ``/compact`` is REFUSED on ``terminated`` / ``error``
# / ``failed`` — their revive semantics and error-surfaces were never
# assessed for compaction and the O-B4-era caution still applies — but
# is COMPACT-ELIGIBLE on ``completed``: the C1 Variant A persistence
# recipe (two ``aupdate_state`` calls WITHOUT ``as_node`` — ``next``
# stays ``()``) structurally eliminates the O-B4 revive-brick for that
# status, so revive-on-send (instance_messaging.py:1486-1510 /
# :3580-3601) runs the agent normally. ``completed`` therefore falls
# THROUGH the gate below to availability → pending-injections →
# rate-limit → record_start, while REMAINING TERMINAL for every other
# consumer of the canonical set (queue-stats short-circuit, agent-tool
# terminal-revive, recovery sweeps, ...). Single source shared with the
# executor's defense-in-depth guard (compact_executor) so the two
# gates can never drift apart.
COMPACT_REJECT_STATUSES: frozenset[str] = frozenset(
    {"terminated", "error", "failed"}
)

# W-1.2 pinned guidance copy (plan S-14 / architect §5) — the executor
# has carried this exact string since the C1 fix; the FE renders the
# ack ``detail`` VERBATIM for ``terminal_instance`` (chat.component.ts
# ``rejectionCopy``). Defect #2 fix: the SAME copy now answers at ack
# time, and the executor reuses this constant so the copy cannot drift.
# Scope (compact-on-COMPLETED, 2026-08-31): this copy is now produced
# ONLY for the ``COMPACT_REJECT_STATUSES`` triple above — "Send a
# message to start a new turn" is exactly right for them (only a real
# message revives them). ``completed`` never reaches this rejection
# anymore.
TERMINAL_INSTANCE_GUIDANCE: str = (
    "Send a message to start a new turn, then /compact."
)


class CommandPhase(str, Enum):
    """Phase machine — emitted via SSE; unchanged by C1 amendment."""

    WAITING = "waiting"
    IN_PROGRESS = "in_progress"
    SUCCESS = "success"
    TIMED_OUT = "timed_out"
    FALLBACK_APPLIED = "fallback_applied"
    FAILED = "failed"


class CompactedType(str, Enum):
    """compacted_type enum — WS-5 §7 amendment (C1, 2026-08-31)."""

    SUMMARY = "summary"
    PARTIAL_SUMMARY = "partial_summary"
    TRUNCATION = "truncation"
    NOOP = "noop"


class NoopReason(str, Enum):
    """noop_reason enum — WS-5 §7.

    Cycle 2 (proactive-compaction-fix review W-4) added
    ``INJECTIONS_DOMINATE``: the engine emits
    ``compaction_type="skipped_injections_dominate"`` on the
    all-injected anti-refire path (the ``if not regular_messages:``
    block through the ``anti_refire_skip`` call).
    The executor's wire mapping translates that to
    ``compacted_type="noop"`` + ``noop_reason="injections_dominate"``
    so the FE enum contract (``CompactedType.NOOP``) is preserved
    (the raw engine string was previously leaking through the wire
    as ``compacted_type="skipped_injections_dominate"`` — outside
    the FE enum). Engine-side ``compacted_at`` stamping for the
    AUTO path is unaffected (T4/T4-ext acceptance).

    Cycle 3 (proactive-compaction-fix residual W-4.5) added
    ``PRESERVED_WITHIN_THRESHOLD``: the engine emits
    ``compaction_type="skipped_preserved_within_threshold"`` on the
    emergency-bail path when the preserved-groups token count still
    fits within ``context_window * threshold``
    (``daemon/compaction.py:2129-2138``). The executor's wire
    mapping translates that to ``compacted_type="noop"`` +
    ``noop_reason="preserved_within_threshold"`` for the SAME
    reason as ``INJECTIONS_DOMINATE`` — the raw engine string was
    previously leaking through the wire AND the user-facing
    ``/compact`` was invoking the 60s dedup stamp seam on a
    no-op. The seam is now skipped for this path (mirror of the
    other two mapped noops) and the FE enum contract is preserved.
    """

    BELOW_FLOOR = "below_floor"
    RECENTLY_COMPACTED = "recently_compacted"
    TOO_FEW_MESSAGES = "too_few_messages"
    INJECTIONS_DOMINATE = "injections_dominate"
    PRESERVED_WITHIN_THRESHOLD = "preserved_within_threshold"


# ─────────────────────────────────────────────────────────────────────────
# Parsed-command + parser
# ─────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ParsedCommand:
    """A successfully-parsed slash-command.

    Attributes:
        name: canonical (lowercase) command name, leading ``/`` stripped.
        args: remainder after the first token (may be empty).
    """

    name: str
    args: str


@dataclass(frozen=True)
class ParseResult:
    """Discriminated parser output.

    Exactly one of ``command`` or ``sanitized_text`` is set:

    - ``command`` set → user typed a slash-command with a parseable name.
    - ``sanitized_text`` set → user typed a ``//``-escape; the router
      should replace the message content with ``sanitized_text`` and
      fall through to the normal enqueue path (no command processing).
    - both ``None`` → plain text; fall through unchanged.
    """

    command: ParsedCommand | None = None
    sanitized_text: str | None = None


def parse_slash_command(text: str, escape_prefix: str = "//") -> ParseResult:
    """Parse a user message into a :class:`ParseResult`.

    **O-B1 (Slack convention, architect 2026-08-31):** the
    ``escape_prefix`` is checked BEFORE the leading ``/``. A leading
    ``//`` strips ONE ``/`` (one character less than the prefix) and
    the rest reaches the normal message branch verbatim.

    Examples (``escape_prefix='//'``)::

        '/compact'                 → ParsedCommand(name='compact', args='')
        '/compact foo'             → ParsedCommand(name='compact', args='foo')
        '/COMPACT'                 → ParsedCommand(name='compact', args='')
        '//etc/hosts'              → ParseResult(sanitized_text='/etc/hosts')
        'hello'                    → ParseResult()  (both None)
        '/'                        → ParseResult()  (slash without name)
        ''                         → ParseResult()  (empty — S4 upstream)

    Args:
        text: user-typed message content.
        escape_prefix: configured escape sequence (default ``//``).
    """
    if not text:
        return ParseResult()
    # O-B1: escape check FIRST. Strip exactly ONE '/' (one char less
    # than the prefix) so the remaining text looks like a plain path
    # that the LLM can interpret verbatim.
    if escape_prefix and text.startswith(escape_prefix):
        stripped = text[len(escape_prefix) - 1:]
        return ParseResult(sanitized_text=stripped)
    # Then leading '/'.
    if not text.startswith("/"):
        return ParseResult()
    body = text[1:]
    if not body or body.isspace():
        return ParseResult()
    parts = body.split(None, 1)
    name = parts[0]
    if not name:
        return ParseResult()
    args = parts[1] if len(parts) > 1 else ""
    return ParseResult(command=ParsedCommand(name=name.lower(), args=args))


# ─────────────────────────────────────────────────────────────────────────
# CommandSpec + CommandRegistry (WS-1.1)
# ─────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CommandSpec:
    """A registered slash-command.

    The (``command_id`` + ``handler``) tuple is the **O-B7 durability
    seam**: a future durable variant wraps ``handler`` in a
    ``JobItem('command')`` enqueue WITHOUT touching ``CommandSpec``
    (the spec's contract is "what to run" and "how fast can the user
    re-trigger it"; persistence is the caller's choice — WS-2 keeps
    things ephemeral per architect verdict).

    Attributes:
        name: canonical command name (lowercase, no leading ``/``).
            Duplicate registration raises ``ValueError`` (mirrors the
            ``daemon.sources.registry`` duplicate-raise pattern).
        description: human-readable text for autocomplete / help.
        availability: optional policy hook (O-B6). Called with the
            instance context; returns ``True`` when the command is
            allowed for that instance. ``None`` (default) = always
            allowed. Unpopulated today — executor coder will wire
            per-agent policy when needed.
        rate_limit_per_instance: minimum seconds between two ACCEPTED
            dispatches of this command on the same instance. The
            daemon-wide ``min_interval_s`` also applies; the LARGER
            of the two wins.
        handler: async callable invoked as a background task after
            the ack is returned. Signature::

                async def handler(
                    *,
                    instance_id: str,
                    args: str,
                    command_id: str,
                    context: CommandContext,
                ) -> None

            The handler is responsible for calling
            ``context.update_phase(...)`` and ``context.terminalize(...)``
            as it progresses (WS-2 executor contract).
    """

    name: str
    description: str
    availability: Callable[[Any], Awaitable[bool]] | None
    rate_limit_per_instance: int
    handler: Callable[..., Awaitable[None]]


class CommandRegistry:
    """Case-insensitive command registry.

    Mirrors ``daemon.sources.registry.SourceRegistry.register`` (raises
    on duplicate) and the ``get`` / ``list`` accessors. Unknown names
    resolve to ``None``; the dispatcher turns that into the
    ``400 UNKNOWN_COMMAND`` ack at the router layer.
    """

    def __init__(self) -> None:
        self._specs: dict[str, CommandSpec] = {}

    def register(self, spec: CommandSpec) -> None:
        """Register ``spec``. Raises ``ValueError`` on duplicate name.

        Duplicate-raise is intentional — keeps startup-time
        configuration bugs loud (mirrors
        ``SourceRegistry.register``).
        """
        key = spec.name.lower()
        if key in self._specs:
            raise ValueError(f"Command already registered: {spec.name}")
        self._specs[key] = spec

    def get(self, name: str) -> CommandSpec | None:
        """Case-insensitive resolve. ``None`` when unknown."""
        return self._specs.get(name.lower())

    def list(self) -> list[str]:
        """Canonical command names — for the 400 ``detail.available``.

        Sorted for stable wire output (FE autocomplete).
        """
        return sorted(self._specs.keys())

    def clear(self) -> None:
        """Test helper — drop every registration."""
        self._specs.clear()


# ─────────────────────────────────────────────────────────────────────────
# State registry — O10 (one active slot per instance + daemon-wide LRU)
# ─────────────────────────────────────────────────────────────────────────


@dataclass
class ActiveCommand:
    """Per-instance command snapshot — backs the GET endpoint.

    Fields:
        instance_id: target instance.
        command_id: UUIDv4 minted at ``record_start`` time. Correlates
            all events for one command (WS-5 schema).
        command: canonical command name (e.g. ``"compact"``).
        started_at: monotonic clock — start instant.
        started_at_iso: ISO-8601 UTC string captured at the SAME
            instant as ``started_at`` — the wire timestamp the SSE
            + GET endpoint surface (W-1.3 / schema pin).
        phase: current :class:`CommandPhase` value (lowercase string).
        phase_seq: monotonic per-command counter — FE dedup/reorder guard.
        last_event_at: monotonic clock — last ``update_phase`` /
            ``terminalize`` instant; drives TTL.
        last_event_at_iso: ISO-8601 UTC string captured at the SAME
            instant as ``last_event_at`` — the wire timestamp the SSE
            + GET endpoint surface (W-1.3 / schema pin).
        ttl_seconds: TTL window for GET-fallback visibility
            (mirrors the ``ttl_seconds`` field on the ack envelope).
        detail: WS-5 ``detail`` dict (``tokens_before``,
            ``tokens_after``, ``compacted_type``, ``failure_kind``,
            ``noop_reason``, ``checkpoint_id``, ``reason``).
    """

    instance_id: str
    command_id: str
    command: str
    started_at: float
    started_at_iso: str
    phase: str
    phase_seq: int
    last_event_at: float
    last_event_at_iso: str
    ttl_seconds: int
    detail: dict | None = None


class CommandStateRegistry:
    """O10 state registry — owned by :class:`CommandDispatcher`.

    Layout:
      ``_active``: ``dict[instance_id → ActiveCommand]`` (one slot per
        instance — a new dispatch for an instance with an active
        command violates the in-flight invariant and raises).
      ``_ring``:  ``dict[instance_id → OrderedDict[command_id →
        ActiveCommand]]`` — daemon-wide terminal ring with per-
        instance LRU.

    Eviction triggers:
      - ``record_start`` / ``update_phase`` / ``terminalize``: evict
        the active entry; terminal events push to ``_ring`` and trim.
      - ``evict_instance``: drop both the active entry and every ring
        entry keyed to that instance (mirrors the
        ``_pending_injections`` cleanup in
        ``manager._cleanup_instance_state``).
      - TTL expiry: ``get_for_endpoint`` lazily drops ``_ring``
        entries older than ``ttl_seconds`` at read time (no
        background sweeper — cheap, in-memory).

    Capacity:
      - ``_active`` is naturally bounded by # active instances.
      - ``_ring`` is bounded by ``max_state_per_instance`` PER
        INSTANCE — total ring size is naturally bounded by
        ``max_state_per_instance`` × # instances.

    Threading: not safe for cross-thread use. All callers funnel
    onto the main event loop (router intercept → dispatcher → bg
    task).
    """

    def __init__(
        self,
        *,
        state_ttl_s: int,
        max_state_per_instance: int,
    ) -> None:
        self._state_ttl_s = state_ttl_s
        self._max_state_per_instance = max_state_per_instance
        self._active: dict[str, ActiveCommand] = {}
        # OrderedDict so we can ``popitem(last=False)`` for LRU.
        self._ring: dict[str, OrderedDict[str, ActiveCommand]] = {}

    @property
    def state_ttl_s(self) -> int:
        return self._state_ttl_s

    @property
    def max_state_per_instance(self) -> int:
        return self._max_state_per_instance

    # ── active slot ────────────────────────────────────────────────────

    def record_start(
        self,
        *,
        instance_id: str,
        command_id: str,
        command: str,
        ttl_seconds: int,
        phase: str = "waiting",
    ) -> ActiveCommand:
        """Insert a new active command — raises if instance is busy.

        The dispatcher's in-flight guard (``busy`` rejection) should
        prevent this from raising; the assert surfaces dispatcher
        invariant violations as ``RuntimeError`` (caught upstream
        and converted to a ``busy`` rejection for defense in depth).

        W-1.3 — captures ``started_at_iso`` at the SAME ``time.monotonic()``
        instant as ``started_at`` (monotonic) so the wire and the
        monotonic clock never drift apart. The GET endpoint surfaces
        the stored ISO string verbatim — no recomputation at read
        time.
        """
        if instance_id in self._active:
            existing = self._active[instance_id]
            raise RuntimeError(
                f"Instance {instance_id} already has an active command "
                f"{existing.command_id} (phase={existing.phase}); cannot "
                f"start {command_id}. The dispatcher's in-flight guard "
                f"should have rejected this."
            )
        now = time.monotonic()
        ac = ActiveCommand(
            instance_id=instance_id,
            command_id=command_id,
            command=command,
            started_at=now,
            started_at_iso=_iso8601_now(),
            phase=phase,
            phase_seq=1,
            last_event_at=now,
            last_event_at_iso=_iso8601_now(),
            ttl_seconds=ttl_seconds,
        )
        self._active[instance_id] = ac
        return ac

    def update_phase(
        self,
        instance_id: str,
        command_id: str,
        phase: str,
        *,
        detail: dict | None = None,
        bump_seq: bool = True,
    ) -> ActiveCommand | None:
        """Update the phase / detail for the active command.

        Returns the updated ``ActiveCommand`` or ``None`` when there is
        no matching active entry (e.g. already terminalized).

        W-1.3 — also updates ``last_event_at_iso`` to the wall-clock
        instant captured AT this call (same ``time.monotonic()`` as
        ``last_event_at``). The GET endpoint surfaces the stored ISO
        string verbatim.
        """
        ac = self._active.get(instance_id)
        if ac is None or ac.command_id != command_id:
            return None
        ac.phase = phase
        if bump_seq:
            ac.phase_seq += 1
        ac.last_event_at = time.monotonic()
        ac.last_event_at_iso = _iso8601_now()
        if detail is not None:
            ac.detail = detail
        return ac

    def terminalize(
        self,
        instance_id: str,
        command_id: str,
        *,
        phase: str,
        detail: dict | None = None,
    ) -> ActiveCommand | None:
        """Move the active command to the terminal ring with LRU trim.

        Returns the (now-terminal) ``ActiveCommand`` or ``None`` when
        there was no matching active entry. Safe to call twice — the
        second call returns ``None``.
        """
        ac = self.update_phase(
            instance_id,
            command_id,
            phase,
            detail=detail,
            bump_seq=True,
        )
        if ac is None:
            return None
        self._active.pop(instance_id, None)
        ring = self._ring.setdefault(instance_id, OrderedDict())
        ring[command_id] = ac
        # LRU trim — evict oldest entries past the bound.
        while len(ring) > self._max_state_per_instance:
            ring.popitem(last=False)
        return ac

    def current_phase_seq(
        self,
        instance_id: str,
        command_id: str,
    ) -> int:
        """Return the dispatcher's current ``phase_seq`` for
        ``command_id`` (active OR ring).

        The registry is the single source of truth for ``phase_seq``.
        The executor's SSE emits MUST use this value (NOT a local
        counter) so the GET endpoint and the SSE stream agree on the
        sequence. W-3.1 — without a single counter helper, a long
        compaction (>10s) can re-emit a heartbeat with the same
        ``phase_seq`` as a later terminal event (FE dedup drops the
        terminal on its side). W-3.2 — without the registry being
        authoritative, the executor's local counter and the registry
        counter can fall out of sync by 1-2 steps.

        Returns ``0`` when no active / ring entry matches — callers
        must NOT emit when this returns 0 (the executor gates on
        ``active or ring existence`` before calling).
        """
        ac = self._active.get(instance_id)
        if ac is not None and ac.command_id == command_id:
            return ac.phase_seq
        ring = self._ring.get(instance_id)
        if ring:
            entry = ring.get(command_id)
            if entry is not None:
                return entry.phase_seq
        return 0

    def get_active(self, instance_id: str) -> ActiveCommand | None:
        """Active (non-terminal) command for ``instance_id`` only.

        For the GET endpoint which wants active OR recent terminal
        within TTL, see :meth:`get_for_endpoint`.
        """
        return self._active.get(instance_id)

    def get_for_endpoint(self, instance_id: str) -> ActiveCommand | None:
        """Active command OR most recent non-expired terminal entry.

        Active events ALWAYS win — they are the freshest state. The
        ring provides the fallback for SSE-loss recovery: the most
        recently inserted (and thus most recent) terminal entry that
        has not yet passed its TTL is returned.

        TTL applies to TERMINAL ring entries only — active commands
        are always returned regardless of elapsed time (the daemon
        will heartbeat via SSE instead).

        Expired entries are lazily evicted at read time so the ring
        size stays bounded without a background sweeper.
        """
        ac = self._active.get(instance_id)
        if ac is not None:
            return ac
        ring = self._ring.get(instance_id)
        if not ring:
            return None
        now = time.monotonic()
        # Snapshot the values atomically BEFORE iterating. Iterating
        # the live OrderedDict while mutating it (the lazy-evict
        # ``ring.pop(...)`` below fires when an entry has expired)
        # raises ``RuntimeError: OrderedDict mutated during iteration``
        # in CPython — see defect #3 (2026-08-31 live gate) which
        # surfaced this as a transient 500 on GET /commands/active.
        # Iterating a ``list`` snapshot decouples iteration from
        # mutation; eviction of expired entries continues to bound
        # the ring at read time as before.
        snapshot = list(ring.values())
        # Iteration is insertion order; ``reversed`` gives newest first.
        for entry in reversed(snapshot):
            elapsed = now - entry.last_event_at
            if elapsed <= entry.ttl_seconds:
                return entry
            # Lazy eviction — drop the expired entry we touched. Safe
            # because we iterate the snapshot, not the live dict.
            ring.pop(entry.command_id, None)
        return None

    def evict_instance(self, instance_id: str) -> None:
        """Drop every registry row keyed to ``instance_id``.

        Mirrors ``manager._cleanup_instance_state`` — called from the
        same call site when an instance is deleted/terminated.
        """
        self._active.pop(instance_id, None)
        self._ring.pop(instance_id, None)


# ─────────────────────────────────────────────────────────────────────────
# CommandContext — handle passed to handlers (WS-2 will consume this)
# ─────────────────────────────────────────────────────────────────────────


@dataclass
class CommandContext:
    """Per-call context threaded into the handler.

    Handlers receive this and use it to call ``update_phase`` /
    ``terminalize`` on the dispatcher. Keeping it as a small struct
    (not a global) makes handlers testable and routes all state
    mutations through the dispatcher's API.
    """

    dispatcher: "CommandDispatcher"
    command_id: str
    instance_id: str

    async def update_phase(
        self,
        phase: str,
        *,
        detail: dict | None = None,
        bump_seq: bool = True,
    ) -> None:
        """Forward to :meth:`CommandDispatcher.update_phase`."""
        self.dispatcher.update_phase(
            self.instance_id,
            self.command_id,
            phase,
            detail=detail,
            bump_seq=bump_seq,
        )

    async def terminalize(
        self,
        phase: str,
        *,
        detail: dict | None = None,
    ) -> None:
        """Forward to :meth:`CommandDispatcher.terminalize`."""
        self.dispatcher.terminalize(
            self.instance_id,
            self.command_id,
            phase=phase,
            detail=detail,
        )

    def current_phase_seq(self) -> int:
        """Return the dispatcher's authoritative ``phase_seq`` for this
        command (W-3.1 / W-3.2 single source of truth).

        SSE emit sites in the executor call this to fetch the
        registry's counter — the executor MUST NOT use a local
        counter (a long compaction's heartbeat + terminal emit can
        otherwise collide on the same phase_seq, dropping the
        terminal on the FE's dedup).
        """
        return self.dispatcher.current_phase_seq(
            self.instance_id, self.command_id
        )


# ─────────────────────────────────────────────────────────────────────────
# CommandDispatcher — the top-level service
# ─────────────────────────────────────────────────────────────────────────


@dataclass
class DispatchOutcome:
    """Result of :meth:`CommandDispatcher.dispatch`.

    The router layer maps each ``kind`` to its HTTP shape:

    - ``"passthrough"`` → fall through to the normal message path.
      ``sanitized_text`` is set when the user typed a ``//``-escape
      (router replaces the message content with this value and
      continues).
    - ``"unknown_command"`` → ``400`` with
      ``ErrorResponse{code:"UNKNOWN_COMMAND", details:{available:[...]}}``.
    - ``"ack"`` → ``200`` with the CommandAck dict in ``ack``
      (``state: "accepted" | "rejected"``). Rejections here are the
      dispatch-time reasons (``busy`` / ``rate_limited`` /
      ``pending_injections``); executor-level reasons arrive as
      terminal SSE phases, not as sync acks.
    """

    kind: str  # "passthrough" | "unknown_command" | "ack"
    ack: dict | None = None
    sanitized_text: str | None = None
    available: list[str] | None = None


class CommandDispatcher:
    """Slash-command dispatcher (WS-1 / WS-5 / WS-6 partial).

    Owns:
      - the parse layer (:func:`parse_slash_command` — free function)
      - the command registry (:class:`CommandRegistry`)
      - the state registry (O10 active slot + terminal ring LRU + TTL)
      - the rate-limit state (in-flight + min-interval per instance)
      - the dispatch loop — escape → parse → registry → availability →
        pending-injections → rate-limit → record_start → ack → spawn bg
        task

    Lifetime: one per daemon process. Constructed in
    ``InstanceManager.__init__`` (mirroring ``ExecutionGateService``)
    and exposed via ``manager.command_dispatcher``. Routers reach it
    through ``app.state.manager.command_dispatcher``.

    Threading: all methods run on the main event loop. Background
    tasks (handlers) run on the main loop too.
    """

    def __init__(
        self,
        *,
        enabled: bool = True,
        escape_prefix: str = "//",
        min_interval_s: int = 10,
        state_ttl_s: int = 600,
        max_state_per_instance: int = 20,
    ) -> None:
        self._enabled = enabled
        self._escape_prefix = escape_prefix
        self._min_interval_s = int(min_interval_s)
        self._state = CommandStateRegistry(
            state_ttl_s=int(state_ttl_s),
            max_state_per_instance=int(max_state_per_instance),
        )
        self._registry = CommandRegistry()
        # Rate-limit state. Monotonic clock; only the dispatcher writes.
        self._inflight: dict[str, str] = {}  # instance_id → command_id
        self._last_dispatch: dict[str, float] = {}  # instance_id → monotonic ts
        # Hold strong references to in-flight bg tasks so the event
        # loop doesn't GC them mid-run. ``done_callback`` discards.
        self._tasks: set[asyncio.Task] = set()

    # ── accessors ──────────────────────────────────────────────────────

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def registry(self) -> CommandRegistry:
        return self._registry

    @property
    def state(self) -> CommandStateRegistry:
        return self._state

    @property
    def escape_prefix(self) -> str:
        return self._escape_prefix

    @property
    def min_interval_s(self) -> int:
        return self._min_interval_s

    def parse(self, text: str) -> ParseResult:
        """Convenience: parse ``text`` with this dispatcher's config."""
        return parse_slash_command(text, self._escape_prefix)

    # ── rate-limit helpers (test-visible seams) ────────────────────────

    def _is_inflight(self, instance_id: str) -> bool:
        return instance_id in self._inflight

    def _last_dispatch_at(self, instance_id: str) -> float | None:
        return self._last_dispatch.get(instance_id)

    # ── state helpers (re-exposed for executor coder / O10) ───────────

    def update_phase(
        self,
        instance_id: str,
        command_id: str,
        phase: str,
        *,
        detail: dict | None = None,
        bump_seq: bool = True,
    ) -> None:
        """Forward to :meth:`CommandStateRegistry.update_phase`."""
        self._state.update_phase(
            instance_id,
            command_id,
            phase,
            detail=detail,
            bump_seq=bump_seq,
        )

    def terminalize(
        self,
        instance_id: str,
        command_id: str,
        *,
        phase: str,
        detail: dict | None = None,
    ) -> None:
        """Forward to :meth:`CommandStateRegistry.terminalize` and clear
        the in-flight slot (handler is done)."""
        self._state.terminalize(
            instance_id,
            command_id,
            phase=phase,
            detail=detail,
        )
        # Clear the in-flight slot — only if it still points at us
        # (defensive against double-terminalize).
        cur = self._inflight.get(instance_id)
        if cur == command_id:
            self._inflight.pop(instance_id, None)

    def get_for_endpoint(self, instance_id: str) -> ActiveCommand | None:
        """Forward to :meth:`CommandStateRegistry.get_for_endpoint`.

        Active OR recent-terminal-within-TTL snapshot for the GET
        endpoint. Returns ``None`` when there is nothing relevant.
        The endpoint serializes this to either ``{exists:false}`` or
        ``{exists:true, command: ...}``.
        """
        return self._state.get_for_endpoint(instance_id)

    def get_active(self, instance_id: str) -> ActiveCommand | None:
        """Active (non-terminal) command only. Forwarder to the
        registry's ``get_active``."""
        return self._state.get_active(instance_id)

    def current_phase_seq(self, instance_id: str, command_id: str) -> int:
        """Forward to :meth:`CommandStateRegistry.current_phase_seq`.

        W-3.1 / W-3.2 — single source of truth for ``phase_seq``.
        The executor reads via this accessor; SSE emits use the
        registry value so SSE / GET agree on the sequence.
        """
        return self._state.current_phase_seq(instance_id, command_id)

    def evict_instance(self, instance_id: str) -> None:
        """Drop every dispatcher row keyed to ``instance_id``.

        Mirrors ``manager._cleanup_instance_state`` for the command
        layer — called from the same call site when an instance is
        deleted/terminated so command state cannot outlive the
        instance.
        """
        self._inflight.pop(instance_id, None)
        self._last_dispatch.pop(instance_id, None)
        self._state.evict_instance(instance_id)

    # ── dispatch entry point ───────────────────────────────────────────

    async def dispatch(
        self,
        instance_id: str,
        text: str,
        *,
        pending_injections: int = 0,
        instance_context: Any = None,
        instance_status: str | None = None,
    ) -> DispatchOutcome:
        """Dispatch a slash-command.

        The router calls this from the POST /messages intercept seam.
        The return value is a :class:`DispatchOutcome` describing what
        the router should do next (passthrough / 400 / 200-ack).

        **Ordering (architect §1, plan 1.4 — LOAD-BEARING):**

        1. Master-switch (``enabled``). Off → passthrough (no-op).
        2. ``//``-escape check (``parse``).
        3. Registry lookup.
        4. Instance-status gate (defect #2, 2026-08-31): an instance in
           ``COMPACT_REJECT_STATUSES`` (terminated/error/failed) →
           ``200 rejected terminal_instance`` ack — BEFORE record_start,
           so no ``command_id`` is minted, no in-flight slot is taken,
           and NO background task is spawned. Cheap + synchronous (the
           router already holds ``instance_info``). ``completed`` is
           compact-eligible and falls through to availability →
           pending-injections → rate-limit → record_start; it stays
           terminal for every other consumer of the canonical set.
           The executor keeps its own terminal guard as defense-in-depth
           for the read→handler-start TOCTOU window.
        5. Availability predicate (O-B6 — unpopulated today).
        6. Pending-injections guard (WS-6 row, O-B11 ratified).
        7. Rate-limit (in-flight + min-interval).
        8. ``record_start`` → mint ``command_id``.
        9. Spawn bg task + return ack.

        The rate-limit step happens BEFORE the bg task spawn, so a
        rate-limited request never acquires the ExecutionGate
        (the executor, WS-2, is the gate-acquirer and runs in the bg
        task).

        Args:
            instance_id: Target instance.
            text: Raw message content (command or plain text).
            pending_injections: Queue depth from the router.
            instance_context: O-B6 availability hook input.
            instance_status: The instance's status string as read by
                the router (``instance_info["status"]``). ``None``
                (default) keeps the pre-defect-#2 behavior — the gate
                is silent, and terminal instances fall through to the
                executor's defense-in-depth guard. Normalized
                case/whitespace-tolerant.
        """
        # 1. Master switch.
        if not self._enabled:
            return DispatchOutcome(kind="passthrough")

        # 2. Escape + parse.
        parsed = self.parse(text)
        if parsed.sanitized_text is not None:
            return DispatchOutcome(
                kind="passthrough",
                sanitized_text=parsed.sanitized_text,
            )
        if parsed.command is None:
            return DispatchOutcome(kind="passthrough")

        # 3. Registry lookup.
        spec = self._registry.get(parsed.command.name)
        if spec is None:
            return DispatchOutcome(
                kind="unknown_command",
                available=self._registry.list(),
            )

        # 4. Instance-status gate (defect #2, 2026-08-31). BEFORE
        # record_start → the rejection mints nothing, records nothing,
        # spawns nothing. Beats busy/rate_limited for the statuses it
        # rejects — that is the more fundamental fact about the
        # instance (§5/§6 matrix, terminal row).
        #
        # compact-on-COMPLETED (2026-08-31): the gate rejects ONLY the
        # COMPACT_REJECT_STATUSES triple (terminated/error/failed).
        # ``completed`` falls through to availability →
        # pending-injections → rate-limit → record_start: it is
        # compact-eligible (C1 Variant A persist keeps ``next=()``) but
        # remains TERMINAL for every other consumer of
        # ``daemon.constants.TERMINAL_INSTANCE_STATUSES`` — the
        # gate-ordering invariant (terminal beats busy/rate-limited)
        # is preserved unchanged for the other 3 statuses.
        if (
            instance_status is not None
            and instance_status.strip().lower()
            in COMPACT_REJECT_STATUSES
        ):
            logger.info(
                "Rejecting command=%s for terminal-status instance=%s "
                "at ack time (status=%s)",
                parsed.command.name,
                instance_id,
                instance_status.strip().lower(),
            )
            return self._rejected(
                command=parsed.command.name,
                reason=RejectionReason.TERMINAL_INSTANCE,
                detail=TERMINAL_INSTANCE_GUIDANCE,
            )

        # 5. Availability predicate (O-B6 — unpopulated today).
        if spec.availability is not None:
            allowed = await spec.availability(instance_context)
            if not allowed:
                # W-2.5 (leader-approved, 2026-08-31) — the rejection
                # reason enum has its own 7th slot for "agent policy
                # denied" (UNAVAILABLE). We surface it directly; the
                # ``pending_injections`` placeholder that the previous
                # implementation used was misleading (it implied a
                # retryable injection-drain path that did not exist).
                logger.info(
                    "Availability predicate returned False for command=%s "
                    "instance=%s; surfacing as rejection reason=unavailable.",
                    parsed.command.name,
                    instance_id,
                )
                return self._rejected(
                    command=parsed.command.name,
                    reason=RejectionReason.UNAVAILABLE,
                    detail="command not available in this context",
                )

        # 6. Pending-injections guard (WS-6 row, O-B11).
        if pending_injections > 0:
            return self._rejected(
                command=parsed.command.name,
                reason=RejectionReason.PENDING_INJECTIONS,
                detail="instance has queued injections; retry after drain",
            )

        # 7. Rate-limit. Checked BEFORE the bg task spawn (and
        # therefore BEFORE any ExecutionGate acquisition, which is
        # the executor's job in WS-2).
        if self._is_inflight(instance_id):
            return self._rejected(
                command=parsed.command.name,
                reason=RejectionReason.BUSY,
                detail="another command is in-flight for this instance",
            )
        last = self._last_dispatch_at(instance_id)
        interval = max(self._min_interval_s, spec.rate_limit_per_instance)
        now = time.monotonic()
        if last is not None and (now - last) < interval:
            return self._rejected(
                command=parsed.command.name,
                reason=RejectionReason.RATE_LIMITED,
                detail=(
                    f"min interval {interval}s not yet elapsed since last "
                    "accepted dispatch"
                ),
            )

        # 8. Record start + mint command_id.
        command_id = str(uuid4())
        try:
            self._state.record_start(
                instance_id=instance_id,
                command_id=command_id,
                command=parsed.command.name,
                ttl_seconds=self._state.state_ttl_s,
            )
        except RuntimeError as e:
            # Invariant violation: instance already has an active
            # command but the in-flight guard didn't catch it. Surface
            # as busy (defense in depth) and log loudly.
            logger.warning(
                "CommandDispatcher invariant violation: %s", e
            )
            return self._rejected(
                command=parsed.command.name,
                reason=RejectionReason.BUSY,
                detail=str(e),
            )
        self._inflight[instance_id] = command_id
        self._last_dispatch[instance_id] = now

        # 9. Spawn background task.
        ctx = CommandContext(
            dispatcher=self,
            command_id=command_id,
            instance_id=instance_id,
        )
        task = asyncio.create_task(
            self._run_handler(
                handler=spec.handler,
                instance_id=instance_id,
                args=parsed.command.args,
                command_id=command_id,
                ctx=ctx,
            )
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

        return DispatchOutcome(
            kind="ack",
            ack=self._accepted(
                command_id=command_id,
                command=parsed.command.name,
            ),
        )

    async def _run_handler(
        self,
        *,
        handler: Callable[..., Awaitable[None]],
        instance_id: str,
        args: str,
        command_id: str,
        ctx: CommandContext,
    ) -> None:
        """Background task body — WS-6 O9: never crashes.

        The handler is responsible for calling ``ctx.terminalize()``
        on its own success path. If it crashes, we synthesize a
        ``failed`` terminal phase so the GET endpoint doesn't hang on
        a phantom. If the handler returns without terminalizing (a
        handler bug), we synthesize ``failed`` so the registry cannot
        grow forever.
        """
        try:
            await handler(
                instance_id=instance_id,
                args=args,
                command_id=command_id,
                context=ctx,
            )
        except asyncio.CancelledError:
            # Deliberate cancel (e.g. daemon shutdown). Don't
            # synthesize a terminal phase — restart will reset.
            #
            # W-3.4 — drop BOTH the in-flight slot AND the state
            # registry's active entry. The previous behaviour kept
            # ``_active`` populated, so a subsequent GET returned
            # exists=true for a command whose handler is no longer
            # running — the instance appeared "stuck busy" until
            # restart. We choose DROP for cancel (let GET return
            # exists:false). The handler's own terminalize never
            # ran (CancelledError is re-raised), so we drop the
            # active slot the registry created at record_start
            # without moving it to the ring (the cancel path is
            # transient; restart resets).
            self._inflight.pop(instance_id, None)
            self._state._active.pop(instance_id, None)
            raise
        except Exception as e:  # noqa: BLE001 — O9: never crash
            logger.exception(
                "Command handler crashed for instance=%s command_id=%s: %s",
                instance_id[:8],
                command_id,
                e,
            )
            try:
                self.terminalize(
                    instance_id,
                    command_id,
                    phase=CommandPhase.FAILED.value,
                    detail={
                        "failure_kind": "error",
                        "reason": type(e).__name__,
                    },
                )
            except Exception:  # pragma: no cover — defensive
                logger.exception("Failed to terminalize crashed handler")
            return

        # Handler returned normally. If it didn't terminalize, force
        # a terminal phase so the registry doesn't leak entries.
        active = self._state._active.get(instance_id)
        if active is not None and active.command_id == command_id:
            logger.warning(
                "Handler returned without terminalizing; forcing FAILED "
                "for command_id=%s instance=%s",
                command_id,
                instance_id,
            )
            self.terminalize(
                instance_id,
                command_id,
                phase=CommandPhase.FAILED.value,
                detail={"reason": "handler_returned_without_terminalize"},
            )

    # ── ack builders ──────────────────────────────────────────────────

    def _accepted(self, *, command_id: str, command: str) -> dict:
        return {
            "status": "command",
            "command": command,
            "command_id": command_id,
            "state": "accepted",
            "reason": None,
            "detail": None,
            "timestamp": _iso8601_now(),
            "ttl_seconds": self._state.state_ttl_s,
        }

    def _rejected(
        self,
        *,
        command: str,
        reason: RejectionReason,
        detail: str,
    ) -> DispatchOutcome:
        return DispatchOutcome(
            kind="ack",
            ack={
                "status": "command",
                "command": command,
                "command_id": None,
                "state": "rejected",
                "reason": reason.value,
                "detail": detail,
                "timestamp": _iso8601_now(),
                "ttl_seconds": self._state.state_ttl_s,
            },
        )


def _iso8601_now() -> str:
    """ISO-8601 UTC timestamp with microsecond precision."""
    return datetime.now(timezone.utc).isoformat()
