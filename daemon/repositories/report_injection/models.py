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
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, Index, String, Text
from sqlmodel import Field, SQLModel


class ReportInjectionState(str, enum.Enum):
    """Lifecycle states for a :class:`ReportInjection` row.

    * ``PENDING`` — initial state on enqueue. The report has not yet
      been delivered to the parent by either path.
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
    INJECTED = "INJECTED"
    TASK_DELIVERED = "TASK_DELIVERED"


class ReportInjection(SQLModel, table=True):
    """One queued child→parent completion report awaiting delivery.

    Enqueued by ``child_reports._process_child_completion_db_sync`` in
    the same ``WriteGuardSession`` transaction that creates the
    ``completion_report`` ``message_queue`` row and the
    ``PROCESS_REPORT`` task. Claimed exactly-once by either the
    agent-node drain (``INJECTED``) or the fallback task
    (``TASK_DELIVERED``).

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
            ``message_queue`` row).
        report_message_id: The ``message_id`` of the
            ``completion_report`` row in ``message_queue``. Indexed
            with ``state`` for the fallback task's per-report claim.
            Also used by the agent-node drain to mark the companion
            ``message_queue`` row ``COMPLETED`` so it is not counted
            as pending own-queue work for the parent.
        content: The report text (the child's last assistant
            message, already prefixed with the agent/report header by
            ``child_reports._get_last_assistant_message``). Stored
            verbatim so the injection is self-contained — the
            agent-node does not need to re-fetch the
            ``message_queue`` row.
        created_at: ISO-8601 timestamp, immutable.
        delivered_at: ISO-8601 timestamp set on the terminal
            transition (``INJECTED`` or ``TASK_DELIVERED``). ``None``
            while ``PENDING``.
        state: One of :class:`ReportInjectionState`. Default
            ``PENDING``. Transitioned atomically by
            :meth:`ReportInjectionRepository.claim_for_injection` /
            :meth:`ReportInjectionRepository.claim_for_task_delivery`
            (guarded UPDATE — only transitions PENDING rows, so the
            two delivery paths cannot double-deliver).
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
    report_message_id: str = Field(
        sa_column=Column(String, nullable=False),
        max_length=64,
    )
    # ``Text`` for content — reports can be long (full agent
    # summaries) and are never indexed.
    content: str = Field(sa_column=Column(Text, nullable=False))

    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    delivered_at: str | None = Field(default=None)
    state: str = Field(default=ReportInjectionState.PENDING.value, max_length=16)
