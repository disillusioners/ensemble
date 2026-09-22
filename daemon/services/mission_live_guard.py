"""Mission-live guard for premature terminal finalize/notify sites.

Bug class (2026-09-22, events 79328/79349): a task-kind LEADER job is
legitimately ``JobItem ACTIVE + backing Task COMPLETED`` mid-mission —
the per-turn task row settles while ``child_reports`` defers the
JobItem finalize behind still-running children (bus held, mission
live). The drift reconciler's Pattern f2
(``job_recovery_service.reconcile_drift_states``) saw
"Task COMPLETED but JobItem never transitioned", force-finalized the
JobItem, and the F10 notify arm delivered a false ``completed ✓``
event and CAS-deleted the mission watcher row. The boot-time
``reconcile_terminal_watches`` sweep
(``job_queue_service``) is an independent re-occurrence vector: it
fires terminal for mission-keyed watches on settled work rows without
consulting mission liveness.

The guard answers one question: **is the mission behind this work row
still live?** The mission is LIVE when ANY of:

* (a) the dependency bus reports pending watchers for this work
  (caller-supplied count — each call site already owns its bus seam);
* (b) any DESCENDANT instance of the work row's instance is
  non-terminal. Reference semantics: the mission resolver /
  ``child_reports`` instance-tree walk over ``instances.parent_id``
  (the permanent record) with the canonical terminal set
  ``TERMINAL_INSTANCE_STATUSES`` — non-terminal therefore includes
  ``running`` / ``waiting_children`` / ``queued`` / ``paused`` AND
  ``idle`` (the resolver canonicalizes IDLE → ``"processing"``, i.e.
  live);
* (c) the root instance itself is non-terminal (same status set).

Fail-open contract (binding): any exception inside the guard returns
``live=False`` with ``error=True`` so the caller proceeds with
finalize + notify. A missing terminal report is WORSE than an extra
premature one — at-least-once terminal delivery is preserved.

Zombie backstop: a guard that keeps saying LIVE forever (crashed
daemon, never-reaped ``running`` rows) must not strand the terminal
forever. When the work's ``completed_at`` anchor is older than
``MISSION_LIVE_ORPHAN_TIMEOUT_SECONDS`` the guard falls through to
``live=False`` (finalize fires; starvation impossible). The anchor is
the backing task's ``completed_at`` at the f2 site and the settled
work row's ``completed_at`` at the boot-sweep site; age math uses
``now_utc_naive()`` per the naive-UTC binding convention — no schema
changes, no new env flags (repo policy: bugfixes are not
user-togglable; the constant is the tuning knob).
"""

from __future__ import annotations

import logging
import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from daemon.constants import TERMINAL_INSTANCE_STATUSES
from daemon.services.timestamps import coerce_to_aware_utc, now_utc_naive

if TYPE_CHECKING:  # pragma: no cover - import for type checkers only
    from daemon.repositories.instance.repository import (
        SQLModelInstanceRepository,
    )

logger = logging.getLogger(__name__)

# Orphan age-timeout backstop — when the mission-live guard has been
# deferring a finalize candidate continuously and the work's
# ``completed_at`` anchor is older than this, the guard falls through
# and the caller finalizes (fail-open). Value rationale:
#
# * Real multi-agent missions legitimately run for hours (waiting
#   children, defer queues) — the timeout must exceed the longest
#   normal mission so the guard never fires mid-mission. The 2026-09-22
#   repro missions were minutes long; other reconciler windows in this
#   repo are minutes (f1 grace ~minutes, orphan sweep ≤20min,
#   stale-sync steal 600s). 6h is ~an order of magnitude above the
#   observed mission span while bounding a zombie's at-least-once
#   delay to ≤ timeout + one sweep interval (300s drift cadence).
# * A pre-existing terminal-izer (stale-instance cancel, f1, operator
#   terminate) flips zombie rows terminal well before this window,
#   which re-opens the guard naturally.
MISSION_LIVE_ORPHAN_TIMEOUT_SECONDS: int = 6 * 60 * 60  # 6 hours


@dataclass(frozen=True)
class MissionLiveVerdict:
    """Result of :func:`evaluate_mission_live`.

    Attributes:
        live: True when the mission is still live (caller must SKIP
            finalize/notify and leave watcher rows alone). False when
            the caller may proceed (no live leg, timeout expired, or
            internal error → fail-open).
        reason: Human-readable explanation naming which leg was live
            (or why the guard opened). Suitable for drift ``details``
            records and INFO logs.
        error: True when an internal exception forced the fail-open
            path (``live=False`` because the guard could not evaluate,
            NOT because the mission is dead).
        timed_out: True when the zombie backstop fired (anchor older
            than the timeout) — the mission looked live but the
            at-least-once guarantee takes precedence.
    """

    live: bool
    reason: str
    error: bool = False
    timed_out: bool = False


def parse_completed_at_naive(value) -> datetime | None:
    """Parse a ``completed_at`` anchor into naive-UTC digits.

    Accepts ``datetime`` or ISO-8601 string (Task rows store
    ``datetime``; JobItem rows store ISO strings — the guard is shared
    by both call sites). Naive inputs follow the documented assume-UTC
    policy (:func:`coerce_to_aware_utc`), then the tzinfo is stripped
    so age math runs naive-against-``now_utc_naive()`` per the
    naive-UTC binding convention (never mix aware and naive).

    Returns ``None`` for missing/unparseable values — the caller
    decides the sentinel (never guess).
    """
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    aware = coerce_to_aware_utc(parsed)
    if aware is None:
        return None
    return aware.replace(tzinfo=None)


def _completed_at_age_exceeds(
    anchor,
    timeout_seconds: int,
) -> bool | None:
    """Has the anchor aged past the timeout?

    Returns ``True`` (backstop fires → finalize), ``False`` (within
    window → keep deferring), or ``None`` (no usable anchor — the
    backstop cannot fire; the caller stays in the defer-holding
    default). An absent anchor must NOT open the guard: the guard's
    whole purpose is holding terminal delivery while the mission is
    plausibly alive, and a missing stamp is a data gap, not evidence
    of a zombie.
    """
    parsed = parse_completed_at_naive(anchor)
    if parsed is None:
        return None
    age_seconds = (now_utc_naive() - parsed).total_seconds()
    return age_seconds >= timeout_seconds


async def evaluate_mission_live(
    *,
    instance_repository: "SQLModelInstanceRepository | None",
    instance_id: str | None,
    task_completed_at=None,
    bus_pending_count: int | None = None,
    timeout_seconds: int = MISSION_LIVE_ORPHAN_TIMEOUT_SECONDS,
) -> MissionLiveVerdict:
    """Evaluate the three-leg mission-live guard (+ zombie backstop).

    Args:
        instance_repository: Instance repository for the permanent
            tree walk. ``None`` (unwired test doubles / partial init)
            is treated as an internal error → fail-open.
        instance_id: The work row's instance id (the mission's root
            for task-kind jobs). ``None`` → fail-open (nothing to
            consult; at-least-once wins).
        task_completed_at: The zombie-backstop anchor — the backing
            task's ``completed_at`` (f2 site) or the settled work
            row's ``completed_at`` (boot-sweep site). ``datetime`` or
            ISO string.
        bus_pending_count: Leg (a) — pending dependency-bus watchers
            for this work, precomputed by the caller (each call site
            owns its bus seam: f2 passes the Gate-1 source-task count;
            the boot sweep passes the target-instance count).
            ``None`` = leg unknown, skipped (the remaining legs
            decide).
        timeout_seconds: Zombie-backstop window; defaults to
            ``MISSION_LIVE_ORPHAN_TIMEOUT_SECONDS``.

    Returns:
        :class:`MissionLiveVerdict`. NEVER raises — internal errors
        collapse to ``live=False, error=True`` (fail-open; the caller
        finalizes).
    """
    try:
        # ── Zombie backstop FIRST: an anchor older than the window
        # opens the door to finalize REGARDLESS of what the liveness
        # legs would say (test-matrix (v): guard still seeing live +
        # timeout exceeded → finalize fires; starvation impossible).
        exceeded = _completed_at_age_exceeds(
            task_completed_at, timeout_seconds
        )
        if exceeded is True:
            return MissionLiveVerdict(
                live=False,
                reason=(
                    f"mission-live defer window expired: completed_at "
                    f"anchor older than {timeout_seconds}s — zombie "
                    f"backstop fires (at-least-once terminal delivery)"
                ),
                timed_out=True,
            )
        return await _evaluate_legs(
            instance_repository=instance_repository,
            instance_id=instance_id,
            bus_pending_count=bus_pending_count,
            anchor_missing=(exceeded is None),
        )
    except Exception as exc:  # noqa: BLE001 — fail-open is the contract
        logger.warning(
            "mission_live_guard: evaluation failed for instance "
            "%s: %s: %s — FAIL-OPEN (proceed with finalize/notify; "
            "at-least-once terminal delivery takes precedence)",
            (instance_id or "?")[:8],
            type(exc).__name__,
            exc,
        )
        return MissionLiveVerdict(
            live=False,
            reason=(
                f"mission-live guard raised {type(exc).__name__} "
                f"({exc}) — fail-open per at-least-once contract"
            ),
            error=True,
        )


async def _evaluate_legs(
    *,
    instance_repository,
    instance_id,
    bus_pending_count,
    anchor_missing: bool,
) -> MissionLiveVerdict:
    """Leg evaluation (async — blocking repo reads go through
    ``asyncio.to_thread`` per the repo's standard pattern). Raises on
    wiring gaps — the wrapper fail-opens."""
    # ── Leg (a): bus pending watchers ─────────────────────────────
    if bus_pending_count is not None and bus_pending_count > 0:
        return MissionLiveVerdict(
            live=True,
            reason=(
                f"dependency bus reports {bus_pending_count} pending "
                f"watcher(s) for this work (leg a)"
            ),
        )

    if instance_repository is None or not instance_id:
        raise RuntimeError(
            "mission_live_guard: instance repository or instance_id "
            "unwired — cannot evaluate tree liveness"
        )

    # ── Legs (b) + (c): permanent instance-tree liveness ─────────
    # One walk returns [root, *descendants] over ``instances.parent_id``
    # (the permanent record — same source of truth as the mission
    # resolver / child_reports reference semantics; the
    # ``instance_hierarchy`` working set deletes rows on child
    # completion and would silently miss live descendants).
    tree_ids = await asyncio.to_thread(
        instance_repository.get_tree_ids_permanent, instance_id
    )
    if not tree_ids:
        # Root row not found — nothing holdable; fail-open direction.
        return MissionLiveVerdict(
            live=False,
            reason=(
                f"root instance {instance_id[:8]}... not found in the "
                f"permanent record — nothing holdable"
            ),
        )

    for tree_id in tree_ids:
        instance = await asyncio.to_thread(
            instance_repository.get, tree_id
        )
        if instance is None:
            # Row vanished mid-walk (concurrent delete) — a missing
            # row cannot hold the mission open; skip it.
            continue
        status = getattr(instance, "status", None)
        if status is not None and status not in TERMINAL_INSTANCE_STATUSES:
            leg = "c (root instance)" if tree_id == instance_id else (
                f"b (descendant {tree_id[:8]}...)"
            )
            return MissionLiveVerdict(
                live=True,
                reason=(
                    f"instance {tree_id[:8]}... status={status!r} is "
                    f"non-terminal — {leg} reports the mission live"
                ),
            )

    # ── All legs quiet ─────────────────────────────────────────────
    # Every instance in the permanent tree is terminal and the bus is
    # quiet → the mission is dead; the caller finalizes. An unusable
    # timeout anchor (no ``completed_at`` stamp) is surfaced in the
    # reason so operators can see the backstop is disarmed for this
    # row — it holds nothing here (the legs already said not-live),
    # but the drift sweep will revisit the row every cycle anyway.
    suffix = (
        " (note: completed_at anchor missing — zombie backstop "
        "disarmed for this row)"
        if anchor_missing
        else ""
    )
    return MissionLiveVerdict(
        live=False,
        reason=f"no live leg: root + descendants terminal, bus quiet{suffix}",
    )
