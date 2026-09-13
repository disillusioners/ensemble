"""Long Tool Call Detection → Parent Nudge (detection surface).

This module is the single canonical home for the long-tool-call-nudge
feature's detection primitive:

* :class:`LongToolNudgeRegistry` — the RAM stamp registry
  (module-level singleton :data:`LONG_TOOL_REGISTRY`). The graph-side
  wrapper writes stamps (``record_start`` / ``clear`` in a ``finally``);
  the scanner reads them via :meth:`LongToolNudgeRegistry.snapshot`.
  There is NO second stamp store (synthesis AD-28).
* :func:`wrapped_tools_node` — the ``"tools"`` node wrapper installed
  at the single ``add_node("tools", ...)`` seam in ``daemon/graph.py``.
  Both graph wiring variants (watched ``agent → watchover_check →
  tools`` and the manager-less ``agent → tools`` fallback) converge on
  that one site.
* :class:`LongToolNudgeScanner` — the lifespan-wired scanner loop
  body (60 s tick) that resolves effective thresholds, fires the
  cross-phase hand-off seam, and runs the AD-9a stale-stamp belt plus
  the orphan-hygiene sweeps.
* :func:`deliver_long_tool_nudge` — the hand-off seam (phase 1:
  INFO-log stub; phase 2 replaces the scanner's bound method with the
  real ``enqueue_message`` delivery).

Canonical constants (synthesis AD-30): ``HARD_MAX_THRESHOLD_SECONDS``,
``MIN_THRESHOLD_SECONDS``, and ``STALE_STAMP_TTL_SECONDS`` live HERE.
``daemon/config.py`` imports the hard max for the ``le=`` boot
validator; ``daemon/tools/instance.py`` (phase 3) imports the floor
and the hard max for the tuning tool. Import direction is acyclic:
this module never imports ``daemon.config`` at module top.

Overestimation semantics (AD-3): LangGraph's ``ToolNode._arun_batch``
dispatches every ``tool_call`` of one AI message concurrently via
``asyncio.gather``, so a node-level wrapper sees only batch
boundaries. All ``tool_call_id``s in the batch get the SAME
``started_at`` at entry; each clears independently at exit. A fast
call sharing a batch with a slow one observes ``duration ≈ batch
wall_clock`` — an OVERESTIMATION. This is the conservative direction:
false positives are recoverable (the nudge is advisory; the parent
decides), false negatives are not. Per-id stamps keep attribution.

Heartbeat-independent detection invariant: detection keys on
in-flight stamp age ONLY — never on ``TaskHeartbeat`` (which beats
every 30 s independent of tool execution; a wedged-mid-tool child
stays "heartbeat-fresh" forever). Regression-pinned by
``tests/unit/services/test_long_tool_nudge_detector.py::
test_heartbeat_fresh_child_still_fires`` (M8 — was cited under the
outdated ``TestLongToolNudgeScannerHeartbeatFreshStillFires`` cast
name; that class never shipped).

Episode keying (AD-2 / AD-31, two levels, coexisting by design):

* STAMP-LEVEL fire dedup — ``(child_id, tool_call_id)`` in the
  scanner's ``_fired_episodes``; prevents consecutive-tick re-fires
  of one in-flight stamp. Advanced ONLY on a successful fire (AM-7).
* NUDGE-LEVEL episode dedup — ``(parent_id, child_id)`` in the
  scanner's ``_active_episodes``; one nudge per wedge episode.
  Closed on a HEALTHY tool completion only
  (``duration_seconds < effective_threshold_seconds``, AD-9 +
  AD-42 Option (ii) wrapper-side close-gate). A LONG completion
  intentionally LEAVES the episode open — closing on it would re-arm
  prematurely and emit one nudge per long call (the bd4b36ef replay
  would emit 5 instead of 1). Re-armed by a NEW ``tool_call_id``
  crossing after a successful close.

  Episode orphan hygiene (B4, council fix-cycle 1): an episode is
  only orphan-discarded after it has been UNTOUCHED for
  ``STALE_STAMP_TTL_SECONDS`` (reused stamp-TTL constant; episodes
  are RAM-only and restart-reset, so a long TTL is harmless). The
  ``_episode_last_seen`` map records the last monotonic tick the
  child had ANY stamp in-flight — refreshed on every tick where the
  child appears in the live snapshot. The earlier
  ``child_id not in live_children`` discard was unsafe: a 60s tick
  landing in the wrapper's inter-batch gap (stamps momentarily empty
  between batches) would discard the OPEN episode and re-arm the
  next long call → one nudge per long call instead of one per wedge
  episode (bd4b36ef replay must yield exactly 1 nudge). The
  TTL-gated discard closes that wedge while still pruning truly
  abandoned episodes.

Canonical threshold precedence chain (AD-41 — stated once, every
other mention refers back):

1. Kill-switch ``LONG_TOOL_NUDGE_ENABLED`` (default ON). When OFF:
   no scanner fires, no tool write, no delivery — but the stamp
   registry and the per-completion ``[LongToolNudge] TOOL_COMPLETED``
   log line continue by design (stamp/log presence ≠ delivery).
2. Per-child metadata key ``instance_metadata[
   "long_tool_call_threshold_seconds"]`` (read via
   ``get_metadata_value(instance_id, key)``). If absent / ``None`` /
   non-int / ``< MIN_THRESHOLD_SECONDS (60)`` (read-side floor
   bypass, AD-38) → fall through.
3. Env default ``LONG_TOOL_NUDGE_DEFAULT_THRESHOLD_SECONDS``
   (validated ``ge=1 le=1800`` at boot).
4. ``min(·, HARD_MAX_THRESHOLD_SECONDS=1800)`` clamp
   (environment ceiling; AD-19).
5. Strict ``>`` comparison in the scanner
   (``elapsed > effective_threshold``; matches the watchdog
   ``age > threshold`` precedent, AD-4) — fire boundary.

Restart semantics (AD-14): the stamp registry, ``_fired_episodes``,
and ``_active_episodes`` are all RAM-only and die with the process.
In-flight calls are cancelled by a restart anyway; the scanner
rebuilds from the next ``tool_start``. Worst case is ≤ 1 duplicate
nudge if a child is still mid-tool across the restart (accepted,
benign). The NUDGE ITSELF is durable the instant the phase-2
``enqueue_message`` call returns (MessageQueue + Task rows in one
txn, ``instance_messaging.py`` commit seam). No
``report_injections``-style obligation table — the MessageQueue row
IS the durable record (AD-15).

Kill-switch semantics (SC9): ``LONG_TOOL_NUDGE_ENABLED=0`` stops the
scanner loop at boot and (phase 3) gates ``set_instance_tunable``
writes. The wrapper's stamping + the per-completion log line
CONTINUE when disabled — they are the duration-observability surface
(SC6), independent of delivery.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional, TypedDict, Union

from langchain_core.runnables import RunnableConfig

from daemon.constants import INSTANCE_STATUS_PAUSED, TERMINAL_INSTANCE_STATUSES

logger = logging.getLogger(__name__)

# ─── Canonical constants (single home — synthesis AD-30) ─────────────────────

#: System ceiling for any effective threshold. NOT operator-tunable
#: (AD-19): enforced three ways — this constant, the ``le=`` boot
#: validator on ``LONG_TOOL_NUDGE_DEFAULT_THRESHOLD_SECONDS``, and the
#: runtime ``min(·, HARD_MAX)`` clamp in threshold resolution.
HARD_MAX_THRESHOLD_SECONDS: int = 1800

#: Maximum allowed value for ``hard_max_threshold_seconds`` — 24h.
#: This is a hard ceiling on the system ceiling; you cannot
#: construct a scanner with a ``hard_max_threshold_seconds`` above
#: 24h. Hoisted from the prior inline literal ``86400`` at :874
#: (LOW, 2026-09-13) so the 24h semantics are searchable and the
#: error message can name it.
MAX_HARD_MAX_THRESHOLD_SECONDS: int = 86400

#: Floor for any effective threshold (AD-38). Enforced on BOTH sides:
#: the phase-3 tool loud-raises below it, and the scanner's
#: ``resolve_threshold`` treats a hand-edited below-floor metadata
#: value as invalid and falls back to the configured default.
MIN_THRESHOLD_SECONDS: int = 60

#: AD-9a stamp-TTL force-close belt (``4 × HARD_MAX_THRESHOLD_SECONDS``).
#: Deliberately a STANDALONE module constant — decoupled from the
#: (unreconciled) effective graph-task cap per implementer pin P-1:
#: ``daemon/constants.py`` ``TASK_TIMEOUT_S`` (=300) diverges from the
#: observed 7200 s effective cap, and the belt must not depend on
#: either. A legitimate in-flight stamp cannot outlive its graph task
#: (process task supervision cancels it first), so a stamp older than
#: this TTL is by definition a leak.
STALE_STAMP_TTL_SECONDS: int = 7200

#: Provenance source stamped on every nudge (watchdog's
#: colon-namespaced pattern).
LONG_TOOL_NUDGE_SOURCE: str = "system:long-tool-nudge"

#: Metadata key carrying the per-child threshold override. Exact
#: spelling is the phase-3 write / phase-1 read contract — a
#: misspelling silently disables the override.
LONG_TOOL_NUDGE_THRESHOLD_KEY = "long_tool_call_threshold_seconds"

#: Defensive fallback used by the registry's threshold-resolution
#: helper when no scanner resolver has been attached (unit-test /
#: pre-lifespan shapes). Matches the configured default.
DEFAULT_THRESHOLD_FALLBACK: int = 900


# ─── Episode context (canonical Working-Names shape) ─────────────────────────


class LongToolNudgeEpisodeCtx(TypedDict):
    """Context handed to the delivery seam on every threshold crossing.

    Canonical 7-field set (Working-Names Table; phase-2 contract).
    The field set must not shrink between phases (additive fields
    allowed) — pinned by the seam contract test.
    """

    child_id: str  # the (busy) child whose tool exceeded threshold
    parent_id: str  # the parent who must be told ("" when unknown)
    tool_name: str  # exact tool name from the AIMessage.tool_calls entry
    tool_call_id: str  # exact tool_call_id so the parent can correlate
    elapsed_seconds: float  # monotonic in-flight age at the crossing
    threshold_seconds: int  # the effective threshold used at the crossing
    episode_started_at: float  # monotonic timestamp of the tool start


# ─── Stamp registry ──────────────────────────────────────────────────────────


@dataclass
class _Stamp:
    """One in-flight tool-call stamp.

    ``started_at`` is ``time.monotonic()`` (never wall-clock).
    ``parent_id`` is captured at ``record_start`` (AD-42 Option (ii):
    one sync repo read per ``tool_start``, cached for the stamp's
    lifetime) so the wrapper's ``finally`` can close the episode via
    the cached value without a second read at ``tool_end``.
    """

    tool_call_id: str
    tool_name: str
    started_at: float
    parent_id: Optional[str] = None


class LongToolNudgeRegistry:
    """RAM stamp registry — the ONLY stamp store (AD-28).

    Written by the graph wrapper (``record_start`` / ``clear``),
    read by the scanner (``snapshot``). All touch-points run on the
    asyncio loop, so an :class:`asyncio.Lock` is the correct
    serializer (architecture-recommendation §4.3).

    The registry also carries three lazily-attached production
    callbacks (wired once at lifespan init; unit tests attach fakes):

    * ``attach_close_handler`` — the scanner's bound
      ``close_episode``; the wrapper reaches the close through the
      registry it already holds (no scanner import, no import
      cycle). NoOp when nothing is attached (phase-1 shapes).
    * ``attach_threshold_resolver`` — the scanner's bound
      ``resolve_threshold`` so the wrapper's per-completion log and
      AD-9 healthy/long classification use the SAME resolution the
      scanner fires with (parity). Falls back to
      ``DEFAULT_THRESHOLD_FALLBACK`` when nothing is attached.
    * ``attach_parent_lookup`` — resolves ``instance.parent_id`` for
      the wrapper's ``record_start`` capture (AD-42 Option (ii)).
      ``None`` when nothing is attached (stamps get ``parent_id=None``,
      which suppresses close calls but never breaks stamping).
    """

    def __init__(self, max_tracked_instances: int = 1024) -> None:
        self._stamps: dict[str, dict[str, _Stamp]] = {}
        self._lock = asyncio.Lock()
        self._max_tracked_instances = int(max_tracked_instances)
        self._close_handler: Optional[
            Callable[[str, str], Optional[Awaitable[None]]]
        ] = None
        self._threshold_resolver: Optional[Callable[[str], int]] = None
        self._parent_lookup: Optional[
            Callable[..., Union[Awaitable[Optional[str]], Optional[str]]]
        ] = None
        # Per-batch threshold cache (council fix-cycle 1, W1; refined
        # in cycle 2): the wrapper stamps at batch entry and clears
        # at batch exit. The wrapper's ``finally`` path (and the
        # scanner's per-tick read) pull the resolved value from this
        # cache — no repo hit on the asyncio loop. The FIRST stamp of
        # a batch still has to resolve once: the attached sync
        # resolver (``resolve_threshold`` → ``repo.get_metadata_value``)
        # runs via ``asyncio.to_thread`` BEFORE the module singleton
        # lock is acquired, so a slow PG read no longer stalls every
        # other instance's stamp / clear / snapshot. Steady state is
        # exactly ONE off-thread DB read per batch (cache populated
        # on the first stamp, evicted when the bucket empties on
        # the final clear of that batch). Cleared alongside the
        # per-instance bucket so a stale value never leaks across
        # batches.
        self._batch_threshold_cache: dict[str, int] = {}

    # ── Attach points (lifespan-init only) ──

    def attach_close_handler(
        self, handler: Callable[[str, str], Optional[Awaitable[None]]]
    ) -> None:
        """Attach the scanner's ``close_episode`` (AD-42 Option (ii))."""
        self._close_handler = handler

    def attach_threshold_resolver(
        self, resolver: Callable[[str], int]
    ) -> None:
        """Attach the scanner's ``resolve_threshold`` (parity pin)."""
        self._threshold_resolver = resolver

    def attach_parent_lookup(
        self,
        lookup: Callable[..., Union[Awaitable[Optional[str]], Optional[str]]],
    ) -> None:
        """Attach the ``child_id -> parent_id`` reader for record_start.

        Accepts either a sync callable (run via ``asyncio.to_thread``
        so a slow repo hit never blocks the event loop) or an async
        callable (awaited directly — ``asyncio.to_thread`` on a
        coroutine function returns the unawaited coroutine object,
        which would stamp ``parent_id=<coroutine>`` and silently
        break every fire path). Production attaches the scanner's
        ``read_parent_id`` (async); legacy sync test doubles stay
        on the to_thread path.
        """
        self._parent_lookup = lookup

    # ── Stamp writes (wrapper) ──

    async def record_start(
        self,
        instance_id: str,
        tool_call_id: str,
        tool_name: str,
        parent_id: Optional[str] = None,
    ) -> None:
        """Stamp one in-flight tool call at batch entry.

        Idempotent per ``(instance_id, tool_call_id)``: a duplicate
        (the gather / resume-re-entry case) keeps the FIRST stamp —
        first-stamp-wins preserves the original ``started_at``.

        Side effect: resolves and caches the per-batch threshold
        for ``instance_id`` (council fix-cycle 1, W1; refined
        cycle 2) so the wrapper's ``finally`` reads from RAM only
        — no sync repo hit on the wrapper's finally hot path.

        Cycle-2 wedge fix: the first-stamp resolver call is moved
        OFF the module singleton lock. The attached resolver is
        sync and may run a slow PG read; running it under the lock
        blocks every other instance's stamp / clear / snapshot for
        the duration of the read. We pre-resolve in
        ``asyncio.to_thread`` before acquiring the lock — the
        lock-held region only does in-memory dict ops.
        """
        # Cycle-2 pre-lock resolve (only on cache miss). The dict
        # check here is racy against concurrent stamp writers, but
        # the race is benign: both threads will resolve to the
        # same value (resolver is deterministic on the DB row)
        # and the cache stores one of them. No resolver call is
        # made when the resolver is unattached or the cache is
        # already warm.
        pre_resolved: Optional[int] = None
        if (
            self._threshold_resolver is not None
            and instance_id not in self._batch_threshold_cache
        ):
            try:
                pre_resolved = int(
                    await asyncio.to_thread(
                        self._threshold_resolver, instance_id
                    )
                )
            except Exception:  # pragma: no cover - defensive
                logger.exception(
                    "[LongToolNudge] record_start threshold resolver "
                    "raised for %s — no cache entry",
                    (instance_id or "")[:8],
                )

        async with self._lock:
            stamps = self._stamps.get(instance_id)
            if stamps is not None and tool_call_id in stamps:
                return  # first-stamp-wins (T1 idempotency pin)
            if stamps is None:
                if len(self._stamps) >= self._max_tracked_instances:
                    # AD-6 overflow: drop the oldest tracked instance.
                    dropped = next(iter(self._stamps))
                    del self._stamps[dropped]
                    self._batch_threshold_cache.pop(dropped, None)
                    logger.warning(
                        "[LongToolNudge] registry overflow (>=%d instances) "
                        "— dropped stamps for instance %s",
                        self._max_tracked_instances,
                        (dropped or "")[:8],
                    )
                stamps = self._stamps[instance_id] = {}
            stamps[tool_call_id] = _Stamp(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                started_at=time.monotonic(),
                parent_id=parent_id,
            )
            # Per-batch threshold cache: store the pre-resolved
            # value under the lock (pure dict write — no DB).
            # Cycle-1 comment ("resolve ONCE at entry while we hold
            # the lock … never blocks other awaits") was wrong on
            # the "never blocks" half — a sync PG read under the
            # lock DOES stall every other instance. Cycle 2 moves
            # the resolver off-lock; this branch is RAM-only.
            if pre_resolved is not None:
                self._batch_threshold_cache[instance_id] = pre_resolved

    async def clear(
        self, instance_id: str, tool_call_id: str
    ) -> Optional[_Stamp]:
        """Clear one stamp; returns the cleared stamp (or ``None``).

        When the last stamp for ``instance_id`` is cleared, the
        per-batch threshold cache entry is evicted alongside (W1) —
        prevents a stale cache value from leaking across batches
        if the resolver was just hot-reloaded with a new default.
        """
        async with self._lock:
            stamps = self._stamps.get(instance_id)
            if stamps is None:
                return None
            stamp = stamps.pop(tool_call_id, None)
            if not stamps:
                self._stamps.pop(instance_id, None)
                self._batch_threshold_cache.pop(instance_id, None)
            return stamp

    async def clear_many(
        self,
        instance_id: str,
        tool_call_ids: list[str],
    ) -> list[tuple[str, _Stamp]]:
        """Clear a batch of stamps under ONE lock acquisition (A5).

        Council fix-cycle 1, A5 — the wrapper's per-batch finally
        MUST clear every stamp in the batch regardless of how many
        ``CancelledError`` deliveries the runtime makes. Doing the
        clears under a single ``async with self._lock`` keeps the
        loop body synchronous (no per-stamp ``await`` on the lock),
        so a cancel arriving mid-loop cannot land between two
        stamp clears and leak the rest of the batch to the AD-9a
        TTL belt. Returns the cleared ``(tool_call_id, stamp)``
        pairs in input order; the wrapper's logging/close path
        iterates this list (best-effort — a cancel there loses
        the log line but cannot leak stamps, since the clears
        already committed atomically).
        """
        cleared: list[tuple[str, _Stamp]] = []
        async with self._lock:
            stamps = self._stamps.get(instance_id)
            if stamps is None:
                return cleared
            for tc_id in tool_call_ids:
                stamp = stamps.pop(tc_id, None)
                if stamp is not None:
                    cleared.append((tc_id, stamp))
            if not stamps:
                self._stamps.pop(instance_id, None)
                self._batch_threshold_cache.pop(instance_id, None)
        return cleared

    async def snapshot(self) -> dict[str, dict[str, _Stamp]]:
        """Shallow copy so the scanner iterates without holding the lock."""
        async with self._lock:
            return {iid: dict(tcs) for iid, tcs in self._stamps.items()}

    # ── Read helpers (scanner / wrapper) ──

    async def resolve_threshold_for(self, instance_id: str) -> int:
        """Threshold via the cache, falling through to the resolver.

        The per-batch cache (W1) eliminates the wrapper's finally
        SYNC repo hit on the asyncio hot path: when the cache is
        cold (a caller that does NOT go through ``record_start`` —
        e.g. an out-of-band test or a post-restart first tick) we
        still pay one off-thread repo read. The cache itself is
        populated by ``record_start`` and evicted by ``clear`` (or by
        the registry's overflow sweep). The off-thread wrapper
        here mirrors :meth:`lookup_parent_for`'s shape-aware
        dispatch — even on cache miss, the resolver MUST run off
        the event loop so a slow PG read cannot stall the wrapper's
        finally path or any other awaiting coroutine.
        """
        async with self._lock:
            cached = self._batch_threshold_cache.get(instance_id)
        if cached is not None:
            return cached
        resolver = self._threshold_resolver
        if resolver is not None:
            try:
                # Mirror :meth:`lookup_parent_for` — run the sync
                # resolver in ``asyncio.to_thread`` so the wrapper's
                # finally hot path stays off the PG DB call. The
                # cache-miss path is rare (post-restart / out-of-band
                # caller) but a slow repo hit here would otherwise
                # stall the wrapper's `finally` block for every
                # other instance's stamp / clear / snapshot.
                value = await asyncio.to_thread(resolver, instance_id)
                return int(value)
            except Exception:  # pragma: no cover - defensive
                logger.exception(
                    "[LongToolNudge] attached threshold resolver raised "
                    "for instance %s — using fallback",
                    (instance_id or "")[:8],
                )
        return DEFAULT_THRESHOLD_FALLBACK

    async def lookup_parent_for(self, child_id: str) -> Optional[str]:
        """``child_id -> parent_id`` via the attached lookup (or ``None``).

        Council fix-cycle 2 — shape-aware dispatch: an async lookup
        is awaited directly (production attaches the scanner's
        ``read_parent_id``, an ``async def``); a sync lookup is
        wrapped in ``asyncio.to_thread`` so the wrapper's batch-
        entry path does NOT block the event loop on the repo. The
        pre-fix implementation always used ``asyncio.to_thread`` —
        on an async callable ``to_thread`` returns the coroutine
        OBJECT unawaited (truthy), so every stamp carried
        ``parent_id=<coroutine>``, the fire-path ``repo.get``
        raised, per-instance error isolation swallowed it, and
        zero nudges fired in production. The daemon's documented
        wedge history at this exact seam (enqueue_message was
        moved to to_thread for the same reason) makes the
        sync-wrap still mandatory for the sync shape.
        """
        lookup = self._parent_lookup
        if lookup is None:
            return None
        try:
            if inspect.iscoroutinefunction(lookup):
                parent_id = await lookup(child_id)
            else:
                parent_id = await asyncio.to_thread(lookup, child_id)
            # Defensive isawaitable post-check (this dispatch site).
            # A ``functools.partial`` / wraps-decorated async attach or
            # any sync wrapper that returns a coroutine evades
            # ``iscoroutinefunction`` → the to_thread branch above
            # returns the inner coroutine OBJECT unawaited (awaiting
            # ``asyncio.to_thread`` only awaits the wrapper coroutine,
            # not the inner value). ``parent_id`` would land as a
            # coroutine truthy value, stamping ``parent_id=<coroutine>``
            # — the exact wedge class fix-cycle 2 closed. Guard catches
            # the awaitable on both branches and awaits it.
            if inspect.isawaitable(parent_id):
                parent_id = await parent_id
            return parent_id if parent_id else None
        except Exception:  # pragma: no cover - defensive
            logger.exception(
                "[LongToolNudge] attached parent lookup raised for child %s",
                (child_id or "")[:8],
            )
            return None

    async def close_episode_for(
        self, parent_id: Optional[str], child_id: str
    ) -> None:
        """Delegate to the attached close handler (NoOp when unattached).

        The wrapper's HEALTHY-completion close-gate lands here (AD-42
        Option (ii)); the close hook rides the same ``tool_end`` clear.
        """
        if not parent_id:
            return
        handler = self._close_handler
        if handler is None:
            return
        try:
            result = handler(parent_id, child_id)
            if inspect.iscoroutine(result):
                await result
        except Exception:  # pragma: no cover - defensive
            logger.exception(
                "[LongToolNudge] close handler raised for parent %s / child %s",
                (parent_id or "")[:8],
                (child_id or "")[:8],
            )


#: Module-level singleton — the lifespan-wired scanner (api.py) and the
#: graph-wrapped wrapper (graph.py) MUST share this ONE instance.
#: Per-graph / per-ctor allocation silently no-ops the whole feature
#: (pinned by the T8 ``id()`` identity smoke test).
LONG_TOOL_REGISTRY = LongToolNudgeRegistry()


# ─── Wrapped "tools" node (graph seam) ───────────────────────────────────────


def wrapped_tools_node(
    tools: list, registry: LongToolNudgeRegistry
) -> Any:
    """Factory wrapping the bare ``ToolNode`` with stamp lifecycle.

    Returns an async node callable ``(state, config) -> result`` (AM-9
    simplified shape: no ``writer`` kwarg — stream-writer access
    arrives via ``config``, not the node signature). The bare
    ``ToolNode(tools, handle_tool_errors=True)`` is instantiated
    locally and delegated to — its error semantics, ``Command``
    handling, and ``_combine_tool_outputs`` return shape are
    preserved verbatim; this wrapper never inspects or reformats
    output.
    """
    # Import ToolNode at FACTORY-CALL time, not module import: the
    # suite's root conftest installs a global langgraph mock, and the
    # real-graph test fixtures (evict_langgraph_mocks) re-import
    # daemon modules WITHOUT evicting daemon.* from sys.modules. A
    # module-level binding would freeze the mock into this module and
    # poison every real-graph turn that crosses the tools node; a
    # call-time import resolves whichever langgraph is live when the
    # graph is actually built.
    from langgraph.prebuilt import ToolNode

    bare = ToolNode(tools, handle_tool_errors=True)

    # NOTE: ``config`` MUST be typed ``RunnableConfig`` — langgraph
    # decides whether to inject the runtime config by inspecting the
    # node signature; an untyped/``Any``-annotated param receives
    # ``None`` and the instance_id extraction silently degrades to "".
    async def node(
        state: Any, config: Optional[RunnableConfig] = None
    ) -> Any:
        cfg = config or {}
        configurable = cfg.get("configurable") or {}
        instance_id = configurable.get("thread_id", "") or ""
        messages = state.get("messages") if isinstance(state, dict) else None
        last_ai = None
        for message in reversed(messages or []):
            if getattr(message, "tool_calls", None):
                last_ai = message
                break
        tool_calls = list(getattr(last_ai, "tool_calls", None) or [])
        if not tool_calls:
            # Defensively empty-safe (the should_continue routers make
            # this unreachable in practice — architecture-rec §4.4).
            return await bare.ainvoke(state, config)

        # (2) Stamp every tool_call at batch entry — SAME started_at for
        # the whole batch (AD-3 overestimation semantics). parent_id is
        # read ONCE via the attached lookup (cheap; AD-42 Option (ii)).
        # M3 — ``lookup_parent_for`` already swallows its own exceptions
        # (see its try/except — defensive contract), so the dead
        # try/except here was unreachable for any raising lookup. A
        # raising lookup test now pins the propagation contract.
        parent_id = await registry.lookup_parent_for(instance_id)
        for tc in tool_calls:
            tc_id = tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", None)
            tc_name = (
                tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", "")
            )
            if not tc_id:
                continue
            await registry.record_start(instance_id, tc_id, tc_name, parent_id)

        # (3) Delegate to the bare ToolNode; (4) ALWAYS clear in finally —
        # pause-cancel (graph_task.cancel()) and the task-cap TimeoutError
        # both raise through this node (Python guarantees finally on
        # BaseException propagation).
        try:
            return await bare.ainvoke(state, config)
        finally:
            # A5 (council fix-cycle 1) — clear every stamp in ONE
            # atomic lock acquisition (``registry.clear_many``),
            # so the cancel-immune contract holds even if a
            # ``CancelledError`` lands mid-batch. The clears are
            # shielded so the outer task's cancel still propagates
            # to the runtime (the runtime needs to see the cancel)
            # while the inner ``clear_many`` completes the entire
            # batch under a single lock — a cancel arriving at any
            # point within the cleared-stamps window CANNOT leak
            # remaining stamps to the AD-9a TTL belt. The
            # post-clear logging + healthy-gated close are
            # best-effort: a cancel there can lose a log line but
            # CANNOT unwind the already-cleared stamps.
            short_instance = (instance_id or "")[:8]
            tool_call_ids: list[str] = []
            for tc in tool_calls:
                tc_id = (
                    tc.get("id")
                    if isinstance(tc, dict)
                    else getattr(tc, "id", None)
                )
                if tc_id:
                    tool_call_ids.append(tc_id)
            try:
                cleared_stamps = await asyncio.shield(
                    registry.clear_many(instance_id, tool_call_ids)
                )
                # Threshold resolve + per-completion log + close-gate
                # are best-effort: a cancel here can lose the log line
                # but CANNOT leak stamps (cleared atomically above).
                threshold = await asyncio.shield(
                    registry.resolve_threshold_for(instance_id)
                )
                now = time.monotonic()
                for tc_id, stamp in cleared_stamps:
                    duration_seconds = now - stamp.started_at
                    threshold_crossed = duration_seconds > threshold
                    # (SC6) Per-completion duration record — logged
                    # REGARDLESS of crossing; this is the forensic
                    # line the bd4b36ef incident never had.
                    logger.info(
                        "[LongToolNudge] TOOL_COMPLETED instance=%s "
                        "tool_call_id=%s tool=%s duration_ms=%d "
                        "threshold_seconds=%d threshold_crossed=%s",
                        short_instance,
                        tc_id,
                        stamp.tool_name,
                        int(duration_seconds * 1000),
                        threshold,
                        threshold_crossed,
                    )
                    # Close-gate (AD-9 + AD-42 Option (ii),
                    # canonical): a HEALTHY completion (< threshold)
                    # closes the (parent, child) episode; a LONG
                    # completion intentionally LEAVES it open.
                    if stamp.parent_id and duration_seconds < threshold:
                        await asyncio.shield(
                            registry.close_episode_for(
                                stamp.parent_id, instance_id
                            )
                        )
            except asyncio.CancelledError:
                # Propagate — the runtime already sees the cancel;
                # stamps were cleared atomically above the propagate
                # point. The shield above means clear_many ran to
                # completion even though we did not get the return
                # value, so the registry is empty for this batch.
                raise
            except Exception:  # pragma: no cover - defensive
                logger.exception(
                    "[LongToolNudge] finally-clear-loop error for %s",
                    short_instance,
                )

    return node


# ─── Hand-off seam (phase 1 stub; phase 2 fills the scanner body) ────────────


async def deliver_long_tool_nudge(
    parent_id: str,
    child_id: str,
    episode_ctx: LongToolNudgeEpisodeCtx,
) -> bool:
    """Hand-off seam — canonical module-level contract.

    Phase 1 body: a single INFO-log stub with the stable grep-able
    ``[LongToolNudge] STUB_FIRE`` marker carrying the EpisodeCtx
    fields. Returns ``True`` (the stub fire counts as a successful
    fire for AM-7 dedup semantics). Phase 2 replaces the SCANNER's
    bound ``deliver_long_tool_nudge`` method body with the real
    ``enqueue_message`` delivery; this module-level stub remains the
    phase-1 contract pinned by ``TestDeliverLongToolNudgeStub``.
    """
    logger.info(
        "[LongToolNudge] STUB_FIRE parent=%s child=%s tool=%s call_id=%s "
        "elapsed=%.2fs threshold=%ds",
        (parent_id or "")[:8],
        (child_id or "")[:8],
        episode_ctx.get("tool_name", ""),
        (episode_ctx.get("tool_call_id", "") or "")[:8],
        float(episode_ctx.get("elapsed_seconds", 0.0)),
        int(episode_ctx.get("threshold_seconds", 0)),
    )
    return True


# ─── Delivery constants + notice builder (phase 2) ───────────────────────────

# Terminal parents NEVER receive a nudge on this feature (AD-40): no
# revive path, skip + WARN. The orphan-child terminal class is owned
# by existing machinery (terminate_instance / cascade-resume /
# instance_lifecycle). M1 — single-home: imported from
# ``daemon.constants`` (``TERMINAL_INSTANCE_STATUSES``) so the
# routing-relevant status sets live in one canonical module (same
# fork-prevention discipline as ``INJECTION_ELIGIBLE_STATUSES``).
_TERMINAL_PARENT_STATUSES = TERMINAL_INSTANCE_STATUSES
# ``_PARENT_STATUS_PAUSED`` likewise imports the canonical literal
# (``INSTANCE_STATUS_PAUSED`` in ``daemon.constants``); this local
# binding keeps the existing call-site tokens unchanged.
_PARENT_STATUS_PAUSED = INSTANCE_STATUS_PAUSED


def _format_age_human(age_seconds: float) -> str:
    """Format an age-in-seconds float as a short, human-friendly string.

    Local copy of the watchdog's formatter (``waiting_children_watchdog``
    ``_format_age_human``) — deliberately NOT imported so the two
    modules stay independently refactorable.

    Examples::

        >>> _format_age_human(45.0)
        '45s'
        >>> _format_age_human(125.0)
        '2m'
        >>> _format_age_human(3725.0)
        '1h2m'
    """
    seconds = int(age_seconds)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    if minutes == 0:
        return f"{hours}h"
    return f"{hours}h{minutes}m"


def _build_long_tool_notice(
    parent_id: str,
    episode_ctx: LongToolNudgeEpisodeCtx,
    effective_threshold: int,
) -> str:
    """Build the nudge body — the SINGLE canonical home (AD-8).

    Locked 5-section structure: (a) header with child/tool/call-id/
    elapsed/threshold; (b) why-it-matters (busy-slow weak-model
    signature, loop-breaker evasion); (c) recs 1-3 using EXISTING
    parent tools — explicitly NO pause/resume advice (agents have
    no pause tools; pause is operator-only); (d) rec 4 — re-spawn
    with high intelligence via ``spawn_instance(model_tier='high')``
    (Feature #1 opt-in, D4); (e) advisory-only footer with the
    episode id.
    """
    child_id = episode_ctx.get("child_id", "") or ""
    tool_name = episode_ctx.get("tool_name", "") or ""
    tool_call_id = episode_ctx.get("tool_call_id", "") or ""
    elapsed_seconds = float(episode_ctx.get("elapsed_seconds", 0.0))
    elapsed_human = _format_age_human(elapsed_seconds)
    threshold_human = _format_age_human(effective_threshold)
    lines: list[str] = [
        (
            f"[system:long-tool-nudge] Long-tool advisory: child "
            f"{child_id[:8]}'s tool '{tool_name}' (call "
            f"{tool_call_id[:8]}) has been in-flight {elapsed_human} "
            f"(threshold {threshold_human})."
        ),
        (
            "This is the busy-slow / weak-model signature — long tool "
            "time, zero LLM errors so far. The loop breaker cannot "
            "catch varying-args calls; you decide."
        ),
        (
            "1. subtree_messages / get_instance_info — inspect the "
            "child's recent turns to confirm wedged vs slow."
        ),
        (
            "2. send_message the child — it lands at the next turn "
            "boundary; a mid-tool child CANNOT receive messages."
        ),
        (
            "3. terminate_instance + re-spawn a replacement if stuck "
            "past 2x threshold."
        ),
        (
            "4. Re-spawn with high intelligence: spawn_instance(agent_id=<role>, "
            "model_tier='high') — picks the configured high-tier model "
            "(default 'agentic'). Use after recommendation 3 if the child "
            "was busy-slow on a low-tier model."
        ),
        (
            f"This is advisory only — no automatic action has been "
            f"taken. Episode id: {child_id[:8]}:{tool_call_id[:8]}."
        ),
    ]
    return "\n".join(lines)


# ─── Scanner ─────────────────────────────────────────────────────────────────


class LongToolNudgeScanner:
    """Scan in-flight stamps; fire the seam on threshold crossings.

    The scanner owns ONLY its nudge-level state: ``_fired_episodes``
    (stamp-level fire dedup), ``_active_episodes`` (nudge-level
    episode dedup), and ``_nudge_counts`` (future escalation hook —
    never gates firing in v1). It declares NO parallel stamp store:
    stamps live exclusively in the injected registry (AD-28).
    """

    def __init__(
        self,
        instance_repository: Any,
        manager: Any = None,
        *,
        registry: Optional[LongToolNudgeRegistry] = None,
        enabled: bool = True,
        interval_seconds: int = 60,
        default_threshold_seconds: int = 900,
        hard_max_threshold_seconds: int = HARD_MAX_THRESHOLD_SECONDS,
        task_repository: Any = None,
        handoff_stub_enabled: bool = True,
        handoff_fn: Optional[
            Callable[[str, str, LongToolNudgeEpisodeCtx], Any]
        ] = None,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError(
                f"interval_seconds must be > 0; got {interval_seconds!r}"
            )
        if default_threshold_seconds < 1:
            raise ValueError(
                "default_threshold_seconds must be >= 1; got "
                f"{default_threshold_seconds!r}"
            )
        if default_threshold_seconds > hard_max_threshold_seconds:
            raise ValueError(
                "default_threshold_seconds must be <= "
                f"hard_max_threshold_seconds ({hard_max_threshold_seconds}); "
                f"got {default_threshold_seconds!r}"
            )
        if hard_max_threshold_seconds > MAX_HARD_MAX_THRESHOLD_SECONDS:
            raise ValueError(
                "hard_max_threshold_seconds must be <= "
                f"{MAX_HARD_MAX_THRESHOLD_SECONDS} (24h); got "
                f"{hard_max_threshold_seconds!r}"
            )

        self._repo = instance_repository
        self._manager = manager
        self._registry = registry if registry is not None else LONG_TOOL_REGISTRY
        self._enabled = bool(enabled)
        self._interval_seconds = int(interval_seconds)
        self._default_threshold_seconds = int(default_threshold_seconds)
        self._hard_max_threshold_seconds = int(hard_max_threshold_seconds)
        # Mirrors the watchdog ctor for fixture compatibility (D2-10);
        # unused in v1.
        self._task_repository = task_repository
        self._handoff_stub_enabled = bool(handoff_stub_enabled)
        self._handoff_fn = handoff_fn

        # STAMP-LEVEL fire dedup: (child_id, tool_call_id). Advanced
        # ONLY on a successful fire (AM-7) — a PAUSED/terminal-parent
        # skip does NOT consume the firing slot, so the same crossing
        # remains eligible on resume with fresh numbers.
        self._fired_episodes: set[tuple[str, str]] = set()
        # NUDGE-LEVEL episode dedup: (parent_id, child_id). One nudge
        # per wedge episode; closed on HEALTHY tool completion only
        # (AD-9 + AD-42 Option (ii)), re-armed by a NEW tool_call_id
        # after the close.
        self._active_episodes: set[tuple[str, str]] = set()
        # EPISODE-TTL orphan gate (B4, council fix-cycle 1): per
        # episode monotonic timestamp of the last tick the child had
        # any stamp in-flight. An episode is only orphan-discarded
        # once ``now - _episode_last_seen[entry] > STALE_STAMP_TTL_SECONDS``
        # AND the child has no live stamps — the timestamp refresh
        # lets the episode survive the wrapper's inter-batch gap.
        self._episode_last_seen: dict[tuple[str, str], float] = {}
        # Future escalation hook — never gates firing in v1.
        self._nudge_counts: dict[tuple[str, str], int] = {}
        # BEHAVIORAL — log-once-per-episode gate for the
        # ``[LongToolNudge] parent ... not found — nudge skipped``
        # WARN. Without this gate, a long-tool episode whose parent
        # row vanished mid-tool would re-WARN every 60s tick for the
        # entire wedge duration (~minutes-hours), flooding the log
        # without adding signal. The set tracks
        # ``(parent_id, child_id)`` pairs we've already surfaced;
        # a HEALTHY tool completion closes the episode via
        # ``close_episode`` (clears the entry here too), so a fresh
        # tool_call_id crossing after a successful close gets a
        # single fresh WARN. RAM-only, restart-reset — matches the
        # lifetime of every other dedup set on this scanner.
        self._missing_parent_warned: set[tuple[str, str]] = set()

    # ── Introspection surface ──

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def interval_seconds(self) -> int:
        return self._interval_seconds

    @property
    def default_threshold_seconds(self) -> int:
        return self._default_threshold_seconds

    # ── Threshold resolution (canonical chain, AD-41) ──

    def resolve_threshold(self, instance_id: str) -> int:
        """Resolve the effective threshold for one child.

        Rung 2 of the canonical chain: the per-child metadata key,
        validated on read (``None`` / non-int / ``< MIN`` are all
        invalid → fall through to the env default, rung 3), then the
        rung-4 ``min(·, hard_max)`` clamp. Strict-``>`` firing is
        rung 5 and lives in :meth:`run_once`.
        """
        # Repo errors propagate to run_once's per-instance error
        # isolation (T3/U18) — a DB blip counts as that instance's
        # tick error instead of silently degrading the threshold.
        raw: Any = self._repo.get_metadata_value(
            instance_id, LONG_TOOL_NUDGE_THRESHOLD_KEY
        )
        override: Optional[int] = None
        if isinstance(raw, int) and not isinstance(raw, bool):
            # Read-side floor (AD-38): a hand-edited / regressed value
            # below MIN_THRESHOLD_SECONDS is INVALID — fall back to
            # the default rather than honoring a micro-threshold.
            if raw >= MIN_THRESHOLD_SECONDS:
                override = raw
        effective = (
            override if override is not None else self._default_threshold_seconds
        )
        return min(effective, self._hard_max_threshold_seconds)

    async def read_parent_id(self, child_id: str) -> Optional[str]:
        """Lazy ``child_id -> parent_id`` read (defer DB hit to crossing).

        Council fix-cycle 1, W1: runs in ``asyncio.to_thread`` so a
        slow repo hit on the lazy path does not wedge the scanner
        tick. Called only when the wrapper's ``record_start`` ran
        without the parent lookup attached (rare in production —
        the lifespan attaches it before any wrapper turn).
        """
        try:
            instance = await asyncio.to_thread(self._repo.get, child_id)
        except Exception:
            return None
        parent_id = getattr(instance, "parent_id", None)
        return parent_id if parent_id else None

    # ── Hand-off seam (scanner-bound; phase 2 replaces this body) ──

    async def _get_parent(self, parent_id: str) -> Any:
        """Council fix-cycle 1, W1: sync repo read off the event loop.

        The scanner runs in an asyncio loop; ``_repo.get`` is a sync
        DB read. Run it in ``asyncio.to_thread`` so a slow query on
        the parent row does not wedge the scanner tick (same
        rationale as the enqueue_message to_thread move).
        """
        try:
            return await asyncio.to_thread(self._repo.get, parent_id)
        except Exception:
            logger.exception(
                "[LongToolNudge] parent read failed for %s",
                (parent_id or "")[:8],
            )
            return None

    async def deliver_long_tool_nudge(
        self,
        parent_id: str,
        child_id: str,
        episode_ctx: LongToolNudgeEpisodeCtx,
    ) -> bool:
        """Deliver a one-nudge-per-episode advisory to the parent.

        Returns ``True`` if a nudge was enqueued this call; ``False``
        if suppressed by: PAUSED parent (skip + WARN — retry every
        tick, fires on resume; AM-7), a terminal parent (skip + WARN —
        never revive; AD-40), nudge-level episode dedup, or a missing
        parent row.

        Delivery is the A5 double-notify pattern (``enqueue_message``
        with ``priority=0`` + best-effort direct
        ``worker_pool.notify_work()``). NO ``is_deferred`` /
        ``is_background`` / ``work_id`` / ``work_id_required`` kwargs —
        system nudges are foreground (AD-13). ``priority=0`` never
        resets leader-attestation counters (the reset branch requires
        ``priority == 1 AND msg_type == HUMAN.value``,
        ``instance_messaging.py:1896-1902``). Priority=0 only
        queue-jumps the PARENT'S OTHER QUEUED MESSAGES via the
        MessageQueue claim's ``ORDER BY priority ASC`` — it does NOT
        queue-jump the graph-task claim lane (task/repository.py
        carries no priority column on Task rows; council fix-cycle
        1, W3).
        """
        # Phase-1 contract dispatch (stub / injected test seam) first —
        # the T6 contract tests pin these paths.
        if self._handoff_stub_enabled:
            return await deliver_long_tool_nudge(
                parent_id, child_id, episode_ctx
            )
        if self._handoff_fn is not None:
            try:
                result = self._handoff_fn(parent_id, child_id, episode_ctx)
                if inspect.iscoroutine(result):
                    result = await result
                return bool(result)
            except Exception:
                logger.exception(
                    "[LongToolNudge] injected handoff_fn raised for parent %s",
                    (parent_id or "")[:8],
                )
                return False

        # ── Real delivery (phase 2) ──
        parent = await self._get_parent(parent_id)
        if parent is None:
            # BEHAVIORAL — log-once-per-episode: only WARN the first
            # time this ``(parent_id, child_id)`` is seen missing;
            # subsequent ticks in the same wedge episode stay silent
            # (debug-level so the missing-parent event is still
            # observable without log spam).
            episode_key = (parent_id, child_id)
            if episode_key not in self._missing_parent_warned:
                self._missing_parent_warned.add(episode_key)
                logger.warning(
                    "[LongToolNudge] parent %s... not found — nudge skipped",
                    (parent_id or "")[:8],
                )
            else:
                logger.debug(
                    "[LongToolNudge] parent %s... still missing for child %s... "
                    "— nudge suppressed (episode-level dedup)",
                    (parent_id or "")[:8],
                    (child_id or "")[:8],
                )
            return False
        status = getattr(parent, "status", None)
        if status == _PARENT_STATUS_PAUSED:
            # AM-7: retry-every-tick-while-paused — the stamp-level
            # dedup only advances on a successful fire, so the same
            # crossing remains eligible on resume with fresh numbers.
            logger.warning(
                "[LongToolNudge] parent %s... is PAUSED — nudge skipped "
                "this tick (will fire on resume)",
                (parent_id or "")[:8],
            )
            return False
        if status in _TERMINAL_PARENT_STATUSES:
            # AD-40: never revive a terminal parent for an advisory.
            logger.warning(
                "[LongToolNudge] parent %s... is %s (terminal) — nudge "
                "skipped, no revive",
                (parent_id or "")[:8],
                status,
            )
            return False
        if (parent_id, child_id) in self._active_episodes:
            return False  # nudge-level episode dedup — no log (hot path)
        # Fresh re-resolution for the notice (canonical chain rungs
        # 2-4, per-child key) — reflects a just-written override.
        effective_threshold = self.resolve_threshold(child_id)
        notice = _build_long_tool_notice(
            parent_id, episode_ctx, effective_threshold
        )
        metadata = {
            "long_tool_nudge": True,
            "long_tool_nudge_tool": episode_ctx.get("tool_name", ""),
            "long_tool_nudge_tool_call_id": episode_ctx.get(
                "tool_call_id", ""
            ),
            "long_tool_nudge_elapsed_seconds": episode_ctx.get(
                "elapsed_seconds", 0.0
            ),
            "long_tool_nudge_threshold_seconds": effective_threshold,
        }
        await self._manager.enqueue_message(
            instance_id=parent_id,
            message=notice,
            source=LONG_TOOL_NUDGE_SOURCE,
            priority=0,
            metadata=metadata,
        )
        # W2 (council fix-cycle 1) — post-enqueue terminal-parent
        # TOCTOU re-check (AD-40 closure). The pre-read above only
        # narrows the race window; a parent that transitions to a
        # terminal status DURING the enqueue commit is revived
        # anyway. We do NOT undo the durable enqueue (the nudge is
        # already on the MessageQueue and will sit there harmless
        # if the parent's eventual lifecycle disposes of it), we
        # only surface the race in the log so the bug class is
        # observable. The TOCTOU window is bounded by the enqueue
        # txn commit time (sub-second in practice); a slow repo or
        # a hung parent-flush daemon widens it but never escapes
        # the per-instance error isolation at the tick level.
        # M4 — ``_get_parent`` already swallows its own exceptions
        # (see its try/except — defensive contract). The dead
        # try/except here was unreachable for any raising lookup;
        # the propagation test pins ``post_status=None`` for a
        # raising lookup.
        post_status_obj = await self._get_parent(parent_id)
        post_status = getattr(post_status_obj, "status", None)
        if post_status in _TERMINAL_PARENT_STATUSES:
            logger.warning(
                "[LongToolNudge] post-enqueue terminal-parent race: "
                "parent %s... is now %s — nudge already durable, no "
                "rollback (AD-40 TOCTOU window)",
                (parent_id or "")[:8],
                post_status,
            )
        # A5 direct-notify defense-in-depth (incident 33252 class):
        # the enqueue path already notified once internally; this
        # second direct pulse survives a lost-wake race. Idempotent on
        # the pool side (a condition-variable signal).
        worker_pool = getattr(self._manager, "_worker_pool", None)
        if worker_pool is not None:
            try:
                notify_result = worker_pool.notify_work()
                if inspect.iscoroutine(notify_result):
                    await notify_result
            except Exception as notify_err:
                logger.warning(
                    "[LongToolNudge] direct notify_work raised %r for "
                    "parent %s... — relying on enqueue_message's "
                    "internal notify",
                    notify_err,
                    (parent_id or "")[:8],
                )
        else:
            logger.debug(
                "[LongToolNudge] direct notify skipped — worker_pool "
                "not wired (legacy test fixture / pre-wiring lifespan)"
            )
        # The episode opens AFTER the durable enqueue committed.
        # ``_episode_last_seen`` is the monotonic anchor the B4
        # orphan sweep keys on; refresh it here so a tick landing in
        # the wrapper's inter-batch gap cannot prematurely discard
        # the episode (council fix-cycle 1, C1 mechanism choice b).
        self._active_episodes.add((parent_id, child_id))
        self._episode_last_seen[(parent_id, child_id)] = time.monotonic()
        self._nudge_counts[(parent_id, child_id)] = 1
        return True

    # ── Episode close (B4 / AD-42 Option (ii) close target) ──

    def close_episode(self, parent_id: str, child_id: str) -> None:
        """Close the ``(parent_id, child_id)`` wedge episode.

        Attached to the registry at lifespan init
        (``registry.attach_close_handler(scanner.close_episode)``);
        invoked by the wrapper's ``finally`` for HEALTHY completions
        only (AD-9 + AD-42 Option (ii)). Idempotent.

        BEHAVIORAL — also clears the ``_missing_parent_warned``
        entry so a fresh tool_call_id crossing for the same
        ``(parent_id, child_id)`` after a HEALTHY close can
        surface its missing-parent WARN again on the first tick.
        """
        self._active_episodes.discard((parent_id, child_id))
        self._episode_last_seen.pop((parent_id, child_id), None)
        self._nudge_counts.pop((parent_id, child_id), None)
        self._missing_parent_warned.discard((parent_id, child_id))

    # ── Scan tick ──

    async def run_once(self) -> dict[str, int]:
        """Run one scan tick over the registry snapshot.

        Returns the observability stats dict (same shape philosophy
        as the watchdog's ``run_once``, plus the AD-9a belt and the
        B4 orphan counters)::

            {
                "instances_scanned": <int>,
                "stamps_inspected": <int>,
                "fired": <int>,
                "skipped_disabled": <0|1>,
                "errors": <int>,
                "stale_stamps_force_cleared": <int>,
                "orphan_episodes_discarded": <int>,
            }

        Fire boundary is strict ``>`` (AD-4). Per-instance error
        isolation: one instance's failure neither blocks the rest of
        the snapshot nor escapes the tick.
        """
        stats: dict[str, int] = {
            "instances_scanned": 0,
            "stamps_inspected": 0,
            "fired": 0,
            "skipped_disabled": 0,
            "errors": 0,
            "stale_stamps_force_cleared": 0,
            "orphan_episodes_discarded": 0,
        }
        if not self._enabled:
            stats["skipped_disabled"] = 1
            return stats

        snapshot = await self._registry.snapshot()

        # Per-tick threshold memoization: ONE resolution per child
        # with stamps per tick (not per stamp) — Working-Names pin.
        # The resolve runs through ``asyncio.to_thread`` so the sync
        # ``_repo.get_metadata_value`` call does NOT block the event
        # loop on a slow PG read (mirrors the to_thread discipline
        # applied in ``_get_parent`` and ``resolve_threshold_for``).
        thresholds: dict[str, int] = {}

        for instance_id, stamps in snapshot.items():
            stats["instances_scanned"] += 1
            try:
                if instance_id not in thresholds:
                    thresholds[instance_id] = await asyncio.to_thread(
                        self.resolve_threshold, instance_id
                    )
                threshold = thresholds[instance_id]
                for tool_call_id, stamp in stamps.items():
                    stats["stamps_inspected"] += 1
                    elapsed = time.monotonic() - stamp.started_at
                    if elapsed <= threshold:
                        continue  # strict > (AD-4)
                    if (instance_id, tool_call_id) in self._fired_episodes:
                        continue  # stamp-level dedup (one fire per stamp)
                    parent_id = stamp.parent_id
                    if not parent_id:
                        # Stamps recorded before the parent lookup was
                        # attached — lazy repo read (defer to crossing).
                        parent_id = await self.read_parent_id(instance_id) or ""
                    episode_ctx = LongToolNudgeEpisodeCtx(
                        child_id=instance_id,
                        parent_id=parent_id or "",
                        tool_name=stamp.tool_name,
                        tool_call_id=tool_call_id,
                        elapsed_seconds=elapsed,
                        threshold_seconds=threshold,
                        episode_started_at=stamp.started_at,
                    )
                    delivered = await self.deliver_long_tool_nudge(
                        parent_id or "", instance_id, episode_ctx
                    )
                    if delivered:
                        # AM-7: the stamp-level dedup advances ONLY on
                        # a successful fire.
                        self._fired_episodes.add((instance_id, tool_call_id))
                        stats["fired"] += 1
                # (council fix-cycle 1, C1) ANY in-flight stamp for
                # this child refreshes the episode TTL anchor — even
                # if none crossed the threshold on this tick. A 60s
                # tick landing in the wrapper's inter-batch gap (the
                # bd4b36ef incident class: weak-model LLM gaps can
                # exceed the scan interval) therefore cannot discard
                # the OPEN episode.
                # Deliberately INSIDE the try: a per-instance tick
                # error means the episode was NOT observed this tick,
                # so the anchor MUST stay stale — the next successful
                # tick will refresh it, and only the second stale
                # tick past ``STALE_STAMP_TTL_SECONDS`` orphans the
                # episode. Hoisting outside the try would mask
                # failures and break C1's "two consecutive
                # observations of no-progress" semantics.
                for entry in list(self._active_episodes):
                    _parent_id, child_id = entry
                    if child_id == instance_id:
                        self._episode_last_seen[entry] = time.monotonic()
            except Exception:
                stats["errors"] += 1
                logger.exception(
                    "[LongToolNudge] per-instance scan error for %s",
                    (instance_id or "")[:8],
                )

        # AD-9a belt + B4 orphan hygiene (end-of-tick sweeps).
        await self._sweep_stale_and_orphans(snapshot, stats)
        return stats

    async def _sweep_stale_and_orphans(
        self,
        snapshot: dict[str, dict[str, _Stamp]],
        stats: dict[str, int],
    ) -> None:
        """End-of-tick belt: stale-stamp force-close + orphan hygiene.

        * AD-9a: any stamp older than ``STALE_STAMP_TTL_SECONDS`` is
          by definition a leak (a legitimate in-flight stamp cannot
          outlive its graph task) — force-clear the stamp, close the
          episode, re-arm the stamp-level dedup, WARN.
        * AM-5 hygiene: discard ``_fired_episodes`` entries whose
          ``(child_id, tool_call_id)`` no longer appears in the
          snapshot.
        * B4: discard orphan ``_active_episodes`` tuples whose child
          stamp has fully cleared AND whose last-seen anchor is older
          than ``STALE_STAMP_TTL_SECONDS`` (council fix-cycle 1, C1
          option b). The TTL gate prevents the wrapper's inter-batch
          gap from prematurely discarding an OPEN episode and
          re-arming the next long call (bd4b36ef replay must yield
          exactly 1 nudge).
        """
        now = time.monotonic()
        for instance_id, stamps in snapshot.items():
            for tool_call_id, stamp in stamps.items():
                age = now - stamp.started_at
                if age <= STALE_STAMP_TTL_SECONDS:
                    continue
                await self._registry.clear(instance_id, tool_call_id)
                stats["stale_stamps_force_cleared"] += 1
                parent_id = stamp.parent_id or ""
                if parent_id:
                    self._active_episodes.discard((parent_id, instance_id))
                    self._episode_last_seen.pop(
                        (parent_id, instance_id), None
                    )
                self._fired_episodes.discard((instance_id, tool_call_id))
                logger.warning(
                    "[LongToolNudge] STALE_STAMP force-cleared instance=%s "
                    "tool_call_id=%s age=%.0fs ttl=%ds",
                    (instance_id or "")[:8],
                    (tool_call_id or "")[:8],
                    age,
                    STALE_STAMP_TTL_SECONDS,
                )

        live_stamp_keys = {
            (instance_id, tool_call_id)
            for instance_id, stamps in snapshot.items()
            for tool_call_id in stamps
        }
        for entry in list(self._fired_episodes):
            if entry not in live_stamp_keys:
                self._fired_episodes.discard(entry)
        live_children = set(snapshot.keys())
        for entry in list(self._active_episodes):
            _parent_id, child_id = entry
            if child_id in live_children:
                continue  # still in flight — refresh anchor above covers this
            last_seen = self._episode_last_seen.get(entry)
            if last_seen is None:
                # Episode opened in a previous process incarnation
                # (impossible — RAM-only) or seeded externally; treat
                # as orphaned-on-arrival and respect the TTL.
                last_seen = now
                self._episode_last_seen[entry] = last_seen
            if (now - last_seen) <= STALE_STAMP_TTL_SECONDS:
                continue  # inter-batch gap — keep the episode open
            self._active_episodes.discard(entry)
            self._episode_last_seen.pop(entry, None)
            stats["orphan_episodes_discarded"] += 1


# ─── Lifespan loop ───────────────────────────────────────────────────────────


async def run_long_tool_nudge_loop(
    scanner: LongToolNudgeScanner,
    *,
    interval_seconds: int,
) -> None:
    """Drive ``scanner.run_once`` in a periodic asyncio loop.

    Mirrors ``run_waiting_children_watchdog_loop`` verbatim.
    Cancellation contract:

    * ``asyncio.CancelledError`` raised inside a tick propagates —
      the shutdown signal must reach the runtime.
    * The post-tick ``asyncio.sleep`` swallows ``CancelledError``
      and returns cleanly — the loop's natural end is between ticks.
    * Any other exception in a tick is logged ERROR and the loop
      continues (best-effort; the next tick retries).
    """
    if not scanner.enabled:
        logger.info("[LongToolNudge] Disabled by config — loop not started.")
        return

    logger.info(
        "[LongToolNudge] Starting periodic loop: interval=%ds "
        "default_threshold=%ds",
        interval_seconds,
        scanner.default_threshold_seconds,
    )

    while True:
        try:
            stats = await scanner.run_once()
            if (
                stats["fired"] > 0
                or stats["errors"] > 0
                or stats["stale_stamps_force_cleared"] > 0
                or stats["orphan_episodes_discarded"] > 0
            ):
                logger.info("[LongToolNudge] tick stats: %s", stats)
        except asyncio.CancelledError:
            # Shutdown — propagate so the runtime knows the task is done.
            raise
        except Exception as exc:
            # Best-effort: a single failed cycle is not fatal.
            logger.error(
                "[LongToolNudge] cycle failed: %s", exc, exc_info=True
            )

        try:
            await asyncio.sleep(interval_seconds)
        except asyncio.CancelledError:
            return


__all__ = [
    "LONG_TOOL_NUDGE_SOURCE",
    "HARD_MAX_THRESHOLD_SECONDS",
    "MAX_HARD_MAX_THRESHOLD_SECONDS",
    "MIN_THRESHOLD_SECONDS",
    "STALE_STAMP_TTL_SECONDS",
    "LONG_TOOL_NUDGE_THRESHOLD_KEY",
    "DEFAULT_THRESHOLD_FALLBACK",
    "LongToolNudgeEpisodeCtx",
    "LongToolNudgeRegistry",
    "LongToolNudgeScanner",
    "run_long_tool_nudge_loop",
    "deliver_long_tool_nudge",
    "LONG_TOOL_REGISTRY",
    "wrapped_tools_node",
]
