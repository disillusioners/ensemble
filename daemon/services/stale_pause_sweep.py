"""Stale-pause backstop sweeper (R3).

Feature: ``fix/question-pause-resume-asymmetry``.

THE GAP THIS FILLS
------------------
``ask_questions`` parks the asker's whole tree. The answer path
un-pauses the asker (and, since this commission, the RECORDED
side-effect set). But there are holes the symmetric path cannot close:

* the answer was delivered BEFORE the deferred pause landed (the pause
  then has no answering counterpart);
* the daemon restarted between the ask and the answer;
* a legacy row / a stranded instance from before the fix;
* any other path that parked an instance with no live turn.

A parked instance with no live turn and no outstanding question holds
its parent in ``waiting_children`` indefinitely — the tree never
completes and the operator sees a hung mission.

THE SWEEP
---------
Periodic + boot pass over instances that are:

  * ``status = 'paused'``;
  * parked longer than the age bound (default 2h — comfortably longer
    than any legitimate "waiting for the user's answer" window, and
    longer than the question wedge-guard's own escalation horizon);
  * with NO live task row (no ``pending`` / ``running`` / ``paused``
    turn — nothing owns the instance);
  * with NO outstanding question anywhere in their permanent-lineage
    tree (D1 — see below).

Candidates are un-paused and given a natural resume (a silent
checkpoint resume) so their turn can complete and their parent can
leave ``waiting_children``. The sweeper NEVER finalizes an instance
(never writes a terminal status): finalizing is irreversible and
user-visible, and "the report was already delivered" is not reliably
determinable for a parked instance (a parked ``waiting_children``
instance has no claimable task by construction).

D1 — PREDICATE SCOPE (asker-scoped vs tree-scoped)
--------------------------------------------------
The "no pending question" half of the predicate is **TREE/LINEAGE
scoped**, not asker-scoped. A side-effect pause only makes sense while
the question that caused it is still outstanding; the question lives on
the ASKER, which is in the same permanent-lineage tree as the parked
instance. Tree scope therefore protects exactly the instances that are
still legitimately waiting, and releases the ones whose question has
been answered / dismissed / expired.

The alternative (asker-scoped: "this instance itself has no pending
question") would release a side-effect target while its asker still
holds a pending question — the tree would be torn apart mid-question.
The 2h age bound is the second line of defence for both readings.

This choice is PINNED by a test
(``tests/unit/test_question_pause_resume_asymmetry.py``).

FAIL-OPEN
---------
A sweep failure never blocks boot and never raises into the caller:
per-candidate errors are counted and retried on the next cycle.

BRIDGE SAFETY (A1)
------------------
The periodic thread runs the sweep on a WORKER thread and bridges the
coroutine onto the manager's loop (``run_coroutine_threadsafe``) —
the RDRS pattern. The BOOT pass is awaited DIRECTLY on the lifespan's
loop (no bridge at all), so no pass ever bridges a coroutine onto the
loop thread it is running on (the self-block class).
"""

from __future__ import annotations

import asyncio
import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from daemon.manager import InstanceManager

logger = logging.getLogger(__name__)

#: Default age bound (seconds) — a parked instance must be older than
#: this before the sweeper considers it stale. 2h.
DEFAULT_STALE_PAUSE_AGE_BOUND_SECONDS: int = 7200

#: Default periodic interval (seconds) — mirrors the RDRS cadence.
DEFAULT_STALE_PAUSE_INTERVAL_SECONDS: int = 300

#: Default batch cap per run.
DEFAULT_STALE_PAUSE_BATCH_CAP: int = 100

#: Upper bound on the tree size inspected by the D1 pending-question
#: probe (a runaway tree must not turn the sweep into an unbounded
#: metadata scan).
_MAX_TREE_PROBE_SIZE: int = 256


def _pending_question_in_tree(
    manager: "InstanceManager", instance_id: str
) -> bool:
    """True when ANY member of the lineage tree holds a pending question.

    D1 (tree-scoped predicate). RAM packs are probed in ONE call; the
    durable ``question_pack_payload`` shadows are probed per member
    (the shadow survives a restart, the RAM pack does not).
    """
    repo = getattr(manager, "_instance_repository", None)
    if repo is None:
        return False
    try:
        root_id = repo.get_tree_root_id(instance_id) or instance_id
        tree_ids = repo.get_cascade_tree_ids(root_id) or [instance_id]
    except Exception as exc:  # noqa: BLE001 — fail-open to "not protected"
        logger.warning(
            "stale-pause sweep: tree enumeration failed for %s: %s",
            instance_id[:8],
            exc,
        )
        return False
    tree_ids = tree_ids[:_MAX_TREE_PROBE_SIZE]

    qm = getattr(manager, "_question_manager", None)
    if qm is not None:
        try:
            if qm.get_pending_packs_for_instances(tree_ids):
                return True
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "stale-pause sweep: RAM pending-pack probe failed for %s: %s",
                instance_id[:8],
                exc,
            )

    try:
        from daemon.services.midflight_qa import (
            QUESTION_PACK_PAYLOAD_METADATA_KEY,
        )
    except Exception:  # noqa: BLE001 — defensive
        return False

    for member_id in tree_ids:
        try:
            payload = repo.get_metadata_value(
                member_id, QUESTION_PACK_PAYLOAD_METADATA_KEY
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "stale-pause sweep: durable shadow probe failed for %s: %s",
                member_id[:8],
                exc,
            )
            continue
        if isinstance(payload, dict) and payload.get("status") == "pending":
            return True
    return False


async def sweep_stale_pauses(
    manager: "InstanceManager",
    *,
    age_bound_seconds: int | None = None,
    batch_cap: int | None = None,
) -> dict[str, Any]:
    """Run ONE stale-pause sweep pass (R3).

    Safe to call from the boot path (awaited on the lifespan loop) and
    from the periodic thread (bridged onto the manager's loop from a
    worker thread). Never raises.
    """
    from daemon.services.question_pause_set import (
        OUTCOME_ERROR,
        OUTCOME_UNPAUSED,
        OUTCOME_WAKE_SCHEDULED,
        unpause_instance_after_question_pause,
    )

    if age_bound_seconds is None:
        age_bound_seconds = DEFAULT_STALE_PAUSE_AGE_BOUND_SECONDS
    if batch_cap is None:
        batch_cap = DEFAULT_STALE_PAUSE_BATCH_CAP

    summary: dict[str, Any] = {
        "candidates": 0,
        "resumed": 0,
        "skipped_protected": 0,
        "errors": 0,
        "instances": [],
    }

    repo = getattr(manager, "_instance_repository", None)
    if repo is None:
        logger.warning(
            "stale-pause sweep: no instance_repository wired — pass skipped"
        )
        summary["errors"] += 1
        return summary

    cutoff = (
        datetime.now(timezone.utc) - timedelta(seconds=max(0, int(age_bound_seconds)))
    ).isoformat()

    try:
        candidates = await asyncio.to_thread(
            repo.find_stale_paused_instances,
            cutoff_iso=cutoff,
            limit=batch_cap,
        )
    except Exception as exc:  # noqa: BLE001 — fail-open
        logger.warning(
            "stale-pause sweep: selection failed: %s: %s",
            type(exc).__name__,
            exc,
        )
        summary["errors"] += 1
        return summary

    summary["candidates"] = len(candidates)
    if not candidates:
        logger.info(
            "stale-pause sweep: candidates=0 resumed=0 "
            "(cutoff=%s, age_bound=%ss)",
            cutoff,
            age_bound_seconds,
        )
        return summary

    for row in candidates:
        instance_id = getattr(row, "instance_id", None)
        if not instance_id:
            continue
        try:
            # D1 — tree-scoped pending-question protection.
            if _pending_question_in_tree(manager, instance_id):
                summary["skipped_protected"] += 1
                logger.info(
                    "stale-pause sweep: SKIP instance=%s — an outstanding "
                    "question in its lineage tree still owns the pause",
                    instance_id[:8],
                )
                continue

            outcome = await unpause_instance_after_question_pause(
                manager, instance_id, reason="stale_pause_sweep"
            )
            if outcome in (OUTCOME_UNPAUSED, OUTCOME_WAKE_SCHEDULED):
                summary["resumed"] += 1
                summary["instances"].append(instance_id)
            elif outcome == OUTCOME_ERROR:
                summary["errors"] += 1
        except Exception as exc:  # noqa: BLE001 — per-candidate isolation
            summary["errors"] += 1
            logger.warning(
                "stale-pause sweep: candidate %s failed: %s: %s",
                str(instance_id)[:8],
                type(exc).__name__,
                exc,
            )

    logger.info(
        "stale-pause sweep: candidates=%d resumed=%d skipped_protected=%d "
        "errors=%d (cutoff=%s, age_bound=%ss)",
        summary["candidates"],
        summary["resumed"],
        summary["skipped_protected"],
        summary["errors"],
        cutoff,
        age_bound_seconds,
    )
    return summary


class StalePauseSweepService:
    """Periodic stale-pause sweeper (thread + ``_stop_event`` pattern).

    Mirrors :class:`daemon.services.report_delivery_recovery.
    ReportDeliveryRecoveryService` (thread lifecycle, ``start`` /
    ``stop``, fire-and-forget boot sweep) and
    :class:`daemon.services.stale_task_recovery.StaleTaskRecovery`.
    """

    def __init__(
        self,
        manager_ref: "InstanceManager",
        *,
        enabled: bool = True,
        interval_seconds: int = DEFAULT_STALE_PAUSE_INTERVAL_SECONDS,
        age_bound_seconds: int = DEFAULT_STALE_PAUSE_AGE_BOUND_SECONDS,
        batch_cap: int = DEFAULT_STALE_PAUSE_BATCH_CAP,
    ) -> None:
        self._manager = manager_ref
        self._enabled = bool(enabled)
        self._interval_seconds = max(1, int(interval_seconds))
        self._age_bound_seconds = max(0, int(age_bound_seconds))
        self._batch_cap = max(1, int(batch_cap))
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the periodic background thread (idempotent)."""
        if not self._enabled:
            logger.info(
                "StalePauseSweepService disabled by config; skipping start"
            )
            return
        if self._thread is not None and self._thread.is_alive():
            logger.warning("StalePauseSweepService already running")
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="StalePauseSweepService",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "StalePauseSweepService started: interval=%ss, age_bound=%ss, "
            "batch_cap=%d",
            self._interval_seconds,
            self._age_bound_seconds,
            self._batch_cap,
        )

    def stop(self, timeout: float | None = None) -> None:
        """Stop the periodic thread (SIGTERM-equivalent join)."""
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout if timeout is not None else 10.0)
        self._thread = None

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.sweep_now()
            except Exception as exc:  # noqa: BLE001 — never kill the thread
                logger.warning(
                    "StalePauseSweepService cycle failed: %s: %s",
                    type(exc).__name__,
                    exc,
                )
            self._stop_event.wait(timeout=self._interval_seconds)

    def sweep_now(self) -> dict[str, Any]:
        """Run one sweep from the SERVICE THREAD.

        The coroutine is bridged onto the manager's loop — the worker
        thread blocks on ``.result()`` while the loop keeps serving.
        The bridge is REFUSED when this method is somehow invoked ON the
        manager's loop thread (that would self-block the loop — the
        boot self-block class).
        """
        if not self._enabled:
            return {"skipped": "disabled"}

        loop = getattr(self._manager, "_loop", None)
        if loop is None or loop.is_closed():
            logger.warning(
                "StalePauseSweepService: no live manager loop — cycle skipped"
            )
            return {"skipped": "no_loop"}

        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is not None and running is loop:
            logger.warning(
                "StalePauseSweepService: invoked on the manager loop thread "
                "— cycle skipped (no self-bridge)"
            )
            return {"skipped": "on_loop_thread"}

        future = asyncio.run_coroutine_threadsafe(
            sweep_stale_pauses(
                self._manager,
                age_bound_seconds=self._age_bound_seconds,
                batch_cap=self._batch_cap,
            ),
            loop,
        )
        try:
            return future.result(timeout=60.0)
        except Exception as exc:  # noqa: BLE001 — fail-open
            logger.warning(
                "StalePauseSweepService: cycle failed: %s: %s",
                type(exc).__name__,
                exc,
            )
            return {"errors": 1}
