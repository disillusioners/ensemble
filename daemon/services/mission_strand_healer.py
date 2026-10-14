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
"""

from __future__ import annotations

import asyncio
import logging
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
    """

    def __init__(
        self,
        manager: "InstanceManager",
        interval_seconds: int = (
            DEFAULT_MISSION_STRAND_HEAL_INTERVAL_SECONDS
        ),
    ):
        if interval_seconds <= 0:
            raise ValueError(
                f"interval_seconds must be > 0; got {interval_seconds!r}"
            )
        self._manager = manager
        self._interval_seconds = int(interval_seconds)
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
        """One heal pass over every WAITING_CHILDREN root.

        Returns a stats dict: scanned / healed / skipped counts with
        per-root reasons (``skipped_roots`` bounded to the first 20
        for log hygiene).
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
        lock = self._heal_locks.get(root_id)
        if lock is None:
            lock = asyncio.Lock()
            self._heal_locks[root_id] = lock
        self._heal_lock_refs[root_id] = (
            self._heal_lock_refs.get(root_id, 0) + 1
        )
        try:
            async with lock:
                return await self._heal_instance_locked(root_id)
        finally:
            remaining = self._heal_lock_refs.get(root_id, 0) - 1
            if remaining > 0:
                # A queued caller still holds interest in this entry.
                self._heal_lock_refs[root_id] = remaining
            else:
                # Last interested caller out — drop the entry so the
                # maps stay bounded (a later heal mints a fresh lock).
                self._heal_lock_refs.pop(root_id, None)
                self._heal_locks.pop(root_id, None)

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
