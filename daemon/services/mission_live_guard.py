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

    U7 fixback (FIXBACK, 2026-09-28): the council verdict crystallized
    the anchor's role to a single legitimate use — the ZOMBIE-BACKSTOP
    path. The decision tree after the walk:

    * **ALL-TERMINAL tree** (root + every descendant terminal) →
      ``live=False`` IMMEDIATELY. The tree-activity anchor plays NO
      role here. This is the wave-3 overshoot's exact failure mode
      (the pre-fixback fall-through returned ``live=True`` for
      all-terminal + fresh anchor, poisoning the natural notify path
      and demanding 6h backstop hold for every ordinary terminal).
    * **Non-terminal member present** + tree-activity anchor stale ≥6h
      → ``live=False, timed_out=True`` (zombie-break). The anchor's
      ONLY legitimate role. A tree that LOOKS live (children still
      nominally non-terminal) but has gone quiet for ≥6h is
      backstop-fired so the at-least-once guarantee holds.
    * **Non-terminal member present** + tree-activity anchor fresh
      → ``live=True`` (defer; the mission is plausibly alive).
    * **Non-terminal member present** + no anchor on any member
      → ``live=True`` (pre-U7 data-gap disarm preserved — missing
      anchor is not zombie evidence).

    The single-walk optimization is preserved (per pre-fixback
    review): ``freshest_activity`` is the in-memory MAX over
    ``last_activity_at`` seen during the loop; no extra DB
    round-trip. The pre-U7 missing-anchor disarm rule now lives at
    the non-terminal branch only.
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

    # ── Single walk: legs (b) + (c) + anchor collection ───────────
    # The walk NEVER short-circuits on a non-terminal observation
    # (the pre-fixback early-return was the overshoot's root cause
    # when paired with the fall-through bug). Instead, the loop
    # RECORDS non-terminal presence and CONTINUES collecting the
    # anchor — the post-walk decision tree (above) is the only
    # site that picks ``live``/``timed_out``. Reference semantics:
    # ``instances.parent_id`` (the permanent record) — same source
    # of truth as the mission resolver + child_reports walks. The
    # ``instance_hierarchy`` working set deletes rows on child
    # completion and would silently miss live descendants.
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

    any_non_terminal = False        # FIXBACK: drives the all-terminal short-circuit
    freshest_activity = None        # max ``last_activity_at`` seen during the walk
    anchor_missing = True           # a single non-NULL value flips this to False
    for tree_id in tree_ids:
        instance = await asyncio.to_thread(
            instance_repository.get, tree_id
        )
        if instance is None:
            # Row vanished mid-walk (concurrent delete) — a missing
            # row cannot hold the mission open; skip it.
            continue
        status = getattr(instance, "status", None)
        # FIXBACK: do NOT early-return on a non-terminal observation.
        # Record it; the post-walk decision tree decides the verdict.
        if status is not None and status not in TERMINAL_INSTANCE_STATUSES:
            any_non_terminal = True
        # Terminal-or-not, contribute ``last_activity_at`` to the
        # anchor (a live descendant's recent activity keeps the
        # backstop from firing on a tree that is plausibly still
        # alive — that is the legitimate zombie-break input).
        activity_str = getattr(instance, "last_activity_at", None)
        parsed_activity = parse_completed_at_naive(activity_str)
        if parsed_activity is not None:
            anchor_missing = False
            if freshest_activity is None or parsed_activity > freshest_activity:
                freshest_activity = parsed_activity

    # ── C1: ALL-TERMINAL tree → finalize immediately ──────────────
    # FIXBACK: the anchor plays NO role here. Every tree member is
    # terminal — the mission is closed, the held row may drain. This
    # restores the pre-U7 frozen terminal contract that the
    # predecessor's overshoot (fall-through ``live=True``) violated.
    if not any_non_terminal:
        return MissionLiveVerdict(
            live=False,
            reason=(
                "no live leg: root + descendants terminal, bus quiet "
                "— all-terminal tree finalizes immediately "
                "(tree-activity anchor plays no role on all-terminal "
                "trees; timed_out is reserved for the zombie-backstop "
                "path on non-terminal members)"
            ),
        )

    # ── Non-terminal members present: anchor check (zombie-break) ──
    # The tree LOOKS live (at least one non-terminal member). The
    # only remaining failure mode is a zombie: the tree is stuck in
    # a non-terminal posture but the freshest tree activity is
    # older than ``timeout_seconds``. The pre-U7 missing-anchor
    # disarm is preserved here — a non-terminal tree with no anchor
    # data is "data gap, not zombie evidence"; the leg verdict
    # wins (``live=True``).
    if anchor_missing:
        # FIXBACK: pre-U7 missing-anchor disarm contract preserved
        # verbatim, now scoped to the non-terminal branch (the
        # all-terminal branch above covers the all-terminal case).
        return MissionLiveVerdict(
            live=True,
            reason=(
                "non-terminal member present (live leg holds) but no "
                "last_activity_at anchor on any tree member — "
                "backstop disarmed (data gap, not zombie evidence); "
                "the leg verdict wins"
            ),
        )

    age_seconds = (
        now_utc_naive() - freshest_activity
    ).total_seconds()
    if age_seconds >= timeout_seconds:
        return MissionLiveVerdict(
            live=False,
            reason=(
                f"mission-tree backstop fires: non-terminal members "
                f"present AND freshest tree last_activity_at is "
                f"{int(age_seconds)}s old (>{timeout_seconds}s "
                f"threshold) — zombie-break, at-least-once terminal "
                f"delivery takes precedence"
            ),
            timed_out=True,
        )

    # ── Non-terminal + anchor fresh → defer ───────────────────────
    return MissionLiveVerdict(
        live=True,
        reason=(
            f"non-terminal member present (live leg holds) and "
            f"freshest tree last_activity_at is {int(age_seconds)}s "
            f"old (<{timeout_seconds}s threshold) — mission is "
            f"plausibly alive; defer to the next tick"
        ),
    )
