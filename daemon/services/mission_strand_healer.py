"""Mission-strand healer — watcher-loss terminal strand (2026-10-14).

Closes the wedge class the live RCA adjudicated (mission b6e24d78 /
receipt 6fc156f3): a spawned-but-never-dispatched grandchild worker
persisted NON-TERMINAL (idle, never-dispatched ghost, terminal direct
parent) and held the mission's emission gate forever; the post-stamp
downgrade left the root in WAITING_CHILDREN with NO future
message-completed signal, so the event-driven re-check never re-armed
— the receipt JobItem stayed ACTIVE, the armed watcher never fired,
and the 6h zombie backstop was the only (anchor-re-rollable) exit.

This sweep is the WRITE-side durable fix. The READ side (the strand
exclusion in ``mission_live_guard``) already keeps such rows from
holding liveness; this service cleans the stranded rows themselves
and drives the held root through the EXISTING emission machinery so
the publish chain (lifecycle completed event → observer
``_finalize_job`` → fresh result fetch → watcher CAS-claim) re-enters
naturally. NO new notify lane — the drive IS the existing path
(``ChildReportsService._process_child_completion_and_notify_parent``),
whose gates (bus / live-children / pending messages / freshness wedge
resolver / recursive strand-aware liveness / L2b-L6 declared-wait)
all re-run on the driven attempt.

HEAL INVARIANT (all legs must hold; ANY miss ⇒ skip, fail-closed):

  root.status == WAITING_CHILDREN (parent_id IS NULL)          [the wedge posture]
  AND zero pending dependency-bus watchers for the root        [no pending children]
  AND zero READY/PROCESSING/RETRYING messages for the root     [queue drained]
  AND zero PENDING tasks for the root                          [no in-flight turn]
  AND zero PENDING/DEFERRED report_injections obligated to the
      root (declared-wait (b) episodes own those roots via L6) [no open obligation]
  AND every non-root non-terminal tree member matches the
      terminal-ancestry-orphan signature: never-dispatched
      (idle + version 1 + zero message_queue rows + zero task
      rows — the canonical ghost signature) with a TERMINAL
      direct parent and ``updated_at`` older than
      ``ORPHAN_MIN_AGE_SECONDS`` (race guard: a freshly spawned
      child may be about to receive its first message)         [strand-only]

Then (single invocation):

  1. Terminalize each orphan via the PROPER LIFECYCLE LANE —
     ``InstanceLifecycleService.terminate_instance(orphan,
     terminal_reason="orphan_retired")`` (H10 single-transaction,
     events emitted, canonical ``orphan_retired → cancelled`` work
     vocabulary) — NEVER a raw status UPDATE.
  2. All non-root members now terminal ⇒ drive the zero-obligation
     WAITING_CHILDREN root itself through the stamp→gate→publish
     path (the self-referential hold: the root's own WC status is
     the last non-terminal member). The drive's discharge+stamp
     reuses the production seam; the recursive strand-aware gate now
     passes; the publish fires; the receipt terminalizes.

SAFETY BOUND: the healer NEVER terminalizes a never-dispatched row
under a LIVE direct parent (the legitimate spawn-then-message
ritual), NEVER touches PAUSED roots (operator domain), and aborts the
whole heal when any tree member fails the strand signature (a
genuinely-live worker can never be swept by this lane).

ALWAYS-ON infrastructure (no kill-switch — per the project owner's
HARD POLICY codified in ``job_lock_sweep.py``); the only knob is the
interval (``ServicesConfig.mission_strand_heal_interval_seconds``,
default 300s — the same 300s-class cadence as the
WatchReconcileSweepService). Modeling (asyncio task + cancel/await
lifecycle) mirrors ``watch_reconcile_sweep.py``.

RUNNING-ZOMBIE STRAND (2026-10-21) — third strand-family member
--------------------------------------------------------------

The two shapes above heal a root parked in ``waiting_children``. This
one heals a root parked in ``running``: the completion EVENT was lost,
so nothing ever terminalized the instance row. Live shape
(2026-10-19/20, instance 432988b8): mission root ``running``, every
descendant terminal, the root's own latest task row terminal-COMPLETED
— and the row stuck for 25h. The f2 drift finalizer heals only the
JobItem (the mission-live guard's all-terminal short-circuit at
``mission_live_guard.py:509-524`` releases the held JobItem); the
INSTANCE row is a different table and stays ``running`` forever.

HEAL INVARIANT (all legs must hold; ANY miss ⇒ skip, fail-closed —
a missed heal is recoverable on the next tick, a wrong terminal write
is not):

  root.status == RUNNING (parent_id IS NULL)               [the zombie
                                                              posture]
  AND the root's latest task row is terminal-COMPLETED with a
      ``completed_at`` stamp                                   [proof of
                                                              completion]
  AND NO non-terminal task row exists for the root (no
      PENDING/RUNNING/PAUSED/PROCESSING)                      [no live turn]
  AND the latest task's ``completed_at`` is older than the age
      bound (default 7200s)                                  [not a fresh
                                                              turn]
  AND every DESCENDANT of the root is terminal                [the tree is
                                                              closed]
  AND the permanent-tree enumeration is neither breadth- nor
      depth-truncated                                         [complete
                                                              view]
  AND NO pending question anywhere in the tree                [nobody is
                                                              waiting for
                                                              an answer]
  AND the dependency bus has zero pending watchers for the
      root AND the root's own queue has zero READY/PROCESSING/
      RETRYING messages                                       [quiesced]

Then (single invocation) the root is driven through the PRODUCTION
completion chain — ``ChildReportsService._process_child_completion_
and_notify_parent(root, …)`` — NEVER a raw status UPDATE and NEVER
``terminate_instance`` on the root. The chain re-runs every gate
authoritatively under TOCTOU: bus-pending (same transaction),
pending-task, live-children cross-check, pending messages, the
emission-time ``_root_completion_gate`` and — on a pass — the
``JobFeedbackObserver._finalize_job`` cascade that transitions the
JobItem, writes the instance terminal row and releases the locks.
An emission-gate downgrade (stamp → ``waiting_children``) is a
LEGITIMATE MISS: log it and retry the next tick, never force.

TWO PREDICATE CORRECTIONS vs the original commission draft (both
adjudicated):

  1. The age key is ``task.completed_at`` — NOT
     ``instances.last_activity_at``. The latter is FROZEN BY DESIGN
     for a running root (only lifecycle writers bump it), so it
     cannot distinguish a zombie from a long-legitimate turn.
  2. Roots with NO task row at all are NEVER swept — there is no
     completion proof, so the shape is indistinguishable from a
     root that never started. A deliberate miss.

The strand lives here (NOT in ``stale_pause_sweep.py`` — that module
is a RELEASER, pinned by test to never finalize, with a fail-OPEN
probe posture that is wrong for terminalizing writes) and NOT in
``job_recovery_service.py`` (f2 — the lane boundary is preserved
deliberately; f2 heals the JobItem for free once the instance row
is terminal).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from sqlmodel import Session, select, func

from daemon.constants import TERMINAL_INSTANCE_STATUSES
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.message_queue.models import (
    MessageQueue,
    MessageStatus,
)
from daemon.repositories.task.models import Task, TaskStatus

if TYPE_CHECKING:  # pragma: no cover
    from daemon.manager import InstanceManager

logger = logging.getLogger(__name__)

# Default cadence — 300s-class promptness (the commission bound:
# a wedged mission of this shape must heal within ONE sweep
# invocation once its interval elapses; the in-session
# downgrade-path hook fires the same heal event-driven with zero
# sweep latency).
DEFAULT_MISSION_STRAND_HEAL_INTERVAL_SECONDS: int = 300

# Race guard threshold — SINGLE SOURCE OF TRUTH lives in
# ``mission_live_guard.ORPHAN_MIN_AGE_SECONDS`` together with the
# shared predicate ``strand_past_race_guard`` (final hardening
# 2026-10-14: the read-side liveness exclusion runs the SAME
# predicate, so the two sides can never drift). Imported lazily at
# the use site, matching this module's service-import convention.
# An orphan candidate's ``updated_at`` must be at least that old
# before the healer will terminalize it: a freshly-spawned
# never-dispatched child may be about to receive its first message
# (the spawn-then-message ritual); age proves the strand is not a
# mid-flight ritual. Same threshold family as the F14/A4 60s
# orphan-age convention.

# ── Running-zombie strand constants (2026-10-21) ─────────────────

#: Default age bound (seconds) — a mission root whose latest task row
#: is terminal-COMPLETED and completed longer ago than this, whose
#: instance row is nevertheless still ``running``, is a running-zombie
#: candidate. 2h — the same class as the stale-pause sweep's age bound
#: (``stale_pause_sweep.DEFAULT_STALE_PAUSE_AGE_BOUND_SECONDS``) and
#: comfortably longer than any legitimate short mission turn while
#: still bounded well under the 6h zombie backstop horizon.
DEFAULT_RUNNING_ZOMBIE_AGE_BOUND_SECONDS: int = 7200

#: Default batch cap per sweep pass. DELIBERATELY SMALLER than the
#: stale-pause sweep's 100: this strand performs TERMINALIZING WRITES
#: through the production completion chain, so a tighter per-tick
#: bound keeps the blast radius of a mis-predicate small. 25 is the
#: same order as the pause-sweep cap while halving it.
DEFAULT_RUNNING_ZOMBIE_BATCH_CAP: int = 25

#: Upper bound on the permanent-tree size probed per candidate. A
#: tree larger than this is NOT probed completely ⇒ the candidate is
#: treated as PROTECTED (fail-closed skip) — same posture as
#: ``stale_pause_sweep._MAX_TREE_PROBE_SIZE``.
_RUNNING_ZOMBIE_MAX_TREE_PROBE_SIZE: int = 256

#: Instance statuses that mean "this instance is still doing something"
#: — the live-descendant CLASSIFICATION set. Mirrors
#: ``stale_pause_sweep._LIVE_STATUSES`` (the set is duplicated rather
#: than imported because ``stale_pause_sweep`` is a releaser module
#: and this module must not take a terminalizing dependency on it).
#:
#: NOTE — this is a CLASSIFICATION set, NOT the liveness authority:
#: the authority for "the tree must be closed" is
#: ``TERMINAL_INSTANCE_STATUSES`` (every descendant must be terminal).
#: This set only decides which SKIP CLASS a non-terminal descendant is
#: reported under. It therefore DELIBERATELY differs from
#: ``daemon.constants.ALIVE_INSTANCE_STATUSES`` (which includes
#: ``idle``/``paused`` — a "may still be doing something" set used by
#: the drift reconciler): an ``idle``/``paused`` descendant is
#: reported as ``non-terminal-descendant`` here, not as
#: ``live-descendant``. Both are skips. The three-set fork across
#: ``stale_pause_sweep`` / ``job_recovery_service`` / this module is a
#: named consolidation follow-up (hoist ONE definition into
#: ``daemon.constants``).
_RUNNING_ZOMBIE_LIVE_STATUSES: frozenset[str] = frozenset(
    {"running", "waiting_children", "queued", "waiting"}
)

#: Task statuses that mean "a turn is still in flight" — the presence
#: of ANY such row for the root means the root is NOT a zombie (the
#: turn owns the instance). ``processing`` is included defensively
#: (legacy task vocabulary).
_RUNNING_ZOMBIE_NON_TERMINAL_TASK_STATUSES: frozenset[str] = frozenset(
    {"pending", "running", "paused", "processing"}
)


class RunningZombieProbeError(RuntimeError):
    """Raised by the fail-closed pending-question probe.

    The stale-pause sweep's checker FAILS OPEN on a probe error (it
    only UN-pauses, and the operator is the safety net). This strand
    TERMINALIZES, so an unprovable probe must SKIP — the miss is
    recoverable on the next tick, a wrong terminal write is not.
    """


class MissionStrandHealer:
    """Periodic single-invocation healer for stranded mission trees.

    Args:
        manager: The InstanceManager facade. The healer consumes
            ``manager._instance_repository`` (tree walk + ghost
            probe), ``manager.engine`` (obligation counts),
            ``manager._lifecycle_service`` (the sanctioned
            terminate lane) and ``manager._child_reports_service``
            (the emission-machinery drive).
        interval_seconds: Sleep between sweep ticks. The first tick
            fires immediately at ``start()`` (fast on-boot catch-up,
            same convention as WatchReconcileSweepService).
        running_zombie_enabled: Kill-switch for the running-zombie
            strand (default True). When False the strand is a COMPLETE
            no-op — no candidate selection, no probe, no drive.
        running_zombie_age_bound_seconds: Age bound on the latest
            task row's ``completed_at``.
        running_zombie_batch_cap: Maximum candidates examined per
            sweep pass.
    """

    def __init__(
        self,
        manager: "InstanceManager",
        interval_seconds: int = (
            DEFAULT_MISSION_STRAND_HEAL_INTERVAL_SECONDS
        ),
        *,
        running_zombie_enabled: bool = True,
        running_zombie_age_bound_seconds: int = (
            DEFAULT_RUNNING_ZOMBIE_AGE_BOUND_SECONDS
        ),
        running_zombie_batch_cap: int = (
            DEFAULT_RUNNING_ZOMBIE_BATCH_CAP
        ),
    ):
        if interval_seconds <= 0:
            raise ValueError(
                f"interval_seconds must be > 0; got {interval_seconds!r}"
            )
        self._manager = manager
        self._interval_seconds = int(interval_seconds)
        self._running_zombie_enabled = bool(running_zombie_enabled)
        self._running_zombie_age_bound_seconds = self._coerce_int(
            running_zombie_age_bound_seconds,
            DEFAULT_RUNNING_ZOMBIE_AGE_BOUND_SECONDS,
        )
        self._running_zombie_batch_cap = self._coerce_int(
            running_zombie_batch_cap,
            DEFAULT_RUNNING_ZOMBIE_BATCH_CAP,
        )
        self._task: asyncio.Task | None = None
        self._stopping = False
        # Per-root heal serialization (final hardening 2026-10-14):
        # the 300s sweep and the event-driven downgrade hand-off
        # (``child_reports._schedule_strand_heal`` — a fire-and-forget
        # task) can target the SAME root concurrently. Exactly-once
        # job closure already holds; the lock kills the duplicate
        # lifecycle/SSE terminal-event noise from interleaved drives.
        # Bounded growth: refcounted entries — ``_heal_lock_refs``
        # counts interested callers per root; the LAST caller out pops
        # both entries (same hygiene family as the WC watchdog's
        # wedge cooldown map). Get-or-create + refcount bump contain
        # NO await points, so the sequence is atomic per event-loop
        # task — no caller can hold a stale lock reference across a
        # pop.
        self._heal_locks: dict[str, asyncio.Lock] = {}
        self._heal_lock_refs: dict[str, int] = {}

    # ── lifecycle (WatchReconcileSweepService contract) ──────────

    @staticmethod
    def _coerce_int(value: Any, default: int) -> int:
        """Coerce a config knob to ``int``, falling back to ``default``
        for a NON-NUMERIC value.

        A mocked config object (``MagicMock``) would otherwise coerce
        to ``1`` — silently turning a 2h age bound into a 1-SECOND one
        (a far more aggressive posture than the operator configured)
        and a batch cap of 1. The fallback keeps the documented
        defaults whenever the knob is not a real number.
        """
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return default
        return int(value)

    @property
    def interval_seconds(self) -> int:
        return self._interval_seconds

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stopping = False
            self._task = asyncio.create_task(
                self._run(), name="mission-strand-healer"
            )

    async def stop(self) -> None:
        self._stopping = True
        task = self._task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._task = None

    async def _run(self) -> None:
        try:
            while not self._stopping:
                try:
                    await self.sweep_once()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 — per-tick isolation
                    logger.error(
                        "MissionStrandHealer: sweep tick failed: %s: %s",
                        type(exc).__name__, exc,
                    )
                await asyncio.sleep(self._interval_seconds)
        except asyncio.CancelledError:
            logger.info("MissionStrandHealer: loop cancelled — shutting down")
            raise

    # ── sweep ────────────────────────────────────────────────────

    async def sweep_once(self) -> dict[str, Any]:
        """One heal pass over every WAITING_CHILDREN root, plus the
        running-zombie pass.

        Returns a stats dict: scanned / healed / skipped counts with
        per-root reasons (``skipped_roots`` bounded to the first 20
        for log hygiene).

        The running-zombie strand reports under its OWN stat keys
        (``running_zombie_*``) — the waiting_children keys keep their
        EXACT meaning so the existing strand's invariants (and the
        tests that pin them) are untouched by the widened selection.
        """
        stats: dict[str, Any] = {
            "scanned": 0,
            "healed": 0,
            "terminalized_orphans": 0,
            "skipped": 0,
            "errors": 0,
            "skipped_roots": [],
        }
        repo = self._manager._instance_repository
        if repo is None:
            return stats
        root_ids = await asyncio.to_thread(self._find_wc_root_ids)
        for root_id in root_ids:
            stats["scanned"] += 1
            try:
                outcome = await self.heal_instance(root_id)
            except Exception as exc:  # noqa: BLE001 — per-root isolation
                stats["errors"] += 1
                logger.error(
                    "MissionStrandHealer: heal of %s... raised %s: %s",
                    root_id[:8], type(exc).__name__, exc,
                )
                continue
            if outcome.get("healed"):
                stats["healed"] += 1
                stats["terminalized_orphans"] += len(
                    outcome.get("terminalized", [])
                )
            else:
                stats["skipped"] += 1
                if len(stats["skipped_roots"]) < 20:
                    stats["skipped_roots"].append(
                        {
                            "root_id": root_id,
                            "reason": outcome.get("reason"),
                        }
                    )
        if stats["healed"]:
            logger.warning(
                "MissionStrandHealer: healed %d wedged mission(s) "
                "(%d orphan(s) terminalized) of %d scanned",
                stats["healed"], stats["terminalized_orphans"],
                stats["scanned"],
            )

        # ── running-zombie strand (2026-10-21) ───────────────────
        # Run in the SAME tick (the healer owns one interval knob);
        # its own keys keep the waiting_children stats untouched.
        zombie_stats = await self.sweep_running_zombies_once()
        stats["running_zombie_scanned"] = zombie_stats["scanned"]
        stats["running_zombie_healed"] = zombie_stats["healed"]
        stats["running_zombie_skipped"] = zombie_stats["skipped"]
        stats["running_zombie_errors"] = zombie_stats["errors"]
        stats["running_zombie_skipped_roots"] = zombie_stats["skipped_roots"]
        return stats

    def _find_wc_root_ids(self) -> list[str]:
        with Session(self._manager.engine) as session:
            rows = session.exec(
                select(Instance.instance_id)
                .where(Instance.status == InstanceStatus.WAITING_CHILDREN.value)
                .where(Instance.parent_id.is_(None))
            ).all()
        return list(rows)

    # ── heal (single root — also the event-driven entry point) ───

    def _acquire_root_lock(self, root_id: str) -> asyncio.Lock:
        """Get-or-create the per-root heal lock and bump its refcount.

        The get-or-create + refcount bump contain NO await points, so
        the sequence is atomic per event-loop task — no caller can hold
        a stale lock reference across a pop. SHARED by the
        waiting_children strand and the running-zombie strand: a root
        can only be in ONE posture at a time, but sharing the lock map
        guarantees that two strands never drive the same root
        concurrently.
        """
        lock = self._heal_locks.get(root_id)
        if lock is None:
            lock = asyncio.Lock()
            self._heal_locks[root_id] = lock
        self._heal_lock_refs[root_id] = (
            self._heal_lock_refs.get(root_id, 0) + 1
        )
        return lock

    def _release_root_lock(self, root_id: str) -> None:
        """Drop the caller's interest in the per-root lock entry.

        The LAST interested caller out pops BOTH entries so the maps
        stay bounded (a later heal mints a fresh lock).
        """
        remaining = self._heal_lock_refs.get(root_id, 0) - 1
        if remaining > 0:
            # A queued caller still holds interest in this entry.
            self._heal_lock_refs[root_id] = remaining
        else:
            self._heal_lock_refs.pop(root_id, None)
            self._heal_locks.pop(root_id, None)

    async def heal_instance(self, root_id: str) -> dict[str, Any]:
        """Serialize per-root, then attempt ONE heal.

        The per-root ``asyncio.Lock`` makes concurrent invocations —
        the 300s sweep tick racing the event-driven downgrade
        hand-off — mutually exclusive per root: the second caller
        waits, then re-runs every invariant check against the
        post-heal state and observes it cleanly (typically the
        ``not the wedge posture`` skip). Lifecycle/SSE terminal
        events therefore fire EXACTLY ONCE per root heal. Lock-map
        entries are refcounted and popped by the last caller out
        (bounded growth).

        Returns the outcome dict of the single serialized heal:
        ``{"healed": bool, "reason": str, "terminalized": [ids],
        "drove": bool}``. NEVER raises for expected skip conditions —
        internal errors propagate (the sweep tick isolates them; the
        event-driven caller wraps fail-soft).
        """
        lock = self._acquire_root_lock(root_id)
        try:
            async with lock:
                return await self._heal_instance_locked(root_id)
        finally:
            self._release_root_lock(root_id)

    async def _heal_instance_locked(self, root_id: str) -> dict[str, Any]:
        """Attempt a heal of ONE mission root (caller holds the
        per-root lock). Returns an outcome dict:

        ``{"healed": bool, "reason": str, "terminalized": [ids], "drove": bool}``

        NEVER raises for expected skip conditions — internal errors
        propagate (the sweep tick isolates them; the event-driven
        caller wraps fail-soft). Every skip is fail-CLOSED: the
        invariant legs are all-must-hold.
        """
        repo = self._manager._instance_repository
        root = await asyncio.to_thread(repo.get, root_id)
        if root is None:
            return {"healed": False, "reason": "root row missing"}
        if root.status != InstanceStatus.WAITING_CHILDREN.value:
            return {
                "healed": False,
                "reason": f"root status {root.status!r} is not the "
                          f"wedge posture (waiting_children)",
            }
        if root.parent_id is not None:
            return {"healed": False, "reason": "not a root instance"}

        # ── Zero-obligation legs ─────────────────────────────────
        # (1) dependency bus: no PENDING watchers targeting the root.
        from daemon.services.dependency_bus import get_dependency_bus
        bus = get_dependency_bus()
        if bus is not None:
            try:
                bus_pending = await asyncio.to_thread(
                    bus.count_pending_for_target_sync, root_id
                )
            except Exception:
                # Fail-open here (transient bus errors): the DRIVE's
                # own in-session bus gate is the authoritative
                # fail-closed check — treating the leg as 0 merely
                # reaches the drive, which re-verifies.
                bus_pending = 0
            if bus_pending > 0:
                return {
                    "healed": False,
                    "reason": f"bus_pending={bus_pending}",
                }

        # (2) root's own queue: no READY/PROCESSING/RETRYING messages.
        pending_msgs = await asyncio.to_thread(
            self._count_pending_messages, root_id
        )
        if pending_msgs > 0:
            return {
                "healed": False,
                "reason": f"pending_messages={pending_msgs}",
            }

        # (3) root's tasks: no PENDING work (an in-flight turn is a
        # live carrier — the wedge will resolve event-driven).
        pending_tasks = await asyncio.to_thread(
            self._count_pending_tasks, root_id
        )
        if pending_tasks > 0:
            return {
                "healed": False,
                "reason": f"pending_tasks={pending_tasks}",
            }

        # (4) declared-wait obligations: PENDING/DEFERRED
        # report_injections rows obligated to this root are the (b)
        # guard's episode — the L6 machinery owns those roots
        # (escalate-at-bind), not this healer.
        open_injections = await asyncio.to_thread(
            self._count_open_report_injections, root_id
        )
        if open_injections > 0:
            return {
                "healed": False,
                "reason": f"open_report_injections={open_injections}",
            }

        # ── Strand-only tree legs ────────────────────────────────
        tree_ids = await asyncio.to_thread(
            repo.get_tree_ids_permanent, root_id
        )
        members = await asyncio.to_thread(self._load_members, tree_ids)
        # Drop vanished rows (concurrent delete) and the root itself.
        members = [m for m in members if m is not None]
        non_root = [m for m in members if m.instance_id != root_id]

        non_terminal = [
            m for m in non_root
            if m.status is not None
            and m.status not in TERMINAL_INSTANCE_STATUSES
        ]
        if not non_terminal:
            # Already all-terminal below the root: the self-
            # referential hold (the root's own WC) is the only
            # blocker — go straight to the drive.
            orphans: list[Instance] = []
        else:
            # Batched ghost probe (canonical never-dispatched
            # signature: idle + v1 + zero messages + zero tasks).
            candidate_ids = [m.instance_id for m in non_terminal]
            try:
                ghost_ids = await asyncio.to_thread(
                    repo.filter_never_dispatched_ids, candidate_ids
                )
            except Exception as exc:  # noqa: BLE001 — fail-closed
                return {
                    "healed": False,
                    "reason": f"never-dispatched probe failed: "
                              f"{type(exc).__name__}: {exc}",
                }
            status_by_id = {m.instance_id: m.status for m in members}
            from daemon.services.mission_live_guard import (
                ORPHAN_MIN_AGE_SECONDS,
                strand_past_race_guard,
            )
            now_naive = await asyncio.to_thread(self._now_naive)
            orphans = []
            for m in non_terminal:
                if m.instance_id not in ghost_ids:
                    # Not never-dispatched-shaped: a genuinely-live
                    # (or at least execution-evidence-bearing)
                    # member — NEVER swept. Abort the whole heal.
                    return {
                        "healed": False,
                        "reason": (
                            f"member {m.instance_id[:8]}... "
                            f"(status={m.status!r}) is not "
                            f"never-dispatched — fail-closed"
                        ),
                    }
                parent_status = status_by_id.get(m.parent_id)
                if (
                    m.parent_id is None
                    or parent_status is None
                    or parent_status not in TERMINAL_INSTANCE_STATUSES
                ):
                    # SAFETY BOUND: live/unknown direct parent — the
                    # spawn-then-message ritual may still deliver.
                    return {
                        "healed": False,
                        "reason": (
                            f"member {m.instance_id[:8]}... direct "
                            f"parent {str(m.parent_id)[:8]}... status "
                            f"{parent_status!r} is not terminal — "
                            f"safety bound"
                        ),
                    }
                if not strand_past_race_guard(
                    getattr(m, "updated_at", None), now=now_naive,
                ):
                    # Race guard (shared predicate — the read-side
                    # liveness exclusion in mission_live_guard runs
                    # the exact same helper): the strand is too fresh
                    # to distinguish from a mid-flight ritual.
                    # Re-check next tick.
                    return {
                        "healed": False,
                        "reason": (
                            f"member {m.instance_id[:8]}... strand is "
                            f"younger than {ORPHAN_MIN_AGE_SECONDS}s — "
                            f"race guard"
                        ),
                    }
                orphans.append(m)

        # ── Write side: lifecycle-lane terminalization ───────────
        terminalized: list[str] = []
        lifecycle = getattr(self._manager, "_lifecycle_service", None)
        if orphans:
            if lifecycle is None:
                return {
                    "healed": False,
                    "reason": "lifecycle service unwired — cannot "
                              "terminalize via the sanctioned lane",
                }
            for orphan in orphans:
                ok = await lifecycle.terminate_instance(
                    orphan.instance_id,
                    terminal_reason="orphan_retired",
                )
                if not ok:
                    return {
                        "healed": False,
                        "reason": (
                            f"terminate_instance returned False for "
                            f"{orphan.instance_id[:8]}... — aborting "
                            f"(fail-closed)"
                        ),
                    }
                terminalized.append(orphan.instance_id)
                logger.warning(
                    "MissionStrandHealer: terminalized stranded "
                    "never-dispatched orphan %s... (direct parent "
                    "terminal; reason=orphan_retired, sanctioned "
                    "lifecycle lane)",
                    orphan.instance_id[:8],
                )

        # ── Drive the zero-obligation WC root through the existing
        #    emission machinery (stamp → gate → publish). The
        #    self-referential hold (the root's own WC status) is the
        #    last non-terminal member; the drive's discharge+stamp
        #    flips it, the strand-aware recursive gate passes, and
        #    the publish chain (lifecycle event → observer finalize
        #    → fresh result → watcher claim) re-enters naturally.
        child_reports = getattr(
            self._manager, "_child_reports_service", None
        )
        if child_reports is None:
            return {
                "healed": True,
                "reason": (
                    f"{len(terminalized)} orphan(s) terminalized but "
                    f"child_reports service unwired — root drive "
                    f"skipped"
                ),
                "terminalized": terminalized,
                "drove": False,
            }
        latest_msg_id = await asyncio.to_thread(
            self._latest_completed_message_id, root_id
        )
        await child_reports._process_child_completion_and_notify_parent(
            root_id, latest_msg_id or "",
        )
        drove_row = await asyncio.to_thread(repo.get, root_id)
        healed = bool(
            drove_row is not None
            and drove_row.status in TERMINAL_INSTANCE_STATUSES
        )
        return {
            "healed": healed,
            "reason": (
                f"drove root through emission machinery "
                f"(orphans_terminalized={len(terminalized)}); post-"
                f"drive status={drove_row.status if drove_row else None!r}"
            ),
            "terminalized": terminalized,
            "drove": True,
        }

    # ── running-zombie strand (2026-10-21) ───────────────────────
    #
    # Third member of the strand family. The two shapes above heal a
    # root parked in ``waiting_children``; this one heals a root parked
    # in ``running`` whose completion EVENT was lost (live incident
    # 2026-10-19/20, instance 432988b8 — the root's own latest task
    # row is terminal-COMPLETED and every descendant is terminal, yet
    # the instance row stayed ``running`` for 25h).
    #
    # SELECTION WIDENING MUST NOT WEAKEN the existing shapes: the
    # waiting_children / ghost strands keep their exact invariants
    # (see ``_heal_instance_locked`` above). This strand is a SEPARATE
    # predicate over a SEPARATE posture (``running``, not
    # ``waiting_children``) with its own candidate selection, its own
    # skip classes and its own drive path.

    async def sweep_running_zombies_once(self) -> dict[str, Any]:
        """One pass over every RUNNING mission root.

        Returns a stats dict: ``scanned`` / ``healed`` / ``skipped`` /
        ``errors`` counts plus per-root skip reasons
        (``skipped_roots`` bounded to the first 20 for log hygiene).
        """
        stats: dict[str, Any] = {
            "scanned": 0,
            "healed": 0,
            "skipped": 0,
            "errors": 0,
            "skipped_roots": [],
        }
        if not self._running_zombie_enabled:
            logger.warning(
                "mission_strand_healer|running-zombie: SKIP "
                "class=kill-switch detail=disabled by config "
                "(SERVICES_MISSION_STRAND_HEAL_RUNNING_ZOMBIE_ENABLED=false)"
            )
            return stats
        repo = self._manager._instance_repository
        if repo is None:
            return stats
        try:
            root_ids = await asyncio.to_thread(self._find_running_root_ids)
        except Exception as exc:  # noqa: BLE001 — fail-closed
            stats["errors"] += 1
            logger.warning(
                "mission_strand_healer|running-zombie: SKIP "
                "class=selection-error detail=%s: %s",
                type(exc).__name__, exc,
            )
            return stats
        for root_id in root_ids:
            stats["scanned"] += 1
            try:
                outcome = await self.heal_running_zombie(root_id)
            except Exception as exc:  # noqa: BLE001 — per-root isolation
                stats["errors"] += 1
                logger.error(
                    "mission_strand_healer|running-zombie: heal of %s "
                    "raised %s: %s",
                    root_id, type(exc).__name__, exc,
                )
                continue
            if outcome.get("healed"):
                stats["healed"] += 1
            else:
                stats["skipped"] += 1
                if len(stats["skipped_roots"]) < 20:
                    stats["skipped_roots"].append(
                        {
                            "root_id": root_id,
                            "reason": outcome.get("reason"),
                            "skip_class": outcome.get("skip_class"),
                        }
                    )
        if stats["healed"]:
            logger.warning(
                "mission_strand_healer|running-zombie: healed %d "
                "running-zombie mission(s) of %d scanned",
                stats["healed"], stats["scanned"],
            )
        return stats

    def _find_running_root_ids(self) -> list[str]:
        """RUNNING mission roots — the ANCHORED running-zombie candidate
        set.

        The POSTURE stays deliberately WIDE (``status == running`` AND
        ``parent_id IS NULL``): the predicate legs — not the selection
        — decide what is a zombie. Narrowing the posture here would
        hide the shape from the logs (the sweep would be silent about
        a stranded root).

        The ANCHOR is the completion proof: only a running root whose
        LATEST task row is terminal-COMPLETED (with a ``completed_at``
        stamp) is a candidate. The anchor is what keeps the
        un-healable population out of the batch.

        WHY THE ANCHOR (F1 — the starvation fix): the batch cap bounds
        the candidates EXAMINED per tick, and a candidate that is
        examined and SKIPPED keeps its sort key — so it is examined
        again on the next tick, forever. Under the previous raw-
        ``status='running'`` selection the head of the batch was
        therefore occupied PERMANENTLY by roots that can never satisfy
        the completion proof (no task row at all, a latest task row
        that is not COMPLETED, a live turn in flight): with more such
        roots than the cap, a real zombie past position ``cap`` was
        NEVER examined. The old "no candidate starves" claim was
        FALSE.

        FAIRNESS / LIVENESS (the TRUE property — do not overstate):
        the cap bounds EXAMINATION of the ANCHORED set per tick,
        ordered OLDEST-``task.completed_at`` first (the most-stranded
        roots are served first). An anchored candidate that is SKIPPED
        keeps its anchor, so it is re-examined on the next tick — it
        re-surfaces as the candidates ahead of it drain, and every skip
        cause that CAN clear clears (a live descendant that resolves, a
        pending question that is answered, a young turn that ages, a
        busy queue that empties). Only the STRUCTURAL skip causes — a
        permanent tree over the probe bound and a permanently live
        descendant — hold a slot indefinitely; each consumes one slot,
        so a backlog of such roots larger than the cap is the only case
        where the cap, not the strand, bounds progress.

        The age bound is applied HERE (in Python), NOT in SQL: the
        completion stamp is a naive-UTC ``timestamp without time zone``
        column and the SQLite dialect stores it as TEXT, so a SQL
        comparison against a bound datetime is a STRING comparison
        there and is NOT chronologically sound. The filter uses the
        SAME ``_age_seconds`` helper the predicate uses, so the two
        can never disagree. It can only ever DROP a candidate from the
        window (the window holds the OLDEST anchors, and the aged ones
        ARE the oldest ones), so it can never hide an aged zombie.
        """
        latest_task_id = (
            select(Task.id)
            .where(Task.instance_id == Instance.instance_id)
            .order_by(Task.created_at.desc(), Task.id.desc())
            .limit(1)
            .correlate(Instance)
            .scalar_subquery()
        )
        with Session(self._manager.engine) as session:
            rows = session.exec(
                select(Instance.instance_id, Task.completed_at)
                .join(Task, Task.id == latest_task_id)
                .where(Instance.status == InstanceStatus.RUNNING.value)
                .where(Instance.parent_id.is_(None))
                .where(Task.status == TaskStatus.COMPLETED.value)
                .where(Task.completed_at.isnot(None))
                .order_by(Task.completed_at.asc())
                .limit(self._running_zombie_batch_cap)
            ).all()
        now_naive = self._now_naive()
        aged: list[tuple[float, str]] = []
        for instance_id, completed_at in rows:
            age = self._age_seconds(completed_at, now_naive)
            if age is None or age < self._running_zombie_age_bound_seconds:
                continue
            aged.append((age, instance_id))
        # OLDEST completion first — ``age`` DESCENDING.
        aged.sort(key=lambda item: (-item[0], item[1]))
        return [instance_id for _, instance_id in aged]

    async def heal_running_zombie(self, root_id: str) -> dict[str, Any]:
        """Serialize per-root, then attempt ONE running-zombie heal.

        Shares the per-root lock map with the waiting_children strand
        (``_acquire_root_lock``) so two strands can never drive the
        same root concurrently.

        Returns the outcome dict: ``{"healed": bool, "reason": str,
        "skip_class": str | None, "drove": bool}``. NEVER raises for
        expected skip conditions — internal errors propagate (the
        sweep tick isolates them per root).
        """
        lock = self._acquire_root_lock(root_id)
        try:
            async with lock:
                return await self._heal_running_zombie_locked(root_id)
        finally:
            self._release_root_lock(root_id)

    async def _heal_running_zombie_locked(self, root_id: str) -> dict[str, Any]:
        """Attempt a heal of ONE running-zombie root (caller holds the
        per-root lock).

        Every leg is all-must-hold and fail-CLOSED: a leg that cannot
        be evaluated SKIPS the candidate (a missed heal is recoverable
        on the next tick; a wrong terminal write is not).
        """
        repo = self._manager._instance_repository
        if repo is None:
            return self._rz_skip(
                root_id, "unwired", "instance repository unwired",
            )
        root = await asyncio.to_thread(repo.get, root_id)
        if root is None:
            return self._rz_skip(
                root_id, "root-missing", "root row missing",
            )
        if root.parent_id is not None:
            return self._rz_skip(
                root_id, "not-root",
                "parent_id is not NULL — non-root running shapes are "
                "out of scope",
            )
        if root.status != InstanceStatus.RUNNING.value:
            return self._rz_skip(
                root_id, "not-running",
                f"root status {root.status!r} is not the zombie posture "
                f"(running)",
            )

        # ── Legs 2 + 3: the completion proof (task rows) ───────────
        task_probe = await asyncio.to_thread(
            self._probe_task_rows, root_id
        )
        if not task_probe["has_any"]:
            # PREDICATE CORRECTION #2: a root with NO task row has no
            # completion proof — indistinguishable from a root that
            # never started. NEVER swept (deliberate miss).
            return self._rz_skip(
                root_id, "no-task-row",
                "no task row — no completion proof (deliberate miss)",
            )
        latest_task = task_probe["latest"]
        if latest_task is None:
            return self._rz_skip(
                root_id, "no-task-row",
                "no task row — no completion proof (deliberate miss)",
            )
        if (latest_task.status or "") != TaskStatus.COMPLETED.value:
            return self._rz_skip(
                root_id, "latest-task-not-completed",
                f"latest task {latest_task.work_id} status="
                f"{latest_task.status!r} is not terminal-COMPLETED",
            )
        if latest_task.completed_at is None:
            return self._rz_skip(
                root_id, "latest-task-no-completed-at",
                f"latest task {latest_task.work_id} has no completed_at "
                f"stamp",
            )
        if task_probe["non_terminal"] > 0:
            return self._rz_skip(
                root_id, "non-terminal-task",
                f"{task_probe['non_terminal']} non-terminal task row(s) "
                f"exist for the root — a turn is still in flight",
            )

        # ── Leg 3: the age bound (on task.completed_at) ───────────
        # PREDICATE CORRECTION #1: the age keys on the TASK's
        # completed_at — NOT ``instances.last_activity_at``, which is
        # frozen-by-design for a running root and cannot distinguish a
        # zombie from a long-legitimate turn.
        now_naive = await asyncio.to_thread(self._now_naive)
        completed_age = self._age_seconds(
            latest_task.completed_at, now_naive
        )
        if completed_age is None:
            return self._rz_skip(
                root_id, "latest-task-no-completed-at",
                f"latest task {latest_task.work_id} completed_at is "
                f"unreadable",
            )
        if completed_age < self._running_zombie_age_bound_seconds:
            return self._rz_skip(
                root_id, "young",
                f"completed_age={int(completed_age)}s < bound "
                f"{self._running_zombie_age_bound_seconds}s — the turn "
                f"is too recent to be a zombie",
            )

        # ── Leg 4: the whole permanent tree, terminal and complete ─
        try:
            tree_ids = await asyncio.to_thread(
                repo.get_tree_ids_permanent, root_id
            )
        except Exception as exc:  # noqa: BLE001 — fail-closed
            return self._rz_skip(
                root_id, "tree-probe-error",
                f"tree enumeration failed: {type(exc).__name__}: {exc}",
            )
        probe_ids = list(dict.fromkeys([root_id, *(tree_ids or [])]))
        if not probe_ids:
            return self._rz_skip(
                root_id, "tree-empty",
                "permanent tree enumeration returned no members",
            )
        if len(probe_ids) > _RUNNING_ZOMBIE_MAX_TREE_PROBE_SIZE:
            return self._rz_skip(
                root_id, "tree-truncated",
                f"tree size {len(probe_ids)} exceeds the probe bound "
                f"{_RUNNING_ZOMBIE_MAX_TREE_PROBE_SIZE}",
            )
        # The repo helper only WARNs when its internal DEPTH cap is
        # hit — a WARN-only signal the caller cannot act on. Detect the
        # truncation HERE (Python-side closure check): every member's
        # permanent child list must be inside the enumerated set.
        try:
            closed, closure_detail = await asyncio.to_thread(
                self._tree_is_closed, repo, probe_ids
            )
        except Exception as exc:  # noqa: BLE001 — fail-closed
            return self._rz_skip(
                root_id, "tree-probe-error",
                f"tree closure probe failed: {type(exc).__name__}: {exc}",
            )
        if not closed:
            return self._rz_skip(
                root_id, "tree-truncated", closure_detail,
            )

        descendants = [pid for pid in probe_ids if pid != root_id]
        try:
            descendant_statuses = await asyncio.to_thread(
                repo.get_statuses, descendants
            )
        except Exception as exc:  # noqa: BLE001 — fail-closed
            return self._rz_skip(
                root_id, "tree-probe-error",
                f"descendant status read failed: "
                f"{type(exc).__name__}: {exc}",
            )
        unreadable = [
            pid for pid in descendants if pid not in descendant_statuses
        ]
        if unreadable:
            return self._rz_skip(
                root_id, "tree-incomplete",
                f"{len(unreadable)} descendant row(s) unreadable — the "
                f"tree view is not complete",
            )
        live_descendants = [
            pid for pid, status in descendant_statuses.items()
            if status in _RUNNING_ZOMBIE_LIVE_STATUSES
        ]
        if live_descendants:
            return self._rz_skip(
                root_id, "live-descendant",
                f"{len(live_descendants)} live descendant(s) — the tree "
                f"is not closed",
            )
        non_terminal_descendants = [
            pid for pid, status in descendant_statuses.items()
            if status not in TERMINAL_INSTANCE_STATUSES
        ]
        if non_terminal_descendants:
            return self._rz_skip(
                root_id, "non-terminal-descendant",
                f"{len(non_terminal_descendants)} non-terminal "
                f"descendant(s) — the tree is not closed",
            )

        # ── Leg 5: no pending question anywhere in the tree ───────
        # The status map spans the WHOLE tree (root + descendants) —
        # the question probe needs the ROOT's liveness too (the root
        # is alive by construction: it is ``running``).
        statuses: dict[str, str] = {root_id: root.status}
        statuses.update(descendant_statuses)
        try:
            has_pending = await self._pending_question_in_tree(
                probe_ids, statuses,
            )
        except RunningZombieProbeError as exc:
            return self._rz_skip(
                root_id, "question-probe-error", str(exc),
            )
        if has_pending:
            return self._rz_skip(
                root_id, "pending-question",
                "an outstanding question in the tree still owns the "
                "mission",
            )

        # ── Leg 6: pre-drive liveness gates ───────────────────────
        # (a) dependency bus: no PENDING watchers targeting the root.
        from daemon.services.dependency_bus import get_dependency_bus
        bus = get_dependency_bus()
        if bus is None:
            # The bus singleton is the SOLE pending-children
            # authority — an unwired bus is an INVALID state (the
            # drive chain raises on it). Skip (fail-closed).
            return self._rz_skip(
                root_id, "bus-unwired",
                "dependency bus singleton is not initialized — the "
                "pending-children authority is unprovable",
            )
        try:
            bus_pending = await asyncio.to_thread(
                bus.count_pending_for_target_sync, root_id
            )
        except Exception as exc:  # noqa: BLE001 — fail-closed
            return self._rz_skip(
                root_id, "bus-probe-error",
                f"bus pending probe failed: {type(exc).__name__}: {exc}",
            )
        if bus_pending > 0:
            return self._rz_skip(
                root_id, "bus-pending",
                f"bus_pending={bus_pending}",
            )

        # (b) root's own queue: no READY/PROCESSING/RETRYING messages.
        pending_msgs = await asyncio.to_thread(
            self._count_pending_messages, root_id
        )
        if pending_msgs > 0:
            return self._rz_skip(
                root_id, "pending-messages",
                f"pending_messages={pending_msgs}",
            )

        # ── Drive the PRODUCTION completion chain ─────────────────
        # NEVER a raw status UPDATE, NEVER ``terminate_instance`` on
        # the root: the chain re-runs every gate authoritatively
        # (bus-pending in-transaction, pending-task, live-children
        # cross-check, pending messages, the emission-time gate and
        # the observer's JobItem/instance/lock cascade).
        child_reports = getattr(
            self._manager, "_child_reports_service", None
        )
        if child_reports is None:
            return self._rz_skip(
                root_id, "child-reports-unwired",
                "child_reports service unwired — cannot drive the "
                "production completion chain",
            )
        latest_msg_id = await asyncio.to_thread(
            self._latest_completed_message_id, root_id
        )
        try:
            await child_reports._process_child_completion_and_notify_parent(
                root_id, latest_msg_id or "",
            )
        except Exception as exc:  # noqa: BLE001 — fail-closed
            return self._rz_skip(
                root_id, "drive-error",
                f"drive raised {type(exc).__name__}: {exc}",
            )
        drove_row = await asyncio.to_thread(repo.get, root_id)
        drove_status = drove_row.status if drove_row is not None else None
        if drove_status in TERMINAL_INSTANCE_STATUSES:
            logger.warning(
                "mission_strand_healer|running-zombie: instance=%s "
                "task=%s completed_age=%s tree=%s transitioned=completed "
                "reason=finalize_gap",
                root_id, latest_task.work_id, int(completed_age),
                len(probe_ids),
            )
            return {
                "healed": True,
                "reason": "finalize_gap",
                "skip_class": None,
                "drove": True,
                "task_id": latest_task.work_id,
                "completed_age": int(completed_age),
                "tree": len(probe_ids),
                "status": drove_status,
            }
        # The emission gate downgraded the stamp (COMPLETED →
        # waiting_children) — a LEGITIMATE MISS. Log it and retry the
        # next tick; NEVER force the transition.
        return self._rz_skip(
            root_id, "gate-block",
            f"post-drive status={drove_status!r} — the emission gate "
            f"blocked the publish (legitimate miss)",
        )

    def _rz_skip(
        self, root_id: str, skip_class: str, detail: str,
    ) -> dict[str, Any]:
        """Loud, greppable WARN for EVERY running-zombie skip class."""
        logger.warning(
            "mission_strand_healer|running-zombie: instance=%s SKIP "
            "class=%s detail=%s",
            root_id, skip_class, detail,
        )
        return {
            "healed": False,
            "reason": f"{skip_class}: {detail}",
            "skip_class": skip_class,
            "drove": False,
        }

    # ── sync helpers (worker-thread bridged) ─────────────────────

    def _count_pending_messages(self, instance_id: str) -> int:
        with Session(self._manager.engine) as session:
            return int(
                session.scalar(
                    select(func.count())
                    .select_from(MessageQueue)
                    .where(MessageQueue.instance_id == instance_id)
                    .where(MessageQueue.status.in_([
                        MessageStatus.READY.value,
                        MessageStatus.PROCESSING.value,
                        MessageStatus.RETRYING.value,
                    ]))
                ) or 0
            )

    def _count_pending_tasks(self, instance_id: str) -> int:
        with Session(self._manager.engine) as session:
            return int(
                session.scalar(
                    select(func.count())
                    .select_from(Task)
                    .where(Task.instance_id == instance_id)
                    .where(Task.status == TaskStatus.PENDING.value)
                ) or 0
            )

    def _count_open_report_injections(self, parent_id: str) -> int:
        from daemon.repositories.report_injection.models import (
            ReportInjection,
            ReportInjectionState,
        )
        with Session(self._manager.engine) as session:
            return int(
                session.scalar(
                    select(func.count())
                    .select_from(ReportInjection)
                    .where(
                        ReportInjection.parent_instance_id == parent_id
                    )
                    .where(ReportInjection.state.in_([
                        ReportInjectionState.PENDING.value,
                        ReportInjectionState.DEFERRED.value,
                    ]))
                ) or 0
            )

    def _load_members(
        self, tree_ids: list[str]
    ) -> list[Instance | None]:
        out: list[Instance | None] = []
        with Session(self._manager.engine) as session:
            for tid in tree_ids:
                out.append(session.get(Instance, tid))
        return out

    def _latest_completed_message_id(self, instance_id: str) -> str | None:
        with Session(self._manager.engine) as session:
            row = session.exec(
                select(MessageQueue.message_id)
                .where(MessageQueue.instance_id == instance_id)
                .where(MessageQueue.status == MessageStatus.COMPLETED.value)
                .order_by(MessageQueue.completed_at.desc())  # type: ignore[union-attr]
                .limit(1)
            ).first()
        return row

    @staticmethod
    def _now_naive():
        from daemon.services.timestamps import now_utc_naive
        return now_utc_naive()

    # ── running-zombie sync helpers (worker-thread bridged) ───────

    def _probe_task_rows(self, instance_id: str) -> dict[str, Any]:
        """Probe the instance's task rows.

        Returns ``{"has_any": bool, "latest": Task | None,
        "non_terminal": int}``:

        * ``has_any`` — the instance has AT LEAST ONE task row (no row
          ⇒ no completion proof ⇒ the caller must skip).
        * ``latest`` — the most recent task row (ordered by
          ``created_at`` DESC, ``id`` DESC as tie-break).
        * ``non_terminal`` — how many task rows carry a non-terminal
          status (a live turn owns the instance).
        """
        with Session(self._manager.engine) as session:
            rows = session.exec(
                select(Task)
                .where(Task.instance_id == instance_id)
                .order_by(Task.created_at.desc(), Task.id.desc())
            ).all()
            latest = rows[0] if rows else None
            non_terminal = sum(
                1 for row in rows
                if (row.status or "") in (
                    _RUNNING_ZOMBIE_NON_TERMINAL_TASK_STATUSES
                )
            )
        return {
            "has_any": bool(rows),
            "latest": latest,
            "non_terminal": non_terminal,
        }

    @staticmethod
    def _age_seconds(
        completed_at: datetime | None, now_naive: datetime,
    ) -> float | None:
        """Age of a (naive-UTC) completion stamp, in seconds.

        ``Task.completed_at`` stores NAIVE-UTC digits (the column is
        ``timestamp without time zone`` on PostgreSQL — see
        ``daemon/repositories/task/models.py``). An aware value (some
        dialects round-trip tzinfo) is normalized to naive-UTC before
        the subtraction.
        """
        if completed_at is None:
            return None
        value = completed_at
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        try:
            delta = now_naive - value
        except TypeError:
            return None
        return delta.total_seconds()

    def _tree_is_closed(
        self, repo, probe_ids: list[str],
    ) -> tuple[bool, str]:
        """Detect DEPTH truncation of the permanent-tree enumeration.

        ``get_tree_ids_permanent`` WARNs (and returns a PARTIAL set)
        when its internal depth cap is hit — a WARN-only signal the
        caller cannot act on. This closure check re-walks each member's
        permanent child list and asserts every child is inside the
        enumerated set. A missing child ⇒ the enumeration was
        truncated ⇒ the caller must SKIP (fail-closed).

        Mirrors the breadth-truncation detection of
        ``stale_pause_sweep.py:186-193`` (which cuts the probe set at
        the bound) — here the BOUND is enforced by the caller and the
        DEPTH leg is caught here.
        """
        member_set = set(probe_ids)
        for member_id in probe_ids:
            children = repo.list_child_ids_permanent(member_id)
            for child_id in children or []:
                if child_id not in member_set:
                    return False, (
                        f"child {child_id[:8]}... of {member_id[:8]}... "
                        f"is missing from the enumerated tree — the "
                        f"permanent-tree walk was depth-truncated"
                    )
        return True, "closed"

    async def _pending_question_in_tree(
        self, probe_ids: list[str], statuses: dict[str, str],
    ) -> bool:
        """FAIL-CLOSED tree-scoped pending-question probe.

        Mirrors the checker pattern of
        ``daemon/services/stale_pause_sweep.py:148-268`` — the RAM
        QuestionManager packs, the durable ``question_pack_payload``
        metadata shadow, and the durable ``awaiting_answer`` task
        handle — with INVERTED error semantics:

        * the stale-pause sweep FAILS OPEN on a probe error (it only
          UN-pauses; the operator is the safety net);
        * this strand TERMINALIZES, so an unprovable probe must SKIP.

        A pack owned by a DEAD (terminal) member is NOT outstanding —
        the durable shadow is cleared only at answer consumption, so a
        terminal asker keeps a stale shadow forever.

        Raises:
            RunningZombieProbeError: any probe error (fail-closed).
        """
        repo = self._manager._instance_repository

        def _owner_is_alive(owner_id: str) -> bool:
            return statuses.get(owner_id) not in TERMINAL_INSTANCE_STATUSES

        # (a) RAM QuestionManager packs.
        qm = getattr(self._manager, "_question_manager", None)
        if qm is not None:
            try:
                pending = await asyncio.to_thread(
                    qm.get_pending_packs_for_instances, probe_ids
                )
            except Exception as exc:  # noqa: BLE001 — fail-closed
                raise RunningZombieProbeError(
                    f"RAM pending-pack probe failed: "
                    f"{type(exc).__name__}: {exc}"
                ) from exc
            for owner_id in pending or {}:
                if _owner_is_alive(owner_id):
                    return True

        # (b) durable metadata shadow (``question_pack_payload``).
        try:
            from daemon.services.midflight_qa import (
                QUESTION_PACK_PAYLOAD_METADATA_KEY,
            )
        except Exception as exc:  # noqa: BLE001 — fail-closed
            raise RunningZombieProbeError(
                f"question-pack metadata key import failed: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
        for member_id in probe_ids:
            try:
                payload = await asyncio.to_thread(
                    repo.get_metadata_value,
                    member_id,
                    QUESTION_PACK_PAYLOAD_METADATA_KEY,
                )
            except Exception as exc:  # noqa: BLE001 — fail-closed
                raise RunningZombieProbeError(
                    f"durable shadow probe failed for {member_id[:8]}...: "
                    f"{type(exc).__name__}: {exc}"
                ) from exc
            if (
                isinstance(payload, dict)
                and payload.get("status") == "pending"
                and _owner_is_alive(member_id)
            ):
                return True

        # (c) the durable ``awaiting_answer`` suspension handle.
        if await asyncio.to_thread(
            self._has_awaiting_answer_handle, probe_ids
        ):
            return True
        return False

    def _has_awaiting_answer_handle(self, probe_ids: list[str]) -> bool:
        """True when any tree member holds an ``awaiting_answer`` handle.

        Mirrors the authoritative handle shape of
        ``TaskRepository.find_suspended_turn_for_answer``
        (``daemon/repositories/task/repository.py:770-830``):
        ``suspension_reason = 'awaiting_answer'`` AND a non-null
        ``resume_target_turn_id`` AND ``status = 'paused'``.
        """
        from daemon.repositories.task.models import SuspensionReason

        if not probe_ids:
            return False
        with Session(self._manager.engine) as session:
            count = int(
                session.scalar(
                    select(func.count())
                    .select_from(Task)
                    .where(Task.instance_id.in_(list(probe_ids)))
                    .where(
                        Task.suspension_reason
                        == SuspensionReason.AWAITING_ANSWER.value
                    )
                    .where(Task.resume_target_turn_id.isnot(None))
                    .where(Task.status == TaskStatus.PAUSED.value)
                ) or 0
            )
        return count > 0
