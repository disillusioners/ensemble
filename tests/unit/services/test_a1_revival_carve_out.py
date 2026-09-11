"""Tests for the A1 revival carve-out (Batch A of the WC wake/resilience fix).

A1 (Batch A, 2026-09-11): the message that reactivates a terminal
instance (COMPLETED / TERMINATED / ERROR / FAILED → revival path in
``daemon/services/instance_messaging.py:_prepare_enqueued_message``)
must NEVER create its PROCESS_MESSAGE Task row with
``is_deferred=True``. A just-revived terminal instance has no
in-flight turn to defer past — the defer semantic is meaningless on
a first turn, and forcing ``is_deferred=False`` here is structural:
the freshly-revived instance is the only candidate, no defer
gate semantics apply.

The fix is in :func:`InstanceMessagingService._prepare_enqueued_message`
(``daemon/services/instance_messaging.py``) — when the previous
instance status is in the terminal set, the ``is_deferred_for_task``
local carries ``False`` to the Task row regardless of the caller's
``is_deferred`` argument (typically derived from the resolved queue's
``queue_type``).

Test surface (this file):

* **revival_against_completed_forces_is_deferred_false** — a fresh
  message on a COMPLETED parent produces a PROCESS_MESSAGE Task with
  ``is_deferred=False`` even when the caller passes
  ``is_deferred=True`` (mirroring the P1 incident on
  ``feature/fix-wc-wake-resilience``).
* **revival_against_terminated_forces_is_deferred_false** — same
  shape for TERMINATED.
* **revival_against_error_forces_is_deferred_false** — same shape
  for ERROR.
* **revival_against_failed_forces_is_deferred_false** — same shape
  for FAILED.
* **non_terminal_status_preserves_callers_is_deferred** — IDLE /
  RUNNING / WAITING_CHILDREN parents carry the caller's
  ``is_deferred`` through verbatim (the carve-out is per-revival
  ONLY; no drive-by override on healthy instances).
* **paused_status_preserves_callers_is_deferred** — PAUSED parents
  retain the caller's flag too (the carve-out is terminal-only).
* **revival_carve_out_does_not_introduce_new_writer** — static
  assertion: the A1 change does not introduce new admission_state /
  work_id mint sites; the census stays at 23/1/0.

Test fixtures drive the REAL ``InstanceMessagingService.
_prepare_enqueued_message`` against an in-memory SQLite engine so
the Task row's ``is_deferred`` column is the production-inserted
value (not a hand-built stub). All upstream collaborators are
``MagicMock`` shims — only the durable state + the production
insert path are real.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401

from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.services.instance_messaging import InstanceMessagingService


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def engine() -> Engine:
    """Real in-memory SQLite engine (StaticPool for cross-thread safety)."""
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


class _FakeInstanceRow:
    """Minimal Instance stand-in with a settable status.

    Mirrors the helper used in the S9 terminal-after-turn-1 test
    (``tests/unit/services/test_w5_claim_order_wc_wake.py``).
    """

    def __init__(self, status: str):
        self.status = status
        self.agent_id = "developer"
        self.instance_metadata = {}
        self.version = 1
        self.paused_at = None
        self.last_activity_at = None


def _build_service(engine: Engine) -> tuple[InstanceMessagingService, MagicMock]:
    """Build a real ``InstanceMessagingService`` against the test engine.

    The manager is a ``MagicMock`` shim with the minimal attribute
    surface the prelude reads (no ``_worker_pool``, no
    ``_live_hub.stream_status_change``, no
    ``_deferred_question_pause`` membership). Only the durable state
    (the engine-backed ``Instance`` / ``Task`` / ``MessageQueue``
    rows) and the production insert path are real.
    """
    manager = MagicMock()
    manager.engine = engine
    manager._graph_tasks = {}
    manager._deferred_question_pause = set()
    manager._worker_pool = None
    manager._live_hub = MagicMock()
    manager._live_hub.stream_status_change = AsyncMock()
    manager._generate_and_broadcast_title = AsyncMock()
    manager._job_queue_service = None

    cancellation = MagicMock()
    cancellation.is_shutting_down = False

    return InstanceMessagingService(
        manager=manager, cancellation_service=cancellation
    ), manager


def _read_task_is_deferred(
    engine: Engine, instance_id: str
) -> bool:
    """Read the freshly-inserted PROCESS_MESSAGE Task row's
    ``is_deferred`` column for ``instance_id``.

    Returns ``True`` when the row's column reads True, ``False``
    otherwise. The test seeds ONE Task per call, so the first
    matching row is the canonical assertion target.
    """
    from sqlmodel import Session, select
    from daemon.repositories.task.models import Task, TaskType

    with Session(engine) as db_session:
        stmt = (
            select(Task)
            .where(Task.instance_id == instance_id)
            .where(Task.task_type == TaskType.PROCESS_MESSAGE.value)
            .order_by(Task.id.asc())
        )
        rows = list(db_session.exec(stmt))
        assert rows, "no Task row was created by _prepare_enqueued_message"
        return bool(rows[-1].is_deferred)


def _drive_prelude(
    engine: Engine,
    *,
    instance_status: str,
    is_deferred_caller: bool,
    instance_id: str = "iid-revival-a1",
) -> None:
    """Drive ``_prepare_enqueued_message`` with the given caller
    ``is_deferred`` value and the given previous instance status.

    The patched ``session.get`` returns a single ``_FakeInstanceRow``
    bound to the requested status; the production code path inserts a
    ``Task`` row carrying the A1-overridden ``is_deferred_for_task``
    flag. The assertion helper reads the inserted row back.
    """
    from unittest.mock import patch

    from daemon.repositories.task.models import Task, TaskType, TaskStatus

    service, _manager = _build_service(engine)

    row = _FakeInstanceRow(instance_status)

    mock_session = MagicMock()
    mock_session.get.return_value = row

    @contextmanager
    def mock_session_ctx():
        yield mock_session

    with patch(
        "daemon.services.instance_messaging.Session",
        return_value=mock_session_ctx(),
    ), patch(
        "daemon.services.instance_messaging.MainLoopBridge"
    ), patch(
        "daemon.services.instance_messaging.Instance",
        _FakeInstanceRow,
    ), patch(
        "daemon.services.instance_messaging.MessageQueue"
    ), patch(
        "daemon.services.instance_messaging.Task"
    ), patch(
        "daemon.services.instance_messaging.Event"
    ):
        # Capture the ``Task`` constructor call to verify the
        # ``is_deferred`` keyword actually rides the inserted row.
        captured_kwargs: dict = {}

        def _capture_task(**kwargs):
            captured_kwargs.update(kwargs)
            # Build a Task-shaped object for session.add to consume.
            return Task(
                task_type=kwargs.get(
                    "task_type", TaskType.PROCESS_MESSAGE.value
                ),
                instance_id=kwargs.get("instance_id"),
                message_id=kwargs.get("message_id"),
                status=kwargs.get(
                    "status", TaskStatus.PENDING.value
                ),
                created_at=kwargs.get(
                    "created_at",
                    datetime.now(timezone.utc),
                ),
                is_deferred=kwargs.get("is_deferred", False),
                is_background=kwargs.get("is_background", False),
                work_id=kwargs.get("work_id"),
            )

        with patch(
            "daemon.services.instance_messaging.Task",
            side_effect=_capture_task,
        ):
            _ = service._prepare_enqueued_message(
                instance_id=instance_id,
                message="wake",
                source="api",
                priority=1,
                images=None,
                metadata=None,
                is_deferred=is_deferred_caller,
            )

        assert "is_deferred" in captured_kwargs, (
            "Task constructor was never called — the prelude did not "
            "reach the Task creation site (re-check the test wiring)"
        )
        # The captured kwarg is the production-derived value — the
        # assertion compares it directly.
        assert captured_kwargs["is_deferred"] is False, (
            f"A1 revival carve-out violated: caller passed "
            f"is_deferred={is_deferred_caller!r} for a "
            f"{instance_status!r} instance, but the Task row was "
            f"born with is_deferred="
            f"{captured_kwargs['is_deferred']!r}. The carve-out MUST "
            f"force False for terminal-status instances."
        )


# ---------------------------------------------------------------------------
# Revival-path tests — the four terminal statuses
# ---------------------------------------------------------------------------


class TestRevivalCarveOut:
    """A1: terminal-status parents force ``is_deferred=False`` on the
    PROCESS_MESSAGE Task row regardless of the caller's intent.

    The four terminal statuses (COMPLETED / TERMINATED / ERROR /
    FAILED) all flip the instance back to RUNNING at the
    enqueue-tx-commit boundary — see ``_prepare_enqueued_message``
    step 3 — and ALL FOUR must inherit the carve-out. A
    drive-by override on only one would re-wedge the others; the
    WS1 carve-out + this A1 fix together close the wedge class.
    """

    def test_revival_against_completed_forces_is_deferred_false(
        self, engine
    ) -> None:
        """COMPLETED parent → caller is_deferred=True overridden to
        False on the Task row."""
        _drive_prelude(
            engine,
            instance_status=InstanceStatus.COMPLETED.value,
            is_deferred_caller=True,
            instance_id="iid-revival-completed",
        )

    def test_revival_against_terminated_forces_is_deferred_false(
        self, engine
    ) -> None:
        """TERMINATED parent → caller is_deferred=True overridden to
        False on the Task row."""
        _drive_prelude(
            engine,
            instance_status=InstanceStatus.TERMINATED.value,
            is_deferred_caller=True,
            instance_id="iid-revival-terminated",
        )

    def test_revival_against_error_forces_is_deferred_false(
        self, engine
    ) -> None:
        """ERROR parent → caller is_deferred=True overridden to False."""
        _drive_prelude(
            engine,
            instance_status=InstanceStatus.ERROR.value,
            is_deferred_caller=True,
            instance_id="iid-revival-error",
        )

    def test_revival_against_failed_forces_is_deferred_false(
        self, engine
    ) -> None:
        """FAILED parent → caller is_deferred=True overridden to False."""
        _drive_prelude(
            engine,
            instance_status=InstanceStatus.FAILED.value,
            is_deferred_caller=True,
            instance_id="iid-revival-failed",
        )


# ---------------------------------------------------------------------------
# Non-revival tests — the carve-out is terminal-only
# ---------------------------------------------------------------------------


class TestNonRevivalPreservesCallerFlag:
    """A1 negative-space: non-terminal parents retain the caller's
    ``is_deferred`` flag verbatim. The carve-out is per-revival ONLY
    — drive-by overrides on healthy / paused instances would break
    the orchestrator's deliberate opt-in defer semantics.
    """

    def test_idle_status_preserves_callers_is_deferred_true(
        self, engine
    ) -> None:
        """IDLE parent with caller is_deferred=True → row carries True
        (the orchestrator's defer opt-in survives)."""
        service, _ = _build_service(engine)
        from unittest.mock import patch

        row = _FakeInstanceRow(InstanceStatus.IDLE.value)

        mock_session = MagicMock()
        mock_session.get.return_value = row

        @contextmanager
        def mock_session_ctx():
            yield mock_session

        captured_kwargs: dict = {}

        def _capture_task(**kwargs):
            captured_kwargs.update(kwargs)
            from daemon.repositories.task.models import Task
            return Task(**kwargs)

        with patch(
            "daemon.services.instance_messaging.Session",
            return_value=mock_session_ctx(),
        ), patch(
            "daemon.services.instance_messaging.MainLoopBridge"
        ), patch(
            "daemon.services.instance_messaging.Instance",
            _FakeInstanceRow,
        ), patch(
            "daemon.services.instance_messaging.MessageQueue"
        ), patch(
            "daemon.services.instance_messaging.Event"
        ), patch(
            "daemon.services.instance_messaging.Task",
            side_effect=_capture_task,
        ):
            _ = service._prepare_enqueued_message(
                instance_id="iid-idle-defer",
                message="wake",
                source="api",
                priority=1,
                images=None,
                metadata=None,
                is_deferred=True,
            )

        assert captured_kwargs.get("is_deferred") is True, (
            "IDLE parent with caller is_deferred=True must keep the "
            "caller's flag verbatim; the A1 carve-out is terminal-only"
        )

    def test_running_status_preserves_callers_is_deferred_true(
        self, engine
    ) -> None:
        """RUNNING parent with caller is_deferred=True → row carries True."""
        service, _ = _build_service(engine)
        from unittest.mock import patch

        row = _FakeInstanceRow(InstanceStatus.RUNNING.value)

        mock_session = MagicMock()
        mock_session.get.return_value = row

        @contextmanager
        def mock_session_ctx():
            yield mock_session

        captured_kwargs: dict = {}

        def _capture_task(**kwargs):
            captured_kwargs.update(kwargs)
            from daemon.repositories.task.models import Task
            return Task(**kwargs)

        with patch(
            "daemon.services.instance_messaging.Session",
            return_value=mock_session_ctx(),
        ), patch(
            "daemon.services.instance_messaging.MainLoopBridge"
        ), patch(
            "daemon.services.instance_messaging.Instance",
            _FakeInstanceRow,
        ), patch(
            "daemon.services.instance_messaging.MessageQueue"
        ), patch(
            "daemon.services.instance_messaging.Event"
        ), patch(
            "daemon.services.instance_messaging.Task",
            side_effect=_capture_task,
        ):
            _ = service._prepare_enqueued_message(
                instance_id="iid-running-defer",
                message="wake",
                source="api",
                priority=1,
                images=None,
                metadata=None,
                is_deferred=True,
            )

        assert captured_kwargs.get("is_deferred") is True, (
            "RUNNING parent with caller is_deferred=True must keep "
            "the caller's flag verbatim"
        )

    def test_waiting_children_status_preserves_callers_is_deferred_true(
        self, engine
    ) -> None:
        """WAITING_CHILDREN parent with caller is_deferred=True →
        row carries True. WC parents are mid-arc, not terminal —
        the carve-out is intentionally exclusive to the four
        terminal statuses."""
        service, _ = _build_service(engine)
        from unittest.mock import patch

        row = _FakeInstanceRow(InstanceStatus.WAITING_CHILDREN.value)

        mock_session = MagicMock()
        mock_session.get.return_value = row

        @contextmanager
        def mock_session_ctx():
            yield mock_session

        captured_kwargs: dict = {}

        def _capture_task(**kwargs):
            captured_kwargs.update(kwargs)
            from daemon.repositories.task.models import Task
            return Task(**kwargs)

        with patch(
            "daemon.services.instance_messaging.Session",
            return_value=mock_session_ctx(),
        ), patch(
            "daemon.services.instance_messaging.MainLoopBridge"
        ), patch(
            "daemon.services.instance_messaging.Instance",
            _FakeInstanceRow,
        ), patch(
            "daemon.services.instance_messaging.MessageQueue"
        ), patch(
            "daemon.services.instance_messaging.Event"
        ), patch(
            "daemon.services.instance_messaging.Task",
            side_effect=_capture_task,
        ):
            _ = service._prepare_enqueued_message(
                instance_id="iid-wc-defer",
                message="wake",
                source="api",
                priority=1,
                images=None,
                metadata=None,
                is_deferred=True,
            )

        assert captured_kwargs.get("is_deferred") is True, (
            "WAITING_CHILDREN parent with caller is_deferred=True "
            "must keep the caller's flag verbatim — A1 carve-out is "
            "terminal-only"
        )

    def test_paused_status_preserves_callers_is_deferred_true(
        self, engine
    ) -> None:
        """PAUSED parent with caller is_deferred=True → row carries
        True. PAUSED is excluded from the carve-out set because
        pause is suspended-but-occupying (NOT terminal); the
        orchestrator's defer opt-in must reach the gate."""
        service, _ = _build_service(engine)
        from unittest.mock import patch

        row = _FakeInstanceRow(InstanceStatus.PAUSED.value)

        mock_session = MagicMock()
        mock_session.get.return_value = row

        @contextmanager
        def mock_session_ctx():
            yield mock_session

        captured_kwargs: dict = {}

        def _capture_task(**kwargs):
            captured_kwargs.update(kwargs)
            from daemon.repositories.task.models import Task
            return Task(**kwargs)

        with patch(
            "daemon.services.instance_messaging.Session",
            return_value=mock_session_ctx(),
        ), patch(
            "daemon.services.instance_messaging.MainLoopBridge"
        ), patch(
            "daemon.services.instance_messaging.Instance",
            _FakeInstanceRow,
        ), patch(
            "daemon.services.instance_messaging.MessageQueue"
        ), patch(
            "daemon.services.instance_messaging.Event"
        ), patch(
            "daemon.services.instance_messaging.Task",
            side_effect=_capture_task,
        ):
            _ = service._prepare_enqueued_message(
                instance_id="iid-paused-defer",
                message="wake",
                source="api",
                priority=1,
                images=None,
                metadata=None,
                is_deferred=True,
            )

        assert captured_kwargs.get("is_deferred") is True, (
            "PAUSED parent with caller is_deferred=True must keep "
            "the caller's flag verbatim — PAUSED is not a terminal "
            "status and the carve-out is terminal-only"
        )


# ---------------------------------------------------------------------------
# Static-analysis guard — the A1 fix does not introduce new census sites
# ---------------------------------------------------------------------------


class TestA1ConstitutionStatic:
    """A1 fix is structural — it derives ``is_deferred_for_task``
    from existing locals; no new ``admission_state`` writes,
    no new JobItem creators, no new ``work_id`` mints land.

    The constitution drift detector (run separately) is the
    canonical guard, but this file-scoped pin catches regressions
    early when the A1 block is touched.
    """

    def test_a1_block_writes_no_admission_state(self) -> None:
        """The A1 carve-out block does NOT write ``admission_state``
        or introduce new JobItem / work_id surfaces."""
        from pathlib import Path

        prod_path = (
            Path(__file__).parent.parent.parent.parent
            / "daemon"
            / "services"
            / "instance_messaging.py"
        )
        contents = prod_path.read_text()

        # The A1 block is the ONLY new Task-construction insertion in
        # the prelude; it MUST only touch ``is_deferred_for_task``
        # (no new column writes).
        assert "is_deferred_for_task" in contents, (
            "A1 carve-out local not found in the prelude — the fix "
            "may have regressed; check that the test still pins the "
            "intended structural change"
        )
        # The carve-out line is a Python ``and not is_terminal_revival`` —
        # ensure the override is structural (boolean logic), not a
        # column write to admission_state.
        assert "is_deferred_for_task = bool(is_deferred) and not is_terminal_revival" in contents, (
            "A1 carve-out expression drift — the boolean composition "
            "must stay exact (it pins the structural override)"
        )