"""Unit tests for Variant B DEFERRED marker guards (pause-report-recovery
Phase 1, Tasks 1.5 + 1.6).

Two Variant B drop sites in ``child_reports`` now persist DEFERRED
markers on ``report_injections`` so the parent's delivery obligation
survives pause/cancel:

* **Variant B fix 1** — live pending-messages site
  (child_reports.py:2106): when ``pending_count > 0`` at the live
  inlined check, write a DEFERRED marker with reason
  ``DEFERRED_REASON_PENDING_MESSAGES``. ROOT GUARD (W5): skip the
  marker for root instances (parent_id is None). N1 (cycle-2 patch):
  ``inst_check`` is loaded BEFORE the if/else split so both branches
  share a single load.

* **Variant B fix 2** — idempotency guard
  (child_reports.py:1626): split the guard. ``status in (COMPLETED,
  ERROR)`` → unchanged ``idempotency_skip``, no marker. ``status ==
  PAUSED`` → new outcome ``deferred_pause`` (an
  ``_ChildCompletionDbResult`` label, NOT a ``terminal_reason``) +
  ``ensure_deferred`` with reason ``DEFERRED_REASON_IDEMPOTENCY_SKIP``.

* **C5 SCOPE NOTE** — the Variant B fix 2 guard fires ONLY when the
  CHILD instance is PAUSED. The canonical Site-1 shape (child
  COMPLETED, parent PAUSED) NEVER reaches this branch — that is 1.4's
  pipeline lane. The 3.2(d) test pins the scope separation: a
  child-COMPLETED instance whose parent is PAUSED must take the
  Variant B fix 1 path (pending_messages_exist) or the natural
  regular_child_completed path, NOT the deferred_pause path.

These tests run against a real in-memory SQLite database so the
write-side and read-side semantics are observable.
"""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, select as sm_select

# Register every table the helper touches before create_all().
import daemon.repositories.dependency_bus.models  # noqa: F401
import daemon.repositories.event.models  # noqa: F401
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.report_injection.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401

from daemon.constants import (
    DEFERRED_REASON_IDEMPOTENCY_SKIP,
    DEFERRED_REASON_PENDING_MESSAGES,
)
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.message_queue.models import (
    MessageQueue,
    MessageStatus,
    MessageType,
)
from daemon.repositories.report_injection.models import (
    ReportInjection,
    ReportInjectionState,
)
from daemon.services.child_reports import ChildReportsService
from daemon.services.dependency_bus import set_dependency_bus
from daemon.write_pause_guard import WritePauseGuard


# =============================================================================
# Fixtures + helpers
# =============================================================================


@pytest.fixture
def engine() -> Engine:
    """Real in-memory SQLite engine with all tables created."""
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(eng, "connect")
    def _enable_fk(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture(autouse=True)
def dependency_bus() -> MagicMock:
    """Install a zero-pending-child bus for the root completion path."""
    bus = MagicMock(name="DependencyBus")
    bus.count_pending_for_target_sync.return_value = 0
    set_dependency_bus(bus)
    try:
        yield bus
    finally:
        set_dependency_bus(None)


def _build_child_reports_service(
    engine: Engine,
) -> ChildReportsService:
    """Build the real completion service with only its DB deps."""
    manager = MagicMock(name="InstanceManager")
    manager.engine = engine
    manager.write_guard = WritePauseGuard()

    service = ChildReportsService.__new__(ChildReportsService)
    service._manager = manager
    service._events_service = None
    return service


def _seed_instance(
    engine: Engine,
    *,
    instance_id: str | None = None,
    parent_id: str | None = "parent-1",
    status: str = InstanceStatus.RUNNING.value,
) -> str:
    """Insert an instance row."""
    instance_id = instance_id or f"child-{uuid.uuid4().hex[:8]}"
    with Session(engine) as session:
        session.add(
            Instance(
                instance_id=instance_id,
                agent_id="child-agent",
                agent_name="child",
                agent_dir="/tmp/child",
                parent_id=parent_id,
                status=status,
                version=1,
                instance_metadata={},
            )
        )
        session.commit()
    return instance_id


def _seed_pending_message(
    engine: Engine,
    *,
    instance_id: str,
    message_id: str | None = None,
) -> str:
    """Insert a PROCESSING message (the live pending-messages gate
    trigger). Returns the message_id."""
    message_id = message_id or f"msg-{uuid.uuid4().hex[:8]}"
    with Session(engine) as session:
        session.add(
            MessageQueue(
                message_id=message_id,
                instance_id=instance_id,
                content="",
                source="http",
                type=MessageType.HUMAN.value,
                status=MessageStatus.PROCESSING.value,
            )
        )
        session.commit()
    return message_id


def _deferred_rows_for(engine: Engine, child_instance_id: str) -> list[ReportInjection]:
    """Return the DEFERRED rows for a child instance, ordered by
    created_at ascending."""
    with Session(engine) as session:
        return list(
            session.exec(
                sm_select(ReportInjection)
                .where(ReportInjection.child_instance_id == child_instance_id)
                .where(ReportInjection.state == ReportInjectionState.DEFERRED.value)
                .order_by(ReportInjection.created_at.asc())
            ).all()
        )


# =============================================================================
# Variant B fix 1 — live pending-messages site
# =============================================================================


class TestVariantBFix1LivePendingMessages:
    """Task 1.5: when ``pending_count > 0`` at the live inlined
    idempotency check, write a DEFERRED marker on the obligation
    triple. ROOT GUARD (W5): skip the marker for root instances."""

    def test_pending_messages_writes_deferred_marker(
        self, engine: Engine
    ) -> None:
        """A child instance with a PROCESSING message in its own
        queue at completion time must persist a DEFERRED marker so
        the parent's eventual delivery obligation survives."""
        service = _build_child_reports_service(engine)
        instance_id = _seed_instance(
            engine, parent_id="parent-1"
        )
        # A pending PROCESSING message (the trigger).
        pending_msg = _seed_pending_message(
            engine, instance_id=instance_id
        )

        result = service._process_child_completion_db_sync(
            instance_id=instance_id,
            completed_message_id="completed-1",
            last_content="done",
        )

        # Outcome is the existing skip — the marker is the
        # persistence side effect, not the outcome label.
        assert result.outcome == "idempotency_skip"

        # The DEFERRED marker landed.
        rows = _deferred_rows_for(engine, instance_id)
        assert len(rows) == 1
        row = rows[0]
        assert row.state == ReportInjectionState.DEFERRED.value
        assert (
            row.deferred_reason
            == DEFERRED_REASON_PENDING_MESSAGES
        )
        assert row.parent_instance_id == "parent-1"
        assert row.child_message_id == "completed-1"
        assert row.report_message_id is None
        assert row.content is None

    def test_root_instance_no_marker_no_crash(
        self, engine: Engine
    ) -> None:
        """W5: a root instance (parent_id is None) must not crash
        the marker writer — and must NOT write a marker (root
        instances have no parent and therefore no delivery
        obligation). The root pending_count>0 path returns
        ``root_waiting_children`` (the existing root carve-out), and
        the marker writer must be a no-op for that branch."""
        service = _build_child_reports_service(engine)
        instance_id = _seed_instance(engine, parent_id=None)
        _seed_pending_message(engine, instance_id=instance_id)

        # Must not crash.
        result = service._process_child_completion_db_sync(
            instance_id=instance_id,
            completed_message_id="completed-1",
            last_content="done",
        )
        # Root pending_count>0 path returns root_waiting_children.
        assert result.outcome == "root_waiting_children"

        # No marker written (root has no parent).
        assert _deferred_rows_for(engine, instance_id) == []

    def test_pending_messages_marker_absorbs_duplicate(
        self, engine: Engine
    ) -> None:
        """W6: a concurrent duplicate ``ensure_deferred`` for the
        same triple must be absorbed — single row, no crash."""
        service = _build_child_reports_service(engine)
        instance_id = _seed_instance(
            engine, parent_id="parent-1"
        )
        _seed_pending_message(engine, instance_id=instance_id)

        # Two consecutive calls (router/sweep/Site 1 race).
        first = service._process_child_completion_db_sync(
            instance_id=instance_id,
            completed_message_id="completed-1",
            last_content="done",
        )
        second = service._process_child_completion_db_sync(
            instance_id=instance_id,
            completed_message_id="completed-1",
            last_content="done",
        )
        # Both calls succeed (idempotency_skip on both).
        assert first.outcome == "idempotency_skip"
        assert second.outcome == "idempotency_skip"

        # Exactly one marker.
        rows = _deferred_rows_for(engine, instance_id)
        assert len(rows) == 1


# =============================================================================
# Variant B fix 2 — idempotency guard at 1626
# =============================================================================


class TestVariantBFix2IdempotencyGuard:
    """Task 1.6: split the idempotency guard. COMPLETED/ERROR →
    ``idempotency_skip`` (no marker). PAUSED → ``deferred_pause`` +
    marker with reason ``DEFERRED_REASON_IDEMPOTENCY_SKIP``."""

    def test_completed_status_returns_idempotency_skip_no_marker(
        self, engine: Engine
    ) -> None:
        """status=COMPLETED → unchanged idempotency_skip, no marker."""
        service = _build_child_reports_service(engine)
        instance_id = _seed_instance(
            engine, parent_id="parent-1",
            status=InstanceStatus.COMPLETED.value,
        )

        result = service._process_child_completion_db_sync(
            instance_id=instance_id,
            completed_message_id="completed-1",
            last_content="done",
        )

        assert result.outcome == "idempotency_skip"
        # No DEFERRED marker for COMPLETED status (delivery has
        # happened; no obligation to recover).
        assert _deferred_rows_for(engine, instance_id) == []

    def test_error_status_returns_idempotency_skip_no_marker(
        self, engine: Engine
    ) -> None:
        """status=ERROR → unchanged idempotency_skip, no marker."""
        service = _build_child_reports_service(engine)
        instance_id = _seed_instance(
            engine, parent_id="parent-1",
            status=InstanceStatus.ERROR.value,
        )

        result = service._process_child_completion_db_sync(
            instance_id=instance_id,
            completed_message_id="completed-1",
            last_content="done",
        )

        assert result.outcome == "idempotency_skip"
        assert _deferred_rows_for(engine, instance_id) == []

    def test_paused_status_returns_deferred_pause_with_marker(
        self, engine: Engine
    ) -> None:
        """status=PAUSED → new outcome ``deferred_pause`` + marker
        with reason ``DEFERRED_REASON_IDEMPOTENCY_SKIP``."""
        service = _build_child_reports_service(engine)
        instance_id = _seed_instance(
            engine, parent_id="parent-1",
            status=InstanceStatus.PAUSED.value,
        )

        result = service._process_child_completion_db_sync(
            instance_id=instance_id,
            completed_message_id="completed-1",
            last_content="done",
        )

        # New outcome (Phase 1 — NOT a terminal_reason).
        assert result.outcome == "deferred_pause"

        rows = _deferred_rows_for(engine, instance_id)
        assert len(rows) == 1
        row = rows[0]
        assert row.state == ReportInjectionState.DEFERRED.value
        assert (
            row.deferred_reason
            == DEFERRED_REASON_IDEMPOTENCY_SKIP
        )
        assert row.parent_instance_id == "parent-1"
        assert row.child_message_id == "completed-1"

    def test_paused_root_no_marker_no_crash(
        self, engine: Engine
    ) -> None:
        """status=PAUSED + parent_id is None → deferred_pause outcome
        but NO marker (root has no parent and therefore no delivery
        obligation)."""
        service = _build_child_reports_service(engine)
        instance_id = _seed_instance(
            engine, parent_id=None,
            status=InstanceStatus.PAUSED.value,
        )

        # Must not crash.
        result = service._process_child_completion_db_sync(
            instance_id=instance_id,
            completed_message_id="completed-1",
            last_content="done",
        )
        assert result.outcome == "deferred_pause"
        # No marker written (root has no parent).
        assert _deferred_rows_for(engine, instance_id) == []


# =============================================================================
# C5 scope separation: child-COMPLETED/parent-PAUSED → Site 1 path
# =============================================================================


class TestC5ScopeSeparation:
    """3.2(d) test: the canonical Site-1 shape (child COMPLETED, parent
    PAUSED) NEVER reaches the Variant B fix 2 guard. The 1.4 pipeline
    lane is the path that handles parent-pause; the Variant B fix 1
    live site handles child-side skip; the Variant B fix 2 guard is
    CHILD-side PAUSED only.

    Scope verification: a CHILD instance whose status is COMPLETED but
    whose parent's status is PAUSED must NOT take the deferred_pause
    branch (it must take the idempotency_skip branch — delivery
    already happened or is terminal). The parent-pause recovery is a
    Phase 2 concern (router / sweep), not this guard.
    """

    def test_child_completed_parent_paused_skips_no_marker(
        self, engine: Engine
    ) -> None:
        """child status=COMPLETED, parent status=PAUSED → the child
        idempotency guard fires (COMPLETED) → idempotency_skip, no
        marker. The parent-pause recovery is out of scope here."""
        service = _build_child_reports_service(engine)
        # Seed parent in PAUSED status.
        with Session(engine) as session:
            session.add(
                Instance(
                    instance_id="parent-1",
                    agent_id="parent-agent",
                    agent_name="parent",
                    agent_dir="/tmp/parent",
                    parent_id=None,
                    status=InstanceStatus.PAUSED.value,
                    version=1,
                    instance_metadata={},
                )
            )
            session.commit()
        # Seed child in COMPLETED status with parent=parent-1.
        instance_id = _seed_instance(
            engine, parent_id="parent-1",
            status=InstanceStatus.COMPLETED.value,
        )

        result = service._process_child_completion_db_sync(
            instance_id=instance_id,
            completed_message_id="completed-1",
            last_content="done",
        )

        # The COMPLETED branch fires (not the PAUSED branch).
        assert result.outcome == "idempotency_skip"
        # No DEFERRED marker — the COMPLETED branch does not write
        # one. The parent-pause recovery is a Phase 2 concern.
        assert _deferred_rows_for(engine, instance_id) == []
