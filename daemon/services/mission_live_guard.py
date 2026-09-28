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

**Zombie backstop — anchored to TREE ACTIVITY (U7 fix,
2026-09-28).** The pre-U7 anchor was the work row's
``task_completed_at`` — a per-work stamp that captured the per-turn
settle moment. On a long-lived mission (the wave-3 evidence: a 6h5m
mission where the leader was still mid-LLM) ``task_completed_at`` is
hours old while tree members continue to wake/sleep/respond, so the
backstop fired 73s pre-terminal on a live row, consuming the
exactly-once notify row with stale text. The new anchor is the freshest
tree signal available in the guard's read path:

    tree_anchor = max(
        root.last_activity_at,
        max(d.last_activity_at for d in descendants),
    )

…computed during the legs (b)/(c) tree walk (same one read, no extra
DB round-trip). On a live tree the anchor rolls forward each tick and
the backstop never fires; on a truly quiet tree (no activity for
``MISSION_LIVE_ORPHAN_TIMEOUT_SECONDS``) the backstop fires as before.
The pre-existing missing-anchor disarm rule is preserved: a tree where
no instance has ever recorded ``last_activity_at`` cannot fire the
backstop (data gap, not evidence of a zombie); the leg verdict — all
terminal + bus quiet — decides the row (terminal, finalize). The
``task_completed_at`` argument is kept in the signature for
backward-call-site compatibility but is now ignored by the backstop
logic; new callers may stop passing it.

Age math uses ``now_utc_naive()`` per the naive-UTC binding
convention — no schema changes, no new env flags (repo policy:
bugfixes are not user-togglable; the constant is the tuning knob).
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
# deferring a finalize candidate continuously and the mission tree has
# shown NO activity (freshest ``last_activity_at`` across the
# permanent lineage) for longer than this, the guard falls through and
# the caller finalizes (fail-open). Value rationale:
#
# * Real multi-agent missions legitimately run for hours (waiting
#   children, defer queues); the timeout must exceed the longest
#   normal mission so the guard never fires mid-mission while the tree
#   is genuinely active. The 2026-09-22 repro missions were minutes
#   long; the wave-3 U7 incident (2026-09-28, mission 1034286a)
#   stretched one 6h5m before a true terminal — the timeout sits
#   ABOVE the longest observed span. Other reconciler windows in this
#   repo are minutes (f1 grace ~minutes, orphan sweep ≤20min,
#   stale-sync steal 600s). 6h is ~an order of magnitude above the
#   observed mission span while bounding a zombie's at-least-once
#   delay to ≤ timeout + one sweep interval (300s drift cadence).
# * A pre-existing terminal-izer (stale-instance cancel, f1, operator
#   terminate) flips zombie rows terminal well before this window,
#   which re-opens the guard naturally.
# * U7 (2026-09-28): the anchor is now the FRESHEST tree signal
#   (``max(root.last_activity_at, max(d.last_activity_at))``), not
#   the per-work ``task_completed_at``. A live tree rolls the anchor
#   forward indefinitely, so the backstop fires only on a
#   genuinely-quiet ≥timeout tree — the wave-3 misfire class
#   (live-tree anchor pinned at per-turn settle) is closed.
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


# Module-private helper kept for back-compat with external callers that
# may import the symbol (None of the in-tree callers do post-U7 — the
# helper is now purely functional, used by tests). The U7 backstop
# routes through ``parse_completed_at_naive`` + in-memory MAX during
# the tree walk inside ``_evaluate_legs``.
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

    .. note::
       U7 (2026-09-28): the production backstop no longer routes
       through this helper — it computes the anchor from the freshest
       ``last_activity_at`` across the permanent lineage during the
       tree walk. The helper survives for legacy callers + tests.
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

    U7 (2026-09-28): the zombie backstop is anchored to the FRESHEST
    tree signal available in the read path —
    ``max(root.last_activity_at, max(d.last_activity_at) for d in
    descendants)`` — NOT to the per-work ``task_completed_at`` (which
    on a long-lived mission is hours old while tree members continue
    to wake / sleep / respond, and which misfired 73s pre-terminal on
    the wave-3 evidence). The anchor is collected during the same
    legs (b)/(c) tree walk; no extra DB round-trip. The
    ``task_completed_at`` parameter is KEPT on the signature for
    backward-call-site compatibility but is no longer consulted by
    the backstop; new callers may stop passing it.

    Args:
        instance_repository: Instance repository for the permanent
            tree walk. ``None`` (unwired test doubles / partial init)
            is treated as an internal error → fail-open.
        instance_id: The work row's instance id (the mission's root
            for task-kind jobs). ``None`` → fail-open (nothing to
            consult; at-least-once wins).
        task_completed_at: DEPRECATED (U7, 2026-09-28) — the work
            row's ``completed_at``. Ignored by the backstop; kept on
            the signature so existing callers compile. Pass ``None``
            in new code.
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
    del task_completed_at  # U7: backstop no longer anchored here.
    try:
        return await _evaluate_legs(
            instance_repository=instance_repository,
            instance_id=instance_id,
            bus_pending_count=bus_pending_count,
            timeout_seconds=timeout_seconds,
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
    timeout_seconds: int,
) -> MissionLiveVerdict:
    """Leg evaluation (async — blocking repo reads go through
    ``asyncio.to_thread`` per the repo's standard pattern). Raises on
    wiring gaps — the wrapper fail-opens.

    U7 (2026-09-28) — the tree walk now ALSO collects the freshest
    ``last_activity_at`` across root + descendants (the new backstop
    anchor). The collection piggybacks on the existing per-row read in
    the legs (b)/(c) loop — no extra DB round-trip — and the MAX
    is reduced in-memory after the walk. ``parse_completed_at_naive``
    is reused so the anchor obeys the same naive-UTC binding as the
    pre-U7 ``task_completed_at`` path did.
    """
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

    # ── Legs (b) + (c) + U7 anchor ───────────────────────────────
    # One walk returns [root, *descendants] over ``instances.parent_id``
    # (the permanent record — same source of truth as the mission
    # resolver / child_reports reference semantics; the
    # ``instance_hierarchy`` working set deletes rows on child
    # completion and would silently miss live descendants). During
    # the walk, the U7 zombie-backstop anchor is collected
    # in-memory — ``freshest_activity`` is the MAX of every
    # ``last_activity_at`` we see; NULL rows are skipped (the
    # pre-U7 missing-anchor disarm rule applies to a tree with NO
    # ``last_activity_at`` at all). The anchor is exposed in the
    # post-walk verdict's ``reason`` so operators can see WHY the
    # backstop did/did not fire on a given tick.
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

    freshest_activity = None  # max ``last_activity_at`` seen during the walk
    anchor_missing = True     # a single non-NULL value flips this to False
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
        # Tree member is terminal but contribute its last_activity_at
        # to the U7 anchor (a recently-settled descendant keeps the
        # backstop from firing on the OLD settle moment of the work
        # row itself — this is precisely the wave-3 fix: live
        # descendant activity within the window keeps the row held).
        activity_str = getattr(instance, "last_activity_at", None)
        parsed_activity = parse_completed_at_naive(activity_str)
        if parsed_activity is not None:
            anchor_missing = False
            if freshest_activity is None or parsed_activity > freshest_activity:
                freshest_activity = parsed_activity

    # ── All legs quiet — evaluate the U7 zombie backstop ──────────
    # The freshest tree signal is the new anchor. If no member has
    # any ``last_activity_at`` yet (fresh tree, never-active), the
    # backstop is disarmed (data gap, not evidence of a zombie) —
    # the row is treated as terminal-dead and the caller finalizes.
    # Otherwise the backstop fires IFF the freshest activity is older
    # than ``timeout_seconds`` — a genuinely quiet tree.
    if anchor_missing:
        # Pre-U7 missing-anchor disarm contract preserved verbatim:
        # the row's leg verdict is terminal-dead AND the backstop
        # cannot fire; surface the disarm in the reason so operators
        # can spot a data gap rather than a stranded fire.
        return MissionLiveVerdict(
            live=False,
            reason=(
                f"no live leg: root + descendants terminal, bus quiet, "
                f"no last_activity_at anchor on any tree member — "
                f"backstop disarmed (finalize fires; data gap, not "
                f"zombie)"
            ),
        )

    age_seconds = (
        now_utc_naive() - freshest_activity
    ).total_seconds()
    if age_seconds >= timeout_seconds:
        return MissionLiveVerdict(
            live=False,
            reason=(
                f"mission-tree backstop fires: freshest tree "
                f"last_activity_at is {int(age_seconds)}s old "
                f"(>{timeout_seconds}s threshold) — every tree member "
                f"terminal AND quiet for {timeout_seconds}s; at-least-"
                f"once terminal delivery takes precedence"
            ),
            timed_out=True,
        )

    # ── All legs quiet AND recent tree activity within window ─────
    # The mission is plausibly live — defer; the next tick will
    # re-evaluate. The reason carries the anchor age so operators
    # can see how close the row sits to the timeout.
    return MissionLiveVerdict(
        live=True,
        reason=(
            f"all legs quiet (root + descendants terminal, bus quiet) "
            f"but freshest tree last_activity_at is {int(age_seconds)}s "
            f"old (<{timeout_seconds}s threshold) — recent activity "
            f"suggests the mission is still deferring; backstop holds"
        ),
    )
