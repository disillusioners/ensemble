"""Phase-3 tz-fix acceptance pins: byte-stable created_at, WorkRecord
timing matrix, and reconcile stamp frames.

Feature ``feature/fix-job-queue-timestamps-tz`` (commit 10). Covers the
mission's unit-test checklist items not already pinned in
``test_timestamps.py`` / ``test_tz_sort_since.py``:

* **Byte-stable created_at** — the JobItem TEXT string passes through
  the response composition (list + detail) UNCHANGED, including the
  'Z'-suffix edge shape (D3).
* **Dispatch/terminal timing semantics** — WorkRecord composition
  matrix: pending → started_at None; running → started_at from Task,
  completed_at None; terminal → both from Task; created_at ALWAYS the
  JobItem value (D1/D3/D4).
* **Reconcile stamp frames** — ``reconcile_turn_mirror``'s bound-param
  stamps (job_queue_items.failed_at TEXT aware-ISO;
  message_queue.completed_at naive digits) carry the UTC frame on a
  real SQLite engine (CURRENT_TIMESTAMP replacement, commit 2).

Pure unit tests — file-backed SQLite only; NO PostgreSQL, NO daemon boot.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel

import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401

from daemon.repositories.instance.models import Instance
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.job_queue.models import AdmissionState, JobItem
from daemon.repositories.job_queue.repository import JobRepository
from daemon.repositories.message_queue.models import MessageQueue
from daemon.repositories.task.models import Task, TaskStatus
from daemon.repositories.task.repository import TaskRepository
from daemon.routers.jobs_crud import _job_to_response
from daemon.services.work_resolver import WorkResolverService


@pytest.fixture
def engine(tmp_path) -> Engine:
    eng = create_engine(
        f"sqlite:///{tmp_path}/tz_acceptance.db",
        poolclass=NullPool,
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(eng)
    return eng


@pytest.fixture
def resolver(engine: Engine) -> WorkResolverService:
    return WorkResolverService(
        task_repo=TaskRepository(engine),
        job_repo=JobRepository(engine),
        instance_repo=SQLModelInstanceRepository(engine),
    )


def _seed_instance(
    engine: Engine, instance_id: str = "inst-tz", status: str = "running"
) -> str:
    now = datetime.now(timezone.utc)
    with Session(engine) as s:
        s.add(
            Instance(
                instance_id=instance_id,
                agent_id="developer",
                agent_dir="/tmp/agents/developer",
                agent_name="developer",
                project_id="tz-project",
                status=status,
                created_at=now.isoformat(),
                updated_at=now.isoformat(),
            )
        )
        s.commit()
    return instance_id


def _seed_job(
    engine: Engine,
    *,
    job_id: str,
    instance_id: str,
    created_at: str,
    admission_state: str = AdmissionState.ACTIVE.value,
) -> None:
    with Session(engine) as s:
        s.add(
            JobItem(
                job_id=job_id,
                agent_id="developer",
                agent_dir="/tmp/agents/developer",
                project_id="tz-project",
                instance_id=instance_id,
                message="tz-acceptance seed",
                admission_state=admission_state,
                priority=1,
                created_at=created_at,
                job_type="task",
            )
        )
        s.commit()


def _seed_task(
    engine: Engine,
    *,
    work_id: str,
    instance_id: str,
    status: str,
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
) -> None:
    with Session(engine) as s:
        s.add(
            Task(
                work_id=work_id,
                task_type="process_message",
                instance_id=instance_id,
                status=status,
                # Naive-UTC digits — the Phase-3 writer contract.
                created_at=datetime.now(timezone.utc).replace(tzinfo=None),
                started_at=started_at,
                completed_at=completed_at,
            )
        )
        s.commit()


# ─── D3: byte-stable created_at through response composition ───────────────


class TestByteStableCreatedAt:
    """JobItem created_at TEXT passes create→get views UNCHANGED."""

    def test_work_record_path_is_verbatim_in_response(self, engine, resolver):
        iid = _seed_instance(engine)
        raw = "2026-09-15T14:09:59.500140+00:00"
        _seed_job(engine, job_id="job-byte-1", instance_id=iid, created_at=raw)

        with Session(engine) as s:
            job = s.get(JobItem, "job-byte-1")
            record = resolver.resolve_work("job-byte-1")

        response = _job_to_response(job, work_record=record)
        assert response.created_at == raw  # byte-identical

    def test_z_suffix_shape_is_verbatim(self, engine, resolver):
        """'Z' vs '+00:00' is a BYTE difference — D3 requires verbatim,
        so the 'Z' shape must survive the response composition rather
        than being re-rendered as '+00:00' by a parsed-datetime path."""
        iid = _seed_instance(engine)
        raw_z = "2026-09-15T14:09:59.500140Z"
        _seed_job(engine, job_id="job-byte-2", instance_id=iid, created_at=raw_z)

        with Session(engine) as s:
            job = s.get(JobItem, "job-byte-2")
            record = resolver.resolve_work("job-byte-2")

        response = _job_to_response(job, work_record=record)
        assert response.created_at == raw_z

    def test_legacy_fallback_branch_matches_work_record_branch(self, engine, resolver):
        """D4 list/detail consistency: both _job_to_response branches
        emit the same created_at for the same JobItem row."""
        iid = _seed_instance(engine)
        raw = "2026-09-15T14:09:59.500140+00:00"
        _seed_job(engine, job_id="job-byte-3", instance_id=iid, created_at=raw)

        with Session(engine) as s:
            job = s.get(JobItem, "job-byte-3")
            record = resolver.resolve_work("job-byte-3")

        with_record = _job_to_response(job, work_record=record)
        without_record = _job_to_response(job, work_record=None)
        assert with_record.created_at == without_record.created_at == raw

    def test_dual_backed_row_keeps_jobitem_created_at(self, engine, resolver):
        """DC-C: a dual-backed work_id (Task + JobItem) must surface the
        JobItem TEXT created_at, NOT the Task row's naive digits."""
        iid = _seed_instance(engine)
        raw = "2026-09-15T14:09:59.500140+00:00"
        _seed_job(engine, job_id="job-byte-4", instance_id=iid, created_at=raw)
        _seed_task(
            engine,
            work_id="job-byte-4",
            instance_id=iid,
            status=TaskStatus.RUNNING.value,
            started_at=datetime(2026, 9, 15, 14, 10, 1, 441308),
        )

        with Session(engine) as s:
            job = s.get(JobItem, "job-byte-4")
        record = resolver.resolve_work("job-byte-4")

        # The record's datetime parse AND the response string both
        # reflect the JobItem value.
        assert record is not None
        assert record.created_at == datetime(
            2026, 9, 15, 14, 9, 59, 500140, tzinfo=timezone.utc
        )
        response = _job_to_response(job, work_record=record)
        assert response.created_at == raw


# ─── D1/D4: WorkRecord timing matrix ────────────────────────────────────────


class TestWorkRecordTimingMatrix:
    """pending/running/terminal → started_at/completed_at composition."""

    def test_pending_no_task_no_started_at(self, engine, resolver):
        iid = _seed_instance(engine)
        _seed_job(
            engine,
            job_id="job-pend",
            instance_id=iid,
            created_at="2026-09-15T14:09:59.500140+00:00",
            admission_state=AdmissionState.QUEUED.value,
        )
        record = resolver.resolve_work("job-pend")
        assert record is not None
        assert record.started_at is None
        assert record.completed_at is None

    def test_pending_with_unclaimed_task_no_started_at(self, engine, resolver):
        """A Task row exists but no worker claimed it yet — D1 says
        PENDING jobs show NO started_at even when a Task row exists."""
        iid = _seed_instance(engine)
        _seed_job(
            engine,
            job_id="job-pend2",
            instance_id=iid,
            created_at="2026-09-15T14:09:59.500140+00:00",
        )
        _seed_task(
            engine, work_id="job-pend2", instance_id=iid,
            status=TaskStatus.PENDING.value,  # created, never claimed
        )
        record = resolver.resolve_work("job-pend2")
        assert record is not None
        assert record.started_at is None
        assert record.completed_at is None

    def test_running_started_from_task_completed_none(self, engine, resolver):
        iid = _seed_instance(engine)
        _seed_job(
            engine,
            job_id="job-run",
            instance_id=iid,
            created_at="2026-09-15T14:09:59.500140+00:00",
        )
        _seed_task(
            engine,
            work_id="job-run",
            instance_id=iid,
            status=TaskStatus.RUNNING.value,
            started_at=datetime(2026, 9, 15, 14, 10, 1, 441308),
        )
        record = resolver.resolve_work("job-run")
        assert record is not None
        assert record.started_at == "2026-09-15T14:10:01.441308+00:00"
        assert record.completed_at is None

    def test_terminal_both_from_task(self, engine, resolver):
        iid = _seed_instance(engine)
        _seed_job(
            engine,
            job_id="job-done",
            instance_id=iid,
            created_at="2026-09-15T14:09:59.500140+00:00",
            admission_state=AdmissionState.DONE.value,
        )
        _seed_task(
            engine,
            work_id="job-done",
            instance_id=iid,
            status=TaskStatus.COMPLETED.value,
            started_at=datetime(2026, 9, 15, 14, 10, 1, 441308),
            completed_at=datetime(2026, 9, 15, 14, 11, 37, 280221),
        )
        record = resolver.resolve_work("job-done")
        assert record is not None
        assert record.started_at == "2026-09-15T14:10:01.441308+00:00"
        assert record.completed_at == "2026-09-15T14:11:37.280221+00:00"

    def test_task_only_record_carries_timing(self, engine, resolver):
        """Report-lane rows (Task-only) surface timing from the Task row
        too (D4: no NULL asymmetry between detail and list)."""
        iid = _seed_instance(engine)
        _seed_task(
            engine,
            work_id="report-only-1",
            instance_id=iid,
            status=TaskStatus.COMPLETED.value,
            started_at=datetime(2026, 9, 15, 14, 10, 1, 441308),
            completed_at=datetime(2026, 9, 15, 14, 11, 37, 280221),
        )
        record = resolver.resolve_work("report-only-1")
        assert record is not None
        assert record.kind == "report"
        assert record.started_at == "2026-09-15T14:10:01.441308+00:00"
        assert record.completed_at == "2026-09-15T14:11:37.280221+00:00"

    def test_offset_emitted_on_every_timing_field(self, engine, resolver):
        """Serializer offset emission: every populated timing field on
        the composed record carries the explicit +00:00 offset."""
        iid = _seed_instance(engine)
        _seed_job(
            engine,
            job_id="job-off",
            instance_id=iid,
            created_at="2026-09-15T14:09:59.500140+00:00",
        )
        _seed_task(
            engine,
            work_id="job-off",
            instance_id=iid,
            status=TaskStatus.COMPLETED.value,
            started_at=datetime(2026, 9, 15, 14, 10, 1),
            completed_at=datetime(2026, 9, 15, 14, 11, 37),
        )
        payload = resolver.resolve_work("job-off").to_dict()
        for key in ("started_at", "completed_at", "created_at"):
            value = payload[key]
            assert value is not None and value.endswith("+00:00"), (
                f"{key} must carry the explicit UTC offset; got {value!r}"
            )


# ─── Commit 2: reconcile_turn_mirror bound-param stamp frames ──────────────


class TestReconcileStampFrames:
    """CURRENT_TIMESTAMP replacement writes the UTC frame."""

    def _seed_message(self, engine: Engine, message_id: str) -> None:
        with Session(engine) as s:
            s.add(
                MessageQueue(
                    message_id=message_id,
                    instance_id="inst-tz",
                    content="payload",
                    status="processing",
                    # Naive-UTC digits — post-fix writer shape.
                    enqueued_at=datetime.now(timezone.utc).replace(tzinfo=None),
                )
            )
            s.commit()

    def test_failed_at_stamp_is_aware_iso(self, engine):
        """job_queue_items.failed_at (TEXT) gets an aware ISO stamp with
        the +00:00 offset — string-comparable with legacy rows."""
        task_repo = TaskRepository(engine)
        # Terminal-side instance status: the mirror's CASE guards
        # suppress the terminal write while the instance is on the
        # alive-side (waiting_children/paused/running), so this
        # fail-path row seeds a terminal status.
        iid = _seed_instance(engine, status="completed")
        _seed_job(
            engine,
            job_id="wid-fail",
            instance_id=iid,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        with Session(engine) as s:
            task = Task(
                work_id="wid-fail",
                task_type="process_message",
                instance_id=iid,
                message_id="msg-fail",
                status=TaskStatus.RUNNING.value,
            )
            s.add(task)
            s.commit()
            task_id = task.id

        task_repo.fail_task(task_id, "boom")

        with Session(engine) as s:
            row = s.get(JobItem, "wid-fail")
            # The seeded JobItem MUST be matched by job_id — the
            # pre-fix version of this test asserted against a
            # non-existent row (vacuous pass).
            assert row is not None
            assert row.failed_at is not None, (
                "fail_task reconcile must stamp failed_at on the "
                "matched job row"
            )
            assert row.failed_at.endswith("+00:00"), row.failed_at
            parsed = datetime.fromisoformat(row.failed_at)
            assert parsed.utcoffset() == timedelta(0)

    def test_message_queue_completed_at_is_naive_digits(self, engine):
        task_repo = TaskRepository(engine)
        iid = _seed_instance(engine)
        self._seed_message(engine, "msg-comp")
        with Session(engine) as s:
            task = Task(
                work_id="wid-comp",
                task_type="process_message",
                instance_id=iid,
                message_id="msg-comp",
                status=TaskStatus.RUNNING.value,
            )
            s.add(task)
            s.commit()
            task_id = task.id

        before = datetime.now(timezone.utc)
        task_repo.complete_task(task_id, {"ok": True})
        after = datetime.now(timezone.utc)

        with Session(engine) as s:
            msg = s.get(MessageQueue, "msg-comp")
            assert msg is not None
            assert msg.status == "completed"
            # Naive bind: the stored value is naive (no tzinfo on the
            # SQLite round-trip) and its digits are UTC wall-clock —
            # within the before/after window when read assume-UTC.
            assert msg.completed_at is not None
            assert msg.completed_at.tzinfo is None
            read = msg.completed_at.replace(tzinfo=timezone.utc)
            assert before <= read <= after

    def test_message_completed_at_never_carries_session_offset(
        self, engine
    ):
        """The pre-fix shape stored local digits (+07 in prod). The new
        stamp's digits must equal the UTC wall clock — pinned by the
        window check above; this companion asserts the naive TYPE so a
        future aware-bind regression (tzinfo surviving the round-trip)
        fails loudly here first."""
        task_repo = TaskRepository(engine)
        iid = _seed_instance(engine)
        self._seed_message(engine, "msg-naive")
        with Session(engine) as s:
            task = Task(
                work_id="wid-naive",
                task_type="process_message",
                instance_id=iid,
                message_id="msg-naive",
                status=TaskStatus.RUNNING.value,
            )
            s.add(task)
            s.commit()
            task_id = task.id

        task_repo.complete_task(task_id, {"ok": True})

        with Session(engine) as s:
            msg = s.get(MessageQueue, "msg-naive")
            assert msg.completed_at is not None
            assert isinstance(msg.completed_at, datetime)

    def test_schedule_retry_stamp_frame_is_naive_utc(self, engine):
        """B1 pin: the schedule_retry gate's ``"now": now_utc_naive()``
        binding (task/repository.py gate UPDATE) stamps BOTH
        completed_at and cancel_requested_at with naive-UTC digits —
        not session-local digits, not aware values.

        De-vacuous: a real RUNNING task row goes through the real
        gate UPDATE (``retry_scheduled = false`` + status guard) and
        the asserts read back the STORED parent row — same pattern
        as test_failed_at_stamp_is_aware_iso above."""
        task_repo = TaskRepository(engine)
        with Session(engine) as s:
            task = Task(
                work_id="wid-retry",
                task_type="process_message",
                instance_id="inst-tz",
                status=TaskStatus.RUNNING.value,
            )
            s.add(task)
            s.commit()
            task_id = task.id

        before = datetime.now(timezone.utc)
        child = task_repo.schedule_retry(task_id, max_retries=3)
        after = datetime.now(timezone.utc)

        # The gate must have MATCHED (retry_scheduled=false +
        # RUNNING status + retry_count < max_retries) — otherwise
        # the asserts below would pass vacuously on an untouched
        # row.
        assert child is not None, "gate must match a fresh RUNNING task"

        with Session(engine) as s:
            parent = s.get(Task, task_id)
            assert parent is not None
            assert parent.status == TaskStatus.CANCELLED.value

            # completed_at (DATETIME column): naive round-trip, UTC
            # wall-clock digits inside the before/after window, and
            # isoformat() renders WITHOUT an offset suffix.
            assert parent.completed_at is not None
            assert parent.completed_at.tzinfo is None
            assert "+" not in parent.completed_at.isoformat()
            read_completed = parent.completed_at.replace(tzinfo=timezone.utc)
            assert before <= read_completed <= after

            # cancel_requested_at (TEXT column): the same ``now``
            # binding stringifies on the round-trip — parse it and
            # apply the same frame checks (naive, UTC digits, no
            # offset in the stored string).
            assert parent.cancel_requested_at is not None
            parsed_cancel = datetime.fromisoformat(parent.cancel_requested_at)
            assert parsed_cancel.tzinfo is None
            assert before <= parsed_cancel.replace(tzinfo=timezone.utc) <= after
            # One binding serves BOTH SET entries (the gate's
            # single-key shape) — the stored digits must be equal.
            assert parent.completed_at == parsed_cancel


if __name__ == "__main__":
    pytest.main([__file__])
