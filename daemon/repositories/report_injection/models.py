"""SQLModel table definition for the report-injection queue.

Single table backing the queued, persistent child→parent report
delivery mechanism (see package docstring).

* :class:`ReportInjection` — one row per child completion report. Rows
  are enqueued by ``child_reports`` in the SAME transaction that
  creates the ``message_queue.completion_report`` row and the
  ``PROCESS_REPORT`` task, so the three are crash-consistent. Rows
  are claimed atomically by exactly one of two delivery paths:

  * the parent's live agent-node drains pending rows and marks them
    ``INJECTED`` (ASAP mid-turn delivery — the deadlock fix);
  * the fallback ``PROCESS_REPORT`` task claims its specific row and
    marks it ``TASK_DELIVERED`` (turn-starter when no turn is live,
    and the crash-recovery path).

  Both claim methods use a guarded ``WHERE state = 'PENDING'`` UPDATE
  (via ``RETURNING``), so a report is delivered exactly once
  regardless of which path races ahead.

The table is created on every backend by
``SQLModel.metadata.create_all()`` at startup (the model is imported
from ``daemon/repositories/__init__.py`` so it is registered with
``SQLModel.metadata`` before ``create_all`` runs). No
``_ensure_postgres_columns`` entry is needed because this is a
brand-new table, not an additive column on an existing table.

DEFERRED marker lifecycle (pause-report-recovery Phase 1)
---------------------------------------------------------

Phase 1 introduces a DB-persisted **delivery-obligation marker** so a
paused/resumed (or otherwise cancelled) child-completion path can
recover its obligation instead of silently dropping the report. The
state machine is::

    [*] --> PENDING : normal — atomic message+task+injection
    [*] --> DEFERRED : pause drop-site marker (1.4/1.5/1.6)
    DEFERRED --> PENDING : recovery claim (guarded UPDATE WHERE state=DEFERRED)
    PENDING --> INJECTED : hot-path drain (claim_for_injection)
    PENDING --> TASK_DELIVERED : fallback (PROCESS_REPORT claim)
    INJECTED --> [*]
    TASK_DELIVERED --> [*]

Forbidden transitions (enforced by the ``state = 'PENDING'`` claim
guards): ``DEFERRED → INJECTED`` and ``DEFERRED → TASK_DELIVERED``.
A DEFERRED row has no artifact yet — it must be recovered to PENDING
first; this is why ``report_message_id`` is nullable (C4). NULL
``report_message_id`` arises ONLY from marker-first writes
(``ensure_deferred``); ``enqueue`` writes always supply it (the
completion_report MessageQueue row is the artifact handle).

The triple ``(parent_instance_id, child_instance_id, child_message_id)``
is the obligation key. The partial unique index ``WHERE state IN
('PENDING','DEFERRED')`` is the **write-once gate**: a duplicate
non-terminal triple raises ``sqlalchemy.exc.IntegrityError`` and the
``ensure_deferred`` caller absorbs it (W6). Terminal states
(INJECTED, TASK_DELIVERED) are out of the index predicate — a fresh
non-terminal obligation for a previously-delivered triple is allowed
(e.g. a re-spawn scenario).

``recovery_attempted_at`` is stamped on ``DEFERRED → PENDING`` so the
Phase 2 sweep can re-process mid-sweep-crash rows without stranding
them.

Storage-layer case contract (C1): enum values and the partial-index
predicate are ``UPPERCASE``. App-layer constants in
``daemon.constants`` mirror the storage literals verbatim. Any new
state/reason value is added to BOTH the storage enum/DDL AND the app
constants in the same change.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, Index, String, Text, text
from sqlmodel import Field, SQLModel


class ReportInjectionState(str, enum.Enum):
    """Lifecycle states for a :class:`ReportInjection` row.

    * ``PENDING`` — initial state on enqueue. The report has not yet
      been delivered to the parent by either path.
    * ``DEFERRED`` — **Phase 1 marker state** (pause-report-recovery):
      a pause drop-site wrote a write-once delivery-obligation marker
      because the natural completion path was blocked (paused parent,
      paused child, sibling-message in flight). The row has no
      ``report_message_id`` artifact yet. Only
      ``transition_deferred_to_pending`` (guarded ``UPDATE ...
      WHERE state='DEFERRED'``) may move it back to ``PENDING``;
      the two delivery claim paths (which guard on
      ``state='PENDING'``) cannot see it. See the module docstring for
      the full state machine.
    * ``INJECTED`` — the parent's live agent-node drained this row and
      injected the content as a ``HumanMessage`` mid-turn. Terminal.
    * ``TASK_DELIVERED`` — the fallback ``PROCESS_REPORT`` task won
      the claim and delivered the report as a fresh parent turn.
      Terminal.

    Stored as a TEXT column with these exact string values (mirrors
    the CHECK-less design used by ``dependency_watchers.state`` — the
    application code is the only writer).
    """

    PENDING = "PENDING"
    DEFERRED = "DEFERRED"
    INJECTED = "INJECTED"
    TASK_DELIVERED = "TASK_DELIVERED"
    # Dead-letter sentinel (T8 (b) / T8 (e)). A recover-from-DEFERRED
    # seam (manager.py ``_create_subshape_a_artifacts`` + sub-shape
    # (b) task-only branch) that finds a dead parent flips the
    # injection row to this state instead of minting a permanently-
    # unclaimable PROCESS_REPORT Task (pause gate, plan §R8). Distinct
    # from INJECTED / TASK_DELIVERED (which would falsely signal
    # delivery). Stored verbatim — the storage layer is
    # case-sensitive (C1). Terminal.
    FAILED = "failed"


class ReportInjection(SQLModel, table=True):
    """One queued child→parent completion report awaiting delivery.

    Enqueued by ``child_reports._process_child_completion_db_sync`` in
    the same ``WriteGuardSession`` transaction that creates the
    ``completion_report`` ``message_queue`` row and the
    ``PROCESS_REPORT`` task. Claimed exactly-once by either the
    agent-node drain (``INJECTED``) or the fallback task
    (``TASK_DELIVERED``).

    Phase 1 (pause-report-recovery) adds a DB-persisted DEFERRED
    marker so a paused/cancelled drop path can recover its obligation
    instead of silently losing the report. See the module docstring
    for the state machine and the case-lockstep contract (C1).

    Attributes:
        injection_id: Primary key. UUID4 by default.
        parent_instance_id: The parent instance that should receive
            the report. Indexed with ``state`` for the agent-node
            drain hot path ("drain all PENDING reports for this
            parent").
        child_instance_id: The child that produced the report
            (diagnostic / traceability).
        child_message_id: The child's completed ``message_id`` that
            triggered the report (diagnostic / traceability; mirrors
            the ``internal_report:{child}:{msg}`` source on the
            ``message_queue`` row). Part of the obligation-triple that
            the partial unique index gates.
        report_message_id: The ``message_id`` of the
            ``completion_report`` row in ``message_queue``. Indexed
            with ``state`` for the fallback task's per-report claim.
            Also used by the agent-node drain to mark the companion
            ``message_queue`` row ``COMPLETED`` so it is not counted
            as pending own-queue work for the parent.

            Phase 1: nullable. NULL arises ONLY from marker-first
            writes (``ensure_deferred``); the Phase 2 reconciliation
            in task 2.2 explicitly handles ``report_message_id IS NULL``
            as the pre-artifact Site-1 shape (the marker is upgraded
            to a full PENDING+artifact row before claim).
        content: The report text (the child's last assistant
            message, already prefixed with the agent/report header by
            ``child_reports._get_last_assistant_message``). Stored
            verbatim so the injection is self-contained — the
            agent-node does not need to re-fetch the
            ``message_queue`` row. NULL for marker-first writes
            (DEFERRED rows have no artifact yet).
        created_at: ISO-8601 timestamp, immutable.
        delivered_at: ISO-8601 timestamp set on the terminal
            transition (``INJECTED`` or ``TASK_DELIVERED``). ``None``
            while ``PENDING`` or ``DEFERRED``.
        state: One of :class:`ReportInjectionState`. Default
            ``PENDING``. Transitioned atomically by
            :meth:`ReportInjectionRepository.claim_for_injection` /
            :meth:`ReportInjectionRepository.claim_for_task_delivery`
            (guarded UPDATE — only transitions PENDING rows, so the
            two delivery paths cannot double-deliver).
        deferred_reason: Phase 1 (pause-report-recovery). Free-text
            rationale for a DEFERRED marker, one of the
            ``DEFERRED_REASON_*`` constants (``PAUSE_TOCTOU``,
            ``PENDING_MESSAGES``, ``IDEMPOTENCY_SKIP``,
            ``RESUME_ROUTER``). ``None`` for non-DEFERRED rows. TEXT
            (not VARCHAR) — open-ended vocabulary, future reasons
            without a schema change.
        recovery_attempted_at: Phase 1. ISO-8601 timestamp set by
            :meth:`ReportInjectionRepository.transition_deferred_to_pending`
            on ``DEFERRED → PENDING``. Partial index over PENDING
            rows; the Phase 2 sweep re-processes
            ``state='PENDING' AND recovery_attempted_at IS NOT NULL``
            rows to close the mid-sweep-crash gap (FM-13).
    """

    __tablename__ = "report_injections"
    __table_args__ = (
        # Hot path: agent-node drain — "give me all PENDING reports
        # for this parent". The state suffix keeps the index selective
        # as delivered rows accumulate.
        Index(
            "ix_report_injections_parent_state",
            "parent_instance_id",
            "state",
        ),
        # Fallback task claim — "claim this specific report for task
        # delivery". Single-row lookup by report_message_id, state
        # suffix makes the guarded UPDATE cheap.
        Index(
            "ix_report_injections_report_msg_state",
            "report_message_id",
            "state",
        ),
        # Phase 1: write-once gate on the obligation triple.
        # ``state IN ('PENDING','DEFERRED')`` keeps the index scoped
        # to non-terminal rows — terminal duplicates (INJECTED /
        # TASK_DELIVERED) are out of scope and the index lets a
        # re-spawn scenario mint a fresh non-terminal obligation for
        # the same triple (different lifecycle).
        #
        # The literal case ('PENDING','DEFERRED') is the C1
        # case-lockstep contract — MUST match the storage enum at
        # ReportInjectionState and the partial unique index DDL in
        # ``_ensure_postgres_columns`` + the SQLite companion
        # migration. Same index name in both DDL paths (precedent:
        # ``idx_job_idempotency`` at job_queue/models.py:292-298).
        Index(
            "uq_report_injections_oblig_triple",
            "parent_instance_id",
            "child_instance_id",
            "child_message_id",
            unique=True,
            sqlite_where=text("state IN ('PENDING','DEFERRED')"),
            postgresql_where=text("state IN ('PENDING','DEFERRED')"),
        ),
        # Phase 1: partial index for the recovery-attempt predicate
        # (Phase 2 sweep reads ``state='PENDING' AND
        # recovery_attempted_at IS NOT NULL``). Keeps the recovery
        # sweep cheap as the table grows.
        Index(
            "ix_report_injections_recovery_attempted",
            "recovery_attempted_at",
            sqlite_where=text("state = 'PENDING'"),
            postgresql_where=text("state = 'PENDING'"),
        ),
        # Phase 2 (C3 no-row backstop): non-unique
        # ``(child_instance_id, child_message_id)`` index. The
        # LEFT JOIN on ``ReportInjection`` in
        # :meth:`ReportInjectionRepository.find_completed_children_without_delivery`
        # keys on the child columns WITHOUT ``parent_instance_id``
        # in the leading position — the unique triple index
        # ``uq_report_injections_oblig_triple`` is
        # ``(parent_instance_id, child_instance_id, child_message_id)``
        # so the leading parent column would force a full scan over
        # the non-terminal prefix. A non-unique index on the child
        # pair keeps the LEFT JOIN cheap as the table grows.
        #
        # The literal case (no ``state`` suffix) covers ALL rows
        # regardless of state — the LEFT JOIN predicate includes the
        # state filter, but a state-suffixed index would force a
        # scan over the non-terminal prefix anyway. The bare index
        # gives PG/SQLite the freedom to choose the cheaper plan.
        Index(
            "ix_report_injections_child_msg",
            "child_instance_id",
            "child_message_id",
        ),
    )

    injection_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        primary_key=True,
        max_length=64,
    )
    parent_instance_id: str = Field(
        sa_column=Column(String, nullable=False),
        max_length=64,
    )
    child_instance_id: str = Field(
        sa_column=Column(String, nullable=False),
        max_length=64,
    )
    child_message_id: str = Field(
        sa_column=Column(String, nullable=False),
        max_length=64,
    )
    # Phase 1 (C4): nullable. NULL = pre-artifact Site-1 marker
    # shape (``ensure_deferred`` writes). The Phase 2 reconciliation
    # (task 2.2) handles ``report_message_id IS NULL`` explicitly.
    # Existing claim sites only look up by ``report_message_id`` on
    # rows where it is set (terminal / enqueue paths).
    report_message_id: str | None = Field(
        sa_column=Column(String, nullable=True),
        default=None,
        max_length=64,
    )
    # ``Text`` for content — reports can be long (full agent
    # summaries) and are never indexed. Nullable for DEFERRED
    # markers (no artifact yet — Phase 2 fills it in).
    content: str | None = Field(
        sa_column=Column(Text, nullable=True), default=None
    )

    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    delivered_at: str | None = Field(default=None)
    state: str = Field(default=ReportInjectionState.PENDING.value, max_length=16)
    # Phase 1: DEFERRED rationale. TEXT (not VARCHAR) — open-ended
    # vocabulary. Mirrors the partial-index predicate's case contract:
    # values are UPPERCASE and match the ``DEFERRED_REASON_*``
    # constants in ``daemon.constants`` verbatim.
    deferred_reason: str | None = Field(
        sa_column=Column(Text, nullable=True), default=None
    )
    # Phase 1: ISO-8601 stamp on ``DEFERRED → PENDING`` recovery
    # transition. Partial-indexed for the Phase 2 recovery sweep.
    recovery_attempted_at: str | None = Field(default=None)
