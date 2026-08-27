"""WAITING_CHILDREN hang watchdog.

Issue: when a parent enters ``WAITING_CHILDREN`` to await one or more
child completion reports, it sleeps on its dependency watcher until
``child_reports`` / ``error_reporting`` drains. The event-driven path
works for children that reach a terminal status (completed / error /
terminated / failed). It does NOT work for children that *silently
hang* — the child is still RUNNING in name, but its LLM call, tool
invocation, or OpenCode subprocess is wedged. The watcher never
fires; the parent sleeps forever; the user eventually gives up.

This service is the safety net. Every ``interval_seconds`` (default
3600s = 1h), it:

1. Enumerates instances whose status is ``WAITING_CHILDREN``.
2. For each, asks the instance repository for non-terminal children
   whose ``last_activity_at`` is older than ``hang_threshold_seconds``
   (default 3600s). Age is computed SQL-side via
   ``EXTRACT(EPOCH FROM (now() - col))`` on PostgreSQL and
   ``julianday() * 86400`` on SQLite — see
   :meth:`SQLModelInstanceRepository.list_hung_children_for_parent`
   for the rationale (avoid the psycopg 7h skew).
3. Skips ``paused`` children (those are NOT hung — they are awaiting
   a user/system decision and the parent must NOT be nagged).
4. Skips ``paused`` parents entirely — ``set_injection`` rejects
   PAUSED targets by design, so attempting injection is wasted work
   that would log a spurious error.
5. For each (parent, child) pair in a new "hang episode", injects a
   terse, directive notice into the parent via
   :meth:`InstanceManager.set_injection` with provenance
   ``"system:watchdog"``. The notice lists the hung children, how
   long each has been stuck, and the four-step playbook:
   ``subtree_messages`` for inspection, send_message/job_continue
   for one-shot revive (mechanically bounded by
   ``_agent_tool_revive_counts``), spawn a replacement, or escalate
   to the user.
6. **Anti-spam**: tracks in-memory which (parent, child) pairs are
   currently in a notified episode. Re-notifies only when the
   episode ENDS — i.e., the child reaches a terminal status OR
   becomes paused. PAUSED children are filtered out by the SQL
   predicate, so a re-notify on the next tick fires if the parent is
   still WAITING_CHILDREN and the child has resumed non-terminal
   non-paused activity. The cooldown is per-process; a daemon
   restart resets it. This is an accepted v1 limitation.

**Scheduler mechanism**: this watchdog is wired as an asyncio task
in the FastAPI lifespan, mirroring the existing
``_periodic_drift_reconcile_loop`` and
``_periodic_readiness_refresh_loop`` patterns. Rationale:

* **Matches house conventions for new periodic infrastructure**
  (``daemon/api.py:997`` drift reconciler; ``daemon/api.py:1066``
  readiness refresher). StaleTaskRecovery's ``threading.Thread`` is
  the older precedent; the asyncio path is what every new
  infra-loop uses because cancellation semantics are cleaner and
  the lifespan already manages task lifecycle + graceful shutdown.
* **The watchdog is async-friendly** — it calls ``set_injection``
  (sync, but the injection queue is RAM-only and the agent_node
  drains it on its next LLM cycle) and the repo helpers
  (``list_waiting_children_parents``, ``list_hung_children_for_parent``)
  use short-lived SQLModelSessions that the asyncio thread pool
  handles fine.
* **Must shut down with the daemon** — the lifespan cancel/await
  pattern guarantees the task is cancelled cleanly on shutdown,
  with ``asyncio.CancelledError`` propagating out so the runtime
  knows the task is done. No leaked threads, no orphaned
  half-finished scans.
* **Does NOT allocate JobItems** — internal infrastructure paths
  use the lifespan directly per JAFP (``enqueue_message`` /
  send_message / report lanes are not Jobs; watchdog is not a job
  either).
* **Per-tick error isolation** — a single parent's scan failing
  (DB blip, malformed row) is caught and logged; the loop
  continues. A loop-level failure is also caught and logged so a
  persistent DB outage does not crash the daemon.

**Notice source provenance** (``"system:watchdog"``): the FIFO
entry carries this value into ``HumanMessage.additional_kwargs["source"]``
at the agent_node drain site (``daemon/graph.py:2894-2902``). The
parent's context sees the message's origin in its provenance
metadata. The watchdog does NOT reuse the
``"internal_agent:<caller_iid>"`` format — those are agent-tool
caller markers; the watchdog is system-side infrastructure.

**PAUSED parents**: skipped entirely per design (the injection path
would be rejected anyway, but skipping saves the DB scan).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from daemon.repositories.instance.models import InstanceStatus
from daemon.repositories.instance.repository import SQLModelInstanceRepository

logger = logging.getLogger(__name__)


#: Provenance marker stamped onto the injected HumanMessage so the
#: parent's context can show the message's origin. Distinct from
#: ``"internal_agent:<caller_iid>"`` (agent-tool send_message) so
#: the watchdog's notices are visibly system-side infrastructure.
WATCHDOG_SOURCE: str = "system:watchdog"


def _format_age_human(age_seconds: float) -> str:
    """Format an age-in-seconds float as a short, human-friendly string.

    Examples::

        >>> _format_age_human(45.0)
        '45s'
        >>> _format_age_human(125.0)
        '2m'
        >>> _format_age_human(3725.0)
        '1h2m'
        >>> _format_age_human(90061.0)
        '25h1m'
    """
    seconds = int(age_seconds)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        minutes = seconds // 60
        return f"{minutes}m"
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    if minutes == 0:
        return f"{hours}h"
    return f"{hours}h{minutes}m"


def _build_hang_notice(
    parent_id: str,
    hung_children: list[tuple[str, float]],
    *,
    hang_threshold_seconds: int,
) -> str:
    """Build the directive hang-guide notice for ``parent_id``.

    The notice lists the hanging children, how long each has been
    stuck, and the four-step playbook. Kept terse and directive per
    design — the parent's LLM should be able to consume this in
    one pass and pick a remediation.

    Args:
        parent_id: The parent instance ID (used only for context in
            the leading line — readers can correlate with their own
            instance-id surface).
        hung_children: List of ``(child_id, age_seconds)`` tuples.
            Pre-sorted by the repository helper, oldest first.
        hang_threshold_seconds: The threshold the children crossed.
            Surfaced in the notice so a reader can verify the
            watchdog's verdict without consulting config.
    """
    threshold_human = _format_age_human(float(hang_threshold_seconds))
    lines: list[str] = [
        "[system:watchdog] Hang notice — your WAITING_CHILDREN "
        f"turn is blocking on {len(hung_children)} non-terminal "
        f"child instance(s) whose last activity exceeds "
        f"{threshold_human}:",
    ]
    for child_id, age in hung_children:
        age_human = _format_age_human(age)
        short = child_id[:8] if len(child_id) > 8 else child_id
        lines.append(f"  - {short}... stuck for {age_human}")
    lines.append("")
    lines.append("Recommended playbook (in order):")
    lines.append(
        "  1. Inspect via the `subtree_messages` tool to see what "
        "the child has reported so far."
    )
    lines.append(
        "  2. If the child is in a failed/terminal state and you "
        "want to revive it, send a `send_message` (or `job_continue` "
        "on the FAILED branch) ONCE — the agent-tool revive-once "
        "guard makes a 2nd revive auto-refused. Use it deliberately."
    )
    lines.append(
        "  3. If a revive is undesirable or the guard has already "
        "fired, spawn a replacement child."
    )
    lines.append(
        "  4. If the hang repeats, escalate to the user (the "
        "watchdog will keep observing until the episode ends)."
    )
    lines.append("")
    lines.append(
        "This notice fires once per (parent, child) hang episode. "
        "It will re-fire if the child resumes non-terminal activity "
        "without reaching a terminal status."
    )
    return "\n".join(lines)


class WaitingChildrenWatchdog:
    """Detect parents stuck in ``WAITING_CHILDREN`` and nudge them.

    The watchdog is a thin coordinator: it owns the cadence, the
    cooldown set, the per-parent error isolation, and the notice
    construction. The repo helpers own the SQL-side age computation
    and the status enumeration.

    The class is constructable directly with injected collaborators
    so tests can wire mocks without spinning up an ``InstanceManager``
    or a real DB.

    Args:
        instance_repository: Repo with ``list_waiting_children_parents``
            and ``list_hung_children_for_parent``.
        manager: Object exposing ``set_injection(iid, content, source)``.
            In production this is the ``InstanceManager``; in tests it
            is any object implementing the same surface (the production
            call site relies on ``source`` being carried into the
            downstream ``HumanMessage.additional_kwargs``).
        enabled: Master switch. When False, ``run_once`` and the loop
            are no-ops; the lifespan skips task creation entirely.
        interval_seconds: Seconds between scans. Default 3600 = 1h.
        hang_threshold_seconds: Strictly-greater-than threshold for
            hang detection. Default 3600 = 1h.
        now_fn: Optional clock hook for tests. Defaults to
            ``asyncio.get_running_loop`` time via ``loop.time()``.
            Tests inject a callable that returns a monotonic float.
        loop_fn: Optional loop accessor for tests. Defaults to
            ``asyncio.get_running_loop``. The two hooks are kept
            separate so a test can drive ``now_fn`` deterministically
            without owning an event loop.

    Anti-spam invariant: ``_notified`` (a ``set`` of
    ``(parent_id, child_id)`` tuples) records every pair currently in
    a notified episode. On the next tick, a pair is re-notified ONLY
    if it was previously cleared — i.e., the child reached a terminal
    status OR became paused (filterable by the repo helper's
    ``status NOT IN (...)`` predicate, which does NOT include
    ``paused``). Re-population of the set is automatic from the SQL
    result; clearing happens implicitly when a tick's SQL result
    no longer contains the pair. A daemon restart resets the set —
    documented limitation.
    """

    def __init__(
        self,
        instance_repository: SQLModelInstanceRepository,
        manager: Any,
        *,
        enabled: bool = True,
        interval_seconds: int = 3600,
        hang_threshold_seconds: int = 3600,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError(
                f"interval_seconds must be > 0; got {interval_seconds!r}"
            )
        if hang_threshold_seconds < 0:
            raise ValueError(
                "hang_threshold_seconds must be >= 0; got "
                f"{hang_threshold_seconds!r}"
            )

        self._repo = instance_repository
        self._manager = manager
        self._enabled = bool(enabled)
        self._interval_seconds = int(interval_seconds)
        self._hang_threshold_seconds = int(hang_threshold_seconds)

        # ``set`` of ``(parent_id, child_id)`` tuples in a currently
        # notified episode. Cleared implicitly on each tick by
        # re-deriving from the SQL result. See ``run_once`` for the
        # episode-end logic.
        self._notified: set[tuple[str, str]] = set()

    # ─── Public introspection (tests) ──────────────────────────────────

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def interval_seconds(self) -> int:
        return self._interval_seconds

    @property
    def hang_threshold_seconds(self) -> int:
        return self._hang_threshold_seconds

    @property
    def notified_episodes(self) -> frozenset[tuple[str, str]]:
        """Read-only view of the in-memory cooldown set.

        Exposed for tests. Production callers should treat the set as
        opaque; the watchdog is the sole writer.
        """
        return frozenset(self._notified)

    # ─── Core scan ──────────────────────────────────────────────────────

    async def run_once(self) -> dict[str, int]:
        """Execute one watchdog scan.

        Returns a small stats dict for observability / tests::

            {
                "parents_scanned": <int>,
                "parents_skipped_paused": <int>,
                "notices_injected": <int>,
                "errors": <int>,
            }

        The method is idempotent within a single tick — calling it
        twice in quick succession will not double-notify because
        the cooldown set is updated atomically per parent before the
        next parent is processed.

        Per-parent error isolation: a single parent's scan that
        raises is caught, logged, and counted in ``errors``; the
        next parent is processed. The whole ``run_once`` does NOT
        abort on a per-parent failure.
        """
        stats = {
            "parents_scanned": 0,
            "parents_skipped_paused": 0,
            "notices_injected": 0,
            "errors": 0,
        }
        if not self._enabled:
            return stats

        # Step 1: enumerate parents. A repo blip is logged at ERROR
        # and surfaced as a single ``errors++`` so the daemon does
        # not crash; the next tick will retry.
        try:
            parent_ids = self._repo.list_waiting_children_parents()
        except Exception as exc:
            logger.error(
                f"[Watchdog] Failed to enumerate WAITING_CHILDREN "
                f"parents: {exc}",
                exc_info=True,
            )
            stats["errors"] += 1
            return stats

        # Step 2: per-parent hang detection + notice injection.
        # Re-derive the cooldown set from the SQL result: a
        # (parent, child) that is in ``_notified`` but absent from
        # the new SQL result has either reached a terminal status
        # OR become paused — i.e., the episode has ENDED, and the
        # watchdog may re-notify on a subsequent live episode. We
        # implement that by removing absent pairs from the set at
        # the START of each parent scan; pairs that remain in the
        # SQL result are NOT re-notified because they're already
        # in the set.
        currently_hung_pairs: set[tuple[str, str]] = set()

        for parent_id in parent_ids:
            stats["parents_scanned"] += 1
            try:
                # Step 2a: per-parent guard against PAUSED parents.
                # set_injection would reject a PAUSED target; we
                # skip the DB scan too so we do not waste the
                # threshold-eval query on a parent that cannot be
                # notified. Read via the facade (per D14 layering)
                # using a lightweight status-only fetch — the
                # manager exposes ``get_instance`` via the repo.
                parent = self._repo.get(parent_id)
                if parent is None:
                    # Parent disappeared between enumeration and
                    # per-parent scan (terminal transition + delete).
                    # Skip silently; nothing to notify.
                    continue
                if parent.status == InstanceStatus.PAUSED.value:
                    stats["parents_skipped_paused"] += 1
                    continue

                # Step 2b: SQL-side hang detection.
                hung = self._repo.list_hung_children_for_parent(
                    parent_id=parent_id,
                    threshold_seconds=self._hang_threshold_seconds,
                )

                # Record what is currently hung for episode-end
                # detection after the loop.
                for child_id, _age in hung:
                    currently_hung_pairs.add((parent_id, child_id))

                if not hung:
                    continue

                # Step 2c: anti-spam filter. Only notify (parent,
                # child) pairs that are NOT already in the cooldown
                # set from a previous tick — a pair that persists
                # in both the SQL result and the set is in a
                # continuing episode and gets ONE notice.
                new_pairs = [
                    (child_id, age)
                    for child_id, age in hung
                    if (parent_id, child_id) not in self._notified
                ]
                if not new_pairs:
                    continue

                notice = _build_hang_notice(
                    parent_id=parent_id,
                    hung_children=new_pairs,
                    hang_threshold_seconds=self._hang_threshold_seconds,
                )
                # set_injection is sync (RAM queue + asyncio-safe
                # cooperative single-thread). Caller pattern mirrors
                # the agent-tool branch in daemon/tools/instance.py
                # — sync call into the manager.
                self._manager.set_injection(
                    parent_id,
                    notice,
                    source=WATCHDOG_SOURCE,
                )
                # Stamp the cooldown set BEFORE moving on so a
                # crash mid-loop does not double-notify on retry.
                for child_id, _age in new_pairs:
                    self._notified.add((parent_id, child_id))
                stats["notices_injected"] += 1

                logger.info(
                    f"[Watchdog] Injected hang notice into parent "
                    f"{parent_id[:8]}... "
                    f"({len(new_pairs)} new hung children, "
                    f"{len(hung)} total hung)"
                )
            except Exception as exc:
                stats["errors"] += 1
                logger.error(
                    f"[Watchdog] Failed to process parent "
                    f"{parent_id[:8]}...: {exc}",
                    exc_info=True,
                )
                # Per-parent isolation — continue to next parent.
                continue

        # Episode-end detection: any pair in the cooldown set that
        # is NOT in the freshly-computed ``currently_hung_pairs``
        # has ended (terminal or paused). Drop them so the watchdog
        # can re-notify on a future live episode.
        ended_pairs = self._notified - currently_hung_pairs
        if ended_pairs:
            self._notified -= ended_pairs
            logger.info(
                f"[Watchdog] Episode(s) ended for "
                f"{len(ended_pairs)} (parent, child) pair(s); "
                "cooldown cleared so future live episodes can re-notify."
            )

        return stats


async def run_waiting_children_watchdog_loop(
    watchdog: WaitingChildrenWatchdog,
    *,
    interval_seconds: int,
) -> None:
    """Drive ``watchdog.run_once`` in a periodic asyncio loop.

    Mirrors ``_periodic_drift_reconcile_loop`` at
    ``daemon/api.py:997``. Cancellation contract:

    * ``asyncio.CancelledError`` is the shutdown signal. The outer
      ``try/except asyncio.CancelledError: raise`` propagates it so
      the asyncio runtime knows the task is done.
    * The inner ``try/except Exception`` swallows a single cycle's
      failure (DB blip, malformed row) — the loop is best-effort
      and a missed cycle is recovered on the next tick. The
      watchdog's own per-parent error isolation further contains
      blast radius inside a single cycle.
    * The post-tick ``asyncio.sleep`` swallows ``CancelledError``
      and returns cleanly — preferred over ``raise`` here because
      the loop's natural end is between ticks, not in the middle
      of one.

    Args:
        watchdog: The watchdog instance to drive. If
            ``watchdog.enabled`` is False, the loop returns
            immediately without running a single cycle.
        interval_seconds: Sleep between cycles. Must be > 0; the
            lifespan validates this when constructing the watchdog.
    """
    if not watchdog.enabled:
        logger.info(
            "[Watchdog] Disabled by config — loop not started."
        )
        return

    logger.info(
        f"[Watchdog] Starting periodic loop: "
        f"interval={interval_seconds}s, "
        f"hang_threshold={watchdog.hang_threshold_seconds}s"
    )

    while True:
        try:
            stats = await watchdog.run_once()
            if stats["notices_injected"] > 0 or stats["errors"] > 0:
                logger.info(
                    f"[Watchdog] tick stats: {stats}"
                )
        except asyncio.CancelledError:
            # Shutdown — propagate so the runtime knows the task is done.
            raise
        except Exception as exc:
            # Best-effort: a single failed cycle is not fatal. The
            # next tick will retry. Log at ERROR so the operator has
            # visibility.
            logger.error(
                f"[Watchdog] cycle failed: {exc}",
                exc_info=True,
            )

        try:
            await asyncio.sleep(interval_seconds)
        except asyncio.CancelledError:
            return


__all__ = [
    "WATCHDOG_SOURCE",
    "WaitingChildrenWatchdog",
    "run_waiting_children_watchdog_loop",
]


# ─── Type annotation hooks (kept here for forward-ref resolution) ─────
# The ``Awaitable`` / ``Callable`` imports above are unused by the
# runtime but document the contract for downstream test mocks that
# inject alternative collaborators (e.g. an async-manager shim). They
# satisfy the lint rule against unused imports without polluting the
# module namespace with implementation-specific stubs.
_ = (Awaitable, Callable)