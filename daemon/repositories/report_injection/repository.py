"""SQLModel-based ReportInjection repository.

Persistence layer for the ``report_injections`` table. Exposes the
three primitives the two delivery paths need:

* :meth:`enqueue` — register a new PENDING report (used by tests and
  callers that do not already hold a ``WriteGuardSession``; production
  enqueue happens inline in ``child_reports`` for transactional
  atomicity with the ``message_queue`` row + ``PROCESS_REPORT`` task).
* :meth:`claim_for_injection` — atomically claim ALL pending reports
  for a parent (the live agent-node drain) and mark their companion
  ``message_queue`` rows ``COMPLETED`` so they are not counted as
  pending own-queue work. Marks each row ``INJECTED``.
* :meth:`claim_for_task_delivery` — atomically claim a single report
  for the fallback ``PROCESS_REPORT`` task. Marks the row
  ``TASK_DELIVERED``; returns ``None`` when the row was already
  claimed by the injection path (exactly-once).

All claim methods use a guarded ``WHERE state = 'PENDING'`` UPDATE so
the two delivery paths cannot double-deliver a report, regardless of
which races ahead. The per-instance serialization guard in
``claim_pending_task`` (one RUNNING task per instance) guarantees the
agent-node drain and a fallback task for the SAME instance never run
concurrently, so the two claim methods are serialized per instance by
construction — the atomic claim is defense-in-depth for the
cross-instance / restart window.

The repository is intentionally sync; callers bridge to async via
``asyncio.to_thread`` (the project's standard pattern). The
agent-node drain runs inside the graph task on the event loop and
wraps the claim in ``asyncio.to_thread`` so the DB write does not
block the loop.

Phase 1 (pause-report-recovery) marker lifecycle
------------------------------------------------

The ``report_injections`` table is the home for the
DEFERRED delivery-obligation marker. State machine (mirrors the
model docstring):

* ``enqueue`` — creates a PENDING row WITH an artifact
  (``report_message_id`` set). Used by the regular completion path.
* :meth:`ensure_deferred` — creates a DEFERRED marker WITHOUT an
  artifact (``report_message_id`` NULL). The pause drop-site writers
  (Site 1, Variant B live site, Variant B idempotency guard) call
  this so a paused/cancelled obligation can be recovered later. The
  partial unique index ``uq_report_injections_oblig_triple`` (state
  IN ('PENDING','DEFERRED')) is the write-once gate: a concurrent
  duplicate ``ensure_deferred`` for the same triple raises
  ``sqlalchemy.exc.IntegrityError`` and this method absorbs it (W6).
* :meth:`find_deferred_for_parent` — Phase 2/3 recovery path lists
  the DEFERRED markers awaiting recovery for a parent.
* :meth:`transition_deferred_to_pending` — guarded ``UPDATE ...
  WHERE state='DEFERRED'`` plus a ``recovery_attempted_at`` stamp.
  rowcount=0 means another actor already recovered → returns False.

Contract: ``enqueue`` and ``ensure_deferred`` are the ONLY row-creation
paths on this table. Every non-terminal obligation is gated by the
triple unique index; every DEFERRED → INJECTED/TASK_DELIVERED
transition is forbidden by the ``state='PENDING'`` claim guards.

The recovery_attempted_at column is partial-indexed over PENDING rows
so the Phase 2 recovery sweep (``state='PENDING' AND
recovery_attempted_at IS NOT NULL``) stays cheap as the table grows.

Case-lockstep contract (C1): storage literals ('PENDING','DEFERRED',
reason values from ``daemon.constants``) and the app-layer constants
are UPPERCASE and must never drift in case. Any new state/reason value
is added to the storage enum / DDL AND the app constants in the same
change. The partial-index predicate uses the storage literals verbatim.

C4 NULL-keyed ``report_message_id`` grep-audit checklist
-------------------------------------------------------

``report_message_id`` is nullable (Phase 1 C4). NULL arises ONLY from
marker-first writes (``ensure_deferred`` — DEFERRED rows with no
artifact yet). Every consumer of the column MUST handle-or-exclude
NULL so a NULL-keyed row never silently mis-claims.

* :meth:`claim_for_injection` — guarded by ``state='PENDING'`` (not
  on ``report_message_id``); the ``RETURNING`` set only includes
  non-terminal rows. DEFERRED rows have NULL ``report_message_id``
  but are excluded by the state predicate. PASS (handles).
* :meth:`claim_for_task_delivery` — keyed on
  ``report_message_id`` via a SELECT-FIRST / UPDATE-SECOND pattern;
  the ``SELECT `` returns ``missing`` when the row is NULL-keyed
  (Phase 2 C4 acceptance — the natural ``enqueue`` path's tri-state
  is unchanged at the consumer; recovery reconciliation handles the
  NULL branch via the 2.2 reconciliation path).
* :meth:`ensure_deferred` — writes ``report_message_id=None``; the
  partial unique index absorbs concurrent duplicates (W6).
* :meth:`enqueue` — production path requires a non-NULL
  ``report_message_id`` (the artifact exists); the parameter type
  is non-optional. PASS (excludes).
* :meth:`transition_deferred_to_pending` — keyed on
  ``injection_id``, not on ``report_message_id``. PASS (excludes).
* :meth:`count_pending_for_parent` — counts by state, not by
  ``report_message_id``. PASS (excludes).
* :meth:`find_deferred_for_parent` and
  :meth:`find_deferred_for_parent_all` — keyed on state, not on
  ``report_message_id``. PASS (excludes).
* :meth:`find_completed_children_without_delivery` (Lane 2) — keyed
  on ``child_instance_id`` / ``child_message_id``, NOT on
  ``report_message_id``. PASS (excludes).
* :meth:`find_pending_past_age` (Lane 3+4) — keyed on state +
  timestamps, NOT on ``report_message_id``. PASS (excludes).
* The reconciliation path in
  :func:`InstanceManager._recover_deferred_report`
  (manager.py 2.1+2.2) explicitly handles the ``report_message_id IS
  NULL`` branch — full artifact creation when NULL, UPDATE-in-place
  when non-NULL.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, NamedTuple

from sqlalchemy import literal, true, update as sa_update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import aliased
from sqlmodel import Session, select

from ..dependency_bus.models import DependencyWatcher, DependencyWatcherState
from ..instance.models import Instance, InstanceStatus
from ..message_queue.models import MessageQueue, MessageStatus
from ..task.models import Task
from .models import ReportInjection, ReportInjectionState

logger = logging.getLogger(__name__)


# Module-level literals captured once to avoid re-evaluating the enums
# on every claim call.
_PENDING_STATE: str = ReportInjectionState.PENDING.value
_DEFERRED_STATE: str = ReportInjectionState.DEFERRED.value
_INJECTED_STATE: str = ReportInjectionState.INJECTED.value
_TASK_DELIVERED_STATE: str = ReportInjectionState.TASK_DELIVERED.value
# Dead-letter sentinel (T8 (b) / T8 (e)). Stored verbatim — the storage
# layer is case-sensitive (C1). Not in the partial-unique-index
# predicate (``uq_report_injections_oblig_triple`` excludes terminal
# states including this one — a fresh non-terminal obligation for a
# previously-dead-lettered triple is allowed, e.g. a re-spawn scenario).
_FAILED_STATE: str = ReportInjectionState.FAILED.value
_MSG_COMPLETED: str = MessageStatus.COMPLETED.value

# Phase 2 (pause-report-recovery) sweep: the parent terminal set is
# the same set the pause-cascade selectors use
# (``InstanceStatus.is_valid`` terminal set + FAILED for task-level
# failures). Mirrored verbatim from
# ``daemon/services/job_queue_service.py:TERMINAL_STATUSES``.
_PARENT_TERMINAL_STATUSES: frozenset[str] = frozenset(
    {
        InstanceStatus.COMPLETED.value,
        InstanceStatus.ERROR.value,
        InstanceStatus.TERMINATED.value,
        InstanceStatus.FAILED.value,
    }
)

# ``dependency_watchers.state`` note (PENDING / FIRED / CANCELLED):
# the watcher table's ``state`` is its own TEXT enum
# (:class:`DependencyWatcherState`) and is NOT the same as
# ``ReportInjectionState``. The FIRED-exclusion subquery below
# references ``DependencyWatcherState.FIRED.value`` directly.


class TaskDeliveryClaim(NamedTuple):
    """Tri-state result of :meth:`ReportInjectionRepository.claim_for_task_delivery`.

    Distinguishes three outcomes so the caller never silently loses a
    report by conflating "no row" with "already delivered":

    * ``"claimed"`` — a PENDING row existed and this call atomically
      transitioned it to ``TASK_DELIVERED``. The task OWNS delivery and
      proceeds with the normal message-processing pipeline. ``row`` is
      the claimed :class:`ReportInjection`.
    * ``"already_delivered"`` — a row exists but is terminal
      (``INJECTED`` by the live agent-node drain, or
      ``TASK_DELIVERED`` by a prior task run). The task MUST skip
      (dedup gate). ``row`` is ``None``.
    * ``"missing"`` — no ``report_injections`` row exists for this
      ``report_message_id`` at all (e.g. a ``PROCESS_REPORT`` task
      created by older code before this table existed, or a code path
      that forgot to enqueue). The task MUST proceed with normal
      delivery — treating "missing" as "delivered" would silently lose
      the report. ``row`` is ``None``.
    """

    status: str
    row: ReportInjection | None


class ReportInjectionRepository:
    """SQLModel-based repository for the ``report_injections`` table.

    All methods are synchronous; callers bridge to async via
    ``asyncio.to_thread`` or invoke from inside a worker-thread
    context.
    """

    def __init__(self, engine: Engine):
        """Initialize the repository with a database engine.

        Args:
            engine: SQLAlchemy Engine bound to a SQLite or PostgreSQL
                database. Should be the same shared engine used by the
                other repositories to avoid lock contention.
        """
        self.engine = engine

    # --------------------------------------------------------
    # INTERNAL HELPERS
    # --------------------------------------------------------

    def _now_iso(self) -> str:
        """Return current UTC time as ISO-8601 string."""
        return datetime.now(timezone.utc).isoformat()

    # --------------------------------------------------------
    # CREATE
    # --------------------------------------------------------

    def enqueue(
        self,
        parent_instance_id: str,
        child_instance_id: str,
        child_message_id: str,
        report_message_id: str,
        content: str,
    ) -> ReportInjection:
        """Insert a new PENDING report-injection row (WITH artifact).

        Production enqueue happens INLINE in
        ``child_reports._process_child_completion_db_sync`` (same
        ``WriteGuardSession`` transaction as the ``message_queue``
        row + ``PROCESS_REPORT`` task) for crash-consistency. This
        method exists for tests and for any caller that does not
        already hold a session.

        Phase 1 (pause-report-recovery): this is one of the ONLY two
        row-creation paths on this table (the other is
        :meth:`ensure_deferred`). The regular completion path uses
        ``enqueue`` because it has a real artifact
        (``report_message_id`` set, ``content`` non-null). Pause
        drop-site writers use ``ensure_deferred`` (no artifact yet —
        Phase 2 reconciliation fills it in).

        Args:
            parent_instance_id: The parent that should receive the
                report.
            child_instance_id: The child that produced the report.
            child_message_id: The child's completed ``message_id``.
            report_message_id: The ``message_id`` of the companion
                ``completion_report`` row in ``message_queue``.
            content: The report text to inject.

        Returns:
            The persisted :class:`ReportInjection` row (refreshed
            with any DB-side defaults).
        """
        row = ReportInjection(
            parent_instance_id=parent_instance_id,
            child_instance_id=child_instance_id,
            child_message_id=child_message_id,
            report_message_id=report_message_id,
            content=content,
        )
        with Session(self.engine) as session:
            session.add(row)
            session.commit()
            session.refresh(row)
        return row

    # --------------------------------------------------------
    # PHASE 1 — DEFERRED marker lifecycle
    # --------------------------------------------------------

    def ensure_deferred(
        self,
        parent_instance_id: str,
        child_instance_id: str,
        child_message_id: str,
        deferred_reason: str,
    ) -> ReportInjection | None:
        """Write-once insert (or in-place update) of a DEFERRED marker.

        Pause drop-site writers (Site 1 at
        ``message_processing_pipeline.py``, Variant B live site at
        ``child_reports.py:2106``, Variant B idempotency guard at
        ``child_reports.py:1626``) call this to record a
        delivery-obligation marker when the natural completion path
        is blocked. Phase 2's router/sweep will recover the marker
        back to PENDING via
        :meth:`transition_deferred_to_pending` and then complete the
        delivery.

        The partial unique index ``uq_report_injections_oblig_triple``
        (``WHERE state IN ('PENDING','DEFERRED')``) is the write-once
        gate. Two outcomes:

        * **No existing non-terminal row**: insert a fresh DEFERRED
          row with ``report_message_id=None``, ``content=None``.
        * **Concurrent duplicate** (e.g. router vs sweep vs Site 1
          racing the same triple): the partial unique index rejects
          the second INSERT with ``sqlalchemy.exc.IntegrityError``;
          this method absorbs the error (W6 — the child-keyed bus
          lock does NOT serialize the three actors, so the index is
          the only cross-actor gate) and returns ``None`` (no-op).
        * **Existing DEFERRED/PENDING row for the same triple**:
          re-fetch and (only if the ``deferred_reason`` differs) UPDATE
          the reason in-place. Never duplicates; never escalates the
          state.

        Args:
            parent_instance_id: The parent that should eventually
                receive the report.
            child_instance_id: The child that produced the report.
            child_message_id: The child's completed ``message_id``.
            deferred_reason: One of the ``DEFERRED_REASON_*``
                constants in ``daemon.constants`` (UPPERCASE;
                C1 case-lockstep contract — the value written here
                matches the storage enum literal verbatim).

        Returns:
            The persisted :class:`ReportInjection` row when this call
            inserted or updated; ``None`` when the call was a
            no-op (concurrent duplicate absorbed by W6).

        Raises:
            Any non-integrity DB error propagates. ``IntegrityError``
            on the obligation-triple index is the ONLY error caught
            here.
        """
        with Session(self.engine) as session:
            try:
                row = ReportInjection(
                    parent_instance_id=parent_instance_id,
                    child_instance_id=child_instance_id,
                    child_message_id=child_message_id,
                    report_message_id=None,
                    content=None,
                    state=_DEFERRED_STATE,
                    deferred_reason=deferred_reason,
                    delivered_at=None,
                    recovery_attempted_at=None,
                )
                session.add(row)
                session.commit()
                session.refresh(row)
                logger.info(
                    f"[ReportInjection] DEFERRED marker written: "
                    f"parent={parent_instance_id[:8]}..., "
                    f"child={child_instance_id[:8]}..., "
                    f"msg={child_message_id[:8]}..., "
                    f"reason={deferred_reason}"
                )
                return row
            except IntegrityError:
                # W6: concurrent duplicate (router/sweep/Site 1 races
                # the same triple). The child-keyed bus lock does
                # NOT serialize the three actors — this is the only
                # cross-actor gate. Roll back, then update the
                # existing non-terminal row's reason if it differs.
                session.rollback()
                existing = session.exec(
                    select(ReportInjection)
                    .where(
                        ReportInjection.parent_instance_id
                        == parent_instance_id
                    )
                    .where(
                        ReportInjection.child_instance_id
                        == child_instance_id
                    )
                    .where(
                        ReportInjection.child_message_id
                        == child_message_id
                    )
                    .where(
                        ReportInjection.state.in_([
                            _PENDING_STATE,
                            _DEFERRED_STATE,
                        ])
                    )
                ).first()
                if existing is None:
                    # Benign race, expected occasionally: the
                    # IntegrityError fired but the SELECT
                    # (post-rollback) finds no non-terminal row. The
                    # terminal rows exist (the index predicate
                    # excludes them), so a race escalated to terminal
                    # between the rolled-back INSERT and this SELECT.
                    # No-op — delivery has happened.
                    logger.info(
                        f"[ReportInjection] ensure_deferred no-op: "
                        f"report was already delivered "
                        f"(racing delivery won) for "
                        f"parent={parent_instance_id[:8]}..., "
                        f"child={child_instance_id[:8]}..., "
                        f"msg={child_message_id[:8]}... "
                        f"(original reason={deferred_reason}) "
                        f"— no action needed"
                    )
                    return None
                if existing.deferred_reason != deferred_reason:
                    logger.info(
                        f"[ReportInjection] ensure_deferred updating "
                        f"reason for existing row "
                        f"injection_id={existing.injection_id[:8]}... "
                        f"{existing.deferred_reason} -> {deferred_reason}"
                    )
                    existing.deferred_reason = deferred_reason
                    session.add(existing)
                    session.commit()
                    session.refresh(existing)
                    return existing
                logger.debug(
                    f"[ReportInjection] ensure_deferred: duplicate "
                    f"DEFERRED marker skipped (another actor already "
                    f"wrote it) — benign, for "
                    f"parent={parent_instance_id[:8]}..., "
                    f"child={child_instance_id[:8]}..., "
                    f"msg={child_message_id[:8]}... "
                    f"(reason={deferred_reason})"
                )
                return None

    def find_row_by_report_message_id(
        self, report_message_id: str | None
    ) -> ReportInjection | None:
        """Return the ``ReportInjection`` row for ``report_message_id``.

        Phase 2 (pause-report-recovery task 2.3) helper for the FM-1
        type-aware guard's exemption predicate. Returns ``None``
        for NULL-keyed rows (C4 — the Site-1 marker shape, handled
        by the recovery sweep / router before the FM-1 loop).

        Args:
            report_message_id: The ``message_id`` to look up; ``None``
                returns ``None`` (C4 — NULL-keyed rows are
                pre-artifact markers, not deliverable PROCESS_REPORT
                tasks).

        Returns:
            The :class:`ReportInjection` row, or ``None`` when no
            row exists for ``report_message_id``.
        """
        if report_message_id is None:
            return None
        with Session(self.engine) as session:
            return session.exec(
                select(ReportInjection).where(
                    ReportInjection.report_message_id == report_message_id
                )
            ).first()

    def find_deferred_for_parent(
        self, parent_instance_id: str
    ) -> list[ReportInjection]:
        """Return all DEFERRED markers awaiting recovery for a parent.

        Phase 2/3 recovery path: router/sweep lists DEFERRED markers
        for a parent and transitions them back to PENDING via
        :meth:`transition_deferred_to_pending`. Ordered by
        ``created_at`` ascending (oldest first) — matches the drain
        order so the recovery sweep is stable across concurrent
        recovery actors.

        Diagnostic / non-hot-path. Used by the Phase 2 router re-entry
        and the Phase 2/3 sweep service.

        Args:
            parent_instance_id: The parent whose DEFERRED markers
                should be listed.

        Returns:
            List of :class:`ReportInjection` rows with
            ``state='DEFERRED'`` for the parent, oldest first. Empty
            list when none.
        """
        with Session(self.engine) as session:
            stmt = (
                select(ReportInjection)
                .where(ReportInjection.parent_instance_id == parent_instance_id)
                .where(ReportInjection.state == _DEFERRED_STATE)
                .order_by(ReportInjection.created_at.asc())
            )
            return list(session.exec(stmt).all())

    def transition_deferred_to_pending(
        self, injection_id: str
    ) -> bool:
        """Atomically transition DEFERRED → PENDING (guarded UPDATE).

        Recovery actor entry point. Stamps ``recovery_attempted_at``
        so the Phase 2 sweep can re-process mid-sweep-crash rows
        (FM-13). Exactly one of the concurrent recovery actors (router
        / sweep / FM-1-guarded path) wins the race; losers see
        ``rowcount=0`` and skip.

        Args:
            injection_id: The :class:`ReportInjection` to recover.

        Returns:
            ``True`` when this call atomically transitioned the row
            from DEFERRED to PENDING (caller OWNS the recovery —
            proceed to the full enqueue). ``False`` when rowcount=0 —
            another actor already recovered the row, or the row was
            never DEFERRED (terminal / missing). The caller MUST skip
            in that case.
        """
        now_iso = self._now_iso()
        with Session(self.engine) as session:
            result = session.execute(
                sa_update(ReportInjection)
                .where(ReportInjection.injection_id == injection_id)
                .where(ReportInjection.state == _DEFERRED_STATE)
                .values(
                    state=_PENDING_STATE,
                    recovery_attempted_at=now_iso,
                )
            )
            if result.rowcount == 0:
                session.rollback()
                return False
            session.commit()
            return True

    # --------------------------------------------------------
    # PHASE 2 — recovery sweep (5 lanes)
    # --------------------------------------------------------

    def find_deferred_for_parent_all(
        self,
        *,
        parent_not_terminal: bool,
        limit: int = 100,
    ) -> list[ReportInjection]:
        """Find DEFERRED rows for the periodic sweep (Lane 1 + Lane 5).

        Phase 2 (pause-report-recovery, W1). Periodic sweep Lane 1
        (DEFERRED rows for non-terminal parents — gated by
        ``has_instance_busy(parent_id)``, no age bound; age
        filtering lives on Lanes 3+4 via ``find_pending_past_age``)
        and Lane 5 (ORPHAN — DEFERRED rows whose parent is TERMINAL)
        share the same SELECT shape, parameterized by
        ``parent_not_terminal``:

        * ``parent_not_terminal=True`` — terminal parents EXCLUDED
          (the periodic sweep's Lane 1 + Lane 2 contract; Lane 5 owns
          terminal-parent rows).
        * ``parent_not_terminal=False`` — all parents (the ORPHAN lane
          and diagnostic/manual callers).

        Args:
            parent_not_terminal: ``True`` → filter to non-terminal
                parents; ``False`` → include terminal parents too.
            limit: Cap the number of rows returned (MVP growth rule —
                batch cap 100/run, remainder logged and picked up on
                the next cycle).

        Returns:
            List of :class:`ReportInjection` rows in DEFERRED state,
            oldest first. Empty list when none.
        """
        with Session(self.engine) as session:
            stmt = (
                select(ReportInjection, Instance.parent_id)
                .join(
                    Instance,
                    Instance.instance_id == ReportInjection.parent_instance_id,
                )
                .where(ReportInjection.state == _DEFERRED_STATE)
            )
            if parent_not_terminal:
                stmt = stmt.where(
                    ~Instance.status.in_(_PARENT_TERMINAL_STATUSES)
                )
            stmt = (
                stmt.order_by(ReportInjection.created_at.asc())
                .limit(limit)
            )
            rows = list(session.exec(stmt).all())
            # Returning just the ReportInjection rows — the JOIN is
            # for filtering only.
            return [row for row, _parent_id in rows]

    def find_completed_children_without_delivery(
        self,
        *,
        parent_not_terminal: bool,
        limit: int = 100,
    ) -> list[dict[str, str]]:
        """Find children with no delivery row, message, or watcher (C3 Lane 2).

        Phase 2 (pause-report-recovery, C3). The designed-from-scratch
        no-row backstop query — replaces the v2 placeholder name. The
        periodic sweep runs this query as **Lane 2**: every row it
        returns is a candidate recovery obligation that no other
        marker wrote (FM-11 escape / cancel-mid-shield / crash
        without a marker / no-row drop). The 5-case false-positive
        matrix (C3) is enforced by the LEFT-JOINs below — every
        false-positive shape (existing message / existing injection
        row / existing FIRED watcher) is excluded.

        Join keys (verified, driver-neutral):

        * ``instances.parent_id = c.parent_id`` — the parent is the
          ``c`` row's parent.
        * ``message_queue.source = 'internal_report:' || c.instance_id
          || ':' || m.message_id`` — string concat is driver-neutral
          (TEXT columns; PG supports ``||``, SQLite supports ``||``).
        * ``dependency_watchers.source_task_id = child's TASK id`` and
          ``dependency_watchers.target_instance_id = parent`` — join
          via ``tasks`` table to keep the join driver-neutral (the
          ``watcher_metadata`` JSONB holds the child id but is
          driver-dependent to query per the design review).

        Args:
            parent_not_terminal: ``True`` → exclude terminal parents
                (the periodic sweep's contract — terminal-parent
                obligations are the ORPHAN lane's territory via their
                DEFERRED rows). ``False`` → include terminal parents
                (diagnostic / manual callers; the periodic sweep
                never passes ``False`` — the ORPHAN lane is the
                terminal-parent entry path).
            limit: Batch cap.

        Returns:
            List of ``{"child_id", "child_msg_id", "parent_id"}``
            dicts. Empty list when none.
        """
        # String concat via SQL ``||`` (driver-neutral on TEXT
        # columns; both SQLite and PostgreSQL support it).
        source_predicate = (
            literal("internal_report:")
            + Instance.instance_id
            + literal(":")
            + MessageQueue.message_id
        )
        # Cast to text so the LEFT JOIN's ON predicate compares the
        # computed expression to ``message_queue.source`` (a String
        # column). SQLAlchemy resolves the cast automatically when the
        # column type is String — we keep the literal() expression
        # opaque to avoid driver-specific string concat differences.
        # The placeholder SELECT below is removed — the real query
        # uses explicit ``aliased`` table instances to keep the join
        # keys driver-neutral (no raw-table aliases that depend on
        # SQLAlchemy internals).

        # NOT EXISTS — driver-neutral FIRED-watcher exclusion.
        # Use SQLAlchemy's ``exists()`` with a ``select()``
        # subquery so the predicate is a ColumnElement (raw
        # ``text()`` is not — see SQLAlchemy 1.4+
        # ``expect(ExpressionElementRole)`` assertion). The
        # join keys (verified, driver-neutral):
        #   * ``tasks.id = dependency_watchers.source_task_id``
        #     (the child's Task id, stored as String for
        #     portability — CAST ensures driver neutrality)
        #   * ``tasks.instance_id = c.instance_id``
        #     (the child instance)
        #   * ``dependency_watchers.target_instance_id = p.instance_id``
        #     (the parent instance)
        #   * ``dependency_watchers.state = 'FIRED'``
        # The ``watcher_metadata`` JSONB alternative is
        # driver-dependent per the design review; the JOIN
        # on ``tasks`` is the canonical driver-neutral path.
        from sqlalchemy import Integer, cast as sa_cast, exists

        with Session(self.engine) as session:
            parent_inst = aliased(Instance, name="p")
            child_inst = aliased(Instance, name="c")
            child_msg = aliased(MessageQueue, name="m")
            # source_match = 'internal_report:' || c.instance_id || ':' || m.message_id
            source_expr = (
                literal("internal_report:")
                + child_inst.instance_id
                + literal(":")
                + child_msg.message_id
            )
            stmt = (
                select(
                    child_inst.instance_id.label("child_id"),
                    child_msg.message_id.label("child_msg_id"),
                    parent_inst.instance_id.label("parent_id"),
                )
                .select_from(child_inst)
                .join(
                    child_msg,
                    (child_msg.instance_id == child_inst.instance_id)
                    & (child_msg.status == _MSG_COMPLETED),
                )
                .join(
                    parent_inst,
                    parent_inst.instance_id == child_inst.parent_id,
                )
                # LEFT JOIN report_queue (existence of a queued
                # report message → no recovery needed)
                .outerjoin(
                    MessageQueue,
                    (MessageQueue.instance_id == parent_inst.instance_id)
                    & (MessageQueue.source == source_expr),
                )
                # LEFT JOIN report_injections (existence of any
                # non-terminal injection row → no recovery needed)
                .outerjoin(
                    ReportInjection,
                    (ReportInjection.child_instance_id == child_inst.instance_id)
                    & (ReportInjection.child_message_id == child_msg.message_id)
                    & (ReportInjection.state.in_([
                        _PENDING_STATE,
                        _DEFERRED_STATE,
                    ])),
                )
            )
            dw = aliased(DependencyWatcher, name="dw")
            task_tt = aliased(Task, name="tt")
            not_exists_predicate = ~exists(
                select(dw.watch_id)
                .join(
                    task_tt,
                    task_tt.id == sa_cast(dw.source_task_id, Integer),
                )
                .where(task_tt.instance_id == child_inst.instance_id)
                .where(dw.target_instance_id == parent_inst.instance_id)
                .where(
                    dw.state == DependencyWatcherState.FIRED.value
                )
            )
            # Append the remaining ``where`` / ``order_by`` /
            # ``limit`` clauses to the chained ``stmt``.
            stmt = (
                stmt
                .where(not_exists_predicate)
                # Only COMPLETED child instances.
                .where(child_inst.status == InstanceStatus.COMPLETED.value)
                # Periodic-sweep contract: terminal parents excluded.
                # Diagnostic/manual callers pass ``False`` and get
                # the full view incl. terminal parents. Uses
                # ``sqlalchemy.true()`` (cross-driver literal) instead
                # of the SQLite-only ``"1=1"`` (cheap-suggestion
                # 2026-08-20).
                .where(
                    ~parent_inst.status.in_(_PARENT_TERMINAL_STATUSES)
                    if parent_not_terminal
                    else true()
                )
                # NULL on the LEFT JOINs = no matching row exists
                # = the row IS a candidate recovery obligation.
                .where(MessageQueue.message_id.is_(None))
                .where(ReportInjection.injection_id.is_(None))
                .order_by(child_inst.last_activity_at.asc().nullslast())
                .limit(limit)
            )
            rows = list(session.exec(stmt).all())
        return [
            {
                "child_id": row.child_id,
                "child_msg_id": row.child_msg_id,
                "parent_id": row.parent_id,
            }
            for row in rows
        ]

    def find_pending_past_age(
        self,
        *,
        age_bound: timedelta,
        recovery_retry_minutes: int,
        limit: int = 100,
    ) -> list[ReportInjection]:
        """Find stranded PENDING rows past the age guard (Lane 3 + 4).

        Phase 2 (pause-report-recovery, W9/FM-13). Two lanes read the
        same query shape — Lane 3 covers stranded PENDING rows that
        were never stamped (FM-1 escape / FM-3 missing corrective
        path); Lane 4 re-processes stamped-stale rows whose
        ``recovery_attempted_at`` is past the retry interval (the
        mid-sweep-crash gap). The predicate is the union of both:

        ``state='PENDING' AND created_at < now - age_bound AND (
        recovery_attempted_at IS NULL OR recovery_attempted_at <
        now - retry_minutes )``

        Args:
            age_bound: Minimum age (``created_at < now - age_bound``).
                Skips very recent PENDING rows that might be in
                flight.
            recovery_retry_minutes: Stamps younger than this are
                skipped (Lane 4 retry guard — avoid re-processing a
                row the previous sweep just claimed). Rows with
                ``recovery_attempted_at IS NULL`` are always eligible
                (Lane 3).
            limit: Batch cap.

        Returns:
            List of :class:`ReportInjection` rows in PENDING state
            past the age guard and past the retry interval. Oldest
            first.
        """
        now = datetime.now(timezone.utc)
        cutoff_age = (now - age_bound).isoformat()
        cutoff_retry = (
            now - timedelta(minutes=recovery_retry_minutes)
        ).isoformat()
        with Session(self.engine) as session:
            stmt = (
                select(ReportInjection)
                .where(ReportInjection.state == _PENDING_STATE)
                .where(ReportInjection.created_at < cutoff_age)
                .where(
                    # IS NULL OR < cutoff — unified Lane 3 + Lane 4
                    # predicate; the partial index
                    # ``ix_report_injections_recovery_attempted``
                    # keeps the lookup cheap for the stamped-stale
                    # case.
                    (
                        ReportInjection.recovery_attempted_at.is_(None)
                    ) | (
                        ReportInjection.recovery_attempted_at < cutoff_retry
                    )
                )
                .order_by(ReportInjection.created_at.asc())
                .limit(limit)
            )
            return list(session.exec(stmt).all())

    # --------------------------------------------------------
    # CLAIM — agent-node drain (live parent turn)
    # --------------------------------------------------------

    def claim_for_injection(
        self, parent_instance_id: str
    ) -> list[dict[str, Any]]:
        """Atomically claim ALL pending reports for a parent.

        Called by the parent's live agent-node (via the
        ``ReportInjectionSlot`` factory-closure handle) right before
        its LLM call, after the user-message injection pull. Drains
        every PENDING report for the parent in one transaction,
        transitions each to ``INJECTED``, and marks the companion
        ``message_queue.completion_report`` rows ``COMPLETED`` so they
        are not counted as pending own-queue work for the parent
        (which would otherwise wedge the parent in
        ``WAITING_CHILDREN``).

        Exactly-once vs the fallback task: the guarded
        ``WHERE state = 'PENDING'`` UPDATE means a report already
        claimed by :meth:`claim_for_task_delivery` (``TASK_DELIVERED``)
        is invisible here, and a report claimed here (``INJECTED``) is
        invisible to the task. The per-instance serialization guard
        further guarantees this method and a fallback task for the
        SAME instance never run concurrently.

        The companion ``message_queue`` UPDATE is guarded by
        ``status = 'ready'``: at drain time the report's
        ``completion_report`` row is still ``ready`` (the fallback
        task — the only other writer — is blocked by the per-instance
        guard while this parent's turn is live). If the row is not
        ``ready`` for any reason (concurrent writer, manual edit), the
        guarded UPDATE is a no-op for that row and the report is
        still delivered via the injection — the message-status update
        is best-effort hygiene, not a correctness requirement.

        Args:
            parent_instance_id: The parent whose pending reports
                should be drained.

        Returns:
            A list of ``{"content": str, "report_message_id": str}``
            dicts for every row transitioned to ``INJECTED``, in
            insertion order (oldest first). Empty list when no pending
            reports exist for the parent.
        """
        now_iso = self._now_iso()
        with Session(self.engine) as session:
            # Single atomic ``UPDATE ... RETURNING``: transitions every
            # PENDING report for this parent to INJECTED and returns
            # ONLY the rows this call actually claimed. This is symmetric
            # with :meth:`claim_for_task_delivery`'s guarded UPDATE — a
            # row concurrently claimed by the fallback task (already
            # non-PENDING) is invisible to RETURNING, so exactly-once
            # holds without relying on the per-instance serialization
            # guard. Both SQLite (>=3.35) and PostgreSQL support
            # RETURNING.
            stmt = (
                sa_update(ReportInjection)
                .where(ReportInjection.parent_instance_id == parent_instance_id)
                .where(ReportInjection.state == _PENDING_STATE)
                .values(state=_INJECTED_STATE, delivered_at=now_iso)
                .returning(
                    ReportInjection.content,
                    ReportInjection.report_message_id,
                    ReportInjection.created_at,
                )
            )
            claimed = list(session.execute(stmt).all())

            if not claimed:
                return []

            report_message_ids = [r.report_message_id for r in claimed]

            # Mark companion message_queue rows COMPLETED so the
            # parent's own-queue pending count does not include
            # already-delivered reports. Best-effort / guarded by
            # ``status = 'ready'`` — see method docstring.
            session.execute(
                sa_update(MessageQueue)
                .where(MessageQueue.message_id.in_(report_message_ids))
                .where(MessageQueue.status == MessageStatus.READY.value)
                .values(status=_MSG_COMPLETED)
            )

            session.commit()

            # Stable delivery order: oldest first. created_at is an ISO
            # timestamp (TEXT); lexicographic sort is chronological for
            # same-format ISO strings.
            claimed.sort(key=lambda r: r.created_at)
            drained = [
                {"content": r.content, "report_message_id": r.report_message_id}
                for r in claimed
            ]

        logger.info(
            f"[ReportInjection] Drained {len(drained)} pending "
            f"report(s) for parent {parent_instance_id[:8]}... "
            f"(marked INJECTED)"
        )
        return drained

    # --------------------------------------------------------
    # CLAIM — fallback PROCESS_REPORT task
    # --------------------------------------------------------

    def claim_for_task_delivery(
        self, report_message_id: str
    ) -> TaskDeliveryClaim:
        """Atomically claim a single report for fallback task delivery.

        Called by ``ProcessMessageProcessor`` at the start of a
        ``process_report`` task, before the normal message-processing
        pipeline runs. Returns a :class:`TaskDeliveryClaim` tri-state so
        the caller can distinguish three outcomes:

        * ``"claimed"`` — a PENDING row existed and was atomically
          transitioned to ``TASK_DELIVERED``; the task OWNS delivery and
          proceeds normally.
        * ``"already_delivered"`` — a row exists but is terminal
          (``INJECTED`` by the live agent-node drain, or
          ``TASK_DELIVERED`` by a prior run); the task MUST skip
          (dedup gate).
        * ``"missing"`` — no ``report_injections`` row exists for this
          ``report_message_id`` (a ``PROCESS_REPORT`` task from older
          code, or a path that did not enqueue); the task MUST proceed
          with normal delivery so the report is NOT lost.

        The ``"missing"`` distinction is critical: conflating it with
        ``"already_delivered"`` would silently drop reports for any
        task without a companion row. ``report_injections`` rows are
        created in the SAME transaction as the ``PROCESS_REPORT`` task
        (see ``child_reports``), so the live path always has a row;
        ``"missing"`` covers upgrade / legacy / future code paths.

        Exactly-once: the guarded ``WHERE state = 'PENDING'`` UPDATE
        means only one caller (this method or the injection drain) can
        transition a given row out of PENDING.

        Args:
            report_message_id: The ``message_id`` of the companion
                ``completion_report`` row this task is responsible for.

        Returns:
            A :class:`TaskDeliveryClaim` with ``status`` in
            ``{"claimed", "already_delivered", "missing"}`` and ``row``
            set only on ``"claimed"``.
        """
        now_iso = self._now_iso()
        with Session(self.engine) as session:
            # Does ANY row exist for this report (any state)? This
            # distinguishes "missing" (no row ever created) from
            # "already_delivered" (row exists but terminal). Rows are
            # created transactionally with the task, so for the live
            # path a row always exists by the time the task runs; the
            # missing case is upgrade/legacy.
            any_row = session.exec(
                select(ReportInjection).where(
                    ReportInjection.report_message_id == report_message_id
                )
            ).first()
            if any_row is None:
                return TaskDeliveryClaim("missing", None)

            # Guarded transition — only PENDING → TASK_DELIVERED. A row
            # that is already INJECTED/TASK_DELIVERED yields rowcount 0.
            result = session.execute(
                sa_update(ReportInjection)
                .where(ReportInjection.injection_id == any_row.injection_id)
                .where(ReportInjection.state == _PENDING_STATE)
                .values(state=_TASK_DELIVERED_STATE, delivered_at=now_iso)
            )
            if result.rowcount == 0:
                # Row exists but is terminal — the live turn (or a
                # prior task run) already delivered this report.
                return TaskDeliveryClaim("already_delivered", None)

            session.commit()
            session.refresh(any_row)
            logger.info(
                f"[ReportInjection] Report {report_message_id[:8]}... "
                f"claimed for TASK delivery (parent="
                f"{any_row.parent_instance_id[:8]}...)"
            )
            return TaskDeliveryClaim("claimed", any_row)

    # --------------------------------------------------------
    # DIAGNOSTIC
    # --------------------------------------------------------

    def count_pending_for_parent(self, parent_instance_id: str) -> int:
        """Return the number of delivery-owed reports for a parent.

        Phase 1 (pause-report-recovery): the count is broadened from
        ``PENDING`` only to ``PENDING ∪ DEFERRED`` — a DEFERRED marker
        is an outstanding delivery obligation (the recovery sweep
        will turn it back to PENDING). Counting only PENDING would
        understate the delivery backlog and could mislead
        observability / idle-gate decisions.

        Diagnostic helper (observability / tests). Not used on the
        hot path.

        Args:
            parent_instance_id: The parent to query.

        Returns:
            Non-negative count of PENDING ∪ DEFERRED report-injection
            rows for the parent.
        """
        from sqlalchemy import func

        with Session(self.engine) as session:
            stmt = (
                select(func.count())
                .select_from(ReportInjection)
                .where(ReportInjection.parent_instance_id == parent_instance_id)
                .where(
                    ReportInjection.state.in_([
                        _PENDING_STATE,
                        _DEFERRED_STATE,
                    ])
                )
            )
            return int(session.exec(stmt).one() or 0)
