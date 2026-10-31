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
  live). STRAND EXCEPTION (2026-10-14, watcher-loss terminal strand):
  a never-dispatched member (idle, version 1, zero ``message_queue``
  and zero ``task`` rows) whose DIRECT parent is TERMINAL is
  stranded-by-construction and does NOT count live once it has aged
  past ``ORPHAN_MIN_AGE_SECONDS`` (final hardening: the shared
  ``strand_past_race_guard`` predicate — read/write mirror) — see
  the STRAND INVARIANT comment in :func:`_evaluate_legs`;
  never-dispatched rows under a LIVE direct parent (the
  spawn-then-message ritual) keep holding liveness;
* (c) the root instance itself is non-terminal (same status set).

Fail-open contract (binding): any exception inside the guard's TREE
WALK returns ``live=False`` with ``error=True`` so the caller proceeds
with finalize + notify. A missing terminal report is WORSE than an extra
premature one — at-least-once terminal delivery is preserved.

**OPPOSITE POLARITY (W-2, scoped):** the MEMBER-ALIVE companion leg is
the exception — a member probe that cannot be evaluated HOLDS the tree
(``live=True``, ``error=True``), because "member liveness unknown" must
never mean "the tree is stale". Only the guard's TREE-WALK errors
fail open. See :func:`_member_alive_companion_verdict` and the
fail-MISS invariant in the design doc §2.10.3.

**Zombie backstop — anchored to TREE ACTIVITY (U7 fix,
2026-09-28).** The pre-U7 anchor was the work row's
``completed_at`` — a per-work stamp that captured the per-turn
settle moment. On a long-lived mission (the wave-3 evidence: a 6h5m
mission where the leader was still mid-LLM) ``completed_at`` is
hours old while tree members continue to wake/sleep/respond, so the
backstop fired 73s pre-terminal on a live row, consuming the
exactly-once notify row with stale text. The new anchor is the freshest
tree signal available in the guard's read path.

**Anchor scope correction (2026-10-21, running-zombie cycle 2).**
The U7 anchor was the max over the WHOLE tree — the root's own
``last_activity_at`` INCLUDED:

    tree_anchor = max(
        root.last_activity_at,
        max(d.last_activity_at for d in descendants),
    )

That inclusion is the defect the running-zombie strand closed: the
root's own stamp is refreshed on EVERY message enqueue
(``daemon/services/instance_messaging.py:2455``), so a stranded root
that keeps receiving messages looks PERMANENTLY fresh to the
backstop. The live 2026-10-21 repro logged a 22s-old "freshest tree
last_activity_at" on a root whose mission had been dead for hours —
the 22s WAS the root's own enqueue-refreshed stamp, and it made the
6h backstop unreachable, so the pre-stamp hold
(``daemon/services/child_reports.py``) refused the COMPLETED stamp
and the root stayed ``running`` forever.

The anchor is now computed over the NON-ROOT members only:

    tree_anchor = max(
        d.last_activity_at for d in descendants
    )            # fallback: the root's own stamp when the tree has
                 # no members at all (a childless root — the root IS
                 # the whole tree, so its stamp is the tree signal)

A childless root therefore keeps its historical behaviour exactly
(its own activity is the tree activity), while a tree WITH members
can no longer be kept "alive" by the root's enqueue-refreshed stamp.
The anchor is still collected during the legs (b)/(c) tree walk (same
one read, no extra DB round-trip). On a live tree the anchor rolls
forward each tick and the backstop never fires; on a truly quiet tree
(no member activity for ``MISSION_LIVE_ORPHAN_TIMEOUT_SECONDS``) the
backstop fires as before.

The pre-existing missing-anchor disarm rule is preserved: a tree where
no instance has ever recorded ``last_activity_at`` cannot fire the
backstop (data gap, not evidence of a zombie); the leg verdict — all
terminal + bus quiet — decides the row (terminal, finalize).

**MEMBER-ALIVE companion leg (2026-10-21, council F1 + RULING-1b).**
``instances.last_activity_at`` is written on the enqueue / child-report
/ observer paths — NOT on turn progress. So a member inside a long
(>6h) turn reads STALE to the backstop even though it is working right
now, and the backstop would break the tree (finalize the root) while
that member is mid-turn. The ruling (leader, cycle 3) is fail-MISS over
false-completion: **a non-terminal member that holds a LIVE task row
counts as ALIVE, not stale.**

Cycle 3 shipped the leg OPT-IN (a ``task_engine`` parameter). The
council REJECTED that shape (F1): the guard is the shared chokepoint,
so an opt-in leg leaves every OTHER consumer (f2, f1, the boot sweeps,
the emission gate, the orphan release, the work notifier) unprotected
against the same false completion. The leg is now **UNCONDITIONAL and
guard-side** — every consumer inherits it — with **ZERO new plumbing**:
the leg asks the instance repository the guard ALREADY holds
(``SQLModelInstanceRepository.has_live_work``) and reads the member's
task rows through that same repository.

**TIME-BOUNDED (RULING-1b).** An unbounded "any live task row ⇒ alive"
leg trades one permanent strand for another: a member whose task row
never completes (a crash / kill mid-turn) would hold its mission open
forever — the exact class this lane closes. So a member's live work
counts as ALIVE **ONLY IF** its task row is YOUNGER than the guard's
own staleness horizon (``MISSION_LIVE_ORPHAN_TIMEOUT_SECONDS`` — the
SAME constant, one source of truth, no new knob). An OLDER task row
(a crashed or killed turn) does NOT hold the tree: the member is
stale-eligible and the root CAN heal.

**TWO PINNED INVARIANTS (council).**

* **ADDITIVE, never a replacement** — a member with stale activity AND
  no live task row REMAINS a zombie candidate (the OBSERVED family: an
  idle member with no task row).
* **FAIL-MISS** — a member with a live (young) task row ⇒ the root is
  NOT finalized.

Cost: PER-MEMBER probes (the repository's ``has_live_work`` + one task
read per candidate member), and they run ONLY on the staleness branch —
the rare path — so a live tree pays nothing.

Failure posture: a probe that cannot be evaluated HOLDS the tree
(``live=True``, ``error=True``) — the leg cannot prove the tree is
stale, and this lane's invariant is "prefer a miss over a wrong
transition".

RESIDUALS (named, not hidden): a member inside a LEGITIMATE >6h turn
is still indistinguishable from stale (the horizon is the system's own)
— "heartbeat on turn progress" is a named follow-up. A member whose
only live work is a JOBITEM row (no task row) is NOT held either: the
task row is the only DATEABLE evidence class, and the repository's
probe also counts a member's own non-terminal CHILD as "live work" — a
``waiting_children`` parent would otherwise hold the tree forever
through its own stuck child.

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

from daemon.constants import (
    NON_TERMINAL_TASK_STATUSES,
    TERMINAL_INSTANCE_STATUSES,
)
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
#   (``max(d.last_activity_at)`` over the DESCENDANTS), not the
#   per-work ``task_completed_at``. A live tree rolls the anchor
#   forward indefinitely, so the backstop fires only on a
#   genuinely-quiet ≥timeout tree — the wave-3 misfire class
#   (live-tree anchor pinned at per-turn settle) is closed.
# * 2026-10-21 (running-zombie cycle 2): the anchor is computed over
#   the NON-ROOT members only (the root's own stamp is refreshed on
#   every message enqueue, so it can never prove a tree is quiet).
#   A childless root falls back to its OWN stamp — the root IS the
#   whole tree there, so its activity is the tree activity.
# * The horizon family is a DEFAULT, NOT an invariant: this constant is
#   FIXED (6 h), while the reconciliation sweep's commission TTL
#   (``ENSEMBLE_MISSION_COMMISSION_TTL_SECONDS``) is INDEPENDENTLY
#   env-tunable and starts on the same 6 h default — it MAY DIVERGE from
#   this constant the moment an operator sets a TTL != 21600.
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
            NOT because the mission is dead). NOTE the OPPOSITE
            polarity (W-2): a MEMBER-ALIVE probe error yields
            ``live=True`` + ``error=True`` — the leg HOLDS, because
            "member liveness unknown" must never mean "the tree is
            stale". Only TREE-WALK errors fail open.
        timed_out: True when the zombie backstop fired (anchor older
            than the timeout) — the mission looked live but the
            at-least-once guarantee takes precedence.
        member_task_hold: True when the MEMBER-ALIVE companion leg
            (council F1 + RULING-1b, 2026-10-21) held the tree: a
            non-terminal member owns a LIVE task row that is YOUNGER
            than the guard's staleness horizon, so the tree is
            WORKING, not stale. The backstop did NOT fire in that
            case. The leg is UNCONDITIONAL (every consumer inherits
            it); always False when no member qualifies.
    """

    live: bool
    reason: str
    error: bool = False
    timed_out: bool = False
    member_task_hold: bool = False


def parse_completed_at_naive(value: datetime | str | None) -> datetime | None:
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


# ── Strand race-guard threshold (watcher-loss terminal strand, final
#    hardening 2026-10-14) ─────────────────────────────────────────
# A never-dispatched strand candidate's ``updated_at`` must be at
# least this old before EITHER side acts on it. A freshly-spawned
# child may be about to receive its first message (the spawn-then-
# message ritual); age proves the strand is not mid-flight. BOTH
# consumers run the SAME predicate below — one threshold, one
# predicate, no read/write drift. Same threshold family as the
# F14/A4 60s orphan-age convention. (Lives HERE — the read side —
# because the write-side healer already imports this module's
# ``parse_completed_at_naive``; the dependency direction healer →
# guard keeps the graph acyclic.)
ORPHAN_MIN_AGE_SECONDS: int = 60


def strand_past_race_guard(
    updated_at: "datetime | str | None",
    now: datetime | None = None,
) -> bool:
    """Shared strand age predicate — single source of truth.

    True ⇔ the strand's ``updated_at`` proves it is at least
    ``ORPHAN_MIN_AGE_SECONDS`` old. Both sides run this exact
    predicate: the READ-side liveness exclusion (:func:`_evaluate_legs`
    — a too-fresh ghost keeps holding liveness; the ritual may still
    deliver) and the WRITE-side healer (``mission_strand_healer`` — a
    too-fresh strand is refused, re-checked next tick).

    ``None``/unparseable → False (fail-closed: an unknown age never
    clears the guard on either side). Age math runs naive-against-
    naive per the ``now_utc_naive()`` binding convention.
    """
    parsed = parse_completed_at_naive(updated_at)
    if parsed is None:
        return False
    if now is None:
        now = now_utc_naive()
    return (now - parsed).total_seconds() >= ORPHAN_MIN_AGE_SECONDS


async def evaluate_mission_live(
    *,
    instance_repository: "SQLModelInstanceRepository | None",
    instance_id: str | None,
    bus_pending_count: int | None = None,
    timeout_seconds: int = MISSION_LIVE_ORPHAN_TIMEOUT_SECONDS,
    skip_root: bool = False,
    discount_instance_id: str | None = None,
) -> MissionLiveVerdict:
    """Evaluate the three-leg mission-live guard (+ zombie backstop).

    U7 (2026-09-28): the zombie backstop is anchored to the FRESHEST
    tree signal available in the read path —
    ``max(d.last_activity_at) for d in descendants`` — NOT to the
    per-work ``completed_at`` (which on a long-lived mission is hours
    old while tree members continue to wake / sleep / respond, and
    which misfired 73s pre-terminal on the wave-3 evidence).

    2026-10-21 (running-zombie cycle 2): the anchor is computed over
    the NON-ROOT members only. The root's own ``last_activity_at`` is
    refreshed on every message enqueue
    (``daemon/services/instance_messaging.py:2455``), so including it
    made the backstop unreachable for a stranded-but-messaged root
    (the live 2026-10-21 repro: a 22s-old "freshest tree
    last_activity_at" on a root whose mission had been dead for
    hours). A tree with NO members (a childless root) falls back to
    the root's own stamp — there the root IS the whole tree.

    2026-10-21 (council F1 + RULING-1b): the MEMBER-ALIVE companion
    leg. ``last_activity_at`` is NOT written on turn progress, so a
    member inside a long turn reads stale and the backstop would break
    a tree that is still working. A non-terminal member that owns a
    LIVE task row YOUNGER than the staleness horizon HOLDS the tree
    (the backstop does not fire). The leg is UNCONDITIONAL — every
    consumer inherits it (f2, f1, the boot sweeps, the emission gate,
    the orphan release, the work notifier, the pre-stamp gate, the
    running-zombie healer's tree leg) — and it needs NO new plumbing:
    it asks the instance repository the guard already holds.

    The anchor is collected during the same legs (b)/(c) tree walk;
    no extra DB round-trip.

    Args:
        instance_repository: Instance repository for the permanent
            tree walk. ``None`` (unwired test doubles / partial init)
            is treated as an internal error → fail-open.
        instance_id: The work row's instance id (the mission's root
            for task-kind jobs). ``None`` → fail-open (nothing to
            consult; at-least-once wins).
        bus_pending_count: Leg (a) — pending dependency-bus watchers
            for this work, precomputed by the caller (each call site
            owns its bus seam: f2 passes the Gate-1 source-task count;
            the boot sweep passes the target-instance count).
            ``None`` = leg unknown, skipped (the remaining legs
            decide).
        timeout_seconds: Zombie-backstop window; defaults to
            ``MISSION_LIVE_ORPHAN_TIMEOUT_SECONDS``.
        skip_root: Pre-stamp mode (watcher-loss strand component 2,
            2026-10-14) — do NOT count the ROOT itself as a live leg.
            For callers that evaluate liveness immediately BEFORE
            stamping the root COMPLETED: at that moment the root's
            own non-terminal status is exactly what the stamp is
            about to flip, so leg (c) is tautologically live and
            would wrongly suppress every completion. Only the
            DESCENDANTS' liveness (leg b, strand-aware) gates the
            pre-stamp decision. Leg (c) stays active for all other
            consumers (f2, sweeps, the emission gate — where the
            root is already terminal when the gate runs).
        discount_instance_id: Generalizes ``skip_root`` (Pattern (h),
            wi-78967dba, 2026-10-31): the named instance's own
            non-terminal status is NOT a hold reason on this
            evaluation — circular bookkeeping evidence, not liveness
            evidence. Unlike ``skip_root`` (which discounts exactly
            the walk root), this discounts the JOB'S OWN instance
            wherever it appears in the tree (for a child job that is
            a non-root member). Every OTHER member's liveness still
            holds through the normal verdict. Default ``None`` =
            zero behavioral change for every existing caller; the
            conjunct is added at the two sites that already carry
            the ``skip_root`` conjunct.

    Returns:
        :class:`MissionLiveVerdict`. NEVER raises — internal TREE-WALK
        errors collapse to ``live=False, error=True`` (fail-open; the
        caller finalizes). A MEMBER-ALIVE probe error does NOT fail
        open: it HOLDS (``live=True``, ``error=True``) — see the
        fail-MISS invariant in the design doc §2.10.3.
    """
    try:
        return await _evaluate_legs(
            instance_repository=instance_repository,
            instance_id=instance_id,
            bus_pending_count=bus_pending_count,
            timeout_seconds=timeout_seconds,
            skip_root=skip_root,
            discount_instance_id=discount_instance_id,
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


def member_live_task_attempt_at(
    instance_repository: "SQLModelInstanceRepository",
    instance_id: str,
) -> datetime | None:
    """PUBLIC cross-module accessor for the member's task-stamp probe.

    Proposal B PR3 (MAJ-4) — the RECONCILIATION SWEEP (RDRS lane 7)
    reuses the guard's task-stamp-age predicate instead of
    re-deriving a second spelling of the same rule (two spellings of
    one rule are how the two legs drift apart). The sweep's liveness
    leg answers the same question the guard's member-alive leg asks:
    "does this member own a NON-TERMINAL task row, and how old is the
    newest one?"

    The contract is :func:`_member_live_task_attempt_at`'s, verbatim —
    this is a VISIBILITY wrapper, not a second implementation (the
    probe itself, its fail-MISS floor and its undateable-row handling
    live in one place).

    Args:
        instance_repository: The instance repository (its ``engine``
            carries the task read).
        instance_id: The member whose stamp is probed.

    Returns:
        The newest ATTEMPT stamp among the member's non-terminal task
        rows (naive UTC), or ``None`` when the member owns no
        non-terminal task row at all.
    """
    return _member_live_task_attempt_at(instance_repository, instance_id)


def _member_live_task_attempt_at(
    instance_repository: "SQLModelInstanceRepository",
    instance_id: str,
) -> datetime | None:
    """The newest ATTEMPT stamp among ``instance_id``'s non-terminal
    task rows — ``None`` when the instance owns NO non-terminal task
    row.

    The stamp is the row's ``started_at`` (when the worker claimed the
    turn), falling back to ``created_at`` (the row was minted but not
    yet claimed). Naive-UTC digits per the naive-UTC binding
    convention — :func:`parse_completed_at_naive` normalises an aware
    or a naive input, and an unparseable stamp is DROPPED (never
    guessed).

    **UNDATEABLE ROW ⇒ fail-MISS (review C1 + W-3b).** ``None`` is
    returned ONLY when the member owns **NO** non-terminal task row at
    all (the ADDITIVE invariant: a stale member with no live task row
    remains a zombie candidate). A member that DOES own a non-terminal
    task row whose stamp cannot be dated is NOT treated as "no row" —
    the backstop must not fire under a possibly-live member (that was
    fail-STALE against this lane's declared fail-MISS posture).

    **REACHABILITY (W-3, stated honestly):** the branch is NOT
    reachable through the ORM read TODAY — ``task.created_at`` is NOT
    NULL (the migration + the model), and an unparseable stamp RAISES
    inside the ORM's DATETIME read (``ValueError: Invalid isoformat
    string``), so the probe-error path is taken instead (and that path
    also HOLDS — fail-MISS). The branch is a HARDENING for a state a
    future schema/read change could expose.

    **BOUNDED (W-3b):** an undateable CHOSEN stamp falls back to the
    row's own ``created_at`` (the row's AGE), so a hold can never last
    forever. Only a row with NO dateable stamp at all (neither
    ``started_at`` nor ``created_at``) takes the CURRENT time — the
    fail-MISS floor — and that state is unreachable today.

    The status set is ``daemon.constants.NON_TERMINAL_TASK_STATUSES``
    — the canonical non-terminal task set (the SSOT; the same set the
    running-zombie strand's root-level leg uses). ``processing`` is
    LEGACY task vocabulary — it is NOT a ``TaskStatus`` member (the
    enum carries PENDING/RUNNING/PAUSED/COMPLETED/FAILED/CANCELLED) —
    and the constant keeps it DELIBERATELY for legacy rows, so a
    legacy in-flight turn still counts as live work.

    The read goes through the instance repository the guard ALREADY
    holds (``instance_repository.engine``) — no new parameter, no new
    repository method, no ``Engine`` import in this module. The model
    import is DEFERRED (a module-level import would create a
    ``daemon.services`` package-init cycle — the same guard the task
    repository's own deferred imports use).
    """
    from daemon.repositories.task.models import Task
    from sqlmodel import Session, select

    engine = getattr(instance_repository, "engine", None)
    if engine is None:
        raise RuntimeError(
            "mission_live_guard: the instance repository exposes no "
            "engine — the member-task age read cannot be evaluated"
        )
    with Session(engine) as session:
        rows = session.exec(
            select(Task.started_at, Task.created_at).where(
                Task.instance_id == instance_id,
                Task.status.in_(sorted(NON_TERMINAL_TASK_STATUSES)),
            )
        ).all()
    stamps: list[datetime] = []
    for row in rows:
        started_at, created_at = row[0], row[1]
        parsed = parse_completed_at_naive(
            started_at if started_at is not None else created_at
        )
        if parsed is None:
            # The CHOSEN stamp is undateable — bound the row by its
            # own ``created_at`` (the row's AGE) so a hold can never
            # last forever (W-3b).
            parsed = parse_completed_at_naive(created_at)
        if parsed is not None:
            stamps.append(parsed)
    if stamps:
        return max(stamps)
    if not rows:
        # NO non-terminal task row at all ⇒ no dateable evidence of an
        # in-flight turn ⇒ NOT held (the ADDITIVE invariant).
        return None
    # The member OWNS a non-terminal task row, but NOT ONE dateable
    # stamp (neither ``started_at`` nor ``created_at``) — unreachable
    # today (see the docstring), kept as the fail-MISS floor: the ROW
    # EXISTS, so the member may be mid-turn and the tree must NOT be
    # finalized (review C1).
    return now_utc_naive()


async def _member_alive_companion_verdict(
    *,
    instance_repository: "SQLModelInstanceRepository",
    instance_id: str | None,
    member_status: dict[str, str | None],
    stranded_excluded: list[str],
    timeout_seconds: int,
) -> MissionLiveVerdict | None:
    """The MEMBER-ALIVE companion leg — ``None`` means "no hold".

    **UNCONDITIONAL** (council F1, 2026-10-21): the leg lives HERE, in
    the guard — the shared chokepoint — so EVERY consumer inherits it
    (the pre-stamp gate, the emission gate, the running-zombie
    healer's tree leg, f1, f2, the boot sweep, the orphan release, the
    work notifier). The cycle-3 opt-in shape (a ``task_engine``
    parameter) was rejected: it left the other consumers unprotected
    against the same false completion.

    It runs on the STALENESS branch only (the tree already looks
    quiet) and asks, per NON-ROOT non-terminal member, the one
    question that decides a member's real state: **does the member own
    a live task row, and is that row YOUNG?**

    * **A live task row YOUNGER than the horizon ⇒ ALIVE** — the tree
      is WORKING, not stale; the backstop holds it (fail-MISS over a
      false completion).
    * **A live task row OLDER than the horizon ⇒ NOT alive** — a
      crash / kill mid-turn leaves exactly that row behind, and it
      must not hold the mission open forever (RULING-1b). The member
      stays stale-eligible and the root CAN heal.
    * **NO live task row ⇒ NOT held** — the ADDITIVE invariant: a
      stale-activity member with no live task row REMAINS a zombie
      candidate (the OBSERVED family).

    The coarse liveness probe is the instance repository's own
    ``has_live_work`` (the council's API — ZERO new plumbing). The
    HOLD itself is decided on the DATEABLE task evidence, because the
    probe also counts a member's own non-terminal CHILD as "live
    work": a ``waiting_children`` parent would then hold the tree
    forever through its own stuck child. A member whose only live work
    is a JOBITEM row (no task row) is deliberately NOT held for the
    same reason.

    * **Fail-MISS on a probe error** — a probe that cannot be
      evaluated cannot PROVE the tree is stale, and the lanes that
      TERMINALIZE a root must not break a tree they cannot see: the
      leg HOLDS instead (``live=True``, ``error=True``).
    """
    excluded = set(stranded_excluded)
    candidates = sorted(
        tid for tid, status in member_status.items()
        if tid != instance_id
        and status is not None
        and status not in TERMINAL_INSTANCE_STATUSES
        and tid not in excluded
    )
    if not candidates:
        return None
    now = now_utc_naive()
    undatable_live_work = 0

    def _flush_undatable_note() -> None:
        # A-3/G4/P1: ONE aggregated operator-visible line per
        # EVALUATION — emitted at EVERY exit of the member loop (the
        # early-return holds included), so no exit can discard the
        # count. The count is TRUTHFUL: it is the number of members
        # whose live work was reported but NOT dateable (those members
        # are not held on that evidence). It says NOTHING about the
        # tree's verdict (a young task row holds the tree on its own
        # evidence) — the per-member detail stays at DEBUG.
        logger.info(
            "mission_live_guard: member-alive probe for %s... — "
            "members_with_undatable_live_work=%d (those members are NOT "
            "held on that evidence; per-member detail at DEBUG)",
            (instance_id or "?")[:8], undatable_live_work,
        )

    for member_id in candidates:
        try:
            live_work = await asyncio.to_thread(
                instance_repository.has_live_work, member_id,
            )
        except Exception as exc:  # noqa: BLE001 — fail-MISS on probe error
            logger.warning(
                "mission_live_guard: member-alive live-work probe "
                "failed for %s... (%s: %s) — HOLDING the tree (fail-miss: "
                "the leg cannot prove the tree is stale)",
                (instance_id or "?")[:8], type(exc).__name__, exc,
            )
            _flush_undatable_note()
            return MissionLiveVerdict(
                live=True,
                reason=(
                    "member-alive live-work probe failed "
                    f"({type(exc).__name__}: {exc}) — the tree cannot be "
                    "proven stale, so the backstop holds it (fail-miss)"
                ),
                error=True,
            )
        try:
            attempt_at = await asyncio.to_thread(
                _member_live_task_attempt_at,
                instance_repository, member_id,
            )
        except Exception as exc:  # noqa: BLE001 — fail-MISS on probe error
            logger.warning(
                "mission_live_guard: member-alive task-age probe failed "
                "for %s... (%s: %s) — HOLDING the tree (fail-miss: the "
                "leg cannot prove the tree is stale)",
                (instance_id or "?")[:8], type(exc).__name__, exc,
            )
            _flush_undatable_note()
            return MissionLiveVerdict(
                live=True,
                reason=(
                    "member-alive task-age probe failed "
                    f"({type(exc).__name__}: {exc}) — the tree cannot be "
                    "proven stale, so the backstop holds it (fail-miss)"
                ),
                error=True,
            )
        if attempt_at is None:
            # No live task row ⇒ no dateable evidence of an in-flight
            # turn. The member stays stale-eligible (the ADDITIVE
            # invariant). The canonical probe's verdict is logged for
            # the operator: ``live_work=True`` here means the member
            # owns live work that is NOT dateable by a task row (a
            # queued JobItem, or its own non-terminal child) — the leg
            # does not hold on that evidence (see the docstring).
            if live_work:
                # H2 (review C2): the probe and the dateable evidence
                # DISAGREE — the probe says "live work", the evidence
                # says "nothing dateable" — and the leg does NOT hold
                # on it. A-3: the per-member detail stays at DEBUG and
                # the OPERATOR-VISIBLE signal is ONE aggregated line
                # per evaluation (the old shape emitted one INFO per
                # member — N=50 ⇒ 50 lines, ≈400 lines/300s across
                # ~8 consumers).
                undatable_live_work += 1
                logger.debug(
                    "mission_live_guard: member-alive probe for %s... — "
                    "member=%s... live work detected but NOT dateable "
                    "(no live task row) ⇒ NOT held (stale-eligible)",
                    (instance_id or "?")[:8], member_id[:8],
                )
            else:
                logger.debug(
                    "mission_live_guard: member-alive probe for %s... — "
                    "member=%s... live_work=%s no live task row ⇒ "
                    "stale-eligible",
                    (instance_id or "?")[:8], member_id[:8], live_work,
                )
            continue
        age_seconds = (now - attempt_at).total_seconds()
        if age_seconds < timeout_seconds:
            # The member id in this reason is a CANDIDATE — the
            # terminal-candidacy filter above (status NOT in
            # TERMINAL_INSTANCE_STATUSES) is what makes "non-terminal
            # member" accurate; drop that filter and the wording would
            # mislabel a terminal member (A-6).
            #
            # P1: this HOLD path is an evaluation exit too — flush the
            # aggregate here so the one-INFO-per-evaluation contract
            # holds on EVERY path (the count is the number of members
            # whose live work was undatable; it is 0 when none were
            # seen, and it says nothing about this hold).
            _flush_undatable_note()
            return MissionLiveVerdict(
                live=True,
                reason=(
                    f"non-terminal member {member_id[:8]} owns a LIVE "
                    f"task row younger than the {timeout_seconds}s "
                    f"staleness horizon ({int(age_seconds)}s old) — "
                    "the tree is working, not stale; the backstop holds "
                    "it (fail-miss over a false completion)"
                ),
                member_task_hold=True,
            )
        # The task row is OLDER than the horizon — a crash / kill
        # mid-turn leaves exactly that row behind. It does NOT hold the
        # tree (RULING-1b): the member stays stale-eligible.
        logger.debug(
            "mission_live_guard: member-alive probe for %s... — "
            "member=%s... live_work=%s task row %ds old "
            "(>= %ds horizon) ⇒ stale-eligible",
            (instance_id or "?")[:8], member_id[:8], live_work,
            int(age_seconds), timeout_seconds,
        )
    _flush_undatable_note()
    return None


async def _evaluate_legs(
    *,
    instance_repository: "SQLModelInstanceRepository | None",
    instance_id: str | None,
    bus_pending_count: int | None,
    timeout_seconds: int,
    skip_root: bool = False,
    discount_instance_id: str | None = None,
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

    # ── STRAND INVARIANT (watcher-loss terminal strand, 2026-10-14) ──
    # A never-dispatched instance row (status=idle, version=1, zero
    # ``message_queue`` rows, zero ``task`` rows — the canonical ghost
    # signature shared with ``ChildReportsService._ghost_child_filter``
    # via ``SQLModelInstanceRepository.filter_never_dispatched_ids``)
    # whose DIRECT parent is TERMINAL is stranded-by-construction:
    # dispatch requires a message, the automatic dispatcher of a
    # spawned child is its parent, and its parent is terminal — the
    # row can never self-progress. A spawned-but-never-dispatched
    # grandchild of this shape (live RCA 2026-10-14, mission b6e24d78,
    # poison row 4d41bd67) persisted non-terminal and held mission
    # liveness forever: the sync stamp path counts DIRECT children
    # only (all completed → root stamped COMPLETED) while THIS
    # recursive walk counted the depth-2 strand (any_non_terminal →
    # gate blocked → post-stamp downgrade → publish suppressed → no
    # lifecycle terminal event → receipt JobItem never finalized →
    # watcher never fired). Terminal-ancestry-stranded rows therefore
    # MUST NOT hold mission liveness: they are excluded from the
    # leg (b)/(c) non-terminal count below. Read-side exclusion only —
    # this site NEVER mutates the rows (write-side cleanup is the
    # strand healer's job, via the sanctioned lifecycle lane).
    #
    # SAFETY BOUND: a never-dispatched row under a LIVE direct parent
    # is the legitimate spawn-then-message ritual — the parent is
    # about to (or still may) send the first message. Such rows
    # continue to hold liveness and are NEVER excluded here.
    # AGE MIRROR (final hardening 2026-10-14): a ghost under a
    # TERMINAL direct parent is excluded ONLY once it has aged past
    # ``ORPHAN_MIN_AGE_SECONDS`` (the shared
    # :func:`strand_past_race_guard` predicate — the SAME predicate
    # the write-side healer runs before terminalizing). A too-fresh
    # ghost keeps holding liveness: the just-messaged-not-yet-rowed
    # window is real, and excluding on a mid-flight ritual would
    # finalize the mission while the parent's first message is still
    # in the delivery lane (the stamp→downgrade churn class).
    # Dispatchability is judged by the DIRECT parent only: any live
    # instance between the member and the root can still dispatch it
    # (send_message has no parent-ownership check), so a terminal
    # GRANDparent does not strand a member whose direct parent is
    # live. Unknown parent status (row vanished mid-walk) counts as
    # live — fail-closed.
    any_non_terminal = False        # FIXBACK: drives the all-terminal short-circuit
    freshest_activity = None        # max ``last_activity_at`` over the NON-ROOT members
    root_activity = None            # the root's OWN ``last_activity_at`` (fallback only)
    member_count = 0                # non-root members seen during the walk
    anchor_missing = True           # a single non-NULL value flips this to False
    member_status: dict[str, str | None] = {}
    member_parent: dict[str, str | None] = {}
    member_updated: dict[str, object] = {}
    for tree_id in tree_ids:
        instance = await asyncio.to_thread(
            instance_repository.get, tree_id
        )
        if instance is None:
            # Row vanished mid-walk (concurrent delete) — a missing
            # row cannot hold the mission open; skip it.
            continue
        status = getattr(instance, "status", None)
        member_status[tree_id] = status
        member_parent[tree_id] = getattr(instance, "parent_id", None)
        member_updated[tree_id] = getattr(instance, "updated_at", None)
        # FIXBACK: do NOT early-return on a non-terminal observation.
        # Record it; the post-walk decision tree decides the verdict.
        # skip_root (pre-stamp mode): the root's own non-terminal
        # status is what the caller's stamp is about to flip — it is
        # not a hold reason on this evaluation.
        if (
            status is not None
            and status not in TERMINAL_INSTANCE_STATUSES
            and not (skip_root and tree_id == instance_id)
            and not (
                discount_instance_id
                and tree_id == discount_instance_id
            )
        ):
            any_non_terminal = True
        # ANCHOR SCOPE (2026-10-21, running-zombie cycle 2): the
        # backstop anchor is computed over the NON-ROOT members only.
        # The root's own ``last_activity_at`` is refreshed on EVERY
        # message enqueue (``daemon/services/instance_messaging.py``
        # :2455) — a stranded-but-messaged root therefore looks
        # PERMANENTLY fresh and the 6h backstop is unreachable for it
        # (the live 2026-10-21 repro: a 22s-old "freshest tree
        # last_activity_at" on a root dead for hours). The root's
        # stamp is collected SEPARATELY and used ONLY as the fallback
        # for a tree with no members at all (a childless root — there
        # the root IS the whole tree, so its activity is the tree
        # activity). A live descendant's recent activity keeps the
        # backstop from firing on a tree that is plausibly still alive
        # — that remains the legitimate zombie-break input.
        activity_str = getattr(instance, "last_activity_at", None)
        parsed_activity = parse_completed_at_naive(activity_str)
        if tree_id == instance_id:
            root_activity = parsed_activity
            continue
        member_count += 1
        if parsed_activity is not None:
            anchor_missing = False
            if freshest_activity is None or parsed_activity > freshest_activity:
                freshest_activity = parsed_activity

    # The anchor fallback: a tree with NO members (a childless root)
    # keeps its historical behaviour — the root's own stamp IS the
    # tree signal there. A tree WITH members never falls back to the
    # root's stamp (that is the fix).
    if member_count == 0 and root_activity is not None:
        freshest_activity = root_activity
        anchor_missing = False

    # ── Strand exclusion (see STRAND INVARIANT above) ─────────────
    # One extra batched query (``filter_never_dispatched_ids`` — the
    # ghost signature) ONLY when a non-terminal member exists; then a
    # pure in-memory parent-status check over rows already in hand.
    # Failure direction: if the never-dispatched probe fails, strand
    # NOTHING (fail-closed — the rows keep holding liveness).
    stranded_excluded: list[str] = []
    if any_non_terminal:
        non_terminal_ids = [
            tid for tid, st in member_status.items()
            if st is not None and st not in TERMINAL_INSTANCE_STATUSES
        ]
        try:
            never_dispatched = await asyncio.to_thread(
                instance_repository.filter_never_dispatched_ids,
                non_terminal_ids,
            )
        except Exception as exc:  # noqa: BLE001 — fail-closed on probe error
            logger.warning(
                "mission_live_guard: never-dispatched probe failed for "
                "%s... (%s: %s) — stranding NOTHING (fail-closed)",
                instance_id[:8], type(exc).__name__, exc,
            )
            never_dispatched = set()
        for oid in never_dispatched:
            parent_id = member_parent.get(oid)
            parent_status = (
                member_status.get(parent_id) if parent_id else None
            )
            if (
                parent_status is not None
                and parent_status in TERMINAL_INSTANCE_STATUSES
                # Age mirror (final hardening 2026-10-14): the SAME
                # race-guard predicate the write-side healer runs.
                # A too-fresh ghost keeps holding liveness — the
                # just-messaged-not-yet-rowed window is real on the
                # read side too, and a stale exclusion here would
                # finalize under a mid-flight ritual (the stamp→
                # downgrade churn class). Aged-only exclusion also
                # unifies read/write semantics: both sides act on
                # exactly the same row set.
                and strand_past_race_guard(member_updated.get(oid))
            ):
                stranded_excluded.append(oid)
        if stranded_excluded:
            stranded_set = set(stranded_excluded)
            any_non_terminal = any(
                st is not None
                and st not in TERMINAL_INSTANCE_STATUSES
                and tid not in stranded_set
                and not (skip_root and tid == instance_id)
                and not (
                    discount_instance_id
                    and tid == discount_instance_id
                )
                for tid, st in member_status.items()
            )

    # ── C1: ALL-TERMINAL tree → finalize immediately ──────────────
    # FIXBACK: the anchor plays NO role here. Every tree member is
    # terminal — the mission is closed, the held row may drain. This
    # restores the pre-U7 frozen terminal contract that the
    # predecessor's overshoot (fall-through ``live=True``) violated.
    if not any_non_terminal:
        _strand_note = (
            f"; {len(stranded_excluded)} terminal-ancestry strand "
            f"member(s) excluded from liveness"
            if stranded_excluded
            else ""
        )
        return MissionLiveVerdict(
            live=False,
            reason=(
                "no live leg: root + descendants terminal, bus quiet "
                "— all-terminal tree finalizes immediately "
                "(anchor plays no role on all-terminal trees)"
                f"{_strand_note}"
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
                "non-terminal member present but no "
                "last_activity_at anchor on any non-root tree member — "
                "backstop disarmed (data gap, not zombie evidence); "
                "the live leg holds by default"
            ),
        )

    age_seconds = (
        now_utc_naive() - freshest_activity
    ).total_seconds()
    if age_seconds >= timeout_seconds:
        # ── MEMBER-ALIVE companion leg (2026-10-21, council F1 +
        # RULING-1b) ─────────────────────────────────────────────────
        # The tree LOOKS stale (no member activity for >= the backstop
        # window). Before breaking it, ask the ONE question that
        # decides a member's real state: does a non-terminal member
        # own a LIVE task row — and is that row YOUNG?
        #
        # ``instances.last_activity_at`` is written on the enqueue /
        # child-report / observer paths — NOT on turn progress — so a
        # member inside a long (>6h) turn reads STALE here while it
        # is working right now. Breaking the tree there would
        # finalize a root whose member is mid-turn: a FALSE
        # COMPLETION. The ruling (leader, cycle 3) is fail-MISS over
        # false-completion: a member with a live task row counts as
        # ALIVE, not stale.
        #
        # TIME-BOUND (RULING-1b): only a task row YOUNGER than the
        # horizon holds the tree. An OLDER row (a crash / kill
        # mid-turn) does NOT — otherwise the leg would trade one
        # permanent strand for another.
        #
        # UNCONDITIONAL (council F1): the leg lives in the guard, so
        # EVERY consumer inherits it — no opt-in parameter, no new
        # plumbing.
        #
        # COST: PER-MEMBER probes (the repository's ``has_live_work``
        # + one task read per candidate member), and they run ONLY on
        # this staleness branch, so a live tree pays nothing.
        _hold_verdict = await _member_alive_companion_verdict(
            instance_repository=instance_repository,
            instance_id=instance_id,
            member_status=member_status,
            stranded_excluded=stranded_excluded,
            timeout_seconds=timeout_seconds,
        )
        if _hold_verdict is not None:
            return _hold_verdict
        return MissionLiveVerdict(
            live=False,
            reason=(
                f"mission-tree backstop fires: non-terminal members "
                f"present AND freshest tree-member last_activity_at "
                f"(root excluded) is {int(age_seconds)}s old "
                f"(>{timeout_seconds}s threshold) — zombie-break, "
                f"at-least-once terminal delivery takes precedence"
            ),
            timed_out=True,
        )

    # ── Non-terminal + anchor fresh → defer ───────────────────────
    return MissionLiveVerdict(
        live=True,
        reason=(
            f"non-terminal member present (live leg holds) and "
            f"freshest tree-member last_activity_at (root excluded) is "
            f"{int(age_seconds)}s old (<{timeout_seconds}s threshold) "
            f"— mission is plausibly alive; defer to the next tick"
        ),
    )
